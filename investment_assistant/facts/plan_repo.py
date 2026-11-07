"""v3.1 plan-line and daily-table SQL (migration 034). Callers own transactions, as every layer repo."""

from __future__ import annotations

from datetime import date

from investment_assistant.core.dbutil import _all, _one, _plan_json

_PLAN_COLUMNS = (
    "id, session, ticker, status, kind, origin, entry_type, entry_low, entry_high, stop, "
    'target1, initial_stop, atr, "trailing", confirmed_stop_order, levels_source, '
    "max_hold_sessions, valid_from, valid_until, shares, sizing, warnings, thesis, "
    "invalidation, counter, refs, agent_session, start_session, reviewed_through, "
    "stop_hit_session, target_hit_session, sell_instruction, ended_session, end_reason, "
    "outcome, created_at, updated_at"
)
_EVENT_COLUMNS = "id, plan_id, session, event, reason, before, after, source, refs, created_at"
_REVIEW_COLUMNS = "id, plan_id, session, verdict, reasons, counter, refs, agent_session, created_at, updated_at"
_CASH_FLOW_SELECT = (
    "c.id, c.request_id, c.kind, c.amount, c.effective_at, c.note, v.created_at AS voided_at, c.created_at"
)
_VOID_COLUMNS = "id, request_id, kind, target_id, reason, created_at"
_BRIEF_COLUMNS = (
    "id, session, run_id, status, error, draft_md, final_md, summary, agenda, runtime, "
    "provider, model, usage, elapsed_seconds, pushed_error_at, pushed_final_at, "
    "created_at, updated_at"
)
_CHAT_COLUMNS = "id, chat_id, title, runtime, provider, model, read_only, created_at, updated_at"
_MESSAGE_COLUMNS = (
    "id, chat_id, seq, role, content, session, agent_session, status, error, usage, tool_refs, created_at"
)

ACTIVE = ("pending", "open")


def insert_plan(conn, row: dict) -> dict:
    """One row of the plan line; ``status`` defaults to 'pending' and ``kind`` to 'planned'."""
    saved = _one(
        conn,
        "INSERT INTO v3_plans (session, ticker, origin, entry_type, entry_low, entry_high, "
        "stop, target1, initial_stop, atr, levels_source, max_hold_sessions, valid_from, "
        "valid_until, shares, sizing, warnings, thesis, invalidation, counter, refs, "
        'agent_session, status, kind, "trailing", confirmed_stop_order, start_session, '
        "reviewed_through, stop_hit_session, target_hit_session, sell_instruction, "
        "ended_session, end_reason, outcome) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, "
        "%s::jsonb, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "%s, %s::jsonb) "
        f"RETURNING {_PLAN_COLUMNS}",
        [
            row["session"],
            row["ticker"],
            row["origin"],
            row["entry_type"],
            row.get("entry_low"),
            row.get("entry_high"),
            row.get("stop"),
            row.get("target1"),
            row.get("initial_stop"),
            row.get("atr"),
            row["levels_source"],
            row["max_hold_sessions"],
            row["valid_from"],
            row["valid_until"],
            row.get("shares"),
            _plan_json(row.get("sizing") or {}),
            _plan_json(row.get("warnings") or []),
            row["thesis"],
            row["invalidation"],
            row.get("counter") or "",
            _plan_json(row.get("refs") or []),
            row["agent_session"],
            row.get("status", "pending"),
            row.get("kind", "planned"),
            row.get("trailing", False),
            row.get("confirmed_stop_order"),
            row.get("start_session"),
            row.get("reviewed_through"),
            row.get("stop_hit_session"),
            row.get("target_hit_session"),
            row.get("sell_instruction"),
            row.get("ended_session"),
            row.get("end_reason"),
            _plan_json(row.get("outcome") or {}),
        ],
    )
    assert saved is not None  # RETURNING always yields the inserted row
    return saved


UPDATABLE = (
    "status",
    "stop",
    "entry_low",
    "entry_high",
    "target1",
    "atr",
    "trailing",
    "confirmed_stop_order",
    "start_session",
    "reviewed_through",
    "stop_hit_session",
    "target_hit_session",
    "sell_instruction",
    "ended_session",
    "end_reason",
    "outcome",
    "valid_from",
    "valid_until",
    "shares",
    "sizing",
    "warnings",
    "thesis",
    "invalidation",
    "max_hold_sessions",
    "levels_source",
)
_JSONB_UPDATABLE = ("sizing", "warnings", "outcome")


