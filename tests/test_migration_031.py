"""Migration 031: run real DDL only in a unique schema of the disposable test database."""

import json
import os
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

TEST_URL = "postgresql://test@127.0.0.1:55433/workbench_test"
ALLOWED_TEST_URLS = (TEST_URL, "postgresql://test@127.0.0.1:55433/workbench_v321_test")
MIGRATION = Path("migrations/031_five_layers.sql")
NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)
DAY = date(2026, 10, 30)
REAL = uuid.UUID("00000000-0000-0000-0000-000000000001")
TABLES = (
    "feature_flag_changes",
    "data_requests",
    "sector_classifications",
    "research_requests",
    "research_versions",
    "portfolio_checks",
    "roadmap_goals",
    "roadmap_versions",
    "roadmap_decisions",
    "roadmap_evidence",
    "roadmap_proposals",
    "roadmap_briefs",
)
JSONB = {"args", "result", "output", "review_when", "commentary"}


@pytest.fixture
def migrated_db():
    url = os.environ.get("INVESTMENT_ASSISTANT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("explicit disposable test database URL required")
    if url not in ALLOWED_TEST_URLS:
        pytest.fail(f"only the disposable test URLs are permitted: {ALLOWED_TEST_URLS}")
    import psycopg

    schema = f"m031_test_{uuid.uuid4().hex}"
    with psycopg.connect(url, connect_timeout=3) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.commit()
        try:
            conn.execute(f"SET search_path TO {schema}")
            for path in sorted(Path("migrations").glob("0*.sql")):
                if path.name < "031":
                    conn.execute(path.read_text(encoding="utf-8"))
            conn.commit()
            yield conn
        finally:
            conn.rollback()
            conn.execute(f"DROP SCHEMA {schema} CASCADE")
            conn.commit()


def apply(conn):
    conn.execute(MIGRATION.read_text(encoding="utf-8"))
    conn.commit()


def insert(conn, table, values, returning="id"):
    params = [json.dumps(v) if k in JSONB and v is not None else v for k, v in values.items()]
    marks = ["%s::jsonb" if k in JSONB else "%s" for k in values]
    with conn.transaction():
        return conn.execute(
            f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join(marks)}) RETURNING {returning}", params
        ).fetchone()[0]


def reject(conn, table, values, error="CheckViolation"):
    import psycopg

    with pytest.raises(getattr(psycopg.errors, error)):
        insert(conn, table, values)


def agent(conn, **overrides):
    return insert(
        conn,
        "agent_runs",
        {
            "id": uuid.uuid4(),
            "ticker": "NVDA",
            "signal_date": DAY,
            "pool": "main",
            "attempt": 1,
            "mode": "propose",
            "agent_version": "agent-4",
            "provider": "chatgpt",
            "model": "gpt-test",
            "cutoff_at": NOW,
            "status": "pass",
            "started_at": NOW,
            "finished_at": NOW,
            **overrides,
        },
    )


def catalog(conn):
    constraints = conn.execute(
        "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE connamespace = current_schema()::regnamespace ORDER BY 1, 2"
    ).fetchall()
    indexes = conn.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema() ORDER BY indexname"
    ).fetchall()
    return constraints, indexes


def test_applies_twice(migrated_db):
    conn = migrated_db
    old_run = agent(conn)  # A legacy row must survive both applications.
    apply(conn)
    before = catalog(conn)
    for table in TABLES:
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    apply(conn)
    assert catalog(conn) == before
    assert conn.execute("SELECT mode, status FROM agent_runs WHERE id = %s", (old_run,)).fetchone() == (
        "propose",
        "pass",
    )


def test_stage_check_has_thirteen(migrated_db):
    conn = migrated_db
    apply(conn)
    row = {
        "job": "daily",
        "trigger": "scheduled",
        "session": DAY,
        "stages_version": "stages-6",
        "status": "success",
        "input_count": 0,
        "output_count": 0,
        "started_at": NOW,
        "finished_at": NOW,
        "duration_ms": 1,
    }
    stages = (
        "sec_monitor",
        "collect",
        "screen",
        "regime",
        "signals",
        "plan",
        "agent",
        "review",
        "portfolio",
        "accounts",
        "push",
        "retro",
        "propose",
    )
    for stage in stages:
        assert insert(conn, "stage_runs", {**row, "run_id": str(uuid.uuid4()), "stage": stage})
    reject(conn, "stage_runs", {**row, "run_id": str(uuid.uuid4()), "stage": "bogus"})


