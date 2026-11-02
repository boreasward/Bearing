import copy
import json

import pytest

from investment_assistant.analysis.agent.pm_check import check_pm
from investment_assistant.analysis.agent.research_check import Rejected


def commentary():
    return {'summary': '先考虑资金占用更低的计划。',
            'ranking': [{'plan_id': 'ok-1', 'rank': 1, 'reason': '行业敞口更低。'}],
            'tradeoffs': [{'text': '科技集中。', 'refs': ['fact:portfolio_check', 'call:2']}],
            'hold_actions': [{'ticker': 'AAPL', 'suggestion': 'keep', 'reason': '等待复查。'}]}


def check(output):
    return check_pm(output, ok_plan_ids=frozenset({'ok-1', 'ok-2'}), refs=frozenset({'call:2', 'user:1'}))


def test_accepts_only_commentary_and_does_not_mutate_host_inputs():
    output = commentary()
    original = copy.deepcopy(output)
    checked = check(json.dumps(output))
    assert checked == original
    checked['tradeoffs'][0]['refs'].clear()
    assert output == original


@pytest.mark.parametrize('plan_id', ['blocked-1', 'deferred-1', 'unknown', [], None])
def test_ranking_cannot_promote_non_ok_plans(plan_id):
    output = commentary()
    output['ranking'][0]['plan_id'] = plan_id
    with pytest.raises(Rejected, match='pm_ranking_invalid'):
        check(output)


@pytest.mark.parametrize('rank', [0, -1, True, 1.5, '1'])
def test_rank_must_be_positive_integer(rank):
    output = commentary()
    output['ranking'][0]['rank'] = rank
    with pytest.raises(Rejected, match='pm_ranking_invalid'):
        check(output)


@pytest.mark.parametrize('other', [{'plan_id': 'ok-1', 'rank': 2, 'reason': '重复计划'},
                                  {'plan_id': 'ok-2', 'rank': 1, 'reason': '重复顺序'}])
def test_ranking_rejects_duplicate_plan_or_rank(other):
    output = commentary()
    output['ranking'].append(other)
    with pytest.raises(Rejected, match='pm_ranking_invalid'):
        check(output)


@pytest.mark.parametrize('key', ['entry', 'entry_low', 'entry_high', 'stop', 'target1', 'shares',
                               'sizing', 'price', 'quantity', 'limit_price'])
@pytest.mark.parametrize('location', ['root', 'ranking', 'hold_actions', 'nested'])
def test_rejects_price_or_quantity_injection_at_any_depth(key, location):
    output = commentary()
    target = output if location == 'root' else output['ranking' if location == 'nested' else location][0]
    if location == 'nested':
        target['extra'] = {key: 99}
    else:
        target[key] = 99
    with pytest.raises(Rejected, match='pm_levels_forbidden'):
        check(output)


@pytest.mark.parametrize('refs', [[], ['call:1'], ['user:1'], ['fact:positions'], ['call:0'], [{}], 'call:2'])
def test_tradeoffs_require_this_runs_calls_or_portfolio_fact(refs):
    output = commentary()
    output['tradeoffs'][0]['refs'] = refs
    with pytest.raises(Rejected, match='pm_refs_invalid'):
        check(output)


@pytest.mark.parametrize('suggestion', ['buy', 'sell', 'half', None, {}])
def test_hold_actions_only_allow_keep_trim_exit(suggestion):
    output = commentary()
    output['hold_actions'][0]['suggestion'] = suggestion
    with pytest.raises(Rejected, match='pm_malformed'):
        check(output)


def test_summary_boundary_and_empty_ranking():
    output = {'summary': '文' * 300, 'ranking': [], 'tradeoffs': [], 'hold_actions': []}
    assert check(output) == output
    output['summary'] += '文'
    with pytest.raises(Rejected, match='pm_summary_length'):
        check(output)


@pytest.mark.parametrize('change', [{'orders': []}, {'tradeoffs': None}, {'hold_actions': [{}]},
                                   {'ranking': [{'plan_id': 'ok-1', 'rank': 1, 'reason': ''}]}])
def test_no_unknown_output_fields_or_invalid_collections(change):
    output = commentary()
    output.update(change)
    with pytest.raises(Rejected):
        check(output)
