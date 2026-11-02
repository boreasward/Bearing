"""Agent4 graph: company research is additive; plan schema stays agent3."""
import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from investment_assistant.analysis.agent import graph
from investment_assistant.analysis.agent.tools import ToolContext
from investment_assistant.core.versions import AGENT_RUN_VERSION
from investment_assistant.data import repo

NOW = datetime(2026, 10, 30, 22, tzinfo=UTC)
SESSION = date(2026, 10, 30)
UNIT = {'ticker': 'AMD', 'signal_date': SESSION, 'pool_tier': 'core'}
RESEARCH = {'facts': [{'text': '财报披露增长', 'kind': 'disclosure', 'refs': ['call:1']}],
            'risks': [], 'blocked': [], 'summary': '已核验披露'}


def tool(name, args):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': name}])


def plan(*, research_ref=True):
    return json.dumps({'action': 'buy', 'summary': '有支撑', 'entry_type': 'zone_limit', 'max_hold_sessions': 10,
        'levels': {k: {'value': v, 'refs': ['call:2']} for k, v in (
            ('entry_low', 98), ('entry_high', 101), ('stop', 95), ('target1', 110))},
        'thesis': [{'text': '基本面与价格', 'refs': ['call:1' if research_ref else 'call:2']}],
        'invalidation': [{'kind': 'close_below', 'params': {'price': 94}, 'refs': ['call:2']}], 'review_when': []})


def execute(monkeypatch, scripted_model, responses, **kwargs):
    monkeypatch.setattr(repo, 'read_news', lambda *a, **kw: [{'source': 'test', 'title': '披露', 'url': 'local', 'published_at': NOW}])
    monkeypatch.setattr(repo, 'read_bars', lambda *a, **kw: [{'price_date': SESSION, 'open': 100, 'high': 101, 'low': 99, 'close': 100, 'volume': 5}])
    ctx = ToolContext('AMD', SESSION, NOW, lambda fn: fn(None))
    model = scripted_model(responses)
    kwargs.setdefault('version', AGENT_RUN_VERSION)      # every agent-4 test runs the new graph
    result = graph.run_unit_v2(unit=UNIT, ctx=ctx, model=model, checkpointer=InMemorySaver(), run_id='origin',
        last_close=Decimal('100'), as_of_session=SESSION, now=lambda: NOW, **kwargs)
    return result, model


def replies(*, research_output=RESEARCH, research_ref=True):
    return [tool('get_news', {}), AIMessage(content=json.dumps(research_output)),
            tool('get_price_bars', {'ticker': 'AMD'}), AIMessage(content=plan(research_ref=research_ref)),
            AIMessage(content='{"counter": [], "risks": []}')]


def test_research_runs_before_propose_and_fact_block_injected(monkeypatch, scripted_model):
    result, model = execute(monkeypatch, scripted_model, replies())
    assert result['status'] == 'ok'
    assert result['research'] == {'status': 'ok', 'reason': None, 'output': RESEARCH}
    assert result['output']['research'] == result['research']
    assert [(c['phase'], c['seq']) for c in result['calls']] == [('research', 1), ('propose', 2)]
    assert result['input_tokens'] == 50
    assert any('[fact:research]' in str(m.content) for m in model.seen[2])
    assert any('已核验披露' in str(m.content) for m in model.seen[-1])
    assert result['output']['entry_type'] == 'zone_limit'


def test_research_rejected_does_not_block_unit(monkeypatch, scripted_model):
    result, model = execute(monkeypatch, scripted_model, replies(research_output={'facts': []}, research_ref=False))
    assert result['status'] == 'ok'
    assert result['research']['status'] == 'rejected'
    assert any('本轮无公司研究' in str(m.content) for m in model.seen[2])


def test_thesis_must_cite_research_on_first_version(monkeypatch, scripted_model):
    result, _ = execute(monkeypatch, scripted_model, replies(research_ref=False)[:-1])
    assert result['status'] == 'rejected'
    assert result['reason'] == 'research_not_cited'
    assert result['research']['status'] == 'ok'


def test_research_disabled_by_flag(monkeypatch, scripted_model):
    responses = [tool('get_news', {}), tool('get_price_bars', {'ticker': 'AMD'}),
                 AIMessage(content=plan(research_ref=False)), AIMessage(content='{"counter": []}')]
    result, model = execute(monkeypatch, scripted_model, responses, research=False)
    assert result['status'] == 'ok'
    assert result['research']['status'] == 'disabled'
    assert not any(c['phase'] == 'research' for c in result['calls'])
    assert '公司研究员' not in str(model.seen[0][0].content)


def previous_output():
    """A prior version as the chain stores it: a checked output (``review_when`` already
    normalized to its default entry) plus the research segment the revision carries."""
    previous = json.loads(plan())
    previous['review_when'] = [{'kind': 'next_session', 'params': {}}]
    return previous


@pytest.mark.parametrize('via', [None, 'retrigger', 'data_request'])
def test_revision_carries_previous_research(monkeypatch, scripted_model, via):
    previous = previous_output()
    previous['research'] = {'status': 'ok', 'reason': None, 'output': RESEARCH}
    new = json.loads(plan(research_ref=False))
    new['levels']['target1']['value'] = 112
    new['changes'] = [{'field': 'target1', 'text': '目标调整', 'refs': ['call:2']}]
    result, model = execute(monkeypatch, scripted_model, [tool('get_news', {}),
        tool('get_price_bars', {'ticker': 'AMD'}), AIMessage(content=json.dumps(new)), AIMessage(content='{"counter": []}')],
        previous=previous, previous_run_id='original-run', via=via)
    assert result['status'] == 'ok'
    assert result['research']['status'] == 'carried'
    assert result['research']['carried_from'] == 'original-run'
    assert result['research']['output'] == RESEARCH
    assert all(c['phase'] != 'research' for c in result['calls'])
    assert any('不是本次可引用证据' in str(m.content) for m in model.seen[0])


def test_continuous_carry_keeps_original_owner_and_allows_current_same_number(monkeypatch, scripted_model):
    old = {'status': 'carried', 'reason': None, 'output': RESEARCH, 'carried_from': 'original-run', 'original_status': 'ok'}
    previous = previous_output()
    previous['research'] = old
    new = json.loads(plan())
    new['levels']['target1']['value'] = 112
    new['changes'] = [{'field': 'target1', 'text': '调整', 'refs': ['call:2']}]
    result, _ = execute(monkeypatch, scripted_model, [tool('get_news', {}), tool('get_price_bars', {'ticker': 'AMD'}),
        AIMessage(content=json.dumps(new)), AIMessage(content='{"counter": []}')], previous=previous,
        previous_run_id='intermediate')
    assert result['status'] == 'ok'
    assert result['research']['carried_from'] == 'original-run'
    assert result['output']['thesis'][0]['refs'] == ['call:1']  # this run's actual news, not the original evidence


def test_unopened_request_rerun_carries_without_research_call(monkeypatch, scripted_model):
    old = {'status': 'ok', 'reason': None, 'output': RESEARCH}
    result, _ = execute(monkeypatch, scripted_model, [tool('get_news', {}), tool('get_price_bars', {'ticker': 'AMD'}),
        AIMessage(content=plan(research_ref=False)), AIMessage(content='{"counter": []}')], research=False,
        carried_research=old, previous_run_id='original-run', via='data_request')
    assert result['status'] == 'ok'
    assert result['research']['status'] == 'carried'
    assert result['research']['carried_from'] == 'original-run'
    assert all(c['phase'] != 'research' for c in result['calls'])
