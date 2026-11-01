"""Data family catalog for the Data menu (design 2026-10-26 §4).

Static registry of the six families plus the display rules built on it: fetch
status classification, failure-reason sanitisation, per-family staleness and
coverage counts, and the per-family target lists. ``targets`` is the only part
that touches the database; row reads live in repository.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from investment_assistant.core.redact import REASON_MAX_CHARS as REASON_MAX_CHARS, sanitize_reason as sanitize_reason
from investment_assistant.workbench import macro, plans, pool, repository, service, universe

EVIDENCE = '这里只展示采集事实。所有规则都未经验证（Phase 0 Gate HOLD）：数据完整不代表信号有效。'
ATR_EVIDENCE = '`plan-1` 的计算输入，规则 `gate_hold`'

RECORDED = ('bars', 'news', 'sec_filings', 'fred', 'universe')   # families that write data_fetch_runs
SEC_FILINGS_LOOKBACK_DAYS = 120   # covered = filing (or sec_8k row) within this many days of session
FRED_STALE_DAYS = 7               # latest observation older than session - 7 days is lagging

# One sentence per family on what its coverage counts (spec §4 展示定义; shown on the
# card so the six different denominators are not misread, M04/D13).
COVERAGE_NOTES = {
    'bars': '覆盖 = 目标中最新日线等于最近已收盘交易日的数量，其余列为滞后。',
    'sec_filings': '覆盖 = 近 120 天有申报记录（filings 行或 sec_8k）的标的数；无申报 ≠ 缺数据。',
    'news': '覆盖 = 近 7 天有资讯标题的标的数（按 UTC 零点计窗口）。',
    'atr': '覆盖 = 有 ≥ 15 根日线且最新一根为最近已收盘交易日的标的数。',
    'fred': '覆盖 = 有观测值的序列数；最新观测早于 session 前 7 个自然日的列为滞后（覆盖与滞后独立）。',
    'universe': '覆盖 = 范围文件成员数（合法区间 90–105）；超过 120 天未更新标「过期」。',
}


@dataclass(frozen=True)
class Family:
    id: str
    title: str
    purpose: str
    kind: str                     # fetched / derived / manual
    job: str                      # task whose schedule and toggle govern this family
    per_ticker: bool              # can be fetched for a single ticker
    fetch_via: str | None = None  # derived families point at the family they are computed from


FAMILIES: tuple[Family, ...] = (
    Family(
        id='bars',
        title='日线',
        purpose='用于规则 v1 信号、`plan-1` 价位、`screen-1` 筛选、区制广度和「标的」页 K 线。',
        kind='fetched', job='daily', per_ticker=True,
    ),
    Family(
        id='sec_filings',
        title='SEC 申报',
        purpose='用于 `earnings_gap_up` 的财报事件、「标的」页 SEC 列表和 SEC 监控通知。',
        kind='fetched', job='daily', per_ticker=True,
    ),
    Family(
        id='news',
        title='资讯',
        purpose='用于 Agent 工具读取的资讯标题和「标的」页资讯列表；规则不读。',
        kind='fetched', job='daily', per_ticker=True,
    ),
    Family(
        id='atr',
        title='ATR',
        purpose='用于 `plan-1` 止损与仓位、`screen-1` 的 ATR 区间和移动止损。',
        kind='derived', job='daily', per_ticker=False, fetch_via='bars',
    ),
    Family(
        id='fred',
        title='FRED 利差',
        purpose='用于区制的宏观部分：HY 利差 `BAMLH0A0HYM2`、2s10s `T10Y2Y`。',
        kind='fetched', job='daily', per_ticker=False,
    ),
    Family(
        id='universe',
        title='纳指 100 范围',
        purpose='用于 `screen-1` 的筛选范围和区制广度样本。',
        kind='manual', job='daily', per_ticker=False,
    ),
)

_BY_ID = {family.id: family for family in FAMILIES}


def get(family_id: str) -> Family:
    family = _BY_ID.get(family_id)
    if family is None:
        raise LookupError(f'未知数据族：{family_id}')
    return family


def classify(target_count: int, ok_count: int, skipped_reason: str | None = None) -> str:
    """Map one fetch to its data_fetch_runs status: skipped / success / failed / partial."""
    if skipped_reason:
        return 'skipped'
    if ok_count >= target_count:
        return 'success'
    if ok_count == 0:
        return 'failed'
    return 'partial'


def _as_date(latest: date | datetime) -> date:
    return latest.date() if isinstance(latest, datetime) else latest


def _as_utc(latest: date | datetime) -> datetime:
    if isinstance(latest, datetime):
        return latest if latest.tzinfo else latest.replace(tzinfo=timezone.utc)
    return datetime.combine(latest, time(), tzinfo=timezone.utc)


def lagging(family_id: str, latest: date | datetime | None, *, session: date,
            bar_count: int | None = None) -> bool:
    """Per-family staleness definition (spec §4 「最新时点」 column).

    A display definition, not a rule parameter. universe is excluded: its
    staleness is universe.is_stale on the range file, not latest-vs-session.
    """
    if family_id == 'universe':
        raise ValueError('universe 的过期由 universe.is_stale 判断，不用 lagging')
    if latest is None:
        return True
    if family_id in ('bars', 'atr'):
        if _as_date(latest) < session:
            return True
        if family_id == 'atr':
            return bar_count is None or bar_count < plans.MIN_BARS
        return False
    if family_id == 'news':
        cutoff = datetime.combine(session - timedelta(days=service.NEWS_LOOKBACK_DAYS),
                                  time(), tzinfo=timezone.utc)
        return _as_utc(latest) < cutoff
    if family_id == 'sec_filings':
        return _as_date(latest) < session - timedelta(days=SEC_FILINGS_LOOKBACK_DAYS)
    if family_id == 'fred':
        return _as_date(latest) < session - timedelta(days=FRED_STALE_DAYS)
    raise LookupError(f'未知数据族：{family_id}')


def coverage(family_id: str, targets: list[str], latest: dict[str, Any], *,
             session: date, bar_counts: dict[str, int] | None = None) -> dict:
    """Covered targets plus the lagging list.

    FRED is special (spec §4): covered counts series with any observation, and
    lagging (latest older than session - 7 days) is an independent freshness
    flag — a series can be covered and lagging at once (I12). Everywhere else
    covered means fresh, so missing latest keys count as lagging."""
    lagged = [t for t in targets
              if lagging(family_id, latest.get(t), session=session,
                         bar_count=None if bar_counts is None else bar_counts.get(t))]
    covered = (sum(1 for t in targets if latest.get(t) is not None) if family_id == 'fred'
               else len(targets) - len(lagged))
    return {'targets': len(targets), 'covered': covered, 'lagging': lagged}


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
    family = get(family_id)
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
    pool_tickers |= {ticker for ticker, row in repository.pool_latest(conn).items()
                     if row['tier'] != 'removed' and row['source'] == 'screen'}
    pool_tickers |= repository.open_position_tickers(conn)   # 持仓：未作废成交得出的未平仓标的
    if family.id not in ('bars', 'atr'):
        return sorted(pool_tickers), notes
    head = [service.BENCHMARK, *pool.SECTOR_ETFS]
    if universe_loader is None:
        universe_loader = universe.load
    try:
        members = universe_loader().tickers
    except universe.UniverseError:
        notes.append('universe_unavailable')
        members = ()
    return head + sorted((pool_tickers | set(members)) - set(head)), notes
