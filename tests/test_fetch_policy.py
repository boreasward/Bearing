"""fetch-1 request policy (v2.8 spec §6): offline, against fake servers on 127.0.0.1 random ports.

Policies in this file use sub-second timeouts so the suite stays fast; the production numbers are asserted on
the constants and on ``worst_case_s``."""
import collections
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from investment_assistant.data import fetch_policy as fp
from investment_assistant.data import http

FAST = fp.FetchPolicy(connect_s=0.5, total_s=1.0, backoff_s=(0.01, 0.02))
FAST_POST = fp.FetchPolicy(connect_s=0.5, total_s=1.0, backoff_s=(0.01, 0.02), retry_post=False)
SLOW_S = 2.5


class FakeServer:
    """A threaded HTTP server; ``routes[path](handler)`` writes the reply, ``hits[path]`` counts arrivals."""

    def __init__(self):
        self.hits = collections.Counter()
        self.routes = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_any(self):
                path = self.path.split('?')[0]
                outer.hits[path] += 1
                length = int(self.headers.get('Content-Length') or 0)
                if length:
                    self.rfile.read(length)
                outer.routes.get(path, lambda h: reply(h, 404, b'{}'))(self)

            do_GET = do_POST = handle_any

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        self.httpd.handle_error = lambda *args: None        # clients that gave up leave broken pipes behind
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True).start()

    def url(self, path):
        return f'http://127.0.0.1:{self.port}{path}'

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def reply(handler, status, body=b'{}', headers=None):
    handler.send_response(status)
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def slow(handler):
    time.sleep(SLOW_S)
    try:
        reply(handler, 200, b'{"late": true}')
    except OSError:
        pass


def trickle(handler):
    handler.send_response(200)
    handler.send_header('Content-Length', '200')
    handler.end_headers()
    try:
        for _ in range(200):
            handler.wfile.write(b'x')
            handler.wfile.flush()
            time.sleep(0.1)
    except OSError:
        pass


@pytest.fixture
def server():
    srv = FakeServer()
    yield srv
    srv.close()


def closed_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def run_get(url, policy=FAST, **kwargs):
    sleeps = []
    try:
        with fp.policy_session(policy) as session:
            result = fp.fetch('t', lambda: _ok(session.get(url)), policy=policy, sleep=sleeps.append, **kwargs)
        return result, sleeps, None
    except fp.FetchError as exc:
        return None, sleeps, exc


def _ok(resp):
    if resp.status_code >= 400:
        resp.raise_for_status()
    return resp


# ---------------------------------------------------------------- constants and shapes

def test_constants_and_default_policy():
    assert (fp.FETCH_POLICY_VERSION, fp.CONNECT_TIMEOUT_S, fp.TOTAL_TIMEOUT_S) == ('fetch-1', 10, 30)
    assert fp.RETRY_BACKOFF_S == (2, 8)
    assert fp.RETRY_STATUSES == frozenset({429, 500, 502, 503, 504})
    assert fp.YF_HARD_DEADLINE_S == 35
    policy = fp.FetchPolicy()
    assert (policy.connect_s, policy.total_s, policy.backoff_s, policy.retry_post) == (10, 30, (2, 8), True)


def test_worst_case_under_two_minutes():
    assert fp.worst_case_s(fp.FetchPolicy()) == 3 * 30 + 2 + 8 == 100
    assert fp.worst_case_s(fp.FetchPolicy(), per_attempt_s=fp.YF_HARD_DEADLINE_S) == 3 * 35 + 10 == 115
    assert fp.worst_case_s(fp.FetchPolicy()) < 120 and fp.worst_case_s(fp.FetchPolicy(), per_attempt_s=35) < 120


@pytest.mark.parametrize('exc,expected', [
    (requests.ReadTimeout('x'), ('timeout', None)),
    (requests.ConnectTimeout('x'), ('timeout', None)),
    (TimeoutError('x'), ('timeout', None)),
    (requests.ConnectionError('x'), ('network', None)),
    (requests.exceptions.SSLError('x'), ('network', None)),
    (requests.exceptions.ProxyError('x'), ('network', None)),
    (ConnectionResetError('x'), ('network', None)),
    (fp.FetchError('x', kind='http_429', status=429), ('http_429', 429)),
    (ValueError('x'), ('other', None)),
    (RuntimeError('x'), ('other', None)),
])
def test_classify_exception(exc, expected):
    assert fp.classify_exception(exc) == expected


