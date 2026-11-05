"""Ordered data-family registry and per-target primary/backup dispatch."""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import timedelta
from functools import partial

from investment_assistant.core import schema as core_schema
from investment_assistant.core.constants import HISTORY_DAYS
from investment_assistant.core.tx import Unavailable
from investment_assistant.data import fetch_policy
from investment_assistant.data.adapter_types import Adapter, Source, DEFAULT_FALLBACK_KINDS
from investment_assistant.data.adapters import bars, news, sec_listing, earnings, fred, universe, fundamentals, sec_text, sector
from investment_assistant.data.fetch_policy import FetchError, Fetched
from investment_assistant.data.fetch_status import sanitize_reason
from investment_assistant.data.tally import Tally

ADAPTERS: tuple[Adapter, ...] = tuple(m.ADAPTER for m in (
    bars, news, sec_listing, earnings, fred, universe, fundamentals, sec_text, sector))
_BY_ID = {a.id: a for a in ADAPTERS}


def get(family_id: str) -> Adapter:
    try:
        return _BY_ID[family_id]
    except KeyError:
        raise LookupError(f'未知数据族：{family_id}') from None


def ids() -> tuple[str, ...]:
    return tuple(a.id for a in ADAPTERS)


def tool_families() -> list[Adapter]:
    return [a for a in ADAPTERS if a.tool is not None]


def run_adapter(adapter, targets, *, scope, trigger, run_id, session, now, deps, features, tally=None,
                observe=None, whole_call_target=None, workers: int = 1) -> Tally:
    """Fetch and store ``targets`` through the adapter's sources, one target at a time.

    ``workers > 1`` fetches and stores several targets concurrently (a bounded pool; the
    store itself runs inside each worker's own transaction) while every ``tally`` update,
    ``observe`` call and the whole-call stop decision stay in the caller thread, in input
    order — the row a pooled run books is the row the sequential default would book; when
    the whole-call ``'*'`` stops the booking, already-submitted fetches still run and their
    results are never booked.

    ``observe(target, source_id, rows, result)`` (optional) sees each stored target before it is booked, so a host can
    build its own summary from the fetched rows and the store receipt; it must not raise. A target's final failure
    whose ``FetchError.target`` equals ``whole_call_target`` (the daily SEC listing passes the ticker directory's) means
    nothing of the family can be resolved: it is booked once as the whole-call ``'*'`` and the rest is not attempted
    (I04); without it every target records its own failure."""
    targets = list(dict.fromkeys(targets))
    tally = tally if tally is not None else Tally(adapter.id, 'daily', targets)
    tally.set_targets(targets)
    tally.detail.update(scope=scope, trigger=trigger, run_id=run_id)
    if adapter.id == 'fred' and not deps.fred_api_key:
        for target in targets:
            tally.record(target, FetchError('FRED_API_KEY 未配置', kind='other'), rows=0)
        tally.skip('FRED_API_KEY 未配置')
        return tally

    def fetch_one(target):
        """One target's fetch and store; returns the outcome for booking and never touches the tally."""
        attempts, elapsed = [], 0
        primary_error = None
        fallback = {'tried': False, 'reason': None}
        source_used = None
        try:
            for index, source in enumerate(adapter.sources):
                if index:
                    if not (features.get('backup_sources', False) and primary_error is not None
                            and primary_error.kind in adapter.fallback_kinds):
                        break
                    fallback = {'tried': True, 'reason': sanitize_reason(primary_error)}
                try:
                    outcome = fetch_policy.fetch(target, partial(source.fetch, target, session=session,
                        start=session - timedelta(days=HISTORY_DAYS), end=session, deps=deps, now=now),
                        policy=source.policy, sleep=deps.sleep)
                    if isinstance(outcome.value, Fetched):
                        outcome = outcome.value
                    attempts.extend({**a, 'source': source.id} for a in outcome.attempts)
                    elapsed += outcome.elapsed_ms
                except FetchError as exc:
                    attempts.extend({**a, 'source': source.id} for a in exc.attempts)
                    elapsed += exc.elapsed_ms
                    primary_error = primary_error or exc
                    continue
                source_used = source.id
                # Fetch succeeded; a storage/transaction failure is a final failure, never another source attempt.
                with deps.transaction() as conn:
                    store = partial(adapter.store, target=target, source_id=source.id, now=now)
                    if adapter.id == 'bars' and source.id == 'stooq':
                        result = bars.fill_from_backup(conn, target, session=session,
                                                       rows=outcome.value, store=store)
                    else:
                        result = store(conn, outcome.value)
                return {'target': target, 'ok': True, 'rows': outcome.value,
                        'fetched': Fetched(outcome.value, attempts, elapsed), 'result': result,
                        'source_used': source_used, 'fallback': fallback}
            raise primary_error or FetchError('adapter has no sources', kind='other')
        except Unavailable:
            raise                       # database/schema not ready: the host's 503, never a per-target failure
        except Exception as exc:
            kind, status = fetch_policy.classify_exception(exc)
            return {'target': target, 'ok': False, 'source_used': source_used, 'fallback': fallback,
                    'error': FetchError(str(exc), kind=kind, status=status, attempts=attempts,
                                        target=target, total_ms=elapsed),
                    'whole_call': whole_call_target is not None and getattr(exc, 'target', None) == whole_call_target}

    def book(outcome) -> bool:
        """Book one outcome in the caller thread; False stops the run (the whole-call failure)."""
        if outcome['ok']:
            if adapter.id == 'universe':
                loaded = outcome['rows'][0]
                tally.detail.update(name=loaded['name'], as_of=loaded['as_of'], tickers=len(loaded['tickers']),
                                    stale=adapter.lagging(loaded['as_of'], session))
            if observe is not None:
                observe(outcome['target'], outcome['source_used'], outcome['rows'], outcome['result'])
            tally.record(outcome['target'], outcome['fetched'], rows=outcome['result']['inserted'],
                         latest=outcome['result']['latest'], source_used=outcome['source_used'],
                         fallback=outcome['fallback'])
            return True
        error = outcome['error']
        if outcome['whole_call']:
            tally.record('*', error, rows=0)
            return False
        tally.record(outcome['target'], error, rows=0, source_used=outcome['source_used'],
                     fallback=outcome['fallback'])
        return True

    if workers <= 1:
        for target in targets:
            if not book(fetch_one(target)):
                break
        return tally
    with ThreadPoolExecutor(max_workers=workers) as executor:
        try:
            for outcome in executor.map(fetch_one, targets):   # results arrive in input order
                if not book(outcome):
                    break
        except Unavailable:
            executor.shutdown(wait=False, cancel_futures=True)  # not-yet-started targets are dropped, never booked
            raise
    return tally


