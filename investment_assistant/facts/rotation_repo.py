"""v3.3 rotation-record SQL (migration 037). Append-only: no UPDATE or DELETE here. Callers own transactions."""

from __future__ import annotations

from datetime import date

from investment_assistant.core.dbutil import _all, _one, _plan_json

_ROTATION_COLUMNS = (
    "id, session, origin, candidate, verdict, holding_plan_id, holding_ticker, reasons, weakness, "
    "counter, cost_response, refs, snapshot, sell_quantity, buy_shares, agent_session, request_id, created_at"
)


def insert_rotation(conn, row: dict) -> dict:
    """One rotation record; the text columns without a value default to the empty string, ``refs`` to []."""
    saved = _one(
        conn,
        "INSERT INTO v3_rotations (session, origin, candidate, verdict, holding_plan_id, holding_ticker, "
        "reasons, weakness, counter, cost_response, refs, snapshot, sell_quantity, buy_shares, "
        "agent_session, request_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s) "
        f"RETURNING {_ROTATION_COLUMNS}",
        [
            row["session"],
            row["origin"],
            row["candidate"],
            row["verdict"],
            row.get("holding_plan_id"),
            row.get("holding_ticker"),
            row["reasons"],
            row.get("weakness") or "",
            row.get("counter") or "",
            row.get("cost_response") or "",
            _plan_json(row.get("refs") or []),
            _plan_json(row["snapshot"]),
            row.get("sell_quantity"),
            row.get("buy_shares"),
            row["agent_session"],
            row.get("request_id"),
        ],
    )
    assert saved is not None  # RETURNING always yields the inserted row
    return saved


def rotation(conn, rotation_id: int) -> dict | None:
    return _one(conn, f"SELECT {_ROTATION_COLUMNS} FROM v3_rotations WHERE id = %s", (rotation_id,))


def rotation_by_request(conn, request_id: str) -> dict | None:
    """The record written for ``request_id`` (idempotent replay lookup)."""
    return _one(conn, f"SELECT {_ROTATION_COLUMNS} FROM v3_rotations WHERE request_id = %s", (request_id,))


def rotations_on(conn, session: date) -> list[dict]:
    return _all(conn, f"SELECT {_ROTATION_COLUMNS} FROM v3_rotations WHERE session = %s ORDER BY id", (session,))


def effective_since(conn, since: date) -> list[dict]:
    """Half and full rotations from ``since`` (inclusive) on, oldest first; 'hold' records are not effective."""
    return _all(
        conn,
        f"SELECT {_ROTATION_COLUMNS} FROM v3_rotations WHERE verdict IN ('half', 'full') AND session >= %s "
        "ORDER BY session, id",
        (since,),
    )


def rotations(conn, *, limit: int | None = None) -> list[dict]:
    """Every record, newest first."""
    bounded = f" LIMIT {int(limit)}" if limit is not None else ""
    return _all(conn, f"SELECT {_ROTATION_COLUMNS} FROM v3_rotations ORDER BY session DESC, id DESC{bounded}", [])
