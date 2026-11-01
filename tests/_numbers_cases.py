"""Shared numeric-convention cases for the frozen snapshot (v2.12 Task 0).

``cases()`` returns ``{key: value}`` with ``key = '<module>.<function>.<case>'``; the
snapshot script (``scripts/make_numbers_snapshot.py``) and the freeze test
(``tests/test_numbers_unchanged.py``) both evaluate the same generator, so the JSON and
the live functions can never drift apart. Bar / plan / trade constructors are imported
from the existing workbench test modules instead of being redefined here.

Every value goes through :func:`normalize` (Decimal → its exact string, dates and
datetimes → ISO text, dataclasses → dicts), so what lands in the JSON round-trips
exactly through ``json.load``. Ids that the real constructors draw from ``uuid4`` are
replaced with fixed uuids, so building the cases twice gives identical output."""
from __future__ import annotations

import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from investment_assistant.config import WorkbenchConfig
from investment_assistant.workbench import discipline, derivation, monthly, plans, regime, rules, tracks
from investment_assistant.workbench.agent import tools as agent_tools

import test_workbench_agent_tools
import test_workbench_derivation
import test_workbench_discipline
import test_workbench_monthly
import test_workbench_paper_fill
import test_workbench_plans
import test_workbench_regime
import test_workbench_rules
import test_workbench_screen
import test_workbench_tracks

PARAMS = WorkbenchConfig()


