"""v1 signal rules (design 2026-10-23 §5). Pure functions over bars known at the cutoff.

Thresholds are fixed a priori, not fitted. Changing one means a new RULE_VERSION;
stored candidates keep the version and inputs they were produced with.
Indicators use adj_close; gaps compare T's open with T-1's close (both split-adjusted,
dividend-unadjusted). Drawdowns compare split-adjusted closes with position cost.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from investment_assistant.data.calendar import add_trading_days, is_trading_day

RULE_VERSION = '1'
MIN_BARS = 200
NY = ZoneInfo('America/New_York')
MARKET_OPEN, MARKET_CLOSE = time(9, 30), time(16, 0)

DAYS_BELOW_MA20 = 3
BREAKOUT_LOOKBACK = 20
VOLUME_MULTIPLE = 1.5
EARNINGS_GAP = 0.03
DAYS_ABOVE_MA50 = 10
COST_STOP = 0.08
TRAILING_STOP = 0.12
ABNORMAL_SIGMA = 2.5
MAX_FACTOR_JUMP = 0.2
FILING_FORMS = ('10-Q', '10-K', '8-K')


@dataclass(frozen=True)
class Candidate:
    rule_id: str
    action: str
    inputs: dict[str, Any]
    rule_version: str = RULE_VERSION


@dataclass(frozen=True)
class Skip:
    rule_id: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


PRICE_RULES = ('trend_reclaim', 'volume_breakout', 'earnings_gap_up', 'trend_break',
               'earnings_gap_down', 'position_drawdown', 'abnormal_move', 'plan_stop', 'plan_target')


def _r(x: float) -> float:
    return round(x, 10)


def earnings_session(accepted_at: datetime) -> date | None:
    """Session whose open first reflects an earnings release; None when released intraday."""
    local = accepted_at.astimezone(NY)
    day = local.date()
    if not is_trading_day(day) or local.time() >= MARKET_CLOSE:
        return add_trading_days(day, 1)
    if local.time() < MARKET_OPEN:
        return day
    return None


def filing_session(filing: dict[str, Any]) -> date:
    """Session on which a filing is new to the user."""
    accepted = filing.get('accepted_at')
    if accepted is not None:
        local = accepted.astimezone(NY)
        day = local.date()
        late = local.time() >= MARKET_CLOSE
    else:
        day, late = filing['filing_date'], False
    return add_trading_days(day, 1) if late or not is_trading_day(day) else day


def precondition(bars: list[dict[str, Any]], signal_date: date) -> str | None:
    if not bars or bars[-1]['price_date'] != signal_date:
        return 'missing_signal_bar'
    if len(bars) < MIN_BARS:
        return 'insufficient_history'
    factors = [b['adj_close'] / b['close'] for b in bars[-MIN_BARS:]]
    if any(f <= 0 for f in factors) or any(abs(b / a - 1) > MAX_FACTOR_JUMP for a, b in zip(factors, factors[1:])):
        return 'adjustment_anomaly'
    return None


# 兼容别名：v2.1 及更早的调用方仍用 _precondition
_precondition = precondition


class _Series:
    def __init__(self, bars: list[dict[str, Any]]):
        self.bars = bars
        self.adj = [b['adj_close'] for b in bars]
        self.t = len(bars) - 1

    def ma(self, n: int, idx: int | None = None) -> float:
        idx = self.t if idx is None else idx
        return sum(self.adj[idx - n + 1: idx + 1]) / n


def _base(signal_date: date, bars: list[dict[str, Any]], *extra_bars: int) -> dict[str, Any]:
    return {'signal_date': signal_date.isoformat(), 'bar_ids': [*extra_bars, bars[-1]['id']]}


def _trend_reclaim(s: _Series, d: date) -> Candidate | None:
    c, m20, m50, m200 = s.adj[s.t], s.ma(20), s.ma(50), s.ma(200)
    below = all(s.adj[s.t - k] < s.ma(20, s.t - k) for k in range(1, DAYS_BELOW_MA20 + 1))
    if c > m50 > m200 and c > m20 and below:
        return Candidate('trend_reclaim', 'buy', {
            **_base(d, s.bars), 'values': {'close': c, 'ma20': _r(m20), 'ma50': _r(m50), 'ma200': _r(m200)},
            'thresholds': {'days_below_ma20': DAYS_BELOW_MA20}})
    return None


def _volume_breakout(s: _Series, d: date) -> Candidate | None:
    c, m50 = s.adj[s.t], s.ma(50)
    prior_high = max(s.adj[s.t - BREAKOUT_LOOKBACK: s.t])
    volumes = [b['volume'] for b in s.bars]
    avg_volume = sum(volumes[s.t - 50: s.t]) / 50  # prior 50 sessions, excluding T
    if c > prior_high and volumes[s.t] >= VOLUME_MULTIPLE * avg_volume and c > m50:
        return Candidate('volume_breakout', 'buy', {
            **_base(d, s.bars), 'values': {'close': c, 'prior_high': prior_high, 'volume': volumes[s.t],
                                           'avg_volume_50': _r(avg_volume), 'ma50': _r(m50)},
            'thresholds': {'lookback': BREAKOUT_LOOKBACK, 'volume_multiple': VOLUME_MULTIPLE}})
    return None


def _earnings_gap(s: _Series, d: date, filing: dict[str, Any]) -> Candidate | None:
    today, prev = s.bars[s.t], s.bars[s.t - 1]
    gap = _r(today['open'] / prev['close'] - 1)
    inputs = {**_base(d, s.bars, prev['id']), 'filing_ids': [filing['id']],
              'values': {'open': today['open'], 'close': today['close'], 'prev_close': prev['close'], 'gap': gap},
              'thresholds': {'gap': EARNINGS_GAP}}
    if gap >= EARNINGS_GAP and today['close'] >= today['open']:
        return Candidate('earnings_gap_up', 'buy', inputs)
    if gap <= -EARNINGS_GAP and today['close'] <= today['open']:
        return Candidate('earnings_gap_down', 'sell', inputs)
    return None


def _trend_break(s: _Series, d: date) -> Candidate | None:
    c, m50 = s.adj[s.t], s.ma(50)
    above = all(s.adj[s.t - k] > s.ma(50, s.t - k) for k in range(1, DAYS_ABOVE_MA50 + 1))
    if above and c < m50:
        return Candidate('trend_break', 'sell', {
            **_base(d, s.bars), 'values': {'close': c, 'ma50': _r(m50)},
            'thresholds': {'days_above_ma50': DAYS_ABOVE_MA50}})
    return None


def _position_drawdown(s: _Series, d: date, position: dict[str, Any]) -> Candidate | None:
    close = s.bars[s.t]['close']
    cost = float(position['avg_cost'])
    opened = position['opened_at'].astimezone(NY).date()
    high = max(b['close'] for b in s.bars if b['price_date'] >= opened)
    from_cost, from_high = _r(close / cost - 1), _r(close / high - 1)
    triggered = [name for name, hit in (('cost', from_cost <= -COST_STOP), ('trailing', from_high <= -TRAILING_STOP))
                 if hit]
    if triggered:
        return Candidate('position_drawdown', 'sell', {
            **_base(d, s.bars), 'values': {'close': close, 'avg_cost': cost, 'high_since_opened': high,
                                           'from_cost': from_cost, 'from_high': from_high, 'triggered': triggered},
            'thresholds': {'cost_stop': COST_STOP, 'trailing_stop': TRAILING_STOP}})
    return None


def _abnormal_move(s: _Series, d: date) -> Candidate | None:
    returns = [s.adj[i] / s.adj[i - 1] - 1 for i in range(s.t - 20, s.t + 1)]
    today, sd = returns[-1], statistics.stdev(returns[:-1])
    if today != 0 and abs(today) >= ABNORMAL_SIGMA * sd:
        return Candidate('abnormal_move', 'watch', {
            **_base(d, s.bars), 'values': {'return': _r(today), 'stdev_20': _r(sd)},
            'thresholds': {'sigma': ABNORMAL_SIGMA}})
    return None


def _plan_stop(s: _Series, d: date, state: dict[str, Any]) -> Candidate | None:
    low, stop = s.bars[s.t]['low'], float(state['stop'])
    if low > stop:
        return None
    return Candidate('plan_stop', 'sell', {
        **_base(d, s.bars), 'values': {'low': low, 'stop': stop, 'mode': state['mode']},
        'thresholds': {}})  # 阈值冻结在计划里；此处只报当期止损


def _plan_target(s: _Series, d: date, state: dict[str, Any]) -> Candidate | None:
    if state['t1_reached']:
        return None
    high, target1 = s.bars[s.t]['high'], float(state['target1'])
    if high < target1:
        return None
    return Candidate('plan_target', 'sell', {
        **_base(d, s.bars), 'values': {'high': high, 'target1': target1, 'sell_fraction': 0.5},
        'thresholds': {}})


def evaluate(ticker: str, signal_date: date, bars: list[dict[str, Any]], filings: list[dict[str, Any]],
             position: dict[str, Any] | None, plan_state: dict[str, Any] | None = None) -> dict[str, list]:
    """Candidates and skip reasons for one ticker on one session. ``bars`` oldest first.

    A planned position (``plan_state`` from exits.active_stop) uses plan_stop /
    plan_target and skips position_drawdown; unplanned positions keep the drawdown rule.
    """
    candidates: list[Candidate] = []
    skips: list[Skip] = []

    new_filings = [f for f in filings if f['form'] in FILING_FORMS and filing_session(f) == signal_date]
    if new_filings:
        candidates.append(Candidate('new_filing', 'watch', {
            'signal_date': signal_date.isoformat(), 'filing_ids': sorted(f['id'] for f in new_filings),
            'values': {'forms': sorted({f['form'] for f in new_filings})}}))

    reason = precondition(bars, signal_date)
    if reason:
        skips.extend(Skip(rule_id, reason) for rule_id in PRICE_RULES)
        return {'candidates': candidates, 'skips': skips}

    s = _Series(bars)
    for rule in (_trend_reclaim, _volume_breakout, _trend_break):
        if (c := rule(s, signal_date)) is not None:
            candidates.append(c)

    previous = add_trading_days(signal_date, -1)
    for f in filings:
        if f['form'] != '8-K' or '2.02' not in (f.get('items') or []):
            continue
        if f.get('accepted_at') is None:
            if previous <= f['filing_date'] <= signal_date:
                skips.append(Skip('earnings_gap_up', 'earnings_time_unknown', {'filing_id': f['id']}))
            continue
        if earnings_session(f['accepted_at']) == signal_date and (c := _earnings_gap(s, signal_date, f)):
            candidates.append(c)

    if position and position['quantity'] > 0:
        if plan_state:
            for rule in (_plan_stop, _plan_target):
                if (c := rule(s, signal_date, plan_state)) is not None:
                    candidates.append(c)
        elif (c := _position_drawdown(s, signal_date, position)) is not None:
            candidates.append(c)
    if not new_filings and (c := _abnormal_move(s, signal_date)):
        candidates.append(c)
    return {'candidates': candidates, 'skips': skips}
