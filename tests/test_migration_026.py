import investment_assistant.core.schema as core_schema
import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

MIGRATION = Path('migrations/026_plan_timeline.sql')

NOW = datetime(2026, 10, 28, 22, 30, tzinfo=UTC)
DAY = date(2026, 10, 21)
_JSONB = {'sizing', 'inputs', 'output', 'counter', 'diff', 'signals', 'conditions', 'refs', 'detail',
          'args', 'result', 'skips', 'failures', 'degradations', 'outputs', 'review_view'}


@pytest.fixture
def migrated_db():
    """Migrations < 026 in a throwaway schema, dropped afterwards; explicit test URL only."""
    url = os.environ.get('INVESTMENT_ASSISTANT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('explicit test database URL required; disposable schema only')
    import psycopg
    schema = f'm026_test_{uuid.uuid4().hex[:12]}'
    with psycopg.connect(url) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
        conn.execute(f'SET search_path TO {schema}')
        for path in sorted(Path('migrations').glob('0*.sql')):
            if path.name < '026':
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
            params.append(json.dumps(value) if value is not None else None)
        else:
            marks.append('%s')
            params.append(value)
    with conn.transaction():
        return conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(marks)}) "
                            f'RETURNING {returning}', params).fetchone()[0]


def _plan(conn, plan_version='agent-2', **overrides):
    values = {'ticker': 'NVDA', 'signal_date': DAY, 'kind': 'agent', 'plan_version': plan_version,
              'ref_price': 100, 'atr': 2, 'entry_low': 99, 'entry_high': 101, 'stop': 96, 'target1': 108,
              'valid_from': date(2026, 10, 22), 'valid_until': date(2026, 10, 28),
              'sizing': {}, 'status': 'ok', 'invalid_reasons': [], 'warnings': [], 'inputs': {}}
    values.update(overrides)
    return _insert(conn, 'trade_plans', values)


def _run(conn, **overrides):
    values = {'id': uuid.uuid4(), 'ticker': 'NVDA', 'signal_date': DAY, 'pool': 'main', 'attempt': 1,
              'mode': 'propose', 'agent_version': 'agent-2', 'provider': 'legacy', 'model': 'gpt-test',
              'cutoff_at': NOW, 'status': 'rejected', 'reason': 'malformed', 'plan_id': None,
              'started_at': NOW, 'finished_at': NOW}
    values.update(overrides)
    return _insert(conn, 'agent_runs', values)


def _version(conn, version_no=0, supersedes=None, **overrides):
    """A plan row, its generating run and the version row; overrides go to the version row."""
    plan_id = _plan(conn)
    run_id = _run(conn, mode='propose' if version_no == 0 else 'revise', status='ok', reason=None,
                  plan_id=plan_id)
    values = {'plan_id': plan_id, 'ticker': 'NVDA', 'signal_date': DAY, 'version_no': version_no,
              'supersedes_plan_id': supersedes, 'origin': 'signal' if version_no == 0 else 'review',
              'agent_run_id': run_id, 'cutoff_at': NOW, 'as_of_session': DAY,
              'diff': None if version_no == 0 else [{'field': 'stop', 'before': '96', 'after': '97'}],
              'schema_version': 'agent-2'}
    values.update(overrides)
    return _insert(conn, 'agent_plan_versions', values, returning='plan_id')


def _review(conn, head_plan_id, **overrides):
    values = {'ticker': 'NVDA', 'signal_date': DAY, 'head_plan_id': head_plan_id, 'session': date(2026, 10, 23),
              'trigger': 'batch', 'run_id': 'workbench-1', 'mode': 'entry', 'timeline_version': 'timeline-1',
              'outcome': 'maintain', 'decided_by': 'host', 'reason': 'no signal', 'attempt': 1, 'cutoff_at': NOW}
    values.update(overrides)
    return _insert(conn, 'plan_reviews', values)


def _input(conn, **overrides):
    values = {'request_id': uuid.uuid4(), 'request_hash': 'h', 'ticker': 'NVDA', 'signal_date': None,
              'kind': 'research', 'body': 'margin outlook', 'source_url': None, 'supersedes_id': None}
    values.update(overrides)
    return _insert(conn, 'user_inputs', values)


