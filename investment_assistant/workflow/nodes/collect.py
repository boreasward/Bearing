from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any

from investment_assistant.config import AssistantConfig
from investment_assistant.core import features as core_features
from investment_assistant.core import schema as core_schema, stages as stages_module, tx as core_tx
from investment_assistant.core.constants import BENCHMARK
from investment_assistant.core.redact import sanitize_reason
from investment_assistant.core.tx import Unavailable
from investment_assistant.db import list_watchlist_items
from investment_assistant.data import collect, registry as data_registry
from investment_assistant.data.adapter_support import adapter_deps_from_service
from investment_assistant.data.adapter_types import TargetFacts
from investment_assistant.data.adapters import fred as macro_module
from investment_assistant.data.sources.edgar import COMPANY_TICKERS_TARGET
from investment_assistant.execution import chains as execution_chains, repo as execution_repo
from investment_assistant.analysis import pool as pool_module, screen_rules as screen_module
from investment_assistant.workflow.state import _moment, _pool_from_state, _pool_state

ETF_TICKERS = pool_module.SECTOR_ETFS
# The order the data_fetch_runs rows are written and listed in (the v2.3 four, the range file, then the families
# v2.12 added); the fetches themselves run in registry (ADAPTERS) order, universe first because its members are
# the wide families' targets.
ROW_ORDER = ('bars', 'news', 'sec_filings', 'fred', 'universe', 'earnings', 'fundamentals', 'sec_text', 'sector')
COUNTED = ('bars', 'news', 'sec_filings', 'fred')   # the families the node's input / output / failure counts read


class _Family:
    """One registry family's run in this node: its tally and what ``run_adapter`` stored per target."""

    def __init__(self, family: str, job: str, targets=()):
        self.id = family
        self.tally = collect.Tally(family, job, targets)
        self.stored: dict[str, tuple[str, Any, dict[str, Any]]] = {}
        self.failure: str | None = None         # the whole family could not run (targets or dispatch)

    def observe(self, target: str, source: str, rows: Any, result: dict[str, Any]) -> None:
        self.stored[target] = (source, rows, result)

    def ok(self, target: str) -> bool:
        return target in self.tally.outcomes and self.tally.outcomes[target] is None

    def reason(self, target: str) -> str:
        return self.tally.outcomes.get(target) or self.failure or 'not attempted'

    def result(self, target: str) -> dict[str, Any]:
        return self.stored[target][2]

    def facts(self, target: str) -> dict[str, Any]:
        return getattr(self.stored[target][1], 'facts', {})


