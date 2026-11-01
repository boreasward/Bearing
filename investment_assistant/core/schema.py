"""Workbench schema readiness: the missing-table probe and the migration gates (spec §4.2).

``core.tx``'s transactions depend on ``missing_tables`` and the ``*_ready`` gates (spec
§24 correction); cut verbatim from ``workbench/repository.py`` in Task 0, which
re-exports them until Task 1. Every check is a pure existence probe on the connection:
no row is ever written and a missing table never raises."""
from __future__ import annotations

WORKBENCH_TABLES = ('price_bars', 'news_items', 'news_item_tickers', 'signal_candidates', 'signal_briefs',
                    'signal_decisions', 'trades', 'weekly_reviews', 'filings', 'trade_plans', 'earnings_dates',
                    'account_snapshots', 'cash_flows', 'monthly_reviews', 'watchlist_items', 'pool_tiers',
                    'macro_observations', 'market_regimes', 'rule_backchecks', 'agent_runs', 'agent_tool_calls',
                    'review_pushes', 'screen_runs', 'screen_results')


def missing_tables(conn, tables) -> list[str]:
    """The tables (or the ``filings.accepted_at`` column) an unmigrated database still lacks."""
    row = conn.execute('SELECT ' + ', '.join(f"to_regclass('{t}')" for t in tables)).fetchone()
    missing = [t for t, found in zip(tables, row) if found is None]
    if 'filings' in tables and 'filings' not in missing and conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'filings' AND column_name = 'accepted_at' "
            "AND table_schema = ANY(current_schemas(false))").fetchone() is None:
        missing.append('filings.accepted_at')
    return missing


def accounts_ready(conn) -> bool:
    """True when migration 030 has run: the six new tables and `trades.account_id` all exist (spec §7).
    A pure existence check; never raises on a missing table."""
    tables = conn.execute(
        "SELECT to_regclass('accounts'), to_regclass('account_param_changes'), to_regclass('account_equity_days'), "
        "to_regclass('paper_orders'), to_regclass('paper_decisions'), to_regclass('paper_chain_done')").fetchone()
    if any(item is None for item in tables):
        return False
    has_account = conn.execute("SELECT 1 FROM pg_attribute WHERE attrelid = 'trades'::regclass "
                               "AND attname = 'account_id' AND NOT attisdropped").fetchone()
    return bool(has_account)


def executable_ready(conn) -> bool:
    """True when migration 029 has run: the three new tables, `trades.plan_id` and the two execution columns of
    `trade_plans` all exist (spec §8). A pure existence check; never raises on a missing table."""
    tables = conn.execute("SELECT to_regclass('chain_retriggers'), to_regclass('holding_status'), "
                          "to_regclass('plan_notes'), to_regclass('trades'), to_regclass('trade_plans')").fetchone()
    if any(item is None for item in tables):
        return False
    wanted = {('trades', 'plan_id'), ('trade_plans', 'entry_type'), ('trade_plans', 'max_hold_sessions')}
    found = conn.execute(
        'SELECT attrelid::regclass::text, attname FROM pg_attribute '
        "WHERE attrelid IN (to_regclass('trades'), to_regclass('trade_plans')) "
        "AND attname IN ('plan_id', 'entry_type', 'max_hold_sessions') AND NOT attisdropped").fetchall()
    return wanted <= {(table.rsplit('.', 1)[-1], name) for table, name in found}


def daily_ready(conn) -> bool:
    """True when migration 028 has run: `stage_runs.attempt`, the attempt-reset table and the `daily` job row.

    028 creates the credential tables in the same transaction, so the attempt-reset table stands in for them (this
    module never touches the connection tables, spec §10.2). A pure existence check (no row is written); `daily` refuses to run without it (D36)."""
    present = conn.execute("SELECT to_regclass('stage_runs'), to_regclass('agent_attempt_resets'), "
                           "to_regclass('scheduled_jobs')").fetchone()
    if any(item is None for item in present):
        return False
    has_attempt = conn.execute("SELECT 1 FROM pg_attribute WHERE attrelid = 'stage_runs'::regclass "
                               "AND attname = 'attempt' AND NOT attisdropped").fetchone()
    has_job = conn.execute("SELECT 1 FROM scheduled_jobs WHERE name = 'daily'").fetchone()
    return bool(has_attempt and has_job)


def five_layers_ready(conn) -> bool:
    """True when migration 031 has run. TODO(Task 2): implement the real gate; the placeholder
    passes so Task 0's sinks stay behaviour-neutral."""
    return True
