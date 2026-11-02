"""The ``portfolio`` node (v2.14 spec §7.2, K2): one hard check per active account and session, the real account's
PM commentary, the feature switch and the failure isolation. SQL tests on the disposable database; the model is
scripted."""
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.core.features as core_features
import investment_assistant.execution.repo as execution_repo
import investment_assistant.workflow.graph as workflow
import investment_assistant.workflow.nodes.portfolio as portfolio_node
import json
from contextlib import nullcontext
from datetime import date, timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from _daily_db import clock, daily_config, daily_db, job_reports, notices, shared_saver  # noqa: F401
from test_daily_workflow import effective, go
from test_workbench_ledger import agent3_version
from test_workbench_paper_accounts import DAY, END, NOW, conn, paper  # noqa: F401
from investment_assistant.core import tx as service
from investment_assistant.core.llm.model import AgentSettings
from investment_assistant.core.versions import PM_AGENT_VERSION, PORTFOLIO_CHECK_VERSION
from investment_assistant.execution import accounts

ON = {'portfolio_manager': True}
OFF = {'portfolio_manager': False}
SETTINGS = AgentSettings('chatgpt', 'fake', None, 1000, None)


def entry_plan(conn, ticker='NVDA'):
    """One valid agent-3 entry head for the session END."""
    return agent3_version(conn, 0, None, NOW - timedelta(days=4), day=date(2026, 10, 20), ticker=ticker,
                          valid_until=date(2026, 10, 28))


def open_real(conn):
    execution_repo.set_account_opening(conn, accounts.REAL_ACCOUNT_ID, opening_cash=D(50000), opened_on=DAY)
    conn.commit()


def deps_for(model=None):
    return service.Deps(agent_settings=SETTINGS, agent_model=model,
                        agent_checkpointer=lambda: nullcontext(InMemorySaver()))


def stage(conn, *, features=ON, model=None, run_id='r1', acc=None, settings=SETTINGS):
    conn.commit()
    return portfolio_node.run_stage(END, now=NOW, deps=deps_for(model), run_id=run_id, acc=acc, settings=settings,
                                    features=features, clock=lambda: 0, started=0)


COLUMNS = ('account_id', 'session', 'run_id', 'cutoff_at', 'version', 'result', 'commentary', 'pm_run_id')


def rows(conn):
    """The portfolio_checks rows by account id."""
    found = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM portfolio_checks ORDER BY id").fetchall()
    return {str(row[0]): dict(zip(COLUMNS, row)) for row in found}


def pm_reply(**overrides):
    body = {'summary': '只有一条可执行计划，先做它。', 'ranking': [], 'tradeoffs': [
        {'text': '行业集中度在上限内', 'refs': ['fact:portfolio_check']}], 'hold_actions': []}
    return AIMessage(content=json.dumps({**body, **overrides}, ensure_ascii=False))


# ------------------------------------------------------------------------------------------------ one row each

def test_writes_one_row_per_active_account(conn):
    a, b = paper(conn, 'a'), paper(conn, 'b', 'agent_managed')
    later = execution_repo.insert_account(conn, dict(id=uuid4(), name='later', kind='paper', strategy='follow_plans',
        opening_cash=D(1000), opened_on=date(2026, 10, 26), params=accounts.default_params(
            __import__('investment_assistant.config', fromlist=['WorkbenchConfig']).WorkbenchConfig())))
    plan = entry_plan(conn)
    out = stage(conn, model=None, settings=AgentSettings('off', '', None, None, None))
    saved = rows(conn)
    # the real account is not opened, the third paper account opens after the session: two rows, one version
    assert set(saved) == {str(a['id']), str(b['id'])} and str(later['id']) not in saved
    assert {r['version'] for r in saved.values()} == {PORTFOLIO_CHECK_VERSION}
    assert {r['run_id'] for r in saved.values()} == {'r1'} and {r['cutoff_at'] for r in saved.values()} == {NOW}
    result = saved[str(a['id'])]['result']
    assert [v['plan_id'] for v in result['verdicts']] == [str(plan['id'])] and result['verdicts'][0]['verdict'] == 'ok'
    assert set(result) >= {'exposure_by_sector', 'verdicts', 'conflicts', 'order', 'params_used', 'version'}
    assert [(i['name'], i['ok'], i['blocked'], i['deferred']) for i in out['accounts']] == [('a', 1, 0, 0), ('b', 1, 0, 0)]
    assert all(i['commentary_status'] is None for i in out['accounts'])      # no model for paper accounts
    # a repeat of the same session keeps the first frozen rows (insert_portfolio_check: first wins)
    stage(conn, run_id='r2', settings=AgentSettings('off', '', None, None, None))
    assert {r['run_id'] for r in rows(conn).values()} == {'r1'} and len(rows(conn)) == 2
    # the real account joins once it is opened
    open_real(conn)
    stage(conn, run_id='r3', settings=AgentSettings('off', '', None, None, None))
    assert str(accounts.REAL_ACCOUNT_ID) in rows(conn) and len(rows(conn)) == 3


