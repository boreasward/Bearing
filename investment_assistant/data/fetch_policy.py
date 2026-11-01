"""External request policy ``fetch-1`` (v2.8 spec §6): tight timeouts, a bounded retry, per-attempt records.

One place defines "connect 10 s, total 30 s, retry twice after 2 s and 8 s, never retry a 4xx"; every call site only
names its target. ``fetch`` runs a callable under that policy and records every attempt, so the data page can show
per target how many attempts it took, how long, and why it failed.

Retryable kinds are timeout, network, http_5xx (500 / 502 / 503 / 504) and http_429; everything else (4xx, empty
result, parse error, anything unclassified) fails at once. A POST that may not be sent twice uses
``FetchPolicy(retry_post=False)``: it is retried only when the request provably never left (connection refused,
DNS failure, connect timeout) or the server said 429.
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Generic, TypeVar

import requests
from requests.adapters import HTTPAdapter

from investment_assistant.core.versions import FETCH_POLICY_VERSION as FETCH_POLICY_VERSION

CONNECT_TIMEOUT_S = 10
TOTAL_TIMEOUT_S = 30
RETRY_BACKOFF_S = (2, 8)            # two retries
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_AFTER_CAP_S = 10              # a 429 never makes one target wait longer than this in total
YF_HARD_DEADLINE_S = 35             # thread-level cut-off around yfinance (spec §6.3)

ERROR_KINDS = ('timeout', 'network', 'http_5xx', 'http_429', 'http_4xx', 'empty', 'parse', 'other')
RETRYABLE_KINDS = frozenset({'timeout', 'network', 'http_5xx', 'http_429'})

T = TypeVar('T')


@dataclass(frozen=True)
class FetchPolicy:
    connect_s: float = CONNECT_TIMEOUT_S
    total_s: float = TOTAL_TIMEOUT_S
    backoff_s: tuple[float, ...] = RETRY_BACKOFF_S
    retry_post: bool = True          # notification calls pass False (D23)


class FetchError(RuntimeError):
    """A target that could not be fetched. ``attempts`` is ``[{'n', 'kind', 'status', 'elapsed_ms'}]``.

    Raised from inside a ``fetch`` callable with an empty ``attempts`` it is a classified single failure the
    enclosing ``fetch`` may retry; one that already carries attempts is final (an inner ``fetch`` gave up)."""

    def __init__(self, message: str, *, kind: str = 'other', status: int | None = None,
                 attempts: list[dict] | None = None, target: str | None = None,
                 retry_after: float | None = None, total_ms: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.attempts = list(attempts or [])
        self.target = target
        self.retry_after = retry_after
        self.total_ms = total_ms          # wall clock of the whole fetch, backoff waits included

    @property
    def elapsed_ms(self) -> int:
        """Wall-clock total including backoff waits (spec §6.4: 3 x 30 s + 10 s = 100412 ms); the sum of the
        attempts when the error was built by hand."""
        if self.total_ms is not None:
            return self.total_ms
        return sum(int(a.get('elapsed_ms') or 0) for a in self.attempts)


@dataclass
class Fetched(Generic[T]):
    value: T
    attempts: list[dict] = field(default_factory=list)
    elapsed_ms: int = 0


def kind_for_status(status: int) -> str:
    if status == 429:
        return 'http_429'
    if status >= 500:
        return 'http_5xx'
    if status >= 400:
        return 'http_4xx'
    return 'other'


def parse_retry_after(value: Any) -> float | None:
    """Seconds from a ``Retry-After`` header (delta-seconds or HTTP-date); None when absent or unreadable."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def http_error(status: int, message: str, *, retry_after: float | str | None = None) -> FetchError:
    """A classified HTTP failure to raise from inside a ``fetch`` callable."""
    wait = retry_after if isinstance(retry_after, (int, float)) else parse_retry_after(retry_after)
    return FetchError(message, kind=kind_for_status(status), status=status, retry_after=wait)


def _chain(exc: BaseException):
    seen: set[int] = set()
    stack = [exc]
    while stack:
        item = stack.pop()
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        yield item
        # ``reason`` is an exception on urllib3 / requests errors but a plain str on ``ssl.SSLError`` and
        # ``urllib.error.HTTPError``: only exceptions continue the chain.
        stack.extend(x for x in (item.__cause__, item.__context__, getattr(item, 'reason', None))
                     if isinstance(x, BaseException))
        stack.extend(a for a in getattr(item, 'args', ()) if isinstance(a, BaseException))


