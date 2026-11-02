"""Next earnings date from the yfinance calendar.

A failed fetch raises so the caller records it in the run report; a missing or
all-past answer is 'unknown', never a fabricated date.
"""
from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any, Callable

from investment_assistant.data import fetch_policy
from investment_assistant.data import repo
from investment_assistant.data.adapter_types import Source
from investment_assistant.data.adapter_support import ONCE, make_adapter
from investment_assistant.data.sources import nasdaq

CalendarFetcher = Callable[[str], dict]
# fetch-1 policy hooks; tests shorten the waits (spec §6.2 row 3).
FETCH_POLICY = fetch_policy.FetchPolicy()
FETCH_SLEEP = time.sleep


def _yfinance_fetcher(ticker: str) -> dict:
    import yfinance as yf

    # The calendar request was the 17.5-minute hang: the shared session with libcurl timeouts plus a thread backstop.
    def call() -> dict:
        return yf.Ticker(ticker, session=fetch_policy.shared_yf_session()).calendar

    return fetch_policy.hard_deadline(call, seconds=fetch_policy.YF_HARD_DEADLINE_S)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, 'date') and callable(value.date):  # pandas.Timestamp
        try:
            return value.date()
        except Exception:
            return None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def fetch_next(ticker: str, *, fetcher: CalendarFetcher | None = None, today: date) -> dict:
    """Earliest earnings date on or after today; unknown when absent or all past."""
    return fetch_next_with_outcome(ticker, fetcher=fetcher, today=today)[0]


def fetch_next_with_outcome(ticker: str, *, fetcher: CalendarFetcher | None = None,
                            today: date) -> tuple[dict, fetch_policy.Fetched]:
    """``fetch_next`` plus the fetch record (attempts, elapsed) for the collect stage's earnings detail.

    The request runs under the fetch-1 policy with target ``earnings:<TICKER>``; a failure raises ``FetchError``."""
    fetched = fetch_policy.fetch(f'earnings:{ticker.upper()}', lambda: (fetcher or _yfinance_fetcher)(ticker.upper()),
                                 policy=FETCH_POLICY, sleep=lambda s: FETCH_SLEEP(s))
    return _earliest(fetched.value, today), fetched


def _earliest(calendar: Any, today: date) -> dict:
    raw = calendar.get('Earnings Date') if isinstance(calendar, dict) else None
    if raw is None:
        raw = []
    elif not isinstance(raw, list):
        raw = [raw]
    upcoming = sorted(d for value in raw if (d := _as_date(value)) is not None and d >= today)
    if not upcoming:
        return {'status': 'unknown', 'earnings_date': None}
    return {'status': 'known', 'earnings_date': upcoming[0]}


def _fetch(target, *, session, start, end, deps):
    row, outcome = fetch_next_with_outcome(target, fetcher=deps.earnings_fetcher, today=session)
    return fetch_policy.Fetched([row], outcome.attempts, outcome.elapsed_ms)


def _nasdaq(target, *, session, start, end, deps):
    return [nasdaq.fetch_earnings_date(target, today=session)]


def _store(conn, rows, *, target, source_id, now):
    result = repo.store_earnings(conn, target, rows[0], now=now, source=source_id)
    inserted = result['status'] == 'inserted'
    if inserted:
        latest = now
    else:
        prior = repo.latest_earnings(conn, target, cutoff_at=now)
        assert prior is not None  # unchanged means store_earnings read a visible append-only row at this cutoff
        latest = prior['fetched_at']
    return {'inserted': int(inserted), 'existing': int(not inserted), 'latest': latest}


def _latest(conn, targets, *, cutoff_at):
    return {t: row['fetched_at'] for t in targets
            if (row := repo.latest_earnings(conn, t, cutoff_at=cutoff_at)) is not None}


ADAPTER = make_adapter('earnings', '财报日期', '下一财报日，未知保持unknown', 'earnings_dates',
    (Source('yfinance', _fetch, ONCE, '内部fetch-1，保留真实attempts'),
     Source('nasdaq', _nasdaq, ONCE, 'Nasdaq announcement；备源只尝试一次')),
    _store, _latest, days=7, coverage_note='覆盖 = 最新财报日答案在 session 前7天内采集。')