def test_classify_http_status_and_yfinance_and_curl():
    def http_error(status):
        err = requests.HTTPError('x')
        err.response = type('R', (), {'status_code': status, 'headers': {}})()
        return err

    assert fp.classify_exception(http_error(503)) == ('http_5xx', 503)
    assert fp.classify_exception(http_error(429)) == ('http_429', 429)
    assert fp.classify_exception(http_error(404)) == ('http_4xx', 404)
    from curl_cffi.requests import exceptions as cffi
    assert fp.classify_exception(cffi.Timeout('x')) == ('timeout', None)
    assert fp.classify_exception(cffi.ConnectionError('x')) == ('network', None)
    assert fp.classify_exception(type('YFRateLimitError', (Exception,), {})('x')) == ('http_429', 429)
    import json
    assert fp.classify_exception(json.JSONDecodeError('x', '', 0)) == ('parse', None)

    class Empty(RuntimeError):
        fetch_kind = 'empty'

    assert fp.classify_exception(Empty('no bars')) == ('empty', None)


# ---------------------------------------------------------------- fetch()

def test_success_one_attempt():
    sleeps = []
    out = fp.fetch('NVDA', lambda: 42, sleep=sleeps.append)
    assert out.value == 42 and sleeps == []
    assert len(out.attempts) == 1 and out.attempts[0]['n'] == 1 and out.attempts[0]['kind'] is None
    assert out.elapsed_ms >= 0


def test_5xx_retried_twice_with_2_and_8(server):
    server.routes['/r'] = lambda h: reply(h, 503)
    sleeps = []
    with pytest.raises(fp.FetchError) as caught:
        with fp.policy_session(FAST) as session:
            fp.fetch('t', lambda: _ok(session.get(server.url('/r'))), sleep=sleeps.append)   # default policy numbers
    err = caught.value
    assert sleeps == [2, 8]
    assert server.hits['/r'] == 3 and len(err.attempts) == 3
    assert err.kind == 'http_5xx' and err.status == 503 and err.target == 't'
    assert [a['n'] for a in err.attempts] == [1, 2, 3] and all(a['kind'] == 'http_5xx' for a in err.attempts)
    assert 'HTTP 503' in str(err) and '3 attempts' in str(err)


def test_retry_then_success_records_every_attempt(server):
    seen = []

    def flaky(handler):
        seen.append(1)
        reply(handler, 500 if len(seen) < 3 else 200, b'{"ok": 1}')

    server.routes['/f'] = flaky
    sleeps = []
    with fp.policy_session(FAST) as session:
        out = fp.fetch('t', lambda: _ok(session.get(server.url('/f'))), sleep=sleeps.append)
    assert out.value.json() == {'ok': 1} and sleeps == [2, 8]
    assert [a['kind'] for a in out.attempts] == ['http_5xx', 'http_5xx', None]


def test_429_retry_after_capped(server):
    server.routes['/big'] = lambda h: reply(h, 429, headers={'Retry-After': '60'})
    server.routes['/small'] = lambda h: reply(h, 429, headers={'Retry-After': '1'})
    _, sleeps, err = run_get(server.url('/big'), fp.FetchPolicy(connect_s=0.5, total_s=1.0))
    assert err.kind == 'http_429' and err.status == 429
    assert sleeps == [2, 8] and sum(sleeps) <= fp.RETRY_AFTER_CAP_S
    assert server.hits['/big'] == 3
    _, sleeps, err = run_get(server.url('/small'), fp.FetchPolicy(connect_s=0.5, total_s=1.0))
    assert sleeps == [1, 1] and err.kind == 'http_429'


def test_4xx_not_retried(server):
    server.routes['/missing'] = lambda h: reply(h, 404)
    _, sleeps, err = run_get(server.url('/missing'))
    assert err.kind == 'http_4xx' and err.status == 404
    assert server.hits['/missing'] == 1 and sleeps == [] and len(err.attempts) == 1


def test_unretryable_5xx_status_is_not_retried():
    sleeps = []
    with pytest.raises(fp.FetchError) as caught:
        fp.fetch('t', lambda: (_ for _ in ()).throw(fp.http_error(501, 'not implemented')), sleep=sleeps.append)
    assert caught.value.kind == 'http_5xx' and caught.value.status == 501 and sleeps == []


