"""entry-1: entry types, the three open scenarios, void_when and the entry replay (v2.9 spec §5.2, §5.3)."""
from datetime import date
from decimal import Decimal

import pytest

from investment_assistant.execution import entry_rules

D = Decimal
F = date(2026, 11, 2)            # first valid day (Monday)
E = date(2026, 11, 4)            # last valid day


def _plan(entry_type='zone_limit', **overrides):
    plan = {'ticker': 'MU', 'entry_low': D('95.10'), 'entry_high': D('97.30'), 'stop': D('91.40'),
            'target1': D('104.70'), 'entry_type': entry_type, 'valid_from': F, 'valid_until': E,
            'max_hold_sessions': 10, 'sizing': {'shares': 120}}
    plan.update(overrides)
    return plan


def _bar(day, op, hi, lo, cl=None):
    return {'price_date': day, 'open': D(str(op)), 'high': D(str(hi)), 'low': D(str(lo)),
            'close': D(str(cl if cl is not None else op))}


DAY1, DAY2, DAY3 = F, date(2026, 11, 3), date(2026, 11, 4)


# ---------------------------------------------------------------- constants and text

def test_constants():
    assert entry_rules.ENTRY_RULES_VERSION == 'entry-1'
    assert entry_rules.ENTRY_TYPES == ('zone_limit', 'breakout_stop', 'next_open')
    assert entry_rules.ENTRY_TYPE_TEXT == {'zone_limit': '区间限价', 'breakout_stop': '突破买入',
                                            'next_open': '次日开盘'}
    assert entry_rules.OPEN_CASES == ('in_zone', 'gap_up', 'gap_down')


EXPECTED = {
    ('zone_limit', 'in_zone'): ('buy_open', '开在 95.10–97.30：开盘买入 120 股'),
    ('zone_limit', 'gap_up'): ('limit_upper', '高开过 97.30：不追，挂 97.30 限价等回落'),
    ('zone_limit', 'gap_down'): ('stop_buy_lower', '低开：≤ 91.40 作废；否则回升到 95.10 时买入'),
    ('breakout_stop', 'in_zone'): ('buy_open', '开在 95.10–97.30：开盘买入 120 股'),
    ('breakout_stop', 'gap_up'): ('limit_upper', '高开过 97.30：不追，挂 97.30 限价等回落'),
    ('breakout_stop', 'gap_down'): ('stop_buy_lower', '低开：≤ 91.40 作废；否则升破 95.10 时买入'),
    ('next_open', 'in_zone'): ('buy_open', '开在 95.10–97.30：开盘买入 120 股'),
    ('next_open', 'gap_up'): ('void', '高开过 97.30：不买，本计划作废'),
    ('next_open', 'gap_down'): ('void', '低开过 95.10：不买，本计划作废'),
}


@pytest.mark.parametrize('entry_type', entry_rules.ENTRY_TYPES)
def test_scenarios_text_exact(entry_type):
    rows = entry_rules.scenarios(_plan(entry_type))
    assert [r['case'] for r in rows] == ['in_zone', 'gap_up', 'gap_down']
    for row in rows:
        action, text = EXPECTED[(entry_type, row['case'])]
        assert (row['action'], row['text']) == (action, text), (entry_type, row['case'])


def test_scenarios_shares_missing_and_default_type():
    row = entry_rules.scenarios(_plan(sizing={'shares': None}))[0]
    assert row['text'] == '开在 95.10–97.30：开盘买入 — 股'
    row = entry_rules.scenarios(_plan(entry_type=None, sizing=None))[0]
    assert row['text'] == '开在 95.10–97.30：开盘买入 — 股'
    assert entry_rules.scenarios(_plan(entry_type=None))[1]['action'] == 'limit_upper'     # reads as zone_limit


def test_case_of():
    plan = _plan()
    assert entry_rules.case_of(plan, '95.10') == 'in_zone' and entry_rules.case_of(plan, '97.30') == 'in_zone'
    assert entry_rules.case_of(plan, '97.31') == 'gap_up' and entry_rules.case_of(plan, '95.09') == 'gap_down'


# ---------------------------------------------------------------- void_when

def test_void_when_order_and_sources():
    conditions = [{'kind': 'close_below', 'params': {'price': '92.00'}, 'last_result': 'not_met'},
                  {'kind': 'earnings_within', 'params': {'sessions': 3}, 'last_result': None},
                  {'kind': 'held_sessions_reached', 'params': {'sessions': 10}}]      # host-injected: never listed
    rows = entry_rules.void_when(_plan(), conditions=conditions, mode='entry', entry_cap=None)
    assert [r['source'] for r in rows] == ['validity', 'gap', 'invalidation', 'invalidation', 'max_hold', 'stop']
    assert rows[0]['text'] == '有效期至 11-04 收盘仍未成交'
    assert rows[1]['text'] == '开盘 ≤ 91.40'
    assert rows[2] == {'source': 'invalidation', 'text': '收盘跌破 92.00', 'last_result': 'not_met'}
    assert rows[3]['text'] == '距财报不足 3 个交易日' and rows[3]['last_result'] is None
    assert rows[4]['text'] == '持仓后：持有 10 个交易日仍未到 1R → 次日开盘卖出'
    assert rows[5]['text'] == '持仓后：收盘前触及止损 91.40（T1 之后为移动止损）'


