"""Shared SQL row helpers, JSON encoding and account scoping; caller owns transactions."""
from __future__ import annotations
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from psycopg.rows import dict_row
from psycopg.sql import Composable
from investment_assistant.core import schema as core_schema
from investment_assistant.core.schema import WORKBENCH_TABLES, GROWTH_TABLES
from investment_assistant.core.constants import REAL_ACCOUNT_ID

def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def lock(conn, key: str) -> None:
    """Transaction-scoped advisory lock serialising writes to one unit or ticker."""
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (key,))

def _account_columns_ready(conn) -> bool:
    """Inspect this connection's current schema, without caching across schemas or migration 030."""
    return bool(conn.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'trades'::regclass "
        "AND attname = 'account_id' AND NOT attisdropped)").fetchone()[0])


def _account_scope(conn, account_id=REAL_ACCOUNT_ID, *, alias: str = '', allow_all: bool = False
                   ) -> tuple[bool, str, list[Any]]:
    """SQL account predicate plus layout flag. Before 030 only the legacy default-real path is safe (§11)."""
    ready = _account_columns_ready(conn)
    if not ready:
        if str(account_id) != str(REAL_ACCOUNT_ID):
            raise RuntimeError('migration_030_missing')
        return False, '', []
    if account_id is None and allow_all:
        return True, '', []
    prefix = f'{alias}.' if alias else ''
    return True, f' AND {prefix}account_id = %s', [account_id]


def _one(conn, sql: str | Composable, params) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _all(conn, sql: str | Composable, params) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _plan_json(value: Any) -> str:
    """Canonical JSON with Decimals and dates as strings, stable across a jsonb round-trip."""
    def scalar(item: Any) -> Any:
        if isinstance(item, Decimal):
            return format(item.normalize(), 'f')
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if isinstance(item, uuid.UUID):
            return str(item)
        raise TypeError(f'not JSON-serializable: {type(item)!r}')

    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, default=scalar)


def missing_tables(conn) -> list[str]:
    return core_schema.missing_tables(conn, WORKBENCH_TABLES)
