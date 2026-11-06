"""031 repositories: real SQL, caller transactions, cutoff and guarded state transitions."""

import uuid
from datetime import timedelta

import pytest
from psycopg import sql
from psycopg.types.json import Jsonb
from test_migration_031 import DAY, NOW, TABLES, apply, migrated_db

from investment_assistant.core import schema
from investment_assistant.data import repo as data


@pytest.fixture
def db(migrated_db):
    apply(migrated_db)
    return migrated_db


def raw(conn, table, **values):
    """Only fixture setup; production functions remain the subject of assertions."""
    with conn.transaction():
        return conn.execute(
            sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
                sql.Identifier(table),
                sql.SQL(", ").join(map(sql.Identifier, values)),
                sql.SQL(", ").join(sql.Placeholder() for _ in values),
            ),
            [
                Jsonb(v)
                if isinstance(v, (dict, list))
                and k not in {"depends_on", "version_ids", "evidence_refs", "concepts", "tickers"}
                else v
                for k, v in values.items()
            ],
        ).fetchone()[0]


def agent(conn, **changes):
    return raw(
        conn,
        "agent_runs",
        id=changes.pop("id", uuid.uuid4()),
        **{
            "ticker": "NVDA",
            "signal_date": DAY,
            "pool": "main",
            "attempt": 1,
            "mode": "propose",
            "agent_version": "agent-4",
            "provider": "chatgpt",
            "model": "test",
            "cutoff_at": NOW,
            "status": "pass",
            "started_at": NOW,
            "finished_at": NOW,
            "created_at": NOW,
            **changes,
        },
    )


def fetch(conn, **changes):
    return data.insert_fetch_run(
        conn,
        {
            "family": "bars",
            "job": "daily",
            "trigger": "data_request",
            "scope": "family",
            "status": "success",
            "target_count": 1,
            "ok_count": 1,
            "started_at": NOW,
            "finished_at": NOW,
            **changes,
        },
    )["id"]


def test_data_request_settlement_is_once_and_rerun_pointer_is_write_once(db):
    run = agent(db)
    rows = [
        {"run_id": "batch", "agent_run_id": run, "family": "bars", "ticker": "nvda", "requested_at": NOW},
        {"run_id": "other", "family": "news", "ticker": "MSFT", "requested_at": NOW},
    ]
    ids = data.insert_data_requests(db, rows)
    assert len(ids) == 2 and data.insert_data_requests(db, []) == []
    queued = data.queued_data_requests(db, "batch")
    assert [(r["id"], r["ticker"], r["status"]) for r in queued] == [(ids[0], "NVDA", "queued")]
    fetched = fetch(db)
    args = dict(status="fetched", fetch_run_id=fetched, rows=2, reason=None, rerun_run_id=None)
    assert data.settle_data_request(db, ids[0], **args) is True
    before = db.execute("SELECT * FROM data_requests WHERE id=%s", (ids[0],)).fetchone()
    assert data.settle_data_request(db, ids[0], **{**args, "status": "failed", "rows": 0}) is False
    assert db.execute("SELECT * FROM data_requests WHERE id=%s", (ids[0],)).fetchone() == before
    rerun = agent(db, attempt=2)
    assert data.attach_data_request_rerun(db, ids[0], rerun) is True
    assert data.attach_data_request_rerun(db, ids[0], uuid.uuid4()) is False
    assert data.attach_data_request_rerun(db, ids[1], rerun) is False
    assert data.queued_data_requests(db, "batch") == []
    assert db.execute("SELECT status, rows, rerun_run_id FROM data_requests WHERE id=%s", (ids[0],)).fetchone() == (
        "fetched",
        2,
        rerun,
    )
    with pytest.raises(ValueError):
        data.settle_data_request(db, ids[1], **{**args, "status": "queued"})


@pytest.mark.parametrize("status", ["failed", "deferred", "fetched"])
def test_data_request_no_rerun_for_failed_deferred_or_zero_new_rows(db, status):
    id_ = data.insert_data_requests(db, [{"family": "bars", "ticker": "NVDA", "requested_at": NOW}])[0]
    assert data.settle_data_request(
        db, id_, status=status, fetch_run_id=None, rows=0, reason="no new rows", rerun_run_id=None
    )
    assert not data.attach_data_request_rerun(db, id_, uuid.uuid4())


