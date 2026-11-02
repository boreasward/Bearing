"""Account orchestration regressions; only the disposable PostgreSQL fixture is used."""
import investment_assistant.analysis.agent.runner as analysis_agent_runner
import investment_assistant.workflow.nodes.accounts as paper_orchestration
from investment_assistant.execution import chains as execution_chains
from investment_assistant.execution.paper import accounts as execution_paper_accounts
from investment_assistant.execution import plan_context as execution_plan_context
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.core.dbutil as core_dbutil
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import investment_assistant.workflow.repo as workflow_repo
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal as D
from importlib import import_module, util
from uuid import uuid4

import pytest

from investment_assistant.core import tx as core_tx

import investment_assistant.core.tx as service
from investment_assistant.execution import accounts

DAY = date(2026, 10, 21)
END = date(2026, 10, 23)
NOW = datetime(2026, 10, 23, 23, tzinfo=UTC)


def module():
    assert util.find_spec('investment_assistant.workflow.nodes.accounts'), 'paper account orchestration missing'
    return import_module('investment_assistant.workflow.nodes.accounts')


@pytest.fixture
def conn(workbench_db, monkeypatch):
    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(core_tx, 'connection', connection)
    return workbench_db


def paper(conn, name='模拟', strategy='follow_plans'):
    return execution_repo.insert_account(conn, dict(id=uuid4(), name=name, kind='paper', strategy=strategy,
        opening_cash=D(100000), opened_on=DAY, params=accounts.default_params(__import__('investment_assistant.config', fromlist=['WorkbenchConfig']).WorkbenchConfig())))


def test_create_replay_retains_defaults_after_parameter_change(conn):
    m = module()
    payload = dict(request_id=str(uuid4()), name='照计划', strategy='follow_plans')
    created = execution_paper_accounts.create_paper_account(payload, now=NOW)
    assert created['status'] == 'created'
    aid = created['account']['id']
    execution_paper_accounts.change_params(aid, dict(request_id=str(uuid4()), params={'max_positions': 2}, reason='限制'), now=NOW)
    retry = execution_paper_accounts.create_paper_account(payload, now=NOW.replace(day=26))
    assert retry['status'] == 'existing'
    assert str(retry['account']['id']) == str(aid)
    assert len(execution_repo.list_accounts(conn)) == 2


@pytest.mark.parametrize('kind', ['trade', 'flow'])
@pytest.mark.parametrize('voided', [False, True])
def test_real_opening_checks_current_records_after_waiting_for_account_lock(monkeypatch, kind, voided):
    from datetime import timedelta
    from investment_assistant.execution import ledger
    m = module()
    real = {'id': accounts.REAL_ACCOUNT_ID, 'kind': 'real', 'opened_on': None}
    record = {'recorded_at': NOW+timedelta(seconds=1), 'executed_at': NOW.replace(day=21),
              'flow_date': DAY, 'voided_at': NOW+timedelta(seconds=2) if voided else None}
    locked = []
    writes = []
    @contextmanager
    def tx():
        yield object()
    def lock(conn, key):
        locked.append(key)
    def trades(conn, *, cutoff=None, account_id):
        assert locked == [f'account:{accounts.REAL_ACCOUNT_ID}'] and account_id == accounts.REAL_ACCOUNT_ID
        return [record] if kind == 'trade' and (cutoff is None or record['recorded_at'] <= cutoff) else []
    def flows(conn, *, account_id):
        assert locked == [f'account:{accounts.REAL_ACCOUNT_ID}'] and account_id == accounts.REAL_ACCOUNT_ID
        return [record] if kind == 'flow' else []
    def opening(conn, account_id, **values):
        writes.append(values)
        return {**real, **values}
    monkeypatch.setattr(core_tx, '_tx', tx)
    monkeypatch.setattr(execution_paper_accounts, 'require_account', lambda *args: real)
    monkeypatch.setattr(core_dbutil, 'lock', lock)
    monkeypatch.setattr(execution_repo, 'list_trades', trades)
    monkeypatch.setattr(execution_repo, 'list_flows', flows)
    monkeypatch.setattr(execution_repo, 'set_account_opening', opening)
    monkeypatch.setattr(execution_repo, 'insert_param_change', lambda *args: None)
    monkeypatch.setattr(execution_paper_accounts, '_result_account', lambda conn, account, *, now: {**account, 'cutoff_at': now})
    payload = {'request_id': str(uuid4()), 'opened_on': '2026-10-22', 'opening_cash': '1000'}
    if voided:
        result = execution_paper_accounts.open_real_account(accounts.REAL_ACCOUNT_ID, payload, now=NOW)
        assert result['account']['opened_on'] == date(2026, 10, 22)
        assert result['account']['cutoff_at'] == NOW
    else:
        with pytest.raises(ledger.LedgerError, match='^opened_on_after_records$'):
            execution_paper_accounts.open_real_account(accounts.REAL_ACCOUNT_ID, payload, now=NOW)
        assert writes == []


def test_settlement_catches_up_and_repeat_is_noop(conn):
    m = module(); a = paper(conn)
    out = execution_paper_accounts.settle_account(conn, a, through=END, now=NOW, run_id='paper-test')
    assert [r['session'] for r in execution_repo.equity_days(conn, a['id'])] == [DAY, date(2026,10,22), END]
    assert execution_repo.account_by_id(conn, a['id'])['settled_through'] == END
    again = execution_paper_accounts.settle_account(conn, a, through=END, now=NOW, run_id='paper-again')
    assert again['settled'] == {'filled':0,'voided':0,'expired':0,'cancelled':0,'exits':0}
    assert conn.execute('SELECT count(*) FROM account_equity_days').fetchone()[0] == 3


def test_real_unopened_no_equity_and_backfill_appends(conn):
    m = module()
    assert execution_paper_accounts.write_real_equity(conn, session=END, now=NOW, run_id='r') is None
    execution_repo.set_account_opening(conn, accounts.REAL_ACCOUNT_ID, opening_cash=10000, opened_on=DAY)
    execution_paper_accounts.write_real_equity(conn, session=END, now=NOW, run_id='r')
    original = execution_repo.latest_equity_day(conn, accounts.REAL_ACCOUNT_ID, on_or_before=END)
    execution_repo.insert_flow(conn, dict(request_id=uuid4(),flow_date=DAY,amount=D(500),note=None), 'h', recorded_at=NOW)
    execution_paper_accounts.write_real_equity(conn, session=END, now=NOW, run_id='backfill')
    updated = execution_repo.latest_equity_day(conn, accounts.REAL_ACCOUNT_ID, on_or_before=END)
    assert updated['equity'] == D(10500) and updated['id'] != original['id']
    count = conn.execute('SELECT count(*) FROM account_equity_days').fetchone()[0]
    execution_paper_accounts.write_real_equity(conn, session=END, now=NOW, run_id='same')
    assert conn.execute('SELECT count(*) FROM account_equity_days').fetchone()[0] == count


def test_stage_orders_four_phases_and_isolates_accounts(conn, monkeypatch):
    m = module(); a=paper(conn,'a'); b=paper(conn,'b','agent_managed'); events=[]
    def settle(conn, account, **kw):
        events.append(('settle',account['name']))
        if account['name']=='a': raise ValueError('bad price')
        return {'settled':{'filled':0,'voided':0,'expired':0,'cancelled':0,'exits':0}}
    monkeypatch.setattr(execution_paper_accounts,'settle_account',settle)
    monkeypatch.setattr(execution_paper_accounts,'write_real_equity',lambda *a,**k: events.append(('equity','real')))
    monkeypatch.setattr(execution_paper_accounts,'write_paper_holdings',lambda conn, account, **kw: events.append(('hold',account['name'])) or [])
    monkeypatch.setattr(analysis_agent_runner,'run_portfolio_decision',lambda conn,account,**kw: events.append(('decide',account['name'])) or {'status':'skipped'})
    m.run_stage(END, now=NOW, deps=service.Deps(), run_id='r', acc=None, settings=None)
    assert events == [('settle','a'),('settle','b'),('equity','real'),('hold','b'),('decide','b')]


def test_follow_decision_reused_before_building_candidates(conn, monkeypatch):
    m=module(); a=paper(conn)
    first=execution_paper_accounts.decide_follow_plans(conn,a,recent_closed=paper_orchestration.recent_closed_facts,session=END,now=NOW,run_id='r')
    monkeypatch.setattr(execution_paper_accounts,'decision_facts',lambda *a,**kw: pytest.fail('repeat rebuilt facts'))
    again=execution_paper_accounts.decide_follow_plans(conn,a,recent_closed=paper_orchestration.recent_closed_facts,session=END,now=NOW,run_id='r2')
    assert first['id']==again['id'] and again['status']=='ok'


