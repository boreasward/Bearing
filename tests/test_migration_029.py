"""Migration 029 (v2.9 spec §8, §21): trades.plan_id, trade_plans execution fields and the replaced price CHECK,
chain_retriggers, holding_status, plan_notes, the relaxed plan_reviews constraint.

SQL tests run in a throwaway schema of the disposable test database only."""
import json
import os
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

# Repository consumers use their owning layers below.

MIGRATION = Path('migrations/029_executable_plans.sql')
NOW = datetime(2026, 10, 30, 22, 30, tzinfo=UTC)
DAY = date(2026, 10, 21)
SESSION = date(2026, 10, 29)
_JSONB = {'sizing', 'inputs', 'output', 'counter', 'diff', 'signals', 'conditions', 'refs', 'detail'}


@pytest.fixture
def migrated_db():
    """Migrations < 029 in a throwaway schema, dropped afterwards; explicit test URL only."""
    url = os.environ.get('INVESTMENT_ASSISTANT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('explicit test database URL required; disposable schema only')
    import psycopg
    schema = f'm029_test_{uuid.uuid4().hex[:12]}'
    with psycopg.connect(url) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
        conn.execute(f'SET search_path TO {schema}')
        for path in sorted(Path('migrations').glob('0*.sql')):
            if path.name < '029':
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


def _plan(conn, kind='agent', plan_version='agent-3', **overrides):
    values = {'ticker': 'NVDA', 'signal_date': DAY, 'kind': kind, 'plan_version': plan_version,
              'ref_price': 100, 'atr': 2, 'entry_low': 99, 'entry_high': 101, 'stop': 96, 'target1': 108,
              'valid_from': date(2026, 10, 22), 'valid_until': date(2026, 10, 28),
              'sizing': {}, 'status': 'ok', 'invalid_reasons': [], 'warnings': [], 'inputs': {}}
    if kind == 'own':
        values.update(request_id=uuid.uuid4(), request_hash='h')
    values.update(overrides)
    return _insert(conn, 'trade_plans', values)


def _old_plan(conn, **overrides):
    """A plan row as written before 029 (no execution columns)."""
    return _plan(conn, kind='rule', plan_version='plan-1', **overrides)


def _trade(conn, **overrides):
    values = {'request_id': uuid.uuid4(), 'request_hash': 'h', 'ticker': 'NVDA', 'side': 'buy', 'quantity': 10,
              'price': 100, 'fee': 0, 'executed_at': NOW, 'recorded_at': NOW}
    values.update(overrides)
    return _insert(conn, 'trades', values)


def _decision(conn):
    return conn.execute(
        "INSERT INTO signal_decisions (request_id, request_hash, ticker, signal_date, candidate_ids, "
        "proposed_action, final_action, decision, reason) VALUES (%s, 'h', 'NVDA', %s, ARRAY[]::uuid[], 'buy', "
        "'buy', 'accept', 'r') RETURNING id", (uuid.uuid4(), DAY)).fetchone()[0]


def _run(conn, **overrides):
    values = {'id': uuid.uuid4(), 'ticker': 'NVDA', 'signal_date': DAY, 'pool': 'main', 'attempt': 1,
              'mode': 'propose', 'agent_version': 'agent-3', 'provider': 'legacy', 'model': 'gpt-test',
              'cutoff_at': NOW, 'status': 'rejected', 'reason': 'malformed', 'plan_id': None,
              'started_at': NOW, 'finished_at': NOW}
    values.update(overrides)
    return _insert(conn, 'agent_runs', values)


def _version(conn, plan_id, **overrides):
    run_id = _run(conn, mode='propose', status='ok', reason=None, plan_id=plan_id)
    values = {'plan_id': plan_id, 'ticker': 'NVDA', 'signal_date': DAY, 'version_no': 0,
              'origin': 'signal', 'agent_run_id': run_id, 'cutoff_at': NOW, 'as_of_session': DAY,
              'schema_version': 'agent-3'}
    values.update(overrides)
    return _insert(conn, 'agent_plan_versions', values, returning='plan_id')


def _review(conn, head_plan_id, **overrides):
    values = {'ticker': 'NVDA', 'signal_date': DAY, 'head_plan_id': head_plan_id, 'session': SESSION,
              'trigger': 'batch', 'run_id': 'workbench-1', 'mode': 'holding', 'timeline_version': 'timeline-2',
              'outcome': 'maintain', 'decided_by': 'host', 'reason': 'no signal', 'attempt': 1, 'cutoff_at': NOW}
    values.update(overrides)
    return _insert(conn, 'plan_reviews', values)


def _retrigger(conn, **overrides):
    values = {'ticker': 'NVDA', 'unit_signal_date': date(2026, 10, 23), 'chain_signal_date': DAY,
              'chain_state': 'holding', 'rule_ids': ['breakout'], 'run_id': 'run-1', 'timeline_version': 'timeline-2'}
    values.update(overrides)
    return _insert(conn, 'chain_retriggers', values)


def _status(conn, **overrides):
    values = {'ticker': 'NVDA', 'session': SESSION, 'run_id': 'run-1', 'quantity': 10, 'avg_cost': 100,
              'held_sessions': 3, 'instruction': 'hold', 'reason': 'ok', 'timeline_version': 'timeline-2'}
    values.update(overrides)
    return _insert(conn, 'holding_status', values)


def _note(conn, **overrides):
    values = {'request_id': uuid.uuid4(), 'request_hash': 'h', 'ticker': 'NVDA', 'chain_signal_date': DAY,
              'kind': 'note', 'body': 'wait for the pullback'}
    values.update(overrides)
    return _insert(conn, 'plan_notes', values)


def _reject(exc_name, fn, *args, **kwargs):
    import psycopg
    with pytest.raises(getattr(psycopg.errors, exc_name)):
        fn(*args, **kwargs)


def _constraints(conn, table):
    return sorted(row[0] for row in conn.execute(
        'SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass', (table,)).fetchall())


def test_old_rows_satisfy_new_constraints(migrated_db):
    conn = migrated_db
    plan = _old_plan(conn)
    agent = _plan(conn, plan_version='agent-2')
    version = _version(conn, agent, schema_version='agent-2')
    decision = _decision(conn)
    trade = _trade(conn, decision_id=decision)
    review_run = _run(conn, mode='review', status='ok', reason=None)
    review = _review(conn, version, mode='entry', timeline_version='timeline-1', decided_by='agent',
                     review_agent_run_id=review_run)
    host_review = _review(conn, version, mode='entry', timeline_version='timeline-1', session=date(2026, 10, 28))
    _apply(conn)                                              # would fail if an old row broke a new constraint
    row = conn.execute('SELECT entry_type, max_hold_sessions FROM trade_plans WHERE id = %s', (plan,)).fetchone()
    assert row == (None, None)
    assert conn.execute('SELECT plan_id, decision_id FROM trades WHERE id = %s', (trade,)).fetchone() == (None, decision)
    assert conn.execute('SELECT count(*) FROM plan_reviews WHERE id = ANY(%s)', ([review, host_review],)
                        ).fetchone()[0] == 2
    # the old rows keep working under the new constraints: a v2.8 style insert still passes
    _plan(conn, plan_version='agent-2', signal_date=date(2026, 10, 22))


def test_trades_link_one_check(migrated_db):
    conn = migrated_db
    _apply(conn)
    plan = _plan(conn, entry_type='zone_limit', max_hold_sessions=10)
    decision = _decision(conn)
    assert _trade(conn, plan_id=plan)
    assert _trade(conn, decision_id=decision)
    assert _trade(conn)                                       # no link at all (a trade without a plan)
    _reject('CheckViolation', _trade, conn, plan_id=plan, decision_id=decision)
    _reject('ForeignKeyViolation', _trade, conn, plan_id=uuid.uuid4())
    assert conn.execute("SELECT to_regclass('idx_trades_plan')").fetchone()[0] == 'idx_trades_plan'


def test_trade_plans_exec_fields_both_or_none(migrated_db):
    conn = migrated_db
    _apply(conn)
    assert _plan(conn, entry_type='zone_limit', max_hold_sessions=10)
    assert _plan(conn, signal_date=date(2026, 10, 22))        # neither (old shape)
    _reject('CheckViolation', _plan, conn, entry_type='zone_limit')
    _reject('CheckViolation', _plan, conn, max_hold_sessions=10)


def test_entry_type_values(migrated_db):
    conn = migrated_db
    _apply(conn)
    for index, value in enumerate(('zone_limit', 'breakout_stop', 'next_open')):
        assert _plan(conn, signal_date=date(2026, 10, 10 + index), entry_type=value, max_hold_sessions=10)
    _reject('CheckViolation', _plan, conn, entry_type='market', max_hold_sessions=10)
    _reject('CheckViolation', _plan, conn, entry_type='', max_hold_sessions=10)


def test_max_hold_range(migrated_db):
    conn = migrated_db
    _apply(conn)
    assert _plan(conn, signal_date=date(2026, 10, 12), entry_type='next_open', max_hold_sessions=3)
    assert _plan(conn, signal_date=date(2026, 10, 13), entry_type='next_open', max_hold_sessions=30)
    _reject('CheckViolation', _plan, conn, entry_type='next_open', max_hold_sessions=2)
    _reject('CheckViolation', _plan, conn, entry_type='next_open', max_hold_sessions=31)


def test_trade_plans_holding_stop_check(migrated_db):
    conn = migrated_db
    _apply(conn)
    # a holding-period Agent plan may put the stop above the entry zone (still below target1)
    assert _plan(conn, signal_date=date(2026, 10, 14), stop=103, inputs={'chain_mode': 'holding'},
                 entry_type='zone_limit', max_hold_sessions=10)
    assert _plan(conn, signal_date=date(2026, 10, 15), stop=99, inputs={'chain_mode': 'holding'},
                 entry_type='zone_limit', max_hold_sessions=10)          # equal to entry_low also allowed there
    # everything else keeps stop < entry_low
    _reject('CheckViolation', _plan, conn, stop=99)                                       # agent, no chain_mode
    _reject('CheckViolation', _plan, conn, stop=103, inputs={'chain_mode': 'entry'})
    _reject('CheckViolation', _plan, conn, kind='rule', plan_version='plan-2', stop=103,
            inputs={'chain_mode': 'holding'})                                              # not an agent row
    _reject('CheckViolation', _plan, conn, kind='own', plan_version='plan-2', stop=103,
            inputs={'chain_mode': 'holding'})
    # the other level rules still hold, holding mode or not
    _reject('CheckViolation', _plan, conn, stop=110, inputs={'chain_mode': 'holding'})     # stop >= target1
    _reject('CheckViolation', _plan, conn, entry_low=102, entry_high=101)                  # entry_low > entry_high
    _reject('CheckViolation', _plan, conn, entry_high=108)                                 # entry_high >= target1
    assert _plan(conn, signal_date=date(2026, 10, 16), stop=95)                            # the ordinary case


def test_chain_retriggers_constraints(migrated_db):
    conn = migrated_db
    _apply(conn)
    plan = _plan(conn, plan_version='plan-2', kind='rule', entry_type='zone_limit', max_hold_sessions=10)
    assert _retrigger(conn, baseline_plan_id=plan)                                   # holding: no entry_cap
    assert _retrigger(conn, unit_signal_date=date(2026, 10, 24), chain_state='exiting')
    assert _retrigger(conn, unit_signal_date=date(2026, 10, 27), chain_state='entry', entry_cap=date(2026, 11, 6))
    _reject('UniqueViolation', _retrigger, conn)                                     # (ticker, unit_signal_date)
    assert _retrigger(conn, ticker='AMD')                                            # another ticker, same date
    _reject('CheckViolation', _retrigger, conn, unit_signal_date=DAY)                # unit must be after the chain
    _reject('CheckViolation', _retrigger, conn, unit_signal_date=date(2026, 10, 20))
    _reject('CheckViolation', _retrigger, conn, unit_signal_date=date(2026, 10, 28), chain_state='entry')  # cap missing
    _reject('CheckViolation', _retrigger, conn, unit_signal_date=date(2026, 10, 28), chain_state='holding',
            entry_cap=date(2026, 11, 6))                                             # cap only in the entry state
    _reject('CheckViolation', _retrigger, conn, unit_signal_date=date(2026, 10, 28), chain_state='expired')
    _reject('NotNullViolation', _retrigger, conn, unit_signal_date=date(2026, 10, 28), rule_ids=None)
    _reject('ForeignKeyViolation', _retrigger, conn, unit_signal_date=date(2026, 10, 28),
            baseline_plan_id=uuid.uuid4())
    assert conn.execute("SELECT to_regclass('idx_chain_retriggers_chain')").fetchone()[0] is not None


def test_holding_status_exit_reason_check(migrated_db):
    conn = migrated_db
    _apply(conn)
    sessions = [date(2026, 10, d) for d in (20, 21, 22, 23, 26, 27)]
    assert _status(conn, session=sessions[0], instruction='hold')
    assert _status(conn, session=sessions[1], instruction='sell_half')
    assert _status(conn, session=sessions[2], instruction='no_plan')
    for index, reason in enumerate(('stop_touched', 'time_stop', 'invalidation', 'agent_invalidate')):
        assert _status(conn, ticker=f'T{index}', instruction='exit', exit_reason=reason)
    _reject('CheckViolation', _status, conn, session=sessions[3], instruction='exit')            # exit needs a reason
    _reject('CheckViolation', _status, conn, session=sessions[3], instruction='hold', exit_reason='time_stop')
    _reject('CheckViolation', _status, conn, session=sessions[3], instruction='exit', exit_reason='other')
    _reject('CheckViolation', _status, conn, session=sessions[3], instruction='sell')
    _reject('CheckViolation', _status, conn, session=sessions[3], held_sessions=0)
    _reject('CheckViolation', _status, conn, session=sessions[3], reason='  ')
    _reject('NotNullViolation', _status, conn, session=sessions[3], quantity=None)


def test_stop_mode_values(migrated_db):
    conn = migrated_db
    _apply(conn)
    assert _status(conn, session=date(2026, 10, 20), stop_mode='initial')
    assert _status(conn, session=date(2026, 10, 21), stop_mode='trailing')
    assert _status(conn, session=date(2026, 10, 22), stop_mode=None)
    for index, value in enumerate(('trail', 'breakeven', '')):
        _reject('CheckViolation', _status, conn, session=date(2026, 10, 23), stop_mode=value)


def test_holding_status_unique_ticker_session(migrated_db):
    conn = migrated_db
    _apply(conn)
    assert _status(conn)
    _reject('UniqueViolation', _status, conn)
    assert _status(conn, ticker='AMD')
    assert _status(conn, session=date(2026, 10, 30))
    assert conn.execute("SELECT to_regclass('idx_holding_status_session')").fetchone()[0] is not None


def test_holding_status_foreign_keys(migrated_db):
    conn = migrated_db
    _apply(conn)
    plan = _plan(conn)
    version = _version(conn, plan)
    review = _review(conn, version)
    assert _status(conn, plan_id=plan, review_id=review, chain_signal_date=DAY)
    _reject('ForeignKeyViolation', _status, conn, ticker='AMD', plan_id=uuid.uuid4())
    _reject('ForeignKeyViolation', _status, conn, ticker='AMD', review_id=999999)


def test_plan_notes_target_xor(migrated_db):
    conn = migrated_db
    _apply(conn)
    plan = _plan(conn, kind='rule', plan_version='plan-2', entry_type='zone_limit', max_hold_sessions=10)
    assert _note(conn)                                                         # a chain
    assert _note(conn, chain_signal_date=None, plan_id=plan)                   # a non-chain plan
    _reject('CheckViolation', _note, conn, chain_signal_date=None, plan_id=None)
    _reject('CheckViolation', _note, conn, chain_signal_date=DAY, plan_id=plan)
    _reject('ForeignKeyViolation', _note, conn, chain_signal_date=None, plan_id=uuid.uuid4())
    _reject('CheckViolation', _note, conn, body='   ')
    _reject('CheckViolation', _note, conn, body='x' * 1001)
    assert _note(conn, body='x' * 1000)
    request_id = uuid.uuid4()
    assert _note(conn, request_id=request_id)
    _reject('UniqueViolation', _note, conn, request_id=request_id)


def test_plan_notes_retract_needs_supersedes(migrated_db):
    conn = migrated_db
    _apply(conn)
    first = _note(conn)
    _reject('CheckViolation', _note, conn, kind='retract', body='retracted')
    assert _note(conn, kind='retract', body='retracted', supersedes_id=first)
    assert _note(conn, supersedes_id=first, body='corrected text')             # a correction is a note row
    _reject('ForeignKeyViolation', _note, conn, kind='retract', supersedes_id=999999)
    _reject('CheckViolation', _note, conn, kind='edit')


def test_plan_reviews_relaxed_decided_by(migrated_db):
    conn = migrated_db
    _apply(conn)
    plan = _plan(conn)
    version = _version(conn, plan)
    revise_run = _run(conn, mode='revise', status='ok', reason=None, plan_id=_plan(conn, signal_date=date(2026, 10, 22)))
    review_run = _run(conn, mode='review', status='ok', reason=None)
    # an agent row with only a revision run (the §4.3 direct revision) is accepted now
    assert _review(conn, version, decided_by='agent', review_agent_run_id=None, revision_agent_run_id=revise_run,
                   session=date(2026, 10, 23))
    # the old shapes keep working
    assert _review(conn, version, decided_by='agent', review_agent_run_id=review_run, session=date(2026, 10, 26))
    assert _review(conn, version, decided_by='host', session=date(2026, 10, 27))
    # an agent row with neither run is still rejected
    _reject('CheckViolation', _review, conn, version, decided_by='agent', session=date(2026, 10, 28))
    # decided_by values and the revise rule are untouched
    _reject('CheckViolation', _review, conn, version, decided_by='model', session=date(2026, 10, 28))
    _reject('CheckViolation', _review, conn, version, new_plan_id=version, session=date(2026, 10, 28))