def test_void_when_rule_baseline_has_four_sources_and_cap_earlier():
    rows = entry_rules.void_when(_plan(), conditions=[], mode='entry', entry_cap=date(2026, 11, 3))
    assert [r['source'] for r in rows] == ['validity', 'gap', 'max_hold', 'stop']
    assert rows[0]['text'] == '有效期至 11-03 收盘仍未成交'                       # the cap is earlier than valid_until


def test_void_when_next_open_gap_and_holding_mode():
    rows = entry_rules.void_when(_plan('next_open'), conditions=[], mode='entry', entry_cap=None)
    assert rows[1] == {'source': 'gap', 'text': '首个有效日开盘不在 95.10–97.30 内'}
    held = entry_rules.void_when(_plan(), conditions=[{'kind': 'sector_weak', 'params': {}}], mode='holding',
                                 entry_cap=None)
    assert [r['source'] for r in held] == ['invalidation', 'max_hold', 'stop']
    assert held[1]['text'] == '持有 10 个交易日仍未到 1R → 次日开盘卖出'
    with pytest.raises(ValueError):
        entry_rules.void_when(_plan(), conditions=[], mode='exiting', entry_cap=None)


def test_void_when_plan_without_max_hold_skips_that_line():
    rows = entry_rules.void_when(_plan(max_hold_sessions=None), conditions=[], mode='entry', entry_cap=None)
    assert [r['source'] for r in rows] == ['validity', 'gap', 'stop']


# ---------------------------------------------------------------- simulate_entry

def test_simulate_entry_in_zone_fills_at_open():
    out = entry_rules.simulate_entry(_plan(), [_bar(DAY1, 96, 98, 94)])
    assert (out['status'], out['fill_date'], out['fill_price'], out['case']) == ('filled', DAY1, D('96'), 'in_zone')


def test_gap_up_fills_at_upper_when_low_reaches():
    bars = [_bar(DAY1, 99, 101, 98), _bar(DAY2, 99, 100, 97.0)]           # day 1 never comes back; day 2 reaches 97.30
    out = entry_rules.simulate_entry(_plan(), bars)
    assert (out['status'], out['fill_date'], out['fill_price'], out['case']) == ('filled', DAY2, D('97.30'), 'gap_up')
    held_off = entry_rules.simulate_entry(_plan(), [_bar(DAY1, 99, 101, 98)])
    assert held_off['status'] == 'pending'                                  # not over yet


def test_gap_down_below_stop_voids():
    out = entry_rules.simulate_entry(_plan(), [_bar(DAY1, 91.0, 99, 90)])   # opens under the stop, rebounds to 99
    assert (out['status'], out['void_reason'], out['void_date'], out['fill_price']) == \
        ('voided', 'gap_below_stop', DAY1, None)
    at_stop = entry_rules.simulate_entry(_plan(), [_bar(DAY1, 91.40, 99, 90)])
    assert at_stop['status'] == 'voided'                                    # O <= S, equality included


def test_gap_down_above_stop_fills_at_lower():
    out = entry_rules.simulate_entry(_plan(), [_bar(DAY1, 93, 96, 92)])
    assert (out['status'], out['fill_date'], out['fill_price'], out['case']) == \
        ('filled', DAY1, D('95.10'), 'gap_down')
    no_rebound = entry_rules.simulate_entry(_plan(), [_bar(DAY1, 93, 94.5, 92), _bar(DAY2, 96, 98, 95)])
    assert (no_rebound['fill_date'], no_rebound['fill_price']) == (DAY2, D('96'))     # day 2 opens inside


def test_next_open_only_first_session():
    # day 1 gaps up and would be buyable later; next_open must not look at day 2 (its open is in the zone)
    out = entry_rules.simulate_entry(_plan('next_open'), [_bar(DAY1, 99, 101, 98), _bar(DAY2, 96, 98, 95)])
    assert (out['status'], out['void_reason']) == ('voided', 'gap_above_zone')
    ok = entry_rules.simulate_entry(_plan('next_open'), [_bar(DAY1, 96, 98, 95), _bar(DAY2, 80, 81, 79)])
    assert (ok['status'], ok['fill_price']) == ('filled', D('96'))
    missing_first = entry_rules.simulate_entry(_plan('next_open'), [_bar(DAY2, 96, 98, 95)])
    assert missing_first['status'] == 'pending'                             # no bar for the first valid day yet


def test_next_open_gap_voids():
    below = entry_rules.simulate_entry(_plan('next_open'), [_bar(DAY1, 93, 99, 92)])
    assert (below['status'], below['void_reason']) == ('voided', 'gap_below_zone')
    under_stop = entry_rules.simulate_entry(_plan('next_open'), [_bar(DAY1, 90, 99, 89)])
    assert (under_stop['status'], under_stop['void_reason']) == ('voided', 'gap_below_stop')


def test_expired_and_pending():
    bars = [_bar(DAY1, 99, 101, 98), _bar(DAY2, 99, 101, 98), _bar(DAY3, 99, 101, 98)]
    assert entry_rules.simulate_entry(_plan(), bars)['status'] == 'expired'
    assert entry_rules.simulate_entry(_plan(), bars[:2])['status'] == 'pending'
    assert entry_rules.simulate_entry(_plan(), [])['status'] == 'pending'
    # bars outside the validity window are ignored
    early = [_bar(date(2026, 10, 30), 96, 98, 94)]
    assert entry_rules.simulate_entry(_plan(), early)['status'] == 'pending'
