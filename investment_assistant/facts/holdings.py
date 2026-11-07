"""v3 holdings view: FIFO positions from the run's recorded trades against latest closes and
plan-line rows (pure). Facts only — the v3 brief does not re-review positions daily (spec §5).

`plans` maps ticker -> the ticker's active plan row (held_sessions precomputed by the caller);
`actions` optionally maps ticker -> the plan_line.next_action dict. `review` stays None here —
the caller fills it with the agent's holding-review conclusion.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from investment_assistant.execution.positions import PositionError, build


def _stop_order_status(plan: dict[str, Any] | None) -> str:
    if plan is None or plan.get('confirmed_stop_order') is None:
        return 'none'
    stop = plan.get('stop')
    confirmed = plan['confirmed_stop_order']
    if stop is not None and confirmed < stop:
        return 'needs_raise'
    return 'confirmed'


def holdings_view(trades: list[dict], *, closes: dict[str, float],
                  plans: dict[str, dict[str, Any]],
                  splits: dict[str, list[tuple[date, Decimal]]] | None = None,
                  actions: dict[str, dict[str, Any]] | None = None) -> list[dict]:
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
        plan = plans.get(ticker)
        stop = plan.get('stop') if plan is not None else None
        avg_cost = float(position['avg_cost'])
        quantity = float(position['quantity'])
        rows.append({'ticker': ticker, 'quantity': position['quantity'], 'avg_cost': position['avg_cost'],
                     'last_close': last_close,
                     'pnl_pct': last_close / avg_cost - 1 if last_close is not None else None,
                     'stop': stop,
                     'to_stop_pct': (last_close - float(stop)) / last_close
                     if last_close is not None and stop is not None else None,
                     'plan_id': plan.get('id') if plan is not None else None,
                     'kind': plan.get('kind') if plan is not None else None,
                     'trailing': bool(plan.get('trailing')) if plan is not None else False,
                     'unrealized_pnl': quantity * (last_close - avg_cost)
                     if last_close is not None else None,
                     'held_sessions': plan.get('held_sessions') if plan is not None else None,
                     'next_action': (actions or {}).get(ticker),
                     'stop_order_status': _stop_order_status(plan),
                     'review': None})
    return rows
