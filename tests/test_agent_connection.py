"""The ChatGPT connection use cases (v2.8 spec §8, §10.2): login, refresh, disconnect, models, status.

SQL tests: a throwaway schema of the disposable test database with the ``workbench_db`` migrations
(``conftest.WORKBENCH_MIGRATIONS`` ends with 029), plus the fake authorization server of
``test_agent_chatgpt_oauth``. Every token is a ``test-…`` placeholder; the tests assert none of them ever reaches a log,
an exception text, an event, ``last_error`` or the status view."""
import contextlib
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import openai
import pytest

from investment_assistant.core import tx as core_tx
import requests
from langchain_core.messages import HumanMessage

from investment_assistant.workbench import agent_connection, service
from investment_assistant.workbench.agent import chatgpt, model
from investment_assistant.workbench.agent.chatgpt import ChatGPTError
from test_agent_chatgpt_oauth import NOW, _get, auth, id_token, token_body  # noqa: F401  (auth is a fixture)
from test_agent_responses_model import _sse, failed_events

REAL_READ = agent_connection.read_for_settings        # conftest replaces it for every test; the SQL tests want the real one
HOST_ID = 'urn:uuid:123e4567-e89b-12d3-a456-426614174000'
OLD_ACCESS, OLD_REFRESH = 'test-access-old11111', 'test-refresh-old11111'
NEW_ACCESS, NEW_REFRESH = 'test-access-aaaa1111', 'test-refresh-bbbb2222'      # what token_body() issues
TOKENS = (OLD_ACCESS, OLD_REFRESH, NEW_ACCESS, NEW_REFRESH, 'test-code-1', 'test-verifier')


# ---------------------------------------------------------------------------------------------- fixtures

@pytest.fixture
def db(workbench_db, monkeypatch):
    import psycopg

    schema = workbench_db.execute('SELECT current_schema()').fetchone()[0]
    url = os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']

    @contextmanager
    def connection():                    # a fresh connection per use: committed state is visible across threads
        with psycopg.connect(url, options=f'-c search_path={schema}') as conn:
            yield conn

    monkeypatch.setattr(core_tx, 'connection', connection)
    monkeypatch.setattr(agent_connection, 'read_for_settings', REAL_READ)
    monkeypatch.setattr(agent_connection, '_PENDING', None)
    monkeypatch.setattr(agent_connection, '_utcnow', lambda: NOW)         # the seeded expiry times are relative to NOW
    yield workbench_db
    with agent_connection._PENDING_LOCK:
        pending, agent_connection._PENDING = agent_connection._PENDING, None
    if pending is not None:
        pending.server.close()


def seed(db, status='connected', *, access=OLD_ACCESS, refresh=OLD_REFRESH, expires_in=3600, earliest=None,
         scopes=None, subject='user-1', email='someone@example.com', model_slug=None, client_id='client-1',
         last_error=None):
    scopes = chatgpt.SCOPES.split() if scopes is None else scopes
    connected = status == 'connected'
    db.execute(
        'INSERT INTO agent_connections (id, host_id, client_id, status, subject, email, scopes, access_token, '
        'access_expires_at, refresh_token, refresh_expires_at, earliest_refresh_at, model, model_display_name, '
        'connected_at, last_error) VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
        (HOST_ID, client_id, status, subject, email, scopes if connected else [], access if connected else None,
         NOW + timedelta(seconds=expires_in) if connected else None, refresh if connected else None,
         NOW + timedelta(days=30) if connected else None, earliest, model_slug,
         model_slug and model_slug.upper(), NOW if connected else None, last_error))
    db.commit()


def row(db):
    cursor = db.execute('SELECT * FROM agent_connections WHERE id = 1')
    names = [column.name for column in cursor.description]
    found = cursor.fetchone()
    db.commit()
    return dict(zip(names, found)) if found else None


def events(db):
    found = db.execute('SELECT kind, code, detail FROM agent_connection_events ORDER BY id').fetchall()
    db.commit()
    return [{'kind': kind, 'code': code, 'detail': detail} for kind, code, detail in found]


def kinds(db):
    return [event['kind'] for event in events(db)]


def refresh_response(auth_server, **overrides):
    body = token_body(auth_server.base, id_token=id_token(auth_server.base), **overrides)
    return 200, body


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError('timed out')


def browser_login(auth_server, db, *, subject='user-1', scope=chatgpt.SCOPES, client_id='client-from-callback',
                  with_client_id=True, extra_query='', now=NOW, **token_overrides):
    """start_login -> the 'browser' hits the callback -> wait until the background completion finished."""
    started = agent_connection.start_login(now=now)
    query = {k: v[0] for k, v in parse_qs(urlsplit(started['authorize_url']).query).items()}
    seen = {}

    def respond(form):
        seen['form'] = form
        return 200, token_body(auth_server.base, scope=scope, **{'id_token': id_token(
            auth_server.base, aud=form['client_id'], sub=subject, nonce=query['nonce']), **token_overrides})

    auth_server.tokens = [respond]
    port = int(urlsplit(query['redirect_uri']).port)
    callback = f"/auth/callback?state={query['state']}&code=test-code-1{extra_query}"
    if with_client_id:
        callback += f'&client_id={client_id}'
    status = _get(port, callback)[0]
    wait_for(lambda: agent_connection._PENDING is None)
    return started, query, seen, status


# ---------------------------------------------------------------------------------------------- refresh

def test_no_refresh_when_enough_time_left(db, auth):
    seed(db, expires_in=301)
    assert agent_connection.access_token(now=NOW) == OLD_ACCESS
    seed_row = row(db)
    assert not auth.requests and seed_row['access_token'] == OLD_ACCESS and kinds(db) == []


