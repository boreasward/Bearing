"""Sign in with ChatGPT protocol (v2.8 spec §8.1, §8.2, §8.5; devkit ``oauth.ts`` / ``errors.ts``).

Offline: a fake authorization server on a random 127.0.0.1 port (``chatgpt.ISSUER`` / ``RESOURCE`` point at it), plain
HTTP to the loopback listener. Every token here is an obvious placeholder; nothing reaches a real host."""
import base64
import http.client
import http.server
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from investment_assistant.core.llm import chatgpt
from investment_assistant.core.llm.chatgpt import ChatGPTError

NOW = datetime(2026, 10, 29, 3, 30, tzinfo=UTC)


# ---------------------------------------------------------------------------------------------- fake server

class FakeAuth:
    """The authorization server + resource server in one: scripted token / revocation / models answers."""

    def __init__(self):
        self.requests = []                  # (method, path, headers, form)
        self.discovery = {}
        self.tokens = []                    # (status, body) answers for POST /oauth/token, the last one repeats
        self.revoke_status = 200
        self.models = {'models': []}
        self.models_status = 200
        self.discovery_status = 200
        self.token_headers = {}
        self.token_hook = None              # called inside the token request, before the answer
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def _send(self, status, body, headers=None):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(raw)

            def _record(self):
                length = int(self.headers.get('Content-Length') or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()} if length else {}
                server.requests.append((self.command, self.path, dict(self.headers), form))
                return form

            def do_GET(self):               # noqa: N802
                self._record()
                if self.path == '/.well-known/openid-configuration':
                    self._send(server.discovery_status, server.discovery)
                elif self.path == '/v1/models':
                    self._send(server.models_status, server.models)
                elif self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', 'http://127.0.0.1:9/elsewhere')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                else:
                    self._send(404, {})

            def do_POST(self):              # noqa: N802
                form = self._record()
                if self.path == '/oauth/token':
                    if server.token_hook is not None:
                        server.token_hook()
                    index = min(sum(1 for r in server.requests if r[1] == '/oauth/token') - 1, len(server.tokens) - 1)
                    entry = server.tokens[index]
                    status, body = entry(form) if callable(entry) else entry      # a callable sees the form
                    self._send(status, body, server.token_headers)
                elif self.path == '/oauth/revoke':
                    self._send(server.revoke_status, {})
                else:
                    self._send(404, {})

        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        self.base = f'http://127.0.0.1:{self.httpd.server_address[1]}'
        self.discovery = {
            'issuer': self.base, 'authorization_endpoint': f'{self.base}/oauth/authorize',
            'token_endpoint': f'{self.base}/oauth/token', 'jwks_uri': f'{self.base}/jwks',
            'revocation_endpoint': f'{self.base}/oauth/revoke'}
        threading.Thread(target=self.httpd.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True).start()

    def calls(self, path):
        return [r for r in self.requests if r[1] == path]


@pytest.fixture
def auth(monkeypatch):
    for name in ('http_proxy', 'https_proxy', 'all_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('NO_PROXY', '127.0.0.1')
    monkeypatch.setenv('no_proxy', '127.0.0.1')
    server = FakeAuth()
    monkeypatch.setattr(chatgpt, 'ISSUER', server.base)
    monkeypatch.setattr(chatgpt, 'RESOURCE', f'{server.base}/v1')
    sleeps = []
    monkeypatch.setattr(chatgpt, 'SLEEP', sleeps.append)
    server.sleeps = sleeps
    chatgpt.clear_discovery_cache()
    yield server
    chatgpt.clear_discovery_cache()
    server.httpd.shutdown()
    server.httpd.server_close()


def id_token(issuer, **claims):
    def part(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b'=').decode()
    body = {'iss': issuer, 'aud': 'client-1', 'sub': 'user-1', 'exp': int((NOW + timedelta(hours=1)).timestamp()),
            'email': 'someone@example.com', **claims}
    return f"{part({'alg': 'RS256'})}.{part({k: v for k, v in body.items() if v is not None})}.c2ln"


def token_body(issuer, **overrides):
    body = {'access_token': 'test-access-aaaa1111', 'refresh_token': 'test-refresh-bbbb2222', 'token_type': 'Bearer',
            'expires_in': 3600, 'scope': chatgpt.SCOPES, 'id_token': id_token(issuer, nonce='nonce-1')}
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None}


def exchange(auth, **overrides):
    kwargs = dict(discovery=chatgpt.discover(), client_id='client-1', code='code-1', verifier='verifier-1',
                  redirect_uri='http://127.0.0.1:1/auth/callback', nonce='nonce-1', subject=None, now=NOW)
    kwargs.update(overrides)
    return chatgpt.exchange_code(**kwargs)


# ---------------------------------------------------------------------------------------------- discovery

