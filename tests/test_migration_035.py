"""Migration 035 adds the v3.2 research tables and the weekly brief columns; DDL in disposable schemas."""

from pathlib import Path

import pytest
from psycopg import errors

MIGRATION_035 = (Path(__file__).resolve().parents[1] / "migrations" / "035_v3_2_research_and_retro.sql").read_text(
    encoding="utf-8"
)


def _insert_research(
    conn,
    *,
    ticker="AAA",
    thesis="论点",
    origin="chat",
    status="watching",
    created_session="2026-11-09",
    valid_until="2026-12-04",
):
    """Raw insert shaped like the v3_research row; the repo is not used here on purpose."""
    return conn.execute(
        "INSERT INTO v3_research (ticker, thesis, origin, status, created_session, valid_until) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (ticker, thesis, origin, status, created_session, valid_until),
    )  # pi-lens-ignore: python-sql-injection -- every value is a %s parameter


def test_rerun_is_idempotent(v3_db):
    conn = v3_db
    _insert_research(conn)
    conn.commit()
    conn.execute(MIGRATION_035)  # pi-lens-ignore: python-sql-injection -- trusted repo migration file, rerun on purpose
    conn.commit()
    row = conn.execute("SELECT ticker, status, thesis FROM v3_research").fetchone()
    assert row == ("AAA", "watching", "论点")  # 已有数据不被改写
    indexes = conn.execute(
        "SELECT count(*) FROM pg_indexes WHERE schemaname = current_schema() AND indexname IN ("
        "'uq_v3_research_one_watching_per_ticker', 'idx_v3_research_status', "
        "'uq_v3_research_events_host_once', 'idx_v3_research_events_session')"
    ).fetchone()[0]
    assert indexes == 4  # 索引重跑后仍是各一份


def test_one_watching_research_per_ticker_is_a_db_constraint(v3_db):
    conn = v3_db
    _insert_research(conn, ticker="AAA", status="watching")
    conn.commit()
    with pytest.raises(errors.UniqueViolation):
        _insert_research(conn, ticker="AAA", status="watching")
    conn.rollback()
    _insert_research(conn, ticker="AAA", status="rejected")  # 终态不占「一只股票一份 watching 研究」
    conn.commit()
    assert conn.execute("SELECT count(*) FROM v3_research").fetchone()[0] == 2


def test_host_research_event_once_per_session_and_type(v3_db):
    conn = v3_db
    research_id = _insert_research(conn).fetchone()[0]
    conn.commit()
    conn.execute(
        "INSERT INTO v3_research_events (research_id, session, event, reason, source) "
        "VALUES (%s, '2026-11-09', 'concluded', '结论 ready', 'host')",
        (research_id,),
    )
    conn.commit()
    with pytest.raises(errors.UniqueViolation):  # 宿主同研究同交易日同类型只记一次
        conn.execute(
            "INSERT INTO v3_research_events (research_id, session, event, reason, source) "
            "VALUES (%s, '2026-11-09', 'concluded', '结论 ready', 'host')",
            (research_id,),
        )
    conn.rollback()
    conn.execute(
        "INSERT INTO v3_research_events (research_id, session, event, reason, source) "
        "VALUES (%s, '2026-11-09', 'concluded', '复查', 'agent')",
        (research_id,),
    )
    conn.execute(
        "INSERT INTO v3_research_events (research_id, session, event, reason, source) "
        "VALUES (%s, '2026-11-09', 'concluded', '再次复查', 'agent')",
        (research_id,),
    )
    conn.commit()
    assert conn.execute("SELECT count(*) FROM v3_research_events WHERE source = 'agent'").fetchone()[0] == 2


def test_plans_research_id_and_briefs_weekly_columns_exist(v3_db):
    conn = v3_db
    plan_columns = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'v3_plans'"
        )
    }
    brief_columns = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'v3_briefs'"
        )
    }
    assert "research_id" in plan_columns
    assert {"weekly_md", "pushed_weekly_at"} <= brief_columns