def test_refresh_when_due_writes_before_use(db, auth):
    seed(db, expires_in=299, earliest=NOW - timedelta(minutes=1))
    probes = []

    def during_refresh():
        import psycopg
        url = os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']
        schema = db.execute('SELECT current_schema()').fetchone()[0]
        with psycopg.connect(url, options=f'-c search_path={schema}') as other:
            try:
                other.execute('SELECT 1 FROM agent_connections WHERE id = 1 FOR UPDATE NOWAIT')
                probes.append('unlocked')
            except psycopg.errors.LockNotAvailable:
                probes.append('locked')
            other.rollback()
            probes.append(other.execute('SELECT access_token FROM agent_connections').fetchone()[0])

    auth.token_hook = during_refresh
    auth.tokens = [refresh_response(auth, earliest_refresh_at=int((NOW + timedelta(minutes=30)).timestamp()))]
    returned = agent_connection.access_token(now=NOW)

    assert returned == NEW_ACCESS
    assert probes == ['locked', OLD_ACCESS]            # the exchange ran inside the row lock; the old token was still stored
    stored = row(db)
    assert stored['access_token'] == NEW_ACCESS == returned and stored['refresh_token'] == NEW_REFRESH   # committed first
    assert stored['access_expires_at'] == NOW + timedelta(hours=1)
    assert stored['refresh_expires_at'] == NOW + timedelta(days=30)
    assert stored['last_refresh_at'] == NOW and stored['last_error'] is None
    assert stored['earliest_refresh_at'] == NOW + timedelta(minutes=30)
    assert stored['status'] == 'connected' and stored['scopes'] == chatgpt.SCOPES.split()
    (_, _, _, form), = auth.calls('/oauth/token')
    assert form == {'grant_type': 'refresh_token', 'client_id': 'client-1', 'refresh_token': OLD_REFRESH,
                    'resource': auth.base + '/v1'}
    assert events(db) == [{'kind': 'refreshed', 'code': None, 'detail': {
        'protocol': 'siwc-1', 'expires_at': (NOW + timedelta(hours=1)).isoformat(),
        'refresh_expires_at': (NOW + timedelta(days=30)).isoformat()}}]


def test_expired_token_is_refreshed_and_scope_may_be_omitted(db, auth):
    seed(db, expires_in=-10)
    auth.tokens = [refresh_response(auth, scope=None)]
    assert agent_connection.access_token(now=NOW) == NEW_ACCESS
    assert row(db)['scopes'] == chatgpt.SCOPES.split()               # the previous scopes stay


def test_earliest_refresh_at_not_reached(db, auth):
    seed(db, expires_in=100, earliest=NOW + timedelta(minutes=10))
    assert agent_connection.access_token(now=NOW) == OLD_ACCESS      # nearly expired but the issuer says not yet
    assert not auth.requests
    seed_row = row(db)
    db.execute('UPDATE agent_connections SET access_expires_at = %s', (NOW - timedelta(seconds=1),))
    db.commit()
    with pytest.raises(ChatGPTError) as caught:
        agent_connection.access_token(now=NOW)                       # expired and not refreshable yet
    assert caught.value.code == 'refresh_not_ready' and caught.value.retryable
    assert not auth.requests and row(db)['status'] == 'connected' and seed_row['access_token'] == OLD_ACCESS
    db.execute('UPDATE agent_connections SET earliest_refresh_at = %s', (NOW,))
    db.commit()
    auth.tokens = [refresh_response(auth)]
    assert agent_connection.access_token(now=NOW) == NEW_ACCESS      # the time has come


@pytest.mark.parametrize('lock', ['thread lock and row lock', 'row lock only'])
def test_concurrent_callers_refresh_once(db, auth, monkeypatch, lock):
    if lock == 'row lock only':
        monkeypatch.setattr(agent_connection, '_TOKEN_LOCK', contextlib.nullcontext())
    seed(db, expires_in=10)
    auth.token_hook = lambda: time.sleep(0.3)
    auth.tokens = [refresh_response(auth)]
    barrier = threading.Barrier(2)
    results, errors = [], []

    def call():
        barrier.wait()
        try:
            results.append(agent_connection.access_token(now=NOW))
        except Exception as exc:          # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert not errors and results == [NEW_ACCESS, NEW_ACCESS]
    assert len(auth.calls('/oauth/token')) == 1                      # the second caller found the new token
    assert kinds(db) == ['refreshed']


REAUTH_SERVER_CODES = ['invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired',
                       'refresh_token_invalidated', 'refresh_token_reused']


def _assert_needs_login(db, auth, code, caught):
    assert caught.value.code == code and code in chatgpt.REAUTH_CODES
    stored = row(db)
    assert stored['status'] == 'needs_login'
    assert stored['access_token'] is None and stored['refresh_token'] is None and stored['access_expires_at'] is None
    assert stored['host_id'] == HOST_ID and stored['client_id'] == 'client-1' and stored['subject'] == 'user-1'
    assert stored['last_error'] and len(stored['last_error']) <= 300
    assert [(e['kind'], e['code'], e['detail']['reauth']) for e in events(db)] == [('refresh_failed', code, True)]
    assert agent_connection.first_failure_since_ok() is True
    seen = len(auth.requests)
    with pytest.raises(ChatGPTError) as again:                        # no new refresh attempt, no new event
        agent_connection.access_token(now=NOW)
    assert again.value.code == 'not_connected' and len(auth.requests) == seen and len(events(db)) == 1


@pytest.mark.parametrize('code', REAUTH_SERVER_CODES)
def test_reauth_errors_need_login(db, auth, code):
    seed(db, expires_in=10)
    auth.tokens = [(400, {'error': code, 'error_description': f'{OLD_REFRESH} was already used'})]
    with pytest.raises(ChatGPTError) as caught:
        agent_connection.access_token(now=NOW)
    _assert_needs_login(db, auth, code, caught)
    assert OLD_REFRESH not in str(caught.value) and OLD_REFRESH not in row(db)['last_error']


def test_invalid_id_token_on_refresh_needs_login(db, auth):
    seed(db, expires_in=10)
    auth.tokens = [(200, token_body(auth.base, id_token=id_token('https://evil.example')))]
    with pytest.raises(ChatGPTError) as caught:
        agent_connection.access_token(now=NOW)
    _assert_needs_login(db, auth, 'invalid_id_token', caught)


def test_account_mismatch_on_refresh_needs_login(db, auth):
    seed(db, expires_in=10)
    auth.tokens = [(200, token_body(auth.base, id_token=id_token(auth.base, sub='someone-else')))]
    with pytest.raises(ChatGPTError) as caught:
        agent_connection.access_token(now=NOW)
    _assert_needs_login(db, auth, 'account_mismatch', caught)


