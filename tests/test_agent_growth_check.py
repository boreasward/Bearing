"""Retro draft, lesson and discussion answer checks, and the knowledge citation extraction (v2.7 spec §4.4, §6.2,
§8.4). Pure functions: no database, no network."""
import copy
import json

import pytest

from investment_assistant.review import agent_runs as growth_check
from investment_assistant.core import knowledge
from investment_assistant.execution.citations import Rejected
from investment_assistant.execution.plan_check import filter_counter_v2
from investment_assistant.review.retro import FACT_KEYS

REFS = {'call:1', 'call:2', 'concept:plan.stop@1', 'know:3@2'}
CONCEPTS = {'plan.stop', 'review.discipline_early_take'}
FACTS = {f'fact:{key}' for key in FACT_KEYS}


def lesson(**over):
    base = {'title': '回踩确认再入', 'body': '等收盘确认再追。', 'scope': '趋势回踩的入场', 'counter_examples': '放量跳空时会错过',
            'concepts': ['review.discipline_early_take'], 'tickers': ['AAPL'], 'refs': ['fact:discipline', 'call:2']}
    return {**base, **over}


def draft(**over):
    base = {'headline': '止损后未再回到入场区',
            'expected_vs_actual': [{'text': '计划止损 96，次日触及。', 'refs': ['fact:plans', 'fact:bars_after']}],
            'decision_review': {'text': '你按 Agent 第 1 版执行。', 'refs': ['fact:decision', 'fact:discipline']},
            'lessons': [lesson()]}
    return {**base, **over}


def check(payload, **kwargs):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return growth_check.check_draft(text, refs=kwargs.get('refs', REFS), concept_ids=kwargs.get('concept_ids', CONCEPTS),
                                    default_ticker='AAPL')


def reason(payload, **kwargs):
    with pytest.raises(Rejected) as caught:
        check(payload, **kwargs)
    return caught.value.reason


def test_check_draft_ok():
    out = check(draft())
    assert out == draft()                                         # nothing is rewritten when it is valid
    assert out['lessons'][0]['concepts'] == ['review.discipline_early_take']
    fenced = "```json\n" + json.dumps(draft(lessons=[]), ensure_ascii=False) + "\n```"
    assert check(fenced)['lessons'] == []                         # a code fence is stripped, no lessons is fine
    no_key = {k: v for k, v in draft().items() if k != 'lessons'}
    assert check(no_key)['lessons'] == []
    for key in (*FACT_KEYS,):
        assert check(draft(expected_vs_actual=[{'text': 'x', 'refs': [f'fact:{key}']}]))


def test_check_draft_allows_only_the_cards_own_fact_keys():
    """M5: a retro-1 draft cannot cite fact:execution / fact:holding, a retro-2 draft cannot cite fact:decision."""
    from investment_assistant.review.retro import FACT_KEYS_V2

    def with_keys(key, keys):
        refs = [f'fact:{key}']
        text = json.dumps(draft(expected_vs_actual=[{'text': 'x', 'refs': refs}],
                                decision_review={'text': 'x', 'refs': refs}, lessons=[]), ensure_ascii=False)
        return growth_check.check_draft(text, refs=REFS, concept_ids=CONCEPTS, default_ticker='AAPL', fact_keys=keys)
    assert with_keys('decision', FACT_KEYS)
    assert with_keys('execution', FACT_KEYS_V2) and with_keys('holding', FACT_KEYS_V2)
    for key, keys in (('execution', FACT_KEYS), ('holding', FACT_KEYS), ('decision', FACT_KEYS_V2)):
        with pytest.raises(Rejected):
            with_keys(key, keys)


def with_(mutate):
    payload = copy.deepcopy(draft())
    mutate(payload)
    return payload


