"""Use cases behind the Data menu (design 2026-10-26 §7–8).

The fetch half: start_fetch is the route's entry — validate, refuse when migration
024 has not run, probe the family's advisory lock (409 when held) and submit the
background job. run_fetch is that job: hold the lock (universe only reads a file
and takes none), run the shared collectors over the family's targets, and append
exactly one data_fetch_runs row — no rules, plans, candidates, pool changes,
pushes or job_reports.

The read half: overview (adapter cards; pre-031 retains the legacy indicator projection), family_detail (card, the last 20
fetch runs, one coverage row per target) and browse (raw values over a fixed
window). All three run in one read-only transaction and never collect: a missing
ticker shows as「无数据」, not as a fetch or an error.
"""
from __future__ import annotations
from investment_assistant.data.adapters import universe as universe_source
import investment_assistant.core.dbutil as core_dbutil
import investment_assistant.core.schema as core_schema
import investment_assistant.data.repo as data_repo

import json
import os
from contextlib import ExitStack
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable

from investment_assistant import db
from investment_assistant.data import identity
from investment_assistant.core import runner, features
from investment_assistant.config import load_config
from investment_assistant.data import registry
from investment_assistant.data.adapter_types import Adapter, AdapterDeps
from investment_assistant.data.adapters import bars as bars_module
from investment_assistant.data import collect
from investment_assistant.workflow import targets as families
from investment_assistant.core import tx as service
from investment_assistant.core.constants import MIN_BARS_ATR, NY
from investment_assistant.derivation.indicators import price
from investment_assistant.data.adapters import fred as macro_module
from investment_assistant.data.adapters import universe as universe_module
from investment_assistant.core import tx as core_tx
from investment_assistant.core.redact import sanitize_reason
from investment_assistant.data import families as data_families


# Scheduling remains pre-Task4: do not label manual-only new adapters as already scheduled.
DAILY_FAMILIES = frozenset({'bars', 'news', 'sec_filings', 'earnings', 'fred', 'universe'})
LEGACY_FAMILIES = ('bars', 'sec_filings', 'news', 'fred', 'universe')
RAW_FAMILIES = frozenset(LEGACY_FAMILIES)


def _require_family_ready(conn, family_id):
    ready = core_schema.five_layers_ready(conn)
    if not ready and family_id not in LEGACY_FAMILIES and family_id != 'atr':
        raise service.Unavailable('需执行迁移 031')
    return ready


def _adapter_deps(deps: service.Deps) -> AdapterDeps:
    return AdapterDeps(transaction=core_tx._tx, bar_fetcher=deps.bar_fetcher,
                       news_fetcher=deps.news_fetcher, earnings_fetcher=deps.earnings_fetcher,
                       macro_fetcher=deps.macro_fetcher, filing_source=deps.filing_source,
                       universe=deps.universe, universe_loader=deps.universe_loader,
                       fred_api_key=os.environ.get('FRED_API_KEY'),
                       sec_text_dir=load_config().filings.output_dir, sleep=collect.FETCH_SLEEP)


class FetchBusy(RuntimeError):
    """The family is being collected right now (the route maps this to 409)."""


def _now() -> datetime:
    return datetime.now(UTC)


def validate_fetch(family_id: str, ticker: str | None) -> tuple[str, str | None]:
    """(family_id, normalized ticker) or the route's error: unknown family LookupError;
    derived family / ticker on a non-per-ticker family / non-string or invalid
    ticker ValueError."""
    if family_id == 'atr':
        raise ValueError('派生族，请拉取日线')
    family = data_families.get(family_id)
    if ticker is not None and not isinstance(ticker, str):
        raise ValueError('ticker 必须是字符串')
    if ticker is not None and not family.per_ticker:
        raise ValueError('该族不支持单只拉取')
    if ticker is not None:
        ticker = ticker.strip().upper()
        try:
            identity.stock_id(ticker)
        except ValueError:
            raise ValueError(f'非法 ticker：{ticker}') from None
    return family_id, ticker


def _unavailable_on_connection_error(exc: Exception) -> bool:
    """True for a dead/unreachable database — psycopg's OperationalError family,
    which the pool's PoolTimeout also belongs to — so POST matches GET's 503
    contract (I06)."""
    import psycopg

    return isinstance(exc, psycopg.OperationalError)


