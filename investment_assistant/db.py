from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

_POOLS: dict[str, Any] = {}
_POOLS_LOCK = threading.Lock()


def _get_pool(database_url: str):
    with _POOLS_LOCK:
        pool = _POOLS.get(database_url)
        if pool is None:
            from psycopg_pool import ConnectionPool

            pool = ConnectionPool(
                database_url,
                min_size=1,
                max_size=int(os.environ.get("INVESTMENT_ASSISTANT_DB_POOL_MAX", "8")),
                open=True,
            )
            _POOLS[database_url] = pool
        return pool


def connect(database_url: str):
    """Return a pooled connection context manager.

    Usage is unchanged from the old per-call psycopg.connect:
        with connect(url) as conn:
            ...
    On exit the connection is returned to the process-wide pool instead of
    being closed.
    """
    return _get_pool(database_url).connection()


def close_pools() -> None:
    """Close and drop all cached pools (best-effort, idempotent).

    One-shot CLIs (e.g. investment_assistant.migrate) must call this before
    exiting: on Python 3.13+ an unclosed ConnectionPool raises
    PythonFinalizationError from __del__ at interpreter shutdown.
    """
    with _POOLS_LOCK:
        for pool in _POOLS.values():
            try:
                pool.close()
            except Exception:
                pass
        _POOLS.clear()


