import time

from fastapi.testclient import TestClient

from investment_assistant.api.app import create_app
from investment_assistant.api import auth
from investment_assistant.tasks import scheduler


def test_unknown_api_path_returns_legacy_404_shape(client):
    resp = client.get("/api/does-not-exist-xyz")
    assert resp.status_code == 404
    assert resp.json() == {"error": "not found"}


def test_no_password_configured_allows_anonymous(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_PASSWORD", "")
    resp = client.get("/api/does-not-exist-xyz")
    assert resp.status_code == 404  # 通过认证，走到 404


def test_password_configured_rejects_anonymous_with_basic_challenge(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_PASSWORD", "secret")
    resp = client.get("/api/does-not-exist-xyz")
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == 'Basic realm="Hermes Investment Assistant"'


def test_password_configured_accepts_valid_basic(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_PASSWORD", "secret")
    resp = client.get("/api/does-not-exist-xyz", auth=(auth.AUTH_USER, "secret"))
    assert resp.status_code == 404


def test_sse_events_route_registered(client):
    paths = {getattr(r, "path", None) for r in client.app.routes}
    assert "/api/events" in paths


def test_value_error_from_handler_returns_400(client, monkeypatch):
    import investment_assistant.services.watchlist as _watchlist
    monkeypatch.setattr(_watchlist, "add_watchlist_item",
                        lambda payload: (_ for _ in ()).throw(ValueError("bad date")))
    resp = client.post("/api/watchlist", json={"ticker": "BAD"})
    assert resp.status_code == 400
    assert resp.json() == {"error": "bad date"}


def test_event_stream_frames_events_and_survives_keepalive(monkeypatch):
    import queue as _queue

    from investment_assistant.api import app as app_mod
    from investment_assistant.core import runner

    unsubscribed = []
    monkeypatch.setattr(runner, "unsubscribe", lambda q: unsubscribed.append(q))

    class FakeQueue:
        def __init__(self):
            self.calls = 0

        def get(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                return {"run_id": "r1", "status": "done", "kind": "metrics"}
            if self.calls == 2:
                raise _queue.Empty
            return {"run_id": "r2", "status": "done", "kind": "metrics"}

    q = FakeQueue()
    gen = app_mod.event_stream(q)

    first = next(gen)
    assert first.startswith("data: ")
    assert '"run_id": "r1"' in first

    assert next(gen) == ": keep-alive\n\n"

    third = next(gen)  # 心跳后循环必须继续——持久连接契约
    assert '"run_id": "r2"' in third

    gen.close()
    assert unsubscribed == [q]


def test_get_unknown_run_returns_404(client):
    resp = client.get("/api/runs/nope-123")
    assert resp.status_code == 404
    assert resp.json() == {"error": "unknown run"}


def test_jobs_run_requires_run_suffix_contract(client, monkeypatch):
    from investment_assistant.services import jobs as jobs_svc
    monkeypatch.setattr(jobs_svc, "trigger_job", lambda name, payload=None: {"run_id": f"{name}-1", "status": "pending"})
    resp = client.post("/api/jobs/metrics/run", json={})
    assert resp.status_code == 200
    assert resp.json()["run_id"] == "metrics-1"


def test_openapi_schema_builds_clean_without_catchall_pollution(client):
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # 重复 operation ID 会以 UserWarning 形式出现
        schema = client.app.openapi()

    paths = set(schema["paths"])
    assert "/{full_path}" not in paths          # catch-all 不进公开 API 面
    assert "/api/events" in paths               # 真实端点保留
    assert "/api/status" in paths


def test_lifespan_starts_and_stops_scheduler_thread(monkeypatch):
    started = {}

    def _fake_run_forever(*, stop_event, database_url, config, tick_seconds):
        started["url"] = database_url
        started["tick"] = tick_seconds
        started["stop_event"] = stop_event
        stop_event.wait(5)

    monkeypatch.setenv("INVESTMENT_ASSISTANT_DATABASE_URL", "postgresql://fake")
    monkeypatch.setenv("SCHEDULER_TICK_SECONDS", "1")
    monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)  # 默认即启用
    monkeypatch.setattr(scheduler, "run_forever", _fake_run_forever)
    with TestClient(create_app()):
        for _ in range(200):  # 线程启动是异步的，最多等 1s
            if "stop_event" in started:
                break
            time.sleep(0.005)
        assert started["url"] == "postgresql://fake"
        assert started["tick"] == 1
        assert not started["stop_event"].is_set()
    assert started["stop_event"].is_set()  # lifespan 退出时优雅停止


def test_lifespan_scheduler_disabled_by_env(monkeypatch):
    called = []
    monkeypatch.setenv("INVESTMENT_ASSISTANT_DATABASE_URL", "postgresql://fake")
    monkeypatch.setenv("SCHEDULER_ENABLED", "0")
    monkeypatch.setattr(scheduler, "run_forever", lambda **kw: called.append(1))
    with TestClient(create_app()):
        pass
    assert not called


def test_lifespan_scheduler_skipped_without_database(monkeypatch):
    called = []
    monkeypatch.delenv("INVESTMENT_ASSISTANT_DATABASE_URL", raising=False)
    monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
    monkeypatch.setattr(scheduler, "run_forever", lambda **kw: called.append(1))
    with TestClient(create_app()):
        pass
    assert not called


def test_legacy_routes_removed(client):
    for path in ("/api/hermes/agents", "/api/market/signals", "/api/tickers/trends",
                 "/api/strategies/scores", "/api/research/event-study/kinds",
                 "/api/research/observation-cards/MU", "/api/research/us-references"):
        assert client.get(path).status_code == 404, path