def test_new_tables_and_checks(migrated_db):
    import psycopg
    conn = migrated_db
    _apply(conn)
    v0 = _version(conn)
    v1 = _version(conn, 1, supersedes=v0)
    row = conn.execute('SELECT created_at IS NOT NULL FROM agent_plan_versions WHERE plan_id = %s',
                       [v1]).fetchone()
    assert row == (True,)

    # agent_plan_versions: each case breaks exactly one CHECK
    with pytest.raises(psycopg.errors.CheckViolation):
        _version(conn, -1, supersedes=v1)                       # version_no >= 0
    with pytest.raises(psycopg.errors.CheckViolation):
        _version(conn, 0, supersedes=v1)                        # version 0 has no predecessor
    with pytest.raises(psycopg.errors.CheckViolation):
        _version(conn, 0, origin='review')                      # version 0 comes from the signal
    with pytest.raises(psycopg.errors.CheckViolation):
        _version(conn, 0, diff=[])                              # version 0 has no diff
    with pytest.raises(psycopg.errors.CheckViolation):
        _version(conn, 2, supersedes=v1, diff=None)             # revisions carry a diff
    with pytest.raises(psycopg.errors.CheckViolation):
        _version(conn, 2, supersedes=v1, origin='manual')       # origin set
    with pytest.raises(psycopg.errors.UniqueViolation):
        _version(conn, 1, supersedes=v1)                        # UNIQUE (ticker, signal_date, version_no)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _version(conn, 2, supersedes=v0)                        # UNIQUE (supersedes_plan_id): no fork
    run_id = conn.execute('SELECT agent_run_id FROM agent_plan_versions WHERE plan_id = %s', [v0]).fetchone()[0]
    with pytest.raises(psycopg.errors.UniqueViolation):
        _version(conn, 2, supersedes=v1, agent_run_id=run_id)   # UNIQUE (agent_run_id)

    # plan_reviews
    review_run = _run(conn, mode='review', status='ok', reason=None)
    assert _review(conn, v1)
    assert _review(conn, v1, trigger='recheck', decided_by='agent', review_agent_run_id=review_run,
                   outcome='revise', new_plan_id=v1, run_id=None)
    defaults = conn.execute('SELECT signals, conditions, user_input_ids, refs, detail, input_tokens, '
                            'output_tokens FROM plan_reviews ORDER BY id LIMIT 1').fetchone()
    assert defaults == ([], [], [], [], {}, 0, 0)
    with pytest.raises(psycopg.errors.CheckViolation):
        _review(conn, v1, outcome='done')
    with pytest.raises(psycopg.errors.CheckViolation):
        _review(conn, v1, trigger='manual')
    with pytest.raises(psycopg.errors.CheckViolation):
        _review(conn, v1, trigger='recheck', mode='closed')
    with pytest.raises(psycopg.errors.CheckViolation):
        _review(conn, v1, trigger='recheck', decided_by='model')
    with pytest.raises(psycopg.errors.CheckViolation):
        _review(conn, v1, trigger='recheck', decided_by='agent')  # agent decision needs its review run
    with pytest.raises(psycopg.errors.CheckViolation):
        _review(conn, v1, trigger='recheck', new_plan_id=v1)    # new_plan_id only on revise
    with pytest.raises(psycopg.errors.CheckViolation):
        _review(conn, v1, trigger='recheck', reason=' ')
    with pytest.raises(psycopg.errors.CheckViolation):
        _review(conn, v1, trigger='recheck', attempt=0)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _review(conn, uuid.uuid4(), trigger='recheck')          # head must be a stored version

    # user_inputs
    first = _input(conn)
    assert _input(conn, kind='retract', body='withdrawn', supersedes_id=first)
    with pytest.raises(psycopg.errors.CheckViolation):
        _input(conn, kind='retract')                            # retract names what it withdraws
    with pytest.raises(psycopg.errors.CheckViolation):
        _input(conn, kind='rumor')
    with pytest.raises(psycopg.errors.CheckViolation):
        _input(conn, body='x' * 4001)
    with pytest.raises(psycopg.errors.CheckViolation):
        _input(conn, body='   ')
    assert _input(conn, body='x' * 4000)
    request_id = uuid.uuid4()
    _input(conn, request_id=request_id)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _input(conn, request_id=request_id)

    # signal_decisions gains the two nullable columns
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'signal_decisions' "
        'AND table_schema = ANY(current_schemas(false))').fetchall()}
    assert {'agent_plan_id', 'review_view'} <= cols