def test_node_detail_and_inputs_are_per_account(conn):
    from investment_assistant.core.stages import StageAcc

    paper(conn, 'a')
    open_real(conn)
    acc = StageAcc()
    stage(conn, model=None, acc=acc, settings=AgentSettings('off', '', None, None, None))
    assert acc.input_count == 2 and acc.output_count == 2
    assert sorted(i['name'] for i in acc.detail['accounts']) == sorted(['真实账户', 'a'])
    assert acc.detail['cutoff_at'] == NOW.isoformat()


# ------------------------------------------------------------------------------------------------ PM commentary

def test_pm_commentary_only_for_real_account_and_validated(conn, scripted_model):
    paper(conn, 'a')
    open_real(conn)
    plan = entry_plan(conn)
    model = scripted_model([pm_reply(ranking=[{'plan_id': str(plan['id']), 'rank': 1, 'reason': '唯一可执行'}])])
    out = stage(conn, model=model)
    saved = rows(conn)
    real = saved[str(accounts.REAL_ACCOUNT_ID)]
    assert real['commentary']['summary'] == '只有一条可执行计划，先做它。' and real['pm_run_id'] is not None
    assert real['commentary']['ranking'][0]['plan_id'] == str(plan['id'])
    assert [r['commentary'] for k, r in saved.items() if k != str(accounts.REAL_ACCOUNT_ID)] == [None]
    assert len(model.seen) == 1                                    # one call: paper accounts get no PM model
    run = conn.execute('SELECT mode, agent_version, status, ticker, signal_date, plan_id FROM agent_runs '
                       'WHERE id = %s', (real['pm_run_id'],)).fetchone()
    assert run == ('pm', PM_AGENT_VERSION, 'ok', None, None, None)
    assert {i['name']: i['commentary_status'] for i in out['accounts']} == {'真实账户': 'ok', 'a': None}
    assert analysis_repo.agent_tokens_on(conn, END) == 15           # the PM call counts into the daily tokens


@pytest.mark.parametrize('bad', [
    {'ranking': [{'plan_id': 'not-an-ok-plan', 'rank': 1, 'reason': 'x'}]},      # ranking only over ok plans
    {'ranking': [], 'tradeoffs': [{'text': 't', 'refs': ['call:9']}]},          # a ref that is no call of this run
    {'ranking': [{'plan_id': 'x', 'rank': 1, 'reason': 'x', 'shares': 3}]},     # levels / shares are forbidden
])
def test_invalid_pm_output_keeps_hard_result_without_commentary(conn, scripted_model, bad):
    open_real(conn)
    entry_plan(conn)
    out = stage(conn, model=scripted_model([pm_reply(**bad)]))
    real = rows(conn)[str(accounts.REAL_ACCOUNT_ID)]
    assert real['commentary'] is None and real['pm_run_id'] is None and real['result']['verdicts']   # hard result stays
    run = conn.execute("SELECT status, reason FROM agent_runs WHERE mode = 'pm'").fetchone()
    assert run[0] == 'rejected' and run[1].startswith('pm_')
    assert out['accounts'][0]['commentary_status'] == 'rejected'


def test_pm_unavailable_or_failing_leaves_hard_result(conn, scripted_model):
    open_real(conn)
    entry_plan(conn)
    off = stage(conn, settings=AgentSettings('off', '', None, None, None))
    real = rows(conn)[str(accounts.REAL_ACCOUNT_ID)]
    assert real['commentary'] is None and off['accounts'][0]['commentary_status'] == 'skipped'
    assert conn.execute("SELECT count(*) FROM agent_runs WHERE mode = 'pm'").fetchone()[0] == 0   # no call, no run


