"""Plan-1 rule plan derivation: independent recomputation from substituted values (offline, pure)."""
import investment_assistant.core.dbutil as core_dbutil
import copy
import json
import uuid
from datetime import date
from decimal import Decimal

import pytest

from investment_assistant.config import WorkbenchConfig
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.execution import plan_levels as plans
from investment_assistant.derivation import chain as derivation
from investment_assistant.derivation.indicators import price

SIGNAL = date(2026, 10, 21)


def _bars(n=15, close=100, tr=2):
    """Bars with constant TR=``tr`` and the given close, ids 1..n."""
    out = []
    start = date(2026, 10, 1)
    for i in range(n):
        d = add_trading_days(start, i) if i else start
        out.append({'id': i + 1, 'price_date': d, 'open': close, 'high': close + tr, 'low': close,
                    'close': close, 'adj_close': close, 'volume': 1000, 'split_factor': 1.0, 'revision': 1})
    return out


def _snapshot(equity='100000'):
    return {'id': uuid.uuid4(), 'as_of': date(2026, 10, 20), 'equity': Decimal(equity)}


def _exposure(ticker_value='0', total_value='0'):
    return {'ticker_value': Decimal(ticker_value), 'total_value': Decimal(total_value)}


def _earnings(d, status='known'):
    return {'id': uuid.uuid4(), 'status': status, 'earnings_date': d}


def _rows(bars, earnings=None, snapshot=None, regime=None):
    dec_bars = [{**b, **{k: Decimal(str(b[k])) for k in ('open', 'high', 'low', 'close')}} for b in bars]
    return derivation.Rows(bars=dec_bars, earnings=earnings, snapshot=snapshot, regime=regime,
                           agent_run=None, rule_plan=None, candidates=[], missing=[])


def _make(*, bars=None, earnings=None, snapshot='default', exposure=None, params=None, context=None,
          regime=None):
    bars = bars or _bars()
    snapshot = _snapshot() if snapshot == 'default' else snapshot
    plan = plans.build_rule_plan(
        ticker='AAA', signal_date=SIGNAL, bars=bars, earnings_row=earnings, snapshot=snapshot,
        exposure=exposure or _exposure(), params=params or WorkbenchConfig(), context=context)
    plan['id'] = uuid.uuid4()
    return plan, _rows(bars, earnings, snapshot, regime)


def _step(result, key):
    return next(s for s in result['steps'] if s['key'] == key)


def _keys(result):
    return [s['key'] for s in result['steps']]


def test_constants_match_plans():
    for name in ('ENTRY_ATR', 'STOP_ATR', 'TARGET_ATR', 'TRAIL_ATR_MULTIPLE', 'VALID_SESSIONS',
                 'EARNINGS_WARN_SESSIONS', 'ATR_PERIOD', 'MIN_BARS'):
        owner = price if name in ('ATR_PERIOD', 'MIN_BARS') else plans
        assert getattr(derivation, name) == getattr(owner, name), name
    assert derivation.DERIVATION_VERSION == 'derivation-1'
    assert plans.PLAN_VERSION in derivation.REGISTERED_VERSIONS
    assert plans.AGENT_VERSION in derivation.REGISTERED_VERSIONS
    from investment_assistant.execution import timeline
    assert timeline.AGENT_V2 in derivation.REGISTERED_VERSIONS and timeline.AGENT_V3 in derivation.REGISTERED_VERSIONS
    assert plans.PLAN_V1 in derivation.REGISTERED_VERSIONS and plans.PLAN_V2 in derivation.REGISTERED_VERSIONS
    assert derivation.CHAIN_ENTRY_SESSIONS == timeline.CHAIN_ENTRY_SESSIONS


def test_fixed_texts_verbatim():
    assert derivation.EVIDENCE_TEXT == (
        '规则 v1 与 `plan-1` 都未经验证（Phase 0 Gate HOLD）。推导一致只说明算术正确，不说明规则有效。')
    assert derivation.TRIGGER_NOTE == '触发段只复核候选冻结的取值是否满足阈值，不从原始日线重算均线。'


def test_rule_plan_worked_example():
    plan, rows = _make()
    result = derivation.derive_plan(plan, rows)
    expected = {'atr': '2', 'ref_price': '100', 'entry_low': '99', 'entry_high': '101', 'stop': '96',
                'target1': '108', 'per_share_risk': '4', 'risk_amount': '1000', 'cap_risk': '250',
                'cap_single': '247', 'cap_total': '990', 'shares': '247'}
    for key, value in expected.items():
        assert Decimal(_step(result, key)['result']) == Decimal(value), key
    assert _step(result, 'atr')['result'] == '2.000000'
    assert _step(result, 'entry_low')['result'] == '99.00'
    assert _step(result, 'applied_cap')['result'] == 'single'
    assert _step(result, 'sizing.status')['result'] == 'capped'
    assert _step(result, 'status')['result'] == 'ok'
    assert all(s['consistent'] is not False for s in result['steps'])
    assert result['consistency'] == {'status': 'consistent', 'mismatches': [], 'missing': []}
    assert result['trigger'] == []
    assert result['derivation_version'] == 'derivation-1'
    assert result['evidence'] == derivation.EVIDENCE_TEXT
    assert result['plan'] == {'id': str(plan['id']), 'ticker': 'AAA', 'signal_date': '2026-10-21',
                              'kind': 'rule', 'plan_version': 'plan-1', 'status': 'ok'}
    assert _step(result, 'valid_from')['result'] == add_trading_days(SIGNAL, 1).isoformat()
    assert _step(result, 'valid_until')['result'] == add_trading_days(SIGNAL, 3).isoformat()
    assert _step(result, 'trail_atr_multiple')['result'] == '3'
    # Every step carries the documented shape and string substitutions.
    for s in result['steps']:
        assert set(s) == {'key', 'label', 'formula', 'substitutions', 'result', 'stored', 'source', 'consistent'}
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in s['substitutions'].items())
    assert _step(result, 'stop')['substitutions'] == {'ref_price': '100.000000', 'STOP_ATR': '2', 'atr': '2.000000'}
    assert result['checks'][0]['key'] == 'levels_ordered' and result['checks'][0]['holds'] is True


CONTEXT_BASE = {'context_version': 'context-2', 'pool_tier': 'core', 'source': 'manual', 'screen': None,
                'regime': {'id': str(uuid.uuid4()), 'label': 'risk_on'}, 'sector': None, 'next_earnings': None}

VARIANTS = {
    'sized': dict(params=WorkbenchConfig(single_cap=0.9)),
    'capped_total': dict(exposure=_exposure('0', '99000')),
    'no_room': dict(exposure=_exposure('30000', '30000')),
    'no_equity': dict(snapshot=None),
    'earnings_in_validity': dict(earnings=_earnings(date(2026, 10, 23))),
    'earnings_within_10d': dict(earnings=_earnings(date(2026, 11, 2))),
    'earnings_far': dict(earnings=_earnings(date(2026, 12, 15))),
    'earnings_past': dict(earnings=_earnings(date(2026, 10, 1))),
    'earnings_unknown_row': dict(earnings=_earnings(None, status='unknown')),
    'earnings_missing': dict(earnings=None),
    'risk_off': dict(context={**CONTEXT_BASE, 'regime': {'id': str(uuid.uuid4()), 'label': 'risk_off'}}),
    'regime_unavailable': dict(context={**CONTEXT_BASE, 'regime': None}),
    'sector_weak': dict(context={**CONTEXT_BASE, 'sector': {'etf': 'XLK', 'rs20': -0.1, 'rs60': -0.2,
                                                           'state': 'weak'}}),
    'screen_source': dict(context={**CONTEXT_BASE, 'source': 'screen',
                                   'screen': {'screen_run_id': str(uuid.uuid4()), 'group': 'growth',
                                              'rs': 0.4, 'rank': 3}}),
    'everything': dict(earnings=_earnings(date(2026, 10, 23)), exposure=_exposure('30000', '30000'),
                       context={**CONTEXT_BASE, 'regime': {'id': 'x', 'label': 'risk_off'},
                                'sector': {'etf': 'XLK', 'rs20': -0.1, 'rs60': -0.2, 'state': 'weak'}}),
}


