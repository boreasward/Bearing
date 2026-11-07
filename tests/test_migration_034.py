"""Migration 034 reshapes v3_plans into the plan line and adds the v3.1 tables; DDL in disposable schemas."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import WORKBENCH_MIGRATIONS, _workbench_schema
from psycopg import errors

MIGRATION_034 = (Path(__file__).resolve().parents[1] / "migrations" / "034_v3_1_plan_line.sql").read_text(
    encoding="utf-8"
)


@pytest.fixture
def db_033():
    """The workbench schema as migration 033 left it, before 034 ever runs."""
    yield from _workbench_schema(tuple(name for name in WORKBENCH_MIGRATIONS if name != "034_v3_1_plan_line.sql"))


def _insert_plan(
    conn,
    *,
    ticker="AAA",
    status="pending",
    entry_low: float | None = 100,
    entry_high: float | None = 101,
    stop: float | None = 99,
    target1: float | None = 110,
    initial_stop: float | None = None,
    session="2026-11-09",
):
    """Raw insert shaped like the post-034 v3_plans row; the repo is not used here on purpose."""
    return conn.execute(
        "INSERT INTO v3_plans (session, ticker, origin, entry_type, entry_low, entry_high, stop, target1, "
        "initial_stop, levels_source, max_hold_sessions, valid_from, valid_until, thesis, invalidation, "
        "agent_session, status) VALUES (%s, %s, 'brief', 'pullback', %s, %s, %s, %s, %s, 'host', 20, %s, "
        "'2026-11-16', '论点', '失效条件', 'as-1', %s) RETURNING id",
        (session, ticker, entry_low, entry_high, stop, target1, initial_stop, session, status),
    )  # pi-lens-ignore: python-sql-injection -- every value is a %s parameter


def test_rerun_is_idempotent(v3_db):
    conn = v3_db
    _insert_plan(conn, ticker="AAA", status="open", initial_stop=99)
    conn.commit()
    conn.execute(MIGRATION_034)  # pi-lens-ignore: python-sql-injection -- trusted repo migration file, rerun on purpose
    conn.commit()
    row = conn.execute("SELECT status, initial_stop FROM v3_plans").fetchone()
    assert row == ("open", Decimal(99))  # 已有数据不被改写
    checks = conn.execute(
        "SELECT count(*) FROM pg_constraint WHERE conrelid = 'v3_plans'::regclass "
        "AND conname IN ('v3_plans_status_check', 'v3_plans_kind_check', "
        "'v3_plans_origin_check', 'v3_plans_sell_instruction_check', "
        "'v3_plans_levels_check')"
    ).fetchone()[0]
    assert checks == 5  # 约束重加后仍是各一份


def test_one_active_plan_per_ticker_is_a_db_constraint(v3_db):
    conn = v3_db
    _insert_plan(conn, ticker="AAA", status="pending")
    conn.commit()
    with pytest.raises(errors.UniqueViolation):
        _insert_plan(conn, ticker="AAA", status="open")
    conn.rollback()
    _insert_plan(conn, ticker="AAA", status="expired")  # 终态不占「一只股票一份有效计划」
    conn.commit()
    assert conn.execute("SELECT count(*) FROM v3_plans").fetchone()[0] == 2


def test_host_event_once_per_session_and_type(v3_db):
    conn = v3_db
    plan_id = _insert_plan(conn).fetchone()[0]
    conn.commit()
    conn.execute(
        "INSERT INTO v3_plan_events (plan_id, session, event, reason, source) "
        "VALUES (%s, '2026-11-09', 'expired', '到期', 'host')",
        (plan_id,),
    )
    conn.commit()
    with pytest.raises(errors.UniqueViolation):  # 宿主同计划同交易日同类型只记一次
        conn.execute(
            "INSERT INTO v3_plan_events (plan_id, session, event, reason, source) "
            "VALUES (%s, '2026-11-09', 'expired', '到期', 'host')",
            (plan_id,),
        )
    conn.rollback()
    conn.execute(
        "INSERT INTO v3_plan_events (plan_id, session, event, reason, source) "
        "VALUES (%s, '2026-11-09', 'expired', '复查', 'agent')",
        (plan_id,),
    )
    conn.execute(
        "INSERT INTO v3_plan_events (plan_id, session, event, reason, source) "
        "VALUES (%s, '2026-11-09', 'expired', '再次复查', 'agent')",
        (plan_id,),
    )
    conn.commit()
    assert conn.execute("SELECT count(*) FROM v3_plan_events WHERE source = 'agent'").fetchone()[0] == 2


def test_levels_all_null_or_ordered(v3_db):
    conn = v3_db
    _insert_plan(conn, ticker="BBB", entry_low=None, entry_high=None, stop=None, target1=None)
    conn.commit()  # 四个价位全空可插（缺日线的补录计划）
    with pytest.raises(errors.CheckViolation):
        _insert_plan(conn, ticker="CCC", stop=None)  # 只有 stop 空其他有值不行
    conn.rollback()


def test_032_rows_become_expired(db_033):
    conn = db_033
    conn.execute(
        "INSERT INTO v3_plans (session, ticker, origin, entry_type, entry_low, entry_high, stop, target1, "
        "levels_source, max_hold_sessions, valid_from, valid_until, thesis, invalidation, agent_session) "
        "VALUES ('2026-11-05', 'AAA', 'brief', 'pullback', 100, 101, 99, 110, 'host', 10, "
        "'2026-11-05', '2026-11-10', '论点', '失效条件', 'as-1')"
    )  # pi-lens-ignore: python-sql-injection -- fixed literal INSERT, no interpolation
    conn.commit()
    conn.execute(MIGRATION_034)  # pi-lens-ignore: python-sql-injection -- trusted repo migration file
    conn.commit()
    row = conn.execute("SELECT status, initial_stop, ended_session, end_reason FROM v3_plans").fetchone()
    assert row == ("expired", Decimal(99), date(2026, 11, 10), "migrated_034")
