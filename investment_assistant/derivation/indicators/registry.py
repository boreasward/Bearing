"""D17's existing indicator conventions, evaluated over already-visible bars.

``window`` counts input rows, including the preceding close for ATR/returns:
ATR14 needs 15, a 20-session return needs 21. Breadth has no single minimum;
its original function applies 50/200/252 eligibility independently per ticker.
Benchmark callers supply SPY for rs20/rs60/sector_strength and QQQ for screen_rs.
This layer does not fetch data or change either benchmark convention.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import partial
from typing import Any, Callable

from investment_assistant.core.versions import INDICATOR_VERSION
from investment_assistant.derivation.indicators import _price_math, screen_metrics
from investment_assistant.derivation.indicators.breadth import (
    _up_to_session, breadth, sector_strength,
)


@dataclass(frozen=True)
class Indicator:
    id: str
    title: str
    basis: str
    window: int | None
    inputs: tuple[str, ...]
    fn: Callable[..., Any]
    version: str
    concept: str
    used_by: tuple[str, ...]


def _single(bars, *, session, benchmark_bars=None, fn):
    return fn(bars)


def _relative_strength(bars, *, session, benchmark_bars=None, days):
    return _price_math._relative_strength(bars, benchmark_bars or [], days)


def _screen_rs(bars, *, session, benchmark_bars=None):
    if benchmark_bars is None or len(benchmark_bars) < 61:
        return None
    return screen_metrics.screen_rs(bars, screen_metrics._ret(benchmark_bars, 20),
                                    screen_metrics._ret(benchmark_bars, 60))


def _breadth(bars, *, session, benchmark_bars=None):
    return breadth(bars, session)


def _sector_strength(bars, *, session, benchmark_bars=None):
    return sector_strength(bars, benchmark_bars or [], session)


INDICATORS: tuple[Indicator, ...] = (
    Indicator('atr14', 'ATR14', 'high_low', 15, ('bars',),
              partial(_single, fn=_price_math.atr14), INDICATOR_VERSION, 'indicator.atr14',
              ('plan-2', 'screen-1', 'agent-tools')),
    *(Indicator(f'ma{n}_close', f'MA{n}（close）', 'close', n, ('bars',),
                partial(_single, fn=partial(_price_math._ma_close, n=n)), INDICATOR_VERSION,
                'indicator.moving_average', ('agent-tools',)) for n in (20, 50, 200)),
    *(Indicator(f'ma{n}_adj_close', f'MA{n}（adj_close）', 'adj_close', n, ('bars',),
                partial(_single, fn=partial(screen_metrics._ma, n=n)), INDICATOR_VERSION,
                'indicator.moving_average', ('rules-v1',) if n == 20 else ('rules-v1', 'screen-1'))
      for n in (20, 50, 200)),
    *(Indicator(f'{kind}_{n}', f'{n} 日{"最高" if kind == "high" else "最低"}价',
                'high_low', n, ('bars',), partial(_single, fn=partial(fn, n=n)),
                INDICATOR_VERSION, 'indicator.high_low', ('agent-tools',))
      for n in (20, 252) for kind, fn in (('high', _price_math._high), ('low', _price_math._low))),
    Indicator('avg_volume_20', '20 日平均成交量（含当日）', 'volume', 20, ('bars',),
              partial(_single, fn=_price_math._avg_volume_20), INDICATOR_VERSION,
              'indicator.volume_ratio', ('agent-tools',)),
    Indicator('volume_ratio', '量比（含当日的 20 日均量）', 'volume', 20, ('bars',),
              partial(_single, fn=_price_math._volume_ratio), INDICATOR_VERSION,
              'indicator.volume_ratio', ('agent-tools',)),
    *(Indicator(f'rs{n}', f'{n} 日相对强弱（vs SPY）', 'adj_close', n + 1, ('bars',),
                partial(_relative_strength, days=n), INDICATOR_VERSION,
                'indicator.relative_strength', ('agent-tools',)) for n in (20, 60)),
    Indicator('screen_rs', '筛选相对强弱（vs QQQ）', 'adj_close', 61, ('bars',),
              _screen_rs, INDICATOR_VERSION, 'indicator.screen_rs', ('screen-1',)),
    *(Indicator(f'ret{n}', f'{n} 日收益率（adj_close）', 'adj_close', n + 1, ('bars',),
                partial(_single, fn=partial(screen_metrics._ret, n=n)), INDICATOR_VERSION,
                'indicator.screen_rs', ('screen-1',)) for n in (20, 60)),
    Indicator('dollar_volume_20', '20 日平均成交额（close × volume）', 'volume', 20, ('bars',),
              partial(_single, fn=screen_metrics._dollar_volume_20), INDICATOR_VERSION,
              'indicator.dollar_volume_20', ('screen-1',)),
    Indicator('atr_pct', 'ATR14 / close', 'high_low', 15, ('bars',),
              partial(_single, fn=screen_metrics.atr_pct), INDICATOR_VERSION,
              'indicator.atr14', ('screen-1',)),
    Indicator('breadth', '观察池广度（adj_close）', 'breadth', None, ('bars',),
              _breadth, INDICATOR_VERSION, 'indicator.breadth', ('regime-2',)),
    Indicator('sector_strength', '行业 ETF 相对强弱（vs SPY）', 'adj_close', 61, ('bars',),
              _sector_strength, INDICATOR_VERSION, 'indicator.sector_strength', ('regime-2',)),
)


def get(id: str) -> Indicator:
    for indicator in INDICATORS:
        if indicator.id == id:
            return indicator
    raise LookupError(f'未知指标：{id}')


def for_used_by(tag: str) -> list[Indicator]:
    return [indicator for indicator in INDICATORS if tag in indicator.used_by]


def _evaluate(indicator: Indicator, bars, *, session, benchmark_bars=None):
    if indicator.window is not None:
        if len(bars) < indicator.window:
            return None
        bars = bars[-indicator.window:]
        if benchmark_bars is not None:
            benchmark_bars = benchmark_bars[-indicator.window:]
    return indicator.fn(bars, session=session, benchmark_bars=benchmark_bars)


def compute(id: str, bars: list[dict] | dict[str, list[dict]], *, session: date,
            benchmark_bars: list[dict] | None = None):
    """Sort and cut off both inputs, then apply the registered sample window.

    Scalar indicators return None for insufficient history; breadth preserves
    its counts/None-percentages shape even for an empty pool. Inputs are not mutated.
    """
    indicator = get(id)
    history = ({ticker: _up_to_session(rows, session) for ticker, rows in bars.items()}
               if isinstance(bars, dict) else _up_to_session(bars, session))
    benchmark = _up_to_session(benchmark_bars, session) if benchmark_bars is not None else None
    return _evaluate(indicator, history, session=session, benchmark_bars=benchmark)


def agent_tool_indicators(bars: list[dict], spy_bars: list[dict]) -> dict:
    """The legacy ordered, float-valued tool dict, from already-cut-off ordered rows.

    last_close is a raw bar field rather than a D17 indicator. Close MA IDs are
    translated back to the original public keys; the adjusted-close set is separate.
    """
    out = {'last_close': float(bars[-1]['close']) if bars else None}
    for indicator in for_used_by('agent-tools'):
        value = _evaluate(indicator, bars, session=None, benchmark_bars=spy_bars)
        key = indicator.id.removesuffix('_close') if indicator.basis == 'close' else indicator.id
        out[key] = float(value) if value is not None else None
    return out
