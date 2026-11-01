from __future__ import annotations

import os
from contextlib import ExitStack
from datetime import date, datetime
from typing import Any

from investment_assistant.core import schema as core_schema, stages as stages_module, tx as core_tx
from investment_assistant.core.constants import BENCHMARK, HISTORY_DAYS
from investment_assistant.core.tx import Deps, Unavailable
from investment_assistant.db import list_watchlist_items
from investment_assistant.data import collect, repo as data_repo
from investment_assistant.data.adapters import earnings, fred as macro_module, universe as universe_module
from investment_assistant.data.adapters import universe as universe_source
from investment_assistant.execution import chains as execution_chains, repo as execution_repo
from investment_assistant.analysis import pool as pool_module, screen_rules as screen_module
from investment_assistant.workflow import screen_collection as workflow_screen_collection
from investment_assistant.workflow.state import _moment, _pool_from_state, _pool_state

ETF_TICKERS = pool_module.SECTOR_ETFS

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
                    summary['earnings'][ticker] = data_repo.store_earnings(conn, ticker, row, now=now)['status']
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

def run(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``collect`` node (v2.8 §4.5): SPY and the sector ETFs, the Nasdaq-100 range (bars and earnings dates of the
    members outside the manual pool, ``workflow_screen_collection.collect_range``), then the pool, off-pool holdings and active chains
    (bars, news, earnings date), their SEC listings, FRED. The four family locks are held over the whole stage."""
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
        held = [t for t in sorted({r['ticker'] for r in execution_repo.list_trades(conn) if r['voided_at'] is None})
                if t not in watchlist]
        if core_schema.accounts_ready(conn):
            held = sorted(set(held) | (execution_repo.paper_tickers(conn) - set(watchlist)))
        ready = core_schema.timeline_ready(conn) if state.get('timeline_ready') is None else state['timeline_ready']
        growth = core_schema.growth_ready(conn) if state.get('growth_ready') is None else state['growth_ready']
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
                loaded = universe_source.load_for(deps)
            except universe_module.UniverseError as exc:
                if universe_tally is not None:
                    universe_tally.fail('nasdaq100', f'universe: {exc}')
            else:
                targets = [t for t in loaded.tickers if t not in pool.tickers and t != screen_module.BENCHMARK]
                fetched, fetch_failures, fetch_reasons = workflow_screen_collection.collect_range(
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
                    chain_tickers = execution_chains.chain_tickers(conn, session, now=now, exclude=all_tickers,
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
