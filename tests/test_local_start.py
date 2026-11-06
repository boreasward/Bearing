import sys
from types import SimpleNamespace

import pytest

from scripts import start_local


def _run_in(tmp_path, monkeypatch, env_text, run_server):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    if env_text is not None:
        (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    monkeypatch.setattr(start_local, "__file__", str(scripts_dir / "start_local.py"))
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "investment_assistant.api.server", SimpleNamespace(main=run_server))
    start_local.main()


def test_local_start_binds_loopback_only_and_runs_the_server(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_HOST", "0.0.0.0")
    seen = []

    def run_server():
        import os

        seen.append(os.environ["HERMES_DASHBOARD_HOST"])

    _run_in(tmp_path, monkeypatch, "", run_server)
    assert seen == ["127.0.0.1"]


def test_local_start_loads_the_env_file_before_importing_the_server(tmp_path, monkeypatch):
    monkeypatch.setenv("START_LOCAL_PROBE", "unset-by-test")
    monkeypatch.delenv("START_LOCAL_PROBE")
    seen = []

    def run_server():
        import os

        seen.append(os.environ.get("START_LOCAL_PROBE"))

    _run_in(tmp_path, monkeypatch, "START_LOCAL_PROBE=from-file\n", run_server)
    assert seen == ["from-file"]


def test_local_start_requires_the_env_file(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as raised:
        _run_in(tmp_path, monkeypatch, None, lambda: pytest.fail("the server must not start without .env"))
    assert ".env" in str(raised.value)
