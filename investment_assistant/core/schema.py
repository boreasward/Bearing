"""Schema readiness: the missing-table probe behind ``core.tx`` and the migration 031 gate of the manual fetch.

Every check is a pure existence probe on the connection: no row is ever written and a missing table never
raises."""
from __future__ import annotations

WORKBENCH_TABLES = ('price_bars', 'news_items', 'news_item_tickers', 'filings', 'earnings_dates',
                    'watchlist_items', 'macro_observations')


def missing_tables(conn, tables) -> list[str]:
    """The tables (or the ``filings.accepted_at`` column) an unmigrated database still lacks."""
    row = conn.execute('SELECT ' + ', '.join(f"to_regclass('{t}')" for t in tables)).fetchone()
    missing = [t for t, found in zip(tables, row) if found is None]
    if 'filings' in tables and 'filings' not in missing and conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'filings' AND column_name = 'accepted_at' "
            "AND table_schema = ANY(current_schemas(false))").fetchone() is None:
        missing.append('filings.accepted_at')
    return missing


FIVE_LAYER_TABLES = ('feature_flag_changes', 'sector_classifications')


def five_layers_ready(conn) -> bool:
    """The tables of migration 031 that survive migration 033, on this connection's search_path."""
    checks = ' AND '.join(f"to_regclass('{table}') IS NOT NULL" for table in FIVE_LAYER_TABLES)
    return bool(conn.execute(f'SELECT {checks}').fetchone()[0])
