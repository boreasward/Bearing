"""Sign in with ChatGPT (``siwc-1``): OAuth with dynamic registration + PKCE, token refresh, the Responses request
rewrite and the error table (v2.8 spec §8).

Everything here is protocol: pure functions (``authorize_url``, ``check_id_token``, ``rewrite_payload``, ``api_error``),
HTTP calls that all go through ``fetch_policy`` (short requests; a POST that consumes a grant is never replayed after
it may have been sent), and the one-shot loopback ``CallbackServer``. Persistence lives in ``agent_connection``.

Tokens never leave this module in an exception: every failure is rebuilt as ``ChatGPTError(code, message)`` where the
code is a safe identifier and the message a fixed Chinese sentence; response bodies are read for the code only.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import http.server
import json
import math
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from investment_assistant.core.http import FetchError, FetchPolicy, fetch, http_error, policy_session

CHATGPT_PROTOCOL_VERSION = 'siwc-1'

# --- OAuth constants (spec §8.1, same as the devkit) ---------------------------------------------------------------
ISSUER = 'https://auth.openai.com'
RESOURCE = 'https://api.openai.com/v1'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
SHARING_SCOPE = 'chatgpt.tokens.use.direct'
CALLBACK_PATH = '/auth/callback'
DYNAMIC_CLIENT = 'dynamic_agent_client'
APP_NAME = 'investment-assistant'
LOGIN_TTL_S = 600

# A refresh that failed in one of these ways cannot be retried: the connection needs a new login (spec §8.2).
REAUTH_CODES = frozenset({'invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired',
                          'refresh_token_invalidated', 'refresh_token_reused', 'invalid_id_token',
                          'account_mismatch'})

# --- Request rewrite (spec §8.5) --------------------------------------------------------------------------------------
# FORBIDDEN_FIELDS is the official restrictions page, verbatim. The other four constants are the knobs the verification
# (spec §17) decides; until it is filled in they can be overridden by environment variables. Those variables are for
# the verification period only: WORKBENCH_CHATGPT_TOOL_SHAPE, WORKBENCH_CHATGPT_EXTRA_FORBIDDEN (comma separated),
# WORKBENCH_CHATGPT_OVERRIDES (a JSON object merged into the request) and WORKBENCH_CHATGPT_INSTRUCTIONS_FROM_SYSTEM
# (0 / 1).
FORBIDDEN_FIELDS = ('background', 'conversation', 'max_output_tokens', 'max_tool_calls', 'metadata', 'moderation',
                    'multi_agent', 'prompt', 'prompt_cache_retention', 'safety_identifier', 'temperature',
                    'top_logprobs', 'top_p', 'truncation', 'user', 'previous_response_id')
TOOL_SHAPES = ('top_level', 'namespace', 'additional_tools')
TOOL_SHAPE = 'namespace'                       # official limits page: functions go in a namespace or additional_tools
NAMESPACE = {'name': 'workbench', 'description': '投研工作台的只读数据工具'}
REQUEST_OVERRIDES: dict[str, Any] = {}         # e.g. {'parallel_tool_calls': False} if the verification demands it
INSTRUCTIONS_FROM_SYSTEM = True                # leading system / developer text -> ``instructions``

# --- HTTP policies: discovery, models, revocation are idempotent; a grant POST must not be sent twice -------------------
STANDARD_POLICY = FetchPolicy()
GRANT_POLICY = FetchPolicy(retry_post=False)
REVOKE_POLICY = FetchPolicy(total_s=10, backoff_s=(1,))
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
SLEEP = time.sleep                             # tests replace it so a retry never really waits

_SAFE_CODE = re.compile(r'^[A-Za-z0-9_.:-]{1,100}$')
_SAFE_PARAM = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.:\[\]-]{0,159}$')
_CLIENT_ID = re.compile(r'^[A-Za-z0-9_-]{1,200}$')
_JWT = re.compile(r'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}')
_BEARER = re.compile(r'(?i)bearer\s+[A-Za-z0-9._~+/=-]+')


# ===================================================================================================================
# errors
# ===================================================================================================================

def safe_code(value: Any) -> str | None:
    """``value`` when it is a safe identifier (letters, digits, ``_ . : -``, at most 100 characters), else None."""
    return value if isinstance(value, str) and _SAFE_CODE.fullmatch(value) else None


def _safe_param(value: Any) -> str | None:
    return value if isinstance(value, str) and _SAFE_PARAM.fullmatch(value) else None


class ChatGPTError(RuntimeError):
    """A protocol failure with a safe ``code`` and a fixed Chinese ``message``; never carries a response body."""

    def __init__(self, code: str, message: str, retryable: bool = False, status: int | None = None,
                 param: str | None = None):
        super().__init__(message)
        self.code = safe_code(code) or 'api_error'
        self.message = message
        self.retryable = retryable
        self.status = status
        self.param = _safe_param(param)


# code -> (fixed message, retryable); the devkit's table (errors.ts) in Chinese
_API_ERRORS: dict[str, tuple[str, bool]] = {
    'subscription_sharing_user_not_eligible': ('这个 ChatGPT 账号或工作区不支持令牌共享。', False),
    'subscription_sharing_usage_limit_exceeded': ('ChatGPT 共享用量已达上限，请到 ChatGPT 设置里查看用量与应用限额。', False),
    'subscription_sharing_usage_unavailable': ('暂时无法查询 ChatGPT 用量，请稍后重试。', True),
    'subscription_sharing_unsupported_capability': ('这个请求包含令牌共享不支持的能力。', False),
    'subscription_sharing_route_not_supported': ('这个请求使用了令牌共享不支持的路由。', False),
    'subscription_sharing_invalid_user': ('ChatGPT 无法验证所选账号的订阅信息，请检查连接和已授予的权限。', False),
    'subscription_sharing_user_unavailable': ('ChatGPT 账号或工作区暂时不可用，请稍后重试。', True),
    'chatpass_v2_scope_not_authorized': ('已授予的 ChatGPT 权限不允许这个请求，请检查应用与授权配置。', False),
    'chatpass_v2_invalid_authorization_context': ('ChatGPT 授权上下文不允许这个请求，请检查应用与授权配置。', False),
    'invalid_client': ('ChatGPT 拒绝了应用的客户端配置，请检查已保存的注册信息。', False),
    'invalid_grant': ('ChatGPT 不接受这次授权，请重新连接。', False),
    'invalid_refresh_token': ('ChatGPT 连接已无法续期，请重新连接。', False),
    'token_expired': ('ChatGPT 连接已无法续期，请重新连接。', False),
    'refresh_token_expired': ('ChatGPT 连接已无法续期，请重新连接。', False),
    'refresh_token_invalidated': ('ChatGPT 连接已无法续期，请重新连接。', False),
    'refresh_token_reused': ('ChatGPT 连接已无法续期，请重新连接。', False),
    'invalid_token': ('ChatGPT 不接受当前凭据，请检查连接与已授予的权限。', False),
    'invalid_api_key': ('ChatGPT 不接受当前凭据，请检查连接与已授予的权限。', False),
    'access_denied': ('登录未完成，可以稍后再试。', False),
    'model_not_found': ('所选模型对当前 ChatGPT 连接不可用，请重新选择模型。', False),
}
# earlier server spellings; the exact server code is kept on the error
_LEGACY_CODES = {
    'subscription_sharing_v2_user_not_eligible': 'subscription_sharing_user_not_eligible',
    'subscription_sharing_v2_route_not_supported': 'subscription_sharing_route_not_supported',
    'subscription_sharing_v2_invalid_user': 'subscription_sharing_invalid_user',
    'subscription_sharing_v2_user_unavailable': 'subscription_sharing_user_unavailable',
}
_REQUEST_FIELDS = {'input': '输入', 'model': '模型', 'instructions': '指令', 'store': '存储设置', 'stream': '流式设置',
                   'tools': '工具', 'background': 'background 设置', 'conversation': 'conversation 设置'}

# messages of the codes this module raises itself
MESSAGES = {
    'discovery_failed': 'ChatGPT 登录配置无法验证，请稍后重试。',
    'network_error': '连不上 ChatGPT，请检查网络后重试。',
    'invalid_response': 'ChatGPT 返回了无法识别的响应，请稍后重试。',
    'invalid_token_response': 'ChatGPT 返回的凭据不完整，请重新连接。',
    'invalid_id_token': '无法验证 ChatGPT 返回的账号身份，请重新连接。',
    'account_mismatch': '这次登录的 ChatGPT 账号与已保存的不同，请使用原来的账号。',
    'registration_incomplete': 'ChatGPT 没有完成应用注册，请重新连接。',
    'callback_port_unavailable': '本机登录回调端口不可用，请关闭占用它的程序或换一个端口。',
    'login_expired': '登录等待超时，请重新连接。',
    'cancelled': '登录已取消。',
    'invalid_model_catalog': 'ChatGPT 返回了无法识别的模型清单，请稍后重试。',
    'response_incomplete': 'ChatGPT 在完成回答之前停止了。',
    'stream_interrupted': '回答在完成之前中断了。',
    'refresh_not_ready': 'ChatGPT 连接暂时还不能续期，请稍后重试。',
    'not_connected': 'ChatGPT 未连接。',
    'model_not_listed': '所选模型不在可用清单里。',
    'login_failed': '登录没有完成，请重新连接。',
    'invalid_request': 'ChatGPT 拒绝了这个请求。',
}


def canonical_code(code: str) -> str:
    """The current spelling of a code the server used to spell differently (``…_v2_…``)."""
    return _LEGACY_CODES.get(code, code)


def error_for(code: str, *, retryable: bool = False, status: int | None = None) -> ChatGPTError:
    """A ChatGPTError with the fixed message of one of this module's own codes."""
    return ChatGPTError(code, MESSAGES.get(code, 'ChatGPT 未能完成这个请求。'), retryable, status)


