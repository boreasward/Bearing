"""Plan-line pure functions (v3.1 spec §6–§8): pending review, holding checks, the trailing
stop, next-session actions, slot replacement, close outcomes and split adjustment.

No SQL, no facts imports — this module is the arithmetic core the host orchestration calls.
All prices are Decimal, rounded to cents via plan_levels._cent.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from investment_assistant.data.calendar import trading_days_between
from investment_assistant.execution.plan_levels import _cent, earnings_flags

ACTIVE = ("pending", "open")
ENDED = ("closed", "expired", "invalidated", "replaced", "cancelled")

# 五种「下一个交易日的操作」的固定文案（spec §7.4；stop_order 的「需上调」文案另拼）
NEXT_ACTION_TEXT = {
    "stop_hit_unresolved": "止损已触及，未见卖出记录",
    "sell_next_open": "下次开盘卖出",
    "unchecked": "缺日线，无法检查止损",
    "stop_order": "未确认止损单",
    "hold": "继续持有",
}
_SELL_REASON = {"max_hold": "持有到期", "thesis_broken": "论点失效"}


def pending_check(*, plan: dict, bar: dict | None, earnings: dict | None, session: date) -> dict | None:
    """The daily review of a pending plan (spec §6.3), first hit wins:

    1. close below stop (skipped when the day's bar is missing), 2. earnings entering the
    validity window, 3. expiry at valid_until. No hit -> None.
    """
    stop = plan["stop"]
    if bar is not None and stop is not None and bar["close"] < stop:
        return {"event": "invalidated", "reason": f"收盘 {bar['close']} 跌破止损 {stop}，未买入"}
    invalid, _ = earnings_flags(
        earnings, signal_date=session, valid_from=plan["valid_from"], valid_until=plan["valid_until"]
    )
    if "earnings_in_validity" in invalid:
        return {"event": "invalidated", "reason": "财报进入有效期"}
    if session >= plan["valid_until"]:
        sessions = len(trading_days_between(plan["valid_from"], plan["valid_until"]))
        return {"event": "expired", "reason": f"{sessions} 个交易日未买入"}
    return None


def holding_check(
    *,
    plan: dict,
    bar: dict | None,
    session: date,
    highest_close: Decimal | None,
    avg_cost: Decimal,
    held_sessions: int,
    trail_atr: Decimal,
) -> dict:
    """One trading day of the stop-loss discipline (spec §7.1). The incoming `plan` carries the
    stop that is in effect today (computed at yesterday's close); any raised stop applies from
    the next session. A missing bar marks the plan unchecked and does nothing else.
    """
    stop = plan["stop"]
    trailing = bool(plan["trailing"])
    sell_instruction = plan.get("sell_instruction")
    if bar is None:
        return {
            "events": [],
            "stop": stop,
            "trailing": trailing,
            "sell_instruction": sell_instruction,
            "unchecked": True,
        }
    events: list[dict] = []
    start_session = plan["start_session"]
    if stop is not None:
        if session == start_session:  # 起算日只看收盘（最低价可能在买入之前）
            hit, how = bar["close"] < stop, f"收盘 {bar['close']} 跌破止损 {stop}"
        else:
            hit, how = bar["low"] <= stop, f"最低 {bar['low']} 触及止损 {stop}"
        if hit:
            events.append({"event": "stop_hit", "reason": how, "before": {}, "after": {}})
        if (
            not trailing
            and session > start_session
            and plan.get("target1") is not None
            and bar["high"] >= plan["target1"]
        ):
            trailing = True
            events.append(
                {
                    "event": "target_hit",
                    "reason": f"最高 {bar['high']} 触及目标 {plan['target1']}",
                    "before": {},
                    "after": {"trailing": True},
                }
            )
        if trailing and highest_close is not None and plan.get("atr") is not None:
            new_stop = trailing_stop(
                current_stop=stop,
                highest_close=highest_close,
                atr=plan["atr"],
                trail_atr=trail_atr,
                avg_cost=avg_cost,
                last_close=bar["close"],
            )
            if new_stop > stop:  # 止损只上不下
                events.append(
                    {
                        "event": "stop_raised",
                        "reason": f"移动止损 {stop} → {new_stop}",
                        "before": {"stop": stop},
                        "after": {"stop": new_stop},
                    }
                )
                stop = new_stop
    if held_sessions >= plan["max_hold_sessions"] and not sell_instruction:
        sell_instruction = "max_hold"
        events.append(
            {"event": "sell_instruction", "reason": "持有到期", "before": {}, "after": {"sell_instruction": "max_hold"}}
        )
    return {
        "events": events,
        "stop": stop,
        "trailing": trailing,
        "sell_instruction": sell_instruction,
        "unchecked": False,
    }


def trailing_stop(
    *,
    current_stop: Decimal,
    highest_close: Decimal,
    atr: Decimal,
    trail_atr: Decimal,
    avg_cost: Decimal,
    last_close: Decimal,
) -> Decimal:
    """max(current stop, highest close − trail_atr × ATR, cost floor). The cost floor only
    applies below the last close — an adopted position whose cost is above price must never
    be pushed above the price (spec §7.1 / Review Focus 2)."""
    floor = avg_cost if avg_cost < last_close else current_stop
    return max(current_stop, _cent(highest_close - trail_atr * atr), floor)


def next_action(*, plan: dict, position: dict | None, unchecked: bool) -> dict:
    """The first applicable rule of spec §7.4 for one open position."""
    if plan.get("stop_hit_session") is not None and position is not None and position["quantity"] > 0:
        return {
            "kind": "stop_hit_unresolved",
            "text": NEXT_ACTION_TEXT["stop_hit_unresolved"],
            "stop": plan["stop"],
            "reason": None,
        }
    instruction = plan.get("sell_instruction")
    if instruction in _SELL_REASON:
        return {
            "kind": "sell_next_open",
            "text": NEXT_ACTION_TEXT["sell_next_open"],
            "stop": None,
            "reason": _SELL_REASON[instruction],
        }
    if unchecked:
        return {"kind": "unchecked", "text": NEXT_ACTION_TEXT["unchecked"], "stop": None, "reason": None}
    confirmed = plan.get("confirmed_stop_order")
    stop = plan["stop"]
    if confirmed is None:
        return {"kind": "stop_order", "text": NEXT_ACTION_TEXT["stop_order"], "stop": stop, "reason": None}
    if stop is not None and confirmed < stop:
        return {
            "kind": "stop_order",
            "text": f"止损单需上调至 {stop}（当前挂单 {confirmed}）",
            "stop": stop,
            "reason": None,
        }
    return {"kind": "hold", "text": NEXT_ACTION_TEXT["hold"], "stop": stop, "reason": None}


def replacement(
    *, top_rows: list[dict], pending: list[dict], active_tickers: set[str], replaced_today: bool
) -> dict | None:
    """The once-per-day replacement candidate (spec §6.4). Callers invoke this only when the
    pending cap is full (free_slots == 0); with no pending plan there is no victim, so None.

    The candidate is the best-ranked top-N row without an active plan; the victim is the
    pending plan with today's worst rank (not in the list counts as 10**6), ties broken by
    the earliest created_at. The candidate must rank strictly better than the victim.
    """
    if replaced_today or not pending:
        return None
    candidate = next(
        (row for row in sorted(top_rows, key=lambda row: row["rank"]) if row["ticker"] not in active_tickers), None
    )
    if candidate is None:
        return None
    rank_by_ticker = {row["ticker"]: row["rank"] for row in top_rows}

    def worst_first(plan_row: dict) -> tuple[int, object]:
        return (-rank_by_ticker.get(plan_row["ticker"], 10**6), plan_row["created_at"])

    victim = min(pending, key=worst_first)
    victim_rank = rank_by_ticker.get(victim["ticker"], 10**6)
    if candidate["rank"] >= victim_rank:
        return None
    return {"candidate": candidate["ticker"], "victim_plan_id": victim["id"], "victim_rank": victim_rank}


def free_slots(*, pending_count: int, cap: int) -> int:
    return max(0, cap - pending_count)


def close_outcome(
    *, plan: dict, closed_lots: list[dict], trades: list[dict], sessions_since_stop_hit: int | None
) -> dict:
    """The recorded outcome of a closed plan (spec §8.4): pnl, risk multiple, exit reason,
    stop discipline and whether the first buy was inside the entry zone."""
    quantity = sum((lot["quantity"] for lot in closed_lots), Decimal(0))
    cost = sum((lot["quantity"] * lot["entry_cost_per_share"] for lot in closed_lots), Decimal(0))
    realized = sum((lot["realized_pnl"] for lot in closed_lots), Decimal(0))
    if plan.get("kind") == "adopted":
        r_multiple, r_text = None, "补录，不计"
    else:
        risk_per_share = (
            (cost / quantity - plan["initial_stop"])
            if quantity and plan.get("initial_stop") is not None
            else Decimal(0)
        )
        r_multiple = (
            (realized / quantity / risk_per_share).quantize(Decimal("0.01"), ROUND_HALF_UP) if risk_per_share else None
        )
        r_text = None
    if plan.get("stop_hit_session") is not None:
        exit_reason = "trailing_stop" if plan.get("trailing") else "initial_stop"
    elif plan.get("sell_instruction") in ("max_hold", "thesis_broken"):
        exit_reason = plan["sell_instruction"]
    else:
        exit_reason = "manual"
    buys = sorted(
        (t for t in trades if t.get("side") == "buy" and t.get("voided_at") is None),
        key=lambda t: (t["executed_at"], str(t["id"])),
    )
    first_price = buys[0]["price"] if buys else None
    entry_in_zone: bool | None = None
    if first_price is not None and plan.get("entry_low") is not None and plan.get("entry_high") is not None:
        entry_in_zone = plan["entry_low"] <= first_price <= plan["entry_high"]
    return {
        "realized_pnl": realized,
        "realized_pct": realized / cost if cost else None,
        "r_multiple": r_multiple,
        "r_text": r_text,
        "exit_reason": exit_reason,
        "stop_delay_sessions": sessions_since_stop_hit if plan.get("stop_hit_session") is not None else None,
        "entry_in_zone": entry_in_zone,
    }


def split_adjust(plan: dict, factor: Decimal) -> tuple[dict, dict]:
    """Adjust a plan's levels for a split (spec §7.1): prices / factor rounded to cents, ATR /
    factor, shares × factor. Returns (before, after) with only the adjusted keys; the plan
    dict itself is never mutated."""
    price_keys = ("entry_low", "entry_high", "stop", "target1", "initial_stop", "confirmed_stop_order")
    before: dict = {}
    after: dict = {}
    for key, value in plan.items():
        if key in price_keys and value is not None:
            before[key], after[key] = value, _cent(value / factor)
        elif key == "atr" and value is not None:
            before[key], after[key] = value, value / factor
        elif key == "shares" and value is not None:
            before[key], after[key] = value, value * factor
    return before, after


def held_sessions(start_session: date, session: date) -> int:
    """Trading sessions strictly after the start session through `session`."""
    return len(trading_days_between(start_session + timedelta(days=1), session))


def deviations(*, plan: dict, price: Decimal, quantity: Decimal) -> list[str]:
    """One sentence per recorded-trade deviation from the plan (spec §6.5)."""
    out: list[str] = []
    low, high = plan.get("entry_low"), plan.get("entry_high")
    if low is not None and high is not None and not low <= price <= high:
        out.append(f"成交价 {price} 不在入场区间 {low}–{high}")
    shares = plan.get("shares")
    if shares is not None and quantity > shares:
        out.append(f"买入 {quantity} 股，多于计划的 {shares} 股")
    return out
