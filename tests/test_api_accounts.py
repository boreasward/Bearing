"""The accounts HTTP contract against an isolated schema; no real service/model calls."""
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal as D
from uuid import uuid4

import pytest
from investment_assistant.workbench import accounts, repository, service
from investment_assistant.core import tx as core_tx

NOW=datetime(2026,10,23,23,tzinfo=UTC)
REAL=str(accounts.REAL_ACCOUNT_ID)

@pytest.fixture
def api_db(workbench_db,monkeypatch):
    @contextmanager
    def connection(): yield workbench_db
    monkeypatch.setattr(core_tx,'connection',connection)
    from investment_assistant.api.routes import workbench
    monkeypatch.setattr(workbench,'_now',lambda:NOW)
    try:
        from investment_assistant.api.routes import accounts as routes
        monkeypatch.setattr(routes,'_now',lambda:NOW)
    except ImportError: pass
    return workbench_db


def create(client,**kw):
    response=client.post('/api/accounts',json={'request_id':str(uuid4()),'name':'模拟','strategy':'follow_plans','opened_on':'2026-10-21',**kw})
    assert response.status_code==200,response.text
    assert set(('latest','inception_return','max_drawdown'))<=response.json()['account'].keys()
    return response.json()['account']['id']


def test_every_account_endpoint(client,api_db):
    rows=client.get('/api/accounts'); assert rows.status_code==200,rows.text
    assert rows.json()['accounts'][0]['opened'] is False
    aid=create(client)
    detail=client.get(f'/api/accounts/{aid}').json()
    assert set(('account','equity','positions','pending_orders','decision','reconciliation','forms','closed_positions'))<=detail.keys()
    assert detail['forms']=={'can_record_trades':False}
    assert detail['account']['opening_cash']=='100000.000000'
    for suffix,key in [('equity','rows'),('trades','trades'),('orders','orders'),('decisions','decisions')]:
        r=client.get(f'/api/accounts/{aid}/{suffix}'); assert r.status_code==200,r.text; assert key in r.json()
    compare=client.get('/api/accounts/compare').json()
    assert [r['key'] for r in compare['rows']]==['inception_return','month_return','max_drawdown','closed_and_win_rate','mean_r','discipline','skipped_plans','positions_and_pending']
    mean=next(r for r in compare['rows'] if r['key']=='mean_r')
    assert all(v['value'] is None and v['text']=='样本 < 5' for v in mean['values'])
    change=client.post(f'/api/accounts/{aid}/params',json={'request_id':str(uuid4()),'params':{'max_positions':3},'reason':'限制'})
    assert change.status_code==200 and change.json()['change']['effective_from']=='2026-10-26'
    assert '_request' not in change.text
    closed=client.post(f'/api/accounts/{aid}/close',json={'reason':'试验结束'})
    assert closed.status_code==200 and closed.json()['status']=='closed'
    assert len(client.get('/api/accounts').json()['accounts'])==1
    assert len(client.get('/api/accounts?include_closed=true').json()['accounts'])==2


def test_opening_cash_positions_and_errors(client,api_db):
    data={'request_id':str(uuid4()),'opening_cash':'10000','opened_on':'2026-10-21',
          'opening_positions':[{'ticker':'NVDA','quantity':'10','cost_per_share':'100'}]}
    r=client.post(f'/api/accounts/{REAL}/opening',json=data); assert r.status_code==200,r.text
    assert r.json()['trades'][0]['detail']['opening_position'] is True
    from investment_assistant.workbench import paper_accounts
    paper_accounts.write_real_equity(api_db,session=date(2026,10,23),now=NOW,run_id='t'); api_db.commit()
    assert client.get(f'/api/accounts/{REAL}').json()['equity']['cash']=='10000.000000'
    assert client.post(f'/api/accounts/{REAL}/opening',json=data).json()['error']=='already_opened'
    assert client.post(f'/api/accounts/{REAL}/close',json={'reason':'x'}).json()['error']=='cannot_close_real_account'
    aid=create(client)
    body={'request_id':str(uuid4()),'account_id':aid,'ticker':'NVDA','side':'buy','quantity':'1','price':'100','executed_at':NOW.isoformat()}
    assert client.post('/api/workbench/trades',json=body).json()['error']=='paper_account_is_host_managed'
    assert client.post('/api/workbench/account/flows',json={'request_id':str(uuid4()),'account_id':aid,'amount':'10','flow_date':'2026-10-21'}).json()['error']=='flows_only_for_real_account'