def test_chinese_maps_and_current_graph_contract():
    import investment_assistant.workflow.graph as workflow
    import investment_assistant.workflow.check as derivation
    from investment_assistant.execution.paper import fill as paper_fill
    from investment_assistant.execution.paper import strategies as paper_strategies
    assert getattr(accounts,'ORDER_STATUS_TEXT',{}).get('rejected')=='未通过校验'
    assert getattr(paper_strategies,'REJECT_TEXT',{}).get('no_position')=='无可卖持仓'
    assert getattr(paper_fill,'EXIT_REASON_TEXT',{}).get('stop')=='止损触及'
    assert workflow.DAILY_GRAPH_VERSION=='daily-3' and {'portfolio','accounts','propose'} <= set(workflow.BODIES)
    assert derivation.RUN_STAGE_COUNT['stages-5']==11 and derivation.RUN_STAGE_COUNT['stages-6']==13


def test_unknown_plan_intent_is_rejected_and_saved(conn):
    m=module(); a=paper(conn)
    facts=execution_paper_accounts.decision_facts(conn,a,recent_closed=paper_orchestration.recent_closed_facts,session=END,now=NOW)
    output={'orders':[{'action':'buy','plan_id':str(uuid4()),'shares':1,'reason':'unknown'}],'holds':[],'summary':'test'}
    row=execution_paper_accounts._save_decision(conn,a,session=END,now=NOW,run_id='r',output=output,facts=facts)
    orders=execution_repo.orders_for(conn,a['id'])
    assert row['detail']['orders_rejected']==1 and orders[0]['status']=='rejected'
    assert orders[0]['plan_id'] is None and row['output']==output


@pytest.mark.parametrize('quantity, portion, expected', [('10', 'all', '10'), ('10', 'half', '5'), ('3', 'half', '1'), ('61', 'half', '30')])
def test_sell_intent_records_expected_portion_without_changing_output(monkeypatch, quantity, portion, expected):
    m = module()
    account = {'id': uuid4(), 'strategy': 'agent_managed'}
    facts = {'account': {'cash': D(1000), 'params': accounts.default_params(
        __import__('investment_assistant.config', fromlist=['WorkbenchConfig']).WorkbenchConfig())},
        'positions': [{'ticker': 'NVDA', 'quantity': D(quantity), 'market_value': D(quantity)*100}],
        'buyable': [], '_orders': []}
    output = {'orders': [{'action': 'sell', 'ticker': 'NVDA', 'portion': portion, 'reason': '减仓'}],
              'holds': [], 'summary': '调整持仓'}
    written = []
    monkeypatch.setattr(execution_repo, 'insert_decision', lambda conn, row: ({**row, 'id': uuid4()}, True))
    monkeypatch.setattr(execution_repo, 'insert_order', lambda conn, row: written.append(row) or row)
    result = execution_paper_accounts._save_decision(None, account, session=END, now=NOW, run_id='unit', output=output, facts=facts, evidence={})
    assert result['output'] == output and result['detail']['orders_placed'] == 1
    assert len(written) == 1 and written[0]['status'] == 'pending'
    assert written[0]['shares'] == D(expected) and written[0]['portion'] == portion
    assert 'shares' not in output['orders'][0]


def seed_order(conn,a,*,ticker='NVDA',max_hold=10,shares=10):
    from datetime import timedelta
    plan=execution_repo.insert_plan(conn,{'ticker':ticker,'signal_date':date(2026,10,20),'kind':'rule','plan_version':'plan-2',
        'ref_price':D(100),'atr':D(2),'entry_low':D(98),'entry_high':D(102),'stop':D(94),'target1':D(112),
        'trail_atr_multiple':D(3),'valid_from':DAY,'valid_until':END,'sizing':{},'status':'ok',
        'invalid_reasons':[],'warnings':[],'inputs':{},'entry_type':'zone_limit','max_hold_sessions':max_hold})
    order=execution_repo.insert_order(conn,{'id':uuid4(),'account_id':a['id'],'action':'buy','ticker':ticker,'plan_id':plan['id'],
        'chain_signal_date':date(2026,10,20),'shares':D(shares),'placed_on':date(2026,10,20),'placed_by':'host','status':'pending'})
    return plan,order


def price(conn,day,*,op=100,hi=103,lo=99,cl=101,split=1,ticker='NVDA',now=NOW):
    data_repo.store_bars(conn,ticker,[{'price_date':day,'open':op,'high':hi,'low':lo,'close':cl,'adj_close':cl,'volume':1000,'split_factor':split}],now=now)


def test_catchup_fill_exit_chain_done_and_frozen_state(conn):
    m=module(); a=paper(conn); p,o=seed_order(conn,a)
    price(conn,DAY); price(conn,date(2026,10,22),op=102,hi=114,lo=100,cl=111); price(conn,END,op=99,hi=100,lo=95,cl=96)
    out=execution_paper_accounts.settle_account(conn,a,through=END,now=NOW,run_id='r')
    fills=execution_repo.list_trades(conn,account_id=a['id'])
    assert [(f['side'],f['quantity']) for f in fills]==[('buy',D(10)),('sell',D(5)),('sell',D(5))]
    assert fills[1]['detail']['exit_reason']=='target1_half'
    assert execution_repo.chain_done(conn,a['id'])=={('NVDA',date(2026,10,20))}
    assert execution_repo.list_trades(conn)==[]
    assert conn.execute('SELECT count(*) FROM plan_reviews').fetchone()[0]==0
    assert out['settled']['filled']==1 and out['settled']['exits']==2
    execution_paper_accounts.settle_account(conn,a,through=END,now=NOW,run_id='again')
    assert len(execution_repo.list_trades(conn,account_id=a['id']))==3


def test_missing_bar_state_survives_separate_runs_and_new_split(conn):
    from datetime import timedelta
    m=module(); a=paper(conn); p,o=seed_order(conn,a)
    price(conn,DAY)
    execution_paper_accounts.settle_account(conn,a,through=DAY,now=NOW,run_id='first')
    execution_paper_accounts.settle_account(conn,a,through=date(2026,10,22),now=NOW,run_id='missing')
    gap=execution_repo.latest_equity_day(conn,a['id'],on_or_before=date(2026,10,22))
    assert gap['detail']['paper_states']['NVDA']['state']['held_sessions']==1
    assert gap['detail']['bars_missing']==['NVDA'] and gap['detail']['stale']['NVDA']==DAY.isoformat()
    later=NOW+timedelta(days=1)
    price(conn,DAY,op=50,hi=51.5,lo=49.5,cl=50.5,now=later)
    price(conn,END,op=50,hi=51,lo=49,cl=50,split=2,now=later)
    execution_paper_accounts.settle_account(conn,a,through=END,now=later,run_id='split')
    eq=execution_repo.latest_equity_day(conn,a['id'],on_or_before=END)
    assert D(eq['detail']['positions']['NVDA']['quantity'])==D(20)
    assert D(eq['detail']['paper_states']['NVDA']['state']['stop'])==D(47)
    assert D(eq['detail']['paper_states']['NVDA']['split_factor'])==D(2)
    held=execution_paper_accounts.write_paper_holdings(conn,a,session=END,now=later,run_id='s')
    assert held[0]['stop']==D(47) and held[0]['account_id']==a['id']


def test_model_unavailable_still_settles_and_rerun_no_model_or_status(conn,monkeypatch):
    from investment_assistant.core.llm.model import AgentSettings
    m=module(); a=paper(conn,strategy='agent_managed'); seed_order(conn,a); price(conn,DAY)
    conn.commit()
    off=AgentSettings('off','',None,None,None)
    out=m.run_stage(DAY,now=NOW,deps=service.Deps(agent_settings=off),run_id='r',acc=None,settings=off)
    assert len(execution_repo.list_trades(conn,account_id=a['id']))==1
    assert execution_repo.decision_for(conn,a['id'],DAY)['status']=='skipped'
    assert len(execution_repo.holding_status_for(conn,account_id=a['id'],session=DAY))==1
    before=conn.execute('SELECT count(*) FROM account_equity_days').fetchone()[0]; conn.commit()
    monkeypatch.setattr(execution_paper_accounts,'decision_facts',lambda *a,**k:pytest.fail('model facts repeated'))
    m.run_stage(DAY,now=NOW,deps=service.Deps(agent_settings=off),run_id='again',acc=None,settings=off)
    assert conn.execute('SELECT count(*) FROM account_equity_days').fetchone()[0]==before


