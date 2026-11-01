"""plan-shadow-1 simulation: entry, stop, T1 and trailing per spec §6.1 (hand-computed)."""
import investment_assistant.review.shadow as review_shadow
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.execution.repo as execution_repo
from datetime import date
from decimal import Decimal as D

import pytest

from investment_assistant.core import tx as core_tx

from investment_assistant.execution import simulation as plan_shadow

D0, D1, D2, D3, D4 = date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23), date(2026, 10, 26)


def plan(**overrides):
    p = {'id': 'p1', 'ticker': 'NVDA', 'signal_date': D0, 'kind': 'rule', 'entry_low': D('98'),
         'entry_high': D('102'), 'stop': D('94'), 'target1': D('112'), 'atr': D('2'),
         'trail_atr_multiple': D('3'), 'valid_from': D1, 'valid_until': D3}
    p.update(overrides)
    return p


def bar(day, o, h, l, c):
    return {'id': 1, 'price_date': day, 'open': float(o), 'high': float(h), 'low': float(l), 'close': float(c),
            'adj_close': float(c), 'volume': 1, 'split_factor': 1.0}


def spy(*days):
    return [bar(d, 500, 501, 499, 500) for d in days]


def sim(plan_dict, bars, splits=()):
    return plan_shadow.simulate(plan_dict, bars, spy(*(b['price_date'] for b in bars)), splits=list(splits))


def test_fill_at_open_inside_zone():
    out = sim(plan(), [bar(D1, 100, 103, 99, 101), bar(D2, 101, 104, 100, 102)])
    assert out['status'] == 'open'
    assert out['fill_date'] == D1 and out['fill_price'] == D('100')
    assert out['legs'] == []
    assert out['r'] == pytest.approx((102 - 100) / (100 - 94))
    assert out['spy_return'] == 0.0


def test_fill_at_entry_high_when_gap_up_then_dip():
    out = sim(plan(), [bar(D1, 110, 111, 101, 105)])
    assert out['fill_price'] == D('102') and out['fill_date'] == D1


def test_fill_at_entry_low_when_gap_down_then_rise():
    out = sim(plan(), [bar(D1, 90, 101, 89, 99)])
    assert out['fill_price'] == D('98') and out['fill_date'] == D1


def test_expired_without_touch():
    out = sim(plan(), [bar(D1, 90, 95, 88, 92), bar(D2, 91, 96, 89, 93), bar(D3, 92, 97, 90, 94), bar(D4, 92, 97, 90, 93)])
    assert out['status'] == 'expired' and out['legs'] == []
    assert out['r'] is None and out['fill_date'] is None


def test_stop_same_day_as_fill():
    out = sim(plan(), [bar(D1, 100, 103, 93.5, 95)])
    assert out['status'] == 'closed'
    assert out['legs'] == [{'fraction': 1.0, 'exit_date': D1.isoformat(), 'exit_price': D('94'), 'reason': 'stop'}]
    assert out['r'] == pytest.approx(-1.0)


def test_gap_below_stop_exits_at_open():
    out = sim(plan(), [bar(D1, 100, 103, 99, 101), bar(D2, 92, 93, 90, 91)])
    assert out['legs'][0]['exit_price'] == D('92') and out['legs'][0]['reason'] == 'stop'
    assert out['r'] == pytest.approx(-8 / 6)


def test_t1_half_then_trail():
    bars = [bar(D1, 100, 103, 99, 101), bar(D2, 105, 113, 104, 111), bar(D3, 106, 108, 104, 105)]
    out = sim(plan(), bars)
    assert out['status'] == 'closed'
    assert out['legs'] == [{'fraction': 0.5, 'exit_date': D2.isoformat(), 'exit_price': D('112'), 'reason': 'target1'},
                           {'fraction': 0.5, 'exit_date': D3.isoformat(), 'exit_price': D('105'), 'reason': 'trail'}]
    assert out['r'] == pytest.approx(((112 - 100) / 6 + (105 - 100) / 6) / 2)
    assert out['return'] == pytest.approx((0.5 * 112 + 0.5 * 105) / 100 - 1)


def test_stop_and_t1_same_day_stop_first():
    out = sim(plan(), [bar(D1, 100, 103, 99, 101), bar(D2, 101, 113, 93, 99)])
    assert out['legs'] == [{'fraction': 1.0, 'exit_date': D2.isoformat(), 'exit_price': D('94'), 'reason': 'stop'}]
    assert out['r'] == pytest.approx(-1.0)


