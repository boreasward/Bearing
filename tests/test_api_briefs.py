"""GET /api/briefs、/api/briefs/latest、/api/briefs/{session}：列表与单份简报；没有 → 404 no_brief。"""

from datetime import UTC, date, datetime

import investment_assistant.core.tx as core_tx
from investment_assistant.api.routes.briefs import _brief_market
from investment_assistant.facts import plan_repo, repo
from investment_assistant.facts.brief import render_error_brief

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
        "sections",
        "lead",
        "footer",
        "market",
    }
    assert body["session"] == "2026-11-04" and body["markdown"] == "# 新终稿"
    assert body["unfinished"] is False
    assert body["runtime"] == "fake" and body["model"] == "model-a1"
    assert body["usage"]["total"] == 9 and body["elapsed_seconds"] == 30


def _insert_run(conn, run_id: str, session: date, market: dict, *, hour=NOW.hour):
    return repo.insert_run(
        conn,
        {
            "run_id": run_id,
            "session": session,
            "trigger": "manual_batch",
            "status": "ok",
            "market": market,
            "draft_md": "",
            "started_at": NOW.replace(hour=hour),
            "finished_at": NOW.replace(hour=hour),
        },
    )


def test_brief_has_sections_footer_and_that_days_market(client, v3_tx):
    older_session, latest_session = date(2026, 11, 3), date(2026, 11, 4)
    _insert_run(
        v3_tx,
        "brief-older",
        older_session,
        {"label": "neutral", "calc_version": "m1", "etf_table": [], "watch": [{"ticker": "ZZZ"}]},
    )
    _insert_run(v3_tx, "brief-latest", latest_session, {"label": "risk_on", "etf_table": []})
    original = (
        "# 简报 · 2026-11-03\n\n## 和昨天比变了什么\n变化\n\n"
        "## 持仓\n- AAPL\n\n数据截至 2026-11-03 收盘。\n"
    )
    plan_repo.upsert_brief(
        v3_tx,
        {
            "session": older_session,
            "run_id": "brief-older",
            "status": "final",
            "draft_md": original,
            "final_md": original,
        },
    )
    plan_repo.upsert_brief(
        v3_tx,
        {
            "session": latest_session,
            "run_id": "brief-latest",
            "status": "final",
            "draft_md": "# 新草稿",
            "final_md": "# 新终稿",
        },
    )
    v3_tx.commit()

    body = client.get("/api/briefs/2026-11-03").json()
    assert body["market"]["label"] == "neutral"
    assert body["market"]["session"] == "2026-11-03"
    assert body["market"]["calc_version"] == "m1"
    assert "watch" not in body["market"]
    assert [section["key"] for section in body["sections"]] == ["changes", "holdings"]
    assert body["footer"].startswith("数据截至") and body["lead"] is None
    assert body["markdown"] == original
    assert client.get("/api/briefs/latest").json()["market"]["label"] == "risk_on"


def test_brief_market_is_null_without_run_or_snapshot(client, v3_tx):
    assert _brief_market(v3_tx, {"run_id": "missing-run"}) is None
    sessions = [date(2026, 11, day) for day in (5, 6, 7)]
    _insert_run(v3_tx, "empty-market-run", sessions[1], {})
    _insert_run(v3_tx, "draft-only-run", sessions[2], {})
    for session, run_id, markdown in (
        (sessions[0], None, "# 无运行"),
        (sessions[1], "empty-market-run", "# 空快照"),
        (sessions[2], "draft-only-run", "# 旧草稿"),
    ):
        plan_repo.upsert_brief(
            v3_tx,
            {"session": session, "run_id": run_id, "status": "draft", "draft_md": markdown},
        )
    v3_tx.commit()

    for session in sessions:
        response = client.get(f"/api/briefs/{session.isoformat()}")
        assert response.status_code == 200 and response.json()["market"] is None
    old_draft = client.get(f"/api/briefs/{sessions[2].isoformat()}").json()
    assert old_draft["sections"] == [] and old_draft["footer"] is None


def test_error_brief_has_lead_and_no_sections(client, v3_tx):
    markdown = render_error_brief(session=date(2026, 11, 8), as_of=NOW, error="no_bars", has_holdings=True)
    plan_repo.upsert_brief(
        v3_tx,
        {"session": date(2026, 11, 8), "status": "error", "error": "no_bars", "draft_md": markdown},
    )
    v3_tx.commit()
    response = client.get("/api/briefs/2026-11-08")
    assert response.status_code == 200
    body = response.json()
    assert body["lead"].startswith("今天无法检查止损：")
    assert body["sections"] == [] and body["market"] is None and body["unfinished"] is True


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
