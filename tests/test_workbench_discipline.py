"""Discipline flags against confirmed plans (spec §6.2)."""
from datetime import date, datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest

from investment_assistant.data.calendar import add_trading_days
from investment_assistant.review import discipline

NY = ZoneInfo('America/New_York')
D0, D1, D2, D3, D4 = (date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23),
                      date(2026, 10, 26))


def at(day, hour=15):
    return datetime(day.year, day.month, day.day, hour, tzinfo=NY)


def bar(day, o=100, h=101, l=99, c=100):
    return {'id': 1, 'price_date': day, 'open': float(o), 'high': float(h), 'low': float(l), 'close': float(c),
            'adj_close': float(c), 'volume': 1, 'split_factor': 1.0}


def trade(id_, ticker='NVDA', side='buy', price=100, executed=None, decision_id='d1', quantity=10):
    return {'id': id_, 'ticker': ticker, 'side': side, 'quantity': quantity, 'price': str(price),
            'executed_at': executed or at(D1), 'decision_id': decision_id, 'voided_at': None}


def decision(id_='d1', plan_id='p1'):
    return {'id': id_, 'plan_id': plan_id}


def plan(id_='p1', kind='rule'):
    return {'id': id_, 'kind': kind, 'ticker': 'NVDA', 'signal_date': D0, 'entry_low': D('98'),
            'entry_high': D('102'), 'stop': D('94'), 'target1': D('112')}


def segment(buy='b1', sell='s1', *, cost=100, exit_=105, opened=D1, closed=D2, quantity=10):
    return {'buy_trade_id': buy, 'sell_trade_id': sell, 'quantity': quantity,
            'entry_cost_per_share': D(cost), 'exit_price': D(exit_), 'opened_at': at(opened),
            'closed_at': at(closed)}


def evaluate(trades, *, decisions=None, plans=None, bars=None, stop_events=(), segments=(), session=D4):
    return discipline.evaluate(
        trades=trades,
        decisions_by_id=decisions if decisions is not None else {'d1': decision()},
        plans_by_id=plans if plans is not None else {'p1': plan()},
        bars_by_ticker={'NVDA': bars if bars is not None else [bar(D1), bar(D2), bar(D3), bar(D4)]},
        stop_events=list(stop_events), segments=list(segments), session=session)


def test_chase_above_entry_high():
    out = evaluate([trade('b1', price=103)])
    assert out['trades'][0]['flags'] == ['chase']
    assert out['counts'] == {'chase': 1, 'early_take': 0, 'held_loser': 0, 'unplanned': 0}
    assert out['compliance_rate'] == 0.0


def test_unplanned_without_decision():
    out = evaluate([trade('b1', decision_id=None)])
    assert out['trades'][0]['flags'] == ['unplanned']
    assert out['planned_buys'] == 0 and out['compliance_rate'] is None


def test_unplanned_decision_without_plan():
    out = evaluate([trade('b1')], decisions={'d1': decision(plan_id=None)})
    assert out['trades'][0]['flags'] == ['unplanned']


def test_early_take_matures_after_20_sessions():
    sell_day = D2
    later_t1 = add_trading_days(sell_day, 21)
    bars = [bar(D1), bar(sell_day), bar(add_trading_days(sell_day, 1))]
    bars += [bar(add_trading_days(sell_day, 5), h=113)]          # T1 到达（卖出后 20 个交易日内）
    bars += [bar(later_t1)]
    trades = [trade('b1', price=100, executed=at(D1)), trade('s1', side='sell', price=105, executed=at(sell_day))]

    immature = evaluate(trades, bars=bars, segments=[segment(closed=sell_day)],
                        session=add_trading_days(sell_day, 19))
    assert 'early_take' in immature['trades'][0]['immature']
    assert immature['counts']['early_take'] == 0

    mature = evaluate(trades, bars=bars, segments=[segment(closed=sell_day)],
                      session=add_trading_days(sell_day, 20))
    assert 'early_take' in mature['trades'][0]['flags']