def start_fetch(family_id: str, ticker: str | None, *, now_fn: Callable[[], datetime] | None = None,
                deps: service.Deps | None = None) -> dict[str, Any]:
    """Submit one family fetch in the background and return {'run_id', 'status': 'pending'}.

    The lock is only probed here, then released: the background task takes it for
    real, so a batch starting in between ends as {'status': 'busy'} instead of two
    writers racing on the data tables (§6.4).

    Before anything is submitted the database must be provably writable: the
    workbench tables and migration 024 are checked in this same safe transaction,
    and a failed probe unlock discards the connection instead of returning a
    locked session to the pool (I06, I01)."""
    family_id, ticker = validate_fetch(family_id, ticker)
    now_fn = now_fn or _now
    try:
        with core_tx.connection() as conn:
            missing = core_dbutil.missing_tables(conn)
            if missing:
                raise service.Unavailable(
                    '工作台数据表未就绪（需执行迁移 016–020）：' + ', '.join(missing))
            if not core_schema.fetch_runs_ready(conn):
                raise service.Unavailable('需执行迁移 024')
            _require_family_ready(conn, family_id)
            if registry.get(family_id).lock:
                if not collect.try_family_lock(conn, family_id):
                    raise FetchBusy('该族正在采集')
                try:
                    collect.unlock_family(conn, family_id)
                except Exception:
                    collect.discard_connection(conn)   # never return a locked session (I01)
                    raise
    except service.Unavailable:
        raise
    except Exception as exc:
        if _unavailable_on_connection_error(exc):
            raise service.Unavailable('数据库不可达') from None
        raise

    def job(run_id: str) -> dict[str, Any]:
        return run_fetch(family_id, ticker, run_id=run_id, now=now_fn(), deps=deps)

    run_id = runner.submit(f'data:{family_id}', job, with_run_id=True)
    return {'run_id': run_id, 'status': 'pending'}


