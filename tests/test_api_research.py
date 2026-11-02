"""The research HTTP contract (spec §12.1, UI1) against an isolated schema; no model, no network."""
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from investment_assistant.analysis import repo as analysis_repo
from investment_assistant.analysis import research_requests
from investment_assistant.core import locks as core_locks
from investment_assistant.core import tx as core_tx
from investment_assistant.core.constants import RESEARCH_DAILY_MAX

NOW = datetime(2026, 10, 30, 22, tzinfo=UTC)


@pytest.fixture
def api_db(workbench_db, monkeypatch):
    @contextmanager
    def connection():
        yield workbench_db

    monkeypatch.setattr(core_tx, 'connection', connection)
    from investment_assistant.api.routes import research as routes

    monkeypatch.setattr(routes, '_now', lambda: NOW)
    return workbench_db


@pytest.fixture
def submitted(monkeypatch):
    """Every background submission is recorded, none runs; the "daily run" is idle unless a test says so."""
    calls = []
    monkeypatch.setattr(research_requests.core_runner, 'submit',
                        lambda kind, fn, **kw: calls.append(kind) or 'run-test')
    monkeypatch.setattr(core_locks, 'is_running', lambda: False)
    return calls


def post(client, **body):
    body.setdefault('request_id', str(uuid.uuid4()))
    body.setdefault('ticker', 'AMD')
    return client.post('/api/research', json=body)


def test_invalid_ticker_400_missing_migration_503_and_queue_when_daily_running(client, api_db, submitted, monkeypatch):
    bad = post(client, ticker='not a ticker!')
    assert bad.status_code == 400 and bad.json() == {'error': 'invalid_ticker'}
    assert post(client, ticker=None).json() == {'error': 'invalid_ticker'}
    assert post(client, request_id='nope').json() == {'error': 'invalid_request_id'}
    assert api_db.execute('SELECT count(*) FROM research_requests').fetchone()[0] == 0 and submitted == []

    # review focus 5: a running daily run only queues the request — 200 queued, no 409 daily_running, no submit
    monkeypatch.setattr(core_locks, 'is_running', lambda: True)
    request_id = str(uuid.uuid4())
    queued = post(client, request_id=request_id, question='为何增长？', horizon_days=30)
    assert queued.status_code == 200 and set(queued.json()) == {'id', 'status'}
    assert queued.json()['status'] == 'queued' and submitted == []
    replay = post(client, request_id=request_id, question='为何增长？', horizon_days=30)
    assert replay.status_code == 200 and replay.json() == queued.json()
    assert api_db.execute('SELECT count(*) FROM research_requests').fetchone()[0] == 1   # D51: replay writes nothing

    # an idle daily run: the same request answers queued and the background run is submitted once
    monkeypatch.setattr(core_locks, 'is_running', lambda: False)
    idle = post(client, ticker='nvda')
    assert idle.status_code == 200 and idle.json()['status'] == 'queued' and submitted == ['research']

    # migration 031 not applied: 503 before anything is written
    api_db.execute('DROP TABLE research_versions, research_requests CASCADE')
    api_db.commit()
    missing = post(client)
    assert missing.status_code == 503 and missing.json() == {'error': 'migration_031_missing'}
    assert client.get('/api/research').json() == {'error': 'migration_031_missing'}


def test_guards_feature_flag_and_daily_quota_are_409(client, api_db, submitted, monkeypatch):
    monkeypatch.setattr(core_locks, 'is_running', lambda: True)
    for n in range(RESEARCH_DAILY_MAX):
        assert post(client, ticker='AMD').status_code == 200, n
    over = post(client)
    assert over.status_code == 409 and over.json() == {'error': 'research_quota'}

    api_db.execute("INSERT INTO feature_flag_changes (flag, enabled_before, enabled_after, reason, request_id) "
                   "VALUES ('company_researcher', true, false, '关', %s)", (str(uuid.uuid4()),))
    api_db.commit()
    off = post(client)
    assert off.status_code == 409 and off.json() == {'error': 'feature_disabled'}


