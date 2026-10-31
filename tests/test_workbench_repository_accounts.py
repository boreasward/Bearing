"""Repository functions of v2.11 accounts and paper trading (spec §7–§8): account scoping of the ledger
readers, equity days, paper orders and decisions. Disposable schema only; the static scoping check reads
repository.py's source and never touches a database."""
import ast
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from investment_assistant.workbench import repository
from conftest import WORKBENCH_MIGRATIONS

NOW = datetime(2026, 10, 31, 23, 0, tzinfo=UTC)
DAY = date(2026, 10, 21)
SESSION = date(2026, 10, 30)
REAL = repository.REAL_ACCOUNT_ID
MIGRATION_030 = Path('migrations/030_accounts.sql')


@pytest.fixture
def accounts_db(workbench_db):
    """workbench_db (migrations through 029) plus migration 030."""
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
    assert repository.trade_by_id(conn, host)['recorded_by'] == 'host'


def test_trade_readers_all_scoped():
    """Every SQL statement reading `trades` carries `account_id` (spec D4): the readers share `_TRADE_COLUMNS`,
    so the column reaches them through the constant; every other statement filters by it."""
    source = Path(repository.__file__).read_text(encoding='utf-8')

    class Literals(ast.NodeVisitor):
        """Merged string literals of the module: implicit concatenation (plain and f-strings) is one literal."""
        def __init__(self):
            self.texts: list[str] = []

        def visit_JoinedStr(self, node):        # a placeholder keeps its name — the check resolves the two it knows
            self.texts.append(''.join(part.value if isinstance(part, ast.Constant)
                                      else '{' + ast.unparse(part.value) + '}'
                                      for part in node.values))

        def visit_Constant(self, node):
            if isinstance(node.value, str):
                self.texts.append(node.value)

    literals = Literals()
    literals.visit(ast.parse(source))
    statements = [text for text in literals.texts if 'FROM trades' in text]
    assert statements                                # the guard itself must keep finding the statements
    for text in statements:
        resolved = (text.replace('{_TRADE_COLUMNS}', repository._TRADE_COLUMNS)
                        .replace('{columns}', repository._TRADE_COLUMNS))
        assert 'account_id' in resolved, text


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


def test_settle_order_only_once(accounts_db):
    conn = accounts_db
    paper = _paper(conn)
    order = _buy_order(conn, paper, 'NVDA')
    settled = repository.settle_order(conn, order['id'], status='filled', status_reason=None, settled_on=SESSION)
    assert (settled['status'], settled['settled_on'], settled['trade_id']) == ('filled', SESSION, None)
    # no longer pending: a replayed settlement must not overwrite the first outcome
    assert repository.settle_order(conn, order['id'], status='expired', status_reason='valid_until passed',
                                   settled_on=SESSION) is None
    assert repository.order_by_id(conn, order['id'])['status'] == 'filled'
    assert repository.pending_orders(conn, paper) == []
    assert [o['id'] for o in repository.orders_for(conn, paper, status='filled')] == [order['id']]


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
    assert repository.paper_tickers(conn) == {'NVDA', 'AMD'}


def test_accounts_ready_false_before_030(pre030_db):
    conn = pre030_db
    assert repository.accounts_ready(conn) is False
    conn.execute(MIGRATION_030.read_text(encoding='utf-8'))
    conn.commit()
    assert repository.accounts_ready(conn) is True
    assert [a['name'] for a in repository.list_accounts(conn)] == ['真实账户']
    assert repository.account_by_name(conn, '真实账户')['id'] == REAL