def test_constants_are_the_documented_ones():
    assert chatgpt.CHATGPT_PROTOCOL_VERSION == 'siwc-1'
    assert chatgpt.SCOPES == 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
    assert chatgpt.SHARING_SCOPE == 'chatgpt.tokens.use.direct'
    assert (chatgpt.CALLBACK_PATH, chatgpt.DYNAMIC_CLIENT, chatgpt.APP_NAME, chatgpt.LOGIN_TTL_S) == (
        '/auth/callback', 'dynamic_agent_client', 'investment-assistant', 600)
    assert chatgpt.REAUTH_CODES == {'invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired',
                                    'refresh_token_invalidated', 'refresh_token_reused', 'invalid_id_token',
                                    'account_mismatch'}


def test_defaults_are_the_real_endpoints():
    # the module constants, not the fixture's: nothing here may point anywhere but the documented hosts
    source = open(chatgpt.__file__, encoding='utf-8').read()
    assert "ISSUER = 'https://auth.openai.com'" in source and "RESOURCE = 'https://api.openai.com/v1'" in source


def test_discovery_validated(auth):
    document = chatgpt.discover()
    assert document['token_endpoint'] == f'{auth.base}/oauth/token'
    assert chatgpt.discover() is document and len(auth.calls('/.well-known/openid-configuration')) == 1   # cached

    bad_documents = {
        'issuer mismatch': {**auth.discovery, 'issuer': 'https://evil.example'},
        'authorization elsewhere': {**auth.discovery, 'authorization_endpoint': 'https://evil.example/authorize'},
        'token elsewhere': {**auth.discovery, 'token_endpoint': 'http://127.0.0.1:9/oauth/token'},
        'jwks elsewhere': {**auth.discovery, 'jwks_uri': 'https://evil.example/jwks'},
        'revocation elsewhere': {**auth.discovery, 'revocation_endpoint': 'https://evil.example/revoke'},
        'token missing': {k: v for k, v in auth.discovery.items() if k != 'token_endpoint'},
        'jwks missing': {k: v for k, v in auth.discovery.items() if k != 'jwks_uri'},
        'not an object': ['x'],
    }
    for label, bad in bad_documents.items():
        chatgpt.clear_discovery_cache()
        auth.discovery = bad
        with pytest.raises(ChatGPTError) as caught:
            chatgpt.discover()
        assert caught.value.code == 'discovery_failed', label

    chatgpt.clear_discovery_cache()
    auth.discovery = {k: v for k, v in bad_documents['token missing'].items()}
    auth.discovery['token_endpoint'] = f'{auth.base}/oauth/token'
    del auth.discovery['revocation_endpoint']                   # revocation_endpoint is optional
    assert 'revocation_endpoint' not in chatgpt.discover()


def test_discovery_failure_is_not_cached_and_5xx_is_retried(auth):
    auth.discovery_status = 503
    with pytest.raises(ChatGPTError) as caught:
        chatgpt.discover()
    assert caught.value.code == 'discovery_failed'
    assert len(auth.calls('/.well-known/openid-configuration')) == 3 and auth.sleeps == [2, 8]   # fetch-1 for a GET
    auth.discovery_status = 200
    assert chatgpt.discover()['issuer'] == auth.base


def test_grant_post_is_not_replayed_after_a_5xx(auth):
    auth.tokens = [(503, {'error': 'server_error'})]
    with pytest.raises(ChatGPTError) as caught:
        exchange(auth)
    assert len(auth.calls('/oauth/token')) == 1 and caught.value.retryable and caught.value.status == 503
    assert auth.sleeps == []


def test_grant_post_retries_429_within_the_cap(auth):
    auth.token_headers = {'Retry-After': '1'}
    auth.tokens = [(429, {'error': 'rate_limit'}), (200, token_body(auth.base))]
    assert exchange(auth)['access_token'] == 'test-access-aaaa1111'
    assert len(auth.calls('/oauth/token')) == 2 and auth.sleeps == [1]


def test_conftest_keeps_chatgpt_http_on_the_loopback():
    with pytest.raises(pytest.fail.Exception):
        chatgpt._http('GET', 'https://auth.openai.com/.well-known/openid-configuration', None, None, chatgpt.STANDARD_POLICY)


def test_redirects_are_errors_not_followed(auth):
    status, body = chatgpt._call('redirect', 'GET', f'{auth.base}/redirect')
    assert status == 302 and body is None and not auth.calls('/elsewhere')


# ---------------------------------------------------------------------------------------------- authorize URL

def _query(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}


