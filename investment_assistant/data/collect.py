"""The fetch-run bookkeeping the batch, the screen backfill and the manual fetch feed (v2.3 §6): the per-family
``Tally``, the append-only ``data_fetch_runs`` write and the per-family advisory locks.

Every fetch itself is a ``registry.run_adapter`` call (spec D15, R23): the per-family collector functions that lived here
(``bars_one`` / ``news_one`` / ``sec_listing`` / ``fred_series``) have no caller any more and are gone."""
from __future__ import annotations
from investment_assistant.data.tally import Tally as Tally, TARGETS_MAX as TARGETS_MAX
import investment_assistant.data.repo as data_repo

import time
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from investment_assistant.core import redact as families
from investment_assistant.core import tx as core_tx


# fetch-1 hooks (spec §6): the sleep every host hands to the adapters (``AdapterDeps.sleep``); tests swap it so a retry
# backoff costs nothing. The policy itself lives on each adapter's sources (``registry.run_adapter``).
FETCH_SLEEP = time.sleep


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