def test_data_fetch_runs_family_and_trigger(migrated_db):
    conn = migrated_db
    apply(conn)
    row = {
        "job": "daily",
        "trigger": "scheduled",
        "scope": "family",
        "status": "success",
        "target_count": 0,
        "ok_count": 0,
        "started_at": NOW,
        "finished_at": NOW,
    }
    for family in ("bars", "news", "sec_filings", "fred", "universe", "earnings", "fundamentals", "sec_text", "sector"):
        assert insert(conn, "data_fetch_runs", {**row, "family": family})
    for family in ("atr", "bogus"):
        reject(conn, "data_fetch_runs", {**row, "family": family})
    for trigger in ("scheduled", "manual_batch", "manual_family", "manual_research", "data_request"):
        assert insert(conn, "data_fetch_runs", {**row, "family": "bars", "trigger": trigger})
    reject(conn, "data_fetch_runs", {**row, "family": "bars", "trigger": "bogus"})


def test_agent_runs_mode_and_phase(migrated_db):
    import psycopg

    conn = migrated_db
    # D39 keeps mode_plan and origin restrictions intact.
    preserved = conn.execute(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE "
        "(conrelid = 'agent_runs'::regclass AND conname = 'agent_runs_mode_plan_check') OR "
        "conrelid IN ('agent_attempt_resets'::regclass, 'agent_plan_versions'::regclass) ORDER BY 1"
    ).fetchall()
    apply(conn)
    assert (
        conn.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE "
            "(conrelid = 'agent_runs'::regclass AND conname = 'agent_runs_mode_plan_check') OR "
            "conrelid IN ('agent_attempt_resets'::regclass, 'agent_plan_versions'::regclass) ORDER BY 1"
        ).fetchall()
        == preserved
    )
    run = agent(conn, mode="research", status="pass", signal_date=None, pool=None)
    assert agent(conn, mode="research", status="ok", signal_date=None, pool=None)
    for mode in ("propose", "revise"):
        assert agent(conn, mode=mode, status="pass")
    for mode in ("interpret", "review", "retro", "discuss"):
        assert agent(conn, mode=mode, status="ok")
        with pytest.raises(psycopg.errors.CheckViolation):
            agent(conn, mode=mode, status="pass")
    for mode in ("portfolio", "pm", "optimize", "brief"):
        assert agent(conn, mode=mode, ticker=None, signal_date=None, pool=None, status="ok")
        with pytest.raises(psycopg.errors.CheckViolation):
            agent(conn, mode=mode, ticker="NVDA", status="ok")
        with pytest.raises(psycopg.errors.CheckViolation):
            agent(conn, mode=mode, ticker=None, status="pass")
    for changes in ({"mode": "bogus"}, {"mode": "research", "ticker": None}, {"status": "ok"}):
        with pytest.raises(psycopg.errors.CheckViolation):
            agent(conn, **changes)
    call = {
        "run_id": run,
        "tool": "get_price_bars",
        "args": {},
        "result": {},
        "result_sha256": "a" * 64,
        "called_at": NOW,
    }
    for seq, phase in enumerate(
        ("propose", "counter", "review", "retro", "discuss", "portfolio", "research", "pm", "optimize", "brief"), 1
    ):
        assert insert(conn, "agent_tool_calls", {**call, "seq": seq, "phase": phase})
    reject(conn, "agent_tool_calls", {**call, "seq": 11, "phase": "bogus"})


def test_feature_flag_changes_constraints(migrated_db):
    conn = migrated_db
    apply(conn)
    row = {"flag": "backup_sources", "enabled_after": False, "reason": "暂停备源", "request_id": uuid.uuid4()}
    first = insert(conn, "feature_flag_changes", row)
    assert conn.execute(
        "SELECT enabled_before, created_at IS NOT NULL FROM feature_flag_changes WHERE id = %s", (first,)
    ).fetchone() == (None, True)
    reject(conn, "feature_flag_changes", row, "UniqueViolation")
    for flag in ("company_researcher", "portfolio_manager", "optimize_proposals"):
        assert insert(conn, "feature_flag_changes", {**row, "flag": flag, "request_id": uuid.uuid4()})
    for changes in ({"flag": "bogus"}, {"reason": ""}, {"reason": "x" * 301}):
        reject(conn, "feature_flag_changes", {**row, "request_id": uuid.uuid4(), **changes})
    for key in ("enabled_after", "reason", "request_id"):
        reject(conn, "feature_flag_changes", {**row, key: None}, "NotNullViolation")
    assert insert(conn, "feature_flag_changes", {**row, "reason": "理" * 300, "request_id": uuid.uuid4()})


