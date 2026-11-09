"""Migration 033 drops the v2 tables; DDL runs only in unique schemas of the disposable test database."""

import ast
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

import pytest
from conftest import _workbench_schema

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
UPTO_032 = tuple(path.name for path in sorted(MIGRATIONS.glob("0*.sql")) if path.name < "033")
THROUGH_033 = (*UPTO_032, "033_drop_v2_tables.sql")

DROPPED = (
    "accounts",
    "account_snapshots",
    "cash_flows",
    "trades",
    "account_param_changes",
    "account_equity_days",
    "paper_orders",
    "paper_decisions",
    "paper_chain_done",
    "signal_candidates",
    "signal_briefs",
    "signal_decisions",
    "trade_plans",
    "weekly_reviews",
    "monthly_reviews",
    "market_regimes",
    "rule_backchecks",
    "review_pushes",
    "pool_tiers",
    "screen_runs",
    "screen_results",
    "agent_runs",
    "agent_tool_calls",
    "agent_plan_versions",
    "plan_reviews",
    "user_inputs",
    "job_reports",
    "scheduled_jobs",
    "notify_settings",
    "stage_runs",
    "agent_attempt_resets",
    "agent_connections",
    "agent_connection_events",
    "chain_retriggers",
    "holding_status",
    "plan_notes",
    "review_cards",
    "review_card_drafts",
    "review_card_verdicts",
    "discussions",
    "discussion_turns",
    "knowledge_items",
    "knowledge_versions",
    "knowledge_status",
    "knowledge_drafts",
    "knowledge_draft_verdicts",
    "knowledge_citations",
    "data_requests",
    "research_requests",
    "research_versions",
    "portfolio_checks",
    "roadmap_goals",
    "roadmap_versions",
    "roadmap_decisions",
    "roadmap_evidence",
    "roadmap_proposals",
    "roadmap_briefs",
)
CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations")
KEPT_FACT_TABLES = (
    "price_bars",
    "news_items",
    "news_item_tickers",
    "filings",
    "earnings_dates",
    "macro_observations",
    "sector_classifications",
    "us_research_observations",
    "us_research_collection_state",
    "data_fetch_runs",
    "watchlist_items",
    "v3_runs",
    "v3_candidates",
    "v3_verdicts",
    "v3_plans",
    "v3_trades",
    "v3_equity",
    "v3_tool_calls",
)
# Read by core.features.current (the manual fetch's backup_sources switch), so the table stays.
KEPT_FLAG_TABLE = "feature_flag_changes"


def _exists(conn, table):
    return conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is not None


def _tables(conn):
    return {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
        )
    }


@pytest.fixture
def db_032():
    yield from _workbench_schema(UPTO_032)


@pytest.fixture
def blank_schema():
    yield from _workbench_schema(())


def test_033_drops_v2_tables_and_keeps_fact_tables(db_032):
    for table in DROPPED:
        assert _exists(db_032, table), f"{table} should exist before 033"
    db_032.execute((MIGRATIONS / "033_drop_v2_tables.sql").read_text(encoding="utf-8"))
    db_032.commit()
    for table in DROPPED:
        assert not _exists(db_032, table), f"{table} survived 033"
    for table in (*KEPT_FACT_TABLES, KEPT_FLAG_TABLE):
        assert _exists(db_032, table), f"{table} was dropped by 033"
    assert _tables(db_032) - {"schema_migrations"} <= {*KEPT_FACT_TABLES, KEPT_FLAG_TABLE}


def test_033_drops_exactly_the_documented_tables(db_032):
    before = _tables(db_032)
    db_032.execute((MIGRATIONS / "033_drop_v2_tables.sql").read_text(encoding="utf-8"))
    db_032.commit()
    assert before - _tables(db_032) == set(DROPPED)


def test_033_is_rerunnable(db_032):
    text = (MIGRATIONS / "033_drop_v2_tables.sql").read_text(encoding="utf-8")
    db_032.execute(text)
    db_032.commit()
    db_032.execute(text)
    db_032.commit()
    assert not _exists(db_032, "trades")