def test_missing_migration_invalid_identifiers_and_dates(client,api_db):
    assert client.get('/api/accounts/not-a-uuid').status_code==400
    assert client.get(f'/api/accounts/{REAL}/equity?from=bad').status_code==400
    assert client.get(f'/api/accounts/{REAL}/decisions?session=bad').status_code==400
    api_db.execute('DROP TABLE paper_chain_done'); api_db.commit()
    r=client.get('/api/accounts'); assert r.status_code==503 and r.json()['error']=='migration_030_missing'


def test_opening_cannot_postdate_records(client,api_db):
    r=client.post('/api/workbench/account/flows',json={'request_id':str(uuid4()),'amount':'100','flow_date':'2026-10-20'})
    assert r.status_code==200,r.text
    r=client.post(f'/api/accounts/{REAL}/opening',json={'request_id':str(uuid4()),'opening_cash':'1000','opened_on':'2026-10-21'})
    assert r.status_code==400 and r.json()['error']=='opened_on_after_records'


@pytest.mark.parametrize('kind', ['trade', 'flow'])
def test_real_opening_rejects_earlier_record_committed_after_request_cutoff(client, api_db, kind):
    from datetime import timedelta
    recorded_later = NOW+timedelta(seconds=1)
    if kind == 'trade':
        repository.insert_trade(api_db, {'request_id': uuid4(), 'ticker': 'NVDA', 'side': 'buy',
            'quantity': D(1), 'price': D(100), 'fee': D(0), 'executed_at': NOW.replace(day=20),
            'note': None, 'account_id': accounts.REAL_ACCOUNT_ID}, 'late-trade', recorded_at=recorded_later)
    else:
        repository.insert_flow(api_db, {'request_id': uuid4(), 'flow_date': date(2026, 10, 20),
            'amount': D(500), 'note': None}, 'late-flow', recorded_at=recorded_later)
    api_db.commit()
    response = client.post(f'/api/accounts/{REAL}/opening', json={
        'request_id': str(uuid4()), 'opening_cash': '1000', 'opened_on': '2026-10-21'})
    assert response.status_code == 400 and response.json()['error'] == 'opened_on_after_records'
    assert repository.account_by_id(api_db, accounts.REAL_ACCOUNT_ID)['opened_on'] is None
    assert repository.param_changes(api_db, accounts.REAL_ACCOUNT_ID) == []


@pytest.mark.parametrize('kind', ['trade', 'flow'])
def test_opened_real_account_rejects_older_new_records_but_accepts_opening_day(client, api_db, kind):
    from investment_assistant.workbench import paper_accounts
    opening = client.post(f'/api/accounts/{REAL}/opening', json={
        'request_id': str(uuid4()), 'opening_cash': '1000', 'opened_on': '2026-10-22'})
    assert opening.status_code == 200, opening.text
    if kind == 'trade':
        path = '/api/workbench/trades'
        payload = {'request_id': str(uuid4()), 'ticker': 'NVDA', 'side': 'buy', 'quantity': '1',
                   'price': '100', 'fee': '0', 'executed_at': '2026-10-22T03:59:59+00:00'}
        same_day = {'executed_at': '2026-10-22T04:00:00+00:00'}
    else:
        path = '/api/workbench/account/flows'
        payload = {'request_id': str(uuid4()), 'flow_date': '2026-10-21', 'amount': '500'}
        same_day = {'flow_date': '2026-10-22'}
    rejected = client.post(path, json=payload)
    assert rejected.status_code == 400 and rejected.json()['error'] == 'record_before_opened_on'
    # A rejected backfill leaves opening cash intact; broker snapshots may predate opening.
    assert paper_accounts.write_real_equity(api_db, session=date(2026, 10, 22), now=NOW, run_id='before')['cash'] == D(1000)
    api_db.commit()
    snapshot = client.post('/api/workbench/account/snapshots', json={
        'request_id': str(uuid4()), 'as_of': '2026-10-20', 'equity': '777'})
    assert snapshot.status_code == 200, snapshot.text
    accepted = client.post(path, json={**payload, **same_day})
    assert accepted.status_code == 200 and accepted.json()['status'] == 'created'
    equity = paper_accounts.write_real_equity(api_db, session=date(2026, 10, 22), now=NOW, run_id='after')
    assert equity['cash'] == (D(900) if kind == 'trade' else D(1500))