def run_fetch(family_id: str, ticker: str | None, *, run_id: str | None, now: datetime,
              deps: service.Deps | None = None,
              wall_now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """The background body: hold the family lock, collect, append one manual_family row.

    Validated again so direct callers cannot bypass start_fetch's checks. The lock
    is held over the whole collection on a dedicated connection; without it the
    fetch reports busy and writes nothing. Per-target failures land in the row,
    never abort the family. The row's timestamps are the real wall clock
    (injectable, I07); ``now`` stays the data cutoff."""
    family_id, ticker = validate_fetch(family_id, ticker)
    deps = deps or service.Deps()
    wall = wall_now or collect.wall_clock
    session = bars_module.latest_closed_session(now)
    started_at = wall()
    with ExitStack() as stack:
        if registry.get(family_id).lock:
            lock_conn = stack.enter_context(core_tx.connection())
            if not collect.try_family_lock(lock_conn, family_id):
                return {'status': 'busy'}
            stack.callback(collect.release_family, lock_conn, family_id)
        else:
            lock_conn = None
        tally = _collect_family(family_id, ticker, session=session, now=now, deps=deps,
                                run_id=run_id)
    row = tally.row(trigger='manual_family', run_id=run_id, session=session, started_at=started_at,
                    finished_at=wall(), scope='ticker' if ticker is not None else 'family',
                    ticker=ticker)
    return {'status': row['status'], 'row': row, 'data_status': collect.write_runs([row])}


def _collect_family(family_id: str, ticker: str | None, *, session, now: datetime,
                    deps: service.Deps, run_id: str | None) -> collect.Tally:
    """One frozen host context, then the single adapter dispatch path."""
    adapter = registry.get(family_id)
    projected = _adapter_deps(deps)
    with core_tx._tx() as conn:
        ready = _require_family_ready(conn, family_id)
        flags = features.frozen(conn) if ready else {'backup_sources': False}
        selected, notes = families.targets(conn, family_id, session=session, now=now,
            scope='manual' if ticker is not None else 'daily',
            explicit=[ticker] if ticker is not None else None,
            universe_loader=lambda: universe_source.load_for(deps))
    tally = collect.Tally(family_id, 'workbench', selected)
    tally.detail['target_notes'] = notes
    tally.detail['feature_context'] = 'frozen' if ready else 'pre_031_primary_only'
    return registry.run_adapter(adapter, selected, scope='manual', trigger='manual_family',
        run_id=run_id, session=session, now=now, deps=projected, features=flags, tally=tally)


# ---------------------------------------------------------------- read half (v2.3 §8)

# Browse windows are fixed display definitions (spec §8), never rule parameters.
BROWSE_BARS_LIMIT = 120
BROWSE_ATR_LIMIT = 60
BROWSE_NEWS_DAYS = 60
BROWSE_FILINGS_DAYS = 180
BROWSE_FRED_LIMIT = 120
# news/sec browse read their whole window with no second, silent cap (I10): the
# SQL filters by source first, so the 8-K listings are never pushed out by
# yfinance headlines.
BAR_NOTE = '拆股调整、未分红调整；adj_close 全调整'
ATR_NOTE = '现算，与 `plan-1` 同一函数；不入库'


# The read-only transaction lives in core.tx now; the old private name stays for existing callers
# (resolved through the module so test patches on core.tx reach it).
def _read_tx():
    return core_tx.read_only_tx()


def _ny_midnight(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=NY)


def _latest_map(conn, family_id: str, targets: list[str], *, now: datetime) -> dict[str, Any]:
    """Per target the family's newest data point visible at ``now`` (spec §4「最新时点」)."""
    return registry.get(family_id).latest(conn, targets, cutoff_at=now)


def _last_run_view(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {'status': row['status'], 'trigger': row['trigger'], 'scope': row['scope'],
            'ticker': row['ticker'], 'job': row['job'],
            'reason': (row.get('detail') or {}).get('reason'),
            'finished_at': row['finished_at'],
            'ok_count': row['ok_count'], 'target_count': row['target_count'],
            'failures': (row['failures'] or [])[:5]}


def _newer(row: dict[str, Any] | None, than: dict[str, Any] | None) -> bool:
    """Whether ``row`` was recorded after ``than`` (the table's own newest-first order)."""
    if row is None:
        return False
    return than is None or (row['created_at'], row['id']) > (than['created_at'], than['id'])


def _run_facts(conn, ready: bool) -> dict[str, dict[str, Any]]:
    """The fetch-run facts every card reads: newest family-scope and ticker-scope rows
    per family, and the newest family-scope success. Empty before migration 024."""
    if not ready:
        return {'family': {}, 'ticker': {}, 'success_at': {}}
    return {'family': data_repo.latest_fetch_runs(conn, scope='family'),
            'ticker': data_repo.latest_fetch_runs(conn, scope='ticker'),
            'success_at': data_repo.last_success_at(conn, scope='family')}


def _atr_lagging(latest, count, session):
    return latest is None or latest < session or count is None or count < MIN_BARS_ATR


def _card(conn, family: Adapter, *, session: date, now: datetime, jobs, run_facts,
          ready: bool, atr: bool = False) -> dict[str, Any]:
    """One family's overview card: coverage and freshness computed at read time.

    The fetch-run lines (2026-10-27, after acceptance): ``last_run`` is the newest
    family-scope run, so a single-ticker probe never stands in for the family; a
    single-ticker run newer than it shows apart as ``last_ticker_run`` and folds away
    once a later family run lands; ``last_success_at`` counts family runs only."""
    notes: list[str] = []
    current_error: str | None = None
    if family.id == 'universe':
        try:
            loaded = universe_module.load()
        except universe_module.UniverseError as exc:
            # 范围文件不可读/非法：覆盖 0、时点为空；真实原因独立暴露，不伪造拉取行（M03）
            notes.append('universe_unavailable')
            current_error = sanitize_reason(exc)
            coverage, latest_at = {'targets': 0, 'covered': 0, 'lagging': []}, None
        else:
            coverage = {'targets': len(loaded.tickers), 'covered': len(loaded.tickers), 'lagging': []}
            latest_at = loaded.as_of
            if universe_module.is_stale(loaded, session):
                notes.append('universe_stale')
    else:
        targets, target_notes = families.targets(conn, family.id, session=session, now=now)
        notes.extend(target_notes)
        latest = _latest_map(conn, family.id, targets, now=now)
        counts = (data_repo.bar_counts(conn, targets, end_date=session, cutoff_at=now)
                  if atr else None)
        if atr:
            lagged = [t for t in targets if _atr_lagging(latest.get(t), (counts or {}).get(t), session)]
            coverage = {'targets': len(targets), 'covered': len(targets) - len(lagged), 'lagging': lagged}
        else:
            coverage = data_families.coverage(family.id, targets, latest, session=session)
        latest_at = max(latest.values()) if latest else None
        if family.id == 'sec_filings':
            notes.append('no_filing_is_not_missing')   # 无申报 ≠ 缺数据
    recorded = family.id
    family_run = run_facts['family'].get(recorded)
    ticker_run = run_facts['ticker'].get(recorded)
    job_row = jobs.get('daily') if family.id in DAILY_FAMILIES else None
    return {
        'id': 'atr' if atr else family.id, 'title': 'ATR' if atr else family.title,
        'purpose': '用于 `plan-1` 止损与仓位、`screen-1` 的 ATR 区间和移动止损。' if atr else family.purpose,
        'kind': 'derived' if atr else family.kind,
        'fetch_via': 'bars' if atr else None, 'per_ticker': False if atr else family.per_ticker,
        'job': ({'name': job_row['name'], 'enabled': job_row['enabled'],
                 'next_run_at': job_row['next_run_at']} if job_row is not None else None),
        'coverage': coverage, 'latest_at': latest_at,
        'coverage_note': ('覆盖 = 有 ≥ 15 根日线且最新一根为最近已收盘交易日的标的数。' if atr
                          else family.coverage_note),
        'last_run': _last_run_view(family_run),
        'last_ticker_run': _last_run_view(ticker_run) if _newer(ticker_run, family_run) else None,
        'last_success_at': run_facts['success_at'].get(recorded),
        'evidence': (data_families.EVIDENCE + ' `plan-1` 的计算输入，规则 `gate_hold`'
                     if atr else data_families.EVIDENCE),
        'notes': notes,
        'current_error': current_error,
        'fetch_runs_ready': ready,
    }


def overview(*, now: datetime) -> dict[str, Any]:
    """Registry cards when 031-ready; old data/ATR projections remain readable before migration."""
    session = bars_module.latest_closed_session(now)
    with core_tx.read_only_tx() as conn:
        jobs = {job['name']: job for job in db.list_scheduled_jobs(conn)}
        ready = core_schema.fetch_runs_ready(conn)
        run_facts = _run_facts(conn, ready)
        adapters = (registry.ADAPTERS if core_schema.five_layers_ready(conn)
                    else tuple(registry.get(id_) for id_ in LEGACY_FAMILIES))
        cards = [_card(conn, family, session=session, now=now, jobs=jobs,
                       run_facts=run_facts, ready=ready)
                 for family in adapters]
        if len(adapters) == len(LEGACY_FAMILIES):
            cards.insert(3, _card(conn, registry.get('bars'), session=session, now=now, jobs=jobs,
                                  run_facts=run_facts, ready=ready, atr=True))
    return {'session': session, 'evidence': data_families.EVIDENCE, 'fetch_runs_ready': ready,
            'families': cards}


def _last_failures(runs: list[dict[str, Any]], targets: list[str]) -> dict[str, str]:
    """Per target the newest failure reason among ``runs`` (newest first).

    A failure only ever belongs to the targets its own run covered (I05): a
    ticker-scoped row — including its '*' whole-call sentinel — counts for that
    ticker alone; a family-scoped '*' counts for that run's own target set
    (``detail.failed_targets``), falling back to the current targets only for
    workbench rows, never spreading another job's list (e.g. the filings monitor)
    onto uncollected pool targets."""
    failure: dict[str, str] = {}
    target_set = set(targets)
    for row in runs:
        for entry in row['failures'] or []:
            reason = entry.get('reason')
            target = entry.get('target')
            if row.get('scope') == 'ticker':
                if target == '*' or target == row.get('ticker'):
                    if row.get('ticker') in target_set:
                        failure.setdefault(row['ticker'], reason)
            elif target == '*':
                scoped = (row.get('detail') or {}).get('failed_targets')
                if scoped is None:
                    scoped = targets if row.get('job') in ('workbench', 'daily') else []
                for name in scoped:
                    if name in target_set:
                        failure.setdefault(name, reason)
            elif target in target_set:
                failure.setdefault(target, reason)
    return failure


def _detail_rows(conn, family: Adapter, *, runs: list[dict[str, Any]],
                 session: date, now: datetime, atr: bool = False) -> list[dict[str, Any]]:
    """One row per target: its latest point, whether it lags, its newest failure."""
    if family.id == 'universe':
        return []
    targets, _ = families.targets(conn, family.id, session=session, now=now)
    if not targets:
        return []
    latest = _latest_map(conn, family.id, targets, now=now)
    counts = (data_repo.bar_counts(conn, targets, end_date=session, cutoff_at=now)
              if atr else None)
    failure = _last_failures(runs, targets)
    return [{'target': target,
             'latest_at': latest.get(target),
             'lagging': (_atr_lagging(latest.get(target), (counts or {}).get(target), session) if atr
                         else family.lagging(latest.get(target), session)),
             'last_failure': failure.get(target)} for target in targets]


def family_detail(family_id: str, *, now: datetime) -> dict[str, Any]:
    """One family's card plus its last 20 fetch runs and per-target coverage table."""
    family = data_families.get('bars' if family_id == 'atr' else family_id)   # 未知族 LookupError
    session = bars_module.latest_closed_session(now)
    recorded = family.id
    with core_tx.read_only_tx() as conn:
        _require_family_ready(conn, family_id)
        jobs = {job['name']: job for job in db.list_scheduled_jobs(conn)}
        ready = core_schema.fetch_runs_ready(conn)
        run_facts = _run_facts(conn, ready)
        card = _card(conn, family, session=session, now=now, jobs=jobs,
                     run_facts=run_facts, ready=ready, atr=family_id == 'atr')
        runs = data_repo.fetch_runs(conn, recorded, 20) if ready else []
        rows = _detail_rows(conn, family, runs=runs, session=session, now=now, atr=family_id == 'atr')
    return {'card': card, 'runs': runs, 'rows': rows}


def browse(family_id: str, *, now: datetime, ticker: str | None = None,
           series: str | None = None) -> dict[str, Any]:
    """Raw values of one family over its fixed window (spec §8); never collects.

    A never-collected ticker is an empty result, not an error; a per-ticker family
    without its ``ticker`` argument and an unknown FRED series are ValueErrors."""
    family = data_families.get('bars' if family_id == 'atr' else family_id)
    if family.id not in RAW_FAMILIES:
        raise ValueError(f'数据族暂不支持原始浏览：{family_id}')
    session = bars_module.latest_closed_session(now)
    if family.id == 'fred':
        if series is None:
            raise ValueError('FRED 浏览需要 series 参数')
        if series not in macro_module.MACRO_SERIES:
            raise ValueError(f'未知序列：{series}')
        key = series
    elif family.id != 'universe':
        if ticker is None:
            raise ValueError('该族浏览需要 ticker 参数')
        key = ticker.strip().upper()
    else:
        key = None
    with core_tx.read_only_tx() as conn:
        if family.id == 'universe':
            return _browse_universe(conn, now=now)
        assert key is not None  # every non-universe branch validated ticker or series above
        if family_id == 'atr':
            return _browse_atr(conn, key, session=session, now=now)
        if family.id == 'bars':
            return _browse_bars(conn, key, session=session, now=now)
        if family.id == 'news':
            return _browse_news(conn, key, session=session, now=now)
        if family.id == 'sec_filings':
            return _browse_filings(conn, key, session=session, now=now)
        if family.id == 'fred':
            return _browse_fred(conn, key, session=session, now=now)
        raise ValueError(f'数据族暂不支持原始浏览：{family_id}')


def _browse_bars(conn, ticker: str, *, session: date, now: datetime) -> dict[str, Any]:
    bars_ = data_repo.read_bars(conn, ticker, session, cutoff_at=now, limit=BROWSE_BARS_LIMIT)
    revisions = (data_repo.bar_revision_counts(conn, ticker, since=bars_[0]['price_date'],
                                                cutoff_at=now) if bars_ else {})
    rows = [{'price_date': bar['price_date'], 'open': bar['open'], 'high': bar['high'],
             'low': bar['low'], 'close': bar['close'], 'adj_close': bar['adj_close'],
             'volume': bar['volume'], 'split_factor': bar['split_factor'],
             'revisions': revisions.get(bar['price_date'], 0),
             'fetched_at': bar['fetched_at'], 'note': BAR_NOTE}
            for bar in reversed(bars_)]   # 新到旧；每日取截至现在的最新修订
    return {'family': 'bars', 'key': ticker, 'has_data': bool(rows), 'rows': rows}


def _browse_atr(conn, ticker: str, *, session: date, now: datetime) -> dict[str, Any]:
    bars_ = data_repo.read_bars(conn, ticker, session, cutoff_at=now,
                                 limit=BROWSE_ATR_LIMIT + price.MIN_BARS)
    values = []
    for i in range(len(bars_)):
        atr = price.atr14(bars_[:i + 1])   # 截至该日的日线，与 plan-1 同一函数
        if atr is not None:
            close = bars_[i]['close']
            values.append({'price_date': bars_[i]['price_date'], 'close': close,
                           'atr': float(atr), 'atr_pct': float(atr) / close})
    return {'family': 'atr', 'key': ticker, 'has_data': bool(values),
            'rows': values[-BROWSE_ATR_LIMIT:], 'note': ATR_NOTE}


def _browse_news(conn, ticker: str, *, session: date, now: datetime) -> dict[str, Any]:
    rows = data_repo.read_news(conn, ticker, cutoff_at=now,
                                since=_ny_midnight(session - timedelta(days=BROWSE_NEWS_DAYS)),
                                limit=None)
    return {'family': 'news', 'key': ticker, 'has_data': bool(rows), 'rows': rows}


def _browse_filings(conn, ticker: str, *, session: date, now: datetime) -> dict[str, Any]:
    since = session - timedelta(days=BROWSE_FILINGS_DAYS)
    filings = data_repo.read_filings(conn, ticker, since=since, cutoff_at=now)
    known = {row['accession_number'].replace('-', '') for row in filings}
    # Source-filtered first, then the whole window (I10): a busy news ticker can
    # no longer push its 8-K listings out of the result.
    news = data_repo.read_news(conn, ticker, cutoff_at=now, since=_ny_midnight(since),
                                limit=None, source='sec_8k')
    sec_8k = []
    for item in news:
        if any(acc in item['url'] for acc in known):
            continue
        # Without an acceptance time there is no filing date basis; the fetched-at
        # fallback must not masquerade as one (I14).
        item['filing_date_note'] = None if item['published_at'] is not None else '申报日未知'
        sec_8k.append(item)
    return {'family': 'sec_filings', 'key': ticker, 'has_data': bool(filings or sec_8k),
            'rows': len(filings) + len(sec_8k), 'filings': filings, 'sec_8k': sec_8k}


def _browse_fred(conn, series_id: str, *, session: date, now: datetime) -> dict[str, Any]:
    obs = data_repo.read_macro(conn, series_id, end_date=session, cutoff_at=now,
                                limit=BROWSE_FRED_LIMIT)
    revisions = data_repo.macro_revision_counts(conn, series_id, cutoff_at=now)
    rows = [{'obs_date': row['obs_date'], 'value': float(row['value']),
             'revisions': revisions.get(row['obs_date'], 0)} for row in obs]
    return {'family': 'fred', 'key': series_id, 'has_data': bool(rows), 'rows': rows}


def _universe_note() -> str | None:
    """The range file's free-form note; load() has already proved the file readable."""
    try:
        raw = json.loads(Path(universe_module.DEFAULT_PATH).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    return raw.get('note')


def _browse_universe(conn, *, now: datetime) -> dict[str, Any]:
    try:
        loaded = universe_module.load()
    except universe_module.UniverseError as exc:
        return {'family': 'universe', 'key': None, 'has_data': False, 'rows': [], 'meta': None,
                'notes': ['universe_unavailable'],
                'current_error': sanitize_reason(exc)}
    latest = data_repo.latest_bar_dates(conn, list(loaded.tickers), cutoff_at=now)
    rows = [{'ticker': ticker, 'latest_bar': latest.get(ticker)} for ticker in loaded.tickers]
    return {'family': 'universe', 'key': None, 'has_data': bool(rows), 'rows': rows,
            'meta': {'name': loaded.name, 'as_of': loaded.as_of, 'source': loaded.source,
                     'note': _universe_note()}}
