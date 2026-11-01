"""v1 rules on synthetic bars (all prices below are fabricated test data)."""
from datetime import date, datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest

from investment_assistant.data.calendar import add_trading_days
from investment_assistant.analysis import rules

NY = ZoneInfo('America/New_York')
T = date(2026, 10, 22)  # Thursday


def series(closes, *, opens=None, volumes=None, end=T):
    """Bars ending on ``end``; adj_close == close unless overridden later."""
    n = len(closes)
    days = [add_trading_days(end, -(n - 1 - i)) for i in range(n)]
    opens = opens or closes
    volumes = volumes or [1000] * n
    return [{'id': i + 1, 'price_date': d, 'open': float(o), 'high': float(max(o, c)), 'low': float(min(o, c)),
             'close': float(c), 'adj_close': float(c), 'volume': int(v), 'split_factor': 1.0}
            for i, (d, o, c, v) in enumerate(zip(days, opens, closes, volumes))]


def ids(result, action=None):
    return [c.rule_id for c in result['candidates'] if action is None or c.action == action]


def run(bars, filings=(), position=None, plan_state=None):
    return rules.evaluate('NVDA', T, bars, list(filings), position, plan_state=plan_state)


def earnings(accepted, items=('2.02',), fid=7):
    return {'id': fid, 'form': '8-K', 'items': list(items), 'filing_date': accepted.date() if accepted else T,
            'accepted_at': accepted}


def ny(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=NY)


# --- preconditions ---

def test_insufficient_history_skips_price_rules():
    result = run(series([100] * 150))
    assert result['candidates'] == []
    assert {s.reason for s in result['skips']} == {'insufficient_history'}


def test_missing_signal_bar_is_reported_not_treated_as_no_signal():
    result = run(series([100] * 250, end=add_trading_days(T, -1)))
    assert result['candidates'] == [] and {s.reason for s in result['skips']} == {'missing_signal_bar'}


def test_adjustment_factor_jump_is_anomaly():
    bars = series([100] * 250)
    for b in bars[-5:]:
        b['adj_close'] = b['close'] * 0.5
    assert {s.reason for s in run(bars)['skips']} == {'adjustment_anomaly'}


def test_new_filing_still_evaluated_without_price_history():
    result = run(series([100] * 10), filings=[earnings(ny(2026, 10, 22, 8))])
    assert ids(result) == ['new_filing']


# --- B1 trend_reclaim ---

def uptrend_with_dip(dip_days):
    closes = [50 + i * 0.25 for i in range(240)]          # steady uptrend, ends ~109.75
    top = closes[-1]
    closes += [top - 6] * dip_days + [top + 1]             # dip below MA20, then reclaim on T
    return closes


def test_b1_triggers_after_three_days_below_ma20():
    result = run(series(uptrend_with_dip(3)))
    assert 'trend_reclaim' in ids(result, 'buy')
    cand = next(c for c in result['candidates'] if c.rule_id == 'trend_reclaim')
    assert cand.inputs['thresholds'] == {'days_below_ma20': 3}
    assert cand.inputs['bar_ids'] == [244]


def test_b1_needs_three_full_days_below():
    assert 'trend_reclaim' not in ids(run(series(uptrend_with_dip(2))))


def test_b1_requires_trend_stack():
    closes = [150 - i * 0.25 for i in range(240)]
    closes += [closes[-1] - 3] * 3 + [closes[-1] + 2]
    assert 'trend_reclaim' not in ids(run(series(closes)))


# --- B2 volume_breakout ---

def breakout(volume_multiple):
    closes = [100 + (i % 5) * 0.1 for i in range(249)] + [105]
    volumes = [1000] * 249 + [int(1000 * volume_multiple)]
    return series([c + i * 0.05 for i, c in enumerate(closes)], volumes=volumes)


def test_b2_triggers_on_new_high_with_volume():
    assert 'volume_breakout' in ids(run(breakout(1.5)), 'buy')


def test_b2_volume_just_below_threshold():
    assert 'volume_breakout' not in ids(run(breakout(1.49)))


# --- earnings timing ---

@pytest.mark.parametrize('accepted, session', [
    (ny(2026, 10, 21, 16, 20), T),                    # after close -> next session
    (ny(2026, 10, 22, 7, 0), T),                      # pre-market -> same session
    (ny(2026, 10, 22, 11, 0), None),                  # intraday -> gap not attributable
    (ny(2026, 10, 18, 12, 0), date(2026, 10, 19)),    # Sunday -> Monday
])
def test_earnings_reaction_session(accepted, session):
    assert rules.earnings_session(accepted) == session


def gap_bars(open_t, close_t):
    closes = [100.0] * 249 + [close_t]
    opens = [100.0] * 249 + [open_t]
    return series(closes, opens=opens)


def test_b3_gap_up_that_holds():
    result = run(gap_bars(103, 104), filings=[earnings(ny(2026, 10, 21, 16, 20))])
    assert 'earnings_gap_up' in ids(result, 'buy')
    cand = next(c for c in result['candidates'] if c.rule_id == 'earnings_gap_up')
    assert cand.inputs['filing_ids'] == [7] and cand.inputs['values']['gap'] == pytest.approx(0.03)