def test_network_error_keeps_state(db, auth, monkeypatch):
    seed(db, expires_in=10)
    chatgpt.discover()
    auth.tokens = [(503, {'error': 'server_error'})]
    with pytest.raises(ChatGPTError) as caught:
        agent_connection.access_token(now=NOW)
    assert caught.value.code == 'server_error' and caught.value.retryable and caught.value.status == 503
    stored = row(db)
    assert stored['status'] == 'connected' and stored['access_token'] == OLD_ACCESS
    assert stored['refresh_token'] == OLD_REFRESH and stored['last_error']
    assert kinds(db) == []                                            # a transient failure writes no event

    def unreachable(*args, **kwargs):
        raise requests.ConnectionError('no route to host')
    real_http = chatgpt._http
    monkeypatch.setattr(chatgpt, '_http', unreachable)
    with pytest.raises(ChatGPTError) as caught:
        agent_connection.access_token(now=NOW)
    assert caught.value.code == 'network_error'
    assert row(db)['status'] == 'connected' and agent_connection.first_failure_since_ok() is False
    # the next unit can succeed: nothing was lost
    monkeypatch.setattr(chatgpt, '_http', real_http)
    auth.tokens = [refresh_response(auth)]
    assert agent_connection.access_token(now=NOW) == NEW_ACCESS and row(db)['last_error'] is None


def test_refresh_response_without_refresh_token_is_not_a_logout(db, auth):
    seed(db, expires_in=10)
    auth.tokens = [(200, token_body(auth.base, refresh_token=None))]
    with pytest.raises(ChatGPTError) as caught:
        agent_connection.access_token(now=NOW)
    assert caught.value.code == 'invalid_token_response'
    assert row(db)['status'] == 'connected' and kinds(db) == []


def test_not_connected_without_a_usable_connection(db):
    with pytest.raises(ChatGPTError) as caught:
        agent_connection.access_token(now=NOW)                        # no row at all
    assert caught.value.code == 'not_connected'
    for status in ('disconnected', 'needs_login'):
        db.execute('DELETE FROM agent_connections')
        db.commit()
        seed(db, status)
        with pytest.raises(ChatGPTError) as caught:
            agent_connection.access_token(now=NOW)
        assert caught.value.code == 'not_connected'


def test_not_connected_notifies_once(db):
    """first_failure_since_ok(since=): true once per failure episode, for the first run that starts after it."""
    def add(kind, at, code=None, **detail):
        db.execute("INSERT INTO agent_connection_events (kind, code, detail, created_at) VALUES (%s, %s, %s::jsonb, %s)",
                   (kind, code, json.dumps(detail), at))
        db.commit()

    def status(value):
        db.execute('DELETE FROM agent_connections')
        db.commit()
        seed(db, value)

    t = lambda hours: NOW + timedelta(hours=hours)                       # noqa: E731
    assert agent_connection.first_failure_since_ok() is False            # no row, no events
    status('connected')
    add('connected', t(0))
    assert agent_connection.first_failure_since_ok(since=t(-1)) is False  # healthy

    # a failure at t+1h: the connection is needs_login now
    status('needs_login')
    add('refresh_failed', t(1), 'refresh_token_reused', reauth=True)
    run_1_start, run_2_start = t(2), t(26)                               # two consecutive daily runs after it
    assert agent_connection.first_failure_since_ok(since=t(-24)) is True  # run 1 (previous run started earlier)
    assert agent_connection.first_failure_since_ok(since=run_1_start) is False   # run 2: already reported
    assert agent_connection.first_failure_since_ok(since=run_2_start) is False
    assert agent_connection.first_failure_since_ok() is True             # without a window: any failure counts

    # a transient failure in between changes nothing
    add('refresh_failed', t(3), 'network_error', reauth=False)
    assert agent_connection.first_failure_since_ok(since=run_1_start) is False
    assert agent_connection.first_failure_since_ok(since=t(0.5)) is True

    # reconnect, then a new failure: a new episode, reported by the first run after it
    status('connected')
    add('connected', t(30))
    assert agent_connection.first_failure_since_ok(since=t(26)) is False
    status('needs_login')
    add('refresh_failed', t(40), 'invalid_grant', reauth=True)
    assert agent_connection.first_failure_since_ok(since=t(26)) is True
    assert agent_connection.first_failure_since_ok(since=t(50)) is False

    # a success after the failure while the row still says needs_login (cannot happen, but the rule is explicit)
    add('refreshed', t(41))
    assert agent_connection.first_failure_since_ok(since=t(26)) is False
    status('disconnected')
    assert agent_connection.first_failure_since_ok(since=t(-100)) is False    # disconnected on purpose: nothing to report


def test_failure_episode_through_the_real_paths(db, auth):
    """Two daily runs after a refresh failure: only the first one is told; a reconnect starts a new episode."""
    seed(db, expires_in=10)
    auth.tokens = [(400, {'error': 'refresh_token_reused'})]
    with pytest.raises(ChatGPTError):
        agent_connection.access_token(now=NOW)
    run_1_start = NOW - timedelta(hours=1)                               # the failure happened during run 1
    assert agent_connection.first_failure_since_ok(since=run_1_start) is True
    run_2_start = NOW + timedelta(hours=24)
    with pytest.raises(ChatGPTError):                                    # run 2: node-level not_connected, no new event
        agent_connection.access_token(now=run_2_start)
    assert agent_connection.first_failure_since_ok(since=NOW + timedelta(seconds=1)) is False
    browser_login(auth, db, with_client_id=False)
    assert agent_connection.first_failure_since_ok(since=run_1_start) is False
    db.execute('UPDATE agent_connections SET access_expires_at = %s', (NOW + timedelta(seconds=5),))
    db.commit()
    auth.tokens = [(400, {'error': 'invalid_grant'})]
    with pytest.raises(ChatGPTError):
        agent_connection.access_token(now=NOW + timedelta(seconds=1))
    assert agent_connection.first_failure_since_ok(since=NOW - timedelta(minutes=5)) is True


def test_mark_needs_login_after_a_model_call_proved_it_unusable(db):
    assert agent_connection.mark_needs_login('invalid_token', now=NOW) is False      # nothing to mark
    seed(db)
    assert agent_connection.mark_needs_login('subscription_sharing_user_not_eligible', now=NOW) is True
    stored = row(db)
    assert stored['status'] == 'needs_login' and stored['access_token'] is None and stored['refresh_token'] is None
    assert [(e['kind'], e['code']) for e in events(db)] == [('refresh_failed', 'subscription_sharing_user_not_eligible')]
    assert agent_connection.mark_needs_login('invalid_token', now=NOW) is False      # already there