def test_authorize_url_params(auth):
    pkce = chatgpt.new_pkce()
    assert len({pkce['state'], pkce['nonce'], pkce['verifier']}) == 3
    challenge = base64.urlsafe_b64encode(__import__('hashlib').sha256(pkce['verifier'].encode()).digest())
    assert pkce['challenge'] == challenge.rstrip(b'=').decode()
    assert all(len(base64.urlsafe_b64decode(pkce[k] + '=' * (-len(pkce[k]) % 4))) == 32
               for k in ('state', 'nonce', 'verifier'))
    host_id = 'urn:uuid:123e4567-e89b-12d3-a456-426614174000'
    common = dict(discovery=chatgpt.discover(), redirect_uri='http://127.0.0.1:5555/auth/callback',
                  state=pkce['state'], nonce=pkce['nonce'], challenge=pkce['challenge'], host_id=host_id)

    first = chatgpt.authorize_url(client_id=None, first=True, **common)
    assert first.startswith(f'{auth.base}/oauth/authorize?')
    assert _query(first) == {
        'client_id': 'dynamic_agent_client', 'response_type': 'code',
        'redirect_uri': 'http://127.0.0.1:5555/auth/callback', 'scope': chatgpt.SCOPES, 'resource': auth.base + '/v1',
        'state': pkce['state'], 'nonce': pkce['nonce'], 'code_challenge_method': 'S256',
        'code_challenge': pkce['challenge'], 'ext_agent_host_id': host_id, 'agent_name_hint': 'investment-assistant'}

    again = _query(chatgpt.authorize_url(client_id='client-1', email_hint='someone@example.com', **common))
    assert again['client_id'] == 'client-1' and again['login_hint'] == 'someone@example.com'
    assert 'agent_name_hint' not in again
    assert 'login_hint' not in _query(chatgpt.authorize_url(client_id='client-1', **common))
    # the verifier is never in the URL
    assert pkce['verifier'] not in first


# ---------------------------------------------------------------------------------------------- loopback callback

def _get(port, path, *, host=None, method='GET'):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader('Host', host or f'127.0.0.1:{port}')
    conn.endheaders()
    response = conn.getresponse()
    body = response.read()
    headers = {k.lower(): v for k, v in response.getheaders()}
    conn.close()
    return response.status, headers, body


@pytest.fixture
def listener():
    servers = []

    def make(state='state-1', saved=None):
        server = chatgpt.CallbackServer(state, saved)
        server.start(0)
        servers.append(server)
        return server
    yield make
    for server in servers:
        server.close()


def test_port_zero_selected(listener):
    first, second = listener(), listener()
    assert first.port > 0 and second.port > 0 and first.port != second.port
    assert first.redirect_uri == f'http://127.0.0.1:{first.port}/auth/callback'
    taken = chatgpt.CallbackServer('s')
    with pytest.raises(ChatGPTError) as caught:
        taken.start(first.port)                                 # a fixed port that is busy
    assert caught.value.code == 'callback_port_unavailable'


def test_callback_host_state_and_duplicates(listener):
    server = listener('state-1')
    port, path = server.port, '/auth/callback'
    good = f'{path}?state=state-1&code=code-1&client_id=client-1'

    assert _get(port, good, host='evil.example')[0] == 404                       # wrong Host
    assert _get(port, good, host=f'127.0.0.1:{port + 1}')[0] == 404
    assert _get(port, good, host=f'localhost:{port}')[0] == 404
    assert _get(port, f'/other?state=state-1&code=c')[0] == 404                  # wrong path
    assert _get(port, good, method='POST')[0] == 404                             # wrong method
    assert _get(port, f'{path}?code=c&client_id=client-1')[0] == 400             # no state
    assert _get(port, f'{path}?state=nope&code=c&client_id=client-1')[0] == 400  # wrong state
    assert _get(port, f'{path}?state=state-1&state=state-1&code=c&client_id=client-1')[0] == 400   # duplicated state
    assert _get(port, f'{path}?state=state-1&state=other&code=c&client_id=client-1')[0] == 400
    assert not server._done.is_set()                                             # none of those consumed it

    status, headers, body = _get(port, good)
    assert status == 200 and '已完成，可以关闭此标签页'.encode() in body
    assert headers['cache-control'] == 'no-store' and headers['referrer-policy'] == 'no-referrer'
    assert "default-src 'none'" in headers['content-security-policy'] and 'sha256-' in headers['content-security-policy']
    assert headers['content-type'].startswith('text/html')
    assert server.wait(1) == {'code': 'code-1', 'client_id': 'client-1'}
    assert _get(port, good)[0] == 404                                            # settled: a replay is refused


def test_callback_error_param(listener):
    server = listener()
    status, _, body = _get(server.port, '/auth/callback?state=state-1&error=access_denied&error_description=x')
    assert status == 200 and '已完成'.encode() not in body
    assert server.wait(1) == {'error': 'access_denied'}
    odd = listener()
    _get(odd.port, '/auth/callback?state=state-1&error=%3Cscript%3E')
    assert odd.wait(1) == {'error': 'api_error'}                                 # not a safe identifier


@pytest.mark.parametrize('query,saved,expected', [
    ('code=c1', None, {'error': 'registration_incomplete'}),                          # no client_id anywhere
    ('code=c1&client_id=dynamic_agent_client', None, {'error': 'registration_incomplete'}),
    ('code=c1', 'dynamic_agent_client', {'error': 'registration_incomplete'}),
    ('code=c1&client_id=other', 'saved', {'error': 'registration_incomplete'}),      # differs from the saved one
    ('code=c1&client_id=bad%20id', None, {'error': 'registration_incomplete'}),
    ('code=c1&client_id=' + 'a' * 201, None, {'error': 'registration_incomplete'}),
    ('code=c1&client_id=a&client_id=b', None, {'error': 'registration_incomplete'}),   # duplicated client_id
    ('code=c1&code=c2&client_id=a', None, {'error': 'registration_incomplete'}),        # duplicated code
    ('client_id=a', None, {'error': 'registration_incomplete'}),                      # no code
    ('code=c1&client_id=a%0A', None, {'error': 'registration_incomplete'}),          # a trailing newline is no client_id
    ('code=c1&client_id=client_1-A', None, {'code': 'c1', 'client_id': 'client_1-A'}),
    ('code=c1&client_id=saved', 'saved', {'code': 'c1', 'client_id': 'saved'}),
    ('code=c1', 'saved', {'code': 'c1', 'client_id': 'saved'}),                       # the saved registration is reused
])
def test_callback_client_id_rules(listener, query, saved, expected):
    server = listener('state-1', saved)
    _get(server.port, f'/auth/callback?state=state-1&{query}')
    assert server.wait(1) == expected