def test_033_runs_with_langgraph_checkpoint_tables_present(db_032):
    for table in CHECKPOINT_TABLES:
        db_032.execute(f"CREATE TABLE {table} (id INT)")
    db_032.commit()
    db_032.execute((MIGRATIONS / "033_drop_v2_tables.sql").read_text(encoding="utf-8"))
    db_032.commit()
    for table in CHECKPOINT_TABLES:
        assert not _exists(db_032, table)


def test_033_keeps_rows_of_fact_tables(db_032):
    db_032.execute("INSERT INTO watchlist_items (ticker) VALUES ('MU')")
    db_032.execute(
        "INSERT INTO v3_runs (run_id, session, trigger, status, draft_md, started_at, finished_at) "
        "VALUES ('r1', '2026-11-02', 'scheduled', 'ok', '', now(), now())"
    )
    db_032.commit()
    db_032.execute((MIGRATIONS / "033_drop_v2_tables.sql").read_text(encoding="utf-8"))
    db_032.commit()
    assert db_032.execute("SELECT ticker FROM watchlist_items").fetchall() == [("MU",)]
    assert db_032.execute("SELECT run_id FROM v3_runs").fetchall() == [("r1",)]


def test_fresh_database_builds_through_033(blank_schema):
    from investment_assistant import db

    applied = db.apply_pending_migrations(blank_schema, MIGRATIONS)
    assert applied == [path.name for path in sorted(MIGRATIONS.glob("*.sql"))]
    migration_033 = "033_drop_v2_tables.sql"
    position = applied.index(migration_033)
    assert applied[position + 1 :] == [
        path.name for path in sorted(MIGRATIONS.glob("*.sql")) if path.name > migration_033
    ]
    for table in DROPPED:
        assert not _exists(blank_schema, table)
    for table in KEPT_FACT_TABLES:
        assert _exists(blank_schema, table)
    assert db.apply_pending_migrations(blank_schema, MIGRATIONS) == []


