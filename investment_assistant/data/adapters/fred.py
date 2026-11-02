"""FRED credit and curve spreads (BAMLH0A0HYM2, T10Y2Y).

Observations are append-only: a revision on the same date with a different
value appends a row, reads reconstruct values as of a cutoff. The API key
comes only from the FRED_API_KEY environment variable and never appears in
messages — redacted to *** wherever it could leak.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from investment_assistant.core.redact import redact as redact
from investment_assistant.data.fetch_policy import FetchError, Fetched
from investment_assistant.data.http import get_json
from investment_assistant.data import repo
from investment_assistant.data.adapter_types import Source
from investment_assistant.data.adapter_support import ONCE, make_adapter, receipt

from investment_assistant.data.market_constants import (
    MACRO_SERIES as MACRO_SERIES, MACRO_LOOKBACK_DAYS as MACRO_LOOKBACK_DAYS,
    MACRO_CHANGE_OBS as MACRO_CHANGE_OBS,
)

FRED_URL = 'https://api.stlouisfed.org/fred/series/observations'


class NotConfigured(RuntimeError):
    """FRED_API_KEY is absent; no request is made and nothing is stored."""


def _default_fetcher(url: str, *, params: dict, api_key: str | None) -> tuple[dict | None, dict]:
    # The shared http layer logs request failures; scrub the key there too, not
    # only from the exception we raise (spec §5.1: the key never reaches logs).
    return get_json(url, params=params, target=params.get('series_id'), redact=lambda text: redact(text, api_key))


def fetch_series(series_id: str, *, start: date, end: date, api_key: str | None,
                 fetcher=None) -> list[dict]:
    """Fetch one series as [{'obs_date', 'value'}], oldest first, '.' values skipped."""
    if not api_key:
        raise NotConfigured('FRED 未配置：缺少环境变量 FRED_API_KEY')
    fetch = fetcher or (lambda url, *, params: _default_fetcher(url, params=params, api_key=api_key))
    params = {'series_id': series_id, 'api_key': api_key, 'file_type': 'json',
              'observation_start': start.isoformat(), 'observation_end': end.isoformat()}
    try:
        data, meta = fetch(FRED_URL, params=params)
    except FetchError as exc:
        raise FetchError(redact(str(exc), api_key), kind=exc.kind, status=exc.status, attempts=exc.attempts,
                         target=series_id) from None
    except Exception as exc:  # requests exceptions embed the full URL, key included
        raise RuntimeError(redact(str(exc), api_key)) from None
    if not meta.get('ok'):
        # The error category and the attempt records travel with the exception (fetch-1 spec §6.2 row 6).
        raise FetchError(redact(f"FRED 请求失败：HTTP {meta.get('status_code')} {meta.get('error')}", api_key),
                         kind=meta.get('error_kind') or 'other', status=meta.get('status_code'),
                         attempts=meta.get('attempts'), target=series_id)
    if not isinstance(data, dict) or 'observations' not in data:
        raise RuntimeError(redact(f'FRED 响应缺少 observations：{series_id}', api_key))
    rows: list[dict] = []
    for obs in data['observations']:
        raw = obs.get('value')
        if raw in (None, '.'):
            continue
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            continue
        rows.append({'obs_date': date.fromisoformat(obs['date']), 'value': value})
    rows.sort(key=lambda r: r['obs_date'])
    return rows


def _fetch(target, *, session, start, end, deps, now):
    metadata = {}
    def transport(url, *, params):
        value, meta = (deps.macro_fetcher(url, params=params) if deps.macro_fetcher else
                       _default_fetcher(url, params=params, api_key=deps.fred_api_key))
        metadata.update(meta)
        return value, meta
    rows = fetch_series(target, start=session - timedelta(days=MACRO_LOOKBACK_DAYS), end=end,
                        api_key=deps.fred_api_key, fetcher=transport)
    attempts = metadata.get('attempts', [])
    return Fetched(rows, attempts, sum(a.get('elapsed_ms', 0) for a in attempts)) if attempts else rows


def _store(conn, rows, *, target, source_id, now):
    return receipt(repo.store_macro(conn, target, rows, now=now), rows, 'obs_date')


ADAPTER = make_adapter('fred', 'FRED 利差', '宏观利差观测值', 'macro_observations',
    (Source('fred', _fetch, ONCE, 'FRED观测日；get_json内部fetch-1'),), _store, repo.latest_macro_dates,
    days=7, per_ticker=False,
    coverage_note='覆盖 = 有观测值的序列数；最新观测早于 session 前7天独立标滞后。')