def _pending_chain_change(conn, *, change='revision', strategy='agent_managed', filled=False):
    from datetime import timedelta
    from test_workbench_ledger import agent3_version
    a = paper(conn, strategy=strategy)
    original = agent3_version(conn, 0, None, NOW-timedelta(days=3), day=date(2026, 10, 20),
        valid_from=DAY, valid_until=date(2026, 10, 28))
    order = execution_repo.insert_order(conn, {'id': uuid4(), 'account_id': a['id'], 'action': 'buy',
        'ticker': 'NVDA', 'plan_id': original['id'], 'chain_signal_date': original['signal_date'],
        'shares': 10, 'placed_on': date(2026, 10, 20), 'placed_by': 'agent', 'status': 'pending'})
    revised = None
    if change in ('revision', 'future_revision'):
        at = NOW + timedelta(minutes=1) if change == 'future_revision' else NOW-timedelta(minutes=1)
        revised = agent3_version(conn, 1, original['id'], at, day=original['signal_date'],
            valid_from=date(2026, 10, 26), valid_until=date(2026, 10, 28), stop=D(96))
    elif change == 'ended':
        record = execution_repo.insert_plan_review(conn, {'ticker': 'NVDA', 'signal_date': original['signal_date'],
            'head_plan_id': original['id'], 'session': END, 'trigger': 'batch', 'mode': 'entry',
            'timeline_version': 'timeline-2', 'outcome': 'invalidate', 'decided_by': 'host',
            'reason': '失效条件成立', 'attempt': 1, 'cutoff_at': NOW-timedelta(minutes=1)})
        conn.execute('UPDATE plan_reviews SET created_at=%s WHERE id=%s', (NOW-timedelta(minutes=1), record['id']))
    if filled:
        price(conn, END)
    else:
        price(conn, END, op=110, hi=112, lo=109, cl=110)
    return a, original, order, revised


@pytest.mark.parametrize('change, reason', [('revision', 'superseded_by_revision'), ('ended', 'chain_ended')])
@pytest.mark.parametrize('provider, expected_status', [('off', 'skipped'), ('invalid_schema', 'rejected'), ('hold', 'ok')])
def test_agent_host_cancels_obsolete_pending_buy_before_any_model_result(conn, scripted_model, change, reason, provider, expected_status):
    from contextlib import nullcontext
    from datetime import timedelta
    from langgraph.checkpoint.memory import InMemorySaver
    from langchain_core.messages import AIMessage
    from investment_assistant.core.llm.model import AgentSettings
    m = module()
    a, original, old, revised = _pending_chain_change(conn, change=change)
    settings = AgentSettings('off' if provider == 'off' else 'chatgpt', 'fake', None, None, None)
    fake = scripted_model([AIMessage(content='not JSON' if provider == 'invalid_schema' else '{"orders":[],"holds":[],"summary":"观察"}')])
    deps = service.Deps(agent_settings=settings, agent_model=fake, agent_checkpointer=lambda: nullcontext(InMemorySaver()))
    conn.commit()
    result = m.run_stage(END, now=NOW, deps=deps, run_id='host-reconcile', acc=None, settings=settings)
    updated = execution_repo.order_by_id(conn, old['id'])
    assert (updated['status'], updated['status_reason'], updated['settled_on']) == ('cancelled', reason, END)
    decision = execution_repo.decision_for(conn, a['id'], END)
    assert decision['status'] == expected_status
    assert decision['detail']['host_cancellations'] == [{'order_id': str(old['id']), 'reason': reason}]
    assert result['accounts'][0]['settled']['cancelled'] == 1
    assert execution_repo.pending_orders(conn, a['id']) == []
    assert execution_repo.chain_done(conn, a['id']) == set()
    assert original['id'] in execution_repo.tried_plan_ids(conn, a['id'])
    if revised:
        assert revised['id'] not in execution_repo.tried_plan_ids(conn, a['id'])
    before = conn.execute('SELECT (SELECT count(*) FROM paper_orders), (SELECT count(*) FROM paper_decisions), (SELECT count(*) FROM account_equity_days)').fetchone()
    conn.commit()
    again = m.run_stage(END, now=NOW+timedelta(minutes=2), deps=deps, run_id='rerun', acc=None, settings=settings)
    assert again['accounts'][0]['settled']['cancelled'] == 0
    assert conn.execute('SELECT (SELECT count(*) FROM paper_orders), (SELECT count(*) FROM paper_decisions), (SELECT count(*) FROM account_equity_days)').fetchone() == before
    assert len(fake.seen) == (0 if provider == 'off' else 1)
    price(conn, date(2026, 10, 26), now=NOW+timedelta(days=3))
    execution_paper_accounts.settle_account(conn, a, through=date(2026, 10, 26), now=NOW+timedelta(days=3), run_id='next-day')
    assert execution_repo.list_trades(conn, account_id=a['id']) == []


def test_future_revision_does_not_cancel_and_same_day_decision_still_reuses_original_cutoff(conn):
    from datetime import timedelta
    from investment_assistant.core.llm.model import AgentSettings
    m = module()
    a, _, old, _ = _pending_chain_change(conn, change='future_revision')
    settings = AgentSettings('off', '', None, None, None)
    conn.commit()
    for at in (NOW, NOW+timedelta(minutes=2)):
        result = m.run_stage(END, now=at, deps=service.Deps(), run_id='future-version', acc=None, settings=settings)
        assert result['accounts'][0]['settled']['cancelled'] == 0
        assert execution_repo.order_by_id(conn, old['id'])['status'] == 'pending'
        conn.commit()
    assert execution_repo.decision_for(conn, a['id'], END)['detail']['cutoff_at'] == NOW.isoformat()


@pytest.mark.parametrize('change', ['revision', 'ended'])
def test_host_reconciliation_preserves_today_fill_and_frozen_position(conn, change):
    from investment_assistant.core.llm.model import AgentSettings
    m = module()
    a, original, old, _ = _pending_chain_change(conn, change=change, filled=True)
    settings = AgentSettings('off', '', None, None, None)
    conn.commit()
    result = m.run_stage(END, now=NOW, deps=service.Deps(), run_id='filled-before-reconcile', acc=None, settings=settings)
    assert execution_repo.order_by_id(conn, old['id'])['status'] == 'filled'
    fills = execution_repo.list_trades(conn, account_id=a['id'])
    assert len(fills) == 1 and fills[0]['plan_id'] == original['id']
    status = execution_repo.holding_status_for(conn, account_id=a['id'], session=END)[0]
    assert status['plan_id'] == original['id'] and status['stop'] == original['stop']
    assert status['instruction'] == 'hold' and result['accounts'][0]['settled']['cancelled'] == 0


def test_follow_replaces_after_common_host_cancellation(conn):
    m = module()
    a, _, old, revised = _pending_chain_change(conn, strategy='follow_plans')
    conn.commit()
    result = m.run_stage(END, now=NOW, deps=service.Deps(), run_id='follow-reconcile', acc=None, settings=None)
    active = execution_repo.pending_orders(conn, a['id'])
    assert len(active) == 1 and active[0]['plan_id'] == revised['id']
    assert execution_repo.order_by_id(conn, old['id'])['status_reason'] == 'superseded_by_revision'
    assert result['accounts'][0]['settled']['cancelled'] == 1
    assert execution_repo.decision_for(conn, a['id'], END)['detail']['orders_rejected'] == 0


def test_direct_agent_decision_also_cancels_before_provider_gate(conn):
    from investment_assistant.core.llm.model import AgentSettings
    m = module()
    a, _, old, _ = _pending_chain_change(conn)
    execution_paper_accounts.settle_account(conn, a, through=END, now=NOW, run_id='settle')
    row = analysis_agent_runner.run_portfolio_decision(conn, a, session=END, now=NOW, run_id='direct', settings=AgentSettings('off', '', None, None, None),
        deps=service.Deps(), clock=lambda:0, started=0, recent_closed=m.recent_closed_facts)
    assert row['status'] == 'skipped'
    assert execution_repo.order_by_id(conn, old['id'])['status'] == 'cancelled'


@pytest.mark.parametrize('text,status', [('not JSON','rejected'),('{"orders":[],"holds":[],"summary":"观察"}','ok')])
def test_portfolio_runs_tokens_and_decision_replay(conn,scripted_model,text,status):
    from contextlib import nullcontext
    from langgraph.checkpoint.memory import InMemorySaver
    from langchain_core.messages import AIMessage
    from investment_assistant.core.llm.model import AgentSettings
    m=module(); a=paper(conn,strategy='agent_managed'); settings=AgentSettings('chatgpt','fake',None,1000,None)
    fake=scripted_model([AIMessage(content=text)]); deps=service.Deps(agent_model=fake,agent_checkpointer=lambda:nullcontext(InMemorySaver()))
    row=analysis_agent_runner.run_portfolio_decision(conn,a,session=END,now=NOW,run_id='r',settings=settings,deps=deps,clock=lambda:0,started=0,recent_closed=m.recent_closed_facts)
    assert row['status']==status
    run=data_repo.agent_run_by_id(conn,row['agent_run_id'])
    assert run['mode']=='portfolio' and run['ticker'] is None and run['signal_date'] is None and run['pool'] is None
    assert analysis_repo.agent_tokens_on(conn,END)==15
    assert analysis_agent_runner.run_portfolio_decision(conn,a,session=END,now=NOW,run_id='r2',settings=settings,deps=deps,clock=lambda:0,started=0,recent_closed=m.recent_closed_facts)['id']==row['id']
    assert len(fake.seen)==1 and analysis_repo.track_units(conn,DAY,END)==[]


