"""Shared SQL row helpers and JSON encoding; caller owns transactions."""
from __future__ import annotations
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from psycopg.rows import dict_row
from psycopg.sql import Composable


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
