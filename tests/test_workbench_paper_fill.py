"""Daily settlement matches the frozen shadow for the full entry/exit matrix."""
from dataclasses import asdict
from datetime import date
from decimal import Decimal as D
from importlib import import_module, util
from zoneinfo import ZoneInfo

import pytest

from investment_assistant.execution import simulation as plan_shadow
from investment_assistant.execution import positions

D0, D1, D2, D3 = date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23)
ACCOUNT = {'id': 'account', 'params': {'fee_per_trade': 0}}


def engine():
    assert util.find_spec('investment_assistant.execution.paper.fill') is not None, 'paper fill module missing'
    return import_module('investment_assistant.execution.paper.fill')


def plan(kind='zone_limit', **kw):
    return {'id': 'p', 'ticker': 'NVDA', 'signal_date': D0, 'entry_type': kind, 'entry_low': D('98'),
            'entry_high': D('102'), 'stop': D('94'), 'target1': D('112'), 'atr': D('2'),
            'trail_atr_multiple': D('3'), 'valid_from': D1, 'valid_until': D3, 'max_hold_sessions': 2, **kw}


def bar(day, op=100, hi=103, lo=99, cl=101):
    return {'price_date': day, 'open': D(str(op)), 'high': D(str(hi)), 'low': D(str(lo)), 'close': D(str(cl)),
            'adj_close': D(str(cl))}


def order(**kw):
    return {'id': 'o', 'account_id': 'account', 'ticker': 'NVDA', 'plan_id': 'p', 'action': 'buy',
            'shares': 10, 'chain_signal_date': D0, 'placed_on': D0, 'status': 'pending', **kw}


def settle(day, *, p=None, orders=None, holdings=None, bars=None, account=ACCOUNT, splits=None):
    return engine().settle_day(session=day, account=account, bars_by_ticker={'NVDA': bar(day)} if bars is None else bars,
                              pending_orders=[order()] if orders is None else orders, holdings=holdings or [],
                              plans_by_id={'p': p or plan()}, splits_by_ticker=splits or {})


@pytest.mark.parametrize('kind', ['zone_limit', 'breakout_stop', 'next_open'])
@pytest.mark.parametrize('case', ['in_zone', 'gap_up', 'gap_down'])
@pytest.mark.parametrize('script', ['stop', 't1', 'time'])
def test_repeated_days_equal_shadow(kind, case, script):
    p = plan(kind)
    first = {'in_zone': bar(D1), 'gap_up': bar(D1, 105, 106, 101, 101),
             'gap_down': bar(D1, 96, 103, 95, 101)}[case]
    tail = {'stop': [bar(D2, 101, 102, 93, 95)],
            't1': [bar(D2, 105, 113, 104, 111), bar(D3, 106, 108, 104, 105)],
            'time': [bar(D2, 101, 102, 100, 101), bar(D3, 102, 103, 101, 102)]}[script]
    bars = [first] + tail
    expected = plan_shadow.simulate_v2(p, bars, [], splits=[])
    holding, pending, fills, chain_done = [], [order()], [], []
    for b in bars:
        out = settle(b['price_date'], p=p, orders=pending, holdings=holding, bars={'NVDA': b})
        fills.extend(out['fills']); chain_done.extend(out['chain_done'])
        if out['order_updates']:
            pending = []
        qty = sum((f['quantity'] * (1 if f['side'] == 'buy' else -1) for f in fills), D(0))
        holding = [{'ticker': 'NVDA', 'quantity': qty, 'plan_id': 'p', 'chain_signal_date': D0,
                    'state': out['states']['NVDA']}] if qty else []
    buys, sells = [f for f in fills if f['side'] == 'buy'], [f for f in fills if f['side'] == 'sell']
    if expected['status'] == 'voided':
        assert fills == [] and chain_done == [{'ticker': 'NVDA', 'chain_signal_date': D0, 'reason': 'entry_voided'}]
        return
    assert buys[0]['price'] == expected['fill_price']
    assert buys[0]['executed_at'].astimezone(ZoneInfo('America/New_York')).date() == expected['fill_date']
    assert [(f['quantity'] / D('10'), f['price'], f['executed_at'].date().isoformat()) for f in sells] == [
        (D(str(leg['fraction'])), leg['exit_price'], leg['exit_date']) for leg in expected['legs']]
    r = sum(float(f['quantity'] / D(10)) * float((f['price'] - buys[0]['price']) / (buys[0]['price'] - p['stop']))
            for f in sells)
    assert r == pytest.approx(expected['r'])
    assert chain_done == [{'ticker': 'NVDA', 'chain_signal_date': D0, 'reason': 'exited'}]