def _set_clause(fields: dict, allowed: tuple[str, ...], jsonb: tuple[str, ...]) -> tuple[str, list]:
    """Whitelisted `SET` assignments with parameter placeholders; ``jsonb`` columns get the ::jsonb cast."""
    assignments, params = [], []
    for name in allowed:
        if name not in fields:
            continue
        column = f'"{name}"' if name == "trailing" else name  # trailing 是 PostgreSQL 保留字
        if name in jsonb:
            assignments.append(f"{column} = %s::jsonb")
            value = fields[name] if fields[name] is not None else ([] if name == "warnings" else {})
            params.append(_plan_json(value))
        else:
            assignments.append(f"{column} = %s")
            params.append(fields[name])
    return ", ".join(assignments), params


def update_plan(conn, plan_id: int, **fields) -> dict:
    """Change the plan's current values; unknown keys raise, `updated_at` always refreshes."""
    unknown = sorted(set(fields) - set(UPDATABLE))
    if unknown:
        raise ValueError(f"v3_plans columns not updatable: {unknown}")
    assignments, params = _set_clause(fields, UPDATABLE, _JSONB_UPDATABLE)
    saved = _one(
        conn,
        f"UPDATE v3_plans SET {assignments}, updated_at = clock_timestamp() WHERE id = %s RETURNING {_PLAN_COLUMNS}",
        [*params, plan_id],
    )
    assert saved is not None  # the plan row is always there
    return saved


def plan(conn, plan_id: int) -> dict | None:
    return _one(conn, f"SELECT {_PLAN_COLUMNS} FROM v3_plans WHERE id = %s", (plan_id,))


def active_plan(conn, ticker: str) -> dict | None:
    """The one valid plan (pending or open) of a ticker, if any."""
    return _one(
        conn,
        f"SELECT {_PLAN_COLUMNS} FROM v3_plans WHERE ticker = %s "
        f"AND status IN ('pending', 'open') ORDER BY id DESC LIMIT 1",
        (ticker,),
    )


def plans_by_status(conn, statuses: tuple[str, ...], *, limit: int | None = None) -> list[dict]:
    """Plans of the given statuses, newest first."""
    bounded = f" LIMIT {int(limit)}" if limit is not None else ""
    return _all(
        conn,
        f"SELECT {_PLAN_COLUMNS} FROM v3_plans WHERE status = ANY(%s) ORDER BY created_at DESC, id DESC{bounded}",
        (list(statuses),),
    )


