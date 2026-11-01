"""Execution discipline against confirmed plans (design §6.2; v2.9 spec D28). Pure.

Flags: chase (buy above entry_high, judged at fill), early_take (sold before T1
and T1 arrived within 20 sessions, matured after those 20 sessions), held_loser
(no sell through the session after a plan_stop), unplanned (no decision or no
plan). Actual R prices risk at the plan stop, FIFO-matched.

`evaluate` (`discipline-1`, kept as `evaluate_v1`) finds each buy's plan through the old decision;
`evaluate_v2` (`discipline-2`) through the fill's own plan link, judges against the version the buy followed
(chase), the chain's newest version at the sale (early take) and the daily holding status's effective stop
(held loser), and adds 「成交日在关联版本有效期外」 to unplanned. Both run one core loop.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from investment_assistant.data.calendar import add_trading_days

DISCIPLINE_V1 = 'discipline-1'     # the logic of v2.8 and before (`evaluate` / `evaluate_v1`)
DISCIPLINE_VERSION = 'discipline-2'
EARLY_TAKE_SESSIONS = 20
NY = ZoneInfo('America/New_York')


def _session(ts) -> date:
    return ts.astimezone(NY).date()


def _splits_of(bars: list[dict[str, Any]]) -> list[tuple[date, Decimal]]:
    return [(b['price_date'], Decimal(str(b['split_factor']))) for b in bars if b['split_factor'] != 1]


def _at_basis(level: Decimal, signal_date: date, day: date, splits: list[tuple[date, Decimal]]) -> Decimal:
    """Express a plan-time level in ``day``'s split basis (for trade-date comparisons)."""
    factor = Decimal(1)
    for split_date, split_factor in splits:
        if signal_date < split_date <= day:
            factor *= split_factor
    return level / factor


def _current_basis(level: Decimal, signal_date: date, splits: list[tuple[date, Decimal]]) -> Decimal:
    """Bars are stored in the current split basis, so bar comparisons use this basis."""
    return _at_basis(level, signal_date, date.max, splits)


def _t1_touched_while_held(bars: list[dict[str, Any]], target1: Decimal, signal_date: date, since: date,
                           through: date, splits: list[tuple[date, Decimal]]) -> bool:
    """Whether T1 traded between the buy and the sell; highs from before the buy never count."""
    target = _current_basis(target1, signal_date, splits)
    return any(since <= b['price_date'] <= through and Decimal(str(b['high'])) >= target for b in bars)


