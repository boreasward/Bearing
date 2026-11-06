"""Regime-2 advisory label and frozen snapshot."""
from __future__ import annotations

from datetime import date

from investment_assistant.core.versions import REGIME_VERSION as REGIME_VERSION
from investment_assistant.derivation.indicators.breadth import breadth, sector_strength, macro_view

MIN_ELIGIBLE_200 = 20


def label(b: dict) -> str:
    """Regime label by spec §5.2 order: insufficient, risk_on, risk_off, neutral."""
    if b['eligible_200'] < MIN_ELIGIBLE_200:
        return 'insufficient'
    if b['pct_200'] >= 0.60 and b['pct_50'] >= 0.50:
        return 'risk_on'
    if b['pct_200'] < 0.40 and b['pct_50'] < 0.30:
        return 'risk_off'
    return 'neutral'


def snapshot(*, session: date, pool_bars: dict[str, list[dict]], etf_bars: dict[str, list[dict]],
             spy_bars: list[dict], macro_rows: dict[str, list[dict]],
             macro_status: dict[str, str] | None = None) -> dict:
    """The full market context record for one session, frozen at calculation time.

    `macro_status` ('ok' / 'not_configured' / 'error' per series) lets readers tell
    a missing key from a failed fetch when no observations are stored; it sits in
    inputs, outside the conflict fingerprint.
    """
    b = breadth(pool_bars, session)
    return {'session': session, 'calc_version': REGIME_VERSION, 'label': label(b), 'breadth': b,
            'sectors': {etf: sector_strength(bars, spy_bars, session) for etf, bars in etf_bars.items()},
            'macro': {series: macro_view(rows) for series, rows in macro_rows.items()},
            'inputs': {'tickers': sorted(pool_bars), 'etfs': list(etf_bars), 'series': list(macro_rows),
                       'macro_status': dict(macro_status or {})}}
