"""Feature history, replay and transaction semantics on the disposable 031 schema."""
import importlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from investment_assistant.core.idempotency import Conflict
from test_migration_031 import NOW, TEST_URL, apply, insert, migrated_db


@pytest.fixture
def features():
    return importlib.import_module('investment_assistant.core.features')


@pytest.fixture
def flags_db(migrated_db):
    apply(migrated_db)
    return migrated_db


def record(features, conn, **changes):
    return features.record_change(conn, **{'flag': 'backup_sources', 'enabled': False,
                                          'reason': '暂停备源', 'request_id': uuid.uuid4(), **changes})


def test_no_rows_means_enabled(features, flags_db):
    assert features.FLAGS == ('backup_sources', 'company_researcher', 'portfolio_manager', 'optimize_proposals')
    expected = {'backup_sources': True, 'company_researcher': True, 'portfolio_manager': True,
                'optimize_proposals': True}
    assert features.current(flags_db) == expected
    assert features.frozen(flags_db) == expected
    assert features.history(flags_db) == []


def test_latest_row_wins(features, flags_db):
    # id is the authority even when timestamps are out of order; other flags retain their own latest value.
    for flag, enabled, at in [('backup_sources', False, NOW), ('portfolio_manager', False, NOW),
                               ('backup_sources', True, NOW - timedelta(days=1))]:
        insert(flags_db, 'feature_flag_changes', {'flag': flag, 'enabled_after': enabled, 'reason': '测试',
                                                 'request_id': uuid.uuid4(), 'created_at': at})
    expected = {'backup_sources': True, 'company_researcher': True, 'portfolio_manager': False,
                'optimize_proposals': True}
    assert features.current(flags_db) == expected
    frozen = features.frozen(flags_db)
    record(features, flags_db, flag='company_researcher')
    assert frozen == expected  # Frozen state is an independent snapshot.
    assert features.current(flags_db)['company_researcher'] is False


def test_record_change_requires_reason_and_replays_request_id(features, flags_db):
    request_id = uuid.uuid4()
    first = record(features, flags_db, request_id=str(request_id), reason='  暂停备源  ')
    assert first['request_id'] == request_id
    assert (first['enabled_before'], first['enabled_after'], first['reason']) == (True, False, '暂停备源')
    assert first['created_at'] is not None
    second = record(features, flags_db, enabled=True, reason='恢复备源')
    assert (second['enabled_before'], second['enabled_after']) == (False, True)
    assert record(features, flags_db, request_id=request_id) == first
    assert features.current(flags_db)['backup_sources'] is True
    assert len(features.history(flags_db)) == 2
    for changes in ({'enabled': True}, {'reason': '不同理由'}, {'flag': 'portfolio_manager'}):
        with pytest.raises(Conflict, match='request_id'):
            record(features, flags_db, request_id=request_id, **changes)
    assert features.history(flags_db) == [first, second]


@pytest.mark.parametrize('reason', [None, '', '  \t\n', 1, '理' * 301])
def test_invalid_reason_cannot_write(features, flags_db, reason):
    with pytest.raises(ValueError, match='reason'):
        record(features, flags_db, reason=reason)
    assert features.history(flags_db) == []


@pytest.mark.parametrize('enabled', [None, 0, 1, 'false', 'true'])
def test_enabled_must_be_boolean(features, flags_db, enabled):
    with pytest.raises(ValueError, match='enabled'):
        record(features, flags_db, enabled=enabled)
    assert features.history(flags_db) == []


def test_unknown_flag_and_invalid_request_rejected(features, flags_db):
    with pytest.raises(ValueError, match='flag'):
        record(features, flags_db, flag='unknown')
    with pytest.raises(ValueError, match='flag'):
        features.history(flags_db, 'unknown')
    for request_id in (None, '', 'not-a-uuid'):
        with pytest.raises(ValueError, match='request_id'):
            record(features, flags_db, request_id=request_id)
    assert features.history(flags_db) == []
    assert record(features, flags_db, reason='理' * 300)['reason'] == '理' * 300


def test_history_order(features, flags_db):
    first = record(features, flags_db)
    other = record(features, flags_db, flag='portfolio_manager')
    last = record(features, flags_db, enabled=True)
    assert features.history(flags_db) == [first, other, last]
    assert features.history(flags_db, 'backup_sources') == [first, last]
    assert features.history(flags_db, 'company_researcher') == []


def test_caller_owns_transaction(features, flags_db):
    record(features, flags_db)
    flags_db.rollback()
    assert features.history(flags_db) == []
    assert features.current(flags_db)['backup_sources'] is True


def test_concurrent_changes_preserve_before_and_replay(features, flags_db):
    import psycopg
    from psycopg import sql
    schema = flags_db.execute('SELECT current_schema()').fetchone()[0]
    flags_db.commit()
    request_id = uuid.uuid4()

    def write(request):
        with psycopg.connect(TEST_URL, connect_timeout=3) as conn:
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            conn.execute("SET LOCAL lock_timeout = '5s'")
            return record(features, conn, request_id=request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        duplicate = list(pool.map(write, [request_id, request_id]))
        following = list(pool.map(write, [uuid.uuid4(), uuid.uuid4()]))
    assert duplicate[0] == duplicate[1]
    assert duplicate[0]['enabled_before'] is True
    assert all(row['enabled_before'] is False for row in following)
    assert len(features.history(flags_db)) == 3
