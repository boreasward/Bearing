"""Price chart payload for the instrument view: split-adjusted bars with simple moving averages,
and (v2.6 spec §9.3) the annotations drawn on them: candidate markers, plan level segments, your fills."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from investment_assistant.core import texts as labels
from investment_assistant.derivation.indicators.price import _rolling_mean

READ_LIMIT = 450
KEEP = 250
WINDOWS = (20, 50, 200)
MAX_PLANS = 12                      # plans drawn at most; the rest are counted in ``omitted``
NY = ZoneInfo('America/New_York')

_BAR_FIELDS = ('price_date', 'open', 'high', 'low', 'close', 'volume')


def build(bars: list[dict[str, Any]], *, keep: int = KEEP) -> dict[str, Any]:
    """Keep the last ``keep`` bars; averages always use the full history handed in, oldest first."""
    closes = [bar['close'] for bar in bars]
    ma = {window: _rolling_mean(closes, window) for window in WINDOWS}
    return {'bars': [{**{field: bar[field] for field in _BAR_FIELDS},
                      **{f'ma{window}': ma[window][i] for window in WINDOWS}}
                     for i, bar in enumerate(bars)][-keep:],
            'adjustment': 'split_only'}


# ---------------------------------------------------------------- annotations (v2.6 spec §9.3)

_LEVELS = (('entry_high', 'entry_high'), ('entry_low', 'entry_low'), ('stop', 'stop'), ('target1', 'target1'))
_KIND_ORDER = {'candidate': 0, 'trade': 1}


def _day(value: Any) -> date:
    return value.date() if isinstance(value, datetime) else value


def _action_of(actions: set[str]) -> str:
    """The unit's action as ``synthesis`` composes it: buy and sell on one day is a conflict -> watch."""
    if {'buy', 'sell'} <= actions:
        return 'watch'
    return 'buy' if 'buy' in actions else 'sell' if 'sell' in actions else 'watch'


_CANDIDATE_MARK = {'buy': ('belowBar', 'arrowUp'), 'sell': ('aboveBar', 'arrowDown'), 'watch': ('aboveBar', 'circle')}


def _candidate_markers(candidates: list[dict], on_chart: set[date]) -> list[dict]:
    by_day: dict[date, list[dict]] = {}
    for cand in candidates:
        day = _day(cand['signal_date'])
        if day in on_chart:
            by_day.setdefault(day, []).append(cand)
    markers = []
    for day, group in by_day.items():
        position, shape = _CANDIDATE_MARK[_action_of({c['action'] for c in group})]
        names = list(dict.fromkeys(labels.RULE_SHORT.get(c['rule_id'], c['rule_id']) for c in group))
        markers.append({'time': day.isoformat(), 'position': position, 'shape': shape, 'text': '+'.join(names),
                        'kind': 'candidate', 'refs': [str(c['id']) for c in group]})
    return markers


def _quantity_text(quantity: Any) -> str:
    text = format(quantity, 'f') if hasattr(quantity, 'as_tuple') else str(quantity)
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _trade_markers(trades: list[dict], days: list[date]) -> list[dict]:
    """Each live fill on the bar of its New York date; not a bar day -> the next bar; outside the chart -> none."""
    markers = []
    for trade in trades:
        if trade.get('voided_at') is not None:
            continue
        executed = trade['executed_at']
        local = executed.astimezone(NY).date() if isinstance(executed, datetime) else executed
        index = bisect_left(days, local)
        if index >= len(days) or (index == 0 and local < days[0]):
            continue
        buy = trade['side'] == 'buy'
        markers.append({'time': days[index].isoformat(), 'position': 'belowBar' if buy else 'aboveBar',
                        'shape': 'square', 'text': f"{'B' if buy else 'S'} {_quantity_text(trade['quantity'])}",
                        'kind': 'trade', 'refs': [str(trade['id'])]})
    return markers


def _plan_label(plan: dict, versions: dict[str, dict]) -> str:
    if plan['kind'] == 'own':
        return '自定'
    if plan['kind'] == 'agent':
        version = versions.get(str(plan['id']))
        return f"Agent v{version['version_no']}" if version else 'Agent'
    return '规则'


def _segments(plans: list[dict], versions: dict[str, dict], days: list[date]) -> tuple[list[dict], int]:
    """Four short segments per plan over the chart days inside its validity; at most MAX_PLANS plans.

    Each segment carries the plan's ``status`` (``ok`` / ``invalid``) so the chart can fade voided plans."""
    clipped = []
    for plan in plans:
        start = bisect_left(days, _day(plan['valid_from']))
        end = bisect_right(days, _day(plan['valid_until'])) - 1
        if start <= end and start < len(days):
            clipped.append((plan, days[start], days[end]))
    clipped.sort(key=lambda item: (_day(item[0]['valid_until']), _day(item[0]['valid_from']),
                                   str(item[0].get('created_at') or '')), reverse=True)
    segments = []
    for plan, first, last in clipped[:MAX_PLANS]:
        label = _plan_label(plan, versions)
        for level, column in _LEVELS:
            segments.append({'plan_id': str(plan['id']), 'kind': plan['kind'], 'label': label, 'level': level,
                             'from': first.isoformat(), 'to': last.isoformat(), 'value': float(plan[column]),
                             'status': plan.get('status') or 'ok'})
    return segments, max(len(clipped) - MAX_PLANS, 0)


def annotate(*, bars: list[dict[str, Any]], candidates: list[dict[str, Any]], plans: list[dict[str, Any]],
             versions: dict[str, dict[str, Any]], trades: list[dict[str, Any]]) -> dict[str, Any]:
    """``{markers, segments, omitted}`` for the chart's own bar dates (``bars`` = ``build(...)['bars']``).

    markers are sorted by time (lightweight-charts needs ascending order; candidates before fills on one day),
    a candidate day outside the chart is not drawn, a plan is clipped to the chart days inside its validity,
    ``versions`` maps ``str(plan_id)`` to its agent_plan_versions row."""
    days = sorted(_day(bar['price_date']) for bar in bars)
    if not days:
        return {'markers': [], 'segments': [], 'omitted': 0}
    markers = _candidate_markers(candidates, set(days)) + _trade_markers(trades, days)
    markers.sort(key=lambda m: (m['time'], _KIND_ORDER[m['kind']]))
    segments, omitted = _segments(plans, versions, days)
    return {'markers': markers, 'segments': segments, 'omitted': omitted}
