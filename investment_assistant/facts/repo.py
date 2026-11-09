"""v3 fact-table SQL (migrations 032 and 034). Callers own transactions, as every layer repo."""

from __future__ import annotations

from datetime import date

from investment_assistant.core.dbutil import _all, _one, _plan_json

_RUN_COLUMNS = (
    "id, run_id, session, trigger, status, error, market, factors, coverage, holdings, "
    "dropped, draft_md, started_at, finished_at, created_at"
)
_CANDIDATE_COLUMNS = (
    "id, run_id, session, ticker, rank, screen_group, trigger_rules, rs, atr_pct, "
    "setup_tier, industry, industry_tier, reason, detail"
)
_VERDICT_COLUMNS = "id, session, ticker, verdict, reasons, counter, refs, origin, agent_session, created_at, updated_at"
_TRADE_COLUMNS = "id, request_id, ticker, side, quantity, price, fee, executed_at, plan_id, note, voided_at, created_at"
_TRADE_SELECT = (
    "t.id, t.request_id, t.ticker, t.side, t.quantity, t.price, t.fee, t.executed_at, "
    "t.plan_id, t.note, v.created_at AS voided_at, t.created_at"
)
_TRADE_VOID_JOIN = " LEFT JOIN v3_voids v ON v.kind = 'trade' AND v.target_id = t.id"
_EQUITY_COLUMNS = "id, request_id, as_of, equity, note, created_at"
_TOOL_CALL_COLUMNS = (
    "id, agent_session, mode, session, seq, tool, args, result_sha256, error, called_at, result, truncated"
)
_RUN_BRIEF_COLUMNS = (
    "status",
    "error",
    "runtime",
    "provider",
    "model",
    "usage",
    "elapsed_seconds",
    "pushed_final_at",
    "pushed_error_at",
    "pushed_weekly_at",
)


def insert_run(conn, row: dict) -> dict:
    saved = _one(
        conn,
        "INSERT INTO v3_runs (run_id, session, trigger, status, error, market, factors, "
        "coverage, holdings, dropped, draft_md, started_at, finished_at) "
        "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, "
        "%s, %s, %s) "
        f"RETURNING {_RUN_COLUMNS}",
        [
            row["run_id"],
            row["session"],
            row["trigger"],
            row["status"],
            row.get("error"),
            _plan_json(row.get("market") or {}),
            _plan_json(row.get("factors") or {}),
            _plan_json(row.get("coverage") or {}),
            _plan_json(row.get("holdings") or []),
            _plan_json(row.get("dropped") or []),
            row["draft_md"],
            row["started_at"],
            row["finished_at"],
        ],
    )
    assert saved is not None  # RETURNING always yields the inserted row
    return saved


def latest_run(conn, session: date | None = None) -> dict | None:
    """The latest run of that trading session; without one, the latest run overall."""
    where, params = (" WHERE session = %s", [session]) if session is not None else ("", [])
    return _one(conn, f"SELECT {_RUN_COLUMNS} FROM v3_runs{where} ORDER BY created_at DESC, id DESC LIMIT 1", params)


def latest_ok_run(conn) -> dict | None:
    """The latest run with status='ok' (the trading session the page and chats read)."""
    return _one(
        conn, f"SELECT {_RUN_COLUMNS} FROM v3_runs WHERE status = 'ok' ORDER BY created_at DESC, id DESC LIMIT 1", []
    )


def previous_ok_run(conn, before: date) -> dict | None:
    """The latest ok run of a trading session strictly before ``before`` (continuity's comparison target)."""
    return _one(
        conn,
        f"SELECT {_RUN_COLUMNS} FROM v3_runs WHERE status = 'ok' AND session < %s "
        "ORDER BY session DESC, created_at DESC, id DESC LIMIT 1",
        (before,),
    )


