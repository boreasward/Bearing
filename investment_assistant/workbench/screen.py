"""screen-1: pure daily-screener rules. No database access.

The pipeline (spec §5.3): data precondition -> liquidity -> earnings window ->
trigger group (price buy rules of the day) or setup band -> RS ranking ->
select at most MAX_NEW. Ranking favors triggers, then setups, both by RS
against the QQQ benchmark; everything else is recorded, not written elsewhere.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from investment_assistant.core.versions import SCREEN_VERSION as SCREEN_VERSION
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.workbench import plans, rules

MIN_DOLLAR_VOLUME = Decimal('50000000')  # 20 日平均成交额下限（美元）
ATR_PCT_MIN = Decimal('0.02')
ATR_PCT_MAX = Decimal('0.06')
EARNINGS_EXCLUDE_SESSIONS = 5  # 财报排除窗口（交易日，含当天）
MAX_NEW = 20                   # 每天新入选上限
MAX_MEMBERS = 40               # 筛选成员上限（还要减手动观察池）
RETENTION_SESSIONS = 10        # 入选保留期（交易日，含入选当天）
TRIGGER_RULES = ('trend_reclaim', 'volume_breakout')
BENCHMARK = 'QQQ'


class BenchmarkMissing(RuntimeError):
    """The benchmark has no usable bar for the session; the whole run fails."""


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _ret(bars: list[dict[str, Any]], n: int) -> Decimal:
    return _dec(bars[-1]['adj_close']) / _dec(bars[-1 - n]['adj_close']) - 1


def _ma(bars: list[dict[str, Any]], n: int) -> Decimal:
    window = [_dec(b['adj_close']) for b in bars[-n:]]
    return sum(window) / len(window)


def _dollar_volume_20(bars: list[dict[str, Any]]) -> Decimal:
    return sum(_dec(b['close']) * _dec(b['volume']) for b in bars[-20:]) / 20


def evaluate(*, session: date, bars_by_ticker: dict[str, list[dict[str, Any]]],
             benchmark_bars: list[dict[str, Any]],
             earnings_by_ticker: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    """Screen one session over pure inputs. Data skips land in `skips`, never in `results`."""
    reason = rules.precondition(benchmark_bars, session)
    if reason:
        raise BenchmarkMissing(f'{BENCHMARK}: {reason}')
    bench_r20, bench_r60 = _ret(benchmark_bars, 20), _ret(benchmark_bars, 60)
    results: list[dict[str, Any]] = []
    skips: dict[str, str] = {}
    counts = {'universe': len(bars_by_ticker), 'data_skipped': 0, 'illiquid': 0, 'earnings_soon': 0,
              'no_setup': 0, 'trigger': 0, 'setup': 0, 'selected': 0}
    earnings_window_end = add_trading_days(session, EARNINGS_EXCLUDE_SESSIONS)

    for ticker, bars in bars_by_ticker.items():
        reason = rules.precondition(bars, session)
        if reason:
            skips[ticker] = reason
            counts['data_skipped'] += 1
            continue
        row: dict[str, Any] = {'ticker': ticker, 'group': 'none', 'trigger_rules': [],
                               'filters': {'data': 'ok'}, 'rs': None, 'atr_pct': None,
                               'dollar_volume_20': None, 'rank': None, 'selected': False}
        results.append(row)

        volume20 = _dollar_volume_20(bars)
        row['dollar_volume_20'] = float(volume20)
        if volume20 < MIN_DOLLAR_VOLUME:
            row['filters']['liquidity'] = 'illiquid'
            counts['illiquid'] += 1
            continue
        row['filters']['liquidity'] = 'ok'

        earnings = earnings_by_ticker.get(ticker)
        if earnings and earnings.get('status') == 'known' and earnings.get('earnings_date') is not None:
            if session <= earnings['earnings_date'] <= earnings_window_end:
                row['filters']['earnings'] = 'earnings_soon'
                counts['earnings_soon'] += 1
                continue
            row['filters']['earnings'] = 'ok'
        else:
            row['filters']['earnings'] = 'earnings_unknown'

        close = _dec(bars[-1]['adj_close'])
        row['rs'] = float(Decimal('0.5') * (_ret(bars, 20) - bench_r20)
                          + Decimal('0.5') * (_ret(bars, 60) - bench_r60))
        atr = plans.atr14(bars)
        atr_pct = (atr / _dec(bars[-1]['close'])) if atr is not None else None
        row['atr_pct'] = float(atr_pct) if atr_pct is not None else None

        outcome = rules.evaluate(ticker, session, bars, [], None)
        has_sell = any(c.action == 'sell' for c in outcome['candidates'])
        buy_triggers = [c.rule_id for c in outcome['candidates']
                        if c.rule_id in TRIGGER_RULES and c.action == 'buy']
        if buy_triggers and not has_sell:
            row['group'] = 'trigger'
            row['trigger_rules'] = buy_triggers
            row['filters']['setup'] = 'ok'  # 触发组不做形态过滤
            counts['trigger'] += 1
        else:
            ma50, ma200 = _ma(bars, 50), _ma(bars, 200)
            in_band = atr_pct is not None and ATR_PCT_MIN <= atr_pct <= ATR_PCT_MAX
            if close > ma50 > ma200 and in_band:
                row['group'] = 'setup'
                row['filters']['setup'] = 'ok'
                counts['setup'] += 1
            else:
                row['filters']['setup'] = 'no_setup'
                counts['no_setup'] += 1

    ranked = sorted((r for r in results if r['group'] == 'trigger'),
                    key=lambda r: (-r['rs'], r['ticker']))
    ranked += sorted((r for r in results if r['group'] == 'setup'),
                     key=lambda r: (-r['rs'], r['ticker']))
    for i, row in enumerate(ranked, start=1):
        row['rank'] = i
        if i <= MAX_NEW:
            row['selected'] = True
            row['filters']['rank'] = 'ok'
            counts['selected'] += 1
        else:
            row['filters']['rank'] = 'rank_cut'
    return {'results': results, 'counts': counts, 'skips': skips}


def members(*, selected_today: list[str], recent: dict[str, dict[str, Any]], locked: set[str],
            manual: set[str], cap: int) -> dict[str, Any]:
    """Today's screen members by spec §6.2: locked -> today's picks -> retention by newest pick.

    Priority order decides what survives `cap`; the overflow is `evicted`. Manual-pool
    tickers never compete (they are screened by the watchlist path anyway)."""
    locked_members = sorted(t for t in locked if t not in manual)
    taken = set(locked_members)
    today_members = [t for t in selected_today if t not in manual and t not in taken]
    taken.update(today_members)
    recent_members = sorted(
        (t for t in recent if t not in manual and t not in taken),
        key=lambda t: (-recent[t]['session'].toordinal(), recent[t]['rank']))
    flags: list[str] = []
    if len(locked_members) > cap:
        flags.append('screen_locked_over_cap')
        return {'members': tuple(locked_members),
                'evicted': today_members + recent_members, 'flags': flags}
    ordered = locked_members + today_members + recent_members
    return {'members': tuple(ordered[:cap]), 'evicted': ordered[cap:], 'flags': flags}


def main(argv: list[str] | None = None) -> int:
    """`python -m investment_assistant.workbench.screen replay [--sessions N]` prints JSON."""
    import argparse
    import json
    from datetime import UTC, datetime

    parser = argparse.ArgumentParser(prog='python -m investment_assistant.workbench.screen')
    sub = parser.add_subparsers(dest='command', required=True)
    replay_cmd = sub.add_parser('replay', help='read-only replay of the last N closed sessions')
    replay_cmd.add_argument('--sessions', type=int, default=5)
    args = parser.parse_args(argv)
    if args.command == 'replay':
        from investment_assistant.workbench import screening
        print(json.dumps(screening.replay(sessions=args.sessions, now=datetime.now(UTC)),
                         ensure_ascii=False, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
