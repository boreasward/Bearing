"""Host facts for adapter targets; pool/position/chain policy stays with its owners."""
from __future__ import annotations

from datetime import date, datetime

from investment_assistant.analysis import pool, repo as analysis_repo
from investment_assistant.core import schema
from investment_assistant.core.redact import sanitize_reason as sanitize_reason
from investment_assistant.data import registry
from investment_assistant.data.adapter_types import TargetFacts
from investment_assistant.data.adapters import universe
from investment_assistant.db import list_watchlist_items
from investment_assistant.execution import chains, repo as execution_repo


def targets(conn, family_id: str, *, session: date, now: datetime, scope: str = 'daily',
            explicit: list[str] | None = None, universe_loader=None) -> tuple[list[str], list[str]]:
    """Project existing host algorithms once, delegate set selection to Adapter.targets.

    Pre-031 skips only the unavailable research-request input explicitly. Pool and
    universe degradation retain their notes; database/schema errors still propagate.
    Explicit scopes never evaluate the whole pool or broaden the requested targets.
    """
    adapter = registry.get(family_id)
    if explicit is not None or not adapter.per_ticker:
        return adapter.targets(conn, scope, explicit=explicit), []
    if scope != 'daily':
        raise ValueError('explicit targets required outside daily scope')
    notes: list[str] = []
    try:
        watch_pool = pool.from_watchlist(list_watchlist_items(conn))
    except ValueError as exc:
        watch_pool = None
        notes.append('pool_empty' if str(exc).startswith('股票池为空') else 'pool_invalid')
    pool_tickers = set(watch_pool.tickers if watch_pool is not None else ())
    pool_tickers.update(ticker for ticker, row in analysis_repo.pool_latest(conn).items()
                        if row['tier'] != 'removed' and row['source'] == 'screen')
    holdings = execution_repo.open_position_tickers(conn)
    paper_holdings: set[str] = set()
    accounts_ready = schema.accounts_ready(conn)
    if accounts_ready:
        for account in execution_repo.list_accounts(conn):
            if account['kind'] == 'paper':
                paper_holdings.update(execution_repo.open_position_tickers(conn, account_id=account['id']))
    failures: list[tuple[str, str]] = []
    active = (chains.chain_tickers(conn, session, now=now, exclude=(), failures=failures)
              if schema.timeline_ready(conn) else [])
    notes.extend(f'chains:{ticker}: {reason}' for ticker, reason in failures)
    facts = TargetFacts(pool=tuple(sorted(pool_tickers)), holdings=tuple(sorted(holdings)),
                        paper_holdings=tuple(sorted(paper_holdings)), active_chains=tuple(active),
                        sector_etfs=pool.SECTOR_ETFS, paper_accounts_ready=accounts_ready, research_requests_ready=schema.five_layers_ready(conn))

    def load_universe():
        try:
            return (universe_loader or universe.load)()
        except universe.UniverseError:
            notes.append('universe_unavailable')
            return universe.Universe('', session, '', ())

    selected = adapter.targets(conn, scope, facts=facts, universe_loader=load_universe)
    return selected, notes
