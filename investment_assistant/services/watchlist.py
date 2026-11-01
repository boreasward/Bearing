from __future__ import annotations
import investment_assistant.data.repo as data_repo

import os
from datetime import UTC, date, datetime
from typing import Any

from investment_assistant.api.http import parse_payload_tags
from investment_assistant.config import load_config
from investment_assistant.db import connect, delete_watchlist_item as db_delete_watchlist_item, list_watchlist_items, upsert_watchlist_item


def validated_symbol(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError('证券代码必须为字符串')
    from investment_assistant.data.identity import stock_id as us_stock_id
    return us_stock_id(value.strip().upper()).split(':')[-1]


def market_of(ticker: str) -> str:
    validated_symbol(ticker)
    return 'US'


def watchlist_rows(*, enriched: bool = False) -> list[dict[str, Any]]:
    database_url = os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL')
    if not database_url:
        return [_enrich(r, None) if enriched else _listed(r) for r in _config_watchlist_rows()]
    with connect(database_url) as conn:
        return [_enrich(r, conn) if enriched else _listed(r) for r in list_watchlist_items(conn)]


def _row_market(row):
    try:
        return market_of(row.get('ticker'))
    except ValueError:
        return None


def _listed(row):
    if _row_market(row) is None:
        return {**row, 'market': None, 'identity_error': '证券身份无效，请删除后使用完整有效代码重新添加'}
    return row


def _enrich(row, conn):
    market = _row_market(row)
    result = {**_listed(row), 'market': market, 'name': None, 'latest_period': None,
              'datasets': {}, 'next_event': None}
    if conn is None or market is None:
        return result
    from investment_assistant.data import repo as us_repo
    from investment_assistant.data.identity import stock_id as us_stock_id
    # Repository consumers use their owning layers below.
    sid = us_stock_id(row['ticker'])
    states = us_repo.states(conn, sid)
    financials = us_repo.read_rows(conn, sid, 'financials')
    # Instant shares can be reported after the fiscal quarter; they are not a report period.
    periods = [r['end'] for r in financials if r.get('period_kind') == 'duration'
               and r.get('start') and r.get('end') and r['start'] < r['end']]
    successful = states.get('financials', {}).get('last_success_details') or {}
    identity = successful.get('identity') or {}
    earnings = data_repo.latest_earnings(conn, row['ticker'], cutoff_at=datetime.now(UTC))
    earnings_day = str(earnings.get('earnings_date')) if earnings and earnings.get('earnings_date') else None
    next_event = ({'date': earnings_day, 'kind': 'earnings', 'title': None}
                  if earnings_day and earnings_day >= date.today().isoformat() else None)
    result.update(name=identity.get('name'), latest_period=max(periods, default=None),
                  datasets=states, next_event=next_event)
    return result


def current_watchlist() -> list[str]:
    """Legacy consumers get only active US entries; an empty selection stays empty."""
    return [str(r['ticker']).upper() for r in watchlist_rows()
            if r.get('ticker') and r.get('status', 'active') == 'active' and _row_market(r) == 'US']


def resolve_candidates(symbol: str) -> list[dict[str, Any]]:
    return [{'symbol': validated_symbol(symbol), 'market': 'US', 'asset_type': 'stock'}]


def add_watchlist_item(payload: dict[str, Any]) -> dict[str, Any]:
    database_url = os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL')
    if not database_url:
        raise ValueError('INVESTMENT_ASSISTANT_DATABASE_URL missing')
    ticker = validated_symbol(payload.get('ticker', ''))
    fields = {k: payload[k] for k in ('status', 'thesis', 'expected_updated_at') if k in payload}
    if 'tags' in payload:
        fields['tags'] = parse_payload_tags(payload['tags'])
    if 'expected_updated_at' in fields and not fields['expected_updated_at']:
        raise ValueError('编辑必须提供有效的expected_updated_at')
    with connect(database_url) as conn:
        _validate_pool_write(conn, ticker, fields)
        return upsert_watchlist_item(conn, ticker=ticker, **fields)


def _validate_pool_write(conn, ticker: str, fields: dict[str, Any]) -> None:
    """Pool caps and sector tags are checked before the write, on the would-be result."""
    rows = list_watchlist_items(conn, include_archived=True)
    existing = next((r for r in rows if r['ticker'] == ticker), None)
    status = fields.get('status') or (existing or {}).get('status') or 'active'
    if status != 'active':
        return  # only rows that are active after the write join the pool
    merged = dict(existing) if existing else {'ticker': ticker, 'thesis': None, 'tags': []}
    merged['ticker'] = ticker
    merged['status'] = status
    if 'tags' in fields:
        merged['tags'] = fields['tags']
    result = [merged if r['ticker'] == ticker else r for r in rows] if existing else rows + [merged]
    from investment_assistant.analysis.pool import from_watchlist
    from_watchlist(result)


def delete_watchlist_item(ticker: str) -> dict[str, Any]:
    database_url = os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL')
    if not database_url:
        raise ValueError('INVESTMENT_ASSISTANT_DATABASE_URL missing')
    with connect(database_url) as conn:
        return db_delete_watchlist_item(conn, ticker)


def _config_watchlist_rows() -> list[dict[str, Any]]:
    return [{'ticker': ticker, 'status': 'active', 'thesis': '来自配置文件的默认标的', 'tags': [], 'source': 'config'} for ticker in load_config().watchlist]