def test_sector_latest_respects_cutoff_and_ties(db):
    first = data.insert_sector_classification(
        db,
        {
            "ticker": "nvda",
            "sic": "3674",
            "sic_description": "半导体",
            "group": "chips",
            "source": "sec",
            "fetched_at": NOW,
        },
    )
    second = data.insert_sector_classification(
        db, {"ticker": "NVDA", "group": "hardware", "source": "override", "fetched_at": NOW + timedelta(days=1)}
    )
    assert first != second
    assert data.latest_sectors(db, [], cutoff_at=NOW) == {}
    assert data.latest_sectors(db, ["nvda", "MSFT"], cutoff_at=NOW - timedelta(seconds=1)) == {}
    assert data.latest_sectors(db, ["nvda", "MSFT"], cutoff_at=NOW)["NVDA"]["group"] == "chips"
    assert data.latest_sectors(db, ["NVDA"], cutoff_at=NOW + timedelta(days=2))["NVDA"]["group"] == "hardware"


def test_bar_sources_missing_sessions_and_earnings_source(db):
    bar = {
        "price_date": DAY,
        "open": 10,
        "high": 12,
        "low": 9,
        "close": 11,
        "adj_close": 11,
        "volume": 100,
        "split_factor": 1,
    }
    assert data.store_bars(db, "nvda", [bar], source="stooq", now=NOW)["inserted"] == 1
    assert data.store_bars(db, "NVDA", [{**bar, "close": 12}], now=NOW)["inserted"] == 1
    assert db.execute("SELECT source FROM price_bars ORDER BY revision").fetchall() == [("stooq",), ("yfinance",)]
    sessions = [DAY - timedelta(days=1), DAY, DAY + timedelta(days=1)]
    assert data.missing_bar_sessions(db, "nvda", sessions) == [sessions[0], sessions[2]]
    assert data.missing_bar_sessions(db, "NVDA", []) == []
    data.store_earnings(db, "NVDA", {"status": "known", "earnings_date": DAY}, source="nasdaq", now=NOW)
    earnings = data.latest_earnings(db, "NVDA", cutoff_at=NOW)
    assert earnings is not None and earnings["source"] == "nasdaq"


def test_five_layers_probe_checks_every_table_and_exact_constraint(migrated_db):
    db = migrated_db
    assert schema.five_layers_ready(db) is False
    apply(db)
    assert schema.five_layers_ready(db) is True
    for table in TABLES:
        db.execute(sql.SQL("ALTER TABLE {} RENAME TO hidden_031").format(sql.Identifier(table)))
        assert schema.five_layers_ready(db) is False, table
        db.execute(sql.SQL("ALTER TABLE hidden_031 RENAME TO {}").format(sql.Identifier(table)))
    db.execute("ALTER TABLE stage_runs DROP CONSTRAINT stage_runs_stage_check")
    assert schema.five_layers_ready(db) is False
    db.execute("ALTER TABLE stage_runs ADD CONSTRAINT stage_runs_stage_check CHECK (stage <> 'not_portfolio')")
    assert schema.five_layers_ready(db) is False
    db.execute("ALTER TABLE stage_runs DROP CONSTRAINT stage_runs_stage_check")
    db.execute("ALTER TABLE stage_runs ADD CONSTRAINT stage_runs_stage_check CHECK (stage IN ('portfolio', 'collect'))")
    assert schema.five_layers_ready(db) is True
    db.execute("ALTER TABLE stage_runs RENAME TO hidden_stages")
    assert schema.five_layers_ready(db) is False


def test_probe_rejects_constraint_that_explicitly_disallows_portfolio(db):
    db.execute("ALTER TABLE stage_runs DROP CONSTRAINT stage_runs_stage_check")
    db.execute("ALTER TABLE stage_runs ADD CONSTRAINT stage_runs_stage_check CHECK (stage <> 'portfolio')")
    assert not schema.five_layers_ready(db)