def _unwrap(body: Any) -> dict:
    detail = body if isinstance(body, dict) else {}
    for _ in range(4):
        if isinstance(detail.get('error'), dict):
            detail = detail['error']
        elif isinstance(detail.get('detail'), dict):
            detail = detail['detail']
        else:
            break
    return detail


def _invalid_request_message(detail: dict) -> str:
    # validation responses can echo the submitted input: recognised field names only, never values or server text
    issues = [i for i in detail['detail'] if isinstance(i, dict)] if isinstance(detail.get('detail'), list) else [detail]
    for issue in issues:
        places = issue['loc'] if isinstance(issue.get('loc'), list) else [issue.get('param')]
        field = next((p for p in places if isinstance(p, str) and p in _REQUEST_FIELDS), None)
        if field == 'input' and issue.get('type') == 'list_type':
            return 'ChatGPT 要求 input 是消息列表，请检查请求格式。'
        if field:
            return f'ChatGPT 拒绝了请求里的{_REQUEST_FIELDS[field]}，请检查格式与取值。'
    return 'ChatGPT 拒绝了这个请求，请检查输入、模型与所用选项。'


def api_error(body: Any, status: int | None = None) -> ChatGPTError:
    """The devkit's ``apiError``: a safe code from the body (OAuth ``error`` string or Responses ``error.code``), a
    fixed Chinese message from the table below, ``param`` when it is a safe identifier. Nothing else of the body."""
    detail = _unwrap(body)
    code = safe_code(detail['error'] if isinstance(detail.get('error'), str) else detail.get('code')) or 'api_error'
    param = _safe_param(detail.get('param'))

    def err(message: str, retryable: bool = False) -> ChatGPTError:
        return ChatGPTError(code, message, retryable, status, param)

    known = _API_ERRORS.get(code) or _API_ERRORS.get(_LEGACY_CODES.get(code, ''))
    if known:
        return err(*known)
    if code == 'subscription_sharing_v2_client_not_enabled':
        return err('这个应用没有启用令牌共享。')
    if status in (400, 422) or code in ('invalid_request_error', 'invalid_request'):
        return err(_invalid_request_message(detail))
    if status == 401:
        return err('ChatGPT 不接受当前连接的签名身份或直接权限，请检查连接与已授予的 scope。')
    if status == 403:
        return err('ChatGPT 的策略或权限限制拒绝了这个请求，请检查应用配置与服务地区。')
    if status == 429:
        return err('请求过多，请稍后再试。', True)
    if status is not None and status >= 500:
        return err('ChatGPT 暂时不可用，请稍后重试。', True)
    return err('ChatGPT 未能完成这个请求。')


