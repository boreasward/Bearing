"""Stooq CSV normalization; source availability and gap filling belong to the adapter."""
from __future__ import annotations

import csv
import io
import re
from contextlib import nullcontext
from datetime import date
from decimal import Decimal, InvalidOperation

import requests

from investment_assistant.data import fetch_policy
from investment_assistant.data.fetch_policy import FetchError

URL = 'https://stooq.com/q/d/l/?s={ticker}.us&i=d'
_COLUMNS = ('Date', 'Open', 'High', 'Low', 'Close', 'Volume')


def validate_rows(rows, *, latest_session) -> list[dict]:
    """Reject the entire answer if any date/OHLC is invalid; never return a partial success."""
    valid = []
    rejected = 0
    for row in rows:
        try:
            day = row['price_date']
            values = [Decimal(str(row[key])) for key in ('open', 'high', 'low', 'close')]
            opening, high, low, close = values
            ok = (type(day) is date and day <= latest_session
                  and all(value.is_finite() and value > 0 for value in values)
                  and low <= opening <= high and low <= close <= high)
        except (KeyError, TypeError, ValueError, InvalidOperation):
            ok = False
        if ok:
            valid.append(row)
        else:
            rejected += 1
    if rejected:
        raise FetchError(f'Stooq 日线日期或 OHLC 无效（{rejected} 行）', kind='parse')
    return valid


def fetch_bars(ticker, *, start, end, session=None) -> list[dict]:
    """Inclusive [start, end]; ``end`` must be no later than the latest completed session.

    Prices remain exact Decimals. ``adj_close=close`` is only normalization; the later
    bars adapter applies the primary-source ratio to missing sessions before storage.
    Makes one HTTP attempt, leaving fallback/retry policy to the registry.
    """
    target = f'bars:{ticker.upper()}'
    try:
        with nullcontext(session) if session is not None else fetch_policy.policy_session() as client:
            response = client.get(URL.format(ticker=ticker.lower()),
                                  timeout=(fetch_policy.CONNECT_TIMEOUT_S, fetch_policy.TOTAL_TIMEOUT_S),
                                  allow_redirects=False)
    except requests.RequestException as exc:
        kind, status = fetch_policy.classify_exception(exc)
        raise FetchError('Stooq 请求失败', kind=kind, status=status, target=target) from None
    if response.status_code != 200:
        raise fetch_policy.http_error(response.status_code, f'Stooq HTTP {response.status_code}',
                                      retry_after=response.headers.get('Retry-After'))
    text = response.text.strip().lstrip('\ufeff')
    if not text or text.lower().rstrip('.') == 'no data':
        raise FetchError('Stooq 无日线数据', kind='empty', target=target)
    rows = []
    try:
        reader = csv.DictReader(io.StringIO(text), strict=True)
        if reader.fieldnames is None or len(reader.fieldnames) != len(_COLUMNS) or set(reader.fieldnames) != set(_COLUMNS):
            raise ValueError('columns')
        for raw in reader:
            if None in raw or any(raw[key] is None for key in _COLUMNS):
                raise ValueError('row shape')
            if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', raw['Date']):
                raise ValueError('date')
            prices = {key.lower(): Decimal(raw[key]) for key in ('Open', 'High', 'Low', 'Close')}
            if not re.fullmatch(r'[0-9]+', raw['Volume']):
                raise ValueError('volume')
            rows.append({'price_date': date.fromisoformat(raw['Date']), **prices,
                         'adj_close': prices['close'], 'volume': int(raw['Volume']),
                         'split_factor': Decimal('1'), 'source': 'stooq'})
    except (csv.Error, ValueError, TypeError, InvalidOperation):
        raise FetchError('Stooq CSV 格式或数值无效', kind='parse', target=target) from None
    # Validate before filtering so an unexpected future row cannot disappear silently.
    rows = validate_rows(rows, latest_session=end)
    rows = sorted((row for row in rows if start <= row['price_date'] <= end), key=lambda row: row['price_date'])
    if not rows:
        raise FetchError('Stooq 窗口内无日线数据', kind='empty', target=target)
    return rows
