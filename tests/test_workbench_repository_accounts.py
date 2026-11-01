"""Repository functions of v2.11 accounts and paper trading (spec §7–§8): account scoping of the ledger
readers, equity days, paper orders and decisions. Disposable schema only; the static scoping check reads
repository.py's source and never touches a database."""
import ast
import os
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from investment_assistant.workbench import account, ledger, repository
from conftest import WORKBENCH_MIGRATIONS

NOW = datetime(2026, 10, 31, 23, 0, tzinfo=UTC)
DAY = date(2026, 10, 21)
SESSION = date(2026, 10, 30)
REAL = repository.REAL_ACCOUNT_ID
MIGRATION_030 = Path('migrations/030_accounts.sql')


@pytest.fixture
def accounts_db(workbench_db):
    """A disposable workbench schema with migration 030; reapply also checks rerunnability."""
    workbench_db.execute(MIGRATION_030.read_text(encoding='utf-8'))
    workbench_db.commit()
    return workbench_db


@pytest.fixture
def pre030_db():
    """The workbench schema stopped before 030: how a not-yet-migrated database looks (D22)."""
    url = os.environ.get('INVESTMENT_ASSISTANT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('explicit test database URL required; disposable schema only')
    import psycopg
    schema = f'pre030_{uuid.uuid4().hex[:12]}'
    with psycopg.connect(url) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
        conn.execute(f'SET search_path TO {schema}')
        for name in WORKBENCH_MIGRATIONS:
            if name < '030_accounts.sql':
                conn.execute(Path('migrations', name).read_text(encoding='utf-8'))
        conn.commit()
        try:
            yield conn
        finally:
            conn.rollback()
            conn.execute(f'DROP SCHEMA {schema} CASCADE')
            conn.commit()


def _paper(conn, name='Agent 自管', **overrides):
    rec = {'id': uuid.uuid4(), 'kind': 'paper', 'strategy': 'agent_managed', 'name': name,
           'opening_cash': Decimal('100000'), 'opened_on': DAY}
    rec.update(overrides)
    return repository.insert_account(conn, rec)['id']


def _trade(conn, account_id=REAL, **overrides):
    rec = {'request_id': uuid.uuid4(), 'ticker': 'NVDA', 'side': 'buy', 'quantity': Decimal(10),
           'price': Decimal(100), 'fee': Decimal(0), 'executed_at': NOW, 'note': None, 'account_id': account_id}
    rec.update(overrides)
    return repository.insert_trade(conn, rec, 'h', recorded_at=NOW)['id']


def _plan(conn):
    return repository.insert_plan(conn, {
        'ticker': 'NVDA', 'signal_date': DAY, 'kind': 'rule', 'plan_version': f'plan-{uuid.uuid4().hex[:6]}',
        'ref_price': Decimal(100), 'atr': Decimal(2), 'entry_low': Decimal(99), 'entry_high': Decimal(101),
        'stop': Decimal(96), 'target1': Decimal(108), 'trail_atr_multiple': Decimal(3),
        'valid_from': date(2026, 10, 22), 'valid_until': date(2026, 10, 28), 'sizing': {}, 'status': 'ok',
        'invalid_reasons': [], 'warnings': [], 'inputs': {}})['id']


def _buy_order(conn, account_id, ticker, **overrides):
    row = {'id': uuid.uuid4(), 'account_id': account_id, 'action': 'buy', 'ticker': ticker, 'plan_id': _plan(conn),
           'chain_signal_date': DAY, 'shares': Decimal(10), 'placed_on': SESSION, 'placed_by': 'host',
           'status': 'pending'}
    row.update(overrides)
    return repository.insert_order(conn, row)


def _equity_day(conn, session, equity, computed_at):
    return repository.insert_equity_day(conn, {
        'account_id': REAL, 'session': session, 'cash': Decimal(0), 'positions_value': Decimal(0),
        'equity': Decimal(equity), 'realized_pnl_cum': Decimal(0), 'unrealized_pnl': Decimal(0),
        'net_flows_cum': Decimal(0), 'positions_count': 0, 'account_version': 'account-1', 'run_id': 'run-1',
        'computed_at': computed_at})


def _snapshot_rec(**overrides):
    return {'request_id': uuid.uuid4(), 'as_of': DAY, 'equity': Decimal(100000), 'note': None,
            'supersedes_id': None, **overrides}


def _flow_rec(**overrides):
    return {'request_id': uuid.uuid4(), 'flow_date': DAY, 'amount': Decimal(1000), 'note': None, **overrides}


def _holding_rec(**overrides):
    return {'ticker': 'NVDA', 'session': SESSION, 'run_id': 'run-1', 'quantity': Decimal(10),
            'avg_cost': Decimal(100), 'held_sessions': 3, 'instruction': 'hold', 'reason': 'ok',
            'timeline_version': 'timeline-2', **overrides}


def test_list_trades_defaults_to_real_account(accounts_db):
    conn = accounts_db
    paper = _paper(conn)
    real_trade = _trade(conn)
    paper_trade = _trade(conn, account_id=paper, ticker='AMD')
    assert [t['id'] for t in repository.list_trades(conn)] == [real_trade]
    assert {t['id'] for t in repository.list_trades(conn, account_id=None)} == {real_trade, paper_trade}
    assert [t['id'] for t in repository.list_trades(conn, account_id=paper)] == [paper_trade]
    # the rows carry the account, the recorder and the detail of migration 030
    row = repository.trade_by_id(conn, real_trade)
    assert (row['account_id'], row['recorded_by'], row['detail']) == (REAL, 'user', {})
    host = _trade(conn, account_id=paper, ticker='MU', recorded_by='host', detail={'fill_case': 'in_zone'})
    assert repository.trade_by_id(conn, host, account_id=paper)['recorded_by'] == 'host'


@pytest.mark.parametrize(('reader', 'key'), [(repository.trade_by_id, 'id'),
                                          (repository.trade_by_request, 'request_id')])
def test_trade_lookup_defaults_to_real_account(accounts_db, reader, key):
    conn = accounts_db
    paper = _paper(conn)
    real_request, paper_request = uuid.uuid4(), uuid.uuid4()
    real_id = _trade(conn, request_id=real_request)
    paper_id = _trade(conn, account_id=paper, request_id=paper_request)
    real_key, paper_key = (real_id, paper_id) if key == 'id' else (real_request, paper_request)
    assert reader(conn, real_key)['id'] == real_id
    assert reader(conn, paper_key) is None
    assert reader(conn, paper_key, account_id=paper)['id'] == paper_id
    assert reader(conn, real_key, account_id=paper) is None


def test_real_ledger_cannot_void_a_paper_trade_by_uuid(accounts_db):
    paper = _paper(accounts_db)
    trade_id = _trade(accounts_db, account_id=paper)
    with pytest.raises(ledger.NotFound, match='trade not found'):
        ledger.void_trade(accounts_db, trade_id, '录错', now=NOW)
    assert repository.trade_by_id(accounts_db, trade_id, account_id=paper)['voided_at'] is None


def test_trade_readers_all_scoped():
    """Every SQL read of trades filters its account; merely selecting account_id is insufficient (D4)."""
    source = Path(repository.__file__).read_text(encoding='utf-8')
    tree = ast.parse(source)
    functions = {node.name: ast.unparse(node) for node in tree.body if isinstance(node, ast.FunctionDef)}

    class Literals(ast.NodeVisitor):
        """Merged string literals of the module: implicit concatenation (plain and f-strings) is one literal."""
        def __init__(self):
            self.texts: list[tuple[str, str]] = []
            self.function = ''

        def visit_FunctionDef(self, node):
            previous = self.function
            self.function = node.name
            self.generic_visit(node)
            self.function = previous

        def visit_JoinedStr(self, node):        # Keep dynamic SQL placeholders for the predicate checks below.
            self.texts.append((self.function, ''.join(part.value if isinstance(part, ast.Constant)
                                                     else '{' + ast.unparse(part.value) + '}'
                                                     for part in node.values)))

        def visit_Constant(self, node):
            if isinstance(node.value, str):
                self.texts.append((self.function, node.value))

    literals = Literals()
    literals.visit(tree)
    statements = [(fn, text) for fn, text in literals.texts if re.search(r'\b(?:FROM|JOIN) trades\b', text)]
    assert statements                                # the guard itself must keep finding the statements
    for fn, text in statements:
        if fn == '_trades_where':
            # The shared list builder inserts its WHERE dynamically; inspect the builder, not the SELECT list.
            body = functions[fn]
            assert "clauses.append('account_id = %s')" in body or '_account_scope(' in body
            assert '{where}' in text
        else:
            assert (re.search(r'\bWHERE\b.*\b(?:t\.)?account_id\s*=', text, re.S)
                    or ('{scope}' in text and '_account_scope(' in functions[fn])), text


def test_equity_days_latest_per_session(accounts_db):
    conn = accounts_db
    early, late = NOW, NOW + timedelta(minutes=5)
    _equity_day(conn, date(2026, 10, 28), 100, early)
    _equity_day(conn, date(2026, 10, 28), 101, late)         # a recompute of the same session appends
    _equity_day(conn, date(2026, 10, 29), 103, early)
    rows = repository.equity_days(conn, REAL)
    assert [(r['session'], r['equity'], r['computed_at']) for r in rows] == [
        (date(2026, 10, 28), Decimal(101), late), (date(2026, 10, 29), Decimal(103), early)]
    assert [r['session'] for r in repository.equity_days(conn, REAL, from_session=date(2026, 10, 29))] == \
        [date(2026, 10, 29)]
    assert [r['session'] for r in repository.equity_days(conn, REAL, to_session=date(2026, 10, 28))] == \
        [date(2026, 10, 28)]
    assert repository.latest_equity_day(conn, REAL, on_or_before=date(2026, 10, 28))['equity'] == Decimal(101)
    assert repository.latest_equity_day(conn, REAL, on_or_before=date(2026, 10, 27)) is None


@pytest.mark.parametrize('status', ['filled', 'voided', 'expired', 'cancelled', 'rejected'])
def test_settle_order_only_once(accounts_db, status):
    conn = accounts_db
    paper = _paper(conn)
    order = _buy_order(conn, paper, 'NVDA')
    settled = repository.settle_order(conn, order['id'], status=status, status_reason=None, settled_on=SESSION)
    assert (settled['status'], settled['settled_on'], settled['trade_id']) == (status, SESSION, None)
    # no longer pending: a replayed settlement must not overwrite the first outcome
    assert repository.settle_order(conn, order['id'], status='expired', status_reason='valid_until passed',
                                   settled_on=SESSION) is None
    assert repository.order_by_id(conn, order['id']) == settled
    assert repository.pending_orders(conn, paper) == []
    assert [o['id'] for o in repository.orders_for(conn, paper, status=status)] == [order['id']]


@pytest.mark.parametrize('status', ['pending', 'unknown'])
def test_settle_order_rejects_nonterminal_status_without_changes(accounts_db, status):
    conn = accounts_db
    paper = _paper(conn)
    trade_id = _trade(conn, account_id=paper)
    order = _buy_order(conn, paper, 'NVDA')
    for reason in ['first attempt', 'second attempt']:
        with pytest.raises(ValueError, match='terminal status'):
            repository.settle_order(conn, order['id'], status=status, status_reason=reason,
                                    settled_on=SESSION, trade_id=trade_id)
        assert repository.order_by_id(conn, order['id']) == order


def test_insert_decision_conflict_returns_existing(accounts_db):
    conn = accounts_db
    paper = _paper(conn)
    first, inserted = repository.insert_decision(conn, {'account_id': paper, 'session': SESSION, 'status': 'ok',
                                                        'strategy_version': 'agent-portfolio-1',
                                                        'summary': '持仓不动', 'run_id': 'run-1'})
    assert inserted is True
    again, inserted_again = repository.insert_decision(
        conn, {'account_id': paper, 'session': SESSION, 'status': 'skipped',
               'strategy_version': 'agent-portfolio-1', 'run_id': 'run-2'})
    assert inserted_again is False
    assert again['id'] == first['id'] and again['status'] == 'ok'      # the stored row, unchanged
    assert repository.decision_for(conn, paper, SESSION)['id'] == first['id']
    assert [d['session'] for d in repository.decisions_for(conn, paper)] == [SESSION]


def test_paper_tickers(accounts_db):
    conn = accounts_db
    paper = _paper(conn)
    closed = _paper(conn, name='旧的对照')
    repository.close_account(conn, closed, '对照结束', closed_at=NOW)
    _trade(conn, account_id=paper, ticker='NVDA')             # an active paper account's holding
    _buy_order(conn, paper, 'AMD')                            # its pending buy
    _trade(conn, account_id=closed, ticker='TSLA')            # a closed account is out of scope
    _trade(conn, ticker='MU')                                 # the real account is out of scope
    _buy_order(conn, paper, 'INTC', status='filled', settled_on=SESSION)   # a settled order is out of scope
    _trade(conn, account_id=paper, ticker='AVGO', executed_at=NOW - timedelta(days=2))
    _trade(conn, account_id=paper, ticker='AVGO', side='sell')  # a closed position is out of scope
    voided = _trade(conn, account_id=paper, ticker='QCOM')
    repository.mark_trade_voided(conn, voided, '录错', voided_at=NOW)
    _buy_order(conn, closed, 'AAPL')                          # closed-account orders are out of scope
    _buy_order(conn, paper, 'ORCL', action='sell', plan_id=None, chain_signal_date=None)
    _buy_order(conn, paper, 'CSCO', action='cancel', plan_id=None, chain_signal_date=None, shares=None,
               target_order_id=uuid.uuid4())
    assert repository.paper_tickers(conn) == {'NVDA', 'AMD'}


def test_paper_tickers_uses_split_adjusted_positions_per_account(accounts_db):
    conn = accounts_db
    paper = _paper(conn)
    other = _paper(conn, name='另一个账户')
    before, after = NOW - timedelta(days=5), NOW - timedelta(days=1)
    for ticker, sold in [('NVDA', 10), ('AMD', 20)]:
        _trade(conn, account_id=paper, ticker=ticker, executed_at=before)
        repository.store_bars(conn, ticker, [{'price_date': date(2026, 10, 28), 'open': 50, 'high': 51,
                                              'low': 49, 'close': 50, 'adj_close': 50, 'volume': 1000,
                                              'split_factor': 2}], now=NOW)
        _trade(conn, account_id=paper, ticker=ticker, side='sell', quantity=Decimal(sold), executed_at=after)
    # This account's malformed history must not consume another account's remaining position.
    _trade(conn, account_id=other, ticker='NVDA', side='sell', executed_at=after + timedelta(minutes=1))
    assert repository.paper_tickers(conn) == {'NVDA'}


def test_snapshot_flow_and_holding_readers_scope_accounts(accounts_db):
    conn = accounts_db
    paper = _paper(conn)
    real_snap = repository.insert_snapshot(conn, _snapshot_rec(), 'h', recorded_at=NOW)
    paper_snap = repository.insert_snapshot(conn, _snapshot_rec(account_id=paper), 'h',
                                            recorded_at=NOW + timedelta(minutes=1))
    real_flow = repository.insert_flow(conn, _flow_rec(), 'h', recorded_at=NOW)
    paper_flow = repository.insert_flow(conn, _flow_rec(account_id=paper), 'h', recorded_at=NOW)
    real_status = repository.insert_holding_status(conn, _holding_rec())
    paper_status = repository.insert_holding_status(conn, _holding_rec(account_id=paper))
    for reader, real_row, paper_row in [(repository.list_snapshots, real_snap, paper_snap),
                                        (repository.list_flows, real_flow, paper_flow),
                                        (repository.holding_status_for, real_status, paper_status)]:
        assert [r['id'] for r in reader(conn)] == [real_row['id']]
        assert [r['id'] for r in reader(conn, account_id=paper)] == [paper_row['id']]
    assert repository.snapshot_head(conn)['id'] == real_snap['id']
    assert repository.snapshot_head(conn, account_id=paper)['id'] == paper_snap['id']
    assert repository.snapshot_at(conn, DAY, cutoff_at=NOW + timedelta(hours=1))['id'] == real_snap['id']
    for reader, row, key in [(repository.snapshot_by_id, paper_snap, 'id'),
                             (repository.snapshot_by_request, paper_snap, 'request_id'),
                             (repository.flow_by_id, paper_flow, 'id'),
                             (repository.flow_by_request, paper_flow, 'request_id')]:
        assert reader(conn, row[key]) is None
        assert reader(conn, row[key], account_id=paper)['id'] == row['id']


def test_plan_linked_trades_default_to_real_account(accounts_db):
    conn = accounts_db
    paper, plan_id = _paper(conn), _plan(conn)
    real_id = _trade(conn, plan_id=plan_id)
    paper_id = _trade(conn, account_id=paper, plan_id=plan_id)
    assert [t['id'] for t in repository.trades_by_plan_ids(conn, [plan_id])] == [real_id]
    assert [t['id'] for t in repository.trades_by_plan_ids(conn, [plan_id], account_id=paper)] == [paper_id]


@pytest.mark.parametrize('kind', ['trade', 'snapshot', 'flow', 'holding'])
def test_legacy_real_account_paths_work_before_030(pre030_db, kind):
    conn = pre030_db
    if kind == 'trade':
        payload = {'request_id': str(uuid.uuid4()), 'ticker': 'NVDA', 'side': 'buy', 'quantity': '10',
                   'price': '100', 'fee': '0', 'executed_at': (NOW - timedelta(hours=1)).isoformat()}
        trade = ledger.record_trade(conn, payload, now=NOW)['record']
        assert ledger.record_trade(conn, payload, now=NOW)['status'] == 'existing'
        assert repository.trade_by_id(conn, trade['id'])['id'] == trade['id']
        assert repository.trade_by_request(conn, trade['request_id'])['id'] == trade['id']
        assert repository.list_trades(conn, cutoff=NOW)[0]['id'] == trade['id']
        assert ledger.void_trade(conn, trade['id'], '录错', now=NOW)['voided_at'] == NOW
    elif kind == 'snapshot':
        payload = {'request_id': str(uuid.uuid4()), 'as_of': DAY.isoformat(), 'equity': '100000'}
        snap = account.record_snapshot(conn, payload, now=NOW)['record']
        assert account.record_snapshot(conn, payload, now=NOW)['status'] == 'existing'
        assert repository.snapshot_by_id(conn, snap['id'])['id'] == snap['id']
        assert repository.snapshot_head(conn)['id'] == snap['id']
        assert repository.snapshot_at(conn, DAY, cutoff_at=NOW)['id'] == snap['id']
        assert [r['id'] for r in repository.list_snapshots(conn)] == [snap['id']]
    elif kind == 'flow':
        payload = {'request_id': str(uuid.uuid4()), 'flow_date': DAY.isoformat(), 'amount': '1000'}
        flow = account.record_flow(conn, payload, now=NOW)['record']
        assert account.record_flow(conn, payload, now=NOW)['status'] == 'existing'
        assert [r['id'] for r in repository.list_flows(conn)] == [flow['id']]
        assert account.void_flow(conn, flow['id'], '录错', now=NOW)['voided_at'] == NOW
    else:
        holding = repository.insert_holding_status(conn, _holding_rec())
        assert holding['replayed'] is False
        assert repository.insert_holding_status(conn, _holding_rec())['replayed'] is True
        assert [r['id'] for r in repository.holding_status_for(conn)] == [holding['id']]


@pytest.mark.parametrize('account_id', [uuid.UUID('11111111-1111-1111-1111-111111111111'), None])
@pytest.mark.parametrize('reader', [repository.list_trades, repository.list_snapshots,
                                  repository.list_flows, repository.holding_status_for])
def test_pre030_never_falls_back_to_global_for_non_real_reads(pre030_db, reader, account_id):
    with pytest.raises(RuntimeError, match='migration_030_missing'):
        reader(pre030_db, account_id=account_id)


@pytest.mark.parametrize('kind', ['trade', 'snapshot', 'flow', 'holding'])
def test_pre030_rejects_paper_writes(pre030_db, kind):
    paper = uuid.uuid4()
    with pytest.raises(RuntimeError, match='migration_030_missing'):
        if kind == 'trade':
            _trade(pre030_db, account_id=paper)
        elif kind == 'snapshot':
            repository.insert_snapshot(pre030_db, _snapshot_rec(account_id=paper), 'h', recorded_at=NOW)
        elif kind == 'flow':
            repository.insert_flow(pre030_db, _flow_rec(account_id=paper), 'h', recorded_at=NOW)
        else:
            repository.insert_holding_status(pre030_db, _holding_rec(account_id=paper))


@pytest.mark.parametrize('reader', ['plan_for_buy_trades', 'retro_subjects', 'retro_subject_keys'])
def test_legacy_derived_trade_readers_work_before_030(pre030_db, reader):
    conn = pre030_db
    before = NOW - timedelta(days=3)
    plan_id = _plan(conn)
    linked_id = _trade(conn, plan_id=plan_id, executed_at=before)
    plain_id = _trade(conn, ticker='AMD', executed_at=before)
    assert [r['id'] for r in repository.trades_by_plan_ids(conn, [plan_id])] == [linked_id]
    if reader == 'plan_for_buy_trades':
        assert repository.plan_for_buy_trades(conn, [linked_id, plain_id]) == {}
    elif reader == 'retro_subjects':
        subjects = repository.retro_subjects(conn, since=SESSION, until=SESSION)
        assert {r['buy_trade_id'] for r in subjects} == {linked_id, plain_id}
    else:
        subjects = repository.retro_subject_keys(conn, until=SESSION)
        assert subjects['units'] == [('NVDA', DAY)]
        assert [r['id'] for r in subjects['trades']] == [plain_id]


def test_accounts_ready_false_before_030(pre030_db):
    conn = pre030_db
    assert repository.accounts_ready(conn) is False
    real_id = _trade(conn)
    assert [r['id'] for r in repository.list_trades(conn)] == [real_id]
    conn.execute(MIGRATION_030.read_text(encoding='utf-8'))
    conn.commit()
    assert repository.accounts_ready(conn) is True
    assert [a['name'] for a in repository.list_accounts(conn)] == ['真实账户']
    assert repository.account_by_name(conn, '真实账户')['id'] == REAL
    paper = _paper(conn)
    paper_id = _trade(conn, account_id=paper)
    assert [r['id'] for r in repository.list_trades(conn)] == [real_id]
    assert [r['id'] for r in repository.list_trades(conn, account_id=paper)] == [paper_id]
