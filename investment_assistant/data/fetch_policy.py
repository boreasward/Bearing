"""Data fetch-1 policy: shared HTTP primitives and yfinance's session/deadline."""
from __future__ import annotations

import threading

from investment_assistant.core.http import (
    CONNECT_TIMEOUT_S, TOTAL_TIMEOUT_S, RETRY_BACKOFF_S, RETRY_STATUSES, RETRY_AFTER_CAP_S,
    ERROR_KINDS, RETRYABLE_KINDS, FETCH_POLICY_VERSION, FetchPolicy, FetchError, Fetched,
    kind_for_status, parse_retry_after, http_error, classify_exception, request_never_sent,
    fetch, worst_case_s, hard_deadline, policy_session,
)
from investment_assistant.core.redact import sanitize_reason

YF_HARD_DEADLINE_S = 35             # thread-level cut-off around yfinance (spec §6.3)


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

            # Let Yahoo transport errors reach fetch() instead of swallowing them as empty frames.
            yf.config.debug.hide_exceptions = False
            _SHARED_YF.append(yf_session())
        return _SHARED_YF[0]


def drop_yf_response_cache() -> None:
    """Empty yfinance's response cache (``YfData.cache_get``, an lru_cache of 64 responses).

    ``history()`` of a past range is fetched through that cache, and every cached curl_cffi response keeps the curl
    handle of the thread that fetched it; ``hard_deadline`` runs each call on a fresh thread, so the cache pinned up to
    64 handles (a socket plus a pipe pair each) — about 200 descriptors, which ran a launchd job (soft limit 256) out of
    files. Each ticker is fetched once per run, so the cache saves nothing here."""
    from yfinance.data import YfData

    YfData().cache_get.cache_clear()
