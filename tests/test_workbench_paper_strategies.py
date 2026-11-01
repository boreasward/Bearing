"""Candidate selection, five share caps, mechanical plans and host order rejection."""
from datetime import date
from decimal import Decimal as D
from importlib import import_module, util

import pytest

from investment_assistant.config import WorkbenchConfig
from investment_assistant.workbench.accounts import default_params

S = date(2026, 10, 20)
NEXT = date(2026, 10, 21)
PARAMS = default_params(WorkbenchConfig())


def strategies():
    assert util.find_spec('investment_assistant.workbench.paper_strategies') is not None
    return import_module('investment_assistant.workbench.paper_strategies')


def plan(id='p', ticker='NVDA', **kw):
    return {'id': id, 'ticker': ticker, 'signal_date': S, 'plan_version': 'agent-3', 'inputs': {},
            'entry_type': 'zone_limit', 'ref_price': D('100'), 'entry_low': D('98'), 'entry_high': D('102'),
            'stop': D('94'), 'target1': D('112'), 'atr': D('2'), 'trail_atr_multiple': D('3'),
            'valid_from': NEXT, 'valid_until': date(2026, 10, 23), 'max_hold_sessions': 10, **kw}


def chain(*plans, state='entry', enrich=True):
    return {'chain': {'ticker': plans[0]['ticker'], 'signal_date': S, 'head_plan': plans[-1],
                       'reviews': [{'id': 'r', 'outcome': 'maintain', 'reason': '延续', 'session': S}],
                       'versions': [{'plan_id': p['id'], 'version_no': i, 'schema_version': 'agent-3',
                                     'diff': [] if i == 0 else [{'field': 'stop', 'before': '93', 'after': '94'}],
                                     **({'plan': p} if enrich else {})} for i, p in enumerate(plans)]},
            'state': {'state': state}, 'facts': {}, 'buys': []}


def item(id='p', ticker='NVDA', **kw):
    return {**plan(id, ticker), 'plan_id': id, 'chain_signal_date': S, 'version_no': 0, 'max_shares': 10,
            'sizing_status': 'sized', 'blocked_reason': None, **kw}


def order(id='o', **kw):
    return {'id': id, 'action': 'buy', 'plan_id': 'p', 'ticker': 'NVDA', 'chain_signal_date': S,
            'shares': 2, 'entry_high': D('102'), 'placed_on': S, 'status': 'pending', **kw}


def output(orders, holds=None, **kw):
    return {'orders': orders, 'holds': [] if holds is None else holds, 'summary': '按计划', **kw}


def buy(id='p', shares=2):
    return {'action': 'buy', 'plan_id': id, 'shares': shares, 'reason': '执行计划'}


def validate(data, *, items=None, positions=None, pending=None, cash=D('10000'), params=None):
    return strategies().validate_orders(data, items=[item()] if items is None else items, positions=positions or [],
                                        pending_orders=pending or [], cash=cash, params=params or PARAMS)


def test_buyable_native_and_enriched_latest_entry_even_when_real_account_holds():
    native = strategies().buyable([chain(plan(), enrich=False)], next_session=NEXT)[0]
    assert native['plan_id'] == 'p' and native['chain_signal_date'] == S and native['current_real_state'] == 'entry'
    entry = plan('p1', stop=D('95'))
    held = plan('p2', inputs={'chain_mode': 'holding'}, stop=D('99'))
    for real_state in ('holding', 'exiting', 'position_closed'):
        got = strategies().buyable([chain(plan(), entry, held, state=real_state)], next_session=NEXT)[0]
        assert got['plan_id'] == 'p1' and got['version_no'] == 1 and got['stop'] == D('95')
        assert got['latest_review']['id'] == 'r' and got['current_real_state'] == real_state and got['change']
    raw = chain(plan(), held, enrich=False)
    raw['plans_by_id'] = {'p': plan(), 'p2': held}
    assert strategies().buyable([raw], next_session=NEXT)[0]['plan_id'] == 'p'
    with pytest.raises(ValueError, match='plan'):
        strategies().buyable([chain(plan(), held, enrich=False)], next_session=NEXT)


@pytest.mark.parametrize('state', ['invalidated', 'expired', 'superseded'])
def test_buyable_excludes_ended_chains(state):
    assert strategies().buyable([chain(plan(), state=state)], next_session=NEXT) == []


