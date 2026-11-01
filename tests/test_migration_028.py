"""Migration 028 (v2.8 spec §10): stage_runs attempt / daily / sec_monitor, three new tables, scheduled_jobs merged.

SQL tests run in a throwaway schema of the disposable test database only; the two source checks are offline."""
import investment_assistant.core.dbutil as core_dbutil
import investment_assistant.core.schema as core_schema
import json
import os
import re
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

# Repository consumers use their owning layers below.

MIGRATION = Path('migrations/028_daily_workflow.sql')
NOW = datetime(2026, 10, 29, 3, 30, tzinfo=UTC)
DAY = date(2026, 10, 28)
NEW_TABLES = ('agent_connections', 'agent_connection_events', 'agent_attempt_resets')
HOST_ID = 'urn:uuid:123e4567-e89b-12d3-a456-426614174000'
_JSONB = {'skips', 'failures', 'degradations', 'outputs', 'detail'}


@pytest.fixture
def migrated_db():
    """Migrations < 028 in a throwaway schema, dropped afterwards; explicit test URL only."""
    url = os.environ.get('INVESTMENT_ASSISTANT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('explicit test database URL required; disposable schema only')
    import psycopg
    schema = f'm028_test_{uuid.uuid4().hex[:12]}'
    with psycopg.connect(url) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
        conn.execute(f'SET search_path TO {schema}')
        for path in sorted(Path('migrations').glob('0*.sql')):
            if path.name < '028':
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


def _insert(conn, table, values, returning='id'):
    """One row inside a savepoint, so a rejected insert leaves the outer transaction usable."""
    cols, marks, params = [], [], []
    for key, value in values.items():
        cols.append(key)
        if key in _JSONB:
            marks.append('%s::jsonb')
            params.append(json.dumps(value))
        else:
            marks.append('%s')
            params.append(value)
    with conn.transaction():
        return conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(marks)}) "
                            f'RETURNING {returning}', params).fetchone()[0]


def _stage(conn, **overrides):
    values = {'run_id': 'run-1', 'job': 'workbench', 'trigger': 'scheduled', 'session': DAY, 'stage': 'collect',
              'stages_version': 'stages-3', 'status': 'success', 'input_count': 0, 'output_count': 0,
              'started_at': NOW, 'finished_at': NOW, 'duration_ms': 0}
    values.update(overrides)
    return _insert(conn, 'stage_runs', values)


def _reject(conn, exc_name, fn, *args, **kwargs):
    import psycopg
    with pytest.raises(getattr(psycopg.errors, exc_name)):
        fn(conn, *args, **kwargs)


def test_migrate_001_to_028_and_rerun(migrated_db):
    conn = migrated_db
    assert core_schema.daily_ready(conn) is False
    _apply(conn)
    _apply(conn)                                              # idempotent: rerunning must not fail or duplicate
    assert core_schema.daily_ready(conn) is True
    for table in NEW_TABLES:
        assert conn.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is not None, table
    assert conn.execute("SELECT to_regclass('idx_job_reports_run_id'), to_regclass('idx_agent_connection_events_created'), "
                        "to_regclass('idx_agent_attempt_resets_unit')").fetchone() == (
        'idx_job_reports_run_id', 'idx_agent_connection_events_created', 'idx_agent_attempt_resets_unit')
    names = {row[0] for row in conn.execute(
        "SELECT conname FROM pg_constraint WHERE conrelid = 'stage_runs'::regclass").fetchall()}
    assert {'stage_runs_attempt_check', 'stage_runs_job_check', 'stage_runs_stage_check',
            'stage_runs_run_stage_attempt_key'} <= names
    assert 'stage_runs_run_id_stage_key' not in names          # the old (run_id, stage) key is gone
    assert conn.execute("SELECT count(*) FROM scheduled_jobs WHERE name = 'daily'").fetchone()[0] == 1