# ---------------------------------------------------------------- command line (spec §12.3, R12a)

def _list_rows() -> list[list[str]]:
    return [[a.id, a.title, '逐只' if a.per_ticker else '整族', a.kind, '/'.join(s.id for s in a.sources)]
            for a in ADAPTERS]


def _fetch(family_id: str, ticker: str | None) -> int:
    """One manual fetch through ``run_adapter`` (the Data menu's path, ``manual_family`` row). The data layer cannot
    resolve a per-ticker family's whole target set (watchlist, positions, chains live in the workflow layer), so a
    per-ticker family needs ``--ticker``; the menu or the daily batch fetches the whole family."""
    from investment_assistant.config import load_config
    from investment_assistant.core import features as core_features
    from investment_assistant.core import tx as core_tx
    from investment_assistant.data import collect, identity
    from investment_assistant.data.adapter_support import adapter_deps_from_service
    from investment_assistant.data.adapters.bars import latest_closed_session

    adapter = get(family_id)
    if ticker is None and adapter.per_ticker:
        print(f'{family_id} 是逐只拉取的族：请加 --ticker（整族请用数据菜单或日批）', file=sys.stderr)
        return 2
    if ticker is not None:
        if not adapter.per_ticker:
            print('该族不支持单只拉取', file=sys.stderr)
            return 2
        ticker = ticker.strip().upper()
        try:
            identity.stock_id(ticker)
        except ValueError:
            print(f'非法 ticker：{ticker}', file=sys.stderr)
            return 2
    now = collect.wall_clock()
    session = latest_closed_session(now)
    deps = adapter_deps_from_service(core_tx.Deps(), transaction=core_tx._tx, config=load_config(),
                                     sleep=collect.FETCH_SLEEP)
    with core_tx._tx() as conn:
        ready = core_schema.five_layers_ready(conn)
        if not ready and family_id not in ('bars', 'sec_filings', 'news', 'fred', 'universe'):
            raise Unavailable('需执行迁移 031')
        flags = core_features.frozen(conn) if ready else {'backup_sources': False}
        selected = adapter.targets(conn, 'manual', explicit=[ticker] if ticker is not None else None)
    tally = Tally(family_id, 'workbench', selected)
    tally.detail['feature_context'] = 'frozen' if ready else 'pre_031_primary_only'
    with (collect.family_locks([family_id]) if adapter.lock else nullcontext()):
        tally = run_adapter(adapter, selected, scope='manual', trigger='manual_family', run_id=None,
                            session=session, now=now, deps=deps, features=flags, tally=tally)
    row = tally.row(trigger='manual_family', run_id=None, session=session, started_at=now,
                    finished_at=collect.wall_clock(), scope='ticker' if ticker is not None else 'family',
                    ticker=ticker)
    print(json.dumps({'status': row['status'], 'family': family_id, 'ticker': ticker,
                      'targets': row['target_count'], 'ok': row['ok_count'], 'data_status': collect.write_runs([row])},
                     ensure_ascii=False, default=str))
    return 0 if row['status'] != 'failed' else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.data.registry',
                                     description='数据族注册表：列出适配器、手动拉取一个族。')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list', help='列出全部数据族（id、标题、拉取方式、数据源）')
    fetch = commands.add_parser('fetch', help='手动拉取一个族（写一行 manual_family 拉取记录）')
    fetch.add_argument('family', help='族 id，见 list')
    fetch.add_argument('--ticker', help='只拉这一只（逐只族必填）')
    args = parser.parse_args(argv)
    if args.command == 'list':
        for line in _table(['id', '标题', '拉取', '类型', '数据源'], _list_rows()):
            print(line)
        return 0
    from dotenv import load_dotenv

    load_dotenv()
    try:
        return _fetch(args.family, args.ticker)
    except (LookupError, ValueError, Unavailable) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    import unicodedata

    def width(text):
        return sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1 for ch in text)

    widths = [max(width(cell) for cell in column) for column in zip(headers, *rows)]
    return [('  '.join(cell + ' ' * (w - width(cell)) for cell, w in zip(row, widths))).rstrip()
            for row in [headers, *rows]]


if __name__ == '__main__':
    raise SystemExit(main())
