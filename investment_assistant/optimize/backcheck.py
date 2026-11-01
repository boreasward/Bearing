"""Pre-launch historical rule check (backcheck-2, spec §5.3).

Replays stored bars through the current rules with no position, prices buy
candidates with plan-1 and simulates them with plan-shadow-1. Descriptive
only: no parameter tuning, no claim of validity.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from statistics import fmean, median, quantiles
from typing import Any

from investment_assistant.data.calendar import add_trading_days
from investment_assistant.analysis import rules, pool as pool_module
from investment_assistant.core import tx as core_tx
from investment_assistant.core.constants import BENCHMARK
from investment_assistant.data import repo as data_repo
from investment_assistant.data.adapters import bars
from investment_assistant.db import list_watchlist_items
from investment_assistant.optimize import repo as optimize_repo
from investment_assistant.execution import simulation as plan_shadow
from investment_assistant.execution import plan_levels as plans

CALC_VERSION = 'backcheck-2'
WARMUP = rules.MIN_BARS
MATURE_SESSIONS = 20
SWEEP_SESSIONS = 5
FREQ_LIMIT = 5.0
SWEEP_SHARE = 0.5
MIN_FILLED = 10
CLUSTER_MIN = 3
BUY_RULES = ('trend_reclaim', 'volume_breakout', 'earnings_gap_up')
EXCESS_HORIZONS = (5, 10, 20)

NOTE = '描述性检查，不调参，不代表规则有效。超额是信号日收盘到第 h 日收盘，不代表可成交。'


def replay(ticker: str, bars: list[dict[str, Any]], filings: list[dict[str, Any]], spy_bars: list[dict[str, Any]],
           *, splits: list, evaluate=rules.evaluate) -> dict[str, Any]:
    """Evaluate every session after the warmup on the bars known at that time.

    The first WARMUP bars only warm the indicators; signals need a priceable
    plan, days without ATR count as skipped. Immature signals count as
    triggers but are not scored.
    """
    from investment_assistant.config import WorkbenchConfig

    spy_close = {bar['price_date']: bar['close'] for bar in spy_bars}
    signals: list[dict[str, Any]] = []
    skipped_atr = 0
    warmup_end = bars[WARMUP - 1]['price_date'] if len(bars) >= WARMUP else None
    eval_start = bars[WARMUP]['price_date'] if len(bars) > WARMUP else None
    eval_end = bars[-1]['price_date'] if len(bars) > WARMUP else None
    for i in range(WARMUP, len(bars)):
        day, upto = bars[i]['price_date'], bars[:i + 1]
        result = evaluate(ticker, day, upto, filings, None)
        cands = [c for c in result['candidates']
                 if getattr(c, 'rule_id', None) in BUY_RULES and getattr(c, 'action', None) == 'buy']
        if not cands:
            continue
        plan = plans.build_rule_plan(ticker=ticker, signal_date=day, bars=upto, earnings_row=None, snapshot=None,
                                     exposure={'ticker_value': 0, 'total_value': 0}, params=WorkbenchConfig())
        if 'skip' in plan:
            skipped_atr += 1
            continue
        shadow_plan = {'ticker': ticker, 'signal_date': day, 'entry_low': plan['entry_low'],
                       'entry_high': plan['entry_high'], 'stop': plan['stop'], 'target1': plan['target1'],
                       'atr': plan['atr'], 'trail_atr_multiple': plan['trail_atr_multiple'],
                       'valid_from': plan['valid_from'], 'valid_until': plan['valid_until']}
        mature = add_trading_days(plan['valid_until'], MATURE_SESSIONS) <= bars[-1]['price_date']
        outcome = (plan_shadow.simulate(shadow_plan, bars, spy_bars, splits=splits) if mature else None)
        # One signal per ticker-day (one plan); every buy rule that fired is listed and counts for its own row.
        rule_ids = list(dict.fromkeys(c.rule_id for c in cands))
        signals.append({'rule_id': rule_ids[0], 'rule_ids': rule_ids, 'signal_date': day, 'mature': mature,
                        'outcome': outcome, 'excess': _excess(bars, i, spy_close)})
    return {'ticker': ticker, 'warmup_end': warmup_end, 'eval_start': eval_start, 'eval_end': eval_end,
            'eval_sessions': max(0, len(bars) - WARMUP), 'skipped_atr': skipped_atr, 'signals': signals}


def _excess(bars: list[dict[str, Any]], i: int, spy_close: dict[Any, Any]) -> dict[int, Any]:
    """Close-to-close excess over SPY per horizon; bar h trading days after the signal day.

    Beyond the last bar is immature; a missing close on either endpoint is missing —
    neither is substituted from neighbouring bars.
    """
    stock0 = bars[i]['close']
    spy0 = spy_close.get(bars[i]['price_date'])
    out: dict[int, Any] = {}
    for h in EXCESS_HORIZONS:
        j = i + h
        if j >= len(bars):
            out[h] = 'immature'
            continue
        stock1, spy1 = bars[j]['close'], spy_close.get(bars[j]['price_date'])
        if stock0 in (None, 0) or stock1 is None or spy0 in (None, 0) or spy1 is None:
            out[h] = 'missing'
        else:
            out[h] = stock1 / stock0 - spy1 / spy0
    return out


def _excess_stats(signals: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for h in EXCESS_HORIZONS:
        values: list[float] = []
        immature = missing = 0
        for signal in signals:
            value = (signal.get('excess') or {}).get(h)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(float(value))
            elif value == 'immature':
                immature += 1
            elif value == 'missing':
                missing += 1
        out[str(h)] = {'n': len(values), 'mean': fmean(values) if values else None,
                       'median': median(values) if values else None,
                       'positive_share': (sum(1 for v in values if v > 0) / len(values)) if values else None,
                       'immature': immature, 'missing': missing}
    return out


def _row() -> dict[str, Any]:
    return {'triggers': 0, 'ticker_sessions': 0, 'per_100': 0.0, 'max_per_ticker': 0, 'cluster_days': 0,
            'filled': 0, 'expired': 0, 'stopped_within_5': 0, 'stopped_within_5_share': None, 't1_share': None,
            'r': {'count': 0, 'mean': None, 'median': None, 'p25': None, 'p75': None},
            'excess': {str(h): {'n': 0, 'mean': None, 'median': None, 'positive_share': None,
                                'immature': 0, 'missing': 0} for h in EXCESS_HORIZONS}, 'flags': []}


def _stopped_within_5(outcome: dict[str, Any]) -> bool:
    legs = outcome.get('legs') or []
    fill_date = outcome.get('fill_date')
    if not legs or fill_date is None:
        return False
    first = legs[0]
    return (first.get('reason') == 'stop' and first.get('exit_date') is not None
            and date.fromisoformat(first['exit_date']) <= add_trading_days(fill_date, SWEEP_SESSIONS))


def _fill_row(row: dict[str, Any], signals: list[dict[str, Any]], per_ticker_counts: dict[str, int],
              ticker_sessions: int, cluster_dates: set[date]) -> None:
    row['triggers'] += len(signals)
    row['ticker_sessions'] += ticker_sessions
    row['per_100'] = round(row['triggers'] / row['ticker_sessions'] * 100, 6) if row['ticker_sessions'] else 0.0
    row['max_per_ticker'] = max([row['max_per_ticker'], *per_ticker_counts.values()]) if per_ticker_counts else \
        row['max_per_ticker']
    row['cluster_days'] = len(cluster_dates)
    mature_filled = 0
    t1_reached = 0
    rs: list[float] = []
    for signal in signals:
        outcome = signal.get('outcome') or {}
        status = outcome.get('status')
        if status in ('open', 'closed'):
            row['filled'] += 1
            if _stopped_within_5(outcome):
                row['stopped_within_5'] += 1
            if signal['mature']:
                mature_filled += 1
                if any(leg.get('reason') == 'target1' for leg in outcome.get('legs') or []):
                    t1_reached += 1
        elif status == 'expired':
            row['expired'] += 1
        if signal['mature'] and outcome.get('r') is not None:
            rs.append(outcome['r'])
    row['stopped_within_5_share'] = (row['stopped_within_5'] / row['filled']) if row['filled'] else None
    row['t1_share'] = (t1_reached / mature_filled) if mature_filled else None
    row['r'] = {'count': len(rs), 'mean': fmean(rs) if rs else None, 'median': median(rs) if rs else None,
                'p25': quantiles(rs, n=4, method='inclusive')[0] if len(rs) >= 2 else None,
                'p75': quantiles(rs, n=4, method='inclusive')[2] if len(rs) >= 2 else None}
    if row['per_100'] > FREQ_LIMIT:
        row['flags'].append('too_frequent')
    if row['filled'] >= MIN_FILLED and row['stopped_within_5_share'] is not None \
            and row['stopped_within_5_share'] >= SWEEP_SHARE:
        row['flags'].append('stops_swept')
    if row['filled'] < MIN_FILLED:
        row['flags'].append('insufficient')
    row['excess'] = _excess_stats(signals)


def summarize(replays: list[dict[str, Any]]) -> dict[str, Any]:
    """Per rule and overall rows from replay outputs; flags answer the two launch questions."""
    by_rule: dict[str, dict[str, list]] = {}
    all_signals: list[dict[str, Any]] = []
    tickers_meta = []
    for replayed in replays:
        tickers_meta.append({k: (replayed[k].isoformat() if isinstance(replayed[k], date) else replayed[k])
                             for k in ('ticker', 'warmup_end', 'eval_start', 'eval_end', 'eval_sessions')})
        for signal in replayed['signals']:
            for rule_id in signal.get('rule_ids') or [signal['rule_id']]:
                bucket = by_rule.setdefault(rule_id, {'signals': [], 'counts': {}, 'dates': {},
                                                      'ticker_sessions': 0})
                bucket['signals'].append(signal)
                bucket['counts'][replayed['ticker']] = bucket['counts'].get(replayed['ticker'], 0) + 1
                day_tickers = bucket['dates'].setdefault(signal['signal_date'], set())
                day_tickers.add(replayed['ticker'])
        # The overall row counts each signal (one plan) once, however many rules fired.
        all_signals.append((replayed, list(replayed['signals'])))

    rules_out: dict[str, Any] = {}
    total_sessions = sum(r['eval_sessions'] for r in replays)
    for rule_id, bucket in by_rule.items():
        row = _row()
        cluster_dates = {day for day, tickers in bucket['dates'].items() if len(tickers) >= CLUSTER_MIN}
        _fill_row(row, bucket['signals'], bucket['counts'], total_sessions, cluster_dates)
        rules_out[rule_id] = row
    all_row = _row()
    all_counts: dict[str, int] = {}
    all_dates: dict[date, set] = {}
    for replayed, signals in all_signals:
        if signals:
            all_counts[replayed['ticker']] = all_counts.get(replayed['ticker'], 0) + len(signals)
        for s in signals:
            all_dates.setdefault(s['signal_date'], set()).add(replayed['ticker'])
    cluster_all = {day for day, tickers in all_dates.items() if len(tickers) >= CLUSTER_MIN}
    _fill_row(all_row, [s for _, signals in all_signals for s in signals], all_counts, total_sessions, cluster_all)
    return {'rules': rules_out, 'all': all_row, 'tickers': tickers_meta, 'note': NOTE}


def run_backcheck(*, now: datetime, tickers: list[str] | None = None) -> dict[str, Any]:
    """Replay stored data through the current rules; reads only, never collects."""
    from investment_assistant.execution import simulation as shadow_module

    with core_tx._tx() as conn:
        symbols = (pool_module.Pool.all_core(tickers).tickers if tickers is not None
                   else pool_module.from_watchlist(list_watchlist_items(conn)).tickers)
        spy_bars = data_repo.read_bars(conn, BENCHMARK, bars.latest_closed_session(now), cutoff_at=now, limit=2000)
        replays = []
        for ticker in symbols:
            ticker_bars = data_repo.read_bars(conn, ticker, bars.latest_closed_session(now), cutoff_at=now,
                                               limit=2000)
            first_day = ticker_bars[0]['price_date'] if ticker_bars else date(2026, 1, 1)
            replays.append(replay(
                ticker, ticker_bars, data_repo.read_filings(conn, ticker, since=first_day, cutoff_at=now),
                spy_bars, splits=data_repo.split_events(conn, ticker)))
        stats = summarize(replays)
        params = {'tickers': list(symbols), 'rule_version': rules.RULE_VERSION,
                  'plan_version': plans.PLAN_V1, 'shadow_version': shadow_module.SHADOW_V1,
                  'bars': 'stored current revisions'}
        return optimize_repo.insert_backcheck(conn, CALC_VERSION, params, stats)


def list_backchecks(limit: int = 10) -> list[dict[str, Any]]:
    with core_tx._tx() as conn:
        out = optimize_repo.list_backchecks(conn, limit)
    return [{**row, 'id': str(row['id'])} for row in out]


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m investment_assistant.optimize.backcheck [--tickers A,B]"""
    import argparse

    parser = argparse.ArgumentParser(description='上线前历史检查（backcheck-2）')
    parser.add_argument('--tickers', help='逗号分隔的标的；缺省用当前观察池')
    args = parser.parse_args(argv)
    record = run_backcheck(now=datetime.now(UTC),
                                   tickers=[t.strip().upper() for t in args.tickers.split(',')] if args.tickers else None)
    print(json.dumps({'id': str(record['id']), 'all': record['stats']['all']}, ensure_ascii=False, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
