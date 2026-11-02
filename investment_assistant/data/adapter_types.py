"""Single definitions shared by adapters and the public registry (no aggregator imports)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
import time

from investment_assistant.data.fetch_policy import FetchPolicy, Fetched
from investment_assistant.data.market_constants import SECTOR_ETFS

DEFAULT_FALLBACK_KINDS = frozenset({'timeout', 'network', 'http_5xx', 'http_429', 'empty'})


class Rows(list):
    """Fetched rows plus fetch-side facts a store cannot see (raw counts, skipped items); behaves as a plain list."""

    def __init__(self, items=(), **facts: Any):
        super().__init__(items)
        self.facts = facts


@dataclass(frozen=True)
class ToolSpec:
    family: str
    description: str


@dataclass(frozen=True)
class Source:
    id: str
    fetch: Callable[..., list[dict] | Fetched[list[dict]]]
    policy: FetchPolicy
    note: str


class AdapterTargets(Protocol):
    def __call__(self, conn: Any, scope: str, *, explicit: list[str] | tuple[str, ...] | None = None,
                 facts: TargetFacts | None = None, universe_loader: Callable | None = None) -> list[str]: ...


@dataclass(frozen=True)
class Adapter:
    id: str
    title: str
    purpose: str
    kind: str
    per_ticker: bool
    table: str
    sources: tuple[Source, ...]
    fallback_kinds: frozenset[str]
    targets: AdapterTargets
    store: Callable[..., dict]
    latest: Callable[..., dict]
    lagging: Callable[[Any, Any], bool]
    coverage_note: str
    tool: ToolSpec | None
    concept: str
    lock: bool = True


@dataclass(frozen=True)
class TargetFacts:
    """Frozen results of the host's existing pool/position/chain algorithms, never re-derived here."""
    pool: tuple[str, ...] = ()
    holdings: tuple[str, ...] = ()
    paper_holdings: tuple[str, ...] = ()
    active_chains: tuple[str, ...] = ()
    sector_etfs: tuple[str, ...] = SECTOR_ETFS
    paper_accounts_ready: bool = True
    research_requests_ready: bool = True  # host capability: false only for pre-031 compatibility


@dataclass
class AdapterDeps:
    """Data-only injection boundary. transaction() owns commit/rollback, including store failure.

    Hosts may project their core.tx.Deps fields here; no data import of host orchestration.
    """
    transaction: Callable
    bar_fetcher: Callable | None = None
    news_fetcher: Callable | None = None
    earnings_fetcher: Callable | None = None
    macro_fetcher: Callable | None = None
    filing_source: Any = None
    universe: Any = None
    universe_loader: Callable | None = None
    fred_api_key: str | None = None
    sec_text_dir: Path | None = None
    sec_monitor_state_dir: Path | None = None
    sleep: Callable = time.sleep