def test_buyable_validity_agent3_and_next_open_first_day():
    assert strategies().buyable([chain(plan(entry_type='next_open'))], next_session=NEXT)
    assert strategies().buyable([chain(plan(entry_type='next_open'))], next_session=date(2026, 10, 22)) == []
    assert strategies().buyable([chain(plan())], next_session=S) == []
    assert strategies().buyable([chain(plan())], next_session=date(2026, 10, 26)) == []
    assert strategies().buyable([chain(plan(plan_version='agent-2'))], next_session=NEXT) == []


@pytest.mark.parametrize('kw,reason', [({'done': {('NVDA', S)}}, 'chain_consumed'),
                                     ({'tried_plan_ids': {'p'}}, 'version_tried'),
                                     ({'held_chains': {('NVDA', S)}}, 'held_chain'),
                                     ({'held_chains': {('NVDA', date(2026, 10, 1))}}, 'held_ticker'),
                                     ({'pending_chains': {('NVDA', S)}}, 'pending_chain'),
                                     ({'pending_chains': {('NVDA', date(2026, 10, 1))}}, 'pending_ticker')])
def test_account_filter_keeps_visible_blocked_items(kw, reason):
    args = {'done': set(), 'tried_plan_ids': set(), 'held_chains': set(), 'pending_chains': set(), **kw}
    got = strategies().account_filter([item()], **args)
    assert len(got) == 1 and got[0]['blocked_reason'] == reason
    assert item()['blocked_reason'] is None


@pytest.mark.parametrize('overrides,expected,cap,status', [
    ({}, 166, 'risk', 'sized'),
    ({'params': {**PARAMS, 'single_cap': .1}}, 98, 'single', 'capped'),
    ({'total_positions_value': D('95000')}, 49, 'total', 'capped'),
    ({'cash': D('1000'), 'pending_reserved': D('200'), 'params': {**PARAMS, 'fee_per_trade': 5}}, 7, 'cash', 'capped'),
    ({'positions_count': 7, 'pending_count': 1}, 0, 'max_positions', 'max_positions'),
])
def test_five_share_caps(overrides, expected, cap, status):
    args = {'equity': D('100000'), 'params': PARAMS, 'cash': D('100000'), 'positions_value_by_ticker': {},
            'total_positions_value': D(0), 'pending_reserved': D(0), 'positions_count': 0, 'pending_count': 0, **overrides}
    assert strategies().max_shares(item(), **args) == {'shares': expected, 'applied_cap': cap, 'status': status}


def test_follow_plan_place_replace_end_skip_and_no_retry():
    s = strategies()
    assert s.FOLLOW_PLANS_VERSION == 'follow-plans-1'
    placed = s.follow_plans_orders([item()], pending_orders=[], session=S)
    assert placed['place'][0]['shares'] == 10 and placed['place'][0]['placed_by'] == 'host'
    assert placed['cancel'] == [] and placed['skipped'] == []
    new = item('p2', blocked_reason='pending_chain')
    revised = s.follow_plans_orders([new], pending_orders=[order()], session=S)
    assert revised['cancel'] == [{'order_id': 'o', 'reason': 'superseded_by_revision'}]
    assert [p['plan_id'] for p in revised['place']] == ['p2']
    assert s.follow_plans_orders([], pending_orders=[order()], session=S)['cancel'] == [
        {'order_id': 'o', 'reason': 'chain_ended'}]
    assert s.follow_plans_orders([item()], pending_orders=[order()], session=S)['place'] == []
    for status in ('voided', 'expired', 'filled', 'cancelled'):
        assert s.follow_plans_orders([item()], pending_orders=[order(status=status)], session=S)['place'] == []
    for reason in ('chain_consumed', 'version_tried', 'held_chain', 'held_ticker'):
        assert s.follow_plans_orders([item(blocked_reason=reason)], pending_orders=[], session=S)['place'] == []
    for status in ('no_room', 'max_positions'):
        got = s.follow_plans_orders([item(max_shares=0, sizing_status=status)], pending_orders=[], session=S)
        assert got['skipped'] == [{'plan_id': 'p', 'reason': status}]


def test_all_rejection_codes_are_fixed():
    assert strategies().REJECT_CODES == ('schema_invalid', 'plan_not_buyable', 'chain_consumed', 'duplicate_order',
                                         'exceeds_max_shares', 'max_positions', 'insufficient_cash', 'no_position',
                                         'cannot_halve', 'order_not_pending')


