"""screen-1 as pure functions: filters, groups, ranking and member rotation."""
from datetime import date, timedelta

import pytest

from investment_assistant.data.calendar import add_trading_days
from investment_assistant.analysis import rules
from investment_assistant.analysis import screen_rules as screen

SESSION = date(2026, 10, 21)  # 周三


def make_bars(closes, *, volume=1_000_000, atr_pct=0.04, end=SESSION):
    """Flat-adjusted bars; `pad` is chosen so ATR14 / last close == atr_pct for flat closes."""
    pad = float(str(atr_pct)) * closes[-1] / 2
    n = len(closes)
    return [{'id': f'b{i}', 'price_date': end - timedelta(days=n - 1 - i),
             'open': c, 'high': c + pad, 'low': c - pad, 'close': c, 'adj_close': c,
             'volume': volume, 'split_factor': 1.0}
            for i, c in enumerate(closes)]


def rising(n=300, start=100.0, step=0.1):
    return [start + step * i for i in range(n)]


def breakout_bars(jump, *, base=100.0, volume=1_000_000):
    """A close that clears the 20-day high on 3x average volume: volume_breakout on the last bar."""
    bars = make_bars([base] * 299 + [jump], volume=volume)
    bars[-1] = {**bars[-1], 'volume': volume * 3}
    return bars


def flat_bars(n=300):
    return make_bars([100.0] * n)


def evaluate(bars_by_ticker, *, earnings_by_ticker=None, benchmark=None):
    return screen.evaluate(session=SESSION, bars_by_ticker=bars_by_ticker,
                           benchmark_bars=benchmark if benchmark is not None else flat_bars(),
                           earnings_by_ticker=earnings_by_ticker
                           if earnings_by_ticker is not None else {t: None for t in bars_by_ticker})


def rows_by_ticker(out):
    return {r['ticker']: r for r in out['results']}


def test_data_skip_is_not_a_filter():
    out = evaluate({'X': make_bars([100.0] * 150)})
    assert out['skips'] == {'X': 'insufficient_history'}
    assert out['results'] == []
    assert out['counts']['data_skipped'] == 1
    assert out['counts']['universe'] == 1


def test_illiquid():
    out = evaluate({'A': make_bars([100.0] * 300, volume=499_999.99),
                    'B': make_bars([100.0] * 300, volume=500_000)})
    rows = rows_by_ticker(out)
    assert rows['A']['filters']['liquidity'] == 'illiquid' and rows['A']['group'] == 'none'
    assert rows['B']['filters']['liquidity'] == 'ok'
    assert out['counts']['illiquid'] == 1


def test_earnings_window():
    bars = make_bars(rising())
    out = evaluate({'E0': bars, 'E5': bars, 'E6': bars, 'UN': bars},
                   earnings_by_ticker={
                       'E0': {'earnings_date': SESSION, 'status': 'known'},
                       'E5': {'earnings_date': add_trading_days(SESSION, 5), 'status': 'known'},
                       'E6': {'earnings_date': add_trading_days(SESSION, 6), 'status': 'known'},
                       'UN': {'earnings_date': None, 'status': 'unknown'}})
    rows = rows_by_ticker(out)
    assert rows['E0']['filters']['earnings'] == 'earnings_soon'
    assert rows['E5']['filters']['earnings'] == 'earnings_soon'
    assert rows['E6']['filters']['earnings'] == 'ok'
    assert rows['UN']['filters']['earnings'] == 'earnings_unknown' and rows['UN']['group'] != 'none'
    assert out['counts']['earnings_soon'] == 2


def test_trigger_group_bypasses_setup():
    bars = make_bars([100.0] * 299 + [110.0], atr_pct=0.08)
    bars[-1] = {**bars[-1], 'volume': 3_000_000}
    out = evaluate({'T': bars})
    row = out['results'][0]
    assert row['group'] == 'trigger' and row['trigger_rules'] == ['volume_breakout']
    assert row['selected'] is True
    assert out['counts']['trigger'] == 1 and out['counts']['selected'] == 1


def test_trigger_with_sell_candidate_is_not_trigger(monkeypatch):
    def fake_evaluate(ticker, signal_date, bars, filings, position, plan_state=None):
        return {'candidates': [rules.Candidate('volume_breakout', 'buy', {}),
                               rules.Candidate('trend_break', 'sell', {})],
                'skips': []}
    monkeypatch.setattr(rules, 'evaluate', fake_evaluate)
    out = evaluate({'S': make_bars(rising())})
    row = rows_by_ticker(out)['S']
    assert row['group'] == 'setup' and row['trigger_rules'] == []
    assert out['counts']['trigger'] == 0


