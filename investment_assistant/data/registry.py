"""Ordered data-family registry and per-target primary/backup dispatch."""
from datetime import timedelta
from functools import partial

from investment_assistant.core.constants import HISTORY_DAYS
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


def run_adapter(adapter, targets, *, scope, trigger, run_id, session, now, deps, features, tally=None) -> Tally:
    targets = list(dict.fromkeys(targets))
    tally = tally if tally is not None else Tally(adapter.id, 'daily', targets)
    tally.set_targets(targets)
    tally.detail.update(scope=scope, trigger=trigger, run_id=run_id)
    if adapter.id == 'fred' and not deps.fred_api_key:
        for target in targets:
            tally.record(target, FetchError('FRED_API_KEY 未配置', kind='other'), rows=0)
        tally.skip('FRED_API_KEY 未配置')
        return tally
    for target in targets:
        attempts, elapsed = [], 0
        primary_error = None
        fallback = {'tried': False, 'reason': None}
        source_used = None
        result = None
        try:
            for index, source in enumerate(adapter.sources):
                if index:
                    if not (features.get('backup_sources', False) and primary_error is not None
                            and primary_error.kind in adapter.fallback_kinds):
                        break
                    fallback = {'tried': True, 'reason': sanitize_reason(primary_error)}
                try:
                    outcome = fetch_policy.fetch(target, partial(source.fetch, target, session=session,
                        start=session - timedelta(days=HISTORY_DAYS), end=session, deps=deps),
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
                if adapter.id == 'universe':
                    loaded = outcome.value[0]
                    tally.detail.update(name=loaded['name'], as_of=loaded['as_of'], tickers=len(loaded['tickers']),
                                        stale=adapter.lagging(loaded['as_of'], session))
                tally.record(target, Fetched(outcome.value, attempts, elapsed),
                             rows=result['inserted'], latest=result['latest'],
                             source_used=source_used, fallback=fallback)
                break
            if result is None:
                raise primary_error or FetchError('adapter has no sources', kind='other')
        except Exception as exc:
            kind, status = fetch_policy.classify_exception(exc)
            error = FetchError(str(exc), kind=kind, status=status, attempts=attempts,
                               target=target, total_ms=elapsed)
            tally.record(target, error, rows=0, source_used=source_used, fallback=fallback)
    return tally
