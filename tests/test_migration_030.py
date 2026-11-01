"""Migration 030 (v2.11 spec §7): accounts, account_id on the four ledger tables, account_equity_days,
paper orders and decisions, chain consumption, and the three relaxations (stage_runs.stage 'accounts',
agent_runs mode 'portfolio' with nullable ticker/signal_date/pool, agent_tool_calls.phase 'portfolio').

SQL tests run in a throwaway schema of the disposable test database only."""
import investment_assistant.core.schema as core_schema
import json
import itertools
import os
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

# Repository consumers use their owning layers below.

MIGRATION = Path('migrations/030_accounts.sql')
NOW = datetime(2026, 10, 31, 22, 30, tzinfo=UTC)
DAY = date(2026, 10, 21)
SESSION = date(2026, 10, 30)
REAL = uuid.UUID('00000000-0000-0000-0000-000000000001')
_PLAN_NO = itertools.count(2)      # fresh plan_version per helper call: (ticker, signal_date, plan_version) is unique
_JSONB = {'params', 'detail', 'evidence', 'output', 'holds', 'sizing', 'inputs', 'args', 'result'}

ALL_TABLES = ('accounts', 'account_param_changes', 'account_equity_days', 'paper_orders', 'paper_decisions',
              'paper_chain_done', 'trades', 'account_snapshots', 'cash_flows', 'holding_status', 'agent_runs',
              'agent_tool_calls', 'stage_runs')