@pytest.mark.parametrize('name', sorted(VARIANTS))
def test_rule_plan_variants_consistent(name):
    plan, rows = _make(**VARIANTS[name])
    result = derivation.derive_plan(plan, rows)
    assert result['consistency'] == {'status': 'consistent', 'mismatches': [], 'missing': []}, result['consistency']
    assert _step(result, 'status')['result'] == plan['status']
    assert _step(result, 'invalid_reasons')['result'] == sorted(plan['invalid_reasons'])
    assert _step(result, 'warnings')['result'] == sorted(plan['warnings'])
    assert _step(result, 'status')['consistent'] is True
    assert _step(result, 'invalid_reasons')['consistent'] is True
    assert _step(result, 'warnings')['consistent'] is True


def test_variant_outcomes_are_what_they_claim():
    def run(name):
        plan, rows = _make(**VARIANTS[name])
        return plan, derivation.derive_plan(plan, rows)

    plan, result = run('sized')
    assert plan['sizing']['status'] == 'sized' and _step(result, 'applied_cap')['result'] == 'risk'
    assert _step(result, 'sizing.status')['result'] == 'sized'
    plan, result = run('capped_total')
    assert _step(result, 'applied_cap')['result'] == 'total' and _step(result, 'shares')['result'] == '9'
    plan, result = run('no_room')
    assert plan['status'] == 'invalid' and 'no_room' in plan['invalid_reasons']
    assert _step(result, 'sizing.status')['result'] == 'no_room'
    assert _step(result, 'invalid_reasons')['result'] == ['no_room']
    plan, result = run('no_equity')
    assert plan['sizing']['status'] == 'no_equity' and plan['sizing']['shares'] is None
    assert _step(result, 'shares')['result'] is None and _step(result, 'sizing.status')['result'] == 'no_equity'
    assert _step(result, 'per_share_risk')['consistent'] is True
    assert 'cap_risk' not in _keys(result)
    plan, result = run('earnings_in_validity')
    assert _step(result, 'invalid_reasons')['result'] == ['earnings_in_validity']
    assert _step(result, 'status')['result'] == 'invalid'
    plan, result = run('earnings_within_10d')
    assert _step(result, 'warnings')['result'] == ['earnings_within_10d']
    plan, result = run('earnings_missing')
    assert _step(result, 'warnings')['result'] == ['earnings_unknown']
    plan, result = run('risk_off')
    assert _step(result, 'warnings')['result'] == ['earnings_unknown', 'regime_risk_off']
    plan, result = run('regime_unavailable')
    assert 'regime_unavailable' in _step(result, 'warnings')['result']
    plan, result = run('sector_weak')
    assert 'sector_weak' in _step(result, 'warnings')['result']


def test_json_roundtripped_plan_is_consistent():
    """A plan read back from the database has JSON sizing/inputs (strings, floats, iso dates)."""
    plan, rows = _make(earnings=_earnings(date(2026, 10, 23)),
                       context={**CONTEXT_BASE, 'next_earnings': date(2026, 10, 23)})
    stored = copy.deepcopy(plan)
    stored['sizing'] = json.loads(core_dbutil._plan_json(plan['sizing']))
    stored['inputs'] = json.loads(core_dbutil._plan_json(plan['inputs']))
    result = derivation.derive_plan(stored, rows)
    assert result['consistency']['status'] == 'consistent', result['consistency']


def _tamper_stop(p):
    p['stop'] += Decimal('0.01')


def _tamper_atr(p):
    p['atr'] += Decimal('0.000001')


def _tamper_shares(p):
    p['sizing']['shares'] += 1


def _tamper_warnings(p):
    p['warnings'] = [*p['warnings'], 'regime_risk_off']


@pytest.mark.parametrize('key,tamper', [('stop', _tamper_stop), ('atr', _tamper_atr),
                                        ('shares', _tamper_shares), ('warnings', _tamper_warnings)])
def test_tampered_value_is_mismatch(key, tamper):
    plan, rows = _make()
    tamper(plan)
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'mismatch'
    assert result['consistency']['mismatches'][0]['key'] == key
    assert len(result['consistency']['mismatches']) == 1
    step = _step(result, key)
    assert step['consistent'] is False
    assert result['consistency']['mismatches'][0] == {'key': key, 'result': step['result'], 'stored': step['stored']}


def test_tampered_frozen_equity_and_status_are_mismatch():
    plan, rows = _make()
    plan['sizing']['equity'] = Decimal('100001')
    assert derivation.derive_plan(plan, rows)['consistency']['mismatches'][0]['key'] == 'equity'
    plan, rows = _make()
    plan['status'] = 'invalid'
    assert derivation.derive_plan(plan, rows)['consistency']['mismatches'][0]['key'] == 'status'
    plan, rows = _make()
    plan['valid_until'] = add_trading_days(SIGNAL, 4)
    assert derivation.derive_plan(plan, rows)['consistency']['mismatches'][0]['key'] == 'valid_until'


def test_altered_input_row_is_mismatch():
    """If the frozen bar row read back differs from what the plan used, the recomputation disagrees."""
    plan, rows = _make()
    rows.bars[-1]['close'] += Decimal('1')
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'mismatch'
    assert result['consistency']['mismatches'][0]['key'] == 'ref_price'


def test_missing_rows_incomplete():
    plan, rows = _make()
    rows.missing = ['price_bars:42']
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'incomplete'
    assert result['consistency']['missing'] == ['price_bars:42']

    plan, rows = _make()
    rows.bars = rows.bars[:14]
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'incomplete' and 'bar_ids' in result['consistency']['missing']

    plan, rows = _make()
    rows.bars = None
    assert 'bar_ids' in derivation.derive_plan(plan, rows)['consistency']['missing']

    plan, rows = _make()
    plan['plan_version'] = 'plan-9'
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'incomplete'
    assert 'unknown_plan_version' in result['consistency']['missing']
    assert result['steps'] == []


def test_missing_snapshot_or_earnings_row_is_incomplete():
    plan, rows = _make()
    rows.snapshot = None   # inputs.snapshot_id still points at a row that was not read back
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'incomplete'
    assert result['consistency']['missing'] == [f"account_snapshots:{plan['inputs']['snapshot_id']}"]

    plan, rows = _make(earnings=_earnings(date(2026, 10, 23)))
    rows.earnings = None
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'incomplete'
    assert result['consistency']['missing'] == [f"earnings_dates:{plan['inputs']['earnings_date_id']}"]


def test_incomplete_outranks_mismatch():
    plan, rows = _make()
    plan['stop'] += Decimal('1')
    rows.missing = ['price_bars:42']
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'incomplete'
    assert result['consistency']['mismatches'] != []


def test_unimplemented_kind_is_incomplete_not_a_crash():
    plan, rows = _make()
    plan['kind'] = 'other'
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'incomplete'
    assert result['consistency']['missing'] == ['no_derivation:other']


def test_atr_step_shows_fourteen_true_ranges():
    plan, rows = _make()
    result = derivation.derive_plan(plan, rows)
    trs = [s for s in result['steps'] if s['key'].startswith('tr_')]
    assert [s['key'] for s in trs] == [f'tr_{i}' for i in range(1, 15)]
    assert all('max(H − L, |H − C前|, |L − C前|)' in s['formula'] for s in trs)
    assert all(s['result'] == '2' for s in trs)
    assert trs[0]['substitutions'] == {'H': '102', 'L': '100', 'C前': '100'}
    assert _step(result, 'atr')['substitutions']['tr_14'] == '2'
    assert len(_step(result, 'atr')['substitutions']) == 14


def test_true_range_uses_previous_close_gap():
    bars = _bars()
    bars[5]['high'] = bars[5]['low'] = bars[5]['close'] = 110   # gap up: |H − C前| = 10 dominates
    plan, rows = _make(bars=bars)
    result = derivation.derive_plan(plan, rows)
    assert _step(result, 'tr_5')['result'] == '10'
    assert result['consistency']['status'] == 'consistent'