SHAPE_CASES = [
    ('not_json', 'not json at all', 'retro_malformed'),
    ('not_object', '[1, 2]', 'retro_malformed'),
    ('headline_60', with_(lambda p: p.update(headline='字' * 60)), None),
    ('headline_61', with_(lambda p: p.update(headline='字' * 61)), 'retro_malformed'),
    ('headline_blank', with_(lambda p: p.update(headline='   ')), 'retro_malformed'),
    ('headline_number', with_(lambda p: p.update(headline=3)), 'retro_malformed'),
    ('narrative_0', with_(lambda p: p.update(expected_vs_actual=[])), 'retro_malformed'),
    ('narrative_4', with_(lambda p: p.update(expected_vs_actual=[{'text': 'x', 'refs': ['fact:plans']}] * 4)), None),
    ('narrative_5', with_(lambda p: p.update(expected_vs_actual=[{'text': 'x', 'refs': ['fact:plans']}] * 5)),
     'retro_malformed'),
    ('narrative_text_300', with_(lambda p: p['expected_vs_actual'][0].update(text='字' * 300)), None),
    ('narrative_text_301', with_(lambda p: p['expected_vs_actual'][0].update(text='字' * 301)), 'retro_malformed'),
    ('narrative_text_blank', with_(lambda p: p['expected_vs_actual'][0].update(text='')), 'retro_malformed'),
    ('narrative_refs_not_list', with_(lambda p: p['expected_vs_actual'][0].update(refs='fact:plans')),
     'retro_malformed'),
    ('review_missing', with_(lambda p: p.pop('decision_review')), 'retro_malformed'),
    ('review_text_301', with_(lambda p: p['decision_review'].update(text='字' * 301)), 'retro_malformed'),
    ('lessons_3', with_(lambda p: p.update(lessons=[lesson()] * 3)), None),
    ('lessons_4', with_(lambda p: p.update(lessons=[lesson()] * 4)), 'retro_malformed'),
    ('lessons_not_list', with_(lambda p: p.update(lessons={'a': 1})), 'retro_malformed'),
    ('lesson_not_object', with_(lambda p: p.update(lessons=['教训'])), 'retro_malformed'),
    ('title_60', with_(lambda p: p['lessons'][0].update(title='字' * 60)), None),
    ('title_61', with_(lambda p: p['lessons'][0].update(title='字' * 61)), 'retro_malformed'),
    ('title_blank', with_(lambda p: p['lessons'][0].update(title=' ')), 'retro_malformed'),
    ('body_1500', with_(lambda p: p['lessons'][0].update(body='字' * 1500)), None),
    ('body_1501', with_(lambda p: p['lessons'][0].update(body='字' * 1501)), 'retro_malformed'),
    ('scope_300', with_(lambda p: p['lessons'][0].update(scope='字' * 300)), None),
    ('scope_301', with_(lambda p: p['lessons'][0].update(scope='字' * 301)), 'retro_malformed'),
    ('counter_500', with_(lambda p: p['lessons'][0].update(counter_examples='字' * 500)), None),
    ('counter_501', with_(lambda p: p['lessons'][0].update(counter_examples='字' * 501)), 'retro_malformed'),
    ('concepts_not_list', with_(lambda p: p['lessons'][0].update(concepts='plan.stop')), 'retro_malformed'),
    ('lesson_refs_missing', with_(lambda p: p['lessons'][0].pop('refs')), 'retro_malformed'),
]


@pytest.mark.parametrize('payload, expected', [(c[1], c[2]) for c in SHAPE_CASES], ids=[c[0] for c in SHAPE_CASES])
def test_draft_shape_limits(payload, expected):
    if expected is None:
        assert check(payload)
    else:
        assert reason(payload) == expected


def test_shape_beats_citations_beats_lessons():
    bad_all = draft(headline='', lessons=[lesson(scope='', refs=['bogus'])])
    assert reason(bad_all) == 'retro_malformed'
    bad_refs_and_lesson = draft(lessons=[lesson(scope='', refs=['call:99'])])
    assert reason(bad_refs_and_lesson) == 'invalid_citation'
    assert reason(draft(lessons=[lesson(scope='')])) == 'lesson_invalid'


@pytest.mark.parametrize('ref', ['fact:bogus', 'fact:context', 'call:99', 'know:9@1', 'know:3@1', 'concept:plan.nope@1',
                                 'concept:plan.stop@2', 'user:1', 'fact:', ''])