@pytest.mark.parametrize('kind', ['trade', 'flow'])
def test_old_voided_record_exact_replay_survives_later_real_opening(client, api_db, kind):
    if kind == 'trade':
        path = '/api/workbench/trades'
        payload = {'request_id': str(uuid4()), 'ticker': 'NVDA', 'side': 'buy', 'quantity': '1',
                   'price': '100', 'executed_at': '2026-10-20T14:00:00+00:00'}
    else:
        path = '/api/workbench/account/flows'
        payload = {'request_id': str(uuid4()), 'flow_date': '2026-10-20', 'amount': '500'}
    original = client.post(path, json=payload)
    assert original.status_code == 200, original.text
    rid = original.json()['record']['id']
    assert client.post(f'{path}/{rid}/void', json={'reason': '旧记录录错'}).status_code == 200
    opened = client.post(f'/api/accounts/{REAL}/opening', json={
        'request_id': str(uuid4()), 'opening_cash': '1000', 'opened_on': '2026-10-22'})
    assert opened.status_code == 200, opened.text
    replay = client.post(path, json=payload)
    assert replay.status_code == 200 and replay.json()['status'] == 'existing'
    assert replay.json()['record']['id'] == rid and replay.json()['record']['voided_at'] is not None
    table = 'trades' if kind == 'trade' else 'cash_flows'
    assert api_db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 1


def test_account_trades_positions_and_discipline_are_scoped(client,api_db):
    from test_workbench_paper_accounts import paper,seed_order,price,DAY
    from investment_assistant.workbench import paper_accounts
    a=paper(api_db); seed_order(api_db,a); price(api_db,DAY)
    paper_accounts.settle_account(api_db,a,through=DAY,now=NOW,run_id='r'); api_db.commit()
    aid=str(a['id'])
    assert client.get(f'/api/accounts/{aid}').json()['positions'][0]['ticker']=='NVDA'
    assert client.get(f'/api/accounts/{REAL}').json()['positions']==[]
    assert len(client.get(f'/api/accounts/{aid}/trades').json()['trades'])==1
    assert client.get('/api/workbench/trades').json()['trades']==[]
    assert len(client.get(f'/api/workbench/trades?account_id={aid}').json()['trades'])==1
    assert client.get('/api/workbench/discipline').json()['trades']==[]
    assert len(client.get(f'/api/workbench/discipline?account_id={aid}').json()['trades'])==1


def test_compare_discipline_text_names_every_count(client,api_db):
    from test_workbench_paper_accounts import paper,seed_order,price,DAY
    from investment_assistant.workbench import paper_accounts
    a=paper(api_db); seed_order(api_db,a); price(api_db,DAY)
    paper_accounts.settle_account(api_db,a,through=DAY,now=NOW,run_id='r'); api_db.commit()
    data=client.get('/api/accounts/compare').json()
    row=next(r for r in data['rows'] if r['key']=='discipline')
    value=row['values'][1]
    assert all(label in value['text'] for label in ('计划外','追高','早卖','死扛'))


