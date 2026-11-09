"""The migration031 fixture must reject every unapproved URL before connecting."""

from inspect import unwrap

import pytest
from test_migration_031 import migrated_db


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://test@127.0.0.1:55433/workbench_test",
        "postgresql://test@127.0.0.1:55433/workbench_v321_test",
    ],
)
def test_migrated_db_accepts_only_exact_approved_urls_without_opening_database(monkeypatch, url):
    import psycopg

    monkeypatch.setenv("INVESTMENT_ASSISTANT_TEST_DATABASE_URL", url)
    calls = []

    def stop_before_connection(actual_url, **kwargs):
        calls.append((actual_url, kwargs))
        raise RuntimeError("connection sentinel")

    monkeypatch.setattr(psycopg, "connect", stop_before_connection)
    with pytest.raises(RuntimeError, match="connection sentinel"):
        next(unwrap(migrated_db)())
    assert calls == [(url, {"connect_timeout": 3})]


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://test@localhost:55433/workbench_v321_test",
        "postgresql://test@127.0.0.2:55433/workbench_v321_test",
        "postgresql://test@127.0.0.1:5432/workbench_v321_test",
        "postgresql://other@127.0.0.1:55433/workbench_v321_test",
        "postgresql://test@127.0.0.1:55433/production",
        "postgresql://test@127.0.0.1:55433/workbench_v321_test?sslmode=disable",
        "postgresql://test@127.0.0.1:55433/workbench_v321_test/",
        "postgresql://test@127.0.0.1:55433/workbench_test?application_name=test",
    ],
)
def test_migrated_db_rejects_changed_host_port_user_database_or_query_before_connect(monkeypatch, url):
    import psycopg

    monkeypatch.setenv("INVESTMENT_ASSISTANT_TEST_DATABASE_URL", url)
    calls = []
    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: calls.append((args, kwargs)))
    with pytest.raises(pytest.fail.Exception, match="only the disposable test URL"):
        next(unwrap(migrated_db)())
    assert calls == []


def test_migrated_db_without_explicit_url_skips_before_connect(monkeypatch):
    import psycopg

    monkeypatch.delenv("INVESTMENT_ASSISTANT_TEST_DATABASE_URL", raising=False)
    calls = []
    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: calls.append((args, kwargs)))
    with pytest.raises(pytest.skip.Exception, match="explicit disposable test database URL required"):
        next(unwrap(migrated_db)())
    assert calls == []
