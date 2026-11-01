"""Pin API routing and no-DB fallback behaviour before layering refactor."""
import investment_assistant.analysis.agent.runner as analysis_agent_runner
from investment_assistant.execution import trades as execution_trades
import pytest


@pytest.fixture(autouse=True)
def no_db(monkeypatch):
    monkeypatch.delenv("INVESTMENT_ASSISTANT_DATABASE_URL", raising=False)


def test_unknown_api_get_returns_404(client):
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404
    assert resp.json() == {"error": "not found"}


def test_status_get_route_resolves(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    assert set(resp.json().keys()) == {"database", "filings", "system"}


def test_operations_registry_shape(client):
    resp = client.get("/api/operations")
    assert resp.status_code == 200
    ids = {op["id"] for op in resp.json()["operations"]}
    assert ids == {"sync_filings", "health_check"}


def test_watchlist_get_falls_back_to_config_without_db(client):
    resp = client.get("/api/watchlist")
    assert resp.json()["count"] >= 1
    assert all(row["source"] == "config" for row in resp.json()["rows"])


def test_data_routes_without_db(client):
    resp = client.get("/api/data/families")
    assert resp.status_code == 503
    assert "数据库未配置" in resp.json()["error"]


def test_delete_route_resolves(monkeypatch, client):
    import investment_assistant.services.watchlist as watchlist_svc
    monkeypatch.setattr(watchlist_svc, "delete_watchlist_item", lambda ticker: {"ticker": ticker, "deleted": True})
    resp = client.delete("/api/watchlist/NVDA")
    assert resp.status_code == 200
    assert resp.json() == {"ticker": "NVDA", "deleted": True}
    resp_nope = client.delete("/api/nope")
    assert resp_nope.status_code == 404


DERIVATION_PATHS = {
    "/api/workbench/plans/{plan_id}/derivation",
    "/api/workbench/units/{ticker}/{signal_date}/derivation",
    "/api/workbench/stage-runs",
}


def test_derivation_and_stage_runs_routes_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert DERIVATION_PATHS <= set(spec["paths"])
    for path in DERIVATION_PATHS:
        assert set(spec["paths"][path].keys()) == {"get"}          # 全部是只读 GET


def test_plan_derivation_response_top_level_keys(client, monkeypatch):
    import investment_assistant.views.derivation as derivation_view
    use_case = {"derivation_version": "derivation-1", "plan": {"id": "p1"}, "trigger": [],
                "inputs": {}, "steps": [], "checks": [], "consistency": {"status": "consistent"},
                "evidence": "evidence", "trigger_note": "note", "run": {"ready": True}}
    monkeypatch.setattr(derivation_view, "plan_derivation", lambda plan_id: dict(use_case))
    resp = client.get("/api/workbench/plans/6f1d1c1e-6666-4444-8888-000000000001/derivation")
    assert resp.status_code == 200
    assert set(resp.json().keys()) == {"derivation_version", "plan", "trigger", "inputs", "steps",
                                       "checks", "consistency", "evidence", "run"}


TIMELINE_ROUTES = {
    "/api/workbench/chains": {"get"},
    "/api/workbench/chains/{ticker}/{signal_date}": {"get"},
    "/api/workbench/plan-reviews": {"get"},
    "/api/workbench/chains/{ticker}/{signal_date}/recheck": {"post"},
    "/api/workbench/user-inputs": {"get", "post"},
}


def test_plan_timeline_routes_in_openapi(client):
    spec = client.get("/openapi.json").json()
    for path, methods in TIMELINE_ROUTES.items():
        assert path in spec["paths"], path
        assert set(spec["paths"][path].keys()) == methods, path


def test_chain_view_response_top_level_keys(client, monkeypatch):
    import investment_assistant.workflow.nodes.review as plan_review
    use_case = {"ticker": "SYNTH", "signal_date": "2026-09-01", "session": "2026-09-02",
                "state": {"state": "entry", "mode": "entry", "basis": "未决定"},
                "head_plan_id": "p1", "version_no": 0, "latest_review": None,
                "versions": [], "reviews": [], "inputs": []}
    monkeypatch.setattr(plan_review, "chain_view", lambda ticker, signal_date, *, now: dict(use_case))
    resp = client.get("/api/workbench/chains/SYNTH/2026-09-01")
    assert resp.status_code == 200
    body = resp.json()
    assert {"state", "versions", "reviews", "inputs"} <= set(body)
    assert set(body) == set(use_case)


def test_timeline_routes_without_db_are_503(client):
    assert client.get("/api/workbench/chains/SYNTH/2026-09-01").status_code == 503
    assert client.get("/api/workbench/chains?ticker=SYNTH").status_code == 503
    assert client.get("/api/workbench/plan-reviews?session=2026-09-02").status_code == 503
    assert client.get("/api/workbench/user-inputs?ticker=SYNTH").status_code == 503
    assert client.post("/api/workbench/chains/SYNTH/2026-09-01/recheck").status_code == 503


def test_ticker_chains_response_shape(client, monkeypatch):
    import investment_assistant.workflow.nodes.review as plan_review
    use_case = {"ticker": "SYNTH", "chains": [{"signal_date": "2026-09-01", "state": "entry", "mode": "entry",
                                               "version_no": 1, "head_plan_id": "p1", "latest_review": None}]}
    monkeypatch.setattr(plan_review, "ticker_chains", lambda ticker, *, now: use_case)
    body = client.get("/api/workbench/chains?ticker=SYNTH").json()
    assert set(body) == {"ticker", "chains"}
    assert set(body["chains"][0]) == {"signal_date", "state", "mode", "version_no", "head_plan_id", "latest_review"}


KNOWLEDGE_PATHS = {
    "/api/knowledge/concepts",
    "/api/knowledge/concepts/{ref}",
    "/api/knowledge/principles",
}


def test_knowledge_routes_in_openapi_and_read_only(client):
    spec = client.get("/openapi.json").json()
    assert KNOWLEDGE_PATHS <= set(spec["paths"])
    for path in KNOWLEDGE_PATHS:
        assert set(spec["paths"][path].keys()) == {"get"}


def test_knowledge_routes_work_without_db(client):
    assert client.get("/api/knowledge/concepts").status_code == 200      # real docs/knowledge (may be empty)


UNDERSTANDING_PATHS = {
    "/api/workbench/run-overview",
    "/api/workbench/agent-runs/{run_id}/reasoning",
}


def test_run_overview_and_reasoning_routes_in_openapi_and_read_only(client):
    spec = client.get("/openapi.json").json()
    assert UNDERSTANDING_PATHS <= set(spec["paths"])
    for path in UNDERSTANDING_PATHS:
        assert set(spec["paths"][path].keys()) == {"get"}


def test_run_overview_and_reasoning_without_db_are_503(client):
    assert client.get("/api/workbench/run-overview?session=2026-10-27").status_code == 503
    assert client.get(
        "/api/workbench/agent-runs/6f1d1c1e-6666-4444-8888-000000000001/reasoning").status_code == 503


# --- v2.7 growth loop (spec §12): every new path and its methods, error bodies and the 503 text ---

GROWTH_ROUTES = {
    "/api/workbench/review-cards": {"get"},
    "/api/workbench/review-cards/{card_id}": {"get"},
    "/api/workbench/review-cards/{card_id}/verdict": {"post"},
    "/api/workbench/review-cards/{card_id}/redraft": {"post"},
    "/api/workbench/my-knowledge": {"get", "post"},
    "/api/workbench/my-knowledge/{knowledge_id}": {"get"},
    "/api/workbench/my-knowledge/{knowledge_id}/versions": {"post"},
    "/api/workbench/my-knowledge/{knowledge_id}/status": {"post"},
    "/api/workbench/my-knowledge/{knowledge_id}/performance": {"get"},
    "/api/workbench/my-knowledge-drafts": {"get"},
    "/api/workbench/my-knowledge-drafts/{draft_id}/verdict": {"post"},
    "/api/workbench/discussions": {"get", "post"},
    "/api/workbench/discussions/{discussion_id}": {"get"},
    "/api/workbench/discussions/{discussion_id}/turns": {"post"},
}


def test_growth_routes_in_openapi(client):
    spec = client.get("/openapi.json").json()
    for path, methods in GROWTH_ROUTES.items():
        assert path in spec["paths"], path
        assert set(spec["paths"][path].keys()) == methods, path


def test_growth_turn_route_answers_202(client):
    spec = client.get("/openapi.json").json()
    assert "202" in spec["paths"]["/api/workbench/discussions/{discussion_id}/turns"]["post"]["responses"]


GROWTH_WITHOUT_DB = [
    ("get", "/api/workbench/review-cards"),
    ("get", "/api/workbench/review-cards/1"),
    ("post", "/api/workbench/review-cards/1/verdict"),
    ("post", "/api/workbench/review-cards/1/redraft"),
    ("get", "/api/workbench/my-knowledge"),
    ("post", "/api/workbench/my-knowledge"),
    ("get", "/api/workbench/my-knowledge/1"),
    ("post", "/api/workbench/my-knowledge/1/versions"),
    ("post", "/api/workbench/my-knowledge/1/status"),
    ("get", "/api/workbench/my-knowledge/1/performance"),
    ("get", "/api/workbench/my-knowledge-drafts"),
    ("post", "/api/workbench/my-knowledge-drafts/1/verdict"),
    ("get", "/api/workbench/discussions?card_id=1"),
    ("get", "/api/workbench/discussions/1"),
]

BODIES = {
    "/api/workbench/review-cards/1/verdict": {"request_id": "6f1d1c1e-6666-4444-8888-000000000001", "note": "",
                                             "lessons": []},
    "/api/workbench/my-knowledge": {"request_id": "6f1d1c1e-6666-4444-8888-000000000001", "title": "t", "body": "b",
                                    "scope": "s", "counter_examples": "", "concepts": [], "tickers": ["NVDA"]},
    "/api/workbench/my-knowledge/1/versions": {"request_id": "6f1d1c1e-6666-4444-8888-000000000001",
                                               "base_version": 1, "title": "t", "body": "b", "scope": "s",
                                               "tickers": ["NVDA"]},
    "/api/workbench/my-knowledge/1/status": {"request_id": "6f1d1c1e-6666-4444-8888-000000000001",
                                             "status": "archived", "reason": ""},
    "/api/workbench/my-knowledge-drafts/1/verdict": {"request_id": "6f1d1c1e-6666-4444-8888-000000000001",
                                                     "action": "dismiss"},
}


def test_growth_routes_without_db_are_503(client):
    for method, path in GROWTH_WITHOUT_DB:
        resp = getattr(client, method)(path, **({"json": BODIES[path]} if method == "post" and path in BODIES else {}))
        assert resp.status_code == 503, path
        assert "数据库未配置" in resp.json()["error"], path
    post = client.post("/api/workbench/discussions",
                       json={"request_id": "6f1d1c1e-6666-4444-8888-000000000001",
                             "context": {"kind": "card", "card_id": 1}})
    assert post.status_code == 503
    turn = client.post("/api/workbench/discussions/1/turns",
                       json={"request_id": "6f1d1c1e-6666-4444-8888-000000000001", "body": "问"})
    assert turn.status_code == 503


def test_growth_response_shapes_stay_stable(client, monkeypatch):
    from investment_assistant.review import discussion
    from investment_assistant.review.knowledge import my_knowledge
    from investment_assistant.review import retro_cards
    monkeypatch.setattr(retro_cards, "list_view", lambda **kw: {"cards": [], "counts": {}})
    monkeypatch.setattr(retro_cards, "card_view", lambda card_id, *, now: {
        "card": {}, "facts": {}, "status": "to_review", "drafts": [], "lesson_drafts": [], "verdict": None,
        "discipline_now": {}, "discussions": []})
    monkeypatch.setattr(my_knowledge, "list_view", lambda **kw: {"items": []})
    monkeypatch.setattr(my_knowledge, "item_view", lambda knowledge_id, *, version=None: {
        "item": {}, "versions": [], "status_history": [], "citations": [], "current": {}})
    monkeypatch.setattr(my_knowledge, "drafts_view", lambda *, open_only: {"drafts": []})
    monkeypatch.setattr(discussion, "list_for", lambda **kw: [])
    monkeypatch.setattr(discussion, "view", lambda discussion_id: {
        "id": discussion_id, "context_kind": "plan", "ticker": "NVDA", "signal_date": "2026-10-20", "plan_id": "p",
        "card_id": None, "created_at": "x", "context": {}, "turns": []})
    assert set(client.get("/api/workbench/review-cards").json()) == {"cards", "counts"}
    assert set(client.get("/api/workbench/review-cards/1").json()) == {
        "card", "facts", "status", "drafts", "lesson_drafts", "verdict", "discipline_now", "discussions"}
    assert set(client.get("/api/workbench/my-knowledge").json()) == {"items"}
    assert set(client.get("/api/workbench/my-knowledge/1").json()) == {
        "item", "versions", "status_history", "citations", "current"}
    assert set(client.get("/api/workbench/my-knowledge-drafts").json()) == {"drafts"}
    assert client.get("/api/workbench/discussions?card_id=1").json() == {"discussions": []}
    assert set(client.get("/api/workbench/discussions/5").json()) == {
        "id", "context_kind", "ticker", "signal_date", "plan_id", "card_id", "created_at", "context", "turns"}


# --- v2.8 daily workflow (spec §11.1): every new path with its methods, status codes, field names and the 503 text ---

V28_ROUTES = {
    "/api/runs/{run_id}/detail": {"get"},
    "/api/jobs/daily/resume": {"post"},
    "/api/jobs/{name}/run": {"post"},
    "/api/jobs/reports": {"get"},
    "/api/workbench/run": {"post"},
    "/api/workbench/agent-runs/rerun": {"post"},
    "/api/settings/agent-connection": {"get", "delete"},
    "/api/settings/agent-connection/login": {"post"},
    "/api/settings/agent-connection/cancel": {"post"},
    "/api/settings/agent-connection/models": {"get"},
    "/api/settings/agent-connection/model": {"put"},
}
RUN_ID = "daily-20261030033000-1a2b3c4d"
REQUEST_ID = "6f1d1c1e-6666-4444-8888-000000000001"


def test_v28_routes_in_openapi(client):
    spec = client.get("/openapi.json").json()
    for path, methods in V28_ROUTES.items():
        assert path in spec["paths"], path
        assert methods <= set(spec["paths"][path]), path
    assert "get" in spec["paths"]["/api/runs/{run_id}"]                      # the in-memory run status stays
    assert "202" in spec["paths"]["/api/jobs/daily/resume"]["post"]["responses"]
    assert set(spec["paths"]["/api/runs/{run_id}/detail"]) == {"get"}         # read-only


V28_WITHOUT_DB = [
    ("get", f"/api/runs/{RUN_ID}/detail", None),
    ("post", "/api/jobs/daily/resume", {"run_id": RUN_ID, "node": "collect", "request_id": REQUEST_ID}),
    ("post", "/api/workbench/agent-runs/rerun", {"ticker": "NVDA", "signal_date": "2026-10-22", "mode": "propose",
                                                  "request_id": REQUEST_ID, "origin": "run_detail"}),
    ("get", "/api/settings/agent-connection", None),
    ("post", "/api/settings/agent-connection/login", {}),
    ("post", "/api/settings/agent-connection/cancel", {}),
    ("delete", "/api/settings/agent-connection", None),
    ("get", "/api/settings/agent-connection/models", None),
    ("put", "/api/settings/agent-connection/model", {"slug": "gpt-x"}),
]


def test_v28_routes_without_db_are_503(client):
    for method, path, body in V28_WITHOUT_DB:
        resp = getattr(client, method)(path, **({} if body is None else {"json": body}))
        assert resp.status_code == 503, path
        assert "数据库未配置" in resp.json()["error"], path


def test_v28_request_errors_use_the_error_envelope(client):
    cases = [("get", "/api/runs/bad/detail", None, 400),
             ("post", "/api/jobs/daily/resume", {}, 400),
             ("post", "/api/jobs/sec_backfill/run", {}, 400),
             ("post", "/api/jobs/workbench/run", {}, 404),
             ("post", "/api/workbench/agent-runs/rerun", {}, 400),
             ("put", "/api/settings/agent-connection/model", {}, 400)]
    for method, path, body, status in cases:
        resp = getattr(client, method)(path, **({} if body is None else {"json": body}))
        assert resp.status_code == status and set(resp.json()) == {"error"} and resp.json()["error"], path


def test_v28_response_shapes(client, monkeypatch):
    from investment_assistant.services import jobs
    from investment_assistant.tasks import daily
    from investment_assistant.core import runner
    import investment_assistant.views.runs.detail as run_detail
    import investment_assistant.core.tx as service
    import investment_assistant.workflow.graph as workflow
    from investment_assistant.core.llm import connection as agent_connection
    from investment_assistant.api.routes import settings as settings_routes

    detail_keys = {"run_id", "task", "kind", "graph_version", "stages_version", "session", "cutoff_at", "trigger",
                   "status", "headline", "executions", "resume", "nodes", "raw_summary"}
    monkeypatch.setattr(run_detail, "detail", lambda run_id, *, now, checkpointer_factory=None: {k: None for k in detail_keys})
    assert set(client.get(f"/api/runs/{RUN_ID}/detail").json()) == detail_keys

    monkeypatch.setattr(workflow, "resume_check", lambda run_id, node, *, now, checkpointer_factory=None: {"attempt": 2})
    from investment_assistant.core import locks
    monkeypatch.setattr(locks, "is_running", lambda: False)
    monkeypatch.setattr(runner, "submit", lambda kind, fn: f"{kind}-20261030050102-9f8e7d6c")
    resp = client.post("/api/jobs/daily/resume", json={"run_id": RUN_ID, "node": "collect"})
    assert resp.status_code == 202 and set(resp.json()) == {"run_id", "node", "status", "attempt", "runner_id"}
    monkeypatch.setattr(jobs, "load_config", lambda: object())
    for path in ("/api/jobs/daily/run", "/api/workbench/run"):
        assert set(client.post(path, json={}).json()) == {"run_id", "daily_run_id", "status"}, path
    monkeypatch.setattr(analysis_agent_runner, "rerun_agent", lambda *a, **k: {"reset_id": 1, "run_id": None, "replayed": False})
    rerun = client.post("/api/workbench/agent-runs/rerun", json={"ticker": "NVDA", "signal_date": "2026-10-22",
                                                                  "mode": "interpret", "request_id": REQUEST_ID})
    assert set(rerun.json()) == {"reset_id", "run_id", "replayed"}

    view = {"status": "disconnected", "email_masked": None, "scopes_ok": False, "access_expires_at": None,
            "refresh_expires_at": None, "last_refresh_at": None, "last_error": None, "model": None,
            "model_display_name": None, "provider_setting": "chatgpt", "pending": None}
    monkeypatch.setattr(agent_connection, "status_view", lambda **kw: view)
    assert set(client.get("/api/settings/agent-connection").json()) == set(view)
    monkeypatch.setattr(agent_connection, "start_login", lambda *, now: {"authorize_url": "u", "expires_at": "t"})
    assert set(client.post("/api/settings/agent-connection/login", json={}).json()) == {"authorize_url", "expires_at"}
    monkeypatch.setattr(agent_connection, "cancel_login", lambda: {"cancelled": False})
    assert client.post("/api/settings/agent-connection/cancel", json={}).json() == {"cancelled": False}
    monkeypatch.setattr(agent_connection, "models", lambda: [{"slug": "a", "display_name": "A"}])
    assert client.get("/api/settings/agent-connection/models").json() == {"models": [{"slug": "a", "display_name": "A"}]}
    monkeypatch.setattr(agent_connection, "select_model", lambda slug, *, now: {"model": slug, "model_display_name": "A"})
    assert set(client.put("/api/settings/agent-connection/model", json={"slug": "a"}).json()) == {"model", "model_display_name"}
    view["status"] = "connected"
    monkeypatch.setattr(agent_connection, "disconnect", lambda *, now: {"revoked": True})
    assert client.delete("/api/settings/agent-connection").json() == {"revoked": True}
    assert settings_routes.agent_connection is agent_connection


def test_job_reports_task_filter_accepts_the_new_names(client, monkeypatch):
    from investment_assistant.services import jobs

    seen = []
    monkeypatch.setattr(jobs, "job_reports", lambda task=None, limit=50: seen.append(task) or {"reports": [], "degraded": False})
    for name in ("daily", "sec_backfill", "workbench", "workbench_review", "filings"):
        assert client.get(f"/api/jobs/reports?task={name}").status_code == 200
    assert seen == ["daily", "sec_backfill", "workbench", "workbench_review", "filings"]


V29_ROUTES = {
    "/api/workbench/plan-table": {"get"},
    "/api/workbench/holding-status": {"get"},
    "/api/workbench/instruments/{ticker}/plan-history": {"get"},
    "/api/workbench/trades/linkable-plans": {"get"},
    "/api/workbench/plan-notes": {"get", "post"},
    "/api/workbench/decisions": {"post"},
}
V29_PLAN = "6f1d1c1e-6666-4444-8888-000000000009"


def test_v29_routes_in_openapi(client):
    spec = client.get("/openapi.json").json()
    for path, methods in V29_ROUTES.items():
        assert path in spec["paths"], path
        assert methods <= set(spec["paths"][path]), path
    assert set(spec["paths"]["/api/workbench/plan-table"]) == {"get"}              # read-only
    assert set(spec["paths"]["/api/workbench/trades/linkable-plans"]) == {"get"}


V29_WITHOUT_DB = [
    ("get", "/api/workbench/plan-table", None),
    ("get", "/api/workbench/plan-table?session=2026-10-30&include_extended=true", None),
    ("get", "/api/workbench/holding-status?session=2026-10-30", None),
    ("get", "/api/workbench/instruments/MU/plan-history?include_reviews=true&include_baselines=true", None),
    ("get", "/api/workbench/trades/linkable-plans?ticker=MU&executed_at=2026-10-30T14:00:00Z", None),
    ("get", "/api/workbench/plan-notes?ticker=MU&chain_signal_date=2026-10-29", None),
    ("post", "/api/workbench/plan-notes", {"request_id": REQUEST_ID, "ticker": "MU", "chain_signal_date": "2026-10-29",
                                           "body": "等回踩"}),
]


def test_v29_routes_without_db_are_503(client):
    for method, path, body in V29_WITHOUT_DB:
        resp = getattr(client, method)(path, **({} if body is None else {"json": body}))
        assert resp.status_code == 503, path
        assert set(resp.json()) == {"error"} and resp.json()["error"], path


def test_v29_request_errors_use_the_error_envelope(client):
    cases = [("get", "/api/workbench/plan-table?session=not-a-date", None, 400),
             ("get", "/api/workbench/holding-status?session=2026-13-01", None, 400),
             ("get", "/api/workbench/trades/linkable-plans", None, 400),
             ("get", "/api/workbench/trades/linkable-plans?ticker=MU&executed_at=yesterday", None, 400),
             ("get", "/api/workbench/trades/linkable-plans?ticker=MU&executed_at=2026-10-30T14:00:00", None, 400),
             ("get", "/api/workbench/plan-notes", None, 400),
             ("get", "/api/workbench/plan-notes?ticker=MU&plan_id=not-a-uuid", None, 400),
             ("post", "/api/workbench/decisions", {}, 410)]
    for method, path, body, status in cases:
        resp = getattr(client, method)(path, **({} if body is None else {"json": body}))
        assert resp.status_code == status and set(resp.json()) == {"error"} and resp.json()["error"], path


def test_decisions_post_410(client):
    """The decision endpoint is gone for good (D24): 410 with the code and the sentence, whatever the body."""
    for body in ({}, {"request_id": REQUEST_ID, "ticker": "MU", "decision": "accept"}):
        resp = client.post("/api/workbench/decisions", json=body)
        assert resp.status_code == 410
        assert resp.json() == {"error": "decisions_retired: v2.9 起不再记录决定，采纳由成交推导；可在计划上写备注"}


def test_v29_response_shapes(client, monkeypatch):
    import investment_assistant.views.plan_table as plan_views
    import investment_assistant.core.tx as service

    table = {"session": "2026-10-30", "groups": {"entry": [], "holding": [], "ended": []}, "other_units": [],
             "counts": {"entry": 0, "holding": 0, "ended": 0}, "errors": {}}
    seen = {}

    def table_view(*, now, session, include_extended):
        seen.update(session=session, include_extended=include_extended)
        return table
    monkeypatch.setattr(plan_views, "table_view", table_view)
    body = client.get("/api/workbench/plan-table?session=2026-10-29&include_extended=true").json()
    assert set(body) == {"session", "groups", "other_units", "counts", "errors"}
    assert set(body["groups"]) == {"entry", "holding", "ended"}
    assert seen == {"session": __import__("datetime").date(2026, 10, 29), "include_extended": True}
    client.get("/api/workbench/plan-table")
    assert seen == {"session": None, "include_extended": False}                         # both default

    monkeypatch.setattr(plan_views, "history_view", lambda ticker, *, now, include_reviews, include_baselines: {
        "ticker": ticker, "rows": [], "chains": [], "flags": (include_reviews, include_baselines)})
    body = client.get("/api/workbench/instruments/MU/plan-history?include_reviews=true").json()
    assert body["flags"] == [True, False] and set(body) >= {"ticker", "rows", "chains"}

    monkeypatch.setattr(plan_views, "holding_view", lambda *, now, session: {"session": "2026-10-30", "rows": []})
    assert set(client.get("/api/workbench/holding-status").json()) == {"session", "rows"}

    monkeypatch.setattr(plan_views, "linkable_view", lambda ticker, executed_at: {
        "ticker": ticker, "plans": [], "default_plan_id": None, "at": executed_at.isoformat()})
    body = client.get("/api/workbench/trades/linkable-plans?ticker=mu&executed_at=2026-10-30T14:00:00Z").json()
    assert set(body) >= {"plans", "default_plan_id"} and body["at"] == "2026-10-30T14:00:00+00:00"

    monkeypatch.setattr(execution_trades, "add_plan_note", lambda payload, *, now: {"status": "created", "record": {"id": 3}})
    assert client.post("/api/workbench/plan-notes", json={"body": "x"}).json() == {
        "status": "created", "record": {"id": 3}}
    monkeypatch.setattr(plan_views, "notes_view", lambda *, ticker, chain_signal_date, plan_id: {"notes": []})
    assert client.get("/api/workbench/plan-notes?ticker=mu").json() == {"notes": []}


def test_v29_error_codes_reach_the_page(client, monkeypatch):
    """409 / 400 / 503 bodies carry `code: sentence` the page splits on (spec §6.1, §6.4, §21.4)."""
    from datetime import date

    import investment_assistant.workflow.nodes.review as plan_review
    import investment_assistant.views.plan_table as plan_views
    import investment_assistant.core.tx as service
    from investment_assistant.execution import ledger

    def raises(exc):
        def fn(*args, **kwargs):
            raise exc
        return fn
    monkeypatch.setattr(execution_trades, "add_trade", raises(ledger.Conflict("plan_stale: 成交时这条链已是第 3 版，请关联第 3 版")))
    resp = client.post("/api/workbench/trades", json={})
    assert resp.status_code == 409 and resp.json() == {"error": "plan_stale: 成交时这条链已是第 3 版，请关联第 3 版"}
    monkeypatch.setattr(execution_trades, "add_trade", raises(ledger.LedgerError(ledger.DECISION_LINK_RETIRED)))
    resp = client.post("/api/workbench/trades", json={})
    assert resp.status_code == 400
    assert resp.json() == {"error": "decision_link_retired: v2.9 起成交不再关联决定，请改为关联计划"}
    monkeypatch.setattr(execution_trades, "add_trade", raises(service.Unavailable("migration_029_missing")))
    assert client.post("/api/workbench/trades", json={}).json() == {"error": "migration_029_missing"}
    monkeypatch.setattr(plan_views, "table_view", raises(plan_views.NotReady()))
    resp = client.get("/api/workbench/plan-table")
    assert resp.status_code == 503 and resp.json() == {"error": "migration_029_missing"}
    # a manual run on a ticker that already has an active chain: the sentence is filled from the exception
    monkeypatch.setattr(analysis_agent_runner, "run_agent", raises(analysis_agent_runner.AgentConflict(
        "active_chain", chain_signal_date=date(2026, 10, 29), version_no=2)))
    resp = client.post("/api/workbench/agent-runs", json={"ticker": "mu", "signal_date": "2026-10-30"})
    assert resp.status_code == 409
    assert resp.json() == {"error": "active_chain: MU 已有进行中的计划链（10-29 第 2 版），这次触发已并入"}
    monkeypatch.setattr(analysis_agent_runner, "rerun_agent", raises(analysis_agent_runner.AgentConflict(
        "active_chain", chain_signal_date=date(2026, 10, 29), version_no=2)))
    resp = client.post("/api/workbench/agent-runs/rerun", json={"ticker": "mu", "signal_date": "2026-10-30",
                                                                 "mode": "propose", "request_id": REQUEST_ID})
    assert resp.json() == {"error": "active_chain: MU 已有进行中的计划链（10-29 第 2 版），这次触发已并入"}
    # the other conflict keeps its plain reason
    monkeypatch.setattr(analysis_agent_runner, "run_agent", raises(analysis_agent_runner.AgentConflict("already_completed")))
    assert client.post("/api/workbench/agent-runs", json={"ticker": "mu", "signal_date": "2026-10-30"}).json() == {
        "error": "already_completed"}
    # a recheck of an exiting chain
    monkeypatch.setattr(plan_review, "recheck_check", raises(plan_review.RecheckConflict("chain_exiting")))
    resp = client.post("/api/workbench/chains/MU/2026-10-29/recheck")
    assert resp.status_code == 409 and resp.json() == {"error": "chain_exiting: 这条链已发出退出指令，不再复查"}
    monkeypatch.setattr(plan_review, "recheck_check", raises(plan_review.RecheckConflict("chain_not_active")))
    assert client.post("/api/workbench/chains/MU/2026-10-29/recheck").json() == {"error": "chain_not_active"}
