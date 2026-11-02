"""Account-scoped hard checks use real sizing, frozen reservations and chain state."""
from copy import deepcopy
from datetime import date
from decimal import Decimal as D
from importlib import import_module
from uuid import UUID

import pytest

from investment_assistant.config import WorkbenchConfig
from investment_assistant.core.versions import PORTFOLIO_CHECK_VERSION
from investment_assistant.execution.accounts import default_params
from investment_assistant.execution import portfolio_check as evidence

S = date(2026, 10, 30)
PARAMS = default_params(WorkbenchConfig())


def plan(pid: str | UUID = 'p', ticker='AAA', **kw):
    # 100000 * .01 / (100 - 87.5) = 80 shares, exposure 8000.
    return {'plan_id': pid, 'ticker': ticker, 'chain_signal_date': S,
            'entry_low': D('100'), 'entry_high': D('100'), 'ref_price': D('100'),
            'stop': D('87.5'), 'inputs': {}, 'state': 'entry', **kw}


def position(ticker='HELD', value='30000', **kw):
    return {'ticker': ticker, 'quantity': D('10'), 'market_value': D(value), **kw}


def check(**kw):
    return import_module('investment_assistant.execution.portfolio').check(**{
        'session': S, 'plans': [plan()], 'positions': [], 'pending': [],
        'cash': D('100000'), 'equity': D('100000'), 'params': PARAMS,
        'sectors': {'AAA': '科技'}, **kw})


def test_sector_cap_blocks_candidate_over_cap():
    result = check(positions=[position()], cash=D('70000'), sectors={'AAA': '科技', 'HELD': '科技'})
    assert result.verdicts == [{'plan_id': 'p', 'ticker': 'AAA', 'group': '科技',
                                'verdict': 'blocked', 'reasons': ['sector_cap'], 'order': None}]
    assert result.exposure_by_sector['科技'] == {
        'held_value': D('30000'), 'pending_value': D(0),
        'candidate_value': D('8000'), 'share_of_equity': D('.38')}
    assert result.order == []


def test_max_per_sector_conflict_group():
    result = check(plans=[plan('b', 'BBB'), plan('a')], positions=[position('H1', '1000'), position('H2', '1000')],
                   cash=D('98000'), sectors=dict.fromkeys(['AAA', 'BBB', 'H1', 'H2'], '科技'))
    assert result.conflicts == [{'group': '科技', 'plan_ids': ['a', 'b'], 'rule': 'max_per_sector'}]
    assert [v['reasons'] for v in result.verdicts] == [['max_per_sector'], ['max_per_sector']]
    assert all(v['verdict'] == 'blocked' for v in result.verdicts)
    assert result.order == []


def test_cash_greedy_order_by_signal_date():
    result = check(plans=[plan('b', 'BBB'), plan('old', 'ZZZ', chain_signal_date='2026-10-29'), plan('a')],
                   positions=[position(value='80000')], cash=D('20000'),
                   sectors={'AAA': '科技', 'BBB': '科技', 'ZZZ': '科技', 'HELD': '金融'})
    assert result.order == ['old', 'a']
    assert [(v['plan_id'], v['order']) for v in result.verdicts] == [('old', 1), ('a', 2), ('b', None)]
    assert result.verdicts[-1]['verdict'] == 'deferred'
    assert result.verdicts[-1]['reasons'] == ['deferred_by_cash']


@pytest.mark.parametrize('holding', [
    {'inputs': {'chain_mode': 'holding'}}, {'state': 'holding'},
    {'state': {'state': 'exiting'}}, {'current_real_state': 'holding', 'state': 'holding'},
])
def test_holding_chains_only_in_exposure(holding):
    result = check(plans=[plan('held', 'HELD', **holding), plan('new')], positions=[position()],
                   cash=D('70000'), params={**PARAMS, 'max_positions': 1},
                   sectors={'AAA': '科技', 'HELD': '科技'})
    assert [v['plan_id'] for v in result.verdicts] == ['new']
    assert result.verdicts[0]['reasons'] == ['max_positions']
    assert result.exposure_by_sector['科技']['held_value'] == D('30000')
    assert result.exposure_by_sector['科技']['candidate_value'] == 0
    assert result.conflicts == [] and result.order == []