def test_network_error_retried():
    url = f'http://127.0.0.1:{closed_port()}/x'
    _, sleeps, err = run_get(url, fp.FetchPolicy(connect_s=0.5, total_s=1.0))
    assert err.kind == 'network' and len(err.attempts) == 3 and sleeps == [2, 8]
    assert 'network error' in str(err) and '3 attempts' in str(err)


def test_connect_timeout():
    """A listener that never accepts: the request goes nowhere and the call ends at the policy's timeout."""
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(0)
        url = f'http://127.0.0.1:{listener.getsockname()[1]}/x'
        started = time.monotonic()
        _, sleeps, err = run_get(url, fp.FetchPolicy(connect_s=0.3, total_s=0.6, backoff_s=()))
        assert time.monotonic() - started < 3
    assert err.kind == 'timeout' and err.status is None and len(err.attempts) == 1
    assert str(err) == 'timeout after 0.6s'


def test_total_timeout_slow_body(server):
    """Bytes keep arriving inside every read timeout, so only the total deadline can end the call."""
    server.routes['/t'] = trickle
    started = time.monotonic()
    _, _, err = run_get(server.url('/t'), fp.FetchPolicy(connect_s=0.5, total_s=0.8, backoff_s=()))
    assert err.kind == 'timeout'
    assert time.monotonic() - started < 3


def test_slow_headers_time_out(server):
    server.routes['/slow'] = slow
    started = time.monotonic()
    _, _, err = run_get(server.url('/slow'), fp.FetchPolicy(connect_s=0.5, total_s=0.6, backoff_s=()))
    assert err.kind == 'timeout' and time.monotonic() - started < 2


def test_nested_fetch_error_with_attempts_is_final():
    inner = fp.FetchError('inner gave up', kind='network',
                          attempts=[{'n': 1, 'kind': 'network', 'status': None, 'elapsed_ms': 1}] * 3)
    calls = []

    def fn():
        calls.append(1)
        raise inner

    sleeps = []
    with pytest.raises(fp.FetchError) as caught:
        fp.fetch('outer', fn, sleep=sleeps.append)
    assert caught.value is inner and len(calls) == 1 and sleeps == [] and inner.target == 'outer'


def test_only_classified_retryable_kinds_retry():
    calls = []

    def fn():
        calls.append(1)
        raise ValueError('bad data')

    with pytest.raises(fp.FetchError) as caught:
        fp.fetch('t', fn, sleep=lambda s: pytest.fail('must not sleep'))
    assert len(calls) == 1 and caught.value.kind == 'other' and str(caught.value) == 'bad data'
    custom = []

    def always_timeout():
        custom.append(1)
        raise RuntimeError('anything')

    with pytest.raises(fp.FetchError) as caught:
        fp.fetch('t', always_timeout, sleep=lambda s: None, classify=lambda exc: ('timeout', None))
    assert len(custom) == 3 and caught.value.kind == 'timeout'


# ---------------------------------------------------------------- POST policy (D23)

def run_post(url, policy=FAST_POST):
    sleeps = []
    try:
        with fp.policy_session(policy) as session:
            fp.fetch('t', lambda: _ok(session.post(url, json={'a': 1})), policy=policy, sleep=sleeps.append)
        return sleeps, None
    except fp.FetchError as exc:
        return sleeps, exc


def test_post_policy_never_resends_after_send(server):
    server.routes['/read-timeout'] = slow
    server.routes['/boom'] = lambda h: reply(h, 503)
    server.routes['/limited'] = lambda h: reply(h, 429, headers={'Retry-After': '0'})
    sleeps, err = run_post(server.url('/read-timeout'), fp.FetchPolicy(connect_s=0.5, total_s=0.5, backoff_s=(0.01, 0.02),
                                                                        retry_post=False))
    assert err.kind == 'timeout' and server.hits['/read-timeout'] == 1 and sleeps == []
    sleeps, err = run_post(server.url('/boom'))
    assert err.kind == 'http_5xx' and server.hits['/boom'] == 1 and sleeps == []
    # the request provably never left: retried
    sleeps, err = run_post(f'http://127.0.0.1:{closed_port()}/x')
    assert err.kind == 'network' and len(err.attempts) == 3 and len(sleeps) == 2
    # 429 means the server did nothing: retried
    sleeps, err = run_post(server.url('/limited'))
    assert err.kind == 'http_429' and server.hits['/limited'] == 3 and sleeps == [0.0, 0.0]


