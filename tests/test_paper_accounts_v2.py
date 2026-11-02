"""``agent-portfolio-2`` (v2.14 spec §7.3, K2): the Agent-managed account reads the session's stored portfolio check.

The evidence block gains ``[fact:portfolio_check]``; a buy of a ``blocked`` plan is rejected with the check's first
reason; a missing check (node failed) or a switched-off ``portfolio_manager`` never blocks. SQL tests on the
disposable database through the real accounts-node wiring; the model is scripted."""
import investment_assistant.execution.repo as execution_repo
import investment_assistant.workflow.nodes.accounts as accounts_node
import json
from contextlib import nullcontext
from datetime import date, timedelta
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from test_workbench_ledger import agent3_version
from test_workbench_paper_accounts import DAY, END, NOW, conn, paper  # noqa: F401
from investment_assistant.analysis.agent import graph as agent_graph
from investment_assistant.core import tx as service
from investment_assistant.core.llm.model import AgentSettings
from investment_assistant.core.versions import PORTFOLIO_AGENT_VERSION, PORTFOLIO_CHECK_VERSION

SETTINGS = AgentSettings('chatgpt', 'fake', None, 1000, None)
ON, OFF = {'portfolio_manager': True}, {'portfolio_manager': False}


def entry_plan(conn):
    return agent3_version(conn, 0, None, NOW - timedelta(days=4), day=date(2026, 10, 20),
                          valid_until=date(2026, 10, 28))


def stored_check(conn, account, plan, *, verdict, reasons):
    """The row the portfolio node would have written for this account and session."""
    result = {'exposure_by_sector': {}, 'conflicts': [], 'order': [str(plan['id'])] if verdict == 'ok' else [],
              'verdicts': [{'plan_id': str(plan['id']), 'ticker': plan['ticker'], 'group': 'semis',
                            'verdict': verdict, 'reasons': reasons, 'order': 1 if verdict == 'ok' else None}],
              'params_used': {'sector_cap': 0.35, 'max_per_sector': 3}, 'version': PORTFOLIO_CHECK_VERSION}
    execution_repo.insert_portfolio_check(conn, {'account_id': account['id'], 'session': END, 'run_id': 'node',
                                                 'cutoff_at': NOW, 'version': PORTFOLIO_CHECK_VERSION,
                                                 'result': result})
    conn.commit()


def buy(plan, shares=1):
    return AIMessage(content=json.dumps({'orders': [{'action': 'buy', 'plan_id': str(plan['id']), 'shares': shares,
                                                     'reason': '计划成立'}], 'holds': [], 'summary': '买入'},
                                        ensure_ascii=False))


def decide(conn, scripted_model, plan, *, features=ON, run_id='r1'):
    """The accounts node's four phases, with the Agent-managed account deciding on a scripted model."""
    model = scripted_model([buy(plan)])
    deps = service.Deps(agent_settings=SETTINGS, agent_model=model, agent_checkpointer=lambda: nullcontext(InMemorySaver()))
    conn.commit()
    accounts_node.run_stage(END, now=NOW, deps=deps, run_id=run_id, acc=None, settings=SETTINGS, features=features)
    return model


def orders(conn, account):
    return [dict(zip(('status', 'status_reason', 'shares'), r)) for r in conn.execute(
        'SELECT status, status_reason, shares FROM paper_orders WHERE account_id = %s ORDER BY placed_on', (account['id'],))]


def decision(conn, account):
    return execution_repo.decision_for(conn, account['id'], END)


# ------------------------------------------------------------------------------------------------ evidence

def test_evidence_has_portfolio_check_fact(conn, scripted_model):
    a = paper(conn, strategy='agent_managed')
    plan = entry_plan(conn)
    stored_check(conn, a, plan, verdict='ok', reasons=[])
    model = decide(conn, scripted_model, plan)
    human = '\n'.join(str(m.content) for turn in model.seen for m in turn)
    assert '[fact:portfolio_check]' in human and '"verdict": "ok"' in human
    fact = decision(conn, a)['evidence']['portfolio_check']
    assert fact['status'] == 'ready' and fact['version'] == PORTFOLIO_CHECK_VERSION
    assert fact['verdicts'][0]['plan_id'] == str(plan['id']) and fact['order'] == [str(plan['id'])]
    assert set(fact) >= {'verdicts', 'exposure_by_sector', 'conflicts', 'order'}
    assert decision(conn, a)['detail']['portfolio_check'] == 'ready'
    # the v2 paragraph of the prompt: the check is a hard result
    assert '组合检查是硬结果' in agent_graph.PORTFOLIO_PROMPT and 'blocked' in agent_graph.PORTFOLIO_PROMPT