def test_trail_floor_is_fill_price():
    bars = [bar(D1, 100, 103, 99, 101), bar(D2, 105, 112.5, 104, 105), bar(D3, 101, 103, 99.5, 100)]
    out = sim(plan(), bars)
    assert out['legs'][1]['exit_price'] == D('100') and out['legs'][1]['reason'] == 'trail'
    assert out['r'] == pytest.approx(((112 - 100) / 6 + 0) / 2)


def test_split_inside_window():
    # 存储口径：拆股后重抓取会把历史 bar 修订为当前基准；只有计划价位需要换算
    bars = [bar(D1, 50, 51.5, 49.5, 50), bar(D2, 50.5, 57, 49.5, 56), bar(D3, 52, 52.5, 51, 51.2)]
    out = sim(plan(), bars, splits=[(D2, D(2))])
    assert out['fill_price'] == D('50') and out['fill_date'] == D1
    # trail = 56 − 3 ×（ATR 2 ÷ 2）= 53，D3 开盘 52 跳空低于止损，按开盘出场
    assert out['legs'] == [{'fraction': 0.5, 'exit_date': D2.isoformat(), 'exit_price': D('56'), 'reason': 'target1'},
                           {'fraction': 0.5, 'exit_date': D3.isoformat(), 'exit_price': D('52'), 'reason': 'trail'}]
    assert out['r'] == pytest.approx(((56 - 50) / 3 + (52 - 50) / 3) / 2)


def test_open_reports_floating_r():
    bars = [bar(D1, 100, 103, 99, 101), bar(D2, 105, 113, 104, 111), bar(D3, 118, 121, 117, 120)]
    out = sim(plan(), bars)
    assert out['status'] == 'open'
    assert out['legs'][0]['reason'] == 'target1'
    assert out['r'] == pytest.approx(((112 - 100) / 6 + (120 - 100) / 6) / 2)
    assert out['return'] == pytest.approx((0.5 * 112 + 0.5 * 120) / 100 - 1)


def test_pending_before_valid_from():
    out = sim(plan(), [bar(D0, 100, 101, 99, 100)])
    assert out['status'] == 'pending' and out['fill_date'] is None and out['r'] is None


# --- v2.9 spec §5.6: plan-shadow-2 ---

from investment_assistant.execution import entry_rules# noqa: E402

D5 = date(2026, 10, 27)


def plan2(entry_type='zone_limit', max_hold=10, **overrides):
    return plan(entry_type=entry_type, max_hold_sessions=max_hold, sizing={'shares': 100}, **overrides)


def sim2(plan_dict, bars, splits=()):
    return plan_shadow.simulate_v2(plan_dict, bars, spy(*(b['price_date'] for b in bars)), splits=list(splits))


def test_shadow_versions():
    assert (plan_shadow.SHADOW_V1, plan_shadow.SHADOW_V2) == ('plan-shadow-1', 'plan-shadow-2')
    assert plan_shadow.SHADOW_VERSION == plan_shadow.SHADOW_V2        # the read views label each outcome with its own version


def test_shadow_v1_unchanged():
    """simulate() is the old function: its result keys and numbers do not change, voiding or time stop never happen."""
    bars = [bar(D1, 90, 93, 89, 92), bar(D2, 91, 96, 89, 93), bar(D3, 92, 99, 90, 94)]
    out = sim(plan(), bars)
    assert set(out) == {'status', 'fill_date', 'fill_price', 'legs', 'r', 'return', 'spy_return'}
    assert out['fill_price'] == D('98')             # gap below the stop-ish open rebounds to 98: v1 still fills
    # no time stop in plan-shadow-1: a long flat hold stays open
    held = [bar(D1, 100, 103, 99, 101)] + [bar(d, 101, 102, 100.5, 101) for d in (D2, D3, D4, D5)]
    assert sim(plan(), held)['status'] == 'open'


CASE_OPEN = {'in_zone': dict(o=100, h=103, l=99), 'gap_up': dict(o=105, h=106, l=101),
             'gap_down': dict(o=96, h=99, l=95), 'gap_down_under_stop': dict(o=93, h=99, l=92)}


