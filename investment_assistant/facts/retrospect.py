"""The retrospect view assembled from the database (spec §5): one record per ended plan, the
weekly summary with as-of account snapshots, and the rule statistics — all computed on read
and never stored (spec §4 复盘不另建记录类表)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.data.calendar import add_trading_days, trading_days_between
from investment_assistant.execution.account import ledger
from investment_assistant.execution.plan_line import ENDED
from investment_assistant.execution.retrospect import NY, plan_record, rule_stats, week_bounds, weekly_summary
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo

BARS_LIMIT = 400  # 与 facts.review 同窗口：有效期与持有期远小于一年的交易日
RULE_KEYS = ("trigger_rules", "setup_tier", "industry")


def _decimal_bars(rows: list[dict]) -> list[dict]:
    """``read_bars`` 的浮点价格折成 Decimal：``execution.retrospect`` 的纯函数只吃 Decimal。"""
    return [
        {
            **row,
            "open": Decimal(str(row["open"])),
            "high": Decimal(str(row["high"])),
            "low": Decimal(str(row["low"])),
            "close": Decimal(str(row["close"])),
        }
        for row in rows
    ]


def records(conn, *, session: date, cutoff_at: datetime, cfg: AssistantConfig, limit: int | None = None) -> list[dict]:
    """每份已结束计划一行（spec §5.1）：日线读到 session、候选行取保存当天、trail_atr 取配置。"""
    trail_atr = Decimal(str(cfg.v3.trail_atr))
    out: list[dict] = []
    for plan in plan_repo.plans_by_status(conn, ENDED, limit=limit):
        ticker = plan["ticker"]
        bars = _decimal_bars(data_repo.read_bars(conn, ticker, session, cutoff_at=cutoff_at, limit=BARS_LIMIT))
        out.append(
            plan_record(
                plan=plan,
                events=plan_repo.events(conn, plan["id"]),
                trades=facts_repo.list_trades(conn, ticker),
                bars=bars,
                candidate=facts_repo.candidate_at(conn, plan["session"], ticker),
                trail_atr=trail_atr,
            )
        )
    return out


def _asof_ledger(conn, *, session: date, through: date, cutoff_at: datetime) -> dict:
    """该交易日收盘口径的账本快照：收盘价读到 ``session``，出入金与成交只算纽约日历日在
    ``through``（含）之前的，收盘价与拆股照 ``facts.account.account_view`` 的同一读法。"""
    trades = [
        trade
        for trade in facts_repo.list_trades(conn)
        if trade["executed_at"].astimezone(NY).date() <= through
    ]
    flows = [
        flow
        for flow in plan_repo.cash_flows(conn)
        if flow["effective_at"].astimezone(NY).date() <= through
    ]
    closes: dict[str, Decimal] = {}
    splits: dict[str, list] = {}
    for ticker in sorted({trade["ticker"] for trade in trades}):
        bars = data_repo.read_bars(conn, ticker, session, cutoff_at=cutoff_at)
        if bars:
            closes[ticker] = Decimal(str(bars[-1]["close"]))
        splits[ticker] = data_repo.split_events(conn, ticker, cutoff_at=cutoff_at)
    return ledger(cash_flows=flows, trades=trades, closes=closes, splits=splits)


def weekly_view(conn, *, week_end: date, cutoff_at: datetime, cfg: AssistantConfig) -> dict:
    """一周的复盘汇总（spec §5.4）。账本快照不重用 ``facts.account.account_view``：它不按
    session 过滤成交，两个快照的已实现盈亏恒相等，给不出「本周已实现盈亏」（§5.4）。"""
    week_start = week_bounds(week_end)[0]
    all_records = records(conn, session=week_end, cutoff_at=cutoff_at, cfg=cfg)
    # 周初快照：收盘价取本周首个交易日的前一个收盘，出入金与成交只算本周之前（含周末）
    start_ledger = _asof_ledger(
        conn, session=add_trading_days(week_start, -1), through=week_start - timedelta(days=1), cutoff_at=cutoff_at
    )
    end_ledger = _asof_ledger(conn, session=week_end, through=week_end, cutoff_at=cutoff_at)
    candidates_week: list[dict] = []
    for day in trading_days_between(week_start, week_end):
        run = facts_repo.previous_ok_run(conn, before=add_trading_days(day, 1))
        if run is not None and run["session"] == day:  # 当天有 ok 运行才取它的名单
            candidates_week.extend(facts_repo.candidates(conn, run["run_id"]))
    return weekly_summary(
        records=all_records,
        week_start=week_start,
        week_end=week_end,
        equity_start=start_ledger["equity"],
        equity_end=end_ledger["equity"],
        realized_pnl=end_ledger["realized_pnl"] - start_ledger["realized_pnl"],
        candidates_week=candidates_week,
    )


def retrospect_view(
    conn, *, session: date, cutoff_at: datetime, cfg: AssistantConfig, limit: int = 20, week_end: date | None = None
) -> dict:
    """页面复盘块的全部输入：最近 ``limit`` 份记录、本周（或 ``week_end`` 所在周）的汇总与
    规则检验两张表（spec §5.5）。规则检验用全部记录，不受 ``limit``。"""
    all_records = records(conn, session=session, cutoff_at=cutoff_at, cfg=cfg)
    return {
        "records": all_records if limit is None else all_records[:limit],
        "weekly": weekly_view(
            conn, week_end=week_end or week_bounds(session)[1], cutoff_at=cutoff_at, cfg=cfg
        ),
        "rules": {
            "realized": {
                key: rule_stats(all_records, key=key, realized=True, min_sample=cfg.v3.retro_min_sample)
                for key in RULE_KEYS
            },
            "hypothetical": {
                key: rule_stats(all_records, key=key, realized=False, min_sample=cfg.v3.retro_min_sample)
                for key in RULE_KEYS
            },
        },
        "empty": not all_records,
        "min_sample": cfg.v3.retro_min_sample,
        "note": "规则未经验证；样本不足的组不给数",
    }
