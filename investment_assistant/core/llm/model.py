"""Agent model wiring: settings, the Sign-in-with-ChatGPT client, error classification, checkpoints (v2.8 spec §8).

The Agent runs on the user's ChatGPT subscription: ``AgentSettings`` reads the stored connection (never a key from the
environment), ``chat_model`` takes a fresh access token and builds ``SiwcChatOpenAI`` — ``ChatOpenAI`` in Responses
mode whose request body is rewritten for the ChatGPT direct route (``chatgpt.rewrite_payload``). Tokens are scrubbed
from every error detail; a quota error code stops the agent track for the rest of the run, anything else lands as
``model_error`` or, when it means the connection / model is unusable, ``not_connected`` / ``model_unavailable``.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, ContextManager, Mapping

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI

from investment_assistant.core.llm import connection as agent_connection
from investment_assistant.core.llm import chatgpt
from investment_assistant.core.llm.chatgpt import ChatGPTError
from investment_assistant.core.redact import redact
from investment_assistant.core import versions

log = logging.getLogger(__name__)

PROVIDERS = ('chatgpt', 'off')
TIMEOUT = httpx.Timeout(180, connect=10)

# Responses error codes by outcome (spec §8.8)
QUOTA_CODES = frozenset({'subscription_sharing_usage_limit_exceeded'})
MODEL_UNAVAILABLE_CODES = frozenset({'model_not_found'})
# §8.8 row 4 codes that prove the connection itself unusable (the model call is not retried after a fresh login):
RELOGIN_CODES = frozenset({
    'subscription_sharing_user_not_eligible', 'subscription_sharing_invalid_user', 'chatpass_v2_scope_not_authorized',
    'chatpass_v2_invalid_authorization_context',
})
UNAUTHORIZED_CODES = frozenset({'invalid_token', 'invalid_api_key'})          # like an HTTP 401: refresh first
NOT_CONNECTED_CODES = frozenset({
    'subscription_sharing_user_not_eligible', 'subscription_sharing_invalid_user', 'chatpass_v2_scope_not_authorized',
    'chatpass_v2_invalid_authorization_context', 'invalid_token', 'invalid_api_key',
    'not_connected',                    # agent_connection.access_token(): no usable connection
}) | chatgpt.REAUTH_CODES


@dataclass(frozen=True)
class AgentSettings:
    provider: str                       # 'chatgpt' | 'off'
    model: str | None                   # agent_connections.model; '' when none is chosen (agent_runs.model is NOT NULL)
    api_key: str | None = field(repr=False)     # only for scrubbing: the stored access token, never an API key
    daily_tokens: int | None
    config_error: str | None
    connection: str = 'connected'       # 'connected' | 'needs_login' | 'disconnected' | 'pending' | 'no_sharing' | 'unknown'


def settings_from_env(env: Mapping[str, str] = os.environ, *,
                      read_connection: Callable[[], dict] | None = None) -> AgentSettings:
    daily_tokens, config_error = None, None
    raw = env.get('WORKBENCH_AGENT_DAILY_TOKENS')
    if raw:
        try:
            parsed = int(raw)
        except ValueError:
            parsed = 0
        if parsed > 0:
            daily_tokens = parsed
        else:
            config_error = 'WORKBENCH_AGENT_DAILY_TOKENS 必须是正整数'
    provider = env.get('WORKBENCH_AGENT_PROVIDER') or 'chatgpt'
    if provider not in PROVIDERS:
        config_error = config_error or 'WORKBENCH_AGENT_PROVIDER 只能是 chatgpt 或 off'
    model, secret, connection = '', None, 'unknown'
    if provider != 'off':                # a disabled agent does not need the connection
        try:
            info = (read_connection or agent_connection.read_for_settings)()
            connection = info['status']
            if connection == 'connected' and not info.get('scopes_ok'):
                connection = 'no_sharing'
            model, secret = info.get('model') or '', info.get('secret')
        except Exception:                # noqa: BLE001 - unreadable (table missing, database down): say so, never raise
            connection = 'unknown'
            config_error = config_error or '连接状态不可读'
    return AgentSettings(provider=provider, model=model, api_key=secret, daily_tokens=daily_tokens,
                         config_error=config_error, connection=connection)


def unavailable_reason(s: AgentSettings) -> str | None:
    if s.provider == 'off':
        return 'disabled'
    if s.config_error or s.connection == 'no_sharing':        # signed in, but the account shares no plan usage
        return 'not_configured'
    if s.connection != 'connected':
        return 'not_connected'
    if not s.model:
        return 'model_unavailable'
    return None


# ---------------------------------------------------------------------------------------------------------------------
# the model
# ---------------------------------------------------------------------------------------------------------------------

_CODE_IN_TEXT = (re.compile(r"code='([A-Za-z0-9_.:-]{1,100})'"), re.compile(r'^([A-Za-z0-9_.:-]{1,100}): '))


def _flatten(content: Any) -> Any:
    """Responses text arrives as ``[{'type': 'text', 'text': …}]`` even with ``output_version='v0'``; the subgraphs
    read ``message.content`` as a string."""
    if isinstance(content, list) and all(isinstance(block, dict) and block.get('type') == 'text' for block in content):
        return ''.join(str(block.get('text') or '') for block in content)
    return content


def _failure_from(exc: ValueError) -> ChatGPTError | None:
    """langchain-openai turns a ``response.failed`` / ``error`` event into a bare ``ValueError`` whose text starts
    with the error code; rebuild the structured error from the code alone (the message text is dropped)."""
    if type(exc) is not ValueError:
        return None
    text = str(exc)
    for pattern in _CODE_IN_TEXT:
        found = pattern.search(text)
        if found:
            return chatgpt.api_error({'error': {'code': found.group(1)}})
    return None


class SiwcChatOpenAI(ChatOpenAI):
    """``ChatOpenAI`` for the ChatGPT direct route (spec §8.5): the request body goes through
    ``chatgpt.rewrite_payload``; the stream is read for failure and completion and its text blocks become one string.

    With ``report_connection_failures`` (set for models built on the stored connection) a failure that proves the
    connection unusable is also written back to it (spec §8.8 row 4); the error itself is always re-raised."""

    report_connection_failures: bool = False

    def _get_request_payload(self, input_, *, stop: list[str] | None = None, **kwargs: Any) -> dict:
        return chatgpt.rewrite_payload(super()._get_request_payload(input_, stop=stop, **kwargs))

    def _report_failure(self, exc: BaseException) -> None:
        if not self.report_connection_failures:
            return
        try:
            facts = _facts(exc)
            if facts is None:
                return
            code = chatgpt.canonical_code(facts.code)
            if code in RELOGIN_CODES:
                agent_connection.mark_needs_login(code)
            elif facts.status == 401 or code in UNAUTHORIZED_CODES:
                agent_connection.report_unauthorized(self.openai_api_key.get_secret_value())
        except Exception as inner:        # noqa: BLE001 - never hide the model error behind a bookkeeping failure
            log.warning('recording a connection failure failed: %s', type(inner).__name__)

    def _stream(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager=None,
                **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        completed = False
        try:
            for chunk in super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
                message = chunk.message
                message.content = _flatten(message.content)
                if message.response_metadata.get('status') == 'incomplete':
                    raise chatgpt.error_for('response_incomplete', retryable=True)
                if (message.response_metadata.get('status') == 'completed'
                        or getattr(message, 'chunk_position', None) == 'last'):
                    completed = True
                yield chunk
        except ValueError as exc:
            failure = _failure_from(exc)
            if failure is None:
                raise
            self._report_failure(failure)
            raise failure from None
        except Exception as exc:
            self._report_failure(exc)
            raise
        if not completed:
            raise chatgpt.error_for('stream_interrupted', retryable=True)


_SHARED_CLIENT: list[httpx.Client] = []
_SHARED_LOCK = threading.Lock()


def _strip_account_headers(request: httpx.Request) -> None:
    """This is a ChatGPT account, not a Platform API account: an OPENAI_ORG_ID / OPENAI_PROJECT_ID in the environment
    must not reach the request (the SDK reads them on its own)."""
    for name in ('openai-organization', 'openai-project'):
        if name in request.headers:
            del request.headers[name]


def _http_client(transport: httpx.BaseTransport | None) -> httpx.Client:
    """The HTTP client of a model. One shared client for production (connection reuse, the environment's proxy
    variables apply); an explicit ``transport`` (tests) gets its own."""
    hooks = {'request': [_strip_account_headers]}
    if transport is not None:
        return httpx.Client(transport=transport, timeout=TIMEOUT, event_hooks=hooks)
    with _SHARED_LOCK:
        if not _SHARED_CLIENT:
            _SHARED_CLIENT.append(httpx.Client(timeout=TIMEOUT, event_hooks=hooks))
        return _SHARED_CLIENT[0]


def chat_model(s: AgentSettings, *, token_provider: Callable[[], str] | None = None,
               transport: httpx.BaseTransport | None = None, report_failures: bool | None = None) -> BaseChatModel:
    """A model bound to a fresh access token (refreshed when due). Built once per Agent unit (D32).

    ``report_failures`` (default: only when the token comes from the stored connection) lets a model error that proves
    the connection unusable move it to ``needs_login`` (``agent_connection.mark_needs_login`` /
    ``report_unauthorized``)."""
    token = (token_provider or agent_connection.access_token)()
    chatgpt.remember_secret(token)
    return SiwcChatOpenAI(
        model=s.model, api_key=token, base_url=chatgpt.RESOURCE,
        use_responses_api=True, output_version='v0', store=False, streaming=True, temperature=None,
        max_retries=0, timeout=TIMEOUT, http_client=_http_client(transport), http_socket_options=(),
        default_headers={'Accept': 'text/event-stream'},          # every call streams (the devkit sends the same)
        report_connection_failures=token_provider is None if report_failures is None else report_failures,
    )


# ---------------------------------------------------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------------------------------------------------

def _facts(exc: BaseException) -> ChatGPTError | None:
    """The structured error behind ``exc``: itself, or one built from an HTTP error's status and body (openai's
    ``APIStatusError`` and anything that looks like it), looked up along the cause chain."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(seen) < 6:
        seen.add(id(current))
        if isinstance(current, ChatGPTError):
            return current
        status = getattr(current, 'status_code', None)
        if isinstance(status, int):
            return chatgpt.api_error(getattr(current, 'body', None), status)
        current = current.__cause__ or current.__context__
    return None


