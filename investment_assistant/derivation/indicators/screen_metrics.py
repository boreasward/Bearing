"""Screen-1 adjusted-close returns/averages and close-based dollar volume."""
from __future__ import annotations

from decimal import Decimal
from typing import Any


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _ret(bars: list[dict[str, Any]], n: int) -> Decimal:
    return _dec(bars[-1]['adj_close']) / _dec(bars[-1 - n]['adj_close']) - 1


def _ma(bars: list[dict[str, Any]], n: int) -> Decimal:
    window = [_dec(b['adj_close']) for b in bars[-n:]]
    return sum(window) / len(window)


def _dollar_volume_20(bars: list[dict[str, Any]]) -> Decimal:
    return sum(_dec(b['close']) * _dec(b['volume']) for b in bars[-20:]) / 20
