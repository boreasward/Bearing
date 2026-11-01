"""The Agent connection API (v2.8 §8.9, §11.1): status, login, cancel, disconnect, models, model choice.

The route tests inject a fake ``agent_connection`` (offline); the last test drives the real module and a fake
authorization server through the routes and checks that no response and no log line ever carries a token, the
``client_id``, the ``host_id``, the subject or the whole e-mail."""
import logging
import re

import psycopg
import pytest

from investment_assistant.api.routes import settings as settings_routes
from investment_assistant.core import tx as core_tx
from investment_assistant.core.llm import connection as agent_connection
from investment_assistant.core.llm import chatgpt
from investment_assistant.core.llm.chatgpt import ChatGPTError, error_for

VIEW_KEYS = {'status', 'email_masked', 'scopes_ok', 'access_expires_at', 'refresh_expires_at', 'last_refresh_at',
             'last_error', 'model', 'model_display_name', 'provider_setting', 'pending'}
SECRET = 'test-access-must-not-leak'


def view(**overrides):
    return {'status': 'connected', 'email_masked': 's***@example.com', 'scopes_ok': True,
            'access_expires_at': '2026-10-29T12:00:00+00:00', 'refresh_expires_at': '2026-11-28T12:00:00+00:00',
            'last_refresh_at': '2026-10-29T11:00:00+00:00', 'last_error': None, 'model': 'gpt-x',
            'model_display_name': 'GPT X', 'provider_setting': 'chatgpt', 'pending': None, **overrides}


