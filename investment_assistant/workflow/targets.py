"""Data family catalog for the Data menu (design 2026-10-26 §4).

Static registry of the six families plus the display rules built on it: fetch
status classification, failure-reason sanitisation, per-family staleness and
coverage counts, and the per-family target lists. ``targets`` is the only part
that touches the database; row reads live in repository.py.
"""
from __future__ import annotations
from investment_assistant.data import families as data_families
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.execution.repo as execution_repo

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from investment_assistant.core.redact import REASON_MAX_CHARS as REASON_MAX_CHARS, sanitize_reason as sanitize_reason
from investment_assistant.analysis import pool
from investment_assistant.core.constants import BENCHMARK
from investment_assistant.core.constants import MIN_BARS_ATR
from investment_assistant.data.adapters import fred as macro, universe

def targets(conn, family_id: str, *, universe_loader=None) -> tuple[list[str], list[str]]:
    """The family's fetch targets as (targets, notes) (spec §4 「目标清单」 column).

    One list for the batch loop and the data menu's manual fetch to align on.
    bars/atr lead with the benchmark and sector ETFs, the rest sorted;
    news/sec_filings follow the current pool plus holdings; fred lists the two
    series; universe is fetched from its file, not per member. Notes record
    degraded inputs, never an error: `pool_empty` (no active watchlist tickers),
    `pool_invalid` (a non-empty but invalid pool: over the caps or bad tags) and
    `universe_unavailable` (the range file failed to load).
    """
    family = data_families.get(family_id)
    if family.id == 'universe':
        return [], []
    if family.id == 'fred':
        return list(macro.MACRO_SERIES), []
    from investment_assistant.db import list_watchlist_items

    notes: list[str] = []
    try:
        watch_pool = pool.from_watchlist(list_watchlist_items(conn))
    except ValueError as exc:
        watch_pool = None
        # 非空但非法的池配置（超限/标签无效）与真空池分开说，不互相冒充（M08）。
        notes.append('pool_empty' if str(exc).startswith('股票池为空') else 'pool_invalid')
    pool_tickers: set[str] = set(watch_pool.tickers if watch_pool is not None else ())
    pool_tickers |= {ticker for ticker, row in analysis_repo.pool_latest(conn).items()
                     if row['tier'] != 'removed' and row['source'] == 'screen'}
    pool_tickers |= execution_repo.open_position_tickers(conn)   # 持仓：未作废成交得出的未平仓标的
    if family.id not in ('bars', 'atr'):
        return sorted(pool_tickers), notes
    head = [BENCHMARK, *pool.SECTOR_ETFS]
    if universe_loader is None:
        universe_loader = universe.load
    try:
        members = universe_loader().tickers
    except universe.UniverseError:
        notes.append('universe_unavailable')
        members = ()
    return head + sorted((pool_tickers | set(members)) - set(head)), notes
