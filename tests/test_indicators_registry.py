"""D17/D18: discoverable conventions, point-in-time windows and frozen tool numbers."""
import json
from copy import deepcopy
from dataclasses import fields, replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from investment_assistant.core.versions import INDICATOR_VERSION
from investment_assistant.derivation.indicators import price, registry
from test_workbench_agent_tools import _bars as agent_bars

SESSION = date(2026, 10, 21)
ROOT = Path(__file__).resolve().parents[1]
IDS = (
    'atr14', 'ma20_close', 'ma50_close', 'ma200_close',
    'ma20_adj_close', 'ma50_adj_close', 'ma200_adj_close',
    'high_20', 'low_20', 'high_252', 'low_252', 'avg_volume_20', 'volume_ratio',
    'rs20', 'rs60', 'screen_rs', 'ret20', 'ret60', 'dollar_volume_20', 'atr_pct',
    'breadth', 'sector_strength',
)
TOOL_KEYS = ('last_close', 'atr14', 'ma20', 'ma50', 'ma200', 'high_20', 'low_20',
             'high_252', 'low_252', 'avg_volume_20', 'volume_ratio', 'rs20', 'rs60')
SCALAR_WINDOWS = (
    ('atr14', 15, Decimal('4')), ('ma20_close', 20, 100.0),
    ('ma50_close', 50, 100.0), ('ma200_close', 200, 100.0),
    ('ma20_adj_close', 20, Decimal('50')), ('ma50_adj_close', 50, Decimal('50')),
    ('ma200_adj_close', 200, Decimal('50')),
    ('high_20', 20, 102.0), ('low_20', 20, 98.0),
    ('high_252', 252, 102.0), ('low_252', 252, 98.0),
    ('avg_volume_20', 20, 1000.0), ('volume_ratio', 20, 1.0),
    ('rs20', 21, 0.0), ('rs60', 61, 0.0), ('screen_rs', 61, Decimal('0.0')),
    ('ret20', 21, Decimal('0')), ('ret60', 61, Decimal('0')),
    ('dollar_volume_20', 20, Decimal('100000')), ('atr_pct', 15, Decimal('0.04')),
)


def bars(n, *, end=SESSION):
    return [{'price_date': end - timedelta(days=n - 1 - i),
             'close': 100, 'adj_close': 50, 'high': 102, 'low': 98, 'volume': 1000}
            for i in range(n)]


def test_first_batch_ids():
    assert tuple(item.id for item in registry.INDICATORS) == IDS
    assert len(set(IDS)) == len(registry.INDICATORS)
    assert tuple(field.name for field in fields(registry.Indicator)) == (
        'id', 'title', 'basis', 'window', 'inputs', 'fn', 'version', 'concept', 'used_by')
    for item in registry.INDICATORS:
        assert registry.get(item.id) is item
        assert item.version == INDICATOR_VERSION
        assert item.inputs == ('bars',)
        assert (ROOT / 'docs' / 'knowledge' / 'concepts' / f'{item.concept}.md').is_file()
    assert [item.id for item in registry.for_used_by('agent-tools')] == [
        'atr14', 'ma20_close', 'ma50_close', 'ma200_close', 'high_20', 'low_20',
        'high_252', 'low_252', 'avg_volume_20', 'volume_ratio', 'rs20', 'rs60']
    assert [item.id for item in registry.for_used_by('regime-2')] == ['breadth', 'sector_strength']
    assert registry.for_used_by('no-such-consumer') == []
    with pytest.raises(LookupError):
        registry.get('ma20')
    with pytest.raises(LookupError):
        registry.compute('unknown', [], session=SESSION)


@pytest.mark.parametrize('length,key', [(300, 'full_300'), (120, 'short_120_nulls')])
def test_agent_tool_indicators_equals_snapshot(length, key):
    stored = json.loads((ROOT / 'tests/fixtures/numbers_snapshot.json').read_text())
    ticker = agent_bars([100 + i for i in range(length)])
    if length == 300:
        ticker[-1]['volume'] = 3000
    spy = agent_bars([500.0] * length)
    expected = stored[f'agent.tools.indicators.{key}']
    actual = registry.agent_tool_indicators(ticker, spy)
    assert actual == expected
    assert price.indicators(ticker, spy) == expected
    assert tuple(actual) == TOOL_KEYS


def test_two_ma_bases_registered_separately():
    for n in (20, 50, 200):
        assert registry.get(f'ma{n}_close').basis == 'close'
        assert registry.get(f'ma{n}_adj_close').basis == 'adj_close'
        assert registry.compute(f'ma{n}_close', bars(300), session=SESSION) == 100.0
        assert registry.compute(f'ma{n}_adj_close', bars(300), session=SESSION) == Decimal('50')


