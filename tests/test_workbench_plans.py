"""Plan math the v3 tool surface uses: ATR, levels, validity, earnings flags, sizing, level validation."""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from investment_assistant.config import WorkbenchConfig
from investment_assistant.execution import plan_levels as plans
from investment_assistant.derivation.indicators.price import atr14

PARAMS = WorkbenchConfig()


def _bars(n=15, *, start=date(2026, 10, 1), close=100):
    """Synthetic bars with constant TR=2, ids 1..n."""
    from investment_assistant.data.calendar import add_trading_days
    out = []
    for i in range(n):
        d = add_trading_days(start, i) if i else start
        hi, lo, c = close + 2, close, close
        out.append({'id': i + 1, 'price_date': d, 'open': close, 'high': hi, 'low': lo, 'close': c,
                    'adj_close': c, 'volume': 1000, 'split_factor': 1.0})
    return out


def _snapshot(equity='100000'):
    return {'id': uuid.uuid4(), 'as_of': date(2026, 10, 20), 'equity': Decimal(equity)}


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


def test_validity_sessions_parameter():
    # v3.1 调用方传配置（cfg.v3.valid_sessions）；缺省仍是 T+3（VALID_SESSIONS = 3）
    valid_from, valid_until = plans.validity(date(2026, 11, 25), sessions=5)
    assert (valid_from, valid_until) == (date(2026, 11, 27), date(2026, 12, 3))
    assert plans.VALID_SESSIONS == 3


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
