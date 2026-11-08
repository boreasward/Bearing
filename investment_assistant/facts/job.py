"""The v3.1 facts job: collect (optional), deterministic compute, host plan review, the draft
brief via facts.view, the persisted run and the draft file (spec §5 步骤 1–4)."""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import investment_assistant.analysis.pool as pool_module
import investment_assistant.analysis.rules as rules_module
import investment_assistant.analysis.screen_rules as screen_module
import investment_assistant.core.tx as core_tx
from investment_assistant.config import AssistantConfig, briefs_dir, load_config
from investment_assistant.core.constants import BENCHMARK
from investment_assistant.data import collect as data_collect
from investment_assistant.data import repo as data_repo
from investment_assistant.data.adapter_types import AdapterDeps
from investment_assistant.data.adapters.bars import latest_closed_session
from investment_assistant.data.adapters.universe import load as load_universe
from investment_assistant.data.market_constants import MACRO_SERIES
from investment_assistant.db import list_watchlist_items
from investment_assistant.execution.positions import PositionError, build
from investment_assistant.facts import collect as facts_collect
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts import research_review as facts_research_review
from investment_assistant.facts import review as facts_review
from investment_assistant.facts.brief import render_brief, render_error_brief
from investment_assistant.facts.continuity import continuity
from investment_assistant.facts.coverage import coverage
from investment_assistant.facts.factors import industry_strength, setup_followthrough
from investment_assistant.facts.market import market_view
from investment_assistant.facts.ranking import rank_candidates
from investment_assistant.facts.view import brief_view

_BEIJING = ZoneInfo("Asia/Shanghai")  # 简报页脚的时间口径


def load_inputs(conn, *, session: date, now: datetime, cfg: AssistantConfig) -> dict:
    """Everything compute needs, all cut off at ``now``: bars, earnings, sector groups and macro."""
    try:
        members = load_universe().tickers
    except ValueError:  # 范围文件读不出来：范围退到关注列表
        members = ()
    try:
        watch = pool_module.from_watchlist(list_watchlist_items(conn)).tickers
    except ValueError:
        watch = ()
    stocks = sorted((set(members) | set(watch)) - {BENCHMARK, *cfg.v3.sector_etfs})

    def bars_of(ticker: str) -> list[dict]:
        return data_repo.read_bars(conn, ticker, session, cutoff_at=now, limit=300)

    sectors = data_repo.latest_sectors(conn, stocks, cutoff_at=now)
    etf_bars = {etf: bars_of(etf) for etf in cfg.v3.sector_etfs}
    return {
        "stocks": stocks,
        "bars_by_ticker": {ticker: bars_of(ticker) for ticker in stocks},
        "etf_bars": etf_bars,
        "benchmark_bars": etf_bars.get(screen_module.BENCHMARK, []),
        "spy_bars": bars_of(BENCHMARK),
        "earnings_by_ticker": {ticker: data_repo.latest_earnings(conn, ticker, cutoff_at=now) for ticker in stocks},
        "groups": {ticker: (sectors.get(ticker) or {}).get("group") for ticker in stocks},
        "macro_rows": {
            series: data_repo.read_macro(conn, series, end_date=session, cutoff_at=now, limit=60)
            for series in MACRO_SERIES
        },
        "macro_status": {
            series: "ok" if os.environ.get("FRED_API_KEY") else "not_configured" for series in MACRO_SERIES
        },
    }


def compute(inputs: dict, *, session: date, cfg: AssistantConfig) -> dict:
    """The deterministic chain: SPY must have the session's bar, then screen (QQQ benchmark),
    market view, both factor tables and the ranked list."""
    reason = rules_module.precondition(inputs["spy_bars"], session)
    if reason:
        raise screen_module.BenchmarkMissing(f"{BENCHMARK}: {reason}")
    screen = screen_module.evaluate(
        session=session,
        bars_by_ticker=inputs["bars_by_ticker"],
        benchmark_bars=inputs["benchmark_bars"],
        earnings_by_ticker=inputs["earnings_by_ticker"],
    )
    market = market_view(
        session=session,
        universe_bars=inputs["bars_by_ticker"],
        etf_bars=inputs["etf_bars"],
        spy_bars=inputs["spy_bars"],
        macro_rows=inputs["macro_rows"],
        macro_status=inputs["macro_status"],
    )
    setup = setup_followthrough(inputs["bars_by_ticker"], session=session, cfg=cfg.v3)
    industry = industry_strength(
        inputs["bars_by_ticker"], inputs["spy_bars"], inputs["groups"], session=session, cfg=cfg.v3
    )
    candidates = rank_candidates(screen["results"], setup=setup, industry=industry, groups=inputs["groups"])
    return {"market": market, "screen": screen, "setup": setup, "industry": industry, "candidates": candidates}


def _has_holdings(conn, *, now: datetime) -> bool:
    """有任一未平仓持仓时，错误简报的第一行要写「今天无法检查止损」（spec §5）。"""
    by_ticker: dict[str, list[dict]] = {}
    for trade in facts_repo.list_trades(conn):
        by_ticker.setdefault(trade["ticker"], []).append(trade)
    for ticker, rows in by_ticker.items():
        try:
            if build(rows, splits=data_repo.split_events(conn, ticker, cutoff_at=now))["position"] is not None:
                return True
        except PositionError:
            continue
    return False


def _write_draft(cfg: AssistantConfig, session: date, draft: str) -> Path:
    path = briefs_dir(cfg) / f"{session}.draft.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(draft, encoding="utf-8")
    return path