# --- redaction ---------------------------------------------------------------------------------------------------------
_KNOWN_SECRETS: list[str] = []
_KNOWN_LOCK = threading.Lock()


def remember_secret(value: Any) -> None:
    """Register a token handed out in this process so ``redact`` can scrub it from any text."""
    if isinstance(value, str) and len(value) >= 8:
        with _KNOWN_LOCK:
            if value in _KNOWN_SECRETS:
                _KNOWN_SECRETS.remove(value)
            _KNOWN_SECRETS.append(value)
            del _KNOWN_SECRETS[:-8]


def redact(text: str, secret: str | None = None) -> str:
    """Remove ``secret``, every token this process handed out, ``Bearer …`` and JWT-shaped fragments from ``text``."""
    with _KNOWN_LOCK:
        known = list(_KNOWN_SECRETS)
    for token in sorted({*known, *([secret] if secret else [])}, key=len, reverse=True):
        if token:
            text = text.replace(token, '***')
    text = _BEARER.sub('Bearer ***', text)
    return _JWT.sub('***', text)


# ===================================================================================================================
# HTTP
# ===================================================================================================================

def _http(method: str, url: str, form: Mapping[str, str] | None, headers: Mapping[str, str] | None,
          policy: FetchPolicy) -> tuple[int, Any, Mapping]:
    """One request: ``(status, decoded JSON or None, headers)``. Redirects are errors (a redirect could forward the
    request, and its credentials, to another origin); the environment's proxy variables are honoured."""
    with policy_session(policy) as session:
        response = session.request(method, url, data=dict(form) if form is not None else None,
                                   headers=dict(headers or {}), allow_redirects=False)
    try:
        body = response.json()
    except ValueError:
        body = None
    return response.status_code, body, response.headers


