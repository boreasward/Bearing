"""v3 holdings view: FIFO positions from the run's recorded trades against latest closes and
stops (pure). Facts only — the v3 draft does not re-review positions daily (spec §5.3)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from investment_assistant.execution.positions import PositionError, build


def holdings_view(trades: list[dict], *, closes: dict[str, float],
                  stops: dict[str, Decimal | None],
                  splits: dict[str, list[tuple[date, Decimal]]] | None = None) -> list[dict]:
    """One row per open position, sorted by ticker; fully sold tickers drop out and a sell
    beyond the open position marks that ticker `position_invalid` without touching the rest."""
    by_ticker: dict[str, list[dict]] = {}
    for trade in trades:
        by_ticker.setdefault(trade['ticker'], []).append(trade)
    rows: list[dict[str, Any]] = []
    for ticker in sorted(by_ticker):
        try:
            position = build(by_ticker[ticker], splits=(splits or {}).get(ticker))['position']
        except PositionError:
            rows.append({'ticker': ticker, 'error': 'position_invalid'})
            continue
        if position is None:
            continue
        last_close = closes.get(ticker)
        stop = stops.get(ticker)
        avg_cost = float(position['avg_cost'])
        rows.append({'ticker': ticker, 'quantity': position['quantity'], 'avg_cost': position['avg_cost'],
                     'last_close': last_close,
                     'pnl_pct': last_close / avg_cost - 1 if last_close is not None else None,
                     'stop': stop,
                     'to_stop_pct': (last_close - float(stop)) / last_close
                     if last_close is not None and stop is not None else None})
    return rows