def test_login_expires_after_ttl(listener):
    server = listener()
    started = time.monotonic()
    assert server.wait(0.2) == {'error': 'login_expired'}
    assert time.monotonic() - started < 2


def test_cancel_login(listener):
    server = listener()
    port = server.port
    waiter = []
    thread = threading.Thread(target=lambda: waiter.append(server.wait(30)))
    thread.start()
    server.close()
    thread.join(5)
    assert waiter == [{'error': 'cancelled'}]
    with pytest.raises(OSError):
        _get(port, '/auth/callback?state=state-1&code=c&client_id=a')           # the listener is gone
    server.close()                                                               # idempotent


# ---------------------------------------------------------------------------------------------- tokens

def test_token_exchange_form_and_result(auth):
    auth.tokens = [(200, token_body(auth.base, earliest_refresh_at=int((NOW + timedelta(minutes=30)).timestamp())))]
    fields = exchange(auth, subject='user-1')
    (_, _, headers, form), = auth.calls('/oauth/token')
    assert form == {'grant_type': 'authorization_code', 'client_id': 'client-1', 'code': 'code-1',
                    'code_verifier': 'verifier-1', 'redirect_uri': 'http://127.0.0.1:1/auth/callback',
                    'resource': auth.base + '/v1'}
    assert headers['Content-Type'] == 'application/x-www-form-urlencoded'
    assert fields['access_token'] == 'test-access-aaaa1111' and fields['refresh_token'] == 'test-refresh-bbbb2222'
    assert fields['expires_in'] == 3600.0 and fields['scopes'] == chatgpt.SCOPES.split()
    assert fields['subject'] == 'user-1' and fields['email'] == 'someone@example.com'
    assert fields['earliest_refresh_at'] == NOW + timedelta(minutes=30)
    assert 'id_token' not in fields


@pytest.mark.parametrize('label,overrides', [
    ('no refresh token', {'refresh_token': None}),
    ('empty refresh token', {'refresh_token': ''}),
    ('no access token', {'access_token': None}),
    ('expires_in zero', {'expires_in': 0}),
    ('expires_in negative', {'expires_in': -5}),
    ('expires_in text', {'expires_in': '3600'}),
    ('expires_in bool', {'expires_in': True}),
    ('token_type mac', {'token_type': 'mac'}),
    ('no token_type', {'token_type': None}),
    ('scope missing', {'scope': None}),
    ('scope not a string', {'scope': ['openid']}),
])
def test_token_response_validation(auth, label, overrides):
    auth.tokens = [(200, token_body(auth.base, **overrides))]
    with pytest.raises(ChatGPTError) as caught:
        exchange(auth)
    assert caught.value.code == 'invalid_token_response', label
    assert 'test-access' not in str(caught.value) and 'test-refresh' not in str(caught.value)


def test_token_response_other_shapes(auth):
    for body in ([], 'text', None):
        auth.tokens = [(200, body)]
        with pytest.raises(ChatGPTError) as caught:
            exchange(auth)
        assert caught.value.code == 'invalid_token_response'
    auth.tokens = [(200, token_body(auth.base, token_type='bearer', id_token=None))]
    with pytest.raises(ChatGPTError) as caught:
        exchange(auth)
    assert caught.value.code == 'invalid_id_token'
    auth.tokens = [(400, {'error': 'invalid_grant', 'error_description': 'the code test-refresh-bbbb2222 is spent'})]
    with pytest.raises(ChatGPTError) as caught:
        exchange(auth)
    assert caught.value.code == 'invalid_grant' and 'spent' not in str(caught.value)
    # the code exchange must return a refresh token whatever the scope says (spec §8.1 step 4)
    auth.tokens = [(200, token_body(auth.base, scope='openid profile', refresh_token=None))]
    with pytest.raises(ChatGPTError) as caught:
        exchange(auth)
    assert caught.value.code == 'invalid_token_response'