def _header(headers: Any, name: str) -> Any:
    try:
        return headers.get(name)
    except AttributeError:
        return None


def _call(target: str, method: str, url: str, *, form: Mapping[str, str] | None = None,
          headers: Mapping[str, str] | None = None, policy: FetchPolicy = STANDARD_POLICY,
          fetcher: Callable | None = None) -> tuple[int, Any]:
    """``(status, body)`` of a request run under ``fetch``: connection failures and 429 / 5xx are retried per policy;
    when they persist the error is the table's ``api_error`` (status known) or ``network_error``."""
    last: dict[str, Any] = {}

    def attempt() -> tuple[int, Any]:
        if fetcher is not None:
            status, body, response_headers = fetcher(method, url, form, headers)
        else:
            status, body, response_headers = _http(method, url, form, headers, policy)
        last['status'], last['body'] = status, body
        if status in RETRY_STATUSES:
            raise http_error(status, f'HTTP {status}', retry_after=_header(response_headers, 'Retry-After'))
        return status, body

    try:
        return fetch(target, attempt, policy=policy, sleep=lambda seconds: SLEEP(seconds)).value
    except FetchError as exc:
        if 'status' in last and exc.status is not None:
            raise api_error(last['body'], last['status']) from None
        if exc.kind in ('timeout', 'network'):
            raise error_for('network_error', retryable=True) from None
        raise error_for('invalid_response') from None


# ===================================================================================================================
# discovery, PKCE, authorization URL
# ===================================================================================================================

_DISCOVERY: dict[str, dict] = {}
_DISCOVERY_LOCK = threading.Lock()


def clear_discovery_cache() -> None:
    with _DISCOVERY_LOCK:
        _DISCOVERY.clear()


def _origin(url: Any) -> tuple[str, str] | None:
    if not isinstance(url, str):
        return None
    parts = urlsplit(url)
    return (parts.scheme, parts.netloc) if parts.scheme and parts.netloc else None


def discover(*, fetcher: Callable | None = None) -> dict:
    """The OpenID discovery document, validated: ``issuer`` equals ``ISSUER`` and every endpoint is on its origin
    (``revocation_endpoint`` is optional). Cached in the process per issuer."""
    issuer = ISSUER
    with _DISCOVERY_LOCK:
        if issuer in _DISCOVERY:
            return _DISCOVERY[issuer]

    def failure() -> ChatGPTError:
        return error_for('discovery_failed', retryable=True)

    try:
        status, body = _call('chatgpt:discovery', 'GET', f'{issuer}/.well-known/openid-configuration',
                             headers={'Accept': 'application/json'}, fetcher=fetcher)
    except ChatGPTError:
        raise failure() from None
    if status != 200 or not isinstance(body, dict) or body.get('issuer') != issuer:
        raise failure()
    for key in ('authorization_endpoint', 'token_endpoint', 'jwks_uri', 'revocation_endpoint'):
        value = body.get(key)
        if key == 'revocation_endpoint' and value is None:
            continue
        if _origin(value) is None or _origin(value) != _origin(issuer):
            raise failure()
    document = {key: body[key] for key in ('issuer', 'authorization_endpoint', 'token_endpoint', 'jwks_uri')}
    if body.get('revocation_endpoint'):
        document['revocation_endpoint'] = body['revocation_endpoint']
    with _DISCOVERY_LOCK:
        _DISCOVERY[issuer] = document
    return document


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def new_pkce() -> dict:
    """``{'state', 'nonce', 'verifier', 'challenge'}``: three 32-byte random values and the S256 challenge."""
    verifier = _b64url(secrets.token_bytes(32))
    return {'state': _b64url(secrets.token_bytes(32)), 'nonce': _b64url(secrets.token_bytes(32)),
            'verifier': verifier, 'challenge': _b64url(hashlib.sha256(verifier.encode('ascii')).digest())}


