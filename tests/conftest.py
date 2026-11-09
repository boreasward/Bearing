import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from investment_assistant.api.app import create_app


@pytest.fixture(autouse=True)
def isolate_unit_tests_from_local_database(monkeypatch):
    """Individual DB tests opt in explicitly; a local .env must not make mocks use production."""
    monkeypatch.delenv("INVESTMENT_ASSISTANT_DATABASE_URL", raising=False)
    # A test-triggered load_dotenv() can still import keys from .env mid-session; the FRED key must never reach it.
    monkeypatch.delenv("FRED_API_KEY", raising=False)


# 离线套件里的假服务都绑在回环地址上；macOS 系统代理的例外名单可能不含 127.0.0.1，
# 会让本应打到假服务的请求被代理拦截（ProxyError）。测试进程永远不应经代理访问回环地址。
for _name in ("no_proxy", "NO_PROXY"):
    _current = os.environ.get(_name, "")
    _hosts = {host.strip() for host in _current.split(",") if host.strip()}
    if not {"127.0.0.1", "localhost"} <= _hosts:
        os.environ[_name] = ",".join(sorted({*_hosts, "127.0.0.1", "localhost"}))


@pytest.fixture()
def client():
    return TestClient(create_app())


WORKBENCH_MIGRATIONS = (
    "002_watchlist_items.sql",
    "006_job_reports.sql",
    "007_scheduled_jobs.sql",
    "010_job_reports_target_dates.sql",
    "011_filings.sql",
    "016_us_workbench.sql",
    "017_trade_plans.sql",
    "018_pool_and_market_context.sql",
    "019_agent_track.sql",
    "020_review_expansion.sql",
    "023_screen_and_beijing_jobs.sql",
    "024_data_fetch_runs.sql",
    "025_stage_runs.sql",
    "026_plan_timeline.sql",
    "027_growth_loop.sql",
    "028_daily_workflow.sql",
    "029_executable_plans.sql",
    "030_accounts.sql",
    "031_five_layers.sql",
    "032_v3_agent_first.sql",
    "033_drop_v2_tables.sql",
    "034_v3_1_plan_line.sql",
    "035_v3_2_research_and_retro.sql",
    "036_v3_2_1_tool_results.sql",
)


def _workbench_schema(migrations):
    """Workbench schema in a throwaway Postgres schema, dropped afterwards; explicit test URL only."""
    url = os.environ.get("INVESTMENT_ASSISTANT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("explicit test database URL required; disposable schema only")
    import psycopg
    from psycopg import sql

    schema = f"wb_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(url) as conn:
        # Identifier composition is psycopg's parameterized form for schema names; the migration
        # files are trusted repo sources (multi-statement, plain-string simple protocol).
        # pi-lens-ignore: python-sql-injection -- Identifier() IS psycopg's parameterized form for DDL schema names
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(
            sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
        )  # pi-lens-ignore: python-sql-injection -- Identifier() composition
        for name in migrations:
            conn.execute(Path("migrations", name).read_text(encoding="utf-8"))  # type: ignore[call-overload, arg-type]  # pi-lens-ignore: python-sql-injection -- trusted repo migration files from the constant tuples above
        conn.commit()
        try:
            yield conn
        finally:
            conn.rollback()
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )  # pi-lens-ignore: python-sql-injection -- Identifier() composition
            conn.commit()


@pytest.fixture
def workbench_db():
    """The schema through migration 036: the fact tables and the v3_ tables, the v2 tables dropped."""
    yield from _workbench_schema(WORKBENCH_MIGRATIONS)


@pytest.fixture
def v3_db():
    """The schema after migration 036: v3.2 research/weekly briefs plus optional tool results."""
    yield from _workbench_schema(WORKBENCH_MIGRATIONS)


@pytest.fixture
def v3_tx(v3_db, monkeypatch):
    """v3_db with core.tx.connection swapped to it."""
    from contextlib import contextmanager

    from investment_assistant.core import tx as core_tx

    @contextmanager
    def connection():
        yield v3_db

    monkeypatch.setattr(core_tx, "connection", connection)
    return v3_db