# ---------------------------------------------------------------------------------------------- disconnect

def test_disconnect_revoked(db, auth):
    seed(db, model_slug='gpt-a')
    assert agent_connection.disconnect(now=NOW) == {'revoked': True}
    (_, _, _, form), = auth.calls('/oauth/revoke')
    assert form == {'token': OLD_REFRESH, 'token_type_hint': 'refresh_token', 'client_id': 'client-1'}
    stored = row(db)
    assert stored['status'] == 'disconnected' and stored['access_token'] is None and stored['refresh_token'] is None
    assert stored['access_expires_at'] is None and stored['refresh_expires_at'] is None and stored['scopes'] == []
    assert (stored['host_id'], stored['client_id'], stored['model']) == (HOST_ID, 'client-1', 'gpt-a')   # kept
    assert stored['subject'] is None and stored['email'] is None      # the next login may be another account
    assert events(db) == [{'kind': 'disconnected', 'code': None, 'detail': {'protocol': 'siwc-1', 'revoked': True}}]


@pytest.mark.parametrize('how', ['issuer refuses', 'issuer down', 'no revocation endpoint'])
def test_disconnect_revoke_unconfirmed(db, auth, how, monkeypatch):
    seed(db)
    if how == 'issuer refuses':
        auth.revoke_status = 400
    elif how == 'issuer down':
        auth.revoke_status = 503
    else:
        del auth.discovery['revocation_endpoint']
    assert agent_connection.disconnect(now=NOW) == {'revoked': False}
    stored = row(db)
    assert stored['status'] == 'disconnected' and stored['access_token'] is None and stored['refresh_token'] is None
    assert events(db)[-1]['detail']['revoked'] is False


def test_disconnect_when_there_is_nothing_to_revoke(db, auth):
    assert agent_connection.disconnect(now=NOW) == {'revoked': False}   # no row at all
    seed(db, 'needs_login')
    assert agent_connection.disconnect(now=NOW) == {'revoked': False} and not auth.calls('/oauth/revoke')
    assert row(db)['status'] == 'disconnected'


# ---------------------------------------------------------------------------------------------- login

def test_login_flow_connects(db, auth):
    started, query, seen, status = browser_login(auth, db)
    assert status == 200
    assert query['client_id'] == 'dynamic_agent_client' and query['agent_name_hint'] == 'investment-assistant'
    assert query['resource'] == auth.base + '/v1' and query['scope'] == chatgpt.SCOPES
    assert query['code_challenge_method'] == 'S256' and query['response_type'] == 'code'
    assert query['redirect_uri'].startswith('http://127.0.0.1:') and query['redirect_uri'].endswith('/auth/callback')
    stored = row(db)
    assert query['ext_agent_host_id'] == stored['host_id'] and stored['host_id'].startswith('urn:uuid:')
    form = seen['form']
    assert form['grant_type'] == 'authorization_code' and form['code'] == 'test-code-1'
    assert form['client_id'] == 'client-from-callback' and form['redirect_uri'] == query['redirect_uri']
    assert form['resource'] == auth.base + '/v1'
    import base64
    import hashlib
    assert base64.urlsafe_b64encode(hashlib.sha256(form['code_verifier'].encode()).digest()).rstrip(b'=').decode() \
        == query['code_challenge']
    assert stored['status'] == 'connected' and stored['client_id'] == 'client-from-callback'
    assert stored['subject'] == 'user-1' and stored['email'] == 'someone@example.com'
    assert stored['scopes'] == chatgpt.SCOPES.split()
    assert (stored['access_token'], stored['refresh_token']) == (NEW_ACCESS, NEW_REFRESH)
    assert abs((stored['access_expires_at'] - NOW).total_seconds() - 3600) < 30
    assert abs((stored['refresh_expires_at'] - NOW).total_seconds() - 30 * 86400) < 30
    assert stored['connected_at'] is not None and stored['last_error'] is None
    assert [e['kind'] for e in events(db)] == ['login_started', 'connected']
    assert events(db)[0]['detail'] == {'protocol': 'siwc-1', 'prior': 'disconnected'}
    assert events(db)[1]['detail']['scopes_ok'] is True
    view = agent_connection.status_view(now=NOW)
    assert view['status'] == 'connected' and view['pending'] is None and view['email_masked'] == 's***@example.com'


def test_second_login_reuses_registration_and_hints_the_email(db, auth):
    seed(db, 'needs_login', client_id='client-1')
    started, query, seen, _ = browser_login(auth, db, with_client_id=False, client_id='unused')
    assert query['client_id'] == 'client-1' and query['login_hint'] == 'someone@example.com'
    assert 'agent_name_hint' not in query and query['ext_agent_host_id'] == HOST_ID     # the host id never changes
    assert seen['form']['client_id'] == 'client-1'
    assert row(db)['status'] == 'connected' and row(db)['host_id'] == HOST_ID
    assert events(db)[0]['detail']['prior'] == 'needs_login'


def test_login_persists_client_id_before_exchange(db, auth):
    auth.tokens = [(400, {'error': 'invalid_grant'})]

    def failing(form):
        failing.client_id_in_db = row(db)['client_id']                # what is stored when the exchange is attempted
        return 400, {'error': 'invalid_grant', 'error_description': 'code test-code-1 expired'}
    started = agent_connection.start_login(now=NOW)
    query = {k: v[0] for k, v in parse_qs(urlsplit(started['authorize_url']).query).items()}
    auth.tokens = [failing]
    _get(urlsplit(query['redirect_uri']).port,
         f"/auth/callback?state={query['state']}&code=test-code-1&client_id=issued-client")
    wait_for(lambda: agent_connection._PENDING is None)
    assert failing.client_id_in_db == 'issued-client'                 # saved before the one-time code was spent
    stored = row(db)
    assert stored['client_id'] == 'issued-client' and stored['status'] == 'disconnected'
    assert stored['access_token'] is None and stored['last_error']
    assert [(e['kind'], e['code']) for e in events(db)] == [('login_started', None), ('login_failed', 'invalid_grant')]
    assert 'test-code-1' not in stored['last_error']
    assert agent_connection.status_view(now=NOW)['last_error'] == stored['last_error']