@pytest.mark.parametrize('id,window,expected', SCALAR_WINDOWS)
def test_compute_respects_window(id, window, expected):
    assert registry.get(id).window == window
    assert registry.compute(id, bars(window - 1), session=SESSION,
                            benchmark_bars=bars(300)) is None
    assert registry.compute(id, bars(window), session=SESSION,
                            benchmark_bars=bars(300)) == expected
    old = bars(1, end=SESSION - timedelta(days=window))
    old[0].update(close=9999, adj_close=9999, high=99999, low=-99999, volume=999999)
    future = bars(1, end=SESSION + timedelta(days=1))
    future[0].update(close=7777, adj_close=7777, high=77777, low=-77777, volume=777777)
    history = list(reversed(old + bars(window) + future))
    benchmark = list(reversed(bars(window) + future))
    before = deepcopy((history, benchmark))
    assert registry.compute(id, history, session=SESSION, benchmark_bars=benchmark) == expected
    assert (history, benchmark) == before


@pytest.mark.parametrize('id,window', [('rs20', 21), ('rs60', 61), ('screen_rs', 61),
                                    ('sector_strength', 61)])
def test_benchmark_history_must_be_available_by_session(id, window):
    assert registry.compute(id, bars(300), session=SESSION) is None
    assert registry.compute(id, bars(300), session=SESSION, benchmark_bars=[]) is None
    short_with_future = bars(window - 1) + bars(1, end=SESSION + timedelta(days=1))
    assert registry.compute(id, bars(300), session=SESSION, benchmark_bars=short_with_future) is None


def test_spy_and_qqq_relative_strength_keep_separate_conventions():
    ticker, spy, qqq = bars(61), bars(61), bars(61)
    ticker[-1]['adj_close'] = 60
    spy[-1]['adj_close'] = 55
    qqq[-1]['adj_close'] = Decimal('52.5')
    assert registry.compute('rs20', ticker, session=SESSION, benchmark_bars=spy) == 0.09999999999999987
    assert registry.compute('rs60', ticker, session=SESSION, benchmark_bars=spy) == 0.09999999999999987
    assert registry.compute('screen_rs', ticker, session=SESSION, benchmark_bars=qqq) == Decimal('0.150')
    assert registry.compute('ret20', ticker, session=SESSION) == Decimal('0.2')
    assert registry.compute('ret60', ticker, session=SESSION) == Decimal('0.2')
    assert 'SPY' in registry.get('rs20').title
    assert 'QQQ' in registry.get('screen_rs').title


def test_empty_and_zero_volume_keep_missing_shapes():
    assert registry.agent_tool_indicators([], []) == dict.fromkeys(TOOL_KEYS)
    for id in IDS:
        if id != 'breadth':
            assert registry.compute(id, [], session=SESSION) is None
    zero = bars(20)
    for row in zero:
        row['volume'] = 0
    assert registry.compute('avg_volume_20', zero, session=SESSION) == 0
    assert registry.compute('volume_ratio', zero, session=SESSION) is None
    assert registry.compute('dollar_volume_20', zero, session=SESSION) == Decimal('0')
    assert registry.agent_tool_indicators(zero, [])['volume_ratio'] is None


def test_breadth_keeps_per_metric_eligibility_and_excludes_stale_and_future():
    assert registry.get('breadth').window is None
    pool = {'SHORT': bars(120), 'LONG': bars(300),
            'STALE': bars(300, end=SESSION - timedelta(days=1)),
            'FUTURE': bars(1, end=SESSION + timedelta(days=1))}
    pool['SHORT'][-1]['adj_close'] = 60
    pool['LONG'][-1]['adj_close'] = 40
    pool['SHORT'] += bars(1, end=SESSION + timedelta(days=1))
    before = deepcopy(pool)
    assert registry.compute('breadth', pool, session=SESSION) == {
        'tickers': 2, 'eligible_50': 2, 'above_50': 1, 'eligible_200': 1, 'above_200': 0,
        'eligible_52w': 1, 'new_highs': 0, 'new_lows': 1, 'pct_50': 0.5, 'pct_200': 0.0}
    assert pool == before
    assert registry.compute('breadth', {}, session=SESSION) == {
        'tickers': 0, 'eligible_50': 0, 'above_50': 0, 'eligible_200': 0, 'above_200': 0,
        'eligible_52w': 0, 'new_highs': 0, 'new_lows': 0, 'pct_50': None, 'pct_200': None}


def test_sector_strength_keeps_shape_and_both_windows():
    ticker, spy = bars(61), bars(61)
    ticker[-1]['adj_close'] = 60
    spy[-1]['adj_close'] = 55
    future = bars(1, end=SESSION + timedelta(days=1))
    future[0]['adj_close'] = 9000
    assert registry.get('sector_strength').window == 61
    assert registry.compute('sector_strength', ticker[:60], session=SESSION, benchmark_bars=spy) is None
    assert registry.compute('sector_strength', list(reversed(ticker + future)), session=SESSION,
                            benchmark_bars=list(reversed(spy + future))) == {
        'rs20': 0.09999999999999987, 'rs60': 0.09999999999999987, 'state': 'strong'}


def test_price_uses_registered_functions_with_legacy_keys(monkeypatch):
    """Replacing a registered calculation must reach the public legacy facade."""
    item = registry.get('ma20_close')
    replacement = replace(item, fn=lambda bars, *, session, benchmark_bars=None: 1234.5)
    monkeypatch.setattr(registry, 'INDICATORS', tuple(
        replacement if row.id == item.id else row for row in registry.INDICATORS))
    result = price.indicators(bars(20), [])
    assert result['ma20'] == 1234.5
    assert 'ma20_close' not in result
