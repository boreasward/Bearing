"""Trade plan math: rule levels, validity, earnings flags, sizing, level validation.

Pure functions; SQL and transactions live elsewhere. Levels are rounded to cents half-up.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from investment_assistant.config import WorkbenchConfig
from investment_assistant.data.calendar import add_trading_days

MAX_HOLD_RANGE = (3, 30)  # the allowed max_hold_sessions
ENTRY_ATR = Decimal("0.5")
STOP_ATR = Decimal(2)
TARGET_ATR = Decimal(4)
VALID_SESSIONS = 3
EARNINGS_WARN_SESSIONS = 10

CENT = Decimal("0.01")
LEVEL_KEYS = ("entry_low", "entry_high", "stop", "target1")


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _cent(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


cent = _cent  # 公开别名：页面计划卡片等上层与内部共用同一取分实现（单一来源）


def _floor(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def rule_levels(ref_price: Decimal, atr: Decimal) -> dict[str, Decimal]:
    """Entry band ±0.5 ATR around the reference close, stop 2 ATR below, target1 4 ATR above."""
    return {
        "entry_low": _cent(ref_price - ENTRY_ATR * atr),
        "entry_high": _cent(ref_price + ENTRY_ATR * atr),
        "stop": _cent(ref_price - STOP_ATR * atr),
        "target1": _cent(ref_price + TARGET_ATR * atr),
    }


def levels_ordered(levels: dict[str, Decimal]) -> bool:
    return levels["stop"] < levels["entry_low"] <= levels["entry_high"] < levels["target1"]


def validity(signal_date: date, sessions: int = VALID_SESSIONS) -> tuple[date, date]:
    """The plan may fill from T+1 through T+`sessions`, trading days only. The default stays
    T+3 (VALID_SESSIONS); v3.1 callers pass cfg.v3.valid_sessions explicitly."""
    return add_trading_days(signal_date, 1), add_trading_days(signal_date, sessions)


def earnings_flags(
    row: dict[str, Any] | None, *, signal_date: date, valid_from: date, valid_until: date
) -> tuple[list[str], list[str]]:
    """(invalid_reasons, warnings) for the next earnings date known at the cutoff."""
    if row is None or row.get("status") != "known" or not row.get("earnings_date"):
        return [], ["earnings_unknown"]
    earnings_date = row["earnings_date"]
    if valid_from <= earnings_date <= valid_until:
        return ["earnings_in_validity"], []
    if earnings_date < signal_date:
        return [], ["earnings_unknown"]
    if earnings_date <= add_trading_days(signal_date, EARNINGS_WARN_SESSIONS):
        return [], ["earnings_within_10d"]
    return [], []


def size(
    *,
    snapshot: dict[str, Any] | None,
    ref_price: Decimal,
    stop: Decimal,
    entry_high: Decimal,
    ticker_value: Decimal,
    total_value: Decimal,
    params: WorkbenchConfig,
) -> tuple[dict[str, Any], list[str]]:
    """Share count as the smallest of the risk budget and the two exposure caps.

    No snapshot means no equity is known: per-share risk only, status no_equity.
    A non-positive result means no room under the caps (reason no_room, shares 0).
    """
    per_share_risk = ref_price - stop
    base = {
        "risk_pct": params.risk_pct,
        "single_cap": params.single_cap,
        "total_cap": params.total_cap,
        "per_share_risk": per_share_risk,
    }
    if snapshot is None:
        return (
            {
                "equity": None,
                "snapshot_id": None,
                "snapshot_as_of": None,
                **base,
                "risk_amount": None,
                "shares": None,
                "applied_cap": None,
                "status": "no_equity",
            },
            [],
        )
    equity = _dec(snapshot["equity"])
    risk_amount = equity * _dec(params.risk_pct)
    caps = (
        ("risk", _floor(risk_amount / per_share_risk)),
        ("single", _floor((equity * _dec(params.single_cap) - ticker_value) / entry_high)),
        ("total", _floor((equity * _dec(params.total_cap) - total_value) / entry_high)),
    )
    applied_cap, shares = min(caps, key=lambda cap: cap[1])
    invalid = ["no_room"] if shares <= 0 else []
    status = "no_room" if shares <= 0 else ("sized" if applied_cap == "risk" else "capped")
    return (
        {
            "equity": equity,
            "snapshot_id": snapshot["id"],
            "snapshot_as_of": snapshot["as_of"],
            **base,
            "risk_amount": risk_amount,
            "shares": shares,
            "applied_cap": applied_cap,
            "status": status,
        },
        invalid,
    )


def validate_levels(raw: dict[str, Any]) -> dict[str, Decimal]:
    """User-submitted levels: numeric, finite, positive and strictly ordered."""
    levels: dict[str, Decimal] = {}
    for key in LEVEL_KEYS:
        try:
            value = _dec(raw[key])
        except (InvalidOperation, TypeError, KeyError):
            raise ValueError(f"{key} must be a number") from None
        if not value.is_finite() or value <= 0:
            raise ValueError(f"{key} must be a positive number")
        levels[key] = value
    if not levels_ordered(levels):
        raise ValueError("levels must satisfy stop < entry_low <= entry_high < target1")
    return levels
