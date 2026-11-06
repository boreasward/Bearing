import sys
import types
from unittest.mock import MagicMock

import importlib


def _install_fake_pool(monkeypatch):
    created = []
    fake_module = types.ModuleType("psycopg_pool")

    class FakePool:
        def __init__(self, conninfo, **kw):
            self.conninfo = conninfo
            self.kw = kw
            created.append(self)

        def connection(self):
            return MagicMock(name="conn-ctx")

    fake_module.ConnectionPool = FakePool
    monkeypatch.setitem(sys.modules, "psycopg_pool", fake_module)
    return created


def test_connect_returns_pool_connection_context(monkeypatch):
    created = _install_fake_pool(monkeypatch)
    db = importlib.reload(importlib.import_module("investment_assistant.db"))
    db.close_pools()  # clear cache to avoid cross-test pollution
    ctx = db.connect("postgres://u/db1")
    assert ctx is not None
    assert len(created) == 1


def test_pool_is_cached_per_url(monkeypatch):
    created = _install_fake_pool(monkeypatch)
    db = importlib.reload(importlib.import_module("investment_assistant.db"))
    db.close_pools()
    db.connect("postgres://u/db1")
    db.connect("postgres://u/db1")  # same url, reuse
    db.connect("postgres://u/db2")  # different url, new pool
    assert len(created) == 2
