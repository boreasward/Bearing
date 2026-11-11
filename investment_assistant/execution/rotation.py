"""v3.3 rotation pure functions (spec §5, §6, §9): position exposure, the short-of-room test,
holding eligibility, sale numbers, the cooldown window, the fee estimate, the after-the-fact
comparison of the two stocks, execution state and the summary.

No SQL, no facts imports. Plan / trade / bar / event rows arrive as plain dicts in the shapes
the repositories return; money is Decimal, rounded to cents via plan_levels._cent.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from zoneinfo import ZoneInfo

from investment_assistant.data.calendar import trading_days_between
from investment_assistant.execution.plan_levels import _cent

NY = ZoneInfo("America/New_York")

VERDICTS = ("hold", "half", "full")
VERDICT_TEXT = {"hold": "继续持有", "half": "半仓换", "full": "全仓换"}
NO_ROOM_TEXT = "仓位已到上限"
HOLD_REJECT_TEXT = "仓位已到上限，比较后继续持有"
DRAFT_TEXT = "仓位已到上限，换仓比较未做"
UNFINISHED_TEXT = "换仓比较未完成"
BLOCKED_TEXT = {
    "no_holding": "仓位已到上限，没有可比的持仓",
    "compared_today": "仓位已到上限，当天已比较过一次",
    "cooldown": "仓位已到上限，冷却期内（上次换仓 {last:%m-%d}）",
    "incomplete": "有持仓缺日线，仓位占比偏低，未做换仓比较",
}
INELIGIBLE_TEXT = {
    "stop_hit": "止损已触及",
    "sell_instruction": "已有卖出指令",
    "unchecked": "缺日线",
    "review_broken": "当天复查为不成立",
    "review_missing": "当天还没有复查结论",
}
ENDED_TEXT = {"expired": "已过期", "invalidated": "已作废", "replaced": "被替换", "cancelled": "已取消"}
# 比较表允许 agent 逐只读取的单只事实工具（spec §6.3）
SINGLE_READS = ("get_price_bars", "get_indicators", "get_news", "get_filings", "get_earnings", "get_fundamentals")

_INCOMPLETE_NOTE = "（有持仓缺日线，占比偏低）"
_FOUR = Decimal("0.0001")


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _percent(value: Decimal) -> str:
    """0.632 → 「63.2」：一位小数，四舍五入（不用银行家舍入）。"""
    return f"{(_dec(value) * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP):f}"


def _cap_percent(cap: Decimal) -> str:
    """0.6 → 「60」：上限去掉尾零。"""
    return f"{(_dec(cap) * 100).normalize():f}"


def exposure(*, equity: Decimal | None, market_value: Decimal, cap: Decimal, missing_close: bool = False) -> dict:
    """仓位占比与剩余额度（spec §5.1）。总资产为空或不为正时占比与额度为空，不除零；
    有持仓缺最新收盘时 ``incomplete`` 为真（市值被低估）。"""
    cap = _dec(cap)
    if equity is None or _dec(equity) <= 0:
        return {"pct": None, "cap": cap, "room": None, "incomplete": missing_close}
    equity, market_value = _dec(equity), _dec(market_value)
    return {
        "pct": (market_value / equity).quantize(_FOUR, rounding=ROUND_HALF_UP),
        "cap": cap,
        "room": max(Decimal(0), _cent(equity * cap - market_value)),
        "incomplete": missing_close,
    }


def exposure_text(row: dict) -> str:
    if row["pct"] is None:
        text = "仓位占比 —（未提供账户资金）"
    else:
        text = f"仓位占比 {_percent(row['pct'])}%（上限 {_cap_percent(row['cap'])}%），剩余额度 ${row['room']:f}"
    return text + _INCOMPLETE_NOTE if row.get("incomplete") else text


def short_of_room(sizing: dict, *, min_fill: Decimal) -> bool:
    """「钱不够」（spec §5.2）：额度内的股数（负数记 0）不到不受总仓位限制股数的 ``min_fill`` 倍，
    且后者大于 0。没有资金记录不判。"""
    if sizing.get("status") == "no_equity":
        return False
    uncapped = sizing.get("uncapped_shares")
    if uncapped is None or uncapped <= 0:
        return False
    return max(sizing["shares"], 0) < _dec(min_fill) * uncapped


def ineligible_reason(*, plan: dict, unchecked: bool, review: dict | None, require_review: bool) -> str | None:
    """持仓不可比的第一个原因代码（spec §6.2）；``review_missing`` 只在要求复查结论时成立。"""
    if plan.get("stop_hit_session") is not None:
        return "stop_hit"
    if plan.get("sell_instruction"):
        return "sell_instruction"
    if unchecked:
        return "unchecked"
    if review is not None and review.get("verdict") == "broken":
        return "review_broken"
    if review is None and require_review:
        return "review_missing"
    return None


def sell_quantity(verdict: str, quantity: Decimal) -> Decimal:
    """半仓 = 持仓 ÷ 2 向下取整到整数（小于 1 拒绝）；全仓 = 全部；继续持有没有卖出。"""
    quantity = _dec(quantity)
    if verdict == "half":
        half = Decimal(int(quantity / 2))
        if half < 1:
            raise ValueError("half_needs_two_shares")
        return half
    if verdict == "full":
        return quantity
    raise ValueError(f"no sale for verdict {verdict!r}")


def cooldown_last(rotations: list[dict], *, session: date, cooldown_sessions: int) -> date | None:
    """仍在冷却期内的最近一次半仓 / 全仓的交易日：R 到 ``session`` 两端都算，不超过
    ``cooldown_sessions`` 个交易日。"""
    inside = [
        row["session"]
        for row in rotations
        if row["verdict"] in ("half", "full")
        and row["session"] <= session
        and len(trading_days_between(row["session"], session)) <= cooldown_sessions
    ]
    return max(inside) if inside else None


def fee_estimate(trades: list[dict], *, lookback: int) -> dict:
    """手续费估计（spec §6.3）：最近 ``lookback`` 笔未作废成交费用的中位数，一来一回乘 2。"""
    live = sorted((t for t in trades if t.get("voided_at") is None), key=lambda t: t["executed_at"])[-lookback:]
    if not live:
        return {"per_trade": Decimal(0), "total": Decimal(0), "sample": 0, "note": "没有费用记录，按 0 计"}
    per_trade = _cent(Decimal(median(_dec(t["fee"]) for t in live)))
    return {"per_trade": per_trade, "total": per_trade * 2, "sample": len(live), "note": None}


def sale(*, quantity: Decimal, avg_cost: Decimal, last_close: Decimal, verdict: str) -> dict | None:
    """按最新收盘假设卖出的数字；半仓不够 2 股返回 ``None``。"""
    try:
        sold = sell_quantity(verdict, quantity)
    except ValueError:
        return None
    return {
        "sell_quantity": sold,
        "proceeds": _cent(sold * _dec(last_close)),
        "realized_pnl": _cent(sold * (_dec(last_close) - _dec(avg_cost))),
    }


def over_cap_sentence(pct: Decimal, cap: Decimal) -> str:
    return f"买入后仓位占比 {_percent(pct)}%，高于上限 {_cap_percent(cap)}%"


def _after(bars: list[dict], session: date) -> list[dict]:
    return sorted((bar for bar in bars if bar["price_date"] > session), key=lambda bar: bar["price_date"])


def _returns(bars: list[dict], horizons: tuple[int, ...]) -> dict[int, Decimal | None]:
    """涨跌幅 = adj_close[k] ÷ (open[1] × adj_close[1] ÷ close[1]) − 1；入场价用复权后的第一根开盘。"""
    if not bars:
        return {k: None for k in horizons}
    first = bars[0]
    entry = _dec(first["open"]) * _dec(first["adj_close"]) / _dec(first["close"])
    out: dict[int, Decimal | None] = {}
    for k in horizons:
        pct = _dec(bars[k - 1]["adj_close"]) / entry - 1 if len(bars) >= k else None
        out[k] = None if pct is None else pct.quantize(_FOUR, rounding=ROUND_HALF_UP)
    return out


def compare_returns(*, bars_a: list[dict], bars_b: list[dict], session: date, horizons: tuple[int, ...]) -> list[dict]:
    """结论之后 A 与 B 各自的涨跌幅与差（spec §9）：从结论的下一个交易日开盘起，到第 k 个交易日收盘；
    日线不够 k 根写 ``unfinished``。"""
    a_pct = _returns(_after(bars_a, session), horizons)
    b_pct = _returns(_after(bars_b, session), horizons)
    rows = []
    for k in horizons:
        if a_pct[k] is None or b_pct[k] is None:
            rows.append({"horizon": k, "status": "unfinished", "a_pct": None, "b_pct": None, "diff": None})
        else:
            rows.append(
                {"horizon": k, "status": "done", "a_pct": a_pct[k], "b_pct": b_pct[k], "diff": b_pct[k] - a_pct[k]}
            )
    return rows


def _event_rotation_id(event: dict):
    for source in (event, event.get("before") or {}, event.get("after") or {}):
        if source.get("rotation_id") is not None:
            return source["rotation_id"]
    return None


def execution_state(*, rotation: dict, events_a: list[dict], sells_a: list[dict], plan_b: dict | None) -> dict:
    """换仓指令的执行情况（spec §9）：撤回 / 被取代的事件优先，否则累计卖出达到指令股数为 ``done``。"""
    sold = sum((_dec(sell["quantity"]) for sell in sells_a), Decimal(0))
    ordered = sorted(sells_a, key=lambda sell: sell["executed_at"])
    sold_session = ordered[-1]["executed_at"].astimezone(NY).date() if ordered else None
    state = "pending"
    for event in events_a:
        if _event_rotation_id(event) != rotation["id"]:
            continue
        if event["event"] == "rotation_withdrawn":
            state = "withdrawn"
        elif event["event"] == "rotation_superseded":
            state = "superseded"
    if state == "pending" and sold >= _dec(rotation["sell_quantity"]):
        state = "done"
    return {
        "state": state,
        "sold_quantity": sold,
        "sold_session": sold_session,
        "bought": bool(plan_b) and plan_b.get("status") in ("open", "closed"),
    }


def _group(records: list[dict], *, min_sample: int) -> dict:
    """对照分组：只数最后一个时点已有结果的记录；B 跑赢 A = 差值 > 0。"""
    diffs = []
    for record in records:
        returns = record.get("returns") or []
        if returns and returns[-1]["status"] == "done":
            diffs.append(returns[-1]["diff"])
    sample = len(diffs)
    enough = sample >= min_sample and sample > 0
    return {
        "sample": sample,
        "enough": enough,
        "b_win_rate": (Decimal(sum(1 for d in diffs if d > 0)) / sample).quantize(_FOUR, rounding=ROUND_HALF_UP)
        if enough
        else None,
        "mean_diff": (sum(diffs, Decimal(0)) / sample).quantize(_FOUR, rounding=ROUND_HALF_UP) if enough else None,
    }


def rotation_summary(records: list[dict], *, min_sample: int) -> dict:
    """换仓复盘汇总（spec §9）。每条记录：``verdict``、``state``（半仓 / 全仓的执行状态，继续持有没有）、
    ``fee``（这一条的手续费估计合计）、``returns``（``compare_returns`` 的结果）。"""
    counts = {verdict: sum(1 for record in records if record["verdict"] == verdict) for verdict in VERDICTS}
    switched = [record for record in records if record["verdict"] in ("half", "full")]
    return {
        "counts": counts,
        "executed": sum(1 for record in switched if record.get("state") == "done"),
        "fee_total": sum(
            (_dec(record.get("fee") or 0) for record in records if record.get("state") == "done"), Decimal(0)
        ),
        "switched": _group(switched, min_sample=min_sample),
        "held": _group([record for record in records if record["verdict"] == "hold"], min_sample=min_sample),
    }