def normalize(value: Any) -> Any:
    """Decimal → exact string, dates/datetimes → ISO text, dataclasses → dicts, tuples → lists."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return normalize(asdict(value))
    if isinstance(value, dict):
        return {str(k): normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(normalize(v) for v in value)
    return value


def _fixed_uuid(tag: int) -> uuid.UUID:
    return uuid.UUID(f'00000000-0000-0000-0000-{tag:012d}')


def _snapshot(equity: str = '100000', tag: int = 1) -> dict:
    return {'id': _fixed_uuid(tag), 'as_of': date(2026, 10, 20), 'equity': Decimal(equity)}


# ---------------------------------------------------------------- plans

def _plans_cases(out: dict) -> None:
    out['plans.atr14.constant_tr_2'] = plans.atr14(test_workbench_plans._bars(15))
    out['plans.atr14.fourteen_bars_none'] = plans.atr14(test_workbench_plans._bars(14))
    out['plans.atr14.flat_zero'] = plans.atr14(test_workbench_plans._bars(15, flat=True))
    out['plans.rule_levels.ref100_atr3'] = plans.rule_levels(Decimal('100'), Decimal('3'))
    out['plans.rule_levels.half_up_rounding'] = plans.rule_levels(Decimal('10.005'), Decimal('0.01'))
    out['plans.validity.thanksgiving_week'] = plans.validity(date(2026, 11, 25))
    out['plans.validity.regular_week'] = plans.validity(date(2026, 10, 21))
    out['plans.size.risk_bound'] = plans.size(snapshot=_snapshot(tag=11), ref_price=Decimal('100'),
                                              stop=Decimal('94'), entry_high=Decimal('101.5'),
                                              ticker_value=Decimal('0'), total_value=Decimal('0'), params=PARAMS)
    out['plans.size.single_cap'] = plans.size(snapshot=_snapshot('10000', tag=12), ref_price=Decimal('100'),
                                              stop=Decimal('99.9'), entry_high=Decimal('100.1'),
                                              ticker_value=Decimal('0'), total_value=Decimal('0'), params=PARAMS)
    out['plans.size.total_cap_no_room'] = plans.size(snapshot=_snapshot(tag=13), ref_price=Decimal('100'),
                                                     stop=Decimal('94'), entry_high=Decimal('101.5'),
                                                     ticker_value=Decimal('0'), total_value=Decimal('100000'),
                                                     params=PARAMS)
    out['plans.size.no_equity'] = plans.size(snapshot=None, ref_price=Decimal('100'), stop=Decimal('94'),
                                             entry_high=Decimal('101.5'), ticker_value=Decimal('0'),
                                             total_value=Decimal('0'), params=PARAMS)


# ---------------------------------------------------------------- agent.tools

def _indicators_cases(out: dict) -> None:
    bars = test_workbench_agent_tools._bars
    ticker = bars([100 + i for i in range(300)])
    ticker[-1]['volume'] = 3000
    out['agent.tools.indicators.full_300'] = agent_tools.indicators(ticker, bars([500.0] * 300))
    out['agent.tools.indicators.short_120_nulls'] = agent_tools.indicators(bars([100 + i for i in range(120)]),
                                                                           bars([500] * 120))


# ---------------------------------------------------------------- regime

def _regime_cases(out: dict) -> None:
    session = test_workbench_regime.SESSION
    _bars = test_workbench_regime._bars
    out['regime.breadth.eligibility_by_history'] = regime.breadth(
        {'A': _bars(120), 'B': _bars(300, closes=[100] * 299 + [50])}, session)
    out['regime.breadth.excludes_stale_ticker'] = regime.breadth(
        {'STALE': _bars(60, end=session - test_workbench_regime.timedelta(days=1)), 'FRESH': _bars(60)}, session)
    out['regime.breadth.new_high_and_low'] = regime.breadth(
        {'H': _bars(300, closes=list(range(1, 301))), 'L': _bars(300, closes=list(range(300, 0, -1)))}, session)
    spy_flat = _bars(70, closes=[100] * 70)
    out['regime.sector_strength.weak'] = regime.sector_strength(
        _bars(70, closes=list(range(140, 70, -1))), spy_flat, session)
    out['regime.sector_strength.strong'] = regime.sector_strength(
        _bars(70, closes=list(range(70, 140))), spy_flat, session)
    out['regime.sector_strength.mixed'] = regime.sector_strength(
        _bars(70, closes=[90] * 40 + [100] * 30), spy_flat, session)
    out['regime.sector_strength.short_history_none'] = regime.sector_strength(_bars(60), spy_flat, session)


# ---------------------------------------------------------------- screen

def _screen_cases(out: dict) -> None:
    evaluate = test_workbench_screen.evaluate
    make_bars = test_workbench_screen.make_bars
    trigger = make_bars([100.0] * 299 + [110.0], atr_pct=0.08)
    trigger[-1] = {**trigger[-1], 'volume': 3_000_000}
    out['screen.evaluate.trigger_setup_illiquid_skip'] = evaluate(
        {'T': trigger, 'S': make_bars(test_workbench_screen.rising()),
         'A': make_bars([100.0] * 300, volume=499_999.99), 'X': make_bars([100.0] * 150)})
    add_trading_days = test_workbench_screen.add_trading_days
    bars = make_bars(test_workbench_screen.rising())
    out['screen.evaluate.earnings_window'] = evaluate(
        {'E0': bars, 'E5': bars, 'E6': bars, 'UN': bars},
        earnings_by_ticker={'E0': {'earnings_date': test_workbench_screen.SESSION, 'status': 'known'},
                            'E5': {'earnings_date': add_trading_days(test_workbench_screen.SESSION, 5),
                                   'status': 'known'},
                            'E6': {'earnings_date': add_trading_days(test_workbench_screen.SESSION, 6),
                                   'status': 'known'},
                            'UN': {'earnings_date': None, 'status': 'unknown'}})


# ---------------------------------------------------------------- rules

def _rules_cases(out: dict) -> None:
    series = test_workbench_rules.series
    T = test_workbench_rules.T

    def run(bars, filings=(), position=None, plan_state=None):
        return rules.evaluate('NVDA', T, bars, list(filings), position, plan_state=plan_state)

    out['rules.precondition.ok_none'] = rules.precondition(series([100] * 250), T)
    out['rules.precondition.insufficient_history'] = rules.precondition(series([100] * 150), T)
    out['rules.precondition.missing_signal_bar'] = rules.precondition(
        series([100] * 250, end=test_workbench_rules.add_trading_days(T, -1)), T)
    anomalous = series([100] * 250)
    for b in anomalous[-5:]:
        b['adj_close'] = b['close'] * 0.5
    out['rules.precondition.adjustment_anomaly'] = rules.precondition(anomalous, T)

    out['rules.evaluate.trend_reclaim'] = run(series(test_workbench_rules.uptrend_with_dip(3)))
    out['rules.evaluate.volume_breakout'] = run(test_workbench_rules.breakout(1.5))
    out['rules.evaluate.earnings_gap_up'] = run(
        test_workbench_rules.gap_bars(103, 104),
        filings=[test_workbench_rules.earnings(test_workbench_rules.ny(2026, 10, 21, 16, 20))])
    out['rules.evaluate.trend_break'] = run(series([100 + i * 0.1 for i in range(249)] + [100]))
    out['rules.evaluate.earnings_gap_down'] = run(
        test_workbench_rules.gap_bars(97, 96.5),
        filings=[test_workbench_rules.earnings(test_workbench_rules.ny(2026, 10, 22, 6, 0))])
    out['rules.evaluate.position_drawdown'] = run(series([100.0] * 249 + [91.0]),
                                                  position=test_workbench_rules.held(100))
    out['rules.evaluate.new_filing'] = run(series([100] * 10),
                                           filings=[test_workbench_rules.earnings(test_workbench_rules.ny(2026, 10, 22, 8))])
    wobbly = series([100 + (1 if i % 2 else -1) for i in range(249)] + [115])
    out['rules.evaluate.abnormal_move'] = run(wobbly)
    out['rules.evaluate.plan_stop'] = run(series([100] * 249 + [92]), position=test_workbench_rules.held(100),
                                          plan_state=test_workbench_rules.plan_state(stop=Decimal('95')))
    out['rules.evaluate.plan_target'] = run(series([100] * 249 + [113]), position=test_workbench_rules.held(100),
                                            plan_state=test_workbench_rules.plan_state())


# ---------------------------------------------------------------- derivation

def _derivation_cases(out: dict) -> None:
    bars = test_workbench_derivation._bars()
    snapshot = {'id': _fixed_uuid(21), 'as_of': date(2026, 10, 20), 'equity': Decimal('100000')}
    plan = plans.build_rule_plan(ticker='AAA', signal_date=test_workbench_derivation.SIGNAL, bars=bars,
                                 earnings_row=None, snapshot=snapshot,
                                 exposure=test_workbench_derivation._exposure(), params=PARAMS)
    plan['id'] = _fixed_uuid(22)
    rows = test_workbench_derivation._rows(bars, None, snapshot)
    out['derivation.derive_plan.rule_plan_worked_example'] = derivation.derive_plan(plan, rows)


# ---------------------------------------------------------------- paper_fill

def _paper_fill_cases(out: dict) -> None:
    D1 = test_workbench_paper_fill.D1
    first = {'in_zone': test_workbench_paper_fill.bar(D1),
             'gap_up': test_workbench_paper_fill.bar(D1, 105, 106, 101, 101),
             'gap_down': test_workbench_paper_fill.bar(D1, 96, 103, 95, 101)}
    for kind in ('zone_limit', 'breakout_stop', 'next_open'):
        for case, day_bar in first.items():
            out[f'paper_fill.settle_day.{kind}.{case}'] = test_workbench_paper_fill.settle(
                D1, p=test_workbench_paper_fill.plan(kind), bars={'NVDA': day_bar})


# ---------------------------------------------------------------- monthly

def _monthly_cases(out: dict) -> None:
    UTC = test_workbench_monthly.UTC

    def snap(as_of, equity, tag):
        return {'id': _fixed_uuid(tag), 'as_of': as_of, 'equity': Decimal(equity), 'supersedes_id': None,
                'recorded_at': datetime(2026, 11, 1, tzinfo=UTC)}

    def flow(day, amount, voided=False):
        return {'flow_date': day, 'amount': Decimal(amount), 'voided_at': date(2026, 11, 1) if voided else None}

    out['monthly.month_return.with_flows'] = monthly.month_return(
        date(2026, 10, 1), [snap(date(2026, 9, 30), 100000, 31), snap(date(2026, 10, 31), 112000, 32)],
        [flow(date(2026, 10, 15), 5000), flow(date(2026, 11, 2), 999)])
    out['monthly.month_return.missing_snapshot'] = monthly.month_return(
        date(2026, 10, 1), [snap(date(2026, 10, 31), 112000, 33)], [])
    out['monthly.month_return.superseded_snapshot'] = monthly.month_return(
        date(2026, 10, 1),
        [snap(date(2026, 9, 30), 100000, 34), snap(date(2026, 10, 31), 112000, 35),
         {**snap(date(2026, 10, 31), 110000, 36), 'supersedes_id': _fixed_uuid(35)}], [])
    out['monthly.month_return_v2.ledger_endpoints'] = monthly.month_return_v2(
        date(2026, 10, 1),
        equity_rows=[{'session': date(2026, 10, 30), 'equity': Decimal('112000'),
                      'computed_at': datetime(2026, 11, 1, tzinfo=UTC)},
                     {'session': date(2026, 9, 30), 'equity': Decimal('100000'),
                      'computed_at': datetime(2026, 10, 1, tzinfo=UTC)},
                     {'session': date(2026, 10, 30), 'equity': Decimal('90000'),
                      'computed_at': datetime(2026, 10, 31, tzinfo=UTC)}],
        snapshots=[], flows=[flow(date(2026, 10, 15), 5000), flow(date(2026, 10, 16), 999, voided=True)],
        opened=True)
    out['monthly.month_return_v2.snapshot_fallback'] = monthly.month_return_v2(
        date(2026, 10, 1), equity_rows=[],
        snapshots=[snap(date(2026, 9, 30), 100000, 37), snap(date(2026, 10, 31), 110000, 38)],
        flows=[flow(date(2026, 10, 15), 5000)], opened=False)
    out['monthly.tier.boundaries'] = {str(r): monthly.tier(r, PARAMS.return_tiers)
                                      for r in (0.2001, 0.20, 0.10, 0.0999, 0.0, -0.0001)}
    out['monthly.review_flag.loss_and_underperform'] = monthly.review_flag(
        [{'return': r, 'spy_return': s} for r, s in zip((-0.05, -0.02, -0.01), (0.01, 0.01, 0.01))])
    out['monthly.review_flag.beating_spy_is_ok'] = monthly.review_flag(
        [{'return': r, 'spy_return': s} for r, s in zip((-0.05, -0.02, -0.01), (-0.10, -0.10, -0.10))])
    out['monthly.sensitivity.drawdown'] = monthly.sensitivity([2, -1, -1, 3])


# ---------------------------------------------------------------- discipline

def _discipline_cases(out: dict) -> None:
    out['discipline.evaluate.chase_above_entry_high'] = test_workbench_discipline.evaluate(
        [test_workbench_discipline.trade('b1', price=103)])
    out['discipline.evaluate_v2.unplanned_buy'] = test_workbench_discipline.evaluate_v2(
        [test_workbench_discipline.v2_trade('b1', plan_id=None)])


# ---------------------------------------------------------------- tracks

def _tracks_cases(out: dict) -> None:
    S = test_workbench_tracks.S
    timedelta = test_workbench_tracks.timedelta
    tail = {'splits': [], 'session': S + timedelta(days=5), 'unit_valid_until': S + timedelta(days=3)}
    plan = test_workbench_tracks._plan_row()
    buy = {**test_workbench_tracks._buy('b1'), 'plan_id': 'p1'}
    out['tracks.actual_track_v2.closed_fill_basis'] = tracks.actual_track_v2(
        plans_by_trade={'b1': plan}, buys=[buy], segments=[test_workbench_tracks._seg('b1')],
        open_lots=[], last_close=None, **tail)
    out['tracks.actual_track_v2.open_lot'] = tracks.actual_track_v2(
        plans_by_trade={'b1': plan}, buys=[buy], segments=[], open_lots=[test_workbench_tracks._lot('b1')],
        last_close=Decimal('105'), **tail)
    out['tracks.actual_track_v2.not_executed_after_validity'] = tracks.actual_track_v2(
        plans_by_trade={}, buys=[], segments=[], open_lots=[], last_close=None, **tail)
    out['tracks.actual_track_v2.pending_before_valid_until'] = tracks.actual_track_v2(
        plans_by_trade={}, buys=[], segments=[], open_lots=[], last_close=None, session=S + timedelta(days=2),
        unit_valid_until=S + timedelta(days=3), splits=[])
    out['tracks.actual_track_v2.no_plan'] = tracks.actual_track_v2(
        plans_by_trade={}, buys=[test_workbench_tracks._buy('b1')], segments=[test_workbench_tracks._seg('b1')],
        open_lots=[], last_close=None, **tail)


def cases() -> dict[str, Any]:
    """The frozen conventions as ``{key: normalized value}``; building it twice gives identical output."""
    out: dict[str, Any] = {}
    for group in (_plans_cases, _indicators_cases, _regime_cases, _screen_cases, _rules_cases,
                  _derivation_cases, _paper_fill_cases, _monthly_cases, _discipline_cases, _tracks_cases):
        group(out)
    return {key: normalize(value) for key, value in out.items()}