def authorize_url(*, discovery: Mapping, client_id: str | None, redirect_uri: str, state: str, nonce: str,
                  challenge: str, host_id: str, email_hint: str | None = None, first: bool = False) -> str:
    """The authorization URL (parameters as the devkit sends them). ``client_id`` None means not registered yet:
    ``dynamic_agent_client``; ``first`` adds ``agent_name_hint``; a known e-mail becomes ``login_hint``."""
    params = {
        'client_id': client_id or DYNAMIC_CLIENT, 'response_type': 'code', 'redirect_uri': redirect_uri,
        'scope': SCOPES, 'resource': RESOURCE, 'state': state, 'nonce': nonce,
        'code_challenge_method': 'S256', 'code_challenge': challenge, 'ext_agent_host_id': host_id,
    }
    if first:
        params['agent_name_hint'] = APP_NAME
    if email_hint:
        params['login_hint'] = email_hint
    parts = urlsplit(discovery['authorization_endpoint'])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ''))


# ===================================================================================================================
# the loopback callback
# ===================================================================================================================

_PAGE = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>{title}</title>'
         '<style>body{{font:17px system-ui;max-width:32rem;margin:18vh auto;padding:24px;color:#202123}}'
         'h1{{font-size:26px}}</style><h1>{title}</h1><p>{text}</p><script>{script}</script></html>')
_PAGE_SCRIPT = 'history.replaceState(null, "", "/auth/complete");'


class CallbackServer:
    """A one-shot HTTP listener on ``127.0.0.1`` for the browser's redirect (spec §8.1 step 3, devkit ``oauth.ts``).

    Only ``GET /auth/callback`` with ``Host: 127.0.0.1:<port>`` is answered; ``state`` must appear exactly once and
    match in constant time (otherwise 400 and the transaction stays open, so a stray local request cannot consume
    it). The first valid callback settles the transaction; ``wait`` returns ``{'code', 'client_id'}`` or
    ``{'error': <safe code>}``."""

    def __init__(self, state: str, saved_client_id: str | None = None):
        self._state = state.encode('utf-8')
        self._saved_client_id = saved_client_id
        self._httpd: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._result: dict = {}
        self.port = 0

    @property
    def redirect_uri(self) -> str:
        return f'http://127.0.0.1:{self.port}{CALLBACK_PATH}'

    def start(self, port: int = 0) -> int:
        server = self
        csp_hash = base64.b64encode(hashlib.sha256(_PAGE_SCRIPT.encode()).digest()).decode()
        csp = (f"default-src 'none'; script-src 'sha256-{csp_hash}'; style-src 'unsafe-inline'; "
               "frame-ancestors 'none'; base-uri 'none'")

        class Handler(http.server.BaseHTTPRequestHandler):
            timeout = 15                       # a stalled client cannot hold a handler thread

            def log_message(self, *args: Any) -> None:       # the request line carries the one-time code
                return

            def _reply(self, status: int, text: str, *, page: tuple[str, str] | None = None) -> None:
                if page is not None:
                    body = _PAGE.format(title=page[0], text=page[1], script=_PAGE_SCRIPT).encode('utf-8')
                    content_type = 'text/html; charset=utf-8'
                else:
                    body, content_type = text.encode('utf-8'), 'text/plain; charset=utf-8'
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', csp)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:        # noqa: N802 - http.server's method name
                host = f'127.0.0.1:{server.port}'
                parts = urlsplit(self.path)
                if (self.headers.get('Host') != host or (parts.netloc and parts.netloc != host)
                        or parts.path != CALLBACK_PATH or server._done.is_set()):
                    self._reply(404, 'Not found')
                    return
                query = parse_qs(parts.query, keep_blank_values=True)
                states = query.get('state', [])
                if len(states) != 1 or not hmac.compare_digest(states[0].encode('utf-8'), server._state):
                    self._reply(400, 'Invalid sign-in state. Return to the browser tab that started sign-in.')
                    return
                result = server._interpret(query)
                if 'error' in result:
                    self._reply(200, '', page=('登录未完成', '可以关闭这个标签页，回到投研工作台重新连接。'))
                else:
                    self._reply(200, '', page=('已完成，可以关闭此标签页', '投研工作台会接着检查 ChatGPT 连接。'))
                server._finish(result)

            def _not_found(self) -> None:
                self._reply(404, 'Not found')

            do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _not_found

        try:
            httpd = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
        except OSError:
            raise error_for('callback_port_unavailable') from None
        httpd.daemon_threads = True
        self._httpd = httpd
        self.port = httpd.server_address[1]
        self._thread = threading.Thread(target=httpd.serve_forever, kwargs={'poll_interval': 0.05},
                                        name='chatgpt-callback', daemon=True)
        self._thread.start()
        return self.port

    def _interpret(self, query: dict[str, list[str]]) -> dict:
        if 'error' in query:
            return {'error': safe_code(query['error'][0]) or 'api_error'}
        codes, returned = query.get('code', []), query.get('client_id', [])
        saved = self._saved_client_id
        client_id = returned[0] if returned else saved
        if (len(codes) != 1 or not codes[0] or len(returned) > 1 or not client_id or not _CLIENT_ID.fullmatch(client_id)
                or client_id == DYNAMIC_CLIENT or (saved is not None and returned and returned[0] != saved)):
            return {'error': 'registration_incomplete'}
        return {'code': codes[0], 'client_id': client_id}

    def _finish(self, result: dict) -> None:
        with self._lock:
            if self._done.is_set():
                return
            self._result = result
            self._done.set()

    def wait(self, timeout: float) -> dict:
        """The settled result, ``{'error': 'login_expired'}`` after ``timeout`` seconds, or ``{'error': 'cancelled'}``
        when the listener was closed first."""
        if not self._done.wait(max(0.0, timeout)):
            return {'error': 'login_expired'}
        return dict(self._result)

    def close(self) -> None:
        self._finish({'error': 'cancelled'})
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()


