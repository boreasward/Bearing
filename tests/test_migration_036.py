"""Migration 036 preserves old tool calls and adds optional result storage."""

from pathlib import Path

import pytest
from conftest import WORKBENCH_MIGRATIONS, _workbench_schema

MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / "036_v3_2_1_tool_results.sql"


@pytest.fixture
def db_035():
    yield from _workbench_schema(tuple(name for name in WORKBENCH_MIGRATIONS if name < MIGRATION_PATH.name))


def test_first_application_preserves_legacy_call_and_rerun_is_idempotent(db_035):
    conn = db_035
    assert (
        conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'v3_tool_calls' "
            "AND column_name IN ('result', 'truncated')"
        ).fetchall()
        == []
    )
    conn.execute(
        "INSERT INTO v3_tool_calls (agent_session, mode, session, seq, tool, args, result_sha256) "
        "VALUES ('old-call', 'chat', '2026-11-09', 1, 'get_market', '{\"ticker\":\"SPY\"}'::jsonb, %s)",
        ("a" * 64,),
    )
    conn.commit()
    before = conn.execute("SELECT * FROM v3_tool_calls").fetchone()
    migration = MIGRATION_PATH.read_text(encoding="utf-8")
    conn.execute(migration)  # pi-lens-ignore: python-sql-injection -- trusted repo migration, first application
    conn.commit()
    after = conn.execute("SELECT * FROM v3_tool_calls").fetchone()
    assert after[:-2] == before
    assert after[-2:] == (None, False)
    conn.execute(migration)  # pi-lens-ignore: python-sql-injection -- trusted repo migration, rerun on purpose
    conn.commit()
    assert conn.execute("SELECT * FROM v3_tool_calls").fetchone() == after


def test_new_columns_exist(v3_db):
    assert MIGRATION_PATH.is_file()
    columns = dict(
        v3_db.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'v3_tool_calls' "
            "AND column_name IN ('result', 'truncated')"
        )
    )
    assert columns == {"result": "jsonb", "truncated": "boolean"}
