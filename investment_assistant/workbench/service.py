"""Transactions and use cases for the US workbench. SQL lives in repository.py."""
from __future__ import annotations

import copy
import os
import uuid
import time as _time_module
from contextlib import ExitStack, nullcontext
from decimal import Decimal
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any, Callable

from investment_assistant.data.calendar import is_trading_day
from investment_assistant.workbench.accounts import REAL_ACCOUNT_ID
from investment_assistant.db import connect, list_watchlist_items
from investment_assistant.workbench import (account, bars, briefs, chart, collect, earnings, exits, families, ledger, monthly,
                                               plans, positions, repository, review, rules, synthesis)
from investment_assistant.workbench.agent import graph as agent_graph
from investment_assistant.workbench.agent import growth_check as agent_growth_check
from investment_assistant.workbench.agent import model as agent_model
from investment_assistant.workbench.agent import tools as agent_tools
from investment_assistant.workbench import macro as macro_module
from investment_assistant.workbench import pool as pool_module
from investment_assistant.workbench import regime as regime_module
from investment_assistant.workbench import screen as screen_module
from investment_assistant.workbench import screening
from investment_assistant.workbench import stages as stages_module
from investment_assistant.workbench import universe as universe_module
from investment_assistant.workbench import discipline as _discipline_module
from investment_assistant.workbench import plan_shadow as _plan_shadow_module
from investment_assistant.workbench import review_digest
from investment_assistant.workbench import execution as execution_module
from investment_assistant.workbench import timeline
from investment_assistant.workbench import tracks as _tracks_module

from investment_assistant.core.constants import (AGENT_DEADLINE_S as AGENT_DEADLINE_S, BENCHMARK as BENCHMARK,
                                                 CONNECTION_LOST as CONNECTION_LOST, HISTORY_DAYS as HISTORY_DAYS,
                                                 NEWS_LOOKBACK_DAYS as NEWS_LOOKBACK_DAYS, NY as NY,
                                                 OPEN_UNIT_DAYS as OPEN_UNIT_DAYS, SEC_EVIDENCE_DIR as SEC_EVIDENCE_DIR)
from investment_assistant.core import tx as core_tx
from investment_assistant.core.tx import (Deps as Deps, Gone as Gone, Unavailable as Unavailable,
                                          _jsonable as _jsonable, _read_only as _read_only, _tx as _tx,
                                          connection as connection, read_only_tx as read_only_tx)

ETF_TICKERS = pool_module.SECTOR_ETFS
AGENT_MAX_ATTEMPTS = 3      # a failed unit runs at most this many times per batch re-run cycle
FILING_LOOKBACK_DAYS = 10


DECISIONS_RETIRED = 'decisions_retired: v2.9 起不再记录决定，采纳由成交推导；可在计划上写备注'


def _book(conn, ticker: str, *, account_id=REAL_ACCOUNT_ID) -> dict[str, Any]:
    return positions.build(repository.list_trades(conn, ticker, account_id=account_id), splits=repository.split_events(conn, ticker))


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=NY)


def _filing_events(conn, ticker: str, session: date, cutoff_at: datetime,
                   since: date | None = None) -> tuple[list, list]:
    """(rule inputs, filings rows). SEC 8-K listings not in the filings table still count as new filings.
    ``since`` (default: FILING_LOOKBACK_DAYS before the session) is the first filing date read."""
    since = session - timedelta(days=FILING_LOOKBACK_DAYS) if since is None else since
    rows = repository.read_filings(conn, ticker, since=since, cutoff_at=cutoff_at)
    events = [{**r, 'id': f"filing:{r['id']}"} for r in rows]
    known = {r['accession_number'].replace('-', '') for r in rows}
    for item in repository.read_news(conn, ticker, cutoff_at=cutoff_at, since=_midnight(since)):
        if item['source'] != 'sec_8k' or any(acc in item['url'] for acc in known):
            continue
        seen = item['published_at'] or item['fetched_at']
        events.append({'id': f"news:{item['id']}", 'form': '8-K', 'items': None,
                       'filing_date': seen.astimezone(NY).date(), 'accepted_at': item['published_at']})
    return events, rows


def exposure(conn, session: date, *, cutoff_at: datetime, ticker: str) -> dict[str, Decimal]:
    """Market value of the ticker's open position and of all open positions, at each latest close."""
    from decimal import Decimal

    ticker_value = Decimal('0')
    total_value = Decimal('0')
    for held in sorted({t['ticker'] for t in repository.list_trades(conn) if t['voided_at'] is None}):
        pos = _book(conn, held)['position']
        if pos is None:
            continue
        latest = repository.read_bars(conn, held, session, cutoff_at=cutoff_at, limit=1)
        value = Decimal(str(pos['quantity'])) * Decimal(str(latest[-1]['close'])) if latest else Decimal('0')
        total_value += value
        if held == ticker.upper():
            ticker_value = value
    return {'ticker_value': ticker_value, 'total_value': total_value}


def _position_plan_state(conn, ticker: str, book: dict[str, Any], ticker_bars: list[dict[str, Any]],
                         session: date, *, now: datetime) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(plan, active_stop state) for a held ticker; both None when the position has no plan.

    v2.9 D20 / D22 (migration 029 and the timeline tables present): a position that belongs to a holding / exiting
    chain follows the chain's latest version (a stop lifted by a holding-phase revision takes effect); otherwise the
    plan its fills follow — ``trades.plan_id`` first, then the old decision route (``plan_review._plan_of_buy``). Fills
    are read as the cutoff knew them (R11). Before 029 only the decision route exists, as before."""
    position = book['position']
    if position is None:
        return None, None
    if repository.timeline_ready(conn) and repository.executable_ready(conn):
        from investment_assistant.workbench import plan_review     # imports this module

        ctx = plan_review.ticker_context(conn, ticker, now=now)
        position = ctx['book']['position']
        if position is None:
            return None, None
        held = [i for i in plan_review.ticker_chain_items(conn, ticker, session=session, now=now, context=ctx)
                if i['state']['state'] in ('holding', 'exiting')]
        if held:
            plan = max(held, key=lambda i: i['chain']['signal_date'])['chain']['head_plan']
        else:
            by_id = {t['id']: t for t in ctx['trades']}
            plans_by_trade = {lot['buy_trade_id']: found for lot in ctx['book']['open']
                              if (found := plan_review._plan_of_buy(conn, by_id[lot['buy_trade_id']])) is not None}
            plan = exits.position_plan(ctx['book']['open'], ctx['trades'], plans_by_trade)
        avg_cost = position['avg_cost']
    else:
        trades = repository.list_trades(conn, ticker)
        plans_by_trade = repository.plan_for_buy_trades(conn, [lot['buy_trade_id'] for lot in book['open']])
        plan = exits.position_plan(book['open'], trades, plans_by_trade)
        avg_cost = position['avg_cost']
    if plan is None:
        return None, None
    opened = position['opened_at'].astimezone(NY).date()
    bars_since = [b for b in ticker_bars if b['price_date'] >= opened]
    state = exits.active_stop(plan, bars_since, avg_cost=avg_cost,
                              splits=repository.split_events(conn, ticker)) if bars_since else {
        'stop': plan['stop'], 't1_reached': False, 't1_reached_on': None, 'mode': 'initial', 'target1': plan['target1']}
    return plan, state


def plan_context(conn, ticker: str, session: date, *, now: datetime,
                 screen_ref: dict[str, Any] | None = None) -> dict[str, Any]:
    """Market context frozen into a plan: tier and source at the signal date, regime, sector,
    earnings, and the screen pick that put the ticker in the pool."""
    entry = repository.pool_entry_at(conn, ticker, session, cutoff_at=now)
    tier = entry['tier'] if entry and entry['tier'] in ('core', 'extended') else 'unknown'
    regime_row = repository.regime_for(conn, session, cutoff_at=now)
    sector = None
    if entry and entry.get('sector_etf'):
        snap_sector = (regime_row or {}).get('sectors', {}).get(entry['sector_etf'])
        sector = ({'etf': entry['sector_etf'], 'rs20': snap_sector['rs20'], 'rs60': snap_sector['rs60'],
                   'state': snap_sector['state']} if snap_sector is not None
                  else {'etf': entry['sector_etf'], 'rs20': None, 'rs60': None, 'state': None})
    earnings = repository.latest_earnings(conn, ticker, cutoff_at=now)
    next_earnings = (earnings['earnings_date'] if earnings and earnings['status'] == 'known'
                     and earnings['earnings_date'] and earnings['earnings_date'] >= session else None)
    return {'context_version': plans.CONTEXT_VERSION, 'pool_tier': tier,
            'source': entry.get('source') if entry else None,
            'screen': screen_ref,
            'regime': ({'id': str(regime_row['id']), 'label': regime_row['label']}
                       if regime_row is not None else None),
            'sector': sector, 'next_earnings': next_earnings}


def sizing_equity(conn, session: date, *, now: datetime) -> dict | None:
    """A real opened ledger takes priority; a not-yet-valued ledger keeps the existing snapshot fallback."""
    if repository.accounts_ready(conn):
        account_ = repository.account_by_id(conn, REAL_ACCOUNT_ID)
        rows = repository.equity_days(conn, REAL_ACCOUNT_ID, to_session=session, cutoff_at=now)
        if account_ and account_['opened_on'] and account_['opened_on'] <= session and rows:
            row = rows[-1]
            return {'id': row['id'], 'as_of': row['session'], 'equity': row['equity'], 'equity_source': 'ledger'}
    snapshot = repository.snapshot_at(conn, session, cutoff_at=now)
    return {**snapshot, 'equity_source': 'snapshot'} if snapshot else None


def mark_sizing_source(plan: dict, source: dict | None) -> None:
    if 'sizing' in plan:
        plan['sizing']['equity_source'] = source.get('equity_source', 'snapshot') if source else 'snapshot'


def _generate_rule_plan(conn, ticker: str, session: date, ticker_bars: list[dict[str, Any]], summary: dict[str, Any],
                        *, now: datetime, active_chain: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Build and store the rule baseline (`plan-2`, v2.9 §5.4) for a fresh buy unit; record skip/conflict in the summary.

    ``active_chain`` (``{'ticker', 'signal_date'}``) marks a unit that joined an active chain: the plan row keeps
    ``inputs.active_chain``. Returns None when the plan was skipped, else ``{'status': inserted|existing|conflict,
    'id': plan_id, 'plan_status': ok|invalid}`` for the stage accounting (v2.4 §4); ``plan_status`` is the stored
    plan row's own status. The summary is written exactly as before."""
    from investment_assistant.config import load_config

    entry = repository.pool_entry_at(conn, ticker, session, cutoff_at=now)
    screen_ref = (screening.screen_ref(conn, ticker, session)
                  if entry is not None and entry.get('source') == 'screen' else None)
    equity_source = sizing_equity(conn, session, now=now)
    plan = plans.build_rule_plan(
        ticker=ticker, signal_date=session, bars=ticker_bars,
        earnings_row=repository.latest_earnings(conn, ticker, cutoff_at=now),
        snapshot=equity_source,
        exposure=exposure(conn, session, cutoff_at=now, ticker=ticker),
        params=load_config().workbench,
        context=plan_context(conn, ticker, session, now=now, screen_ref=screen_ref),
        version=plans.PLAN_VERSION, active_chain=active_chain)
    mark_sizing_source(plan, equity_source)
    if 'skip' in plan:
        summary['plans'][ticker] = f"skip: {plan['skip']}"
        return None
    out = repository.store_plan(conn, plan)
    summary['plans'][ticker] = out['status']
    if out['status'] == 'conflict':
        summary['conflicts'].append({'ticker': ticker, 'rule_id': 'rule_plan', 'id': str(out['record']['id'])})
    return {'status': out['status'], 'id': out['record']['id'], 'plan_status': out['record']['status']}


def _note_signals(acc: stages_module.StageAcc, ticker: str, noted: dict[str, Any]) -> None:
    """Fold one committed ticker's candidates, rule skips and unit action into the signals stage."""
    for rule_id, out in noted['candidates']:
        if out['status'] == 'conflict':
            acc.degrade('conflict', f'{ticker}:{rule_id}')
            continue
        acc.output()
        acc.ref('inserted' if out['status'] == 'inserted' else 'existing', out['id'])
    for reason in noted['skips']:
        acc.skip(reason, ticker)
    if 'action' in noted:
        units = acc.detail.setdefault('units', {})
        units[noted['action']] = units.get(noted['action'], 0) + 1


def _note_plan(acc: stages_module.StageAcc, ticker: str, planned: dict[str, Any] | None,
               summary_text: str) -> None:
    """Fold one rule-plan attempt into the plan stage: a skip keeps its reason, a stored plan its id."""
    if planned is None:
        acc.skip(summary_text.removeprefix('skip: '), ticker)
        return
    acc.output()
    acc.ref('inserted' if planned['status'] == 'inserted' else 'existing', planned['id'])
    if planned.get('plan_status') in ('ok', 'invalid'):      # detail.status 与 outputs 里的计划一一对应
        counts = acc.detail.setdefault('status', {'ok': 0, 'invalid': 0})
        counts[planned['plan_status']] += 1
    if planned['status'] == 'conflict':
        acc.degrade('conflict', ticker)


RETRIGGER_STATES = ('entry', 'holding', 'exiting')


def _empty_retriggers() -> dict[str, Any]:
    return {'count': 0, 'by_state': {state: 0 for state in RETRIGGER_STATES}, 'tickers': []}


def _note_retrigger(acc: stages_module.StageAcc, ticker: str, row: dict[str, Any]) -> None:
    """Fold one committed merge (a stored ``chain_retriggers`` row, never a locally computed value) into the plan
    stage's ``detail.retriggers = {count, by_state, tickers}`` (v2.9 spec §4.2)."""
    retriggers = acc.detail.setdefault('retriggers', _empty_retriggers())
    retriggers['count'] += 1
    retriggers['by_state'][row['chain_state']] += 1
    if ticker not in retriggers['tickers']:
        retriggers['tickers'].append(ticker)


def _open_or_merge(conn, ticker: str, session: date, *, now: datetime) -> dict[str, Any] | None:
    """The `plan` node's chain decision for one buy unit (v2.9 spec §4.2, D3), under the transaction-level lock
    ``chain-open:<ticker>``: the ticker's active chain on S (R5: an entry chain on its last valid day renews), or
    None when the unit opens a chain of its own. A unit already merged on an earlier run of the session stays
    merged (its stored row decides), and a unit never merges into its own or a later chain."""
    from investment_assistant.workbench import plan_review     # imports this module

    repository.lock(conn, f'chain-open:{ticker}')
    stored = repository.retrigger_for_unit(conn, ticker, session)
    if stored is not None:
        return {'row': stored}
    found = plan_review.active_chain_of(conn, ticker, session=session, now=now, before=session)
    return {'found': found} if found is not None else None


def _check_derivations(acc: stages_module.StageAcc, plan_ids: list[Any], summary: dict[str, Any]) -> None:
    """Re-derive this stage's plans read-only and fold the verdicts into the stage (v2.4 §8).

    Counts go to ``acc.detail['derivation']``; a ``mismatch`` / ``incomplete`` plan, or a check that raises,
    degrades the stage and adds ``derivation_mismatch: ...`` to ``summary['errors']``. Nothing else changes:
    plans, pushes and every other stage are untouched, and no failure here escapes."""
    from investment_assistant.workbench import derivation_view     # derivation_view imports this module

    try:
        result = derivation_view.check_plans([str(i) for i in dict.fromkeys(plan_ids)])
        bad = [item for item in result['items'] if item['status'] in ('mismatch', 'incomplete')]
        errors = [f"derivation_mismatch: {item['ticker'] or '*'} {item['plan_id']}" for item in bad]
        counts = f"mismatch {result['mismatch']}, incomplete {result['incomplete']}"
        acc.detail['derivation'] = result
    except Exception as exc:    # noqa: BLE001 - the check is accounting; it must never fail the batch
        reason = families.sanitize_reason(exc)
        acc.degrade('derivation_check_error', reason)
        summary['errors'].append(f'derivation_mismatch: * {reason}')
        return
    if bad:
        acc.degrade('derivation_mismatch', counts)
        summary['errors'].extend(errors)


def _sync_pool(conn, pool_: pool_module.Pool, session: date, now: datetime) -> list[dict[str, Any]]:
    """Append tier changes against the newest recorded state, effective this session."""
    changes = pool_module.tier_changes(pool_, repository.pool_latest(conn))
    repository.insert_pool_tiers(conn, changes, session=session, now=now)
    return changes


def _unit_is_extended(conn, ticker: str, signal_date: date, *, held: bool, now: datetime) -> bool:
    """Extended-pool unit: the signal-date tier is extended, the ticker is not held, and a screen
    pick with a valid rule plan counts as a main-list unit (spec §7.2)."""
    if held:
        return False
    entry = repository.pool_entry_at(conn, ticker, signal_date, cutoff_at=now)
    if entry is None or entry['tier'] != 'extended':
        return False
    if entry.get('source') == 'screen':
        plan = repository.rule_plan(conn, ticker, signal_date)
        if plan is not None and plan['status'] == 'ok':
            return False
    return True


def _breadth_tickers(manual: pool_module.Pool, universe_: Any) -> tuple[list[str], list[str]]:
    """Breadth sample and notes: the screening universe ∪ the manual pool, deduped.

    A missing universe falls back to the manual pool alone and flags it. """
    if universe_ is not None:
        merged = sorted({*manual.tickers, *universe_.tickers})
        return merged, []
    return sorted(manual.tickers), ['regime_universe_missing']


# ---------------------------------------------------------------- the daily stages (v2.8 §4.3)
#
# ``run_batch`` used to be one long function. v2.8 cuts it at the stage borders, line for line: judgements, writes and
# the ``summary`` keys are the ones of v2.7. Each ``stage_*`` takes the graph state (JSON-able values only), the
# node context and the node's stage accumulator (``None`` when nobody records stage evidence) and returns the state
# increment; it records whole-stage skips and failures on ``ctx.recorder`` itself, as the old inline code did.
# ``workflow.py`` wraps them into graph nodes (timing, retry, evidence row); ``run_batch`` below calls them in order
# for the CLI and the v2.7 tests.

def _pool_state(pool_: pool_module.Pool) -> dict[str, Any]:
    return {'core': list(pool_.core), 'extended': list(pool_.extended), 'sectors': dict(pool_.sectors),
            'sources': dict(pool_.sources)}


def _pool_from_state(data: dict[str, Any]) -> pool_module.Pool:
    return pool_module.Pool(core=tuple(data.get('core') or ()), extended=tuple(data.get('extended') or ()),
                            sectors=dict(data.get('sectors') or {}), sources=dict(data.get('sources') or {}))


def _moment(state: dict[str, Any]) -> tuple[datetime, date]:
    """The run's cutoff (every computation's ``now``) and session from the state's ISO strings."""
    return datetime.fromisoformat(state['cutoff_at']), date.fromisoformat(state['session'])


def _stage_skip(ctx, stage_id: str, reason: str) -> None:
    if ctx.recorder is not None:
        ctx.recorder.skip(stage_id, reason)


def _stage_finish(ctx, stage_id: str, failed: str | None = None) -> None:
    if ctx.recorder is not None:
        ctx.recorder.finish(stage_id, failed=failed)


def agent_window(state: dict[str, Any], ctx) -> tuple[float, Callable[[], float]]:
    """``(started, clock)`` for the agent budget (v2.8 §5.8): the window opens when the ``agent`` node starts.

    The state keeps that moment as wall time (a monotonic clock does not cross processes); here it becomes
    ``clock() - (wall_now() - agent_started_at)``. The v2.7 ``run_batch`` shell keeps its own batch-start window."""
    clock = ctx.clock
    if ctx.window_start is not None:
        return ctx.window_start, clock
    opened = state.get('agent_started_at')
    if not opened:
        return clock(), clock
    opened_at = datetime.fromisoformat(opened)
    if ctx.resumed and ctx.execution_started is not None:
        opened_at = max(opened_at, ctx.execution_started)     # D24: a resume counts from the resumed execution
    elapsed = (_wall(ctx)() - opened_at).total_seconds()
    return clock() - max(0.0, elapsed), clock


class _Collector:
    """Bars, news and the earnings date of one ticker; failures land in the summary (v2.7 ``collect_ticker``)."""

    def __init__(self, *, now: datetime, session: date, ny_date: date, deps: Deps, summary: dict[str, Any],
                 fetched: set[str], bars_tally=None, news_tally=None, earnings_tally=None,
                 drop_after: datetime | None = None):
        self.now, self.session, self.ny_date, self.deps, self.summary = now, session, ny_date, deps, summary
        self.fetched = fetched
        self.bars_tally, self.news_tally, self.earnings_tally = bars_tally, news_tally, earnings_tally
        self.drop_after = drop_after
        self.earnings_ok = self.earnings_failed = 0
        self.news_dropped = 0

    def ticker(self, ticker: str, *, earnings_date: bool = True, acc: stages_module.StageAcc | None = None) -> None:
        summary, now, deps = self.summary, self.now, self.deps
        entry: dict[str, Any] = {'bars': None, 'skipped_bars': [], 'news': None, 'candidates': [], 'skips': [],
                                 'error': None}
        problems = []
        try:
            if ticker in self.fetched:
                entry['bars'] = 'collected_in_screen'   # news and earnings still collect below
            else:
                out = collect.bars_one(ticker, session=self.session, now=now, deps=deps,
                                       lookback_days=HISTORY_DAYS, tally=self.bars_tally)
                entry['bars'] = out['counts']
                entry['skipped_bars'] = [d.isoformat() for d in out['skipped']]
        except Unavailable:
            raise
        except Exception as exc:
            problems.append(f'bars: {exc}')
            summary['sources']['bars'].append(ticker)
        try:
            entry['news'] = collect.news_one(ticker, now=now, deps=deps, drop_after=self.drop_after,
                                             tally=self.news_tally)
            self.news_dropped += entry['news'].get('after_cutoff_dropped', 0)
        except Unavailable:
            raise
        except Exception as exc:
            problems.append(f'news: {exc}')
            summary['sources']['news'].append(ticker)
        if earnings_date:
            try:
                row, fetched = earnings.fetch_next_with_outcome(ticker, fetcher=deps.earnings_fetcher,
                                                                today=self.ny_date)
                with core_tx._tx() as conn:
                    summary['earnings'][ticker] = repository.store_earnings(conn, ticker, row, now=now)['status']
                if self.earnings_tally is not None:
                    self.earnings_tally.record(ticker, fetched, latest=row.get('earnings_date'))
                self.earnings_ok += 1
            except Unavailable:
                raise
            except Exception as exc:  # 采集失败不写库，也不冒充无财报
                summary['earnings'][ticker] = f'error: {exc}'
                summary['errors'].append(f'{ticker} earnings: {exc}')
                summary['sources']['earnings'].append(ticker)
                if self.earnings_tally is not None:
                    self.earnings_tally.record(ticker, exc)
                if acc is not None:
                    acc.fail(f'earnings:{ticker}', exc)
                self.earnings_failed += 1
        if problems:
            entry['error'] = '; '.join(problems)
            summary['errors'].extend(f'{ticker} {p}' for p in problems)
        summary['tickers'][ticker] = entry


