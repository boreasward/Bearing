"""Push payloads for weekly and monthly reviews and the snapshot reminder (P4 design §6).

Pure functions over frozen snapshot rows: the payload is built here, the Discord
messages in notify.templates. Due weeks and months follow §6.1 - a week is due
when the latest closed session's next trading day falls in the next week, and a
month likewise; reminders wait until the first Saturday of the following month
in Beijing time.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from investment_assistant.data.calendar import add_trading_days, trading_days_between
from investment_assistant.data.adapters import bars

SH = ZoneInfo('Asia/Shanghai')
NOTE_TEXT = '描述性统计，样本小且重叠，不代表规则或 Agent 有效。'
EVIDENCE_TEXT = '证据：未验证'


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def due_week(now: datetime) -> date:
    session = bars.latest_closed_session(now)
    next_session = add_trading_days(session, 1)
    monday = _monday(session)
    return monday if _monday(next_session) > monday else monday - timedelta(days=7)


def due_month(now: datetime) -> date:
    session = bars.latest_closed_session(now)
    next_session = add_trading_days(session, 1)
    month = session.replace(day=1)
    if (next_session.year, next_session.month) > (session.year, session.month):
        return month
    return (month - timedelta(days=1)).replace(day=1)


def last_session(start: date, end: date) -> date | None:
    days = trading_days_between(start, end)
    return days[-1] if days else None


def reminder_due(month: date, now: datetime) -> bool:
    next_month = (month.replace(day=28) + timedelta(days=5)).replace(day=1)
    first_saturday = next_month + timedelta(days=(5 - next_month.weekday()) % 7)
    return now.astimezone(SH).date() >= first_saturday


def _tracks_of(stats):
    tracks = stats.get('tracks')
    return tracks['summary']['all'] if tracks else None


def _rules_of(stats):
    rules = []
    for pool, per_rule in (stats.get('by_pool') or {}).items():
        for rule_id, row in per_rule.items():
            rules.append({'pool': pool, 'rule_id': rule_id, 'triggers': row['triggers'],
                          'plans': row['plans'], 'filled': row['filled'], 'r_mean': row.get('r_mean')})
    return rules


def _discipline_of(stats):
    discipline = stats.get('discipline')
    if not discipline:
        return None
    counts = discipline.get('counts') or {}
    return {'chase': counts.get('chase', 0), 'early_take': counts.get('early_take', 0),
            'held_loser': counts.get('held_loser', 0), 'unplanned': counts.get('unplanned', 0),
            'compliance_rate': discipline.get('compliance_rate')}


def _overview_of(stats):
    """The weekly overview line's numbers. A `review-8` snapshot (v2.9 D30) counts executed / not executed units and
    the plan-less buys (`unplanned_trades`); older snapshots keep decided / undecided / `unsignaled_trades`."""
    units = stats.get('units') or {}
    closed = len(stats.get('closed_trades') or [])
    if 'executed' in units:
        return {'execution': True, 'executed': units.get('executed', 0), 'not_executed': units.get('not_executed', 0),
                'unplanned': len(stats.get('unplanned_trades') or []), 'closed': closed}
    return {'decided': units.get('decided', 0), 'undecided': units.get('undecided', 0),
            'unsignaled': len(stats.get('unsignaled_trades') or []), 'closed': closed}


def weekly(review: dict[str, Any], *, public_url: str, proposals: int | None = None) -> dict[str, Any]:
    """The weekly review's push payload. ``proposals`` (v2.15 spec §8.3 ④) is how many optimisation proposals the
    ``propose`` node wrote since the previous weekly push — the Saturday run pushes the review before ``propose``
    runs, so a proposal reaches the next one; None (no migration 031) keeps the message as it was."""
    stats = review['stats']
    start = review['week_start']
    return {'kind': 'weekly', 'period': start, 'start': start.isoformat(),
            'end': (start + timedelta(days=6)).isoformat(),
            'overview': _overview_of(stats),
            'tracks': _tracks_of(stats), 'rules': _rules_of(stats),
            'discipline': _discipline_of(stats), 'learning': stats.get('learning'),
            'note': NOTE_TEXT, 'evidence': EVIDENCE_TEXT, 'proposals': proposals,
            'url': public_url.rstrip('/') + '/#review/weekly'}


def monthly(review: dict[str, Any], *, public_url: str) -> dict[str, Any]:
    stats = review['stats']
    if stats.get('review'):
        review_block = {'flag': stats['review'].get('flag'),
                        'compound': stats['review'].get('compound'),
                        'spy_compound': stats['review'].get('spy_compound')}
    else:                                            # monthly-1 snapshots only carry the flag
        review_block = {'flag': stats.get('review_flag')}
    sensitivity = stats.get('sensitivity') or {}
    return {'kind': 'monthly', 'period': review['month'],
            'month': stats.get('month') or review['month'].strftime('%Y-%m'),
            'return': stats.get('return'), 'tier': stats.get('tier'), 'spy': stats.get('spy'),
            'review': review_block,
            'sensitivity': {'count': sensitivity.get('count'),
                            'rows': sensitivity.get('rows') or []},
            'tracks': _tracks_of(stats), 'discipline': _discipline_of(stats),
            'note': NOTE_TEXT, 'evidence': EVIDENCE_TEXT,
            'url': public_url.rstrip('/') + '/#review/monthly'}


def reminder(month: date, *, public_url: str) -> dict[str, Any]:
    return {'kind': 'monthly_reminder', 'period': month, 'month': month.strftime('%Y-%m'),
            'url': public_url.rstrip('/') + '/#review/account'}