def test_login_error_param_restores_the_status(db, auth):
    seed(db, 'needs_login')
    started = agent_connection.start_login(now=NOW)
    assert row(db)['status'] == 'pending'
    query = {k: v[0] for k, v in parse_qs(urlsplit(started['authorize_url']).query).items()}
    _get(urlsplit(query['redirect_uri']).port, f"/auth/callback?state={query['state']}&error=access_denied")
    wait_for(lambda: agent_connection._PENDING is None)
    stored = row(db)
    assert stored['status'] == 'needs_login' and stored['last_error']
    assert [(e['kind'], e['code']) for e in events(db)] == [('login_started', None), ('login_failed', 'access_denied')]
    assert not auth.calls('/oauth/token')


def test_login_with_a_different_registration_is_incomplete(db, auth):
    seed(db, 'needs_login', client_id='client-1')
    started, query, seen, _ = browser_login(auth, db, client_id='another-client')
    assert 'form' not in seen and row(db)['client_id'] == 'client-1' and row(db)['status'] == 'needs_login'
    assert events(db)[-1]['code'] == 'registration_incomplete'


def test_account_mismatch_on_relogin(db, auth):
    seed(db, 'needs_login', subject='user-1')
    browser_login(auth, db, subject='someone-else', with_client_id=False)
    stored = row(db)
    assert stored['status'] == 'needs_login' and stored['subject'] == 'user-1' and stored['access_token'] is None
    assert events(db)[-1] | {'detail': None} == {'kind': 'login_failed', 'code': 'account_mismatch', 'detail': None}
    assert 'ChatGPT' in stored['last_error']


def test_no_sharing_scope_not_configured(db, auth):
    browser_login(auth, db, scope='openid profile email offline_access resource.invoke')
    stored = row(db)
    assert stored['status'] == 'connected' and chatgpt.SHARING_SCOPE not in stored['scopes']
    assert agent_connection.status_view(now=NOW)['scopes_ok'] is False
    assert events(db)[-1]['detail']['scopes_ok'] is False
    settings = model.settings_from_env({})
    assert settings.connection == 'no_sharing'
    assert model.unavailable_reason(settings) == 'not_configured'     # signed in, but nothing to share


def test_login_expires_after_ttl(db, auth):
    seed(db, 'needs_login')
    started = agent_connection.start_login(now=NOW)
    port = urlsplit(urlsplit(started['authorize_url']).query and
                    parse_qs(urlsplit(started['authorize_url']).query)['redirect_uri'][0]).port
    assert started['expires_at'] == (NOW + timedelta(seconds=600)).isoformat()
    assert agent_connection.status_view(now=NOW + timedelta(seconds=599))['status'] == 'pending'
    view = agent_connection.status_view(now=NOW + timedelta(seconds=600))
    assert view['status'] == 'needs_login' and view['pending'] is None     # back to the status it had
    assert row(db)['status'] == 'needs_login'
    with pytest.raises(OSError):
        _get(port, '/auth/callback?state=x&code=y&client_id=z')            # the listener is closed
    assert events(db)[-1]['code'] == 'login_expired' and events(db)[-1]['kind'] == 'login_failed'


def test_cancel_login(db, auth):
    assert agent_connection.cancel_login() == {'cancelled': False}
    started = agent_connection.start_login(now=NOW)
    query = {k: v[0] for k, v in parse_qs(urlsplit(started['authorize_url']).query).items()}
    port = urlsplit(query['redirect_uri']).port
    assert row(db)['status'] == 'pending' and agent_connection.status_view(now=NOW)['pending']
    assert agent_connection.cancel_login() == {'cancelled': True}
    assert row(db)['status'] == 'disconnected' and agent_connection.status_view(now=NOW)['pending'] is None
    with pytest.raises(OSError):
        _get(port, f"/auth/callback?state={query['state']}&code=c&client_id=a")
    assert [(e['kind'], e['code']) for e in events(db)] == [('login_started', None), ('login_failed', 'cancelled')]
    assert row(db)['last_error'] is None


def test_pending_row_is_restored_after_a_restart(db, auth):
    seed(db, 'needs_login')
    agent_connection.start_login(now=NOW)
    assert row(db)['status'] == 'pending'
    with agent_connection._PENDING_LOCK:                               # the process dies: memory is gone
        lost, agent_connection._PENDING = agent_connection._PENDING, None
    lost.server.close()
    view = agent_connection.status_view(now=NOW)
    assert view['status'] == 'needs_login' and row(db)['status'] == 'needs_login'
    assert events(db)[-1]['code'] == 'login_expired'


def test_login_on_a_connected_row_keeps_the_connection_until_it_succeeds(db, auth):
    seed(db)
    started = agent_connection.start_login(now=NOW)
    view = agent_connection.status_view(now=NOW)
    assert view['status'] == 'connected' and view['pending'] == {'expires_at': started['expires_at']}
    assert row(db)['access_token'] == OLD_ACCESS
    assert agent_connection.cancel_login() == {'cancelled': True}
    assert row(db)['status'] == 'connected' and row(db)['access_token'] == OLD_ACCESS


def test_start_login_replaces_a_login_in_progress(db, auth):
    first = agent_connection.start_login(now=NOW)
    second = agent_connection.start_login(now=NOW)
    port = lambda started: urlsplit(parse_qs(urlsplit(started['authorize_url']).query)['redirect_uri'][0]).port  # noqa: E731
    with pytest.raises(OSError):
        _get(port(first), '/auth/callback?state=x')
    assert _get(port(second), '/auth/callback?state=x')[0] == 400
    assert row(db)['status'] == 'pending'


def test_callback_port_setting(db, auth, monkeypatch):
    import socket
    probe = socket.socket()
    probe.bind(('127.0.0.1', 0))
    free = probe.getsockname()[1]
    probe.close()
    monkeypatch.setenv('WORKBENCH_CHATGPT_CALLBACK_PORT', str(free))
    started = agent_connection.start_login(now=NOW)
    assert parse_qs(urlsplit(started['authorize_url']).query)['redirect_uri'] == [f'http://127.0.0.1:{free}/auth/callback']
    agent_connection.cancel_login()
    started = agent_connection.start_login(now=NOW, open_port=0)       # an explicit argument wins
    assert f':{free}/' not in parse_qs(urlsplit(started['authorize_url']).query)['redirect_uri'][0]
    agent_connection.cancel_login()
    monkeypatch.setenv('WORKBENCH_CHATGPT_CALLBACK_PORT', 'not-a-port')
    with pytest.raises(ChatGPTError) as caught:
        agent_connection.start_login(now=NOW)
    assert caught.value.code == 'callback_port_unavailable'