def test_follow_plans_account_does_not_read_the_check(conn):
    a = paper(conn, strategy='follow_plans')
    plan = entry_plan(conn)
    stored_check(conn, a, plan, verdict='blocked', reasons=['sector_cap'])
    conn.commit()
    accounts_node.run_stage(END, now=NOW, deps=service.Deps(), run_id='r1', acc=None, settings=None, features=ON)
    row = decision(conn, a)
    assert row['status'] == 'ok' and row['detail']['orders_placed'] == 1 and row['detail']['orders_rejected'] == 0
    assert 'portfolio_check' not in (row['detail'] or {})            # the control account is untouched (D31)


# ------------------------------------------------------------------------------------------------ gate

def test_blocked_plan_rejected(conn, scripted_model):
    a = paper(conn, strategy='agent_managed')
    plan = entry_plan(conn)
    stored_check(conn, a, plan, verdict='blocked', reasons=['sector_cap', 'max_per_sector'])
    decide(conn, scripted_model, plan)
    assert orders(conn, a) == [{'status': 'rejected', 'status_reason': 'sector_cap', 'shares': 1}]   # first reason
    assert decision(conn, a)['detail']['orders_rejected'] == 1


def test_deferred_plan_may_be_ordered(conn, scripted_model):
    a = paper(conn, strategy='agent_managed')
    plan = entry_plan(conn)
    stored_check(conn, a, plan, verdict='deferred', reasons=['deferred_by_cash'])
    decide(conn, scripted_model, plan)
    assert orders(conn, a)[0]['status'] == 'pending'                    # cash checks stay with validate_orders


def test_missing_check_does_not_block(conn, scripted_model):
    a = paper(conn, strategy='agent_managed')
    plan = entry_plan(conn)
    model = decide(conn, scripted_model, plan)                           # the node failed: no portfolio_checks row
    assert orders(conn, a)[0]['status'] == 'pending'
    assert decision(conn, a)['detail']['portfolio_check'] == 'missing'
    assert decision(conn, a)['evidence']['portfolio_check']['status'] == 'missing'
    assert '[fact:portfolio_check]' in '\n'.join(str(m.content) for turn in model.seen for m in turn)


def test_flag_off_ignores_a_stale_check_and_says_not_checked(conn, scripted_model):
    """K2: with the switch off no hard check is consulted — not even the same session's earlier row."""
    a = paper(conn, strategy='agent_managed')
    plan = entry_plan(conn)
    stored_check(conn, a, plan, verdict='blocked', reasons=['sector_cap'])
    model = decide(conn, scripted_model, plan, features=OFF)
    assert orders(conn, a)[0]['status'] == 'pending'                    # not blocked
    fact = decision(conn, a)['evidence']['portfolio_check']
    assert fact['status'] == 'not_checked' and 'verdicts' not in fact and fact['note'] == '本版未检查'
    assert decision(conn, a)['detail']['portfolio_check'] == 'missing'
    assert '本版未检查' in '\n'.join(str(m.content) for turn in model.seen for m in turn)


def test_runtime_flag_read_when_no_frozen_state(conn, scripted_model):
    """A caller without frozen flags (the v2.7 shell, a direct call) reads the current ones: default on."""
    from investment_assistant.core import features as core_features

    a = paper(conn, strategy='agent_managed')
    plan = entry_plan(conn)
    stored_check(conn, a, plan, verdict='blocked', reasons=['single_cap'])
    core_features.record_change(conn, flag='portfolio_manager', enabled=True, reason='显式开启', request_id=uuid4())
    decide(conn, scripted_model, plan, features=None)
    assert orders(conn, a)[0]['status_reason'] == 'single_cap'


# ------------------------------------------------------------------------------------------------ version

def test_decision_records_agent_portfolio_2(conn, scripted_model):
    a = paper(conn, strategy='agent_managed')
    plan = entry_plan(conn)
    stored_check(conn, a, plan, verdict='ok', reasons=[])
    decide(conn, scripted_model, plan)
    row = decision(conn, a)
    assert PORTFOLIO_AGENT_VERSION == agent_graph.PORTFOLIO_AGENT_VERSION == 'agent-portfolio-2'
    assert row['agent_version'] == 'agent-portfolio-2' and row['status'] == 'ok'
    run = conn.execute('SELECT mode, agent_version FROM agent_runs WHERE id = %s', (row['agent_run_id'],)).fetchone()
    assert run == ('portfolio', 'agent-portfolio-2')