def test_full_decision_joins_next_day_fill_and_host_check(client,api_db):
    from test_workbench_paper_accounts import paper,seed_order,price,DAY
    from investment_assistant.workbench import paper_accounts
    from investment_assistant.workbench.agent import portfolio_check
    a=paper(api_db); p,old=seed_order(api_db,a)
    repository.settle_order(api_db,old['id'],status='cancelled',status_reason='fixture',settled_on=DAY)
    p=repository.insert_plan(api_db,{**p,'kind':'agent','plan_version':'agent-3','valid_from':date(2026,10,22)})
    facts=paper_accounts.decision_facts(api_db,a,session=DAY,now=NOW)
    facts['buyable']=[{**p,'plan_id':p['id'],'chain_signal_date':p['signal_date'],'max_shares':10}]
    output={'orders':[{'action':'buy','plan_id':str(p['id']),'shares':1,'reason':'test'}],'holds':[],'summary':'buy'}
    paper_accounts._save_decision(api_db,a,session=DAY,now=NOW,run_id='r',output=output,facts=facts,
        evidence=portfolio_check.build_evidence(facts))
    price(api_db,date(2026,10,22))
    paper_accounts.settle_account(api_db,a,through=date(2026,10,22),now=NOW,run_id='r2');api_db.commit()
    response=client.get(f"/api/accounts/{a['id']}/decisions?session={DAY}")
    assert response.status_code==200,response.text
    decision=response.json()['decision']
    assert set(decision['evidence'])==set(portfolio_check.FACT_KEYS)
    assert decision['output']==output and decision['orders_placed']==1
    order=decision['orders'][0]
    assert order['status']=='filled' and order['filled_price']=='100.000000'
    assert order['host_check']=='通过校验' and order['result_text'].startswith('已成交')


@pytest.mark.parametrize('status, trade_id, quantity', [('pending', None, None), ('pending', uuid4(), None),
                                                     ('filled', uuid4(), D('1.5'))])
def test_order_view_reports_actual_filled_quantity_scoped_to_account(monkeypatch, status, trade_id, quantity):
    from investment_assistant.workbench import account_views
    aid = uuid4()
    lookups = []
    def trade(conn, identifier, *, account_id):
        lookups.append((identifier, account_id))
        return {'quantity': quantity, 'price': D(50), 'executed_at': NOW}
    monkeypatch.setattr(repository, 'trade_by_id', trade)
    row = {'account_id': aid, 'trade_id': trade_id, 'status': status, 'status_reason': None, 'shares': D(3)}
    result = account_views._order(None, row)
    assert result['filled_quantity'] == quantity
    assert result['shares'] == D(3)
    assert lookups == ([(trade_id, aid)] if status == 'filled' and trade_id else [])


def test_order_view_never_fills_quantity_from_another_account(monkeypatch):
    from investment_assistant.workbench import account_views
    aid, other_aid, trade_id = uuid4(), uuid4(), uuid4()
    def trade(conn, identifier, *, account_id):
        assert identifier == trade_id and account_id == aid
        return {'quantity': D(999), 'price': D(50), 'executed_at': NOW} if account_id == other_aid else None
    monkeypatch.setattr(repository, 'trade_by_id', trade)
    result = account_views._order(None, {'account_id': aid, 'trade_id': trade_id, 'status': 'filled',
        'status_reason': None, 'shares': D(3)})
    assert result['filled_quantity'] is None


