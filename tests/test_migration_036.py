"""Migration 036 preserves old tool calls and adds optional result storage."""

from pathlib import Path

MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / "036_v3_2_1_tool_results.sql"


def test_rerun_is_idempotent(v3_db):
    conn = v3_db
    conn.execute(
        "INSERT INTO v3_tool_calls (agent_session, mode, session, seq, tool, args) "
        "VALUES ('old-call', 'chat', '2026-11-09', 1, 'get_market', '{}'::jsonb)"
    )
    conn.commit()
    migration = MIGRATION_PATH.read_text(encoding="utf-8")
    conn.execute(migration)  # pi-lens-ignore: python-sql-injection -- trusted repo migration, rerun on purpose
    conn.execute(migration)  # pi-lens-ignore: python-sql-injection -- trusted repo migration, rerun on purpose
    conn.commit()
    assert conn.execute("SELECT result, truncated FROM v3_tool_calls").fetchone() == (None, False)


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