def test_post_policy_resend_is_off_even_for_reset_after_send(server):
    def reset(handler):
        handler.connection.close()

    server.routes['/reset'] = reset
    sleeps, err = run_post(server.url('/reset'))
    assert err.kind == 'network' and server.hits['/reset'] == 1 and sleeps == []



def test_post_policy_survives_a_string_reason_in_the_chain():
    """``ssl.SSLError.reason`` is a str; walking the chain to decide "did the POST leave?" must skip it, not crash."""
    import ssl

    inner = ssl.SSLError(1, '[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred')
    inner.reason = 'UNEXPECTED_EOF_WHILE_READING'

    def fn():
        raise requests.exceptions.SSLError('tls handshake failed') from inner

    assert fp.request_never_sent(inner) is False
    with pytest.raises(fp.FetchError) as caught:
        fp.fetch('t', fn, policy=FAST_POST, sleep=lambda s: None)
    assert caught.value.kind == 'network' and len(caught.value.attempts) == 1

# ---------------------------------------------------------------- hard_deadline / yf_session

def test_hard_deadline_raises_timeout():
    started = time.monotonic()
    with pytest.raises(fp.FetchError) as caught:
        fp.hard_deadline(lambda: time.sleep(2), seconds=0.2)
    assert caught.value.kind == 'timeout' and time.monotonic() - started < 1
    assert fp.hard_deadline(lambda: 7, seconds=1) == 7
    with pytest.raises(KeyError):
        fp.hard_deadline(lambda: {}['x'], seconds=1)


def test_yf_session_total_timeout(server):
    """The curl_cffi session yfinance runs on ends a slow response at its total timeout."""
    server.routes['/slow'] = slow
    session = fp.yf_session(fp.FetchPolicy(connect_s=0.5, total_s=0.8))
    started = time.monotonic()
    with pytest.raises(Exception) as caught:
        session.get(server.url('/slow'))
    assert time.monotonic() - started < 2
    assert fp.classify_exception(caught.value) == ('timeout', None)
    server.routes['/ok'] = lambda h: reply(h, 200, b'fine')
    assert session.get(server.url('/ok')).text == 'fine'


def test_yf_session_connection_refused_is_network():
    session = fp.yf_session(fp.FetchPolicy(connect_s=0.5, total_s=1.0))
    with pytest.raises(Exception) as caught:
        session.get(f'http://127.0.0.1:{closed_port()}/x')
    assert fp.classify_exception(caught.value)[0] == 'network'


# ---------------------------------------------------------------- sanitising

def test_errors_sanitized(monkeypatch, server):
    secret = 'FRED-KEY-0123456789'
    monkeypatch.setenv('FRED_API_KEY', secret)
    with pytest.raises(fp.FetchError) as caught:
        fp.fetch('t', lambda: (_ for _ in ()).throw(RuntimeError(f'GET https://x/?api_key={secret} failed')),
                 sleep=lambda s: None)
    assert secret not in str(caught.value) and secret not in repr(caught.value.attempts)
    # get_json: the key is in the query string; a refused connection's exception text repeats the URL
    redact = lambda text: text.replace(secret, '***')                      # noqa: E731
    data, meta = http.get_json(f'http://127.0.0.1:{closed_port()}/series', params={'api_key': secret},
                               policy=FAST, sleep=lambda s: None, redact=redact)
    assert data is None and meta['ok'] is False and meta['error_kind'] == 'network'
    assert secret not in meta['error'] and secret not in repr(meta['attempts'])
    server.routes['/echo'] = lambda h: reply(h, 500, f'oops {secret}'.encode())
    data, meta = http.get_json(server.url('/echo'), params={'api_key': secret}, policy=FAST,
                               sleep=lambda s: None, redact=redact)
    assert meta['error_kind'] == 'http_5xx' and secret not in meta['error'] and '***' in meta['error']


# ---------------------------------------------------------------- fix round 1: hard per-attempt deadline

def stall_after_headers(handler, *, first_byte_after=0.0, then=3.0):
    """200 with Content-Length 100, one byte after `first_byte_after` s, then silence for `then` s."""
    handler.send_response(200)
    handler.send_header('Content-Length', '100')
    handler.end_headers()
    try:
        time.sleep(first_byte_after)
        handler.wfile.write(b'x')
        handler.wfile.flush()
        time.sleep(then)
    except OSError:
        pass


