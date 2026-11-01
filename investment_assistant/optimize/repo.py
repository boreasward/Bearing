"""Layer-owned SQL. Callers own transactions."""
from __future__ import annotations
from typing import Any
from investment_assistant.core.dbutil import _one, _all, _canonical


_BACKCHECK_COLUMNS = 'id, calc_version, params, stats, created_at'


def insert_backcheck(conn, calc_version: str, params: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    """Freeze one backcheck run; rows are never rewritten."""
    return _one(conn, 'INSERT INTO rule_backchecks (calc_version, params, stats) '
                      f'VALUES (%s, %s::jsonb, %s::jsonb) RETURNING {_BACKCHECK_COLUMNS}',
                (calc_version, _canonical(params), _canonical(stats)))


def list_backchecks(conn, limit: int) -> list[dict[str, Any]]:
    return _all(conn, f'SELECT {_BACKCHECK_COLUMNS} FROM rule_backchecks ORDER BY created_at DESC LIMIT %s',
                (limit,))
