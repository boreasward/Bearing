"""Read tools: the day's facts, all cut at the env's session and cutoff (spec §6 读, §10)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from investment_assistant.core.constants import BENCHMARK
from investment_assistant.core.knowledge import agent_text, catalog
from investment_assistant.data import repo as data_repo
from investment_assistant.derivation.indicators import price as price_indicators
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts.account import account_view
from investment_assistant.facts.agenda import agenda
from investment_assistant.facts.coverage import coverage_lines
from investment_assistant.facts.holdings import holdings_view
from investment_assistant.facts.view import today_actions
from investment_assistant.toolsurface.env import ToolEnv
from investment_assistant.toolsurface.validation import normalize_ticker

NEWS_LIMIT = 20
FILINGS_LIMIT = 20
FUNDAMENTALS_LIMIT = 40


def _as_int(value: Any) -> int | None:
    """真整数（不含 bool）才返回；其余输入归 None，由调用方走错误信封。"""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _int_arg(value: Any, name: str, lo: int, hi: int) -> int:
    number = _as_int(value)
    if number is None or not lo <= number <= hi:
        raise ValueError(f"invalid_argument: {name} 必须是 {lo}–{hi} 之间的整数")
    return number


def latest_run_or_none(conn, env: ToolEnv) -> dict | None:
    return facts_repo.latest_run(conn, env.session)


def need_run(conn, env: ToolEnv) -> dict:
    run = latest_run_or_none(conn, env)
    if run is None:
        raise ValueError("no_run")
    return run


def top_tickers(conn, env: ToolEnv) -> list[str]:
    """当期名单前 N 只；没有运行记录时为空（brief 模式下任何标的都不在范围内）。"""
    run = latest_run_or_none(conn, env)
    if run is None:
        return []
    return [row["ticker"] for row in facts_repo.candidates(conn, run["run_id"]) if row["rank"] <= env.cfg.v3.top_n]


def session_bars(conn, env: ToolEnv, ticker: str) -> list[dict]:
    return data_repo.read_bars(conn, ticker, env.session, cutoff_at=env.cutoff_at)


def holdings_rows(conn, env: ToolEnv) -> list[dict]:
    """该交易日口径下的持仓视图：FIFO 持仓、最新收盘、有效计划的止损与下一交易日操作。"""
    trades = facts_repo.list_trades(conn)
    tickers = sorted({trade["ticker"] for trade in trades})
    closes, splits, plans = {}, {}, {}
    for ticker in tickers:
        bars = session_bars(conn, env, ticker)
        if bars:
            closes[ticker] = bars[-1]["close"]
        splits[ticker] = data_repo.split_events(conn, ticker, cutoff_at=env.cutoff_at)
        plan = plan_repo.active_plan(conn, ticker)
        if plan is not None:
            plans[ticker] = plan
    actions = {
        row["ticker"]: {"kind": row["kind"], "text": row["text"], "stop": row["stop"], "reason": row["reason"]}
        for row in today_actions(conn, session=env.session, cutoff_at=env.cutoff_at, cfg=env.cfg)
    }
    return holdings_view(trades, closes=closes, plans=plans, splits=splits, actions=actions)


def get_market(env: ToolEnv) -> dict:
    def _read(conn):
        run = need_run(conn, env)
        market = run["market"] or {}
        return {
            "session": run["session"],
            "label": market.get("label"),
            "regime_tier": market.get("regime_tier"),
            "breadth": market.get("breadth"),
            "spy_ret20": market.get("spy_ret20"),
            "etf_table": market.get("etf_table"),
            "macro": market.get("macro"),
        }

    return env.read(_read)


def get_candidates(env: ToolEnv) -> dict:
    def _read(conn):
        run = need_run(conn, env)
        verdicts = facts_repo.verdicts(conn, env.session)
        active = {plan["ticker"]: plan for plan in plan_repo.plans_by_status(conn, ("pending", "open"))}
        rows = [
            {
                "ticker": row["ticker"],
                "rank": row["rank"],
                "reason": row["reason"],
                "screen_group": row["screen_group"],
                "trigger_rules": row["trigger_rules"],
                "setup_tier": row["setup_tier"],
                "industry": row["industry"],
                "industry_tier": row["industry_tier"],
                "rs": row["rs"],
                "in_top_n": row["rank"] <= env.cfg.v3.top_n,
                "verdict": verdicts[row["ticker"]]["verdict"] if row["ticker"] in verdicts else None,
                "streak": (row.get("detail") or {}).get("streak"),
                "has_active_plan": row["ticker"] in active,
                "plan_id": active[row["ticker"]]["id"] if row["ticker"] in active else None,
            }
            for row in facts_repo.candidates(conn, run["run_id"])
        ]
        return {
            "run_id": run["run_id"],
            "top_n": env.cfg.v3.top_n,
            "candidates": rows,
            "dropped": list(run.get("dropped") or []),
        }

    return env.read(_read)


def get_coverage(env: ToolEnv) -> dict:
    def _read(conn):
        run = need_run(conn, env)
        cov = run["coverage"] or {}
        families = cov.get("families") or {}
        missing = cov.get("missing_bars") or []
        return {
            "families": families,
            "missing_bars": missing,
            "lines": coverage_lines({"families": families, "missing_bars": missing}),
        }

    return env.read(_read)


def get_price_bars(env: ToolEnv, *, ticker: str, lookback: int = 60) -> list[dict]:
    ticker = normalize_ticker(ticker)
    lookback = _int_arg(lookback, "lookback", 1, 120)

    def _read(conn):
        rows = data_repo.read_bars(conn, ticker, env.session, cutoff_at=env.cutoff_at, limit=lookback)
        return [
            {
                "date": row["price_date"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
            }
            for row in rows
        ]

    return env.read(_read)


def get_indicators(env: ToolEnv, *, ticker: str) -> dict:
    ticker = normalize_ticker(ticker)

    def _read(conn):
        bars = session_bars(conn, env, ticker)
        if not bars:
            raise ValueError("no_bars")
        spy = session_bars(conn, env, BENCHMARK)
        return price_indicators.indicators(bars, spy or [])

    return env.read(_read)


def get_news(env: ToolEnv, *, ticker: str, days: int = 7) -> list[dict]:
    ticker = normalize_ticker(ticker)
    days = _int_arg(days, "days", 1, 14)

    def _read(conn):
        rows = data_repo.read_news(
            conn, ticker, cutoff_at=env.cutoff_at, since=env.cutoff_at - timedelta(days=days), limit=NEWS_LIMIT
        )
        return [
            {"source": row["source"], "title": row["title"], "url": row["url"], "published_at": row["published_at"]}
            for row in rows
        ]

    return env.read(_read)


def get_filings(env: ToolEnv, *, ticker: str, days: int = 30) -> list[dict]:
    ticker = normalize_ticker(ticker)
    days = _int_arg(days, "days", 1, 90)

    def _read(conn):
        rows = data_repo.read_filings(conn, ticker, since=env.session - timedelta(days=days), cutoff_at=env.cutoff_at)
        return [
            {
                "form": row["form"],
                "filing_date": row["filing_date"],
                "accepted_at": row["accepted_at"],
                "items": row["items"],
                "accession_number": row["accession_number"],
            }
            for row in rows[:FILINGS_LIMIT]
        ]

    return env.read(_read)


def get_earnings(env: ToolEnv, *, ticker: str) -> dict | None:
    ticker = normalize_ticker(ticker)

    def _read(conn):
        row = data_repo.latest_earnings(conn, ticker, cutoff_at=env.cutoff_at)
        return None if row is None else {"earnings_date": row["earnings_date"], "status": row["status"]}

    return env.read(_read)


def get_fundamentals(env: ToolEnv, *, ticker: str) -> dict:
    ticker = normalize_ticker(ticker)
    return env.read(
        lambda conn: {"rows": data_repo.financial_quarters(conn, ticker, cutoff_at=env.cutoff_at)[:FUNDAMENTALS_LIMIT]}
    )


def get_positions(env: ToolEnv) -> list[dict]:
    return env.read(lambda conn: holdings_rows(conn, env))


def get_plans(
    env: ToolEnv, *, status: str | None = None, ticker: str | None = None, plan_id: int | None = None
) -> dict:
    """缺省返回全部有效计划；可按状态、股票或计划编号过滤，每份带它的变化记录。"""
    ticker = normalize_ticker(ticker) if ticker is not None else None
    if status is not None and not isinstance(status, str):
        raise ValueError("invalid_argument: status 必须是字符串")
    if plan_id is not None and (not isinstance(plan_id, int) or isinstance(plan_id, bool)):
        raise ValueError("invalid_argument: plan_id 必须是整数")

    def _read(conn):
        if plan_id is not None:
            found = plan_repo.plan(conn, plan_id)
            plans = [] if found is None else [found]
        elif ticker is not None and status is None:
            found = plan_repo.active_plan(conn, ticker)
            plans = [] if found is None else [found]
        elif ticker is not None:
            plans = [row for row in plan_repo.plans_by_status(conn, (status or "pending",)) if row["ticker"] == ticker]
        else:
            plans = plan_repo.plans_by_status(conn, (status,) if status is not None else ("pending", "open"))
        return {"plans": [{**row, "events": plan_repo.events(conn, row["id"])} for row in plans]}

    return env.read(_read)


def get_account(env: ToolEnv) -> dict:
    """账户视图：账本、最近对账与差额、最近平仓结果、仓位本金快照（spec §8）。"""
    return env.read(lambda conn: account_view(conn, session=env.session, cutoff_at=env.cutoff_at))


def get_agenda(env: ToolEnv) -> dict:
    """当天待办：持仓复查、再次上榜、二次确认队列与名额/替换（spec §9.3）。"""
    return env.read(lambda conn: agenda(conn, session=env.session, cfg=env.cfg))


def get_concept(env: ToolEnv, *, concept_id: str | None = None) -> dict:
    if concept_id is not None and not isinstance(concept_id, str):
        raise ValueError("invalid_argument: concept_id 必须是字符串")
    return agent_text(catalog(), concept_id or None)
