import copy
import json

import pytest

from investment_assistant.analysis.agent.research_check import Rejected, check_research, research_fact_block


REFS = frozenset({'call:1', 'call:2', 'call:3'})


def research():
    return {
        'facts': [{'text': '披露营收增长 8%。', 'kind': 'disclosure', 'refs': ['call:1']}],
        'risks': [{'text': '客户集中。', 'refs': ['call:2']}],
        'blocked': [{'what': '最新指引', 'why': '工具返回 not_found'}],
        'summary': '营收增长，指引尚缺。',
    }


def rejected(output, reason, *, refs=REFS):
    with pytest.raises(Rejected) as exc:
        check_research(output, refs=refs)
    assert exc.value.reason == reason
    assert str(exc.value) == reason


@pytest.mark.parametrize('count', [0, 13])
def test_rejects_fact_count_outside_bounds(count):
    output = research()
    output['facts'] *= count
    rejected(output, 'research_facts_count')


@pytest.mark.parametrize('kind', ['opinion', None, [], 1])
def test_rejects_unknown_kind(kind):
    output = research()
    output['facts'][0]['kind'] = kind
    rejected(output, 'research_kind')


@pytest.mark.parametrize('support', [[], ['call:2'], ['call:3']])
def test_inference_requires_source_shared_with_non_inference_fact(support):
    output = research()
    output['facts'].append({'text': '增长或持续。', 'kind': 'inference', 'refs': support})
    rejected(output, 'research_inference_unsupported')


def test_inference_cannot_support_itself_or_another_inference():
    output = research()
    output['facts'] = [{'text': '推断。', 'kind': 'inference', 'refs': ['call:1']}] * 2
    rejected(output, 'research_inference_unsupported')


def test_inference_may_cite_later_fact_but_at_most_three_inferences():
    output = research()
    inference = {'text': '增长或持续。', 'kind': 'inference', 'refs': ['call:1']}
    output['facts'] = [inference] * 3 + output['facts']
    assert len(check_research(output, refs=REFS)['facts']) == 4
    output['facts'].insert(0, inference)
    rejected(output, 'research_inference_unsupported')


@pytest.mark.parametrize('bad_ref', ['call:99', 'fact:research', 'call:0', 'user:1', 'call:01', {}])
def test_all_refs_must_be_valid_calls_from_this_stage(bad_ref):
    output = research()
    output['facts'][0]['refs'] = [bad_ref]
    rejected(output, 'research_refs_invalid', refs=REFS | frozenset({'fact:research', 'call:0', 'user:1', 'call:01'}))


@pytest.mark.parametrize('field', ['facts', 'risks'])
@pytest.mark.parametrize('refs', [[], None, 'call:1', ['call:99']])
def test_fact_and_risk_refs_are_nonempty_valid_lists(field, refs):
    output = research()
    output[field][0]['refs'] = refs
    rejected(output, 'research_refs_invalid')


@pytest.mark.parametrize('summary', ['文' * 301, '', '  ', None])
def test_summary_length_and_type(summary):
    output = research()
    output['summary'] = summary
    rejected(output, 'research_summary_length')


@pytest.mark.parametrize('blocked', [[{'what': '指引'}], [{'what': '', 'why': 'timeout'}],
                                    [{'what': '指引', 'why': ' '}], [{}] * 7, None])
def test_blocked_requires_what_and_tool_reason(blocked):
    output = research()
    output['blocked'] = blocked
    rejected(output, 'research_blocked_shape')


@pytest.mark.parametrize('field', ['facts', 'risks'])
def test_rejects_overlong_fact_or_risk_text(field):
    output = research()
    output[field][0]['text'] = '文' * 201
    rejected(output, 'research_malformed')


def test_rejects_seventh_risk():
    output = research()
    output['risks'] *= 7
    rejected(output, 'research_malformed')


