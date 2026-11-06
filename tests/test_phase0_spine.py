from __future__ import annotations

import importlib

import pytest


def test_dashboard_refuses_public_bind_without_optin(monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_HOST", "0.0.0.0")
    monkeypatch.delenv("HERMES_DASHBOARD_ALLOW_PUBLIC", raising=False)
    api_auth = importlib.reload(importlib.import_module("investment_assistant.api.auth"))
    with pytest.raises(SystemExit):
        api_auth.resolve_bind_host()


def test_dashboard_allows_localhost_bind(monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_HOST", "127.0.0.1")
    api_auth = importlib.reload(importlib.import_module("investment_assistant.api.auth"))
    assert api_auth.resolve_bind_host() == "127.0.0.1"