def test_derivation_does_not_call_plans_math(monkeypatch):
    plan, rows = _make()

    def boom(*a, **k):
        raise AssertionError('derivation must not call plans math')

    for name in ('rule_levels', 'size', 'atr14', 'earnings_flags', 'validity', 'context_flags', 'levels_ordered',
                 '_cent', '_freeze', '_floor'):
        monkeypatch.setattr(plans, name, boom)
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'consistent'


def test_derivation_is_deterministic_and_does_not_mutate_inputs():
    plan, rows = _make(earnings=_earnings(date(2026, 10, 23)))
    plan_before, rows_before = copy.deepcopy(plan), copy.deepcopy(rows)
    first = derivation.derive_plan(plan, rows)
    second = derivation.derive_plan(plan, rows)
    assert first == second
    assert plan == plan_before and rows == rows_before
    json.dumps(first)   # everything is JSON-ready (strings, lists, bools)


def test_inputs_section_shows_frozen_rows():
    snap = _snapshot()
    plan, rows = _make(snapshot=snap, earnings=_earnings(date(2026, 10, 23)),
                       exposure=_exposure('10', '20'), regime={'id': 'r', 'label': 'risk_on', 'calc_version': 'regime-1'},
                       context={**CONTEXT_BASE, 'regime': {'id': 'r', 'label': 'risk_on'}})
    inputs = derivation.derive_plan(plan, rows)['inputs']
    assert len(inputs['bars']) == 15 and inputs['bars'][0]['id'] == '1'
    assert inputs['bars'][-1]['close'] == '100'
    assert inputs['snapshot']['equity'] == '100000'
    assert inputs['earnings']['earnings_date'] == '2026-10-23'
    assert inputs['exposure']['ticker_value'] == '10'
    assert inputs['sizing_params']['risk_pct'] == '0.01'
    assert inputs['regime']['label'] == 'risk_on'
    no_snap, rows2 = _make(snapshot=None)
    assert derivation.derive_plan(no_snap, rows2)['inputs']['snapshot'] is None


# ---------------------------------------------------------------- rule plan: boundaries (carry-overs)

def test_atr_rounding_is_half_up_not_half_even():
    """TR 0.03 on 15 bars: atr 0.030000, entry_low 99.985 -> 99.99 half-up (half-even would give 99.98)."""
    plan, rows = _make(bars=_bars(tr=Decimal('0.03')))
    result = derivation.derive_plan(plan, rows)
    assert _step(result, 'atr')['result'] == '0.030000'
    assert _step(result, 'entry_low')['result'] == '99.99'
    assert _step(result, 'entry_high')['result'] == '100.02'
    assert _step(result, 'stop')['result'] == '99.94'
    assert _step(result, 'target1')['result'] == '100.12'
    assert plan['entry_low'] == Decimal('99.99')   # what plans.py stored
    assert result['consistency'] == {'status': 'consistent', 'mismatches': [], 'missing': []}


@pytest.mark.parametrize('offset,flag_key,expected', [
    ('valid_from', 'invalid_reasons', ['earnings_in_validity']),
    ('valid_until', 'invalid_reasons', ['earnings_in_validity']),
    ('after_valid_until', 'warnings', ['earnings_within_10d']),
    ('signal', 'warnings', ['earnings_within_10d']),
    ('before_signal', 'warnings', ['earnings_unknown']),
    ('t10', 'warnings', ['earnings_within_10d']),
    ('after_t10', 'warnings', []),
])
def test_earnings_date_boundaries(offset, flag_key, expected):
    dates = {'valid_from': add_trading_days(SIGNAL, 1), 'valid_until': add_trading_days(SIGNAL, 3),
             'after_valid_until': add_trading_days(SIGNAL, 4), 'signal': SIGNAL,
             'before_signal': add_trading_days(SIGNAL, -1), 't10': add_trading_days(SIGNAL, 10),
             'after_t10': add_trading_days(SIGNAL, 11)}
    plan, rows = _make(earnings=_earnings(dates[offset]))
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'consistent', result['consistency']
    assert _step(result, flag_key)['result'] == expected
    assert _step(result, flag_key)['stored'] == expected


def test_cap_ties_pick_the_earlier_cap():
    # equity 100000: risk budget 1000 / per-share risk 4 = 250; 0.2525 × 100000 / 101 = 250 exactly.
    plan, rows = _make(params=WorkbenchConfig(single_cap=0.2525))
    result = derivation.derive_plan(plan, rows)
    assert (_step(result, 'cap_risk')['result'], _step(result, 'cap_single')['result']) == ('250', '250')
    assert _step(result, 'applied_cap')['result'] == 'risk' == plan['sizing']['applied_cap']
    assert _step(result, 'sizing.status')['result'] == 'sized'
    assert result['consistency']['status'] == 'consistent'
    # single and total tie below the risk cap: 'single' comes before 'total'.
    plan, rows = _make(params=WorkbenchConfig(risk_pct=0.05, single_cap=0.2525, total_cap=0.2525))
    result = derivation.derive_plan(plan, rows)
    assert (_step(result, 'cap_single')['result'], _step(result, 'cap_total')['result']) == ('250', '250')
    assert _step(result, 'applied_cap')['result'] == 'single' == plan['sizing']['applied_cap']
    assert result['consistency']['status'] == 'consistent'


# ---------------------------------------------------------------- own plans

OWN_LEVELS = {'entry_low': Decimal('98.5'), 'entry_high': Decimal('101.25'), 'stop': Decimal('95'),
              'target1': Decimal('110')}


def _make_own(*, bars=None, earnings=None, snapshot='default', exposure=None, params=None, context=None,
              rule_plan='default', levels=None):
    bars = bars or _bars()
    snapshot = _snapshot() if snapshot == 'default' else snapshot
    rule_plan = {'id': uuid.uuid4(), 'atr': Decimal('2')} if rule_plan == 'default' else rule_plan
    plan = plans.build_own_plan(
        rule_plan=rule_plan, levels=levels or OWN_LEVELS, ticker='AAA', signal_date=SIGNAL, bars=bars,
        earnings_row=earnings, snapshot=snapshot, exposure=exposure or _exposure(),
        params=params or WorkbenchConfig(), context=context)
    plan['id'] = uuid.uuid4()
    rows = _rows(bars, earnings, snapshot)
    rows.rule_plan = rule_plan
    return plan, rows


def test_own_plan_consistent_and_sources():
    # The rule plan would be invalid (earnings in validity, no room); the own plan keeps them as warnings.
    plan, rows = _make_own(earnings=_earnings(date(2026, 10, 23)), exposure=_exposure('30000', '30000'))
    assert plan['status'] == 'ok' and {'earnings_in_validity', 'no_room'} <= set(plan['warnings'])
    result = derivation.derive_plan(plan, rows)
    assert result['consistency'] == {'status': 'consistent', 'mismatches': [], 'missing': []}, result['consistency']
    assert result['plan']['kind'] == 'own'
    for key in ('entry_low', 'entry_high', 'stop', 'target1'):
        step = _step(result, key)
        assert step['source'] == 'user_input' and step['consistent'] is None and step['stored'] is None
        assert Decimal(step['result']) == OWN_LEVELS[key]
    assert _step(result, 'status')['result'] == 'ok' and _step(result, 'status')['consistent'] is True
    assert _step(result, 'invalid_reasons')['result'] == []
    assert {'earnings_in_validity', 'no_room'} <= set(_step(result, 'warnings')['result'])
    assert result['checks'][0]['key'] == 'levels_ordered' and result['checks'][0]['holds'] is True
    assert all(s['consistent'] is not False for s in result['steps'])


def test_own_plan_sizing_uses_entry_midpoint():
    plan, rows = _make_own()
    result = derivation.derive_plan(plan, rows)
    mid = _step(result, 'entry_mid')
    assert mid['result'] == '99.875' and mid['formula'] == '(entry_low + entry_high) / 2'
    risk = _step(result, 'per_share_risk')
    assert risk['result'] == '4.875' and risk['substitutions'] == {'entry_mid': '99.875', 'stop': '95'}
    assert risk['consistent'] is True
    assert _step(result, 'ref_price')['result'] == '100.000000'   # frozen close, not the sizing reference
    assert _step(result, 'shares')['consistent'] is True