def _curl_exceptions():
    try:
        from curl_cffi.requests import exceptions as cffi
    except Exception:       # curl_cffi missing: only requests-based call sites exist then
        return None
    return cffi


def classify_exception(exc: BaseException) -> tuple[str, int | None]:
    """Map an exception to ``(kind, http status)``; see ``ERROR_KINDS``."""
    if isinstance(exc, FetchError):
        return exc.kind, exc.status
    custom = getattr(exc, 'fetch_kind', None)
    if custom in ERROR_KINDS:
        return custom, getattr(exc, 'status', None)
    if type(exc).__name__ == 'YFRateLimitError':
        return 'http_429', 429
    if isinstance(exc, requests.Timeout):
        return 'timeout', None
    # urllib3 errors raised while the body is read are not wrapped by requests
    import urllib3.exceptions as u3
    if isinstance(exc, (u3.TimeoutError, u3.ReadTimeoutError)):
        return 'timeout', None
    if isinstance(exc, (u3.ProtocolError, u3.NewConnectionError, u3.NameResolutionError, u3.SSLError,
                        u3.ProxyError, u3.DecodeError, u3.IncompleteRead)):
        return 'network', None
    cffi = _curl_exceptions()
    if cffi is not None and isinstance(exc, cffi.Timeout):
        return 'timeout', None
    response = getattr(exc, 'response', None)
    status = getattr(response, 'status_code', None)
    if isinstance(exc, requests.HTTPError) or (cffi is not None and isinstance(exc, cffi.HTTPError)):
        if isinstance(status, int):
            return kind_for_status(status), status
        return 'other', None
    if isinstance(exc, (requests.ConnectionError, requests.exceptions.ChunkedEncodingError)):
        return 'network', None
    if cffi is not None and isinstance(exc, (cffi.ConnectionError, cffi.SSLError, cffi.ProxyError, cffi.DNSError)):
        return 'network', None
    if isinstance(exc, TimeoutError):
        return 'timeout', None
    if isinstance(exc, ConnectionError):
        return 'network', None
    if isinstance(exc, json.JSONDecodeError):
        return 'parse', None
    return 'other', None


def _retry_after_of(exc: BaseException) -> float | None:
    if isinstance(exc, FetchError):
        return exc.retry_after
    headers = getattr(getattr(exc, 'response', None), 'headers', None)
    if headers is not None:
        try:
            return parse_retry_after(headers.get('Retry-After'))
        except Exception:
            return None
    return None


def request_never_sent(exc: BaseException) -> bool:
    """True when the failure proves the request did not leave the machine (refused, DNS, connect timeout)."""
    from urllib3.exceptions import ConnectTimeoutError, NameResolutionError, NewConnectionError

    if isinstance(exc, requests.ConnectTimeout):
        return True
    for item in _chain(exc):
        if isinstance(item, (NewConnectionError, ConnectTimeoutError, NameResolutionError)):
            return True
        code = getattr(item, 'code', None)
        if type(item).__module__.startswith('curl_cffi') and code in (6, 7):    # could not resolve / connect
            return True
    return False


def _retryable(kind: str, status: int | None, exc: BaseException, policy: FetchPolicy) -> bool:
    if kind not in RETRYABLE_KINDS:
        return False
    if kind in ('http_5xx', 'http_429') and status is not None and status not in RETRY_STATUSES:
        return False
    if not policy.retry_post and kind != 'http_429' and not request_never_sent(exc):
        return False
    return True


_WEBHOOK = re.compile(r'/api/webhooks/\d+/[\w-]+')


def _clean(text: str, redact: Callable[[str], str] | None) -> str:
    if redact is not None:
        text = redact(text)
    text = _WEBHOOK.sub('/api/webhooks/<redacted>', text)       # a Discord webhook token is a credential
    try:
        from investment_assistant.workbench import families
        return families.sanitize_reason(text)
    except Exception:
        return text[:300]