@pytest.mark.parametrize('bad,kw,code', [
    (buy('missing'), {}, 'plan_not_buyable'),
    (buy(), {'items': [item(blocked_reason='held_chain')]}, 'plan_not_buyable'),
    (buy(), {'items': [item(blocked_reason='chain_consumed')]}, 'chain_consumed'),
    (buy(), {'items': [item(blocked_reason='version_tried')]}, 'chain_consumed'),
    (buy(), {'pending': [order()]}, 'duplicate_order'),
    (buy(shares=11), {}, 'exceeds_max_shares'), (buy(shares=0), {}, 'exceeds_max_shares'),
    (buy(shares=True), {}, 'exceeds_max_shares'), (buy(shares=1.5), {}, 'exceeds_max_shares'),
    (buy(shares='2'), {}, 'exceeds_max_shares'),
    (buy(), {'params': {**PARAMS, 'max_positions': 1},
             'pending': [order('other', ticker='MU', plan_id='p2')]}, 'max_positions'),
    (buy(), {'cash': D('100')}, 'insufficient_cash'),
    ({'action': 'sell', 'ticker': 'AMD', 'portion': 'all', 'reason': 'x'}, {}, 'no_position'),
    ({'action': 'sell', 'ticker': 'AMD', 'portion': 'half', 'reason': 'x'},
     {'positions': [{'ticker': 'AMD', 'quantity': 1}]}, 'cannot_halve'),
    ({'action': 'cancel', 'order_id': 'foreign', 'reason': 'x'}, {}, 'order_not_pending'),
])
def test_individual_rejections_keep_other_valid_orders(bad, kw, code):
    data = output([bad])
    result = validate(data, **kw)
    assert result == {'accepted': [], 'rejected': [{'order': bad, 'code': code}], 'schema_error': None}


def test_batch_cash_duplicates_positions_and_cancel_release():
    items = [item('p', 'NVDA'), item('p2', 'AMD'), item('p3', 'MU')]
    result = validate(output([buy(), buy('p2')]), items=items, cash=D('300'))
    assert result['accepted'] == [buy()] and result['rejected'][0]['code'] == 'insufficient_cash'
    result = validate(output([buy(), buy()]))
    assert result['accepted'] == [buy()] and result['rejected'][0]['code'] == 'duplicate_order'
    result = validate(output([buy(), buy('p2')]), items=items, params={**PARAMS, 'max_positions': 1})
    assert result['rejected'][0]['code'] == 'max_positions'
    cancel = {'action': 'cancel', 'order_id': 'o', 'reason': '换版'}
    replacement = item('p2', blocked_reason='pending_chain')
    result = validate(output([buy('p2'), cancel]), items=[replacement], pending=[order()], cash=D('210'))
    assert result['accepted'] == [buy('p2'), cancel] and result['rejected'] == []


def test_batch_total_cap_and_same_ticker_guard():
    items = [item(), item('p2', 'AMD')]
    result = validate(output([buy(), buy('p2')]), items=items, cash=D('1000'), params={**PARAMS, 'total_cap': .3})
    assert result['accepted'] == [buy()] and result['rejected'][0]['code'] == 'exceeds_max_shares'
    held = [{'ticker': 'NVDA', 'quantity': 10, 'market_value': D('1000')}]
    result = validate(output([buy('new')], holds=[{'ticker': 'NVDA', 'reason': '持有'}]),
                      items=[item('new', chain_signal_date=date(2026, 10, 19))], positions=held)
    assert result['rejected'][0]['code'] == 'plan_not_buyable'
    result = validate(output([buy('new')]), items=[item('new', chain_signal_date=date(2026, 10, 19))], pending=[order()])
    assert result['rejected'][0]['code'] == 'duplicate_order'


def test_missing_holds_or_invalid_reason_reject_entire_output():
    valid, invalid = buy(), buy('p2') | {'reason': 'x'*301}
    result = validate(output([valid, invalid]))
    assert result['accepted'] == [] and result['schema_error'].startswith('schema_invalid:')
    assert [r['code'] for r in result['rejected']] == ['schema_invalid', 'schema_invalid']
    result = validate(output([valid]), positions=[{'ticker': 'AMD', 'quantity': 2}])
    assert result['schema_error'] and result['accepted'] == []


def test_valid_sell_and_cancel_and_partial_rejection():
    sell = {'action': 'sell', 'ticker': 'AMD', 'portion': 'half', 'reason': '减仓'}
    result = validate(output([sell, buy('missing'), buy()]), positions=[{'ticker': 'AMD', 'quantity': 10}])
    assert result['accepted'] == [sell, buy()] and result['rejected'][0]['code'] == 'plan_not_buyable'
    cancel = {'action': 'cancel', 'order_id': 'o', 'reason': '撤单'}
    assert validate(output([cancel]), pending=[order()])['accepted'] == [cancel]
    assert validate(output([cancel]), pending=[order(status='filled')])['rejected'][0]['code'] == 'order_not_pending'