def test_own_plan_tamper_is_mismatch():
    plan, rows = _make_own()
    plan['sizing']['shares'] += 1
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'mismatch'
    assert [m['key'] for m in result['consistency']['mismatches']] == ['shares']
    plan, rows = _make_own()
    plan['status'] = 'invalid'
    assert derivation.derive_plan(plan, rows)['consistency']['mismatches'][0]['key'] == 'status'


def test_own_plan_atr_fallback_sources():
    bars = _bars(n=14)
    rule_plan = {'id': uuid.uuid4(), 'atr': Decimal('3.5')}
    plan, rows = _make_own(bars=bars, rule_plan=rule_plan)
    assert plan['atr'] == Decimal('3.5')
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'consistent', result['consistency']
    atr = _step(result, 'atr')
    assert atr['source'] == '取规则计划' and atr['result'] == '3.5' and atr['consistent'] is True
    assert not any(k.startswith('tr_') for k in _keys(result))

    plan, rows = _make_own(bars=bars, rule_plan=None)
    assert plan['atr'] == 0
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'consistent', result['consistency']
    atr = _step(result, 'atr')
    assert atr['source'] == '无 ATR' and atr['result'] == '0' and atr['consistent'] is True

    plan, rows = _make_own(bars=bars, rule_plan={'id': uuid.uuid4(), 'atr': None})
    assert _step(derivation.derive_plan(plan, rows), 'atr')['source'] == '无 ATR'


def test_own_plan_atr_from_bars_when_fifteen():
    plan, rows = _make_own(rule_plan={'id': uuid.uuid4(), 'atr': Decimal('9')})
    result = derivation.derive_plan(plan, rows)
    assert _step(result, 'atr')['source'] == 'formula' and _step(result, 'atr')['result'] == '2.000000'
    assert len([k for k in _keys(result) if k.startswith('tr_')]) == 14


def test_own_plan_fallback_without_rule_plan_row_is_incomplete():
    bars = _bars(n=14)
    plan, rows = _make_own(bars=bars)
    rows.rule_plan = None   # based_on_id points at a row that was not read back
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'incomplete'
    assert result['consistency']['missing'] == [f"trade_plans:{plan['based_on_id']}"]
    # with fifteen bars the rule plan is not needed
    plan, rows = _make_own()
    rows.rule_plan = None
    assert derivation.derive_plan(plan, rows)['consistency']['status'] == 'consistent'


def test_own_plan_bars_not_matching_ids_is_incomplete():
    plan, rows = _make_own()
    rows.bars = rows.bars[:10]
    assert 'bar_ids' in derivation.derive_plan(plan, rows)['consistency']['missing']


# ---------------------------------------------------------------- agent plans

def _make_agent(*, bars=None, earnings=None, snapshot='default', exposure=None, params=None, context=None,
                run_id='run-1', levels=None):
    bars = bars or _bars()
    snapshot = _snapshot() if snapshot == 'default' else snapshot
    levels = levels or OWN_LEVELS
    plan = plans.build_agent_plan(
        levels=levels, agent_run_id=run_id, model='m', ticker='AAA', signal_date=SIGNAL, bars=bars,
        earnings_row=earnings, snapshot=snapshot, exposure=exposure or _exposure(),
        params=params or WorkbenchConfig(), context=context)
    plan['id'] = uuid.uuid4()
    rows = _rows(bars, earnings, snapshot)
    rows.agent_run = {'id': run_id, 'status': 'ok', 'output': {'levels': {
        key: {'value': str(value), 'refs': [f'call:{i}', 'call:9']} for i, (key, value) in enumerate(levels.items())}}}
    return plan, rows


def test_agent_plan_checks_output():
    plan, rows = _make_agent()
    result = derivation.derive_plan(plan, rows)
    assert result['consistency'] == {'status': 'consistent', 'mismatches': [], 'missing': []}, result['consistency']
    assert result['plan']['kind'] == 'agent' and result['plan']['plan_version'] == 'agent-1'
    for i, key in enumerate(('entry_low', 'entry_high', 'stop', 'target1')):
        step = _step(result, key)
        assert step['source'] == 'agent_output' and step['consistent'] is True
        assert step['refs'] == [f'call:{i}', 'call:9']
        assert Decimal(step['result']) == Decimal(step['stored']) == OWN_LEVELS[key]
    assert _step(result, 'per_share_risk')['substitutions'] == {'entry_mid': '99.875', 'stop': '95'}
    assert 'refs' not in _step(result, 'atr')

    plan, rows = _make_agent()
    rows.agent_run['output']['levels']['stop']['value'] = '94.00'
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'mismatch'
    assert [m['key'] for m in result['consistency']['mismatches']] == ['stop']
    assert result['consistency']['mismatches'][0] == {'key': 'stop', 'result': '94.00', 'stored': '95'}


def test_agent_plan_missing_run_is_incomplete():
    plan, rows = _make_agent()
    rows.agent_run = None
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'incomplete'
    assert result['consistency']['missing'] == ['agent_runs:run-1']
    # the rest still shows
    assert _step(result, 'atr')['consistent'] is True and 'entry_mid' in _keys(result)

    plan, rows = _make_agent()
    del rows.agent_run['output']['levels']['target1']
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'incomplete'
    assert result['consistency']['missing'] == ['agent_runs.output.levels.target1']

    plan, rows = _make_agent()
    rows.agent_run['output'] = None
    assert derivation.derive_plan(plan, rows)['consistency']['status'] == 'incomplete'


def test_agent_plan_without_atr_degrades_to_breakeven():
    plan, rows = _make_agent(bars=_bars(n=14))
    assert plan['atr'] == 0
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'consistent', result['consistency']
    atr = _step(result, 'atr')
    assert atr['result'] == '0' and '移动止损退化为保本' in atr['formula'] and atr['source'] == '无 ATR'
    # fifteen bars: ATR is computed, no breakeven note
    plan, rows = _make_agent()
    assert '移动止损退化为保本' not in _step(derivation.derive_plan(plan, rows), 'atr')['formula']


def test_agent_plan_invalid_reasons_stay_invalid():
    plan, rows = _make_agent(earnings=_earnings(date(2026, 10, 23)), exposure=_exposure('30000', '30000'))
    assert plan['status'] == 'invalid'
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'consistent', result['consistency']
    assert _step(result, 'status')['result'] == 'invalid'
    assert set(_step(result, 'invalid_reasons')['result']) == {'earnings_in_validity', 'no_room'}


@pytest.mark.parametrize('maker', [_make_own, _make_agent])
@pytest.mark.parametrize('name', ['no_equity', 'earnings_within_10d', 'risk_off', 'capped_total'])
def test_variant_plans_consistent_across_scenarios(maker, name):
    plan, rows = maker(**VARIANTS[name])
    result = derivation.derive_plan(plan, rows)
    assert result['consistency'] == {'status': 'consistent', 'mismatches': [], 'missing': []}, result['consistency']


@pytest.mark.parametrize('maker', [_make_own, _make_agent])
def test_variant_plan_json_roundtrip_and_purity(maker, monkeypatch):
    plan, rows = maker(earnings=_earnings(date(2026, 10, 23)),
                       context={**CONTEXT_BASE, 'next_earnings': date(2026, 10, 23)})
    stored = copy.deepcopy(plan)
    stored['sizing'] = json.loads(core_dbutil._plan_json(plan['sizing']))
    stored['inputs'] = json.loads(core_dbutil._plan_json(plan['inputs']))

    def boom(*a, **k):
        raise AssertionError('derivation must not call plans math')

    for name in ('rule_levels', 'size', 'atr14', 'earnings_flags', 'validity', 'context_flags', 'levels_ordered',
                 '_cent', '_freeze', '_floor'):
        monkeypatch.setattr(plans, name, boom)
    before = copy.deepcopy(rows)
    result = derivation.derive_plan(stored, rows)
    assert result['consistency']['status'] == 'consistent', result['consistency']
    assert rows == before
    json.dumps(result)


