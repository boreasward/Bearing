"""Screen-1 adjusted-close returns/averages and close-based dollar volume."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from investment_assistant.derivation.indicators._price_math import atr14


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _ret(bars: list[dict[str, Any]], n: int) -> Decimal:
    return _dec(bars[-1]['adj_close']) / _dec(bars[-1 - n]['adj_close']) - 1


def _ma(bars: list[dict[str, Any]], n: int) -> Decimal:
    window = [_dec(b['adj_close']) for b in bars[-n:]]
    return sum(window) / len(window)


def _dollar_volume_20(bars: list[dict[str, Any]]) -> Decimal:
    return sum(_dec(b['close']) * _dec(b['volume']) for b in bars[-20:]) / 20


def screen_rs(bars: list[dict], bench_r20: Decimal, bench_r60: Decimal) -> Decimal:
    """Original screen-1 weighted returns against QQQ (not Agent tools' SPY RS)."""
    return (Decimal('0.5') * (_ret(bars, 20) - bench_r20)
            + Decimal('0.5') * (_ret(bars, 60) - bench_r60))


def atr_pct(bars: list[dict]) -> Decimal | None:
    """Original screen-1 ATR divided by the unadjusted close."""
    atr = atr14(bars)
    return (atr / _dec(bars[-1]['close'])) if atr is not None else None