def test_fresh_account_and_unclassified_ticker():
    result = check(sectors={})
    assert result.verdicts[0]['reasons'] == ['sector_unknown']
    assert result.verdicts[0]['verdict'] == 'ok' and result.order == ['p']
    assert result.exposure_by_sector[None]['share_of_equity'] == D('.08')
    empty = check(plans=[], cash=0, equity=0, sectors={})
    assert empty.exposure_by_sector == {} and empty.verdicts == []
    zero = check(cash=0, equity=0, sectors={})
    assert zero.exposure_by_sector[None]['share_of_equity'] is None
    assert zero.order == [] and zero.verdicts[0]['reasons'][-1] == 'sector_unknown'


def test_to_fact_shape():
    module = import_module('investment_assistant.execution.portfolio')
    pid = UUID('00000000-0000-0000-0000-000000000003')
    result = check(plans=[plan(pid)], sectors={})
    fact = module.to_fact(result)
    assert set(fact) == {'exposure_by_sector', 'verdicts', 'conflicts', 'order', 'params_used', 'version'}
    assert result.version == fact['version'] == PORTFOLIO_CHECK_VERSION
    assert fact['exposure_by_sector']['未归类']['candidate_value'] == D('8000')
    assert fact['params_used']['sector_cap'] == .35 and fact['params_used']['max_per_sector'] == 3
    assert module.verdict_for(result, str(pid)) == result.verdicts[0]
    assert module.verdict_for(result, 'missing') is None
    decoded = evidence.evidence_facts(evidence.build_evidence({'portfolio_check': fact}))['portfolio_check']
    assert decoded['verdicts'][0]['plan_id'] == str(pid) and decoded['order'] == [str(pid)]
    fact['verdicts'][0]['reasons'].append('test')
    assert result.verdicts[0]['reasons'] == ['sector_unknown']


def test_sector_caps_use_all_candidates_and_accept_exact_boundary():
    result = check(plans=[plan('a'), plan('b', 'BBB')], positions=[position(value='20000')],
                   cash=D('80000'), sectors=dict.fromkeys(['AAA', 'BBB', 'HELD'], '科技'))
    assert [v['reasons'] for v in result.verdicts] == [['sector_cap'], ['sector_cap']]
    boundary = check(positions=[position(value='27000')], cash=D('73000'),
                     sectors={'AAA': '科技', 'HELD': '科技'})
    assert boundary.verdicts[0]['verdict'] == 'ok'
    assert boundary.exposure_by_sector['科技']['share_of_equity'] == D('.35')


def test_unknown_group_exempt_from_both_sector_limits():
    result = check(plans=[plan('a'), plan('b', 'BBB')], positions=[position(value='30000')], cash=D('70000'),
                   sectors={}, params={**PARAMS, 'sector_cap': .05, 'max_per_sector': 1})
    assert result.order == ['a', 'b'] and result.conflicts == []
    assert all(v['reasons'] == ['sector_unknown'] for v in result.verdicts)


