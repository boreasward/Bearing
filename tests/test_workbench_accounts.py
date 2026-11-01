"""Account ledger math and input boundaries: no SQL or market calls."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from importlib import import_module
from uuid import UUID, uuid4

import pytest

from investment_assistant.config import WorkbenchConfig

A = UUID('00000000-0000-0000-0000-000000000002')
S = date(2026, 10, 20)
NOW = datetime(2026, 10, 20, 22, tzinfo=UTC)


@pytest.fixture
def ledger():
    return import_module('investment_assistant.workbench.accounts')


def account(**kw):
    return {'id': A, 'opening_cash': D('10000'), 'opened_on': S, **kw}


def trade(id, ticker='NVDA', side='buy', quantity='10', price='100', fee='1', **kw):
    return {'id': id, 'account_id': A, 'ticker': ticker, 'side': side, 'quantity': D(quantity),
            'price': D(price), 'fee': D(fee), 'executed_at': NOW, 'voided_at': None, 'detail': {}, **kw}


def flow(amount, day=S, **kw):
    return {'flow_date': day, 'amount': D(amount), 'voided_at': None, **kw}


def test_defaults_and_new_account(ledger):
    defaults = ledger.default_params(WorkbenchConfig(risk_pct=.02, single_cap=.3, total_cap=.8))
    assert ledger.ACCOUNT_VERSION == 'account-1'
    assert ledger.REAL_ACCOUNT_ID == UUID('00000000-0000-0000-0000-000000000001')
    assert ledger.DEFAULT_PAPER_CASH == D('100000')
    assert ledger.STRATEGIES == ('manual', 'agent_managed', 'follow_plans')
    assert ledger.STRATEGY_TEXT == {'manual': '手动', 'agent_managed': 'Agent 自管', 'follow_plans': '照计划执行'}
    assert defaults == {'risk_pct': .02, 'single_cap': .3, 'total_cap': .8, 'fee_per_trade': 0,
                        'max_positions': 8, 'reconcile_tolerance_pct': .01, 'reconcile_tolerance_abs': 100}
    rid = uuid4()
    got = ledger.validate_new_account({'request_id': str(rid), 'name': ' 对照 ', 'strategy': 'follow_plans',
                                       'opened_on': '2026-10-20', 'params': {'max_positions': 3}}, defaults=defaults)
    assert got['request_id'] == rid and got['name'] == '对照' and got['kind'] == 'paper'
    assert got['opening_cash'] == D('100000') and got['opened_on'] == S
    assert got['params']['max_positions'] == 3 and got['params']['risk_pct'] == D('.02')


@pytest.mark.parametrize('update', [
    {'opening_cash': 0}, {'opening_cash': -1}, {'opening_cash': True}, {'opening_cash': 'NaN'},
    {'opening_cash': float('inf')}, {'name': ' '}, {'name': 42}, {'strategy': 'manual'}, {'kind': 'real'},
    {'request_id': 'bad'}, {'opened_on': '2026-02-30'}, {'params': []}, {'params': {'risk_pct': .051}},
    {'params': {'single_cap': 0}}, {'params': {'total_cap': 2}}, {'params': {'fee_per_trade': -1}},
    {'params': {'max_positions': True}}, {'params': {'max_positions': 2.0}}, {'params': {'max_positions': 31}},
    {'params': {'risk_pct': True}}, {'params': {'fee_per_trade': 'Infinity'}},
    {'params': {'reconcile_tolerance_pct': -1}}, {'params': {'reconcile_tolerance_abs': 'NaN'}},
])
def test_new_account_rejects_invalid(ledger, update):
    with pytest.raises(ValueError):
        ledger.validate_new_account({'request_id': str(uuid4()), 'name': '试验', 'strategy': 'agent_managed',
                                     'opening_cash': 100, 'opened_on': S, **update},
                                    defaults=ledger.default_params(WorkbenchConfig()))


def test_opening_positions_and_partial_params(ledger):
    got = ledger.validate_opening({'opening_cash': 1, 'opened_on': S,
                                   'opening_positions': [{'ticker': ' nvda ', 'quantity': '2.5', 'cost_per_share': 100}]})
    assert got == {'opening_cash': D('1'), 'opened_on': S,
                   'opening_positions': [{'ticker': 'NVDA', 'quantity': D('2.5'), 'cost_per_share': D('100')}]}
    assert ledger.validate_params({'fee_per_trade': '2.5'}) == {'fee_per_trade': D('2.5')}
    for payload in ({'opening_cash': 0, 'opened_on': S}, {'opening_cash': -1, 'opened_on': S},
                    {'opening_cash': 1, 'opened_on': S, 'opening_positions': [{'ticker': 'AMD', 'quantity': True, 'cost_per_share': 1}]},
                    {'opening_cash': 1, 'opened_on': S, 'opening_positions': [{'ticker': 'AMD', 'quantity': 1, 'cost_per_share': 'NaN'}]}):
        with pytest.raises(ValueError):
            ledger.validate_opening(payload)


def test_cash_excludes_void_future_and_opening_positions(ledger):
    ts = [trade('b'), trade('s', side='sell', quantity='3', price='110'),
          trade('v', voided_at=NOW), trade('future', executed_at=NOW + timedelta(days=1)),
          trade('opening', quantity='100', detail={'opening_position': True})]
    fs = [flow('500'), flow('-50'), flow('999', voided_at=NOW), flow('999', S + timedelta(days=1))]
    assert ledger.cash(account(), ts, fs, cutoff=NOW) == D('9778')  # 10000+450-1001+329


def test_book_fifo_equity_and_flow_adjusted_day_pnl(ledger):
    ts = [trade('b'), trade('s', side='sell', quantity='4', price='110', executed_at=NOW + timedelta(minutes=1))]
    book = ledger.book(ts, splits_by_ticker={})
    assert book['NVDA']['position']['quantity'] == D('6')
    previous = {'equity': D('10000'), 'net_flows_cum': D('0')}
    row = ledger.equity_day(account(), session=S, trades=ts, flows=[flow('500')],
                            closes={'NVDA': (S, D('120'))}, splits_by_ticker={}, previous=previous, run_id='r')
    assert row['account_id'] == A and row['account_version'] == 'account-1' and row['run_id'] == 'r'
    assert row['cash'] == D('9938') and row['positions_value'] == D('720') and row['equity'] == D('10658')
    assert row['realized_pnl_cum'] == D('38.6') and row['unrealized_pnl'] == D('119.4')
    assert row['net_flows_cum'] == D('500') and row['day_pnl'] == D('158') and row['positions_count'] == 1


def test_stale_missing_and_splits(ledger):
    ts = [trade('b'), trade('other', ticker='AMD')]
    row = ledger.equity_day(account(), session=S + timedelta(days=1), trades=ts, flows=[],
                            closes={'NVDA': (S, D('60'))}, splits_by_ticker={'NVDA': [(S + timedelta(days=1), D('2'))]},
                            previous=None, run_id='r')
    assert row['positions_value'] == D('1200') and row['day_pnl'] is None
    assert row['detail']['stale']['NVDA'] == S
    assert row['detail']['bars_missing'] == ['AMD', 'NVDA']
    assert row['detail']['positions']['NVDA']['quantity'] == D('20')
    assert row['detail']['positions']['NVDA']['avg_cost'] == D('50.05')


def test_inception_dietz_and_drawdown(ledger):
    rows = [{'session': S, 'equity': D('10000'), 'net_flows_cum': D('0')},
            {'session': S + timedelta(days=5), 'equity': D('15000'), 'net_flows_cum': D('5000')},
            {'session': S + timedelta(days=10), 'equity': D('16500'), 'net_flows_cum': D('5000')}]
    assert ledger.inception_return(account(), rows) == pytest.approx(1500 / 12500)
    assert ledger.inception_return(account(), rows[:1]) == 0
    assert ledger.inception_return(account(opening_cash=None), rows) is None
    assert ledger.max_drawdown([]) is None
    curve = [{'session': S + timedelta(days=i), 'equity': D(e)} for i, e in enumerate([100, 120, 90, 100, 80, 130])]
    assert ledger.max_drawdown(curve) == {'pct': pytest.approx(1/3), 'peak_session': S + timedelta(days=1),
                                         'trough_session': S + timedelta(days=4)}


def test_inception_return_starts_from_opening_day_equity_with_opening_positions(ledger):
    opening = trade('open', ticker='AAPL', quantity='100', price='200', fee='0',
                    executed_at=datetime(2026, 10, 20, 20, tzinfo=UTC), detail={'opening_position': True})
    rows = []
    for day, close in ((S, '200'), (S + timedelta(days=1), '210'), (S + timedelta(days=2), '190')):
        rows.append(ledger.equity_day(account(), session=day, trades=[opening], flows=[],
                                      closes={'AAPL': (day, D(close))}, splits_by_ticker={},
                                      previous=rows[-1] if rows else None, run_id='r'))
    assert rows[0]['equity'] == D('30000')
    assert ledger.inception_return(account(), rows[:1]) == 0
    assert ledger.inception_return(account(), rows[:2]) == pytest.approx(1000 / 30000)
    assert ledger.inception_return(account(), rows) == pytest.approx(-1000 / 30000)


def test_inception_return_opening_day_flow_is_not_profit_and_weekend_opening(ledger):
    first = {'session': S, 'equity': D('11000'), 'net_flows_cum': D('1000'), 'detail': {}}
    last = {'session': S + timedelta(days=10), 'equity': D('11000'), 'net_flows_cum': D('1000'), 'detail': {}}
    assert ledger.inception_return(account(), [first, last]) == 0
    # Opened on a Saturday: the first valued session is the next trading day.
    weekend = account(opened_on=date(2026, 10, 24))
    assert ledger.opening_session(weekend) == date(2026, 10, 26)
    monday = {'session': date(2026, 10, 26), 'equity': D('10000'), 'net_flows_cum': D('0'), 'detail': {}}
    later = {'session': date(2026, 10, 27), 'equity': D('10100'), 'net_flows_cum': D('0'), 'detail': {}}
    assert ledger.inception_return(weekend, [monday, later]) == pytest.approx(.01)


def test_inception_return_none_without_complete_opening_row(ledger):
    later = {'session': S + timedelta(days=1), 'equity': D('10100'), 'net_flows_cum': D('0'), 'detail': {}}
    assert ledger.inception_return(account(), [later]) is None
    for flag in ({'bars_missing': ['AAPL']}, {'stale': {'AAPL': S}}):
        row = {'session': S, 'equity': D('10000'), 'net_flows_cum': D('0'), 'detail': flag}
        assert ledger.inception_return(account(), [row, {**later, 'detail': {}}]) is None
    broke = {'session': S, 'equity': D('0'), 'net_flows_cum': D('0'), 'detail': {}}
    assert ledger.inception_return(account(), [broke, later]) is None


def test_month_window_requires_exact_trading_month_ends_and_latest_revision(ledger):
    start = {'session': date(2026, 9, 30), 'equity': D('100'), 'computed_at': NOW}
    old = {'session': date(2026, 10, 30), 'equity': D('101'), 'computed_at': NOW}
    latest = {**old, 'equity': D('102'), 'computed_at': NOW + timedelta(seconds=1)}
    assert ledger.month_window([latest, start, old], date(2026, 10, 1)) == (start, latest)
    assert ledger.month_window([start, {**old, 'session': date(2026, 10, 29)}], date(2026, 10, 1)) == (start, None)


@pytest.mark.parametrize('equity,diff,status', [('10000', '100', 'matched'), ('10000', '-100', 'matched'),
                                             ('10000', '100.01', 'mismatch'), ('1000', '10.01', 'mismatch'),
                                             ('100000', '101', 'mismatch')])
def test_reconcile_either_tolerance_strict_boundary(ledger, equity, diff, status):
    rows = [{'session': S, 'equity': D(equity), 'computed_at': NOW}]
    old = {'id': 'old', 'as_of': S, 'equity': D('1'), 'supersedes_id': None}
    snap = {'id': 'new', 'as_of': S + timedelta(days=1), 'equity': D(equity) + D(diff), 'supersedes_id': 'old'}
    result = ledger.reconcile([old, snap], rows, ledger.default_params(WorkbenchConfig()))
    assert result == [{'snapshot_id': 'new', 'as_of': S + timedelta(days=1), 'ledger_session': S,
                       'snapshot_equity': snap['equity'], 'ledger_equity': D(equity), 'diff': D(diff),
                       'pct': D(diff)/D(equity), 'status': status}]
    assert ledger.reconcile([old], [], {})[0]['status'] == 'no_ledger'