def test_sizing_uses_ledger_when_opened(conn):
    m=module(); execution_repo.set_account_opening(conn,accounts.REAL_ACCOUNT_ID,opening_cash=12345,opened_on=DAY)
    execution_paper_accounts.write_real_equity(conn,session=END,now=NOW,run_id='r')
    assert hasattr(execution_plan_context,'sizing_equity')
    source=execution_plan_context.sizing_equity(conn,END,now=NOW)
    assert source['equity']==D(12345) and source['equity_source']=='ledger' and source['as_of']==END


def test_ledger_sized_rule_plan_derivation_reads_frozen_equity_row(conn):
    import investment_assistant.views.derivation as derivation_view
    from investment_assistant.execution import plan_levels as plans
    from investment_assistant.derivation import chain as derivation
    from investment_assistant.config import WorkbenchConfig
    from investment_assistant.data.calendar import add_trading_days
    m=module(); execution_repo.set_account_opening(conn,accounts.REAL_ACCOUNT_ID,opening_cash=100000,opened_on=DAY)
    execution_paper_accounts.write_real_equity(conn,session=END,now=NOW,run_id='r')
    source=execution_plan_context.sizing_equity(conn,END,now=NOW)
    for offset in range(-14,1): price(conn,add_trading_days(END,offset))
    rows=data_repo.read_bars(conn,'NVDA',END,cutoff_at=NOW,limit=15)
    plan=plans.build_rule_plan(ticker='NVDA',signal_date=END,bars=rows,earnings_row=None,snapshot=source,
        exposure={'ticker_value':D(0),'total_value':D(0)},params=WorkbenchConfig(),version='plan-2')
    execution_plan_context.mark_sizing_source(plan,source)
    stored=execution_repo.insert_plan(conn,plan)
    original=execution_repo.latest_equity_day(conn,accounts.REAL_ACCOUNT_ID,on_or_before=END)
    execution_repo.insert_equity_day(conn,{**original,'equity':D(200000),'run_id':'newer'})
    loaded=derivation_view.load_rows(conn,stored)
    assert loaded.snapshot is not None and loaded.snapshot['id']==source['id']
    assert loaded.snapshot['equity']==D(100000)
    assert derivation.derive_plan(stored,loaded)['consistency']['status']=='consistent'
    other=paper(conn)
    foreign=execution_paper_accounts._equity(conn,other,END,NOW,'paper')
    mixed={**stored,'inputs':{**stored['inputs'],'snapshot_id':foreign['id']}}
    missing=derivation_view.load_rows(conn,mixed)
    assert missing.snapshot is None and f"account_equity_days:{foreign['id']}" in missing.missing


def test_check_only_expects_decision_for_actual_daily_sessions(conn):
    m=module(); a=paper(conn)
    assert hasattr(m,'check_accounts')
    execution_paper_accounts.settle_account(conn,a,through=END,now=NOW,run_id='r')
    assert m.check_accounts(conn,session=DAY,now=NOW)==[]  # caught up offline, no historical decisions
    workflow_repo.insert_stage_run(conn,{'run_id':'daily-check','job':'daily','trigger':'manual_batch','session':END,'stage':'accounts',
        'stages_version':'stages-5','status':'success','reason':None,'input_count':1,'output_count':1,'skips':[],
        'failures':[],'degradations':[],'outputs':{},'detail':{},'started_at':NOW,'finished_at':NOW,'duration_ms':0})
    conn.execute('UPDATE accounts SET created_at=%s WHERE id=%s',(NOW,a['id']))
    assert any('decision' in e for e in m.check_accounts(conn,session=END,now=NOW))
    conn.execute('DELETE FROM account_equity_days WHERE account_id=%s AND session=%s',(a['id'],END))
    assert any('equity' in e for e in m.check_accounts(conn,session=END,now=NOW))


def test_follow_revision_releases_pending_slot_before_sizing(conn,monkeypatch):
    m=module(); a=paper(conn); p,old=seed_order(conn,a)
    execution_repo.update_account_params(conn,a['id'],{**a['params'],'max_positions':1})
    a=execution_repo.account_by_id(conn,a['id'])
    new=execution_repo.insert_plan(conn,{**p,'plan_version':'agent-3','kind':'agent','valid_from':date(2026,10,26),'valid_until':date(2026,10,28)})
    item={**new,'plan_id':new['id'],'chain_signal_date':p['signal_date'],'version_no':1}
    monkeypatch.setattr(m.paper_strategies,'buyable',lambda *a,**k:[item.copy()])
    row=execution_paper_accounts.decide_follow_plans(conn,a,recent_closed=paper_orchestration.recent_closed_facts,session=END,now=NOW,run_id='r')
    active=execution_repo.pending_orders(conn,a['id'])
    assert len(active)==1 and active[0]['plan_id']==new['id']
    assert execution_repo.order_by_id(conn,old['id'])['status']=='cancelled'
    assert row['detail']['orders_rejected']==0


def _budget_candidates(conn, monkeypatch, *, fee=0, total_cap='.8', tickers=('NVDA', 'AMD'), replace_pending=False, max_positions=8):
    m = module()
    a = paper(conn)
    conn.execute('UPDATE accounts SET opening_cash=1000 WHERE id=%s', (a['id'],))
    execution_repo.update_account_params(conn, a['id'], {**a['params'], 'risk_pct': '.05',
        'single_cap': '.6', 'total_cap': total_cap, 'fee_per_trade': fee, 'max_positions': max_positions})
    a = execution_repo.account_by_id(conn, a['id'])
    candidates, original = [], []
    for index, ticker in enumerate(tickers):
        p, order = seed_order(conn, a, ticker=ticker)
        original.append(order)
        if not replace_pending or index:
            execution_repo.settle_order(conn, order['id'], status='cancelled', status_reason='fixture', settled_on=DAY)
        new = execution_repo.insert_plan(conn, {**p, 'kind': 'agent', 'plan_version': 'agent-3',
            'entry_low': D(100), 'entry_high': D(100), 'ref_price': D(100), 'stop': D(90),
            'valid_from': date(2026, 10, 26), 'valid_until': date(2026, 10, 28)})
        candidates.append({**new, 'plan_id': new['id'], 'chain_signal_date': p['signal_date'], 'version_no': 1})
    monkeypatch.setattr(m.paper_strategies, 'buyable', lambda *a, **k: [dict(c) for c in candidates])
    return a, candidates, original


@pytest.mark.parametrize('fee,total_cap', [(0, '.8'), (60, '1')])
def test_follow_reserves_each_placement_before_sizing_next(conn, monkeypatch, fee, total_cap):
    m = module()
    a, _, _ = _budget_candidates(conn, monkeypatch, fee=fee, total_cap=total_cap)
    row = execution_paper_accounts.decide_follow_plans(conn, a,recent_closed=paper_orchestration.recent_closed_facts, session=END, now=NOW, run_id='sequential')
    assert {o['ticker']: o['shares'] for o in execution_repo.pending_orders(conn, a['id'])} == {'NVDA': D(5), 'AMD': D(3)}
    assert row['detail']['orders_placed'] == 2 and row['detail']['orders_rejected'] == 0
    assert row['detail']['skipped'] == []


def test_follow_cancel_releases_budget_before_sequential_replacement(conn, monkeypatch):
    m = module()
    a, _, old = _budget_candidates(conn, monkeypatch, replace_pending=True)
    row = execution_paper_accounts.decide_follow_plans(conn, a,recent_closed=paper_orchestration.recent_closed_facts, session=END, now=NOW, run_id='replace')
    assert execution_repo.order_by_id(conn, old[0]['id'])['status'] == 'cancelled'
    assert {o['ticker']: o['shares'] for o in execution_repo.pending_orders(conn, a['id'])} == {'NVDA': D(5), 'AMD': D(3)}
    assert row['detail']['orders_rejected'] == 0


@pytest.mark.parametrize('slots,reason', [(8, 'no_room'), (2, 'max_positions')])
def test_follow_exhausted_capacity_skips_without_rejected_order(conn, monkeypatch, slots, reason):
    m = module()
    a, candidates, _ = _budget_candidates(conn, monkeypatch, tickers=('NVDA', 'AMD', 'MSFT'), max_positions=slots)
    row = execution_paper_accounts.decide_follow_plans(conn, a,recent_closed=paper_orchestration.recent_closed_facts, session=END, now=NOW, run_id='no-room')
    assert {o['ticker']: o['shares'] for o in execution_repo.pending_orders(conn, a['id'])} == {'NVDA': D(5), 'AMD': D(3)}
    assert row['detail']['orders_rejected'] == 0
    assert row['detail']['skipped'] == [{'plan_id': str(candidates[2]['plan_id']), 'reason': reason}]