def test_one_effective_batch_review_per_session(migrated_db):
    import psycopg
    conn = migrated_db
    _apply(conn)
    head = _version(conn)
    _review(conn, head, outcome='skipped', reason='review_in_progress')
    _review(conn, head, outcome='skipped', reason='review_in_progress', attempt=2)
    _review(conn, head, outcome='error', reason='timeout', attempt=3)
    _review(conn, head)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _review(conn, head, attempt=2)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _review(conn, head, outcome='invalidate', attempt=2)
    # rechecks are not limited, and another session is a fresh slot
    _review(conn, head, trigger='recheck', run_id=None)
    _review(conn, head, trigger='recheck', run_id=None, attempt=2)
    _review(conn, head, session=date(2026, 10, 26))


def test_relaxed_constraints(migrated_db):
    import psycopg
    conn = migrated_db
    _apply(conn)
    plan_id = _plan(conn)
    assert _run(conn, mode='review', status='ok', reason=None)
    assert _run(conn, mode='revise', status='ok', reason=None, plan_id=plan_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        _run(conn, mode='revise', status='ok', reason=None)              # a revise ok run has a plan
    with pytest.raises(psycopg.errors.CheckViolation):
        _run(conn, mode='review', status='ok', reason=None, plan_id=plan_id)  # a review never has one
    with pytest.raises(psycopg.errors.CheckViolation):
        _run(conn, mode='review', status='pass', reason=None)            # pass only for propose / revise
    assert _run(conn, mode='revise', status='pass', reason=None)
    with pytest.raises(psycopg.errors.CheckViolation):
        _run(conn, mode='rewrite')
    # old rules still hold
    assert _run(conn, mode='interpret', status='ok', reason=None)
    with pytest.raises(psycopg.errors.CheckViolation):
        _run(conn, mode='propose', status='ok', reason=None)

    run_id = _run(conn, mode='review', status='ok', reason=None)
    call = {'run_id': run_id, 'seq': 1, 'phase': 'review', 'tool': 'get_price_bars', 'args': {},
            'result': {'ref': 'call:1'}, 'result_sha256': 'a' * 64, 'called_at': NOW}
    assert _insert(conn, 'agent_tool_calls', call)
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, 'agent_tool_calls', {**call, 'seq': 2, 'phase': 'revise'})

    stage = {'run_id': 'r1', 'job': 'workbench', 'trigger': 'scheduled', 'session': DAY, 'stage': 'review',
             'stages_version': 'stages-2', 'status': 'success', 'input_count': 0, 'output_count': 0,
             'started_at': NOW, 'finished_at': NOW + timedelta(seconds=1), 'duration_ms': 1000}
    assert _insert(conn, 'stage_runs', stage)
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, 'stage_runs', {**stage, 'stage': 'recheck'})

    # the inline CHECKs were replaced by named ones, not stacked
    for table, column, name in (('agent_runs', 'mode', 'agent_runs_mode_check'),
                                ('agent_tool_calls', 'phase', 'agent_tool_calls_phase_check'),
                                ('stage_runs', 'stage', 'stage_runs_stage_check')):
        names = [r[0] for r in conn.execute(
            'SELECT c.conname FROM pg_constraint c '
            'JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) '
            "WHERE c.conrelid = %s::regclass AND c.contype = 'c' AND a.attname = %s "
            'AND cardinality(c.conkey) = 1', [table, column]).fetchall()]
        assert names == [name]


def test_agent_unique_index_only_for_agent_1(migrated_db):
    import psycopg
    conn = migrated_db
    _apply(conn)
    _plan(conn, plan_version='agent-1')
    with pytest.raises(psycopg.errors.UniqueViolation):
        _plan(conn, plan_version='agent-1')
    assert _plan(conn) and _plan(conn)                      # agent-2 versions share the unit


def test_migration_026_is_rerunnable(migrated_db):
    conn = migrated_db
    _apply(conn)
    head = _version(conn)
    _review(conn, head)
    conn.commit()
    _apply(conn)
    _apply(conn)
    for name in ('agent_plan_versions', 'plan_reviews', 'user_inputs', 'idx_plan_reviews_session',
                 'idx_plan_reviews_chain', 'idx_plan_reviews_one_batch', 'idx_trade_plans_agent_unique'):
        assert conn.execute('SELECT to_regclass(%s)', [name]).fetchone()[0] is not None, name
    assert conn.execute('SELECT count(*) FROM plan_reviews').fetchone()[0] == 1
    index_def = conn.execute("SELECT pg_get_indexdef(to_regclass('idx_trade_plans_agent_unique'))").fetchone()[0]
    assert "'agent-1'" in index_def


def test_new_tables_not_in_workbench_tables():
    # Repository consumers use their owning layers below.
    for table in ('agent_plan_versions', 'plan_reviews', 'user_inputs'):
        assert table not in core_schema.WORKBENCH_TABLES
