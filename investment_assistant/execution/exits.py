"""A position's plan and its current stop (design §5.1, §5.2). Pure.

price_bars OHLC are stored split-adjusted in the current basis (a split makes
the next fetch append revised rows for all history), so bars compare as-is;
only the plan's frozen levels are rescaled by splits after its signal date.
t1_reached looks only at sessions strictly before the evaluation day, and the
trailing stop updates after each close and applies from the next session.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any


def position_plan(open_lots: list[dict[str, Any]], trades: list[dict[str, Any]],
                  plans_by_trade: dict[Any, dict[str, Any]]) -> dict[str, Any] | None:
    """The plan of the latest-executed open buy that carries one; None for unplanned positions."""
    executed_at = {t['id']: t['executed_at'] for t in trades}
    best: tuple[Any, dict[str, Any]] | None = None
    for lot in open_lots:
        plan = plans_by_trade.get(lot['buy_trade_id'])
        if plan is None:
            continue
        if best is None or executed_at[lot['buy_trade_id']] > best[0]:
            best = (executed_at[lot['buy_trade_id']], plan)
    return best[1] if best else None


def _basis_factor(day: date, splits: list[tuple[date, Decimal]]) -> Decimal:
    """Cumulative factor that moves a price known on ``day`` into the current split basis."""
    factor = Decimal(1)
    for split_date, split_factor in splits:
        if day < split_date:
            factor *= split_factor
    return factor


def active_stop(plan: dict[str, Any], bars_since_open: list[dict[str, Any]], *, avg_cost: Decimal,
                splits: list[tuple[date, Decimal]]) -> dict[str, Any]:
    """Current stop and T1 state for a planned position.

    ``bars_since_open`` runs from the position's opening session through the
    evaluation session; the last bar sets today's low/high for the rules, and
    only earlier bars count for t1_reached.
    """
    splits = sorted(splits)
    plan_factor = _basis_factor(plan['signal_date'], splits)
    stop, target1 = plan['stop'] / plan_factor, plan['target1'] / plan_factor
    atr = plan['atr'] / plan_factor

    t1_reached, t1_reached_on = False, None
    for bar in bars_since_open[:-1]:
        if Decimal(str(bar['high'])) >= target1:
            t1_reached, t1_reached_on = True, bar['price_date']
            break
    if not t1_reached:
        return {'stop': stop, 't1_reached': False, 't1_reached_on': None, 'mode': 'initial', 'target1': target1}
    # Trailing uses closes strictly before the evaluation day: it updates after the close.
    highest = max(Decimal(str(b['close'])) for b in bars_since_open[:-1])
    trail = max(highest - plan['trail_atr_multiple'] * atr, avg_cost)
    return {'stop': trail, 't1_reached': True, 't1_reached_on': t1_reached_on, 'mode': 'trailing',
            'target1': target1}