def test_agent_excess_order_remains_rejected_without_automatic_resize(conn, monkeypatch):
    m = module()
    a, candidates, _ = _budget_candidates(conn, monkeypatch)
    facts = execution_paper_accounts.decision_facts(conn, a,recent_closed=paper_orchestration.recent_closed_facts, session=END, now=NOW)
    output = {'orders': [{'action': 'buy', 'plan_id': str(c['plan_id']), 'shares': 5, 'reason': 'Agent intent'} for c in candidates], 'holds': [], 'summary': 'buy both'}
    row = execution_paper_accounts._save_decision(conn, a, session=END, now=NOW, run_id='agent', output=output, facts=facts)
    assert row['detail']['orders_placed'] == 1 and row['detail']['orders_rejected'] == 1
    rejected = execution_repo.orders_for(conn, a['id'], status='rejected')
    assert rejected[0]['shares'] == 5 and rejected[0]['status_reason'] == 'exceeds_max_shares'


def test_follow_no_room_does_not_occupy_ticker_for_a_later_affordable_chain(conn, monkeypatch):
    m = module()
    a, candidates, _ = _budget_candidates(conn, monkeypatch, total_cap='.55')
    later = execution_repo.insert_plan(conn, {**candidates[1], 'signal_date': date(2026, 10, 19),
        'entry_low': D(25), 'entry_high': D(25), 'ref_price': D(25), 'stop': D(20)})
    candidates.append({**later, 'plan_id': later['id'], 'chain_signal_date': later['signal_date'], 'version_no': 0})
    row = execution_paper_accounts.decide_follow_plans(conn, a,recent_closed=paper_orchestration.recent_closed_facts, session=END, now=NOW, run_id='affordable-chain')
    active = execution_repo.pending_orders(conn, a['id'])
    assert {o['ticker']: o['shares'] for o in active} == {'NVDA': D(5), 'AMD': D(2)}
    assert next(o for o in active if o['ticker'] == 'AMD')['plan_id'] == later['id']
    assert row['detail']['skipped'] == [{'plan_id': str(candidates[1]['plan_id']), 'reason': 'no_room'}]
    assert row['detail']['orders_rejected'] == 0


def test_future_known_split_excluded_from_historical_cutoff(conn):
    from datetime import timedelta
    m=module(); a=paper(conn); seed_order(conn,a)
    price(conn,DAY)
    price(conn,date(2026,10,26),op=50,hi=51,lo=49,cl=50,split=2,now=NOW+timedelta(days=4))
    execution_paper_accounts.settle_account(conn,a,through=DAY,now=NOW,run_id='before')
    eq=execution_repo.latest_equity_day(conn,a['id'],on_or_before=DAY)
    assert eq['positions_count']==1 and D(eq['detail']['positions']['NVDA']['quantity'])==D(10)
    assert D(eq['detail']['paper_states']['NVDA']['split_factor'])==D(1)


def test_agent_buy_before_cancel_order_is_persisted_without_pending_conflict(conn,monkeypatch):
    m=module(); a=paper(conn); p,old=seed_order(conn,a)
    new=execution_repo.insert_plan(conn,{**p,'plan_version':'agent-3','kind':'agent','valid_from':date(2026,10,26),'valid_until':date(2026,10,28)})
    monkeypatch.setattr(m.paper_strategies,'buyable',lambda *a,**k:[{**new,'plan_id':new['id'],'chain_signal_date':p['signal_date'],'version_no':1}])
    facts=execution_paper_accounts.decision_facts(conn,a,recent_closed=paper_orchestration.recent_closed_facts,session=END,now=NOW)
    output={'orders':[{'action':'buy','plan_id':str(new['id']),'shares':1,'reason':'新版'},
                      {'action':'cancel','order_id':str(old['id']),'reason':'换版'}],'holds':[],'summary':'换版'}
    row=execution_paper_accounts._save_decision(conn,a,session=END,now=NOW,run_id='r',output=output,facts=facts)
    assert len(execution_repo.pending_orders(conn,a['id']))==1
    assert row['detail']['orders_rejected']==0


def test_closed_and_before_opened_accounts_are_not_processed(conn):
    m=module(); a=paper(conn)
    execution_repo.close_account(conn,a['id'],'end',closed_at=NOW)
    other=paper(conn,'future'); conn.execute('UPDATE accounts SET opened_on=%s WHERE id=%s',(date(2026,10,26),other['id'])); conn.commit()
    out=m.run_stage(END,now=NOW,deps=service.Deps(),run_id='r',acc=None,settings=None)
    assert out['accounts']==[]
    assert conn.execute('SELECT count(*) FROM account_equity_days').fetchone()[0]==0
    assert conn.execute('SELECT count(*) FROM paper_decisions').fetchone()[0]==0


def test_quota_failure_skips_next_agent_account(conn,monkeypatch):
    from investment_assistant.core.llm.model import AgentSettings
    m=module(); a=paper(conn,'one','agent_managed'); b=paper(conn,'two','agent_managed'); conn.commit()
    called=[]
    def decide(conn,account,**kw):
        called.append(account['name'])
        return execution_repo.insert_decision(conn,{'account_id':account['id'],'session':END,'status':'skipped','status_reason':'quota_exhausted'})[0]
    monkeypatch.setattr(analysis_agent_runner,'run_portfolio_decision',decide)
    m.run_stage(END,now=NOW,deps=service.Deps(),run_id='r',acc=None,settings=AgentSettings('chatgpt','fake',None,None,None))
    assert called==['one']
    assert execution_repo.decision_for(conn,b['id'],END)['status_reason']=='quota_exhausted'


def test_params_only_effective_next_trading_day(conn):
    m=module(); a=paper(conn); conn.commit()
    execution_paper_accounts.change_params(a['id'],{'request_id':str(uuid4()),'params':{'fee_per_trade':'9'},'reason':'费率'},now=NOW)
    latest=execution_repo.account_by_id(conn,a['id'])
    assert execution_paper_accounts.effective_params(conn,latest,END)['fee_per_trade']==0
    assert execution_paper_accounts.effective_params(conn,latest,date(2026,10,26))['fee_per_trade']==9


def test_portfolio_evidence_has_account_capacity_frozen_holding_and_pending_plan(conn):
    m=module(); a=paper(conn); p,o=seed_order(conn,a); price(conn,DAY)
    execution_paper_accounts.settle_account(conn,a,through=DAY,now=NOW,run_id='r')
    execution_paper_accounts.write_paper_holdings(conn,a,session=DAY,now=NOW,run_id='r')
    facts=execution_paper_accounts.decision_facts(conn,a,recent_closed=paper_orchestration.recent_closed_facts,session=DAY,now=NOW)
    assert set(('inception_return','max_drawdown','positions_count','available_positions'))<=facts['account'].keys()
    held=facts['positions'][0]
    assert set(('stop','instruction','r_now','frozen_plan','current_chain'))<=held.keys()
    assert held['frozen_plan']['id']==p['id']


def test_real_fractional_valuation_rerun_does_not_append_rounding_only_change(conn):
    m=module()
    execution_repo.set_account_opening(conn,accounts.REAL_ACCOUNT_ID,opening_cash=1000,opened_on=DAY)
    execution_repo.insert_trade(conn,{'request_id':uuid4(),'ticker':'NVDA','side':'buy','quantity':D('.123456'),
        'price':D('101.123456'),'fee':0,'executed_at':NOW.replace(day=21),'note':None,'plan_id':None,
        'own_plan':None,'detail':{},'recorded_by':'user'},'fractional',recorded_at=NOW)
    price(conn,DAY,cl=102)
    execution_paper_accounts.write_real_equity(conn,session=END,now=NOW,run_id='first')
    count=conn.execute('SELECT count(*) FROM account_equity_days').fetchone()[0]
    execution_paper_accounts.write_real_equity(conn,session=END,now=NOW,run_id='rerun')
    assert conn.execute('SELECT count(*) FROM account_equity_days').fetchone()[0]==count


def test_real_backfill_recomputes_five_sessions_and_excludes_later_known_flow(conn):
    from datetime import timedelta
    from investment_assistant.data.calendar import add_trading_days
    m=module()
    execution_repo.set_account_opening(conn,accounts.REAL_ACCOUNT_ID,opening_cash=10000,opened_on=date(2026,10,1))
    execution_paper_accounts.write_real_equity(conn,session=END,now=NOW,run_id='initial')
    count=conn.execute('SELECT count(*) FROM account_equity_days').fetchone()[0]
    before=execution_repo.latest_equity_day(conn,accounts.REAL_ACCOUNT_ID,on_or_before=add_trading_days(END,-5))
    execution_repo.insert_flow(conn,{'request_id':uuid4(),'flow_date':add_trading_days(END,-4),'amount':D(500),'note':None},'known',recorded_at=NOW)
    execution_repo.insert_flow(conn,{'request_id':uuid4(),'flow_date':DAY,'amount':D(999),'note':None},'future',recorded_at=NOW+timedelta(days=1))
    latest=execution_paper_accounts.write_real_equity(conn,session=END,now=NOW,run_id='backfill')
    assert latest['cash']==D(10500)
    assert execution_repo.latest_equity_day(conn,accounts.REAL_ACCOUNT_ID,on_or_before=add_trading_days(END,-5))['id']==before['id']
    assert conn.execute('SELECT count(*) FROM account_equity_days').fetchone()[0]==count+5


