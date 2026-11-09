"""Retrospect pure functions (v3.2 spec §5): the validity window, deviation tags, the
hypothetical 「如果照做」simulation, one record per ended plan, week-end detection, the
weekly summary and the rule statistics.

No SQL, no facts imports — plan/event/trade/bar/candidate rows arrive as plain dicts in
the shapes ``facts.plan_repo`` / ``facts.repo`` / ``data.repo`` return: bars carry
``price_date`` and Decimal prices, ``executed_at`` is timezone-aware New York time.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from zoneinfo import ZoneInfo

from investment_assistant.core.texts import quantity_value
from investment_assistant.data.calendar import add_trading_days, trading_days_between
from investment_assistant.execution.plan_levels import _cent
from investment_assistant.execution.plan_line import ENDED, held_sessions, holding_check

NY = ZoneInfo("America/New_York")

DEVIATIONS = (
    "not_bought_in_zone",
    "entry_out_of_zone",
    "over_bought",
    "stop_order_missing",
    "late_sell_after_stop",
    "late_sell_after_instruction",
    "early_sell",
)
NOT_FILLED = "not_filled"
TAG_TEXT = {
    "not_bought_in_zone": "未买入：价格进过入场区间",
    NOT_FILLED: "未买入：价格未进入场区间",
    "entry_out_of_zone": "买价出区间",
    "over_bought": "多买",
    "stop_order_missing": "止损未挂",
    "late_sell_after_stop": "止损触及后晚卖 {n} 个交易日",
    "late_sell_after_instruction": "卖出指令后晚卖 {n} 个交易日",
    "early_sell": "早卖",
}
FOLLOWED_TEXT = "照做"


def _tag(code: str, sessions: int | None = None) -> dict:
    return {"code": code, "text": TAG_TEXT[code].format(n=sessions), "sessions": sessions}


def _unvoided(trades: list[dict], side: str) -> list[dict]:
    return [trade for trade in trades if trade.get("side") == side and trade.get("voided_at") is None]


def _ordered(trades: list[dict], side: str) -> list[dict]:
    return sorted(_unvoided(trades, side), key=lambda trade: (trade["executed_at"], str(trade["id"])))


def _ny_date(value: datetime) -> date:
    return value.astimezone(NY).date()


def _decimal_or_none(value) -> Decimal | None:
    """仓储的 jsonb 编码把 Decimal 存成字符串（core.dbutil._plan_json），回读后归一回 Decimal；
    None 原样返回，聚合（weekly_summary / rule_stats）只见到 Decimal。"""
    if value is None or isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def validity_window(plan: dict) -> tuple[date, date]:
    """保存日的下一个交易日到最终 ``valid_until``（延续会覆盖 ``valid_from``，审阅记录 R2）。"""
    return add_trading_days(plan["session"], 1), plan["valid_until"]


def touched_zone(plan: dict, bars: list[dict]) -> bool:
    """有效期窗口内是否有任何一个交易日的日线进过入场区间（``low ≤ entry_high`` 且 ``high ≥ entry_low``）。"""
    start, end = validity_window(plan)
    return any(
        start <= bar["price_date"] <= end and bar["low"] <= plan["entry_high"] and bar["high"] >= plan["entry_low"]
        for bar in bars
    )


def _deviation_reason(events: list[dict], keyword: str) -> bool:
    return any(event["event"] == "deviation" and keyword in event.get("reason", "") for event in events)


def _instruction_delay(events: list[dict], sells: list[dict]) -> int | None:
    """卖出指令（交易日 X）后晚卖的交易日数：Y 晚于 X 的下一个交易日才算，N = X 后第 2 个交易日起到 Y。"""
    instructions = [event["session"] for event in events if event["event"] == "sell_instruction"]
    if not instructions or not sells:
        return None
    x, y = max(instructions), _ny_date(sells[-1]["executed_at"])
    if y <= add_trading_days(x, 1):
        return None
    sessions = len(trading_days_between(add_trading_days(x, 2), y))
    return sessions if sessions >= 1 else None


def deviation_tags(*, plan: dict, events: list[dict], trades: list[dict], bars: list[dict]) -> list[dict]:
    """一份计划的偏差标签（spec §5.2 逐条）；补录（``kind = adopted``）不打标签；
    ``not_filled`` 不算偏差、算「照做」。"""
    if plan.get("kind") == "adopted":
        return []
    buys, sells = _ordered(trades, "buy"), _ordered(trades, "sell")
    outcome = plan.get("outcome") or {}
    tags: list[dict] = []
    pending_terminal = plan["status"] in ENDED and plan["status"] != "closed"
    if pending_terminal and not buys:
        touched = touched_zone(plan, bars)
        if plan["status"] == "expired" and touched:
            tags.append(_tag("not_bought_in_zone"))  # 替换 / 取消 / 作废不打：结束不是用户的决定
        elif not touched:
            tags.append(_tag(NOT_FILLED))
    if buys and (outcome.get("entry_in_zone") is False or _deviation_reason(events, "不在入场区间")):
        tags.append(_tag("entry_out_of_zone"))
    if _deviation_reason(events, "多于计划"):
        tags.append(_tag("over_bought"))
    if buys and not any(event["event"] == "stop_order_confirmed" for event in events):
        tags.append(_tag("stop_order_missing"))
    if (outcome.get("stop_delay_sessions") or 0) >= 1:
        tags.append(_tag("late_sell_after_stop", outcome["stop_delay_sessions"]))
    instruction_delay = _instruction_delay(events, sells)
    if instruction_delay is not None:
        tags.append(_tag("late_sell_after_instruction", instruction_delay))
    if outcome.get("exit_reason") == "manual" and not any(
        event["event"] in ("stop_hit", "sell_instruction") for event in events
    ):
        tags.append(_tag("early_sell"))
    return tags


def hypothetical(*, plan: dict, bars: list[dict], trail_atr: Decimal) -> dict:
    """「如果照做会怎样」（spec §5.3）：有效期窗口内按 ``low ≤ entry_high`` 成交（价
    ``min(open, entry_high)``），收盘跌破止损先于成交 → 未成交；成交后逐日用
    ``plan_line.holding_check`` 推演，``stop_hit`` 当天按 ``min(open, 当日生效止损)`` 离场，
    ``sell_instruction`` 下一根日线开盘离场；日线用完 → 进行中，按最后一根收盘计浮动 R。
    替换 / 取消 / 否决与财报作废一概不理——假设的意思就是这份计划没被动过。"""
    result: dict = {
        "status": None,
        "reason": None,
        "fill_session": None,
        "fill_price": None,
        "exit_session": None,
        "exit_price": None,
        "exit_reason": None,
        "sessions_held": None,
        "r_multiple": None,
    }
    start, end = validity_window(plan)
    ordered = sorted(bars, key=lambda bar: bar["price_date"])
    fill_index: int | None = None
    for index, bar in enumerate(ordered):
        if bar["price_date"] < start:
            continue
        if bar["price_date"] > end:
            break
        if bar["low"] <= plan["entry_high"]:
            result["fill_session"], result["fill_price"] = bar["price_date"], min(bar["open"], plan["entry_high"])
            fill_index = index
            break
        if plan.get("stop") is not None and bar["close"] < plan["stop"]:
            result["status"], result["reason"] = "not_filled", "close_below_stop"
            return result
    if fill_index is None:
        result["status"], result["reason"] = "not_filled", "never_in_zone"
        return result

    def finish(*, status, exit_session, exit_price, exit_reason, sessions) -> dict:
        result.update(
            status=status,
            exit_session=exit_session,
            exit_price=exit_price,
            exit_reason=exit_reason,
            sessions_held=sessions,
        )
        risk = result["fill_price"] - plan["initial_stop"]
        result["r_multiple"] = _cent((exit_price - result["fill_price"]) / risk) if risk else None
        return result

    sim = {
        "stop": plan["initial_stop"],
        "trailing": False,
        "sell_instruction": None,
        "start_session": result["fill_session"],
        "target1": plan.get("target1"),
        "atr": plan.get("atr"),
        "max_hold_sessions": plan["max_hold_sessions"],
    }
    highest_close = ordered[fill_index]["close"]
    pending_open_exit = False
    for bar in ordered[fill_index:]:
        day = bar["price_date"]
        held = held_sessions(result["fill_session"], day)
        highest_close = max(highest_close, bar["close"])  # 含当日收盘（与宿主复查同口径）
        if pending_open_exit:  # 昨天的卖出指令：今天开盘离场
            return finish(
                status="exited", exit_session=day, exit_price=bar["open"], exit_reason="max_hold", sessions=held
            )
        check = holding_check(
            plan=sim,
            bar=bar,
            session=day,
            highest_close=highest_close,
            avg_cost=result["fill_price"],
            held_sessions=held,
            trail_atr=trail_atr,
        )
        names = {event["event"] for event in check["events"]}
        if "stop_hit" in names:  # 离场价用今天生效的止损（今天的上移明天才生效）
            return finish(
                status="exited",
                exit_session=day,
                exit_price=min(bar["open"], sim["stop"]),
                exit_reason="trailing_stop" if sim["trailing"] else "initial_stop",
                sessions=held,
            )
        pending_open_exit = "sell_instruction" in names
        sim["stop"], sim["trailing"], sim["sell_instruction"] = (
            check["stop"],
            check["trailing"],
            check["sell_instruction"],
        )
    last = ordered[-1]  # 日线用完还没离场 → 进行中，按最后一根收盘算浮动 R
    return finish(
        status="unfinished",
        exit_session=last["price_date"],
        exit_price=last["close"],
        exit_reason=None,
        sessions=held_sessions(result["fill_session"], last["price_date"]),
    )


def plan_record(
    *, plan: dict, events: list[dict], trades: list[dict], bars: list[dict], candidate: dict | None, trail_atr: Decimal
) -> dict:
    """复盘一行的全部纯内存组装（spec §5.1）：计划说的 / 实际做的 / 结果 / 偏差标签 /
    假设结果（只对没买的 ``planned`` 计划）/ 分组键（规则名单取候选行，研究记「用户研究」）。"""
    buys, sells = _ordered(trades, "buy"), _ordered(trades, "sell")
    first_buy = (
        {"price": buys[0]["price"], "session": _ny_date(buys[0]["executed_at"]), "quantity": quantity_value(buys[0]["quantity"])}
        if buys
        else None
    )
    last_sell = {"price": sells[-1]["price"], "session": _ny_date(sells[-1]["executed_at"])} if sells else None
    tags = deviation_tags(plan=plan, events=events, trades=trades, bars=bars)
    followed = not [tag for tag in tags if tag["code"] != NOT_FILLED] and (
        bool(buys) or any(tag["code"] == NOT_FILLED for tag in tags)
    )
    outcome = plan.get("outcome") or {}
    if plan.get("kind") == "adopted":
        origin_kind = "adopted"
        groups = {"trigger_rules": [], "setup_tier": None, "industry": None}
    elif plan.get("research_id"):
        origin_kind = "research"
        groups = {"trigger_rules": ["用户研究"], "setup_tier": "用户研究", "industry": "用户研究"}
    else:
        origin_kind = "rule"
        groups = {
            "trigger_rules": list((candidate or {}).get("trigger_rules") or []),
            "setup_tier": (candidate or {}).get("setup_tier"),
            "industry": (candidate or {}).get("industry"),
        }
    simulation = (
        hypothetical(plan=plan, bars=bars, trail_atr=trail_atr)
        if plan["status"] != "closed" and plan["kind"] == "planned"
        else None
    )
    window_start, window_end = validity_window(plan)  # 「计划说的」用复盘窗口，不吃被延续覆盖的列值（R2）
    return {
        "plan_id": plan["id"],
        "ticker": plan["ticker"],
        "status": plan["status"],
        "kind": plan["kind"],
        "origin_kind": origin_kind,
        "session": plan["session"],
        "ended_session": plan.get("ended_session"),
        "end_reason": plan.get("end_reason"),
        "planned": {
            "entry_low": plan.get("entry_low"),
            "entry_high": plan.get("entry_high"),
            "initial_stop": plan.get("initial_stop"),
            "target1": plan.get("target1"),
            "valid_from": window_start,
            "valid_until": window_end,
            "shares": plan.get("shares"),
            "max_hold_sessions": plan.get("max_hold_sessions"),
        },
        "actual": {
            "first_buy": first_buy,
            "last_sell": last_sell,
            "quantity": sum((trade["quantity"] for trade in buys), Decimal(0)),
        },
        "outcome": outcome,
        "r_multiple": _decimal_or_none(outcome.get("r_multiple")),
        "r_text": outcome.get("r_text"),
        "tags": tags,
        "followed": followed,
        "hypothetical": simulation,
        "groups": groups,
    }


def is_week_end(session: date) -> bool:
    """当天的下一个交易日落在另一个 ISO 周 → 本周最后一个交易日（spec §5.4 的产出判定）。"""
    return add_trading_days(session, 1).isocalendar()[:2] != session.isocalendar()[:2]


def week_bounds(session: date) -> tuple[date, date]:
    return session - timedelta(days=session.weekday()), session


def _listing(candidates_week: list[dict], *, week_start: date, week_end: date) -> dict:
    """本周名单（spec §5.4）：新上榜 = 本周首次上榜当天 ``detail.streak`` 为 1；掉出 = 本周
    最后一个交易日不在榜；再次上榜最多 = 周内掉出又回来的次数最多（没有则 None）。周内
    的交易日历来自 data.calendar（而不是候选行的日期集）：整天空缺的日子也要算进出与回榜。"""
    rows = sorted(candidates_week or [], key=lambda row: (row["session"], row["ticker"]))
    if not rows:
        return {"new": [], "dropped": [], "most_relisted": None}
    sessions = trading_days_between(week_start, week_end)
    seen: dict[str, list[date]] = {}
    first_streak: dict[str, int] = {}
    for row in rows:
        ticker = row["ticker"]
        seen.setdefault(ticker, []).append(row["session"])
        if ticker not in first_streak:
            first_streak[ticker] = int((row.get("detail") or {}).get("streak") or 1)

    def relists(days: list[date]) -> int:
        return sum(1 for previous, current in pairwise(days) if any(previous < day < current for day in sessions))

    ranked = sorted(((relists(days), ticker) for ticker, days in seen.items()), key=lambda item: (-item[0], item[1]))
    return {
        "new": sorted(ticker for ticker, streak in first_streak.items() if streak == 1),
        "dropped": sorted(ticker for ticker, days in seen.items() if days[-1] < sessions[-1]),
        "most_relisted": ranked[0][1] if ranked and ranked[0][0] >= 1 else None,
    }


def weekly_summary(
    *,
    records: list[dict],
    week_start: date,
    week_end: date,
    equity_start: Decimal | None,
    equity_end: Decimal | None,
    realized_pnl: Decimal,
    candidates_week: list[dict],
) -> dict:
    """一周的复盘汇总（spec §5.4）：只统计 ``ended_session`` 落在本周的复盘记录；
    ``tag_counts`` 只数偏差标签（``not_filled`` 不算），键是带 N 的标签文案。"""
    week = [
        record
        for record in records
        if record.get("ended_session") is not None and week_start <= record["ended_session"] <= week_end
    ]
    tag_counts: dict[str, int] = {}
    for record in week:
        for tag in record.get("tags") or []:
            if tag["code"] != NOT_FILLED:
                tag_counts[tag["text"]] = tag_counts.get(tag["text"], 0) + 1
    closed = [
        {
            "plan_id": record["plan_id"],
            "ticker": record["ticker"],
            "r_multiple": record.get("r_multiple"),
            "exit_reason": (record.get("outcome") or {}).get("exit_reason"),
            "tags": [tag["text"] for tag in record.get("tags") or [] if tag["code"] != NOT_FILLED],
        }
        for record in week
        if record["status"] == "closed"
    ]
    return {
        "week_start": week_start,
        "week_end": week_end,
        "ended": len(week),
        "followed": sum(1 for record in week if record["followed"]),
        "deviated": sum(
            1 for record in week if any(tag["code"] in DEVIATIONS for tag in record.get("tags") or [])
        ),  # 有偏差 = 打了偏差标签，不等价于不照做（被替换且进过区间的未买计划两边都不是）
        "tag_counts": tag_counts,
        "closed": closed,
        "total_r": sum((row["r_multiple"] for row in closed if row["r_multiple"] is not None), Decimal(0)),
        "not_bought": [
            {
                "plan_id": record["plan_id"],
                "ticker": record["ticker"],
                "end_reason": record.get("end_reason"),
                "hypothetical": record.get("hypothetical"),
            }
            for record in week
            if record["status"] != "closed" and record["kind"] == "planned"
        ],
        "equity_start": equity_start,
        "equity_end": equity_end,
        "realized_pnl": realized_pnl,
        "listing": _listing(candidates_week, week_start=week_start, week_end=week_end),
    }


def rule_stats(records: list[dict], *, key: str, realized: bool, min_sample: int) -> list[dict]:
    """规则检验（spec §5.5）：按 ``trigger_rules`` / ``setup_tier`` / ``industry`` 分组；
    真实表用已平仓 ``planned`` 计划的 R，假设表用假设结果已离场的 R，两张表互不相加。
    样本不足门槛的组只给样本数（三项为 None、``insufficient`` 为真）。"""
    samples: dict[str, list[Decimal]] = {}
    for record in records:
        if realized:
            if not (record.get("status") == "closed" and record.get("kind") == "planned"):
                continue
            value = record.get("r_multiple")
        else:
            simulation = record.get("hypothetical") or {}
            if simulation.get("status") != "exited":
                continue
            value = simulation.get("r_multiple")
        if value is None:
            continue
        members = (record.get("groups") or {}).get(key) or []
        for group in members if isinstance(members, list) else [members]:
            samples.setdefault(str(group), []).append(value)
    stats = []
    for group, values in samples.items():
        sample = len(values)
        insufficient = sample < min_sample
        stats.append(
            {
                "group": group,
                "sample": sample,
                "win_rate": None if insufficient else Decimal(sum(1 for value in values if value > 0)) / sample,
                "avg_r": None if insufficient else sum(values, Decimal(0)) / sample,
                "total_r": None if insufficient else sum(values, Decimal(0)),
                "insufficient": insufficient,
            }
        )
    return sorted(stats, key=lambda row: (-row["sample"], row["group"]))