class Fake:
    """A stand-in ``agent_connection``: every call is recorded; ``raises[name]`` makes that call fail."""

    def __init__(self):
        self.calls = []
        self.raises = {}
        self.current = view()

    def _go(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if name in self.raises:
            raise self.raises[name]

    def status_view(self):
        self._go('status_view')
        return self.current

    def start_login(self, *, now):
        self._go('start_login', now=now)
        return {'authorize_url': 'https://auth.example.com/authorize?state=abc', 'expires_at': '2026-10-29T12:10:00+00:00'}

    def cancel_login(self):
        self._go('cancel_login')
        return {'cancelled': True}

    def disconnect(self, *, now):
        self._go('disconnect', now=now)
        return {'revoked': True}

    def models(self):
        self._go('models')
        return [{'slug': 'gpt-x', 'display_name': 'GPT X'}, {'slug': 'gpt-y', 'display_name': 'GPT Y'}]

    def select_model(self, slug, *, now):
        self._go('select_model', slug, now=now)
        return {'model': slug, 'model_display_name': slug.upper()}

    def names(self):
        return [name for name, _, _ in self.calls]


@pytest.fixture
def fake(monkeypatch):
    stand_in = Fake()
    monkeypatch.setattr(settings_routes, 'agent_connection', stand_in)
    return stand_in


def test_status_is_the_view(client, fake):
    resp = client.get('/api/settings/agent-connection')
    assert resp.status_code == 200 and resp.json() == view() and set(resp.json()) == VIEW_KEYS
    fake.current = view(status='pending', pending={'expires_at': '2026-10-29T12:10:00+00:00'})
    assert client.get('/api/settings/agent-connection').json()['pending'] == {'expires_at': '2026-10-29T12:10:00+00:00'}


def test_login_returns_the_authorize_url(client, fake):
    resp = client.post('/api/settings/agent-connection/login', json={'request_id': 'ignored'})
    assert resp.status_code == 200 and set(resp.json()) == {'authorize_url', 'expires_at'}
    assert fake.names() == ['status_view', 'start_login']
    assert client.post('/api/settings/agent-connection/login').status_code == 200          # no body at all


def test_login_while_one_is_pending_is_409(client, fake):
    fake.current = view(status='pending', pending={'expires_at': 'x'})
    resp = client.post('/api/settings/agent-connection/login', json={})
    assert resp.status_code == 409 and resp.json() == {'error': 'login_pending'}
    assert 'start_login' not in fake.names()                                # nothing was started or cancelled
    assert client.post('/api/settings/agent-connection/cancel', json={}).json() == {'cancelled': True}


@pytest.mark.parametrize('code, status', [('discovery_failed', 502), ('callback_port_unavailable', 502),
                                          ('network_error', 502), ('migration_028_missing', 503)])
def test_login_errors_carry_only_the_code(client, fake, code, status):
    fake.raises['start_login'] = ChatGPTError(code, f'{SECRET} fixed message')
    resp = client.post('/api/settings/agent-connection/login', json={})
    assert resp.status_code == status and resp.json() == {'error': code}
    assert SECRET not in resp.text


def test_cancel_and_disconnect(client, fake):
    assert client.post('/api/settings/agent-connection/cancel', json={}).json() == {'cancelled': True}
    assert client.delete('/api/settings/agent-connection').json() == {'revoked': True}
    assert fake.names() == ['cancel_login', 'status_view', 'disconnect']
    fake.current = view(status='needs_login')                               # a lost connection can be dropped too
    assert client.delete('/api/settings/agent-connection').status_code == 200
    fake.current = view(status='pending', pending={'expires_at': 'x'})      # so can a login still waiting
    assert client.delete('/api/settings/agent-connection').status_code == 200


def test_disconnect_without_a_connection_is_409(client, fake):
    fake.current = view(status='disconnected', pending=None, email_masked=None)
    resp = client.delete('/api/settings/agent-connection')
    assert resp.status_code == 409 and resp.json() == {'error': 'not_connected'}
    assert 'disconnect' not in fake.names()


def test_models(client, fake):
    resp = client.get('/api/settings/agent-connection/models')
    assert resp.status_code == 200 and resp.json() == {'models': [{'slug': 'gpt-x', 'display_name': 'GPT X'},
                                                                  {'slug': 'gpt-y', 'display_name': 'GPT Y'}]}
    fake.raises['models'] = error_for('not_connected')
    resp = client.get('/api/settings/agent-connection/models')
    assert resp.status_code == 409 and resp.json() == {'error': 'not_connected'}
    fake.raises['models'] = ChatGPTError('subscription_sharing_usage_unavailable', f'{SECRET} text')
    resp = client.get('/api/settings/agent-connection/models')
    assert resp.status_code == 502 and resp.json() == {'error': 'subscription_sharing_usage_unavailable'}


def test_select_model(client, fake):
    resp = client.put('/api/settings/agent-connection/model', json={'slug': 'gpt-y', 'request_id': 'ignored'})
    assert resp.status_code == 200 and resp.json() == {'model': 'gpt-y', 'model_display_name': 'GPT-Y'}
    assert fake.calls[-1][:2] == ('select_model', ('gpt-y',))
    for body in ({}, {'slug': ''}, {'slug': '  '}, {'slug': 3}, {'slug': None}):
        assert client.put('/api/settings/agent-connection/model', json=body).status_code == 400
    fake.raises['select_model'] = error_for('model_not_listed')
    resp = client.put('/api/settings/agent-connection/model', json={'slug': 'nope'})
    assert resp.status_code == 400 and resp.json() == {'error': 'model_not_listed'}
    fake.raises['select_model'] = error_for('not_connected')
    assert client.put('/api/settings/agent-connection/model', json={'slug': 'gpt-x'}).status_code == 409


@pytest.mark.parametrize('error', [core_tx.Unavailable('数据库未配置'), psycopg.OperationalError('connection refused'),
                                   ChatGPTError('migration_028_missing', '迁移 028 未执行。')])
@pytest.mark.parametrize('method, path, body', [
    ('get', '/api/settings/agent-connection', None),
    ('post', '/api/settings/agent-connection/login', {}),
    ('post', '/api/settings/agent-connection/cancel', {}),
    ('delete', '/api/settings/agent-connection', None),
    ('get', '/api/settings/agent-connection/models', None),
    ('put', '/api/settings/agent-connection/model', {'slug': 'gpt-x'}),
])
def test_database_not_ready_is_503(client, fake, error, method, path, body):
    for name in ('status_view', 'start_login', 'cancel_login', 'disconnect', 'models', 'select_model'):
        fake.raises[name] = error
    resp = getattr(client, method)(path, **({} if body is None else {'json': body}))
    assert resp.status_code == 503 and set(resp.json()) == {'error'}
    assert 'connection refused' not in resp.text                      # the driver text is not echoed


def test_connection_routes_and_methods(client):
    paths = client.get('/openapi.json').json()['paths']
    assert set(paths['/api/settings/agent-connection']) == {'get', 'delete'}
    assert set(paths['/api/settings/agent-connection/login']) == {'post'}
    assert set(paths['/api/settings/agent-connection/cancel']) == {'post'}
    assert set(paths['/api/settings/agent-connection/models']) == {'get'}
    assert set(paths['/api/settings/agent-connection/model']) == {'put'}


# ---------------------------------------------------------------------------------------------- the real module

from test_agent_chatgpt_oauth import NOW, _get, auth, id_token, token_body  # noqa: E402,F401  (auth is a fixture)
from test_agent_connection import db, wait_for  # noqa: E402,F401  (db is a fixture)


def test_settings_agent_connection_api_never_returns_tokens(client, db, auth, caplog, monkeypatch):
    """Log in, read, list, choose, cancel and disconnect through the routes against the real connection module: no
    body and no log line holds a token, the ``client_id``, the ``host_id``, the subject or the whole e-mail."""
    from urllib.parse import parse_qs, urlsplit

    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(settings_routes, '_now', lambda: NOW)       # the fake tokens are dated around NOW
    auth.models = {'models': [{'slug': 'gpt-x', 'display_name': 'GPT X', 'visibility': 'list'}]}
    bodies = []

    def call(method, path, **kwargs):
        resp = getattr(client, method)(path, **kwargs)
        bodies.append(resp.text)
        return resp

    login = call('post', '/api/settings/agent-connection/login', json={'request_id': 'r1'})
    assert login.status_code == 200
    query = {k: v[0] for k, v in parse_qs(urlsplit(login.json()['authorize_url']).query).items()}
    auth.tokens = [(200, token_body(auth.base, id_token=id_token(auth.base, aud='client-from-callback', sub='user-1',
                                                                 nonce=query['nonce'])))]
    port = int(urlsplit(query['redirect_uri']).port)
    pending = call('get', '/api/settings/agent-connection').json()
    assert pending['status'] == 'pending' and set(pending) == VIEW_KEYS
    assert call('post', '/api/settings/agent-connection/login', json={}).json() == {'error': 'login_pending'}
    assert _get(port, f"/auth/callback?state={query['state']}&code=test-code-1&client_id=client-from-callback")[0] == 200
    wait_for(lambda: agent_connection._PENDING is None)

    status = call('get', '/api/settings/agent-connection').json()
    assert status['status'] == 'connected' and set(status) == VIEW_KEYS and status['email_masked'] == 's***@example.com'
    assert call('get', '/api/settings/agent-connection/models').json() == {
        'models': [{'slug': 'gpt-x', 'display_name': 'GPT X'}]}
    assert call('put', '/api/settings/agent-connection/model', json={'slug': 'gpt-x'}).json() == {
        'model': 'gpt-x', 'model_display_name': 'GPT X'}
    assert call('put', '/api/settings/agent-connection/model', json={'slug': 'gone'}).json() == {
        'error': 'model_not_listed'}
    assert call('get', '/api/settings/agent-connection').json()['model'] == 'gpt-x'
    assert call('post', '/api/settings/agent-connection/cancel', json={}).json() == {'cancelled': False}
    assert call('delete', '/api/settings/agent-connection').json() == {'revoked': True}
    after = call('get', '/api/settings/agent-connection').json()
    assert after['status'] == 'disconnected' and after['email_masked'] is None
    assert call('delete', '/api/settings/agent-connection').json() == {'error': 'not_connected'}
    assert call('get', '/api/settings/agent-connection/models').json() == {'error': 'not_connected'}

    host_id = db.execute('SELECT host_id FROM agent_connections').fetchone()[0]
    db.commit()
    secrets_ = ['test-access-aaaa1111', 'test-refresh-bbbb2222', 'test-code-1', query['code_challenge'],
                query['state'], query['nonce'], 'client-from-callback', host_id, 'user-1', 'someone@example.com']
    login_body, rest = bodies[0], bodies[1:]
    for text in rest:                                   # everything after the login response, header-free and complete
        for secret in secrets_:
            assert secret not in text, secret
    for secret in secrets_[:3] + ['someone@example.com', 'user-1']:
        assert secret not in login_body, secret         # the URL is the one body that must carry the OAuth parameters
    assert re.fullmatch(r'urn:uuid:[0-9a-f-]{36}', host_id)
    logged = '\n'.join(f'{r.name} {r.getMessage()}' for r in caplog.records)
    for secret in secrets_:
        assert secret not in logged, secret
    assert chatgpt.CHATGPT_PROTOCOL_VERSION == 'siwc-1'