def test_b3_gap_that_fades_does_not_trigger():
    assert 'earnings_gap_up' not in ids(run(gap_bars(103, 102.5), filings=[earnings(ny(2026, 10, 21, 16, 20))]))


def test_b3_needs_item_202():
    filing = earnings(ny(2026, 10, 21, 16, 20), items=('8.01',))
    assert 'earnings_gap_up' not in ids(run(gap_bars(104, 105), filings=[filing]))


def test_unknown_acceptance_blocks_gap_rules_but_not_w1():
    filing = earnings(None)
    result = run(gap_bars(104, 105), filings=[filing])
    assert 'earnings_gap_up' not in ids(result)
    assert 'new_filing' in ids(result, 'watch')
    assert 'earnings_time_unknown' in {s.reason for s in result['skips']}


def test_s2_gap_down_that_holds():
    result = run(gap_bars(97, 96.5), filings=[earnings(ny(2026, 10, 22, 6, 0))])
    assert 'earnings_gap_down' in ids(result, 'sell')


# --- S1 trend_break ---

def test_s1_break_after_ten_days_above_ma50():
    closes = [100 + i * 0.1 for i in range(249)] + [100]
    assert 'trend_break' in ids(run(series(closes)), 'sell')


def test_s1_not_when_already_below():
    closes = [100 + i * 0.1 for i in range(240)] + [100] * 9 + [99]
    assert 'trend_break' not in ids(run(series(closes)))


# --- S3 position_drawdown ---

def held(avg_cost, opened=date(2026, 10, 1)):
    return {'quantity': D('10'), 'avg_cost': D(str(avg_cost)),
            'opened_at': datetime(opened.year, opened.month, opened.day, 15, tzinfo=NY)}


def test_s3_only_with_position():
    bars = series([100.0] * 249 + [91.0])
    assert 'position_drawdown' not in ids(run(bars))
    assert 'position_drawdown' in ids(run(bars, position=held(100)), 'sell')


def test_s3_cost_threshold_boundary():
    bars = series([100.0] * 249 + [92.01])
    assert 'position_drawdown' not in ids(run(bars, position=held(100)))


def test_s3_trailing_drawdown_from_high_since_opened():
    closes = [100.0] * 240 + [130.0] + [120.0] * 8 + [114.0]   # 12.3% off the 130 high, still above cost
    result = run(series(closes), position=held(100, opened=date(2026, 9, 1)))
    cand = next(c for c in result['candidates'] if c.rule_id == 'position_drawdown')
    assert cand.inputs['values']['triggered'] == ['trailing']


# --- W2 abnormal_move ---

def wobbly(last):
    closes = [100 + (1 if i % 2 else -1) for i in range(249)] + [last]
    return series(closes)


def test_w2_flags_large_move_without_filing():
    assert 'abnormal_move' in ids(run(wobbly(115)), 'watch')


def test_w2_suppressed_when_filing_that_day():
    result = run(wobbly(115), filings=[earnings(ny(2026, 10, 22, 7, 0), items=('8.01',))])
    assert 'abnormal_move' not in ids(result)


def test_every_candidate_carries_rule_version_and_signal_bar():
    result = run(series(uptrend_with_dip(3)))
    for c in result['candidates']:
        assert c.rule_version == '1'
        assert c.inputs['signal_date'] == T.isoformat()


# --- S8 plan_stop / plan_target ---

def plan_state(**overrides):
    state = {'stop': D('94'), 't1_reached': False, 't1_reached_on': None, 'mode': 'initial', 'target1': D('112')}
    state.update(overrides)
    return state


def test_plan_stop_fires_on_low():
    result = run(series([100] * 249 + [92]), position=held(100), plan_state=plan_state(stop=D('95')))
    cand = next(c for c in result['candidates'] if c.rule_id == 'plan_stop')
    assert cand.action == 'sell'
    assert cand.inputs['values'] == {'low': 92.0, 'stop': 95.0, 'mode': 'initial'}
    assert cand.rule_version == '1'


def test_plan_stop_quiet_above_stop():
    result = run(series([100] * 249 + [99]), position=held(100), plan_state=plan_state(stop=D('95')))
    assert 'plan_stop' not in ids(result) and 'plan_target' not in ids(result)


def test_plan_target_fires_once():
    bars = series([100] * 249 + [113])
    result = run(bars, position=held(100), plan_state=plan_state())
    cand = next(c for c in result['candidates'] if c.rule_id == 'plan_target')
    assert cand.action == 'sell'
    assert cand.inputs['values'] == {'high': 113.0, 'target1': 112.0, 'sell_fraction': 0.5}
    # 已触及 T1（之前已触发过）则不再触发
    again = run(bars, position=held(100),
                plan_state=plan_state(t1_reached=True, t1_reached_on=add_trading_days(T, -1), mode='trailing',
                                      stop=D('111')))
    assert 'plan_target' not in ids(again)


def test_drawdown_skipped_with_plan():
    result = run(series([100] * 249 + [92]), position=held(100), plan_state=plan_state(stop=D('80')))
    assert 'position_drawdown' not in ids(result)
    assert 'plan_stop' not in ids(result)


def test_drawdown_runs_without_plan():
    result = run(series([100] * 249 + [92]), position=held(100))
    assert 'position_drawdown' in ids(result, 'sell')