def test_pending_uses_frozen_price_fees_and_only_active_buys():
    pending = [{'id': 'o', 'plan_id': 'frozen', 'ticker': 'PEND', 'shares': 50, 'entry_high': D('100'),
                'action': 'buy', 'status': 'pending'},
               {'id': 'terminal', 'action': 'buy', 'status': 'cancelled'},
               {'id': 'sell', 'action': 'sell', 'status': 'pending'}]
    result = check(pending=pending, cash=D('13010'), params={**PARAMS, 'fee_per_trade': 5},
                   sectors={'AAA': '科技', 'PEND': '科技'})
    assert result.exposure_by_sector['科技']['pending_value'] == D('5000')
    assert result.order == ['p']  # 5000+5 pending and 8000+5 candidate, exactly funded.
    pending[0].pop('entry_high')
    pending[0]['reserved_cash'] = D('5005')
    assert check(pending=pending, cash=D('13010'), params={**PARAMS, 'fee_per_trade': 5},
                 sectors={'AAA': '科技', 'PEND': '科技'}).exposure_by_sector == result.exposure_by_sector
    pending[0].pop('reserved_cash')
    with pytest.raises(ValueError, match='missing frozen entry_high'):
        check(pending=pending)


@pytest.mark.parametrize('updates,shares', [
    ({'single_cap': .05}, 50), ({'total_cap': .03}, 30),
    ({'risk_pct': .005}, 40), ({'fee_per_trade': 5}, 80),
])
def test_candidate_sizing_uses_real_account_caps_not_stale_sizing(updates, shares):
    result = check(plans=[plan(sizing={'shares': 9999}, max_shares=9999)], params={**PARAMS, **updates})
    assert result.exposure_by_sector['科技']['candidate_value'] == D(shares * 100)
    assert result.order == ['p']


def test_cash_cap_is_shared_before_greedy_allocation():
    result = check(cash=D('755'), params={**PARAMS, 'fee_per_trade': 5})
    assert result.exposure_by_sector['科技']['candidate_value'] == D('700')
    assert result.order == ['p']


@pytest.mark.parametrize('updates,reason', [({'max_positions': 1}, 'max_positions'), ({'total_cap': .1}, 'total_cap')])
def test_running_shared_caps_reserve_accepted_candidates(updates, reason):
    result = check(plans=[plan('a'), plan('b', 'BBB')], sectors={'AAA': '科技', 'BBB': '金融'},
                   params={**PARAMS, **updates})
    assert result.order == ['a'] and result.verdicts[-1]['reasons'] == [reason]
    assert result.verdicts[-1]['verdict'] == 'blocked'


def test_held_and_duplicate_tickers_cannot_enter_again():
    held = check(positions=[position('AAA', '1000')], cash=D('99000'))
    assert held.verdicts[0]['reasons'] == ['held_ticker'] and held.order == []
    duplicate = check(plans=[plan('a'), plan('b')])
    assert [v['reasons'] for v in duplicate.verdicts] == [['held_ticker'], ['held_ticker']]
    assert duplicate.order == []


def test_no_room_reasons_precede_unknown_sector_hint():
    result = check(cash=D('50'), sectors={})
    assert result.verdicts[0]['verdict'] == 'blocked'
    assert result.verdicts[0]['reasons'] == ['insufficient_cash', 'sector_unknown']
    result = check(positions=[position(value='100000')], params={**PARAMS, 'total_cap': 1})
    assert result.verdicts[0]['reasons'] == ['total_cap']


def test_check_does_not_mutate_inputs():
    inputs = {'plans': [plan('b', 'BBB'), plan('a')], 'positions': [position()], 'pending': [],
              'params': dict(PARAMS), 'sectors': {'AAA': '科技', 'BBB': '金融'}}
    before = deepcopy(inputs)
    check(**inputs)
    assert inputs == before


def test_evidence_missing_sections_and_old_numbers_preserved():
    old = {'account': {'cash': D('123456789.123456789')}, 'positions': [], 'pending_orders': [],
           'buyable': [{'max_shares': 80}], 'rejected_yesterday': [], 'recent_closed': [], 'market': {}}
    parsed = evidence.evidence_facts(evidence.build_evidence(old))
    assert parsed.pop('portfolio_check') == {'status': 'missing'}
    assert parsed == old
    empty = evidence.evidence_facts(evidence.build_evidence({}))
    assert set(empty) == set(old) | {'portfolio_check'}
    assert all(value == {'status': 'missing'} for value in empty.values())
