"""The transaction base of every layer: the only place `connection` is defined.

``tests/test_core_tx_single_definition.py`` guards the single definition."""
from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

from investment_assistant.core.schema import WORKBENCH_TABLES, missing_tables
from investment_assistant.db import connect


class Unavailable(RuntimeError):
    """Database or schema not ready (HTTP 503). Never shown as an empty result."""


@dataclass
class Deps:
    bar_fetcher: Callable | None = None
    news_fetcher: Callable | None = None
    filing_source: Any = None           # object with discover(tickers, start_iso, end_iso)
    earnings_fetcher: Callable | None = None
    macro_fetcher: Callable | None = None
    universe: Any = None                    # screening universe; universe.load() when both are None
    universe_loader: Callable[[], Any] | None = None


def connection():
    url = os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL')
    if not url:
        raise Unavailable('数据库未配置')
    return connect(url)


@contextmanager
def _tx():
    import psycopg

    try:
        with connection() as conn:
            missing = missing_tables(conn, WORKBENCH_TABLES)
            if missing:
                raise Unavailable('工作台数据表未就绪（需执行迁移 002、011、016、017、018）：' + ', '.join(missing))
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
    except psycopg.OperationalError:
        raise Unavailable('数据库不可达') from None


@contextmanager
def read_only_tx():
    """Read-only transaction for read paths.

    SET TRANSACTION READ ONLY comes first, so the database itself refuses any
    write; commit on the way out simply ends the read-only transaction."""
    import psycopg

    try:
        with connection() as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            missing = missing_tables(conn, WORKBENCH_TABLES)
            if missing:
                raise Unavailable('工作台数据表未就绪（需执行迁移 002、011、016、017、018）：' + ', '.join(missing))
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
    except psycopg.OperationalError:
        raise Unavailable('数据库不可达') from None


def _jsonable(value: Any) -> Any:
    """Dates to ISO text, Decimals and UUIDs to strings, recursively."""
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (Decimal, uuid.UUID)):
        return str(value)
    return value
