"""Repository functions of v2.8 (migration 028): daily_ready, stage rows with attempts, attempt resets, last push.

SQL tests run in a throwaway schema of the disposable test database; `daily_ready` is also checked against a
database that has not run 028."""
import investment_assistant.core.schema as core_schema
import os
import uuid
from pathlib import Path

import pytest

# Repository consumers use their owning layers below.


def _schema(before: str):
    url = os.environ.get('INVESTMENT_ASSISTANT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('explicit test database URL required; disposable schema only')
    import psycopg
    schema = f'rd_test_{uuid.uuid4().hex[:12]}'
    conn = psycopg.connect(url)
    conn.execute(f'CREATE SCHEMA {schema}')
    conn.execute(f'SET search_path TO {schema}')
    for path in sorted(Path('migrations').glob('0*.sql')):
        if path.name < before:
            conn.execute(path.read_text(encoding='utf-8'))
    conn.commit()
    return conn, schema


def _drop(conn, schema):
    conn.rollback()
    conn.execute(f'DROP SCHEMA {schema} CASCADE')
    conn.commit()
    conn.close()


@pytest.fixture
def db():
    conn, schema = _schema('029')            # everything through 028
    yield conn
    _drop(conn, schema)


@pytest.fixture
def db_027():
    conn, schema = _schema('028')            # a database that has not run 028
    yield conn
    _drop(conn, schema)


def test_daily_ready_false_without_028(db_027):
    assert core_schema.daily_ready(db_027) is False
    db_027.execute(Path('migrations/028_daily_workflow.sql').read_text(encoding='utf-8'))
    assert core_schema.daily_ready(db_027) is True


def test_daily_ready_needs_each_piece(db):
    assert core_schema.daily_ready(db) is True
    db.execute("DELETE FROM scheduled_jobs WHERE name = 'daily'")
    assert core_schema.daily_ready(db) is False
    db.execute("INSERT INTO scheduled_jobs (name, time_local) VALUES ('daily', '11:30')")
    db.execute('DROP TABLE agent_attempt_resets')
    assert core_schema.daily_ready(db) is False
