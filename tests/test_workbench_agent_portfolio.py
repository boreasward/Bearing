"""Portfolio evidence/structure and a single-phase graph, using only scripted models."""
import investment_assistant.data.repo as data_repo
import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal as D
from importlib import import_module, util

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from investment_assistant.analysis.agent import graph
from investment_assistant.execution.citations import Rejected
from investment_assistant.analysis.agent.tools import ToolContext

NOW = datetime(2026, 10, 21, 22, tzinfo=UTC)
S = NOW.date()


def checker():
    assert util.find_spec('investment_assistant.execution.portfolio_check') is not None
    return import_module('investment_assistant.execution.portfolio_check')


def facts():
    return {'account': {'name': 'test', 'cash': D('123456789.123456789'), 'equity': D('150000'),
                        'api_key': 'secret-123', 'note': 'key secret-123'},
            'positions': [{'ticker': 'AMD', 'quantity': 10}], 'pending_orders': [],
            'buyable': [{'ticker': 'NVDA', 'plan_id': 'p', 'max_shares': 10}],
            'rejected_yesterday': [], 'recent_closed': [], 'market': {'label': '中性', 'cutoff_at': NOW}}


def output(**kw):
    return {'orders': [], 'holds': [{'ticker': 'AMD', 'reason': '保持原计划'}], 'summary': '观察', **kw}


def test_seven_evidence_blocks_preserve_numbers_and_redact_sensitive_values():
    c = checker()
    text = c.build_evidence(facts())
    assert re.findall(r'^\[fact:([^]]+)\]', text, re.M) == [
        'account', 'positions', 'pending_orders', 'buyable', 'rejected_yesterday', 'recent_closed', 'market']
    assert '123456789.123456789' in text and 'secret-123' not in text and '***' in text
    assert NOW.isoformat() in text
    assert c.REASON_MAX_CHARS == 300 and c.SUMMARY_MAX_CHARS == 200 and c.PORTFOLIO_RECENT_SESSIONS == 20


def test_structure_and_holds_coverage():
    c = checker()
    assert c.check_portfolio(json.dumps(output()), expected_positions=[{'ticker': 'AMD'}]) == output()
    sold = output(orders=[{'action': 'sell', 'ticker': 'AMD', 'portion': 'half', 'reason': '降风险'}], holds=[])
    assert c.check_portfolio(json.dumps(sold), expected_positions=['AMD']) == sold
    with pytest.raises(Rejected, match='schema_invalid:'):
        c.check_portfolio(json.dumps(output(holds=[])), expected_positions=['AMD'])


@pytest.mark.parametrize('bad', ['not json', [], {}, output(summary=''), output(summary='x'*201),
                                  output(holds=[{'ticker': 'AMD', 'reason': ''}]),
                                  output(holds=[{'ticker': 'AMD', 'reason': 'x'*301}]),
                                  output(orders=[{'action': 'sell', 'ticker': 'AMD', 'portion': 'third', 'reason': 'x'}]),
                                  output(orders=[{'action': 'buy', 'plan_id': 'p', 'reason': 'x'}]),
                                  output(orders=[{'action': 'buy', 'plan_id': 'p', 'shares': 1, 'reason': 'x', 'stop': 90}]),
                                  output(orders=[{'action': 'cancel', 'order_id': 3, 'reason': 'x'}]),
                                  output(holds=[{'ticker': 'AMD', 'reason': 'a'}, {'ticker': 'AMD', 'reason': 'b'}])])
def test_bad_structure_is_rejected(bad):
    with pytest.raises(Rejected, match='schema_invalid:'):
        checker().check_portfolio(bad if isinstance(bad, str) else json.dumps(bad))


def run(make, messages, monkeypatch):
    assert hasattr(graph, 'run_portfolio')
    # Repository consumers use their owning layers below.
    monkeypatch.setattr(data_repo, 'read_bars', lambda conn, ticker, session, **kw: [])
    model = make(messages)
    ctx = ToolContext(ticker='ACCOUNT', session=S, cutoff_at=NOW, read=lambda fn: fn(object()))
    result = graph.run_portfolio(account={'id': 'a', 'name': 'test'}, ctx=ctx, model=model,
                                  checkpointer=InMemorySaver(), run_id='portfolio-test', now=lambda: NOW,
                                  evidence=checker().build_evidence(facts()))
    return result, model


def test_portfolio_prompt_and_valid_graph(scripted_model, monkeypatch):
    result, model = run(scripted_model, [AIMessage(content=json.dumps(output()))], monkeypatch)
    assert graph.PORTFOLIO_AGENT_VERSION == 'agent-portfolio-1'
    assert graph.PORTFOLIO_TOOLS == ('get_price_bars', 'get_indicators', 'get_market_context', 'get_concept', 'get_knowledge')
    assert '你管理一个模拟账户，目标是用 Agent 已经给出的交易计划管好一个组合' in graph.PORTFOLIO_PROMPT
    assert result == {'status': 'ok', 'reason': None, 'output': output(), 'calls': [], 'input_tokens': 10, 'output_tokens': 5}
    assert len(model.seen) == 1


def test_graph_enforces_coverage_from_trusted_positions(scripted_model, monkeypatch):
    result, _ = run(scripted_model, [AIMessage(content=json.dumps(output(holds=[])))], monkeypatch)
    assert result['status'] == 'rejected' and result['reason'].startswith('schema_invalid:')