def classify_error(exc: BaseException, secret: str | None) -> tuple[str, str]:
    """``(reason, detail)`` for a failed model call (spec §8.8). ``detail`` is scrubbed of ``secret``, every token this
    process handed out, ``Bearer …`` and JWT-like fragments, never carries a request body, and is cut to 300 chars."""
    facts = _facts(exc)
    if facts is None:
        detail = f'{type(exc).__name__}: {exc}'
        return 'model_error', chatgpt.redact(redact(detail, secret), secret)[:300]
    code, status = chatgpt.canonical_code(facts.code), facts.status
    if code in QUOTA_CODES:
        reason = 'quota_exhausted'
    elif code in MODEL_UNAVAILABLE_CODES:
        reason = 'model_unavailable'
    elif code in NOT_CONNECTED_CODES or status == 401:
        reason = 'not_connected'
    else:
        reason = 'model_error'
    parts = [f'{type(exc).__name__}: {facts.code}']
    if status is not None:
        parts.append(f'HTTP {status}')
    if facts.param:
        parts.append(f'param={facts.param}')
    return reason, chatgpt.redact(redact(' '.join(parts) + f' — {facts.message}', secret), secret)[:300]


@contextmanager
def checkpointer(url: str) -> ContextManager:
    from langgraph.checkpoint.postgres import PostgresSaver
    with PostgresSaver.from_conn_string(url) as saver:
        yield saver