@pytest.fixture
def migrated_db():
    """Migrations < 030 in a throwaway schema, dropped afterwards; explicit test URL only."""
    url = os.environ.get('INVESTMENT_ASSISTANT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('explicit test database URL required; disposable schema only')
    import psycopg
    schema = f'm030_test_{uuid.uuid4().hex[:12]}'
    with psycopg.connect(url) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
        conn.execute(f'SET search_path TO {schema}')
        for path in sorted(Path('migrations').glob('0*.sql')):
            if path.name < '030':
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


def _reject(exc_name, fn, *args, **kwargs):
    import psycopg
    with pytest.raises(getattr(psycopg.errors, exc_name)):
        fn(*args, **kwargs)


def _constraints(conn, table):
    return sorted(row[0] for row in conn.execute(
        'SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass', (table,)).fetchall())


def _indexes(conn):
    return sorted(row[0] for row in conn.execute(
        'SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()').fetchall())


def _account(conn, **overrides):
    values = {'id': uuid.uuid4(), 'kind': 'paper', 'strategy': 'agent_managed', 'name': 'Agent 自管',
              'opening_cash': 100000, 'opened_on': DAY}
    values.update(overrides)
    return _insert(conn, 'accounts', values)


def _plan(conn, **overrides):
    values = {'ticker': 'NVDA', 'signal_date': DAY, 'kind': 'rule', 'plan_version': f'plan-{next(_PLAN_NO)}',
              'ref_price': 100, 'atr': 2, 'entry_low': 99, 'entry_high': 101, 'stop': 96, 'target1': 108,
              'valid_from': date(2026, 10, 22), 'valid_until': date(2026, 10, 28),
              'sizing': {}, 'status': 'ok', 'invalid_reasons': [], 'warnings': [], 'inputs': {}}
    values.update(overrides)
    return _insert(conn, 'trade_plans', values)


def _trade(conn, **overrides):
    values = {'request_id': uuid.uuid4(), 'request_hash': 'h', 'ticker': 'NVDA', 'side': 'buy', 'quantity': 10,
              'price': 100, 'fee': 0, 'executed_at': NOW, 'recorded_at': NOW}
    values.update(overrides)
    return _insert(conn, 'trades', values)


def _snapshot(conn, **overrides):
    values = {'request_id': uuid.uuid4(), 'request_hash': 'h', 'as_of': DAY, 'equity': 100000, 'recorded_at': NOW}
    values.update(overrides)
    return _insert(conn, 'account_snapshots', values)


def _flow(conn, **overrides):
    values = {'request_id': uuid.uuid4(), 'request_hash': 'h', 'flow_date': DAY, 'amount': 1000,
              'recorded_at': NOW}
    values.update(overrides)
    return _insert(conn, 'cash_flows', values)


def _status(conn, **overrides):
    values = {'ticker': 'NVDA', 'session': SESSION, 'run_id': 'run-1', 'quantity': 10, 'avg_cost': 100,
              'held_sessions': 3, 'instruction': 'hold', 'reason': 'ok', 'timeline_version': 'timeline-2'}
    values.update(overrides)
    return _insert(conn, 'holding_status', values)


def _stage_run(conn, **overrides):
    values = {'run_id': f'run-{uuid.uuid4().hex[:8]}', 'job': 'daily', 'trigger': 'scheduled', 'session': SESSION,
              'stage': 'accounts', 'stages_version': 'stages-5', 'status': 'success', 'input_count': 0,
              'output_count': 0, 'started_at': NOW, 'finished_at': NOW, 'duration_ms': 10}
    values.update(overrides)
    return _insert(conn, 'stage_runs', values)


def _agent_run(conn, **overrides):
    values = {'id': uuid.uuid4(), 'ticker': None, 'signal_date': None, 'pool': None, 'attempt': 1,
              'mode': 'portfolio', 'agent_version': 'agent-portfolio-1', 'provider': 'chatgpt', 'model': 'gpt-test',
              'cutoff_at': NOW, 'status': 'error', 'reason': 'quota', 'plan_id': None,
              'started_at': NOW, 'finished_at': NOW}
    values.update(overrides)
    return _insert(conn, 'agent_runs', values)


def _order(conn, account_id, **overrides):
    values = {'id': uuid.uuid4(), 'account_id': account_id, 'action': 'buy', 'ticker': 'NVDA',
              'plan_id': _plan(conn), 'chain_signal_date': DAY, 'shares': 10, 'placed_on': SESSION,
              'placed_by': 'host', 'status': 'pending'}
    values.update(overrides)
    return _insert(conn, 'paper_orders', values)


def test_rerunnable(migrated_db):
    conn = migrated_db
    assert core_schema.accounts_ready(conn) is False
    _apply(conn)
    assert core_schema.accounts_ready(conn) is True
    before = {t: _constraints(conn, t) for t in ALL_TABLES}
    indexes = _indexes(conn)
    _apply(conn)                                              # rerunning must not fail or duplicate anything
    assert {t: _constraints(conn, t) for t in ALL_TABLES} == before
    assert _indexes(conn) == indexes
    # exactly one account: the seeded real one, still exactly one row (D2)
    rows = conn.execute('SELECT id, kind, strategy, name, opening_cash, opened_on, status FROM accounts').fetchall()
    assert rows == [(REAL, 'real', 'manual', '真实账户', None, None, 'active')]
    assert conn.execute('SELECT count(*) FROM accounts').fetchone()[0] == 1


def test_old_rows_get_real_account(migrated_db):
    conn = migrated_db
    trade = _trade(conn)
    snap = _snapshot(conn)
    flow = _flow(conn)
    status = _status(conn)
    _apply(conn)                                              # the default folds old rows into the real account
    assert conn.execute('SELECT account_id, recorded_by, detail FROM trades WHERE id = %s', (trade,)
                        ).fetchone() == (REAL, 'user', {})
    assert conn.execute('SELECT account_id FROM account_snapshots WHERE id = %s', (snap,)).fetchone()[0] == REAL
    assert conn.execute('SELECT account_id FROM cash_flows WHERE id = %s', (flow,)).fetchone()[0] == REAL
    assert conn.execute('SELECT account_id FROM holding_status WHERE id = %s', (status,)).fetchone()[0] == REAL


def test_accounts_checks(migrated_db):
    conn = migrated_db
    _apply(conn)
    assert _account(conn)
    assert _account(conn, strategy='follow_plans', name='照计划执行')
    # kind / strategy pairing: a real account is manual, a paper account is not
    _reject('CheckViolation', _account, conn, kind='paper', strategy='manual', name='甲')
    _reject('CheckViolation', _account, conn, kind='real', strategy='follow_plans', name='乙')
    # exactly one real account (partial unique index)
    _reject('UniqueViolation', _account, conn, kind='real', strategy='manual', name='丙')
    # opening_cash and opened_on come together, positive, and a paper account needs them
    _reject('CheckViolation', _account, conn, name='丁', opening_cash=None, opened_on=None)
    _reject('CheckViolation', _account, conn, name='戊', opened_on=None)
    _reject('CheckViolation', _account, conn, name='己', opening_cash=None)
    _reject('CheckViolation', _account, conn, name='庚', opening_cash=-1)
    _reject('CheckViolation', _account, conn, name='  ')
    _reject('UniqueViolation', _account, conn, name='Agent 自管')
    # closing needs a moment; an active account has none
    _reject('CheckViolation', _account, conn, name='辛', status='closed')
    _reject('CheckViolation', _account, conn, name='壬', status='closed', close_reason='对照结束')
    closed = _account(conn, name='癸', status='closed', closed_at=NOW, close_reason='对照结束')
    assert conn.execute('SELECT status, closed_at, close_reason FROM accounts WHERE id = %s', (closed,)
                        ).fetchone() == ('closed', NOW, '对照结束')
    # params default to an empty object; settled_through starts empty
    row = conn.execute('SELECT params, settled_through FROM accounts WHERE id = %s', (closed,)).fetchone()
    assert row == ({}, None)


def test_paper_orders_one_pending_buy_per_chain(migrated_db):
    conn = migrated_db
    _apply(conn)
    acct = _account(conn)
    other = _account(conn, name='照计划执行', strategy='follow_plans')
    assert _order(conn, acct)
    # Same signal date on a different ticker is a different chain and must remain independently buyable.
    assert _order(conn, acct, ticker='AMD', plan_id=_plan(conn, ticker='AMD'))
    # one pending buy per chain per account (spec §7.5): a second pending buy of the same chain is rejected
    # (another plan version of the same chain — the chain, not the version, is what the index keys on)
    _reject('UniqueViolation', _order, conn, acct)
    # a settled order of the same chain no longer blocks, another account is not blocked either
    assert _order(conn, acct, status='filled', settled_on=SESSION)
    assert _order(conn, other)
    # buys must name the plan version and the chain; shares as required per action (the reject rows sit on their
    # own chains so the one-pending-buy index does not fire first)
    _reject('CheckViolation', _order, conn, acct, plan_id=None, chain_signal_date=date(2026, 10, 14))
    _reject('CheckViolation', _order, conn, acct, chain_signal_date=None)
    _reject('CheckViolation', _order, conn, acct, shares=None, chain_signal_date=date(2026, 10, 15))
    assert _order(conn, acct, action='sell', plan_id=None, chain_signal_date=None, shares=None, portion='half')
    _reject('CheckViolation', _order, conn, acct, action='sell', plan_id=None, chain_signal_date=None, shares=None,
            portion='all')
    _reject('CheckViolation', _order, conn, acct, action='sell', plan_id=None, chain_signal_date=None, shares=None,
            portion=None)
    assert _order(conn, acct, action='cancel', plan_id=None, chain_signal_date=None, shares=None,
                  target_order_id=uuid.uuid4())
    _reject('CheckViolation', _order, conn, acct, action='short')
    _reject('CheckViolation', _order, conn, acct, placed_by='model')
    _reject('CheckViolation', _order, conn, acct, status='done')
    _reject('ForeignKeyViolation', _order, conn, acct, plan_id=uuid.uuid4(), chain_signal_date=date(2026, 10, 16))


def test_rerun_repairs_pending_buy_index_without_ticker(migrated_db):
    conn = migrated_db
    _apply(conn)
    # A locally applied earlier 030 draft used the date alone; rerunning the corrected migration repairs it.
    conn.execute('DROP INDEX idx_paper_orders_one_pending_buy')
    conn.execute("CREATE UNIQUE INDEX idx_paper_orders_one_pending_buy ON paper_orders "
                 "(account_id, chain_signal_date) WHERE status = 'pending' AND action = 'buy'")
    acct = _account(conn)
    _order(conn, acct)
    _apply(conn)
    assert _order(conn, acct, ticker='AMD', plan_id=_plan(conn, ticker='AMD'))
    _reject('UniqueViolation', _order, conn, acct)


def test_paper_sell_without_shares_requires_half_portion(migrated_db):
    conn = migrated_db
    _apply(conn)
    acct = _account(conn)
    assert _order(conn, acct, action='sell', plan_id=None, chain_signal_date=None, shares=None, portion='half')
    for portion in (None, 'all'):
        _reject('CheckViolation', _order, conn, acct, action='sell', plan_id=None, chain_signal_date=None,
                shares=None, portion=portion)


def test_rerun_repairs_nullable_shares_check(migrated_db):
    from psycopg import sql

    conn = migrated_db
    _apply(conn)
    checks = conn.execute("SELECT conname FROM pg_constraint WHERE conrelid = 'paper_orders'::regclass "
                          "AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%shares%'").fetchall()
    for (name,) in checks:
        conn.execute(sql.SQL('ALTER TABLE paper_orders DROP CONSTRAINT {}').format(sql.Identifier(name)))
    conn.execute("ALTER TABLE paper_orders ADD CONSTRAINT old_shares_check "
                 "CHECK (shares IS NOT NULL OR action = 'cancel' OR (action = 'sell' AND portion = 'half'))")
    _apply(conn)
    acct = _account(conn)
    _reject('CheckViolation', _order, conn, acct, action='sell', plan_id=None, chain_signal_date=None,
            shares=None, portion=None)


def test_rejected_unknown_buy_plan_keeps_pending_and_fk_checks(migrated_db):
    conn=migrated_db
    _apply(conn)
    aid=_account(conn)
    assert _order(conn,aid,status='rejected',plan_id=None,chain_signal_date=None)
    _reject('CheckViolation',_order,conn,aid,plan_id=None,chain_signal_date=None)
    _reject('ForeignKeyViolation',_order,conn,aid,status='rejected',plan_id=uuid.uuid4())
    _apply(conn)
    assert _order(conn,aid,status='rejected',plan_id=None,chain_signal_date=None)


def test_stage_runs_accepts_accounts(migrated_db):
    conn = migrated_db
    _apply(conn)
    assert _stage_run(conn)
    assert _stage_run(conn, stage='review', stages_version='stages-4')      # the old stages keep working
    _reject('CheckViolation', _stage_run, conn, stage='account')
    _reject('CheckViolation', _stage_run, conn, stage='bogus')


def test_agent_runs_portfolio_mode(migrated_db):
    conn = migrated_db
    _apply(conn)
    run = _agent_run(conn)                                   # ticker / signal_date / pool all NULL (D27)
    assert conn.execute('SELECT mode, ticker, signal_date, pool FROM agent_runs WHERE id = %s', (run,)
                        ).fetchone() == ('portfolio', None, None, None)
    assert _agent_run(conn, ticker='NVDA', signal_date=DAY, pool='main', mode='propose',
                      status='error', reason='x')            # the old shapes keep working
    # ticker is NULL exactly for portfolio runs
    _reject('CheckViolation', _agent_run, conn, mode='propose')
    _reject('CheckViolation', _agent_run, conn, ticker='NVDA')
    _reject('CheckViolation', _agent_run, conn, mode='bogus')
    # tool calls of a portfolio run are recorded like any other phase
    call = {'run_id': run, 'seq': 1, 'phase': 'portfolio', 'tool': 'get_price_bars', 'args': {}, 'result': {},
            'result_sha256': 'a' * 64, 'called_at': NOW}
    assert _insert(conn, 'agent_tool_calls', call)
    _reject('CheckViolation', lambda: _insert(conn, 'agent_tool_calls', {**call, 'seq': 2, 'phase': 'bogus'}))


def test_holding_status_unique_per_account(migrated_db):
    conn = migrated_db
    _apply(conn)
    paper = _account(conn)
    assert _status(conn, account_id=REAL)                     # the 029 unique key is gone: same (ticker, session)
    assert _status(conn, account_id=paper)                    # under another account is fine now
    _reject('UniqueViolation', _status, conn, account_id=REAL)
    assert _status(conn, account_id=REAL, session=date(2026, 10, 29))
    assert _status(conn, account_id=paper, ticker='AMD')
    assert conn.execute("SELECT to_regclass('idx_trades_account')").fetchone()[0] == 'idx_trades_account'