def _version(db, request_id, version_no, **fields):
    return analysis_repo.insert_research_version(db, {
        'request_id': request_id, 'version_no': version_no, 'cutoff_at': NOW, 'created_at': NOW,
        'status': 'concluded', 'action': 'buy', 'summary': '增长可期', 'tokens': 120, 'elapsed_ms': 3400,
        'output': {'action': 'buy', 'summary': '增长可期', 'catalysts': [], 'counter': []},
        'review_when': None, **fields})


def test_list_and_detail_shapes(client, api_db, submitted, monkeypatch):
    monkeypatch.setattr(core_locks, 'is_running', lambda: True)
    from datetime import timedelta

    from investment_assistant.api.routes import research as routes

    first = post(client, ticker='AMD', question='为何增长？').json()['id']
    monkeypatch.setattr(routes, '_now', lambda: NOW + timedelta(minutes=1))     # distinct created_at: stable order
    second = post(client, ticker='NVDA').json()['id']
    _version(api_db, first, 1, status='error', action=None, summary='运行失败：model_error', tokens=40, output=None)
    _version(api_db, first, 2)
    analysis_repo.update_research_status(api_db, first, from_status='queued', to_status='running')
    analysis_repo.update_research_status(api_db, first, from_status='running', to_status='concluded')
    api_db.commit()

    listing = client.get('/api/research')
    assert listing.status_code == 200
    items = listing.json()['items']
    assert [item['id'] for item in items] == [second, first]                       # newest first
    assert set(items[0]) >= {'id', 'ticker', 'question', 'status', 'latest_summary', 'cutoff_at', 'tokens',
                             'created_at'}
    assert (items[0]['status'], items[0]['latest_summary'], items[0]['cutoff_at'], items[0]['tokens']) == (
        'queued', None, None, 0)
    assert (items[1]['ticker'], items[1]['question'], items[1]['status'], items[1]['latest_summary']) == (
        'AMD', '为何增长？', 'concluded', '增长可期')
    assert items[1]['tokens'] == 160 and datetime.fromisoformat(items[1]['cutoff_at']) == NOW
    assert [i['id'] for i in client.get('/api/research?status=concluded').json()['items']] == [first]
    assert client.get('/api/research?status=bogus').status_code == 400

    detail = client.get(f'/api/research/{first}')
    assert detail.status_code == 200
    body = detail.json()
    assert set(body) == {'request', 'versions', 'chain'} and body['chain'] is None
    assert body['request']['id'] == first and body['request']['status'] == 'concluded'
    assert body['request']['latest_summary'] == '增长可期' and body['request']['tokens'] == 160
    assert [v['version_no'] for v in body['versions']] == [1, 2]
    assert set(body['versions'][1]) == {'version_no', 'status', 'action', 'summary', 'cutoff_at', 'agent_run_id',
                                        'output', 'review_when', 'tokens', 'elapsed_ms', 'created_at'}
    assert body['versions'][1]['output']['action'] == 'buy' and body['versions'][1]['elapsed_ms'] == 3400
    assert (body['versions'][0]['status'], body['versions'][0]['output']) == ('error', None)
    assert client.get(f'/api/research/{uuid.uuid4()}').status_code == 404
    assert client.get('/api/research/not-a-uuid').status_code == 400


def test_close_ends_a_waiting_request(client, api_db, submitted, monkeypatch):
    monkeypatch.setattr(core_locks, 'is_running', lambda: True)
    made = post(client).json()['id']
    closed = client.post(f'/api/research/{made}/close', json={'reason': '不需要了'})
    assert closed.status_code == 200
    assert closed.json() == {'id': made, 'status': 'closed', 'closed_reason': '不需要了'}
    assert client.post(f'/api/research/{made}/close').json()['status'] == 'closed'   # idempotent
    assert client.post(f'/api/research/{uuid.uuid4()}/close').status_code == 404
