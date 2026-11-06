
import investment_assistant.api.static_files as static_files_mod


def test_static_route_serves_index_html(tmp_path, monkeypatch, client):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>Hermes Dashboard</body></html>", encoding="utf-8")
    monkeypatch.setattr(static_files_mod, "STATIC_DIR", dist)

    resp = client.get("/")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert b"Hermes Dashboard" in resp.content
