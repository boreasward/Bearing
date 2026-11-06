"""Feature flag reads on the disposable 031 schema."""
import importlib
import uuid
from datetime import timedelta

import pytest

from test_migration_031 import NOW, apply, insert, migrated_db


@pytest.fixture
def features():
    return importlib.import_module('investment_assistant.core.features')


@pytest.fixture
def flags_db(migrated_db):
    apply(migrated_db)
    return migrated_db


def test_no_rows_means_enabled(features, flags_db):
    assert features.FLAGS == ('backup_sources',)
    assert features.current(flags_db) == {'backup_sources': True}
    assert features.frozen(flags_db) == {'backup_sources': True}


def test_latest_row_wins(features, flags_db):
    # id is the authority even when timestamps are out of order.
    for enabled, at in [(False, NOW), (True, NOW - timedelta(days=1))]:
        insert(flags_db, 'feature_flag_changes', {'flag': 'backup_sources', 'enabled_after': enabled,
                                                 'reason': '测试', 'request_id': uuid.uuid4(), 'created_at': at})
    assert features.current(flags_db) == {'backup_sources': True}
    insert(flags_db, 'feature_flag_changes', {'flag': 'backup_sources', 'enabled_after': False, 'reason': '测试',
                                             'request_id': uuid.uuid4(), 'created_at': NOW - timedelta(days=2)})
    frozen = features.frozen(flags_db)
    assert frozen == features.current(flags_db) == {'backup_sources': False}
    insert(flags_db, 'feature_flag_changes', {'flag': 'backup_sources', 'enabled_after': True, 'reason': '测试',
                                             'request_id': uuid.uuid4(), 'created_at': NOW})
    assert frozen == {'backup_sources': False}      # the snapshot is independent of later writes
    assert features.current(flags_db) == {'backup_sources': True}
