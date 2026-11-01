"""Repository functions of v2.8 (migration 028): daily_ready, stage rows with attempts, attempt resets, last push.

SQL tests run in a throwaway schema of the disposable test database; `daily_ready` is also checked against a
database that has not run 028."""
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.core.schema as core_schema
import investment_assistant.workflow.repo as workflow_repo
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

# Repository consumers use their owning layers below.

T0 = datetime(2026, 10, 29, 3, 30, tzinfo=UTC)
DAY = date(2026, 10, 28)


def _schema(before: str):
    url = os.environ.get('INVESTMENT_ASSISTANT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('explicit test database URL required; disposable schema only')
    import psycopg
    schema = f'rd_test_{uuid.uuid4().hex[:12]}'
    conn = psycopg.connect(url)
    conn.execute(f'CREATE SCHEMA {schema}')
    conn.execute(f'SET search_path TO {schema}')
    for path in sorted(Path('migrations').glob('0*.sql')):
        if path.name < before:
            conn.execute(path.read_text(encoding='utf-8'))
    conn.commit()
    return conn, schema


def _drop(conn, schema):
    conn.rollback()
    conn.execute(f'DROP SCHEMA {schema} CASCADE')
    conn.commit()
    conn.close()


@pytest.fixture
def db():
    conn, schema = _schema('029')            # everything through 028
    yield conn
    _drop(conn, schema)


@pytest.fixture
def db_027():
    conn, schema = _schema('028')            # a database that has not run 028
    yield conn
    _drop(conn, schema)


def _row(**overrides):
    row = {'run_id': 'run-1', 'job': 'daily', 'trigger': 'scheduled', 'session': DAY, 'stage': 'collect',
           'stages_version': 'stages-4', 'attempt': 1, 'status': 'success', 'reason': None, 'input_count': 0,
           'output_count': 0, 'started_at': T0, 'finished_at': T0, 'duration_ms': 0}
    row.update(overrides)
    return row


def test_daily_ready_false_without_028(db_027):
    assert core_schema.daily_ready(db_027) is False
    db_027.execute(Path('migrations/028_daily_workflow.sql').read_text(encoding='utf-8'))
    assert core_schema.daily_ready(db_027) is True


def test_daily_ready_needs_each_piece(db):
    assert core_schema.daily_ready(db) is True
    db.execute("DELETE FROM scheduled_jobs WHERE name = 'daily'")
    assert core_schema.daily_ready(db) is False
    db.execute("INSERT INTO scheduled_jobs (name, time_local) VALUES ('daily', '11:30')")
    db.execute('DROP TABLE agent_attempt_resets')
    assert core_schema.daily_ready(db) is False


def test_insert_stage_run_writes_attempt_and_job(db):
    first = workflow_repo.insert_stage_run(db, _row())
    second = workflow_repo.insert_stage_run(db, _row(attempt=2))
    assert (first['attempt'], first['job']) == (1, 'daily')              # a daily row always carries its attempt
    assert second['attempt'] == 2 and second['job'] == 'daily'
    legacy = workflow_repo.insert_stage_run(db, _row(run_id='legacy', job='workbench', stages_version='stages-3'))
    assert 'attempt' not in legacy and legacy['job'] == 'workbench'      # the v2.7 first attempt keeps its column set
    assert [r['attempt'] for r in workflow_repo.stage_rows_for_run(db, 'run-1')] == [1, 2]
    # the older reader keeps its column set (no attempt), so it works before 028 as well
    assert 'attempt' not in workflow_repo.stage_runs_for_run(db, 'run-1')[0]


def test_insert_stage_run_attempt_one_still_works_before_028(db_027):
    row = workflow_repo.insert_stage_run(db_027, _row(job='workbench', stages_version='stages-3'))
    assert row['job'] == 'workbench' and row['stage'] == 'collect'
    import psycopg
    with pytest.raises(psycopg.errors.UndefinedColumn):          # only attempt > 1 needs the new column
        workflow_repo.insert_stage_run(db_027, _row(job='workbench', stages_version='stages-3', attempt=2))


def test_stage_rows_for_run_orders_attempts(db):
    # written out of order on purpose: a resumed run books screen / regime again as attempt 2
    rows = [
        _row(stage='retro', status='skipped', reason='upstream_failed'),
        _row(stage='regime', attempt=2),
        _row(stage='push', status='skipped', reason='upstream_failed'),
        _row(stage='screen', attempt=2),
        _row(stage='collect'),
        _row(stage='screen', status='failed', reason='aborted: boom'),
        _row(stage='sec_monitor'),
        _row(stage='regime', status='skipped', reason='upstream_failed'),
    ]
    for row in rows:
        workflow_repo.insert_stage_run(db, row)
    workflow_repo.insert_stage_run(db, _row(run_id='other', stage='collect'))
    got = workflow_repo.stage_rows_for_run(db, 'run-1')
    assert [(r['stage'], r['attempt']) for r in got] == [
        ('sec_monitor', 1), ('collect', 1), ('screen', 1), ('screen', 2), ('regime', 1), ('regime', 2),
        ('push', 1), ('retro', 1)]                                      # stages-4: retro after push
    assert {r['run_id'] for r in got} == {'run-1'}
    assert workflow_repo.max_stage_attempt(db, 'run-1') == 2
    assert workflow_repo.max_stage_attempt(db, 'other') == 1
    assert workflow_repo.max_stage_attempt(db, 'nope') == 0
    old = [_row(run_id='old', job='workbench', stages_version='stages-3', stage=stage)
           for stage in ('push', 'retro', 'collect')]
    for row in old:
        workflow_repo.insert_stage_run(db, row)
    assert [r['stage'] for r in workflow_repo.stage_rows_for_run(db, 'old')] == ['collect', 'retro', 'push']


def test_insert_attempt_reset_replayed(db):
    request_id = uuid.uuid4()
    row = {'ticker': 'NVDA', 'signal_date': DAY, 'mode': 'propose', 'request_id': request_id, 'origin': 'run_detail'}
    first = analysis_repo.insert_attempt_reset(db, row)
    again = analysis_repo.insert_attempt_reset(db, {**row, 'origin': 'api'})      # same request: nothing new is written
    assert first['replayed'] is False and again['replayed'] is True
    assert again['id'] == first['id'] and again['origin'] == 'run_detail'
    assert (first['ticker'], first['signal_date'], first['mode'], first['request_id']) == (
        'NVDA', DAY, 'propose', request_id)
    assert db.execute('SELECT count(*) FROM agent_attempt_resets').fetchone()[0] == 1
    later = analysis_repo.insert_attempt_reset(db, {**row, 'request_id': uuid.uuid4(), 'origin': 'plan_page'})
    analysis_repo.insert_attempt_reset(db, {**row, 'request_id': uuid.uuid4(), 'mode': 'interpret'})
    analysis_repo.insert_attempt_reset(db, {**row, 'request_id': uuid.uuid4(), 'ticker': 'MU'})
    got = analysis_repo.attempt_resets_for(db, 'NVDA', DAY, 'propose')
    assert [r['id'] for r in got] == [later['id'], first['id']]                 # newest first
    assert len(analysis_repo.attempt_resets_for(db, 'NVDA', DAY, 'interpret')) == 1
    assert analysis_repo.attempt_resets_for(db, 'NVDA', DAY - timedelta(days=1), 'propose') == []


def test_latest_daily_push_finished_at(db):
    def push(minutes, status, *, job='daily', stage='push', reason=None, run='r', detail=None):
        at = T0 + timedelta(minutes=minutes)
        workflow_repo.insert_stage_run(db, _row(run_id=f'{run}{minutes}', job=job, stage=stage, status=status,
                                             reason=reason, started_at=at, finished_at=at, detail=detail or {},
                                             stages_version='stages-4' if job == 'daily' else 'stages-3'))
        return at

    assert workflow_repo.latest_daily_push_finished_at(db, before=T0 + timedelta(days=1)) is None
    pushed = push(10, 'success')
    push(20, 'skipped', reason='manual_run')
    push(30, 'failed', reason='aborted: discord down')
    push(40, 'success', job='workbench')                                         # the old job does not count
    push(50, 'success', stage='retro')
    far = timedelta(days=1)
    assert workflow_repo.latest_daily_push_finished_at(db, before=T0 + far) == pushed
    assert workflow_repo.latest_daily_push_finished_at(db, before=pushed) is None   # strictly before
    degraded = push(60, 'degraded')
    assert workflow_repo.latest_daily_push_finished_at(db, before=T0 + far) == degraded
    assert workflow_repo.latest_daily_push_finished_at(db, before=T0 + timedelta(minutes=30)) == pushed
    closed = push(70, 'success', detail={'closed': True})                        # the one holiday line is no digest
    assert workflow_repo.latest_daily_push_finished_at(db, before=T0 + far) == degraded != closed