def runs_with_briefs(conn, *, limit: int) -> list[dict]:
    """Recent runs with the brief of their trading session, preserving SQL value types."""
    brief_columns = ", ".join(f"b.{name} AS brief_{name}" for name in _RUN_BRIEF_COLUMNS)
    rows = _all(
        conn,
        f"SELECT r.*, b.session AS brief_session, {brief_columns}, "
        "b.weekly_md IS NOT NULL AS brief_weekly_present "
        "FROM v3_runs r LEFT JOIN v3_briefs b ON b.session = r.session "
        "ORDER BY r.session DESC, r.started_at DESC LIMIT %s",
        (limit,),
    )
    for row in rows:
        brief_session = row.pop("brief_session")
        brief = {name: row.pop(f"brief_{name}") for name in (*_RUN_BRIEF_COLUMNS, "weekly_present")}
        row["brief"] = brief if brief_session is not None else None
    return rows


def insert_candidates(conn, run_id: str, rows: list[dict]) -> int:
    """`session` comes from the run row of `run_id`; the caller does not pass it."""
    run = _one(conn, "SELECT session FROM v3_runs WHERE run_id = %s", (run_id,))
    if run is None:
        raise ValueError(f"unknown v3 run: {run_id}")
    for row in rows:
        _one(
            conn,
            "INSERT INTO v3_candidates (run_id, session, ticker, rank, screen_group, trigger_rules, "
            "rs, atr_pct, setup_tier, industry, industry_tier, reason, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s::jsonb) RETURNING id",
            [
                run_id,
                run["session"],
                row["ticker"],
                row["rank"],
                row["screen_group"],
                _plan_json(row.get("trigger_rules") or []),
                row.get("rs"),
                row.get("atr_pct"),
                row["setup_tier"],
                row.get("industry"),
                row["industry_tier"],
                row["reason"],
                _plan_json(row.get("detail") or {}),
            ],
        )
    return len(rows)


def candidates(conn, run_id: str) -> list[dict]:
    return _all(conn, f"SELECT {_CANDIDATE_COLUMNS} FROM v3_candidates WHERE run_id = %s ORDER BY rank", (run_id,))


def candidate_at(conn, session: date, ticker: str) -> dict | None:
    """A ticker's candidate row of the latest run on that trading session (highest id), if any."""
    return _one(
        conn,
        f"SELECT {_CANDIDATE_COLUMNS} FROM v3_candidates WHERE session = %s AND ticker = %s ORDER BY id DESC LIMIT 1",
        (session, ticker),
    )


def upsert_verdict(conn, row: dict) -> dict:
    saved = _one(
        conn,
        "INSERT INTO v3_verdicts (session, ticker, verdict, reasons, counter, refs, origin, "
        "agent_session) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s) "
        "ON CONFLICT (session, ticker) DO UPDATE SET verdict = EXCLUDED.verdict, "
        "reasons = EXCLUDED.reasons, counter = EXCLUDED.counter, refs = EXCLUDED.refs, "
        "origin = EXCLUDED.origin, agent_session = EXCLUDED.agent_session, "
        "updated_at = clock_timestamp() "
        f"RETURNING {_VERDICT_COLUMNS}",
        [
            row["session"],
            row["ticker"],
            row["verdict"],
            row["reasons"],
            row.get("counter") or "",
            _plan_json(row.get("refs") or []),
            row["origin"],
            row["agent_session"],
        ],
    )
    assert saved is not None  # RETURNING always yields the upserted row
    return saved


def verdicts(conn, session: date) -> dict[str, dict]:
    """Verdicts of one trading session, keyed by ticker."""
    return {
        row["ticker"]: row
        for row in _all(conn, f"SELECT {_VERDICT_COLUMNS} FROM v3_verdicts WHERE session = %s", (session,))
    }


def insert_trade(conn, row: dict) -> dict:
    """Same request_id replayed returns the original row (idempotent recording)."""
    saved = _one(
        conn,
        "INSERT INTO v3_trades (request_id, ticker, side, quantity, price, fee, executed_at, "
        "plan_id, note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (request_id) DO NOTHING "
        f"RETURNING {_TRADE_COLUMNS}",
        [
            row["request_id"],
            row["ticker"],
            row["side"],
            row["quantity"],
            row["price"],
            row.get("fee", 0),
            row["executed_at"],
            row.get("plan_id"),
            row.get("note") or "",
        ],
    )
    if saved is None:
        saved = _one(conn, f"SELECT {_TRADE_COLUMNS} FROM v3_trades WHERE request_id = %s", (row["request_id"],))
    assert saved is not None  # the replayed row is always there
    return saved