@pytest.mark.parametrize('entry_type', entry_rules.ENTRY_TYPES)
@pytest.mark.parametrize('case', ['in_zone', 'gap_up', 'gap_down', 'gap_down_under_stop'])
def test_shadow_v2_matches_scenarios(entry_type, case):
    p = plan2(entry_type)
    spec = CASE_OPEN[case]
    rows = {r['case']: r for r in entry_rules.scenarios(p)}
    row = rows['gap_down' if case.startswith('gap_down') else case]
    out = sim2(p, [bar(D1, spec['o'], spec['h'], spec['l'], spec['o'])])
    if row['action'] == 'void' or (row['action'] == 'stop_buy_lower' and case == 'gap_down_under_stop'):
        assert out['status'] == 'voided' and out['fill_price'] is None and out['r'] is None
        assert out['void_reason'] in ('gap_below_stop', 'gap_above_zone', 'gap_below_zone')
    elif row['action'] == 'buy_open':
        assert out['fill_price'] == D(str(spec['o'])) and out['fill_date'] == D1
    elif row['action'] == 'limit_upper':
        assert out['fill_price'] == D('102')            # the limit stays at U and the low reaches it
    else:                                               # stop_buy_lower with the open above the stop
        assert out['fill_price'] == D('98')
    # the replay never fills where the text says the plan is dead
    if entry_type == 'next_open' and case != 'in_zone':
        assert out['status'] == 'voided'


def test_shadow_v2_void_shape_and_unfilled_statuses():
    out = sim2(plan2(), [bar(D1, 93, 99, 92, 95)])
    assert out == {'status': 'voided', 'fill_date': None, 'fill_price': None, 'legs': [], 'r': None, 'return': None,
                   'spy_return': None, 'void_reason': 'gap_below_stop', 'void_date': D1, 'time_stop_due': False}
    expired = sim2(plan2(), [bar(D1, 110, 111, 105, 106), bar(D2, 110, 111, 105, 106), bar(D3, 110, 111, 105, 106)])
    assert expired['status'] == 'expired' and expired['r'] is None
    assert sim2(plan2(), [bar(D0, 100, 101, 99, 100)])['status'] == 'pending'


def test_shadow_v2_same_as_v1_when_nothing_special():
    bars = [bar(D1, 100, 103, 99, 101), bar(D2, 105, 113, 104, 111), bar(D3, 106, 108, 104, 105)]
    assert sim2(plan2(), bars) == {**sim(plan(), bars), 'void_reason': None, 'void_date': None,
                                   'time_stop_due': False}


def test_time_stop_exits_next_open():
    # fill D1 at 100 (open inside 98-102), R = 100 - 94 = 6, 1R = 106; max hold 3: D3's close is day 3
    bars = [bar(D1, 100, 101, 99.5, 101), bar(D2, 101, 102, 100.5, 101), bar(D3, 101, 102, 100.5, 101.5),
            bar(D4, 102.5, 103, 100.5, 102)]
    out = sim2(plan2(max_hold=3), bars)
    assert out['status'] == 'closed'
    assert out['legs'] == [{'fraction': 1.0, 'exit_date': D4.isoformat(), 'exit_price': D('102.5'),
                            'reason': 'time_stop'}]
    assert out['r'] == pytest.approx(2.5 / 6)
    pending = sim2(plan2(max_hold=3), bars[:3])                       # day 3 closed, day 4 not yet there
    assert pending['status'] == 'open' and pending['time_stop_due'] is True and pending['legs'] == []
    early = sim2(plan2(max_hold=4), bars)                             # day 3 is too early for a 4-day limit
    assert early['status'] == 'open' and early['legs'] == []


def test_time_stop_not_applied_after_1r():
    bars = [bar(D1, 100, 101, 99.5, 101), bar(D2, 101, 108, 100.5, 107), bar(D3, 106, 107, 100.5, 101),
            bar(D4, 101, 102, 100.5, 101.5), bar(D5, 101, 102, 100.5, 101)]
    out = sim2(plan2(max_hold=3), bars)            # a close at 107 >= 106 on day 2 switches the time stop off
    assert out['status'] == 'open' and out['legs'] == [] and out['time_stop_due'] is False


def test_time_stop_off_after_t1_even_below_1r():
    # T1 = 103 is below fill + 1R = 106, so a plan can hit T1 without ever closing at +1R (R9)
    bars = [bar(D1, 100, 101, 99.5, 101), bar(D2, 101, 104, 100.5, 102), bar(D3, 101.5, 102, 100.5, 101.5),
            bar(D4, 101.5, 102, 100.5, 101.5), bar(D5, 101.5, 102, 100.5, 101.5)]
    out = sim2(plan2(max_hold=3, target1=D('103')), bars)
    assert [leg['reason'] for leg in out['legs']] == ['target1']
    assert out['status'] == 'open' and out['time_stop_due'] is False


