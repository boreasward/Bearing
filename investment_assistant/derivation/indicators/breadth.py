"""Pool breadth, regime label, sector strength and macro views (pure functions).

Breadth uses adj_close over tickers with a bar on the session; the sample is the
screening universe ∪ the manual watch pool; the label is advisory only
(spec §5.2) and never changes levels or sizing.
"""
from __future__ import annotations

from datetime import date

from investment_assistant.data.adapters.fred import MACRO_CHANGE_OBS

MA_50_SAMPLES = 50
MA_200_SAMPLES = 200
NEW_EXTREME_WINDOW = 252
RS_WINDOWS = (20, 60)


def _up_to_session(bars: list[dict], session: date) -> list[dict]:
    return sorted((b for b in bars if b['price_date'] <= session), key=lambda b: b['price_date'])


def breadth(bars_by_ticker: dict[str, list[dict]], session: date) -> dict:
    """Breadth over pool tickers that have a bar on the session itself.

    A ticker with too little history is only dropped from the metric it is
    not eligible for; pct_* is None when the eligible sample is empty.
    """
    counts = {k: 0 for k in ('tickers', 'eligible_50', 'above_50', 'eligible_200', 'above_200',
                             'eligible_52w', 'new_highs', 'new_lows')}
    for bars in bars_by_ticker.values():
        history = _up_to_session(bars, session)
        if not history or history[-1]['price_date'] != session:
            continue
        counts['tickers'] += 1
        closes = [b['adj_close'] for b in history]
        last = closes[-1]
        if len(closes) >= MA_50_SAMPLES:
            counts['eligible_50'] += 1
            if last > sum(closes[-MA_50_SAMPLES:]) / MA_50_SAMPLES:
                counts['above_50'] += 1
        if len(closes) >= MA_200_SAMPLES:
            counts['eligible_200'] += 1
            if last > sum(closes[-MA_200_SAMPLES:]) / MA_200_SAMPLES:
                counts['above_200'] += 1
        if len(closes) >= NEW_EXTREME_WINDOW:
            counts['eligible_52w'] += 1
            previous = closes[-NEW_EXTREME_WINDOW:-1]
            if last >= max(previous):
                counts['new_highs'] += 1
            if last <= min(previous):
                counts['new_lows'] += 1
    return {**counts,
            'pct_50': counts['above_50'] / counts['eligible_50'] if counts['eligible_50'] else None,
            'pct_200': counts['above_200'] / counts['eligible_200'] if counts['eligible_200'] else None}


def sector_strength(etf_bars: list[dict], spy_bars: list[dict], session: date) -> dict | None:
    """rs20/rs60 of the ETF minus SPY over adj_close; None when either side lacks 61 bars."""
    etf = _up_to_session(etf_bars, session)
    spy = _up_to_session(spy_bars, session)
    needed = max(RS_WINDOWS) + 1
    if len(etf) < needed or len(spy) < needed:
        return None
    etf_close = [b['adj_close'] for b in etf]
    spy_close = [b['adj_close'] for b in spy]
    rs = {f'rs{w}': etf_close[-1] / etf_close[-1 - w] - spy_close[-1] / spy_close[-1 - w]
          for w in RS_WINDOWS}
    if rs['rs20'] < 0 and rs['rs60'] < 0:
        state = 'weak'
    elif rs['rs20'] > 0 and rs['rs60'] > 0:
        state = 'strong'
    else:
        state = 'mixed'
    return {**rs, 'state': state}


def macro_view(rows: list[dict]) -> dict | None:
    """Latest value plus the change against MACRO_CHANGE_OBS observations back."""
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: r['obs_date'])
    view = {'value': rows[-1]['value'], 'obs_date': rows[-1]['obs_date'],
            'change': None, 'change_from': None}
    if len(rows) > MACRO_CHANGE_OBS:
        base = rows[-1 - MACRO_CHANGE_OBS]
        view['change'] = rows[-1]['value'] - base['value']
        view['change_from'] = base['obs_date']
    return view