def test_login_needs_a_verifiable_discovery_document(db, auth):
    auth.discovery = {**auth.discovery, 'issuer': 'https://evil.example'}
    with pytest.raises(ChatGPTError) as caught:
        agent_connection.start_login(now=NOW)
    assert caught.value.code == 'discovery_failed'
    assert row(db) is None and agent_connection._PENDING is None       # nothing was started or stored


# ---------------------------------------------------------------------------------------------- model failures

def _client_for(handler, token=OLD_ACCESS):
    return model.chat_model(SETTINGS_CONNECTED, token_provider=lambda: token, transport=httpx.MockTransport(handler),
                            report_failures=True)


SETTINGS_CONNECTED = model.AgentSettings(provider='chatgpt', model='gpt-test', api_key=None, daily_tokens=None,
                                         config_error=None, connection='connected')
MESSAGES = [HumanMessage(content='hi')]


def _http_failure(status, code=None, **body):
    payload = {'error': {'code': code, 'message': 'server text', **body}} if code else {'detail': 'x'}
    return lambda request: httpx.Response(status, json=payload, request=request)


def _sse_failure(code):
    return lambda request: httpx.Response(200, headers={'content-type': 'text/event-stream'},
                                          content=_sse(failed_events(code)), request=request)


@pytest.mark.parametrize('code', sorted(model.RELOGIN_CODES))
@pytest.mark.parametrize('how', ['http', 'stream'])
def test_relogin_class_model_failure_marks_needs_login(db, auth, code, how):
    seed(db)
    handler = _http_failure(403, code) if how == 'http' else _sse_failure(code)
    with pytest.raises((openai.APIStatusError, ChatGPTError)) as caught:
        _client_for(handler).invoke(MESSAGES)
    assert model.classify_error(caught.value, OLD_ACCESS)[0] == 'not_connected'    # the error still surfaces
    stored = row(db)
    assert stored['status'] == 'needs_login' and stored['access_token'] is None and stored['refresh_token'] is None
    assert [(e['kind'], e['code'], e['detail']['reauth']) for e in events(db)] == [('refresh_failed', code, True)]
    assert agent_connection.first_failure_since_ok(since=NOW - timedelta(minutes=1)) is True
    assert OLD_ACCESS not in json.dumps(events(db)) and 'server text' not in (stored['last_error'] or '')


def test_unauthorized_with_an_old_token_forces_a_refresh_first(db, auth):
    seed(db)
    db.execute('UPDATE agent_connections SET connected_at = %s, last_refresh_at = %s',
               (NOW - timedelta(hours=5), NOW - timedelta(hours=2)))
    db.commit()
    with pytest.raises(openai.AuthenticationError):
        _client_for(_http_failure(401)).invoke(MESSAGES)
    stored = row(db)
    assert stored['status'] == 'connected' and stored['access_token'] == OLD_ACCESS      # nothing is lost yet
    assert stored['access_expires_at'] == NOW and kinds(db) == []
    auth.tokens = [refresh_response(auth)]
    assert agent_connection.access_token(now=NOW) == NEW_ACCESS                          # the next unit refreshes
    assert row(db)['last_refresh_at'] == NOW and kinds(db) == ['refreshed']


@pytest.mark.parametrize('code', [None, 'invalid_token', 'invalid_api_key'])
def test_unauthorized_with_a_fresh_token_marks_needs_login(db, auth, code):
    seed(db)
    db.execute('UPDATE agent_connections SET last_refresh_at = %s', (NOW - timedelta(minutes=2),))
    db.commit()
    with pytest.raises(openai.AuthenticationError):
        _client_for(_http_failure(401, code)).invoke(MESSAGES)
    assert row(db)['status'] == 'needs_login'
    assert [(e['kind'], e['code']) for e in events(db)] == [('refresh_failed', 'invalid_token')]


def test_unauthorized_report_for_a_token_that_is_no_longer_stored_is_ignored(db, auth):
    seed(db)
    assert agent_connection.report_unauthorized('test-access-someone-else', now=NOW) == 'ignored'
    assert row(db)['status'] == 'connected' and row(db)['access_expires_at'] == NOW + timedelta(hours=1)
    db.execute('DELETE FROM agent_connections')
    db.commit()
    assert agent_connection.report_unauthorized(OLD_ACCESS, now=NOW) == 'ignored'


@pytest.mark.parametrize('handler', [
    _http_failure(429, 'subscription_sharing_usage_limit_exceeded'), _http_failure(404, 'model_not_found'),
    _http_failure(400, 'invalid_request_error'), _http_failure(500), _http_failure(503, 'server_error'),
    _http_failure(429, 'subscription_sharing_usage_unavailable'), _sse_failure('subscription_sharing_user_unavailable'),
])
def test_other_model_failures_leave_the_connection_alone(db, auth, handler):
    seed(db)
    with pytest.raises(Exception):
        _client_for(handler).invoke(MESSAGES)
    assert row(db)['status'] == 'connected' and row(db)['access_token'] == OLD_ACCESS and kinds(db) == []


def test_models_with_an_injected_token_do_not_touch_the_connection(db, auth):
    seed(db)
    handler = _http_failure(403, 'subscription_sharing_invalid_user')
    plain = model.chat_model(SETTINGS_CONNECTED, token_provider=lambda: OLD_ACCESS, transport=httpx.MockTransport(handler))
    with pytest.raises(openai.PermissionDeniedError):
        plain.invoke(MESSAGES)
    assert row(db)['status'] == 'connected' and kinds(db) == []


def test_a_failing_bookkeeping_never_hides_the_model_error(db, auth, monkeypatch):
    seed(db)
    monkeypatch.setattr(agent_connection, 'mark_needs_login', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('db down')))
    with pytest.raises(openai.PermissionDeniedError):
        _client_for(_http_failure(403, 'subscription_sharing_invalid_user')).invoke(MESSAGES)
    assert row(db)['status'] == 'connected'


# ---------------------------------------------------------------------------------------------- accounts, edge cases

def test_disconnect_then_another_account_can_log_in(db, auth):
    seed(db, subject='user-1')
    agent_connection.disconnect(now=NOW)
    browser_login(auth, db, subject='user-2', with_client_id=False)
    stored = row(db)
    assert stored['status'] == 'connected' and stored['subject'] == 'user-2'
    assert stored['host_id'] == HOST_ID and stored['client_id'] == 'client-1'      # the registration is reused