def test_setup_band_edges():
    out = evaluate({'IN2': make_bars(rising(), atr_pct=0.02),
                    'IN6': make_bars(rising(), atr_pct=0.06),
                    'OUT_LO': make_bars(rising(), atr_pct=0.0199),
                    'OUT_HI': make_bars(rising(), atr_pct=0.0601)})
    rows = rows_by_ticker(out)
    assert rows['IN2']['group'] == 'setup' and rows['IN6']['group'] == 'setup'
    assert rows['OUT_LO']['group'] == 'none' and rows['OUT_LO']['filters']['setup'] == 'no_setup'
    assert rows['OUT_HI']['group'] == 'none' and rows['OUT_HI']['filters']['setup'] == 'no_setup'
    # 收盘站上 MA50 但 MA50 仍在 MA200 之下：下跌后的短促反弹，不算形态
    v_shape = [100.0 - 0.1 * i for i in range(280)] + [72.0 + 0.5 * j for j in range(20)]
    out = evaluate({'V': make_bars(v_shape)})
    row = rows_by_ticker(out)['V']
    assert row['filters']['setup'] == 'no_setup' and row['group'] == 'none'


def test_rank_triggers_first_then_rs_and_cap_20():
    tickers = {f'T{i}': breakout_bars(110.0 + 5 * i) for i in range(5)}
    tickers.update({f'U{i}': make_bars(rising(step=0.05 + 0.05 * i)) for i in range(30)})
    out = evaluate(tickers)
    ranked = [r for r in out['results'] if r['rank'] is not None]
    assert sorted(r['rank'] for r in ranked) == list(range(1, 36))
    selected = sorted((r for r in out['results'] if r['selected']), key=lambda r: r['rank'])
    assert len(selected) == 20
    assert all(r['group'] == 'trigger' for r in selected[:5])
    assert all(r['group'] == 'setup' for r in selected[5:])
    assert [r['rank'] for r in selected] == list(range(1, 21))
    assert selected[0]['ticker'] == 'T4'  # 触发组里 RS 最高
    cut = [r for r in out['results'] if r['rank'] is not None and r['rank'] > 20]
    assert len(cut) == 15 and all(r['filters']['rank'] == 'rank_cut' for r in cut)
    assert out['counts']['selected'] == 20


def test_rs_formula():
    closes_q = [100.0 + 0.02 * i for i in range(300)]
    bars_a, bars_q = make_bars(rising()), make_bars(closes_q)
    out = evaluate({'A': bars_a}, benchmark=bars_q)
    row = rows_by_ticker(out)['A']

    def r(bars, n):
        return bars[-1]['adj_close'] / bars[-1 - n]['adj_close'] - 1

    expected = 0.5 * (r(bars_a, 20) - r(bars_q, 20)) + 0.5 * (r(bars_a, 60) - r(bars_q, 60))
    assert row['rs'] == pytest.approx(expected)


def test_benchmark_missing_raises():
    bench = flat_bars()
    bench[-1] = {**bench[-1], 'price_date': SESSION - timedelta(days=1)}
    with pytest.raises(screen.BenchmarkMissing):
        evaluate({'A': make_bars(rising())}, benchmark=bench)


def test_members_priority_and_eviction():
    out = screen.members(selected_today=['A', 'B'],
                         recent={'C': {'session': SESSION - timedelta(days=3), 'rank': 1},
                                 'D': {'session': SESSION - timedelta(days=1), 'rank': 2}},
                         locked={'L'}, manual=set(), cap=3)
    assert out['members'] == ('L', 'A', 'B')
    assert out['evicted'] == ['D', 'C']
    assert out['flags'] == []


def test_members_excludes_manual():
    out = screen.members(selected_today=['A', 'B'], recent={}, locked=set(), manual={'A'}, cap=10)
    assert out['members'] == ('B',)
    assert out['evicted'] == []
    assert out['flags'] == []


def test_members_cap_zero_keeps_only_locked():
    out = screen.members(selected_today=['A', 'B'], recent={}, locked={'L'}, manual=set(), cap=0)
    assert out['members'] == ('L',)
    assert out['flags'] == ['screen_locked_over_cap']
    assert out['evicted'] == ['A', 'B']