@pytest.mark.parametrize('quantity, portion', [('10', 'all'), ('10', 'half'), ('3', 'half')])
@pytest.mark.parametrize('split', [1, 2])
def test_sell_orders_keep_expected_shares_and_expose_actual_fill_after_split(client, api_db, quantity, portion, split):
    from test_workbench_paper_accounts import paper, seed_order, price, DAY
    from investment_assistant.workbench import paper_accounts
    first_now = NOW.replace(day=21)
    next_now = NOW.replace(day=22)
    next_day = date(2026, 10, 22)
    a = paper(api_db, strategy='agent_managed')
    seed_order(api_db, a, shares=quantity)
    price(api_db, DAY, now=first_now)
    paper_accounts.settle_account(api_db, a, through=DAY, now=first_now, run_id='buy')
    facts = paper_accounts.decision_facts(api_db, a, session=DAY, now=first_now)
    output = {'orders': [{'action': 'sell', 'ticker': 'NVDA', 'portion': portion, 'reason': '调整仓位'}],
              'holds': [], 'summary': '开盘卖出'}
    paper_accounts._save_decision(api_db, a, session=DAY, now=first_now, run_id='decision', output=output, facts=facts)
    # Half is rounded down to whole shares on the basis in force when each number is computed.
    expected = D(quantity) if portion == 'all' else D(int(quantity) // 2)
    filled = D(quantity) * split if portion == 'all' else D(int(quantity) * split // 2)
    api_db.commit()
    pending = client.get(f"/api/accounts/{a['id']}/orders?status=pending").json()['orders']
    assert len(pending) == 1 and D(pending[0]['shares']) == expected
    assert pending[0]['portion'] == portion and pending[0]['filled_quantity'] is None
    # The split becomes known after the decision; pending intent remains on its original share basis.
    if split == 2:
        price(api_db, DAY, op=50, hi=51.5, lo=49.5, cl=50.5, now=next_now)
    price(api_db, next_day, op=100/split, hi=103/split, lo=99/split, cl=101/split, split=split, now=next_now)
    paper_accounts.settle_account(api_db, a, through=next_day, now=next_now, run_id='sell')
    api_db.commit()
    decision = client.get(f"/api/accounts/{a['id']}/decisions?session={DAY}").json()['decision']
    order = decision['orders'][0]
    assert order['status'] == 'filled' and order['portion'] == portion
    assert D(order['shares']) == expected and D(order['filled_quantity']) == filled
    assert decision['output'] == output
    fills = repository.list_trades(api_db, account_id=a['id'])
    assert fills[0]['quantity'] == D(quantity)
    assert fills[1]['quantity'] == filled and fills[1]['detail']['exit_reason'] == 'agent_sell'
    assert order['trade_id'] == str(fills[1]['id'])
    assert repository.order_by_id(api_db, pending[0]['id'])['shares'] == expected
    assert repository.list_trades(api_db, account_id=accounts.REAL_ACCOUNT_ID) == []


def _equity(conn, session, amount, *, detail=None):
    return repository.insert_equity_day(conn, {
        'account_id': accounts.REAL_ACCOUNT_ID, 'session': session, 'cash': D(amount),
        'positions_value': 0, 'equity': D(amount), 'realized_pnl_cum': 0,
        'unrealized_pnl': 0, 'net_flows_cum': 0, 'positions_count': 0,
        'detail': detail or {}, 'account_version': accounts.ACCOUNT_VERSION,
        'run_id': 'month-to-date', 'computed_at': NOW,
    })


def _month_value(client):
    response = client.get('/api/accounts/compare')
    assert response.status_code == 200, response.text
    row = next(r for r in response.json()['rows'] if r['key'] == 'month_return')
    return row['values'][0], row['spy']


@pytest.mark.parametrize('net_flow, expected', [(0, .1), (40, .06), (-40, .14)])
def test_compare_month_to_date_uses_exact_common_endpoints_and_net_flows(client, api_db, net_flow, expected):
    from test_workbench_paper_accounts import price
    repository.set_account_opening(api_db, accounts.REAL_ACCOUNT_ID, opening_cash=1000, opened_on=date(2026, 9, 1))
    _equity(api_db, date(2026, 9, 30), 1000)
    _equity(api_db, date(2026, 10, 23), 1100)
    # The starting day's flow is already in starting equity; future flows are excluded.
    for day, amount in [(date(2026, 9, 30), 900), (date(2026, 10, 21), net_flow), (date(2026, 10, 26), 900)]:
        if amount:
            repository.insert_flow(api_db, {'request_id': uuid4(), 'flow_date': day, 'amount': D(amount), 'note': None}, str(uuid4()), recorded_at=NOW)
    for day, close in [(date(2026, 9, 30), 100), (date(2026, 10, 22), 105), (date(2026, 10, 23), 120)]:
        price(api_db, day, cl=close, ticker='SPY')
    api_db.commit()
    value, spy = _month_value(client)
    assert value['value'] == pytest.approx(expected)
    assert value['note'] == 'ledger'
    assert spy == '+20.0%'


@pytest.mark.parametrize('missing', ['start', 'end', 'stale', 'no_price'])
def test_compare_month_to_date_rejects_missing_or_incomplete_exact_endpoints(client, api_db, missing):
    from test_workbench_paper_accounts import price
    repository.set_account_opening(api_db, accounts.REAL_ACCOUNT_ID, opening_cash=1000, opened_on=date(2026, 9, 1))
    _equity(api_db, date(2026, 9, 29) if missing == 'start' else date(2026, 9, 30), 1000)
    detail = {'bars_missing': ['NVDA'], 'stale': {'NVDA': '2026-10-22'}} if missing == 'stale' else {'bars_missing': ['NVDA']} if missing == 'no_price' else {}
    _equity(api_db, date(2026, 10, 22) if missing == 'end' else date(2026, 10, 23), 0 if missing == 'no_price' else 1100, detail=detail)
    price(api_db, date(2026, 9, 30), cl=100, ticker='SPY')
    price(api_db, date(2026, 10, 22), cl=110, ticker='SPY')  # cutoff session missing
    api_db.commit()
    value, spy = _month_value(client)
    assert value['value'] is None and value['text'] == '—'
    assert value['note'] in ('缺少区间端点', '端点估值不完整')
    assert spy == '—'


def test_compare_month_end_matches_frozen_monthly5_math(client, api_db, monkeypatch):
    from investment_assistant.api.routes import accounts as routes
    from investment_assistant.workbench import monthly
    at = NOW.replace(day=30)
    monkeypatch.setattr(routes, '_now', lambda: at)
    repository.set_account_opening(api_db, accounts.REAL_ACCOUNT_ID, opening_cash=1000, opened_on=date(2026, 9, 1))
    rows = [_equity(api_db, date(2026, 9, 30), 1000), _equity(api_db, date(2026, 10, 30), 1150)]
    flow = repository.insert_flow(api_db, {'request_id': uuid4(), 'flow_date': date(2026, 10, 23), 'amount': D(50), 'note': None}, 'flow', recorded_at=NOW)
    expected = monthly.month_return_v2(date(2026, 10, 1), equity_rows=rows, snapshots=[], flows=[flow], opened=True)
    api_db.commit()
    assert _month_value(client)[0]['value'] == expected['return'] == .1


def test_compare_month_to_date_snapshot_fallback_requires_same_exact_dates(client, api_db):
    for day, amount in [(date(2026, 9, 30), 1000), (date(2026, 10, 22), 1100)]:
        repository.insert_snapshot(api_db, {'request_id': uuid4(), 'as_of': day, 'equity': D(amount), 'note': None, 'supersedes_id': None}, str(uuid4()), recorded_at=NOW)
    api_db.commit()
    assert _month_value(client)[0]['value'] is None
    repository.insert_snapshot(api_db, {'request_id': uuid4(), 'as_of': date(2026, 10, 23), 'equity': D(1200), 'note': None, 'supersedes_id': None}, str(uuid4()), recorded_at=NOW)
    api_db.commit()
    value, _ = _month_value(client)
    assert value['value'] == .2 and value['note'] == 'snapshot'


@pytest.mark.parametrize('opened_on, opening_session', [('2026-10-17', '2026-10-19'), ('2026-09-07', '2026-09-08')])
def test_opening_session_uses_exchange_calendar_in_every_account_projection(client, api_db, opened_on, opening_session):
    assert client.get('/api/accounts').json()['accounts'][0]['opening_session'] is None
    payload = {'request_id': str(uuid4()), 'name': '非交易日开户', 'strategy': 'follow_plans', 'opened_on': opened_on}
    created = client.post('/api/accounts', json=payload).json()['account']
    aid = created['id']
    changed = client.post(f'/api/accounts/{aid}/params', json={'request_id': str(uuid4()), 'params': {'max_positions': 3}, 'reason': '限制'}).json()['account']
    opened = client.post(f'/api/accounts/{REAL}/opening', json={'request_id': str(uuid4()), 'opening_cash': '1000', 'opened_on': opened_on}).json()['account']
    detail = client.get(f'/api/accounts/{aid}').json()['account']
    listed = next(a for a in client.get('/api/accounts').json()['accounts'] if a['id'] == aid)
    closed = client.post(f'/api/accounts/{aid}/close', json={'reason': '结束'}).json()
    for row in (created, changed, opened, detail, listed, closed):
        assert row['opened_on'] == opened_on
        assert row['opening_session'] == opening_session