def test_id_token_claims():
    def check(**claims):
        base = dict(client_id='client-1', nonce='nonce-1', subject='user-1', now=NOW)
        return chatgpt.check_id_token(id_token(chatgpt.ISSUER, **{'nonce': 'nonce-1', **claims}), **base)

    assert check() == {'subject': 'user-1', 'email': 'someone@example.com'}
    assert check(email=None)['email'] is None
    assert check(aud=['client-1'])['subject'] == 'user-1'
    assert check(aud=['client-1', 'other'], azp='client-1')['subject'] == 'user-1'
    for label, claims in {
        'iss': {'iss': 'https://evil.example'}, 'aud': {'aud': 'other-client'},
        'aud list without azp': {'aud': ['client-1', 'other']}, 'azp mismatch': {'azp': 'other-client'},
        'exp past': {'exp': int((NOW - timedelta(minutes=1)).timestamp())}, 'exp missing': {'exp': None},
        'exp text': {'exp': 'soon'}, 'nonce': {'nonce': 'another'}, 'sub missing': {'sub': None},
        'sub empty': {'sub': ''},
    }.items():
        with pytest.raises(ChatGPTError) as caught:
            check(**claims)
        assert caught.value.code == 'invalid_id_token', label
    with pytest.raises(ChatGPTError) as caught:
        check(sub='someone-else')
    assert caught.value.code == 'account_mismatch'
    for junk in ('', 'x', 'a.b.c', 'a.' + base64.urlsafe_b64encode(b'[1]').decode() + '.c', None):
        with pytest.raises(ChatGPTError) as caught:
            chatgpt.check_id_token(junk, client_id='client-1', now=NOW)
        assert caught.value.code == 'invalid_id_token'
    # a refresh has no nonce to compare
    assert chatgpt.check_id_token(id_token(chatgpt.ISSUER), client_id='client-1', subject='user-1', now=NOW)


def test_refresh_grant(auth):
    auth.tokens = [(200, token_body(auth.base, scope=None, id_token=id_token(auth.base), earliest_refresh_at=None))]
    fields = chatgpt.refresh(discovery=chatgpt.discover(), client_id='client-1', refresh_token='test-refresh-old',
                             previous_scope='openid chatgpt.tokens.use.direct', subject='user-1', now=NOW)
    (_, _, _, form), = auth.calls('/oauth/token')
    assert form == {'grant_type': 'refresh_token', 'client_id': 'client-1', 'refresh_token': 'test-refresh-old',
                    'resource': auth.base + '/v1'}
    assert fields['scopes'] == ['openid', 'chatgpt.tokens.use.direct']        # scope omitted: the previous one stays
    assert fields['earliest_refresh_at'] is None and fields['refresh_token'] == 'test-refresh-bbbb2222'

    auth.tokens = [(200, {**token_body(auth.base, id_token=id_token(auth.base)), 'scope': None})]   # null = omitted
    fields = chatgpt.refresh(discovery=chatgpt.discover(), client_id='client-1', refresh_token='x',
                             previous_scope='openid chatgpt.tokens.use.direct', subject='user-1', now=NOW)
    assert fields['scopes'] == ['openid', 'chatgpt.tokens.use.direct']

    auth.tokens = [(200, token_body(auth.base, refresh_token=None))]
    with pytest.raises(ChatGPTError) as caught:                                # a refresh must return a refresh token
        chatgpt.refresh(discovery=chatgpt.discover(), client_id='client-1', refresh_token='x',
                        previous_scope='openid', subject=None, now=NOW)
    assert caught.value.code == 'invalid_token_response'

    auth.tokens = [(200, token_body(auth.base, id_token=id_token(auth.base, sub='someone-else')))]
    with pytest.raises(ChatGPTError) as caught:
        chatgpt.refresh(discovery=chatgpt.discover(), client_id='client-1', refresh_token='x',
                        previous_scope='openid', subject='user-1', now=NOW)
    assert caught.value.code == 'account_mismatch'


@pytest.mark.parametrize('value,expected', [
    (1_800_000_000, datetime.fromtimestamp(1_800_000_000, UTC)),
    (1_800_000_000.5, datetime.fromtimestamp(1_800_000_000.5, UTC)),
    ('1800000000', datetime.fromtimestamp(1_800_000_000, UTC)),
    ('2026-10-29T04:00:00Z', datetime(2026, 10, 29, 4, tzinfo=UTC)),
    ('2026-10-29T12:00:00+08:00', datetime(2026, 10, 29, 4, tzinfo=UTC)),
    ('2026-10-29T04:00:00', datetime(2026, 10, 29, 4, tzinfo=UTC)),
    (None, None), ('', None), ('soon', None), (True, None), (float('inf'), None), (1e30, None), ({}, None),
])
def test_earliest_refresh_at_parsing(value, expected):
    assert chatgpt.parse_earliest_refresh_at(value) == expected


def test_revoke(auth):
    discovery = chatgpt.discover()
    assert chatgpt.revoke(discovery=discovery, client_id='client-1', refresh_token='test-refresh-bbbb2222') is True
    (_, _, _, form), = auth.calls('/oauth/revoke')
    assert form == {'token': 'test-refresh-bbbb2222', 'token_type_hint': 'refresh_token', 'client_id': 'client-1'}
    auth.revoke_status = 400
    assert chatgpt.revoke(discovery=discovery, client_id='c', refresh_token='t') is False
    auth.revoke_status = 503                                                     # retried once, then given up
    before = len(auth.calls('/oauth/revoke'))
    assert chatgpt.revoke(discovery=discovery, client_id='c', refresh_token='t') is False
    assert len(auth.calls('/oauth/revoke')) - before == 2
    assert chatgpt.revoke(discovery={k: v for k, v in discovery.items() if k != 'revocation_endpoint'},
                          client_id='c', refresh_token='t') is False
    assert chatgpt.revoke(discovery={**discovery, 'revocation_endpoint': 'http://127.0.0.1:9/revoke'},
                          client_id='c', refresh_token='t') is False             # unreachable: False, not an exception


