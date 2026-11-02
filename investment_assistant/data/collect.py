"""Per-ticker collectors shared by the batch and the screen stage.

Each function owns exactly one fetch -> normalize -> store step and its own
transaction (``service._tx``), so every caller keeps its try/except and its
summary writes where they are. Exceptions always propagate unchanged.

The rest of this module is the fetch-run bookkeeping the batch and the manual
fetch feed (v2.3 §6): the per-family Tally, the append-only write, and the
per-family advisory locks.
"""
from __future__ import annotations
from investment_assistant.data.tally import Tally as Tally, TARGETS_MAX as TARGETS_MAX
from investment_assistant.data.fetch_status import iso as _iso
from investment_assistant.data.adapter_support import discover_one as _discover_one
from investment_assistant.core.constants import FILING_LOOKBACK_DAYS, SEC_EVIDENCE_DIR
from investment_assistant.data.sources import edgar as monitor_source
import investment_assistant.data.repo as data_repo

import time
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

from investment_assistant.data import fetch_policy
from investment_assistant.data.fetch_policy import FetchError, Fetched
from investment_assistant.data.adapters import bars as bars_module
from investment_assistant.core import redact as families
from investment_assistant.data.adapters import fred as macro_module
from investment_assistant.data.adapters import news as news_module
# Repository consumers use their owning layers below.
from investment_assistant.data.adapters import universe as universe_module
from investment_assistant.core import tx as core_tx


# fetch-1 hooks (spec §6): every external request of this module runs under FETCH_POLICY; tests shorten it and
# swap the sleep. ``deps``-injected fetchers are wrapped too, so a fake that raises is classified, not special-cased.
FETCH_POLICY = fetch_policy.FetchPolicy()
FETCH_SLEEP = time.sleep


def _fetch(target: str, fn: Callable[[], Any]) -> Fetched:
    return fetch_policy.fetch(target, fn, policy=FETCH_POLICY, sleep=lambda seconds: FETCH_SLEEP(seconds))


def _fail(tally: 'Tally | None', target: str, exc: BaseException, outcome: Fetched | None = None) -> None:
    """Book a failure on the tally. A failure after a successful fetch (normalise, store) keeps that fetch's
    attempt records; the caller still re-raises the original exception."""
    if tally is None:
        return
    if not isinstance(exc, FetchError):
        kind, status = fetch_policy.classify_exception(exc)
        exc = FetchError(str(exc) or type(exc).__name__, kind=kind, status=status,
                         attempts=outcome.attempts if outcome else [], target=target)
    tally.record(target, exc)


def bars_one(ticker: str, *, session: date, now: datetime, deps: Any, lookback_days: int,
             tally: 'Tally | None' = None) -> dict:
    """One ticker's daily bars, stored in their own transaction.

    Returns ``{'counts': store_bars result, 'skipped': list[date]}``. The request runs under the fetch-1 policy;
    a failure raises ``FetchError``. With ``tally`` the outcome is booked there (target = ticker, ``rows`` = bars
    newly written, ``latest`` = newest ``trade_date`` fetched) and the caller must not book it again."""
    outcome: Fetched | None = None
    try:
        outcome = _fetch(ticker, lambda: bars_module.fetch_history(
            ticker, session - timedelta(days=lookback_days), session, fetcher=deps.bar_fetcher))
        rows, skipped = bars_module.normalize_history(outcome.value)
        with core_tx._tx() as conn:
            counts = data_repo.store_bars(conn, ticker, rows, now=now)
    except Exception as exc:
        _fail(tally, ticker, exc, outcome)
        raise
    if tally is not None:
        tally.record(ticker, outcome, rows=counts['inserted'],
                     latest=_iso(max((row['price_date'] for row in rows), default=None)))
    return {'counts': counts, 'skipped': skipped}