def _core(*, trades: list[dict[str, Any]], plan_for, target_for, exempt_days: dict[str, list[date]],
          loser_days: dict[str, list[date]], bars_by_ticker: dict[str, list[dict[str, Any]]],
          segments: list[dict[str, Any]], session: date) -> dict[str, Any]:
    """The one discipline loop. plan_for(t) -> (plan | None, forced_unplanned); target_for(t, p, sell_ts) -> the
    plan row whose T1 and signal date the early-take test uses; exempt_days / loser_days: per ticker, the days a
    stop fired (a sale within a session of one is executing the stop; a stop day starts the held-loser clock)."""
    segments_by_buy: dict[Any, list[dict[str, Any]]] = {}
    for seg in segments:
        segments_by_buy.setdefault(seg['buy_trade_id'], []).append(seg)
    sell_days_by_ticker = {t: sorted(_session(x['executed_at']) for x in trades
                                     if x['ticker'] == t and x['side'] == 'sell' and x['voided_at'] is None)
                           for t in {x['ticker'] for x in trades}}

    out_trades, counts, immature_counts = [], {'chase': 0, 'early_take': 0, 'held_loser': 0, 'unplanned': 0}, \
        {'early_take': 0, 'held_loser': 0}
    planned_buys = planned_flagged = 0

    for t in (x for x in trades if x['side'] == 'buy' and x['voided_at'] is None):
        ticker, flags, immature = t['ticker'], [], []
        bars = bars_by_ticker.get(ticker, [])
        splits = _splits_of(bars)
        p, forced_unplanned = plan_for(t)
        track = p['kind'] if p else None
        buy_day = _session(t['executed_at'])
        price = Decimal(str(t['price']))
        r: float | None = None

        if p is None or forced_unplanned:
            flags.append('unplanned')
        else:
            planned_buys += 1
            if price > _at_basis(p['entry_high'], p['signal_date'], buy_day, splits):
                flags.append('chase')
            # Actual R over this lot's closed segments, risk priced at the plan stop.
            total_qty = Decimal(0)
            weighted = Decimal(0)
            stop_at_buy = _at_basis(p['stop'], p['signal_date'], buy_day, splits)
            for seg in segments_by_buy.get(t['id'], []):
                entry, exit_ = seg['entry_cost_per_share'], seg['exit_price']
                if entry - stop_at_buy == 0:
                    continue
                weighted += seg['quantity'] * (exit_ - entry) / (entry - stop_at_buy)
                total_qty += seg['quantity']
            r = float(weighted / total_qty) if total_qty else None
            # Early take: sold before T1, not executing a plan_stop, and T1 arrived within 20 sessions.
            for seg in segments_by_buy.get(t['id'], []):
                sell_day = _session(seg['closed_at'])
                tp = target_for(t, p, seg['closed_at'])
                if _t1_touched_while_held(bars, tp['target1'], tp['signal_date'], buy_day, sell_day, splits):
                    continue
                if any(d <= sell_day <= add_trading_days(d, 1) for d in exempt_days.get(ticker, [])):
                    continue  # the sell followed a plan_stop within a session: executing the stop is the plan
                window_end = add_trading_days(sell_day, EARLY_TAKE_SESSIONS)
                if session < window_end:
                    immature.append('early_take')
                    continue
                window = [b for b in bars if sell_day < b['price_date'] <= window_end]
                if any(Decimal(str(b['high'])) >= _current_basis(tp['target1'], tp['signal_date'], splits)
                       for b in window):
                    flags.append('early_take')
            # Held loser: a plan_stop fired on an open lot and no sell followed through the next session.
            sells = sell_days_by_ticker.get(ticker, [])
            for stop_day in sorted(loser_days.get(ticker, [])):
                if stop_day < buy_day:
                    continue
                segs = segments_by_buy.get(t['id'], [])
                if segs and all(_session(s['closed_at']) < stop_day for s in segs):
                    continue  # this lot was already gone
                deadline = add_trading_days(stop_day, 1)
                if session < deadline:
                    immature.append('held_loser')
                    continue
                if not any(stop_day <= s <= deadline for s in sells):
                    flags.append('held_loser')

        for flag in dict.fromkeys(flags):
            counts[flag] += 1
        for flag in dict.fromkeys(immature):
            immature_counts[flag] += 1
        if p is not None and not forced_unplanned and flags:
            planned_flagged += 1
        out_trades.append({'trade_id': str(t['id']), 'ticker': ticker, 'track': track, 'flags': flags,
                           'immature': list(dict.fromkeys(immature)), 'r': r})

    return {'trades': out_trades, 'counts': counts, 'immature': immature_counts, 'planned_buys': planned_buys,
            'compliance_rate': (planned_buys - planned_flagged) / planned_buys if planned_buys else None}


def _plan_stop_days(stop_events: list[dict[str, Any]]) -> dict[str, list[date]]:
    stop_days: dict[str, list[date]] = {}
    for candidate in stop_events:
        if candidate['rule_id'] == 'plan_stop':
            stop_days.setdefault(candidate['ticker'], []).append(candidate['signal_date'])
    return stop_days