def test_list_models(auth):
    auth.models = {'models': [
        {'slug': 'gpt-a', 'display_name': 'GPT A', 'visibility': 'list'},
        {'slug': 'gpt-hidden', 'display_name': 'Hidden', 'visibility': 'hide'},
        {'slug': 'gpt-b', 'display_name': 'GPT B', 'visibility': 'list', 'extra': 1}]}
    assert chatgpt.list_models('test-access-aaaa1111') == [{'slug': 'gpt-a', 'display_name': 'GPT A'},
                                                          {'slug': 'gpt-b', 'display_name': 'GPT B'}]
    (_, _, headers, _), = auth.calls('/v1/models')
    assert headers['Authorization'] == 'Bearer test-access-aaaa1111'
    for bad in ({'data': []}, {'models': 'x'}, {'models': [{'slug': '', 'display_name': 'x', 'visibility': 'list'}]},
                {'models': [{'slug': 'a', 'visibility': 'list'}]}, []):
        auth.models = bad
        with pytest.raises(ChatGPTError) as caught:
            chatgpt.list_models('t')
        assert caught.value.code == 'invalid_model_catalog'
    auth.models_status, auth.models = 401, {'error': {'code': 'invalid_token'}}
    with pytest.raises(ChatGPTError) as caught:
        chatgpt.list_models('t')
    assert caught.value.code == 'invalid_token' and caught.value.status == 401


# ---------------------------------------------------------------------------------------------- request rewrite

SYSTEM = {'role': 'system', 'content': '你是投研助手。', 'type': 'message'}
USER = {'role': 'user', 'content': '问题', 'type': 'message'}
FUNCTION = {'type': 'function', 'name': 'get_price', 'description': 'd', 'parameters': {'type': 'object'}}
BASE = {'model': 'gpt-test', 'stream': True, 'store': False, 'input': [SYSTEM, USER], 'tools': [FUNCTION]}


def test_rewrite_payload_moves_system_text_into_instructions():
    out = chatgpt.rewrite_payload(BASE, tool_shape='top_level')
    assert out['instructions'] == '你是投研助手。' and out['input'] == [USER]
    assert BASE['input'] == [SYSTEM, USER]                                       # the argument is not mutated

    many = {**BASE, 'input': [SYSTEM, {'role': 'developer', 'content': '第二段', 'type': 'message'},
                              {'role': 'system', 'content': [{'type': 'input_text', 'text': '第三'},
                                                             {'type': 'input_text', 'text': '段'}]},
                              USER, {'role': 'system', 'content': '后面的 system', 'type': 'message'},
                              {'type': 'function_call_output', 'call_id': 'c', 'output': 'o'}]}
    out = chatgpt.rewrite_payload(many, tool_shape='top_level')
    assert out['instructions'] == '你是投研助手。\n\n第二段\n\n第三段'
    assert out['input'] == [USER, {'role': 'developer', 'content': '后面的 system', 'type': 'message'},
                            {'type': 'function_call_output', 'call_id': 'c', 'output': 'o'}]
    assert all(item.get('role') != 'system' for item in out['input'])

    # an existing instructions string stays in front; a non-text head item stops the extraction
    out = chatgpt.rewrite_payload({**BASE, 'instructions': '前缀', 'input': [
        SYSTEM, {'role': 'system', 'content': [{'type': 'input_image', 'image_url': 'x'}]}, USER]}, tool_shape='top_level')
    assert out['instructions'] == '前缀\n\n你是投研助手。'
    assert out['input'][0] == {'role': 'developer', 'content': [{'type': 'input_image', 'image_url': 'x'}]}
    # a payload without any system message gets no instructions
    assert 'instructions' not in chatgpt.rewrite_payload({**BASE, 'input': [USER]}, tool_shape='top_level')


def test_rewrite_payload_removes_every_forbidden_field():
    dirty = {**BASE, **{name: 1 for name in chatgpt.FORBIDDEN_FIELDS}, 'parallel_tool_calls': False, 'include': ['x']}
    out = chatgpt.rewrite_payload(dirty, tool_shape='top_level')
    assert set(out).isdisjoint(chatgpt.FORBIDDEN_FIELDS) and len(chatgpt.FORBIDDEN_FIELDS) == 16
    assert out['parallel_tool_calls'] is False and out['include'] == ['x']       # only the forbidden ones go
    assert out['store'] is False and out['stream'] is True and out['model'] == 'gpt-test'


