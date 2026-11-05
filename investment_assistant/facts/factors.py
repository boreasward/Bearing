"""v3 market factors: trigger mirroring, setup follow-through, industry strength, regime tier.

Pure functions over daily-bar dicts (`id, price_date, open, high, low, close, adj_close,
volume`); the v1 arithmetic (rules.evaluate, TRIGGER_RULES) is called as-is, never re-derived.
Tiers are fixed a priori (v3 spec §5.4): 有利 / 中性 / 不利 / 样本不足 — they rank and label,
never filter, and are unvalidated defaults from the config.
"""
from __future__ import annotations

import math
import statistics
from datetime import date

from investment_assistant.analysis.rules import evaluate as _evaluate_rules
from investment_assistant.analysis.screen_rules import TRIGGER_RULES
from investment_assistant.config import V3Config
from investment_assistant.data.calendar import add_trading_days

BUY_RULES = TRIGGER_RULES   # ('trend_reclaim', 'volume_breakout')

_ABOVE_MA_SAMPLES = 50
_RS_SAMPLES = 21


def trigger_rules_on(bars: list[dict], day: date) -> list[str]:
    """Buy-trigger rule ids active on ``day`` — the same condition screen-1 uses for its
    trigger group: a sell candidate on the day suppresses every buy trigger."""
    outcome = _evaluate_rules('', day, bars, [], None)
    if any(c.action == 'sell' for c in outcome['candidates']):
        return []
    return sorted(c.rule_id for c in outcome['candidates']
                  if c.action == 'buy' and c.rule_id in BUY_RULES)


def _tier(n: int, win_rate: float | None, mean_return: float | None, cfg: V3Config) -> str:
    if n < cfg.min_triggers or win_rate is None or mean_return is None:
        return 'insufficient'
    if win_rate >= cfg.win_hi and mean_return > 0:
        return 'favorable'
    if win_rate <= cfg.win_lo or mean_return < 0:
        return 'unfavorable'
    return 'neutral'


def setup_followthrough(bars_by_ticker: dict[str, list[dict]], *, session: date,
                        cfg: V3Config) -> dict[str, dict]:
    """Replay each buy rule's triggers over the last ``replay_sessions`` signal days — the
    window ends ``hold_sessions`` before ``session`` so every outcome is complete — and score
    the fixed-hold return: how often it rose and by how much."""
    window_end = add_trading_days(session, -cfg.hold_sessions)
    signal_days = [add_trading_days(window_end, -(cfg.replay_sessions - 1 - i))
                   for i in range(cfg.replay_sessions)]
    events: dict[str, list[float]] = {rule: [] for rule in BUY_RULES}
    for bars in bars_by_ticker.values():
        ordered = sorted(bars, key=lambda b: b['price_date'])
        at = {bar['price_date']: i for i, bar in enumerate(ordered)}
        for day in signal_days:
            i = at.get(day)
            if i is None or len(ordered) - (i + 1) < cfg.hold_sessions:
                continue
            after = ordered[i + cfg.hold_sessions]
            for rule in trigger_rules_on(ordered[:i + 1], day):
                events[rule].append(after['adj_close'] / ordered[i]['adj_close'] - 1)
    out: dict[str, dict] = {}
    for rule in BUY_RULES:
        returns = events[rule]
        n, wins = len(returns), sum(1 for value in returns if value > 0)
        win_rate = round(wins / n, 4) if n else None
        mean_return = round(sum(returns) / n, 4) if n else None
        out[rule] = {'n': n, 'wins': wins, 'win_rate': win_rate, 'mean_return': mean_return,
                     'tier': _tier(n, win_rate, mean_return, cfg),
                     'window': [signal_days[0].isoformat(), signal_days[-1].isoformat()]}
    return out


def industry_strength(bars_by_ticker: dict[str, list[dict]], spy_bars: list[dict],
                      groups: dict[str, str | None], *, session: date,
                      cfg: V3Config) -> dict[str, dict]:
    """Per industry group: share of members above their 50-day mean and the median 20-day
    relative strength against SPY, ranked across groups with enough eligible members."""
    spy_closes = [b['adj_close'] for b in sorted((b for b in spy_bars if b['price_date'] <= session),
                                                 key=lambda b: b['price_date'])]
    spy_ret20 = (spy_closes[-1] / spy_closes[-_RS_SAMPLES] - 1) if len(spy_closes) >= _RS_SAMPLES else None
    members: dict[str, int] = {}
    eligible: dict[str, int] = {}
    above: dict[str, int] = {}
    rs20s: dict[str, list[float]] = {}
    for ticker, group in groups.items():
        if group is None:
            continue
        members[group] = members.get(group, 0) + 1
        history = sorted((b for b in bars_by_ticker.get(ticker, []) if b['price_date'] <= session),
                         key=lambda b: b['price_date'])
        if not history or history[-1]['price_date'] != session:
            continue
        closes = [b['adj_close'] for b in history]
        if len(closes) >= _ABOVE_MA_SAMPLES:
            eligible[group] = eligible.get(group, 0) + 1
            if closes[-1] > sum(closes[-_ABOVE_MA_SAMPLES:]) / _ABOVE_MA_SAMPLES:
                above[group] = above.get(group, 0) + 1
        if spy_ret20 is not None and len(closes) >= _RS_SAMPLES:
            rs20s.setdefault(group, []).append(closes[-1] / closes[-_RS_SAMPLES] - 1 - spy_ret20)
    out = {group: {'members': count, 'eligible': eligible.get(group, 0),
                   'pct_above_50': above.get(group, 0) / eligible[group] if eligible.get(group) else None,
                   'median_rs20': statistics.median(rs20s[group]) if rs20s.get(group) else None,
                   'rank': None, 'tier': 'insufficient'}
           for group, count in members.items()}
    ranked = sorted((group for group in out if out[group]['eligible'] >= cfg.min_group),
                    key=lambda g: (-(above.get(g, 0) / eligible[g]),
                                   math.inf if (median := out[g]['median_rs20']) is None else -median, g))
    k = len(ranked)
    cut = math.ceil(k / 3)
    for rank, group in enumerate(ranked, start=1):
        out[group]['rank'] = rank
        out[group]['tier'] = ('neutral' if k < 3 else
                              'favorable' if rank <= cut else
                              'unfavorable' if rank > k - cut else 'neutral')
    return out


def regime_tier(label: str) -> str:
    """The regime label as one of the four factor tiers (regime-2's own sample rule stands)."""
    return {'risk_on': 'favorable', 'neutral': 'neutral', 'risk_off': 'unfavorable'}.get(label, 'insufficient')