def stage_collect(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``collect`` node (v2.8 §4.5): SPY and the sector ETFs, the Nasdaq-100 range (bars and earnings dates of the
    members outside the manual pool, ``screening.collect_range``), then the pool, off-pool holdings and active chains
    (bars, news, earnings date), their SEC listings, FRED. The four family locks are held over the whole stage."""
    from investment_assistant.workbench import plan_review     # imports this module

    deps, clock = ctx.deps, ctx.clock
    now, session = _moment(state)
    ny_date = date.fromisoformat(state['ny_date'])
    run_id, record_trigger, job = state.get('run_id'), ctx.record_trigger, ctx.job
    explicit = bool(state.get('explicit'))
    drop_after = now if ctx.resumed else None       # a resumed run never reads past its own cutoff (§5.5)
    # The first transaction only reads the manual pool and holdings: the screen node runs between them and the
    # merged pool's tier sync.
    with core_tx._tx() as conn:
        pool = (_pool_from_state(state['pool']) if explicit
                else pool_module.from_watchlist(list_watchlist_items(conn)))
        watchlist = list(pool.tickers)
        # Holdings join the batch even off the pool: plan stops and earnings reminders need their data.
        held = [t for t in sorted({r['ticker'] for r in repository.list_trades(conn) if r['voided_at'] is None})
                if t not in watchlist]
        if repository.accounts_ready(conn):
            held = sorted(set(held) | (repository.paper_tickers(conn) - set(watchlist)))
        ready = repository.timeline_ready(conn) if state.get('timeline_ready') is None else state['timeline_ready']
        growth = repository.growth_ready(conn) if state.get('growth_ready') is None else state['growth_ready']
    summary: dict[str, Any] = {
        **(state.get('summary') or {}),
        'session': session.isoformat(), 'cutoff_at': now.isoformat(), 'tickers': {}, 'benchmark': {},
        'filings': {}, 'earnings': {}, 'plans': {}, 'conflicts': [], 'errors': [], 'briefs': {},
        'screen': None,
        'pool': {'core': len(pool.core), 'extended': len(pool.extended), 'holdings': len(held),
                 'screen': None, 'changes': []},
        'etfs': {}, 'macro': {}, 'regime': None,
        'sources': {'bars': [], 'news': [], 'earnings': [], 'sec': [], 'etf_bars': [], 'fred': [],
                    'regime': [], 'agent': []},
        'timings': {}}

    # v2.3 §6.2 / v2.4 §6.2: per-family outcome accounting, fed next to every collect call. record_trigger holds the
    # locks and writes data_fetch_runs; a recorder alone still builds the tallies for the node's counts — no locks,
    # no rows; with neither set the path stays exactly as before (no tallies at all).
    tallies = ({'bars': collect.Tally('bars', job),
                'news': collect.Tally('news', job),
                'sec_filings': collect.Tally('sec_filings', job),
                'fred': collect.Tally('fred', job, macro_module.MACRO_SERIES)}
               if record_trigger is not None or acc is not None else None)
    if tallies is not None and not explicit:   # no screen node -> no universe row
        tallies['universe'] = collect.Tally('universe', job, ('nasdaq100',))
    bars_tally = tallies['bars'] if tallies is not None else None
    news_tally = tallies['news'] if tallies is not None else None
    sec_tally = tallies['sec_filings'] if tallies is not None else None
    fred_tally = tallies['fred'] if tallies is not None else None

    t_collect = clock()
    # I07: the rows' timestamps are real wall clock (injectable), not the batch's cutoff ``now``; captured from the
    # moment the collection stage starts.
    if tallies is not None:
        wall = ctx.wall_now or collect.wall_clock
        collect_started_at = wall()
    c_started = clock() if acc is not None else 0.0
    # v2.3 §6.4: one session advisory lock per fetched family over the whole collection stage, on a dedicated
    # connection, acquired in a fixed order (a manual fetch holds a single lock, so no deadlock); universe only reads
    # a file and takes no lock.
    universe_meta: dict[str, Any] | None = None
    fetched: set[str] = set()
    chain_tickers: list[str] = []
    collected: list[str] = []
    with ExitStack() as locks:
        if record_trigger is not None:
            locks.enter_context(collect.family_locks(['bars', 'news', 'sec_filings', 'fred']))
        # SPY and the industry ETFs collect bars only, before the range: QQQ is the screen's benchmark.
        for ticker in [BENCHMARK, *ETF_TICKERS]:
            try:
                out = collect.bars_one(ticker, session=session, now=now, deps=deps,
                                       lookback_days=HISTORY_DAYS, tally=bars_tally)
                if ticker == BENCHMARK:
                    summary['benchmark'] = {'bars': out['counts'],
                                            'skipped_bars': [d.isoformat() for d in out['skipped']],
                                            'news': None, 'candidates': [], 'skips': [], 'error': None}
                else:
                    summary['etfs'][ticker] = f"inserted {out['counts']['inserted']}"
            except Unavailable:
                raise
            except Exception as exc:
                if ticker == BENCHMARK:
                    summary['benchmark'] = {'bars': None, 'skipped_bars': [], 'news': None, 'candidates': [],
                                            'skips': [], 'error': f'bars: {exc}'}
                    summary['sources']['bars'].append(ticker)
                    summary['errors'].append(f'{ticker} bars: {exc}')
                else:
                    summary['etfs'][ticker] = f'error: {exc}'
                    summary['sources']['etf_bars'].append(ticker)
                    summary['errors'].append(f'{ticker} etf_bars: {exc}')

        # The range (v2.8 D4): the Nasdaq-100 members outside the manual pool collect bars and earnings dates here;
        # the screen node only reads them. Explicit tickers/pool stay a manual override and skip it entirely.
        if not explicit:
            universe_tally = tallies['universe'] if tallies is not None else None
            try:
                loaded = collect.load_universe(deps)
            except universe_module.UniverseError as exc:
                if universe_tally is not None:
                    universe_tally.fail('nasdaq100', f'universe: {exc}')
            else:
                targets = [t for t in loaded.tickers if t not in pool.tickers and t != screen_module.BENCHMARK]
                fetched, fetch_failures, fetch_reasons = screening.collect_range(
                    core_tx.connection, session=session, now=now, targets=targets, deps=deps, tally=bars_tally)
                summary['sources']['bars'].extend(fetch_failures)
                universe_meta = {'name': loaded.name, 'as_of': loaded.as_of.isoformat(),
                                 'tickers': list(loaded.tickers), 'fetched': sorted(fetched),
                                 'fetch_reasons': dict(fetch_reasons),
                                 'stale': universe_module.is_stale(loaded, session)}
                if universe_tally is not None:
                    universe_tally.ok('nasdaq100')
                    universe_tally.detail.update(name=loaded.name, as_of=loaded.as_of,
                                                 tickers=len(loaded.tickers), stale=universe_meta['stale'])
        all_tickers = [*pool.tickers, *held]
        if not pool.core:
            summary['errors'].append('core_empty')

        collector = _Collector(now=now, session=session, ny_date=ny_date, deps=deps, summary=summary,
                               fetched=fetched, bars_tally=bars_tally, news_tally=news_tally,
                               earnings_tally=(collect.Tally('earnings', job) if acc is not None else None),
                               drop_after=drop_after)
        for ticker in all_tickers:
            collector.ticker(ticker, acc=acc)
        # D21: tickers of active agent-2 chains outside the pool and holdings are collected only — bars,
        # news, earnings and the SEC listing below — and never reach the rule loop.
        if ready:
            try:
                chain_failures: list[tuple[str, str]] = []
                with core_tx._tx() as conn:
                    chain_tickers = plan_review.chain_tickers(conn, session, now=now, exclude=all_tickers,
                                                              failures=chain_failures)
                if acc is not None:     # the review stage reports the same chain in summary['errors']
                    for ticker, reason in chain_failures:
                        acc.fail(f'chains:{ticker}', reason)
            except Unavailable:
                raise
            except Exception as exc:
                summary['errors'].append(f'review chains: {exc}')
        summary['pool']['chains'] = len(chain_tickers)
        for ticker in chain_tickers:
            collector.ticker(ticker, acc=acc)
        collected = [*all_tickers, *chain_tickers]

        # Freeze the denominator before the call: the listing is one request per ticker (D21); a whole-call failure
        # (company_tickers.json unreachable) is the '*' sentinel, not an N+1th target.
        if tallies is not None:
            sec_tally.set_targets(collected)
        try:
            summary['filings'] = collect.sec_listing(collected, session=session, now=now, deps=deps,
                                                     tally=sec_tally, drop_after=drop_after)
            _note_sec_errors(summary, summary['filings'])
        except Unavailable:
            raise
        except Exception as exc:
            summary['filings'] = {'error': str(exc)}
            summary['errors'].append(f'SEC listings: {exc}')
            summary['sources']['sec'] = ['all']
            if tallies is not None:
                sec_tally.fail('*', exc)

        # FRED: no key means not_configured, no request, no rows.
        api_key = os.environ.get('FRED_API_KEY')
        if tallies is not None and not api_key:
            fred_tally.skip('FRED_API_KEY 未配置')
        if acc is not None and not api_key:
            acc.degrade('fred_not_configured', 'FRED_API_KEY 未配置')
        for series_id in macro_module.MACRO_SERIES:
            try:
                summary['macro'][series_id] = collect.fred_series(series_id, session=session, now=now,
                                                                  deps=deps, api_key=api_key, tally=fred_tally)
            except Unavailable:
                raise
            except Exception as exc:
                summary['macro'][series_id] = f'error: {exc}'
                summary['sources']['fred'].append(series_id)
                summary['errors'].append(f'{series_id} fred: {exc}')
        summary['timings']['collect'] = round(clock() - t_collect, 1)

    families_view: list[dict[str, Any]] = []
    if tallies is not None:
        collect_finished_at = wall()
        fetch_rows = [tally.row(trigger=record_trigger, run_id=run_id, session=session,
                                started_at=collect_started_at, finished_at=collect_finished_at)
                      for tally in tallies.values()]
        families_view = [{'family': row['family'], 'status': row['status'], 'ok_count': row['ok_count'],
                          'target_count': row['target_count']} for row in fetch_rows]
        if record_trigger is not None:
            summary['data_status'], fetch_ids = collect.write_runs_with_ids(fetch_rows)
        else:
            fetch_ids = []           # 只记账不落 fetch 行（v2.4 §6.2）
        if acc is not None:
            # 计数与失败取自 v2.3 四个 Tally 的冻结行（universe 若有在其后，§4）。
            for row in fetch_rows[:4]:
                acc.input(row['target_count'])
                acc.output(row['ok_count'])
                for failure in row['failures']:
                    acc.fail(f"{row['family']}:{failure['target']}", failure['reason'])
            if fetch_ids:
                acc.set_ref('data_fetch_runs', fetch_ids)
            earnings_items = collector.earnings_tally.row(
                trigger=record_trigger or 'scheduled', run_id=run_id, session=session, started_at=collect_started_at,
                finished_at=collect_finished_at)['detail'].get('targets', [])
            acc.detail['earnings'] = {'targets': len(collected), 'ok': collector.earnings_ok,
                                      'failed': collector.earnings_failed, 'items': earnings_items}
            if drop_after is not None:
                acc.detail['after_cutoff_dropped'] = {
                    'news': collector.news_dropped,
                    'sec': int((summary['filings'] or {}).get('after_cutoff_dropped') or 0)}
            acc.duration_ms += (clock() - c_started) * 1000
    return {'summary': summary, 'pool': _pool_state(pool), 'manual_pool': _pool_state(pool), 'held': held,
            'chain_tickers': chain_tickers, 'collected': collected, 'universe': universe_meta,
            'timeline_ready': ready, 'growth_ready': growth,
            'refs': {**(state.get('refs') or {}), 'collect': {'families': families_view}}}


def _note_sec_errors(summary: dict[str, Any], filings: dict[str, Any], *, whole_call: bool = True) -> None:
    """Fold the listing's own error map into the summary. One failed ticker is a ``sec`` source and an error line; when
    nothing could be listed at all — ``company_tickers.json`` unreachable (``'*'``) or every ticker failed — it is the
    v2.7 whole-call failure: ``summary['filings'] = {'error': …}``, one ``SEC listings`` line, ``sources.sec = ['all']``."""
    errors = filings.get('errors') or {}
    if not errors:
        return
    if whole_call and ('*' in errors or not filings.get('by_ticker')):
        reason = errors.get('*') or next(iter(errors.values()))
        summary['filings'] = {'error': reason}
        summary['errors'].append(f'SEC listings: {reason}')
        summary['sources']['sec'] = ['all']
        return
    for ticker, reason in errors.items():
        summary['errors'].append(f'{ticker} sec: {reason}')
        if summary['sources']['sec'] != ['all'] and ticker not in summary['sources']['sec']:
            summary['sources']['sec'].append(ticker)


def stage_screen(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``screen`` node: ``screen-1`` over what ``collect`` stored (no requests for the range), members into the
    extended pool; the members the collect node did not cover get their news and SEC listings now (v2.8 §4.5)."""
    deps, clock = ctx.deps, ctx.clock
    now, session = _moment(state)
    run_id, record_trigger, job = state.get('run_id'), ctx.record_trigger, ctx.job
    summary = state['summary']
    pool = manual_pool = _pool_from_state(state['pool'])
    held = list(state.get('held') or [])
    if state.get('explicit'):
        summary['screen'] = {'status': 'skipped', 'reason': 'explicit_pool'}
        _stage_skip(ctx, 'screen', 'explicit_pool')      # 显式池：整阶段跳过（v2.4 §4）
        return {'summary': summary, 'pool': _pool_state(pool), 'held': held}
    universe_meta = state.get('universe') or {}
    s_started = clock() if acc is not None else 0.0
    stage = screening.run_stage(session=session, now=now, manual=pool, held=held, deps=deps, run_id=run_id,
                                fetched=set(universe_meta.get('fetched') or ()),
                                fetch_reasons=dict(universe_meta.get('fetch_reasons') or {}))
    failed: str | None = None
    if acc is not None:
        acc.duration_ms += (clock() - s_started) * 1000
        acc.input(len(stage['universe'].tickers) if stage['universe'] is not None else 0)
        acc.output(len(stage['summary']['selected']))
        if stage['summary']['screen_run_id'] is not None:
            acc.set_ref('screen_run_id', stage['summary']['screen_run_id'])
        error = stage['summary']['error']
        if error is not None and not (error.startswith('universe:') or error == 'benchmark_missing'):
            failed = error
        elif error is not None:
            # 范围文件加载失败或 QQQ 缺当日数据、沿用成员：降级不是失败（§4）
            acc.degrade('universe_load_failed' if error.startswith('universe:') else 'benchmark_missing', error)
    summary['screen'] = stage['summary']
    if stage['status'] == 'error':
        summary['errors'].append(f"screen: {stage['summary']['error']}")
    pool = pool_module.with_screen(pool, stage['members'])
    with core_tx._tx() as conn:
        tier_changes = _sync_pool(conn, pool, session, now)
    # A held screen member is in the pool now; run it once, not again as an off-pool holding.
    held = [t for t in held if t not in pool.tickers]
    summary['pool'] = {**summary['pool'], 'extended': len(pool.extended), 'holdings': len(held),
                       'screen': len(stage['members']), 'changes': tier_changes}

    # v2.8 D4: members that collect did not cover (collect only reads the manual pool, holdings and chains) get
    # their news and SEC listings now; today's range bars and earnings dates are already stored.
    covered = set(state.get('collected') or ())
    new_members = [t for t in stage['members'] if t not in covered]
    if new_members:
        _backfill_members(new_members, state=state, ctx=ctx, acc=acc, summary=summary, now=now, session=session,
                          fetched=set(universe_meta.get('fetched') or ()), job=job,
                          record_trigger=record_trigger, run_id=run_id)
    if failed is not None:
        _stage_finish(ctx, 'screen', failed=failed)
    return {'summary': summary, 'pool': _pool_state(pool), 'manual_pool': _pool_state(manual_pool), 'held': held}


def _backfill_members(new_members: list[str], *, state: dict[str, Any], ctx, acc, summary: dict[str, Any],
                      now: datetime, session: date, fetched: set[str], job: str,
                      record_trigger: str | None, run_id: str | None) -> None:
    """News and SEC listing for the screen's new members; failures only count against the screen node (``partial``)."""
    drop_after = now if ctx.resumed else None
    # The v2.7 shell never wrote rows for these members (it collected them with the pool): no tallies, locks or rows.
    tallies = ({'news': collect.Tally('news', job, new_members),
                'sec_filings': collect.Tally('sec_filings', job, new_members)}
               if (record_trigger is not None or acc is not None) and not ctx.legacy else None)
    wall = _wall(ctx)
    started_at = wall() if tallies is not None else None
    with ExitStack() as locks:
        if record_trigger is not None and not ctx.legacy:
            locks.enter_context(collect.family_locks(['news', 'sec_filings']))
        got = screening.backfill_new_members(
            new_members, session=session, now=now, deps=ctx.deps, drop_after=drop_after, fetched=fetched,
            ny_date=date.fromisoformat(state['ny_date']),
            news_tally=tallies['news'] if tallies is not None else None,
            sec_tally=tallies['sec_filings'] if tallies is not None else None)
    for ticker in new_members:
        problems = list(got['problems'].get(ticker) or [])
        bars_ = got['bars'].get(ticker)
        entry = {'bars': bars_['counts'] if isinstance(bars_, dict) else bars_,
                 'skipped_bars': [d.isoformat() for d in bars_['skipped']] if isinstance(bars_, dict) else [],
                 'news': got['news'].get(ticker), 'candidates': [], 'skips': [], 'error': None}
        if any(p.startswith('bars:') for p in problems):
            summary['sources']['bars'].append(ticker)
        if any(p.startswith('news:') for p in problems):
            summary['sources']['news'].append(ticker)
        status = got['earnings'].get(ticker)
        if status is not None:
            summary['earnings'][ticker] = status
            if status.startswith('error:'):
                summary['errors'].append(f'{ticker} earnings: {status[len("error: "):]}')
                summary['sources']['earnings'].append(ticker)
        if problems:
            entry['error'] = '; '.join(problems)
            summary['errors'].extend(f'{ticker} {p}' for p in problems)
        summary['tickers'][ticker] = entry
    if got['filings_error'] is not None:
        if 'error' not in (summary.get('filings') or {}):       # one failure line, however many calls failed
            summary['errors'].append(f"SEC listings: {got['filings_error']}")
        summary['sources']['sec'] = ['all']
        if not isinstance(summary.get('filings'), dict) or 'error' not in summary['filings']:
            summary['filings'] = {'error': got['filings_error']}
    elif got['filings'] is not None:
        _merge_filings(summary, got['filings'])
        if 'error' not in (summary.get('filings') or {}):
            _note_sec_errors(summary, got['filings'], whole_call=False)
    if tallies is not None:
        finished_at = wall()
        rows = [tallies[family].row(trigger=record_trigger or 'scheduled', run_id=run_id, session=session,
                                    started_at=started_at, finished_at=finished_at)
                for family in ('news', 'sec_filings')]
        for row in rows:
            row['detail']['scope'] = 'screen_new_members'
        if record_trigger is not None:
            _, fetch_ids = collect.write_runs_with_ids(rows)
            if acc is not None and fetch_ids:
                acc.set_ref('data_fetch_runs', fetch_ids)
        if acc is not None:
            failed_targets = set()
            for row in rows:
                for failure in row['failures']:
                    acc.fail(f"{'news' if row['family'] == 'news' else 'sec'}:{failure['target']}", failure['reason'])
                    failed_targets.add(failure['target'])
            for ticker in new_members:
                if any(p.startswith('bars:') for p in got['problems'].get(ticker) or []):
                    acc.fail(f'bars:{ticker}', next(p for p in got['problems'][ticker] if p.startswith('bars:')))
            failed = len(failed_targets - {'*'})
            acc.detail['new_members'] = list(new_members)
            acc.detail['backfill'] = {'targets': len(new_members), 'ok': max(0, len(new_members) - failed),
                                      'failed': failed}


def _merge_filings(summary: dict[str, Any], listed: dict[str, Any]) -> None:
    """Add a second listing (the screen's new members) to ``summary['filings']``: counts add up, per-ticker maps merge."""
    base = summary.get('filings')
    if not isinstance(base, dict) or 'error' in base:
        summary['filings'] = listed
        return
    for key in ('inserted', 'existing', 'acceptance_filled', 'after_cutoff_dropped'):
        if key in listed:
            base[key] = int(base.get(key) or 0) + int(listed[key] or 0)
    base.setdefault('by_ticker', {}).update(listed.get('by_ticker') or {})
    base.setdefault('errors', {}).update(listed.get('errors') or {})


def stage_regime(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``regime`` node. Market context sits between collection and rules; pool bars exclude off-pool holdings.

    An exception is booked in ``summary`` (as in v2.7) and then raised: the node retries it and, failing again, stops
    the chain — the one stage that no longer carries on after a failure, because ``signals`` depend on today's
    regime (v2.8 §5.2, §22 Q5). The v2.7 ``run_batch`` shell catches it and carries on."""
    clock = ctx.clock
    now, session = _moment(state)
    summary = state['summary']
    manual_pool = _pool_from_state(state.get('manual_pool') or state['pool'])
    t_regime = clock()
    try:
        with core_tx._tx() as conn:
            if ctx.breadth_tickers is not None:
                breadth_sample = list(ctx.breadth_tickers)
            elif not state.get('explicit'):
                loaded = state.get('universe')
                breadth_sample, breadth_notes = _breadth_tickers(
                    manual_pool, SimpleNamespace(tickers=tuple(loaded['tickers'])) if loaded else None)
                summary['errors'].extend(breadth_notes)
            else:
                breadth_sample = list(manual_pool.tickers)
            pool_bars = {t: b for t in breadth_sample
                         if (b := repository.read_bars(conn, t, session, cutoff_at=now, limit=300))}
            etf_read = {etf: repository.read_bars(conn, etf, session, cutoff_at=now) for etf in ETF_TICKERS}
            spy_read = repository.read_bars(conn, BENCHMARK, session, cutoff_at=now)
            macro_read = {s: repository.read_macro(conn, s, end_date=session, cutoff_at=now)
                          for s in macro_module.MACRO_SERIES}
            macro_status = {s: v if v == 'not_configured' else
                            ('error' if isinstance(v, str) and v.startswith('error:') else 'ok')
                            for s, v in summary['macro'].items()}
            snap = regime_module.snapshot(session=session, pool_bars=pool_bars, etf_bars=etf_read,
                                          spy_bars=spy_read, macro_rows=macro_read, macro_status=macro_status)
            out = repository.store_regime(conn, snap, now=now)
        summary['regime'] = {'status': out['status'], 'label': snap['label']}
        if out['status'] == 'conflict':
            summary['conflicts'].append({'ticker': None, 'rule_id': 'market_regime',
                                         'id': str(out['record']['id'])})
    except Unavailable:
        raise
    except Exception as exc:
        summary['regime'] = f'error: {exc}'
        summary['sources']['regime'] = ['all']
        summary['errors'].append(f'regime: {exc}')
        summary['timings']['regime'] = round(clock() - t_regime, 1)
        raise
    if acc is not None:
        acc.input(len(breadth_sample))
        acc.output(1)      # 写入或已存的快照（§4）
        acc.set_ref('market_regime_id', out['record']['id'])
        if snap['label'] == 'insufficient':
            acc.degrade('label_insufficient', '')      # 广度样本不足
        for series_id, state_ in macro_status.items():
            if state_ != 'ok':
                acc.degrade('macro_not_ok', f'{series_id}:{state_}')
        if out['status'] == 'conflict':
            acc.degrade('conflict', '')
        acc.duration_ms += (clock() - t_regime) * 1000
    summary['timings']['regime'] = round(clock() - t_regime, 1)
    return {'summary': summary}


def stage_rules(state: dict[str, Any], ctx, sig_acc: stages_module.StageAcc | None,
                plan_acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``signals`` node's body: the rule loop over the pool and the holdings, one transaction per ticker, which
    also writes the rule plan of a fresh buy unit (v2.8 §4.3, §22 Q4). The ``plan`` accumulator is frozen into
    ``refs['plan_acc']``: the ``plan`` node only re-derives and writes its own row."""
    clock = ctx.clock
    now, session = _moment(state)
    summary, run_id = state['summary'], state.get('run_id')
    all_tickers = [*_pool_from_state(state['pool']).tickers, *(state.get('held') or [])]
    t_rules = clock()
    for acc_ in (sig_acc, plan_acc):   # 空跑与重跑时两个列表也在（inserted 为 []，v2.4 §4）
        if acc_ is not None:
            acc_.outputs.update({'inserted': [], 'existing': []})
    if plan_acc is not None:           # v2.9 §4.2: every run states how many units merged into an active chain
        plan_acc.detail['retriggers'] = _empty_retriggers()
    for ticker in all_tickers:
        entry = summary['tickers'].setdefault(ticker, {'bars': None, 'skipped_bars': [], 'news': None,
                                                       'candidates': [], 'skips': [], 'error': None})
        if sig_acc is not None:
            sig_acc.input()
        phase = 'signals'          # 异常归到出错的那个阶段
        noted: dict[str, Any] = {}  # 本只的记账，事务提交后才并入阶段（回滚的写入不进证据）
        try:
            with core_tx._tx() as conn:
                ticker_bars = repository.read_bars(conn, ticker, session, cutoff_at=now)
                events, _ = _filing_events(conn, ticker, session, now)
                book = _book(conn, ticker)
                _, plan_state = _position_plan_state(conn, ticker, book, ticker_bars, session, now=now)
                with (sig_acc.timing() if sig_acc is not None else nullcontext()):
                    result = rules.evaluate(ticker, session, ticker_bars, events, book['position'],
                                            plan_state=plan_state)
                    stored_candidates = []
                    for cand in result['candidates']:
                        out = repository.store_candidate(conn, ticker, session, cand, run_id=run_id)
                        stored_candidates.append((cand.rule_id, out))
                        if out['status'] == 'conflict':
                            summary['conflicts'].append({'ticker': ticker, 'rule_id': cand.rule_id, 'id': str(out['id'])})
                entry['candidates'] = [c.rule_id for c in result['candidates']]
                entry['skips'] = sorted({f'{s.rule_id}:{s.reason}' for s in result['skips']})
                noted['candidates'] = stored_candidates
                noted['skips'] = entry['skips']
                group = repository.unit_candidates(conn, ticker, session)
                unit = synthesis.synthesize(ticker, session, group, holding=book['position'] is not None,
                                            now=now) \
                    if group and repository.decision_head(conn, ticker, session) is None else None
                if unit is not None:
                    noted['action'] = unit['proposed_action']
                if unit is not None and unit['proposed_action'] == 'buy' and 'conflict' not in unit['labels']:
                    phase = 'plan'
                    if plan_acc is not None:
                        plan_acc.input()
                    with (plan_acc.timing() if plan_acc is not None else nullcontext()):
                        # v2.9 §4.2 (R2): the active-chain decision and the chain_retriggers row share this
                        # ticker's transaction with the rule baseline, under the chain-open lock
                        merged = (_open_or_merge(conn, ticker, session, now=now)
                                  if repository.timeline_ready(conn) and repository.executable_ready(conn) else None)
                        found = (merged or {}).get('found')
                        stored = (merged or {}).get('row')
                        target = ({'ticker': ticker, 'signal_date': found['chain']['signal_date']} if found
                                  else {'ticker': ticker, 'signal_date': stored['chain_signal_date']} if stored
                                  else None)
                        noted['plan'] = _generate_rule_plan(conn, ticker, session, ticker_bars, summary, now=now,
                                                            active_chain=target)
                        if found is not None:
                            noted['retrigger'] = repository.insert_chain_retrigger(conn, {
                                'ticker': ticker, 'unit_signal_date': session,
                                'chain_signal_date': found['chain']['signal_date'],
                                'chain_state': found['chain_state'], 'rule_ids': unit['rule_ids'],
                                'baseline_plan_id': noted['plan']['id'] if noted['plan'] else None,
                                'entry_cap': timeline.retrigger_cap(found['chain_state'], session),
                                'run_id': run_id or 'unknown', 'timeline_version': timeline.TIMELINE_VERSION,
                                'created_at': now})
                        elif stored is not None:
                            noted['retrigger'] = stored
                    noted['planned'] = True
                # Briefs come from the agent stage only (P4 §7.3): propose for
                # conflict-free buys, interpret for everything else on the main list.
        except Unavailable:
            raise
        except Exception as exc:
            entry['error'] = '; '.join(filter(None, [entry['error'], f'rules: {exc}']))
            summary['errors'].append(f'{ticker} rules: {exc}')
            if sig_acc is not None:
                (plan_acc if phase == 'plan' else sig_acc).fail(ticker, exc)
        else:
            if sig_acc is not None:
                _note_signals(sig_acc, ticker, noted)
                if noted.get('planned'):
                    _note_plan(plan_acc, ticker, noted['plan'], summary['plans'][ticker])
                    if noted.get('retrigger') is not None:
                        _note_retrigger(plan_acc, ticker, noted['retrigger'])
    summary['timings']['rules'] = round(clock() - t_rules, 1)
    delta: dict[str, Any] = {'summary': summary}
    if plan_acc is not None:
        frozen = copy.deepcopy(plan_acc.freeze())
        # What the plan stage did inside the signals loop: its own timing, not the row's measured time.
        frozen['detail'] = {**frozen['detail'], 'timings': {'in_signals_ms': round(frozen['duration_ms'])}}
        frozen['duration_ms'] = 0.0
        delta['refs'] = {**(state.get('refs') or {}), 'plan_acc': frozen}
    return delta


def stage_plan_check(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``plan`` node's body: re-derive the plans the rule loop wrote (v2.4 §8). ``acc`` already carries the loop's
    accounting (the shell passes the live accumulator, the graph restores it from ``refs['plan_acc']``)."""
    summary = state['summary']
    if acc is not None:
        acc.detail.setdefault('retriggers', _empty_retriggers())     # written by the rule loop; a node restored without it says 0
        _check_derivations(acc, acc.outputs['inserted'] + acc.outputs['existing'], summary)
    return {'summary': summary}


def stage_agent(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``agent`` node: the budget window opens now (``agent_started_at``, wall time, v2.8 D24)."""
    now, session = _moment(state)
    summary = state['summary']
    opened = None if ctx.legacy else _wall(ctx)().isoformat()
    started, clock = agent_window({**state, 'agent_started_at': opened}, ctx)
    unavailable = _agent_stage(session, now=now, deps=ctx.deps, summary=summary, started=started, clock=clock,
                               acc=acc, timeline_ready=state.get('timeline_ready'), run_id=state.get('run_id'))
    if ctx.recorder is not None:
        if unavailable is not None:    # 不可用压过「空跑成功」：整阶段 skipped（v2.4 §4）
            ctx.recorder.skip('agent', f'agent_unavailable: {unavailable}')
        else:
            _check_derivations(acc, acc.outputs.get('plan_ids', []), summary)
    return {'summary': summary} if opened is None else {'summary': summary, 'agent_started_at': opened}


def stage_review(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``review`` node (v2.5 §7): the daily review shares the agent node's budget window; a failure of the whole
    stage is booked as its ``failed`` row and the chain carries on (as in v2.7)."""
    from investment_assistant.workbench import plan_review     # imports this module

    now, session = _moment(state)
    summary = state['summary']
    started, clock = agent_window(state, ctx)
    try:
        skipped = plan_review.run_stage(session, now=now, deps=ctx.deps, summary=summary, started=started,
                                        clock=clock, run_id=state.get('run_id'), acc=acc,
                                        timeline_ready=state.get('timeline_ready'))
    except Unavailable:
        raise
    except Exception as exc:
        reason = families.sanitize_reason(exc)
        summary['errors'].append(f'review: {reason}')
        _stage_finish(ctx, 'review', failed=reason or type(exc).__name__)
    else:
        if skipped is not None:
            _stage_skip(ctx, 'review', skipped)
    return {'summary': summary}


def stage_accounts(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """Account failures are recorded without preventing push or retro; its own 600-second budget starts here."""
    from investment_assistant.workbench import paper_accounts
    _, session = _moment(state)
    # Upstream plans/reviews are committed after the run cutoff. Freeze this node's own cutoff once.
    now = _wall(ctx)()
    if acc is not None:
        acc.detail['cutoff_at'] = now.isoformat()
    summary = state['summary']
    try:
        summary['accounts'] = paper_accounts.run_stage(session, now=now, deps=ctx.deps,
            run_id=state.get('run_id'), acc=acc, settings=ctx.deps.agent_settings)
    except Exception as exc:
        reason = families.sanitize_reason(exc)
        summary.setdefault('errors', []).append(f'accounts: {reason}')
        _stage_finish(ctx, 'accounts', failed=reason or type(exc).__name__)
    return {'summary': summary}


def account_push(recorder: stages_module.StageRecorder | None, acc: stages_module.StageAcc,
                 out: dict[str, Any]) -> None:
    """Close the push stage from the notifier's result (v2.4 §4): a skip for the two non-events, success for sent /
    no_notifications, failed for every other reason.

    The notifier reports only the messages actually sent, so input equals output here."""
    reason = out.get('reason')
    if not out.get('sent') and reason in ('already_pushed', 'task_disabled'):
        if recorder is not None:
            recorder.skip('push', reason)
        return
    messages = int(out.get('messages') or 0)
    acc.input(messages)
    acc.output(messages)
    acc.set_ref('push', {'sent': bool(out.get('sent')), 'messages': messages,
                         'reason': families.sanitize_reason(reason) if reason else None})
    if recorder is not None:
        if out.get('sent') or reason == 'no_notifications':
            recorder.finish('push')
        else:
            recorder.finish('push', failed=str(reason or 'not_sent'))


def retro_push_counts(*, now: datetime) -> dict[str, int]:
    """The push overview's retro-card line (v2.8 D3): cards created since the last ``daily`` push and the cards waiting
    for review now. Without an earlier ``daily`` push nothing counts as new."""
    from investment_assistant.workbench import retro_cards

    with core_tx.read_only_tx() as conn:
        since = repository.latest_daily_push_finished_at(conn, before=now)
        cards = repository.review_cards(conn)
        new = sum(1 for card in cards if since is not None and card['created_at'] > since)
        to_review = sum(1 for card in cards if retro_cards.card_state(conn, card['id'])['status'] == 'to_review')
    return {'new_cards': new, 'to_review': to_review}


def stage_push(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``push`` node (from the v2.7 ``workbench`` task): the day's digest, or the one holiday line; a manual run
    does not push. The retro-card line counts cards since the last ``daily`` push; the digest carries a line for
    every family that fetched partially or failed."""
    now, _ = _moment(state)
    summary = state['summary']
    if state.get('trigger') == 'manual_batch':
        _stage_skip(ctx, 'push', 'manual_run')       # 手动运行不推送
        return {'summary': summary}
    closed = summary.get('status') == 'skipped'
    if closed:
        acc.detail['closed'] = True
    with acc.timing():
        if closed:
            summary['push'] = push_closed(now=now, notify_cfg=ctx.notify_cfg)
        else:
            summary['push'] = push(now=now, force=False, summary=summary, notify_cfg=ctx.notify_cfg,
                                   retro_counts=retro_push_counts(now=_wall(ctx)()),
                                   fetch_status=(state.get('refs') or {}).get('collect', {}).get('families'))
    account_push(ctx.recorder, acc, summary['push'])
    return {'summary': summary}


def stage_retro(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None, *,
                ticks: bool = True) -> dict[str, Any]:
    """The ``retro`` node (v2.8 D2): on a trading day the review cards (v2.7 §9: detect, freeze, draft — in its own
    1500-second window, D24), then on any day the weekly and monthly review when due (each in its own ``try``, the
    v2.7 ``review_tick`` parts; a manual run generates but does not push). Neither done → ``skipped / not_due``.
    ``ticks=False`` is the v2.7 ``run_batch`` shell: the cards only."""
    from investment_assistant.workbench import retro_cards

    now, session = _moment(state)
    summary = state['summary']
    window = ctx.window_start if ctx.window_start is not None else ctx.clock()
    growth = state.get('growth_ready')
    if growth is None:
        with core_tx._tx() as conn:
            growth = repository.growth_ready(conn)
    cards = bool(state.get('trading_day', True))
    if cards and not growth:
        summary['errors'].append('growth: migration_027_missing')
        _stage_skip(ctx, 'retro', 'migration_027_missing')
        return {'summary': summary}
    card_failure: str | None = None
    if cards:
        try:
            retro_cards.run_stage(session, now=now, deps=ctx.deps, summary=summary, started=window,
                                  clock=ctx.clock, run_id=state.get('run_id'), acc=acc)
        except Unavailable:
            raise
        except Exception as exc:
            reason = families.sanitize_reason(exc)
            summary['errors'].append(f'retro: {reason}')
            card_failure = reason or type(exc).__name__
    if not ticks:
        if card_failure is not None:
            _stage_finish(ctx, 'retro', failed=card_failure)
        return {'summary': summary}
    acted = False
    manual = state.get('trigger') == 'manual_batch'
    out: dict[str, Any] = {'weekly': None, 'monthly': None, 'errors': []}
    for name, part in (('weekly', _tick_weekly), ('monthly', _tick_monthly)):
        try:
            out[name] = part(now=now, notify_cfg=ctx.notify_cfg, trigger_source='manual' if manual else 'scheduled',
                             client=None, push=not manual)
        except Unavailable:
            raise
        except Exception as exc:
            reason = families.sanitize_reason(exc)
            out[name] = {'action': 'error', 'error': reason}
            out['errors'].append(f'review {name}: {reason}')
            if acc is not None:
                acc.fail(name, reason)
        if out[name].get('action') in ('generated', 'reused', 'reminded'):
            acted = True
    summary.setdefault('errors', []).extend(out['errors'])
    summary['review_tick'] = _jsonable(out)
    if acc is not None:
        acc.detail['cards'] = cards
        acc.detail['weekly'] = summary['review_tick']['weekly']
        acc.detail['monthly'] = summary['review_tick']['monthly']
    if card_failure is not None:        # the row is written once, after the weekly / monthly part has set its detail
        _stage_finish(ctx, 'retro', failed=card_failure)
    elif not cards and not acted and not out['errors']:
        _stage_skip(ctx, 'retro', 'not_due')
    return {'summary': summary}


def _wall(ctx) -> Callable[[], datetime]:
    return ctx.wall_now or collect.wall_clock


def run_batch(*, now: datetime, tickers: list[str] | None = None, pool: pool_module.Pool | None = None,
              deps: Deps | None = None, skip_non_trading: bool = False, run_id: str | None = None,
              breadth_tickers: list[str] | None = None,
              record_trigger: str | None = None,
              wall_now: Callable[[], datetime] | None = None,
              stages: stages_module.StageRecorder | None = None,
              timeline_ready: bool | None = None, growth_ready: bool | None = None) -> dict[str, Any]:
    """Collect -> market context -> rules -> units -> briefs for the latest settled session. Idempotent per session.

    Since v2.8 a thin shell: the stage functions above, called in order (the ``daily`` graph calls the same functions
    as nodes). It is what the tests and the ``derivation`` CLI use, and with ``stages=None`` it behaves as in v2.7.

    The batch runs over the tiered watch pool read from the watchlist (holdings
    join regardless of tier); explicit tickers build an all-core pool for tests
    and manual overrides.

    ``record_trigger`` set ('scheduled' / 'manual_batch') holds the per-family
    advisory locks for the collection stage and appends one data_fetch_runs row
    per recorded family afterwards; None keeps the batch exactly as before (v2.3 §6.2).
    ``wall_now`` (default real wall clock, I07) timestamps those rows' start and
    finish; ``now`` keeps its old meaning as trading session, data fetched_at and
    every computation cutoff.
    ``stages`` (v2.4 §6.2) is a stage recorder: accounting next to the existing code for the stages, never changing
    the control flow; None keeps the batch exactly as before.
    ``timeline_ready`` (v2.5 D22, migration 026; read in the first transaction when None) is handed to the
    agent and review stages. The review stage (v2.5 §7) runs after the agent stage; tickers of active
    agent-2 chains that left the pool and holdings are collected but never run through the rules (D21).
    ``growth_ready`` (v2.7 D29, migration 027; read in the first transaction when None) decides whether the retro
    stage (v2.7 §9) runs after the review stage; without it the batch notes ``growth: migration_027_missing`` in
    ``summary['errors']`` and the recorder was a ``stages-2`` one (no retro row). The weekly / monthly review and
    the push are nodes of the ``daily`` graph only.
    """
    from investment_assistant.workbench import workflow

    deps = deps or Deps()
    clock = deps.clock or _time_module.monotonic
    ny_date = now.astimezone(NY).date()
    if skip_non_trading and not is_trading_day(ny_date):
        if stages is not None:   # 非交易日：push 之前的阶段整段跳过，push 留给任务层（v2.4 §4）
            ids = stages.stage_ids
            stages.skip_many(ids[:ids.index('push')] if 'push' in ids else ids, 'not_trading_day')
        return {'status': 'skipped', 'reason': 'not a trading day in New York', 'ny_date': ny_date.isoformat()}
    session = bars.latest_closed_session(now)
    started = clock()
    explicit = pool is not None or tickers is not None
    state: dict[str, Any] = {
        'run_id': run_id, 'session': session.isoformat(), 'cutoff_at': now.isoformat(), 'ny_date': ny_date.isoformat(),
        'trading_day': True, 'explicit': explicit, 'trigger': record_trigger or 'scheduled',
        'timeline_ready': timeline_ready, 'growth_ready': growth_ready, 'summary': {}}
    if explicit:
        state['pool'] = _pool_state(pool if pool is not None else pool_module.Pool.all_core(tickers))
    ctx = workflow.NodeContext(recorder=stages, deps=deps, clock=clock, wall_now=wall_now, notify_cfg=None,
                               record_trigger=record_trigger, job=stages.job if stages is not None else 'workbench',
                               window_start=started, breadth_tickers=breadth_tickers, legacy=True)

    def open_(stage_id: str):
        return stages.open(stage_id) if stages is not None else None

    def finish(stage_id: str) -> None:
        if stages is not None:
            stages.finish(stage_id)

    # v2.4 §4: collect 的窗口从 SPY 采集前开始，止于 fetch 行落库。
    state.update(stage_collect(state, ctx, open_('collect')))
    finish('collect')
    state.update(stage_screen(state, ctx, None if explicit else open_('screen')))
    finish('screen')
    r_acc = open_('regime')
    try:
        state.update(stage_regime(state, ctx, r_acc))
    except Unavailable:
        raise
    except Exception as exc:   # 区制异常：failed，批处理继续（v2.4 §4；图里改为停链，v2.8 §5.2）
        if stages is not None:
            stages.finish('regime', failed=str(exc) or type(exc).__name__)
    else:
        finish('regime')
    sig_acc, plan_acc = open_('signals'), open_('plan')
    state.update({k: v for k, v in stage_rules(state, ctx, sig_acc, plan_acc).items() if k != 'refs'})
    if stages is not None:
        stages.finish('signals')
        stage_plan_check(state, ctx, plan_acc)
        stages.finish('plan')
    a_acc = open_('agent')
    state.update(stage_agent(state, ctx, a_acc))
    finish('agent')
    state.update(stage_review(state, ctx, open_('review')))
    finish('review')
    ro_acc = open_('retro')
    state.update(stage_retro(state, ctx, ro_acc, ticks=False))
    finish('retro')
    summary = state['summary']
    summary['timings']['total'] = round(clock() - started, 1)
    return summary


def _holding_fields(book: dict[str, Any], bars_: list[dict[str, Any]], plan: dict[str, Any] | None,
                    state: dict[str, Any] | None) -> dict[str, Any]:
    """The holding row shared by 「今日」 and the instrument page: position, current stop and T1 from the
    plan the user decided on (``_position_plan_state``), distance of the last close to both."""
    position = book['position']
    last_close = bars_[-1]['close'] if bars_ else None
    return {
        'quantity': position['quantity'], 'avg_cost': position['avg_cost'],
        'plan_id': plan['id'] if plan else None,
        'stop': state['stop'] if state else None, 'mode': state['mode'] if state else None,
        't1_reached': state['t1_reached'] if state else False, 'target1': state['target1'] if state else None,
        'last_close': last_close,
        'to_stop_pct': float(last_close / float(state['stop']) - 1) if state and last_close else None,
        'to_target_pct': float(float(state['target1']) / last_close - 1) if state and last_close else None,
    }


def _holdings(conn, session: date, *, now: datetime) -> list[dict[str, Any]]:
    out = []
    for ticker in sorted({t['ticker'] for t in repository.list_trades(conn) if t['voided_at'] is None}):
        book = _book(conn, ticker)
        position = book['position']
        if position is None:
            continue
        bars_ = repository.read_bars(conn, ticker, session, cutoff_at=now)
        plan, state = _position_plan_state(conn, ticker, book, bars_, session, now=now)
        earnings = repository.latest_earnings(conn, ticker, cutoff_at=now)
        next_earnings = (earnings['earnings_date'] if earnings and earnings['status'] == 'known'
                         and earnings['earnings_date'] and earnings['earnings_date'] >= session else None)
        out.append({'ticker': ticker, **_holding_fields(book, bars_, plan, state),
                    'next_earnings': next_earnings.isoformat() if next_earnings else None})
    return out


def _brief_view(brief: dict[str, Any] | None) -> dict[str, Any] | None:
    if brief is None or brief['status'] != 'error':
        return brief
    # A brief citing unknown sources is never shown; only its failure is.
    return {k: brief[k] for k in ('id', 'status', 'error', 'created_at', 'prompt_version')}


def _attach_knowledge_refs(conn, agents: list[dict[str, Any] | None]) -> None:
    """v2.7 §6.2: every ok / pass agent block (the ones that show a plan) gets ``knowledge_refs`` — the knowledge
    versions its (head) plan cited, an empty list without citations or without migration 027. Blocks that only
    carry a status and a reason (error, skipped, rejected) keep their small shape."""
    from investment_assistant.workbench import my_knowledge

    blocks = [a for a in agents if a is not None and 'plan' in a]
    refs = my_knowledge.refs_for_plans(conn, [(a['plan'] or {}).get('id') for a in blocks])
    for block in blocks:
        plan_id = (block['plan'] or {}).get('id')
        block['knowledge_refs'] = refs.get(str(plan_id), []) if plan_id is not None else []


def _unit_cards(conn, session: date, *, now: datetime, ready: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The undecided units of the last ``OPEN_UNIT_DAYS`` days as unit cards: ``(main-list units, extended-pool
    units)``. Since v2.9 「今日」 no longer shows them (the plan table does, D34); the push and the read helpers
    below still build their entries from them."""
    units: list[dict[str, Any]] = []
    extended_units: list[dict[str, Any]] = []
    for group in repository.open_candidate_groups(conn, since=session - timedelta(days=OPEN_UNIT_DAYS)):
        ticker, signal_date = group['ticker'], group['signal_date']
        book = _book(conn, ticker)
        held = book['position'] is not None
        unit = synthesis.synthesize(ticker, signal_date, group['candidates'], holding=held, now=now)
        since = signal_date - timedelta(days=NEWS_LOOKBACK_DAYS)
        entry = repository.pool_entry_at(conn, ticker, signal_date, cutoff_at=now)
        unit.update(
            source=entry.get('source') if entry else None,
            candidates=group['candidates'], position=book['position'],
            brief=_brief_view(repository.latest_brief(conn, ticker, signal_date)),
            plan=repository.rule_plan(conn, ticker, signal_date),
            filings=repository.read_filings(conn, ticker, since=since, cutoff_at=now),
            news=repository.read_news(conn, ticker, cutoff_at=now, since=_midnight(since), limit=20),
            agent=_unit_agent(conn, ticker, signal_date, session=session, ready=ready, book=book, now=now))
        # The tier as of the signal date decides the list, not today's tier.
        if _unit_is_extended(conn, ticker, signal_date, held=held, now=now):
            agent_run = repository.latest_agent_run(conn, ticker, signal_date)
            extended_units.append({'ticker': ticker, 'signal_date': signal_date,
                                   'proposed_action': unit['proposed_action'],
                                   'rule_ids': unit['rule_ids'], 'plan': unit['plan'],
                                   'agent_status': _agent_status_line(agent_run)})
        else:
            units.append(unit)
    _attach_knowledge_refs(conn, [unit.get('agent') for unit in units])
    return units, extended_units


def _today_view(conn, session: date, *, now: datetime, cards: bool) -> dict[str, Any]:
    ready = repository.timeline_ready(conn)      # once per request (v2.5 §6.5)
    units, extended_units = _unit_cards(conn, session, now=now, ready=ready) if cards else ([], [])
    holdings = _holdings(conn, session, now=now)
    chains = _active_chains(conn, session, now=now) if ready else []
    held = held_chain_states(conn, session, now=now) if ready else {}
    for holding in holdings:      # D7: a holding without an agent-2 chain is not reviewed
        holding['has_chain'] = holding['ticker'] in held
        holding['chain_state'] = held.get(holding['ticker'])
    market = repository.latest_regime(conn, on_or_before=session, cutoff_at=now)
    screen_report = screening.report(conn, session, now=now)
    view = {'session': session, 'units': units, 'extended_units': extended_units,
            'holdings': holdings, 'market': market, 'screen': screen_report,
            'chains': chains, 'timeline_ready': ready}
    if not cards:
        view['retired'] = True
    return view


def today(*, now: datetime) -> dict[str, Any]:
    """The 「今日」 payload. v2.9 D34 retires the unit cards: ``units`` and ``extended_units`` stay as empty lists with
    ``retired: true`` (the page reads ``GET /plan-table`` instead); the holdings, the active chains, the market and the
    screen report are as before, chains judged by `timeline-2`."""
    session = bars.latest_closed_session(now)
    with core_tx._tx() as conn:
        return _today_view(conn, session, now=now, cards=False)


def unit_cards(*, now: datetime) -> dict[str, Any]:
    """The old 「今日」 payload with its unit cards (the undecided units of the last 14 days). Not an endpoint any more;
    the push and the tests read the units through it."""
    session = bars.latest_closed_session(now)
    with core_tx._tx() as conn:
        return _today_view(conn, session, now=now, cards=True)


def run_backcheck(*, now: datetime, tickers: list[str] | None = None) -> dict[str, Any]:
    """Replay stored data through the current rules; reads only, never collects."""
    from investment_assistant.workbench import backcheck as backcheck_module
    from investment_assistant.workbench import plan_shadow as shadow_module

    with core_tx._tx() as conn:
        symbols = (pool_module.Pool.all_core(tickers).tickers if tickers is not None
                   else pool_module.from_watchlist(list_watchlist_items(conn)).tickers)
        spy_bars = repository.read_bars(conn, BENCHMARK, bars.latest_closed_session(now), cutoff_at=now, limit=2000)
        replays = []
        for ticker in symbols:
            ticker_bars = repository.read_bars(conn, ticker, bars.latest_closed_session(now), cutoff_at=now,
                                               limit=2000)
            first_day = ticker_bars[0]['price_date'] if ticker_bars else date(2026, 1, 1)
            replays.append(backcheck_module.replay(
                ticker, ticker_bars, repository.read_filings(conn, ticker, since=first_day, cutoff_at=now),
                spy_bars, splits=repository.split_events(conn, ticker)))
        stats = backcheck_module.summarize(replays)
        params = {'tickers': list(symbols), 'rule_version': rules.RULE_VERSION,
                  'plan_version': plans.PLAN_V1, 'shadow_version': shadow_module.SHADOW_V1,
                  'bars': 'stored current revisions'}
        return repository.insert_backcheck(conn, backcheck_module.CALC_VERSION, params, stats)


def list_backchecks(limit: int = 10) -> list[dict[str, Any]]:
    with core_tx._tx() as conn:
        out = repository.list_backchecks(conn, limit)
    return [{**row, 'id': str(row['id'])} for row in out]


def decide(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """v2.9 D24: there is no decision button any more; adoption comes from the fills. Always ``Gone`` (410), nothing is
    written. ``signal_decisions`` keeps its old rows, read-only."""
    raise Gone(DECISIONS_RETIRED)


def add_trade(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    with core_tx._tx() as conn:
        return ledger.record_trade(conn, payload, now=now)


def add_plan_note(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """v2.9 §6.5: a note on a chain or a non-chain plan, a correction or a retraction. Never read by an Agent."""
    with core_tx._tx() as conn:
        return ledger.record_note(conn, payload, now=now)


def list_trades(*, account_id=REAL_ACCOUNT_ID) -> list[dict[str, Any]]:
    """Every fill, newest first. A fill that follows a plan carries ``plan_label`` (the label the link list uses)."""
    from investment_assistant.workbench import plan_views

    with core_tx.read_only_tx() as conn:
        rows = list(reversed(repository.list_trades(conn, account_id=ledger._uuid(account_id, 'account_id'))))
        labels_ = plan_views.trade_plan_labels(conn, rows)
        return [{**t, 'plan_label': labels_[t['id']]} if t['id'] in labels_ else t for t in rows]


def void_trade(trade_id: str, reason: Any, *, now: datetime) -> dict[str, Any]:
    with core_tx._tx() as conn:
        return ledger.void_trade(conn, trade_id, reason, now=now)


def account_view(*, account_id=REAL_ACCOUNT_ID) -> dict[str, Any]:
    from investment_assistant.config import load_config
    from investment_assistant.workbench import accounts, paper_accounts
    params = load_config().workbench
    aid = ledger._uuid(account_id, 'account_id')
    with core_tx.read_only_tx() as conn:
        snapshots = repository.list_snapshots(conn, account_id=aid)
        rows = repository.equity_days(conn, aid) if repository.accounts_ready(conn) else []
        account_ = repository.account_by_id(conn, aid) if repository.accounts_ready(conn) else None
        effective = paper_accounts.effective_params(conn, account_) if account_ else accounts.default_params(params)
        return {'snapshots': snapshots, 'flows': repository.list_flows(conn, account_id=aid),
                'ledger': rows[-1] if rows else None, 'reconciliation': accounts.reconcile(snapshots, rows, effective),
                'params': {**effective, 'public_url': params.public_url, 'return_tiers': list(params.return_tiers)}}


def add_snapshot(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    with core_tx._tx() as conn:
        return account.record_snapshot(conn, payload, now=now)


def add_flow(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    with core_tx._tx() as conn:
        return account.record_flow(conn, payload, now=now)


def void_flow(flow_id: str, reason: Any, *, now: datetime) -> dict[str, Any]:
    with core_tx._tx() as conn:
        return account.void_flow(conn, flow_id, reason, now=now)


def _last_push_failed(conn, session: date) -> bool:
    last = repository.last_push(conn)
    return bool(last and last.get('session') == session.isoformat() and last.get('sent') is False
                and last.get('reason') not in (None, 'already_pushed'))


def push(*, now: datetime, force: bool, summary: dict[str, Any] | None = None, notify_cfg, client=None,
         retro_counts: dict[str, Any] | None = None, fetch_status: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Send the day's digest; scheduled runs skip when today already pushed successfully.

    ``retro_counts`` (v2.8 D3: ``{'new_cards', 'to_review'}``, see ``retro_push_counts``) feeds the overview's retro-card
    line; ``fetch_status`` (the collect node's family list) the one line for families that fetched partially or failed.
    Both None (the manual push) keep the digest without those lines — except that a ``summary['retro']`` (the v2.7
    ``run_batch`` shell sets one before the push) still feeds the retro line."""
    from investment_assistant.config import load_config
    from investment_assistant.notify import notifier, templates
    from investment_assistant.workbench import digest as digest_module

    session = bars.latest_closed_session(now)
    with core_tx._tx() as conn:
        if not force:
            last = repository.last_push(conn, session=session)
            if last and last.get('sent') is True:
                return {'session': session.isoformat(), 'sent': False, 'reason': 'already_pushed', 'messages': 0}
        view = unit_cards(now=now)
        plan_updates = None
        reviews_until = None
        units, holding_rows, merged = view['units'], None, set()
        if repository.timeline_ready(conn):
            # D25: reviews written since the last successful daily push of an earlier session
            # (batch and recheck alike); never pushed before → this session's reviews only. The window
            # ends at this read (clock_timestamp, not the transaction start); it goes into the push result
            # (summary.push.reviews_until) so the next push starts exactly here, not at the report time.
            reviews_until = conn.execute('SELECT clock_timestamp()').fetchone()[0]
            since = repository.last_successful_push_at(conn, before_session=session)
            plan_updates = _plan_update_rows(conn, repository.reviews_since(conn, since, session,
                                                                             until=reviews_until))
        if repository.timeline_ready(conn) and repository.executable_ready(conn):
            # v2.9 §11: the new plans are the entry-phase chains and the day's rule baselines (there are no
            # undecided units any more); the holdings are the day's status rows, quiet days included
            units, merged = _push_units(conn, session, view['units'], now=now, plan_updates=plan_updates)
            holding_rows = repository.holding_status_for(conn, session=session)
        payload = digest_module.build(
            session=session, units=units, holdings=view.get('holdings', []), summary=summary or {},
            last_push_failed=_last_push_failed(conn, session),
            public_url=load_config().workbench.public_url,
            extended_plans=sum(1 for u in view.get('extended_units', []) if u.get('plan') is not None),
            market=view.get('market'), agent_summary=(summary or {}).get('agent'),
            screen=view.get('screen'), plan_updates=plan_updates,
            retro=retro_counts if retro_counts is not None else (summary or {}).get('retro'),
            fetch_status=fetch_status, holding_status=holding_rows, merged=merged, session_units=view['units'])
    out = notifier.send_notifications('workbench', templates.workbench_digest(payload), notify_cfg, client=client)
    result = {'session': session.isoformat(), **out}
    if reviews_until is not None:
        result['reviews_until'] = reviews_until.isoformat()
    return result


def _push_units(conn, session: date, view_units: list[dict[str, Any]], *, now: datetime,
                plan_updates: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], set[tuple]]:
    """The unit entries of the push's 「新计划」 section (v2.9 §11) and the set of today's units merged into a chain.

    One entry per main-list chain in the entry phase at S (judged by `timeline-2`): its head version with the chain
    key and, when a retrigger revision of the window produced that head, the 「再次触发，第 n−1 → n 版」 mark; the
    unit's rule baseline follows, then the baselines of the units merged into the chain today. Today's buy units that
    opened no chain (an Agent pass, error or skip) keep their old entry (rule baseline plus the Agent's reason).
    Extended-pool chains are not pushed."""
    revised = {u['ticker']: u for u in plan_updates or []
               if u.get('via') == 'retrigger' and u.get('outcome') == 'revise' and u.get('to_version') is not None}
    entries: list[dict[str, Any]] = []
    by_chain: dict[tuple, dict[str, Any]] = {}
    for chain in repository.all_chains(conn):
        if chain['pool'] == 'extended':
            continue
        ticker, signal_date = chain['ticker'], chain['signal_date']
        status = chain_status(conn, ticker, signal_date, session=session, now=now)
        if status is None or status['state']['state'] != 'entry':
            continue
        head = status['head']
        run = repository.agent_run_by_id(conn, head['version']['agent_run_id'])
        update = revised.get(ticker)
        retrigger = ({'from_version': update['from_version'], 'to_version': update['to_version']}
                     if update and update['to_version'] == head['version']['version_no'] else None)
        entry = repository.pool_entry_at(conn, ticker, signal_date, cutoff_at=now)
        unit = {'ticker': ticker, 'signal_date': signal_date, 'proposed_action': 'buy', 'labels': [],
                'rule_ids': [c['rule_id'] for c in repository.unit_candidates(conn, ticker, signal_date)],
                'source': entry.get('source') if entry else None,
                'plan': repository.rule_plan(conn, ticker, signal_date),
                'brief': _brief_view(repository.latest_brief(conn, ticker, signal_date)),
                'agent': _agent_view(run, head['plan'], {**_chain_view(status), 'signal_date': signal_date,
                                                         'retrigger': retrigger}),
                'merged': []}
        entries.append(unit)
        by_chain[(ticker, signal_date)] = unit
    merged: set[tuple] = set()
    for u in view_units:
        if u['signal_date'] != session or u.get('proposed_action') != 'buy' or 'conflict' in (u.get('labels') or []):
            continue
        row = repository.retrigger_for_unit(conn, u['ticker'], u['signal_date'])
        if row is not None:
            merged.add((u['ticker'], u['signal_date']))
            target = by_chain.get((u['ticker'], row['chain_signal_date']))
            if target is not None:
                target['merged'].append({'signal_date': u['signal_date'], 'plan': u.get('plan')})
        elif (u['ticker'], u['signal_date']) not in by_chain:
            entries.append({**u, 'merged': []})
    entries.sort(key=lambda e: (e['ticker'], e['signal_date']))
    return entries, merged


def _plan_update_rows(conn, reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The window's invalidations and revisions with a new version, as digest.build reads them: each carries
    its chain's pool (version 0's run) and version numbers, a revision also its `changes` texts and the new
    version's levels. Other rows (maintain, failed revisions, skips, errors) are dropped before any lookup."""
    pools: dict[tuple, str | None] = {}
    out = []
    for row in reviews:
        if not (row['outcome'] == 'invalidate' or row['new_plan_id'] is not None):
            continue
        item = {k: row[k] for k in ('ticker', 'signal_date', 'session', 'trigger', 'mode', 'outcome',
                                    'decided_by', 'reason', 'new_plan_id', 'created_at')}
        key = (row['ticker'], row['signal_date'])
        if key not in pools:
            first = next(iter(repository.chain_versions(conn, *key)), None)
            run = repository.agent_run_by_id(conn, first['agent_run_id']) if first else None
            pools[key] = run.get('pool') if run else None
        reviewed = repository.version_by_plan(conn, row['head_plan_id'])
        detail = row.get('detail') or {}
        item.update(pool=pools[key], from_version=reviewed['version_no'] if reviewed else None,
                    to_version=None, changes=[], levels=None, valid_until=None, via=detail.get('via'),
                    exit_reason=detail.get('exit_reason'), retrigger=detail.get('retrigger'))
        if row['outcome'] == 'revise' and row['new_plan_id'] is not None:
            version = repository.version_by_plan(conn, row['new_plan_id'])
            plan = repository.plan_by_id(conn, row['new_plan_id']) or {}
            run = (repository.agent_run_by_id(conn, row['revision_agent_run_id'])
                   if row.get('revision_agent_run_id') else None)
            changes = ((run or {}).get('output') or {}).get('changes') or []
            item.update(to_version=version['version_no'] if version else None,
                        changes=[c['text'] for c in changes if isinstance(c, dict) and c.get('text')],
                        levels={k: plan.get(k) for k in ('entry_low', 'entry_high', 'stop', 'target1')},
                        valid_until=plan.get('valid_until'))
        out.append(item)
    return out


def push_closed(*, now: datetime, notify_cfg, client=None) -> dict[str, Any]:
    """One line for a New York holiday instead of the digest."""
    from investment_assistant.notify import notifier, templates

    ny_date = now.astimezone(NY).date()
    out = notifier.send_notifications('workbench', templates.workbench_closed(ny_date), notify_cfg, client=client)
    return {'session': ny_date.isoformat(), **out}


def screen_view(*, now: datetime, session: date | None = None, full: bool = False) -> dict[str, Any]:
    """The screen report of one session (default the newest run, spec §9.2); None when no run exists.

    The newest run, not the latest closed session: between the New York close and the
    batch the closed session has no run yet, and the page should keep showing the last one."""
    with core_tx._tx() as conn:
        if session is None:
            run = repository.latest_screen_run(conn, session=None)
            if run is None:
                return {'report': None}
            session = run['session']
        return {'report': screening.report(conn, session, now=now, full=full)}


def replay_screen(*, now: datetime, sessions: int) -> dict[str, Any]:
    return {'reports': screening.replay(sessions=sessions, now=now)}


def _instrument_holding(conn, ticker: str, book: dict[str, Any], ticker_bars: list[dict[str, Any]], session: date,
                        *, now: datetime) -> dict[str, Any] | None:
    """The 「今日」 holdings row of this ticker (same bars, same ``_position_plan_state``); None when not held."""
    if book['position'] is None:
        return None
    plan, state = _position_plan_state(conn, ticker, book, ticker_bars, session, now=now)
    return _holding_fields(book, ticker_bars, plan, state)


def _instrument_chains(conn, ticker: str, book: dict[str, Any], session: date, *,
                       now: datetime) -> list[dict[str, Any]]:
    """The ticker's agent chains in state entry / holding / exiting at S (`timeline-2`), newest signal date first
    (empty without 026)."""
    if not repository.timeline_ready(conn):
        return []
    rows = []
    for chain in repository.all_chains(conn):
        if chain['ticker'] != ticker:
            continue
        status = chain_status(conn, ticker, chain['signal_date'], session=session, now=now)
        if status is None or status['state']['state'] not in timeline.ACTIVE_STATES:
            continue
        plan = status['head']['plan']
        rows.append({'signal_date': chain['signal_date'], 'state': status['state']['state'],
                     'mode': status['state']['mode'], 'version_no': status['head']['version']['version_no'],
                     'head_plan_id': str(plan['id']), 'entry_low': plan['entry_low'], 'entry_high': plan['entry_high'],
                     'stop': plan['stop'], 'target1': plan['target1'], 'latest_review': status['latest_review']})
    rows.sort(key=lambda r: r['signal_date'], reverse=True)
    return rows


def _instrument_plans(conn, ticker: str, since: date) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """(plan rows of the page, str(plan_id) -> version row): every plan whose validity reaches the chart window.
    Version rows exist for agent-2 plans once migration 026 has run; without them ``version_no`` is None."""
    rows = repository.plans_for_ticker(conn, ticker, since=since)
    ready = repository.timeline_ready(conn)
    versions: dict[str, dict[str, Any]] = {}
    if ready:
        for plan in rows:
            if plan['kind'] == 'agent' and plan['plan_version'] in (agent_graph.AGENT_V2, agent_graph.AGENT_V3):
                version = repository.version_by_plan(conn, plan['id'])
                if version is not None:
                    versions[str(plan['id'])] = version
    return rows, versions


def instrument(ticker: str, *, now: datetime) -> dict[str, Any]:
    """The instrument page payload. The original keys are read as before; v2.6 adds ``focus``, ``key_data``,
    ``context``, ``plans``, ``holding`` and ``chart.annotations`` (spec §9.4; ``holding`` is the 「今日」 holdings row
    of this ticker or None), each in its own savepoint: a failing block is
    ``None`` and its reason lands in ``errors`` (keyed by block), the rest of the page is unaffected."""
    from investment_assistant.workbench import instrument_view

    ticker = ticker.strip().upper()
    session = bars.latest_closed_session(now)
    errors: dict[str, str] = {}
    with core_tx.read_only_tx() as conn:
        def guarded(key: str, fn: Callable[[], Any]) -> Any:
            try:
                with conn.transaction():          # a savepoint: one failed statement must not poison the page
                    return fn()
            except Exception as exc:              # noqa: BLE001 - isolate the block, report why
                errors[key] = str(exc) or type(exc).__name__
                return None

        book = _book(conn, ticker)
        chart_bars = repository.read_bars(conn, ticker, session, cutoff_at=now, limit=chart.READ_LIMIT)
        chart_payload = chart.build(chart_bars)
        shown = chart_payload['bars']
        first_day = shown[0]['price_date'] if shown else session - timedelta(days=120)
        candidates = repository.read_candidates(conn, ticker, since=first_day)
        trades = repository.list_trades(conn, ticker)
        earnings = repository.latest_earnings(conn, ticker, cutoff_at=now)

        plan_data = guarded('plans', lambda: _instrument_plans(conn, ticker, first_day))
        plan_rows, versions = plan_data if plan_data is not None else ([], {})
        plans_view = None if plan_data is None else [
            {'id': str(p['id']), 'kind': p['kind'], 'plan_version': p['plan_version'],
             'signal_date': p['signal_date'], 'entry_low': p['entry_low'], 'entry_high': p['entry_high'],
             'stop': p['stop'], 'target1': p['target1'], 'valid_from': p['valid_from'],
             'valid_until': p['valid_until'], 'status': p['status'],
             'version_no': versions[str(p['id'])]['version_no'] if str(p['id']) in versions else None}
            for p in plan_rows]
        annotations = guarded('chart.annotations', lambda: chart.annotate(
            bars=shown, candidates=candidates, plans=plan_rows, versions=versions, trades=trades))
        # the bars 「今日」 holdings and get_indicators read (default window), shared by holding and key_data
        ticker_bars = repository.read_bars(conn, ticker, session, cutoff_at=now)
        holding = guarded('holding', lambda: _instrument_holding(conn, ticker, book, ticker_bars, session, now=now))

        def focus_view():
            if book['position'] is not None and holding is None:
                raise RuntimeError('持仓止损读取失败，见 errors.holding')
            return instrument_view.focus(
                position=holding, chains=_instrument_chains(conn, ticker, book, session, now=now),
                candidates_today=[c for c in candidates if c['signal_date'] == session],
                earnings=earnings, session=session)

        focus = guarded('focus', focus_view)

        def key_data():
            spy = repository.read_bars(conn, BENCHMARK, session, cutoff_at=now)
            as_of = ticker_bars[-1]['price_date'] if ticker_bars else session
            return instrument_view.key_data(ticker_bars, spy, as_of=as_of)

        def context():
            entry = repository.pool_entry_at(conn, ticker, session, cutoff_at=now)
            return instrument_view.context_view(
                earnings=earnings, regime=repository.latest_regime(conn, on_or_before=session, cutoff_at=now),
                sector_etf=entry.get('sector_etf') if entry else None, session=session)

        key_data_view = guarded('key_data', key_data)
        context_view = guarded('context', context)

        def my_knowledge_block():
            from investment_assistant.workbench import my_knowledge

            return my_knowledge.for_ticker(conn, ticker)

        knowledge_view = guarded('knowledge', my_knowledge_block)      # v2.7 §5.5: the 「我的知识」 block
        return {
            'ticker': ticker, 'session': session,
            'bars': repository.read_bars(conn, ticker, session, cutoff_at=now, limit=120),
            'chart': {**chart_payload, 'annotations': annotations},
            'filings': repository.read_filings(conn, ticker, since=session - timedelta(days=180), cutoff_at=now),
            'news': repository.read_news(conn, ticker, cutoff_at=now, since=_midnight(session - timedelta(days=60))),
            'candidates': candidates,
            'decisions': repository.decision_heads(conn, ticker=ticker),
            'trades': trades,
            'position': book['position'], 'closed': book['closed'],
            'focus': focus, 'key_data': key_data_view, 'context': context_view, 'plans': plans_view,
            'holding': holding, 'knowledge': knowledge_view, 'errors': errors,
        }


def _decided_units(conn, heads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rule_ids = {c['id']: c['rule_id'] for c in repository.candidates_by_ids(
        conn, [cid for h in heads for cid in h['candidate_ids']])}
    return [{**h, 'rule_ids': [rule_ids[c] for c in h['candidate_ids'] if c in rule_ids]} for h in heads]


def _outcomes(conn, units, session: date, now: datetime) -> dict[Any, dict[str, Any]]:
    spy = repository.read_bars(conn, BENCHMARK, session, cutoff_at=now, limit=400)
    cache: dict[str, list] = {}
    out = {}
    for u in units:
        if u['ticker'] not in cache:
            cache[u['ticker']] = repository.read_bars(conn, u['ticker'], session, cutoff_at=now, limit=400)
        out[u['id']] = review.shadow_outcome(u, cache[u['ticker']], spy)
    return out


def shadow(*, now: datetime, since: date | None = None) -> dict[str, Any]:
    """The control group (`review-8`, v2.9 D26): every unit since ``since`` — a ticker-day with candidates, whether or
    not anybody decided on it or traded it — assumed entered at the next open, with its 5 / 10 / 20 session excess
    return over SPY. Old decided-only snapshots keep their own `review-7` shape in the weekly review."""
    session = bars.latest_closed_session(now)
    since = since or session - timedelta(days=90)
    with core_tx._tx() as conn:
        units = []
        for group in sorted(repository.candidate_groups_since(conn, since), key=lambda g: (g['signal_date'], g['ticker'])):
            if group['signal_date'] > session:
                continue
            shape = synthesis.synthesize(group['ticker'], group['signal_date'], group['candidates'], holding=False,
                                         now=now)
            units.append({'id': f"{group['ticker']}:{group['signal_date'].isoformat()}", 'ticker': group['ticker'],
                          'signal_date': group['signal_date'], 'proposed_action': shape['proposed_action'],
                          'rule_ids': shape['rule_ids']})
        outcomes = _outcomes(conn, units, session, now)
    return {'session': session, 'since': since, 'calc_version': review.CALC_VERSION,
            'units': [{**u, 'outcome': outcomes[u['id']]} for u in units]}


def _plan_chains(conn, plans: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
    """v2.5 §9.1: ``plan_id → {version_no, origin, is_head, supersedes_plan_id}`` for the agent-2 versions
    among ``plans``; agent-1, rule and own plans (and everything without migration 026) are absent."""
    wanted = [p for p in plans if p['kind'] == 'agent' and p['plan_version'] in (agent_graph.AGENT_V2, agent_graph.AGENT_V3)]
    if not wanted or not repository.timeline_ready(conn):
        return {}
    versions_by_unit: dict[tuple, list[dict[str, Any]]] = {}
    out = {}
    for plan in wanted:
        key = (plan['ticker'], plan['signal_date'])
        if key not in versions_by_unit:
            versions_by_unit[key] = repository.chain_versions(conn, *key)
        versions = versions_by_unit[key]
        head_no = max(v['version_no'] for v in versions) if versions else None
        version = next((v for v in versions if v['plan_id'] == plan['id']), None)
        if version is not None:
            out[plan['id']] = {'version_no': version['version_no'], 'origin': version['origin'],
                               'is_head': version['version_no'] == head_no,
                               'supersedes_plan_id': (str(version['supersedes_plan_id'])
                                                      if version['supersedes_plan_id'] else None)}
    return out


def shadow_outcomes(plan: dict[str, Any], bars_: list[dict[str, Any]], spy: list[dict[str, Any]], *,
                    splits: list[tuple[date, Decimal]]) -> dict[str, Any]:
    """One plan's shadow outcomes (v2.9 D14): ``{'outcome', 'shadow_version', 'shadow_v1'}``. A plan with execution
    fields (``plan-2``, ``agent-3``, own plans since v2.9) is replayed by `plan-shadow-2` and, as the counterfactual
    「无时间止损、旧入场规则」, by `plan-shadow-1` (``shadow_v1``); an older plan only by `plan-shadow-1`
    (``shadow_v1`` None, ``outcome`` is that result)."""
    both = _plan_shadow_module.simulate_both(plan, bars_, spy, splits=splits)
    if 'v2' in both:
        return {'outcome': both['v2'], 'shadow_version': _plan_shadow_module.SHADOW_V2, 'shadow_v1': both['v1']}
    return {'outcome': both['v1'], 'shadow_version': _plan_shadow_module.SHADOW_V1, 'shadow_v1': None}


def plan_shadow(*, now: datetime, since: date | None = None) -> dict[str, Any]:
    """Simulated outcomes for rule, agent and own plans of the last 90 days; nothing frozen until a review.

    v2.5 §9.1: every agent version is its own row with ``chain`` (None for other plans), and ``agent_summary`` sums
    each chain's first version and its current head. v2.9 §5.6: every row carries its ``shadow_version``; a plan
    with execution fields is replayed by `plan-shadow-2` and its `plan-shadow-1` result rides along as ``shadow_v1``;
    ``agent_summary.counterfactual`` sums the same agent rows under both."""
    session = bars.latest_closed_session(now)
    since = since or session - timedelta(days=90)
    with core_tx._tx() as conn:
        spy = repository.read_bars(conn, BENCHMARK, session, cutoff_at=now, limit=400)
        plans = repository.plans_since(conn, since)
        chains = _plan_chains(conn, plans)
        out = []
        for plan in plans:
            ticker_bars = repository.read_bars(conn, plan['ticker'], session, cutoff_at=now, limit=400)
            found = shadow_outcomes(plan, ticker_bars, spy, splits=repository.split_events(conn, plan['ticker']))
            out.append({**plan, 'chain': chains.get(plan['id']), **found})
    summary = review.shadow_agent_summary(out)
    paired = [row for row in out if row.get('kind') == 'agent' and row['shadow_v1'] is not None]
    v2_outcomes = [row['outcome'] for row in paired]
    summary['counterfactual'] = {
        'v1': review.shadow_block([row['shadow_v1'] for row in paired]),
        'v2': {**review.shadow_block(v2_outcomes), 'voided': sum(1 for o in v2_outcomes if o.get('status') == 'voided')}}
    return {'session': session, 'since': since, 'calc_version': _plan_shadow_module.SHADOW_VERSION,
            'shadow_version': _plan_shadow_module.SHADOW_VERSION, 'plans': out, 'agent_summary': summary}


def _discipline_v2(conn, *, trades: list[dict[str, Any]], segments: list[dict[str, Any]], tickers: list[str],
                   session: date, now: datetime, since: date, until: date | None = None, account_id=REAL_ACCOUNT_ID) -> dict[str, Any]:
    """`discipline-2` over ``trades`` (v2.9 D28): every buy is judged against the plan its fill is linked to
    (``trades.plan_id``; an old fill through its decision), the chain's newest version at the sale for the early take
    and the daily holding status for the held loser. ``segments`` are the closed lots, ``tickers`` the ones whose
    bars, stop events and holding rows are read (since ``since``; stop events up to ``until``)."""
    from investment_assistant.workbench import plan_review     # imports this module

    contexts: dict[str, dict[str, Any]] = {}
    plans_by_trade: dict[Any, dict[str, Any]] = {}
    chain_head_by_trade: dict[Any, list[dict[str, Any]]] = {}
    legacy_decisions: dict[Any, dict[str, Any]] = {}
    plans_by_id: dict[Any, dict[str, Any]] = {}
    for t in trades:
        if t['side'] != 'buy' or t['voided_at'] is not None:
            continue
        if t.get('plan_id') is not None:
            plan = repository.plan_by_id(conn, t['plan_id'])
            if plan is None:
                continue
            plans_by_trade[t['id']] = plan
            context = contexts.get(t['ticker']) or contexts.setdefault(
                t['ticker'], plan_review.ticker_context(conn, t['ticker'], now=now, account_id=account_id))
            key = context['plan_chain'].get(t['plan_id'])
            if key is not None and str(account_id) == str(REAL_ACCOUNT_ID):
                chain_head_by_trade[t['id']] = [repository.plan_by_id(conn, v['plan_id'])
                                                for v in repository.chain_versions(conn, key[0], key[1])]
        elif t.get('decision_id') and t['decision_id'] not in legacy_decisions:
            decision = repository.decision_by_id(conn, t['decision_id'])
            if decision is not None:
                legacy_decisions[t['decision_id']] = decision
                if decision['plan_id'] and decision['plan_id'] not in plans_by_id:
                    plan = repository.plan_by_id(conn, decision['plan_id'])
                    if plan is not None:
                        plans_by_id[decision['plan_id']] = plan
    stop_events, holding_rows = [], []
    for tk in tickers:
        stop_events += [c for c in repository.read_candidates(conn, tk, since=since)
                        if str(account_id) == str(REAL_ACCOUNT_ID) and c['rule_id'] == 'plan_stop' and (until is None or c['signal_date'] <= until)]
        if repository.executable_ready(conn):
            holding_rows += [r for r in repository.holding_status_for(conn, ticker=tk, account_id=account_id) if r['session'] >= since]
    out = _discipline_module.evaluate_v2(
        trades=trades, plans_by_trade=plans_by_trade, chain_head_by_trade=chain_head_by_trade,
        holding_rows=holding_rows, legacy_decisions=legacy_decisions,
        bars_by_ticker={tk: repository.read_bars(conn, tk, session, cutoff_at=now, limit=400) for tk in tickers},
        segments=segments, session=session, plans_by_id=plans_by_id, stop_events=stop_events)
    return {**out, 'calc_version': _discipline_module.DISCIPLINE_VERSION}


def discipline(*, now: datetime, since: date | None = None, account_id=REAL_ACCOUNT_ID) -> dict[str, Any]:
    """Execution discipline of recorded buys against the plans their fills follow (`discipline-2`)."""
    session = bars.latest_closed_session(now)
    since = since or session - timedelta(days=90)
    with core_tx.read_only_tx() as conn:
        # Buys and stop events share one window, or old buys see no stops and flag false early_takes.
        trades = [t for t in repository.list_trades(conn, cutoff=now, account_id=ledger._uuid(account_id, 'account_id'))
                  if t['voided_at'] is None and t['executed_at'].astimezone(NY).date() >= since]
        tickers = sorted({t['ticker'] for t in trades})
        segments = []
        for tk in tickers:
            segments += _book(conn, tk, account_id=account_id)['closed']
        out = _discipline_v2(conn, trades=trades, segments=segments, tickers=tickers, session=session, now=now,
                             since=since, account_id=account_id)
    return {'session': session, 'since': since, **out}


def trade_review(*, now: datetime) -> dict[str, Any]:
    session = bars.latest_closed_session(now)
    with core_tx._tx() as conn:
        spy = repository.read_bars(conn, BENCHMARK, session, cutoff_at=now, limit=400)
        closed, open_ = [], []
        for ticker in sorted({t['ticker'] for t in repository.list_trades(conn)}):
            book = _book(conn, ticker)
            ticker_bars = repository.read_bars(conn, ticker, session, cutoff_at=now, limit=400)
            closed += [{'ticker': ticker, **review.trade_segment_review(s, ticker_bars, spy)} for s in book['closed']]
            if book['position'] and ticker_bars:
                open_.append(review.open_position_review(ticker, book['position'], ticker_bars))
    return {'session': session, 'calc_version': review.CALC_VERSION, 'closed': closed, 'open': open_}


def _week_discipline(conn, session: date, now: datetime) -> dict[str, Any]:
    """Discipline over the same 90-day window the stop events use, in an existing transaction."""
    since = session - timedelta(days=90)
    trades = [t for t in repository.list_trades(conn)
              if t['voided_at'] is None and t['executed_at'].astimezone(NY).date() >= since]
    tickers = sorted({t['ticker'] for t in trades})
    segments = []
    for tk in tickers:
        segments += _book(conn, tk)['closed']
    return _discipline_v2(conn, trades=trades, segments=segments, tickers=tickers, session=session, now=now,
                          since=since)


def _track_agent_pick(conn, ticker: str, signal_date: date, decision: dict[str, Any] | None, *,
                      ready: bool) -> dict[str, Any]:
    """v2.5 §9.2: which Agent version the agent track takes — ``{run, plan, version_no, basis, latest}``.

    1. a decision with ``agent_plan_id`` → that version (``'decision'``);
    2. a decision without it (before v2.5) → the unit's agent-1 plan, else version 0 (``'first'``);
    3. undecided → version 0 (``'first'``), so a later revision never polishes the result.
    ``run`` is the run that produced the plan (agent-1: the latest propose run, as before); ``latest`` is the
    unit's newest agent plan (the chain head, or the agent-1 plan) for the unit's validity and pool. Units
    without an agent-2 chain read exactly as before v2.5; ``basis`` is None when no agent plan is taken."""
    versions = repository.chain_versions(conn, ticker, signal_date) if ready else []
    chosen_id = decision.get('agent_plan_id') if decision is not None else None
    if not versions:
        latest = repository.agent_plan(conn, ticker, signal_date)
        plan = repository.plan_by_id(conn, chosen_id) if chosen_id is not None else latest
        return {'run': repository.latest_agent_run(conn, ticker, signal_date), 'plan': plan, 'version_no': None,
                'basis': None if plan is None else ('decision' if chosen_id is not None else 'first'),
                'latest': latest}
    latest = repository.plan_by_id(conn, versions[-1]['plan_id'])
    by_plan = {v['plan_id']: v for v in versions}
    if chosen_id is not None and chosen_id not in by_plan:        # an agent-1 plan id beside a chain
        return {'run': repository.latest_agent_run(conn, ticker, signal_date),
                'plan': repository.plan_by_id(conn, chosen_id), 'version_no': None, 'basis': 'decision',
                'latest': latest}
    if chosen_id is None and decision is not None:
        legacy = next((p for p in repository.plans_for_unit(conn, ticker, signal_date)
                       if p['kind'] == 'agent' and p['plan_version'] == agent_graph.AGENT_VERSION), None)
        if legacy is not None:
            return {'run': repository.latest_agent_run(conn, ticker, signal_date), 'plan': legacy,
                    'version_no': None, 'basis': 'first', 'latest': latest}
    version = by_plan[chosen_id] if chosen_id is not None else versions[0]
    return {'run': repository.agent_run_by_id(conn, version['agent_run_id']),
            'plan': repository.plan_by_id(conn, version['plan_id']), 'version_no': version['version_no'],
            'basis': 'decision' if chosen_id is not None else 'first', 'latest': latest}


def _track_rows(conn, since: date, until: date, *, session: date, now: datetime,
                version: str = _tracks_module.TRACKS_V2, account_id=REAL_ACCOUNT_ID) -> list[dict[str, Any]]:
    """§4.4: one row per unit in the window — rule, agent and actual side by side, every id traceable. Bars and books
    are cached per ticker within the transaction; ids and dates are stringified so the rows freeze into JSONB
    snapshots as-is. ``version`` picks `tracks-2` (default, v2.9 D27) or the old `tracks-1` reading."""
    spy = repository.read_bars(conn, BENCHMARK, session, cutoff_at=now, limit=400)
    bars_by_ticker: dict[str, list[dict[str, Any]]] = {}
    ready = repository.timeline_ready(conn)
    contexts: dict[str, dict[str, Any]] = {}
    builder = _track_row if version == _tracks_module.TRACKS_V2 else _track_row_v1
    return [builder(conn, unit['ticker'], unit['signal_date'], spy=spy, bars_by_ticker=bars_by_ticker, ready=ready,
                    session=session, now=now, **({'contexts': contexts, 'account_id': account_id} if builder is _track_row else {}))
            for unit in repository.track_units(conn, since, until)]


def unit_track_row(conn, ticker: str, signal_date: date, *, session: date, now: datetime,
                   version: str = _tracks_module.TRACKS_V2) -> dict[str, Any]:
    """The track row of one unit, exactly the row ``_track_rows`` builds for it (the review card's frozen
    ``facts.tracks`` takes its three tracks from here, v2.7 D7). ``version='tracks-1'`` reads the old way (the check
    of a retro-1 card compares against it)."""
    spy = repository.read_bars(conn, BENCHMARK, session, cutoff_at=now, limit=400)
    ready = repository.timeline_ready(conn)
    if version == _tracks_module.TRACKS_V1:
        return _track_row_v1(conn, ticker, signal_date, spy=spy, bars_by_ticker={}, ready=ready, session=session,
                             now=now)
    return _track_row(conn, ticker, signal_date, spy=spy, bars_by_ticker={}, ready=ready, session=session, now=now,
                      contexts={})


def unit_fills(conn, ticker: str, signal_date: date, context: dict[str, Any]) -> list[dict[str, Any]]:
    """The live buys that belong to one unit, oldest first (D22, D27): the fills linked to any plan row of the unit
    (rule baseline, own plan, Agent version), the fills of the unit's own chain (``execution.chain_buys``: also those
    linked to a unit merged into it) and, for old data, the fills of the unit's decisions. ``context`` is
    ``plan_review.ticker_context`` of the ticker."""
    unit_plans = {p['id'] for p in repository.plans_for_unit(conn, ticker, signal_date)}
    legacy = repository.unit_decision_ids(conn, ticker, signal_date)
    chain = ({'ticker': ticker, 'signal_date': signal_date}
             if any(c['signal_date'] == signal_date for c in context['chains']) else None)
    owned = {str(b['id']): b for b in execution_module.chain_buys(
        (ticker, signal_date), context['trades'], plan_chain=context['plan_chain'],
        legacy_decision_ids=legacy)} if chain is not None else {}
    found = dict(owned)
    for t in context['trades']:
        if t['side'] != 'buy' or t.get('voided_at') is not None:
            continue
        if (t.get('plan_id') is not None and t['plan_id'] in unit_plans) or \
                (t.get('decision_id') is not None and t['decision_id'] in legacy):
            found[str(t['id'])] = t
    return sorted(found.values(), key=lambda t: (t['executed_at'], str(t['id'])))


def _track_row(conn, ticker: str, signal_date: date, *, spy: list[dict[str, Any]],
               bars_by_ticker: dict[str, list[dict[str, Any]]], ready: bool, session: date,
               now: datetime, contexts: dict[str, dict[str, Any]], account_id=REAL_ACCOUNT_ID) -> dict[str, Any]:
    """The `tracks-2` row of one unit (v2.9 D27): the Agent track takes the version the first fill was linked to
    (`fill`), an old decision's version (`decision`) or the first version; a unit merged into another chain has
    `merged` (no R); the actual track takes its plan from the fills' own plan link."""
    from investment_assistant.workbench import plan_review     # imports this module

    if ticker not in bars_by_ticker:
        bars_by_ticker[ticker] = repository.read_bars(conn, ticker, session, cutoff_at=now, limit=400)
    ticker_bars = bars_by_ticker[ticker]
    splits = repository.split_events(conn, ticker)
    context = contexts.get(ticker) or contexts.setdefault(ticker, plan_review.ticker_context(conn, ticker, now=now, account_id=account_id))
    rule = repository.rule_plan(conn, ticker, signal_date)
    legacy = repository.decision_head(conn, ticker, signal_date, timeline=ready) if str(account_id) == str(REAL_ACCOUNT_ID) else None
    versions = repository.chain_versions(conn, ticker, signal_date) if ready else []
    fills = unit_fills(conn, ticker, signal_date, context)
    merged = (not versions and repository.executable_ready(conn)
              and repository.retrigger_for_unit(conn, ticker, signal_date) is not None)

    def outcome_of(plan: dict[str, Any] | None) -> dict[str, Any] | None:
        if plan is None or plan['status'] != 'ok':
            return None
        return shadow_outcomes(plan, ticker_bars, spy, splits=splits)['outcome']

    if versions:
        pick = _tracks_module.agent_pick({'ticker': ticker, 'signal_date': signal_date}, legacy_decision=legacy,
                                         chain_buys=fills, versions=versions, merged=False)
        plan = repository.plan_by_id(conn, pick['plan_id']) if pick['plan_id'] is not None else None
        run = (repository.agent_run_by_id(conn, pick['plan']['agent_run_id']) if pick['plan'] is not None
               else repository.latest_agent_run(conn, ticker, signal_date))
        agent_plan = repository.plan_by_id(conn, versions[-1]['plan_id'])
        agent_track = _tracks_module.agent_track(run, plan, outcome_of(plan), version_no=pick['version_no'],
                                                 version_basis=pick['version_basis'])
    elif merged:
        agent_plan = None
        agent_track = _tracks_module.merged_agent_track()
    else:
        old = _track_agent_pick(conn, ticker, signal_date, legacy, ready=ready)
        agent_plan = old['latest']
        agent_track = _tracks_module.agent_track(old['run'], old['plan'], outcome_of(old['plan']),
                                                 version_no=old['version_no'], version_basis=old['basis'])

    rule_track = _tracks_module.plan_track(rule, outcome_of(rule))
    plans_by_trade = {}
    for fill in fills:
        linked = plan_review._plan_of_buy(conn, fill)
        if linked is not None:
            plans_by_trade[fill['id']] = linked
    book = context['book']
    buy_ids = {t['id'] for t in fills}
    actual = _tracks_module.actual_track_v2(
        legacy_decision=legacy, plans_by_trade=plans_by_trade, buys=fills,
        segments=[sg for sg in book['closed'] if sg['buy_trade_id'] in buy_ids],
        open_lots=[lot for lot in book['open'] if lot['buy_trade_id'] in buy_ids],
        last_close=Decimal(str(ticker_bars[-1]['close'])) if ticker_bars else None,
        splits=splits, session=session,
        unit_valid_until=max((p['valid_until'] for p in (rule, agent_plan, *plans_by_trade.values())
                              if p is not None), default=None))

    def context_of(plan):
        return (plan.get('inputs') or {}).get('context') if plan else None
    pool = review.pool_of(context_of(rule) or context_of(agent_plan),
                          repository.pool_entry_at(conn, ticker, signal_date, cutoff_at=now))
    return {
        'ticker': ticker, 'signal_date': signal_date.isoformat(), 'pool': pool,
        'rule': {**rule_track, 'plan_id': str(rule_track['plan_id']) if rule_track['plan_id'] else None},
        'agent': {**agent_track,
                  'run_id': str(agent_track['run_id']) if agent_track['run_id'] else None,
                  'plan_id': str(agent_track['plan_id']) if agent_track['plan_id'] else None},
        'actual': {**actual,
                   'decision_id': str(actual['decision_id']) if actual['decision_id'] else None,
                   'plan_id': str(actual['plan_id']) if actual['plan_id'] else None,
                   'buy_trade_ids': [str(tid) for tid in actual['buy_trade_ids']]},
    }


def _track_row_v1(conn, ticker: str, signal_date: date, *, spy: list[dict[str, Any]],
               bars_by_ticker: dict[str, list[dict[str, Any]]], ready: bool, session: date,
               now: datetime) -> dict[str, Any]:
    if ticker not in bars_by_ticker:
        bars_by_ticker[ticker] = repository.read_bars(conn, ticker, session, cutoff_at=now, limit=400)
    ticker_bars = bars_by_ticker[ticker]
    splits = repository.split_events(conn, ticker)
    rule = repository.rule_plan(conn, ticker, signal_date)
    decision = repository.decision_head(conn, ticker, signal_date, timeline=ready)
    pick = _track_agent_pick(conn, ticker, signal_date, decision, ready=ready)
    agent_plan = pick['latest']
    rule_outcome = (_plan_shadow_module.simulate(rule, ticker_bars, spy, splits=splits)
                    if rule is not None and rule['status'] == 'ok' else None)
    agent_outcome = (_plan_shadow_module.simulate(pick['plan'], ticker_bars, spy, splits=splits)
                     if pick['plan'] is not None and pick['plan']['status'] == 'ok' else None)
    rule_track = _tracks_module.plan_track(rule, rule_outcome)
    agent_track = _tracks_module.agent_track(pick['run'], pick['plan'], agent_outcome,
                                             version_no=pick['version_no'], version_basis=pick['basis'])
    chosen = (repository.plan_by_id(conn, decision['plan_id'])
              if decision is not None and decision.get('plan_id') else None)
    book = _book(conn, ticker)
    # A buy may link to any decision in the unit's chain: correcting a decision
    # after the fill must not drop the fill from the actual track.
    chain_ids = repository.unit_decision_ids(conn, ticker, signal_date) if decision is not None else set()
    buy_ids = ({t['id'] for t in repository.list_trades(conn, ticker)
                if t.get('voided_at') is None and t['side'] == 'buy'
                and t.get('decision_id') in chain_ids})
    actual = _tracks_module.actual_track(
        decision=decision, plan=chosen, buys=[{'id': tid} for tid in sorted(buy_ids, key=str)],
        segments=[s for s in book['closed'] if s['buy_trade_id'] in buy_ids],
        open_lots=[l for l in book['open'] if l['buy_trade_id'] in buy_ids],
        last_close=Decimal(str(ticker_bars[-1]['close'])) if ticker_bars else None,
        splits=splits, session=session,
        unit_valid_until=max((p['valid_until'] for p in (rule, agent_plan, chosen)
                              if p is not None), default=None))
    def context_of(plan):
        return (plan.get('inputs') or {}).get('context') if plan else None
    pool = review.pool_of(context_of(rule) or context_of(agent_plan),
                          repository.pool_entry_at(conn, ticker, signal_date, cutoff_at=now))
    return {
        'ticker': ticker, 'signal_date': signal_date.isoformat(), 'pool': pool,
        'rule': {**rule_track, 'plan_id': str(rule_track['plan_id']) if rule_track['plan_id'] else None},
        'agent': {**agent_track,
                  'run_id': str(agent_track['run_id']) if agent_track['run_id'] else None,
                  'plan_id': str(agent_track['plan_id']) if agent_track['plan_id'] else None},
        'actual': {**actual,
                   'decision_id': str(actual['decision_id']) if actual['decision_id'] else None,
                   'plan_id': str(actual['plan_id']) if actual['plan_id'] else None,
                   'buy_trade_ids': [str(tid) for tid in actual['buy_trade_ids']]},
    }


def tracks(*, now: datetime, since: date | None = None, until: date | None = None, account_id=REAL_ACCOUNT_ID) -> dict[str, Any]:
    """§4.5: the live three-track view, defaulting to the last 90 days of signal dates;
    not frozen — weekly and monthly snapshots carry their own copy."""
    session = bars.latest_closed_session(now)
    since = since if since is not None else session - timedelta(days=90)
    until = until if until is not None else session
    with core_tx.read_only_tx() as conn:
        rows = _track_rows(conn, since, until, session=session, now=now, account_id=ledger._uuid(account_id, 'account_id'))
    return {'session': session.isoformat(), 'since': since.isoformat(), 'until': until.isoformat(),
            'calc_version': _tracks_module.TRACKS_VERSION, 'units': rows,
            'summary': _tracks_module.summarize_v2(rows)}


def _weekly_learning(conn, week_start: date, week_end: date, *, session: date, now: datetime) -> dict[str, Any]:
    """The weekly 「你学到了什么」 (v2.7 §10, ``review-7``) from the same transaction as the rest of the snapshot:
    the week's review cards and the knowledge items whose cited plans reached an outcome that week. Without
    migration 027 it is the empty learning (all zeros), not a missing key."""
    from investment_assistant.workbench import my_knowledge, retro_cards

    cards: list[dict[str, Any]] = []
    perf: dict[int, dict[str, Any]] = {}
    if repository.growth_ready(conn):
        cards = retro_cards.learning_cards(conn, week_start, week_end)
        perf = my_knowledge.learning_perf(conn, session=session, now=now)
    return review.learning_stats(week_start=week_start, week_end=week_end, cards=cards, perf_by_item=perf)


def _week_units(conn, week_start: date, week_end: date, *, now: datetime) -> dict[str, Any]:
    """v2.9 D26 / D30: every unit of the week (a ticker-day with candidates, whatever became of it) with its
    `execution-1` state, the week's linked fills and the buys that follow no plan.

    ``units``: ``{id, ticker, signal_date, proposed_action, rule_ids, execution}``; ``fills``: ``{trade, unit_id,
    plan_created_at}`` for the live buys of those units; ``unplanned``: the live buys executed in the week with no plan
    link (and no plan through an old decision); ``groups``: the candidate groups the units come from."""
    from investment_assistant.workbench import plan_review     # imports this module

    groups = [g for g in repository.candidate_groups_since(conn, week_start) if g['signal_date'] <= week_end]
    contexts: dict[str, dict[str, Any]] = {}

    def context_of(ticker: str) -> dict[str, Any]:
        return contexts.get(ticker) or contexts.setdefault(ticker, plan_review.ticker_context(conn, ticker, now=now))

    units, fills = [], []
    owner: dict[Any, str] = {}          # a fill belongs to one unit: the unit that owns the chain wins over the unit whose
                                        # baseline it was linked to (R17 / D22)
    ordered = sorted(groups, key=lambda g: (g['signal_date'], g['ticker']))
    chain_first = sorted(ordered, key=lambda g: not any(c['signal_date'] == g['signal_date']
                                                         for c in context_of(g['ticker'])['chains']))
    mine_of: dict[tuple, list] = {}
    for group in chain_first:
        key = (group['ticker'], group['signal_date'])
        mine_of[key] = []
        for buy in unit_fills(conn, group['ticker'], group['signal_date'], context_of(group['ticker'])):
            if buy['id'] not in owner:
                owner[buy['id']] = f"{group['ticker']}:{group['signal_date'].isoformat()}"
                mine_of[key].append(buy)
    for group in ordered:
        ticker, signal_date = group['ticker'], group['signal_date']
        shape = synthesis.synthesize(ticker, signal_date, group['candidates'], holding=False, now=now)
        uid = f'{ticker}:{signal_date.isoformat()}'
        mine = mine_of[(ticker, signal_date)]
        # the execution state is a buy unit's; a sell / watch / conflict unit nobody traded has none (M3)
        state = 'not_executed' if shape['proposed_action'] == 'buy' and 'conflict' not in shape['labels'] \
            or mine else 'not_applicable'
        for buy in mine:
            plan = plan_review._plan_of_buy(conn, buy)
            # an own plan is written with the fill itself, so its age says nothing about how long you waited
            created = plan['created_at'] if plan and plan['kind'] != 'own' else None
            fills.append({'trade': buy, 'unit_id': uid, 'plan_created_at': created})
            day = buy['executed_at'].astimezone(NY).date()
            if plan is None or plan['valid_from'] <= day <= plan['valid_until']:
                state = 'adopted'
            elif state != 'adopted':
                state = 'out_of_validity'
        units.append({'id': uid, 'ticker': ticker, 'signal_date': signal_date,
                      'proposed_action': shape['proposed_action'], 'rule_ids': shape['rule_ids'],
                      'execution': state})
    unplanned = []
    for t in repository.list_trades(conn, cutoff=now):
        if t['side'] != 'buy' or t['voided_at'] is not None or not week_start <= t['executed_at'].astimezone(NY).date() <= week_end:
            continue
        legacy_plan = None
        if t.get('decision_id') is not None:
            decision = repository.decision_by_id(conn, t['decision_id'])
            legacy_plan = decision['plan_id'] if decision else None
        if execution_module.unplanned(t, legacy_plan_id=legacy_plan if t.get('plan_id') is None else t['plan_id']):
            unplanned.append(t)
    return {'groups': groups, 'units': units, 'fills': fills, 'unplanned': unplanned}


def generate_weekly(week_start: date, *, notes: str = '', supersedes_id=None, now: datetime) -> dict[str, Any]:
    """The `review-8` weekly snapshot (v2.9 D30): executions instead of decisions, the control group is every unit of
    the week, plans split by shadow version, tracks-2 and discipline-2 frozen."""
    if week_start.isoweekday() != 1:
        raise ledger.LedgerError('week_start must be a Monday')
    week_end = week_start + timedelta(days=6)
    session = bars.latest_closed_session(now)
    with core_tx._tx() as conn:
        week = _week_units(conn, week_start, week_end, now=now)
        units = week['units']
        outcomes = _outcomes(conn, units, session, now)
        spy = repository.read_bars(conn, BENCHMARK, session, cutoff_at=now, limit=400)
        segments = []
        for ticker in sorted({t['ticker'] for t in repository.list_trades(conn)}):
            ticker_bars = repository.read_bars(conn, ticker, session, cutoff_at=now, limit=400)
            segments += [{'ticker': ticker, **review.trade_segment_review(s, ticker_bars, spy)}
                         for s in _book(conn, ticker)['closed']
                         if week_start <= s['closed_at'].astimezone(NY).date() <= week_end]
        plan_outcomes = []
        week_plans = [p for p in repository.plans_since(conn, week_start) if p['signal_date'] <= week_end]
        chains = _plan_chains(conn, week_plans)        # review-6: first versions vs heads at generation
        for plan in week_plans:
            ticker_bars = repository.read_bars(conn, plan['ticker'], session, cutoff_at=now, limit=400)
            # Pool of a plan: the frozen context first, else the tier recorded on its signal date.
            plan_outcomes.append({**plan, 'chain': chains.get(plan['id']), 'pool': review.pool_of(
                (plan.get('inputs') or {}).get('context'),
                repository.pool_entry_at(conn, plan['ticker'], plan['signal_date'], cutoff_at=now)),
                'rule_ids': [c['rule_id'] for c in repository.unit_candidates(conn, plan['ticker'], plan['signal_date'])
                             if c['action'] == 'buy'],
                **shadow_outcomes(plan, ticker_bars, spy, splits=repository.split_events(conn, plan['ticker']))})
        triggers = []
        for group in week['groups']:
            for cand in group['candidates']:
                if cand['action'] != 'buy':
                    continue
                triggers.append({'pool': review.pool_of(
                    None, repository.pool_entry_at(conn, group['ticker'], group['signal_date'], cutoff_at=now)),
                    'rule_id': cand['rule_id']})
        track_rows = _track_rows(conn, week_start, week_end, session=session, now=now)
        stats = review.weekly_stats(week_start, units, outcomes, repository.list_trades(conn), segments=segments,
                                    fills=week['fills'], unplanned=week['unplanned'],
                                    entry_opens={k: v.get('entry_price') for k, v in outcomes.items()},
                                    plan_outcomes=plan_outcomes,
                                    # the by-pool plan columns never mix two shadow versions: triggers and the
                                    # plan-shadow-2 plans here, the older plans (no triggers) in by_pool_v1
                                    by_pool=review.pool_summary(triggers, [p for p in plan_outcomes if p['shadow_version'] == _plan_shadow_module.SHADOW_V2]),
                                    by_pool_v1=review.pool_summary([], [p for p in plan_outcomes if p['shadow_version'] != _plan_shadow_module.SHADOW_V2]),
                                    discipline=_week_discipline(conn, session, now),
                                    tracks={'units': track_rows, 'summary': _tracks_module.summarize_v2(track_rows)},
                                    learning=_weekly_learning(conn, week_start, week_end, session=session, now=now))
        stats['generated_at'] = now.isoformat()
        return review.save_weekly(conn, week_start, stats, notes=notes, supersedes_id=supersedes_id)


def _prev_month(m: date) -> date:
    return (m - timedelta(days=1)).replace(day=1)


def _spy_return_between(conn, start_day: date, end_day: date, *, now: datetime) -> float | None:
    index = {b['price_date']: b['adj_close'] for b in
             repository.read_bars(conn, BENCHMARK, end_day, cutoff_at=now, limit=400)}
    if start_day not in index or end_day not in index:
        return None
    return float(index[end_day] / index[start_day] - 1)


def generate_monthly(month: date, *, notes: str = '', supersedes_id=None, now: datetime) -> dict[str, Any]:
    """Freeze one month's review; corrections supersede. Missing snapshots show, never fake zeros."""
    from investment_assistant.config import load_config

    if month.day != 1:
        raise ledger.LedgerError('month must be the first day of the month')
    month_end = month.replace(day=[31, 29 if month.year % 4 == 0 and (month.year % 100 != 0 or month.year % 400 == 0)
                                   else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month.month - 1])
    params = load_config().workbench
    with core_tx._tx() as conn:
        repository.lock(conn, f'monthly:{month}')
        head = repository.monthly_head(conn, month)
        if (head['id'] if head else None) != supersedes_id:
            raise ledger.Conflict('该月已有更新的月度复盘；请更正最新一条')
        snapshots = [s for s in repository.list_snapshots(conn) if s['recorded_at'] <= now]
        from investment_assistant.workbench.paper_accounts import known_flows
        flows = known_flows(conn, REAL_ACCOUNT_ID, now)
        ledger_rows = repository.equity_days(conn, REAL_ACCOUNT_ID, cutoff_at=now) if repository.accounts_ready(conn) else []
        real_account = repository.account_by_id(conn, REAL_ACCOUNT_ID) if repository.accounts_ready(conn) else None
        opened = bool(real_account and real_account['opened_on'])
        def month_return(m):
            return monthly.month_return_v2(m, equity_rows=ledger_rows, snapshots=snapshots, flows=flows, opened=opened)
        months = [month]
        for _ in range(2):
            months.insert(0, _prev_month(months[0]))
        per_month, spy_ret = [], None
        for m in months:
            ret = month_return(m)
            if ret['status'] != 'ok':
                per_month.append({'month': m.isoformat(), 'return': None, 'spy_return': None})
                continue
            spy_r = _spy_return_between(conn, ret['start'].get('session', ret['start'].get('as_of')), ret['end'].get('session', ret['end'].get('as_of')), now=now)
            per_month.append({'month': m.isoformat(), 'return': ret['return'], 'spy_return': spy_r})
            if m == month:
                spy_ret = spy_r
        ret = month_return(month)

        from investment_assistant.workbench import plan_review     # imports this module

        trades = repository.list_trades(conn)
        trades_by_id = {t['id']: t for t in trades}
        tickers = sorted({t['ticker'] for t in trades})
        rs, segments_month, plan_outcomes = [], [], []   # rs: (closed_at, r), sorted below
        for ticker in tickers:
            book = _book(conn, ticker)
            splits = repository.split_events(conn, ticker)
            for seg in book['closed']:
                if not month <= seg['closed_at'].astimezone(NY).date() <= month_end:
                    continue
                segments_month.append({'ticker': ticker, **seg})
                buy_trade = trades_by_id.get(seg['buy_trade_id'])
                plan = plan_review._plan_of_buy(conn, buy_trade) if buy_trade is not None else None   # plan_id, else old decision
                if plan is None:
                    continue
                buy_day = buy_trade['executed_at'].astimezone(NY).date()
                stop = _discipline_module._at_basis(plan['stop'], plan['signal_date'], buy_day, splits)
                entry, exit_ = seg['entry_cost_per_share'], seg['exit_price']
                if entry != stop:
                    # P4 §5: sensitivity orders by close time, not ticker order
                    rs.append((seg['closed_at'], float((exit_ - entry) / (entry - stop))))
        spy_bars = repository.read_bars(conn, BENCHMARK, bars.latest_closed_session(now), cutoff_at=now, limit=400)
        month_plans = [p for p in repository.plans_since(conn, month) if p['signal_date'] <= month_end]
        chains = _plan_chains(conn, month_plans)       # monthly-3: first versions vs heads at generation
        for plan in month_plans:
            ticker_bars = repository.read_bars(conn, plan['ticker'], bars.latest_closed_session(now),
                                               cutoff_at=now, limit=400)
            plan_outcomes.append({**plan, 'chain': chains.get(plan['id']), **shadow_outcomes(
                plan, ticker_bars, spy_bars, splits=repository.split_events(conn, plan['ticker']))})
        disc = _discipline_v2(
            conn, trades=[t for t in trades if month <= t['executed_at'].astimezone(NY).date() <= month_end],
            segments=segments_month, tickers=tickers, session=bars.latest_closed_session(now), now=now,
            since=month, until=month_end)

        rs = [r for _, r in sorted(rs, key=lambda item: item[0])]
        track_rows = _track_rows(conn, month, month_end, session=bars.latest_closed_session(now), now=now)
        stats = {
            'calc_version': monthly.MONTHLY_VERSION, 'month': month.isoformat(), 'return': ret,
            'tier': monthly.tier(ret['return'], params.return_tiers) if ret['status'] == 'ok' else None,
            'spy': ({'return': spy_ret, 'excess': ret['return'] - spy_ret}
                    if ret['status'] == 'ok' and spy_ret is not None else None),
            'review_flag': monthly.review_flag(per_month),
            'review': monthly.review_detail(per_month),
            'sensitivity': {'approximate': True, 'count': len(rs), 'rows': monthly.sensitivity(rs)},
            'discipline': disc,
            **review.shadow_plan_summaries(plan_outcomes, month, month_end),
            'tracks': {'units': track_rows, 'summary': _tracks_module.summarize_v2(track_rows)},
            'generated_at': now.isoformat(),
        }
        return repository.insert_monthly_review(conn, month, monthly.MONTHLY_VERSION, stats, notes, supersedes_id)


def list_monthly(limit: int = 12) -> list[dict[str, Any]]:
    with core_tx._tx() as conn:
        return repository.monthly_heads(conn, limit)


def list_weekly(limit: int = 20) -> list[dict[str, Any]]:
    with core_tx._tx() as conn:
        return review.list_weekly(conn, limit)


# ---------------------------------------------------------------- agent track (P3 §8)

def _session_units(conn, session: date, *, now: datetime) -> dict[str, list[dict[str, Any]]]:
    """This session's undecided units in three groups (P4 §7.3): main-list
    conflict-free buys (propose), other main-list units (interpret), extended
    conflict-free buys (propose); each alphabetical, every item carrying its
    candidates for the fallback brief."""
    main_buy: list[dict[str, Any]] = []
    main_other: list[dict[str, Any]] = []
    extended: list[dict[str, Any]] = []
    for group in repository.candidate_groups_since(conn, session):
        if group['signal_date'] != session:
            continue
        ticker = group['ticker']
        if repository.decision_head(conn, ticker, group['signal_date']) is not None:
            continue
        book = _book(conn, ticker)
        unit = synthesis.synthesize(ticker, group['signal_date'], group['candidates'],
                                    holding=book['position'] is not None,
                                    now=now)
        if unit is None:
            continue
        conflict_free_buy = unit['proposed_action'] == 'buy' and 'conflict' not in unit['labels']
        is_extended = _unit_is_extended(conn, ticker, group['signal_date'],
                                        held=book['position'] is not None, now=now)
        entry = repository.pool_entry_at(conn, ticker, group['signal_date'], cutoff_at=now)
        item = {'ticker': ticker, 'signal_date': group['signal_date'],
                'pool': 'extended' if is_extended else 'main',
                'pool_tier': entry['tier'] if entry else 'core',
                'proposed_action': unit['proposed_action'], 'labels': unit['labels'],
                'candidates': group['candidates']}
        if is_extended:
            if conflict_free_buy:
                extended.append(item)
        elif conflict_free_buy:
            main_buy.append(item)
        else:
            main_other.append(item)
    for bucket in (main_buy, main_other, extended):
        bucket.sort(key=lambda u: u['ticker'])
    return {'main_buy': main_buy, 'main_other': main_other, 'extended': extended}


def _agent_units(conn, session: date, *, now: datetime) -> list[dict[str, Any]]:
    """§8.1 1–2: this session's undecided, conflict-free buy units (ATR-skipped rule
    plans included); main-list units first, then extended, each alphabetical."""
    groups = _session_units(conn, session, now=now)
    return groups['main_buy'] + groups['extended']


def _interpret_units(conn, session: date, *, now: datetime) -> list[dict[str, Any]]:
    """§7.1: this session's undecided main-list units that are not conflict-free
    buys (sell, watch, conflicted); alphabetical. Extended units never interpret."""
    return _session_units(conn, session, now=now)['main_other']


def _agent_run_row(unit: dict, *, settings, attempt: int, now: datetime, status: str,
                   run_id: str | None = None, reason: str | None = None, detail: str | None = None,
                   output: dict | None = None,
                   counter: dict | None = None, plan_id=None, input_tokens: int = 0,
                   output_tokens: int = 0, mode: str = 'propose', agent_version: str | None = None) -> dict:
    return {'id': run_id or str(uuid.uuid4()), 'ticker': unit['ticker'],
            'signal_date': unit['signal_date'], 'pool': unit['pool'], 'attempt': attempt,
            'mode': mode,
            # v2.5 §5.1 / v2.9 §5.5: first versions, revisions (and the skips standing in for them) are agent-3 runs
            'agent_version': agent_version or {'interpret': agent_graph.INTERPRET_VERSION,
                                               'review': agent_graph.REVIEW_VERSION,
                                               'retro': agent_graph.RETRO_AGENT_VERSION,
                                               'discuss': agent_graph.DISCUSS_AGENT_VERSION}.get(mode, agent_graph.AGENT_V3),
            'provider': settings.provider,
            'model': settings.model, 'cutoff_at': now, 'status': status, 'reason': reason, 'detail': detail,
            'output': output, 'counter': counter, 'plan_id': plan_id,
            'input_tokens': input_tokens, 'output_tokens': output_tokens,
            'started_at': now, 'finished_at': now}


def _brief_run(run: dict) -> dict:
    """The run as briefs.for_run should see it. briefs.for_run takes contra from ``counter.risks`` (the
    agent-1 shape); an agent-2 brief's contra is the first per-thesis counter instead (v2.5 Task 6), so the
    agent-2 counter is presented with ``counter.counter[:1]`` in that slot. Other runs pass through."""
    counter = run.get('counter')
    if run.get('agent_version') not in (agent_graph.AGENT_V2, agent_graph.AGENT_V3) or not isinstance(counter, dict):
        return run
    return {**run, 'counter': {'status': counter.get('status'), 'risks': list(counter.get('counter') or [])[:1]}}


def _skip(unit: dict, reason: str, *, now: datetime, settings, attempt: int, mode: str = 'propose') -> dict:
    """A skipped unit still gets its agent_runs row: every candidate has an answer or
    a reason. A skipped main-list unit also gets its rule-only brief (§7.3)."""
    run_row = _agent_run_row(unit, settings=settings, attempt=attempt, now=now,
                             status='skipped', reason=reason, mode=mode)
    with core_tx._tx() as conn:
        record = repository.insert_agent_run(conn, run_row, [])
        if unit['pool'] == 'main':
            repository.store_brief(conn, briefs.for_run(run_row, [], unit=unit,
                                                        candidates=unit['candidates']))
        return record


def _checkpoint_missing(exc: BaseException) -> bool:
    """A missing checkpoint table (setup not run) is checkpoint_unavailable, not a model
    error (spec §3.4); psycopg raises UndefinedTable, possibly wrapped in the chain."""
    try:
        import psycopg.errors
        table_errors = (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn)
    except ImportError:   # pragma: no cover - psycopg is a hard dependency in practice
        return False
    current, seen = exc, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, table_errors):
            return True
        current = current.__cause__ or current.__context__
    return False


def _merged_brief(unit: dict, *, now: datetime) -> dict[str, Any]:
    """The rule-only brief of a unit merged into an active chain (v2.9 §4.3): the unit is not proposed, so nothing
    but the rules speaks for it; no agent run stands behind it."""
    return {'ticker': unit['ticker'], 'signal_date': unit['signal_date'], 'model': None, 'prompt_version': 'rules-1',
            'cutoff_at': now, 'input_refs': [], 'output': briefs.rule_summary(unit, unit['candidates']),
            'status': 'fallback', 'error': agent_reason_text('active_chain'), 'agent_run_id': None}


def agent_reason_text(reason: str) -> str:
    from investment_assistant.workbench.agent import REASON_TEXT

    return REASON_TEXT.get(reason, reason)


def _previous_chain_facts(conn, ticker: str, signal_date: date, *, now: datetime) -> dict[str, Any]:
    """``timeline.previous_chain_block``'s input for the ticker's chains opened before ``signal_date``, read as the
    cutoff ``now`` knew them (spec §4.6): fills with ``list_trades(cutoff=now)`` (R11), bars up to the last session
    closed by ``now``, the version plan rows with the model's ``changes`` text, the closed segments of the chain's
    fills with the sale's recording time (so a sale recorded after the cutoff cannot show)."""
    from investment_assistant.workbench import plan_review     # imports this module

    session = bars.latest_closed_session(now)
    ctx = plan_review.ticker_context(conn, ticker, now=now)
    sale_recorded = {t['id']: t['recorded_at'] for t in ctx['trades'] if t['side'] == 'sell'}
    items = []
    for chain in ctx['chains']:
        if chain['signal_date'] >= signal_date:
            continue
        item = plan_review.read_chain_facts(conn, chain, session=session, now=now, context=ctx, stamped=True)
        if item is None:
            continue
        rows = []
        for version in item['chain']['versions']:
            plan = repository.plan_by_id(conn, version['plan_id'])
            run = repository.agent_run_by_id(conn, version['agent_run_id'])
            rows.append({**plan, 'version_no': version['version_no'], 'created_at': version['created_at'],
                         'changes': ((run or {}).get('output') or {}).get('changes') or []})
        buy_ids = {str(b['id']) for b in item['buys']}
        segments = [{**seg, 'sell_recorded_at': sale_recorded.get(seg['sell_trade_id'])}
                    for seg in ctx['book']['closed'] if str(seg['buy_trade_id']) in buy_ids]
        items.append({'chain': item['chain'], 'facts': item['facts'], 'plans': rows, 'segments': segments})
    return {'as_of_session': session, 'bars': repository.read_bars(conn, ticker, session, cutoff_at=now, limit=400),
            'spy_bars': repository.read_bars(conn, BENCHMARK, session, cutoff_at=now, limit=400),
            'splits': repository.split_events(conn, ticker), 'chains': items}


def _first_version_guard(conn, unit: dict, *, now: datetime) -> dict[str, Any] | None:
    """The check a first version needs under the ``chain-open:<ticker>`` lock just before it is written (v2.9
    §4.7): None when the ticker has no active chain, else ``{'found': the active chain}``. Judged at
    ``max(signal date, latest closed session)``. A merge row of the unit counts only through the chain it points
    at being active (R17: a unit merged into a chain that has ended may open its own)."""
    from investment_assistant.workbench import plan_review     # imports this module

    found = plan_review.active_chain_of(conn, unit['ticker'],
                                        session=max(unit['signal_date'], bars.latest_closed_session(now)), now=now)
    return {'found': found} if found is not None else None


def _run_agent_unit(unit: dict, *, now: datetime, deps: Deps, settings, attempt: int,
                    run_id: str | None = None) -> dict:
    """One unit through the agent-3 graph: the chain's first version (v2.5 §5.1, v2.9 §5.5). Plan, run, calls,
    version 0 and the brief share one transaction taken under the ``chain-open:<ticker>`` lock, which first checks
    again that the ticker has no active chain and the unit was not merged (§4.7; otherwise the proposal is recorded
    as a ``skipped / active_chain`` run with its tokens and the rule-only brief, and a merge row is written when
    the active chain is an earlier one). The proposer's message carries the 「同标的上一条链」 block (§4.6); its
    text and hash are kept in the run's ``output`` as ``input_digest.previous_chain`` (``agent_runs`` has no digest
    column; ``detail`` is the error text of a failed run and stays empty for an ok one). A model or checkpoint
    failure, or any failure while writing, lands as this unit's error row and never propagates. Needs migration
    026 (and 029 for the guard); ``run_id`` is the daily run's, stamped on a merge row written here."""
    from investment_assistant.config import load_config

    agent_run_id = str(uuid.uuid4())
    ticker, signal_date = unit['ticker'], unit['signal_date']

    def row(status: str, **kwargs) -> dict:
        return _agent_run_row(unit, settings=settings, attempt=attempt, now=now, status=status,
                              run_id=agent_run_id, **kwargs)

    def write(run_row: dict, calls: list[dict] | None = None) -> dict:
        with core_tx._tx() as conn:
            record = repository.insert_agent_run(conn, run_row, calls or [])
            if unit['pool'] == 'main':   # §7.1: main-list brief in the run's transaction
                repository.store_brief(conn, briefs.for_run(_brief_run(run_row), calls or [], unit=unit,
                                                            candidates=unit['candidates']))
            return record

    try:
        with core_tx._tx() as conn:
            bars_ = repository.read_bars(conn, ticker, signal_date, cutoff_at=now)
            block = timeline.previous_chain_block(_previous_chain_facts(conn, ticker, signal_date, now=now), now=now)
        digest = {'input_digest': {'previous_chain': timeline.previous_chain_digest(block)}}
        last_close = Decimal(str(bars_[-1]['close']))
        ctx = agent_tools.ToolContext(ticker=ticker, session=signal_date, cutoff_at=now, read=_read_only)
        with ExitStack() as stack:
            try:
                factory = deps.agent_checkpointer or (
                    lambda: agent_model.checkpointer(os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL', '')))
                checkpointer = stack.enter_context(factory())
            except Exception as exc:
                _, detail = agent_model.classify_error(exc, settings.api_key)
                return write(row('error', reason='checkpoint_unavailable', detail=detail))
            model_obj = deps.agent_model if deps.agent_model is not None else agent_model.chat_model(settings)
            try:
                result = agent_graph.run_unit_v2(unit=unit, ctx=ctx, model=model_obj,
                                                 checkpointer=checkpointer, run_id=agent_run_id,
                                                 last_close=last_close, as_of_session=signal_date,
                                                 now=lambda: now, version=timeline.AGENT_V3,
                                                 previous_chain=block)
            except Unavailable:
                raise
            except Exception as exc:
                reason, detail = agent_model.classify_error(exc, settings.api_key)
                if _checkpoint_missing(exc):
                    reason = 'checkpoint_unavailable'
                return write(row('error', reason=reason, detail=detail))
        if result['status'] == 'ok':
            with core_tx._tx() as conn:   # v2.5 §5.1: plan → run → calls → version 0 → brief, one transaction
                repository.lock(conn, f'chain-open:{ticker}')
                guard = _first_version_guard(conn, unit, now=now) if repository.executable_ready(conn) else None
                if guard is not None:       # §4.7: a chain opened (or the unit merged) since the plan node ran
                    found = guard.get('found')
                    if (found is not None and found['chain']['signal_date'] < signal_date
                            and repository.retrigger_for_unit(conn, ticker, signal_date) is None):
                        baseline = repository.rule_plan(conn, ticker, signal_date)
                        repository.insert_chain_retrigger(conn, {
                            'ticker': ticker, 'unit_signal_date': signal_date,
                            'chain_signal_date': found['chain']['signal_date'], 'chain_state': found['chain_state'],
                            'rule_ids': [c['rule_id'] for c in unit['candidates']],
                            'baseline_plan_id': baseline['id'] if baseline else None,
                            'entry_cap': timeline.retrigger_cap(found['chain_state'], signal_date),
                            'run_id': run_id or 'agent', 'timeline_version': timeline.TIMELINE_VERSION,
                            'created_at': now})
                    skipped = row('skipped', reason='active_chain', input_tokens=result['input_tokens'],
                                  output_tokens=result['output_tokens'])
                    record = repository.insert_agent_run(conn, skipped, result['calls'])
                    if unit['pool'] == 'main':
                        repository.store_brief(conn, briefs.for_run(_brief_run(skipped), result['calls'], unit=unit,
                                                                    candidates=unit['candidates']))
                    return record
                equity_source = sizing_equity(conn, signal_date, now=now)
                plan = timeline.build_version_plan(
                    output=result['output'], agent_run_id=agent_run_id, model=settings.model, ticker=ticker,
                    signal_date=signal_date, as_of_session=signal_date, version_no=0, supersedes_plan_id=None,
                    mode='entry', bars=bars_,
                    earnings_row=repository.latest_earnings(conn, ticker, cutoff_at=now),
                    snapshot=equity_source,
                    exposure=exposure(conn, signal_date, cutoff_at=now, ticker=ticker),
                    params=load_config().workbench,
                    context=plan_context(conn, ticker, signal_date, now=now), version=timeline.AGENT_V3)
                mark_sizing_source(plan, equity_source)
                plan_id = repository.store_plan(conn, plan)['record']['id']
                run_row = row('ok', plan_id=plan_id, output={**result['output'], **digest},
                              counter=result['counter'], input_tokens=result['input_tokens'],
                              output_tokens=result['output_tokens'])
                record = repository.insert_agent_run(conn, run_row, result['calls'])
                repository.insert_plan_version(conn, {
                    'plan_id': plan_id, 'ticker': ticker, 'signal_date': signal_date, 'version_no': 0,
                    'supersedes_plan_id': None, 'origin': 'signal', 'agent_run_id': agent_run_id,
                    'cutoff_at': run_row['cutoff_at'], 'as_of_session': signal_date, 'diff': None,
                    'schema_version': timeline.AGENT_V3})
                # v2.7 D18: the knowledge this plan cited, in the plan's own transaction (none: nothing written)
                repository.insert_knowledge_citations(conn, [
                    {**cited, 'agent_run_id': agent_run_id, 'plan_id': plan_id}
                    for cited in agent_growth_check.knowledge_citations(
                        output=result['output'], counter=result['counter'], mode='propose', calls=result['calls'])])
                if unit['pool'] == 'main':
                    repository.store_brief(conn, briefs.for_run(_brief_run(run_row), result['calls'], unit=unit,
                                                                candidates=unit['candidates']))
                return record
        return write(row(result['status'], reason=result['reason'],
                         output={**result['output'], **digest} if result['output'] else result['output'],
                         counter=result['counter'], input_tokens=result['input_tokens'],
                         output_tokens=result['output_tokens']), result['calls'])
    except Unavailable:
        raise
    except Exception as exc:   # the unexpected: this unit only, never the batch
        reason, detail = agent_model.classify_error(exc, settings.api_key)
        return write(row('error', reason=reason, detail=detail))


def _run_interpret_unit(unit: dict, *, now: datetime, deps: Deps, settings, attempt: int) -> dict:
    """One unit through the interpret graph (§7.2); error classification mirrors
    _run_agent_unit. The run (mode='interpret', no plan) and its brief share
    one transaction; model exceptions become this unit's error row."""
    run_id = str(uuid.uuid4())
    ticker, signal_date = unit['ticker'], unit['signal_date']

    def row(status: str, **kwargs) -> dict:
        return _agent_run_row(unit, settings=settings, attempt=attempt, now=now, status=status,
                              run_id=run_id, mode='interpret', **kwargs)

    def write(run_row: dict, calls: list[dict] | None = None) -> dict:
        with core_tx._tx() as conn:
            record = repository.insert_agent_run(conn, run_row, calls or [])
            repository.store_brief(conn, briefs.for_run(run_row, calls or [], unit=unit,
                                                        candidates=unit['candidates']))
            return record

    try:
        ctx = agent_tools.ToolContext(ticker=ticker, session=signal_date, cutoff_at=now, read=_read_only)
        with ExitStack() as stack:
            try:
                factory = deps.agent_checkpointer or (
                    lambda: agent_model.checkpointer(os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL', '')))
                checkpointer = stack.enter_context(factory())
            except Exception as exc:
                _, detail = agent_model.classify_error(exc, settings.api_key)
                return write(row('error', reason='checkpoint_unavailable', detail=detail))
            model_obj = deps.agent_model if deps.agent_model is not None else agent_model.chat_model(settings)
            try:
                result = agent_graph.run_interpret(unit=unit, ctx=ctx, model=model_obj,
                                                   checkpointer=checkpointer, run_id=run_id,
                                                   now=lambda: now)
            except Unavailable:
                raise
            except Exception as exc:
                reason, detail = agent_model.classify_error(exc, settings.api_key)
                if _checkpoint_missing(exc):
                    reason = 'checkpoint_unavailable'
                return write(row('error', reason=reason, detail=detail))
        return write(row(result['status'], reason=result['reason'], output=result['output'],
                         input_tokens=result['input_tokens'], output_tokens=result['output_tokens']),
                     result['calls'])
    except Unavailable:
        raise
    except Exception as exc:   # the unexpected: this unit only, never the batch
        reason, detail = agent_model.classify_error(exc, settings.api_key)
        return write(row('error', reason=reason, detail=detail))


def _agent_stage(session: date, *, now: datetime, deps: Deps, summary: dict[str, Any],
                 started: float, clock: Callable[[], float],
                 acc: stages_module.StageAcc | None = None, timeline_ready: bool | None = None,
                 run_id: str | None = None) -> str | None:
    """The agent track after all rule plans (§8.1): skip rules, then one unit at a time.

    v2.9 §4.3: a unit the `plan` node merged into an active chain (a ``chain_retriggers`` row) is not proposed — no
    run row, stage skip ``active_chain`` naming the chain — and gets the rule-only brief; ``summary['agent']
    ['merged']`` maps ticker -> chain signal date. ``run_id`` (the daily run's) stamps a retrigger row the
    first-version guard has to write itself (§4.7).

    ``acc`` (v2.4 §4) collects the stage accounting next to ``record`` and the skip branches. Returns the
    unavailable reason (disabled / not_configured) or None, so the caller can close an unavailable stage
    as skipped rather than as an empty success.

    ``timeline_ready`` (migration 026; read here when None): without it no first version can be written,
    so every propose unit gets a ``skipped / migration_026_missing`` run (main-list units still get their
    rule-only brief), interpretations run as usual, the stage is degraded and ``summary['errors']`` gets
    ``timeline: migration_026_missing`` once (v2.5 §6.5)."""
    t_agent = clock()
    settings = deps.agent_settings if deps.agent_settings is not None else agent_model.settings_from_env()
    if settings.config_error:
        summary['errors'].append(f'agent_config: {settings.config_error}')
    unavailable = agent_model.unavailable_reason(settings)
    ny_date = now.astimezone(NY).date()
    runs: dict[str, str] = {}
    briefs: dict[str, str] = {}
    tokens = {'input': 0, 'output': 0}
    quota_exhausted = False
    connection_lost: str | None = None     # §8.8: set by the first unit that finds the connection / model unusable

    def record(unit: dict, run: dict) -> None:
        nonlocal quota_exhausted, connection_lost
        if run.get('mode', 'propose') == 'propose':    # 7.3: runs only records propose
            runs[unit['ticker']] = run['status'] if run['status'] in ('ok', 'pass') else (
                f"{run['status']}: {run['reason']}")
        if unit['pool'] == 'main':
            # 7.3: agent.briefs is the run view; the top-level briefs key carries the brief row's status
            briefs[unit['ticker']] = run['status'] if run['status'] in ('ok', 'pass') else (
                f"{run['status']}: {run['reason']}")
            summary['briefs'][unit['ticker']] = brief_status(run)
        tokens['input'] += run['input_tokens'] or 0
        tokens['output'] += run['output_tokens'] or 0
        if acc is not None:
            acc.ref('agent_run_ids', run['id'])
            if run['status'] in ('ok', 'pass'):
                acc.output()
            if run['status'] == 'ok' and run.get('mode', 'propose') == 'propose' and run.get('plan_id') is not None:
                acc.ref('plan_ids', run['plan_id'])      # 只收本次 ok 运行新增的 Agent 计划
            elif run['status'] == 'skipped':
                acc.skip(run['reason'], unit['ticker'])
            elif run['status'] == 'error':
                acc.fail(unit['ticker'], run['reason'])
        if run['status'] == 'error':
            summary['sources']['agent'].append(unit['ticker'])
            if run['reason'] == 'quota_exhausted':
                quota_exhausted = True
            elif run['reason'] in CONNECTION_LOST:
                connection_lost = run['reason']

    def brief_status(run: dict) -> str:
        mode, status = run.get('mode', 'propose'), run['status']
        if mode == 'propose' and status in ('ok', 'pass'):
            return 'ok'
        if mode == 'interpret' and status == 'ok':
            return 'ok'
        if mode == 'interpret' and status == 'rejected':
            return 'error'
        return 'fallback'

    def run_one(unit: dict, mode: str, runner: Callable) -> None:
        """The three shared gates (§7.3: deadline, provider, quota, token budget);
        completion and attempts are counted per mode."""
        try:
            with core_tx._tx() as conn:
                history = repository.agent_runs_for(conn, unit['ticker'], unit['signal_date'], mode=mode)
                # D26: only the runs after the unit's latest reset count towards ``max_attempts``; the history
                # still counts for the ``attempt`` number, which keeps counting.
                resets = (repository.attempt_resets_for(conn, unit['ticker'], unit['signal_date'], mode)
                          if resets_ready else [])
            prior = [r for r in history if not resets or r['created_at'] > resets[0]['created_at']]
            done = ('ok', 'pass') if mode == 'propose' else ('ok',)
            if any(r['status'] in done for r in history) or (
                    mode == 'propose' and any(r['status'] == 'skipped' and r['reason'] == 'active_chain'
                                              for r in prior)):
                # a proposal set aside by the first-version guard (v2.9 §4.7) is done for the session: another
                # run would pay for the same model call and be set aside again (a reset re-opens it)
                if acc is not None:
                    acc.skip('already_completed', unit['ticker'])
                return
            if any(r['status'] == 'skipped' and r['reason'] == 'max_attempts' for r in prior):
                if acc is not None:
                    acc.skip('max_attempts', unit['ticker'])
                return
            attempt = len(history) + 1
            if clock() - started >= AGENT_DEADLINE_S:
                run = _skip(unit, 'stage_budget', now=now, settings=settings, attempt=attempt, mode=mode)
            elif unavailable is not None:
                run = _skip(unit, unavailable, now=now, settings=settings, attempt=attempt, mode=mode)
            elif quota_exhausted:
                run = _skip(unit, 'quota_exhausted', now=now, settings=settings, attempt=attempt, mode=mode)
            elif connection_lost:       # the rest of the node is skipped for the same reason as the unit that found out
                run = _skip(unit, connection_lost, now=now, settings=settings, attempt=attempt, mode=mode)
            elif len(prior) >= AGENT_MAX_ATTEMPTS:
                run = _skip(unit, 'max_attempts', now=now, settings=settings, attempt=attempt, mode=mode)
            elif settings.daily_tokens is not None:
                with core_tx._tx() as conn:
                    used = repository.agent_tokens_on(conn, ny_date)
                if used >= settings.daily_tokens:
                    run = _skip(unit, 'token_budget', now=now, settings=settings, attempt=attempt, mode=mode)
                else:
                    run = runner(unit, attempt)
            else:
                run = runner(unit, attempt)
            record(unit, run)
        except Unavailable:
            raise
        except Exception as exc:   # §2: an agent failure never drags the rule track
            summary['errors'].append(f"{unit['ticker']} agent: {exc}")
            if acc is not None:
                acc.fail(unit['ticker'], exc)

    merged: dict[str, str] = {}
    with core_tx._tx() as conn:
        groups = _session_units(conn, session, now=now)
        ready = repository.timeline_ready(conn) if timeline_ready is None else timeline_ready
        resets_ready = repository.daily_ready(conn)     # migration 028: the attempt-reset table exists
        merges_ready = repository.executable_ready(conn)        # migration 029: chain_retriggers exists
    if acc is not None:
        acc.input(sum(len(units) for units in groups.values()))
    if not ready:
        summary['errors'].append('timeline: migration_026_missing')
        if acc is not None:
            acc.degrade('migration_026_missing')

    def propose(unit: dict, attempt: int) -> dict:
        if not ready:       # the shared gates above still come first (deadline, provider, quota, attempts)
            return _skip(unit, 'migration_026_missing', now=now, settings=settings, attempt=attempt)
        return _run_agent_unit(unit, now=now, deps=deps, settings=settings, attempt=attempt, run_id=run_id)

    def propose_or_merge(unit: dict) -> None:
        """§4.3: a merged unit is not proposed; everything else goes through the shared gates."""
        if merges_ready:
            try:
                with core_tx._tx() as conn:
                    row = repository.retrigger_for_unit(conn, unit['ticker'], unit['signal_date'])
                    if row is not None and unit['pool'] == 'main' and repository.latest_brief(
                            conn, unit['ticker'], unit['signal_date']) is None:
                        repository.store_brief(conn, _merged_brief(unit, now=now))
            except Unavailable:
                raise
            except Exception as exc:   # §2: an agent failure never drags the rule track
                summary['errors'].append(f"{unit['ticker']} agent: {exc}")
                if acc is not None:
                    acc.fail(unit['ticker'], exc)
                return
            if row is not None:
                merged[unit['ticker']] = row['chain_signal_date'].isoformat()
                if acc is not None:
                    acc.skip('active_chain', f"{unit['ticker']}·{row['chain_signal_date'].isoformat()}")
                return
        run_one(unit, 'propose', propose)

    def interpret(unit: dict, attempt: int) -> dict:
        return _run_interpret_unit(unit, now=now, deps=deps, settings=settings, attempt=attempt)

    for unit in groups['main_buy']:          # §7.3: main propose, main interpret, extended propose
        propose_or_merge(unit)
    for unit in groups['main_other']:
        run_one(unit, 'interpret', interpret)
    for unit in groups['extended']:
        propose_or_merge(unit)

    summary['agent'] = {'runs': runs, 'briefs': briefs, 'tokens': tokens,
                       'quota_exhausted': quota_exhausted, 'merged': merged}
    if connection_lost:
        summary['agent']['connection_lost'] = connection_lost      # the review stage after this one honours it
    summary['timings']['agent'] = round(clock() - t_agent, 1)
    if acc is not None:
        acc.detail['tokens'] = dict(tokens)
        acc.detail['merged'] = len(merged)
        acc.duration_ms += (clock() - t_agent) * 1000
    return unavailable


class AgentConflict(RuntimeError):
    """The unit already has an ok/pass agent run (``already_completed``), needs no re-run (``not_needed``), or —
    v2.9 §4.7 — the ticker already has an active plan chain (``active_chain``; ``chain_signal_date`` names it)."""

    def __init__(self, message: str = '', *, chain_signal_date: date | None = None, version_no: int | None = None):
        super().__init__(message)
        self.chain_signal_date = chain_signal_date
        self.version_no = version_no


def _assert_unit_may_open(conn, ticker: str, signal_date: date, *, now: datetime) -> None:
    """A manual run (``run_agent``, ``rerun_agent``) may only open a chain when the ticker has no active one
    (v2.9 §4.7, D2): ``AgentConflict('active_chain')`` otherwise. The unit's own chain does not count (the
    already-ok guard answers for that), and a unit merged into a chain that has since ended may open its own
    (R17: the merge row only counts while the chain it points at is active). A no-op before migration 029."""
    from investment_assistant.workbench import plan_review     # imports this module

    if not repository.executable_ready(conn):
        return
    found = plan_review.active_chain_of(conn, ticker, session=max(signal_date, bars.latest_closed_session(now)),
                                        now=now)
    if found is not None and found['chain']['signal_date'] != signal_date:
        raise AgentConflict('active_chain', chain_signal_date=found['chain']['signal_date'],
                            version_no=found['chain']['versions'][-1]['version_no'])


def run_agent(ticker: str, signal_date: date, *, now: datetime, deps: Deps | None = None) -> dict[str, Any]:
    """Manual single-unit run (§10.3): not a conflict-free buy unit -> ValueError,
    already ok/pass -> AgentConflict; otherwise one run at the given cutoff.

    v2.5 §5.1: like the batch it only ever produces version 0. An ok run is exactly what writes
    version 0, so the existing ok/pass guard is also the "chain already exists" guard; revisions only
    come from reviews. Without migration 026 the run is recorded as ``skipped / migration_026_missing``."""
    deps = deps or Deps()
    settings = deps.agent_settings if deps.agent_settings is not None else agent_model.settings_from_env()
    with core_tx._tx() as conn:
        group = repository.unit_candidates(conn, ticker, signal_date)
        if not group:
            raise ValueError(f'{ticker} {signal_date} 没有候选，不是决策单元')
        book = _book(conn, ticker)
        unit = synthesis.synthesize(ticker, signal_date, group, holding=book['position'] is not None,
                                    now=now)
        if unit is None or unit['proposed_action'] != 'buy' or 'conflict' in unit['labels']:
            raise ValueError(f'{ticker} {signal_date} 不是无冲突的买入单元')
        prior = repository.agent_runs_for(conn, ticker, signal_date)
        if any(r['status'] in ('ok', 'pass') for r in prior):
            raise AgentConflict(f'{ticker} {signal_date} 已有 Agent 建议')
        _assert_unit_may_open(conn, ticker, signal_date, now=now)
        extended = _unit_is_extended(conn, ticker, signal_date, held=book['position'] is not None, now=now)
        entry = repository.pool_entry_at(conn, ticker, signal_date, cutoff_at=now)
        agent_unit = {'ticker': unit['ticker'], 'signal_date': signal_date,
                      'pool': 'extended' if extended else 'main',
                      'pool_tier': entry['tier'] if entry else 'core',
                      'proposed_action': unit['proposed_action'], 'labels': unit['labels'],
                      'candidates': group}
        ready = repository.timeline_ready(conn)
    unavailable = agent_model.unavailable_reason(settings)
    if unavailable is not None:
        return _skip(agent_unit, unavailable, now=now, settings=settings, attempt=len(prior) + 1)
    if not ready:
        return _skip(agent_unit, 'migration_026_missing', now=now, settings=settings, attempt=len(prior) + 1)
    return _run_agent_unit(agent_unit, now=now, deps=deps, settings=settings, attempt=len(prior) + 1)


RERUN_MODES = ('propose', 'interpret')
RERUN_ORIGINS = ('run_detail', 'plan_page', 'api')


def rerun_agent(ticker: str, signal_date: date, mode: str, *, request_id: str, origin: str, now: datetime,
                deps: Deps | None = None) -> dict[str, Any]:
    """Re-run the Agent of one unit whose attempts ran out or whose last run failed (v2.8 D26, §5.7).

    Appends an ``agent_attempt_resets`` row (``request_id`` makes a repeat harmless: the stored reset comes back with
    ``replayed: True`` and nothing runs again); ``propose`` then runs the unit once (``run_agent``), ``interpret``
    only resets — the next run or resume executes it. Returns ``{'reset_id', 'run_id' | None, 'replayed'}``.

    ``ValueError``: bad mode / origin / request id, or a ``propose`` unit that is not a conflict-free buy;
    ``LookupError``: no such unit; ``AgentConflict('not_needed')``: the unit's latest run of this mode is neither
    ``skipped / max_attempts`` nor ``error``; ``tasks.daily.DailyBusy`` (``propose`` only): a ``daily`` execution holds
    the lock and may be working on this very unit."""
    if mode not in RERUN_MODES:
        raise ValueError(f'mode 必须是 {" / ".join(RERUN_MODES)}')
    if origin not in RERUN_ORIGINS:
        raise ValueError(f'origin 必须是 {" / ".join(RERUN_ORIGINS)}')
    try:
        request_uuid = str(uuid.UUID(str(request_id)))
    except ValueError:
        raise ValueError('request_id 必须是 UUID') from None
    ticker = ticker.upper()
    if mode == 'propose':
        from investment_assistant.tasks import daily       # late: the task shell imports this module

        try:
            busy = daily.is_running()
        except Exception:      # noqa: BLE001 - no database to ask: the checks below report what is wrong
            busy = False
        if busy:               # the node being re-run may be working on this very unit
            raise daily.DailyBusy()
    with core_tx._tx() as conn:
        group = repository.unit_candidates(conn, ticker, signal_date)
        if not group:
            raise LookupError(f'{ticker} {signal_date} 没有候选，不是决策单元')
        for earlier in repository.attempt_resets_for(conn, ticker, signal_date, mode):
            if str(earlier['request_id']) == request_uuid:
                return {'reset_id': earlier['id'], 'run_id': None, 'replayed': True}
        if mode == 'propose':
            book = _book(conn, ticker)
            unit = synthesis.synthesize(ticker, signal_date, group, holding=book['position'] is not None, now=now)
            if unit is None or unit['proposed_action'] != 'buy' or 'conflict' in unit['labels']:
                raise ValueError(f'{ticker} {signal_date} 不是无冲突的买入单元')
            _assert_unit_may_open(conn, ticker, signal_date, now=now)     # before the reset row is written
        latest = repository.latest_agent_run(conn, ticker, signal_date, mode)
        merged_before = (mode == 'propose' and repository.executable_ready(conn)
                         and repository.retrigger_for_unit(conn, ticker, signal_date) is not None)
        if merged_before and (latest is None or (latest['status'] == 'skipped' and latest['reason'] == 'active_chain')):
            pass        # R17: merged (never proposed, or proposed and set aside), and the chain it joined is over
        elif latest is None or not (latest['status'] == 'error'
                                    or (latest['status'] == 'skipped' and latest['reason'] == 'max_attempts')):
            raise AgentConflict('not_needed')
        reset = repository.insert_attempt_reset(conn, {'ticker': ticker, 'signal_date': signal_date, 'mode': mode,
                                                       'request_id': request_uuid, 'origin': origin})
    run_id = None
    if mode == 'propose' and not reset['replayed']:      # a request id already used (by another unit): nothing runs
        run_id = str(run_agent(ticker, signal_date, now=now, deps=deps)['id'])
    return {'reset_id': reset['id'], 'run_id': run_id, 'replayed': bool(reset['replayed'])}


def _agent_view(run: dict[str, Any] | None, plan: dict[str, Any] | None,
                chain: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """One unit's agent block (§10.1; v2.5 §10.1): full suggestion on ok/pass, status and reason otherwise.

    For an agent-2 chain ``run`` / ``plan`` are the head version's run and plan (not the latest run) and
    ``chain`` is ``_chain_view``'s dict; an ok/pass view always carries ``chain`` (None without a chain).
    An agent-1 ok / pass run is marked ``legacy: True`` (D2: the unit has an agent-1 suggestion, read-only,
    never reviewed); an agent-1 run that was skipped / failed left no plan and takes the「Agent 未给出计划」
    path (§10.1)."""
    if run is None:
        return None
    if run['status'] in ('ok', 'pass'):
        view = {'id': str(run['id']), 'status': run['status'], 'model': run['model'],
                'agent_version': run['agent_version'], 'output': run['output'], 'counter': run['counter'],
                'plan': plan, 'input_tokens': run['input_tokens'], 'output_tokens': run['output_tokens'],
                'created_at': run['created_at'], 'chain': chain}
    else:
        view = {'id': str(run['id']), 'status': run['status'], 'reason': run['reason'],
                'created_at': run['created_at']}
    if run.get('agent_version') == agent_graph.AGENT_VERSION and run['status'] in ('ok', 'pass'):
        view['legacy'] = True
    return view


# --- agent-2 chains for the read views (v2.5 §5.3, §10) ---

CHAIN_REVIEW_PREVIEW = 40      # 「今日」进行中的 Agent 计划：最近复查的理由只显示前 40 字（§10.2）


def unit_buys(conn, ticker: str, signal_date: date) -> list[dict[str, Any]]:
    """Buys linked to any decision in the unit's decision chain (§5.3: the three-way comparison's reading,
    ``unit_decision_ids`` + ``trades.decision_id``). Voided rows are left in; ``chain_state`` drops them."""
    ids = repository.unit_decision_ids(conn, ticker, signal_date)
    if not ids:
        return []
    return [t for t in repository.list_trades(conn, ticker) if t['side'] == 'buy' and t.get('decision_id') in ids]


def chain_status(conn, ticker: str, signal_date: date, *, session: date,
                 book: dict[str, Any] | None = None, now: datetime | None = None,
                 context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """An agent chain read at session S: ``{'head', 'versions', 'reviews', 'state', 'latest_review'}``, or None
    when the unit has no agent chain. ``latest_review`` is the chain's newest effective review row (maintain /
    revise / invalidate) or None. The caller has checked ``repository.timeline_ready``.

    With ``now`` (the cutoff) the chain is judged by `timeline-2` (v2.9 spec §4.1): ``state`` is
    ``chain_state_v2``'s ``{state, mode, active, basis, basis_ref, timeline_version, renewable}`` and the result
    also carries ``chain`` / ``facts`` (its inputs, for the `plan` node and the review) and ``buys`` (the
    chain's live fills, read with ``list_trades(cutoff=now)``); ``context`` (``plan_review.ticker_context``) saves
    the ticker-wide reads when several chains of one ticker are read. Without ``now`` the `timeline-1` judge runs as
    before (the read views not yet moved to timeline-2; ``book`` may be passed in to save the trade read)."""
    head = repository.chain_head(conn, ticker, signal_date)
    if head is None:
        return None
    versions = repository.chain_versions(conn, ticker, signal_date)
    reviews = repository.chain_reviews(conn, ticker, signal_date)
    effective = [r for r in reviews if r['outcome'] in repository.EFFECTIVE_REVIEW_OUTCOMES]
    latest = effective[-1] if effective else None
    if now is not None:
        from investment_assistant.workbench import plan_review     # imports this module

        item = plan_review.read_chain_facts(conn, {'ticker': ticker, 'signal_date': signal_date}, session=session,
                                            now=now, context=context)
        return {'head': head, 'versions': versions, 'reviews': reviews, 'state': item['state'],
                'latest_review': latest, 'chain': item['chain'], 'facts': item['facts'], 'buys': item['buys']}
    book = book if book is not None else _book(conn, ticker)
    state = timeline.chain_state(versions=versions, head_plan=head['plan'], reviews=reviews,
                                 decision=repository.decision_head(conn, ticker, signal_date),
                                 unit_buys=unit_buys(conn, ticker, signal_date), open_lots=book['open'],
                                 session=session, legacy=False)
    return {'head': head, 'versions': versions, 'reviews': reviews, 'state': state, 'latest_review': latest}


def _review_view(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {'id': row['id'], 'session': row['session'], 'trigger': row['trigger'], 'outcome': row['outcome'],
            'decided_by': row['decided_by'], 'reason': row['reason'],
            'new_plan_id': str(row['new_plan_id']) if row['new_plan_id'] else None,
            'revision_status': (row.get('detail') or {}).get('revision_status'), 'created_at': row['created_at'],
            'conditions': row['conditions']}


def _chain_view(status: dict[str, Any]) -> dict[str, Any]:
    """The ``chain`` dict of a unit's agent block (§12 GET today); §10.1 also shows the head's generation
    time (``cutoff_at``) and its host diff against the previous version (``diff``, None for version 0)."""
    version = status['head']['version']
    return {'version_no': version['version_no'],
            'head_plan_id': str(status['head']['plan']['id']),
            'cutoff_at': version['cutoff_at'], 'diff': version['diff'],
            'state': status['state']['state'], 'mode': status['state']['mode'], 'basis': status['state']['basis'],
            'latest_review': _review_view(status['latest_review'])}


def _unit_agent(conn, ticker: str, signal_date: date, *, session: date, ready: bool,
                book: dict[str, Any] | None = None, now: datetime | None = None) -> dict[str, Any] | None:
    """The unit's agent block: the chain head's run and plan when there is an agent-2 chain, else the latest
    propose run and the unit's agent plan as before (agent-1 units, units without an ok run, migration 026
    missing). With ``now`` the chain is judged by `timeline-2`."""
    status = chain_status(conn, ticker, signal_date, session=session, book=book, now=now) if ready else None
    if status is None:
        return _agent_view(repository.latest_agent_run(conn, ticker, signal_date),
                           repository.agent_plan(conn, ticker, signal_date))
    run = repository.agent_run_by_id(conn, status['head']['version']['agent_run_id'])
    return _agent_view(run, status['head']['plan'], _chain_view(status))


def _active_chains(conn, session: date, *, now: datetime) -> list[dict[str, Any]]:
    """§10.2 进行中的 Agent 计划: the chains in the entry, holding or exiting phase at S (`timeline-2`, no decision
    needed any more)."""
    rows: list[dict[str, Any]] = []
    for chain in repository.all_chains(conn):
        ticker, signal_date = chain['ticker'], chain['signal_date']
        status = chain_status(conn, ticker, signal_date, session=session, now=now)
        if status is None:
            continue
        state = status['state']['state']
        if state not in timeline.ACTIVE_STATES:
            continue
        latest = status['latest_review']
        today_rows = [r for r in status['reviews'] if r['session'] == session]
        fired = [s['kind'] for s in (today_rows[-1]['signals'] or []) if s.get('fired')] if today_rows else []
        head = status['head']
        rows.append({
            'ticker': ticker, 'signal_date': signal_date, 'state': state, 'mode': status['state']['mode'],
            'basis': status['state']['basis'], 'version_no': head['version']['version_no'],
            'head_plan_id': str(head['plan']['id']), 'valid_until': head['plan']['valid_until'],
            'pool': chain['pool'],
            'latest_review': ({'outcome': latest['outcome'], 'decided_by': latest['decided_by'],
                               'created_at': latest['created_at'],
                               'reason': latest['reason'][:CHAIN_REVIEW_PREVIEW]} if latest else None),
            'signals_today': fired,
        })
    return rows


def held_chain_states(conn, session: date, *, tickers: set[str] | None = None,
                      now: datetime | None = None) -> dict[str, str]:
    """D7: held ticker -> state at S of the agent chain that holds the position: a chain in state holding or
    exiting, or one that ended while its buys still have open lots (invalidated by a review, closed by an old
    decision) — such a holding has a chain, it just is no longer reviewed. ``holding`` / ``exiting`` win over an ended
    state when a ticker has several. A held ticker absent from the result has no agent chain at all. ``tickers`` limits
    the read to those tickers. With ``now`` the chains are judged by `timeline-2` and their buys are the fills linked
    through ``plan_id`` or an old decision (``execution.chain_buys``); without it the `timeline-1` reading stays."""
    out: dict[str, str] = {}
    books: dict[str, dict[str, Any]] = {}
    contexts: dict[str, dict[str, Any]] = {}
    held_now: set[str] | None = None
    for chain in repository.all_chains(conn):
        ticker, signal_date = chain['ticker'], chain['signal_date']
        if tickers is not None and ticker not in tickers:
            continue
        if now is not None:
            from investment_assistant.workbench import plan_review     # imports this module

            if held_now is None:
                held_now = repository.open_position_tickers(conn)
            if ticker not in held_now:
                continue
            context = contexts.get(ticker) or contexts.setdefault(ticker, plan_review.ticker_context(conn, ticker, now=now))
            book = context['book']
            if book['position'] is None:
                continue
            status = chain_status(conn, ticker, signal_date, session=session, now=now, context=context)
            if status is None:
                continue
            buy_ids = {str(b['id']) for b in status['buys']}
        else:
            book = books.get(ticker) or books.setdefault(ticker, _book(conn, ticker))
            if book['position'] is None:
                continue
            status = chain_status(conn, ticker, signal_date, session=session, book=book)
            if status is None:
                continue
            buy_ids = {str(b['id']) for b in unit_buys(conn, ticker, signal_date) if b.get('voided_at') is None}
        state = status['state']['state']
        owns_lots = any(str(lot.get('buy_trade_id')) in buy_ids and Decimal(str(lot.get('quantity') or 0)) > 0
                        for lot in book['open'])
        if state not in ('holding', 'exiting') and not owns_lots:
            continue
        if out.get(ticker) not in ('holding', 'exiting'):
            out[ticker] = state
    return out


def _agent_status_line(run: dict[str, Any] | None) -> str | None:
    if run is None:
        return None
    return run['status'] if run['status'] in ('ok', 'pass') else f"{run['status']}: {run['reason']}"


def agent_runs(*, now: datetime, since: date | None = None) -> dict[str, Any]:
    """Run rows without result payloads, plus coverage: every buy unit in range has a run, is merged into an active
    chain (``coverage.merged``, counted as covered, v2.9 §4.3) or is missing."""
    session = bars.latest_closed_session(now)
    with core_tx._tx() as conn:
        start = since if since is not None else session - timedelta(days=14)
        runs = repository.list_agent_runs(conn, start)
        total = covered = 0
        missing: list[dict[str, Any]] = []
        merged: list[dict[str, Any]] = []
        merges_ready = repository.executable_ready(conn)
        for group in repository.candidate_groups_since(conn, start):
            ticker, signal_date = group['ticker'], group['signal_date']
            book = _book(conn, ticker)
            unit = synthesis.synthesize(ticker, signal_date, group['candidates'],
                                        holding=book['position'] is not None,
                                        now=now)
            if unit is None or unit['proposed_action'] != 'buy' or 'conflict' in unit['labels']:
                continue
            total += 1
            if repository.latest_agent_run(conn, ticker, signal_date) is not None:
                covered += 1
            elif merges_ready and (row := repository.retrigger_for_unit(conn, ticker, signal_date)) is not None:
                covered += 1        # v2.9 §4.3: merged into an active chain, so nothing to propose: not missing
                merged.append({'ticker': ticker, 'signal_date': signal_date,
                               'chain_signal_date': row['chain_signal_date']})
            else:
                missing.append({'ticker': ticker, 'signal_date': signal_date})
    return {'since': start, 'runs': runs,
            'coverage': {'units': total, 'covered': covered, 'merged': len(merged), 'merged_units': merged,
                         'missing': missing}}


def agent_run_detail(run_id: str) -> dict[str, Any]:
    """One run with every tool call, arguments, results and hashes (§10.3 traceability)."""
    with core_tx._tx() as conn:
        run = repository.agent_run_by_id(conn, run_id)
        if run is None:
            raise LookupError(f'运行 {run_id} 不存在')
        return {**run, 'id': str(run['id']), 'tool_calls': repository.agent_tool_calls(conn, run['id'])}


# --- review pushes (P4 §6.1, §6.3) ---

def _redact_reason(reason: str, notify_cfg) -> str:
    """Webhook URLs never survive into stored push reasons."""
    from investment_assistant.workbench.macro import redact

    for secret in (getattr(notify_cfg, 'webhooks', None) or {}).values():
        if secret:
            reason = redact(reason, str(secret))
    return reason


def _record_push(kind, period, *, review_id, trigger_source, result, notify_cfg, conn=None) -> dict[str, Any]:
    row = {'kind': kind, 'period': period, 'review_id': review_id, 'trigger_source': trigger_source,
           'sent': bool(result.get('sent')), 'messages': result.get('messages') or 0,
           'reason': _redact_reason(result['reason'], notify_cfg) if result.get('reason') else None}
    with core_tx._tx() as conn2:
        repository.insert_review_push(conn2, row)
    return {'kind': kind, 'period': period, 'review_id': review_id, 'sent': row['sent'],
            'messages': row['messages'], 'reason': row['reason']}


def push_review(kind: str, period: date, *, now: datetime, notify_cfg, trigger_source: str = 'manual',
                client=None) -> dict[str, Any]:
    """Push one period's latest frozen snapshot and record the attempt (§6.3).
    Manual pushes resend regardless of a previous success."""
    from investment_assistant.config import load_config
    from investment_assistant.notify import notifier, templates

    if kind not in ('weekly', 'monthly'):
        raise ledger.LedgerError('kind 必须是 weekly 或 monthly')
    if kind == 'weekly' and period.weekday() != 0:
        raise ledger.LedgerError('period 必须是周一')
    if kind == 'monthly' and period.day != 1:
        raise ledger.LedgerError('period 必须是当月 1 日')
    public_url = load_config().workbench.public_url
    with core_tx._tx() as conn:
        head = (repository.weekly_head(conn, period) if kind == 'weekly'
                else repository.monthly_head(conn, period))
    if head is None:
        raise ledger.NotFound(f'{period} 没有该周期的复盘快照')
    payload = (review_digest.weekly(head, public_url=public_url) if kind == 'weekly'
               else review_digest.monthly(head, public_url=public_url))
    messages = (templates.workbench_weekly if kind == 'weekly' else templates.workbench_monthly)(payload)
    result = notifier.send_notifications('workbench_review', messages, notify_cfg, client=client)
    return _record_push(kind, period, review_id=head['id'], trigger_source=trigger_source,
                        result=result, notify_cfg=notify_cfg)


def _tick_weekly(*, now: datetime, notify_cfg, trigger_source: str, client, push: bool = True) -> dict[str, Any]:
    """§6.1 weekly: reuse a snapshot frozen after the week's final close, else
    generate one (superseding whatever is there), then push."""
    week = review_digest.due_week(now)
    week_end = week + timedelta(days=6)
    with core_tx._tx() as conn:
        if repository.review_push_sent(conn, 'weekly', week):
            return {'period': week, 'action': 'already_sent'}
        head = repository.weekly_head(conn, week)
    final = review_digest.last_session(week, week_end)
    fresh = head is not None and final is not None and bars.latest_closed_session(head['created_at']) >= final
    if fresh:
        action = 'reused'
    else:
        head = generate_weekly(week, notes='自动生成', supersedes_id=head['id'] if head else None, now=now)
        action = 'generated'
    if not push:        # v2.8 §13.3: a manual run generates the snapshot but never pushes
        return {'period': week, 'action': action, 'push': 'manual_run'}
    sent = push_review('weekly', week, now=now, notify_cfg=notify_cfg, trigger_source=trigger_source,
                       client=client)
    return {'period': week, 'action': action, 'push': sent}


def _tick_monthly(*, now: datetime, notify_cfg, trigger_source: str, client, push: bool = True) -> dict[str, Any]:
    """With ledger month endpoints or a broker month-end snapshot, reuse-or-generate then push; without either,
    remind once from the first Saturday of the next month on — unless the month predates
    the workbench (no decision or account snapshot on or before its last day)."""
    from investment_assistant.notify import notifier, templates
    from investment_assistant.config import load_config

    month = review_digest.due_month(now)
    month_end = (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    with core_tx._tx() as conn:
        if repository.review_push_sent(conn, 'monthly', month):
            return {'period': month, 'action': 'already_sent'}
        reminded_before = repository.review_push_sent(conn, 'monthly_reminder', month)
        snapshots, flows = repository.list_snapshots(conn), repository.list_flows(conn)
        head = repository.monthly_head(conn, month)
        account_recorded = repository.latest_account_record_at(conn)
        started_on = repository.workbench_started_on(conn)
        real = repository.account_by_id(conn, REAL_ACCOUNT_ID) if repository.accounts_ready(conn) else None
        equity_rows = repository.equity_days(conn, REAL_ACCOUNT_ID, cutoff_at=now) if real else []
        ledger_ready = monthly.month_return_v2(month, equity_rows=equity_rows, snapshots=snapshots, flows=flows,
                                               opened=bool(real and real['opened_on'])).get('source') == 'ledger'
        if ledger_ready:
            account_recorded = max([r['computed_at'] for r in equity_rows]
                                   + ([account_recorded] if account_recorded else []))
    if not ledger_ready and monthly.month_end_snapshot(snapshots, month) is None:
        if started_on is None or started_on > month_end:
            return {'period': month, 'action': 'before_start'}
        if reminded_before:
            return {'period': month, 'action': 'waiting'}
        if not review_digest.reminder_due(month, now):
            return {'period': month, 'action': 'waiting'}
        if not push:
            return {'period': month, 'action': 'waiting', 'push': 'manual_run'}
        payload = review_digest.reminder(month, public_url=load_config().workbench.public_url)
        messages = templates.workbench_review_reminder(payload)
        result = notifier.send_notifications('workbench_review', messages, notify_cfg, client=client)
        push = _record_push('monthly_reminder', month, review_id=None, trigger_source=trigger_source,
                            result=result, notify_cfg=notify_cfg)
        return {'period': month, 'action': 'reminded', 'push': push}
    final = review_digest.last_session(month, month_end)
    fresh = (head is not None and final is not None
             and bars.latest_closed_session(head['created_at']) >= final
             and (account_recorded is None or head['created_at'] > account_recorded))
    if fresh:
        action = 'reused'
    else:
        head = generate_monthly(month, notes='自动生成', supersedes_id=head['id'] if head else None, now=now)
        action = 'generated'
    if not push:
        return {'period': month, 'action': action, 'push': 'manual_run'}
    sent = push_review('monthly', month, now=now, notify_cfg=notify_cfg, trigger_source=trigger_source,
                       client=client)
    return {'period': month, 'action': action, 'push': sent}


def review_tick(*, now: datetime, notify_cfg, trigger_source: str, client=None) -> dict[str, Any]:
    """The workbench_review task body (§6.1): weekly and monthly each in their own
    transaction; one failing never blocks the other, and errors land in the report."""
    out: dict[str, Any] = {'weekly': None, 'monthly': None, 'errors': []}
    for name, part in (('weekly', _tick_weekly), ('monthly', _tick_monthly)):
        try:
            out[name] = part(now=now, notify_cfg=notify_cfg, trigger_source=trigger_source, client=client)
        except Unavailable:
            raise
        except Exception as exc:
            out[name] = {'action': 'error', 'error': str(exc)}
            out['errors'].append(f'review {name}: {exc}')
    return out


def list_review_pushes(limit: int = 20) -> list[dict[str, Any]]:
    with core_tx._tx() as conn:
        rows = repository.list_review_pushes(conn, limit)
    return [{**row, 'id': str(row['id']), 'review_id': str(row['review_id']) if row['review_id'] else None,
             'period': row['period'].isoformat(), 'created_at': row['created_at'].isoformat()}
            for row in rows]