def test_stage_runs_attempt_and_new_values(migrated_db):
    conn = migrated_db
    old = _stage(conn, run_id='old-run')                       # a v2.7 row, written before the column exists
    _apply(conn)
    assert conn.execute('SELECT attempt FROM stage_runs WHERE id = %s', (old,)).fetchone()[0] == 1
    # new values are accepted
    _stage(conn, run_id='run-d', job='daily', stage='sec_monitor', stages_version='stages-4', attempt=1)
    _stage(conn, run_id='run-d', job='daily', stage='collect', stages_version='stages-4', attempt=1,
           status='failed', reason='aborted: boom')
    _stage(conn, run_id='run-d', job='daily', stage='collect', stages_version='stages-4', attempt=2)
    assert conn.execute("SELECT array_agg(attempt ORDER BY attempt) FROM stage_runs WHERE run_id = 'run-d' "
                        "AND stage = 'collect'").fetchone()[0] == [1, 2]
    # rejected: attempt below 1, a duplicate (run_id, stage, attempt), unknown job / stage, other triggers
    _reject(conn, 'CheckViolation', _stage, run_id='run-x', attempt=0)
    _reject(conn, 'UniqueViolation', _stage, run_id='run-d', job='daily', stage='collect',
            stages_version='stages-4', attempt=2)
    _reject(conn, 'UniqueViolation', _stage, run_id='old-run')            # attempt defaults to 1
    _reject(conn, 'CheckViolation', _stage, run_id='run-y', job='other')
    _reject(conn, 'CheckViolation', _stage, run_id='run-y', stage='bogus')
    _reject(conn, 'CheckViolation', _stage, run_id='run-y', trigger='resume')
    # the old v2.7 batch still writes its rows (stage set of 027 stays valid, retro included)
    for stage in ('collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'retro', 'push'):
        _stage(conn, run_id='run-b', stage=stage)


def _connection(conn, **overrides):
    values = {'id': 1, 'host_id': HOST_ID, 'status': 'connected', 'client_id': 'client_abc-123', 'subject': 'sub-1',
              'access_token': 'test-access-token', 'refresh_token': 'test-refresh-token', 'access_expires_at': NOW}
    values.update(overrides)
    return _insert(conn, 'agent_connections', values)


def test_agent_connections_constraints(migrated_db):
    conn = migrated_db
    _apply(conn)
    assert _connection(conn) == 1
    _reject(conn, 'UniqueViolation', _connection)                             # id 1 exists
    _reject(conn, 'CheckViolation', _connection, id=2)                        # single row only
    conn.execute('DELETE FROM agent_connections')
    for missing in ('client_id', 'subject', 'access_token', 'refresh_token', 'access_expires_at'):
        _reject(conn, 'CheckViolation', _connection, **{missing: None})       # connected needs all of them
    for status in ('disconnected', 'pending', 'needs_login'):
        _reject(conn, 'CheckViolation', _connection, status=status)           # tokens only while connected
        _reject(conn, 'CheckViolation', _connection, status=status, refresh_token=None)
    assert _connection(conn, status='pending', access_token=None, refresh_token=None, client_id=None,
                       subject=None, access_expires_at=None) == 1
    conn.execute('DELETE FROM agent_connections')
    assert _connection(conn, status='needs_login', access_token=None, refresh_token=None) == 1
    conn.execute('DELETE FROM agent_connections')
    for bad in ('not-a-urn', 'urn:uuid:123E4567-e89b-12d3-a456-426614174000', HOST_ID + 'x', ''):
        _reject(conn, 'CheckViolation', _connection, host_id=bad)             # lower-case urn:uuid only
    _reject(conn, 'CheckViolation', _connection, client_id='has space')
    _reject(conn, 'CheckViolation', _connection, provider='openai')
    _reject(conn, 'CheckViolation', _connection, status='bogus')
    _reject(conn, 'CheckViolation', _connection, model='')
    _reject(conn, 'CheckViolation', _connection, last_error='x' * 301)
    assert _connection(conn, model='gpt-test', last_error='x' * 300) == 1


def test_connection_events_code_format(migrated_db):
    conn = migrated_db
    _apply(conn)
    for code in (None, 'refresh_token_reused', 'a:b.c-d', 'x' * 100):
        _insert(conn, 'agent_connection_events', {'kind': 'refresh_failed', 'code': code, 'detail': {'siwc': 'siwc-1'}})
    for code in ('has space', 'x' * 101, '', 'bad/char'):
        _reject(conn, 'CheckViolation', lambda c, code=code: _insert(
            c, 'agent_connection_events', {'kind': 'refresh_failed', 'code': code}))
    for kind in ('login_started', 'connected', 'login_failed', 'refreshed', 'refresh_failed', 'disconnected',
                 'model_selected'):
        _insert(conn, 'agent_connection_events', {'kind': kind})
    _reject(conn, 'CheckViolation', lambda c: _insert(c, 'agent_connection_events', {'kind': 'bogus'}))
    assert conn.execute('SELECT detail FROM agent_connection_events WHERE id = 1').fetchone()[0] == {'siwc': 'siwc-1'}


def test_attempt_resets_request_id_unique(migrated_db):
    conn = migrated_db
    _apply(conn)
    request_id = uuid.uuid4()

    def reset(c, **overrides):
        values = {'ticker': 'NVDA', 'signal_date': DAY, 'mode': 'propose', 'request_id': request_id,
                  'origin': 'run_detail'}
        values.update(overrides)
        return _insert(c, 'agent_attempt_resets', values)

    assert reset(conn)
    _reject(conn, 'UniqueViolation', reset)
    assert reset(conn, request_id=uuid.uuid4(), mode='interpret', origin='plan_page')
    assert reset(conn, request_id=uuid.uuid4(), origin='api')
    _reject(conn, 'CheckViolation', reset, request_id=uuid.uuid4(), mode='review')
    _reject(conn, 'CheckViolation', reset, request_id=uuid.uuid4(), origin='cron')


@pytest.mark.parametrize('workbench_enabled', [False, True])
def test_scheduled_jobs_merged(migrated_db, workbench_enabled):
    conn = migrated_db
    for name, time_local in (('workbench', '09:30'), ('workbench_review', '08:00'), ('filings', '09:30')):
        conn.execute('INSERT INTO scheduled_jobs (name, time_local, weekday_mask, timezone) '
                     "VALUES (%s, %s, '1-5', 'Asia/Shanghai') ON CONFLICT (name) DO NOTHING", (name, time_local))
    conn.execute('UPDATE scheduled_jobs SET enabled = %s WHERE name = %s', (workbench_enabled, 'workbench'))
    conn.execute("UPDATE scheduled_jobs SET enabled = NOT %s WHERE name = 'filings'", (workbench_enabled,))
    conn.commit()
    others = conn.execute("SELECT name FROM scheduled_jobs WHERE name NOT IN ('workbench', 'workbench_review', "
                          "'filings') ORDER BY name").fetchall()
    _apply(conn)
    names = [row[0] for row in conn.execute('SELECT name FROM scheduled_jobs ORDER BY name').fetchall()]
    assert names == sorted([row[0] for row in others] + ['daily'])        # the three old rows are gone, others stay
    daily = conn.execute("SELECT time_local, weekday_mask, timezone, enabled, next_run_at FROM scheduled_jobs "
                         "WHERE name = 'daily'").fetchone()
    assert daily[:3] == ('11:30', '2-6', 'Asia/Shanghai')
    assert daily[3] is workbench_enabled                                    # taken over from the workbench row
    local = conn.execute("SELECT extract(isodow FROM %s AT TIME ZONE 'Asia/Shanghai'), "
                         "(%s AT TIME ZONE 'Asia/Shanghai')::time, %s > now(), %s < now() + interval '8 days'",
                         (daily[4], daily[4], daily[4], daily[4])).fetchone()
    assert 2 <= local[0] <= 6 and str(local[1]) == '11:30:00' and local[2] and local[3]
    _apply(conn)                                                            # a rerun leaves the daily row alone
    assert conn.execute("SELECT next_run_at, enabled FROM scheduled_jobs WHERE name = 'daily'").fetchone() == (
        daily[4], workbench_enabled)


def test_new_tables_not_in_workbench_tables():
    assert not set(NEW_TABLES) & set(core_dbutil.WORKBENCH_TABLES)


def test_repository_never_mentions_agent_connections():
    source = '\n'.join(path.read_text(encoding='utf-8')
                       for path in Path('investment_assistant').glob('*/repo*.py'))
    assert 'agent_connections' not in source and 'agent_connection_events' not in source


def test_repository_only_inserts_into_attempt_resets():
    source = Path('investment_assistant/analysis/repo.py').read_text(encoding='utf-8')
    assert 'agent_attempt_resets' in source
    assert not re.search(r'(UPDATE|DELETE\s+FROM|TRUNCATE|ALTER\s+TABLE|DROP\s+TABLE)\s+agent_attempt_resets', source,
                         re.IGNORECASE)
    verbs = {re.sub(r'\s+', ' ', verb.upper())
             for verb in re.findall(r'\b(INSERT\s+INTO|DELETE\s+FROM|UPDATE|FROM|JOIN)\s+agent_attempt_resets', source,
                                    re.IGNORECASE)}
    assert verbs == {'INSERT INTO', 'FROM'}
