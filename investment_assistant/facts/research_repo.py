"""v3.2 research-tracking SQL (migration 035). Callers own transactions, as every layer repo."""

from __future__ import annotations

from datetime import date

from investment_assistant.core.dbutil import _all, _one, _plan_json
from investment_assistant.facts.plan_repo import _set_clause

_RESEARCH_COLUMNS = (
    "id, ticker, thesis, status, origin, created_session, valid_until, condition, condition_text, "
    "verdict, reasons, counter, refs, agent_session, checked_through, triggered_session, plan_id, "
    "ended_session, end_reason, created_at, updated_at"
)
_EVENT_COLUMNS = "id, research_id, session, event, reason, before, after, source, refs, created_at"


def insert_research(conn, row: dict) -> dict:
    """One research row; ``status`` defaults to 'watching', the remaining columns to their blanks."""
    saved = _one(
        conn,
        "INSERT INTO v3_research (ticker, thesis, status, origin, created_session, valid_until, "
        "condition, condition_text, verdict, reasons, counter, refs, agent_session, checked_through, "
        "triggered_session, plan_id, ended_session, end_reason) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        f"RETURNING {_RESEARCH_COLUMNS}",
        [
            row["ticker"],
            row["thesis"],
            row.get("status", "watching"),
            row["origin"],
            row["created_session"],
            row["valid_until"],
            _plan_json(row["condition"]) if row.get("condition") is not None else None,
            row.get("condition_text"),
            row.get("verdict"),
            row.get("reasons") or "",
            row.get("counter") or "",
            _plan_json(row.get("refs") or []),
            row.get("agent_session"),
            row.get("checked_through"),
            row.get("triggered_session"),
            row.get("plan_id"),
            row.get("ended_session"),
            row.get("end_reason"),
        ],
    )
    assert saved is not None  # RETURNING always yields the inserted row
    return saved


UPDATABLE = (
    "status",
    "valid_until",
    "condition",
    "condition_text",
    "verdict",
    "reasons",
    "counter",
    "refs",
    "agent_session",
    "checked_through",
    "triggered_session",
    "plan_id",
    "ended_session",
    "end_reason",
)
_JSONB_UPDATABLE = ("condition", "refs")


def update_research(conn, research_id: int, **fields) -> dict:
    """Change the research's current values; unknown keys raise, `updated_at` always refreshes."""
    unknown = sorted(set(fields) - set(UPDATABLE))
    if unknown:
        raise ValueError(f"v3_research columns not updatable: {unknown}")
    assignments, params = _set_clause(fields, UPDATABLE, _JSONB_UPDATABLE)
    saved = _one(
        conn,
        f"UPDATE v3_research SET {assignments}, updated_at = clock_timestamp() "
        f"WHERE id = %s RETURNING {_RESEARCH_COLUMNS}",
        [*params, research_id],
    )
    assert saved is not None  # the research row is always there
    return saved


def research(conn, research_id: int) -> dict | None:
    return _one(conn, f"SELECT {_RESEARCH_COLUMNS} FROM v3_research WHERE id = %s", (research_id,))


def watching_research(conn, ticker: str) -> dict | None:
    """The one watching research of a ticker, if any."""
    return _one(
        conn,
        f"SELECT {_RESEARCH_COLUMNS} FROM v3_research WHERE ticker = %s AND status = 'watching' "
        "ORDER BY id DESC LIMIT 1",
        (ticker,),
    )


def research_by_status(conn, statuses: tuple[str, ...], *, limit: int | None = None) -> list[dict]:
    """Research of the given statuses, newest first."""
    bounded = f" LIMIT {int(limit)}" if limit is not None else ""
    return _all(
        conn,
        f"SELECT {_RESEARCH_COLUMNS} FROM v3_research WHERE status = ANY(%s) "
        f"ORDER BY created_at DESC, id DESC{bounded}",
        (list(statuses),),
    )


def watching_tickers(conn) -> list[str]:
    """The tickers with a watching research, sorted and deduplicated."""
    return [
        row["ticker"]
        for row in _all(conn, "SELECT DISTINCT ticker FROM v3_research WHERE status = 'watching' ORDER BY ticker", [])
    ]


def insert_research_event(conn, row: dict) -> dict | None:
    """One change record; a host rerun of the same (research, session, event) returns None, it does not raise."""
    saved = _one(
        conn,
        "INSERT INTO v3_research_events (research_id, session, event, reason, before, after, "
        "source, refs) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb) "
        "ON CONFLICT (research_id, session, event) WHERE source = 'host' DO NOTHING "
        f"RETURNING {_EVENT_COLUMNS}",
        [
            row["research_id"],
            row["session"],
            row["event"],
            row["reason"],
            _plan_json(row.get("before") or {}),
            _plan_json(row.get("after") or {}),
            row["source"],
            _plan_json(row.get("refs") or []),
        ],
    )
    return saved  # None exactly when the host row is already there


def research_events(conn, research_id: int) -> list[dict]:
    """One research's change records in session order."""
    return _all(
        conn,
        f"SELECT {_EVENT_COLUMNS} FROM v3_research_events WHERE research_id = %s ORDER BY session, id",
        (research_id,),
    )


def research_events_on(conn, session: date) -> list[dict]:
    return _all(conn, f"SELECT {_EVENT_COLUMNS} FROM v3_research_events WHERE session = %s ORDER BY id", (session,))
