"""The feature-switch HTTP contract (spec §9, §12.1, UI1) against an isolated schema through migration 031."""
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from investment_assistant.core import features as core_features
from investment_assistant.core import tx as core_tx

FLAGS = ['backup_sources', 'company_researcher', 'portfolio_manager', 'optimize_proposals']
FLAG_KEYS = {'flag', 'enabled', 'changed_at', 'reason', 'history_count'}
HISTORY_KEYS = {'id', 'flag', 'enabled_before', 'enabled_after', 'reason', 'request_id', 'created_at'}


@pytest.fixture
def api_db(workbench_db, monkeypatch):
    workbench_db.execute(Path('migrations/031_five_layers.sql').read_text(encoding='utf-8'))
    workbench_db.commit()

    @contextmanager
    def connection():
        yield workbench_db

    monkeypatch.setattr(core_tx, 'connection', connection)
    return workbench_db


def change(client, flag='backup_sources', **body):
    body.setdefault('enabled', False)
    body.setdefault('reason', '备源暂停观察')
    body.setdefault('request_id', str(uuid.uuid4()))
    return client.post(f'/api/settings/features/{flag}', json=body)


def test_get_defaults_everything_on_with_no_invented_history(client, api_db):
    body = client.get('/api/settings/features').json()
    assert set(body) == {'flags', 'history'} and body['history'] == []
    assert [f['flag'] for f in body['flags']] == FLAGS
    for item in body['flags']:
        assert set(item) == FLAG_KEYS
        assert (item['enabled'], item['changed_at'], item['reason'], item['history_count']) == (True, None, None, 0)
    assert api_db.execute('SELECT count(*) FROM feature_flag_changes').fetchone()[0] == 0


def test_get_and_post_require_reason_and_replay(client, api_db):
    request_id = str(uuid.uuid4())
    first = change(client, request_id=request_id)
    assert first.status_code == 200
    assert set(first.json()) == {'flag', 'enabled', 'changed_at'}
    assert first.json()['flag'] == 'backup_sources' and first.json()['enabled'] is False
    assert first.json()['changed_at'] is not None

    # a reason is required to switch off and on alike; nothing is written for a refused change
    for reason in (None, '', '   '):
        refused = change(client, reason=reason)
        assert refused.status_code == 400 and 'reason' in refused.json()['error']
    off_without_reason = change(client, flag='portfolio_manager', reason=None)
    on_without_reason = change(client, enabled=True, reason=None)
    assert off_without_reason.status_code == on_without_reason.status_code == 400
    assert api_db.execute('SELECT count(*) FROM feature_flag_changes').fetchone()[0] == 1

    # the same request id and content replays the first answer without a second row
    replay = change(client, request_id=request_id)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert api_db.execute('SELECT count(*) FROM feature_flag_changes').fetchone()[0] == 1
    # the same id with other content conflicts
    for other in ({'enabled': True}, {'reason': '换个理由'}):
        conflict = change(client, request_id=request_id, **other)
        assert conflict.status_code == 409 and 'request_id' in conflict.json()['error']
    assert change(client, flag='portfolio_manager', request_id=request_id).status_code == 409

    # the read side: the current values, per-flag counts and the real rows, oldest first
    back = change(client, enabled=True, reason='恢复备源')
    assert back.status_code == 200 and back.json()['enabled'] is True
    body = client.get('/api/settings/features').json()
    by_flag = {f['flag']: f for f in body['flags']}
    assert by_flag['backup_sources']['enabled'] is True and by_flag['backup_sources']['history_count'] == 2
    assert by_flag['backup_sources']['reason'] == '恢复备源'
    assert by_flag['backup_sources']['changed_at'] == back.json()['changed_at']
    assert by_flag['company_researcher'] == {'flag': 'company_researcher', 'enabled': True, 'changed_at': None,
                                             'reason': None, 'history_count': 0}
    assert [set(h) for h in body['history']] == [HISTORY_KEYS] * 2
    first_row, second_row = body['history']
    assert first_row['id'] < second_row['id']
    assert (first_row['flag'], first_row['enabled_before'], first_row['enabled_after'], first_row['reason'],
            first_row['request_id']) == ('backup_sources', True, False, '备源暂停观察', request_id)
    assert (second_row['enabled_before'], second_row['enabled_after'], second_row['reason']) == (False, True, '恢复备源')
    assert core_features.current(api_db)['backup_sources'] is True


def test_malformed_requests_write_nothing(client, api_db):
    assert change(client, flag='nope').status_code == 404
    assert change(client, flag='nope').json() == {'error': 'unknown_flag'}
    assert change(client, enabled='false').status_code == 400          # a boolean, not a word
    assert change(client, enabled=None).status_code == 400
    assert change(client, request_id='not-a-uuid').status_code == 400
    assert change(client, reason='理' * 301).status_code == 400
    assert api_db.execute('SELECT count(*) FROM feature_flag_changes').fetchone()[0] == 0


def test_missing_migration_031_is_503_for_both_directions(client, api_db):
    api_db.execute('DROP TABLE feature_flag_changes CASCADE')
    api_db.commit()
    got = client.get('/api/settings/features')
    assert got.status_code == 503 and got.json() == {'error': 'migration_031_missing'}
    posted = change(client)
    assert posted.status_code == 503 and posted.json() == {'error': 'migration_031_missing'}
