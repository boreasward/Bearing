"""Agent reasoning reading view (v2.6 spec §8): steps assembled from stored runs, calls, versions and reviews.

The offline part drives the pure `build` with hand-made rows shaped like the stored ones; the SQL part
builds a first version through the real batch and reads it back through `reasoning.reasoning`."""
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.execution.repo as execution_repo
import investment_assistant.review.repo as review_repo
import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest

import investment_assistant.views.reasoning as reasoning
from investment_assistant.derivation.indicators.price import indicators
from investment_assistant.analysis.agent import REASON_TEXT
from investment_assistant.analysis.agent import tools

RUN_ID = '11111111-1111-4111-8111-111111111111'
PLAN_ID = '22222222-2222-4222-8222-222222222222'
T = datetime(2026, 10, 22, 22, 30, tzinfo=UTC)
LEGACY_NOTE = 'agent-1（旧版）：没有失效条件、下次复查条件与逐条对应的反证，也不参与复查；以下按原样显示'
NO_TIMELINE = '迁移 026 未执行，没有版本与复查记录'
EXEMPT_TEXT = '概念卡随代码版本走，不受截止时间约束'

OUTPUT = {
    'action': 'buy', 'summary': '回踩修复放量',
    'levels': {'entry_low': {'value': '98.50', 'refs': ['call:1']}, 'entry_high': {'value': '101.50', 'refs': ['call:1']},
               'stop': {'value': '96.00', 'refs': ['call:1']}, 'target1': {'value': '108.00', 'refs': ['call:1']}},
    'thesis': [{'id': 'T1', 'text': '突破 20 日线', 'refs': ['call:1']}, {'id': 'T2', 'text': '量能放大', 'refs': ['call:2']}],
    'invalidation': [{'kind': 'close_below', 'params': {'price': '94.00'}, 'text': '收盘跌破 94.00', 'refs': ['call:1']}],
    'review_when': [{'kind': 'next_session', 'params': {}, 'text': '下一交易日'}],
    'changes': [], 'diff': None}
COUNTER = {'status': 'ok', 'counter': [{'thesis': 1, 'text': '20 日线多次假突破', 'refs': ['call:3']}],
           'risks': [{'text': '财报临近', 'refs': ['call:3']}], 'dropped': 1, 'uncountered': [2]}
VERDICT = {'outcome': 'revise', 'reason': '财报后需要上调止损', 'refs': ['call:1']}
INTERPRETATION = {'summary': '回踩后放量', 'support': [{'text': '量能放大', 'refs': ['call:1']}],
                  'contra': [], 'conflicts': []}
LEGACY_OUTPUT = {'action': 'buy', 'summary': '回踩修复放量',
                 'levels': OUTPUT['levels'], 'thesis': [{'text': '突破 20 日线', 'refs': ['call:1']}]}


def run_row(*, mode='propose', status='ok', reason=None, agent_version='agent-2',
            output: dict | None = OUTPUT, counter: dict | None = COUNTER,
            plan_id: str | None = PLAN_ID, **extra):
    return {'id': RUN_ID, 'ticker': 'AMD', 'signal_date': date(2026, 10, 22), 'pool': 'main', 'attempt': 1,
            'mode': mode, 'agent_version': agent_version, 'provider': 'chatgpt', 'model': 'gpt-test', 'cutoff_at': T,
            'status': status, 'reason': reason, 'detail': None, 'output': output, 'counter': counter,
            'plan_id': plan_id, 'input_tokens': 10, 'output_tokens': 5, 'started_at': T, 'finished_at': T, **extra}


def call_row(seq, tool, *, phase='propose', args=None, data=None, error=None):
    envelope: dict[str, Any] = {'ref': f'call:{seq}'}
    envelope['error' if error else 'data'] = error or data
    return {'id': seq, 'run_id': RUN_ID, 'seq': seq, 'phase': phase, 'tool': tool, 'args': args or {},
            'result': envelope, 'result_sha256': 'ab12cd34' + '0' * 56, 'called_at': T}


CALLS = [call_row(1, 'get_indicators', args={'ticker': 'AMD'}, data={'last_close': 100.0, 'atr14': 2.0, 'ma20': 99.0,
                                                                     'high_20': 101.0, 'volume_ratio': 1.2}),
         call_row(2, 'get_news', args={'days': 7}, data=[]),
         call_row(3, 'get_earnings', phase='counter', data={'earnings_date': '2026-11-05', 'status': 'known'})]

PLAN_STATE = {'plan': {'id': PLAN_ID, 'status': 'ok', 'invalid_reasons': [], 'warnings': [],
                       'valid_from': date(2026, 10, 23), 'valid_until': date(2026, 10, 27)},
              'consistency': {'status': 'consistent', 'mismatches': [], 'missing': []}}
VERSION = {'plan_id': PLAN_ID, 'version_no': 0, 'origin': 'signal', 'agent_run_id': RUN_ID, 'supersedes_plan_id': None,
           'diff': None}