def test_sell_after_plan_stop_not_early_take():
    bars = [bar(D1), bar(D2), bar(D3), bar(D4, h=113)]
    trades = [trade('b1', price=100, executed=at(D1)), trade('s1', side='sell', price=95, executed=at(D2))]
    out = evaluate(trades, bars=bars, segments=[segment(exit_=95, closed=D2)], session=D4,
                   stop_events=[{'ticker': 'NVDA', 'signal_date': D2, 'rule_id': 'plan_stop'}])
    assert out['trades'][0]['flags'] == []


def test_held_loser_when_no_sell_next_session():
    out = evaluate([trade('b1', price=100)], session=D3,
                   stop_events=[{'ticker': 'NVDA', 'signal_date': D2, 'rule_id': 'plan_stop'}])
    assert 'held_loser' in out['trades'][0]['flags']
    assert out['counts']['held_loser'] == 1


def test_early_take_after_stop_window_still_flags():
    # 止损日后第 3 个交易日才卖出：超出「当日/次日」豁免窗口，T1 随后到位仍算过早止盈
    sell_day = add_trading_days(D2, 3)
    bars = [bar(D1), bar(D2), bar(sell_day), bar(add_trading_days(sell_day, 2), h=113)]
    trades = [trade('b1', price=100, executed=at(D1)), trade('s1', side='sell', price=105, executed=at(sell_day))]
    out = evaluate(trades, bars=bars, segments=[segment(closed=sell_day)],
                   session=add_trading_days(sell_day, 20),
                   stop_events=[{'ticker': 'NVDA', 'signal_date': D2, 'rule_id': 'plan_stop'}])
    assert 'early_take' in out['trades'][0]['flags']


def test_held_loser_cleared_by_next_day_sell():
    trades = [trade('b1', price=100, executed=at(D1)), trade('s1', side='sell', price=95, executed=at(D3))]
    out = evaluate(trades, session=D4,
                   stop_events=[{'ticker': 'NVDA', 'signal_date': D2, 'rule_id': 'plan_stop'}])
    assert out['trades'][0]['flags'] == []


def test_compliance_rate():
    trades = [trade('b1', price=100, executed=at(D1)), trade('b2', price=103, executed=at(D1)),
              trade('b3', decision_id=None)]
    out = evaluate(trades)
    assert out['planned_buys'] == 2
    assert out['counts'] == {'chase': 1, 'early_take': 0, 'held_loser': 0, 'unplanned': 1}
    assert out['compliance_rate'] == 0.5


def test_actual_r_uses_plan_stop():
    trades = [trade('b1', price=100, executed=at(D1)), trade('s1', side='sell', price=106, executed=at(D2))]
    out = evaluate(trades, segments=[segment(exit_=106, closed=D2)])
    assert out['trades'][0]['r'] == pytest.approx(1.0)  # (106-100)/(100-94)，不是成本回撤口径


def test_track_from_plan_kind():
    out = evaluate([trade('b1')], plans={'p1': plan(kind='own')})
    assert out['trades'][0]['track'] == 'own'


def test_early_take_ignores_highs_before_the_buy():
    # 买入前很久曾高于 T1，不能算作「卖出时已触及 T1」
    sell_day = D2
    bars = [bar(add_trading_days(D1, -30), h=150), bar(D1), bar(sell_day)]
    bars += [bar(add_trading_days(sell_day, 5), h=113)]          # 卖出后 20 个交易日内 T1 到达
    trades = [trade('b1', price=100, executed=at(D1)), trade('s1', side='sell', price=105, executed=at(sell_day))]
    out = evaluate(trades, bars=bars, segments=[segment(closed=sell_day)], session=add_trading_days(sell_day, 20))
    assert 'early_take' in out['trades'][0]['flags']


def test_discipline_track_agent():
    out = evaluate([trade('b1')], plans={'p1': plan(kind='agent')})
    assert out['trades'][0]['track'] == 'agent'


# --- v2.9 discipline-2 (spec D28) ---