def test_paper_holding_ignores_split_unknown_at_cutoff(conn):
    from datetime import timedelta
    import investment_assistant.workflow.nodes.review as plan_review
    m=module(); a=paper(conn); p,_=seed_order(conn,a); price(conn,DAY)
    price(conn,date(2026,10,26),op=50,hi=51,lo=49,cl=50,split=2,now=NOW+timedelta(days=4))
    execution_paper_accounts.settle_account(conn,a,through=DAY,now=NOW,run_id='r')
    row=execution_chains._holding_row(conn,'NVDA',DAY,'r',now=NOW,skipped={},unavailable=None,account_id=a['id'])
    assert row['target1']==p['target1'] and row['r_now']==D('.17')
    assert row['to_stop_pct']==D('6.93') and row['instruction']=='hold'


def test_holding_stop_distance_matches_persisted_stop_after_t1(conn):
    import investment_assistant.workflow.nodes.review as plan_review
    from investment_assistant.execution import timeline
    m=module(); a=paper(conn); seed_order(conn,a)
    price(conn,DAY)
    price(conn,date(2026,10,22),op=110,hi=114,lo=109,cl=113)
    execution_paper_accounts.settle_account(conn,a,through=date(2026,10,22),now=NOW,run_id='r')
    row=execution_chains._holding_row(conn,'NVDA',date(2026,10,22),'r',now=NOW,skipped={},unavailable=None,account_id=a['id'])
    assert row['t1_reached'] and row['quantity']==5
    assert row['to_stop_pct']==timeline._pct(row['last_close']-row['stop'],row['last_close'])
    assert row['to_t1_pct'] is None and row['instruction']=='hold'


@pytest.mark.parametrize('last_low, remains', [('100.2', True), ('100', False)])
def test_paper_holding_instruction_uses_settled_stop_after_fee_and_t1(conn, last_low, remains):
    import investment_assistant.workflow.nodes.review as plan_review
    m = module()
    a = paper(conn)
    execution_repo.update_account_params(conn, a['id'], {**a['params'], 'fee_per_trade': 10})
    seed_order(conn, a)
    price(conn, DAY)
    price(conn, date(2026, 10, 22), op=101, hi=112, lo=99, cl=100.5)
    execution_paper_accounts.settle_account(conn, a, through=date(2026, 10, 22), now=NOW, run_id='t1')
    execution_paper_accounts.write_paper_holdings(conn, a, session=date(2026, 10, 22), now=NOW, run_id='t1')
    price(conn, END, op=100.5, hi=100.8, lo=D(last_low), cl=100.5)
    execution_paper_accounts.settle_account(conn, a, through=END, now=NOW, run_id='retrace')
    row = execution_chains._holding_row(conn, 'NVDA', END, 'r', now=NOW, skipped={}, unavailable=None, account_id=a['id'])
    if remains:
        assert row['quantity'] == 5 and row['avg_cost'] == D(101)
        assert row['stop'] == 100 and row['t1_reached']
        assert row['instruction'] == 'hold' and row['exit_reason'] is None
        assert row['remaining_sessions'] is None and row['held_sessions'] == 3
        assert '止损触及' not in row['reason'] and 'exit_since' not in row['detail']
        facts = execution_paper_accounts.decision_facts(conn, a,recent_closed=paper_orchestration.recent_closed_facts, session=END, now=NOW)
        assert facts['positions'][0]['instruction'] == 'hold'
    else:
        assert row is None
        trades = execution_repo.list_trades(conn, account_id=a['id'])
        assert trades[-1]['quantity'] == 5 and trades[-1]['price'] == 100
        assert trades[-1]['detail']['exit_reason'] == 'stop'
        assert execution_paper_accounts.decision_facts(conn, a,recent_closed=paper_orchestration.recent_closed_facts, session=END, now=NOW)['positions'] == []


def test_paper_holding_deadline_counts_observed_settlements_and_preserves_due_exit(conn):
    import investment_assistant.workflow.nodes.review as plan_review
    m = module()
    a = paper(conn)
    seed_order(conn, a, max_hold=3)
    price(conn, DAY)
    # A missing session neither increments the host state nor triggers a time exit.
    execution_paper_accounts.settle_account(conn, a, through=date(2026, 10, 22), now=NOW, run_id='gap')
    row = execution_chains._holding_row(conn, 'NVDA', date(2026, 10, 22), 'r', now=NOW, skipped={}, unavailable=None, account_id=a['id'])
    assert row['held_sessions'] == 1 and row['remaining_sessions'] == 2
    assert row['instruction'] == 'hold' and '缺 2026-10-22 的日线' in row['reason']
    price(conn, END)
    execution_paper_accounts.settle_account(conn, a, through=END, now=NOW, run_id='second-observed')
    row = execution_chains._holding_row(conn, 'NVDA', END, 'r', now=NOW, skipped={}, unavailable=None, account_id=a['id'])
    assert row['held_sessions'] == 2 and row['remaining_sessions'] == 1
    assert row['instruction'] == 'hold'
    due = date(2026, 10, 26)
    later = NOW.replace(day=26)
    price(conn, due, now=later)
    execution_paper_accounts.settle_account(conn, a, through=due, now=later, run_id='due')
    row = execution_chains._holding_row(conn, 'NVDA', due, 'r', now=later, skipped={}, unavailable=None, account_id=a['id'])
    assert row['held_sessions'] == 3 and row['remaining_sessions'] == 0
    assert row['instruction'] == 'exit' and row['exit_reason'] == 'time_stop'
    assert '时间止损' in row['reason']


def test_negative_paper_cash_records_failure_and_cannot_open_more(conn):
    from types import SimpleNamespace
    m=module(); a=paper(conn); seed_order(conn,a); price(conn,DAY)
    conn.execute('UPDATE accounts SET opening_cash=10 WHERE id=%s',(a['id'],)); conn.commit()
    failures=[]
    acc=SimpleNamespace(detail={},input=lambda n:None,output=lambda n:None,fail=lambda target,reason:failures.append(reason))
    out=m.run_stage(DAY,now=NOW,deps=service.Deps(),run_id='r',acc=acc,settings=None)
    assert failures==['negative_cash']
    assert out['accounts'][0]['equity']['cash']<0
    assert execution_repo.pending_orders(conn,a['id'])==[]


def test_rejected_extreme_shares_preserves_other_accepted_order(conn,monkeypatch):
    m=module(); a=paper(conn); p,old=seed_order(conn,a)
    execution_repo.settle_order(conn,old['id'],status='cancelled',status_reason='fixture',settled_on=DAY)
    p=execution_repo.insert_plan(conn,{**p,'kind':'agent','plan_version':'agent-3'})
    item={**p,'plan_id':p['id'],'chain_signal_date':p['signal_date'],'max_shares':10}
    facts=execution_paper_accounts.decision_facts(conn,a,recent_closed=paper_orchestration.recent_closed_facts,session=END,now=NOW); facts['buyable']=[item]
    output={'orders':[{'action':'buy','plan_id':str(uuid4()),'shares':'1e100','reason':'invalid'},
                      {'action':'buy','plan_id':str(p['id']),'shares':1,'reason':'valid'}],
            'holds':[],'summary':'mixed'}
    row=execution_paper_accounts._save_decision(conn,a,session=END,now=NOW,run_id='r',output=output,facts=facts)
    assert row['detail']['orders_placed']==1 and row['detail']['orders_rejected']==1
    assert row['output']==output and len(execution_repo.pending_orders(conn,a['id']))==1


def test_follow_hydrates_entry_version_beneath_real_holding_head(conn):
    from test_workbench_ledger import agent3_version
    from datetime import timedelta
    m=module(); a=paper(conn)
    at=NOW-timedelta(days=4)
    entry=agent3_version(conn,0,None,at,day=date(2026,10,20),valid_until=date(2026,10,28))
    head=agent3_version(conn,1,entry['id'],at+timedelta(days=1),day=entry['signal_date'],stop=D(96),
                        inputs={'chain_mode':'holding'},valid_until=date(2026,10,28))
    execution_repo.insert_trade(conn,{'request_id':uuid4(),'ticker':'NVDA','side':'buy','quantity':10,'price':100,
        'fee':0,'executed_at':at+timedelta(days=1),'note':None,'plan_id':entry['id'],'own_plan':None,
        'detail':{},'recorded_by':'user'},'real',recorded_at=at+timedelta(days=1))
    facts=execution_paper_accounts.decision_facts(conn,a,recent_closed=paper_orchestration.recent_closed_facts,session=END,now=NOW)
    assert len(facts['buyable'])==1
    item=facts['buyable'][0]
    assert item['plan_id']==entry['id'] and item['current_real_state']=='holding'
    assert item['stop']==entry['stop'] and item['stop']!=head['stop']
    row=execution_paper_accounts.decide_follow_plans(conn,a,recent_closed=paper_orchestration.recent_closed_facts,session=END,now=NOW,run_id='r')
    assert row['detail']['orders_placed']==1