def test_time_stop_same_day_stop_first():
    # day 3 is the max-hold day and trades through the stop: the stop exit counts, not the time stop
    bars = [bar(D1, 100, 101, 99.5, 101), bar(D2, 101, 102, 100.5, 101), bar(D3, 101, 102, 93.5, 95),
            bar(D4, 96, 97, 95, 96)]
    out = sim2(plan2(max_hold=3), bars)
    assert out['legs'] == [{'fraction': 1.0, 'exit_date': D3.isoformat(), 'exit_price': D('94'), 'reason': 'stop'}]
    assert out['r'] == pytest.approx(-1.0)


def test_simulate_both_old_plan_v1_only():
    bars = [bar(D1, 100, 103, 99, 101), bar(D2, 101, 104, 100, 102)]
    old = plan_shadow.simulate_both(plan(), bars, spy(D1, D2), splits=[])
    assert set(old) == {'v1'} and old['v1'] == sim(plan(), bars)
    new = plan_shadow.simulate_both(plan2(), bars, spy(D1, D2), splits=[])
    assert set(new) == {'v2', 'v1'} and new['v1'] == sim(plan2(), bars)
    assert new['v2']['status'] == 'open' and 'void_reason' in new['v2']
    assert not plan_shadow.has_exec_fields(plan()) and plan_shadow.has_exec_fields(plan2())
    assert not plan_shadow.has_exec_fields(plan(entry_type='zone_limit'))     # both fields or none


# --- v2.5 §9.1: one shadow per version, chain info, first / latest summaries ---

import uuid  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

from investment_assistant.review import outcomes as review

import investment_assistant.core.tx as service  # noqa: E402

NOW = datetime(2026, 10, 28, 1, 0, tzinfo=UTC)


@pytest.fixture
def db(workbench_db, monkeypatch):
    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(core_tx, 'connection', connection)
    return workbench_db


def _plan_row(conn, ticker, *, kind='agent', version='agent-2', **levels):
    row = {'ticker': ticker, 'signal_date': D2, 'kind': kind, 'based_on_id': None, 'plan_version': version,
           'ref_price': D('100'), 'atr': D('2'), 'entry_low': D('98'), 'entry_high': D('102'), 'stop': D('94'),
           'target1': D('112'), 'trail_atr_multiple': D('3'), 'valid_from': D3, 'valid_until': D4,
           'sizing': {'status': 'no_equity'}, 'status': 'ok', 'invalid_reasons': [], 'warnings': [],
           'inputs': {'bar_ids': [1]}, 'request_id': None, 'request_hash': None}
    row.update(levels)
    return execution_repo.insert_plan(conn, row)


def _version(conn, ticker, version_no, supersedes=None):
    plan = _plan_row(conn, ticker)
    run_id = uuid.uuid4()
    analysis_repo.insert_agent_run(conn, {
        'id': run_id, 'ticker': ticker, 'signal_date': D2, 'pool': 'main', 'attempt': 1,
        'mode': 'propose' if version_no == 0 else 'revise', 'agent_version': 'agent-2', 'provider': 'chatgpt',
        'model': 'gpt-test', 'cutoff_at': NOW, 'status': 'ok', 'reason': None, 'output': {'action': 'buy'},
        'counter': None, 'plan_id': plan['id'], 'input_tokens': 0, 'output_tokens': 0,
        'started_at': NOW, 'finished_at': NOW}, [])
    execution_repo.insert_plan_version(conn, {
        'plan_id': plan['id'], 'ticker': ticker, 'signal_date': D2, 'version_no': version_no,
        'supersedes_plan_id': supersedes, 'origin': 'signal' if version_no == 0 else 'review',
        'agent_run_id': run_id, 'cutoff_at': NOW, 'as_of_session': D2,
        'diff': None if version_no == 0 else [{'field': 'stop', 'before': '94', 'after': '95'}],
        'schema_version': 'agent-2'})
    return plan


def test_shadow_rows_per_version(db):
    v0 = _version(db, 'NVDA', 0)
    v1 = _version(db, 'NVDA', 1, v0['id'])
    rule = _plan_row(db, 'NVDA', kind='rule', version='plan-1')
    view = review_shadow.plan_shadow(now=NOW, since=D0)
    rows = {str(p['id']): p for p in view['plans']}
    assert len(rows) == 3
    assert rows[str(v0['id'])]['chain'] == {'version_no': 0, 'origin': 'signal', 'is_head': False,
                                            'supersedes_plan_id': None}
    assert rows[str(v1['id'])]['chain'] == {'version_no': 1, 'origin': 'review', 'is_head': True,
                                            'supersedes_plan_id': str(v0['id'])}
    assert rows[str(rule['id'])]['chain'] is None
    assert rows[str(v1['id'])]['outcome']['status'] in ('pending', 'expired')     # 每版各自模拟
    summary = view['agent_summary']
    assert summary['first']['count'] == 1 and summary['latest']['count'] == 1
    assert summary['first']['r_mean'] is None and summary['first']['r_median'] is None      # 样本 < 5