def test_voided_or_filled_buy_consumes_chain_even_when_new_version_arrives():
    for terminal in ('voided', 'filled'):
        out = strategies().follow_plans_orders([item('new')], pending_orders=[order(status=terminal)], session=S)
        assert out['place'] == []
    out = strategies().follow_plans_orders([item('new')], pending_orders=[order(status='expired')], session=S)
    assert [p['plan_id'] for p in out['place']] == ['new']


def test_existing_pending_sell_cannot_be_duplicated_next_decision():
    sell = {'action': 'sell', 'ticker': 'AMD', 'portion': 'all', 'reason': '提前离场'}
    pending = order(action='sell', ticker='AMD', portion='all')
    result = validate(output([sell]), pending=[pending], positions=[{'ticker': 'AMD', 'quantity': 10}])
    assert result['accepted'] == [] and result['rejected'][0]['code'] == 'duplicate_order'


def test_missing_pending_price_cannot_silently_release_reserved_cash():
    pending = order(ticker='AMD', plan_id='unlisted')
    pending.pop('entry_high')
    with pytest.raises(ValueError, match='missing frozen entry_high'):
        validate(output([buy()]), pending=[pending])
    pending['reserved_cash'] = D('9900')
    assert validate(output([buy()]), pending=[pending])['rejected'][0]['code'] == 'insufficient_cash'


def test_host_validation_also_enforces_consumed_chain_across_versions():
    for terminal in ('voided', 'filled'):
        result = validate(output([buy('new')]), items=[item('new')], pending=[order(status=terminal)])
        assert result['accepted'] == [] and result['rejected'][0]['code'] == 'chain_consumed'


def test_latest_review_same_day_keeps_repository_order_when_timestamp_is_stripped():
    ch = chain(plan())
    ch['chain']['reviews'].append({'id': 'last', 'outcome': 'maintain', 'session': S, 'created_at': None})
    ch['chain']['reviews'][0]['created_at'] = None
    assert strategies().buyable([ch], next_session=NEXT)[0]['latest_review']['id'] == 'last'


def test_accepted_sell_ticker_is_canonical_copy_and_settles_to_terminal():
    from investment_assistant.workbench.paper_fill import settle_day
    from investment_assistant.workbench.plan_shadow import PositionState

    raw = {'action': 'sell', 'ticker': ' nvda ', 'portion': 'all', 'reason': '提前离场'}
    result = validate(output([raw]), positions=[{'ticker': 'NVDA', 'quantity': 10}])
    assert result['rejected'] == [] and result['schema_error'] is None
    accepted = result['accepted'][0]
    assert accepted['ticker'] == 'NVDA'
    assert accepted is not raw and raw['ticker'] == ' nvda '
    pending = order(id='sell-order', **accepted)
    out = settle_day(session=NEXT, account={'id': 'a', 'params': PARAMS}, pending_orders=[pending],
                      holdings=[{'ticker': 'NVDA', 'quantity': 10, 'plan_id': 'p',
                                 'state': PositionState(D('100'), D('94'), D('94'), D('6'))}],
                      bars_by_ticker={'NVDA': {'price_date': NEXT, 'open': D('103'), 'high': D('104'),
                                               'low': D('101'), 'close': D('102')}},
                      plans_by_id={'p': plan()}, splits_by_ticker={})
    assert len(out['fills']) == 1 and out['fills'][0]['ticker'] == 'NVDA'
    assert out['fills'][0]['price'] == D('103') and out['fills'][0]['quantity'] == D('10')
    assert out['fills'][0]['detail']['exit_reason'] == 'agent_sell'
    assert out['order_updates'] == [{'order_id': 'sell-order', 'status': 'filled', 'status_reason': None}]
    assert out['states'] == {}


def test_host_rejected_order_does_not_consume_version_but_others_do():
    s = strategies()
    rejected = order(status='rejected', shares=51, status_reason='exceeds_max_shares')
    assert [p['plan_id'] for p in s.follow_plans_orders([item()], pending_orders=[rejected], session=S)['place']] == ['p']
    retry = validate(output([buy()]), pending=[rejected])
    assert retry['accepted'] == [buy()] and retry['rejected'] == []
    # voided / filled still consume the chain; expired / cancelled / pending still consume the version.
    for status in ('voided', 'filled'):
        assert validate(output([buy()]), pending=[order(status=status)])['rejected'][0]['code'] == 'chain_consumed'
    for status in ('expired', 'cancelled'):
        assert validate(output([buy()]), pending=[order(status=status)])['rejected'][0]['code'] == 'chain_consumed'
        assert s.follow_plans_orders([item()], pending_orders=[order(status=status)], session=S)['place'] == []