def review_row(**extra):
    return {'id': 7, 'ticker': 'AMD', 'signal_date': date(2026, 10, 22), 'head_plan_id': PLAN_ID,
            'session': date(2026, 10, 26), 'trigger': 'batch', 'run_id': 'r1', 'mode': 'entry',
            'signals': [{'kind': 'new_8k', 'fired': True, 'detail': '1 份 8-K'},
                        {'kind': 'abnormal_move', 'fired': False, 'detail': ''}],
            'conditions': [{'list': 'invalidation', 'index': 0, 'kind': 'close_below', 'params': {'price': '94.00'},
                            'result': 'not_met', 'detail': ''}],
            'user_input_ids': [7], 'outcome': 'revise', 'decided_by': 'agent', 'reason': '财报后需要上调止损',
            'refs': ['call:1'], 'review_agent_run_id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            'revision_agent_run_id': RUN_ID, 'new_plan_id': None, 'detail': {}, 'created_at': T, **extra}


def build(**kwargs):
    base: dict[str, Any] = {'run': run_row(), 'calls': CALLS, 'version': VERSION, 'trigger_review': None,
                            'later_reviews': [], 'plan_state': PLAN_STATE, 'timeline_ready': True}
    base.update(kwargs)
    return reasoning.build(**base)


def kinds(view):
    return [step['kind'] for step in view['steps']]


def step(view, kind, nth=0):
    return [s for s in view['steps'] if s['kind'] == kind][nth]


# ---------------------------------------------------------------- tables

def test_tool_concepts_cover_all_tools():
    from investment_assistant.review.discuss_tools import DISCUSS_TOOLS
    assert set(reasoning.TOOL_CONCEPTS) == set(tools.TOOLS) | set(DISCUSS_TOOLS)
    assert len(reasoning.TOOL_CONCEPTS) == 13
    assert {name: reasoning.TOOL_CONCEPTS[name]['concept'] for name in
            ('get_knowledge', 'get_plan_context', 'get_derivation', 'get_agent_run')} == {
        'get_knowledge': 'system.my_knowledge', 'get_plan_context': 'system.discussion',
        'get_derivation': 'plan.derivation', 'get_agent_run': 'agent.tool_calls'}
    assert all(name in reasoning.TOOL_TEXT for name in reasoning.TOOL_CONCEPTS)
    assert reasoning.TOOL_CONCEPTS['get_trigger']['concept'] == 'system.decision_unit'
    assert reasoning.TOOL_CONCEPTS['get_concept']['concept'] == 'system.concept_card'
    fields = reasoning.TOOL_CONCEPTS['get_indicators']['fields']
    assert fields['atr14'] == 'indicator.atr14' and fields['ma200'] == 'indicator.moving_average'
    assert fields['low_252'] == 'indicator.high_low' and fields['rs60'] == 'indicator.relative_strength'
    assert fields['last_close'] == 'family.bars' and fields['volume_ratio'] == 'indicator.volume_ratio'
    assert set(fields) == set(indicators([], []))        # every indicator key is mapped
    market = reasoning.TOOL_CONCEPTS['get_market_context']['fields']
    assert market['macro.BAMLH0A0HYM2'] == 'indicator.hy_oas' and market['macro.T10Y2Y'] == 'indicator.t10y2y'
    assert market['label'] == 'indicator.regime' and market['sectors'] == 'indicator.sector_strength'


def test_checks_tables():
    assert set(reasoning.CHECKS) == {'agent-2-propose', 'agent-2-revise', 'agent-1', 'review', 'interpret', 'retro',
                                     'discuss'}
    ids = lambda key: [item[0] for item in reasoning.CHECKS[key]]          # noqa: E731
    assert ids('agent-2-propose') == ['json', 'refs', 'price_refs', 'level_order', 'level_range', 'thesis_count',
                                      'conditions', 'tool_rounds']
    assert ids('agent-2-revise') == ['json', 'refs', 'price_refs', 'level_order', 'level_range', 'thesis_count',
                                     'conditions', 'changes', 'plan_status', 'tool_rounds']
    assert ids('agent-1') == ['json', 'refs', 'price_refs', 'level_order', 'level_range', 'tool_rounds']
    assert ids('review') == ['json', 'refs', 'tool_rounds'] and ids('interpret') == ['json', 'refs', 'tool_rounds']
    assert ids('retro') == ['json', 'refs', 'lessons', 'tool_rounds']
    assert ids('discuss') == ['json', 'refs', 'draft', 'tool_rounds']
    reasons = {r for items in reasoning.CHECKS.values() for item in items for r in item[2]}
    assert reasons <= set(REASON_TEXT)
    assert {'retro_malformed', 'lesson_invalid', 'answer_malformed'} <= reasons


# ---------------------------------------------------------------- propose

def test_build_propose_steps():
    view = build()
    assert kinds(view) == ['read', 'proposal', 'check', 'read', 'counter', 'response', 'outcome']
    assert view['legacy'] is False and view['notes'] == []
    assert view['run']['mode'] == 'propose' and view['run']['status'] == 'ok' and view['run']['reason_text'] is None
    assert view['run']['signal_date'] == '2026-10-22' and view['run']['cutoff_at'] == T.isoformat()
    assert view['version'] == {'plan_id': PLAN_ID, 'version_no': 0, 'origin': 'signal'} and view['review'] is None
    json.dumps(view)                                            # JSON-safe as a whole

    read = step(view, 'read', 0)
    assert read['phase'] == 'propose' and [c['ref'] for c in read['calls']] == ['call:1', 'call:2']
    first = read['calls'][0]
    assert first['tool'] == 'get_indicators' and first['tool_text'] == '技术指标' and first['args'] == {'ticker': 'AMD'}
    assert first['concept'] == 'indicator.atr14' and first['cutoff_exempt'] is False
    assert first['result_sha256_8'] == 'ab12cd34' and first['fields']['atr14'] == 'indicator.atr14'
    assert set(first['fields']) == {'last_close', 'atr14', 'ma20', 'high_20', 'volume_ratio'}
    assert step(view, 'read', 1)['phase'] == 'counter' and [c['ref'] for c in step(view, 'read', 1)['calls']] == ['call:3']

    proposal = step(view, 'proposal')
    assert proposal['action'] == 'buy' and proposal['summary'] == '回踩修复放量'
    assert [(lv['key'], lv['value'], lv['concept']) for lv in proposal['levels']] == [
        ('entry_low', '98.50', 'plan.entry_zone'), ('entry_high', '101.50', 'plan.entry_zone'),
        ('stop', '96.00', 'plan.stop'), ('target1', '108.00', 'plan.target1')]
    assert proposal['levels'][2]['refs'] == ['call:1']
    assert proposal['thesis'][0] == {'id': 'T1', 'text': '突破 20 日线', 'refs': ['call:1']}
    assert proposal['invalidation'] == [{'kind': 'close_below', 'params': {'price': '94.00'}, 'text': '收盘跌破 94.00',
                                         'refs': ['call:1']}]
    assert proposal['review_when'][0]['text'] == '下一交易日'
    assert 'changes' not in proposal and 'diff' not in proposal

    check = step(view, 'check')
    assert [i['id'] for i in check['items']] == [i[0] for i in reasoning.CHECKS['agent-2-propose']]
    assert {i['result'] for i in check['items']} == {'pass'} and check['note'] is None

    counter = step(view, 'counter')
    assert counter['status'] == 'ok' and counter['dropped'] == 1
    assert counter['items'] == [{'thesis': 'T1', 'text': '20 日线多次假突破', 'refs': ['call:3']}]
    assert counter['risks'] == [{'text': '财报临近', 'refs': ['call:3']}] and counter['uncountered'] == ['T2']

    response = step(view, 'response')
    assert response['items'] == [] and response['title'] == '回应：本版之后还没有复查'
    outcome = step(view, 'outcome')
    assert outcome['plan_id'] == PLAN_ID and outcome['status'] == 'ok'
    assert outcome['consistency'] == {'status': 'consistent', 'mismatches': [], 'missing': []}


def test_pass_run_has_no_levels_but_still_checked():
    out = {**OUTPUT, 'action': 'pass', 'levels': None, 'invalidation': [], 'review_when': []}
    view = build(run=run_row(status='pass', output=out, plan_id=None), plan_state=None, version=None)
    assert kinds(view) == ['read', 'proposal', 'check', 'read', 'counter']
    assert step(view, 'proposal')['levels'] == [] and step(view, 'proposal')['action'] == 'pass'
    assert {i['result'] for i in step(view, 'check')['items']} == {'pass'}


RESEARCH = {'status': 'ok', 'reason': None,
            'output': {'facts': [{'text': '财报披露营收增长', 'kind': 'disclosure', 'refs': ['call:1']}],
                       'risks': [], 'blocked': [], 'summary': '基本面已核验'}}


def test_research_section_rendered():
    """UI1: the researcher segment precedes the proposer read, exact shape, original call numbers kept."""
    calls = [call_row(1, 'get_news', phase='research', args={'days': 30}, data=[]),
             *CALLS]
    view = build(run=run_row(agent_version='agent-4', output={'research': RESEARCH, **OUTPUT}), calls=calls)
    assert kinds(view)[:4] == ['research', 'read', 'read', 'proposal']
    research = view['steps'][0]
    assert research == {'kind': 'research', 'title': '公司研究', 'concept': 'agent.company_researcher',
                        'status': 'ok', 'reason': None, 'output': RESEARCH['output']}
    read = step(view, 'read', 0)
    assert read['phase'] == 'research' and [c['ref'] for c in read['calls']] == ['call:1']
    assert [c['ref'] for c in step(view, 'read', 1)['calls']] == ['call:1', 'call:2']   # propose calls unchanged
    # a carried segment renders the same shape with its provenance status; no calls of THIS run
    carried = {**RESEARCH, 'status': 'carried', 'carried_from': 'original-run', 'original_status': 'ok'}
    view = build(run=run_row(agent_version='agent-4', output={'research': carried, **OUTPUT}), calls=CALLS)
    assert kinds(view)[:2] == ['research', 'read']
    assert view['steps'][0]['status'] == 'carried' and view['steps'][0]['output'] == RESEARCH['output']
    # pre-agent-4 runs keep the old step list exactly
    assert 'research' not in kinds(build())


def test_build_revise_has_evidence_and_diff():
    output = {**OUTPUT, 'changes': [{'field': 'stop', 'text': '财报后上调', 'refs': ['call:1']}],
              'diff': [{'field': 'stop', 'before': '95.00', 'after': '96.00'}]}
    view = build(run=run_row(mode='revise', output=output), trigger_review=review_row(),
                 version={**VERSION, 'version_no': 1, 'origin': 'review'})
    assert kinds(view) == ['evidence', 'read', 'proposal', 'check', 'read', 'counter', 'response', 'outcome']
    evidence = step(view, 'evidence')
    assert evidence['review_id'] == 7 and evidence['session'] == '2026-10-26' and evidence['trigger'] == 'batch'
    assert evidence['signals'][0]['kind'] == 'new_8k' and evidence['conditions'][0]['result'] == 'not_met'
    assert evidence['user_input_ids'] == [7]
    assert evidence['review_agent_run_id'] == 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    assert evidence['revision_agent_run_id'] == RUN_ID
    assert evidence['outcome'] == 'revise' and evidence['decided_by'] == 'agent'
    proposal = step(view, 'proposal')
    assert proposal['changes'] == [{'field': 'stop', 'text': '财报后上调', 'refs': ['call:1']}]
    assert proposal['diff'] == [{'field': 'stop', 'before': '95.00', 'after': '96.00'}]
    assert [i['id'] for i in step(view, 'check')['items']][-2:] == ['plan_status', 'tool_rounds']
    assert view['review']['id'] == 7 and view['version']['origin'] == 'review'


def test_revise_with_missing_review_row_says_so():
    view = build(run=run_row(mode='revise'), trigger_review=None)
    assert step(view, 'evidence')['review_id'] is None
    assert '没有找到' in step(view, 'evidence')['text']


# ---------------------------------------------------------------- review / interpret

def test_build_review_steps():
    calls = [call_row(1, 'get_trigger', phase='review', data=[{'rule_id': 'trend_reclaim', 'action': 'buy',
                                                              'inputs': {}}])]
    view = build(run=run_row(mode='review', agent_version='agent-review-1', output=VERDICT, counter=None, plan_id=None),
                 calls=calls, version=None, plan_state=None,
                 trigger_review=review_row(review_agent_run_id=RUN_ID, revision_agent_run_id=None))
    assert kinds(view) == ['evidence', 'read', 'verdict', 'check']
    assert step(view, 'read')['phase'] == 'review'
    assert step(view, 'read')['calls'][0]['fields'] == {'trend_reclaim': 'rule.trend_reclaim'}
    verdict = step(view, 'verdict')
    assert verdict['outcome'] == 'revise' and verdict['outcome_text'] == '修订'
    assert verdict['reason'] == '财报后需要上调止损' and verdict['refs'] == ['call:1']
    assert [i['id'] for i in step(view, 'check')['items']] == ['json', 'refs', 'tool_rounds']


def test_build_interpret_steps():
    view = build(run=run_row(mode='interpret', agent_version='agent-brief-1', output=INTERPRETATION, counter=None,
                             plan_id=None), calls=CALLS[:2], version=None, plan_state=None)
    assert kinds(view) == ['read', 'interpretation', 'check']
    assert step(view, 'interpretation')['summary'] == '回踩后放量'
    assert step(view, 'interpretation')['support'] == INTERPRETATION['support']


def test_build_interpret_has_tool_rounds_check():
    view = build(run=run_row(mode='interpret', agent_version='agent-brief-1', status='rejected',
                             reason='tool_rounds_exceeded', output=None, counter=None, plan_id=None),
                 calls=CALLS[:2], version=None, plan_state=None)
    items = {i['id']: i for i in step(view, 'check')['items']}
    assert items['tool_rounds']['result'] == 'implicated'
    assert items['json']['result'] == 'not_recorded' and items['refs']['result'] == 'not_recorded'
    assert step(view, 'interpretation')['text'] == '这次运行作废：工具调用超限，只保留工具调用'


# ---------------------------------------------------------------- agent-1

def test_build_agent1_legacy():
    counter = {'status': 'ok', 'risks': [{'text': '财报临近', 'refs': ['call:3']}], 'dropped': 0}
    view = build(run=run_row(agent_version='agent-1', output=LEGACY_OUTPUT, counter=counter), version=None)
    assert view['legacy'] is True and LEGACY_NOTE in view['notes']
    assert kinds(view) == ['read', 'proposal', 'check', 'read', 'counter', 'outcome']
    assert 'response' not in kinds(view)
    proposal = step(view, 'proposal')
    assert proposal['reasons'] == [{'text': '突破 20 日线', 'refs': ['call:1']}]
    assert 'thesis' not in proposal and 'invalidation' not in proposal and 'review_when' not in proposal
    assert [i['id'] for i in step(view, 'check')['items']] == [i[0] for i in reasoning.CHECKS['agent-1']]
    assert step(view, 'counter')['risks'] == counter['risks'] and step(view, 'counter')['items'] == []


# ---------------------------------------------------------------- rejected / error

def test_rejected_run_keeps_calls_only():
    view = build(run=run_row(status='rejected', reason='invalid_citation', output=None, counter=None, plan_id=None),
                 plan_state=None, version=None)
    assert kinds(view) == ['read', 'proposal', 'check', 'read', 'counter']
    assert [c['ref'] for c in step(view, 'read', 0)['calls']] == ['call:1', 'call:2']
    for kind in ('proposal', 'counter'):
        assert step(view, kind)['text'] == '这次运行作废：引用无效已作废，只保留工具调用'
    check = step(view, 'check')
    results = {i['id']: i['result'] for i in check['items']}
    assert results['refs'] == results['price_refs'] == 'implicated'
    assert {v for k, v in results.items() if k not in ('refs', 'price_refs')} == {'not_recorded'}
    assert check['note'] == '未通过的检查在这几项之中：引用都存在、每个价位至少引用一次价格类工具；校验器遇到第一处失败就停，不记录是哪一项'
    assert check['reason'] == 'invalid_citation' and check['reason_text'] == '引用无效已作废'
    # a revision adds `changes`, which can also fail on a bad citation
    revised = build(run=run_row(mode='revise', status='rejected', reason='invalid_citation', output=None, counter=None,
                                plan_id=None), plan_state=None, version=None, trigger_review=review_row())
    implicated = {i['id'] for i in step(revised, 'check')['items'] if i['result'] == 'implicated'}
    assert implicated == {'refs', 'price_refs', 'changes'}


def test_rejected_plan_invalid_revision_shows_output():
    view = build(run=run_row(mode='revise', status='rejected', reason='plan_invalid', plan_id=None), plan_state=None,
                 version=None, trigger_review=review_row())
    assert kinds(view) == ['evidence', 'read', 'proposal', 'check', 'read', 'counter']      # no plan, no response
    assert step(view, 'proposal')['levels'][0]['value'] == '98.50' and 'text' not in step(view, 'proposal')
    assert step(view, 'counter')['items']
    results = {i['id']: i['result'] for i in step(view, 'check')['items']}
    assert results['plan_status'] == 'implicated'
    assert {v for k, v in results.items() if k != 'plan_status'} == {'not_recorded'}


def test_error_and_skipped_runs_are_not_run():
    view = build(run=run_row(status='error', reason='model_error', output=None, counter=None, plan_id=None),
                 calls=[], plan_state=None, version=None)
    assert {i['result'] for i in step(view, 'check')['items']} == {'not_run'}
    assert step(view, 'check')['reason_text'] == '模型调用失败'
    assert step(view, 'read', 0)['calls'] == [] and step(view, 'read', 0)['note'] == '没有记录到工具调用'
    assert step(view, 'proposal')['text'] == '这次运行作废：模型调用失败，只保留工具调用'
    skipped = build(run=run_row(status='skipped', reason='token_budget', output=None, counter=None, plan_id=None),
                    calls=[], plan_state=None, version=None)
    assert {i['result'] for i in step(skipped, 'check')['items']} == {'not_run'}


# ---------------------------------------------------------------- response

def test_response_three_cases():
    assert step(build(), 'response')['title'] == '回应：本版之后还没有复查'
    maintain = review_row(outcome='maintain', decided_by='host', reason='条件均未成立', new_plan_id=None,
                          revision_agent_run_id=None, review_agent_run_id=None, new_version=None)
    revise = review_row(id=9, session=date(2026, 10, 27), new_plan_id='33333333-3333-4333-8333-333333333333',
                        new_version={'version_no': 1, 'diff': [{'field': 'stop', 'before': '95.00', 'after': '96.00'}]})
    view = build(later_reviews=[maintain, revise])
    response = step(view, 'response')
    assert response['title'] == '回应：本版之后的复查' and len(response['items']) == 2
    first, second = response['items']
    assert (first['review_id'], first['session'], first['outcome'], first['decided_by']) == (7, '2026-10-26', 'maintain', 'host')
    assert first['reason'] == '条件均未成立' and first['signals_fired'] == ['new_8k']
    assert first['new_plan_id'] is None and first['new_version_no'] is None and first['changes'] == []
    assert second['review_id'] == 9 and second['new_plan_id'] == '33333333-3333-4333-8333-333333333333'
    assert second['new_version_no'] == 1 and second['changes'] == [{'field': 'stop', 'before': '95.00', 'after': '96.00'}]
    assert second['trigger'] == 'batch'


# ---------------------------------------------------------------- tools, fields, notes

def test_reasoning_marks_get_concept_cutoff_exempt():
    calls = [call_row(1, 'get_concept', args={'concept_id': 'plan.stop'},
                      data={'id': 'plan.stop', 'concept_ref': 'concept:plan.stop@1', 'point_in_time': False}),
             call_row(2, 'get_price_bars', args={'ticker': 'AMD'}, data=[])]
    view = build(calls=calls)
    first, second = step(view, 'read', 0)['calls']
    assert first['cutoff_exempt'] is True and first['cutoff_note'] == EXEMPT_TEXT
    assert first['concept'] == 'system.concept_card' and first['fields'] == {'id': 'plan.stop'}
    assert first['tool_text'] == '概念卡'
    assert second['cutoff_exempt'] is False and second['cutoff_note'] is None and second['concept'] == 'family.bars'


def test_read_fields_by_value():
    calls = [call_row(1, 'get_trigger', data=[{'rule_id': 'trend_reclaim', 'action': 'buy', 'inputs': {}},
                                              {'rule_id': 'plan_stop', 'action': 'sell', 'inputs': {}},
                                              {'rule_id': 'unknown_rule', 'action': 'buy', 'inputs': {}}]),
             call_row(2, 'get_market_context', data={'label': 'neutral', 'breadth': {}, 'sectors': {},
                                                     'macro': {'BAMLH0A0HYM2': {}, 'OTHER': {}}}),
             call_row(3, 'get_position', data=None),
             call_row(4, 'get_filings', error='days 必须是整数')]
    got = {c['seq']: c for c in step(build(calls=calls), 'read', 0)['calls']}
    assert got[1]['fields'] == {'trend_reclaim': 'rule.trend_reclaim', 'plan_stop': 'rule.plan_stop'}
    assert got[1]['concept'] == 'system.decision_unit'
    assert got[2]['fields'] == {'label': 'indicator.regime', 'breadth': 'indicator.breadth',
                                'sectors': 'indicator.sector_strength', 'macro.BAMLH0A0HYM2': 'indicator.hy_oas'}
    assert got[3]['concept'] == 'review.position' and got[3]['fields'] == {}
    assert got[4]['error'] == 'days 必须是整数' and got[4]['result'] is None and got[4]['fields'] == {}


def test_notes_when_timeline_missing():
    view = build(timeline_ready=False, version=None, run=run_row(mode='revise'), trigger_review=None)
    assert NO_TIMELINE in view['notes'] and view['version'] is None and view['review'] is None
    assert 'evidence' not in kinds(view) and 'response' not in kinds(view)
    assert NO_TIMELINE not in build()['notes']


def test_consistency_error_is_shown_not_swallowed():
    state = {**PLAN_STATE, 'consistency': None, 'consistency_error': 'boom: no row'}
    view = build(plan_state=state)
    outcome = step(view, 'outcome')
    assert outcome['consistency'] is None and outcome['consistency_error'] == 'boom: no row'
    assert '推导一致性没能读出（consistency_error）：boom: no row' in view['notes']
    assert step(build(), 'outcome')['consistency_error'] is None and build()['notes'] == []


def test_unknown_run_reason_falls_back_to_code():
    view = build(run=run_row(status='rejected', reason='some_new_reason', output=None, counter=None, plan_id=None),
                 plan_state=None, version=None)
    assert view['run']['reason_text'] == 'some_new_reason'
    assert step(view, 'proposal')['text'] == '这次运行作废：some_new_reason，只保留工具调用'


# ---------------------------------------------------------------- v2.7: retro, discuss, get_knowledge

RETRO_RUN_ID = '33333333-3333-4333-8333-333333333333'
RETRO_OUTPUT = {
    'headline': '止损后没有回到入场区',
    'expected_vs_actual': [{'text': '计划止损 96，次日触及。', 'refs': ['fact:plans', 'fact:bars_after']},
                           {'text': '之后五个交易日都在入场区下方。', 'refs': ['call:1']}],
    'decision_review': {'text': '你按 Agent 第 1 版执行。', 'refs': ['fact:decision']},
    'lessons': [{'title': '回踩确认再入', 'body': '等收盘确认。', 'scope': '趋势回踩', 'counter_examples': '跳空时错过',
                 'concepts': [], 'tickers': ['AMD'], 'refs': ['fact:discipline', 'know:3@1']}]}
CARD = {'id': 7, 'ticker': 'AMD', 'end_kind': 'stop', 'end_session': date(2026, 10, 23),
        'facts': {'subject': {'kind': 'unit', 'exit_basis': None}, 'plans': [], 'discipline': {}, 'decision': {}}}
KNOW_DATA = {'query': {'ticker': 'AMD'}, 'cutoff_at': T.isoformat(), 'point_in_time': True, 'omitted': 0,
             'entries': [{'ref': 'know:3@1', 'id': 3, 'version': 1, 'title': '回踩确认再入'}], 'note': 'n'}


def retro_run(**over):
    return run_row(**{'mode': 'retro', 'agent_version': 'agent-retro-1', 'output': RETRO_OUTPUT, 'counter': None,
                      'plan_id': None, 'id': RETRO_RUN_ID, **over})


def test_build_retro_steps():
    calls = [call_row(1, 'get_price_bars', phase='retro', args={'ticker': 'AMD'}, data=[]),
             call_row(2, 'get_knowledge', phase='retro', args={'ticker': 'AMD'}, data=KNOW_DATA)]
    view = build(run=retro_run(), calls=calls, version=None, plan_state=None, card=CARD)
    assert kinds(view) == ['evidence', 'read', 'draft', 'check']
    assert view['run']['mode'] == 'retro' and view['run']['agent_version'] == 'agent-retro-1'
    assert view['version'] is None and view['notes'] == []
    evidence = step(view, 'evidence')
    assert evidence['fact_keys'] == ['subject', 'plans', 'decision', 'discipline']     # in FACT_KEYS order, as stored
    assert evidence['text'] == '止损 · 结束日 2026-10-23' and evidence['concept'] == 'review.retro_card'
    read = step(view, 'read')
    assert read['phase'] == 'retro' and [c['tool'] for c in read['calls']] == ['get_price_bars', 'get_knowledge']
    draft = step(view, 'draft')
    assert draft['available'] is True and draft['summary'] == '止损后没有回到入场区'
    assert draft['items'] == [{'text': '计划止损 96，次日触及。', 'refs': ['fact:plans', 'fact:bars_after']},
                              {'text': '之后五个交易日都在入场区下方。', 'refs': ['call:1']},
                              {'text': '你按 Agent 第 1 版执行。', 'refs': ['fact:decision']}]
    assert draft['lessons'] == [{'title': '回踩确认再入', 'body': '等收盘确认。', 'scope': '趋势回踩', 'counter_examples': '跳空时错过',
                                 'refs': ['fact:discipline', 'know:3@1']}]
    check = step(view, 'check')
    assert [(i['id'], i['result']) for i in check['items']] == [('json', 'pass'), ('refs', 'pass'), ('lessons', 'pass'),
                                                                ('tool_rounds', 'pass')]
    json.dumps(view)


def test_build_retro_without_a_card_names_all_ten_fact_keys():
    from investment_assistant.review.retro import FACT_KEYS
    view = build(run=retro_run(), calls=[], version=None, plan_state=None)
    assert step(view, 'evidence')['fact_keys'] == list(FACT_KEYS) and step(view, 'read')['note'] == '没有记录到工具调用'


@pytest.mark.parametrize('reason, implicated', [('retro_malformed', 'json'), ('invalid_citation', 'refs'),
                                                ('lesson_invalid', 'lessons'), ('tool_rounds_exceeded', 'tool_rounds')])
def test_retro_rejected_run_implicates_its_check(reason, implicated):
    view = build(run=retro_run(status='rejected', reason=reason, output=None), calls=[], version=None, plan_state=None,
                 card=CARD)
    assert kinds(view) == ['evidence', 'read', 'draft', 'check']
    assert step(view, 'draft')['available'] is False and '这次运行作废' in step(view, 'draft')['text']
    assert {i['id'] for i in step(view, 'check')['items'] if i['result'] == 'implicated'} == {implicated}
    assert step(view, 'check')['reason_text'] == REASON_TEXT[reason]


ANSWER_OUTPUT = {'answer': '止损按 2 倍 ATR 算出。', 'refs': ['call:1', 'fact:context'],
                 'knowledge_draft': {'title': '先看 ATR', 'body': '止损按 ATR 定。', 'scope': '回踩入场',
                                     'counter_examples': '跳空时失效', 'concepts': ['plan.stop'], 'tickers': ['AMD'],
                                     'refs': ['call:1'], 'target': None}}
DISCUSSION = {'id': 4, 'context_kind': 'plan', 'ticker': 'AMD', 'signal_date': date(2026, 10, 22), 'plan_id': PLAN_ID,
              'card_id': None}
TURN = {'id': 11, 'seq': 1, 'role': 'user', 'body': '止损为什么是 96？', 'run_id': RUN_ID,
        'attachment': {'kind': 'knowledge', 'id': 3, 'version': 2}}


def discuss_run(**over):
    return run_row(**{'mode': 'discuss', 'agent_version': 'agent-discuss-1', 'output': ANSWER_OUTPUT, 'counter': None,
                      'plan_id': None, **over})


def test_build_discuss_steps():
    calls = [call_row(1, 'get_derivation', phase='discuss', args={'plan_id': PLAN_ID}, data={'consistency': 'x'})]
    reply = {'id': 12, 'role': 'assistant', 'status': 'ok', 'detail': {}}
    view = build(run=discuss_run(), calls=calls, version=None, plan_state=None, discussion=DISCUSSION, turn=TURN,
                 reply=reply)
    assert kinds(view) == ['question', 'read', 'answer', 'check']
    question = step(view, 'question')
    assert question['text'] == '止损为什么是 96？'
    assert question['attachment'] == '附件（用户提供，非证据，不可引用）：知识 #3@2'
    assert f'计划 {PLAN_ID}' in question['context'] and 'AMD' in question['context']
    assert step(view, 'read')['phase'] == 'discuss' and step(view, 'read')['calls'][0]['concept'] == 'plan.derivation'
    answer = step(view, 'answer')
    assert answer['available'] is True and answer['text'] == '止损按 2 倍 ATR 算出。'
    assert answer['refs'] == ['call:1', 'fact:context']
    assert answer['knowledge_draft'] == {'title': '先看 ATR', 'body': '止损按 ATR 定。', 'scope': '回踩入场',
                                         'counter_examples': '跳空时失效'}
    assert 'note' not in answer
    assert [i['id'] for i in step(view, 'check')['items']] == ['json', 'refs', 'draft', 'tool_rounds']
    dropped = build(run=discuss_run(output={**ANSWER_OUTPUT, 'knowledge_draft': None}), calls=[], version=None,
                    plan_state=None, discussion=DISCUSSION, turn={**TURN, 'attachment': None},
                    reply={**reply, 'detail': {'draft_dropped': 'lesson_invalid'}})
    assert step(dropped, 'answer')['knowledge_draft'] is None
    assert step(dropped, 'answer')['note'] == '经验草稿没有通过校验，已丢弃：教训草稿缺适用范围、反例或挂靠无效'
    assert step(dropped, 'question')['attachment'] is None
    # the dropped draft is not a pass: the check step names it, the other checks still pass
    checks = {i['id']: i['result'] for i in step(dropped, 'check')['items']}
    assert checks == {'json': 'pass', 'refs': 'pass', 'draft': 'implicated', 'tool_rounds': 'pass'}
    assert step(dropped, 'check')['note'] == '经验草稿没有通过校验，已丢弃：教训草稿缺适用范围、反例或挂靠无效'
    assert {i['result'] for i in step(view, 'check')['items']} == {'pass'} and step(view, 'check')['note'] is None
    json.dumps([view, dropped])


def test_build_discuss_without_rows_and_when_rejected():
    view = build(run=discuss_run(status='rejected', reason='answer_malformed', output=None), calls=[], version=None,
                 plan_state=None)
    assert kinds(view) == ['question', 'read', 'answer', 'check']
    assert step(view, 'question')['text'] == '没有找到这一轮的记录'
    assert step(view, 'answer')['available'] is False
    assert [i['id'] for i in step(view, 'check')['items'] if i['result'] == 'implicated'] == ['json']
    card_ctx = build(run=discuss_run(), calls=[], version=None, plan_state=None,
                     discussion={**DISCUSSION, 'plan_id': None, 'card_id': 7})
    assert '复盘卡 #7' in step(card_ctx, 'question')['context']


def test_get_knowledge_call_note():
    calls = [call_row(1, 'get_knowledge', args={'ticker': 'AMD'}, data=KNOW_DATA),
             call_row(2, 'get_concept', data={'id': 'plan.stop'})]
    view = build(calls=calls)
    first, second = step(view, 'read', 0)['calls']
    assert first['cutoff_note'] == '我的知识按截止时间读取：只含截止时间之前写入的版本' and first['cutoff_exempt'] is False
    assert first['concept'] == 'system.my_knowledge' and first['tool_text'] == '我的知识'
    assert first['result']['entries'][0]['ref'] == 'know:3@1'            # the frontend links entries[].ref / title
    assert second['cutoff_exempt'] is True and second['cutoff_note'] == EXEMPT_TEXT


# ---------------------------------------------------------------- SQL

from test_workbench_service import NOW, SESSION, _ok_responses, agent_deps, db   # noqa: E402,F401
import investment_assistant.workflow.batch as service  # noqa: E402


def _insert_revision_chain(db, scripted_model):
    """First version through the real batch, then by hand: a review run, a revise run, plan B, version 1, the review."""
    service.run_batch(tickers=['AAA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses('AAA')))
    db.commit()
    first = analysis_repo.latest_agent_run(db, 'AAA', SESSION)
    assert first is not None and first['plan_id'] is not None
    plan_a = execution_repo.plan_by_id(db, first['plan_id'])
    assert plan_a is not None
    plan_b = execution_repo.insert_plan(db, {k: v for k, v in plan_a.items() if k not in ('id', 'created_at')})
    review_run, revise_run = str(uuid.uuid4()), str(uuid.uuid4())
    base = {'ticker': 'AAA', 'signal_date': SESSION, 'pool': 'main', 'attempt': 1, 'provider': 'chatgpt', 'model': 'm',
            'cutoff_at': NOW, 'reason': None, 'detail': None, 'counter': None, 'input_tokens': 0, 'output_tokens': 0,
            'started_at': NOW, 'finished_at': NOW}
    call = {'seq': 1, 'tool': 'get_news', 'args': {}, 'result': {'ref': 'call:1', 'data': []},
            'result_sha256': 'c' * 64, 'called_at': NOW}
    analysis_repo.insert_agent_run(db, {**base, 'id': review_run, 'mode': 'review', 'agent_version': 'agent-review-1',
                                     'status': 'ok', 'plan_id': None, 'output': VERDICT},
                                [{**call, 'phase': 'review'}])
    analysis_repo.insert_agent_run(db, {**base, 'id': revise_run, 'mode': 'revise', 'agent_version': 'agent-2',
                                     'status': 'ok', 'plan_id': plan_b['id'], 'output': first['output'],
                                     'counter': first['counter']}, [{**call, 'phase': 'propose'}])
    execution_repo.insert_plan_version(db, {
        'plan_id': plan_b['id'], 'ticker': 'AAA', 'signal_date': SESSION, 'version_no': 1,
        'supersedes_plan_id': plan_a['id'], 'origin': 'review', 'agent_run_id': revise_run, 'cutoff_at': NOW,
        'as_of_session': SESSION, 'diff': [{'field': 'stop', 'before': '95.00', 'after': '96.00'}],
        'schema_version': 'agent-2'})
    review = execution_repo.insert_plan_review(db, {
        'ticker': 'AAA', 'signal_date': SESSION, 'head_plan_id': plan_a['id'], 'session': SESSION, 'trigger': 'batch',
        'run_id': 'run-x', 'mode': 'entry', 'timeline_version': 'timeline-1',
        'signals': [{'kind': 'new_8k', 'fired': True, 'detail': 'x'}], 'conditions': [], 'user_input_ids': [],
        'outcome': 'revise', 'decided_by': 'agent', 'reason': '上调止损', 'refs': ['call:1'],
        'review_agent_run_id': review_run, 'revision_agent_run_id': revise_run, 'new_plan_id': plan_b['id'],
        'detail': {}, 'attempt': 1, 'cutoff_at': NOW})
    db.commit()
    return first, plan_a, plan_b, review_run, revise_run, review


def test_reasoning_reads_version_and_reviews(db, scripted_model):
    first, plan_a, plan_b, review_run, revise_run, review = _insert_revision_chain(db, scripted_model)
    before = db.execute('SELECT (SELECT count(*) FROM agent_runs), (SELECT count(*) FROM plan_reviews)').fetchone()

    view = reasoning.reasoning(str(first['id']))
    assert view['run']['id'] == str(first['id']) and view['run']['mode'] == 'propose'
    assert view['version']['version_no'] == 0 and view['version']['plan_id'] == str(plan_a['id'])
    assert kinds(view) == ['previous_chain', 'research', 'read', 'read', 'proposal', 'check', 'read',
                           'counter', 'response', 'outcome']   # agent-4: researcher precedes the proposer
    [item] = step(view, 'response')['items']
    assert item['new_version_no'] == 1 and item['new_plan_id'] == str(plan_b['id'])
    assert item['outcome'] == 'revise' and item['changes'] == [{'field': 'stop', 'before': '95.00', 'after': '96.00'}]
    outcome = step(view, 'outcome')
    assert outcome['plan_id'] == str(plan_a['id']) and outcome['consistency']['status'] == 'consistent'
    read = step(view, 'read', 1)          # read #0 is the researcher's (agent-4)
    assert [c['tool'] for c in read['calls']] == ['get_indicators']
    assert read['calls'][0]['concept'] == 'indicator.atr14'

    revised = reasoning.reasoning(revise_run)
    assert kinds(revised) == ['evidence', 'research', 'read', 'read', 'proposal', 'check', 'read',
                             'counter', 'response', 'outcome']   # the revision carries the researcher segment
    assert step(revised, 'research')['status'] in ('ok', 'carried')
    assert step(revised, 'read', 0)['calls'] == []               # no researcher calls of THIS run (R16)
    evidence = step(revised, 'evidence')
    assert evidence['review_id'] == review['id'] and evidence['review_agent_run_id'] == review_run
    assert evidence['signals'][0]['kind'] == 'new_8k'
    assert revised['version']['version_no'] == 1 and revised['version']['origin'] == 'review'
    assert step(revised, 'response')['items'] == []

    single = reasoning.reasoning(review_run)
    assert kinds(single) == ['evidence', 'read', 'verdict', 'check']
    assert step(single, 'evidence')['revision_agent_run_id'] == revise_run
    assert single['review']['id'] == review['id']
    json.dumps([view, revised, single], default=str)

    after = db.execute('SELECT (SELECT count(*) FROM agent_runs), (SELECT count(*) FROM plan_reviews)').fetchone()
    assert before == after


def test_reasoning_shows_the_knowledge_a_run_cited(db, scripted_model):
    """Spec §6.2: the plan card and the reasoning view both show the cited knowledge; same item shape."""
    from datetime import timedelta
    from test_workbench_repository_growth import item_with_versions
    first, plan_a, plan_b, review_run, revise_run, review = _insert_revision_chain(db, scripted_model)
    old = NOW - timedelta(days=30)
    item_id = item_with_versions(db, [(old, ['AAA'], []), (old, ['AAA'], [])])          # two versions, titles t1 / t2
    plain_id = item_with_versions(db, [(old, ['AAA'], [])])
    analysis_repo.insert_knowledge_citations(db, [
        {'agent_run_id': first['id'], 'plan_id': plan_a['id'], 'knowledge_id': item_id, 'version': 1, 'field': 'thesis',
         'call_seq': 1},
        {'agent_run_id': first['id'], 'plan_id': plan_a['id'], 'knowledge_id': item_id, 'version': 1, 'field': 'risks',
         'call_seq': 1},
        {'agent_run_id': uuid.UUID(revise_run), 'plan_id': plan_b['id'], 'knowledge_id': plain_id, 'version': 1,
         'field': 'changes', 'call_seq': 1},
        {'agent_run_id': uuid.UUID(review_run), 'review_id': review['id'], 'knowledge_id': item_id, 'version': 2,
         'field': 'review', 'call_seq': 1}])
    db.commit()
    entry = {'ref': f'know:{item_id}@1', 'title': 't1', 'fields': ['thesis', 'risks'], 'current_version': 2}
    assert reasoning.reasoning(str(first['id']))['knowledge_refs'] == [entry]
    assert reasoning.reasoning(revise_run)['knowledge_refs'] == [
        {'ref': f'know:{plain_id}@1', 'title': 't1', 'fields': ['changes'], 'current_version': 1}]
    assert reasoning.reasoning(review_run)['knowledge_refs'] == [
        {'ref': f'know:{item_id}@2', 'title': 't2', 'fields': ['review'], 'current_version': 2}]
    db.execute('DELETE FROM knowledge_citations')
    db.commit()
    assert reasoning.reasoning(str(first['id']))['knowledge_refs'] == []        # nothing cited: an empty list, not a gap


def test_reasoning_without_timeline_tables(db, scripted_model):
    service.run_batch(tickers=['AAA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses('AAA')))
    db.commit()
    run = analysis_repo.latest_agent_run(db, 'AAA', SESSION)
    assert run is not None
    db.execute('DROP TABLE plan_reviews, user_inputs, agent_plan_versions CASCADE')
    db.commit()
    view = reasoning.reasoning(str(run['id']))
    assert NO_TIMELINE in view['notes'] and view['version'] is None
    assert 'response' not in kinds(view) and 'proposal' in kinds(view)


def test_reasoning_400_404(db):
    with pytest.raises(ValueError):
        reasoning.reasoning('not-a-uuid')
    with pytest.raises(LookupError):
        reasoning.reasoning(str(uuid.uuid4()))


def test_reasoning_derivation_failure_reports_reason(db, scripted_model, monkeypatch):
    from investment_assistant.derivation import chain as derivation
    service.run_batch(tickers=['AAA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses('AAA')))
    db.commit()
    run = analysis_repo.latest_agent_run(db, 'AAA', SESSION)
    assert run is not None

    def broken(plan, rows):
        raise RuntimeError('derive exploded\nsecond line')
    monkeypatch.setattr(derivation, 'derive_plan', broken)
    view = reasoning.reasoning(str(run['id']))
    outcome = step(view, 'outcome')
    assert outcome['consistency'] is None and outcome['consistency_error'] == 'derive exploded'
    assert any('derive exploded' in n for n in view['notes'])
    assert 'proposal' in kinds(view)


def test_reasoning_reads_retro_and_discuss_context(db, scripted_model):
    from test_workbench_repository_growth import card as make_card
    service.run_batch(tickers=['AAA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses('AAA')))
    db.commit()
    first = analysis_repo.latest_agent_run(db, 'AAA', SESSION)
    assert first is not None and first['plan_id'] is not None
    plan = execution_repo.plan_by_id(db, first['plan_id'])
    assert plan is not None
    base = {'ticker': 'AAA', 'signal_date': SESSION, 'pool': 'main', 'attempt': 1, 'provider': 'chatgpt', 'model': 'm',
            'cutoff_at': NOW, 'reason': None, 'detail': None, 'counter': None, 'input_tokens': 0, 'output_tokens': 0,
            'started_at': NOW, 'finished_at': NOW, 'plan_id': None, 'status': 'ok'}
    know_call = {'seq': 1, 'tool': 'get_knowledge', 'args': {}, 'result': {'ref': 'call:1', 'data': KNOW_DATA},
                 'result_sha256': 'c' * 64, 'called_at': NOW}
    retro_id, discuss_id = str(uuid.uuid4()), str(uuid.uuid4())
    analysis_repo.insert_agent_run(db, {**base, 'id': retro_id, 'mode': 'retro', 'agent_version': 'agent-retro-1',
                                     'output': RETRO_OUTPUT}, [{**know_call, 'phase': 'retro'}])
    analysis_repo.insert_agent_run(db, {**base, 'id': discuss_id, 'mode': 'discuss', 'agent_version': 'agent-discuss-1',
                                     'output': ANSWER_OUTPUT}, [{**know_call, 'phase': 'discuss'}])
    cid = make_card(db, key='unit:AAA:ended', ticker='AAA', signal_date=SESSION, end_kind='stop',
                    end_session=SESSION, facts={'subject': {'kind': 'unit', 'exit_basis': None}, 'plans': []})
    review_repo.insert_card_draft(db, {'card_id': cid, 'attempt': 1, 'trigger': 'batch', 'agent_run_id': retro_id,
                                      'status': 'ok', 'reason': None, 'output': RETRO_OUTPUT})
    discussion = review_repo.insert_discussion(db, {'request_id': uuid.uuid4(), 'request_hash': 'h',
                                                   'context_kind': 'plan', 'ticker': 'AAA', 'signal_date': SESSION,
                                                   'plan_id': plan['id'], 'card_id': None})
    user = review_repo.insert_turn(db, {'discussion_id': discussion['id'], 'seq': 1, 'role': 'user',
                                       'request_id': uuid.uuid4(), 'request_hash': 'h', 'body': '为什么？',
                                       'run_id': discuss_id, 'attachment': {'kind': 'draft', 'id': 5}})
    review_repo.insert_turn(db, {'discussion_id': discussion['id'], 'seq': 2, 'role': 'assistant', 'body': '因为…',
                                'reply_to': user['id'], 'status': 'ok', 'cutoff_at': NOW, 'agent_run_id': discuss_id,
                                'detail': {'draft_dropped': 'invalid_citation'}})
    db.commit()
    before = db.execute('SELECT (SELECT count(*) FROM agent_runs), (SELECT count(*) FROM discussion_turns)').fetchone()

    retro_view = reasoning.reasoning(retro_id)
    assert kinds(retro_view) == ['evidence', 'read', 'draft', 'check']
    assert step(retro_view, 'evidence')['text'] == '止损 · 结束日 2026-10-22'
    assert step(retro_view, 'evidence')['fact_keys'] == ['subject', 'plans']
    assert step(retro_view, 'read')['calls'][0]['tool'] == 'get_knowledge'
    discuss_view = reasoning.reasoning(discuss_id)
    assert kinds(discuss_view) == ['question', 'read', 'answer', 'check']
    question = step(discuss_view, 'question')
    assert question['text'] == '为什么？' and question['attachment'] == '附件（用户提供，非证据，不可引用）：草稿 #5'
    assert f"计划 {plan['id']}" in question['context']
    assert step(discuss_view, 'answer')['note'] == '经验草稿没有通过校验，已丢弃：引用无效已作废'
    json.dumps([retro_view, discuss_view], default=str)
    assert db.execute('SELECT (SELECT count(*) FROM agent_runs), (SELECT count(*) FROM discussion_turns)').fetchone() == before

    # without migration 027 the extra context is simply absent
    db.execute('DROP TABLE knowledge_citations, knowledge_draft_verdicts, review_card_verdicts, knowledge_drafts, '
               'discussion_turns, discussions, review_card_drafts, review_cards, knowledge_status, '
               'knowledge_versions, knowledge_items CASCADE')
    db.commit()
    bare = reasoning.reasoning(retro_id)
    assert kinds(bare) == ['evidence', 'read', 'draft', 'check'] and len(step(bare, 'evidence')['fact_keys']) == 10
    assert step(reasoning.reasoning(discuss_id), 'question')['text'] == '没有找到这一轮的记录'