def test_pm_model_error_is_recorded_not_raised(conn):
    class Boom:
        def bind_tools(self, *a, **k):
            return self

        def invoke(self, *a, **k):
            raise RuntimeError('model down')

    open_real(conn)
    entry_plan(conn)
    out = stage(conn, model=Boom())
    assert out['accounts'][0]['commentary_status'] == 'error'
    assert rows(conn)[str(accounts.REAL_ACCOUNT_ID)]['commentary'] is None
    assert conn.execute("SELECT status FROM agent_runs WHERE mode = 'pm'").fetchone()[0] == 'error'


# ------------------------------------------------------------------------------------------------ the switch (K2)

def test_skipped_when_flag_off(conn):
    skipped = []
    ctx = SimpleNamespace(recorder=SimpleNamespace(skip=lambda stage_id, reason: skipped.append((stage_id, reason))),
                          deps=deps_for(), clock=lambda: 0, wall_now=lambda: NOW)
    state = {'run_id': 'r1', 'session': END.isoformat(), 'cutoff_at': NOW.isoformat(), 'summary': {},
             'features': OFF}
    open_real(conn)
    entry_plan(conn)
    out = portfolio_node.run(state, ctx, None)
    assert skipped == [('portfolio', 'feature_disabled')]
    assert out['summary'].get('portfolio') is None
    assert conn.execute('SELECT count(*) FROM portfolio_checks').fetchone()[0] == 0     # the ENTIRE node is skipped


def test_flag_off_through_the_graph_skips_node_and_decisions_do_not_use_a_stale_check(daily_db, clock, notices):
    # a check of the same session exists (an earlier run) — K2: it must not be consulted when the flag is off
    core_features.record_change(daily_db, flag='portfolio_manager', enabled=False, reason='测试', request_id=uuid4())
    daily_db.commit()
    go(daily_db, clock)
    eff = effective(daily_db)
    assert (eff['portfolio']['status'], eff['portfolio']['reason']) == ('skipped', 'feature_disabled')
    assert eff['accounts']['status'] == 'success'
    assert daily_db.execute('SELECT count(*) FROM portfolio_checks').fetchone()[0] == 0


def test_flag_on_runs_and_skips_on_holiday(daily_db, clock, notices):
    go(daily_db, clock)
    assert effective(daily_db)['portfolio']['status'] in ('success', 'degraded')
    assert list(effective(daily_db))[8] == 'portfolio'                     # review → portfolio → accounts


# ------------------------------------------------------------------------------------------------ isolation

def test_failure_does_not_block_accounts_node(daily_db, clock, notices, monkeypatch):
    monkeypatch.setattr(portfolio_node, 'run_stage', lambda *a, **k: (_ for _ in ()).throw(ValueError('check boom')))
    result = go(daily_db, clock)
    eff = effective(daily_db)
    assert eff['portfolio']['status'] == 'failed' and 'check boom' in eff['portfolio']['reason']
    assert eff['accounts']['status'] == 'success'                         # the chain carries on, no upstream_failed
    assert eff['push']['reason'] == 'manual_run' and eff['retro']['reason'] != 'upstream_failed'
    assert result['failed_node'] == 'portfolio' and result['status'] == 'failed'
    assert daily_db.execute('SELECT count(*) FROM portfolio_checks').fetchone()[0] == 0


def test_a_failing_account_check_is_isolated_to_that_account(conn, monkeypatch):
    from investment_assistant.core.stages import StageAcc

    paper(conn, 'a')
    paper(conn, 'b')
    entry_plan(conn)
    real = portfolio_node.portfolio.check
    calls = {'n': 0}

    def second_fails(**kwargs):
        calls['n'] += 1
        if calls['n'] == 1:
            raise ValueError('bad inputs')
        return real(**kwargs)

    monkeypatch.setattr(portfolio_node.portfolio, 'check', second_fails)
    acc = StageAcc()
    stage(conn, acc=acc, settings=AgentSettings('off', '', None, None, None))
    assert len(rows(conn)) == 1                                           # the other account still has its row
    assert [f['reason'] for f in acc.failures] == ['bad inputs'] and acc.output_count == 1