def _message(kind: str, status: int | None, exc: BaseException, policy: FetchPolicy, attempts: int,
             redact: Callable[[str], str] | None) -> str:
    if isinstance(exc, FetchError):
        base = str(exc)
    elif kind == 'timeout':
        base = f'timeout after {policy.total_s:g}s'
    elif kind == 'network':
        # Exception texts of connection errors repeat the full request URL (webhook tokens, API keys): keep only
        # the exception type names, the way EdgarSource does.
        names = [type(item).__name__ for item in _chain(exc)
                 if item is exc or type(item).__module__.split('.')[0] in ('requests', 'urllib3', 'curl_cffi')]
        base = 'network error: ' + ' / '.join(dict.fromkeys(names))
    elif kind in ('http_4xx', 'http_5xx', 'http_429'):
        base = f'HTTP {status}'
    else:
        base = str(exc) or type(exc).__name__
    base = _clean(base, redact)[:270]
    return f'{base} ({attempts} attempts)' if attempts > 1 else base


def fetch(target: str, fn: Callable[[], T], *, policy: FetchPolicy = FetchPolicy(), sleep=time.sleep,
          classify: Callable[[BaseException], tuple[str, int | None]] = classify_exception,
          redact: Callable[[str], str] | None = None) -> Fetched[T]:
    """Call ``fn`` under ``policy``; retry retryable kinds after ``policy.backoff_s``, fail the rest at once.

    Returns ``Fetched(value, attempts, elapsed_ms)``; a final failure raises ``FetchError`` carrying every attempt.
    A 429 waits ``min(Retry-After, backoff)`` and never more than ``RETRY_AFTER_CAP_S`` in total. An inner
    ``FetchError`` that already has attempts is final: the inner ``fetch`` has retried, so nothing is retried twice."""
    attempts: list[dict] = []
    started = time.monotonic()
    waited_429 = 0.0
    n = 0
    while True:
        n += 1
        t0 = time.monotonic()
        try:
            value = fn()
        except Exception as exc:
            elapsed = round((time.monotonic() - t0) * 1000)
            kind, status = classify(exc)
            if isinstance(exc, FetchError) and exc.attempts:
                if exc.target is None:
                    exc.target = target
                raise
            attempts.append({'n': n, 'kind': kind, 'status': status, 'elapsed_ms': elapsed})
            if n <= len(policy.backoff_s) and _retryable(kind, status, exc, policy):
                delay = float(policy.backoff_s[n - 1])
                if kind == 'http_429':
                    retry_after = _retry_after_of(exc)
                    if retry_after is not None:
                        delay = min(delay, retry_after)
                    delay = max(0.0, min(delay, RETRY_AFTER_CAP_S - waited_429))
                    waited_429 += delay
                sleep(delay)
                continue
            raise FetchError(_message(kind, status, exc, policy, n, redact), kind=kind, status=status,
                             attempts=attempts, target=target, retry_after=_retry_after_of(exc),
                             total_ms=round((time.monotonic() - started) * 1000)) from None
        attempts.append({'n': n, 'kind': None, 'status': 200, 'elapsed_ms': round((time.monotonic() - t0) * 1000)})
        return Fetched(value, attempts, round((time.monotonic() - started) * 1000))


def worst_case_s(policy: FetchPolicy = FetchPolicy(), *, per_attempt_s: float | None = None) -> float:
    """Longest one target can take: every attempt times out, plus every backoff wait (default 3 x 30 + 2 + 8)."""
    per_attempt = policy.total_s if per_attempt_s is None else per_attempt_s
    return (len(policy.backoff_s) + 1) * per_attempt + sum(policy.backoff_s)


def hard_deadline(fn: Callable[[], T], *, seconds: float) -> T:
    """Run ``fn`` in a daemon thread and give up after ``seconds`` with ``FetchError(kind='timeout')``.

    The abandoned thread is left to its own library-level timeout; this is only the backstop for libraries that
    can hang outside the transport (yfinance, spec §6.3). Exceptions from ``fn`` are re-raised in the caller."""
    box: dict[str, Any] = {}
    done = threading.Event()

    def run() -> None:
        try:
            box['value'] = fn()
        except BaseException as exc:         # noqa: BLE001 - handed to the caller below
            box['error'] = exc
        finally:
            done.set()

    threading.Thread(target=run, name='fetch-hard-deadline', daemon=True).start()
    if not done.wait(seconds):
        raise FetchError(f'timeout after {seconds:g}s', kind='timeout')
    if 'error' in box:
        raise box['error']
    return box['value']


