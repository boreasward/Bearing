"""Repository functions of v2.9 (migration 029): executable_ready, chain_retriggers, holding_status, plan_notes,
trades.plan_id, cutoff-aware trade reads, chain summaries, the default of rule_plan and the private decision insert.

SQL tests run in a throwaway schema of the disposable test database (the `workbench_db` fixture, migrations through
029); `executable_ready` is also checked against databases missing one of the pieces."""
import ast
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from investment_assistant.workbench import ledger, repository

T0 = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)
CHAIN = date(2026, 10, 21)
UNIT = date(2026, 10, 23)
SESSION = date(2026, 10, 29)


def _plan_rec(**overrides):
    rec = {'ticker': 'NVDA', 'signal_date': CHAIN, 'kind': 'rule', 'plan_version': 'plan-1', 'ref_price': Decimal('100'),
           'atr': Decimal('2'), 'entry_low': Decimal('99'), 'entry_high': Decimal('101'), 'stop': Decimal('96'),
           'target1': Decimal('108'), 'trail_atr_multiple': Decimal('3'), 'valid_from': date(2026, 10, 22),
           'valid_until': date(2026, 10, 28), 'sizing': {}, 'status': 'ok', 'invalid_reasons': [], 'warnings': [],
           'inputs': {}}
    rec.update(overrides)
    return rec


def _trade_rec(**overrides):
    rec = {'request_id': uuid.uuid4(), 'ticker': 'NVDA', 'side': 'buy', 'quantity': Decimal('10'),
           'price': Decimal('100'), 'fee': Decimal('0'), 'executed_at': T0, 'decision_id': None, 'note': None}
    rec.update(overrides)
    return rec


def _trade(conn, *, recorded_at=T0, **overrides):
    return repository.insert_trade(conn, _trade_rec(**overrides), 'h', recorded_at=recorded_at)


def _retrigger_rec(**overrides):
    rec = {'ticker': 'NVDA', 'unit_signal_date': UNIT, 'chain_signal_date': CHAIN, 'chain_state': 'holding',
           'rule_ids': ['breakout', 'pullback'], 'baseline_plan_id': None, 'entry_cap': None, 'run_id': 'run-1',
           'timeline_version': 'timeline-2'}
    rec.update(overrides)
    return rec


def _status_rec(**overrides):
    rec = {'ticker': 'NVDA', 'session': SESSION, 'chain_signal_date': CHAIN, 'plan_id': None, 'review_id': None,
           'run_id': 'run-1', 'quantity': Decimal('10'), 'avg_cost': Decimal('100'), 'last_close': Decimal('104'),
           'stop': Decimal('96'), 'target1': Decimal('108'), 'stop_mode': 'initial', 't1_reached': False,
           'reached_1r': False, 'to_stop_pct': Decimal('7.69'), 'to_t1_pct': Decimal('3.85'),
           'r_now': Decimal('1.00'), 'held_sessions': 4, 'max_hold_sessions': 10, 'remaining_sessions': 6,
           'instruction': 'hold', 'exit_reason': None, 'reason': 'no new signal', 'detail': {'bars_missing': False},
           'timeline_version': 'timeline-2'}
    rec.update(overrides)
    return rec


def _note_rec(**overrides):
    rec = {'request_id': uuid.uuid4(), 'request_hash': 'h1', 'ticker': 'NVDA', 'chain_signal_date': CHAIN,
           'plan_id': None, 'kind': 'note', 'body': 'wait for the pullback', 'supersedes_id': None}
    rec.update(overrides)
    return rec


