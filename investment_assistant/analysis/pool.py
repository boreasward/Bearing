"""Tiered watch pool parsed from the watchlist (pure functions).

The pool is the active rows of watchlist_items with a valid US identity; the `core` tag marks the
core pool, `sector:<ETF>` maps a ticker to its industry ETF.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from investment_assistant.data.identity import stock_id
from investment_assistant.data.market_constants import SECTOR_ETFS as SECTOR_ETFS

MAX_POOL = 100
MAX_CORE = 15
CORE_TAG = 'core'
SECTOR_PREFIX = 'sector:'


@dataclass(frozen=True)
class Pool:
    core: tuple[str, ...]
    extended: tuple[str, ...]
    sectors: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)  # ticker -> 'watchlist' | 'screen'，缺失视为 watchlist

    @property
    def tickers(self) -> tuple[str, ...]:
        return self.core + self.extended

    def tier(self, ticker: str) -> str | None:
        if ticker in self.core:
            return 'core'
        if ticker in self.extended:
            return 'extended'
        return None

    def source(self, ticker: str) -> str | None:
        return self.sources.get(ticker, 'watchlist') if self.tier(ticker) else None

    @classmethod
    def all_core(cls, tickers: list[str]) -> 'Pool':
        """Single-tier pool for tests and manual overrides; no core cap check."""
        unique: list[str] = []
        for ticker in tickers:
            upper = ticker.upper()
            if upper not in unique:
                unique.append(upper)
        if not unique:
            raise ValueError('股票池为空')
        if len(unique) > MAX_POOL:
            raise ValueError(f'股票池超出上限：{len(unique)} > {MAX_POOL}')
        return cls(core=tuple(sorted(unique)), extended=(), sectors={})


def parse_tags(tags: list[str] | None) -> tuple[bool, str | None]:
    """Return (is_core, sector_etf) from watchlist tags; comparisons case-insensitive."""
    is_core = False
    sector = None
    for tag in tags or ():
        text = tag.strip()
        if not text:
            continue
        if text.lower() == CORE_TAG:
            is_core = True
        elif text.lower().startswith(SECTOR_PREFIX):
            if sector is not None:
                raise ValueError(f'最多一个 sector: 标签：{tag}')
            etf = text[len(SECTOR_PREFIX):].strip().upper()
            if etf not in SECTOR_ETFS:
                raise ValueError(f'未知行业 ETF：{tag}（可选 {"、".join(SECTOR_ETFS)}）')
            sector = etf
    return is_core, sector


def _valid_us(ticker: str) -> bool:
    try:
        stock_id(ticker)
    except ValueError:
        return False
    return True


def from_watchlist(rows: list[dict]) -> Pool:
    """Build the pool from watchlist rows; paused, archived and invalid-identity rows are ignored."""
    core: list[str] = []
    extended: list[str] = []
    sectors: dict[str, str] = {}
    seen: set[str] = set()
    for row in rows:
        if row.get('status') != 'active':
            continue
        ticker = row['ticker'].upper()
        if ticker in seen or not _valid_us(ticker):
            continue
        seen.add(ticker)
        is_core, sector = parse_tags(row.get('tags'))
        (core if is_core else extended).append(ticker)
        if sector:
            sectors[ticker] = sector
    if not seen:
        raise ValueError('股票池为空：观察名单中没有 active 的美股')
    if len(seen) > MAX_POOL:
        raise ValueError(f'观察池超出上限：{len(seen)} > {MAX_POOL}')
    if len(core) > MAX_CORE:
        raise ValueError(f'核心池超出上限：{len(core)} > {MAX_CORE}')
    return Pool(core=tuple(sorted(core)), extended=tuple(sorted(extended)), sectors=sectors)