def news_one(ticker: str, *, now: datetime, deps: Any, drop_after: datetime | None = None,
             tally: 'Tally | None' = None) -> dict:
    """One ticker's yfinance news, stored in its own transaction.

    ``drop_after`` (a resumed run's ``cutoff_at``, spec §5.5) drops items published after it; the count is
    returned as ``after_cutoff_dropped`` (the key only exists when ``drop_after`` is given). Items without a
    publication time cannot be judged and are kept."""
    outcome: Fetched | None = None
    try:
        outcome = _fetch(ticker, lambda: news_module.fetch_yfinance(ticker, fetcher=deps.news_fetcher))
        raw = outcome.value
        items, bad = news_module.normalize_yfinance(raw, ticker=ticker)
        dropped = 0
        if drop_after is not None:
            kept = [i for i in items if i['published_at'] is None or i['published_at'] <= drop_after]
            dropped, items = len(items) - len(kept), kept
        with core_tx._tx() as conn:
            counts = data_repo.store_news(conn, [(i, [ticker]) for i in items], now=now)
    except Exception as exc:
        _fail(tally, ticker, exc, outcome)
        raise
    if tally is not None:
        tally.record(ticker, outcome, rows=counts['inserted'],
                     latest=_iso(max((i['published_at'] for i in items if i['published_at'] is not None),
                                     default=None)))
    result = {**counts, 'fetched': len(raw), 'skipped': bad}
    if drop_after is not None:
        result['after_cutoff_dropped'] = dropped
    return result


def sec_listing(tickers: list[str], *, session: date, now: datetime, deps: Any, tally: 'Tally | None' = None,
                drop_after: datetime | None = None) -> dict:
    """SEC 8-K listings over the filing lookback, stored as news plus acceptance fill — one ticker at a time.

    Every ticker is its own target: a missing CIK or a dropped connection fails only that ticker. The result keeps
    the old keys (``inserted`` / ``existing`` / ``acceptance_filled``) and adds ``by_ticker`` (``{ticker: {'rows',
    'latest'}}`` for the ones that worked), ``errors`` (``{ticker: reason}``) and, when ``drop_after`` is given,
    ``after_cutoff_dropped`` (filings accepted after that cutoff are left out, spec §5.5).

    With ``tally`` each ticker's outcome is booked there and failures do not raise; a ``company_tickers.json``
    failure (nothing can be resolved) is booked as the whole-call ``'*'`` and listing stops. Without ``tally``
    (older callers) any failure raises and nothing is stored, as the single-call version did."""
    source = deps.filing_source
    if source is None:
        source = monitor_source.EdgarSource(SEC_EVIDENCE_DIR)
    start = (session - timedelta(days=FILING_LOOKBACK_DAYS)).isoformat()
    end = session.isoformat()
    once = fetch_policy.FetchPolicy(backoff_s=())      # EdgarSource.get retries each request itself
    metas: list[dict] = []
    by_ticker: dict[str, dict] = {}
    errors: dict[str, str] = {}
    dropped = 0
    for ticker in tickers:
        try:
            outcome = fetch_policy.fetch(ticker, lambda ticker=ticker: _discover_one(source, ticker, start, end),
                                         policy=once, sleep=lambda seconds: FETCH_SLEEP(seconds))
        except Exception as exc:
            if tally is None:
                raise
            if isinstance(exc, FetchError) and exc.target == monitor_source.COMPANY_TICKERS_TARGET:
                errors['*'] = families.sanitize_reason(exc)
                tally.record('*', exc)
                break
            errors[ticker] = families.sanitize_reason(exc)
            tally.record(ticker, exc)
            continue
        found, drop_count = monitor_source.drop_after_cutoff(outcome.value, drop_after)
        dropped += drop_count
        metas.extend(found)
        latest = max((m['filing_date'] for m in found), default=None)
        by_ticker[ticker] = {'rows': len(found), 'latest': latest}
        if tally is not None:
            tally.record(ticker, outcome, rows=len(found), latest=latest)
    with core_tx._tx() as conn:
        result = {**data_repo.store_news(conn, news_module.filing_news(metas), now=now),
                  'acceptance_filled': data_repo.fill_filing_acceptance(conn, metas),
                  'by_ticker': by_ticker, 'errors': errors}
    if drop_after is not None:
        result['after_cutoff_dropped'] = dropped
    return result