def _agent_chain(conn, signal_date, schemas, *, start=T0):
    """An agent chain with one version per entry of `schemas`; returns the version rows."""
    rows, previous = [], None
    for number, schema in enumerate(schemas):
        plan = repository.insert_plan(conn, _plan_rec(kind='agent', plan_version=schema, signal_date=signal_date))
        run_id = uuid.uuid4()
        conn.execute(
            "INSERT INTO agent_runs (id, ticker, signal_date, pool, attempt, mode, agent_version, provider, model, "
            "cutoff_at, status, plan_id, started_at, finished_at) VALUES (%s, 'NVDA', %s, 'main', %s, %s, %s, 'x', "
            "'m', %s, 'ok', %s, %s, %s)",
            (run_id, signal_date, number + 1, 'propose' if number == 0 else 'revise', schema, start, plan['id'],
             start, start))
        rows.append(repository.insert_plan_version(conn, {
            'plan_id': plan['id'], 'ticker': 'NVDA', 'signal_date': signal_date, 'version_no': number,
            'supersedes_plan_id': previous, 'origin': 'signal' if number == 0 else 'review',
            'agent_run_id': run_id, 'cutoff_at': start, 'as_of_session': signal_date,
            'diff': None if number == 0 else [{'field': 'stop'}], 'schema_version': schema}))
        previous = plan['id']
    return rows


# --- executable_ready ---

def test_executable_ready_true_after_029(workbench_db):
    assert repository.executable_ready(workbench_db) is True


@pytest.mark.parametrize('drop', [
    'DROP TABLE chain_retriggers', 'DROP TABLE holding_status', 'DROP TABLE plan_notes',
    'ALTER TABLE trades DROP COLUMN plan_id CASCADE', 'ALTER TABLE trade_plans DROP COLUMN entry_type CASCADE',
    'ALTER TABLE trade_plans DROP COLUMN max_hold_sessions CASCADE'])
def test_executable_ready_false_when_a_piece_is_missing(workbench_db, drop):
    workbench_db.execute(drop)
    assert repository.executable_ready(workbench_db) is False


