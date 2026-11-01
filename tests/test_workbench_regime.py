"""Breadth, regime labels, sector strength and macro views (pure functions)."""
from datetime import date, timedelta

from investment_assistant.analysis import regime
from investment_assistant.derivation.indicators import breadth

SESSION = date(2026, 10, 21)


def _bars(n, *, closes=None, end=SESSION):
    closes = closes if closes is not None else list(range(1, n + 1))
    return [{'price_date': end - timedelta(days=n - 1 - i), 'adj_close': c} for i, c in enumerate(closes)]


def test_constants():
    assert regime.REGIME_VERSION == 'regime-2'
    assert regime.MIN_ELIGIBLE_200 == 20
    assert breadth.RS_WINDOWS == (20, 60)
    assert regime.LABEL_TEXT == {'risk_on': '进攻', 'neutral': '中性', 'risk_off': '防守', 'insufficient': '样本不足'}


def test_breadth_eligibility_by_history():
    rising_120 = _bars(120)
    flat_drop = _bars(300, closes=[100] * 299 + [50])
    b = breadth.breadth({'A': rising_120, 'B': flat_drop}, SESSION)
    assert b['tickers'] == 2
    assert b['eligible_50'] == 2
    assert b['above_50'] == 1
    assert b['pct_50'] == 0.5
    assert b['eligible_200'] == 1
    assert b['above_200'] == 0
    assert b['pct_200'] == 0.0
    assert b['eligible_52w'] == 1
    assert b['new_highs'] == 0
    assert b['new_lows'] == 1


def test_breadth_excludes_ticker_without_session_bar():
    stale = _bars(60, end=SESSION - timedelta(days=1))
    fresh = _bars(60)
    b = breadth.breadth({'STALE': stale, 'FRESH': fresh}, SESSION)
    assert b['tickers'] == 1
    assert b['eligible_50'] == 1
    assert b['eligible_200'] == 0
    assert b['pct_200'] is None


def test_new_high_and_low():
    high = _bars(300, closes=list(range(1, 301)))
    low = _bars(300, closes=list(range(300, 0, -1)))
    b = breadth.breadth({'H': high, 'L': low}, SESSION)
    assert b['new_highs'] == 1
    assert b['new_lows'] == 1
    assert b['eligible_52w'] == 2


def test_all_ineligible_is_insufficient():
    b = breadth.breadth({'A': _bars(60), 'B': _bars(30)}, SESSION)
    assert b['eligible_200'] == 0
    assert regime.label(b) == 'insufficient'


def test_label_boundaries():
    def b(pct_200, pct_50, eligible_200=25):
        return {'eligible_200': eligible_200, 'pct_200': pct_200, 'pct_50': pct_50}
    assert regime.label(b(0.60, 0.50)) == 'risk_on'
    assert regime.label(b(0.5999, 0.50)) == 'neutral'
    assert regime.label(b(0.39, 0.29)) == 'risk_off'
    assert regime.label(b(0.40, 0.29)) == 'neutral'
    assert regime.label(b(0.70, 0.45)) == 'neutral'
    assert regime.label(b(0.60, 0.50, eligible_200=19)) == 'insufficient'


def test_sector_states():
    spy_flat = _bars(70, closes=[100] * 70)
    down = _bars(70, closes=list(range(140, 70, -1)))          # falls over both windows
    up = _bars(70, closes=list(range(70, 140)))                 # rises over both windows
    mixed = _bars(70, closes=[90] * 40 + [100] * 30)            # flat 20, up over 60 -> rs20=0, rs60>0 => mixed
    assert breadth.sector_strength(down, spy_flat, SESSION)['state'] == 'weak'
    assert breadth.sector_strength(up, spy_flat, SESSION)['state'] == 'strong'
    assert breadth.sector_strength(mixed, spy_flat, SESSION)['state'] == 'mixed'
    assert breadth.sector_strength(down, spy_flat, SESSION)['rs20'] < 0
    assert breadth.sector_strength(up, spy_flat, SESSION)['rs60'] > 0


def test_sector_short_history_none():
    spy = _bars(70)
    assert breadth.sector_strength(_bars(60), spy, SESSION) is None
    assert breadth.sector_strength(_bars(70), _bars(50), SESSION) is None


def test_macro_change_against_20_obs_back():
    rows = [{'obs_date': SESSION - timedelta(days=30 - i), 'value': float(i)} for i in range(31)]
    view = breadth.macro_view(rows)
    assert view['value'] == 30.0
    assert view['obs_date'] == SESSION
    assert view['change'] == 20.0
    assert view['change_from'] == SESSION - timedelta(days=20)


def test_macro_short_series_no_change():
    rows = [{'obs_date': SESSION - timedelta(days=20 - i), 'value': float(i)} for i in range(20)]
    view = breadth.macro_view(rows)
    assert view['change'] is None
    assert view['change_from'] is None
    assert breadth.macro_view([]) is None


def test_snapshot_shape():
    spy = _bars(70, closes=[100] * 70)
    bars = {f'T{i:02d}': _bars(260) for i in range(20)}
    snap = regime.snapshot(session=SESSION, pool_bars=bars,
                           etf_bars={'SMH': _bars(70), 'XLK': _bars(70), 'QQQ': _bars(60)},
                           spy_bars=spy,
                           macro_rows={'BAMLH0A0HYM2': [], 'T10Y2Y': []})
    assert snap['calc_version'] == 'regime-2'
    assert snap['session'] == SESSION
    assert snap['label'] == 'risk_on'
    assert set(snap['sectors']) == {'SMH', 'XLK', 'QQQ'}
    assert snap['sectors']['QQQ'] is None
    assert snap['macro'] == {'BAMLH0A0HYM2': None, 'T10Y2Y': None}
    assert snap['inputs'] == {'tickers': sorted(bars), 'etfs': ['SMH', 'XLK', 'QQQ'],
                              'series': ['BAMLH0A0HYM2', 'T10Y2Y'], 'macro_status': {}}


def test_snapshot_records_macro_status():
    status = {'BAMLH0A0HYM2': 'not_configured', 'T10Y2Y': 'error'}
    snap = regime.snapshot(session=SESSION, pool_bars={}, etf_bars={}, spy_bars=[],
                           macro_rows={'BAMLH0A0HYM2': [], 'T10Y2Y': []}, macro_status=status)
    assert snap['inputs']['macro_status'] == status