def trade_by_request(conn, request_id: str) -> dict | None:
    """The trade already recorded under ``request_id``, if any (replays are answered before any re-check)."""
    return _one(conn, f"SELECT {_TRADE_COLUMNS} FROM v3_trades WHERE request_id = %s", (request_id,))


def list_trades(conn, ticker: str | None = None) -> list[dict]:
    """Trades oldest first, optionally one ticker's only; `voided_at` is the v3_voids row's time, if voided."""
    where, params = (" WHERE t.ticker = %s", [ticker]) if ticker is not None else ("", [])
    return _all(
        conn, f"SELECT {_TRADE_SELECT} FROM v3_trades t{_TRADE_VOID_JOIN}{where} ORDER BY t.executed_at, t.id", params
    )


def recent_trades(conn, *, limit: int) -> list[dict]:
    """Latest ledger trades, including the void record when one exists."""
    return _all(
        conn,
        f"SELECT {_TRADE_SELECT}, v.id AS voided_by, v.reason AS void_reason "
        f"FROM v3_trades t{_TRADE_VOID_JOIN} ORDER BY t.id DESC LIMIT %s",
        (limit,),
    )


def trade(conn, trade_id: int) -> dict | None:
    """One trade by id, `voided_at` from the v3_voids row if voided."""
    return _one(conn, f"SELECT {_TRADE_SELECT} FROM v3_trades t{_TRADE_VOID_JOIN} WHERE t.id = %s", (trade_id,))


def insert_equity(conn, row: dict) -> dict:
    """Same request_id replayed returns the original row (idempotent recording)."""
    saved = _one(
        conn,
        "INSERT INTO v3_equity (request_id, as_of, equity, note) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (request_id) DO NOTHING "
        f"RETURNING {_EQUITY_COLUMNS}",
        [row["request_id"], row["as_of"], row["equity"], row.get("note") or ""],
    )
    if saved is None:
        saved = _one(conn, f"SELECT {_EQUITY_COLUMNS} FROM v3_equity WHERE request_id = %s", (row["request_id"],))
    assert saved is not None  # the replayed row is always there
    return saved


def latest_equity(conn) -> dict | None:
    """The newest account equity by as_of, then by id."""
    return _one(conn, f"SELECT {_EQUITY_COLUMNS} FROM v3_equity ORDER BY as_of DESC, id DESC LIMIT 1", [])


def recent_equity(conn, *, limit: int) -> list[dict]:
    return _all(conn, f"SELECT {_EQUITY_COLUMNS} FROM v3_equity ORDER BY id DESC LIMIT %s", (limit,))


def next_seq(conn, agent_session: str) -> int:
    """The next call number of an agent session, starting at 1."""
    row = _one(
        conn, "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM v3_tool_calls WHERE agent_session = %s", (agent_session,)
    )
    assert row is not None  # COALESCE always yields one row
    return row["seq"]


def insert_tool_call(conn, row: dict) -> dict:
    saved = _one(
        conn,
        "INSERT INTO v3_tool_calls (agent_session, mode, session, seq, tool, args, "
        "result_sha256, error, result, truncated) "
        "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s) "
        f"RETURNING {_TOOL_CALL_COLUMNS}",
        [
            row["agent_session"],
            row["mode"],
            row["session"],
            row["seq"],
            row["tool"],
            _plan_json(row.get("args") or {}),
            row.get("result_sha256"),
            row.get("error"),
            _plan_json(row["result"]) if row.get("result") is not None else None,
            row.get("truncated", False),
        ],
    )
    assert saved is not None  # RETURNING always yields the inserted row
    return saved


def tool_call(conn, agent_session: str, seq: int) -> dict | None:
    return _one(
        conn,
        f"SELECT {_TOOL_CALL_COLUMNS} FROM v3_tool_calls WHERE agent_session = %s AND seq = %s",
        (agent_session, seq),
    )


def tool_call_refs(conn, agent_session: str) -> set[str]:
    """Stable citation ids of one agent session's calls: 'call:1', 'call:2', ..."""
    return {
        f"call:{row['seq']}"
        for row in _all(conn, "SELECT seq FROM v3_tool_calls WHERE agent_session = %s ORDER BY seq", (agent_session,))
    }
