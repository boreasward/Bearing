"""Plan-1 pure functions: ATR, levels, validity, earnings flags, sizing, plan building."""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from investment_assistant.config import WorkbenchConfig
from investment_assistant.execution import plan_levels as plans
from investment_assistant.derivation.indicators.price import atr14

PARAMS = WorkbenchConfig()


def _bars(n=15, *, flat=False, start=date(2026, 10, 1), close=100):
    """Synthetic bars with constant TR=2 (or flat=TR 0), ids 1..n."""
    from investment_assistant.data.calendar import add_trading_days
    out = []
    for i in range(n):
        d = add_trading_days(start, i) if i else start
        if flat:
            hi = lo = c = close
        else:
            hi, lo, c = close + 2, close, close
        out.append({'id': i + 1, 'price_date': d, 'open': close, 'high': hi, 'low': lo, 'close': c,
                    'adj_close': c, 'volume': 1000, 'split_factor': 1.0})
    return out


def _snapshot(equity='100000'):
    return {'id': uuid.uuid4(), 'as_of': date(2026, 10, 20), 'equity': Decimal(equity)}


def _exposure(ticker_value='0', total_value='0'):
    return {'ticker_value': Decimal(ticker_value), 'total_value': Decimal(total_value)}


def test_atr14_wilder():
    assert atr14(_bars(15)) == Decimal('2')
    assert atr14(_bars(14)) is None


def test_rule_levels():
    levels = plans.rule_levels(Decimal('100'), Decimal('3'))
    assert levels['entry_low'] == Decimal('98.50')
    assert levels['entry_high'] == Decimal('101.50')
    assert levels['stop'] == Decimal('94.00')
    assert levels['target1'] == Decimal('112.00')


def test_rounding_half_up():
    levels = plans.rule_levels(Decimal('10.005'), Decimal('0.01'))
    assert str(levels['entry_low']) == '10.00'
    assert str(levels['entry_high']) == '10.01'
    assert str(levels['stop']) == '9.99'      # 9.985 rounds half-up, not to even
    assert str(levels['target1']) == '10.05'  # 10.045 rounds half-up, not to even


def test_validity_skips_holidays():
    valid_from, valid_until = plans.validity(date(2026, 11, 25))
    assert (valid_from, valid_until) == (date(2026, 11, 27), date(2026, 12, 1))


def _earnings(d, *, known=True):
    return {'id': uuid.uuid4(), 'status': 'known' if known else 'unknown', 'earnings_date': d}


def test_earnings_in_validity_invalid():
    invalid, warnings = plans.earnings_flags(_earnings(date(2026, 10, 23)), signal_date=date(2026, 10, 21),
                                             valid_from=date(2026, 10, 22), valid_until=date(2026, 10, 26))
    assert invalid == ['earnings_in_validity']
    assert warnings == []


def test_earnings_within_10_warns():
    for d in (date(2026, 11, 2), date(2026, 11, 4)):  # after valid_until, through T+10
        invalid, warnings = plans.earnings_flags(_earnings(d), signal_date=date(2026, 10, 21),
                                                 valid_from=date(2026, 10, 22), valid_until=date(2026, 10, 26))
        assert invalid == []
        assert warnings == ['earnings_within_10d']


def test_earnings_unknown_or_past_warns():
    for row in (None, _earnings(None, known=False), _earnings(date(2026, 10, 15))):
        invalid, warnings = plans.earnings_flags(row, signal_date=date(2026, 10, 21),
                                                 valid_from=date(2026, 10, 22), valid_until=date(2026, 10, 26))
        assert invalid == []
        assert warnings == ['earnings_unknown']


def test_earnings_far_away_no_flag():
    invalid, warnings = plans.earnings_flags(_earnings(date(2026, 12, 25)), signal_date=date(2026, 10, 21),
                                             valid_from=date(2026, 10, 22), valid_until=date(2026, 10, 26))
    assert invalid == []
    assert warnings == []


