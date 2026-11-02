"""Original price arithmetic, below both the public facade and the registry.

Keep operations and float conversion points intact: close averages belong to
Agent tools; adjusted-close averages belong to screen_metrics, not this module.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from investment_assistant.core.constants import MIN_BARS_ATR as MIN_BARS

ATR_PERIOD = 14


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def atr14(bars: list[dict[str, Any]]) -> Decimal | None:
    """Wilder ATR over the last 14 true ranges; None when fewer than 15 bars are given."""
    if len(bars) < MIN_BARS:
        return None
    trs: list[Decimal] = []
    prev_close: Decimal | None = None
    for bar in bars[-MIN_BARS:]:
        high, low = _dec(bar['high']), _dec(bar['low'])
        if prev_close is not None:
            trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = _dec(bar['close'])
    atr = sum(trs[:ATR_PERIOD]) / ATR_PERIOD
    for tr in trs[ATR_PERIOD:]:
        atr = (atr * (ATR_PERIOD - 1) + tr) / ATR_PERIOD
    return atr


def _ma_close(bars: list[dict], n: int) -> float:
    closes = [b['close'] for b in bars]
    return float(sum(closes[-n:]) / n)


def _high(bars: list[dict], n: int) -> float:
    return float(max(b['high'] for b in bars[-n:]))


def _low(bars: list[dict], n: int) -> float:
    return float(min(b['low'] for b in bars[-n:]))


def _avg_volume_20(bars: list[dict]):
    # Do not convert before the ratio: Decimal inputs retain their original precision.
    return sum(b['volume'] for b in bars[-20:]) / 20


def _volume_ratio(bars: list[dict]) -> float | None:
    avg_volume = _avg_volume_20(bars)
    return float(bars[-1]['volume'] / avg_volume) if avg_volume else None


def _relative_strength(bars: list[dict], spy_bars: list[dict], days: int) -> float | None:
    need = days + 1
    if len(bars) < need or len(spy_bars) < need:
        return None
    ret = bars[-1]['adj_close'] / bars[-need]['adj_close'] - 1
    spy_ret = spy_bars[-1]['adj_close'] / spy_bars[-need]['adj_close'] - 1
    return float(ret - spy_ret)


def _rolling_mean(values: list[float], window: int) -> list[float | None]:
    total = 0.0
    out: list[float | None] = []
    for i, value in enumerate(values):
        total += value
        if i >= window:
            total -= values[i - window]
        out.append(total / window if i >= window - 1 else None)
    return out
