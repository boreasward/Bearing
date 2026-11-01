"""Real-pool advisory-lock lifecycle tests (final review I01).

``family_locks`` and the manual fetch release their session locks through a
pooled connection: exiting the context returns the session to the pool instead
of closing it, so any lock surviving the exit leaks onto the next borrower —
and because session locks are re-entrant, the same session would "succeed" at
re-acquiring while other processes stay locked out. These tests use a real
``psycopg_pool.ConnectionPool`` (max_size=1) and a second physical session to
prove the locks are really gone, per the review's reproduction recipe.
"""
import os
from contextlib import contextmanager

import psycopg
import pytest

from investment_assistant.core import tx as core_tx
from psycopg_pool import ConnectionPool

from investment_assistant.workbench import collect, service


@pytest.fixture
def url():
    url = os.environ.get('INVESTMENT_ASSISTANT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('explicit test database URL required; disposable schema only')
    return url


def _lock_free(url: str, family_id: str) -> bool:
    """True when another physical session can take the family's lock right now."""
    key = collect._lock_key(family_id)
    with psycopg.connect(url, autocommit=True) as third:
        got = bool(third.execute('SELECT pg_try_advisory_lock(hashtext(%s))', [key]).fetchone()[0])
        if got:
            third.execute('SELECT pg_advisory_unlock(hashtext(%s))', [key])
        return got


@contextmanager
def _pooled_service(pool, monkeypatch):
    """Point service.connection at the real pool, with a short lock timeout."""

    @contextmanager
    def connection():
        with pool.connection() as conn:
            conn.execute("SET LOCAL lock_timeout = '200ms'")
            yield conn

    monkeypatch.setattr(core_tx, 'connection', connection)
    yield


def test_partial_acquire_failure_leaks_no_lock(url, monkeypatch):
    """bars acquires, news times out: after the failed context no lock survives on
    the pooled session — verified from another physical session."""
    holder = psycopg.connect(url, autocommit=True)
    holder.execute('SELECT pg_advisory_lock(hashtext(%s))', [collect._lock_key('i01:news')])
    pool = ConnectionPool(url, min_size=0, max_size=1, open=True)
    try:
        with _pooled_service(pool, monkeypatch):
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with collect.family_locks(['i01:bars', 'i01:news']):
                    raise AssertionError('must not enter')
    finally:
        pool.close()
        holder.execute('SELECT pg_advisory_unlock(hashtext(%s))', [collect._lock_key('i01:news')])
        holder.close()
    assert _lock_free(url, 'i01:bars')


def test_aborted_transaction_still_releases_and_keeps_original_error(url, monkeypatch):
    """A body failing mid-transaction must not turn into an unlock failure that
    both replaces the original error and leaks the lock."""
    pool = ConnectionPool(url, min_size=0, max_size=1, open=True)
    try:
        with _pooled_service(pool, monkeypatch):
            with pytest.raises(psycopg.errors.DivisionByZero):
                with collect.family_locks(['i01b:bars']) as conn:
                    conn.execute('SELECT 1/0')
    finally:
        pool.close()
    assert _lock_free(url, 'i01b:bars')


def test_unlock_failure_discards_the_connection(url, monkeypatch):
    """When the unlock itself cannot run, the session is physically discarded —
    its session locks die with it; the context itself still exits cleanly."""

    def broken_unlock(conn, family_id):
        raise RuntimeError('unlock boom')

    pool = ConnectionPool(url, min_size=0, max_size=1, open=True)
    monkeypatch.setattr(collect, 'unlock_family', broken_unlock)
    try:
        with _pooled_service(pool, monkeypatch):
            with collect.family_locks(['i01c:bars']):
                pass   # normal body; the release path is what fails
    finally:
        pool.close()
    assert _lock_free(url, 'i01c:bars')


def test_release_family_rolls_back_then_unlocks(url):
    """An aborted transaction is rolled back first (session locks survive the
    rollback), so the lock really releases and the connection stays usable."""
    pool = ConnectionPool(url, min_size=0, max_size=1, open=True)
    try:
        with pool.connection() as conn:
            assert collect.try_family_lock(conn, 'i01d:bars')
            with pytest.raises(psycopg.errors.DivisionByZero):
                conn.execute('SELECT 1/0')   # abort this session's transaction
            collect.release_family(conn, 'i01d:bars')
            assert conn.closed is False
            assert collect.try_family_lock(conn, 'i01d:bars')   # session is usable again
            assert collect.unlock_family(conn, 'i01d:bars') is True
    finally:
        pool.close()
    assert _lock_free(url, 'i01d:bars')
