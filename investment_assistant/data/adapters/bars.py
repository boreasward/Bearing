"""Daily bars: fetch from yfinance without auto-adjustment and normalize for storage."""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from investment_assistant.data import fetch_policy
from investment_assistant.data.calendar import add_trading_days, is_trading_day
from investment_assistant.core.constants import BACKUP_FILL_SESSIONS
from investment_assistant.data import repo
from investment_assistant.data.adapter_types import Rows, Source
from investment_assistant.data.adapter_support import make_adapter, receipt
from investment_assistant.data.sources import stooq

NY = ZoneInfo('America/New_York')
# Daily bars settle after the 16:00 close; one hour of slack also covers 13:00 early closes.
SESSION_SETTLED = time(17, 0)
_SIX = Decimal('0.000001')

# (ticker, start, end_exclusive) -> DataFrame with Open/High/Low/Close/Adj Close/Volume/Stock Splits
HistoryFetcher = Callable[[str, date, date], pd.DataFrame]


class BarsUnavailable(RuntimeError):
    fetch_kind = 'empty'      # an empty answer is not retried (fetch-1 spec §6.1)


def _yfinance_fetcher(ticker: str, start: date, end: date) -> pd.DataFrame:
    import yfinance as yf

    # The shared curl_cffi session, so libcurl's total / connect timeouts apply to every request yfinance makes
    # (cookie, crumb, history), plus a thread-level backstop (fetch-1 spec §6.2 row 1, §6.3).
    def call() -> pd.DataFrame:
        from yfinance.exceptions import YFPricesMissingError, YFTzMissingError

        try:
            return yf.Ticker(ticker, session=fetch_policy.shared_yf_session()).history(
                start=start.isoformat(), end=end.isoformat(), auto_adjust=False, actions=True,
                timeout=fetch_policy.TOTAL_TIMEOUT_S)
        except (YFPricesMissingError, YFTzMissingError):
            # Yahoo answered and had nothing for this ticker / range (the shared session shows yfinance's
            # exceptions, so a transport failure never lands here): the caller's final ``empty``.
            return pd.DataFrame()

    return fetch_policy.hard_deadline(call, seconds=fetch_policy.YF_HARD_DEADLINE_S)


def fetch_history(ticker: str, start: date, end: date, *, fetcher: HistoryFetcher | None = None) -> pd.DataFrame:
    """Bars for [start, end]; OHLC are split-adjusted only, Adj Close is fully adjusted."""
    df = (fetcher or _yfinance_fetcher)(ticker.upper(), start, end + timedelta(days=1))
    if df is None or df.empty:
        raise BarsUnavailable(f'no bars returned for {ticker.upper()} {start}..{end}')
    return df


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return Decimal(str(number)).quantize(_SIX)


def normalize_history(df: pd.DataFrame) -> tuple[list[dict[str, Any]], list[date]]:
    """Return (rows, skipped_dates). Incomplete rows are skipped, never filled from neighbours."""
    rows: list[dict[str, Any]] = []
    skipped: list[date] = []
    for ts, rec in df.iterrows():
        day = ts.date() if hasattr(ts, 'date') else ts
        values = {key: _decimal(rec.get(col)) for key, col in
                  (('open', 'Open'), ('high', 'High'), ('low', 'Low'), ('close', 'Close'), ('adj_close', 'Adj Close'))}
        volume = rec.get('Volume')
        if any(v is None or v <= 0 for v in values.values()) or volume is None or pd.isna(volume):
            skipped.append(day)
            continue
        split = _decimal(rec.get('Stock Splits', 0))
        rows.append({'price_date': day, **values, 'volume': int(volume),
                     'split_factor': split if split and split > 0 else Decimal('1').quantize(_SIX)})
    return rows, skipped


def latest_closed_session(now: datetime) -> date:
    """Most recent NYSE session whose daily bar has settled at ``now``."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('now must be timezone-aware')
    local = now.astimezone(NY)
    today = local.date()
    if is_trading_day(today) and local.time() >= SESSION_SETTLED:
        return today
    return add_trading_days(today, -1)


def last_n_sessions(session, n):
    end = session if is_trading_day(session) else add_trading_days(session, -1)
    return [add_trading_days(end, -offset) for offset in reversed(range(n))]


def fill_from_backup(conn, ticker, *, session, rows, store) -> dict:
    """D46: validate first, then fill only missing NYSE sessions; never revise existing dates."""
    rows = stooq.validate_rows(rows, latest_session=session)
    missing = set(repo.missing_bar_sessions(conn, ticker, last_n_sessions(session, BACKUP_FILL_SESSIONS)))
    selected = {}
    for row in rows:
        day = row['price_date']
        if day in missing:
            selected[day] = {**row, 'adj_close': row['close'] * repo.ratio_from_primary(conn, ticker, day)}
    if not selected:
        return {'inserted': 0, 'existing': 0, 'latest': None}
    return store(conn, [selected[d] for d in sorted(selected)])


def _fetch(target, *, session, start, end, deps, now):
    rows, skipped = normalize_history(fetch_history(target, start, end, fetcher=deps.bar_fetcher))
    if not rows:
        raise fetch_policy.FetchError('no valid bars', kind='empty')
    return Rows(rows, skipped=skipped)


def fetch_backup(target, *, session, start, end, deps, now):
    """Testable D46 source wrapper; intentionally absent from the official sources tuple."""
    with deps.transaction() as conn:
        missing = repo.missing_bar_sessions(conn, target, last_n_sessions(session, BACKUP_FILL_SESSIONS))
    if not missing:
        return []
    rows = stooq.fetch_bars(target, start=min(missing), end=session)
    return [r for r in rows if r['price_date'] in missing]


def _store(conn, rows, *, target, source_id, now):
    return receipt(repo.store_bars(conn, target, rows, now=now, source=source_id), rows, 'price_date')


ADAPTER = make_adapter('bars', '日线', '日线价格与复权数据', 'price_bars',
    (Source('yfinance', _fetch, fetch_policy.FetchPolicy(), '主源复权日线；Stooq spike不可用，未注册'),),
    _store, repo.latest_bar_dates,
    coverage_note='覆盖 = 目标中最新日线等于最近已收盘交易日的数量，其余列为滞后。')