def test_size_risk_bound():
    sizing, invalid = plans.size(snapshot=_snapshot(), ref_price=Decimal('100'), stop=Decimal('94'),
                                 entry_high=Decimal('101.5'), ticker_value=Decimal('0'),
                                 total_value=Decimal('0'), params=PARAMS)
    assert invalid == []
    assert sizing['shares'] == 166
    assert sizing['status'] == 'sized'
    assert sizing['per_share_risk'] == Decimal('6')


def test_size_single_cap():
    sizing, invalid = plans.size(snapshot=_snapshot('10000'), ref_price=Decimal('100'), stop=Decimal('99.9'),
                                 entry_high=Decimal('100.1'), ticker_value=Decimal('0'),
                                 total_value=Decimal('0'), params=PARAMS)
    assert invalid == []
    assert sizing['shares'] == (Decimal('10000') * Decimal('0.25') // Decimal('100.1'))
    assert sizing['status'] == 'capped'
    assert sizing['applied_cap'] == 'single'


def test_size_total_cap_no_room():
    sizing, invalid = plans.size(snapshot=_snapshot(), ref_price=Decimal('100'), stop=Decimal('94'),
                                 entry_high=Decimal('101.5'), ticker_value=Decimal('0'),
                                 total_value=Decimal('100000'), params=PARAMS)
    assert invalid == ['no_room']


def test_size_no_equity():
    sizing, invalid = plans.size(snapshot=None, ref_price=Decimal('100'), stop=Decimal('94'),
                                 entry_high=Decimal('101.5'), ticker_value=Decimal('0'),
                                 total_value=Decimal('0'), params=PARAMS)
    assert invalid == []
    assert sizing['status'] == 'no_equity'
    assert sizing['shares'] is None
    assert sizing['per_share_risk'] == Decimal('6')


def test_zero_atr_skips():
    plan = plans.build_rule_plan(ticker='NVDA', signal_date=date(2026, 10, 21), bars=_bars(flat=True),
                                 earnings_row=None, snapshot=_snapshot(), exposure=_exposure(), params=PARAMS)
    assert plan == {'skip': 'atr_unavailable'}


def test_build_rule_plan_fields():
    bars = _bars()
    plan = plans.build_rule_plan(ticker='NVDA', signal_date=date(2026, 10, 21), bars=bars, earnings_row=None,
                                 snapshot=_snapshot(), exposure=_exposure(), params=PARAMS)
    levels = plans.rule_levels(Decimal('100'), atr14(bars))
    for key, value in levels.items():
        assert plan[key] == value
    assert plan['kind'] == 'rule'
    assert plan['plan_version'] == 'plan-1'
    assert plan['trail_atr_multiple'] == Decimal('3')
    assert plan['status'] == 'ok'
    assert (plan['valid_from'], plan['valid_until']) == plans.validity(date(2026, 10, 21))
    assert plan['inputs']['bar_ids'] == [b['id'] for b in bars[-15:]]
    assert plan['inputs']['snapshot_id'] == plan['sizing']['snapshot_id']


def test_own_plan_demotes_invalid_reasons():
    bars = _bars()
    row = _earnings(date(2026, 10, 23))
    rule = plans.build_rule_plan(ticker='NVDA', signal_date=date(2026, 10, 21), bars=bars, earnings_row=row,
                                 snapshot=_snapshot(), exposure=_exposure(), params=PARAMS)
    assert rule['status'] == 'invalid'
    own = plans.build_own_plan(rule_plan={**rule, 'id': uuid.uuid4()},
                               levels=plans.rule_levels(Decimal('100'), Decimal('2')),
                               ticker='NVDA', signal_date=date(2026, 10, 21), bars=bars, earnings_row=row,
                               snapshot=_snapshot(), exposure=_exposure(), params=PARAMS)
    assert own['kind'] == 'own'
    assert own['status'] == 'ok'
    assert own['invalid_reasons'] == []
    assert 'earnings_in_validity' in own['warnings']


def test_validate_levels_rejects():
    good = {'entry_low': '98.5', 'entry_high': '101.5', 'stop': '94', 'target1': '112'}
    levels = plans.validate_levels(good)
    assert levels == {'entry_low': Decimal('98.5'), 'entry_high': Decimal('101.5'),
                      'stop': Decimal('94'), 'target1': Decimal('112')}
    with pytest.raises(ValueError):
        plans.validate_levels({**good, 'stop': 'abc'})
    with pytest.raises(ValueError):
        plans.validate_levels({**good, 'entry_low': '-1'})
    with pytest.raises(ValueError):
        plans.validate_levels({**good, 'entry_low': '102'})
    with pytest.raises(ValueError):
        plans.validate_levels({**good, 'stop': '99'})


def test_own_plan_sizes_from_own_entry_not_signal_close():
    # 收盘 100，自定入场区间 105–106、止损 100：每股风险按自定区间中点 105.5 − 100 = 5.5 计
    plan = plans.build_own_plan(
        rule_plan=None, levels={'entry_low': Decimal('105'), 'entry_high': Decimal('106'),
                                'stop': Decimal('100'), 'target1': Decimal('115')},
        ticker='NVDA', signal_date=date(2026, 10, 22), bars=_bars(20), earnings_row=None,
        snapshot=_snapshot(), exposure=_exposure(), params=PARAMS)
    assert plan['sizing']['per_share_risk'] == Decimal('5.5')
    assert plan['sizing']['shares'] == 181          # floor(1000 / 5.5)
    assert plan['sizing']['status'] == 'sized'
    assert 'no_room' not in plan['warnings']


# --- P2: market context frozen into plans ---

CONTEXT = {'context_version': 'context-2', 'pool_tier': 'core',
           'regime': {'id': 'r1', 'label': 'risk_off'},
           'sector': {'etf': 'SMH', 'rs20': -0.01, 'rs60': -0.02, 'state': 'weak'},
           'next_earnings': date(2026, 11, 20)}


def test_context_flags():
    assert plans.CONTEXT_VERSION == 'context-2'
    assert plans.context_flags(None) == []
    assert plans.context_flags({'regime': {'id': 'r', 'label': 'neutral'}, 'sector': None}) == []
    assert plans.context_flags({'regime': None, 'sector': None}) == ['regime_unavailable']
    assert plans.context_flags({'regime': {'id': 'r', 'label': 'risk_off'}, 'sector': None}) == ['regime_risk_off']
    assert plans.context_flags({'regime': {'id': 'r', 'label': 'neutral'},
                                'sector': {'etf': 'SMH', 'rs20': None, 'rs60': None, 'state': 'weak'}}) == \
        ['sector_weak']


def test_context_none_keeps_p1_output():
    bars = _bars()
    snap = _snapshot()
    without = plans.build_rule_plan(ticker='NVDA', signal_date=date(2026, 10, 21), bars=bars, earnings_row=None,
                                    snapshot=snap, exposure=_exposure(), params=PARAMS)
    explicit = plans.build_rule_plan(ticker='NVDA', signal_date=date(2026, 10, 21), bars=bars, earnings_row=None,
                                     snapshot=snap, exposure=_exposure(), params=PARAMS, context=None)
    assert without == explicit
    assert 'context' not in without['inputs']
    assert not [w for w in without['warnings'] if w.startswith(('regime_', 'sector_'))]


def test_context_frozen_in_inputs():
    plan = plans.build_rule_plan(ticker='NVDA', signal_date=date(2026, 10, 21), bars=_bars(), earnings_row=None,
                                 snapshot=_snapshot(), exposure=_exposure(), params=PARAMS, context=CONTEXT)
    assert plan['inputs']['context'] == CONTEXT


def test_risk_off_warns():
    plan = plans.build_rule_plan(ticker='NVDA', signal_date=date(2026, 10, 21), bars=_bars(), earnings_row=None,
                                 snapshot=_snapshot(), exposure=_exposure(), params=PARAMS, context=CONTEXT)
    assert 'regime_risk_off' in plan['warnings']


def test_regime_missing_warns_unavailable():
    ctx = {**CONTEXT, 'regime': None}
    plan = plans.build_rule_plan(ticker='NVDA', signal_date=date(2026, 10, 21), bars=_bars(), earnings_row=None,
                                 snapshot=_snapshot(), exposure=_exposure(), params=PARAMS, context=ctx)
    assert 'regime_unavailable' in plan['warnings']


def test_weak_sector_warns():
    plan = plans.build_rule_plan(ticker='NVDA', signal_date=date(2026, 10, 21), bars=_bars(), earnings_row=None,
                                 snapshot=_snapshot(), exposure=_exposure(), params=PARAMS, context=CONTEXT)
    assert 'sector_weak' in plan['warnings']


def test_context_does_not_change_levels_or_sizing():
    snap = _snapshot()
    plain = plans.build_rule_plan(ticker='NVDA', signal_date=date(2026, 10, 21), bars=_bars(), earnings_row=None,
                                  snapshot=snap, exposure=_exposure(), params=PARAMS)
    with_ctx = plans.build_rule_plan(ticker='NVDA', signal_date=date(2026, 10, 21), bars=_bars(), earnings_row=None,
                                     snapshot=snap, exposure=_exposure(), params=PARAMS, context=CONTEXT)
    for key in ('ref_price', 'atr', 'entry_low', 'entry_high', 'stop', 'target1', 'sizing', 'status',
                'valid_from', 'valid_until', 'invalid_reasons'):
        assert with_ctx[key] == plain[key]


def test_own_plan_freezes_context_too():
    own = plans.build_own_plan(rule_plan=None, levels=plans.rule_levels(Decimal('100'), Decimal('2')),
                               ticker='NVDA', signal_date=date(2026, 10, 21), bars=_bars(), earnings_row=None,
                               snapshot=_snapshot(), exposure=_exposure(), params=PARAMS, context=CONTEXT)
    assert own['inputs']['context'] == CONTEXT
    assert 'regime_risk_off' in own['warnings'] and 'sector_weak' in own['warnings']


def _agent_levels(**overrides):
    levels = {'entry_low': Decimal('98'), 'entry_high': Decimal('102'), 'stop': Decimal('94'),
              'target1': Decimal('110')}
    levels.update(overrides)
    return levels


def _agent_plan(**overrides):
    args = {'levels': _agent_levels(), 'agent_run_id': 'run-1', 'model': 'gpt-test', 'ticker': 'NVDA',
            'signal_date': date(2026, 10, 21), 'bars': _bars(15), 'earnings_row': None,
            'snapshot': _snapshot(), 'exposure': _exposure(), 'params': PARAMS}
    args.update(overrides)
    return plans.build_agent_plan(**args)


def test_agent_plan_fields():
    plan = _agent_plan()
    assert plan['kind'] == 'agent' and plan['plan_version'] == 'agent-1' and plan['based_on_id'] is None
    assert (plan['valid_from'], plan['valid_until']) == plans.validity(date(2026, 10, 21))
    assert plan['inputs']['agent_run_id'] == 'run-1' and plan['inputs']['model'] == 'gpt-test'
    assert plan['status'] == 'ok' and plan['invalid_reasons'] == []
    assert plan['ref_price'] == Decimal('100.000000') and plan['atr'] == Decimal('2.000000')
    assert plan['entry_low'] == Decimal('98')   # levels land flat, like rule plans


def test_agent_plan_sizes_from_entry_midpoint():
    plan = _agent_plan(levels=_agent_levels(entry_low=Decimal('110'), entry_high=Decimal('120'),
                                            stop=Decimal('104'), target1=Decimal('140')))
    # 风险按入场区间中点 115 计：每股 115-104=11 → 1000/11=90 股（按信号收盘 100 会是负风险）
    assert plan['sizing']['shares'] == 90 and plan['sizing']['applied_cap'] == 'risk'
    assert plan['status'] == 'ok'


def test_agent_plan_earnings_in_validity_invalid():
    plan = _agent_plan(earnings_row=_earnings(date(2026, 10, 22)))
    assert plan['status'] == 'invalid'
    assert 'earnings_in_validity' in plan['invalid_reasons']


def test_agent_plan_no_atr_uses_zero():
    plan = _agent_plan(bars=_bars(14))
    assert plan['atr'] == Decimal('0')


# --- v2.9: plan-2 = plan-1 + execution fields (spec §5.4) ---

_SNAP = _snapshot()
_EARN = _earnings(date(2026, 10, 23))


def _rule(version, **kwargs):
    return plans.build_rule_plan(ticker='NVDA', signal_date=date(2026, 10, 21), bars=_bars(20),
                                 earnings_row=_EARN, snapshot=_SNAP,
                                 exposure=_exposure(), params=PARAMS, context={'regime': {'label': 'neutral'}},
                                 version=version, **kwargs)


def test_version_constants():
    assert (plans.PLAN_V1, plans.PLAN_V2) == ('plan-1', 'plan-2')
    assert plans.RULE_MAX_HOLD_SESSIONS == 10 and plans.MAX_HOLD_DEFAULT == 10
    # v2.9 Task 3: stage_rules writes plan-2 and derivation registers it, so PLAN_VERSION is the plan-2 constant
    assert plans.PLAN_VERSION == plans.PLAN_V2 == 'plan-2' and plans.PLAN_V1 == 'plan-1'
    default = plans.build_rule_plan(ticker='NVDA', signal_date=date(2026, 10, 21), bars=_bars(20), earnings_row=None,
                                    snapshot=_snapshot(), exposure=_exposure(), params=PARAMS)
    assert default['plan_version'] == 'plan-1' and 'entry_type' not in default and 'max_hold_sessions' not in default
    assert 'entry_rules_version' not in default['inputs']


def test_plan2_levels_equal_plan1():
    one, two = _rule(plans.PLAN_V1), _rule(plans.PLAN_V2)
    assert (one['plan_version'], two['plan_version']) == ('plan-1', 'plan-2')
    for key in ('ticker', 'signal_date', 'kind', 'based_on_id', 'ref_price', 'atr', 'entry_low', 'entry_high', 'stop',
                'target1', 'trail_atr_multiple', 'valid_from', 'valid_until', 'sizing', 'status',
                'invalid_reasons', 'warnings'):
        assert one[key] == two[key], key
    assert two['status'] == 'invalid' and two['invalid_reasons'] == ['earnings_in_validity']
    assert {k: v for k, v in two['inputs'].items() if k != 'entry_rules_version'} == one['inputs']


def test_plan2_exec_fields():
    plan = _rule(plans.PLAN_V2)
    assert plan['entry_type'] == 'zone_limit' and plan['max_hold_sessions'] == 10
    assert plan['inputs']['entry_rules_version'] == 'entry-1' and 'active_chain' not in plan['inputs']
    joined = _rule(plans.PLAN_V2, active_chain={'ticker': 'NVDA', 'signal_date': '2026-10-20'})
    assert joined['inputs']['active_chain'] == {'ticker': 'NVDA', 'signal_date': '2026-10-20'}
    assert {k: v for k, v in joined.items() if k != 'inputs'} == {k: v for k, v in plan.items() if k != 'inputs'}
    with pytest.raises(ValueError):
        _rule('plan-9')


def test_own_plan_fill_session_validity():
    levels = {'entry_low': Decimal('105'), 'entry_high': Decimal('106'), 'stop': Decimal('100'),
              'target1': Decimal('115')}
    common = dict(rule_plan=None, levels=levels, ticker='NVDA', signal_date=date(2026, 10, 22), bars=_bars(20),
                  earnings_row=None, snapshot=_snapshot(), exposure=_exposure(), params=PARAMS)
    fill = date(2026, 10, 27)
    own = plans.build_own_plan(**common, version=plans.PLAN_V2, fill_session=fill)
    assert (own['valid_from'], own['valid_until']) == (fill, fill)
    assert own['plan_version'] == 'plan-2' and own['entry_type'] == 'zone_limit' and own['max_hold_sessions'] == 10
    assert own['inputs']['entry_rules_version'] == 'entry-1'
    custom = plans.build_own_plan(**common, version=plans.PLAN_V2, fill_session=fill, max_hold_sessions=15)
    assert custom['max_hold_sessions'] == 15
    for bad in (2, 31, 10.5, True):
        with pytest.raises(ValueError):
            plans.build_own_plan(**common, version=plans.PLAN_V2, fill_session=fill, max_hold_sessions=bad)
    old = plans.build_own_plan(**common)
    assert old['plan_version'] == 'plan-1' and 'entry_type' not in old
    assert (old['valid_from'], old['valid_until']) == plans.validity(date(2026, 10, 22))