@pytest.mark.parametrize('shape', ['top_level', 'namespace', 'additional_tools'])
def test_rewrite_payload_tool_shapes(shape):
    call = {'type': 'function_call', 'name': 'get_price', 'arguments': '{}', 'call_id': 'c1', 'id': 'fc_1'}
    other = {'type': 'function_call', 'name': 'elsewhere', 'arguments': '{}', 'call_id': 'c2', 'id': 'fc_2'}
    out = chatgpt.rewrite_payload({**BASE, 'input': [SYSTEM, USER, call, other]}, tool_shape=shape)
    replay = [i for i in out['input'] if i.get('type') == 'function_call']
    assert all('id' not in item for item in replay)
    if shape == 'top_level':
        assert out['tools'] == [FUNCTION] and replay[0].get('namespace') is None
    elif shape == 'namespace':
        assert out['tools'] == [{'type': 'namespace', 'name': 'workbench', 'description': '投研工作台的只读数据工具',
                                 'tools': [FUNCTION]}]
        assert replay[0]['namespace'] == 'workbench' and 'namespace' not in replay[1]
    else:
        assert 'tools' not in out
        assert out['input'][0] == {'type': 'additional_tools', 'role': 'developer', 'tools': [FUNCTION]}
    assert chatgpt.NAMESPACE == {'name': 'workbench', 'description': '投研工作台的只读数据工具'}


def test_rewrite_payload_non_function_tools_stay_top_level():
    search = {'type': 'web_search'}
    out = chatgpt.rewrite_payload({**BASE, 'tools': [search, FUNCTION]}, tool_shape='namespace')
    assert out['tools'][0] == search and out['tools'][1]['type'] == 'namespace'
    out = chatgpt.rewrite_payload({**BASE, 'tools': [search, FUNCTION]}, tool_shape='additional_tools')
    assert out['tools'] == [search] and out['input'][0]['type'] == 'additional_tools'
    assert 'tools' not in chatgpt.rewrite_payload({k: v for k, v in BASE.items() if k != 'tools'}, tool_shape='namespace')


def test_rewrite_payload_rejects_an_unknown_shape():
    with pytest.raises(ValueError):
        chatgpt.rewrite_payload(BASE, tool_shape='sideways')


def test_rewrite_payload_default_is_namespace_and_env_overrides(monkeypatch):
    assert chatgpt.TOOL_SHAPE == 'namespace' and chatgpt.REQUEST_OVERRIDES == {} and chatgpt.INSTRUCTIONS_FROM_SYSTEM
    assert chatgpt.rewrite_payload(BASE)['tools'][0]['type'] == 'namespace'
    monkeypatch.setenv('WORKBENCH_CHATGPT_TOOL_SHAPE', 'top_level')
    assert chatgpt.rewrite_payload(BASE)['tools'] == [FUNCTION]
    monkeypatch.setenv('WORKBENCH_CHATGPT_TOOL_SHAPE', 'bogus')
    with pytest.raises(ValueError):
        chatgpt.rewrite_payload(BASE)
    monkeypatch.delenv('WORKBENCH_CHATGPT_TOOL_SHAPE')
    # the explicit argument wins over the environment
    monkeypatch.setenv('WORKBENCH_CHATGPT_TOOL_SHAPE', 'top_level')
    assert chatgpt.rewrite_payload(BASE, tool_shape='additional_tools')['input'][0]['type'] == 'additional_tools'


def test_rewrite_payload_overrides_and_extra_forbidden(monkeypatch):
    monkeypatch.setenv('WORKBENCH_CHATGPT_OVERRIDES', json.dumps({'parallel_tool_calls': False, 'user': 'x'}))
    monkeypatch.setenv('WORKBENCH_CHATGPT_EXTRA_FORBIDDEN', 'store, include ,')
    out = chatgpt.rewrite_payload({**BASE, 'include': ['a']}, tool_shape='top_level')
    assert out['parallel_tool_calls'] is False                                    # merged in
    assert 'user' not in out                                                      # forbidden wins over an override
    assert 'store' not in out and 'include' not in out                            # the extra names are removed too
    monkeypatch.setenv('WORKBENCH_CHATGPT_OVERRIDES', '[1]')
    with pytest.raises(ValueError):
        chatgpt.rewrite_payload(BASE)
    monkeypatch.setenv('WORKBENCH_CHATGPT_OVERRIDES', 'not json')
    with pytest.raises(ValueError):
        chatgpt.rewrite_payload(BASE)
    monkeypatch.setattr(chatgpt, 'REQUEST_OVERRIDES', {'service_tier': 'auto'})
    monkeypatch.delenv('WORKBENCH_CHATGPT_OVERRIDES')
    assert chatgpt.rewrite_payload(BASE, tool_shape='top_level')['service_tier'] == 'auto'


def test_rewrite_payload_instructions_toggle(monkeypatch):
    monkeypatch.setenv('WORKBENCH_CHATGPT_INSTRUCTIONS_FROM_SYSTEM', '0')
    out = chatgpt.rewrite_payload(BASE, tool_shape='top_level')
    assert 'instructions' not in out
    assert out['input'][0] == {'role': 'developer', 'content': '你是投研助手。', 'type': 'message'}   # never role: system
    monkeypatch.setenv('WORKBENCH_CHATGPT_INSTRUCTIONS_FROM_SYSTEM', '1')
    assert chatgpt.rewrite_payload(BASE, tool_shape='top_level')['instructions'] == '你是投研助手。'