def test_constants_fees_fill_case_and_fill_day_stop_precedes_target():
    eng = engine()
    assert eng.PAPER_FILL_VERSION == 'paper-fill-1'
    assert eng.FILL_CASES == ('in_zone', 'gap_up_limit', 'gap_down_stop_buy')
    assert eng.EXIT_REASONS == ('stop', 'gap_stop', 'target1_half', 'time_stop', 'agent_sell', 'exit_instruction')
    out = settle(D1, account={**ACCOUNT, 'params': {'fee_per_trade': D('2')}}, bars={'NVDA': bar(D1, 100, 120, 90, 101)})
    assert [(f['side'], f['price'], f['fee']) for f in out['fills']] == [('buy', D('100'), D('2')), ('sell', D('94'), D('2'))]
    assert out['fills'][0]['detail'] == {'fill_case': 'in_zone', 'order_id': 'o', 'paper_fill_version': 'paper-fill-1'}
    assert out['fills'][1]['detail']['exit_reason'] == 'stop'
    assert out['states'] == {}
    assert out['order_updates'] == [{'order_id': 'o', 'status': 'filled', 'status_reason': None}]
    for trade in out['fills']:
        assert trade['account_id'] == 'account' and trade['plan_id'] == 'p' and trade['recorded_by'] == 'host'


@pytest.mark.parametrize('op,hi,lo,case,hour', [(100, 103, 99, 'in_zone', 9), (105, 106, 101, 'gap_up_limit', 16),
                                            (96, 103, 95, 'gap_down_stop_buy', 16)])
def test_fill_timing(op, hi, lo, case, hour):
    fill = settle(D1, bars={'NVDA': bar(D1, op, hi, lo)})['fills'][0]
    assert fill['detail']['fill_case'] == case and fill['executed_at'].hour == hour


def test_fill_day_target_gap_and_trail_floor_ignores_fees():
    out = settle(D1, p=plan(target1=D('103')), account={**ACCOUNT, 'params': {'fee_per_trade': 10}},
                 bars={'NVDA': bar(D1, 100, 104, 99, 101)})
    assert out['fills'][1]['quantity'] == D('5')
    assert out['fills'][1]['detail']['exit_reason'] == 'target1_half'
    assert out['states']['NVDA'].stop == D('100')
    state = plan_shadow.PositionState(D('100'), D('94'), D('94'), D('6'))
    held = [{'ticker': 'NVDA', 'quantity': 10, 'plan_id': 'p', 'state': state}]
    gap = settle(D2, orders=[], holdings=held, bars={'NVDA': bar(D2, 115, 120, 110, 116)})
    assert gap['fills'][0]['price'] == D('115')


@pytest.mark.parametrize('instruction,reason', [({'agent_sell': 'all'}, 'agent_sell'),
                                              ({'exit_instruction': True}, 'exit_instruction')])
def test_manual_next_open_exit_has_priority_over_stop(instruction, reason):
    state = plan_shadow.PositionState(D('100'), D('94'), D('94'), D('6'))
    held = [{'ticker': 'NVDA', 'quantity': D('10'), 'plan_id': 'p', 'state': asdict(state), **instruction}]
    out = settle(D2, orders=[], holdings=held, bars={'NVDA': bar(D2, 96, 97, 90, 91)})
    assert len(out['fills']) == 1 and out['fills'][0]['price'] == D('96')
    assert out['fills'][0]['detail']['exit_reason'] == reason and out['states'] == {}


def test_agent_half_does_not_cancel_stop_for_remaining_half():
    held = [{'ticker': 'NVDA', 'quantity': D('10'), 'plan_id': 'p', 'agent_sell': 'half',
             'state': plan_shadow.PositionState(D('100'), D('94'), D('94'), D('6'))}]
    out = settle(D2, orders=[], holdings=held, bars={'NVDA': bar(D2, 96, 97, 90, 91)})
    assert [(f['quantity'], f['price'], f['detail']['exit_reason']) for f in out['fills']] == [
        (D('5'), D('96'), 'agent_sell'), (D('5'), D('94'), 'stop')]


