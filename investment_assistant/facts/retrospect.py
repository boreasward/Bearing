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

BARS_LIMIT = 400  # 单次读的日线根数上限；按计划各自窗口锚定后，窗口+持有尾巴远小于它（F6）
RULE_KEYS = ("trigger_rules", "setup_tier", "industry")


def _attributed_trades(trades: list[dict], plan: dict, events: list[dict]) -> list[dict]:
    """该计划自己的成交（spec §2 反目标「复盘每一行必须与该计划记录对得上」）：带
    ``plan_id`` 的直连那份计划；否则成交的 ``request_id`` 写进了哪份计划事件的
    ``refs``（买入 / 加仓 / 卖出推进事件都带）就归那份；两样都对不上（比如晚于该计划
    的手工补录）的不进任何复盘行——它们仍算进账户视图（``_asof_ledger``）。"""
    request_ids = {ref for event in events for ref in (event.get("refs") or [])}
    return [trade for trade in trades if trade.get("plan_id") == plan["id"] or trade.get("request_id") in request_ids]


def _bar_end(plan: dict, session: date) -> date:
    """每份计划的日线读到它自己的有效期末尾加持有尾巴，不晚于复盘锚定的交易日：成交可
    落在有效期最后一日，最多再持 ``max_hold_sessions`` 个交易日、卖出指令次日开盘离场，
    再留一天余量。老计划的复盘结果不随后续日线漂移（F6）。"""
    tail = add_trading_days(plan["valid_until"], int(plan["max_hold_sessions"] or 0) + 2)
    return min(tail, session)


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
    """每份已结束计划一行（spec §5.1）：日线按各计划自己的窗口读到（F6）、候选行取保存
    当天、trail_atr 取配置；成交按 F1 的归属规则取各计划自己的。"""
    trail_atr = Decimal(str(cfg.v3.trail_atr))
    out: list[dict] = []
    trades_by_ticker: dict[str, list[dict]] = {}
    for plan in plan_repo.plans_by_status(conn, ENDED, limit=limit):
        ticker = plan["ticker"]
        if ticker not in trades_by_ticker:
            trades_by_ticker[ticker] = facts_repo.list_trades(conn, ticker)
        events = plan_repo.events(conn, plan["id"])
        bars = _decimal_bars(
            data_repo.read_bars(conn, ticker, _bar_end(plan, session), cutoff_at=cutoff_at, limit=BARS_LIMIT)
        )
        out.append(
            plan_record(
                plan=plan,
                events=events,
                trades=_attributed_trades(trades_by_ticker[ticker], plan, events),
                bars=bars,
                candidate=facts_repo.candidate_at(conn, plan["session"], ticker),
                trail_atr=trail_atr,
            )
        )
    return out


def _asof_ledger(conn, *, session: date, through: date, cutoff_at: datetime) -> dict:
    """该交易日收盘口径的账本快照：收盘价读到 ``session``，出入金、成交与拆股都只算纽约
    日历日在 ``through``（含）之前生效的（周内拆股不回灌进周初快照）；收盘价照 ``facts.account.account_view``
    的同一读法。"""
    trades = [trade for trade in facts_repo.list_trades(conn) if trade["executed_at"].astimezone(NY).date() <= through]
    flows = [flow for flow in plan_repo.cash_flows(conn) if flow["effective_at"].astimezone(NY).date() <= through]
    closes: dict[str, Decimal] = {}
    splits: dict[str, list] = {}
    for ticker in sorted({trade["ticker"] for trade in trades}):
        bars = data_repo.read_bars(conn, ticker, session, cutoff_at=cutoff_at)
        if bars:
            closes[ticker] = Decimal(str(bars[-1]["close"]))
        splits[ticker] = [
            split for split in data_repo.split_events(conn, ticker, cutoff_at=cutoff_at) if split[0] <= through
        ]
    return ledger(cash_flows=flows, trades=trades, closes=closes, splits=splits)


def weekly_view(conn, *, week_end: date, cutoff_at: datetime, cfg: AssistantConfig) -> dict:
    """一周的复盘汇总（spec §5.4）。账本快照不重用 ``facts.account.account_view``：它不按
    session 过滤成交，两个快照的已实现盈亏恒相等，给不出「本周已实现盈亏」（§5.4）。"""
    week_start = week_bounds(week_end)[0]
    all_records = records(conn, session=week_end, cutoff_at=cutoff_at, cfg=cfg)
    # 周初快照截在「本周首个实际交易日之前」（周一休市时首日是周二）：出入金与成交算到它
    # 的前一日（含周末与休市日），收盘取它前一个交易日的收盘（spec §5.4）。
    first_session = trading_days_between(week_start, week_end)[0]
    start_ledger = _asof_ledger(
        conn,
        session=add_trading_days(first_session, -1),
        through=first_session - timedelta(days=1),
        cutoff_at=cutoff_at,
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


def week_end_of(day: date) -> date:
    """任一日期归一到它所在日历周（ISO 周）的最后一个交易日（F8）：周一休市的周（如
    总统日）传周一不再空周崩 500；整周没有交易日（现行日历下不会发生）拒
    ``invalid_argument``，接口映射 400，绝不 500。"""
    monday = day - timedelta(days=day.weekday())
    days = trading_days_between(monday, monday + timedelta(days=6))
    if not days:
        raise ValueError(f"invalid_argument: {day:%Y-%m-%d} 所在的日历周没有交易日")
    return days[-1]


def retrospect_view(
    conn, *, session: date, cutoff_at: datetime, cfg: AssistantConfig, limit: int = 20, week_end: date | None = None
) -> dict:
    """页面复盘块的全部输入：最近 ``limit`` 份记录、本周（或 ``week_end`` 所在周）的汇总与
    规则检验两张表（spec §5.5）。规则检验用全部记录，不受 ``limit``。显式传的 ``week_end``
    先经 ``week_end_of`` 归一；缺省周锚定当前交易日（周中即周中，不补到周五）。"""
    all_records = records(conn, session=session, cutoff_at=cutoff_at, cfg=cfg)
    return {
        "records": all_records if limit is None else all_records[:limit],
        "weekly": weekly_view(
            conn,
            week_end=week_end_of(week_end) if week_end is not None else week_bounds(session)[1],
            cutoff_at=cutoff_at,
            cfg=cfg,
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