def _string_constants(path):
    """String literals in the file's code (docstrings excluded), the places a table name can sit as SQL or as a list."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    projection_keys = set()
    if path == ROOT / "investment_assistant/api/routes/ledger.py":
        queries = {"trades": ("facts_repo", "recent_trades"), "cash_flows": ("plan_repo", "recent_cash_flows")}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    continue
                if key.value not in queries or not isinstance(value, ast.Tuple) or len(value.elts) != 2:
                    continue
                query = value.elts[0]
                if (
                    isinstance(query, ast.Attribute)
                    and isinstance(query.value, ast.Name)
                    and (query.value.id, query.attr) == queries[key.value]
                ):
                    # These exact API projection keys name response arrays, not dropped v2 tables.
                    projection_keys.add(id(key))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings | projection_keys
        ):
            yield node.value


@pytest.mark.parametrize(
    "key, owner, query",
    [("trades", "facts_repo", "recent_trades"), ("cash_flows", "plan_repo", "recent_cash_flows")],
)
def test_string_constants_allow_ledger_response_projection_keys(tmp_path, monkeypatch, key, owner, query):
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    ledger = tmp_path / "investment_assistant/api/routes/ledger.py"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(f'tables = {{"{key}": ({owner}.{query}, ("id",))}}')
    assert key not in set(_string_constants(ledger))


@pytest.mark.parametrize(
    "source",
    [
        'query = "SELECT * FROM trades"',
        'tables = ["trades", "cash_flows"]',
        'tables = {"trades": (plan_repo.unrelated, ("id",))}',
        'tables = {"cash_flows": (plan_repo.recent_trades, ("id",))}',
        'tables = {"trades": (plan_repo.recent_trades, ("id",))}',
        'tables = {"cash_flows": (facts_repo.recent_cash_flows, ("id",))}',
    ],
)
def test_string_constants_keep_sql_lists_and_unrelated_mapping_keys(tmp_path, monkeypatch, source):
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    ledger = tmp_path / "investment_assistant/api/routes/ledger.py"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(source)
    assert any(re.search(r"\b(trades|cash_flows)\b", text) for text in _string_constants(ledger))


def test_string_constants_do_not_exempt_other_files(tmp_path):
    path = tmp_path / "unrelated.py"
    path.write_text('tables = {"trades": (facts_repo.recent_trades, ("id",))}')
    assert "trades" in set(_string_constants(path))


def test_no_kept_code_references_a_dropped_table():
    pattern = re.compile(r"\b(" + "|".join(DROPPED + CHECKPOINT_TABLES) + r")\b")
    hits = []
    files = [*sorted((ROOT / "investment_assistant").rglob("*.py")), *sorted((ROOT / "scripts").rglob("*.py"))]
    for path in files:
        for text in _string_constants(path):
            if text.startswith("/"):
                continue  # FastAPI 路由路径（如 v3.1 的 /api/trades），不是表引用
            match = pattern.search(text)
            if match:
                hits.append(f"{path.relative_to(ROOT)}: {match.group(1)}")
    assert not hits, "\n".join(hits)


def test_migration_has_a_drop_for_every_documented_table():
    text = (MIGRATIONS / "033_drop_v2_tables.sql").read_text(encoding="utf-8")
    dropped = re.findall(r"^DROP TABLE IF EXISTS (\w+);$", text, re.MULTILINE)
    assert sorted(dropped) == sorted((*DROPPED, *CHECKPOINT_TABLES))
    statements = "\n".join(line for line in text.splitlines() if not line.startswith("--"))
    assert "CASCADE" not in statements.upper()
    assert text.count("BEGIN;") == 1 and text.count("COMMIT;") == 1


# --- the guard in migrate.run: 033 never deletes tables that hold rows without an explicit confirmation ---

DROP_FILE = "033_drop_v2_tables.sql"


@pytest.fixture
def guarded(tmp_path):
    """(url, conn, migrations dir through 032) on an empty unique schema; ``url`` lands in that schema."""
    base = os.environ.get("INVESTMENT_ASSISTANT_TEST_DATABASE_URL")
    if not base:
        pytest.skip("explicit test database URL required; disposable schema only")
    import psycopg
    from psycopg import sql

    schema = f"g033_{uuid.uuid4().hex[:12]}"
    upto = tmp_path / "upto_032"
    upto.mkdir()
    for name in UPTO_032:
        shutil.copy(MIGRATIONS / name, upto / name)
    with psycopg.connect(base) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        conn.commit()
        try:
            yield f"{base}?options=-csearch_path%3D{schema}", conn, upto
        finally:
            conn.rollback()
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            conn.commit()


def _ledger(conn):
    return {row[0] for row in conn.execute("SELECT filename FROM schema_migrations")}


def _clear_seed_rows(conn):
    """Migrations seed scheduled_jobs, notify_settings and the real account; they count as rows for the guard."""
    for table in ("scheduled_jobs", "notify_settings", "accounts"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def test_guard_lets_a_blank_schema_build_through_033_without_confirmation(guarded):
    from investment_assistant import migrate

    url, conn, _ = guarded
    applied = migrate.run(url, MIGRATIONS)
    assert DROP_FILE in applied and len(applied) == len(list(MIGRATIONS.glob("*.sql")))
    assert not _exists(conn, "trades") and _exists(conn, "v3_runs")


def test_guard_refuses_before_applying_anything_when_a_dropped_table_has_rows(guarded):
    from investment_assistant import migrate

    url, conn, upto = guarded
    migrate.run(url, upto)
    _clear_seed_rows(conn)
    conn.execute("INSERT INTO notify_settings (id) VALUES (1)")
    conn.commit()
    ledger = _ledger(conn)
    # a pending migration that sorts before 033 must not be applied either
    extra = upto.parent / "with_extra"
    shutil.copytree(upto, extra)
    shutil.copy(MIGRATIONS / DROP_FILE, extra / DROP_FILE)
    (extra / "032a_marker.sql").write_text("CREATE TABLE guard_marker (id INT);")
    with pytest.raises(migrate.DropV2NotConfirmed) as refused:
        migrate.run(url, extra)
    assert "notify_settings" in str(refused.value) and "pg_dump" in str(refused.value)
    assert refused.value.tables == ["notify_settings"]
    conn.rollback()
    assert _ledger(conn) == ledger
    assert _exists(conn, "notify_settings") and not _exists(conn, "guard_marker")
    assert conn.execute("SELECT count(*) FROM notify_settings").fetchone()[0] == 1


def test_guard_passes_with_explicit_confirmation(guarded):
    from investment_assistant import migrate

    url, conn, upto = guarded
    migrate.run(url, upto)
    _clear_seed_rows(conn)
    conn.execute("INSERT INTO notify_settings (id) VALUES (1)")
    conn.commit()
    applied = migrate.run(url, MIGRATIONS, allow_drop_v2_tables=True)
    assert applied[0] == DROP_FILE  # 033 及其后的待执行迁移一起应用
    assert not _exists(conn, "notify_settings")
    assert DROP_FILE in _ledger(conn)


def test_guard_ignores_empty_tables(guarded):
    from investment_assistant import migrate

    url, conn, upto = guarded
    migrate.run(url, upto)
    _clear_seed_rows(conn)
    applied = migrate.run(url, MIGRATIONS)
    assert applied[0] == DROP_FILE  # 033 及其后的待执行迁移一起应用
    assert not _exists(conn, "notify_settings")


def test_guard_does_not_apply_once_033_is_recorded(guarded):
    from investment_assistant import migrate

    url, conn, _ = guarded
    migrate.run(url, MIGRATIONS)
    assert migrate.run(url, MIGRATIONS) == []


def test_guard_parses_the_table_list_from_the_sql_file():
    from investment_assistant import migrate

    assert sorted(migrate._tables_dropped_by(MIGRATIONS / DROP_FILE)) == sorted((*DROPPED, *CHECKPOINT_TABLES))


def test_refusal_message_lists_at_most_ten_tables():
    from investment_assistant import migrate

    message = str(migrate.DropV2NotConfirmed([f"t{i}" for i in range(12)]))
    assert "t9" in message and "t10" not in message and "共 12 张" in message
    assert "INVESTMENT_ASSISTANT_ALLOW_DROP_V2_TABLES=1" in message and "不可恢复" in message


@pytest.mark.parametrize("value, expected", [("1", True), ("0", False), ("true", False), (None, False)])
def test_main_passes_the_confirmation_only_for_one(monkeypatch, value, expected):
    from investment_assistant import migrate

    seen = {}
    monkeypatch.setenv("INVESTMENT_ASSISTANT_DATABASE_URL", "postgres://x")
    if value is None:
        monkeypatch.delenv("INVESTMENT_ASSISTANT_ALLOW_DROP_V2_TABLES", raising=False)
    else:
        monkeypatch.setenv("INVESTMENT_ASSISTANT_ALLOW_DROP_V2_TABLES", value)
    monkeypatch.setattr(migrate, "run", lambda url, **kwargs: seen.update(kwargs) or [])
    migrate.main()
    assert seen == {"allow_drop_v2_tables": expected}


def test_main_exits_with_the_refusal_message(monkeypatch):
    from investment_assistant import migrate

    monkeypatch.setenv("INVESTMENT_ASSISTANT_DATABASE_URL", "postgres://x")

    def refuse(url, **kwargs):
        raise migrate.DropV2NotConfirmed(["trades"])

    monkeypatch.setattr(migrate, "run", refuse)
    with pytest.raises(SystemExit) as exit_info:
        migrate.main()
    assert "trades" in str(exit_info.value)
