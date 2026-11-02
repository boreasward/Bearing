import copy
import json

import pytest

from investment_assistant.optimize.proposal_check import check_proposals


PACK = {'data_failures': [{'id': 'bars-network', 'failed_targets': 3}], 'agent_quality': [],
        'retro_patterns': [], 'route': [{'id': 'v2.11', 'days_pending': 31}], 'open_proposals': []}


def proposal(title='补齐数据'):
    return {'source': 'data', 'title': title, 'change': '增加补采记录。', 'rationale': '连续失败三次。',
            'evidence_refs': ['fact:data_failures#bars-network'], 'expected': '减少缺口。',
            'acceptance': '缺口均有记录。'}


def check(rows, *, open_titles=frozenset()):
    return check_proposals({'proposals': rows}, pack=PACK, open_titles=open_titles)


@pytest.mark.parametrize('refs', [['fact:data_failures#unknown'], ['fact:route#bars-network'],
                                 ['call:1'], ['fact:other#x'], [], None, [{}],
                                 ['fact:data_failures#bars-network', 'fact:route#bad']])
def test_drops_proposal_with_unknown_evidence_ref(refs):
    bad = proposal('错误引用')
    bad['evidence_refs'] = refs
    result = check([bad, proposal()])
    assert [p['title'] for p in result['proposals']] == ['补齐数据']
    assert result['dropped'][0]['index'] == 0
    assert result['dropped'][0]['reason'] == 'proposal_refs_invalid'


def test_caps_at_three():
    result = check([proposal(str(i)) for i in range(5)])
    assert [p['title'] for p in result['proposals']] == ['0', '1', '2']
    assert [d['index'] for d in result['dropped']] == [3, 4]
    assert all(d['reason'] == 'proposal_limit' for d in result['dropped'])


def test_bad_entry_does_not_consume_one_of_three_slots():
    result = check([None, proposal('一'), proposal('二'), proposal('三')])
    assert len(result['proposals']) == 3
    assert len(result['dropped']) == 1


def test_dedups_open_titles_and_current_batch():
    result = check([proposal('  DATA   gaps '), proposal('新提案'), proposal(' 新提案 ')],
                   open_titles=frozenset({'data gaps'}))
    assert [p['title'] for p in result['proposals']] == ['新提案']
    assert [d['reason'] for d in result['dropped']] == ['proposal_duplicate_title', 'proposal_duplicate_title']


@pytest.mark.parametrize('field,limit', [('change', 500), ('rationale', 500), ('expected', 200), ('acceptance', 300)])
def test_text_limits_accept_boundary_drop_overflow(field, limit):
    valid, bad = proposal('合法'), proposal('超长')
    valid[field] = '文' * limit
    bad[field] = '文' * (limit + 1)
    result = check([valid, bad])
    assert result['proposals'] == [valid]
    assert result['dropped'][0]['reason'] == 'proposal_length'


@pytest.mark.parametrize('change', [{'source': 'execution'}, {'source': []}, {'title': '  '},
                                   {'change': None}, {'rationale': ''}, {'expected': 123},
                                   {'acceptance': []}, {'shares': 100}])
def test_invalid_fields_drop_only_that_entry(change):
    bad = proposal('坏提案')
    bad.update(change)
    result = check([bad, proposal()])
    assert [p['title'] for p in result['proposals']] == ['补齐数据']
    assert len(result['dropped']) == 1


def test_empty_output_is_valid_and_malformed_top_level_is_diagnostic():
    assert check([]) == {'proposals': [], 'dropped': []}
    for bad in (None, [], 'not json', {'proposals': None}):
        result = check_proposals(bad, pack=PACK, open_titles=frozenset())
        assert result['proposals'] == []
        assert result['dropped'] == [{'index': None, 'reason': 'proposal_malformed'}]


def test_json_payload_and_real_pack_refs_round_trip_without_mutation():
    from investment_assistant.optimize.evidence import summarize

    pack = summarize(fetch_failures=[{'family': 'bars', 'error_kind': 'network', 'failed_targets': 3}],
                     agent_reasons=[], research_blocked=[], counter_adoption=[], lesson_repeats=[],
                     stale_versions=[], open_proposals=[])
    item = proposal()
    item['evidence_refs'] = [f"fact:data_failures#{pack['data_failures'][0]['id']}"]
    before = copy.deepcopy((pack, item))
    result = check_proposals(json.dumps({'proposals': [item]}), pack=pack, open_titles=frozenset())
    assert result['proposals'] == [item]
    result['proposals'][0]['evidence_refs'].clear()
    assert (pack, item) == before


def test_existing_counter_ref_survives_measurement_metadata():
    from investment_assistant.optimize.evidence import summarize

    pack = summarize(fetch_failures=[], agent_reasons=[], research_blocked=[],
                     counter_adoption=[{'adopted': 1, 'total': 2}, {'adopted': 1, 'total': 4}],
                     lesson_repeats=[], stale_versions=[], open_proposals=[])
    item = proposal('明确反方后续修订统计口径')
    item.update(source='agent_quality', rationale='运行级观测不能证明逐条采纳。',
                evidence_refs=['fact:agent_quality#af0a89bc2837a93427c53898'])
    assert check_proposals({'proposals': [item]}, pack=pack, open_titles=frozenset()) == {
        'proposals': [item], 'dropped': [],
    }
