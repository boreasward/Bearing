"""Watchlist continuity (v3.1 spec §13 / acceptance A15): new entries, streak lengths and
why tickers dropped off the list. Pure — the caller supplies both days' candidate rows and
the screen results; Task 3 persists `streak` in v3_candidates.detail.
"""
from __future__ import annotations


def _drop_reasons(ticker: str, filters: dict | None, skips: dict[str, str]) -> list[str]:
    """Why a ticker is gone today, in screen pipeline order (spec §13): a data skip first,
    then liquidity / earnings window / setup / rank cut."""
    if ticker in skips:
        return [skips[ticker]]
    filters = filters or {}
    reasons: list[str] = []
    if filters.get('liquidity') == 'illiquid':
        reasons.append('illiquid')
    if filters.get('earnings') == 'earnings_soon':
        reasons.append('earnings_soon')
    if filters.get('setup') == 'no_setup':
        reasons.append('no_setup')
    if filters.get('rank') == 'rank_cut':
        reasons.append('rank_cut')
    return reasons


def continuity(*, today: list[dict], previous: list[dict] | None, screen_results: list[dict],
               skips: dict[str, str]) -> dict:
    """`today`/`previous` are candidate rows (`ticker`, `rank`; previous rows carry `streak`).
    First run (previous is None) marks everything newly listed with streak 1. A ticker on
    both days gets previous_streak + 1; one only yesterday lands in `dropped` with the day's
    screen reasons for it.
    """
    previous_by_ticker = {row['ticker']: row for row in previous} if previous is not None else None
    streak: dict[str, int] = {}
    new: list[str] = []
    for row in today:
        ticker = row['ticker']
        if previous_by_ticker is None or ticker not in previous_by_ticker:
            streak[ticker] = 1
            new.append(ticker)
        else:
            streak[ticker] = int(previous_by_ticker[ticker].get('streak', 1)) + 1
    dropped: list[dict] = []
    if previous is not None:
        filters_by_ticker = {row['ticker']: row.get('filters') for row in screen_results}
        for row in previous:
            ticker = row['ticker']
            if ticker in streak:
                continue
            dropped.append({'ticker': ticker,
                            'reasons': _drop_reasons(ticker, filters_by_ticker.get(ticker), skips)})
    return {'first_run': previous is None, 'streak': streak, 'new': new, 'dropped': dropped}
