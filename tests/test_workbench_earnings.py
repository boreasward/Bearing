"""Next earnings date collection: pick the earliest future date, unknown otherwise."""
from datetime import UTC, date, datetime, timedelta

import pytest

from investment_assistant.data.adapters import earnings

TODAY = date(2026, 10, 22)


def test_calendar_list_picks_next():
    fetcher = lambda t: {'Earnings Date': [date(2026, 10, 15), date(2026, 11, 19), date(2027, 2, 18)]}
    assert earnings.fetch_next('NVDA', fetcher=fetcher, today=TODAY) == \
        {'status': 'known', 'earnings_date': date(2026, 11, 19)}


def test_calendar_single_date_accepted():
    fetcher = lambda t: {'Earnings Date': date(2026, 11, 19)}
    assert earnings.fetch_next('NVDA', fetcher=fetcher, today=TODAY) == \
        {'status': 'known', 'earnings_date': date(2026, 11, 19)}


def test_calendar_missing_is_unknown():
    for calendar in ({}, {'Earnings Date': []}, {'Earnings Date': [date(2026, 10, 1)]}, {'Dividend Date': 'x'}):
        assert earnings.fetch_next('NVDA', fetcher=lambda t: calendar, today=TODAY) == \
            {'status': 'unknown', 'earnings_date': None}


def test_fetch_error_raises():
    def boom(_t):
        raise RuntimeError('network down')

    with pytest.raises(RuntimeError):
        earnings.fetch_next('NVDA', fetcher=boom, today=TODAY)
