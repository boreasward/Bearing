"""Monthly return math, tiers, review flag and risk sensitivity (design §8.2). Pure.

Returns are broker-equity based, net of deposits and withdrawals; a month
without a usable snapshot at either end reports missing_snapshot, never zero.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from investment_assistant.core.versions import MONTHLY_VERSION as MONTHLY_VERSION

MONTHLY_V4 = 'monthly-4'      # v2.9 D30: tracks-2, discipline-2, plans by shadow version (monthly-3: plans.agent = first versions)
SENSITIVITY_PCTS = (0.005, 0.01, 0.015, 0.02)


def _month_end(month: date) -> date:
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def month_end_snapshot(snapshots: list[dict[str, Any]], month: date) -> dict[str, Any] | None:
    """Public name for the month's last live snapshot (P4 §6.1 uses it for the push)."""
    return _usable(snapshots, month)


def _usable(snapshots: list[dict[str, Any]], month: date) -> dict[str, Any] | None:
    """The month's last live snapshot; an older month's snapshot never stands in for it."""
    superseded = {s['supersedes_id'] for s in snapshots if s['supersedes_id']}
    live = [s for s in snapshots if s['id'] not in superseded and month <= s['as_of'] <= _month_end(month)]
    return max(live, key=lambda s: (s['as_of'], s['recorded_at']), default=None)


def month_return(month: date, snapshots: list[dict[str, Any]], flows: list[dict[str, Any]]) -> dict[str, Any]:
    """(end − start − net deposits and withdrawals) / start over one calendar month.

    start is the previous month's last snapshot and end this month's; either missing
    means missing_snapshot, so a stale snapshot never yields a 0% or multi-month return.
    """
    start = _usable(snapshots, (month - timedelta(days=1)).replace(day=1))
    end = _usable(snapshots, month)
    if start is None or end is None:
        return {'status': 'missing_snapshot', 'start': start, 'end': end, 'net_flow': None, 'return': None}
    net = sum((f['amount'] for f in flows if f['voided_at'] is None
               and start['as_of'] < f['flow_date'] <= end['as_of']), Decimal(0))
    return {'status': 'ok', 'start': start, 'end': end, 'net_flow': float(net),
            'return': float((end['equity'] - start['equity'] - net) / start['equity'])}


def month_return_v2(month: date, *, equity_rows: list[dict], snapshots: list[dict],
                    flows: list[dict], opened: bool) -> dict:
    """monthly-5: complete latest month-end ledger rows, otherwise the unchanged snapshot formula."""
    from investment_assistant.execution.accounts import month_window

    start, end = month_window(equity_rows, month) if opened else (None, None)
    incomplete = any((row.get('detail') or {}).get('bars_missing') or
                     (row.get('detail') or {}).get('stale') for row in (start, end) if row is not None)
    if start is None or end is None or incomplete:
        return {**month_return(month, snapshots, flows), 'source': 'snapshot'}
    net = sum((Decimal(str(f['amount'])) for f in flows if f.get('voided_at') is None
               and start['session'] < f['flow_date'] <= end['session']), Decimal(0))
    start_equity, end_equity = Decimal(str(start['equity'])), Decimal(str(end['equity']))
    return {'status': 'ok' if start_equity > 0 else 'no_equity', 'source': 'ledger',
            'start': start, 'end': end, 'net_flow': float(net),
            'return': float((end_equity - start_equity - net) / start_equity) if start_equity > 0 else None}


def tier(r: float, tiers: tuple[float, float, float]) -> str:
    if r > tiers[0]:
        return 'excellent_plus'
    if r >= tiers[1]:
        return 'excellent'
    if r >= tiers[2]:
        return 'viable'
    return 'attention'


def review_flag(last_three: list[dict[str, Any]]) -> str:
    """'review' needs a negative compound return that also trails SPY over the same months."""
    if len(last_three) < 3 or any(m.get('return') is None or m.get('spy_return') is None for m in last_three):
        return 'insufficient'
    compound = 1.0
    spy_compound = 1.0
    for m in last_three:
        compound *= 1 + m['return']
        spy_compound *= 1 + m['spy_return']
    compound, spy_compound = compound - 1, spy_compound - 1
    return 'review' if compound < 0 and compound < spy_compound else 'ok'


def review_detail(last_three: list[dict[str, Any]]) -> dict[str, Any]:
    """The three months behind the review flag: per-month returns plus compounds;
    any missing month nulls both compounds rather than faking a number."""
    months = [{'month': m.get('month'), 'return': m.get('return'), 'spy_return': m.get('spy_return')}
              for m in last_three]
    complete = (len(last_three) >= 3
                and all(m.get('return') is not None and m.get('spy_return') is not None for m in last_three))
    if not complete:
        return {'flag': review_flag(last_three), 'months': months, 'compound': None, 'spy_compound': None}
    compound = spy_compound = 1.0
    for m in last_three:
        compound *= 1 + m['return']
        spy_compound *= 1 + m['spy_return']
    return {'flag': review_flag(last_three), 'months': months,
            'compound': compound - 1, 'spy_compound': spy_compound - 1}


def sensitivity(rs: list[float], pcts: tuple[float, ...] = SENSITIVITY_PCTS) -> list[dict[str, Any]]:
    """Approximate: return ≈ ΣR × risk_pct, drawdown = the deepest cumulative-R decline × risk_pct."""
    cumulative = peak = max_dd = 0.0
    for r in rs:
        cumulative += r
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return [{'risk_pct': pct, 'return': sum(rs) * pct, 'max_drawdown': max_dd * pct} for pct in pcts]