# ===================================================================================================================
# tokens
# ===================================================================================================================

def _utc(value: datetime | float | int) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromtimestamp(value, UTC)


def parse_earliest_refresh_at(value: Any) -> datetime | None:
    """``earliest_refresh_at`` as the devkit reads it: a number is epoch seconds, a string an ISO-8601 time.
    Anything else (absent or unreadable) means "no restriction" and is None."""
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, UTC) if math.isfinite(value) else None
        if isinstance(value, str) and value.strip():
            return datetime.fromtimestamp(float(value.strip()), UTC)
    except (ValueError, OverflowError, OSError):
        pass
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _jwt_claims(token: Any) -> dict:
    try:
        payload = token.split('.')[1]
        payload += '=' * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode('ascii')).decode('utf-8'))
    except (AttributeError, IndexError, ValueError, UnicodeError):
        raise error_for('invalid_id_token') from None
    if not isinstance(claims, dict):
        raise error_for('invalid_id_token')
    return claims


def check_id_token(id_token: Any, *, client_id: str, nonce: str | None = None, subject: str | None = None,
                   now: datetime) -> dict:
    """Validate an ID token's claims without checking its signature (spec D28: the token comes straight from the token
    endpoint over TLS). ``iss``, ``aud`` (and ``azp``), ``exp``, ``nonce`` when given, ``sub``; ``subject`` is the saved
    account and a different ``sub`` is ``account_mismatch``. Returns ``{'subject', 'email'}``."""
    claims = _jwt_claims(id_token)
    aud = claims.get('aud')
    audiences = aud if isinstance(aud, list) else [aud]
    exp = claims.get('exp')
    sub = claims.get('sub')
    azp = claims.get('azp')
    invalid = (
        claims.get('iss') != ISSUER
        or client_id not in audiences
        or (azp is not None and azp != client_id)
        or (len(audiences) > 1 and azp != client_id)
        or isinstance(exp, bool) or not isinstance(exp, (int, float)) or exp + 5 <= _utc(now).timestamp()
        or not isinstance(sub, str) or not sub
        or (nonce is not None and not hmac.compare_digest(str(claims.get('nonce', '')).encode('utf-8'),
                                                         nonce.encode('utf-8')))
    )
    if invalid:
        raise error_for('invalid_id_token')
    if subject is not None and sub != subject:
        raise error_for('account_mismatch')
    email = claims.get('email')
    return {'subject': sub, 'email': email if isinstance(email, str) and email else None}


def _token_fields(body: Any, *, previous_scope: str | None = None, require_refresh: bool) -> dict:
    """Validate a token endpoint response (spec §8.1 step 4): bearer, ``expires_in > 0``, access token, refresh token
    (when ``offline_access`` was granted or ``require_refresh``), a ``scope`` string (a refresh may omit it and keeps
    ``previous_scope``)."""
    bad = error_for('invalid_token_response')
    if not isinstance(body, dict):
        raise bad
    scope = body.get('scope') if body.get('scope') is not None else previous_scope      # null = omitted
    if not isinstance(scope, str):
        raise bad
    scopes = scope.split()
    access, refresh_token, expires_in = body.get('access_token'), body.get('refresh_token'), body.get('expires_in')
    if (not isinstance(access, str) or not access
            or not isinstance(body.get('token_type'), str) or body['token_type'].lower() != 'bearer'
            or isinstance(expires_in, bool) or not isinstance(expires_in, (int, float))
            or not math.isfinite(expires_in) or expires_in <= 0
            or ((require_refresh or 'offline_access' in scopes)
                and (not isinstance(refresh_token, str) or not refresh_token))):
        raise bad
    remember_secret(access)
    if isinstance(refresh_token, str):
        remember_secret(refresh_token)
    return {'access_token': access, 'refresh_token': refresh_token if isinstance(refresh_token, str) and refresh_token else None,
            'expires_in': float(expires_in), 'scopes': scopes,
            'earliest_refresh_at': parse_earliest_refresh_at(body.get('earliest_refresh_at')),
            'id_token': body.get('id_token')}