def test_void_pending_expiration_missing_and_same_day_do_not_fill():
    assert settle(D0)['fills'] == []
    missing = settle(D1, bars={})
    assert missing['fills'] == [] and missing['order_updates'] == [] and missing['bars_missing'] == ['NVDA']
    stale = settle(D1, bars={'NVDA': bar(D0)})
    assert stale['bars_missing'] == ['NVDA']
    assert settle(D1, bars={'NVDA': bar(D1, 110, 112, 106, 108)})['order_updates'] == []
    expired = settle(D3, bars={'NVDA': bar(D3, 110, 112, 106, 108)})
    assert expired['order_updates'] == [{'order_id': 'o', 'status': 'expired', 'status_reason': 'validity_expired'}]
    void = settle(D1, bars={'NVDA': bar(D1, 93, 98, 90, 96)})
    assert void['order_updates'] == [{'order_id': 'o', 'status': 'voided', 'status_reason': 'gap_below_stop'}]
    assert void['chain_done'][0]['reason'] == 'entry_voided'
    state = plan_shadow.PositionState(D('100'), D('94'), D('94'), D('6'), time_stop_due=True)
    out = settle(D2, orders=[], bars={}, holdings=[{'ticker': 'NVDA', 'quantity': 10, 'plan_id': 'p', 'state': state}])
    assert out['states']['NVDA'] == state and out['fills'] == [] and out['bars_missing'] == ['NVDA']


def test_split_adjusted_bars_do_not_double_split_the_ledger():
    splits = {'NVDA': [(D2, D('2'))]}
    first = settle(D1, splits=splits, bars={'NVDA': bar(D1, 50, 51.5, 49.5, 50.5)})
    buy = {**first['fills'][0], 'id': 'b'}
    assert buy['quantity'] == 10 and buy['price'] == D('100')
    pos = positions.build([buy], splits=splits['NVDA'])['position']
    assert pos['quantity'] == 20 and pos['avg_cost'] == D('50')
    held = [{'ticker': 'NVDA', 'quantity': pos['quantity'], 'plan_id': 'p', 'state': first['states']['NVDA']}]
    second = settle(D2, orders=[], holdings=held, splits=splits, bars={'NVDA': bar(D2, 45, 46, 44, 45)})
    sale = {**second['fills'][0], 'id': 's'}
    assert sale['quantity'] == 20 and sale['price'] == D('45')
    assert positions.build([buy, sale], splits=splits['NVDA'])['position'] is None
    assert second['fills'][0]['detail']['exit_reason'] == 'gap_stop'


def test_fill_day_gap_target_remains_after_its_intraday_buy_in_fifo():
    out = settle(D1, bars={'NVDA': bar(D1, 115, 120, 101, 110)})
    buy, sell = out['fills']
    assert buy['executed_at'] < sell['executed_at']
    book = positions.build([{**buy, 'id': 'z'}, {**sell, 'id': 'a'}])
    assert book['position']['quantity'] == D('5')


def test_settlement_rejects_pending_buys_that_would_merge_frozen_ticker_positions():
    state = plan_shadow.PositionState(D('100'), D('94'), D('94'), D('6'))
    held = [{'ticker': 'NVDA', 'quantity': 10, 'plan_id': 'p', 'state': state}]
    out = settle(D1, holdings=held)
    assert out['fills'] == []
    assert out['order_updates'] == [{'order_id': 'o', 'status': 'rejected', 'status_reason': 'plan_not_buyable'}]
    out = settle(D1, orders=[order(), order(id='o2')])
    assert len(out['fills']) == 1
    assert out['order_updates'][1] == {'order_id': 'o2', 'status': 'rejected', 'status_reason': 'plan_not_buyable'}


def test_one_void_cannot_be_retried_by_another_raw_order_in_same_session():
    newer = plan(id='p2', entry_low=D('92'), entry_high=D('96'), stop=D('90'))
    out = engine().settle_day(session=D1, account=ACCOUNT, bars_by_ticker={'NVDA': bar(D1, 93, 98, 92, 94)},
                               pending_orders=[order(), order(id='new', plan_id='p2')], holdings=[],
                               plans_by_id={'p': plan(), 'p2': newer}, splits_by_ticker={})
    assert out['fills'] == []
    assert out['order_updates'][1] == {'order_id': 'new', 'status': 'rejected', 'status_reason': 'chain_consumed'}


def test_validity_expired_reason_for_both_expiry_paths():
    late = settle(date(2026, 10, 26), bars={'NVDA': bar(date(2026, 10, 26))})
    assert late['order_updates'] == [{'order_id': 'o', 'status': 'expired', 'status_reason': 'validity_expired'}]
    entry_window = settle(D3, p=plan('next_open'), bars={'NVDA': bar(D3, 110, 112, 106, 108)})
    assert entry_window['order_updates'][0]['status'] == 'expired'
    assert entry_window['order_updates'][0]['status_reason'] == 'validity_expired'


