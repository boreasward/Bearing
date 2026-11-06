"""The price indicator set (``price.indicators``): short-history nulls and values. No network."""
from datetime import date

import pytest

from investment_assistant.data.calendar import add_trading_days
from investment_assistant.derivation.indicators.price import indicators

S = date(2026, 10, 21)


def _bars(closes, volume=1000):
    days = [add_trading_days(S, -(len(closes) - 1 - i)) for i in range(len(closes))]
    return [{'price_date': d, 'open': c, 'high': c * 1.02, 'low': c * 0.98, 'close': c, 'adj_close': c,
             'volume': volume, 'split_factor': 1.0} for d, c in zip(days, closes)]


def test_indicators_short_history_nulls():
    out = indicators(_bars([100 + i for i in range(120)]), _bars([500] * 120))
    assert out['ma200'] is None and out['high_252'] is None and out['low_252'] is None
    assert out['ma50'] is not None
    assert out['rs20'] is not None  # 21 根即可
    assert out['rs60'] is not None  # 61 根即够，120 根充足


def test_indicators_values():
    ticker = _bars([100 + i for i in range(300)], volume=1000)
    ticker[-1]['volume'] = 3000
    spy = _bars([500.0] * 300)
    out = indicators(ticker, spy)
    assert out['last_close'] == 399
    assert out['ma20'] == pytest.approx(sum(range(380, 400)) / 20)
    assert out['volume_ratio'] == pytest.approx(3000 / 1100)  # 均量含当日：19×1000+3000 / 20
    assert out['rs20'] == pytest.approx(399 / 379 - 1)  # 21 根窗口，SPY 20 日收益为 0


# ---------------------------------------------------------------- get_concept (v2.6 spec §10)


import yaml   # noqa: E402

from investment_assistant.core import knowledge   # noqa: E402


# ---------------------------------------------------------------- get_knowledge (v2.7 spec §6.1)


# Repository consumers use their owning layers below.


# --- SQL: the cutoff and the drafts (spec §16.1) ---