def setup_checkpoints(url: str) -> None:
    with checkpointer(url) as saver:
        saver.setup()


def _agent_run_row(unit: dict, *, settings, attempt: int, now: datetime, status: str,
                   run_id: str | None = None, reason: str | None = None, detail: str | None = None,
                   output: dict | None = None,
                   counter: dict | None = None, plan_id=None, input_tokens: int = 0,
                   output_tokens: int = 0, mode: str = 'propose', agent_version: str | None = None) -> dict:
    return {'id': run_id or str(uuid.uuid4()), 'ticker': unit['ticker'],
            'signal_date': unit['signal_date'], 'pool': unit['pool'], 'attempt': attempt,
            'mode': mode,
            # Keep the current runtime versions until the workflow upgrade.
            'agent_version': agent_version or {'interpret': versions.INTERPRET_VERSION,
                                               'review': versions.REVIEW_VERSION,
                                               'retro': versions.RETRO_AGENT_V2,
                                               'discuss': versions.DISCUSS_AGENT_VERSION}.get(mode, versions.AGENT_V3),
            'provider': settings.provider,
            'model': settings.model, 'cutoff_at': now, 'status': status, 'reason': reason, 'detail': detail,
            'output': output, 'counter': counter, 'plan_id': plan_id,
            'input_tokens': input_tokens, 'output_tokens': output_tokens,
            'started_at': now, 'finished_at': now}


def _checkpoint_missing(exc: BaseException) -> bool:
    """Missing checkpoint tables/columns are checkpoint_unavailable, including wrapped errors."""
    try:
        import psycopg.errors
        table_errors = (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn)
    except ImportError:   # pragma: no cover - psycopg is a hard dependency in practice
        return False
    current, seen = exc, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, table_errors):
            return True
        current = current.__cause__ or current.__context__
    return False
