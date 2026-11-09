"""Run/brief history and Beijing schedule even when there are no runs."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from test_api_chat import NOW, SESSION, seed_run

from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo


def test_runs_join_briefs_and_schedule(client, v3_tx, monkeypatch):
    seed_run(v3_tx)
    facts_repo.insert_run(
        v3_tx,
        {
            "run_id": "run-2",
            "session": SESSION + timedelta(days=1),
            "trigger": "manual_batch",
            "status": "error",
            "error": "facts_failed",
            "draft_md": "",
            "started_at": NOW,
            "finished_at": NOW,
        },
    )
    plan_repo.upsert_brief(
        v3_tx,
        {
            "session": SESSION,
            "run_id": "run-1",
            "status": "draft",
            "error": "timeout",
            "runtime": "fake",
            "provider": "prov-x",
            "model": "model-x",
            "usage": {"input": 10},
            "elapsed_seconds": 1,
        },
    )
    plan_repo.update_brief(v3_tx, SESSION, weekly_md="周报", pushed_weekly_at=NOW, pushed_error_at=NOW)
    v3_tx.commit()
    from investment_assistant.api.routes import runs
    from investment_assistant.config import AssistantConfig

    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 11, 14, 11, 31, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(tz)

    monkeypatch.setattr(runs, "datetime", Clock)
    monkeypatch.setattr(runs, "load_config", AssistantConfig)
    response = client.get("/api/runs")
    assert response.status_code == 200
    body = response.json()
    assert body["schedule"] == {
        "weekdays": [2, 3, 4, 5, 6],
        "hour": 11,
        "minute": 30,
        "tz": "Asia/Shanghai",
        "next_at": "2026-11-17T11:30:00+08:00",
    }
    assert [r["session"] for r in body["runs"]] == ["2026-11-05", "2026-11-04"]
    assert body["runs"][0] == {
        "session": "2026-11-05",
        "trigger": "manual_batch",
        "status": "error",
        "error": "facts_failed",
        "started_at": NOW.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
        "finished_at": NOW.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
        "brief": None,
    }
    assert body["runs"][1]["brief"] == {
        "status": "draft",
        "error": "timeout",
        "unfinished": True,
        "runtime": "fake",
        "provider": "prov-x",
        "model": "model-x",
        "usage": {"input": 10},
        "elapsed_seconds": 1,
        "pushed_final_at": None,
        "pushed_error_at": NOW.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
        "weekly": {"present": True, "pushed_at": NOW.astimezone(ZoneInfo("Asia/Shanghai")).isoformat()},
    }
    assert len(client.get("/api/runs?limit=1").json()["runs"]) == 1


def test_empty_database_gives_schedule_and_no_runs(client, v3_tx):
    response = client.get("/api/runs")
    assert response.status_code == 200
    body = response.json()
    assert body["runs"] == []
    assert set(body["schedule"]) == {"weekdays", "hour", "minute", "tz", "next_at"}
    assert datetime.fromisoformat(body["schedule"]["next_at"]).utcoffset() == timedelta(hours=8)


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_limit_out_of_range_is_400(client, limit):
    response = client.get(f"/api/runs?limit={limit}")
    assert response.status_code == 400 and response.json()["error"] == "invalid_argument"