def v2_trade(id_, *, plan_id='p1', decision_id=None, **kwargs):
    return {**trade(id_, decision_id=decision_id, **kwargs), 'plan_id': plan_id}


def vplan(id_='p1', *, kind='agent', entry_high=102, stop=94, target1=112, valid_from=D1, valid_until=D3, **over):
    return {**plan(id_, kind=kind), 'entry_high': D(entry_high), 'stop': D(stop), 'target1': D(target1),
            'valid_from': valid_from, 'valid_until': valid_until, **over}


def evaluate_v2(trades, *, plans_by_trade=None, heads=None, holding=(), legacy=None, plans_by_id=None, bars=None,
                segments=(), session=D4, stop_events=()):
    return discipline.evaluate_v2(
        trades=trades, plans_by_trade=plans_by_trade if plans_by_trade is not None else {},
        chain_head_by_trade=heads if heads is not None else {}, holding_rows=list(holding),
        legacy_decisions=legacy if legacy is not None else {}, plans_by_id=plans_by_id,
        bars_by_ticker={'NVDA': bars if bars is not None else [bar(D1), bar(D2), bar(D3), bar(D4)]},
        segments=list(segments), session=session, stop_events=stop_events)


def test_versions():
    assert discipline.DISCIPLINE_V1 == 'discipline-1' and discipline.DISCIPLINE_VERSION == 'discipline-2'


def test_evaluate_v1_unchanged():
    assert discipline.evaluate_v1 is discipline.evaluate
    out = evaluate([trade('b1', price=103), trade('b2', decision_id=None)])
    assert [t['flags'] for t in out['trades']] == [['chase'], ['unplanned']]
    assert out['counts'] == {'chase': 1, 'early_take': 0, 'held_loser': 0, 'unplanned': 1}
    assert out['compliance_rate'] == 0.0 and out['planned_buys'] == 1
    assert set(out) == {'trades', 'counts', 'immature', 'planned_buys', 'compliance_rate'}


def test_unplanned_no_plan_id():
    out = evaluate_v2([v2_trade('b1', plan_id=None)])
    assert out['trades'][0]['flags'] == ['unplanned'] and out['trades'][0]['track'] is None
    assert out['planned_buys'] == 0 and out['compliance_rate'] is None
    assert out['counts']['unplanned'] == 1


def test_unplanned_out_of_validity():
    # the linked version is valid D1-D3; a fill on D4 is outside it: unplanned, judged by no other flag
    late = v2_trade('b1', price=103, executed=at(D4))                                 # would also be a chase
    out = evaluate_v2([late], plans_by_trade={'b1': vplan()})
    assert out['trades'][0]['flags'] == ['unplanned'] and out['trades'][0]['track'] == 'agent'
    assert out['planned_buys'] == 0 and out['counts']['chase'] == 0
    inside = evaluate_v2([v2_trade('b2', executed=at(D3))], plans_by_trade={'b2': vplan()})
    assert inside['trades'][0]['flags'] == [] and inside['planned_buys'] == 1 and inside['compliance_rate'] == 1.0
    edge = evaluate_v2([v2_trade('b3', executed=at(D1))], plans_by_trade={'b3': vplan()})
    assert edge['trades'][0]['flags'] == []                                           # both validity ends count


def test_chase_vs_linked_version():
    # v0 (entry_high 102) and a later version (entry_high 106): the buy followed v0, so 103 is a chase of v0
    v0, v1 = vplan('v0'), vplan('v1', entry_high=106)
    out = evaluate_v2([v2_trade('b1', plan_id='v0', price=103)], plans_by_trade={'b1': v0}, heads={'b1': [v0, v1]})
    assert out['trades'][0]['flags'] == ['chase']
    followed = evaluate_v2([v2_trade('b2', plan_id='v1', price=103)], plans_by_trade={'b2': v1}, heads={'b2': [v0, v1]})
    assert followed['trades'][0]['flags'] == []


