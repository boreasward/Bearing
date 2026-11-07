import warnings

from fastapi.testclient import TestClient

from investment_assistant.api import auth
from investment_assistant.api import static_files as static_files_mod
from investment_assistant.api.app import create_app


def test_unknown_api_path_returns_legacy_404_shape(client):
    resp = client.get("/api/does-not-exist-xyz")
    assert resp.status_code == 404
    assert resp.json() == {"error": "not found"}


def test_api_routers_mount_before_the_fallback_routes(client):
    """所有 /api/... 路由必须在 create_app 的两个兜底路由之前（否则被拦成 404）。
    FastAPI 0.141 起 include_router 是惰性的 IncludedRouter 标记，标记的先后即注册的先后。"""
    routes = client.app.router.routes
    fallbacks = [i for i, route in enumerate(routes) if getattr(route, "path", "") == "/{full_path:path}"]
    assert len(fallbacks) == 2, "create_app 必须保留 GET 与其他方法两个兜底"
    included = [i for i, route in enumerate(routes) if hasattr(route, "original_router")]
    assert included, "页面接口的 router 必须在 app.routes 里"
    assert all(i < fallbacks[0] for i in included)
    for i in included:  # 每个 include 进来的页面 router 只含 /api/... 路径
        assert all(getattr(sub, "path", "").startswith("/api/") for sub in routes[i].original_router.routes)


def test_page_api_paths_exist(client):
    """契约表的 20 条路径全部注册（openapi 是公开面；不含两个兑底）。"""
    schema = client.app.openapi()
    expected = {
        "/api/today",
        "/api/briefs",
        "/api/briefs/latest",
        "/api/briefs/{session}",
        "/api/plans",
        "/api/plans/{plan_id}",
        "/api/plans/{plan_id}/stop-order",
        "/api/plans/{plan_id}/raise-stop",
        "/api/plans/{plan_id}/cancel",
        "/api/trades",
        "/api/cash-flows",
        "/api/reconcile",
        "/api/voids",
        "/api/account",
        "/api/chats",
        "/api/chats/{chat_id}",
        "/api/chats/{chat_id}/messages",
        "/api/chats/{chat_id}/cancel",
        "/api/settings",
        "/api/settings/options",
    }
    assert expected <= set(schema["paths"])


def test_non_get_unknown_path_returns_404(client):
    for method in ("post", "put", "patch", "delete"):
        resp = getattr(client, method)("/api/does-not-exist-xyz")
        assert resp.status_code == 404, method
        assert resp.json() == {"error": "not found"}


def test_no_password_configured_allows_anonymous(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_PASSWORD", "")
    resp = client.get("/api/does-not-exist-xyz")
    assert resp.status_code == 404  # 通过认证，走到 404


def test_password_configured_rejects_anonymous_with_basic_challenge(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_PASSWORD", "secret")
    resp = client.get("/api/does-not-exist-xyz")
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}
    assert resp.headers["WWW-Authenticate"] == 'Basic realm="Hermes Investment Assistant"'


def test_password_configured_accepts_valid_basic(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_PASSWORD", "secret")
    resp = client.get("/api/does-not-exist-xyz", auth=(auth.AUTH_USER, "secret"))
    assert resp.status_code == 404


def test_root_without_frontend_build_is_503(client, monkeypatch, tmp_path):
    monkeypatch.setattr(static_files_mod, "STATIC_DIR", tmp_path / "dist")
    resp = client.get("/")
    assert resp.status_code == 503
    assert resp.json()["error"] == "frontend_not_built"


def test_value_error_returns_400_and_validation_error_returns_400():
    app = create_app()

    @app.get("/boom")
    def _boom():
        raise ValueError("bad date")

    @app.get("/typed/{count}")
    def _typed(count: int):
        return {"count": count}

    catch_all = [r for r in app.router.routes if getattr(r, "path", "") == "/{full_path:path}"]
    app.router.routes[:] = [
        r for r in app.router.routes if r not in catch_all
    ] + catch_all  # handlers before the catch-all
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.status_code == 400
    assert resp.json() == {"error": "bad date"}
    resp = client.get("/typed/not-a-number")
    assert resp.status_code == 400
    assert resp.json()["error"].startswith("invalid request:")


def test_openapi_schema_builds_clean_without_catchall_pollution(client):
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # 重复 operation ID 会以 UserWarning 形式出现
        schema = client.app.openapi()

    assert "/{full_path}" not in set(schema["paths"])  # catch-all 不进公开 API 面