def test_agent1_plan_counts_as_its_own_first_and_latest(db):
    _plan_row(db, 'MU', version='agent-1')
    view = review_shadow.plan_shadow(now=NOW, since=D0)
    assert view['plans'][0]['chain'] is None
    assert view['agent_summary']['first']['count'] == 1 and view['agent_summary']['latest']['count'] == 1


def _shadow_row(r, *, version_no=0, is_head=True, kind='agent', status='closed'):
    return {'kind': kind, 'outcome': {'status': status, 'r': r},
            'chain': None if version_no is None else {'version_no': version_no, 'is_head': is_head}}


def test_agent_summary_needs_five_samples():
    four = [_shadow_row(r) for r in (1.0, 2.0, -1.0, 0.5)]
    out = review.shadow_agent_summary(four + [_shadow_row(0.0), _shadow_row(None, status='pending'),
                                              _shadow_row(None, status='expired'),
                                              _shadow_row(9.0, kind='rule', version_no=None)])
    assert out['first'] == {'count': 7, 'determined': 5, 'expired': 1, 'r_mean': 0.5, 'r_median': 0.5}
    out = review.shadow_agent_summary(four)
    assert out['first'] == {'count': 4, 'determined': 4, 'expired': 0, 'r_mean': None, 'r_median': None}
    chain = [_shadow_row(1.0, version_no=0, is_head=False), _shadow_row(2.0, version_no=1, is_head=False),
             _shadow_row(3.0, version_no=2, is_head=True), _shadow_row(4.0, version_no=None)]
    out = review.shadow_agent_summary(chain)
    assert out['first']['count'] == 2 and out['latest']['count'] == 2      # agent-1 两边都算


@pytest.mark.parametrize('p,bars,expected', [
    (plan2(), [bar(D1, 100, 103, 99, 101), bar(D2, 105, 113, 104, 111), bar(D3, 106, 108, 104, 105)],
     {'fill_date': D1, 'fill_price': D('100.0'),
      'legs': [{'fraction': 0.5, 'exit_date': '2026-10-22', 'exit_price': D('112'), 'reason': 'target1'},
               {'fraction': 0.5, 'exit_date': '2026-10-23', 'exit_price': D('105.0'), 'reason': 'trail'}],
      'r': 1.4166666666666667, 'return': 0.085, 'spy_return': 0.0, 'void_reason': None,
      'void_date': None, 'time_stop_due': False, 'status': 'closed'}),
    (plan2(max_hold=3), [bar(D1, 100, 101, 99.5, 101), bar(D2, 101, 102, 100.5, 101),
                        bar(D3, 101, 102, 100.5, 101.5)],
     {'fill_date': D1, 'fill_price': D('100.0'), 'legs': [], 'r': 0.25, 'return': 0.015,
      'spy_return': 0.0, 'void_reason': None, 'void_date': None, 'time_stop_due': True, 'status': 'open'}),
    (plan2(), [bar(D1, 93, 99, 92, 95)],
     {'fill_date': None, 'fill_price': None, 'legs': [], 'r': None, 'return': None, 'spy_return': None,
      'void_reason': 'gap_below_stop', 'void_date': D1, 'time_stop_due': False, 'status': 'voided'}),
])
def test_v2_literal_golden_before_shared_step(p, bars, expected):
    # Captured from unchanged HEAD 92eab02. repr pins Decimal exponents, float values and dict ordering too.
    assert repr(sim2(p, bars)) == repr(expected)


def test_shared_step_is_pure_and_stop_never_moves_down():
    assert hasattr(plan_shadow, 'PositionState') and hasattr(plan_shadow, 'step_position')
    state = plan_shadow.PositionState(D('100'), D('110'), D('94'), D('6'), True, False,
                                       D('115'), 4, False)
    updated, legs = plan_shadow.step_position(state, bar(D4, 114, 116, 111, 114),
                                              target1=D('112'), atr=D('4'), trail_multiple=D('3'), max_hold=10)
    assert state.held_sessions == 4 and state.stop == D('110')
    assert updated.held_sessions == 5 and updated.stop == D('110') and legs == []
