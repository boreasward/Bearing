"""/api/settings*：读（未设为 null）、可选项、保存（不可用 400 且原因即 error）。切换生效部分见 test_api_chat。"""

import json

import investment_assistant.core.tx as core_tx


def _seed_pi_dir(tmp_path):
    pi_dir = tmp_path / "pi"
    pi_dir.mkdir()
    (pi_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "prov-a": {"models": [{"id": "model-a1"}, {"id": "model-a2"}]},
                    "prov-b": {"models": []},
                }
            }
        ),
        encoding="utf-8",
    )
    return pi_dir


def test_settings_default_to_null(client, v3_tx):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json() == {"runtime": None, "provider": None, "model": None}


def test_put_saves_and_reads_back(client, v3_tx, tmp_path, monkeypatch):
    from investment_assistant.api.routes import settings as settings_routes

    pi_dir = _seed_pi_dir(tmp_path)
    monkeypatch.setattr(settings_routes, "_pi_dir", lambda: pi_dir)
    put = client.put("/api/settings", json={"runtime": "fake", "provider": "prov-a", "model": "model-a1"})
    assert put.status_code == 200
    assert put.json() == {"runtime": "fake", "provider": "prov-a", "model": "model-a1"}
    got = client.get("/api/settings")
    assert got.json() == {"runtime": "fake", "provider": "prov-a", "model": "model-a1"}


def test_put_rejects_unavailable_and_keeps_store(client, v3_tx, tmp_path, monkeypatch):
    from investment_assistant.api.routes import settings as settings_routes

    monkeypatch.setattr(settings_routes, "_pi_dir", lambda: tmp_path / "nope")
    resp = client.put("/api/settings", json={"runtime": "nope", "provider": "prov-a", "model": "model-a1"})
    assert resp.status_code == 400
    assert "nope" in resp.json()["error"]  # error 即不可用原因
    assert client.get("/api/settings").json() == {"runtime": None, "provider": None, "model": None}


def test_put_missing_keys_is_400(client, v3_tx):
    resp = client.put("/api/settings", json={"runtime": "fake"})
    assert resp.status_code == 400
    assert client.get("/api/settings").json() == {"runtime": None, "provider": None, "model": None}


def test_options_lists_runtimes_providers_models(client, v3_tx, tmp_path, monkeypatch):
    from investment_assistant.api.routes import settings as settings_routes

    pi_dir = _seed_pi_dir(tmp_path)
    (pi_dir / "auth.json").write_text(json.dumps({"prov-c": {"apiKey": "x"}}), encoding="utf-8")
    monkeypatch.setattr(settings_routes, "_pi_dir", lambda: pi_dir)
    resp = client.get("/api/settings/options")
    assert resp.status_code == 200
    opts = resp.json()
    assert opts["runtimes"] == ["pi", "fake"]
    assert set(opts["providers"]) == {"prov-a", "prov-b", "prov-c"}
    assert opts["models"]["prov-a"] == ["model-a1", "model-a2"]


def test_pi_provider_needs_auth_when_unknown(client, v3_tx, tmp_path, monkeypatch):
    """专用目录里没有的提供方要过 pi auth check；pi 起不来也是「不可用」的一种，仍给原因不落库。"""
    from investment_assistant.api.routes import settings as settings_routes

    pi_dir = _seed_pi_dir(tmp_path)
    monkeypatch.setattr(settings_routes, "_pi_dir", lambda: pi_dir)
    monkeypatch.setenv("INVESTMENT_ASSISTANT_PI_BIN", str(tmp_path / "no-such-pi"))  # 确定性：不跑真 pi
    resp = client.put("/api/settings", json={"runtime": "pi", "provider": "prov-never", "model": "model-x"})
    assert resp.status_code == 400
    assert "pi auth check" in resp.json()["error"]
    with core_tx.read_only_tx() as conn:
        from investment_assistant.facts import plan_repo

        assert plan_repo.settings(conn) == {}  # 保存被拒时三项都不落库
