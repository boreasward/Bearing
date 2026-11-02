"""Append-only feature flags (migration 031); callers own the connection and transaction."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from investment_assistant.core.idempotency import Conflict

FLAGS = ('backup_sources', 'company_researcher', 'portfolio_manager', 'optimize_proposals')
_COLUMNS = 'id, flag, enabled_before, enabled_after, reason, request_id, created_at'


def _validate_flag(flag: str) -> None:
    if flag not in FLAGS:
        raise ValueError(f"flag must be one of {', '.join(FLAGS)}")


def current(conn) -> dict[str, bool]:
    """One latest row per flag, by id; a flag without history is enabled."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT DISTINCT ON (flag) flag, enabled_after FROM feature_flag_changes '
                    'ORDER BY flag, id DESC')
        values = {row['flag']: row['enabled_after'] for row in cur.fetchall()}
    return {flag: values.get(flag, True) for flag in FLAGS}


def record_change(conn, *, flag: str, enabled: bool, reason: str, request_id) -> dict[str, Any]:
    """Append a change, or replay the original row; reused IDs with different content conflict.

    Like account parameter writes, a transaction advisory lock covers the replay check and the
    previous-value read. Feature settings are low-volume: one lock also serializes request IDs
    reused across different flags. No commit/rollback here; the caller owns the transaction.
    """
    _validate_flag(flag)
    if not isinstance(enabled, bool):
        raise ValueError('enabled must be a boolean')
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('reason is required')
    reason = reason.strip()
    if len(reason) > 300:
        raise ValueError('reason must be at most 300 characters')
    try:
        request_id = UUID(str(request_id))
    except (ValueError, TypeError):
        raise ValueError('request_id must be a UUID') from None

    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', ('feature_flag_changes',))
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f'SELECT {_COLUMNS} FROM feature_flag_changes WHERE request_id = %s', (request_id,))
        existing = cur.fetchone()
        if existing is not None:
            if (existing['flag'], existing['enabled_after'], existing['reason']) != (flag, enabled, reason):
                raise Conflict('request_id was already used with different content')
            return existing
        before = current(conn)[flag]
        cur.execute('INSERT INTO feature_flag_changes (flag, enabled_before, enabled_after, reason, request_id) '
                    f'VALUES (%s, %s, %s, %s, %s) RETURNING {_COLUMNS}',
                    (flag, before, enabled, reason, request_id))
        return cur.fetchone()


def history(conn, flag: str | None = None) -> list[dict[str, Any]]:
    """Changes in append order (oldest first), optionally for one known flag."""
    if flag is not None:
        _validate_flag(flag)
    where, params = (' WHERE flag = %s', (flag,)) if flag is not None else ('', ())
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f'SELECT {_COLUMNS} FROM feature_flag_changes{where} ORDER BY id', params)
        return cur.fetchall()


def frozen(conn) -> dict[str, bool]:
    """A fresh snapshot for initial_state; subsequent changes do not mutate it."""
    return current(conn)
