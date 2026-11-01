import investment_assistant.core.dbutil as core_dbutil
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

MIGRATION = Path('migrations/025_stage_runs.sql')

NOW = datetime(2026, 10, 26, 22, 30, tzinfo=UTC)


@pytest.fixture
def migrated_db():
    """Migrations < 025 in a throwaway schema, dropped afterwards; explicit test URL only."""
    url = os.environ.get('INVESTMENT_ASSISTANT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('explicit test database URL required; disposable schema only')
    import psycopg
    schema = f'm025_test_{uuid.uuid4().hex[:12]}'
    with psycopg.connect(url) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
        conn.execute(f'SET search_path TO {schema}')
        for path in sorted(Path('migrations').glob('0*.sql')):
            if path.name < '025':
                conn.execute(path.read_text(encoding='utf-8'))
        conn.commit()
        try:
            yield conn
        finally:
            conn.rollback()
            conn.execute(f'DROP SCHEMA {schema} CASCADE')
            conn.commit()


def _apply(conn):
    conn.execute(MIGRATION.read_text(encoding='utf-8'))
    conn.commit()


def _insert(conn, **overrides):
    """Insert one stage row with savepoint isolation; JSONB columns stay out so DB defaults apply."""
    values = {'run_id': 'r1', 'job': 'workbench', 'trigger': 'scheduled', 'session': None,
              'stage': 'collect', 'stages_version': 'stages-1', 'status': 'success', 'reason': None,
              'input_count': 1, 'output_count': 1,
              'started_at': NOW, 'finished_at': NOW + timedelta(seconds=30), 'duration_ms': 30000}
    values.update(overrides)
    cols = ', '.join(values)
    marks = ', '.join(['%s'] * len(values))
    with conn.transaction():
        conn.execute(f'INSERT INTO stage_runs ({cols}) VALUES ({marks})', list(values.values()))


def test_stage_runs_table_and_checks(migrated_db):
    import psycopg
    conn = migrated_db
    _apply(conn)
    _insert(conn)  # the base row is legal
    row = conn.execute('SELECT skips, failures, degradations, outputs, detail FROM stage_runs '
                       'ORDER BY id DESC LIMIT 1').fetchone()
    assert row == ([], [], [], {}, {})
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, stage='bogus')
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, status='done')
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, trigger='manual_family')
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, job='filings')
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, input_count=-1)
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, finished_at=NOW - timedelta(seconds=1))
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, status='skipped')  # skipped 必须带 reason
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, status='failed')   # failed 必须带 reason
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert(conn)  # 同一 (run_id, stage) 再插一行


def test_migration_025_is_rerunnable(migrated_db):
    conn = migrated_db
    sql = MIGRATION.read_text(encoding='utf-8')
    conn.execute(sql)
    conn.commit()
    conn.execute(sql)
    conn.commit()
    assert conn.execute("SELECT to_regclass('stage_runs')").fetchone()[0] is not None
    assert conn.execute("SELECT to_regclass('idx_stage_runs_session')").fetchone()[0] is not None


def test_stage_runs_not_in_workbench_tables():
    # Repository consumers use their owning layers below.
    assert 'stage_runs' not in core_dbutil.WORKBENCH_TABLES