def fred_series(series_id: str, *, session: date, now: datetime, deps: Any,
                api_key: str | None, tally: 'Tally | None' = None) -> dict | str:
    """One FRED series: ``'not_configured'`` without a key (no request, no rows),
    otherwise the stored observations. The request runs under the fetch-1 policy (``FetchError`` on failure;
    the key never appears in its text); with ``tally`` the outcome is booked under the series id."""
    if not api_key:
        return 'not_configured'
    outcome: Fetched | None = None
    try:
        outcome = _fetch(series_id, lambda: macro_module.fetch_series(
            series_id, start=session - timedelta(days=macro_module.MACRO_LOOKBACK_DAYS), end=session,
            api_key=api_key, fetcher=deps.macro_fetcher))
        rows = outcome.value
        with core_tx._tx() as conn:
            counts = data_repo.store_macro(conn, series_id, rows, now=now)
    except Exception as exc:
        _fail(tally, series_id, exc, outcome)
        raise
    if tally is not None:
        tally.record(series_id, outcome, rows=counts['inserted'],
                     latest=_iso(max((row['obs_date'] for row in rows), default=None)))
    return counts


# ------------------------------------------------------------- fetch-run bookkeeping

def wall_clock() -> datetime:
    """Real wall time for data_fetch_runs timestamps; injected in tests (I07)."""
    return datetime.now(UTC)



def write_runs_with_ids(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[int]]:
    """Append every row in one transaction; also return the inserted ids.

    The first element is exactly ``write_runs``' return value, the second the
    inserted rows' ids in order (``[]`` when nothing was written). A failure
    (including migration 024 not run) is reported, never raised (design §6.2)."""
    try:
        with core_tx._tx() as conn:
            ids = [data_repo.insert_fetch_run(conn, row)['id'] for row in rows]
        return {'written': len(rows), 'error': None}, ids
    except Exception as exc:
        return {'written': 0, 'error': families.sanitize_reason(exc)}, []


def write_runs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Append every row in one transaction; see ``write_runs_with_ids``."""
    return write_runs_with_ids(rows)[0]


def _lock_key(family_id: str) -> str:
    from investment_assistant.core.locks import family_lock_key

    return family_lock_key(family_id)


def try_family_lock(conn, family_id: str) -> bool:
    """Take the family's session lock without waiting; the caller owns connection and unlock."""
    return bool(conn.execute('SELECT pg_try_advisory_lock(hashtext(%s))',
                             (_lock_key(family_id),)).fetchone()[0])


def unlock_family(conn, family_id: str) -> bool:
    """Release the family's session lock; False when this session did not hold it."""
    return bool(conn.execute('SELECT pg_advisory_unlock(hashtext(%s))',
                             (_lock_key(family_id),)).fetchone()[0])


def discard_connection(conn) -> None:
    """Physically close a pooled connection; the pool replaces it and every
    session-level advisory lock on it dies with the session (I01)."""
    try:
        conn.close()
    except Exception:
        pass


def _session_ready(conn) -> bool:
    """True when a statement can run on this session: idle, or rolled back out of
    an aborted transaction (session advisory locks survive the rollback)."""
    from psycopg.pq import TransactionStatus

    try:
        if conn.info.transaction_status == TransactionStatus.IDLE:
            return True
        conn.rollback()
        return conn.info.transaction_status == TransactionStatus.IDLE
    except Exception:
        return False


def release_family(conn, family_id: str) -> None:
    """Release one family lock without ever raising. When the session cannot be
    proved clean (aborted beyond rollback, unlock refused) the connection is
    physically discarded instead of returned to the pool: a session still holding
    an advisory lock must never go back (I01)."""
    try:
        if _session_ready(conn) and unlock_family(conn, family_id):
            return
    except Exception:
        pass
    discard_connection(conn)


@contextmanager
def family_locks(family_ids: Sequence[str]):
    """Hold every family's session lock on one dedicated connection, taken and released
    in the given order (design §6.4). Session-level, so the locks survive the commit and
    die with the connection; a blocked acquire waits instead of failing.

    Cleanup is protected from the first acquire on: every lock taken is tracked,
    releasing one never stops the next, an aborted transaction is rolled back
    first, and a session that cannot be proved clean is physically closed so its
    locks die with it — never returned to the pool still holding one (I01)."""
    with core_tx.connection() as conn:
        acquired: list[str] = []
        clean = True
        try:
            for family_id in family_ids:
                try:
                    conn.execute('SELECT pg_advisory_lock(hashtext(%s))', (_lock_key(family_id),))
                except Exception:
                    clean = False   # earlier locks may be held and the session may be aborted
                    raise
                acquired.append(family_id)
            try:
                yield conn
            finally:
                for family_id in reversed(acquired):
                    release_family(conn, family_id)   # never raises; discards when unsure
        finally:
            if not clean:
                discard_connection(conn)
