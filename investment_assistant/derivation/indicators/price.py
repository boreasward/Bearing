"""Price indicators: existing close/adjusted-close conventions are kept separately."""
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


def indicators(bars: list[dict], spy_bars: list[dict]) -> dict:
    """Point-in-time indicator snapshot; anything the history is too short for is None."""
    out: dict = {'last_close': None, 'atr14': None, 'ma20': None, 'ma50': None, 'ma200': None,
                 'high_20': None, 'low_20': None, 'high_252': None, 'low_252': None,
                 'avg_volume_20': None, 'volume_ratio': None, 'rs20': None, 'rs60': None}
    if not bars:
        return out
    closes = [b['close'] for b in bars]
    out['last_close'] = float(closes[-1])
    if (atr := atr14(bars)) is not None:
        out['atr14'] = float(atr)
    for n in (20, 50, 200):
        if len(closes) >= n:
            out[f'ma{n}'] = float(sum(closes[-n:]) / n)
    for n in (20, 252):
        if len(bars) >= n:
            window = bars[-n:]
            out[f'high_{n}'] = float(max(b['high'] for b in window))
            out[f'low_{n}'] = float(min(b['low'] for b in window))
    if len(bars) >= 20:
        avg_volume = sum(b['volume'] for b in bars[-20:]) / 20
        out['avg_volume_20'] = float(avg_volume)
        out['volume_ratio'] = float(bars[-1]['volume'] / avg_volume) if avg_volume else None
    for n in (20, 60):
        out[f'rs{n}'] = _relative_strength(bars, spy_bars, n)
    return out


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