def test_tool_and_ticker_allowlists(scripted_model, monkeypatch):
    calls = [{'name': 'get_price_bars', 'args': {'ticker': t}, 'id': t} for t in ('AMD', 'NVDA', 'SPY', 'TSLA')]
    calls.append({'name': 'get_news', 'args': {}, 'id': 'news'})
    result, model = run(scripted_model, [AIMessage(content='', tool_calls=calls),
                                        AIMessage(content=json.dumps(output()))], monkeypatch)
    assert result['status'] == 'ok'
    recorded = {r['args']['ticker']: r for r in result['calls']}
    assert set(recorded) == {'AMD', 'NVDA', 'SPY', 'TSLA'}
    assert 'error' not in recorded['AMD']['result'] and 'error' not in recorded['NVDA']['result']
    assert 'error' in recorded['SPY']['result'] and 'error' in recorded['TSLA']['result']
    assert {r['phase'] for r in result['calls']} == {'portfolio'}
    assert any('get_news' in str(m.content) and 'Error' in str(m.content) for m in model.seen[-1])


def test_portfolio_round_limit(scripted_model, monkeypatch):
    messages = [AIMessage(content='', tool_calls=[{'name': 'get_price_bars', 'args': {'ticker': 'AMD'}, 'id': str(i)}])
                for i in range(graph.MAX_TOOL_ROUNDS + 1)]
    result, _ = run(scripted_model, messages, monkeypatch)
    assert result['status'] == 'rejected' and result['reason'] == 'tool_rounds_exceeded'
    assert len(result['calls']) == graph.MAX_TOOL_ROUNDS


def test_container_share_value_is_structural_invalidity():
    for shares in ([2], {'value': 2}, None):
        bad = output(orders=[{'action': 'buy', 'plan_id': 'p', 'shares': shares, 'reason': '执行'}])
        with pytest.raises(Rejected, match='schema_invalid:'):
            checker().check_portfolio(json.dumps(bad))


def test_redaction_preserves_numeric_literals_and_graph_can_read_evidence(scripted_model, monkeypatch):
    import sys

    payload = facts()
    payload['account'].update(token='123456789', count=123456789,
                               notes=['token=123456789', {'echo': 'quoted "key"\nsecret'}])
    payload['market']['password'] = 'quoted "key"\nsecret'
    payload['secrets'] = ['external-key']
    payload['market']['note'] = 'external-key'
    text = checker().build_evidence(payload)
    assert '"cash": 123456789.123456789' in text
    assert '"count": 123456789' in text
    decoded = checker().evidence_facts(text)
    assert decoded['account']['cash'] == D('123456789.123456789')
    assert decoded['account']['count'] == 123456789
    assert decoded['account']['token'] == '***'
    assert decoded['account']['notes'] == ['token=***', {'echo': '***'}]
    assert decoded['market']['password'] == decoded['market']['note'] == '***'
    assert list(decoded) == ['account', 'positions', 'pending_orders', 'buyable',
                             'rejected_yesterday', 'recent_closed', 'market']
    monkeypatch.setattr(sys.modules[__name__], 'facts', lambda: payload)
    result, _ = run(scripted_model, [AIMessage(content=json.dumps(output()))], monkeypatch)
    assert result['status'] == 'ok'


def test_nested_sensitive_container_collects_all_string_leaves_before_masking():
    payload = facts()
    payload['account']['credentials'] = {'access_token': 'TEST_ONLY_SECRET',
                                          'nested': [{'refresh_token': 'TEST_ONLY_REFRESH'}]}
    payload['market']['echo'] = 'TEST_ONLY_SECRET / TEST_ONLY_REFRESH'
    text = checker().build_evidence(payload)
    assert 'TEST_ONLY_SECRET' not in text and 'TEST_ONLY_REFRESH' not in text
    decoded = checker().evidence_facts(text)
    assert decoded['account']['credentials'] == '***'
    assert decoded['market']['echo'] == '*** / ***'


def test_every_model_visible_account_name_comes_from_scrubbed_evidence(scripted_model):
    payload = facts()
    raw_name = '组合 TEST_ONLY_SECRET'
    payload['account'].update(name=raw_name, token='TEST_ONLY_SECRET')
    model = scripted_model([AIMessage(content=json.dumps(output()))])
    ctx = ToolContext(ticker='ACCOUNT', session=S, cutoff_at=NOW, read=lambda fn: pytest.fail('unexpected read'))
    result = graph.run_portfolio(account={'id': 'a', 'name': raw_name}, ctx=ctx, model=model,
                                  checkpointer=InMemorySaver(), run_id='scrubbed-account-name', now=lambda: NOW,
                                  evidence=checker().build_evidence(payload))
    assert result['status'] == 'ok'
    visible = '\n'.join(str(message.content) for turn in model.seen for message in turn)
    assert 'TEST_ONLY_SECRET' not in visible
    assert '组合 ***' in visible


def test_holds_coverage_uses_same_ticker_normalization_as_sells():
    c = checker()
    raw = output(orders=[{'action': 'sell', 'ticker': ' nvda ', 'portion': 'all', 'reason': '离场'}], holds=[])
    assert c.check_portfolio(json.dumps(raw), expected_positions=['NVDA']) == raw
    raw = output(holds=[{'ticker': ' nvda ', 'reason': '保持'}])
    assert c.check_portfolio(json.dumps(raw), expected_positions=['NVDA']) == raw