def test_paper_holding_never_uses_global_chain_revision(conn):
    from test_workbench_ledger import agent3_version
    import investment_assistant.workflow.nodes.review as plan_review
    from datetime import timedelta
    m=module(); a=paper(conn); at=NOW-timedelta(days=4)
    entry=agent3_version(conn,0,None,at,day=date(2026,10,20),valid_from=DAY)
    agent3_version(conn,1,entry['id'],at+timedelta(days=1),day=entry['signal_date'],stop=D(96),inputs={'chain_mode':'holding'})
    execution_repo.insert_order(conn,{'id':uuid4(),'account_id':a['id'],'action':'buy','ticker':'NVDA','plan_id':entry['id'],
        'chain_signal_date':entry['signal_date'],'shares':10,'placed_on':date(2026,10,20),'placed_by':'host','status':'pending'})
    price(conn,DAY)
    execution_paper_accounts.settle_account(conn,a,through=DAY,now=NOW,run_id='r')
    row=execution_chains._holding_row(conn,'NVDA',DAY,'r',now=NOW,skipped={},unavailable=None,account_id=a['id'])
    assert row['plan_id']==entry['id'] and row['stop']==entry['stop'] and row['chain_signal_date'] is None


def test_portfolio_tool_audit_budget_and_persistence_failure_keep_tokens(conn,scripted_model,monkeypatch):
    from contextlib import nullcontext
    from langgraph.checkpoint.memory import InMemorySaver
    from langchain_core.messages import AIMessage
    from investment_assistant.core.llm.model import AgentSettings
    m=module(); a=paper(conn,strategy='agent_managed'); settings=AgentSettings('chatgpt','fake',None,1000,None)
    fake=scripted_model([AIMessage(content='',tool_calls=[{'name':'get_concept','args':{},'id':'c1'}]),
        AIMessage(content='{"orders":[{"action":"buy","plan_id":"11111111-1111-1111-1111-111111111111","shares":1,"reason":"x"}],"holds":[],"summary":"x"}')])
    deps=service.Deps(agent_model=fake,agent_checkpointer=lambda:nullcontext(InMemorySaver()))
    monkeypatch.setattr(execution_repo,'insert_order',lambda *a,**k:(_ for _ in ()).throw(ValueError('bad persistence')))
    row=analysis_agent_runner.run_portfolio_decision(conn,a,session=END,now=NOW,run_id='r',settings=settings,deps=deps,clock=lambda:599,started=0,recent_closed=m.recent_closed_facts)
    assert row['status_reason']=='host_error' and analysis_repo.agent_tokens_on(conn,END)==30
    calls=conn.execute('SELECT phase FROM agent_tool_calls WHERE run_id=%s',(row['agent_run_id'],)).fetchall()
    assert calls==[('portfolio',)]
    b=paper(conn,'budget','agent_managed')
    skipped=analysis_agent_runner.run_portfolio_decision(conn,b,session=END,now=NOW,run_id='r',settings=settings,deps=deps,clock=lambda:600,started=0,recent_closed=m.recent_closed_facts)
    assert skipped['status_reason']=='stage_budget' and len(fake.seen)==2


def test_accounts_node_captures_later_cutoff_once_and_excludes_truly_future_plan(conn):
    from datetime import timedelta
    from types import SimpleNamespace
    from test_workbench_ledger import agent3_version
    a=paper(conn); at=NOW+timedelta(minutes=5)
    entry=agent3_version(conn,0,None,NOW+timedelta(minutes=2),day=END,
                        valid_from=date(2026,10,26),valid_until=date(2026,10,28),entry_type='next_open')
    agent3_version(conn,0,None,at+timedelta(minutes=1),day=END,ticker='AMD',
                   valid_from=date(2026,10,26),valid_until=date(2026,10,28))
    conn.commit()
    reads=[]
    def wall(): reads.append(at); return at
    ctx=SimpleNamespace(deps=service.Deps(clock=lambda:0),wall_now=wall,recorder=None,breadth_tickers=None)
    acc=SimpleNamespace(detail={},input=lambda n:None,output=lambda n:None,fail=lambda *a:pytest.fail(str(a)))
    state={'cutoff_at':NOW.isoformat(),'session':END.isoformat(),'run_id':'daily-current','summary':{}}
    paper_orchestration.run(state,ctx,acc)
    assert reads==[at] and acc.detail['cutoff_at']==at.isoformat()
    orders=execution_repo.pending_orders(conn,a['id'])
    assert len(orders)==1 and orders[0]['plan_id']==entry['id']
    decision=execution_repo.decision_for(conn,a['id'],END)
    assert decision['detail']['cutoff_at']==at.isoformat()
    assert decision['evidence']['account']['cutoff_at']==at.isoformat()
    ctx.wall_now=lambda:at+timedelta(minutes=2)
    paper_orchestration.run(state,ctx,acc)
    assert execution_repo.decision_for(conn,a['id'],END)['detail']['cutoff_at']==at.isoformat()


def test_cli_show_selected_session_displays_positions_pending_and_that_decision_read_only(conn, monkeypatch, capsys):
    import investment_assistant.views.account as account_views
    m = account_views
    a = paper(conn, '指定账户')
    paper(conn, '其他账户不可混入')
    seed_order(conn, a)
    seed_order(conn, a, ticker='AMD')
    price(conn, DAY)
    price(conn, END, op=93, hi=94, lo=92, cl=93)  # NVDA is closed now, held on DAY.
    price(conn, END, ticker='AMD')                # AMD is filled now, pending on DAY.
    execution_paper_accounts.settle_account(conn, a, through=END, now=NOW, run_id='history')
    execution_repo.insert_decision(conn, {'account_id': a['id'], 'session': DAY, 'status': 'ok',
        'summary': '指定日的机械决策', 'output': {'orders': [], 'holds': [], 'summary': '指定日的机械决策'}})
    execution_repo.insert_decision(conn, {'account_id': a['id'], 'session': END, 'status': 'skipped',
        'status_reason': 'model_unavailable', 'summary': '未来决策不可混入'})
    before = conn.execute('SELECT (SELECT count(*) FROM trades), (SELECT count(*) FROM paper_orders), (SELECT count(*) FROM paper_decisions), (SELECT count(*) FROM account_equity_days)').fetchone()
    conn.commit()
    reads = []
    original = execution_paper_accounts.account_books
    def read_book(db, *args, **kwargs):
        reads.append(db.execute('SHOW transaction_read_only').fetchone()[0])
        return original(db, *args, **kwargs)
    monkeypatch.setattr(execution_paper_accounts, 'account_books', read_book)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return NOW
    monkeypatch.setattr(m, 'datetime', Clock)
    assert m.main(['show', '--account', a['name'], '--session', DAY.isoformat()]) == 0
    output = capsys.readouterr().out
    assert '持仓' in output and 'NVDA\t10.000000\t100.000000' in output
    assert '待成交' in output and 'AMD\t买入\t10.000000' in output
    assert '2026-10-21\t成功\t指定日的机械决策' in output
    assert '其他账户不可混入' not in output and '未来决策不可混入' not in output
    assert reads and set(reads) == {'on'}
    assert conn.execute('SELECT (SELECT count(*) FROM trades), (SELECT count(*) FROM paper_orders), (SELECT count(*) FROM paper_decisions), (SELECT count(*) FROM account_equity_days)').fetchone() == before


def test_cli_show_empty_details_and_unavailable_decision_are_explicit(monkeypatch, capsys):
    import investment_assistant.views.account as account_views
    m = account_views
    row = {'id': 'fixture', 'name': '空账户', 'strategy_text': '照计划执行', 'status': 'active', 'latest': None, 'settled_through': None}
    monkeypatch.setattr(account_views, 'overview', lambda **kw: {'accounts': [row]})
    calls = []
    def session_detail(account_id, **kw):
        calls.append((account_id, kw['session']))
        return {'positions': [], 'pending_orders': [], 'decision': None}
    monkeypatch.setattr(account_views, 'session_detail', session_detail, raising=False)
    assert m.main(['show', '--account', 'fixture', '--session', DAY.isoformat()]) == 0
    output = capsys.readouterr().out
    assert calls == [('fixture', DAY)]
    assert '无持仓' in output and '无待成交订单' in output and '当日无决策记录' in output