def test_draft_citation_sources_rejected(ref):
    assert reason(draft(expected_vs_actual=[{'text': 'x', 'refs': [ref]}])) == 'invalid_citation'
    assert reason(draft(decision_review={'text': 'x', 'refs': ['fact:decision', ref]})) == 'invalid_citation'
    assert reason(draft(lessons=[lesson(refs=[ref])])) == 'invalid_citation'


@pytest.mark.parametrize('ref', [f'fact:{key}' for key in FACT_KEYS] + ['call:1', 'call:2', 'concept:plan.stop@1',
                                                                      'know:3@2'])
def test_draft_citation_sources_accepted(ref):
    assert check(draft(expected_vs_actual=[{'text': 'x', 'refs': [ref]}]))
    assert check(draft(lessons=[lesson(refs=[ref])]))


def test_draft_refs_must_be_non_empty():
    assert reason(draft(expected_vs_actual=[{'text': 'x', 'refs': []}])) == 'invalid_citation'
    assert reason(draft(decision_review={'text': 'x', 'refs': []})) == 'invalid_citation'
    assert reason(draft(lessons=[lesson(refs=[])])) == 'invalid_citation'
    assert reason(draft(expected_vs_actual=[{'text': 'x', 'refs': [3]}])) == 'retro_malformed'


def test_draft_refs_use_the_caller_set_only():
    assert reason(draft(expected_vs_actual=[{'text': 'x', 'refs': ['call:1']}]), refs=set()) == 'invalid_citation'
    assert check(draft(expected_vs_actual=[{'text': 'x', 'refs': ['call:1']}]), refs={'call:1', 'call:2'})


@pytest.mark.parametrize('key', ['scope', 'counter_examples'])
def test_lesson_requires_scope_and_counter(key):
    assert reason(draft(lessons=[lesson(**{key: ''})])) == 'lesson_invalid'
    assert reason(draft(lessons=[lesson(**{key: '  \n '})])) == 'lesson_invalid'
    assert reason(draft(lessons=[lesson(**{key: None})])) == 'lesson_invalid'
    broken = lesson()
    broken.pop(key)
    assert reason(draft(lessons=[broken])) == 'lesson_invalid'
    assert reason(draft(lessons=[lesson(), lesson(**{key: ''})])) == 'lesson_invalid'