def test_target1_half_rounds_down_to_whole_shares():
    out = settle(D1, p=plan(target1=D('103')), orders=[order(shares=61)], bars={'NVDA': bar(D1, 100, 104, 99, 101)})
    buy, sell = out['fills']
    assert (buy['quantity'], sell['quantity'], sell['detail']['exit_reason']) == (D('61'), D('30'), 'target1_half')
    book = positions.build([{**buy, 'id': 'a'}, {**sell, 'id': 'b'}])
    assert book['position']['quantity'] == D('31') and out['states']['NVDA'].t1_done


def test_target1_half_of_one_share_sells_nothing_but_state_moves_on():
    out = settle(D1, p=plan(target1=D('103')), orders=[order(shares=1)], bars={'NVDA': bar(D1, 100, 104, 99, 101)})
    assert [(f['side'], f['quantity']) for f in out['fills']] == [('buy', D('1'))]
    assert out['states']['NVDA'].t1_done and out['states']['NVDA'].stop == D('100')


def test_agent_half_rounds_down_and_lone_share_is_not_sold():
    state = plan_shadow.PositionState(D('100'), D('94'), D('94'), D('6'))
    sell = {'id': 's', 'account_id': 'account', 'ticker': 'NVDA', 'action': 'sell', 'portion': 'half',
            'placed_on': D1, 'status': 'pending', 'plan_id': None, 'chain_signal_date': None}
    held = [{'ticker': 'NVDA', 'quantity': D('61'), 'plan_id': 'p', 'state': state}]
    out = settle(D2, orders=[sell], holdings=held, bars={'NVDA': bar(D2, 101, 103, 99, 101)})
    assert [(f['quantity'], f['detail']['exit_reason']) for f in out['fills']] == [(D('30'), 'agent_sell')]
    assert out['order_updates'] == [{'order_id': 's', 'status': 'filled', 'status_reason': None}]
    held = [{'ticker': 'NVDA', 'quantity': D('1'), 'plan_id': 'p', 'state': state}]
    lone = settle(D2, orders=[sell], holdings=held, bars={'NVDA': bar(D2, 101, 103, 99, 101)})
    assert lone['fills'] == [] and lone['order_updates'] == [
        {'order_id': 's', 'status': 'rejected', 'status_reason': 'cannot_halve'}]
    assert 'NVDA' in lone['states']


@pytest.mark.parametrize('split, held, sold', [(D('2'), D('122'), D('30')), (D('0.1'), D('1.5'), D('7'))])
def test_half_rounds_on_the_execution_day_basis_when_a_later_split_is_known(split, held, sold):
    # Catch-up settlement of D2 after a split on D3 is already known: holdings arrive in the later basis.
    state = plan_shadow.PositionState(D('100'), D('94'), D('94'), D('6'))
    sell = {'id': 's', 'account_id': 'account', 'ticker': 'NVDA', 'action': 'sell', 'portion': 'half',
            'placed_on': D1, 'status': 'pending', 'plan_id': None, 'chain_signal_date': None}
    holding = [{'ticker': 'NVDA', 'quantity': held, 'plan_id': 'p', 'state': state}]
    # Plan levels are stored on the signal day's basis; settlement divides them by the same split factor.
    levels = lambda target1: plan(**{k: v * split for k, v in plan(target1=target1).items()
                                     if k in ('entry_low', 'entry_high', 'stop', 'target1', 'atr')})
    out = settle(D2, orders=[sell], holdings=holding, p=levels(D('112')), splits={'NVDA': [(D3, split)]},
                 bars={'NVDA': bar(D2, 101, 103, 99, 101)})
    assert [(f['quantity'], f['detail']['exit_reason']) for f in out['fills']] == [(sold, 'agent_sell')]
    t1 = settle(D2, orders=[], holdings=holding, p=levels(D('102')), splits={'NVDA': [(D3, split)]},
                bars={'NVDA': bar(D2, 101, 103, 99, 101)})
    assert [(f['quantity'], f['detail']['exit_reason']) for f in t1['fills']] == [(sold, 'target1_half')]


def test_reason_text_tables_cover_every_enum():
    eng = engine()
    from investment_assistant.execution import entry_rules
    assert set(eng.CANCEL_REASON_TEXT) == {'superseded_by_revision', 'chain_ended', 'agent_cancel',
                                           'validity_expired', 'account_closed'}
    assert set(eng.VOID_REASON_TEXT) == set(entry_rules.VOID_REASONS)
    assert set(eng.CHAIN_DONE_TEXT) == {'entry_voided', 'exited'}