def test_body_stalls_after_headers_is_a_retried_timeout(server):
    server.routes['/stall'] = stall_after_headers
    policy = fp.FetchPolicy(connect_s=0.5, total_s=0.6, backoff_s=(0.01, 0.02))
    started = time.monotonic()
    _, sleeps, err = run_get(server.url('/stall'), policy)
    elapsed = time.monotonic() - started
    assert err.kind == 'timeout' and len(err.attempts) == 3 and server.hits['/stall'] == 3
    # worst case per target: 3 x total + the backoff waits, plus scheduling slack
    assert elapsed <= 3 * policy.total_s + sum(policy.backoff_s) + 0.6
    assert all(a['elapsed_ms'] <= policy.total_s * 1000 + 300 for a in err.attempts)


def test_total_is_one_budget_for_headers_and_body(server):
    """Headers after 0.45 s, first byte another 0.45 s later: each wait is inside the read timeout, the sum is not."""
    server.routes['/two'] = lambda h: (time.sleep(0.45), stall_after_headers(h, first_byte_after=0.45))
    policy = fp.FetchPolicy(connect_s=0.5, total_s=0.7, backoff_s=())
    started = time.monotonic()
    _, _, err = run_get(server.url('/two'), policy)
    assert err.kind == 'timeout' and time.monotonic() - started < policy.total_s + 0.4


def test_body_disconnect_is_a_retried_network_error(server):
    def cut(handler):
        handler.send_response(200)
        handler.send_header('Content-Length', '100')
        handler.end_headers()
        handler.wfile.write(b'0123456789')
        handler.wfile.flush()
        handler.connection.close()

    server.routes['/cut'] = cut
    _, sleeps, err = run_get(server.url('/cut'))
    assert err.kind == 'network' and len(err.attempts) == 3 and server.hits['/cut'] == 3 and len(sleeps) == 2
    # the same through get_json
    data, meta = http.get_json(server.url('/cut'), policy=FAST, sleep=lambda s: None)
    assert data is None and meta['error_kind'] == 'network' and len(meta['attempts']) == 3


def test_chunked_body_cut_midway_is_network(server):
    def cut(handler):
        handler.send_response(200)
        handler.send_header('Transfer-Encoding', 'chunked')
        handler.end_headers()
        handler.wfile.write(b'5\r\nhello\r\n')
        handler.wfile.flush()
        handler.connection.close()

    server.routes['/chunk'] = cut
    _, _, err = run_get(server.url('/chunk'))
    assert err.kind == 'network' and len(err.attempts) == 3


def test_urllib3_errors_classify_like_requests_ones():
    import urllib3.exceptions as u3
    assert fp.classify_exception(u3.ReadTimeoutError(None, '/x', 'slow')) == ('timeout', None)
    assert fp.classify_exception(u3.ProtocolError('cut')) == ('network', None)
    assert fp.classify_exception(u3.IncompleteRead(10, 90)) == ('network', None)


def test_error_carries_wall_clock_total_including_backoff():
    policy = fp.FetchPolicy(backoff_s=(0.05, 0.05))
    with pytest.raises(fp.FetchError) as caught:
        fp.fetch('t', lambda: (_ for _ in ()).throw(requests.ReadTimeout('x')), policy=policy, sleep=time.sleep)
    err = caught.value
    assert err.total_ms >= 95 and err.elapsed_ms == err.total_ms
    assert err.elapsed_ms > sum(a['elapsed_ms'] for a in err.attempts) + 80      # the waits are in it
    assert fp.FetchError('x', attempts=[{'elapsed_ms': 5}, {'elapsed_ms': 7}]).elapsed_ms == 12   # built by hand


def test_worst_case_bound_matches_the_session_deadline():
    policy = fp.FetchPolicy()
    # one attempt can no longer take more than total_s (connect + headers + body share one budget)
    assert fp.worst_case_s(policy) == 3 * policy.total_s + sum(policy.backoff_s) < 120


def test_shared_yf_session_is_one_thread_safe_object():
    import threading
    seen = []
    threads = [threading.Thread(target=lambda: seen.append(fp.shared_yf_session())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({id(s) for s in seen}) == 1 and seen[0] is fp.shared_yf_session()