def _token_request(discovery: Mapping, form: Mapping[str, str], fetcher: Callable | None) -> Any:
    status, body = _call('chatgpt:token', 'POST', discovery['token_endpoint'], form=form,
                         headers={'Accept': 'application/json'}, policy=GRANT_POLICY, fetcher=fetcher)
    if not 200 <= status < 300:
        raise api_error(body, status)
    return body


def exchange_code(*, discovery: Mapping, client_id: str, code: str, verifier: str, redirect_uri: str, nonce: str,
                  subject: str | None, now: datetime, fetcher: Callable | None = None) -> dict:
    """Authorization code -> tokens. Returns ``_token_fields`` plus the verified ``subject`` and ``email``; the ID
    token is required and checked against ``nonce`` and the saved ``subject``."""
    body = _token_request(discovery, {
        'grant_type': 'authorization_code', 'client_id': client_id, 'code': code, 'code_verifier': verifier,
        'redirect_uri': redirect_uri, 'resource': RESOURCE}, fetcher)
    fields = _token_fields(body, require_refresh=True)
    if not isinstance(fields['id_token'], str) or not fields['id_token']:
        raise error_for('invalid_id_token')
    fields.update(check_id_token(fields.pop('id_token'), client_id=client_id, nonce=nonce, subject=subject, now=now))
    return fields


def refresh(*, discovery: Mapping, client_id: str, refresh_token: str, previous_scope: str, subject: str | None,
            now: datetime, fetcher: Callable | None = None) -> dict:
    """Refresh grant. The response must carry a new refresh token; ``scope`` may be omitted (the previous one stays);
    an ID token, when present, must still belong to ``subject``. Returns ``_token_fields`` (+ ``email`` when verified)."""
    body = _token_request(discovery, {
        'grant_type': 'refresh_token', 'client_id': client_id, 'refresh_token': refresh_token,
        'resource': RESOURCE}, fetcher)
    fields = _token_fields(body, previous_scope=previous_scope, require_refresh=True)
    id_token = fields.pop('id_token')
    if id_token is not None:
        if not isinstance(id_token, str) or not id_token:
            raise error_for('invalid_id_token')
        fields.update(check_id_token(id_token, client_id=client_id, subject=subject, now=now))
    return fields


def revoke(*, discovery: Mapping, client_id: str, refresh_token: str, fetcher: Callable | None = None) -> bool:
    """Revoke the refresh token at the issuer (a 5xx is retried once). True only when the issuer answered 200; every
    failure, including a missing ``revocation_endpoint``, is False and never raises."""
    endpoint = discovery.get('revocation_endpoint')
    if not endpoint or not refresh_token:
        return False
    try:
        status, _ = _call('chatgpt:revoke', 'POST', endpoint, form={
            'token': refresh_token, 'token_type_hint': 'refresh_token', 'client_id': client_id},
            policy=REVOKE_POLICY, fetcher=fetcher)
    except ChatGPTError:
        return False
    return status == 200


def list_models(access_token: str, *, fetcher: Callable | None = None) -> list[dict]:
    """``GET {RESOURCE}/models``: the entries with ``visibility == "list"`` in the server's order, as
    ``[{'slug', 'display_name'}]``."""
    status, body = _call('chatgpt:models', 'GET', f'{RESOURCE}/models',
                         headers={'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'},
                         fetcher=fetcher)
    if status != 200:
        raise api_error(body, status)
    if not isinstance(body, dict) or not isinstance(body.get('models'), list):
        raise error_for('invalid_model_catalog', retryable=True, status=status)
    result = []
    for model in body['models']:
        if not isinstance(model, dict) or model.get('visibility') != 'list':
            continue
        slug, name = model.get('slug'), model.get('display_name')
        if (not isinstance(slug, str) or not slug.strip() or len(slug) > 200
                or not isinstance(name, str) or not name.strip() or len(name) > 200):
            raise error_for('invalid_model_catalog', retryable=True, status=status)
        result.append({'slug': slug, 'display_name': name})
    return result


# ===================================================================================================================
# request rewrite (spec §8.5)
# ===================================================================================================================