class _PolicySession(requests.Session):
    """``requests`` session with a hard per-attempt deadline: connect + headers + body together take at most
    ``policy.total_s`` (spec D19, the ``(connect, total)`` timeouts only bound single socket operations).

    The request runs in a daemon thread the caller waits on for ``total_s``; on expiry the caller gets
    ``requests.ReadTimeout`` and the abandoned thread stops at its next chunk or socket timeout. Errors urllib3
    raises while the body is read (``ReadTimeoutError``, ``ProtocolError``) are turned into the matching
    ``requests`` exceptions, so they classify as timeout / network and are retried."""

    def __init__(self, policy: FetchPolicy):
        super().__init__()
        self.policy = policy
        adapter = HTTPAdapter(max_retries=0)
        self.mount('http://', adapter)
        self.mount('https://', adapter)

    def request(self, method, url, **kwargs):          # noqa: D102
        policy = self.policy
        kwargs.setdefault('timeout', (policy.connect_s, policy.total_s))
        if kwargs.get('stream'):
            return super().request(method, url, **kwargs)
        deadline = time.monotonic() + policy.total_s
        abandoned = threading.Event()

        def run() -> requests.Response:
            kwargs['stream'] = True
            response = super(_PolicySession, self).request(method, url, **kwargs)
            try:
                response._content = _read_body(response, deadline, abandoned)
            except BaseException:
                response.close()
                raise
            response._content_consumed = True
            return response

        try:
            return hard_deadline(run, seconds=policy.total_s)
        except FetchError:
            abandoned.set()
            raise requests.exceptions.ReadTimeout(f'total timeout of {policy.total_s:g}s exceeded') from None


def _read_body(response: requests.Response, deadline: float, abandoned: threading.Event) -> bytes:
    import urllib3.exceptions as u3

    def check() -> None:
        if abandoned.is_set() or time.monotonic() > deadline:
            raise requests.exceptions.ReadTimeout('total timeout exceeded')

    chunks: list[bytes] = []
    raw = response.raw
    try:
        if hasattr(raw, 'read1'):     # returns as soon as some bytes arrive, so a trickle cannot hide the clock
            while True:
                chunk = raw.read1(65536, decode_content=True)
                if not chunk:
                    break
                chunks.append(chunk)
                check()
        else:
            for chunk in response.iter_content(chunk_size=65536):
                chunks.append(chunk)
                check()
    except (u3.TimeoutError, u3.ReadTimeoutError) as exc:
        raise requests.exceptions.ReadTimeout(type(exc).__name__) from None
    except (u3.ProtocolError, u3.DecodeError, u3.HTTPError) as exc:
        raise requests.exceptions.ChunkedEncodingError(type(exc).__name__) from None
    return b''.join(chunks)


def policy_session(policy: FetchPolicy = FetchPolicy()) -> requests.Session:
    """A ``requests.Session`` that follows ``policy``: connect/total timeouts, no hidden retries."""
    return _PolicySession(policy)


def yf_session(policy: FetchPolicy = FetchPolicy()):
    """The ``curl_cffi`` session yfinance runs on: chrome impersonation, libcurl total + connect timeouts.

    libcurl's total timeout is the only switch that really interrupts a stuck transfer (spec §6.3)."""
    from curl_cffi import CurlOpt
    from curl_cffi import requests as cffi_requests

    return cffi_requests.Session(impersonate='chrome', timeout=policy.total_s,
                                 curl_options={CurlOpt.CONNECTTIMEOUT_MS: max(1, round(policy.connect_s * 1000))})


_SHARED_YF: list = []
_SHARED_YF_LOCK = threading.Lock()


def shared_yf_session():
    """The one process-wide ``yf_session()`` the yfinance fetchers use (lazily created, thread-safe).

    yfinance keeps a single cookie / crumb per process; handing it a fresh session per call would orphan the cookie
    jar each time and make Yahoo's crumb check fail and refetch. Built with the default ``fetch-1`` numbers."""
    with _SHARED_YF_LOCK:
        if not _SHARED_YF:
            import yfinance as yf

            # yfinance's default swallows every transport error inside ``history()`` / the timezone lookup and hands
            # back an empty frame ("possibly delisted; no price data found"), which the callers can only classify as
            # a final ``empty``. With exceptions visible a proxy or connection failure reaches ``fetch`` as
            # ``network`` / ``timeout`` and is retried; Yahoo's own "no data" answers stay typed exceptions the
            # callers map to ``empty`` themselves (2026-10-31 ``benchmark_missing``).
            yf.config.debug.hide_exceptions = False
            _SHARED_YF.append(yf_session())
        return _SHARED_YF[0]