def _error_run(
    *, run_id: str, session: date, trigger: str, now: datetime, started_at: datetime, error: str, cfg: AssistantConfig
) -> dict:
    """A run that produced no list: the error run row plus the reason-only brief (spec §5.2)."""
    with core_tx.read_only_tx() as conn:
        has_holdings = _has_holdings(conn, now=now)
    draft = render_error_brief(session=session, as_of=now.astimezone(_BEIJING), error=error, has_holdings=has_holdings)
    with core_tx._tx() as conn:
        facts_repo.insert_run(
            conn,
            {
                "run_id": run_id,
                "session": session,
                "trigger": trigger,
                "status": "error",
                "error": error,
                "market": {},
                "factors": {},
                "coverage": {},
                "holdings": [],
                "dropped": [],
                "draft_md": draft,
                "started_at": started_at,
                "finished_at": data_collect.wall_clock(),
            },
        )
    path = _write_draft(cfg, session, draft)
    return {
        "run_id": run_id,
        "session": session.isoformat(),
        "status": "error",
        "error": error,
        "candidates": 0,
        "review": {"changes": [], "unchecked": [], "errors": []},
        "research_review": {"changes": [], "unchecked": [], "errors": []},
        "draft_path": str(path),
    }


def _continuity_inputs(conn, *, session: date, candidates: list[dict]) -> dict:
    """上一个有成功运行记录的交易日的名单行（带 streak），没有比较对象时为 None。"""
    previous_run = facts_repo.previous_ok_run(conn, before=session)
    previous = None
    if previous_run is not None:
        previous = [
            {"ticker": row["ticker"], "rank": row["rank"], "streak": (row.get("detail") or {}).get("streak", 1)}
            for row in facts_repo.candidates(conn, previous_run["run_id"])
        ]
    return {"today": candidates, "previous": previous, "first_run": previous_run is None}


def run_facts(
    *,
    now: datetime | None = None,
    trigger: str = "manual_batch",
    collect: bool = True,
    cfg: AssistantConfig | None = None,
    deps: AdapterDeps | None = None,
) -> dict:
    """One facts run: collect (unless off), compute, review the plan line, write the draft
    brief and the run row with its candidates. A missing benchmark writes the error run
    and brief instead of raising."""
    cfg = cfg if cfg is not None else load_config()
    now = now if now is not None else data_collect.wall_clock()
    session = latest_closed_session(now)
    run_id = facts_collect.new_run_id(now)
    started_at = data_collect.wall_clock()
    fetch_rows: list[dict] = []
    if collect:
        fetch_rows = facts_collect.collect_daily(
            session=session, now=now, run_id=run_id, trigger=trigger, cfg=cfg, deps=deps
        )
    try:
        with core_tx.read_only_tx() as conn:
            inputs = load_inputs(conn, session=session, now=now, cfg=cfg)
        computed = compute(inputs, session=session, cfg=cfg)
    except screen_module.BenchmarkMissing as exc:
        return _error_run(
            run_id=run_id,
            session=session,
            trigger=trigger,
            now=now,
            started_at=started_at,
            error=f"基准日线缺失：{exc}",
            cfg=cfg,
        )
    candidates = computed["candidates"]
    if collect:
        fetch_rows += facts_collect.collect_extra(
            [row["ticker"] for row in candidates[: cfg.v3.top_n]],
            session=session,
            now=now,
            run_id=run_id,
            trigger=trigger,
            cfg=cfg,
            deps=deps,
        )
    # 步骤 3：计划与持仓复查 + 研究复查（宿主，写事件与计划/研究字段；同一事务）
    with core_tx._tx() as conn:
        review = facts_review.review_plans(conn, session=session, cutoff_at=now, cfg=cfg)
        research_result = facts_research_review.review_research(conn, session=session, cutoff_at=now, cfg=cfg)
    # 名单连续性（与上一个 ok 运行比），掉出名单的原因取当天筛选结果
    with core_tx.read_only_tx() as conn:
        continuity_in = _continuity_inputs(conn, session=session, candidates=candidates)
    cont = continuity(
        today=continuity_in["today"],
        previous=continuity_in["previous"],
        screen_results=computed["screen"]["results"],
        skips=computed["screen"]["skips"],
    )
    dropped = cont["dropped"]
    rows = [
        {**row, "detail": {**(row.get("detail") or {}), "streak": cont["streak"].get(row["ticker"], 1)}}
        for row in candidates
    ]
    cov = coverage(fetch_rows)
    # 步骤 4：草稿（宿主部分 + 已保存的结论），再写运行记录与名单
    with core_tx.read_only_tx() as conn:
        view = brief_view(
            conn,
            session=session,
            cutoff_at=now,
            cfg=cfg,
            run={
                "run_id": run_id,
                "session": session,
                "market": computed["market"],
                "coverage": cov,
                "dropped": dropped,
                "candidates": rows,
            },
        )
    draft = render_brief(view, final=False)
    with core_tx._tx() as conn:
        facts_repo.insert_run(
            conn,
            {
                "run_id": run_id,
                "session": session,
                "trigger": trigger,
                "status": "ok",
                "error": None,
                "market": computed["market"],
                "factors": {"setup": computed["setup"], "industry": computed["industry"]},
                "coverage": cov,
                "holdings": view["holdings"],
                "dropped": dropped,
                "draft_md": draft,
                "started_at": started_at,
                "finished_at": data_collect.wall_clock(),
            },
        )
        facts_repo.insert_candidates(conn, run_id, rows)
    path = _write_draft(cfg, session, draft)
    return {
        "run_id": run_id,
        "session": session.isoformat(),
        "status": "ok",
        "error": None,
        "candidates": len(candidates),
        "review": review,
        "research_review": research_result,
        "draft_path": str(path),
    }