def test_executable_ready_false_on_database_without_029():
    url = os.environ.get('INVESTMENT_ASSISTANT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('explicit test database URL required; disposable schema only')
    import psycopg
    schema = f'er_test_{uuid.uuid4().hex[:12]}'
    with psycopg.connect(url) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
        conn.execute(f'SET search_path TO {schema}')
        try:
            for path in sorted(Path('migrations').glob('0*.sql')):
                if path.name < '029':
                    conn.execute(path.read_text(encoding='utf-8'))
            conn.commit()
            assert repository.executable_ready(conn) is False
            conn.execute(Path('migrations/029_executable_plans.sql').read_text(encoding='utf-8'))
            conn.commit()
            assert repository.executable_ready(conn) is True
        finally:
            conn.rollback()
            conn.execute(f'DROP SCHEMA {schema} CASCADE')
            conn.commit()


# --- chain_retriggers ---

def test_insert_chain_retrigger_and_replay(workbench_db):
    conn = workbench_db
    baseline = repository.insert_plan(conn, _plan_rec(plan_version='plan-2', entry_type='zone_limit',
                                                       max_hold_sessions=10, signal_date=UNIT))
    first = repository.insert_chain_retrigger(conn, _retrigger_rec(baseline_plan_id=baseline['id']))
    assert first['replayed'] is False and first['id'] and first['created_at'] is not None
    assert first['rule_ids'] == ['breakout', 'pullback'] and first['entry_cap'] is None
    assert first['baseline_plan_id'] == baseline['id']
    again = repository.insert_chain_retrigger(conn, _retrigger_rec(run_id='run-2', rule_ids=['other']))
    assert again['replayed'] is True and again['id'] == first['id'] and again['run_id'] == 'run-1'
    assert conn.execute('SELECT count(*) FROM chain_retriggers').fetchone()[0] == 1
    entry = repository.insert_chain_retrigger(conn, _retrigger_rec(
        unit_signal_date=date(2026, 10, 27), chain_state='entry', entry_cap=date(2026, 11, 6)))
    assert entry['entry_cap'] == date(2026, 11, 6) and entry['replayed'] is False


def test_retrigger_for_unit(workbench_db):
    conn = workbench_db
    assert repository.retrigger_for_unit(conn, 'NVDA', UNIT) is None
    stored = repository.insert_chain_retrigger(conn, _retrigger_rec())
    found = repository.retrigger_for_unit(conn, 'nvda', UNIT)
    assert found['id'] == stored['id'] and found['chain_signal_date'] == CHAIN
    assert repository.retrigger_for_unit(conn, 'NVDA', date(2026, 10, 24)) is None
    assert repository.retrigger_for_unit(conn, 'AMD', UNIT) is None


def test_chain_retriggers_for_filters_and_cutoff(workbench_db):
    conn = workbench_db
    a = repository.insert_chain_retrigger(conn, _retrigger_rec(unit_signal_date=date(2026, 10, 23)))
    b = repository.insert_chain_retrigger(conn, _retrigger_rec(unit_signal_date=date(2026, 10, 27)))
    c = repository.insert_chain_retrigger(conn, _retrigger_rec(unit_signal_date=date(2026, 10, 28),
                                                                chain_signal_date=date(2026, 10, 22)))
    repository.insert_chain_retrigger(conn, _retrigger_rec(ticker='AMD'))
    ids = lambda rows: [row['id'] for row in rows]                      # noqa: E731
    assert ids(repository.chain_retriggers_for(conn, 'NVDA')) == [a['id'], b['id'], c['id']]
    assert ids(repository.chain_retriggers_for(conn, 'NVDA', chain_signal_date=CHAIN)) == [a['id'], b['id']]
    assert ids(repository.chain_retriggers_for(conn, 'NVDA', until_session=date(2026, 10, 27))) == [a['id'], b['id']]
    assert ids(repository.chain_retriggers_for(conn, 'NVDA', chain_signal_date=CHAIN,
                                               until_session=date(2026, 10, 23))) == [a['id']]
    # cutoff: rows written after it do not exist yet
    conn.execute('UPDATE chain_retriggers SET created_at = %s WHERE id = %s', (T0, a['id']))
    conn.execute('UPDATE chain_retriggers SET created_at = %s WHERE id = %s', (T0 + timedelta(hours=1), b['id']))
    conn.execute('UPDATE chain_retriggers SET created_at = %s WHERE id = %s', (T0 + timedelta(hours=2), c['id']))
    assert ids(repository.chain_retriggers_for(conn, 'NVDA', cutoff=T0)) == [a['id']]
    assert ids(repository.chain_retriggers_for(conn, 'NVDA', cutoff=T0 + timedelta(hours=1))) == [a['id'], b['id']]
    assert ids(repository.chain_retriggers_for(conn, 'NVDA', cutoff=T0 - timedelta(seconds=1))) == []
    assert repository.chain_retriggers_for(conn, 'TSLA') == []


# --- holding_status ---

def test_insert_holding_status_and_replay(workbench_db):
    conn = workbench_db
    first = repository.insert_holding_status(conn, _status_rec())
    assert first['replayed'] is False and first['detail'] == {'bars_missing': False}
    assert first['quantity'] == Decimal('10') and first['to_stop_pct'] == Decimal('7.69')
    assert first['instruction'] == 'hold' and first['stop_mode'] == 'initial' and first['exit_reason'] is None
    again = repository.insert_holding_status(conn, _status_rec(reason='rerun', instruction='exit',
                                                               exit_reason='time_stop'))
    assert again['replayed'] is True and again['id'] == first['id'] and again['reason'] == 'no new signal'
    assert conn.execute('SELECT count(*) FROM holding_status').fetchone()[0] == 1
    # the minimal shape: a position without a plan or a close
    bare = repository.insert_holding_status(conn, {
        'ticker': 'amd', 'session': SESSION, 'run_id': 'run-1', 'quantity': Decimal('5'), 'avg_cost': Decimal('50'),
        'held_sessions': 1, 'instruction': 'no_plan', 'reason': 'no plan', 'timeline_version': 'timeline-2'})
    assert bare['ticker'] == 'AMD' and bare['plan_id'] is None and bare['last_close'] is None
    assert bare['detail'] == {} and bare['replayed'] is False


def test_holding_status_for_filters(workbench_db):
    conn = workbench_db
    a = repository.insert_holding_status(conn, _status_rec(session=date(2026, 10, 28)))
    b = repository.insert_holding_status(conn, _status_rec(session=SESSION))
    c = repository.insert_holding_status(conn, _status_rec(ticker='AMD', chain_signal_date=None))
    d = repository.insert_holding_status(conn, _status_rec(ticker='TSLA', chain_signal_date=date(2026, 10, 22)))
    ids = lambda rows: [row['id'] for row in rows]                      # noqa: E731
    assert ids(repository.holding_status_for(conn)) == [a['id'], c['id'], b['id'], d['id']]      # session, ticker
    assert ids(repository.holding_status_for(conn, session=SESSION)) == [c['id'], b['id'], d['id']]      # ticker order
    assert ids(repository.holding_status_for(conn, ticker='nvda')) == [a['id'], b['id']]
    assert ids(repository.holding_status_for(conn, session=SESSION, ticker='NVDA')) == [b['id']]
    assert ids(repository.holding_status_for(conn, chain_signal_date=CHAIN)) == [a['id'], b['id']]
    assert ids(repository.holding_status_for(conn, ticker='AMD', chain_signal_date=CHAIN)) == []
    assert repository.holding_status_for(conn, session=date(2026, 1, 1)) == []


# --- plan_notes ---

def test_insert_plan_note_idempotent_and_conflict(workbench_db):
    conn = workbench_db
    rec = _note_rec()
    first = repository.insert_plan_note(conn, rec)
    assert first['replayed'] is False and first['body'] == 'wait for the pullback' and first['kind'] == 'note'
    again = repository.insert_plan_note(conn, rec)                              # same request, same content
    assert again['replayed'] is True and again['id'] == first['id']
    with pytest.raises(ledger.Conflict):
        repository.insert_plan_note(conn, {**rec, 'request_hash': 'other', 'body': 'different'})
    assert conn.execute('SELECT count(*) FROM plan_notes').fetchone()[0] == 1


def test_insert_plan_note_on_non_chain_plan(workbench_db):
    conn = workbench_db
    plan = repository.insert_plan(conn, _plan_rec(plan_version='plan-2', entry_type='zone_limit', max_hold_sessions=10))
    note = repository.insert_plan_note(conn, _note_rec(chain_signal_date=None, plan_id=plan['id'], ticker='nvda'))
    assert note['plan_id'] == plan['id'] and note['chain_signal_date'] is None and note['ticker'] == 'NVDA'


def test_plan_notes_for_status_and_scope(workbench_db):
    conn = workbench_db
    plan = repository.insert_plan(conn, _plan_rec(plan_version='plan-2', entry_type='zone_limit', max_hold_sessions=10,
                                                   signal_date=UNIT))
    n1 = repository.insert_plan_note(conn, _note_rec(body='first'))
    n2 = repository.insert_plan_note(conn, _note_rec(body='second'))
    n3 = repository.insert_plan_note(conn, _note_rec(body='third'))
    other_chain = repository.insert_plan_note(conn, _note_rec(chain_signal_date=date(2026, 10, 22), body='other'))
    on_plan = repository.insert_plan_note(conn, _note_rec(chain_signal_date=None, plan_id=plan['id'], body='plan'))
    repository.insert_plan_note(conn, _note_rec(ticker='AMD', body='amd'))
    fix = repository.insert_plan_note(conn, _note_rec(body='first (fixed)', supersedes_id=n1['id']))    # correction
    retract = repository.insert_plan_note(conn, _note_rec(kind='retract', body='retract', supersedes_id=n2['id']))
    notes = repository.plan_notes_for(conn, 'NVDA', chain_signal_date=CHAIN)
    by_body = {row['body']: row for row in notes}
    assert list(by_body) == ['first', 'second', 'third', 'first (fixed)']       # oldest first, no retract rows
    assert by_body['first']['status'] == 'corrected' and by_body['first']['superseded_by'] == fix['id']
    assert by_body['second']['status'] == 'retracted' and by_body['second']['superseded_by'] == retract['id']
    assert by_body['third']['status'] == 'active' and by_body['third']['superseded_by'] is None
    assert by_body['first (fixed)']['status'] == 'active'
    assert [r['id'] for r in repository.plan_notes_for(conn, 'NVDA', plan_id=plan['id'])] == [on_plan['id']]
    assert {r['body'] for r in repository.plan_notes_for(conn, 'NVDA')} == {
        'first', 'second', 'third', 'other', 'plan', 'first (fixed)'}
    both = repository.plan_notes_for(conn, 'NVDA', chain_signal_date=date(2026, 10, 22), plan_id=plan['id'])
    assert {r['id'] for r in both} == {other_chain['id'], on_plan['id']}
    assert n3['id'] in {r['id'] for r in notes}


def test_plan_notes_for_cutoff_ignores_later_corrections(workbench_db):
    conn = workbench_db
    n1 = repository.insert_plan_note(conn, _note_rec(body='first'))
    n2 = repository.insert_plan_note(conn, _note_rec(body='second'))
    fix = repository.insert_plan_note(conn, _note_rec(body='first (fixed)', supersedes_id=n1['id']))
    retract = repository.insert_plan_note(conn, _note_rec(kind='retract', body='retract', supersedes_id=n2['id']))
    for row, at in ((n1, 0), (n2, 1), (fix, 5), (retract, 6)):
        conn.execute('UPDATE plan_notes SET created_at = %s WHERE id = %s', (T0 + timedelta(hours=at), row['id']))
    early = repository.plan_notes_for(conn, 'NVDA', chain_signal_date=CHAIN, cutoff=T0 + timedelta(hours=2))
    assert [(r['body'], r['status']) for r in early] == [('first', 'active'), ('second', 'active')]
    mid = repository.plan_notes_for(conn, 'NVDA', chain_signal_date=CHAIN, cutoff=T0 + timedelta(hours=5))
    assert [(r['body'], r['status']) for r in mid] == [('first', 'corrected'), ('second', 'active'),
                                                       ('first (fixed)', 'active')]
    late = repository.plan_notes_for(conn, 'NVDA', chain_signal_date=CHAIN, cutoff=T0 + timedelta(hours=6))
    assert [(r['body'], r['status']) for r in late] == [('first', 'corrected'), ('second', 'retracted'),
                                                        ('first (fixed)', 'active')]
    assert repository.plan_notes_for(conn, 'NVDA', chain_signal_date=CHAIN, cutoff=T0 - timedelta(hours=1)) == []


# --- trades: plan_id, cutoff ---

def test_insert_trade_writes_plan_id(workbench_db):
    conn = workbench_db
    plan = repository.insert_plan(conn, _plan_rec(plan_version='plan-2', entry_type='zone_limit', max_hold_sessions=10))
    linked = _trade(conn, plan_id=plan['id'])
    assert linked['plan_id'] == plan['id'] and linked['decision_id'] is None
    plain = repository.insert_trade(conn, {k: v for k, v in _trade_rec().items() if k != 'decision_id'}, 'h',
                                    recorded_at=T0)                    # old callers: no link keys at all
    assert plain['plan_id'] is None and plain['decision_id'] is None
    assert repository.trade_by_id(conn, linked['id'])['plan_id'] == plan['id']
    assert repository.trade_by_request(conn, linked['request_id'])['plan_id'] == plan['id']
    assert 'plan_id' in repository.list_trades(conn, 'NVDA')[0]


def test_trades_by_plan_ids(workbench_db):
    conn = workbench_db
    p1 = repository.insert_plan(conn, _plan_rec(kind='agent', plan_version='agent-3', entry_type='zone_limit',
                                                 max_hold_sessions=10))
    p2 = repository.insert_plan(conn, _plan_rec(plan_version='plan-2', entry_type='zone_limit', max_hold_sessions=10))
    t1 = _trade(conn, plan_id=p1['id'], executed_at=T0 + timedelta(hours=2))
    t2 = _trade(conn, plan_id=p2['id'], executed_at=T0 + timedelta(hours=1))
    voided = _trade(conn, plan_id=p1['id'], executed_at=T0 + timedelta(hours=3))
    repository.mark_trade_voided(conn, voided['id'], 'typo', voided_at=T0 + timedelta(hours=4))
    _trade(conn)                                                       # unlinked
    got = repository.trades_by_plan_ids(conn, [p1['id'], p2['id']])
    assert [t['id'] for t in got] == [t2['id'], t1['id'], voided['id']]             # voided ones are returned
    assert got[2]['voided_at'] is not None
    assert [t['id'] for t in repository.trades_by_plan_ids(conn, [p2['id']])] == [t2['id']]
    assert repository.trades_by_plan_ids(conn, []) == []
    assert repository.trades_by_plan_ids(conn, [uuid.uuid4()]) == []
    assert [t['id'] for t in repository.trades_by_plan_ids(conn, [p1['id'], p2['id']],
                                                           cutoff=T0 + timedelta(hours=1))] == [t2['id']]


def test_list_trades_cutoff(workbench_db):
    conn = workbench_db
    seen = _trade(conn, executed_at=T0, recorded_at=T0)
    late_record = _trade(conn, executed_at=T0, recorded_at=T0 + timedelta(days=2))          # back-filled afterwards
    late_exec = _trade(conn, executed_at=T0 + timedelta(days=1), recorded_at=T0 + timedelta(days=1))
    other = _trade(conn, ticker='AMD', executed_at=T0, recorded_at=T0)
    cutoff = T0 + timedelta(hours=12)
    assert {t['id'] for t in repository.list_trades(conn, 'NVDA')} == {seen['id'], late_record['id'], late_exec['id']}
    assert [t['id'] for t in repository.list_trades(conn, 'NVDA', cutoff=cutoff)] == [seen['id']]
    assert {t['id'] for t in repository.list_trades(conn, cutoff=cutoff)} == {seen['id'], other['id']}
    assert {t['id'] for t in repository.list_trades(conn, 'NVDA', cutoff=T0 + timedelta(days=2))} == {
        seen['id'], late_record['id'], late_exec['id']}                    # recorded_at == cutoff is included
    assert {t['id'] for t in repository.list_trades(conn, 'nvda', cutoff=T0 + timedelta(days=3))} == {
        seen['id'], late_record['id'], late_exec['id']}
    assert repository.list_trades(conn, 'NVDA', cutoff=T0 - timedelta(seconds=1)) == []


def test_list_trades_cutoff_hides_later_void(workbench_db):
    conn = workbench_db
    trade = _trade(conn)
    repository.mark_trade_voided(conn, trade['id'], 'entered twice', voided_at=T0 + timedelta(days=1))
    before = repository.list_trades(conn, 'NVDA', cutoff=T0 + timedelta(hours=1))
    assert before[0]['voided_at'] is None and before[0]['void_reason'] is None
    after = repository.list_trades(conn, 'NVDA', cutoff=T0 + timedelta(days=2))
    assert after[0]['voided_at'] is not None and after[0]['void_reason'] == 'entered twice'
    assert repository.list_trades(conn, 'NVDA')[0]['voided_at'] is not None            # no cutoff: as stored


# --- chains_for_ticker ---

def test_chains_for_ticker(workbench_db):
    conn = workbench_db
    old = _agent_chain(conn, date(2026, 10, 14), ['agent-2'], start=T0 - timedelta(days=7))
    new = _agent_chain(conn, CHAIN, ['agent-2', 'agent-3', 'agent-3'], start=T0)
    chains = repository.chains_for_ticker(conn, 'nvda')
    assert [c['signal_date'] for c in chains] == [date(2026, 10, 14), CHAIN]
    first, second = chains
    assert first['first_schema_version'] == 'agent-2' and first['head_schema_version'] == 'agent-2'
    assert first['version_count'] == 1 and first['head_version_no'] == 0 and first['head_plan_id'] == old[0]['plan_id']
    assert second['first_schema_version'] == 'agent-2' and second['head_schema_version'] == 'agent-3'
    assert second['version_count'] == 3 and second['head_version_no'] == 2 and second['head_plan_id'] == new[2]['plan_id']
    assert second['ticker'] == 'NVDA' and second['first_created_at'] is not None
    assert repository.chains_for_ticker(conn, 'AMD') == []


def test_chains_for_ticker_cutoff(workbench_db):
    conn = workbench_db
    chain = _agent_chain(conn, CHAIN, ['agent-2', 'agent-3'])
    later = _agent_chain(conn, date(2026, 10, 26), ['agent-3'])
    for index, row in enumerate(chain):
        conn.execute('UPDATE agent_plan_versions SET created_at = %s WHERE plan_id = %s',
                     (T0 + timedelta(days=index * 3), row['plan_id']))
    conn.execute('UPDATE agent_plan_versions SET created_at = %s WHERE plan_id = %s',
                 (T0 + timedelta(days=2), later[0]['plan_id']))
    early = repository.chains_for_ticker(conn, 'NVDA', cutoff=T0 + timedelta(days=1))
    assert [(c['signal_date'], c['head_schema_version'], c['version_count']) for c in early] == [
        (CHAIN, 'agent-2', 1)]
    mid = repository.chains_for_ticker(conn, 'NVDA', cutoff=T0 + timedelta(days=2))
    assert [(c['signal_date'], c['version_count']) for c in mid] == [(CHAIN, 1), (date(2026, 10, 26), 1)]
    full = repository.chains_for_ticker(conn, 'NVDA', cutoff=T0 + timedelta(days=4))
    assert [(c['signal_date'], c['head_schema_version'], c['version_count']) for c in full] == [
        (CHAIN, 'agent-3', 2), (date(2026, 10, 26), 'agent-3', 1)]
    assert repository.chains_for_ticker(conn, 'NVDA', cutoff=T0 - timedelta(days=1)) == []


# --- plans: execution fields, rule_plan default (R7) ---

def test_insert_plan_writes_execution_fields(workbench_db):
    conn = workbench_db
    plan = repository.insert_plan(conn, _plan_rec(plan_version='plan-2', entry_type='breakout_stop',
                                                   max_hold_sessions=12))
    assert (plan['entry_type'], plan['max_hold_sessions']) == ('breakout_stop', 12)
    old = repository.insert_plan(conn, _plan_rec(signal_date=date(2026, 10, 20)))
    assert (old['entry_type'], old['max_hold_sessions']) == (None, None)
    assert repository.plan_by_id(conn, plan['id'])['entry_type'] == 'breakout_stop'


def test_rule_plan_default_latest_version(workbench_db):
    conn = workbench_db
    v1 = repository.insert_plan(conn, _plan_rec(plan_version='plan-1'))
    conn.execute('UPDATE trade_plans SET created_at = %s WHERE id = %s', (T0 - timedelta(hours=1), v1['id']))
    assert repository.rule_plan(conn, 'NVDA', CHAIN)['id'] == v1['id']                 # only plan-1 exists
    v2 = repository.insert_plan(conn, _plan_rec(plan_version='plan-2', entry_type='zone_limit', max_hold_sessions=10))
    assert repository.rule_plan(conn, 'NVDA', CHAIN)['id'] == v2['id']                 # default: the newest of any
    assert repository.rule_plan(conn, 'NVDA', CHAIN, plan_version='plan-1')['id'] == v1['id']
    assert repository.rule_plan(conn, 'NVDA', CHAIN, 'plan-2')['id'] == v2['id']
    assert repository.rule_plan(conn, 'NVDA', CHAIN, plan_version='plan-9') is None
    assert repository.rule_plan(conn, 'NVDA', date(2026, 1, 1)) is None
    # own and agent plans are never the rule baseline
    repository.insert_plan(conn, _plan_rec(kind='agent', plan_version='agent-3', entry_type='zone_limit',
                                           max_hold_sessions=10))
    assert repository.rule_plan(conn, 'NVDA', CHAIN)['id'] == v2['id']


def test_rule_plan_tie_on_created_at_prefers_newer_version(workbench_db):
    conn = workbench_db                                       # one transaction: both rows get the same now()
    for _ in range(5):
        repository.insert_plan(conn, _plan_rec(plan_version='plan-1', signal_date=date(2026, 10, 19)))
        v2 = repository.insert_plan(conn, _plan_rec(plan_version='plan-2', signal_date=date(2026, 10, 19),
                                                    entry_type='zone_limit', max_hold_sessions=10))
        assert repository.rule_plan(conn, 'NVDA', date(2026, 10, 19))['id'] == v2['id']
        conn.execute("DELETE FROM trade_plans WHERE ticker = 'NVDA' AND signal_date = %s", (date(2026, 10, 19),))


def test_store_plan_compares_execution_fields(workbench_db):
    conn = workbench_db
    rec = _plan_rec(plan_version='plan-2', entry_type='zone_limit', max_hold_sessions=10)
    assert repository.store_plan(conn, rec)['status'] == 'inserted'
    assert repository.store_plan(conn, dict(rec))['status'] == 'existing'
    assert repository.store_plan(conn, {**rec, 'entry_type': 'next_open'})['status'] == 'conflict'
    assert repository.store_plan(conn, {**rec, 'max_hold_sessions': 12})['status'] == 'conflict'
    old = _plan_rec(plan_version='plan-1', signal_date=date(2026, 10, 20))          # old rows: both None
    assert repository.store_plan(conn, old)['status'] == 'inserted'
    assert repository.store_plan(conn, dict(old))['status'] == 'existing'


def test_list_trades_cutoff_accepts_naive_datetime(workbench_db):
    conn = workbench_db
    conn.execute("SET TIME ZONE 'UTC'")                       # a naive cutoff is read in the session time zone
    trade = _trade(conn)
    repository.mark_trade_voided(conn, trade['id'], 'typo', voided_at=T0 + timedelta(days=1))
    naive = (T0 + timedelta(hours=1)).replace(tzinfo=None)
    assert repository.list_trades(conn, 'NVDA', cutoff=naive)[0]['voided_at'] is None


def test_store_plan_still_keys_on_plan_version(workbench_db):
    conn = workbench_db
    first = repository.store_plan(conn, _plan_rec(plan_version='plan-1'))
    assert first['status'] == 'inserted'
    assert repository.store_plan(conn, _plan_rec(plan_version='plan-1'))['status'] == 'existing'
    second = repository.store_plan(conn, _plan_rec(plan_version='plan-2', stop=Decimal('95'), entry_type='zone_limit',
                                                   max_hold_sessions=10))
    assert second['status'] == 'inserted'                                     # another version is not a conflict


# --- insert_decision: the paper_decisions writer (signal_decisions stays read-only) ---

def test_insert_decision_writes_paper_decisions_only():
    """v2.9 D24's invariant keeps holding after v2.11: the public `insert_decision` is the `paper_decisions`
    writer of migration 030, never touches `signal_decisions`, and the old-style fixture helper stays private
    (the stricter scan lives in tests/test_no_decision_writes.py)."""
    assert callable(repository.insert_decision) and callable(repository._insert_decision_for_tests)
    source = Path(repository.__file__).read_text(encoding='utf-8')
    start = source.index('def insert_decision(')
    body = source[start:source.index('\ndef ', start)]
    assert 'paper_decisions' in body and 'signal_decisions' not in body


def test_insert_decision_is_defined_only_in_the_repository():
    """`signal_decisions` is read-only in production code: the one `insert_decision` definition is the
    repository's paper_decisions writer, and no `record_decision` bridge exists anywhere."""
    root = Path('investment_assistant')
    for path in root.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.FunctionDef) and node.name == 'insert_decision':
                assert path == root / 'workbench' / 'repository.py', path
            name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None
            if name == 'record_decision':
                raise AssertionError(f'{path}:{node.lineno}')