def test_data_requests_and_sectors(migrated_db):
    conn = migrated_db
    apply(conn)
    row = {"family": "bars", "ticker": "NVDA", "requested_at": NOW}
    queued = insert(conn, "data_requests", row)
    assert conn.execute("SELECT status FROM data_requests WHERE id = %s", (queued,)).fetchone() == ("queued",)
    for status in ("fetched", "failed", "deferred"):
        assert insert(conn, "data_requests", {**row, "status": status})
    reject(conn, "data_requests", {**row, "status": "bogus"})
    reject(conn, "data_requests", {**row, "agent_run_id": uuid.uuid4()}, "ForeignKeyViolation")
    reject(conn, "data_requests", {**row, "fetch_run_id": -1}, "ForeignKeyViolation")
    sector = {
        "ticker": "NVDA",
        "sic": "3674",
        "sic_description": "Semiconductors",
        '"group"': "technology",
        "source": "sec",
        "fetched_at": NOW,
    }
    assert insert(conn, "sector_classifications", sector)
    assert insert(conn, "sector_classifications", sector)  # Changed answers may append, never unique by ticker.
    assert (
        conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() "
            "AND indexname = 'idx_sector_classifications_ticker'"
        )
        .fetchone()[0]
        .endswith("(ticker, fetched_at DESC)")
    )


def test_research_and_portfolio_uniques(migrated_db):
    conn = migrated_db
    apply(conn)
    req = {
        "id": uuid.uuid4(),
        "ticker": "NVDA",
        "source": "manual",
        "status": "queued",
        "request_id": uuid.uuid4(),
        "created_at": NOW,
    }
    insert(conn, "research_requests", req)
    reject(conn, "research_requests", {**req, "id": uuid.uuid4()}, "UniqueViolation")
    assert conn.execute("SELECT attempts FROM research_requests WHERE id = %s", (req["id"],)).fetchone() == (0,)
    for status in ("running", "waiting_data", "concluded", "closed"):
        assert insert(
            conn,
            "research_requests",
            {**req, "id": uuid.uuid4(), "request_id": uuid.uuid4(), "status": status, "question": "问" * 500},
        )
    for changes in ({"source": "agent"}, {"status": "bogus"}, {"question": "x" * 501}):
        reject(conn, "research_requests", {**req, "id": uuid.uuid4(), "request_id": uuid.uuid4(), **changes})
    version = {"request_id": req["id"], "version_no": 1, "cutoff_at": NOW, "status": "concluded", "action": "buy"}
    insert(conn, "research_versions", version)
    reject(conn, "research_versions", version, "UniqueViolation")
    for n, (status, action) in enumerate((("waiting_data", "wait"), ("error", None), ("concluded", "pass")), 2):
        assert insert(conn, "research_versions", {**version, "version_no": n, "status": status, "action": action})
    for changes in ({"status": "bogus"}, {"action": "sell"}):
        reject(conn, "research_versions", {**version, "version_no": 5, **changes})
    reject(conn, "research_versions", {**version, "request_id": uuid.uuid4()}, "ForeignKeyViolation")
    check = {
        "account_id": REAL,
        "session": DAY,
        "run_id": "run-1",
        "cutoff_at": NOW,
        "version": "portfolio-check-1",
        "result": {},
    }
    insert(conn, "portfolio_checks", check)
    reject(conn, "portfolio_checks", check, "UniqueViolation")
    assert insert(conn, "portfolio_checks", {**check, "session": date(2026, 10, 29)})
    reject(conn, "portfolio_checks", {**check, "account_id": uuid.uuid4()}, "ForeignKeyViolation")


