"""Export 35 successful tool payloads (the 38 chat tools minus the three hand-written rotation ones)
from an isolated disposable PostgreSQL schema.

Run from the repository root with ``.venv/bin/python -m scripts.dev.export_tool_fixtures``
and INVESTMENT_ASSISTANT_TEST_DATABASE_URL set to the documented localhost:55433 DB.
External acquisition uses deterministic AdapterDeps; dispatch, collection, coverage,
repositories and writes are real. These snapshots are UI examples, not market evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import LiteralString, cast
from unittest.mock import patch

import pandas as pd
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from investment_assistant.config import AssistantConfig, V3Config
from investment_assistant.core import tx as core_tx
from investment_assistant.data import repo as data_repo
from investment_assistant.data.adapter_types import AdapterDeps
from investment_assistant.facts import collect as facts_collect
from investment_assistant.facts import plan_repo, research_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.toolsurface import chat as chat_tools
from investment_assistant.toolsurface.dispatch import call, tool_defs
from investment_assistant.toolsurface.env import make_env

ROOT = Path(__file__).resolve().parents[2]
SESSION = date(2026, 11, 4)
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)


def _test_module(name: str):
    # The exporter deliberately reuses the focused test seeds/migration manifest.
    tests_path = str(ROOT / "tests")
    if tests_path not in sys.path:
        sys.path.insert(0, tests_path)
    return import_module(name)


def seed(conn) -> dict:
    _test_module("test_api_today").seed(conn)
    market = _test_module("test_api_market").MARKET
    facts_repo.insert_run(
        conn,
        {
            "run_id": "fixture-market",
            "session": SESSION,
            "trigger": "scheduled",
            "status": "ok",
            "market": market,
            "draft_md": "# 示例",
            "started_at": NOW,
            "finished_at": NOW,
        },
    )
    plan_seed = _test_module("test_v3_tools_plan")
    facts_repo.insert_candidates(
        conn, "fixture-market", [plan_seed._candidate(ticker, rank) for rank, ticker in enumerate(("MSFT", "NVDA"), 1)]
    )
    for ticker in ("MSFT", "NVDA", "SPY"):
        data_repo.store_bars(conn, ticker, plan_seed._bars(), now=NOW)
    pending = plan_seed._plan(conn, "NVDA", session=SESSION - timedelta(days=1))
    research_repo.insert_research(
        conn,
        {
            "ticker": "MU",
            "thesis": "观察收盘突破",
            "origin": "chat",
            "created_session": SESSION,
            "valid_until": SESSION + timedelta(days=7),
            "condition": {"kind": "close_above", "level": 105},
            "condition_text": "收盘站上 105.00",
        },
    )
    holding = plan_repo.active_plan(conn, "AAPL")
    assert holding is not None
    return {"holding": holding["id"], "pending": pending["id"]}


class OfflineEdgar:
    """Acquisition input only; real normalization/storage executes downstream."""

    session = object()

    def __init__(self, directory: Path):
        self.evidence_dir = directory

    def get(self, url: str) -> bytes:
        if url.endswith("company_tickers.json"):
            return json.dumps({"0": {"ticker": "NFLX", "cik_str": 1}}).encode()
        if "companyfacts/CIK0000000001.json" in url:
            return json.dumps(
                {
                    "cik": 1,
                    "entityName": "Fixture company",
                    "facts": {
                        "us-gaap": {
                            "Revenues": {
                                "units": {
                                    "USD": [
                                        {
                                            "val": 200000,
                                            "start": "2026-07-01",
                                            "end": "2026-09-30",
                                            "form": "10-Q",
                                            "accn": "0000000001-26-000001",
                                            "filed": SESSION.isoformat(),
                                        }
                                    ]
                                }
                            }
                        }
                    },
                }
            ).encode()
        raise AssertionError(f"unexpected offline SEC URL: {url}")

    def discover_one(self, ticker: str, start: str, end: str) -> list[dict]:
        return [
            {
                "ticker": ticker,
                "form": "8-K",
                "filing_date": SESSION,
                "accepted_at": NOW.isoformat(),
                "items": ["2.02"],
                "accession_number": "0000000001-26-000001",
                "primary_document": "fixture.htm",
                "edgar_url": "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/fixture.htm",
            }
        ]


def offline_deps(directory: Path) -> AdapterDeps:
    bars = _test_module("test_api_today")._bars()
    frame = pd.DataFrame(
        {
            "Open": [r["open"] for r in bars],
            "High": [r["high"] for r in bars],
            "Low": [r["low"] for r in bars],
            "Close": [r["close"] for r in bars],
            "Adj Close": [r["adj_close"] for r in bars],
            "Volume": [1000] * len(bars),
            "Stock Splits": [0] * len(bars),
        },
        index=pd.to_datetime([r["price_date"] for r in bars]),
    )
    return AdapterDeps(
        transaction=core_tx._tx,
        bar_fetcher=lambda *args: frame,
        news_fetcher=lambda ticker: [
            {"title": "示例资讯", "link": "https://example.invalid/news", "pubDate": NOW.isoformat()}
        ],
        earnings_fetcher=lambda ticker: {"Earnings Date": [SESSION + timedelta(days=30)]},
        filing_source=OfflineEdgar(directory),
        sleep=lambda seconds: None,
    )


def export(conn, output: Path, directory: Path) -> int:
    ids = seed(conn)
    conn.commit()
    cfg = AssistantConfig(v3=V3Config(collect_workers=1, sec_workers=1))
    env = make_env(session=SESSION, now=NOW, cfg=cfg, mode="chat", agent_session="fixture-export")
    payloads = {}

    def capture(name: str, **args):
        assert name not in payloads, name
        envelope = call(env, name, args)
        if "error" in envelope:
            raise RuntimeError(f"{name}: {envelope['error']} {envelope.get('detail', '')}")
        assert envelope["ref"] == f"call:{len(payloads) + 1}", "tool call must be persisted"
        payloads[name] = envelope["data"]
        return envelope

    ref = capture("get_market")["ref"]
    for name in (
        "get_candidates",
        "get_coverage",
        "get_positions",
        "get_plans",
        "get_account",
        "get_agenda",
        "get_concept",
        "get_research",
    ):
        capture(name)
    for name in ("get_price_bars", "get_indicators", "get_news", "get_filings", "get_earnings", "get_fundamentals"):
        capture(name, ticker="AAPL")
    capture("compute_plan_levels", ticker="MSFT")
    capture("save_verdict", ticker="NVDA", verdict="confirm", reasons="示例形态确认", refs=[ref])
    with core_tx._tx() as tx:
        facts_repo.upsert_verdict(
            tx,
            {
                "session": SESSION,
                "ticker": "MSFT",
                "verdict": "confirm",
                "reasons": "示例确认",
                "counter": "",
                "refs": [ref],
                "origin": "chat",
                "agent_session": env.agent_session,
            },
        )
    capture("save_plan", ticker="MSFT", thesis="回调入场", invalidation="跌破止损", refs=[ref])
    capture("continue_plan", plan_id=ids["pending"], refs=[ref])
    capture("revise_plan", plan_id=ids["pending"], reason="更新论点", thesis="修订后的论点", refs=[ref])
    capture("save_holding_review", plan_id=ids["holding"], verdict="holds", reasons="继续持有", refs=[ref])
    capture("save_summary", text="已有持仓与待入场计划，等待规则触发。")
    flow = capture(
        "record_cash_flow",
        kind="deposit",
        amount=500,
        effective_at=NOW.isoformat(),
        request_id="fixture-flow",
        confirmed=True,
    )["data"]
    capture("confirm_stop_order", plan_id=ids["holding"], price=96, request_id="fixture-stop", confirmed=True)
    capture("raise_stop", plan_id=ids["holding"], stop=97, request_id="fixture-raise", confirmed=True)
    capture("cancel_plan", plan_id=ids["pending"], reason="取消示例", request_id="fixture-cancel", confirmed=True)
    capture(
        "void_record",
        kind="cash_flow",
        target_id=flow["id"],
        reason="录错入金",
        request_id="fixture-void",
        confirmed=True,
    )
    capture("watchlist_add", ticker="TSLA", thesis="观察", request_id="fixture-watch-add", confirmed=True)
    capture("watchlist_remove", ticker="TSLA", request_id="fixture-watch-remove", confirmed=True)
    capture("record_equity", equity=10000, as_of=SESSION.isoformat(), request_id="fixture-equity", confirmed=True)
    capture(
        "record_trade",
        ticker="AAPL",
        side="buy",
        quantity=1,
        price=100,
        executed_at=NOW.isoformat(),
        plan_id=ids["holding"],
        request_id="fixture-trade",
        confirmed=True,
    )
    created = capture(
        "start_research", ticker="AMZN", thesis="等待突破", request_id="fixture-research", confirmed=True
    )["data"]["research"]
    capture(
        "conclude_research",
        research_id=created["id"],
        verdict="watch",
        reasons="等待站上关键价位",
        condition={"kind": "close_above", "level": 105},
        request_id="fixture-conclude",
        confirmed=True,
    )
    capture("drop_research", research_id=created["id"], reason="结束示例", request_id="fixture-drop", confirmed=True)

    class Clock:
        @staticmethod
        def now(tz):
            return NOW.astimezone(tz)

    with (
        patch.object(facts_collect, "_default_deps", lambda cfg: offline_deps(directory)),
        patch.object(chat_tools, "datetime", Clock),
    ):
        capture("ensure_data", ticker="NFLX")
    # v3.3 的三个换仓工具样例是手写的（脚本不布换仓场景），导出时不覆盖
    assert set(payloads) == {tool.name for tool in tool_defs("chat")} - HAND_WRITTEN
    output.mkdir(parents=True, exist_ok=True)
    for name, data in payloads.items():
        with (output / f"{name}.json").open("w", encoding="utf-8") as stream:
            json.dump(core_tx._jsonable(data), stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    return len(payloads)


HAND_WRITTEN = {"get_rotation_table", "save_rotation", "withdraw_rotation"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "tests/fixtures/tool_results")
    args = parser.parse_args()
    url = os.environ.get("INVESTMENT_ASSISTANT_TEST_DATABASE_URL", "")
    options = conninfo_to_dict(url)
    database = options.get("dbname")
    if (
        options.get("host") not in ("127.0.0.1", "localhost")
        or options.get("port") != "55433"
        or not isinstance(database, str)
        or "test" not in database
    ):
        raise SystemExit("explicit disposable test database URL on localhost:55433 required")
    if set(options) - {"host", "port", "dbname", "user"}:
        raise SystemExit("test URL may only specify host, port, dbname and user")
    schema = f"tool_fixtures_{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="tool-fixtures-") as scratch:
        directory = Path(scratch)
        pgpass = directory / "empty.pgpass"
        pgpass.touch(mode=0o600)
        # No inherited DB credentials or external API keys; no dotenv/config loading.
        with (
            patch.dict(
                os.environ,
                {"PGPASSFILE": str(pgpass), "SEC_USER_AGENT": "fixtures contact@example.invalid"},
                clear=True,
            ),
            psycopg.connect(url) as conn,
        ):
            conn.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema))
            )  # pi-lens-ignore: python-sql-injection -- psycopg Identifier quotes the generated schema name
            conn.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
            )  # pi-lens-ignore: python-sql-injection -- psycopg Identifier quotes the generated schema name
            migrations = ("014_us_research.sql", *_test_module("conftest").WORKBENCH_MIGRATIONS)
            for name in migrations:
                migration = cast(LiteralString, (ROOT / "migrations" / name).read_text(encoding="utf-8"))
                conn.execute(sql.SQL(migration))  # pi-lens-ignore: python-sql-injection -- allowlisted migration SQL
            conn.commit()

            @contextmanager
            def isolated_connection():
                yield conn

            def deny_network(*args, **kwargs):
                raise AssertionError("fixture export attempted a network connection")

            try:
                with (
                    patch.object(core_tx, "connection", isolated_connection),
                    patch.object(socket.socket, "connect", deny_network),
                ):
                    count = export(conn, args.output, directory)
                print(f"exported {count} successful tool payloads")
            finally:
                conn.rollback()
                conn.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
                )  # pi-lens-ignore: python-sql-injection -- psycopg Identifier quotes the generated schema name
                conn.commit()


if __name__ == "__main__":
    main()