# ---------------------------------------------------------------- agent-2 version plans (v2.5 §5.2)

def _make_agent2(*, as_of=SIGNAL, version_no=0, mode='entry', earnings=None, snapshot='default', exposure=None,
                 bars=None, run_id='run-2'):
    from investment_assistant.execution import timeline
    bars = bars or _bars()
    snapshot = _snapshot() if snapshot == 'default' else snapshot
    output = {'action': 'buy', 'summary': 's',
              'levels': {key: {'value': str(value), 'refs': ['call:1']} for key, value in OWN_LEVELS.items()}}
    plan = timeline.build_version_plan(
        output=output, agent_run_id=run_id, model='m', ticker='AAA', signal_date=SIGNAL, as_of_session=as_of,
        version_no=version_no, supersedes_plan_id=None if version_no == 0 else uuid.uuid4(), mode=mode,
        bars=bars, earnings_row=earnings, snapshot=snapshot, exposure=exposure or _exposure(),
        params=WorkbenchConfig(), context=None)
    plan['id'] = uuid.uuid4()
    rows = _rows(bars, earnings, snapshot)
    rows.agent_run = {'id': run_id, 'status': 'ok', 'output': output}
    rows.version = {'plan_id': plan['id'], 'version_no': version_no, 'as_of_session': as_of}
    return plan, rows


AGENT2_CASES = {
    'first_version': dict(),
    'entry_revision_capped': dict(as_of=add_trading_days(SIGNAL, 8), version_no=1),
    'entry_revision': dict(as_of=add_trading_days(SIGNAL, 2), version_no=2),
    'holding_revision': dict(as_of=add_trading_days(SIGNAL, 15), version_no=3, mode='holding'),
    'holding_revision_no_room': dict(as_of=add_trading_days(SIGNAL, 15), version_no=1, mode='holding',
                                     exposure=_exposure('30000', '30000')),
    'earnings_in_revision': dict(as_of=add_trading_days(SIGNAL, 2), version_no=1,
                                 earnings=_earnings(add_trading_days(SIGNAL, 4))),
}


@pytest.mark.parametrize('name', sorted(AGENT2_CASES))
def test_agent2_versions_consistent(name):
    plan, rows = _make_agent2(**AGENT2_CASES[name])
    result = derivation.derive_plan(plan, rows)
    assert result['consistency'] == {'status': 'consistent', 'mismatches': [], 'missing': []}, result['consistency']
    assert result['plan']['plan_version'] == 'agent-2'
    assert _step(result, 'version_no')['consistent'] is True
    assert _step(result, 'as_of_session')['consistent'] is True
    assert _step(result, 'valid_until')['result'] == plan['valid_until'].isoformat()


def test_agent2_validity_steps_follow_as_of_and_cap():
    cap = add_trading_days(SIGNAL, 10)
    plan, rows = _make_agent2(as_of=add_trading_days(SIGNAL, 8), version_no=1)
    result = derivation.derive_plan(plan, rows)
    assert _step(result, 'valid_from')['result'] == add_trading_days(SIGNAL, 9).isoformat()
    assert _step(result, 'chain_entry_cap')['result'] == cap.isoformat()
    assert _step(result, 'valid_until')['result'] == cap.isoformat()             # min(S+3, 信号日+10)
    plan, rows = _make_agent2(as_of=add_trading_days(SIGNAL, 15), version_no=1, mode='holding')
    result = derivation.derive_plan(plan, rows)
    assert 'chain_entry_cap' not in _keys(result)                                  # 持仓期没有上限
    assert _step(result, 'valid_until')['result'] == add_trading_days(SIGNAL, 18).isoformat()
    plan, rows = _make_agent2()
    result = derivation.derive_plan(plan, rows)                                    # 首版：信号日 T+1…T+3
    assert _step(result, 'valid_from')['result'] == add_trading_days(SIGNAL, 1).isoformat()
    assert _step(result, 'valid_until')['result'] == add_trading_days(SIGNAL, 3).isoformat()
    assert _step(result, 'chain_entry_cap')['result'] == cap.isoformat()


def test_agent2_missing_version_row_incomplete():
    plan, rows = _make_agent2()
    rows.version = None
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'incomplete'
    assert result['consistency']['missing'] == [f"agent_plan_versions:{plan['id']}"]
    assert _step(result, 'atr')['consistent'] is True                              # 其余照常展示

    plan, rows = _make_agent2(as_of=add_trading_days(SIGNAL, 2), version_no=1)
    rows.version = {**rows.version, 'version_no': 2}                               # 版本号不一致 → mismatch
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'mismatch'
    assert [m['key'] for m in result['consistency']['mismatches']] == ['version_no']

    plan, rows = _make_agent2(as_of=add_trading_days(SIGNAL, 2), version_no=1)
    plan['valid_until'] = add_trading_days(SIGNAL, 3)                               # 按首版口径存的有效期
    assert derivation.derive_plan(plan, rows)['consistency']['status'] == 'mismatch'


def test_agent2_json_roundtrip_and_purity(monkeypatch):
    from investment_assistant.execution import timeline
    plan, rows = _make_agent2(as_of=add_trading_days(SIGNAL, 8), version_no=1,
                              earnings=_earnings(add_trading_days(SIGNAL, 20)))
    stored = copy.deepcopy(plan)
    stored['sizing'] = json.loads(core_dbutil._plan_json(plan['sizing']))
    stored['inputs'] = json.loads(core_dbutil._plan_json(plan['inputs']))

    def boom(*a, **k):
        raise AssertionError('derivation must not call plans / timeline math')

    for name in ('size', 'atr14', 'earnings_flags', 'validity', 'context_flags', '_freeze'):
        monkeypatch.setattr(plans, name, boom)
    for name in ('version_validity', 'build_version_plan'):
        monkeypatch.setattr(timeline, name, boom)
    result = derivation.derive_plan(stored, rows)
    assert result['consistency']['status'] == 'consistent', result['consistency']
    json.dumps(result)


# ---------------------------------------------------------------- trigger section

def _cand(rule_id, values, thresholds=None, *, version='1', action='buy', **extra):
    return {'id': uuid.uuid4(), 'rule_id': rule_id, 'rule_version': version, 'action': action,
            'inputs': {'signal_date': '2026-10-21', 'bar_ids': [1], 'values': values,
                       'thresholds': {} if thresholds is None else thresholds, **extra}}


def _results(rule_id, values, thresholds=None):
    out = derivation.derive_trigger([_cand(rule_id, values, thresholds)])
    assert len(out) == 1 and out[0]['rule_id'] == rule_id
    return [c['result'] for c in out[0]['clauses']]


H, V, N = 'holds', 'violated', 'not_frozen'
TREND_RECLAIM = {'close': 110.0, 'ma20': 105.0, 'ma50': 100.0, 'ma200': 90.0}
VOLUME_BREAKOUT = {'close': 105.0, 'prior_high': 104.0, 'volume': 2000, 'avg_volume_50': 1000.0, 'ma50': 100.0}
GAP = {'open': 103.0, 'close': 104.0, 'prev_close': 100.0, 'gap': 0.03}
GAP_DOWN = {'open': 97.0, 'close': 96.5, 'prev_close': 100.0, 'gap': -0.03}
DRAWDOWN = {'close': 91.0, 'avg_cost': 100.0, 'high_since_opened': 100.0, 'from_cost': -0.09, 'from_high': -0.09,
            'triggered': ['cost']}

