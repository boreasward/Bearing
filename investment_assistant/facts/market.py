"""v3 market view: the regime-2 snapshot plus the tier, the SPY 20-day return and the ETF
table the draft and the tool surface read (all keys of analysis.regime.snapshot are kept)."""
from __future__ import annotations

from datetime import date

from investment_assistant.analysis.regime import snapshot
from investment_assistant.facts.factors import regime_tier

_RET_SAMPLES = 21


def _ret20(bars: list[dict], session: date) -> float | None:
    closes = [b['adj_close'] for b in sorted((b for b in bars if b['price_date'] <= session),
                                             key=lambda b: b['price_date'])]
    return closes[-1] / closes[-_RET_SAMPLES] - 1 if len(closes) >= _RET_SAMPLES else None


def market_view(*, session: date, universe_bars: dict[str, list[dict]], etf_bars: dict[str, list[dict]],
                spy_bars: list[dict], macro_rows: dict[str, list[dict]],
                macro_status: dict[str, str] | None = None) -> dict:
    """The session's market record: regime.snapshot verbatim, then the derived additions.
    ETFs with fewer than 61 bars keep their own 20-day return but carry no relative strength."""
    snap = snapshot(session=session, pool_bars=universe_bars, etf_bars=etf_bars,
                    spy_bars=spy_bars, macro_rows=macro_rows, macro_status=macro_status)
    table = []
    for etf, bars in etf_bars.items():
        strength = snap['sectors'].get(etf)
        table.append({'etf': etf, 'ret20': _ret20(bars, session),
                      'rs20': strength['rs20'] if strength else None,
                      'rs60': strength['rs60'] if strength else None,
                      'state': strength['state'] if strength else None})
    table.sort(key=lambda row: (row['rs20'] is None, -(row['rs20'] or 0.0), row['etf']))
    return {**snap, 'regime_tier': regime_tier(snap['label']),
            'spy_ret20': _ret20(spy_bars, session), 'etf_table': table}
