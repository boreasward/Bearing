from __future__ import annotations

import logging
import time
from typing import Any, Callable

from investment_assistant.data import fetch_policy
from investment_assistant.data.fetch_policy import FetchError, FetchPolicy

logger = logging.getLogger(__name__)
# Used by data.sources.fundamentals, which runs its own retry loop over these.
_RETRYABLE = set(fetch_policy.RETRY_STATUSES)


def _backoff(attempt: int, base: float) -> float:
    return min(base * (2 ** attempt), 30.0)


def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    policy: FetchPolicy = FetchPolicy(),
    target: str | None = None,
    sleep: Callable[[float], None] | None = None,
    redact: Callable[[str], str] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """GET JSON under the ``fetch-1`` policy (connect 10 s, total 30 s, two retries after 2 s and 8 s).

    Returns ``(data, meta)`` with ``meta = {ok, error, status_code, attempts, error_kind}``; ``attempts`` is the
    list of attempt records. ``redact`` scrubs secrets (e.g. an API key in the URL) from the log lines and from the
    returned error text. Only 200 is a success; 4xx fails at once, 429 / 5xx / timeouts / connection errors retry."""
    def _clean(text: str) -> str:
        return redact(text) if redact else text

    def attempt() -> dict[str, Any]:
        with fetch_policy.policy_session(policy) as session:
            resp = session.get(url, params=params, headers=headers)
        if resp.status_code != 200:
            raise fetch_policy.http_error(
                resp.status_code, f"http {resp.status_code}: {resp.text[:300]}",
                retry_after=resp.headers.get('Retry-After'))
        try:
            return resp.json()
        except ValueError as exc:
            raise FetchError(f'bad json: {exc}', kind='parse', status=200) from None

    try:
        fetched = fetch_policy.fetch(target or url, attempt, policy=policy, sleep=sleep or time.sleep, redact=redact)
    except FetchError as exc:
        error = _clean(str(exc))
        logger.warning('http get failed after %s attempt(s): %s', len(exc.attempts), error)
        return None, {'ok': False, 'error': error, 'status_code': exc.status, 'attempts': exc.attempts,
                      'error_kind': exc.kind}
    return fetched.value, {'ok': True, 'error': None, 'status_code': 200, 'attempts': fetched.attempts,
                           'error_kind': None}