TRIGGER_CASES = [
    ('trend_reclaim', TREND_RECLAIM, {'days_below_ma20': 3}, [H, H, N]),
    ('trend_reclaim', {**TREND_RECLAIM, 'ma50': 120.0}, {'days_below_ma20': 3}, [V, H, N]),
    ('trend_reclaim', {**TREND_RECLAIM, 'ma200': 105.0}, {'days_below_ma20': 3}, [V, H, N]),
    ('trend_reclaim', {**TREND_RECLAIM, 'ma20': 111.0}, {'days_below_ma20': 3}, [H, V, N]),
    ('trend_reclaim', {k: v for k, v in TREND_RECLAIM.items() if k != 'ma200'}, {}, [N, H, N]),
    ('trend_reclaim', {**{k: v for k, v in TREND_RECLAIM.items() if k != 'ma200'}, 'ma50': 120.0}, {}, [V, H, N]),
    ('volume_breakout', VOLUME_BREAKOUT, {'lookback': 20, 'volume_multiple': 1.5}, [H, H, H]),
    ('volume_breakout', {**VOLUME_BREAKOUT, 'volume': 1400}, {'lookback': 20, 'volume_multiple': 1.5}, [H, V, H]),
    ('volume_breakout', {**VOLUME_BREAKOUT, 'volume': 1500}, {'lookback': 20, 'volume_multiple': 1.5}, [H, H, H]),
    ('volume_breakout', {**VOLUME_BREAKOUT, 'close': 104.0, 'ma50': 105.0}, {'volume_multiple': 1.5}, [V, H, V]),
    ('volume_breakout', VOLUME_BREAKOUT, {}, [H, N, H]),
    ('earnings_gap_up', GAP, {'gap': 0.03}, [H, H]),
    ('earnings_gap_up', {**GAP, 'gap': 0.029}, {'gap': 0.03}, [V, H]),
    ('earnings_gap_up', {**GAP, 'close': 102.5}, {'gap': 0.03}, [H, V]),
    ('earnings_gap_up', GAP, {}, [N, H]),
    ('trend_break', {'close': 99.0, 'ma50': 100.0}, {'days_above_ma50': 10}, [N, H]),
    ('trend_break', {'close': 101.0, 'ma50': 100.0}, {'days_above_ma50': 10}, [N, V]),
    ('earnings_gap_down', GAP_DOWN, {'gap': 0.03}, [H, H]),
    ('earnings_gap_down', {**GAP_DOWN, 'gap': -0.02}, {'gap': 0.03}, [V, H]),
    ('earnings_gap_down', {**GAP_DOWN, 'close': 98.0}, {'gap': 0.03}, [H, V]),
    ('position_drawdown', DRAWDOWN, {'cost_stop': 0.08, 'trailing_stop': 0.12}, [H]),
    ('position_drawdown', {**DRAWDOWN, 'from_cost': -0.05, 'from_high': -0.13}, {'cost_stop': 0.08,
                                                                                  'trailing_stop': 0.12}, [H]),
    ('position_drawdown', {**DRAWDOWN, 'from_cost': -0.05, 'from_high': -0.05}, {'cost_stop': 0.08,
                                                                                  'trailing_stop': 0.12}, [V]),
    ('position_drawdown', {**DRAWDOWN, 'from_cost': -0.05, 'from_high': None}, {'cost_stop': 0.08,
                                                                                 'trailing_stop': 0.12}, [N]),
    ('position_drawdown', DRAWDOWN, {}, [N]),
    ('abnormal_move', {'return': -0.06, 'stdev_20': 0.02}, {'sigma': 2.5}, [H, H]),
    ('abnormal_move', {'return': 0.04, 'stdev_20': 0.02}, {'sigma': 2.5}, [H, V]),
    ('abnormal_move', {'return': 0.0, 'stdev_20': 0.02}, {'sigma': 2.5}, [V, V]),
    ('plan_stop', {'low': 92.0, 'stop': 95.0, 'mode': 'initial'}, {}, [H]),
    ('plan_stop', {'low': 95.0, 'stop': 95.0, 'mode': 'initial'}, {}, [H]),
    ('plan_stop', {'low': 96.0, 'stop': 95.0, 'mode': 'initial'}, {}, [V]),
    ('plan_target', {'high': 113.0, 'target1': 112.0, 'sell_fraction': 0.5}, {}, [H, N]),
    ('plan_target', {'high': 111.0, 'target1': 112.0, 'sell_fraction': 0.5}, {}, [V, N]),
    ('new_filing', {'forms': ['8-K', '10-Q']}, {}, [H, N]),
    ('new_filing', {'forms': ['S-1']}, {}, [V, N]),
    ('new_filing', {'forms': []}, {}, [V, N]),
    ('new_filing', {}, {}, [N, N]),
]


@pytest.mark.parametrize('rule_id,values,thresholds,expected', TRIGGER_CASES)
def test_trigger_clauses_three_states(rule_id, values, thresholds, expected):
    assert _results(rule_id, values, thresholds) == expected


def test_every_rule_in_rules_py_has_conditions():
    from investment_assistant.analysis import rules
    assert set(derivation.CONDITIONS) == {*rules.PRICE_RULES, 'new_filing'}
    assert set(derivation.CONDITIONS) == {c[0] for c in TRIGGER_CASES}
    assert rules.RULE_VERSION == '1' and derivation.CONDITION_VERSIONS == ('1',)
    for rule_id, clauses in derivation.CONDITIONS.items():
        assert clauses and all(isinstance(c.text, str) and callable(c.check) for c in clauses), rule_id


def test_trigger_clause_shape_and_operands():
    cand = _cand('volume_breakout', {**VOLUME_BREAKOUT, 'volume': 1400}, {'lookback': 20, 'volume_multiple': 1.5})
    [out] = derivation.derive_trigger([cand])
    assert set(out) == {'candidate_id', 'rule_id', 'rule_version', 'action', 'clauses'}
    assert out['candidate_id'] == str(cand['id']) and out['rule_version'] == '1' and out['action'] == 'buy'
    assert [c['text'] for c in out['clauses']] == [
        'close > prior_high', 'volume ≥ 1.5 × avg_volume_50', 'close > ma50']
    assert [c['result'] for c in out['clauses']] == [H, V, H]
    assert out['clauses'][1]['operands'] == {'values.volume': '1400', 'values.avg_volume_50': '1000.0',
                                             'thresholds.volume_multiple': '1.5'}
    assert all(set(c) == {'text', 'operands', 'result'} for c in out['clauses'])
    json.dumps(out)
    [third] = derivation.derive_trigger([_cand('trend_reclaim', TREND_RECLAIM, {'days_below_ma20': 3})])
    assert third['clauses'][2]['result'] == N and third['clauses'][2]['text'] == '此前连续 3 日收于 MA20 之下'
    assert third['clauses'][0]['text'] == 'close > ma50 > ma200'
    # an operand that was not frozen shows as None
    [partial] = derivation.derive_trigger([_cand('volume_breakout', VOLUME_BREAKOUT, {})])
    assert partial['clauses'][1]['operands']['thresholds.volume_multiple'] is None


def test_trigger_unknown_rule_version_is_not_frozen():
    [out] = derivation.derive_trigger([_cand('trend_reclaim', TREND_RECLAIM, {'days_below_ma20': 3}, version='2')])
    assert out['rule_version'] == '2' and out['note'] == 'unknown_rule_version'
    assert len(out['clauses']) == 3
    assert all(c['result'] == N and c['note'] == 'unknown_rule_version' for c in out['clauses'])
    [unknown_rule] = derivation.derive_trigger([_cand('mystery', {'x': 1})])
    assert unknown_rule['clauses'] == [] and unknown_rule['note'] == 'unknown_rule_version'
    [fine] = derivation.derive_trigger([_cand('trend_reclaim', TREND_RECLAIM, {'days_below_ma20': 3})])
    assert 'note' not in fine and all('note' not in c for c in fine['clauses'])


def test_trigger_malformed_values_are_not_frozen_not_a_crash():
    assert _results('trend_reclaim', {'close': 'abc', 'ma20': 1, 'ma50': 1, 'ma200': 1}) == [N, N, N]
    assert _results('plan_stop', {'low': None, 'stop': 95.0}) == [N]


def test_trigger_candidates_without_inputs():
    [out] = derivation.derive_trigger([{'id': 1, 'rule_id': 'plan_stop', 'rule_version': '1', 'action': 'sell',
                                        'inputs': None}])
    assert [c['result'] for c in out['clauses']] == [N]