def test_login_without_a_refresh_token_fails_cleanly(db, auth):
    seed(db, 'needs_login')
    browser_login(auth, db, with_client_id=False, refresh_token=None)
    stored = row(db)
    assert stored['status'] == 'needs_login' and stored['access_token'] is None     # not stuck in pending
    assert events(db)[-1]['kind'] == 'login_failed' and events(db)[-1]['code'] == 'invalid_token_response'
    assert agent_connection._PENDING is None


def test_failed_start_closes_the_listener_and_forgets_the_login(db, auth, monkeypatch):
    seen = {}

    real_start = chatgpt.CallbackServer.start

    def start(self, port=0):
        seen['port'] = real_start(self, port)
        return seen['port']

    def boom(*args, **kwargs):
        raise RuntimeError('commit failed')
    monkeypatch.setattr(chatgpt.CallbackServer, 'start', start)
    monkeypatch.setattr(agent_connection, '_event', boom)
    with pytest.raises(RuntimeError):
        agent_connection.start_login(now=NOW)
    assert agent_connection._PENDING is None
    assert row(db) is None or row(db)['status'] != 'pending'
    with pytest.raises(OSError):
        _get(seen['port'], '/auth/callback?state=x')                  # the listener is closed


def test_stored_tokens_are_scrubbed_after_a_restart(db, auth):
    seed(db)
    with chatgpt._KNOWN_LOCK:
        chatgpt._KNOWN_SECRETS.clear()                                  # a fresh process knows no token yet
    agent_connection.status_view(now=NOW)
    assert OLD_REFRESH not in chatgpt.redact(f'boom {OLD_REFRESH} {OLD_ACCESS}')
    assert OLD_ACCESS not in model.classify_error(RuntimeError(f'x {OLD_ACCESS}'), None)[1]


# ---------------------------------------------------------------------------------------------- models

MODELS = {'models': [{'slug': 'gpt-a', 'display_name': 'GPT A', 'visibility': 'list'},
                     {'slug': 'gpt-hidden', 'display_name': 'Hidden', 'visibility': 'hide'},
                     {'slug': 'gpt-b', 'display_name': 'GPT B', 'visibility': 'list'}]}


def test_models_filter_visibility_list(db, auth):
    seed(db)
    auth.models = MODELS
    assert agent_connection.models() == [{'slug': 'gpt-a', 'display_name': 'GPT A'},
                                         {'slug': 'gpt-b', 'display_name': 'GPT B'}]
    assert auth.calls('/v1/models')[0][2]['Authorization'] == f'Bearer {OLD_ACCESS}'
    db.execute('DELETE FROM agent_connections')
    db.commit()
    with pytest.raises(ChatGPTError) as caught:
        agent_connection.models()
    assert caught.value.code == 'not_connected'


def test_select_model_must_be_listed(db, auth):
    seed(db)
    auth.models = MODELS
    assert agent_connection.select_model('gpt-b', now=NOW) == {'model': 'gpt-b', 'model_display_name': 'GPT B'}
    stored = row(db)
    assert (stored['model'], stored['model_display_name']) == ('gpt-b', 'GPT B')
    assert events(db) == [{'kind': 'model_selected', 'code': None, 'detail': {'protocol': 'siwc-1', 'model': 'gpt-b'}}]
    for bad in ('gpt-hidden', 'gpt-unknown', ''):
        with pytest.raises(ChatGPTError) as caught:
            agent_connection.select_model(bad, now=NOW)
        assert caught.value.code == 'model_not_listed'
    assert row(db)['model'] == 'gpt-b' and len(events(db)) == 1
    view = agent_connection.status_view(now=NOW)
    assert view['model'] == 'gpt-b' and view['model_display_name'] == 'GPT B'


# ---------------------------------------------------------------------------------------------- views

VIEW_KEYS = {'status', 'email_masked', 'scopes_ok', 'access_expires_at', 'refresh_expires_at', 'last_refresh_at',
             'last_error', 'model', 'model_display_name', 'provider_setting', 'pending'}


def test_status_view_shapes(db, auth, monkeypatch):
    empty = agent_connection.status_view(now=NOW)                      # no row at all
    assert empty == {'status': 'disconnected', 'email_masked': None, 'scopes_ok': False, 'access_expires_at': None,
                     'refresh_expires_at': None, 'last_refresh_at': None, 'last_error': None, 'model': None,
                     'model_display_name': None, 'provider_setting': 'chatgpt', 'pending': None}

    seed(db, model_slug='gpt-a')
    connected = agent_connection.status_view(now=NOW)
    assert set(connected) == VIEW_KEYS
    assert connected['status'] == 'connected' and connected['scopes_ok'] is True
    assert connected['email_masked'] == 's***@example.com'
    assert connected['access_expires_at'] == (NOW + timedelta(hours=1)).isoformat()
    assert connected['refresh_expires_at'] == (NOW + timedelta(days=30)).isoformat()
    assert connected['model'] == 'gpt-a' and connected['model_display_name'] == 'GPT-A' and connected['pending'] is None

    db.execute("UPDATE agent_connections SET scopes = '{openid,profile}'")
    db.commit()
    no_sharing = agent_connection.status_view(now=NOW)
    assert no_sharing['status'] == 'connected' and no_sharing['scopes_ok'] is False

    db.execute("UPDATE agent_connections SET status = 'needs_login', access_token = NULL, refresh_token = NULL, "
               "access_expires_at = NULL, refresh_expires_at = NULL, last_error = '连接失效'")
    db.commit()
    needs = agent_connection.status_view(now=NOW)
    assert needs['status'] == 'needs_login' and needs['last_error'] == '连接失效'
    assert needs['scopes_ok'] is False and needs['access_expires_at'] is None and needs['refresh_expires_at'] is None

    agent_connection.start_login(now=NOW)
    pending = agent_connection.status_view(now=NOW)
    assert pending['status'] == 'pending' and pending['pending'] == {
        'expires_at': (NOW + timedelta(seconds=600)).isoformat()}
    agent_connection.cancel_login()

    db.execute("UPDATE agent_connections SET status = 'disconnected', last_error = NULL")
    db.commit()
    disconnected = agent_connection.status_view(now=NOW)
    assert disconnected['status'] == 'disconnected' and disconnected['email_masked'] == 's***@example.com'
    db.execute("UPDATE agent_connections SET email = NULL")
    db.commit()
    assert agent_connection.status_view(now=NOW)['email_masked'] is None
    monkeypatch.setenv('WORKBENCH_AGENT_PROVIDER', 'off')
    assert agent_connection.status_view(now=NOW)['provider_setting'] == 'off'


