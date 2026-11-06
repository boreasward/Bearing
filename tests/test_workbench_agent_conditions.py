"""condition-1 / condition-2 registry (v2.5 spec §4.2, v2.9 spec §7.1): pure functions, no database."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from investment_assistant.execution import conditions
from investment_assistant.execution.citations import Rejected
from investment_assistant.execution.conditions import (CONDITIONS, CONDITIONS_ALL, CONDITIONS_VERSION, ConditionDef, Facts,
                                                             describe, evaluate, validate)

AS_OF = date(2026, 10, 20)        # version generated on this session (Tuesday)
S = date(2026, 10, 27)            # review session (Tuesday)
LAST_CLOSE = Decimal('100')


def _bar(day: date, low, high, close) -> dict:
    return {'price_date': day, 'open': Decimal(str(close)), 'high': Decimal(str(high)),
            'low': Decimal(str(low)), 'close': Decimal(str(close)), 'adj_close': Decimal(str(close)),
            'volume': 1_000_000}


# Bars after the version: all above the entry zone (98–101) — price never came back.
ABOVE = [_bar(date(2026, 10, 21), 102, 105, 104), _bar(date(2026, 10, 22), 103, 106, 105),
         _bar(date(2026, 10, 23), 101.5, 104, 103), _bar(date(2026, 10, 26), 102, 104, 103),
         _bar(date(2026, 10, 27), 102, 103, 102.5)]
# One bar dips into the zone on 10-22.
TOUCHED = [ABOVE[0], _bar(date(2026, 10, 22), 100, 106, 105), *ABOVE[2:]]


def facts(**overrides) -> Facts:
    base = Facts(session=S, mode='entry', bars=list(ABOVE), bars_since_version=list(ABOVE),
                 entry_low=Decimal('98.00'), entry_high=Decimal('101.00'), regime_label='neutral',
                 sector_state='neutral', earnings_date=None, filings=[], rs={20: 0.02, 60: -0.01},
                 as_of_session=AS_OF)
    return replace(base, **overrides)


def item(kind: str, **params) -> dict:
    return {'kind': kind, 'params': params}


CONDITION1_KINDS = {'close_below', 'close_above', 'no_entry_by', 'no_entry_before_earnings',
                    'earnings_within', 'regime_is', 'sector_weak', 'rs_below', 'new_filing',
                    'date_reached', 'next_session'}


def test_condition1_kinds_unchanged():
    assert conditions.CONDITIONS_V1 == 'condition-1'
    assert set(CONDITIONS) == CONDITION1_KINDS                       # the condition-1 view the agent-2 prompt renders
    assert set(CONDITIONS_ALL) - CONDITION1_KINDS == {'held_sessions_reached'}
    assert all(CONDITIONS_ALL[k] is d for k, d in CONDITIONS.items())
    # the eleven old kinds keep their usage columns and templates
    assert CONDITIONS['close_below'].text == '收盘跌破 {price}' and CONDITIONS['earnings_within'].params == ('sessions',)
    old = {k: (d.invalidation, d.review) for k, d in CONDITIONS.items() if k in CONDITION1_KINDS}
    assert sum(inv for inv, _ in old.values()) == 9 and sum(rev for _, rev in old.values()) == 9


def test_registry_matches_spec():
    assert CONDITIONS_VERSION == 'condition-2'
    assert set(CONDITIONS_ALL) == CONDITION1_KINDS | {'held_sessions_reached'}
    assert all(isinstance(d, ConditionDef) and d.kind == k for k, d in CONDITIONS_ALL.items())
    assert CONDITIONS_ALL['held_sessions_reached'].invalidation and CONDITIONS_ALL['held_sessions_reached'].review
    review_only = {k for k, d in CONDITIONS_ALL.items() if d.review and not d.invalidation}
    invalidation_only = {k for k, d in CONDITIONS_ALL.items() if d.invalidation and not d.review}
    assert review_only == {'date_reached', 'next_session'}
    assert invalidation_only == {'no_entry_by', 'no_entry_before_earnings'}
    assert all(d.invalidation or d.review for d in CONDITIONS_ALL.values())
    assert CONDITIONS['rs_below'].params == ('window', 'value')
    assert CONDITIONS['sector_weak'].params == ()
    assert CONDITIONS['next_session'].text == '下一交易日'
    assert CONDITIONS['close_below'].text == '收盘跌破 {price}'
    assert CONDITIONS['rs_below'].text == '相对 SPY {window} 日强弱低于 {value}'


EARNINGS_SOON = date(2026, 10, 28)     # S+1
CASES = [
    # close_below / close_above
    ('close_below_met', item('close_below', price=Decimal('95')),
     {'bars': [*ABOVE[:-1], _bar(S, 94, 96, 94.5)]}, 'met'),
    ('close_below_not_met', item('close_below', price=Decimal('95')), {}, 'not_met'),
    ('close_below_unknown', item('close_below', price=Decimal('95')), {'bars': []}, 'unknown'),
    ('close_above_met', item('close_above', price=Decimal('104.50')), {}, 'met'),
    ('close_above_not_met', item('close_above', price=Decimal('110')), {}, 'not_met'),
    ('close_above_unknown', item('close_above', price=Decimal('110')), {'bars': []}, 'unknown'),
    # no_entry_by
    ('no_entry_by_met', item('no_entry_by', date=date(2026, 10, 26)), {}, 'met'),
    ('no_entry_by_touched', item('no_entry_by', date=date(2026, 10, 26)),
     {'bars_since_version': TOUCHED}, 'not_met'),
    ('no_entry_by_not_yet', item('no_entry_by', date=date(2026, 10, 30)), {}, 'not_met'),
    ('no_entry_by_holding', item('no_entry_by', date=date(2026, 10, 26)), {'mode': 'holding'}, 'not_met'),
    ('no_entry_by_unknown', item('no_entry_by', date=date(2026, 10, 26)), {'bars_since_version': []}, 'unknown'),
    # touch after the deadline date does not count
    ('no_entry_by_late_touch', item('no_entry_by', date=date(2026, 10, 23)),
     {'bars_since_version': [*ABOVE[:3], _bar(date(2026, 10, 26), 99, 104, 103), ABOVE[4]]}, 'met'),
    # missing / stale bars never make a no-entry condition 'met'
    ('no_entry_by_gap', item('no_entry_by', date=date(2026, 10, 26)),
     {'bars_since_version': [ABOVE[0], *ABOVE[2:]]}, 'unknown'),
    ('no_entry_by_stale', item('no_entry_by', date=date(2026, 10, 26)),
     {'bars_since_version': ABOVE[:1]}, 'unknown'),
    ('no_entry_by_last_bar_before_deadline', item('no_entry_by', date=date(2026, 10, 26)),
     {'bars_since_version': ABOVE[:3]}, 'unknown'),
    ('no_entry_by_only_after_deadline', item('no_entry_by', date=date(2026, 10, 26)),
     {'bars_since_version': ABOVE[4:]}, 'unknown'),
    ('no_entry_by_no_as_of', item('no_entry_by', date=date(2026, 10, 26)), {'as_of_session': None}, 'unknown'),
    ('no_entry_by_gap_but_touched', item('no_entry_by', date=date(2026, 10, 26)),
     {'bars_since_version': [TOUCHED[1]]}, 'not_met'),
    ('no_entry_by_deadline_after_session_bars_to_s', item('no_entry_by', date=date(2026, 10, 26)),
     {'session': date(2026, 10, 26), 'bars_since_version': ABOVE[:4]}, 'met'),
    # no_entry_before_earnings
    ('no_entry_before_earnings_gap', item('no_entry_before_earnings'),
     {'earnings_date': EARNINGS_SOON, 'bars_since_version': [ABOVE[0], *ABOVE[2:]]}, 'unknown'),
    ('no_entry_before_earnings_stale', item('no_entry_before_earnings'),
     {'earnings_date': EARNINGS_SOON, 'bars_since_version': ABOVE[:1]}, 'unknown'),
    ('no_entry_before_earnings_last_bar_before_s', item('no_entry_before_earnings'),
     {'earnings_date': EARNINGS_SOON, 'bars_since_version': ABOVE[:4]}, 'unknown'),
    ('no_entry_before_earnings_no_as_of', item('no_entry_before_earnings'),
     {'earnings_date': EARNINGS_SOON, 'as_of_session': None}, 'unknown'),
    ('no_entry_before_earnings_met', item('no_entry_before_earnings'), {'earnings_date': EARNINGS_SOON}, 'met'),
    ('no_entry_before_earnings_touched', item('no_entry_before_earnings'),
     {'earnings_date': EARNINGS_SOON, 'bars_since_version': TOUCHED}, 'not_met'),
    ('no_entry_before_earnings_far', item('no_entry_before_earnings'),
     {'earnings_date': date(2026, 11, 5)}, 'not_met'),
    ('no_entry_before_earnings_holding', item('no_entry_before_earnings'),
     {'earnings_date': EARNINGS_SOON, 'mode': 'holding'}, 'not_met'),
    ('no_entry_before_earnings_unknown', item('no_entry_before_earnings'), {}, 'unknown'),
    # earnings_within: S + 3 trading days = 2026-10-30
    ('earnings_within_met', item('earnings_within', sessions=3), {'earnings_date': date(2026, 10, 30)}, 'met'),
    ('earnings_within_far', item('earnings_within', sessions=3), {'earnings_date': date(2026, 11, 2)}, 'not_met'),
    ('earnings_within_today', item('earnings_within', sessions=3), {'earnings_date': S}, 'not_met'),
    ('earnings_within_unknown', item('earnings_within', sessions=3), {}, 'unknown'),
    # regime_is
    ('regime_is_met', item('regime_is', label='risk_off'), {'regime_label': 'risk_off'}, 'met'),
    ('regime_is_not_met', item('regime_is', label='risk_off'), {}, 'not_met'),
    ('regime_is_unknown', item('regime_is', label='risk_off'), {'regime_label': None}, 'unknown'),
    ('regime_is_insufficient', item('regime_is', label='risk_off'), {'regime_label': 'insufficient'}, 'unknown'),
    # sector_weak
    ('sector_weak_met', item('sector_weak'), {'sector_state': 'weak'}, 'met'),
    ('sector_weak_not_met', item('sector_weak'), {'sector_state': 'strong'}, 'not_met'),
    ('sector_weak_unknown', item('sector_weak'), {'sector_state': None}, 'unknown'),
    # rs_below
    ('rs_below_met', item('rs_below', window=60, value=0.0), {}, 'met'),
    ('rs_below_not_met', item('rs_below', window=20, value=0.0), {}, 'not_met'),
    ('rs_below_unknown', item('rs_below', window=20, value=0.0), {'rs': {20: None, 60: None}}, 'unknown'),
    # new_filing
    ('new_filing_met', item('new_filing', form='8-K'),
     {'filings': [{'form': '10-Q', 'filing_date': date(2026, 10, 22)},
                  {'form': '8-K', 'filing_date': date(2026, 10, 23)}]}, 'met'),
    ('new_filing_not_met', item('new_filing', form='8-K'),
     {'filings': [{'form': '10-Q', 'filing_date': date(2026, 10, 22)}]}, 'not_met'),
    ('new_filing_unknown', item('new_filing', form='8-K'), {'filings': None}, 'unknown'),
    # date_reached
    ('date_reached_met', item('date_reached', date=S), {}, 'met'),
    ('date_reached_not_met', item('date_reached', date=date(2026, 10, 28)), {}, 'not_met'),
    ('date_reached_unknown', item('date_reached', date=S), {'session': None}, 'unknown'),
    # next_session
    ('next_session_met', item('next_session'), {}, 'met'),
    ('next_session_met_without_data', item('next_session'),
     {'bars': [], 'bars_since_version': [], 'regime_label': None, 'filings': None}, 'met'),
    # held_sessions_reached (condition-2)
    ('held_sessions_met', item('held_sessions_reached', sessions=5), {'mode': 'holding', 'position': {
        'held_sessions': 6, 'reached_1r': False}}, 'met'),
    ('held_sessions_not_met', item('held_sessions_reached', sessions=5), {}, 'not_met'),
    ('held_sessions_unknown', item('held_sessions_reached', sessions=5), {'mode': 'holding', 'position': {
        'held_sessions': 6, 'reached_1r': None}}, 'unknown'),
]


@pytest.mark.parametrize('name,cond,overrides,expected', CASES, ids=[c[0] for c in CASES])
def test_evaluate_three_states(name, cond, overrides, expected):
    state, detail = evaluate(cond, facts(**overrides))
    assert state == expected
    assert isinstance(detail, str) and detail


def test_evaluate_covers_every_kind_in_all_states():
    seen: dict[str, set[str]] = {}
    for _, cond, _, expected in CASES:
        seen.setdefault(cond['kind'], set()).add(expected)
    assert set(seen) == set(CONDITIONS_ALL)
    for kind, states in seen.items():
        assert states == ({'met'} if kind == 'next_session' else {'met', 'not_met', 'unknown'}), kind


def test_evaluate_accepts_stored_json_params():
    """Items read back from agent_runs.output carry strings, not Decimal / date."""
    stored = {'kind': 'close_below', 'params': {'price': '95.00'}}
    assert evaluate(stored, facts(bars=[_bar(S, 94, 96, 94.5)]))[0] == 'met'
    stored = {'kind': 'no_entry_by', 'params': {'date': '2026-10-26'}}
    assert evaluate(stored, facts())[0] == 'met'


def test_facts_missing_data_defaults():
    """filings defaults to None (unavailable -> unknown); an empty list means no filings."""
    bare = Facts(session=S, mode='entry', bars=[], bars_since_version=[], entry_low=None, entry_high=None,
                 regime_label=None, sector_state=None, earnings_date=None)
    assert bare.filings is None and bare.as_of_session is None and bare.rs == {}
    assert evaluate(item('new_filing', form='8-K'), bare)[0] == 'unknown'
    assert evaluate(item('new_filing', form='8-K'), replace(bare, filings=[]))[0] == 'not_met'


def test_evaluate_unknown_kind_is_unknown():
    assert evaluate({'kind': 'moon_phase', 'params': {}}, facts())[0] == 'unknown'


def _valid(cond, use='invalidation'):
    return validate(cond, use=use, last_close=LAST_CLOSE, as_of_session=AS_OF)


def _rejected(cond, use='invalidation'):
    with pytest.raises(Rejected) as err:
        _valid(cond, use)
    assert err.value.reason == 'invalid_condition'


def test_validate_normalizes():
    out = _valid({'kind': 'close_below', 'params': {'price': 95.004}, 'text': '模型自写', 'refs': ['call:1']})
    assert out == {'kind': 'close_below', 'params': {'price': Decimal('95.00')}, 'text': '收盘跌破 95.00',
                   'refs': ['call:1']}
    out = _valid({'kind': 'no_entry_by', 'params': {'date': '2026-10-30'}, 'refs': ['call:2']})
    assert out['params'] == {'date': date(2026, 10, 30)}
    assert out['text'] == '2026-10-30 收盘前未进入入场区'
    out = _valid({'kind': 'rs_below', 'params': {'window': 20, 'value': -0.05}}, use='review')
    assert out == {'kind': 'rs_below', 'params': {'window': 20, 'value': -0.05},
                   'text': '相对 SPY 20 日强弱低于 -0.05'}
    assert isinstance(out['params']['value'], float)
    out = _valid({'kind': 'rs_below', 'params': {'window': 60, 'value': 0}}, use='review')
    assert out['params']['value'] == 0.0 and isinstance(out['params']['value'], float)
    assert _valid({'kind': 'next_session'}, use='review') == {'kind': 'next_session', 'params': {},
                                                              'text': '下一交易日'}
    assert _valid({'kind': 'earnings_within', 'params': {'sessions': 10}})['params'] == {'sessions': 10}
    assert _valid({'kind': 'regime_is', 'params': {'label': 'risk_off'}})['text'] == '区制变为 risk_off'
    assert _valid({'kind': 'new_filing', 'params': {'form': '10-K'}})['text'] == '出现新的 10-K'
    # bounds are inclusive at ±50 % and at as_of + 60 days
    assert _valid(item('close_above', price='150.00'))['params']['price'] == Decimal('150.00')
    assert _valid(item('close_below', price='50'))['params']['price'] == Decimal('50.00')
    assert _valid(item('date_reached', date=(AS_OF + timedelta(days=60)).isoformat()),
                  use='review')['params']['date'] == AS_OF + timedelta(days=60)


@pytest.mark.parametrize('cond,use', [
    (item('close_below', price=151), 'invalidation'),                      # 1.51 × last close
    (item('close_above', price=49.99), 'invalidation'),
    (item('close_below', price=0), 'invalidation'),
    (item('close_below', price=-5), 'invalidation'),
    (item('close_below', price='abc'), 'invalidation'),
    (item('close_below', price=True), 'invalidation'),
    (item('close_below', price=float('nan')), 'invalidation'),
    (item('no_entry_by', date=AS_OF.isoformat()), 'invalidation'),        # = as_of_session
    (item('no_entry_by', date=(AS_OF - timedelta(days=1)).isoformat()), 'invalidation'),
    (item('no_entry_by', date=(AS_OF + timedelta(days=61)).isoformat()), 'invalidation'),
    (item('date_reached', date='2026/10/30'), 'review'),
    (item('date_reached', date=20261030), 'review'),
    (item('earnings_within', sessions=11), 'invalidation'),
    (item('earnings_within', sessions=0), 'invalidation'),
    (item('earnings_within', sessions=2.5), 'invalidation'),
    (item('earnings_within', sessions=True), 'invalidation'),
    (item('regime_is', label='bullish'), 'invalidation'),
    (item('regime_is', label='insufficient'), 'invalidation'),
    (item('new_filing', form='S-1'), 'invalidation'),
    (item('rs_below', window=30, value=0), 'invalidation'),
    (item('rs_below', window=20, value=1.5), 'invalidation'),
    (item('rs_below', window=20, value=float('inf')), 'invalidation'),
    (item('rs_below', window=20, value='low'), 'invalidation'),
    (item('moon_phase'), 'invalidation'),                                   # unknown kind
    (item('close_below', price=95, extra=1), 'invalidation'),               # extra param
    (item('close_below'), 'invalidation'),                                  # missing param
    (item('sector_weak', ticker='XLK'), 'invalidation'),
    (item('rs_below', window=20), 'invalidation'),
    ({'kind': 'close_below', 'params': [95]}, 'invalidation'),
    ({'params': {'price': 95}}, 'invalidation'),
    ('close_below 95', 'invalidation'),
    (item('next_session'), 'invalidation'),                                 # review-only kind
    (item('date_reached', date='2026-10-30'), 'invalidation'),
    (item('no_entry_by', date='2026-10-30'), 'review'),                     # invalidation-only kind
    (item('no_entry_before_earnings'), 'review'),
])
def test_validate_bounds(cond, use):
    _rejected(cond, use)


def test_validate_rejects_unknown_use():
    with pytest.raises(ValueError):
        validate(item('close_below', price=95), use='other', last_close=LAST_CLOSE, as_of_session=AS_OF)


def test_describe():
    assert describe({'kind': 'close_below', 'params': {'price': Decimal('95.00')}}) == '收盘跌破 95.00'
    assert describe({'kind': 'close_above', 'params': {'price': Decimal('104.5')}}) == '收盘突破 104.50'
    assert describe({'kind': 'close_below', 'params': {'price': '95'}}) == '收盘跌破 95.00'
    assert describe({'kind': 'earnings_within', 'params': {'sessions': 3}}) == '距财报不足 3 个交易日'
    assert describe({'kind': 'date_reached', 'params': {'date': date(2026, 11, 2)}}) == '到 2026-11-02 再看'
    assert describe({'kind': 'no_entry_before_earnings', 'params': {}}) == '财报前未进入入场区'
    assert describe({'kind': 'sector_weak'}) == '所属行业转弱'
    assert describe({'kind': 'next_session'}) == '下一交易日'
    assert describe({'kind': 'rs_below', 'params': {'window': 60, 'value': 0.1}}) == '相对 SPY 60 日强弱低于 0.1'


def test_module_is_pure():
    """No database or network imports: the registry is called from read-only paths."""
    source = conditions.__file__
    text = open(source, encoding='utf-8').read()
    for banned in ('psycopg', 'repository', 'requests', 'httpx', 'agent.tools', 'from . import tools'):
        assert banned not in text


# ---------------------------------------------------------------- held_sessions_reached (condition-2)

def _held(sessions=10):
    return item('held_sessions_reached', sessions=sessions)


def _position(held, reached=False, **extra):
    return {'held_sessions': held, 'reached_1r': reached, **extra}


@pytest.mark.parametrize('position,mode,expected', [
    (_position(9), 'holding', 'not_met'),                         # day n-1
    (_position(10), 'holding', 'met'),                            # day n, never closed at +1R
    (_position(14), 'holding', 'met'),
    (_position(12, reached=True), 'holding', 'not_met'),          # reached 1R, then pulled back: stays off
    (_position(12, reached=None), 'holding', 'unknown'),          # R <= 0 or no initial stop
    (_position(9, reached=None), 'holding', 'not_met'),           # too early to matter
    (_position(12, t1_reached=True), 'holding', 'not_met'),       # after T1 the time stop no longer applies (R9)
    (None, 'holding', 'unknown'),
    ({'held_sessions': None, 'reached_1r': None}, 'holding', 'unknown'),
    (_position(12), 'entry', 'not_met'),                          # entry phase is not_met, not unknown
    (None, 'entry', 'not_met'),
])
def test_held_sessions_reached_met_not_met_unknown(position, mode, expected):
    result, detail = evaluate(_held(10), facts(mode=mode, position=position))
    assert result == expected, detail
    assert isinstance(detail, str) and detail


def test_held_sessions_position_mode_overrides_facts_mode():
    assert evaluate(_held(5), facts(mode='entry', position=_position(6, mode='holding')))[0] == 'met'


def test_held_sessions_param_range():
    for ok in (3, 5, 20, 30):
        out = validate(_held(ok), use='invalidation', last_close=LAST_CLOSE, as_of_session=AS_OF)
        assert out['params'] == {'sessions': ok} and out['text'] == f'持有 {ok} 个交易日仍未到 1R'
        assert validate(_held(ok), use='review', last_close=LAST_CLOSE, as_of_session=AS_OF)['kind'] == \
            'held_sessions_reached'
    for bad in (2, 31, 0, 10.5, True, '10', None):
        _rejected(item('held_sessions_reached', sessions=bad), 'invalidation')
        _rejected(item('held_sessions_reached', sessions=bad), 'review')
    _rejected(item('held_sessions_reached'), 'review')
    # earnings_within keeps its own 1-10 range even though the param name is shared
    _rejected(item('earnings_within', sessions=11), 'invalidation')
    assert conditions.HELD_SESSIONS_RANGE == (3, 30)


def test_max_hold_condition_shape():
    cond = conditions.max_hold_condition(12)
    assert cond == {'source': 'max_hold', 'kind': 'held_sessions_reached', 'params': {'sessions': 12}}
    assert describe(cond) == '持有 12 个交易日仍未到 1R'
    assert evaluate(cond, facts(mode='holding', position=_position(12)))[0] == 'met'