# ---- v2.11 review fixes: I-2 rejected orders keep the version, I-3 reason enums, M-8 close, resume, isolation

def _facts_for(m, conn, a, session):
    return execution_paper_accounts.decision_facts(conn, a,recent_closed=paper_orchestration.recent_closed_facts, session=session, now=NOW)


@pytest.mark.parametrize('cause', ['exceeds_max_shares', 'insufficient_cash', 'max_positions'])
def test_rejected_order_does_not_consume_plan_version_next_day_can_retry(conn, monkeypatch, cause):
    m = module()
    a, candidates, _ = _budget_candidates(conn, monkeypatch, tickers=('NVDA',))
    plan_id = candidates[0]['plan_id']
    facts = _facts_for(m, conn, a, END)
    shares = 6 if cause == 'exceeds_max_shares' else 5
    if cause == 'insufficient_cash':
        facts['account']['cash'] = D(10)
    if cause == 'max_positions':
        facts['account']['params'] = {**facts['account']['params'], 'max_positions': 0}
    output = {'orders': [{'action': 'buy', 'plan_id': str(plan_id), 'shares': shares, 'reason': '首次'}], 'holds': [], 'summary': 'x'}
    execution_paper_accounts._save_decision(conn, a, session=END, now=NOW, run_id='r1', output=output, facts=facts)
    assert [o['status_reason'] for o in execution_repo.orders_for(conn, a['id'], status='rejected')] == [cause]
    assert plan_id not in execution_repo.tried_plan_ids(conn, a['id'])
    nxt = date(2026, 10, 26)
    facts2 = _facts_for(m, conn, a, nxt)
    assert facts2['buyable'][0]['blocked_reason'] is None
    output['orders'][0].update(shares=5, reason='改正后重下')
    row = execution_paper_accounts._save_decision(conn, a, session=nxt, now=NOW, run_id='r2', output=output, facts=facts2)
    assert row['detail']['orders_placed'] == 1 and row['detail']['orders_rejected'] == 0
    assert [o['plan_id'] for o in execution_repo.pending_orders(conn, a['id'])] == [plan_id]
    assert plan_id in execution_repo.tried_plan_ids(conn, a['id'])


@pytest.mark.parametrize('status,reason,consumes', [('voided', 'gap_below_stop', True), ('filled', None, True),
                                                    ('expired', 'validity_expired', True), ('cancelled', 'chain_ended', True),
                                                    ('rejected', 'chain_consumed', False)])
def test_tried_plan_ids_only_skips_rejected(conn, status, reason, consumes):
    a = paper(conn)
    p, order = seed_order(conn, a)
    execution_repo.settle_order(conn, order['id'], status=status, status_reason=reason, settled_on=DAY)
    assert (p['id'] in execution_repo.tried_plan_ids(conn, a['id'])) is consumes


def test_agent_cancel_and_expiry_persist_enum_reasons_with_chinese_text(conn, scripted_model):
    from contextlib import nullcontext
    from langgraph.checkpoint.memory import InMemorySaver
    from langchain_core.messages import AIMessage
    import investment_assistant.views.account as account_views
    from investment_assistant.core.llm.model import AgentSettings
    m = module()
    a, _, old, _ = _pending_chain_change(conn, change='none')
    settings = AgentSettings('chatgpt', 'fake', None, None, None)
    text = '{"orders":[{"action":"cancel","order_id":"%s","reason":"我改主意了"}],"holds":[],"summary":"撤单"}' % old['id']
    fake = scripted_model([AIMessage(content=text)])
    deps = service.Deps(agent_settings=settings, agent_model=fake, agent_checkpointer=lambda: nullcontext(InMemorySaver()))
    conn.commit()
    m.run_stage(END, now=NOW, deps=deps, run_id='agent-cancel', acc=None, settings=settings)
    updated = execution_repo.order_by_id(conn, old['id'])
    assert (updated['status'], updated['status_reason']) == ('cancelled', 'agent_cancel')
    cancel_row = next(o for o in execution_repo.orders_for(conn, a['id']) if o['action'] == 'cancel')
    assert cancel_row['reason'] == '我改主意了'
    assert account_views._order(conn, updated)['result_text'] == '已撤单：Agent 主动撤单'


def test_expired_order_is_stored_with_validity_expired(conn):
    m = module()
    a = paper(conn)
    _, order = seed_order(conn, a)
    for day in (DAY, date(2026, 10, 22), END):
        price(conn, day, op=110, hi=112, lo=109, cl=110)
    price(conn, date(2026, 10, 26), op=110, hi=112, lo=109, cl=110)
    execution_paper_accounts.settle_account(conn, a, through=date(2026, 10, 26), now=NOW, run_id='expire')
    import investment_assistant.views.account as account_views
    done = execution_repo.order_by_id(conn, order['id'])
    assert (done['status'], done['status_reason']) == ('expired', 'validity_expired')
    assert account_views._order(conn, done)['result_text'] == '已过期：计划有效期已过'


def test_close_cancels_pending_orders_with_account_closed(conn):
    import investment_assistant.views.account as account_views
    m = module()
    a = paper(conn)
    _, order = seed_order(conn, a)
    conn.commit()
    execution_paper_accounts.close(a['id'], {'reason': '结束模拟'}, now=NOW)
    done = execution_repo.order_by_id(conn, order['id'])
    assert (done['status'], done['status_reason'], done['settled_on']) == ('cancelled', 'account_closed', date(2026, 10, 23))
    assert execution_repo.pending_orders(conn, a['id']) == []
    assert account_views._order(conn, done)['result_text'] == '已撤单：账户已关闭，订单撤销'


def test_paper_fill_does_not_change_chain_state(conn):
    import investment_assistant.workflow.nodes.review as plan_review
    m = module()
    a, original, order, _ = _pending_chain_change(conn, change='none', filled=True)

    def real_state():
        items = execution_chains.ticker_chain_items(conn, 'NVDA', session=END, now=NOW, point_in_time=True)
        return [(i['chain']['signal_date'], i['state']['state'] if isinstance(i['state'], dict) else i['state']) for i in items]
    before = real_state()
    assert before and all(state == 'entry' for _, state in before)
    out = execution_paper_accounts.settle_account(conn, a, through=END, now=NOW, run_id='paper-fill')
    assert out['settled']['filled'] == 1
    paper_trades = execution_repo.list_trades(conn, account_id=a['id'])
    assert [(t['side'], t['recorded_by']) for t in paper_trades] == [('buy', 'host')]
    assert real_state() == before
    assert execution_repo.list_trades(conn) == []
    assert execution_repo.list_trades(conn, account_id=accounts.REAL_ACCOUNT_ID) == []
    assert execution_repo.chain_done(conn, accounts.REAL_ACCOUNT_ID) == set()
    assert conn.execute('SELECT count(*) FROM plan_reviews').fetchone()[0] == 0


def test_resume_from_accounts_reuses_decision(conn, scripted_model, monkeypatch):
    from contextlib import nullcontext
    from datetime import timedelta
    from langgraph.checkpoint.memory import InMemorySaver
    from langchain_core.messages import AIMessage
    from investment_assistant.core.llm.model import AgentSettings
    m = module()
    a, original, old, _ = _pending_chain_change(conn, change='none')
    settings = AgentSettings('chatgpt', 'fake', None, 1000, None)
    fake = scripted_model([AIMessage(content='{"orders":[],"holds":[],"summary":"观察"}')])
    deps = service.Deps(agent_settings=settings, agent_model=fake, agent_checkpointer=lambda: nullcontext(InMemorySaver()))

    class Boom:
        """The stage fails after every account phase committed (as a failing accounts node would)."""
        detail = {}
        def input(self, n): raise RuntimeError('node failed after decision')
        def output(self, n): pass
        def fail(self, *a): pass
    conn.commit()
    with pytest.raises(RuntimeError, match='after decision'):
        m.run_stage(END, now=NOW, deps=deps, run_id='first', acc=Boom(), settings=settings)
    decision = execution_repo.decision_for(conn, a['id'], END)
    assert decision is not None and decision['status'] == 'ok' and len(fake.seen) == 1
    count = lambda: conn.execute('SELECT (SELECT count(*) FROM paper_orders), (SELECT count(*) FROM paper_decisions), '
                                 '(SELECT count(*) FROM account_equity_days), (SELECT count(*) FROM agent_runs)').fetchone()
    before = count()
    conn.commit()
    monkeypatch.setattr(execution_paper_accounts, 'decision_facts', lambda *a_, **k: pytest.fail('resume rebuilt the portfolio facts'))
    again = m.run_stage(END, now=NOW + timedelta(hours=2), deps=deps, run_id='resume', acc=None, settings=settings)
    assert execution_repo.decision_for(conn, a['id'], END)['id'] == decision['id']
    assert again['accounts'][0]['decision']['status'] == 'ok'
    assert len(fake.seen) == 1 and count() == before