def run(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``collect`` node (v2.12 D15, spec §10): every fetched family runs through ``registry.run_adapter`` in
    registry order — primary source, the backup when the run's frozen ``backup_sources`` flag is on, then the visible
    degradation — with targets from the adapters' own ``daily`` rule over the host's frozen pool / holdings / chain
    facts (an explicit run keeps its manual override), the family's advisory lock, one ``Tally`` and one
    ``data_fetch_runs`` row each. The summary the later nodes read (v2.7 shape) is built from what the adapters stored.
    ``fundamentals`` / ``sec_text`` / ``sector`` / ``universe`` failures stay in their own rows (R22)."""
    clock = ctx.clock
    now, session = _moment(state)
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
        paper_ready = core_schema.accounts_ready(conn)
        five_layers = core_schema.five_layers_ready(conn)
        features = state.get('features')
        if features is None:                    # the v2.7 shell has no frozen switches: the current ones (default on)
            features = core_features.frozen(conn) if five_layers else dict.fromkeys(core_features.FLAGS, True)
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

    # v2.3 §6.2 / v2.4 §6.2: record_trigger holds the locks and writes data_fetch_runs; a recorder alone still builds
    # the tallies for the node's counts — no locks, no rows.
    book = record_trigger is not None or acc is not None
    trigger = record_trigger or 'scheduled'
    families = {adapter_id: _Family(adapter_id, job,
                                    macro_module.MACRO_SERIES if adapter_id == 'fred' else
                                    ('nasdaq100',) if adapter_id == 'universe' else ())
                for adapter_id in data_registry.ids()}
    if explicit:                                # no screen node -> no universe row
        del families['universe']

    t_collect = clock()
    # I07: the rows' timestamps are real wall clock (injectable), not the batch's cutoff ``now``; captured from the
    # moment the collection stage starts.
    if book:
        wall = ctx.wall_now or collect.wall_clock
        collect_started_at = wall()
    c_started = clock() if acc is not None else 0.0
    projected = adapter_deps_from_service(
        ctx.deps, transaction=core_tx._tx, config=ctx.config if isinstance(ctx.config, AssistantConfig) else None,
        sleep=collect.FETCH_SLEEP)

    def run_family(family: _Family, targets: list[str]) -> None:
        try:
            data_registry.run_adapter(data_registry.get(family.id), targets, scope='daily', trigger=trigger,
                                      run_id=run_id, session=session, now=now, deps=projected, features=features,
                                      tally=family.tally, observe=family.observe,
                                      whole_call_target=COMPANY_TICKERS_TARGET if family.id == 'sec_filings' else None)
        except Unavailable:
            raise
        except Exception as exc:    # noqa: BLE001 - a whole-family failure is the family's own row
            family.tally.record('*', exc)
            family.failure = sanitize_reason(exc)
        family.tally.detail.pop('scope', None)      # the run's own rows carry no scope label (D47: only sec_monitor,
                                                    # screen_new_members and the manual / request scopes are named)

    chain_tickers: list[str] = []
    collected: list[str] = []
    universe_meta: dict[str, Any] | None = None
    range_targets: list[str] = []
    with ExitStack() as locks:
        # v2.3 §6.4: one session advisory lock per locked family over the whole collection stage, on a dedicated
        # connection, in registry order (a manual fetch holds a single lock, so no deadlock).
        if record_trigger is not None:
            locks.enter_context(collect.family_locks(
                [a.id for a in map(data_registry.get, data_registry.ids()) if a.kind == 'fetched' and a.lock]))

        # The range (v2.8 D4): the Nasdaq-100 members outside the manual pool join the wide families' targets; the
        # screen node only reads them. Explicit tickers/pool stay a manual override and skip it entirely. An unreadable
        # file is the universe row's failure and no range (the screen node degrades on its own).
        loaded_tickers: tuple[str, ...] = ()
        if not explicit:
            universe = families['universe']
            run_family(universe, ['nasdaq100'])
            if universe.ok('nasdaq100'):
                loaded = universe.stored['nasdaq100'][1][0]
                loaded_tickers = tuple(loaded['tickers'])
                universe_meta = {'name': loaded['name'], 'as_of': loaded['as_of'].isoformat(),
                                 'tickers': list(loaded_tickers), 'fetched': [], 'fetch_reasons': {},
                                 'stale': universe.tally.detail['stale']}
        all_tickers = [*pool.tickers, *held]
        if not pool.core:
            summary['errors'].append('core_empty')
        # D21: tickers of active agent-2 chains outside the pool and holdings are collected only — bars, news,
        # earnings and the SEC listing — and never reach the rule loop.
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
        collected = [*all_tickers, *chain_tickers]
        head = [BENCHMARK, *ETF_TICKERS]
        if not explicit:
            range_targets = [t for t in loaded_tickers if t not in pool.tickers and t != screen_module.BENCHMARK
                             and t not in head]

        facts = TargetFacts(pool=tuple(pool.tickers), holdings=tuple(held), paper_holdings=(),
                            active_chains=tuple(chain_tickers), sector_etfs=tuple(ETF_TICKERS),
                            paper_accounts_ready=paper_ready, research_requests_ready=five_layers)
        universe_loader = lambda: SimpleNamespace(tickers=loaded_tickers)   # noqa: E731 - frozen by the universe row
        api_key = projected.fred_api_key
        monitored = set((((state.get('refs') or {}).get('sec_monitor')) or {}).get('sec_text') or ())

        for adapter_id in data_registry.ids():
            family = families.get(adapter_id)
            if family is None or adapter_id == 'universe':
                continue
            adapter = data_registry.get(adapter_id)
            if adapter.id == 'fred' and not api_key:
                # No key means not_configured: no request, no rows, a skipped row and one degradation.
                family.tally.detail.update(trigger=trigger, run_id=run_id)
                family.tally.skip('FRED_API_KEY 未配置')
                continue
            try:
                with core_tx._tx() as conn:
                    if adapter.id == 'fred':
                        targets = adapter.targets(conn, 'daily')
                    elif explicit:
                        targets = adapter.targets(conn, 'daily',
                                                  explicit=[*head, *collected] if adapter.id == 'bars' else collected)
                    else:
                        targets = adapter.targets(conn, 'daily', facts=facts, universe_loader=universe_loader)
            except Unavailable:
                raise
            except Exception as exc:    # noqa: BLE001 - no targets to resolve: the family's own failed row
                family.tally.record('*', exc)
                family.failure = sanitize_reason(exc)
                continue
            if adapter.id == 'sec_text' and monitored:      # D47: the monitor node already handled these this run
                targets = [t for t in targets if t not in monitored]
            run_family(family, targets)
        summary['timings']['collect'] = round(clock() - t_collect, 1)

    _summarise(summary, families, collected=collected, range_targets=range_targets, universe_meta=universe_meta,
               drop_after=drop_after, acc=acc, api_key=api_key)
    if acc is not None and not api_key:
        acc.degrade('fred_not_configured', 'FRED_API_KEY 未配置')

    families_view: list[dict[str, Any]] = []
    if book:
        collect_finished_at = wall()
        fetch_rows = {family_id: families[family_id].tally.row(
            trigger=trigger, run_id=run_id, session=session, started_at=collect_started_at,
            finished_at=collect_finished_at) for family_id in ROW_ORDER if family_id in families}
        families_view = [{'family': row['family'], 'status': row['status'], 'ok_count': row['ok_count'],
                          'target_count': row['target_count']} for row in fetch_rows.values()]
        if record_trigger is not None:
            summary['data_status'], fetch_ids = collect.write_runs_with_ids(list(fetch_rows.values()))
        else:
            fetch_ids = []           # 只记账不落 fetch 行（v2.4 §6.2）
        if acc is not None:
            # 计数与失败取自 v2.3 四个 Tally 的冻结行（universe 若有在其后，§4）。
            for family_id in COUNTED:
                row = fetch_rows[family_id]
                acc.input(row['target_count'])
                acc.output(row['ok_count'])
                for failure in row['failures']:
                    acc.fail(f"{row['family']}:{failure['target']}", failure['reason'])
            if fetch_ids:
                acc.set_ref('data_fetch_runs', fetch_ids)
            earnings, wanted = families['earnings'], set(collected)
            earnings_items = [item for item in fetch_rows['earnings']['detail'].get('targets', [])
                              if item['target'] in wanted]
            ok = sum(1 for t in collected if earnings.ok(t))
            acc.detail['earnings'] = {'targets': len(collected), 'ok': ok, 'failed': len(collected) - ok,
                                      'items': earnings_items}
            if drop_after is not None:
                acc.detail['after_cutoff_dropped'] = {
                    'news': sum(families['news'].result(t).get('after_cutoff_dropped', 0)
                                for t in collected if families['news'].ok(t)),
                    'sec': int((summary['filings'] or {}).get('after_cutoff_dropped') or 0)}
            acc.duration_ms += (clock() - c_started) * 1000
    return {'summary': summary, 'pool': _pool_state(pool), 'manual_pool': _pool_state(pool), 'held': held,
            'chain_tickers': chain_tickers, 'collected': collected, 'universe': universe_meta,
            'timeline_ready': ready, 'growth_ready': growth,
            'refs': {**(state.get('refs') or {}), 'collect': {'families': families_view}}}


def sec_listing_summary(sec: '_Family', *, drop_after) -> tuple[dict[str, Any] | None, str | None]:
    """The v2.7 SEC listing summary from what the ``sec_filings`` adapter stored: ``(filings, None)`` or, when the SEC
    ticker directory (or the whole family) could not be had and nothing was listed, ``(None, reason)``."""
    whole_call = sec.tally.call_failure or (sec.failure if not sec.tally.targets else None)
    if whole_call is not None:
        return None, whole_call
    filings: dict[str, Any] = {'inserted': 0, 'existing': 0, 'acceptance_filled': 0, 'by_ticker': {}, 'errors': {}}
    dropped = 0
    for target in sec.tally.targets:
        if sec.ok(target):
            result = sec.result(target)
            filled = result.get('acceptance_filled', 0)
            filings['inserted'] += result['inserted'] - filled
            filings['existing'] += result['existing']
            filings['acceptance_filled'] += filled
            filings['by_ticker'][target] = {'rows': result.get('listed', 0), 'latest': result['latest']}
            dropped += result.get('after_cutoff_dropped', 0)
        else:
            filings['errors'][target] = sec.reason(target)
    if drop_after is not None:
        filings['after_cutoff_dropped'] = dropped
    return filings, None


def _summarise(summary: dict[str, Any], families: dict[str, _Family], *, collected, range_targets, universe_meta,
               drop_after, acc, api_key) -> None:
    """The v2.7 collection summary from what the adapters stored, in the order the node always wrote it: benchmark and
    sector ETFs, the range's failures, each ticker (earnings error, then bars / news problems), SEC, FRED."""
    sources, errors = summary['sources'], summary['errors']
    bars, news, earnings = families['bars'], families['news'], families['earnings']

    def bar_counts(ticker: str) -> dict[str, int]:
        result = bars.result(ticker)
        return {'inserted': result['inserted'], 'unchanged': result['existing']}

    def skipped_bars(ticker: str) -> list[str]:
        return [d.isoformat() for d in bars.facts(ticker).get('skipped', ())]

    for ticker in [BENCHMARK, *ETF_TICKERS]:
        if bars.ok(ticker):
            if ticker == BENCHMARK:
                summary['benchmark'] = {'bars': bar_counts(ticker), 'skipped_bars': skipped_bars(ticker),
                                        'news': None, 'candidates': [], 'skips': [], 'error': None}
            else:
                summary['etfs'][ticker] = f"inserted {bars.result(ticker)['inserted']}"
            continue
        reason = bars.reason(ticker)
        if ticker == BENCHMARK:
            summary['benchmark'] = {'bars': None, 'skipped_bars': [], 'news': None, 'candidates': [],
                                    'skips': [], 'error': f'bars: {reason}'}
            sources['bars'].append(ticker)
            errors.append(f'{ticker} bars: {reason}')
        else:
            summary['etfs'][ticker] = f'error: {reason}'
            sources['etf_bars'].append(ticker)
            errors.append(f'{ticker} etf_bars: {reason}')

    fetched = {t for t in range_targets if bars.ok(t)}
    for ticker in range_targets:
        if ticker not in fetched:
            sources['bars'].append(ticker)
            if universe_meta is not None:
                universe_meta['fetch_reasons'][ticker] = bars.reason(ticker)
    if universe_meta is not None:
        universe_meta['fetched'] = sorted(fetched)

    for ticker in collected:
        entry: dict[str, Any] = {'bars': None, 'skipped_bars': [], 'news': None, 'candidates': [], 'skips': [],
                                 'error': None}
        problems = []
        if ticker in fetched:
            entry['bars'] = 'collected_in_screen'   # the range already fetched it; news and earnings count below
        elif bars.ok(ticker):
            entry['bars'], entry['skipped_bars'] = bar_counts(ticker), skipped_bars(ticker)
        else:
            problems.append(f'bars: {bars.reason(ticker)}')
            if ticker not in sources['bars']:
                sources['bars'].append(ticker)
        if news.ok(ticker):
            result = news.result(ticker)
            entry['news'] = {'inserted': result['inserted'], 'existing': result['existing'],
                             **{k: news.facts(ticker)[k] for k in ('fetched', 'skipped') if k in news.facts(ticker)}}
            if drop_after is not None:
                entry['news']['after_cutoff_dropped'] = result.get('after_cutoff_dropped', 0)
        else:
            problems.append(f'news: {news.reason(ticker)}')
            sources['news'].append(ticker)
        if earnings.ok(ticker):
            summary['earnings'][ticker] = 'unchanged' if earnings.result(ticker)['existing'] else 'inserted'
        else:   # 采集失败不写库，也不冒充无财报
            reason = earnings.reason(ticker)
            summary['earnings'][ticker] = f'error: {reason}'
            errors.append(f'{ticker} earnings: {reason}')
            sources['earnings'].append(ticker)
            if acc is not None:
                acc.fail(f'earnings:{ticker}', reason)
        if problems:
            entry['error'] = '; '.join(problems)
            errors.extend(f'{ticker} {p}' for p in problems)
        summary['tickers'][ticker] = entry

    filings, whole_call = sec_listing_summary(families['sec_filings'], drop_after=drop_after)
    if whole_call is not None:      # the SEC directory (or the whole family) could not be had: nothing was listed
        summary['filings'] = {'error': whole_call}
        errors.append(f'SEC listings: {whole_call}')
        sources['sec'] = ['all']
    else:
        summary['filings'] = filings
        _note_sec_errors(summary, filings)

    fred = families['fred']
    for series_id in macro_module.MACRO_SERIES:
        if not api_key:
            summary['macro'][series_id] = 'not_configured'
        elif fred.ok(series_id):
            result = fred.result(series_id)
            summary['macro'][series_id] = {'inserted': result['inserted'], 'unchanged': result['existing']}
        else:
            reason = fred.reason(series_id)
            summary['macro'][series_id] = f'error: {reason}'
            sources['fred'].append(series_id)
            errors.append(f'{series_id} fred: {reason}')
    for family_id, family in families.items():     # a family that could not run at all; its row says so too
        if family.failure is not None and family_id not in ('sec_filings', 'universe'):
            errors.append(f'{family_id}: {family.failure}')


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
