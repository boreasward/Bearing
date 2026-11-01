"""FIFO positions from recorded trades (pure). Voided trades never count.

All open lots are carried in the current split basis so cost compares directly
with split-adjusted closes. Fees: buy fees raise cost, sell fees reduce proceeds
pro rata to the matched quantity.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')


class PositionError(ValueError):
    pass


def _session(trade: dict[str, Any]) -> date:
    return trade['executed_at'].astimezone(NY).date()


def build(trades: list[dict[str, Any]], *, splits: list[tuple[date, Decimal]] | None = None) -> dict[str, Any]:
    live = sorted((t for t in trades if t.get('voided_at') is None), key=lambda t: (t['executed_at'], str(t['id'])))
    pending_splits = sorted(splits or [])
    lots: list[dict[str, Any]] = []
    closed: list[dict[str, Any]] = []

    def apply_splits(through: date | None) -> None:
        while pending_splits and (through is None or pending_splits[0][0] <= through):
            _, factor = pending_splits.pop(0)
            for lot in lots:
                lot['quantity'] *= factor
                lot['cost_per_share'] /= factor

    for t in live:
        apply_splits(_session(t))
        qty, price, fee = Decimal(t['quantity']), Decimal(t['price']), Decimal(t.get('fee') or 0)
        if t['side'] == 'buy':
            lots.append({'buy_trade_id': t['id'], 'quantity': qty, 'cost_per_share': (qty * price + fee) / qty,
                         'opened_at': t['executed_at']})
            continue
        if qty > sum(lot['quantity'] for lot in lots):
            raise PositionError(f"sell {t['id']} exceeds the open position")
        remaining = qty
        while remaining > 0:
            lot = lots[0]
            matched = min(lot['quantity'], remaining)
            proceeds = matched * price - fee * matched / qty
            closed.append({'buy_trade_id': lot['buy_trade_id'], 'sell_trade_id': t['id'], 'quantity': matched,
                           'entry_cost_per_share': lot['cost_per_share'], 'exit_price': price,
                           'opened_at': lot['opened_at'], 'closed_at': t['executed_at'],
                           'realized_pnl': proceeds - matched * lot['cost_per_share']})
            lot['quantity'] -= matched
            remaining -= matched
            if lot['quantity'] == 0:
                lots.pop(0)
    apply_splits(None)

    if not lots:
        return {'closed': closed, 'open': [], 'position': None}
    quantity = sum(lot['quantity'] for lot in lots)
    cost = sum(lot['quantity'] * lot['cost_per_share'] for lot in lots)
    return {'closed': closed, 'open': lots,
            'position': {'quantity': quantity, 'avg_cost': cost / quantity, 'opened_at': lots[0]['opened_at']}}
