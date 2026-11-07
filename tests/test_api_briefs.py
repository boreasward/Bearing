"""GET /api/briefs、/api/briefs/latest、/api/briefs/{session}：列表与单份简报；没有 → 404 no_brief。"""

from datetime import UTC, date, datetime

import investment_assistant.core.tx as core_tx
from investment_assistant.facts import plan_repo

NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)


def seed(conn):
    plan_repo.upsert_brief(
        conn,
        {
            "session": date(2026, 11, 3),
            "status": "draft",
            "draft_md": "# 旧草稿",
            "runtime": "fake",
            "provider": "prov-a",
            "model": "model-a1",
            "usage": {"input": 1, "output": 2, "total": 3},
            "elapsed_seconds": 12.5,
        },
    )
    row = plan_repo.upsert_brief(
        conn,
        {
            "session": date(2026, 11, 4),
            "status": "final",
            "draft_md": "# 新草稿",
            "final_md": "# 新终稿",
            "runtime": "fake",
            "provider": "prov-a",
            "model": "model-a1",
            "usage": {"input": 4, "output": 5, "total": 9},
            "elapsed_seconds": 30,
        },
    )
    assert row["pushed_final_at"] is None
    plan_repo.update_brief(conn, date(2026, 11, 4), pushed_final_at=NOW)


def test_briefs_list_newest_first_with_pushed_flag(client, v3_tx):
    with core_tx._tx() as conn:
        seed(conn)
    resp = client.get("/api/briefs")
    assert resp.status_code == 200
    briefs = resp.json()["briefs"]
    assert [row["session"] for row in briefs] == ["2026-11-04", "2026-11-03"]
    assert set(briefs[0]) == {"session", "status", "pushed"}
    assert briefs[0]["pushed"] is True and briefs[1]["pushed"] is False
    limited = client.get("/api/briefs?limit=1").json()["briefs"]
    assert [row["session"] for row in limited] == ["2026-11-04"]


def test_briefs_limit_out_of_range_is_400(client, v3_tx):
    assert client.get("/api/briefs?limit=0").status_code == 400
    assert client.get("/api/briefs?limit=101").status_code == 400


def test_latest_brief_prefers_final_markdown(client, v3_tx):
    with core_tx._tx() as conn:
        seed(conn)
    resp = client.get("/api/briefs/latest")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "session",
        "status",
        "markdown",
        "error",
        "runtime",
        "provider",
        "model",
        "usage",
        "elapsed_seconds",
        "unfinished",
    }
    assert body["session"] == "2026-11-04" and body["markdown"] == "# 新终稿"
    assert body["unfinished"] is False
    assert body["runtime"] == "fake" and body["model"] == "model-a1"
    assert body["usage"]["total"] == 9 and body["elapsed_seconds"] == 30


def test_brief_by_session_draft_is_unfinished(client, v3_tx):
    with core_tx._tx() as conn:
        seed(conn)
    resp = client.get("/api/briefs/2026-11-03")
    assert resp.status_code == 200
    body = resp.json()
    assert body["markdown"] == "# 旧草稿" and body["unfinished"] is True


def test_missing_brief_is_404_no_brief(client, v3_tx):
    empty_latest = client.get("/api/briefs/latest")
    assert empty_latest.status_code == 404 and empty_latest.json() == {"error": "no_brief"}
    empty_day = client.get("/api/briefs/2026-11-04")
    assert empty_day.status_code == 404 and empty_day.json() == {"error": "no_brief"}
    with core_tx._tx() as conn:
        seed(conn)
    assert client.get("/api/briefs/2099-01-01").status_code == 404