@pytest.mark.parametrize('over', [
    {'concepts': ['plan.nope']}, {'concepts': ['concept:plan.nope@1']}, {'concepts': ['not an id']},
    {'concepts': [3]}, {'tickers': ['aapl!']}, {'tickers': ['TOOLONGTICKER1']}, {'tickers': [5]},
    {'concepts': ['plan.stop'] * 1, 'tickers': ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J']},   # 11 together
])
def test_lesson_invalid_links(over):
    assert reason(draft(lessons=[lesson(**over)])) == 'lesson_invalid'


def test_lesson_links_normalised():
    out = check(draft(lessons=[lesson(concepts=['concept:plan.stop@3', 'plan.stop', 'review.discipline_early_take'],
                                      tickers=['aapl', 'AAPL', 'msft'])]))
    [item] = out['lessons']
    assert item['concepts'] == ['plan.stop', 'review.discipline_early_take']          # ids, deduplicated
    assert item['tickers'] == ['AAPL', 'MSFT']
    ten = check(draft(lessons=[lesson(concepts=['plan.stop'], tickers=list('ABCDEFGHI'))]))      # exactly 10: fine
    assert len(ten['lessons'][0]['concepts']) + len(ten['lessons'][0]['tickers']) == 10


def test_lesson_links_default_ticker():
    [item] = check(draft(lessons=[lesson(concepts=[], tickers=[])]))['lessons']
    assert item['concepts'] == [] and item['tickers'] == ['AAPL']                      # the card's ticker
    [absent] = check(draft(lessons=[{k: v for k, v in lesson().items() if k not in ('concepts', 'tickers')}]))['lessons']
    assert absent['tickers'] == ['AAPL']
    [kept] = check(draft(lessons=[lesson(concepts=['plan.stop'], tickers=[])]))['lessons']
    assert kept['tickers'] == []                                                       # only when both are empty


def test_check_lesson_directly():
    ok = growth_check.check_lesson(lesson(), refs=REFS | {'fact:discipline'}, concept_ids=CONCEPTS,
                                   default_ticker='AAPL')
    assert ok == lesson()
    with pytest.raises(Rejected) as caught:                   # a lesson alone has no implicit fact: keys
        growth_check.check_lesson(lesson(refs=['fact:discipline']), refs=REFS, concept_ids=CONCEPTS,
                                  default_ticker='AAPL')
    assert caught.value.reason == 'invalid_citation'


# ---------------------------------------------------------------- discussion answers

def answer(**over):
    base = {'answer': '止损价来自 2 倍 ATR。', 'refs': ['call:1', 'concept:plan.stop@1', 'fact:context'],
            'knowledge_draft': None}
    return {**base, **over}


def knowledge_draft(**over):
    base = {'title': '先看 ATR', 'body': '止损按 ATR 定。', 'scope': '回踩入场', 'counter_examples': '跳空时失效',
            'concepts': ['plan.stop'], 'tickers': ['AAPL'], 'refs': ['call:1'], 'target': None}
    return {**base, **over}


def check_a(payload, *, refs=REFS | {'user:12'}, targets=frozenset({'know:3@2'})):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return growth_check.check_answer(text, refs=set(refs), concept_ids=CONCEPTS, default_ticker='AAPL',
                                     targets=set(targets))


def reason_a(payload, **kwargs):
    with pytest.raises(Rejected) as caught:
        check_a(payload, **kwargs)
    return caught.value.reason


def test_check_answer_ok():
    out, dropped = check_a(answer())
    assert out == answer() and dropped is None
    out, dropped = check_a(answer(refs=['user:12', 'know:3@2', 'fact:context']))
    assert dropped is None and out['refs'] == ['user:12', 'know:3@2', 'fact:context']
    out, dropped = check_a(answer(knowledge_draft=knowledge_draft()))
    assert dropped is None and out['knowledge_draft']['title'] == '先看 ATR' and out['knowledge_draft']['target'] is None
    assert out['knowledge_draft']['concepts'] == ['plan.stop'] and out['knowledge_draft']['tickers'] == ['AAPL']


@pytest.mark.parametrize('payload', ['not json', '[]', {'refs': ['call:1']}, answer(answer=''), answer(answer=' '),
                                     answer(answer='字' * 3001), answer(answer=5), {'answer': 'x'},
                                     answer(refs='call:1'), answer(refs=[1])])
def test_check_answer_malformed(payload):
    assert reason_a(payload) == 'answer_malformed'


def test_answer_length_boundary():
    assert check_a(answer(answer='字' * 3000))[0]['answer'] == '字' * 3000


@pytest.mark.parametrize('refs', [[], ['call:9'], ['know:9@1'], ['concept:plan.nope@1'], ['user:99'], ['fact:plans'],
                                  ['call:1', 'bogus']])
def test_check_answer_rejects_without_refs(refs):
    assert reason_a(answer(refs=refs)) == 'invalid_citation'


def test_check_answer_context_ref_always_allowed():
    assert check_a(answer(refs=['fact:context']), refs=set())[0]['refs'] == ['fact:context']


def test_answer_draft_dropped_keeps_answer():
    cases = {
        'lesson_invalid': [knowledge_draft(scope=''), knowledge_draft(counter_examples=''),
                           knowledge_draft(concepts=['plan.nope']), 'not an object',
                           knowledge_draft(title=''), knowledge_draft(body='字' * 1501)],
        'invalid_citation': [knowledge_draft(refs=[]), knowledge_draft(refs=['call:99'])],
    }
    for expected, drafts in cases.items():
        for bad in drafts:
            out, dropped = check_a(answer(knowledge_draft=bad))
            assert dropped == expected, bad
            assert out['knowledge_draft'] is None and out['answer'] == answer()['answer']
            assert out['refs'] == answer()['refs']


def test_answer_target_must_be_known():
    ok, dropped = check_a(answer(knowledge_draft=knowledge_draft(target='know:3@2')))
    assert dropped is None and ok['knowledge_draft']['target'] == 'know:3@2'
    for target in ('know:9@1', 'know:3', 'know:3@1', 'plan.stop', 5, 'know:0@1'):
        out, dropped = check_a(answer(knowledge_draft=knowledge_draft(target=target)))
        assert dropped == 'lesson_invalid' and out['knowledge_draft'] is None, target
    out, dropped = check_a(answer(knowledge_draft=knowledge_draft(target='know:9@1')), targets={'know:9@1'})
    assert dropped is None and out['knowledge_draft']['target'] == 'know:9@1'


def test_answer_draft_default_ticker():
    out, _ = check_a(answer(knowledge_draft=knowledge_draft(concepts=[], tickers=[])))
    assert out['knowledge_draft']['tickers'] == ['AAPL']


# ---------------------------------------------------------------- knowledge citations

def know_call(seq, *refs, phase='propose', error=None):
    result = {'ref': f'call:{seq}'}
    if error:
        result['error'] = error
    else:
        result['data'] = {'entries': [{'ref': r} for r in refs]}
    return {'seq': seq, 'phase': phase, 'tool': 'get_knowledge', 'args': {}, 'result': result}


def other_call(seq, tool='get_price_bars'):
    return {'seq': seq, 'phase': 'propose', 'tool': tool, 'args': {}, 'result': {'ref': f'call:{seq}', 'data': []}}


def rows(**kwargs):
    return sorted(knowledge.knowledge_citations(**kwargs), key=lambda r: (r['field'], r['knowledge_id'], r['version']))


def test_knowledge_citations_fields():
    calls = [other_call(1), know_call(2, 'know:3@2', 'know:4@1'), know_call(3, 'know:3@2', 'know:5@7', phase='counter')]
    output = {'thesis': [{'id': 'T1', 'text': 'a', 'refs': ['call:1', 'know:3@2']},
                         {'id': 'T2', 'text': 'b', 'refs': ['know:4@1']}],
              'invalidation': [{'kind': 'close_below', 'params': {}, 'text': 'x', 'refs': ['know:4@1', 'call:1']}],
              'changes': [{'field': 'stop', 'text': 'y', 'refs': ['know:3@2']}],
              'levels': {'stop': {'value': '1', 'refs': ['call:1', 'know:3@2']}}}
    counter = {'counter': [{'thesis': 1, 'text': 'c', 'refs': ['know:5@7']}],
               'risks': [{'text': 'r', 'refs': ['know:3@2', 'call:1']}]}
    got = rows(output=output, counter=counter, mode='revise', calls=calls)
    assert got == [
        {'knowledge_id': 3, 'version': 2, 'field': 'changes', 'call_seq': 2},
        {'knowledge_id': 5, 'version': 7, 'field': 'counter', 'call_seq': 3},
        {'knowledge_id': 4, 'version': 1, 'field': 'invalidation', 'call_seq': 2},
        {'knowledge_id': 3, 'version': 2, 'field': 'risks', 'call_seq': 2},      # first call that returned it
        {'knowledge_id': 3, 'version': 2, 'field': 'thesis', 'call_seq': 2},
        {'knowledge_id': 4, 'version': 1, 'field': 'thesis', 'call_seq': 2},
    ]
    assert {r['field'] for r in got} <= set(growth_check.FIELDS)
    # the first version has no changes; the same ref cited twice in one field is one row
    twice = {'thesis': [{'text': 'a', 'refs': ['know:3@2']}, {'text': 'b', 'refs': ['know:3@2', 'know:3@2']}]}
    assert rows(output=twice, counter=None, mode='propose', calls=calls) == [
        {'knowledge_id': 3, 'version': 2, 'field': 'thesis', 'call_seq': 2}]


def test_knowledge_citations_review():
    calls = [know_call(1, 'know:3@2', phase='review')]
    out = rows(output={'outcome': 'maintain', 'reason': 'ok', 'refs': ['know:3@2', 'call:1']}, counter=None,
               mode='review', calls=calls)
    assert out == [{'knowledge_id': 3, 'version': 2, 'field': 'review', 'call_seq': 1}]
    assert rows(output={'outcome': 'maintain', 'reason': 'ok', 'refs': []}, counter=None, mode='review',
                calls=calls) == []


def test_knowledge_citations_ignore_what_no_call_returned():
    calls = [know_call(1, 'know:3@2'), know_call(2, error='我的知识不可用：迁移 027 未执行'), other_call(3)]
    output = {'thesis': [{'text': 'a', 'refs': ['know:9@1', 'know:3', 'know:x@1', 7, 'call:3', 'know:3@2']}]}
    assert rows(output=output, counter=None, mode='propose', calls=calls) == [
        {'knowledge_id': 3, 'version': 2, 'field': 'thesis', 'call_seq': 1}]
    assert rows(output=None, counter=None, mode='propose', calls=calls) == []
    assert rows(output={'thesis': []}, counter={}, mode='propose', calls=[]) == []


def test_dropped_counter_not_cited():
    calls = [know_call(1, 'know:3@2'), know_call(2, 'know:4@1', phase='counter')]
    plan = {'thesis': [{'id': 'T1', 'text': 'a', 'refs': ['know:3@2']}]}
    text = json.dumps({'counter': [
        {'thesis': 1, 'text': 'kept', 'refs': ['know:4@1']},
        {'thesis': 1, 'text': 'dropped: cites a ref nobody returned', 'refs': ['know:9@9', 'know:3@2']},
        {'thesis': 7, 'text': 'dropped: no such thesis', 'refs': ['know:3@2']}],
        'risks': [{'text': 'dropped risk', 'refs': ['know:8@1']}, {'text': 'kept risk', 'refs': ['know:3@2']}]},
        ensure_ascii=False)
    counter = filter_counter_v2(text, refs={'call:1', 'call:2', 'know:3@2', 'know:4@1'}, thesis_count=1)
    assert counter['dropped'] == 3 and len(counter['counter']) == 1
    got = rows(output=plan, counter=counter, mode='propose', calls=calls)
    assert got == [
        {'knowledge_id': 4, 'version': 1, 'field': 'counter', 'call_seq': 2},
        {'knowledge_id': 3, 'version': 2, 'field': 'risks', 'call_seq': 1},
        {'knowledge_id': 3, 'version': 2, 'field': 'thesis', 'call_seq': 1},
    ]


def test_concept_refs():
    calls = [{'seq': 1, 'phase': 'retro', 'tool': 'get_concept', 'args': {}, 'result': {'ref': 'call:1', 'data': {
                 'concept_ref': 'concept:plan.stop@1'}}},
             {'seq': 2, 'phase': 'retro', 'tool': 'get_concept', 'args': {}, 'result': {'ref': 'call:2', 'data': {
                 'concepts': []}}},                                                   # the index: no card ref
             {'seq': 3, 'phase': 'retro', 'tool': 'get_concept', 'args': {}, 'result': {'ref': 'call:3', 'error': 'x'}},
             {'seq': 4, 'phase': 'counter', 'tool': 'get_concept', 'args': {}, 'result': {'ref': 'call:4', 'data': {
                 'concept_ref': 'concept:plan.target1@2'}}},
             other_call(5)]
    assert growth_check.concept_refs(calls) == {'concept:plan.stop@1', 'concept:plan.target1@2'}
    assert growth_check.concept_refs(calls, 'retro') == {'concept:plan.stop@1'}


def test_raw_newlines_inside_strings_are_accepted():
    # the model writes multi-paragraph answers with literal line breaks inside JSON strings (seen in the v2.7
    # acceptance on the real model); strict json.loads rejects them, so both checks read the text leniently
    multi = '第一段。\n\n第二段：\t缩进。'
    text = json.dumps(answer(answer=multi), ensure_ascii=False).replace('\\n', '\n').replace('\\t', '\t')
    assert '\n' in text
    out, dropped = check_a(text)
    assert out['answer'] == multi and dropped is None
    fenced = '```json\n' + text + '\n```'
    assert check_a(fenced)[0]['answer'] == multi
    sentence = '计划止损 96。\n次日触及。'
    raw = json.dumps(draft(expected_vs_actual=[{'text': sentence, 'refs': ['fact:plans']}]),
                     ensure_ascii=False).replace('\\n', '\n')
    assert check(raw)['expected_vs_actual'][0]['text'] == sentence
    assert reason_a('{"answer": "x" "refs": []}') == 'answer_malformed'     # still not JSON → still rejected