def _scenario_series(closes, *, opens=None, volumes=None, end=SIGNAL):
    n = len(closes)
    opens = opens or closes
    volumes = volumes or [1000] * n
    return [{'id': i + 1, 'price_date': add_trading_days(end, -(n - 1 - i)), 'open': float(o),
             'high': float(max(o, c)), 'low': float(min(o, c)), 'close': float(c), 'adj_close': float(c),
             'volume': int(v), 'split_factor': 1.0}
            for i, (o, c, v) in enumerate(zip(opens, closes, volumes))]


def _after_close_8k(day):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return {'id': 7, 'form': '8-K', 'items': ['2.02'], 'filing_date': day,
            'accepted_at': datetime(day.year, day.month, day.day, 16, 20, tzinfo=ZoneInfo('America/New_York'))}


def _held():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return {'quantity': Decimal('10'), 'avg_cost': Decimal('100'),
            'opened_at': datetime(2026, 9, 1, 15, tzinfo=ZoneInfo('America/New_York'))}


def _plan_state(**o):
    return {'stop': Decimal('95'), 't1_reached': False, 't1_reached_on': None, 'mode': 'initial',
            'target1': Decimal('112'), **o}


def _real_scenarios():
    """(bars, filings, position, plan_state) tuples that make every rule fire, built with rules.evaluate."""
    def gap_bars(open_t, close_t):
        return _scenario_series([100.0] * 249 + [close_t], opens=[100.0] * 249 + [open_t])

    up = [50 + i * 0.25 for i in range(240)]
    top = up[-1]
    breakout = [100 + (i % 5) * 0.1 + i * 0.05 for i in range(249)] + [117.5]
    prev_day = add_trading_days(SIGNAL, -1)
    return [
        (_scenario_series(up + [top - 6] * 3 + [top + 1]), [], None, None),
        (_scenario_series(breakout, volumes=[1000] * 249 + [1500]), [], None, None),
        (gap_bars(103, 104), [_after_close_8k(prev_day)], None, None),
        (gap_bars(97, 96.5), [_after_close_8k(prev_day)], None, None),
        (_scenario_series([100 + i * 0.1 for i in range(249)] + [100]), [], None, None),
        (_scenario_series([100.0] * 249 + [91.0]), [], _held(), None),
        (_scenario_series([100 + (1 if i % 2 else -1) for i in range(249)] + [115]), [], None, None),
        (_scenario_series([100] * 249 + [92]), [], _held(), _plan_state()),
        (_scenario_series([100] * 249 + [113]), [], _held(), _plan_state()),
        (_scenario_series([100.0] * 10), [{'id': 3, 'form': '10-Q', 'items': [], 'filing_date': SIGNAL,
                                           'accepted_at': None}], None, None),
    ]


def test_real_candidates_from_rules_are_never_violated():
    """Every clause mirrors rules.py: a candidate rules.evaluate emitted holds (or is not frozen) on each clause."""
    from investment_assistant.analysis import rules
    seen: set[str] = set()
    for bars, filings, position, state in _real_scenarios():
        result = rules.evaluate('AAA', SIGNAL, bars, filings, position, plan_state=state)
        rows = [{'id': uuid.uuid4(), 'rule_id': c.rule_id, 'rule_version': c.rule_version, 'action': c.action,
                 'inputs': json.loads(json.dumps(c.inputs))} for c in result['candidates']]
        for out in derivation.derive_trigger(rows):
            seen.add(out['rule_id'])
            results = [c['result'] for c in out['clauses']]
            assert V not in results, (out['rule_id'], out['clauses'])
            assert H in results, (out['rule_id'], out['clauses'])
    assert seen == set(derivation.CONDITIONS)


def test_derive_unit_trigger_only():
    cands = [_cand('volume_breakout', VOLUME_BREAKOUT, {'volume_multiple': 1.5}),
             _cand('trend_reclaim', TREND_RECLAIM, {'days_below_ma20': 3})]
    unit = derivation.derive_unit(cands)
    assert set(unit) == {'derivation_version', 'trigger', 'consistency', 'evidence', 'note'}
    assert 'steps' not in unit
    assert unit['derivation_version'] == 'derivation-1' and unit['note'] == derivation.TRIGGER_NOTE
    assert unit['evidence'] == derivation.EVIDENCE_TEXT
    assert [t['rule_id'] for t in unit['trigger']] == ['volume_breakout', 'trend_reclaim']
    assert unit['consistency'] == {'status': 'consistent', 'mismatches': [], 'missing': []}
    json.dumps(unit)
    bad = derivation.derive_unit([_cand('volume_breakout', {**VOLUME_BREAKOUT, 'volume': 1400},
                                        {'volume_multiple': 1.5})])
    assert bad['consistency']['status'] == 'mismatch'
    assert [m['key'] for m in bad['consistency']['mismatches']] == ['trigger:volume_breakout']
    assert derivation.derive_unit([])['trigger'] == []


# ---------------------------------------------------------------- consistency and the trigger shape

def _trigger_item(rule_id, *results):
    return {'candidate_id': 'c1', 'rule_id': rule_id, 'rule_version': '1', 'action': 'buy',
            'clauses': [{'text': f'clause {i}', 'operands': {}, 'result': r} for i, r in enumerate(results)]}


def test_consistency_reads_nested_trigger_clauses():
    verdict = derivation.consistency([], [_trigger_item('trend_reclaim', H, V, N)], [])
    assert verdict['status'] == 'mismatch'
    assert [m['key'] for m in verdict['mismatches']] == ['trigger:trend_reclaim']
    assert verdict['mismatches'][0]['result'] == 'violated' and 'clause 1' in verdict['mismatches'][0]['stored']
    # the violated clause is not the first one, and a later candidate may be the offender
    verdict = derivation.consistency([], [_trigger_item('a', H), _trigger_item('b', N, N, V)], [])
    assert [m['key'] for m in verdict['mismatches']] == ['trigger:b']
    # not_frozen and holds never count
    assert derivation.consistency([], [_trigger_item('a', H, N, N)], [])['status'] == 'consistent'
    # incomplete outranks the violated trigger, mismatches still listed
    verdict = derivation.consistency([], [_trigger_item('a', V)], ['price_bars:1'])
    assert verdict['status'] == 'incomplete' and len(verdict['mismatches']) == 1


def test_consistency_rejects_flat_trigger_items():
    """The old flat shape (result on the item) must fail loudly, never be read as 'no violation'."""
    with pytest.raises(KeyError):
        derivation.consistency([], [{'key': 'trigger:x', 'result': 'violated'}], [])


def test_violated_clause_makes_mismatch():
    plan, rows = _make()
    rows.candidates = [_cand('volume_breakout', {**VOLUME_BREAKOUT, 'volume': 1400}, {'volume_multiple': 1.5})]
    result = derivation.derive_plan(plan, rows)
    assert [t['rule_id'] for t in result['trigger']] == ['volume_breakout']
    assert result['consistency']['status'] == 'mismatch'
    assert [m['key'] for m in result['consistency']['mismatches']] == ['trigger:volume_breakout']
    assert all(s['consistent'] is not False for s in result['steps'])
    # the same candidate with a satisfied threshold keeps the plan consistent
    rows.candidates = [_cand('volume_breakout', VOLUME_BREAKOUT, {'volume_multiple': 1.5})]
    result = derivation.derive_plan(plan, rows)
    assert result['trigger'][0]['clauses'][1]['result'] == H
    assert result['consistency'] == {'status': 'consistent', 'mismatches': [], 'missing': []}
    # own and agent plans carry the trigger section too
    own, own_rows = _make_own()
    own_rows.candidates = [_cand('volume_breakout', {**VOLUME_BREAKOUT, 'volume': 1400}, {'volume_multiple': 1.5})]
    assert derivation.derive_plan(own, own_rows)['consistency']['mismatches'][0]['key'] == 'trigger:volume_breakout'


# ---------------------------------------------------------------- plan-2 and agent-3 (v2.9 §5.4, §5.5, D6)