def evaluate(*, trades: list[dict[str, Any]], decisions_by_id: dict, plans_by_id: dict,
             bars_by_ticker: dict[str, list[dict[str, Any]]], stop_events: list[dict[str, Any]],
             segments: list[dict[str, Any]], session: date) -> dict[str, Any]:
    """`discipline-1`: each buy's plan is the plan of its decision (unplanned = no decision or no plan)."""
    def plan_for(t):
        decision = decisions_by_id.get(t['decision_id']) if t['decision_id'] else None
        plan = plans_by_id.get(decision['plan_id']) if decision and decision['plan_id'] else None
        return plan, False

    stop_days = _plan_stop_days(stop_events)
    return _core(trades=trades, plan_for=plan_for, target_for=lambda t, p, sold_at: p, exempt_days=stop_days,
                 loser_days=stop_days, bars_by_ticker=bars_by_ticker, segments=segments, session=session)


evaluate_v1 = evaluate          # the v2.8 signature, named for the replay tests of old snapshots


def _touched_days(holding_rows: list[dict[str, Any]], bars_by_ticker: dict[str, list[dict[str, Any]]]
                  ) -> dict[str, list[date]]:
    """Days the effective stop of a daily holding status was traded through (that day's low <= the stop)."""
    lows = {(tk, b['price_date']): Decimal(str(b['low'])) for tk, bars in bars_by_ticker.items() for b in bars}
    days: dict[str, list[date]] = {}
    for row in holding_rows:
        if row.get('stop') is None:
            continue
        low = lows.get((row['ticker'], row['session']))
        if low is not None and low <= Decimal(str(row['stop'])):
            days.setdefault(row['ticker'], []).append(row['session'])
    return {tk: sorted(set(v)) for tk, v in days.items()}


def evaluate_v2(*, trades: list[dict[str, Any]], plans_by_trade: dict, chain_head_by_trade: dict,
                holding_rows: list[dict[str, Any]], legacy_decisions: dict, bars_by_ticker: dict[str, list[dict[str, Any]]],
                segments: list[dict[str, Any]], session: date, plans_by_id: dict | None = None,
                stop_events: list[dict[str, Any]] = ()) -> dict[str, Any]:
    """`discipline-2` (spec D28).

    plans_by_trade: `{trade id: the plan row the fill is linked to}` (only fills with a `plan_id`);
    chain_head_by_trade: `{trade id: [the chain's version plan rows, oldest first]}`, each row optionally with a
    `created_at`: the early-take test uses the newest version created by the sale; legacy_decisions: `{decision
    id: decision}` and plans_by_id: `{plan id: plan}` for fills that predate the link (their plan is the
    decision's plan, no validity test, as before); holding_rows: the daily `holding_status` rows (the held-loser
    clock starts the day a row's effective stop was traded through); stop_events keeps the old `plan_stop`
    candidates as a second source for the early-take exemption.

    Unplanned = no plan link (and none through an old decision), or a fill on a day outside the validity of the
    version it is linked to. An out-of-validity buy is judged by no other flag.
    """
    plans_by_id = plans_by_id or {}

    def plan_for(t):
        linked = plans_by_trade.get(t['id'])
        if linked is not None:
            day = _session(t['executed_at'])
            return linked, not linked['valid_from'] <= day <= linked['valid_until']
        decision = legacy_decisions.get(t['decision_id']) if t.get('decision_id') else None
        legacy = plans_by_id.get(decision['plan_id']) if decision and decision.get('plan_id') else None
        return legacy, False

    def target_for(t, p, sold_at):
        versions = chain_head_by_trade.get(t['id']) or []
        known = [v for v in versions if v.get('created_at') is None or v['created_at'] <= sold_at]
        return known[-1] if known else p

    touched = _touched_days(holding_rows, bars_by_ticker)
    exempt = {tk: sorted({*touched.get(tk, []), *_plan_stop_days(list(stop_events)).get(tk, [])})
              for tk in {*touched, *_plan_stop_days(list(stop_events))}}
    return _core(trades=trades, plan_for=plan_for, target_for=target_for, exempt_days=exempt, loser_days=touched,
                 bars_by_ticker=bars_by_ticker, segments=segments, session=session)