def insert_event(conn, row: dict) -> dict | None:
    """One change record; a host rerun of the same (plan, session, event) returns None, it does not raise."""
    saved = _one(
        conn,
        "INSERT INTO v3_plan_events (plan_id, session, event, reason, before, after, "
        "source, refs) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb) "
        "ON CONFLICT (plan_id, session, event) WHERE source = 'host' DO NOTHING "
        f"RETURNING {_EVENT_COLUMNS}",
        [
            row["plan_id"],
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


def events(conn, plan_id: int) -> list[dict]:
    """One plan's change records in session order."""
    return _all(
        conn, f"SELECT {_EVENT_COLUMNS} FROM v3_plan_events WHERE plan_id = %s ORDER BY session, id", (plan_id,)
    )


def events_on(conn, session: date) -> list[dict]:
    return _all(conn, f"SELECT {_EVENT_COLUMNS} FROM v3_plan_events WHERE session = %s ORDER BY id", (session,))


def upsert_holding_review(conn, row: dict) -> dict:
    """One review per (plan, session); a rerun of the same session overwrites the verdict."""
    saved = _one(
        conn,
        "INSERT INTO v3_holding_reviews (plan_id, session, verdict, reasons, counter, "
        "refs, agent_session) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s) "
        "ON CONFLICT (plan_id, session) DO UPDATE SET verdict = EXCLUDED.verdict, "
        "reasons = EXCLUDED.reasons, counter = EXCLUDED.counter, refs = EXCLUDED.refs, "
        "agent_session = EXCLUDED.agent_session, updated_at = clock_timestamp() "
        f"RETURNING {_REVIEW_COLUMNS}",
        [
            row["plan_id"],
            row["session"],
            row["verdict"],
            row["reasons"],
            row.get("counter") or "",
            _plan_json(row.get("refs") or []),
            row["agent_session"],
        ],
    )
    assert saved is not None  # RETURNING always yields the upserted row
    return saved


def holding_reviews(conn, session: date) -> dict[int, dict]:
    """Holding reviews of one trading session, keyed by plan id."""
    return {
        row["plan_id"]: row
        for row in _all(conn, f"SELECT {_REVIEW_COLUMNS} FROM v3_holding_reviews WHERE session = %s", (session,))
    }


def insert_cash_flow(conn, row: dict) -> dict:
    """Same request_id replayed returns the original row (idempotent recording)."""
    saved = _one(
        conn,
        "INSERT INTO v3_cash_flows (request_id, kind, amount, effective_at, note) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (request_id) DO NOTHING "
        "RETURNING id, request_id, kind, amount, effective_at, note, created_at",
        [row["request_id"], row["kind"], row["amount"], row["effective_at"], row.get("note") or ""],
    )
    if saved is None:
        saved = _one(
            conn,
            "SELECT id, request_id, kind, amount, effective_at, note, created_at "
            "FROM v3_cash_flows WHERE request_id = %s",
            (row["request_id"],),
        )
    assert saved is not None  # the replayed row is always there
    return saved


def cash_flows(conn) -> list[dict]:
    """Every cash flow with its ``voided_at`` (from the v3_voids row, if any), effective_at first."""
    return _all(
        conn,
        f"SELECT {_CASH_FLOW_SELECT} FROM v3_cash_flows c "
        "LEFT JOIN v3_voids v ON v.kind = 'cash_flow' AND v.target_id = c.id "
        "ORDER BY c.effective_at, c.id",
        [],
    )


def cash_flow(conn, flow_id: int) -> dict | None:
    return _one(
        conn,
        f"SELECT {_CASH_FLOW_SELECT} FROM v3_cash_flows c "
        "LEFT JOIN v3_voids v ON v.kind = 'cash_flow' AND v.target_id = c.id "
        "WHERE c.id = %s",
        (flow_id,),
    )


def insert_void(conn, row: dict) -> dict:
    """Same request_id replayed returns the original row (idempotent recording)."""
    saved = _one(
        conn,
        "INSERT INTO v3_voids (request_id, kind, target_id, reason) VALUES (%s, %s, %s, %s) "
        f"ON CONFLICT (request_id) DO NOTHING RETURNING {_VOID_COLUMNS}",
        [row["request_id"], row["kind"], row["target_id"], row.get("reason") or ""],
    )
    if saved is None:
        saved = _one(conn, f"SELECT {_VOID_COLUMNS} FROM v3_voids WHERE request_id = %s", (row["request_id"],))
    assert saved is not None  # the replayed row is always there
    return saved


def voids(conn) -> list[dict]:
    return _all(conn, f"SELECT {_VOID_COLUMNS} FROM v3_voids ORDER BY id", [])


BRIEF_UPDATABLE = (
    "run_id",
    "status",
    "error",
    "draft_md",
    "final_md",
    "summary",
    "agenda",
    "runtime",
    "provider",
    "model",
    "usage",
    "elapsed_seconds",
    "pushed_error_at",
    "pushed_final_at",
)
_BRIEF_JSONB = ("agenda", "usage")


def upsert_brief(conn, row: dict) -> dict:
    """One brief per trading session; pushed_* timestamps are never touched here (only update_brief sets them)."""
    saved = _one(
        conn,
        "INSERT INTO v3_briefs (session, run_id, status, error, draft_md, final_md, "
        "summary, agenda, runtime, provider, model, usage, elapsed_seconds) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s) "
        "ON CONFLICT (session) DO UPDATE SET run_id = EXCLUDED.run_id, "
        "status = EXCLUDED.status, error = EXCLUDED.error, draft_md = EXCLUDED.draft_md, "
        "final_md = EXCLUDED.final_md, summary = EXCLUDED.summary, "
        "agenda = EXCLUDED.agenda, runtime = EXCLUDED.runtime, "
        "provider = EXCLUDED.provider, model = EXCLUDED.model, usage = EXCLUDED.usage, "
        "elapsed_seconds = EXCLUDED.elapsed_seconds, updated_at = clock_timestamp() "
        f"RETURNING {_BRIEF_COLUMNS}",
        [
            row["session"],
            row.get("run_id"),
            row["status"],
            row.get("error"),
            row.get("draft_md") or "",
            row.get("final_md"),
            row.get("summary"),
            _plan_json(row.get("agenda") or {}),
            row.get("runtime"),
            row.get("provider"),
            row.get("model"),
            _plan_json(row.get("usage") or {}),
            row.get("elapsed_seconds"),
        ],
    )
    assert saved is not None  # RETURNING always yields the upserted row
    return saved


def update_brief(conn, session: date, **fields) -> dict:
    """Change only the given brief columns; unknown keys raise, `updated_at` always refreshes."""
    unknown = sorted(set(fields) - set(BRIEF_UPDATABLE))
    if unknown:
        raise ValueError(f"v3_briefs columns not updatable: {unknown}")
    assignments, params = _set_clause(fields, BRIEF_UPDATABLE, _BRIEF_JSONB)
    saved = _one(
        conn,
        f"UPDATE v3_briefs SET {assignments}, updated_at = clock_timestamp() "
        f"WHERE session = %s RETURNING {_BRIEF_COLUMNS}",
        [*params, session],
    )
    assert saved is not None  # the brief row is always there
    return saved


def brief(conn, session: date) -> dict | None:
    return _one(conn, f"SELECT {_BRIEF_COLUMNS} FROM v3_briefs WHERE session = %s", (session,))


def briefs(conn, *, limit: int = 30) -> list[dict]:
    """Recent briefs, newest session first."""
    return _all(conn, f"SELECT {_BRIEF_COLUMNS} FROM v3_briefs ORDER BY session DESC LIMIT {int(limit)}", [])


def insert_chat(conn, row: dict) -> dict:
    saved = _one(
        conn,
        "INSERT INTO v3_chats (chat_id, title, runtime, provider, model, read_only) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        f"RETURNING {_CHAT_COLUMNS}",
        [
            row["chat_id"],
            row.get("title") or "",
            row["runtime"],
            row["provider"],
            row["model"],
            row.get("read_only", False),
        ],
    )
    assert saved is not None  # RETURNING always yields the inserted row
    return saved


def chats(conn, *, limit: int = 30) -> list[dict]:
    """Recent chats, newest first."""
    return _all(conn, f"SELECT {_CHAT_COLUMNS} FROM v3_chats ORDER BY created_at DESC, id DESC LIMIT {int(limit)}", [])


def chat(conn, chat_id: str) -> dict | None:
    return _one(conn, f"SELECT {_CHAT_COLUMNS} FROM v3_chats WHERE chat_id = %s", (chat_id,))


CHAT_UPDATABLE = ("title", "read_only")


def update_chat(conn, chat_id: str, **fields) -> dict:
    """Change only the given chat columns (e.g. mark it read_only after a runtime switch)."""
    unknown = sorted(set(fields) - set(CHAT_UPDATABLE))
    if unknown:
        raise ValueError(f"v3_chats columns not updatable: {unknown}")
    assignments, params = _set_clause(fields, CHAT_UPDATABLE, ())
    saved = _one(
        conn,
        f"UPDATE v3_chats SET {assignments}, updated_at = clock_timestamp() "
        f"WHERE chat_id = %s RETURNING {_CHAT_COLUMNS}",
        [*params, chat_id],
    )
    assert saved is not None  # the chat row is always there
    return saved


def next_message_seq(conn, chat_id: str) -> int:
    """The next message number of a chat, starting at 1."""
    row = _one(conn, "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM v3_chat_messages WHERE chat_id = %s", (chat_id,))
    assert row is not None  # COALESCE always yields one row
    return row["seq"]


def insert_message(conn, row: dict) -> dict:
    saved = _one(
        conn,
        "INSERT INTO v3_chat_messages (chat_id, seq, role, content, session, "
        "agent_session, status, error, usage, tool_refs) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb) "
        f"RETURNING {_MESSAGE_COLUMNS}",
        [
            row["chat_id"],
            row["seq"],
            row["role"],
            row["content"],
            row.get("session"),
            row.get("agent_session"),
            row["status"],
            row.get("error"),
            _plan_json(row.get("usage") or {}),
            _plan_json(row.get("tool_refs") or []),
        ],
    )
    assert saved is not None  # RETURNING always yields the inserted row
    return saved


def messages(conn, chat_id: str) -> list[dict]:
    """One chat's messages in conversation order."""
    return _all(conn, f"SELECT {_MESSAGE_COLUMNS} FROM v3_chat_messages WHERE chat_id = %s ORDER BY seq", (chat_id,))


def settings(conn) -> dict[str, str]:
    """The runtime settings store as a plain dict; an empty store is an empty dict."""
    return {row["key"]: row["value"] for row in _all(conn, "SELECT key, value FROM v3_settings ORDER BY key", [])}


def set_setting(conn, key: str, value: str) -> None:
    _one(
        conn,
        "INSERT INTO v3_settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
        "updated_at = clock_timestamp() RETURNING key",
        (key, value),
    )
