"""031 repositories: real SQL, caller transactions, cutoff and guarded state transitions."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from psycopg import sql
from psycopg.types.json import Jsonb
from test_migration_031 import DAY, NOW, REAL, TABLES, TEST_URL, apply, migrated_db  # noqa: F401

from investment_assistant.analysis import repo as analysis
from investment_assistant.core import schema
from investment_assistant.core.idempotency import Conflict
from investment_assistant.data import repo as data
from investment_assistant.execution import repo as execution
from investment_assistant.optimize import repo as optimize
from investment_assistant.workflow import repo as workflow


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


def request(**changes):
    return {
        "id": uuid.uuid4(),
        "request_id": uuid.uuid4(),
        "ticker": "nvda",
        "question": "盈利质量？",
        "horizon_days": 30,
        "created_at": NOW,
        **changes,
    }


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


def test_portfolio_first_result_wins_commentary_only_once(db):
    row = {
        "account_id": REAL,
        "session": DAY,
        "run_id": "first",
        "cutoff_at": NOW,
        "version": "portfolio-check-1",
        "result": {"verdicts": [], "cash": "100"},
    }
    id_ = execution.insert_portfolio_check(db, row)
    assert isinstance(id_, int)
    assert execution.insert_portfolio_check(db, {**row, "run_id": "rerun", "result": {"cash": "0"}}) == id_
    record = execution.portfolio_check_for(db, REAL, DAY)
    assert record is not None
    assert record["result"] == row["result"] and record["run_id"] == "first"
    pm = agent(db, mode="pm", ticker=None, status="ok")
    assert execution.set_portfolio_commentary(db, id_, {"summary": "先保留现金"}, pm) is True
    assert execution.set_portfolio_commentary(db, id_, {"summary": "改写"}, pm) is False
    commented = execution.portfolio_check_for(db, REAL, DAY)
    assert commented is not None and commented["commentary"] == {"summary": "先保留现金"}
    assert execution.portfolio_check_for(db, REAL, DAY - timedelta(days=1)) is None
    with pytest.raises(ValueError):
        execution.set_portfolio_commentary(db, id_, None, pm)


def test_research_request_replay_fifo_filters_and_expected_state(db):
    row = request()
    saved = analysis.insert_research_request(db, row)
    assert saved["ticker"] == "NVDA" and saved["status"] == "queued"
    assert analysis.insert_research_request(db, {**row, "id": uuid.uuid4()}) == saved
    with pytest.raises(Conflict):
        analysis.insert_research_request(db, {**row, "question": "changed"})
    later = analysis.insert_research_request(db, request(created_at=NOW + timedelta(days=1)))
    assert [r["id"] for r in analysis.queued_research_requests(db)] == [saved["id"], later["id"]]
    assert not analysis.update_research_status(db, saved["id"], from_status="running", to_status="concluded")
    for before, after in [
        ("queued", "running"),
        ("running", "queued"),
        ("queued", "running"),
        ("running", "waiting_data"),
        ("waiting_data", "running"),
        ("running", "concluded"),
        ("concluded", "closed"),
    ]:
        assert analysis.update_research_status(db, saved["id"], from_status=before, to_status=after, updated_at=NOW)
    closed = analysis.research_request(db, saved["id"])
    assert closed is not None and closed["status"] == "closed"
    assert analysis.research_request(db, uuid.uuid4()) is None
    assert [r["id"] for r in analysis.list_research_requests(db, status="queued")] == [later["id"]]
    assert len(analysis.list_research_requests(db, limit=1)) == 1
    assert [r["id"] for r in analysis.list_research_requests(db)] == [later["id"], saved["id"]]


def test_research_state_machine_enumerates_all_edges(db):
    states = ("queued", "running", "waiting_data", "concluded", "closed")
    allowed = {
        ("queued", "running"),
        ("queued", "closed"),
        ("running", "queued"),
        ("running", "waiting_data"),
        ("running", "concluded"),
        ("running", "closed"),
        ("waiting_data", "running"),
        ("waiting_data", "closed"),
        ("concluded", "closed"),
    }
    for before in states:
        for after in states:
            row = request(ticker="NVDA")
            raw(db, "research_requests", **row, source="manual", status=before)
            if (before, after) in allowed:
                assert analysis.update_research_status(db, row["id"], from_status=before, to_status=after)
            else:
                with pytest.raises(ValueError):
                    analysis.update_research_status(db, row["id"], from_status=before, to_status=after)
                unchanged = analysis.research_request(db, row["id"])
                assert unchanged is not None and unchanged["status"] == before
    queued = analysis.insert_research_request(db, request())
    with pytest.raises(ValueError):
        analysis.update_research_status(db, queued["id"], from_status="queued", to_status="running", ticker="MSFT")


def test_research_versions_dossier_cutoffs_and_caller_rollback(db):
    req = analysis.insert_research_request(db, request(created_at=NOW - timedelta(days=3)))
    assert analysis.update_research_status(db, req["id"], from_status="queued", to_status="running", attempts=1)
    running = analysis.research_request(db, req["id"])
    assert running is not None and running["attempts"] == 1
    base = {
        "request_id": req["id"],
        "version_no": 1,
        "cutoff_at": NOW - timedelta(days=2),
        "created_at": NOW - timedelta(days=1),
        "status": "concluded",
        "summary": "first",
        "output": {"facts": []},
        "review_when": [],
        "tokens": 10,
        "elapsed_ms": 20,
    }
    first = analysis.insert_research_version(db, base)
    second = analysis.insert_research_version(
        db, {**base, "version_no": 2, "summary": "second", "cutoff_at": NOW, "created_at": NOW + timedelta(days=1)}
    )
    assert [r["id"] for r in analysis.research_versions(db, req["id"])] == [first["id"], second["id"]]
    current = analysis.latest_dossier_for(db, "nvda", cutoff_at=NOW)
    later = analysis.latest_dossier_for(db, "NVDA", cutoff_at=NOW + timedelta(days=2))
    assert current is not None and current["id"] == first["id"]
    assert later is not None and later["id"] == second["id"]
    assert analysis.latest_dossier_for(db, "NVDA", cutoff_at=NOW - timedelta(days=2)) is None
    assert analysis.latest_dossier_for(db, "MSFT", cutoff_at=NOW) is None
    assert db.execute("SELECT count(*) FROM research_versions").fetchone()[0] == 2
    db.rollback()
    assert analysis.research_request(db, req["id"]) is None


RECORDS = [
    ("roadmap_goals", "goal_id", "g1", {"body": "目标"}),
    ("roadmap_versions", "version_id", "v3", {"title": "版本", "status": "planned", "depends_on": ["v2"]}),
    (
        "roadmap_decisions",
        "decision_no",
        51,
        {"decided_on": DAY, "question": "问题", "decision": "结论", "quote": " 原话\n", "version_ids": ["v3"]},
    ),
    ("roadmap_evidence", "evidence_id", "e1", {"version_id": "v3", "kind": "manual", "body": "证据"}),
]


@pytest.mark.parametrize("table,key,value,extra", RECORDS)
def test_roadmap_append_only_latest_history_cutoff_and_conflict(db, table, key, value, extra):
    row = {key: value, "version": 1, "created_at": NOW, **extra}
    first = optimize.insert_roadmap_record(db, table, row)
    second = optimize.insert_roadmap_record(db, table, {**row, "version": 2, "created_at": NOW + timedelta(days=1)})
    assert optimize.latest_records(db, table) == [second]
    assert optimize.latest_records(db, table, cutoff_at=NOW) == [first]
    assert optimize.latest_records(db, table, cutoff_at=NOW - timedelta(seconds=1)) == []
    assert optimize.record_history(db, table, value) == [first, second]
    with pytest.raises(Conflict):
        optimize.insert_roadmap_record(db, table, row)
    assert optimize.record_history(db, table, value) == [first, second]  # transaction remains usable
    with pytest.raises(ValueError):
        optimize.latest_records(db, "roadmap_proposals; DELETE FROM roadmap_goals")


def proposal(**changes):
    return {
        "source": "route",
        "title": "Improve",
        "change": "Change",
        "rationale": "Because",
        "evidence_refs": ["fact:route#one"],
        "expected": "Better",
        "acceptance": "Verify",
        **changes,
    }


def test_proposal_decision_and_preapproval_references(db):
    p1 = optimize.insert_proposal(db, proposal())
    p2 = optimize.insert_proposal(db, proposal(title="Version reference"))
    p3 = optimize.insert_proposal(db, proposal(title="Brief reference"))
    assert optimize.proposals_without_changes(db) == [p1["id"], p2["id"], p3["id"]]
    optimize.insert_roadmap_record(
        db,
        "roadmap_versions",
        {"version_id": "v3", "version": 1, "title": "version", "status": "planned", "proposal_id": p2["id"]},
    )
    brief = optimize.insert_brief(
        db, {"version_id": "v3", "proposal_id": p3["id"], "status": "ok", "body": "## 验收\n通过"}
    )
    assert optimize.briefs_for(db, "v3") == [brief]
    assert optimize.briefs_for(db, "v4") == []
    assert optimize.proposals_without_changes(db) == [p1["id"]]
    assert optimize.decide_proposal(db, p1["id"], decision="approved", reason="yes", decided_at=NOW)
    assert not optimize.decide_proposal(db, p1["id"], decision="rejected", reason="no", decided_at=NOW)
    approved = optimize.list_proposals(db, status="approved")
    assert len(approved) == 1 and approved[0]["decision_reason"] == "yes"
    assert approved[0]["decided_at"] == NOW
    assert len(optimize.list_proposals(db)) == 3
    assert len(optimize.list_proposals(db, status="proposed")) == 2
    with pytest.raises(ValueError):
        optimize.decide_proposal(db, p2["id"], decision="proposed", reason="bad", decided_at=NOW)


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


def test_stage_rows_sort_stages6_without_activating_it(db):
    from investment_assistant.core import stages

    current = stages.STAGE_IDS
    order = (
        "sec_monitor",
        "collect",
        "screen",
        "regime",
        "signals",
        "plan",
        "agent",
        "review",
        "portfolio",
        "accounts",
        "push",
        "retro",
        "propose",
    )
    for stage in reversed(order):
        raw(
            db,
            "stage_runs",
            job="daily",
            trigger="scheduled",
            run_id="six",
            session=DAY,
            stages_version="stages-6",
            stage=stage,
            status="success",
            input_count=0,
            output_count=0,
            started_at=NOW,
            finished_at=NOW,
            duration_ms=1,
        )
    assert [r["stage"] for r in workflow.stage_rows_for_run(db, "six")] == list(order)
    assert stages.STAGE_IDS == current


def plan_version(db, *, no, run, previous=None):
    plan = raw(
        db,
        "trade_plans",
        ticker="NVDA",
        signal_date=DAY,
        kind="agent",
        plan_version="agent-3",
        ref_price=10,
        atr=1,
        entry_low=10,
        entry_high=11,
        stop=9,
        target1=13,
        valid_from=DAY,
        valid_until=DAY,
        sizing={},
        inputs={},
        status="ok",
    )
    agent(
        db,
        id=run,
        mode="propose" if no == 0 else "revise",
        status="ok",
        plan_id=plan,
        output={"changes": [] if no == 0 else [{"field": "thesis", "text": "change"}]},
        counter={"status": "ok", "counter": [{"thesis": 1, "text": "risk", "refs": ["call:1"]}]},
    )
    raw(
        db,
        "agent_plan_versions",
        plan_id=plan,
        ticker="NVDA",
        signal_date=DAY,
        version_no=no,
        supersedes_plan_id=previous,
        origin="signal" if no == 0 else "review",
        agent_run_id=run,
        cutoff_at=NOW,
        as_of_session=DAY,
        diff=None if no == 0 else {},
        schema_version="agent-3",
        created_at=NOW,
    )
    return plan


def test_evidence_sql_aggregates(db):
    old = NOW - timedelta(days=31)
    fetch(
        db,
        status="partial",
        target_count=3,
        ok_count=1,
        detail={
            "targets": [
                {"target": "A", "ok": False, "error_kind": "timeout"},
                {"target": "B", "ok": False, "error_kind": "parse"},
                {"target": "C", "ok": True, "source_used": "stooq"},
            ]
        },
    )
    fetch(db, status="failed", target_count=3, ok_count=0, detail={})
    fetch(db, status="failed", target_count=99, ok_count=0, finished_at=old, started_at=old)
    agent(db, status="rejected", reason="refs_invalid")
    agent(db, status="error", reason="timeout")
    agent(db, status="skipped", reason="not_connected")
    agent(db, status="error", reason="old", finished_at=old, created_at=old)
    agent(db, mode="research", output={"research": {"blocked": [{"what": "sales", "why": "missing"}]}})
    agent(
        db,
        mode="research",
        output={"research": {"status": "ok", "output": {"blocked": [{"what": "income", "why": "missing"}]}}},
    )
    # Malformed/empty old JSON remains readable, with no fabricated observations.
    agent(db, mode="research", output={"research": {"blocked": None}})
    raw(db, "data_requests", family="bars", ticker="NVDA", requested_at=old, status="deferred", updated_at=NOW)
    raw(db, "data_requests", family="bars", ticker="MSFT", requested_at=old, status="deferred", updated_at=old)
    first = plan_version(db, no=0, run=uuid.uuid4())
    plan_version(db, no=1, run=uuid.uuid4(), previous=first)
    retro = agent(db, mode="retro", status="ok")
    for n, when in enumerate((NOW, NOW, old)):
        card = raw(
            db,
            "review_cards",
            event_key=f"card{n}",
            subject="unit",
            ticker="NVDA",
            signal_date=DAY,
            end_kind="expired",
            end_session=DAY,
            retro_version="retro-2",
            facts={},
        )
        draft = raw(
            db,
            "review_card_drafts",
            card_id=card,
            attempt=1,
            trigger="batch",
            agent_run_id=retro,
            status="ok",
            output={"lessons": [{"scope": "breakout"}]},
            created_at=when,
        )
        raw(
            db,
            "knowledge_drafts",
            origin="review_card",
            card_draft_id=draft,
            lesson_index=0,
            title="lesson",
            body="body",
            scope="breakout",
            counter_examples="other",
            tickers=["NVDA"],
            created_at=when,
        )
    anchor = db.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
    for version_id, version, status, age in [
        ("stale", 1, "user_acceptance_pending", 35),
        ("done", 1, "user_acceptance_pending", 50),
        ("done", 2, "done", 1),
        ("fresh", 1, "user_acceptance_pending", 20),
    ]:
        optimize.insert_roadmap_record(
            db,
            "roadmap_versions",
            {
                "version_id": version_id,
                "version": version,
                "title": version_id,
                "status": status,
                "created_at": anchor - timedelta(days=age),
            },
        )
    pending = optimize.insert_proposal(db, proposal())
    inputs = dict(
        fetch_failures=optimize.fetch_failures_since(db, NOW),
        agent_reasons=optimize.agent_reasons_since(db, NOW),
        research_blocked=optimize.research_blocked_since(db, NOW),
        counter_adoption=optimize.counter_adoption_since(db, NOW),
        lesson_repeats=optimize.lesson_repeats_since(db, NOW),
        stale_versions=optimize.stale_versions(db),
        open_proposals=optimize.list_proposals(db, status="proposed"),
    )
    assert sum(r["failed_targets"] for r in inputs["fetch_failures"]) == 5
    assert sum(r["backup_used"] for r in inputs["fetch_failures"]) == 1
    assert sum(r["backup_incomplete_runs"] for r in inputs["fetch_failures"]) == 0
    assert sum(r["partial"] for r in inputs["fetch_failures"]) == 1
    assert sum(r["failed"] for r in inputs["fetch_failures"]) == 1
    by_kind = {r["error_kind"]: r["failed_targets"] for r in inputs["fetch_failures"]}
    assert by_kind["timeout"] == 1 and by_kind["parse"] == 1 and by_kind["unknown"] == 3
    assert inputs["research_blocked"] == [{"why": "missing", "count": 2}]
    assert inputs["counter_adoption"] == [{"adopted": 1, "total": 2}]
    assert inputs["lesson_repeats"] == [{"scope": "breakout", "count": 2}]
    assert [(r["version_id"], r["days_pending"]) for r in inputs["stale_versions"]] == [("stale", 35)]
    assert optimize.stale_versions(db, days=40) == []
    assert inputs["agent_reasons"] == [
        {"mode": "data_request", "reason": "deferred", "count": 1},
        {"mode": "propose", "reason": "refs_invalid", "count": 1},
        {"mode": "propose", "reason": "timeout", "count": 1},
    ]
    assert inputs["open_proposals"] == [pending]
    assert all(
        set(row) == {"family", "error_kind", "failed_targets", "backup_used", "partial", "failed",
                     "backup_incomplete_runs"}
        for row in inputs["fetch_failures"]
    )
    assert all(
        type(row[key]) is int
        for row in inputs["fetch_failures"]
        for key in ("failed_targets", "backup_used", "partial", "failed", "backup_incomplete_runs")
    )


def test_empty_evidence_and_repeat_threshold(db):
    assert optimize.fetch_failures_since(db, NOW) == []
    assert optimize.agent_reasons_since(db, NOW) == []
    assert optimize.research_blocked_since(db, NOW) == []
    assert optimize.counter_adoption_since(db, NOW) == [{"adopted": 0, "total": 0}]
    assert optimize.lesson_repeats_since(db, NOW) == []
    assert optimize.stale_versions(db) == []


def test_stale_versions_preserves_fractional_days_over_threshold(db):
    now = db.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
    for key, age in [("boundary", 30), ("overdue", 30.5)]:
        optimize.insert_roadmap_record(
            db,
            "roadmap_versions",
            {
                "version_id": key,
                "version": 1,
                "title": key,
                "status": "user_acceptance_pending",
                "created_at": now - timedelta(days=age),
            },
        )
    rows = optimize.stale_versions(db)
    assert [(r["version_id"], r["days_pending"]) for r in rows] == [("overdue", 30.5)]


@pytest.mark.parametrize(
    "revisions,expected_days",
    [
        ([("planned", 60), ("user_acceptance_pending", 35), ("user_acceptance_pending", 10)], 35),
        ([("user_acceptance_pending", 60), ("in_progress", 40),
          ("user_acceptance_pending", 35), ("user_acceptance_pending", 10)], 35),
        ([("user_acceptance_pending", 60), ("done", 40),
          ("user_acceptance_pending", 10), ("user_acceptance_pending", 1)], None),
        ([("user_acceptance_pending", 60), ("done", 1)], None),
    ],
)
def test_stale_versions_ages_current_consecutive_pending_period(db, revisions, expected_days):
    now = db.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
    # Revision order is authoritative even if older revisions are imported afterwards.
    saved = {}
    for version, (status, age) in reversed(list(enumerate(revisions, 1))):
        saved[version] = optimize.insert_roadmap_record(
            db, "roadmap_versions", {"version_id": "pending-history", "version": version,
                "title": f"correction {version}", "status": status,
                "created_at": now - timedelta(days=age)},
        )
    rows = optimize.stale_versions(db)
    if expected_days is None:
        assert rows == []
    else:
        assert rows == [{**saved[len(revisions)], "days_pending": expected_days}]


@pytest.mark.parametrize("days", [-1, True, 30.5, "30", None])
def test_stale_versions_rejects_invalid_days(db, days):
    with pytest.raises(ValueError, match="stale_version_days"):
        optimize.stale_versions(db, days=days)


def test_fetch_failures_counts_all_final_tally_sources_before_clipping(db):
    from investment_assistant.data import collect, fetch_policy

    tally = collect.Tally("bars", "daily")
    for i in range(collect.TARGETS_MAX):
        tally.record(f"A{i:04d}", fetch_policy.Fetched(None), source_used="yfinance")
    tally.record("Z-backup", fetch_policy.Fetched(None), source_used="stooq",
                 fallback={"tried": True, "reason": "timeout"})
    tally.record("Z-retried", fetch_policy.Fetched(None), source_used="stooq")
    tally.record("Z-retried", fetch_policy.Fetched(None), source_used="yfinance")
    tally.record("Z-overwritten", fetch_policy.Fetched(None), source_used="stooq")
    tally.record("Z-overwritten", fetch_policy.FetchError("store failed", kind="other"), source_used="stooq")
    tally.record("Z-failed", fetch_policy.FetchError("timeout", kind="timeout"), source_used="stooq",
                 fallback={"tried": True, "reason": "both sources failed"})
    row = tally.row(trigger="data_request", run_id="all-targets", session=DAY,
                    started_at=NOW, finished_at=NOW)
    assert row["detail"]["source_counts"] == {"yfinance": collect.TARGETS_MAX + 1, "stooq": 1}
    assert row["detail"]["source_counts_complete"] is True
    assert row["detail"]["targets_truncated"] == 4
    assert len(row["detail"]["targets"]) == collect.TARGETS_MAX
    assert all(t["source_used"] == "yfinance" for t in row["detail"]["targets"])
    data.insert_fetch_run(db, row)
    evidence = optimize.fetch_failures_since(db, NOW)
    assert sum(r["backup_used"] for r in evidence) == 1
    assert sum(r["backup_incomplete_runs"] for r in evidence) == 0
    assert sum(r["failed_targets"] for r in evidence) == 2
    assert sum(r["partial"] for r in evidence) == 1

    # The same pre-summary historic payload only proves a lower bound, not exact zero.
    legacy_detail = {k: v for k, v in row["detail"].items()
                     if k not in {"source_counts", "source_counts_complete"}}
    data.insert_fetch_run(db, {**row, "family": "earnings", "detail": legacy_detail})
    historic = [r for r in optimize.fetch_failures_since(db, NOW) if r["family"] == "earnings"]
    assert sum(r["backup_used"] for r in historic) == 0
    assert sum(r["backup_incomplete_runs"] for r in historic) == 1
    assert sum(r["partial"] for r in historic) == 1


@pytest.mark.parametrize(
    "detail,ok_count,known,incomplete",
    [
        ({"targets": [{"target": "A", "ok": True, "source_used": "nasdaq"}]}, 1, 1, 0),
        ({"targets": [{"target": "A", "ok": True, "source_used": "stooq"},
                      {"target": "B", "ok": True}]}, 2, 1, 1),
        ({"targets": [{"target": "A", "ok": True, "source_used": "stooq"}],
          "targets_truncated": 1}, 2, 1, 1),
        ({}, 1, 0, 1),
        ({"targets": [{"target": "A", "ok": True, "fallback": {"tried": True}}]}, 1, 0, 1),
        ({"source_counts": {"stooq": 1}, "source_counts_complete": False}, 2, 1, 1),
    ],
)
def test_fetch_failures_exposes_incomplete_backup_lower_bounds(db, detail, ok_count, known, incomplete):
    fetch(db, target_count=ok_count, ok_count=ok_count, detail=detail)
    assert optimize.fetch_failures_since(db, NOW) == [
        {"family": "bars", "error_kind": None, "failed_targets": 0, "backup_used": known,
         "backup_incomplete_runs": incomplete, "partial": 0, "failed": 0}
    ]


def test_probe_rejects_constraint_that_explicitly_disallows_portfolio(db):
    db.execute("ALTER TABLE stage_runs DROP CONSTRAINT stage_runs_stage_check")
    db.execute("ALTER TABLE stage_runs ADD CONSTRAINT stage_runs_stage_check CHECK (stage <> 'portfolio')")
    assert not schema.five_layers_ready(db)


def test_queue_retries_waiting_data_and_preserves_closed_reason(db):
    first = analysis.insert_research_request(db, request(created_at=NOW - timedelta(days=1)))
    analysis.update_research_status(db, first["id"], from_status="queued", to_status="running")
    analysis.update_research_status(db, first["id"], from_status="running", to_status="waiting_data")
    second = analysis.insert_research_request(db, request())
    assert [r["id"] for r in analysis.queued_research_requests(db)] == [first["id"], second["id"]]
    assert analysis.update_research_status(
        db, first["id"], from_status="waiting_data", to_status="closed", closed_reason="manual"
    )
    closed = analysis.research_request(db, first["id"])
    assert closed is not None and closed["closed_reason"] == "manual"


def test_latest_revision_not_insertion_order_or_future_data(db):
    base = {"version_id": "v1", "title": "v1", "status": "planned", "created_at": NOW}
    latest = optimize.insert_roadmap_record(db, "roadmap_versions", {**base, "version": 3})
    optimize.insert_roadmap_record(db, "roadmap_versions", {**base, "version": 1})
    assert optimize.latest_records(db, "roadmap_versions") == [latest]
    req = analysis.insert_research_request(db, request(created_at=NOW - timedelta(days=1)))
    base = {
        "request_id": req["id"],
        "version_no": 1,
        "status": "concluded",
        "cutoff_at": NOW,
        "created_at": NOW,
        "output": {"summary": "known"},
    }
    saved = analysis.insert_research_version(db, base)
    analysis.insert_research_version(db, {**base, "version_no": 2, "cutoff_at": NOW + timedelta(days=1)})
    analysis.insert_research_version(db, {**base, "version_no": 3, "status": "error", "output": None})
    dossier = analysis.latest_dossier_for(db, "NVDA", cutoff_at=NOW)
    assert dossier is not None and dossier["id"] == saved["id"]
    assert analysis.research_versions(db, req["id"])[-1]["output"] is None


def test_discussion_draft_and_single_card_are_two_independent_lessons(db):
    retro = agent(db, mode="retro", status="ok")
    card = raw(
        db,
        "review_cards",
        event_key="one",
        subject="unit",
        ticker="NVDA",
        signal_date=DAY,
        end_kind="expired",
        end_session=DAY,
        retro_version="retro-2",
        facts={},
    )
    draft = raw(
        db,
        "review_card_drafts",
        card_id=card,
        attempt=1,
        trigger="batch",
        agent_run_id=retro,
        status="ok",
        output={"lessons": [{"scope": "shared"}, {"scope": "once"}]},
        created_at=NOW,
    )
    raw(
        db,
        "knowledge_drafts",
        origin="review_card",
        card_draft_id=draft,
        lesson_index=0,
        title="copy",
        body="body",
        scope="shared",
        counter_examples="other",
        tickers=["NVDA"],
        created_at=NOW,
    )
    assert optimize.lesson_repeats_since(db, NOW) == []
    discussion = raw(
        db,
        "discussions",
        request_id=uuid.uuid4(),
        request_hash="hash",
        context_kind="card",
        ticker="NVDA",
        signal_date=DAY,
        card_id=card,
    )
    turn = raw(
        db,
        "discussion_turns",
        discussion_id=discussion,
        seq=1,
        role="user",
        request_id=uuid.uuid4(),
        request_hash="hash",
        body="question",
    )
    raw(
        db,
        "knowledge_drafts",
        origin="discussion",
        discussion_turn_id=turn,
        title="independent",
        body="body",
        scope="shared",
        counter_examples="other",
        tickers=["NVDA"],
        created_at=NOW,
    )
    assert optimize.lesson_repeats_since(db, NOW) == [{"scope": "shared", "count": 2}]


def test_concurrent_portfolio_and_state_compare_and_set(db):
    """Two actual sessions contend on the same unique key and each guarded UPDATE."""
    import psycopg

    name = db.execute("SELECT current_schema()").fetchone()[0]
    req = analysis.insert_research_request(db, request())
    data_id = data.insert_data_requests(db, [{"family": "bars", "ticker": "NVDA", "requested_at": NOW}])[0]
    prop = optimize.insert_proposal(db, proposal())
    db.commit()

    def race(operation):
        barrier = Barrier(2)

        def run(index):
            with psycopg.connect(TEST_URL, connect_timeout=3) as conn:
                conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(name)))
                conn.execute("SET LOCAL lock_timeout = '5s'")
                barrier.wait(timeout=5)
                return operation(conn, index)

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(run, range(2)))

    ids = race(
        lambda conn, i: execution.insert_portfolio_check(
            conn,
            {
                "account_id": REAL,
                "session": DAY,
                "run_id": str(i),
                "cutoff_at": NOW,
                "version": "portfolio-check-1",
                "result": {"writer": i},
            },
        )
    )
    assert ids[0] == ids[1]
    assert db.execute("SELECT count(*) FROM portfolio_checks").fetchone()[0] == 1
    assert sorted(race(lambda c, i: execution.set_portfolio_commentary(c, ids[0], {"writer": i}, None))) == [
        False,
        True,
    ]
    assert sorted(
        race(lambda c, i: analysis.update_research_status(c, req["id"], from_status="queued", to_status="running"))
    ) == [False, True]
    assert sorted(
        race(
            lambda c, i: data.settle_data_request(
                c, data_id, status="deferred", rows=0, fetch_run_id=None, reason=str(i), rerun_run_id=None
            )
        )
    ) == [False, True]
    assert sorted(
        race(lambda c, i: optimize.decide_proposal(c, prop["id"], decision="rejected", reason=str(i), decided_at=NOW))
    ) == [False, True]
