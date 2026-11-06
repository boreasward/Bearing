"""Lower-level target selection, freshness and outcome preservation."""
import os
import time
from datetime import date, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

from investment_assistant.core.constants import BENCHMARK, SEC_EVIDENCE_DIR
from investment_assistant.data.adapter_types import DEFAULT_FALLBACK_KINDS, Adapter, AdapterDeps, ToolSpec
from investment_assistant.data.fetch_policy import Fetched, FetchError, FetchPolicy, classify_exception
from investment_assistant.data.market_constants import MACRO_SERIES
from investment_assistant.data.sources.edgar import EdgarSource

ONCE = FetchPolicy(backoff_s=())
SCOPES = frozenset({'daily', 'manual', 'research', 'screen_new_members', 'data_request', 'sec_monitor'})


def targets(conn, scope, *, family, explicit=None, facts=None, universe_loader=None) -> list[str]:
    if scope not in SCOPES:
        raise ValueError(f'unknown scope: {scope}')
    if explicit is not None:
        return list(dict.fromkeys(str(t).upper() if family not in {'fred', 'universe'} else str(t)
                                  for t in explicit))
    if family == 'fred':
        return list(MACRO_SERIES)
    if family == 'universe':
        return ['nasdaq100']
    if scope != 'daily':
        raise ValueError('explicit targets required outside daily scope')
    if facts is None:
        raise ValueError('daily requires frozen TargetFacts from host pool/position/chain algorithms')
    selected = set(facts.pool) | set(facts.holdings) | set(facts.active_chains)
    if family not in {'bars', 'earnings', 'sector'}:
        return sorted(selected)
    selected.update(facts.paper_holdings)
    if universe_loader is None:
        raise ValueError('daily wide-family targets require universe_loader')
    selected.update(universe_loader().tickers)
    head = [BENCHMARK, *facts.sector_etfs]
    return head + sorted(selected - set(head)) if family == 'bars' else sorted(selected - set(head))


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def stale(latest, session, *, days):
    return latest is None or as_date(latest) < session - timedelta(days=days)


def make_adapter(id, title, purpose, table, sources, store, latest, *, days=0,
                 coverage_note, per_ticker=True, kind='fetched', lock=True, lagging=None):
    return Adapter(id, title, purpose, kind, per_ticker, table, sources, DEFAULT_FALLBACK_KINDS,
                   partial(targets, family=id), store, latest, lagging or partial(stale, days=days),
                   coverage_note, ToolSpec(id, purpose) if per_ticker else None, 'family.' + id, lock)


def receipt(counts, rows, field):
    return {'inserted': counts['inserted'], 'existing': counts.get('existing', counts.get('unchanged', 0)),
            'latest': max((r[field] for r in rows if r.get(field) is not None), default=None)}


def sec_source(deps):
    if deps.filing_source is None:
        deps.filing_source = EdgarSource(SEC_EVIDENCE_DIR)
    return deps.filing_source


def adapter_deps_from_service(deps, *, transaction, config=None, sleep=None) -> AdapterDeps:
    """The one host→data projection of a ``core.tx.Deps``: fetchers, the config-derived
    SEC text root and effective config keys (the manual fetch and the v3 collection
    share it; the host keeps owning the transaction callable)."""
    from investment_assistant.config import load_config

    config = config if config is not None else load_config()
    return AdapterDeps(transaction=transaction, bar_fetcher=deps.bar_fetcher,
                       news_fetcher=deps.news_fetcher, earnings_fetcher=deps.earnings_fetcher,
                       macro_fetcher=deps.macro_fetcher, filing_source=deps.filing_source,
                       universe=deps.universe, universe_loader=deps.universe_loader,
                       fred_api_key=os.environ.get('FRED_API_KEY'),
                       sec_text_dir=config.filings.output_dir,
                       sec_monitor_state_dir=Path(config.sec_monitor.state_dir) if config.sec_monitor.state_dir else None,
                       sleep=sleep if sleep is not None else time.sleep)


def _http_outcomes(source) -> list:
    """The calling thread's HTTP outcomes when the source keeps them per thread, else the shared list."""
    per_thread = getattr(source, 'thread_outcomes', None)
    return per_thread() if callable(per_thread) else getattr(source, 'fetch_outcomes', ())


def sec_outcome(source, fn):
    """Preserve actual per-request attempts, including successful retries before parsing/storage."""
    start = len(_http_outcomes(source))
    try:
        value = fn()
    except Exception as exc:
        outcomes = _http_outcomes(source)[start:]
        attempts = [a for result in outcomes for a in result.attempts]
        if isinstance(exc, FetchError):
            if outcomes and outcomes[-1] is not exc:
                attempts += exc.attempts
            if not attempts:
                attempts = exc.attempts
            raise FetchError(str(exc), kind=exc.kind, status=exc.status, target=exc.target,
                             attempts=attempts, total_ms=sum(o.elapsed_ms for o in outcomes)) from None
        kind, status = classify_exception(exc)
        if kind == 'other' and isinstance(exc, (ValueError, TypeError, KeyError)):
            kind = 'parse'
        raise FetchError(str(exc), kind=kind, status=status, attempts=attempts,
                         total_ms=sum(o.elapsed_ms for o in outcomes)) from None
    outcomes = _http_outcomes(source)[start:]
    # Offline/injected sources without HTTP bookkeeping are accounted by the outer single attempt.
    if not outcomes:
        return value
    return Fetched(value, [a for result in outcomes for a in result.attempts],
                   sum(result.elapsed_ms for result in outcomes))


def discover_one(source: Any, ticker: str, start: str, end: str) -> list[dict]:
    """One ticker's filings from ``EdgarSource.discover_one`` or, for sources that only have ``discover``
    (test doubles), from ``discover([ticker])``."""
    if hasattr(source, 'discover_one'):
        return source.discover_one(ticker, start, end)
    found = source.discover([ticker], start, end)
    if isinstance(found, tuple):
        found, errors = found
        if errors:
            raise RuntimeError(next(iter(errors.values())))
    return found