def effective_tool_shape(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    value = (env.get('WORKBENCH_CHATGPT_TOOL_SHAPE') or '').strip()
    if not value:
        return TOOL_SHAPE
    if value not in TOOL_SHAPES:
        raise ValueError(f'WORKBENCH_CHATGPT_TOOL_SHAPE 必须是 {" / ".join(TOOL_SHAPES)} 之一')
    return value


def effective_forbidden(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    env = os.environ if env is None else env
    extra = [name.strip() for name in (env.get('WORKBENCH_CHATGPT_EXTRA_FORBIDDEN') or '').split(',') if name.strip()]
    return tuple(dict.fromkeys([*FORBIDDEN_FIELDS, *extra]))


def effective_overrides(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if env is None else env
    raw = (env.get('WORKBENCH_CHATGPT_OVERRIDES') or '').strip()
    if not raw:
        return dict(REQUEST_OVERRIDES)
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise ValueError('WORKBENCH_CHATGPT_OVERRIDES 必须是 JSON 对象') from None
    if not isinstance(parsed, dict):
        raise ValueError('WORKBENCH_CHATGPT_OVERRIDES 必须是 JSON 对象')
    return {**REQUEST_OVERRIDES, **parsed}


def effective_instructions_from_system(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    value = (env.get('WORKBENCH_CHATGPT_INSTRUCTIONS_FROM_SYSTEM') or '').strip().lower()
    if value in ('0', 'false', 'off', 'no'):
        return False
    if value in ('1', 'true', 'on', 'yes'):
        return True
    return INSTRUCTIONS_FROM_SYSTEM


def _plain_text(item: Any) -> str | None:
    """The text of a system / developer input item when its content is plain text, else None."""
    if not isinstance(item, dict) or item.get('role') not in ('system', 'developer'):
        return None
    if item.get('type') not in (None, 'message'):
        return None
    content = item.get('content')
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content and all(
            isinstance(block, dict) and block.get('type') in ('text', 'input_text')
            and isinstance(block.get('text'), str) for block in content):
        return ''.join(block['text'] for block in content)
    return None


def rewrite_payload(payload: dict, *, tool_shape: str | None = None) -> dict:
    """What ``ChatOpenAI`` built -> what the ChatGPT direct route accepts (pure; returns a copy).

    1. The leading system / developer text items of ``input`` become ``instructions`` (joined by blank lines); any other
       ``system`` item becomes ``developer`` (the route rejects ``role: system``).
    2. Function tools take the configured ``TOOL_SHAPE``: top level (unchanged), one ``namespace`` tool, or an
       ``additional_tools`` input item. Replayed ``function_call`` items lose their ``id`` (nothing is stored,
       ``store`` is false) and, under ``namespace``, carry the namespace of the tool they call.
    3. ``REQUEST_OVERRIDES`` are merged in; 4. ``FORBIDDEN_FIELDS`` are deleted, always last."""
    shape = tool_shape or effective_tool_shape()
    if shape not in TOOL_SHAPES:
        raise ValueError(f'tool_shape 必须是 {" / ".join(TOOL_SHAPES)} 之一')
    out = copy.deepcopy(payload)
    items = out.get('input')
    items = [{'role': 'user', 'content': items}] if isinstance(items, str) else list(items or [])

    if effective_instructions_from_system():
        head: list[str] = []
        while items and _plain_text(items[0]) is not None:
            head.append(_plain_text(items.pop(0)) or '')
        text = '\n\n'.join(part for part in [out.get('instructions'), *head] if part)
        if text:
            out['instructions'] = text
    for item in items:
        if isinstance(item, dict) and item.get('role') == 'system':
            item['role'] = 'developer'

    wrapped: set[str] = set()
    tools = out.get('tools')
    if isinstance(tools, list):
        functions = [tool for tool in tools if isinstance(tool, dict) and tool.get('type') == 'function']
        others = [tool for tool in tools if not (isinstance(tool, dict) and tool.get('type') == 'function')]
        if functions and shape == 'namespace':
            wrapped = {str(tool.get('name')) for tool in functions}
            out['tools'] = [*others, {'type': 'namespace', **NAMESPACE, 'tools': functions}]
        elif functions and shape == 'additional_tools':
            items.insert(0, {'type': 'additional_tools', 'role': 'developer', 'tools': functions})
            if others:
                out['tools'] = others
            else:
                out.pop('tools')
    for item in items:
        if isinstance(item, dict) and item.get('type') == 'function_call':
            item.pop('id', None)
            if item.get('name') in wrapped:
                item.setdefault('namespace', NAMESPACE['name'])
    out['input'] = items

    out.update(copy.deepcopy(effective_overrides()))
    for name in effective_forbidden():
        out.pop(name, None)
    return out
