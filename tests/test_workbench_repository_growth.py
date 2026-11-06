"""Repository helpers for the v2.7 growth-loop tables (SQL; throwaway schema via the workbench_db fixture)."""
import investment_assistant.core.dbutil as core_dbutil
import investment_assistant.core.locks as core_locks
import investment_assistant.core.schema as core_schema
import os
import uuid

import psycopg


def test_growth_ready_false_without_tables(workbench_db):
    conn = workbench_db
    assert core_schema.growth_ready(conn) is True
    assert len(core_dbutil.GROWTH_TABLES) == 11
    conn.execute('DROP TABLE knowledge_citations')
    assert core_schema.growth_ready(conn) is False
    conn.execute('SELECT 1')                                            # the transaction stays usable


def test_try_advisory_lock_mutual_exclusion(workbench_db):
    key = f'retro-test:{uuid.uuid4()}'
    url = os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']                 # the fixture skipped without it
    with psycopg.connect(url) as first, psycopg.connect(url) as second:
        assert core_locks.try_advisory_lock(first, key) is True
        assert core_locks.try_advisory_lock(second, key) is False              # held by the open transaction
        assert core_locks.try_advisory_lock(second, key + ':other') is True    # another key is free
        second.rollback()
        first.commit()                                                         # transaction scope: released here
        assert core_locks.try_advisory_lock(second, key) is True
        second.rollback()


def test_try_advisory_lock(workbench_db):
    conn = workbench_db
    assert core_locks.try_advisory_lock(conn, 'retro:2026-10-28') is True
    assert core_locks.try_advisory_lock(conn, 'retro:2026-10-28') is True      # re-entrant within the transaction
