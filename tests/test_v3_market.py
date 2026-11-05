"""v3 market view: the regime snapshot plus the tiers and ETF table the draft needs."""
import pytest
from test_workbench_rules import T, series

from investment_assistant.facts import factors, market


def ret20(bars):
    return bars[-1]['adj_close'] / bars[-21]['adj_close'] - 1


def test_market_view_adds_etf_table():
    spy = series([400.0 + i for i in range(80)])
    rising = series([100.0 * 1.01 ** i for i in range(80)])
    falling = series([100.0 * 0.99 ** i for i in range(80)])
    short = series([50.0 + i * 0.1 for i in range(40)])          # 不足 61 根
    out = market.market_view(session=T,
                             universe_bars={'U1': series([100.0] * 80), 'U2': rising},
                             etf_bars={'ETF1': rising, 'ETF2': falling, 'ETF3': short},
                             spy_bars=spy, macro_rows={})
    assert {'session', 'calc_version', 'label', 'breadth', 'sectors', 'macro', 'inputs'} <= set(out)
    assert out['regime_tier'] == factors.regime_tier(out['label'])
    assert out['spy_ret20'] == pytest.approx(ret20(spy))
    table = out['etf_table']
    assert [r['etf'] for r in table] == ['ETF1', 'ETF2', 'ETF3']   # rs20 降序，不足 61 根的垫底
    assert table[0]['rs20'] == pytest.approx(ret20(rising) - ret20(spy))
    assert table[0]['rs60'] is not None and table[0]['state'] == 'strong'
    assert table[1]['rs20'] == pytest.approx(ret20(falling) - ret20(spy))
    assert table[2]['rs20'] is None and table[2]['rs60'] is None and table[2]['state'] is None
    assert table[2]['ret20'] == pytest.approx(ret20(short))
    assert out['sectors']['ETF1']['rs20'] == table[0]['rs20']       # 与 regime.snapshot 一致
