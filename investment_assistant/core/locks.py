"""Run locks: the ``daily:run`` session lock and the advisory-lock helpers (was ``tasks/daily.py:31-120``).

Cut verbatim in Task 0; ``tasks/daily.py`` re-exports until Task 1, and
``repository.try_advisory_lock`` / ``collect._lock_key`` are covered by
``try_advisory_lock`` / ``family_lock_key`` here."""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager

from investment_assistant.core.tx import Unavailable

LOCK_KEY = 'daily:run'


class DailyBusy(RuntimeError):
    """Another ``daily`` execution (or a ``sec_backfill``) holds the ``daily:run`` lock (HTTP 409 ``daily_running``)."""

    def __init__(self, message: str = 'daily_running'):
        super().__init__(message)


_ACTIVE: set[str] = set()                    # run ids this process is executing right now (run or resume)
_ACTIVE_LOCK = threading.Lock()


@contextmanager
def _active(run_id: str):
    """Mark ``run_id`` as executing in this process while the ``with`` block runs, so the run detail can call a run
    that holds the lock *running* before its next node has written a row (the scheduler thread and the manual and
    resume entries all execute inside the API process; a command-line run in another process is only seen by the
    lock itself)."""
    with _ACTIVE_LOCK:
        _ACTIVE.add(run_id)
    try:
        yield
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.discard(run_id)


def executing(run_id: str) -> bool:
    with _ACTIVE_LOCK:
        return run_id in _ACTIVE


def _lock_connection():
    """A connection of its own, outside the pool and in autocommit: the lock is held for the whole execution (up to
    hours) and must neither take a pool connection nor ride on one whose transactions commit underneath it."""
    import psycopg

    url = os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL')
    if not url:
        raise Unavailable('数据库未配置')
    return psycopg.connect(url, autocommit=True)


@contextmanager
def exclusive():
    """Hold the ``daily:run`` session advisory lock for the ``with`` block (``DailyBusy`` when taken). Always released
    in ``finally`` and the connection always closed, which releases it too should the unlock fail (D18)."""
    import psycopg

    try:
        conn = _lock_connection()
    except psycopg.OperationalError:
        raise Unavailable('数据库不可达') from None
    try:
        if not conn.execute('SELECT pg_try_advisory_lock(hashtext(%s))', (LOCK_KEY,)).fetchone()[0]:
            raise DailyBusy()
        try:
            yield
        finally:
            try:
                conn.execute('SELECT pg_advisory_unlock(hashtext(%s))', (LOCK_KEY,))
            except Exception:      # noqa: BLE001 - closing the connection releases the lock anyway
                pass
    finally:
        conn.close()


def is_running() -> bool:
    """True while some execution holds the lock (the pre-check of the run / resume entries). It only *looks* at the
    lock — taking it to probe could make the 11:30 scheduled tick find it busy and skip the day."""
    import psycopg

    try:
        conn = _lock_connection()
    except psycopg.OperationalError:
        raise Unavailable('数据库不可达') from None
    try:
        # pg_try_advisory_lock(bigint) stores the key as classid (high 32 bits) / objid (low 32 bits), objsubid 1
        return bool(conn.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND objsubid = 1 AND granted "
            "AND ((classid::bigint << 32) | objid::bigint) = hashtext(%s)::bigint)", (LOCK_KEY,)).fetchone()[0])
    finally:
        conn.close()


def try_advisory_lock(conn, key: str) -> bool:
    """Transaction-scoped, non-blocking advisory lock on ``key``; False when another transaction holds it."""
    return bool(conn.execute('SELECT pg_try_advisory_xact_lock(hashtext(%s))', (key,)).fetchone()[0])


def family_lock_key(family_id: str) -> str:
    """The session-lock key of a data family (was ``collect._lock_key``)."""
    return f'data_family:{family_id}'