def test_roadmap_uniques_and_status_checks(migrated_db):
    conn = migrated_db
    apply(conn)
    records = {
        "roadmap_goals": {"goal_id": "goal-1", "version": 1, "body": "目标"},
        "roadmap_versions": {"version_id": "v2.12", "version": 1, "title": "数据层", "status": "planned"},
        "roadmap_decisions": {
            "decision_no": 114,
            "version": 1,
            "decided_on": DAY,
            "question": "如何推进",
            "decision": "按层实施",
        },
        "roadmap_evidence": {
            "evidence_id": "evidence-1",
            "version": 1,
            "version_id": "v2.12",
            "kind": "manual",
            "body": "一次性测试库验证",
        },
    }
    for table, row in records.items():
        assert insert(conn, table, row)
        reject(conn, table, row, "UniqueViolation")
        assert insert(conn, table, {**row, "version": 2})
    assert conn.execute("SELECT depends_on FROM roadmap_versions WHERE version = 1").fetchone() == ([],)
    assert conn.execute("SELECT version_ids FROM roadmap_decisions WHERE version = 1").fetchone() == ([],)
    for n, status in enumerate(("in_progress", "engineering_done", "user_acceptance_pending", "done", "dropped"), 3):
        assert insert(conn, "roadmap_versions", {**records["roadmap_versions"], "version": n, "status": status})
    reject(conn, "roadmap_versions", {**records["roadmap_versions"], "version": 9, "status": "bogus"})
    for n, kind in enumerate(("scheduled", "user_confirmed"), 3):
        assert insert(conn, "roadmap_evidence", {**records["roadmap_evidence"], "version": n, "kind": kind})
    reject(conn, "roadmap_evidence", {**records["roadmap_evidence"], "version": 9, "kind": "bogus"})
    proposal = {
        "source": "data",
        "title": "修复缺口",
        "change": "补齐数据",
        "rationale": "有缺口",
        "evidence_refs": ["fact:data_failures"],
        "request_id": uuid.uuid4(),
    }
    proposal_id = insert(conn, "roadmap_proposals", proposal)
    assert conn.execute("SELECT status FROM roadmap_proposals WHERE id = %s", (proposal_id,)).fetchone() == (
        "proposed",
    )
    reject(conn, "roadmap_proposals", proposal, "UniqueViolation")
    for status in ("approved", "rejected", "superseded"):
        assert insert(conn, "roadmap_proposals", {**proposal, "request_id": None, "status": status})
    for source in ("agent_quality", "route"):
        assert insert(conn, "roadmap_proposals", {**proposal, "request_id": None, "source": source})
    for changes in ({"status": "bogus"}, {"source": "bogus"}):
        reject(conn, "roadmap_proposals", {**proposal, "request_id": None, **changes})
    brief = {"version_id": "v2.12", "proposal_id": proposal_id, "status": "ok", "body": "实施说明"}
    assert insert(conn, "roadmap_briefs", brief)
    assert insert(conn, "roadmap_briefs", {**brief, "status": "error", "body": None, "reason": "quota"})
    reject(conn, "roadmap_briefs", {**brief, "status": "bogus"})
    reject(conn, "roadmap_briefs", {**brief, "proposal_id": -1}, "ForeignKeyViolation")


def test_price_bars_source_check(migrated_db):
    conn = migrated_db
    apply(conn)
    row = {
        "ticker": "NVDA",
        "price_date": DAY,
        "open": 100,
        "high": 102,
        "low": 99,
        "close": 101,
        "adj_close": 101,
        "volume": 100,
        "revision": 1,
        "fetched_at": NOW,
    }
    assert insert(conn, "price_bars", row)
    assert insert(conn, "price_bars", {**row, "revision": 2, "source": "stooq"})
    reject(conn, "price_bars", {**row, "revision": 3, "source": "bogus"})


def test_earnings_source_and_news_unchanged(migrated_db):
    conn = migrated_db
    before = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE "
        "conrelid = 'news_items'::regclass AND conname = 'news_items_source_check'"
    ).fetchone()
    apply(conn)
    assert (
        conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE "
            "conrelid = 'news_items'::regclass AND conname = 'news_items_source_check'"
        ).fetchone()
        == before
    )
    row = {"ticker": "NVDA", "status": "unknown", "fetched_at": NOW}
    assert insert(conn, "earnings_dates", row)
    assert insert(conn, "earnings_dates", {**row, "source": "nasdaq"})
    reject(conn, "earnings_dates", {**row, "source": "bogus"})


def test_replaces_renamed_legacy_checks(migrated_db):
    """Definition-based removal must work even when a deployed constraint has another name."""
    conn = migrated_db
    from psycopg import sql

    checks = (
        ("stage_runs", "stage_runs_stage_check"),
        ("data_fetch_runs", "data_fetch_runs_family_check"),
        ("data_fetch_runs", "data_fetch_runs_trigger_check"),
        ("agent_runs", "agent_runs_mode_check"),
        ("agent_runs", "agent_runs_interpret_no_pass_check"),
        ("agent_runs", "agent_runs_portfolio_ticker_check"),
        ("agent_tool_calls", "agent_tool_calls_phase_check"),
    )
    for table, name in checks:
        conn.execute(
            sql.SQL("ALTER TABLE {} RENAME CONSTRAINT {} TO {}").format(
                sql.Identifier(table), sql.Identifier(name), sql.Identifier("legacy_" + name)
            )
        )
    apply(conn)
    assert not conn.execute(
        "SELECT conname FROM pg_constraint WHERE connamespace = current_schema()::regnamespace "
        "AND conname LIKE 'legacy_%'"
    ).fetchall()
    assert agent(conn, mode="research", status="pass")
    assert agent(conn, mode="pm", ticker=None, status="ok")