def test_rewrite_payload_is_pure():
    snapshot = json.dumps(BASE, sort_keys=True)
    chatgpt.rewrite_payload(BASE, tool_shape='namespace')
    chatgpt.rewrite_payload(BASE, tool_shape='additional_tools')
    assert json.dumps(BASE, sort_keys=True) == snapshot


# ---------------------------------------------------------------------------------------------- error table

# the devkit's errors.ts table: (code, retryable)
DEVKIT_TABLE = [
    ('subscription_sharing_user_not_eligible', False), ('subscription_sharing_usage_limit_exceeded', False),
    ('subscription_sharing_usage_unavailable', True), ('subscription_sharing_unsupported_capability', False),
    ('subscription_sharing_route_not_supported', False), ('subscription_sharing_invalid_user', False),
    ('subscription_sharing_user_unavailable', True), ('chatpass_v2_scope_not_authorized', False),
    ('chatpass_v2_invalid_authorization_context', False), ('invalid_client', False), ('invalid_grant', False),
    ('invalid_refresh_token', False), ('token_expired', False), ('refresh_token_expired', False),
    ('refresh_token_invalidated', False), ('refresh_token_reused', False), ('invalid_token', False),
    ('invalid_api_key', False), ('access_denied', False), ('model_not_found', False),
]


@pytest.mark.parametrize('code,retryable', DEVKIT_TABLE)
def test_api_error_codes(code, retryable):
    for body in ({'error': {'code': code, 'message': 'server text'}}, {'code': code}, {'error': code},
                 {'detail': {'code': code}}, {'error': {'error': {'code': code}}}):
        error = chatgpt.api_error(body, 400)
        assert error.code == code and error.retryable is retryable and error.status == 400
        assert error.message and 'server text' not in error.message and error.message.endswith(('。', '.'))


def test_api_error_legacy_codes_keep_the_server_code():
    for legacy, current in chatgpt._LEGACY_CODES.items():
        error = chatgpt.api_error({'error': {'code': legacy}}, 403)
        assert error.code == legacy and error.message == chatgpt.api_error({'error': {'code': current}}).message
        assert chatgpt.canonical_code(legacy) == current
    assert chatgpt.api_error({'error': {'code': 'subscription_sharing_v2_client_not_enabled'}}).retryable is False


@pytest.mark.parametrize('status,retryable', [(400, False), (401, False), (403, False), (404, False), (422, False),
                                              (429, True), (500, True), (503, True), (None, False)])
def test_api_error_by_status(status, retryable):
    error = chatgpt.api_error({'error': {'message': 'echo of the whole input'}}, status)
    assert error.code == 'api_error' and error.retryable is retryable and error.status == status
    assert 'echo' not in error.message
    assert chatgpt.api_error(None, status).code == 'api_error'


def test_api_error_request_fields_and_param():
    assert 'input' in chatgpt.api_error({'detail': [{'loc': ['body', 'input'], 'type': 'list_type'}]}, 422).message
    assert 'tools' in chatgpt.api_error({'error': {'param': 'tools', 'code': 'invalid_request_error'}}, 400).message \
        or chatgpt.api_error({'error': {'param': 'tools'}}, 400).param == 'tools'
    error = chatgpt.api_error({'error': {'code': 'invalid_request_error', 'param': 'tools[0].name'}}, 400)
    assert error.param == 'tools[0].name' and error.code == 'invalid_request_error'
    unsafe = chatgpt.api_error({'error': {'code': 'weird code!', 'param': 'x y'}}, 400)
    assert unsafe.code == 'api_error' and unsafe.param is None
    long_code = chatgpt.api_error({'error': {'code': 'a' * 101}}, 500)
    assert long_code.code == 'api_error'


def test_chatgpt_error_shape_and_safe_code():
    error = chatgpt.ChatGPTError('not a code!', '固定文案', retryable=True, status=502, param='p q')
    assert (error.code, error.message, error.retryable, error.status, error.param) == (
        'api_error', '固定文案', True, 502, None)
    assert str(error) == '固定文案' and isinstance(error, RuntimeError)
    assert chatgpt.safe_code('refresh_token_reused') == 'refresh_token_reused'
    assert chatgpt.safe_code('a.b:c-d_9') == 'a.b:c-d_9'
    for bad in ('', 'a b', 'a' * 101, None, 7, 'a\n'):
        assert chatgpt.safe_code(bad) is None


def test_redact_scrubs_known_tokens_bearer_and_jwts():
    chatgpt.remember_secret('test-access-zzzz9999')
    text = 'x test-access-zzzz9999 Bearer qwerty.abc= eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1In0.c2lnbg end secret-q'
    out = chatgpt.redact(text, 'secret-q')
    for leaked in ('test-access-zzzz9999', 'qwerty', 'eyJhbGci', 'secret-q'):
        assert leaked not in out
    assert out.startswith('x ***') and out.endswith('end ***')
