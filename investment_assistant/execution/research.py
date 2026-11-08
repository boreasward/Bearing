"""Research-condition pure functions (v3.2 spec §6.3–§6.4): the four condition kinds a
watching research can carry, their Chinese text, the met check against daily bars and the
one-day review that yields `triggered` / `expired` events.

No SQL, no facts imports — research rows arrive as plain dicts (the shape
``facts.research_repo`` returns; ``condition`` is its stored JSON) and bars are daily-bar
dicts with ``price_date``/``open``/``high``/``low``/``close``, the last one being the day
under review.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from investment_assistant.core.texts import price_text
from investment_assistant.data.calendar import trading_days_between

CONDITION_KINDS = ("close_above", "close_below", "touch_below", "close_above_ma")
MA_CHOICES = (20, 50, 200)
RESEARCH_STATUS_TEXT = {
    "watching": "观察中",
    "planned": "已转计划",
    "rejected": "已否决",
    "expired": "已过期",
    "dropped": "已放弃",
}
VERDICTS = ("ready", "watch", "reject")


def validate_condition(raw: Any) -> dict:
    """Normalize a stored/user condition into ``{"kind", "level": Decimal | None, "ma": int | None}``;
    anything else is ``ValueError("invalid_condition: …")`` (level must be positive, ma one of MA_CHOICES)."""
    if not isinstance(raw, dict):
        raise ValueError("invalid_condition: 条件必须是对象")  # noqa: TRY004 - 工具面错误码约定用 ValueError（同 toolsurface.validation）
    kind = raw.get("kind")
    if kind not in CONDITION_KINDS:
        raise ValueError(f"invalid_condition: 未知条件 {kind}")
    if kind == "close_above_ma":
        ma = raw.get("ma")
        if isinstance(ma, bool) or not isinstance(ma, int) or ma not in MA_CHOICES:
            raise ValueError(f"invalid_condition: ma 必须是 {MA_CHOICES} 之一")
        return {"kind": kind, "level": None, "ma": ma}
    level = raw.get("level")
    try:
        converted = Decimal(str(level))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"invalid_condition: {kind} 需要正数 level") from None
    if not converted.is_finite() or converted <= 0:  # NaN / Infinity 不是有限正数
        raise ValueError(f"invalid_condition: {kind} 需要正数 level")
    return {"kind": kind, "level": converted, "ma": None}


def condition_text(condition: dict) -> str:
    """The four condition sentences verbatim (spec §6.3); level goes through ``price_text``."""
    kind = condition["kind"]
    if kind == "close_above_ma":
        return f"收盘站上 {condition['ma']} 日均线"
    level = price_text(condition["level"])
    if kind == "close_above":
        return f"收盘站上 {level}"
    if kind == "close_below":
        return f"收盘跌破 {level}"
    return f"最低价触及 {level} 或以下"


def _ma(bars: list[dict], n: int) -> Decimal:
    closes = [Decimal(str(bar["close"])) for bar in bars[-n:]]
    return sum(closes, Decimal(0)) / n


def condition_met(condition: dict, bars: list[dict]) -> bool | None:
    """Whether the last bar (the day under review) meets the condition; ``close_above_ma``
    needs ``ma`` bars and answers None when the history is short (spec §6.3 表)."""
    if not bars:
        return None
    bar = bars[-1]
    close = Decimal(str(bar["close"]))
    kind = condition["kind"]
    if kind == "close_above":
        return close >= Decimal(str(condition["level"]))
    if kind == "close_below":
        return close <= Decimal(str(condition["level"]))
    if kind == "touch_below":
        return Decimal(str(bar["low"])) <= Decimal(str(condition["level"]))
    n = int(condition["ma"])
    if len(bars) < n:
        return None
    return close >= _ma(bars, n)


def research_check(*, research: dict, bar: dict | None, bars: list[dict], session: date) -> dict | None:
    """One trading day of the daily research review (spec §6.4): a missing bar checks
    nothing; a met condition yields ``triggered``; otherwise expiry at ``valid_until``
    counts the trading days since the research was created. A research already waiting for
    re-review (``triggered_session`` set) neither re-triggers nor expires."""
    if bar is None:
        return None
    if research.get("triggered_session") is not None:
        return None
    condition = research.get("condition")
    if condition:
        normalized = validate_condition(condition)
        if condition_met(normalized, bars):
            detail = (
                f"最低 {price_text(bar['low'])}"
                if normalized["kind"] == "touch_below"
                else f"收盘 {price_text(bar['close'])}"
            )
            return {"event": "triggered", "reason": f"{condition_text(normalized)} 已到：{detail}"}
    if session >= research["valid_until"]:
        days = len(trading_days_between(research["created_session"] + timedelta(days=1), session))
        return {"event": "expired", "reason": f"{days} 个交易日条件未到"}
    return None
