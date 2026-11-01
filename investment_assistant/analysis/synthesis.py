"""Merge one ticker's candidates for one session into a decision unit (design §6)."""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from investment_assistant.data.calendar import add_trading_days

NY = ZoneInfo('America/New_York')


def entry_passed(signal_date: date, now: datetime) -> bool:
    """Shadow entry is the next session's open; after it the unit is shown as late."""
    entry_day = add_trading_days(signal_date, 1)
    return now >= datetime.combine(entry_day, time(9, 30), tzinfo=NY)


def synthesize(ticker: str, signal_date: date, candidates: list[dict[str, Any]], *, holding: bool,
               now: datetime) -> dict[str, Any]:
    if not candidates:
        raise ValueError('a decision unit needs at least one candidate')
    actions = {c['action'] for c in candidates}
    labels: list[str] = []
    if {'buy', 'sell'} <= actions:
        proposed = 'watch'
        labels.append('conflict')
    elif 'buy' in actions:
        proposed = 'buy'
    elif 'sell' in actions:
        proposed = 'sell'
        if not holding:
            labels.append('avoid')
    else:
        proposed = 'watch'
    return {
        'ticker': ticker.upper(), 'signal_date': signal_date,
        'candidate_ids': [c['id'] for c in candidates], 'rule_ids': [c['rule_id'] for c in candidates],
        'proposed_action': proposed, 'labels': labels, 'holding': holding,
        'entry_passed': entry_passed(signal_date, now),
    }