def test_plan2_derivation_consistent():
    plan = plans.build_rule_plan(
        ticker='AAA', signal_date=SIGNAL, bars=_bars(), earnings_row=None, snapshot=_snapshot(), exposure=_exposure(),
        params=WorkbenchConfig(), version=plans.PLAN_V2)
    plan['id'] = uuid.uuid4()
    result = derivation.derive_plan(plan, _rows(_bars(), None, _snapshot()))
    assert result['consistency'] == {'status': 'consistent', 'mismatches': [], 'missing': []}, result['consistency']
    assert result['plan']['plan_version'] == 'plan-2'
    assert _step(result, 'entry_type')['result'] == 'zone_limit' and _step(result, 'entry_type')['consistent'] is True
    hold = _step(result, 'max_hold_sessions')
    assert (hold['result'], hold['stored'], hold['consistent']) == ('10', '10', True)
    # the plan-1 steps are the same, step for step
    old = plans.build_rule_plan(ticker='AAA', signal_date=SIGNAL, bars=_bars(), earnings_row=None,
                                snapshot=_snapshot(), exposure=_exposure(), params=WorkbenchConfig())
    old['id'] = uuid.uuid4()
    first = derivation.derive_plan(old, _rows(_bars(), None, _snapshot()))
    assert [s for s in result['steps'] if s['key'] not in ('entry_type', 'max_hold_sessions')] == first['steps']
    plan['max_hold_sessions'] = 12                                              # tampered execution field
    tampered = derivation.derive_plan(plan, _rows(_bars(), None, _snapshot()))
    assert [m['key'] for m in tampered['consistency']['mismatches']] == ['max_hold_sessions']
    assert derivation.RULE_ENTRY_TYPE == plans.DEFAULT_ENTRY_TYPE and derivation.RULE_MAX_HOLD_SESSIONS == plans.RULE_MAX_HOLD_SESSIONS


def test_plan2_own_plan_derivation_consistent():
    """Task-6 review I3: a v2.9 own plan (``plan-2`` / ``own``, written with a fill) is derived, not ``incomplete``."""
    bars, snapshot = _bars(), _snapshot()
    fill_day = date(2026, 10, 27)
    rule_plan = {'id': uuid.uuid4(), 'atr': Decimal('2')}
    plan = plans.build_own_plan(
        rule_plan=rule_plan, levels=OWN_LEVELS, ticker='AAA', signal_date=SIGNAL, bars=bars, earnings_row=None,
        snapshot=snapshot, exposure=_exposure(), params=WorkbenchConfig(), version=plans.PLAN_V2, fill_session=fill_day,
        max_hold_sessions=15)
    plan['id'] = uuid.uuid4()
    rows = _rows(bars, None, snapshot)
    rows.rule_plan = rule_plan
    result = derivation.derive_plan(plan, rows)
    assert result['consistency'] == {'status': 'consistent', 'mismatches': [], 'missing': []}, result['consistency']
    assert (result['plan']['kind'], result['plan']['plan_version']) == ('own', 'plan-2')
    assert _step(result, 'valid_until')['result'] == fill_day.isoformat()
    assert _step(result, 'entry_type')['consistent'] is True
    hold = _step(result, 'max_hold_sessions')
    assert (hold['result'], hold['stored'], hold['consistent']) == ('15', '15', True)
    assert derivation.MAX_HOLD_RANGE == plans.MAX_HOLD_RANGE
    for field, value in (('max_hold_sessions', 31), ('entry_type', 'next_open'), ('valid_until', date(2026, 10, 28))):
        bad = derivation.derive_plan({**plan, field: value}, rows)
        assert bad['consistency']['status'] == 'mismatch', field
        assert field in [m['key'] for m in bad['consistency']['mismatches']], field


def _make_agent3(*, as_of=SIGNAL, version_no=0, mode='entry', entry_type='zone_limit', max_hold=10, retriggers=(),
                 cutoff=None, run_id='run-3'):
    from datetime import UTC, datetime

    from investment_assistant.execution import timeline
    bars = _bars()
    cutoff = cutoff or datetime(2026, 10, 30, 22, 0, tzinfo=UTC)
    entry_cap = timeline.entry_cap({'signal_date': SIGNAL}, list(retriggers)) if mode == 'entry' else None
    output = {'action': 'buy', 'summary': 's', 'entry_type': entry_type, 'max_hold_sessions': max_hold,
              'levels': {key: {'value': str(value), 'refs': ['call:1']} for key, value in OWN_LEVELS.items()}}
    plan = timeline.build_version_plan(
        output=output, agent_run_id=run_id, model='m', ticker='AAA', signal_date=SIGNAL, as_of_session=as_of,
        version_no=version_no, supersedes_plan_id=None if version_no == 0 else uuid.uuid4(), mode=mode, bars=bars,
        earnings_row=None, snapshot=_snapshot(), exposure=_exposure(), params=WorkbenchConfig(), context=None,
        version=timeline.AGENT_V3, entry_cap=entry_cap)
    plan['id'] = uuid.uuid4()
    rows = _rows(bars, None, _snapshot())
    rows.agent_run = {'id': run_id, 'status': 'ok', 'output': output}
    rows.version = {'plan_id': plan['id'], 'version_no': version_no, 'as_of_session': as_of, 'cutoff_at': cutoff}
    rows.retriggers = list(retriggers)
    return plan, rows


def test_agent3_derivation_consistent_and_checks_execution_fields():
    plan, rows = _make_agent3(entry_type='breakout_stop', max_hold=12)
    result = derivation.derive_plan(plan, rows)
    assert result['consistency'] == {'status': 'consistent', 'mismatches': [], 'missing': []}, result['consistency']
    assert result['plan']['plan_version'] == 'agent-3'
    assert _step(result, 'entry_type')['source'] == 'agent_output' and _step(result, 'entry_type')['stored'] == 'breakout_stop'
    assert _step(result, 'max_hold_sessions')['result'] == '12'
    plan['max_hold_sessions'] = 9
    plan['entry_type'] = 'next_open'
    bad = derivation.derive_plan(plan, rows)
    assert [m['key'] for m in bad['consistency']['mismatches']] == ['entry_type', 'max_hold_sessions']
    plan, rows = _make_agent3()
    rows.agent_run['output'].pop('entry_type')
    assert 'agent_runs.output.entry_type' in derivation.derive_plan(plan, rows)['consistency']['missing']


def test_agent3_validity_uses_retrigger_cap():
    from datetime import UTC, datetime
    first_cap = add_trading_days(SIGNAL, 10)
    trigger_day = add_trading_days(SIGNAL, 8)
    retrigger = {'unit_signal_date': trigger_day, 'entry_cap': add_trading_days(trigger_day, 10),
                 'created_at': datetime(2026, 10, 30, 21, 0, tzinfo=UTC), 'chain_state': 'entry'}
    plan, rows = _make_agent3(as_of=trigger_day, version_no=1, retriggers=[retrigger])
    result = derivation.derive_plan(plan, rows)
    assert result['consistency']['status'] == 'consistent', result['consistency']
    cap = _step(result, 'chain_entry_cap')
    assert cap['result'] == add_trading_days(trigger_day, 10).isoformat() and cap['consistent'] is True
    assert cap['result'] != first_cap.isoformat()
    assert _step(result, 'valid_until')['result'] == add_trading_days(trigger_day, 3).isoformat()
    # a trigger written after the version's cutoff is not part of its cap
    late = {**retrigger, 'created_at': datetime(2026, 10, 30, 23, 0, tzinfo=UTC)}
    plan, rows = _make_agent3(as_of=trigger_day, version_no=1, retriggers=[retrigger])
    rows.retriggers = [late]
    mismatch = derivation.derive_plan(plan, rows)
    assert [m['key'] for m in mismatch['consistency']['mismatches']] == ['chain_entry_cap', 'valid_until']
    # a holding revision has no cap at all
    plan, rows = _make_agent3(as_of=add_trading_days(SIGNAL, 15), version_no=2, mode='holding')
    assert 'chain_entry_cap' not in _keys(derivation.derive_plan(plan, rows))