def _ensure_migration_ledger(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              filename TEXT PRIMARY KEY,
              applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    conn.commit()


def applied_migrations(conn) -> set[str]:
    _ensure_migration_ledger(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT filename FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def apply_pending_migrations(conn, migrations_dir: str | Path) -> list[str]:
    """Apply every ``*.sql`` in ``migrations_dir`` not yet recorded, in order.

    Records each in ``schema_migrations`` so re-runs are no-ops and the suite
    supports ``ALTER``/data migrations (not only ``CREATE ... IF NOT EXISTS``).
    Returns the filenames that were applied this call.
    """
    done = applied_migrations(conn)
    applied: list[str] = []
    for sql_path in sorted(Path(migrations_dir).glob("*.sql")):
        if sql_path.name in done:
            continue
        sql = sql_path.read_text(encoding="utf-8")
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                "INSERT INTO schema_migrations (filename) VALUES (%s) ON CONFLICT DO NOTHING",
                (sql_path.name,),
            )
        conn.commit()
        applied.append(sql_path.name)
    return applied


def list_watchlist_items(conn, *, include_archived: bool = False) -> list[dict[str, Any]]:
    where = "" if include_archived else "WHERE status <> 'archived'"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT ticker, status, thesis, tags, created_at, updated_at
            FROM watchlist_items
            {where}
            ORDER BY ticker ASC
            """
        )
        rows = cur.fetchall()
    keys = ["ticker", "status", "thesis", "tags", "created_at", "updated_at"]
    return [dict(zip(keys, row)) for row in rows]


class WatchlistConflict(ValueError):
    """The record was edited or removed since the client read it."""


_WATCHLIST_UNSET = object()
_WATCHLIST_FIELDS = ("status", "thesis", "tags")
_WATCHLIST_DEFAULTS = {"status": "active", "thesis": None, "tags": []}


def _normalize_watchlist_ticker(ticker: str) -> str:
    normalized = str(ticker or "").strip().upper()
    if not normalized:
        raise ValueError("ticker is required")
    if not normalized.replace(".", "").replace("-", "").isalnum():
        raise ValueError("ticker contains unsupported characters")
    return normalized


def upsert_watchlist_item(
    conn,
    *,
    ticker: str,
    status=_WATCHLIST_UNSET,
    thesis=_WATCHLIST_UNSET,
    tags=_WATCHLIST_UNSET,
    expected_updated_at=None,
) -> dict[str, Any]:
    """Insert or partially update one watchlist row (recovered from commit d082400, spec §10)."""
    payload: dict[str, Any] = {"ticker": _normalize_watchlist_ticker(ticker)}
    for field in _WATCHLIST_FIELDS:
        value = locals()[field]
        payload[f"{field}_supplied"] = value is not _WATCHLIST_UNSET
        payload[field] = _WATCHLIST_DEFAULTS[field] if value is _WATCHLIST_UNSET else value
    if payload["status"] not in {"active", "paused", "archived"}:
        raise ValueError("status must be active, paused, or archived")
    if expected_updated_at is not None:
        from datetime import datetime

        try:
            expected = (
                datetime.fromisoformat(expected_updated_at)
                if isinstance(expected_updated_at, str)
                else expected_updated_at
            )
            if not isinstance(expected, datetime) or expected.tzinfo is None:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("expected_updated_at must be a timezone-aware timestamp") from None
        payload["expected_updated_at"] = expected
    assignments = ", ".join(
        f"{field} = CASE WHEN %({field}_supplied)s THEN %({field})s ELSE watchlist_items.{field} END"
        for field in _WATCHLIST_FIELDS
    )
    with conn.cursor() as cur:
        if expected_updated_at is not None:
            # A single compare-and-set UPDATE prevents lost edits and deleted-row resurrection.
            # pi-lens-ignore: python-sql-injection -- 字段名来自固定元组 _WATCHLIST_FIELDS，值全部参数化
            cur.execute(
                f"""UPDATE watchlist_items SET {assignments}, updated_at=clock_timestamp()
                WHERE ticker=%(ticker)s AND updated_at=%(expected_updated_at)s
                RETURNING ticker,status,thesis,tags,created_at,updated_at""",
                payload,
            )
        else:
            # pi-lens-ignore: python-sql-injection -- 同上：字段名固定，值全部参数化
            cur.execute(
                f"""INSERT INTO watchlist_items(ticker,status,thesis,tags)
                VALUES (%(ticker)s,%(status)s,%(thesis)s,%(tags)s)
                ON CONFLICT(ticker) DO UPDATE SET {assignments}, updated_at=clock_timestamp()
                RETURNING ticker,status,thesis,tags,created_at,updated_at""",
                payload,
            )
        row = cur.fetchone()
    if row is None:
        raise WatchlistConflict("关注记录已修改或删除，请重新读取后再编辑")
    return dict(zip(["ticker", "status", "thesis", "tags", "created_at", "updated_at"], row))


def delete_watchlist_item(conn, ticker: str) -> dict[str, Any]:
    normalized = _normalize_watchlist_ticker(ticker)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM watchlist_items WHERE ticker = %(ticker)s RETURNING ticker", {"ticker": normalized})
        row = cur.fetchone()
    return {"ticker": normalized, "deleted": row is not None}


def upsert_filing(
    conn,
    *,
    ticker: str,
    form: str,
    filing_date,
    accession_number: str,
    primary_document: str,
    file_path: str,
    run_id: str | None = None,
    accepted_at: str | None = None,
    items: list[str] | None = None,
) -> None:
    """accepted_at/items only fill unknown values; a recorded acceptance is never replaced."""
    from datetime import datetime

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO filings (
              ticker, form, filing_date, accession_number, primary_document, file_path, run_id,
              accepted_at, items
            ) VALUES (
              %(ticker)s, %(form)s, %(filing_date)s, %(accession_number)s,
              %(primary_document)s, %(file_path)s, %(run_id)s, %(accepted_at)s, %(items)s
            )
            ON CONFLICT (accession_number, primary_document) DO UPDATE SET
              file_path = EXCLUDED.file_path,
              run_id = COALESCE(EXCLUDED.run_id, filings.run_id),
              accepted_at = COALESCE(filings.accepted_at, EXCLUDED.accepted_at),
              items = COALESCE(filings.items, EXCLUDED.items)
            """,
            {
                "ticker": ticker,
                "form": form,
                "filing_date": filing_date,
                "accession_number": accession_number,
                "primary_document": primary_document,
                "file_path": file_path,
                "run_id": run_id,
                "accepted_at": datetime.fromisoformat(accepted_at) if accepted_at else None,
                "items": items,
            },
        )
    conn.commit()