def test_early_take_vs_head_t1():
    # sold at 105 on D2 before the version it followed (T1 112) hit; the chain head at the sale has T1 108, which the
    # next week's high of 109 reached: judged against the head's T1 it is an early take, against v0's it is not
    sell_day = D2
    bars = [bar(D1), bar(sell_day), bar(add_trading_days(sell_day, 5), h=109)]
    v0 = vplan('v0', target1=112, created_at=at(D1))
    head = vplan('v1', target1=108, created_at=at(D1, 20))
    trades = [v2_trade('b1', plan_id='v0', executed=at(D1)), trade('s1', side='sell', price=105, executed=at(sell_day))]
    kwargs = dict(plans_by_trade={'b1': v0}, bars=bars, segments=[segment(closed=sell_day)],
                  session=add_trading_days(sell_day, 20))
    assert 'early_take' in evaluate_v2(trades, heads={'b1': [v0, head]}, **kwargs)['trades'][0]['flags']
    assert evaluate_v2(trades, heads={'b1': [v0]}, **kwargs)['trades'][0]['flags'] == []
    # a head created after the sale does not count
    after = vplan('v2', target1=108, created_at=at(add_trading_days(sell_day, 3)))
    assert evaluate_v2(trades, heads={'b1': [v0, after]}, **kwargs)['trades'][0]['flags'] == []


def test_held_loser_vs_holding_status_stop():
    # the effective stop of the D2 holding row is 98 and D2's low 97 traded through it; no sell by D3 -> held loser
    bars = [bar(D1), bar(D2, l=97), bar(D3), bar(D4)]
    rows = [{'ticker': 'NVDA', 'session': D1, 'stop': D('94')}, {'ticker': 'NVDA', 'session': D2, 'stop': D('98')}]
    out = evaluate_v2([v2_trade('b1')], plans_by_trade={'b1': vplan(valid_until=D4)}, holding=rows, bars=bars,
                      session=D4)
    assert 'held_loser' in out['trades'][0]['flags'] and out['counts']['held_loser'] == 1
    sold = evaluate_v2([v2_trade('b1'), trade('s1', side='sell', price=96, executed=at(D3))],
                       plans_by_trade={'b1': vplan(valid_until=D4)}, holding=rows, bars=bars, session=D4)
    assert sold['trades'][0]['flags'] == []                                          # sold the next session
    young = evaluate_v2([v2_trade('b1')], plans_by_trade={'b1': vplan(valid_until=D4)}, holding=rows, bars=bars,
                        session=D2)
    assert young['trades'][0]['immature'] == ['held_loser']                          # D3 not reached yet
    # the plan_stop rule candidates play no part in the v2 clock
    quiet = evaluate_v2([v2_trade('b1')], plans_by_trade={'b1': vplan(valid_until=D4)}, bars=bars, session=D4,
                        stop_events=[{'ticker': 'NVDA', 'signal_date': D2, 'rule_id': 'plan_stop'}])
    assert 'held_loser' not in quiet['trades'][0]['flags']


def test_legacy_trades_via_decisions():
    # a fill that predates the link: its plan comes from the old decision (no validity test, as before)
    legacy = {'d1': decision('d1', 'p1')}
    out = evaluate_v2([trade('b1', price=103, executed=at(D4))], legacy=legacy, plans_by_id={'p1': plan()})
    assert out['trades'][0]['flags'] == ['chase'] and out['trades'][0]['track'] == 'rule'
    none = evaluate_v2([trade('b2', decision_id='d9')], legacy=legacy, plans_by_id={'p1': plan()})
    assert none['trades'][0]['flags'] == ['unplanned']
    plain = evaluate_v2([trade('b3', decision_id=None)])
    assert plain['trades'][0]['flags'] == ['unplanned']
    # the planned_buys / compliance arithmetic is the v1 one
    mixed = evaluate_v2([trade('b1', price=103), trade('b2', price=100), v2_trade('b4', plan_id=None, decision_id=None)],
                        legacy=legacy, plans_by_id={'p1': plan()})
    assert mixed['planned_buys'] == 2 and mixed['compliance_rate'] == 0.5