@pytest.mark.parametrize('output', [None, [], '{broken', {'facts': 'wrong'}])
def test_malformed_input_uses_rejected_not_python_type_errors(output):
    with pytest.raises(Rejected):
        check_research(output, refs=REFS)


def test_boundary_lengths_json_and_fresh_output():
    output = research()
    output['facts'][0]['text'] = '文' * 200
    output['facts'] *= 12
    output['risks'] *= 6
    output['blocked'] *= 6
    output['summary'] = '文' * 300
    before = copy.deepcopy(output)
    checked = check_research(output, refs=REFS)
    assert checked == before
    assert check_research('```json\n' + json.dumps(output) + '\n```', refs=REFS) == before
    checked['facts'][0]['refs'].append('call:999')
    assert output == before


def test_fact_block_numbers_facts_with_refs():
    output = check_research(research(), refs=REFS)
    block = research_fact_block(output)
    assert block.startswith('[fact:research]\n')
    assert '营收增长，指引尚缺。' in block
    assert '1. [disclosure] 披露营收增长 8%。 (call:1)' in block
    assert '1. 客户集中。 (call:2)' in block
    assert '1. 最新指引：工具返回 not_found' in block


def test_empty_optional_fact_block_sections_do_not_invent_evidence():
    output = research()
    output.update(risks=[], blocked=[])
    block = research_fact_block(check_research(output, refs=REFS))
    assert '暂无条目' in block
    assert 'call:2' not in block


# ---------------------------------------------------------------- the dossier conclusion and its independent counter

def conclusion(**overrides):
    base = {'action': 'buy', 'summary': '增长可期', 'catalysts': [{'text': '下季财报', 'refs': ['call:1']}],
            'price_scenarios': [{'text': '回落至支撑', 'refs': ['call:2']}], 'next_steps': []}
    return {**base, **overrides}


def test_conclusion_is_the_writers_three_lists_and_leaves_counter_to_the_counter_phase():
    from investment_assistant.analysis.agent.research_check import check_conclusion

    out = check_conclusion(json.dumps(conclusion()), refs=REFS)
    assert out['counter'] == [] and out['catalysts'][0]['refs'] == ['call:1']
    for bad in ({**conclusion(), 'counter': []}, {k: v for k, v in conclusion().items() if k != 'next_steps'}):
        with pytest.raises(Rejected) as exc:
            check_conclusion(bad, refs=REFS)
        assert exc.value.reason == 'conclusion_malformed'
    with pytest.raises(Rejected) as exc:
        check_conclusion(conclusion(catalysts=[]), refs=REFS)
    assert exc.value.reason == 'conclusion_catalysts_required'
    with pytest.raises(Rejected) as exc:
        check_conclusion(conclusion(price_scenarios=[{'text': '虚构价位', 'refs': ['call:9']}]), refs=REFS)
    assert exc.value.reason == 'research_refs_invalid'


def test_counter_filter_drops_bad_entries_and_never_fails_the_dossier():
    from investment_assistant.analysis.agent.research_check import filter_counter

    good = {'text': '客户集中', 'refs': ['call:1']}
    text = json.dumps({'counter': [good, {'text': '虚构', 'refs': ['call:9']}, {'text': 'x' * 201, 'refs': ['call:1']},
                                   {'text': '缺引用'}, 'bare string']})
    assert filter_counter(text, refs=REFS) == {'status': 'ok', 'counter': [good], 'dropped': 4}
    assert filter_counter('```json\n{"counter": []}\n```', refs=REFS) == {'status': 'ok', 'counter': [], 'dropped': 0}
    assert filter_counter('not json', refs=REFS) == {'status': 'malformed', 'counter': [], 'dropped': 0}
    assert filter_counter(json.dumps({'risks': []}), refs=REFS)['status'] == 'malformed'
    seven = json.dumps({'counter': [good] * 7})
    assert len(filter_counter(seven, refs=REFS)['counter']) == 6 and filter_counter(seven, refs=REFS)['dropped'] == 1