def test_connection_view_has_no_token_fields(db, auth):
    seed(db, model_slug='gpt-a')
    for _ in range(2):
        view = agent_connection.status_view(now=NOW)
        assert set(view) == VIEW_KEYS
        text = json.dumps(view, ensure_ascii=False)
        for forbidden in ('access_token', 'refresh_token', 'id_token', 'client_id', 'host_id', 'subject', 'user-1',
                          'client-1', HOST_ID, OLD_ACCESS, OLD_REFRESH, 'someone@example.com'):
            assert forbidden not in text
        db.execute("UPDATE agent_connections SET status = 'needs_login', access_token = NULL, refresh_token = NULL, "
                   'access_expires_at = NULL, refresh_expires_at = NULL')
        db.commit()


def test_read_for_settings_and_settings_from_env(db, auth):
    assert model.settings_from_env({}).connection == 'disconnected'     # no row yet
    seed(db, model_slug='gpt-a')
    info = agent_connection.read_for_settings()
    assert info == {'status': 'connected', 'scopes_ok': True, 'model': 'gpt-a', 'secret': OLD_ACCESS}
    settings = model.settings_from_env({})
    assert (settings.provider, settings.model, settings.connection) == ('chatgpt', 'gpt-a', 'connected')
    assert settings.api_key == OLD_ACCESS and OLD_ACCESS not in repr(settings)
    assert model.unavailable_reason(settings) is None
    db.execute("UPDATE agent_connections SET model = NULL")
    db.commit()
    assert model.unavailable_reason(model.settings_from_env({})) == 'model_unavailable'


def test_migration_028_missing_is_reported(workbench_db, monkeypatch):
    import psycopg

    workbench_db.execute('DROP TABLE agent_attempt_resets, agent_connection_events, agent_connections')   # 028 "not run"
    workbench_db.commit()
    schema = workbench_db.execute('SELECT current_schema()').fetchone()[0]
    url = os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']

    @contextmanager
    def connection():
        with psycopg.connect(url, options=f'-c search_path={schema}') as conn:
            yield conn
    monkeypatch.setattr(core_tx, 'connection', connection)
    monkeypatch.setattr(agent_connection, 'read_for_settings', REAL_READ)
    with pytest.raises(ChatGPTError) as caught:
        agent_connection.status_view(now=NOW)
    assert caught.value.code == 'migration_028_missing'
    settings = model.settings_from_env({})                               # never raises
    assert settings.connection == 'unknown' and settings.config_error == '连接状态不可读'
    assert model.unavailable_reason(settings) == 'not_configured'


def test_cli_status(db, capsys):
    seed(db, model_slug='gpt-a')
    assert agent_connection.main(['status']) == 0
    out = capsys.readouterr().out
    assert json.loads(out)['status'] == 'connected' and OLD_ACCESS not in out and OLD_REFRESH not in out


# ---------------------------------------------------------------------------------------------- secrecy

def test_tokens_never_logged_or_returned(db, auth, caplog):
    """Run login, refresh, models, selection, failures and disconnect; no token / code / verifier appears in any log
    record, returned value, exception text, event, or ``last_error``."""
    caplog.set_level(logging.DEBUG)
    returned, raised = [], []

    def attempt(fn, *args, **kwargs):
        try:
            returned.append(fn(*args, **kwargs))
        except Exception as exc:          # noqa: BLE001
            raised.append(f'{type(exc).__name__}: {exc}')
            returned.append({'code': getattr(exc, 'code', None), 'message': getattr(exc, 'message', None)})

    started, query, seen, _ = browser_login(auth, db)
    returned += [{'expires_at': started['expires_at']}, agent_connection.status_view(now=NOW)]
    seen_form = json.dumps(seen['form'])

    db.execute('UPDATE agent_connections SET access_expires_at = %s, earliest_refresh_at = NULL', (NOW + timedelta(seconds=5),))
    db.commit()
    auth.tokens = [refresh_response(auth, access_token='test-access-cccc3333', refresh_token='test-refresh-dddd4444')]
    attempt(agent_connection.access_token, now=NOW + timedelta(seconds=1))
    auth.models = MODELS
    attempt(agent_connection.models)
    attempt(agent_connection.select_model, 'gpt-a', now=NOW)
    attempt(agent_connection.select_model, 'nope', now=NOW)
    returned.append(agent_connection.status_view(now=NOW))

    db.execute('UPDATE agent_connections SET access_expires_at = %s', (NOW + timedelta(seconds=5),))
    db.commit()
    auth.tokens = [(400, {'error': 'refresh_token_reused',
                          'error_description': 'test-refresh-dddd4444 test-access-cccc3333 Bearer test-access-cccc3333'})]
    attempt(agent_connection.access_token, now=NOW + timedelta(seconds=1))
    attempt(agent_connection.access_token, now=NOW + timedelta(seconds=1))
    returned.append(agent_connection.status_view(now=NOW))
    attempt(agent_connection.disconnect, now=NOW)

    classified = model.classify_error(RuntimeError('Authorization: Bearer test-access-cccc3333'), None)
    secrets = [*TOKENS, 'test-access-cccc3333', 'test-refresh-dddd4444', query['nonce'], query['state'], seen_form]
    secrets = [s for s in secrets if s != seen_form] + [seen['form']['code_verifier']]
    haystacks = {
        'log': caplog.text,
        'returned': json.dumps(returned, default=str, ensure_ascii=False),
        'raised': '\n'.join(raised),
        'classified': json.dumps(classified, ensure_ascii=False),
        'events': json.dumps(events(db), default=str, ensure_ascii=False),
        'last_error': str(row(db)['last_error']),
        'urls': started['authorize_url'],
    }
    for name, text in haystacks.items():
        for secret in secrets:
            if name == 'urls' and secret in (query['nonce'], query['state']):
                continue                                               # state and nonce are meant for the browser URL
            assert secret not in text, (name, secret[:12])
    assert raised, 'the failing paths ran'
