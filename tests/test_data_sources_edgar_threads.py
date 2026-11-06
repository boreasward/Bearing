"""One EdgarSource shared by a worker pool (run_adapter workers > 1): throttle, CIK map and accounting per thread."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from investment_assistant.data.adapter_support import sec_outcome
from investment_assistant.data.fetch_policy import FetchPolicy, Fetched
from investment_assistant.data.sources import edgar

A = 'https://data.sec.gov/submissions/CIK0000000001.json'
B = 'https://data.sec.gov/submissions/CIK0000000002.json'


def ok(value=None):
    return SimpleNamespace(status_code=200, headers={}, content=json.dumps(value or {'ok': True}).encode())


@pytest.fixture(autouse=True)
def user_agent(monkeypatch):
    monkeypatch.setenv('SEC_USER_AGENT', 'offline-fixture threads@example.invalid')


def make(transport, tmp_path):
    return edgar.EdgarSource(tmp_path / 'evidence', session=transport, policy=FetchPolicy(backoff_s=(0, 0)),
                             sleep=lambda _: None)


def run_threads(*targets):
    threads = [threading.Thread(target=t) for t in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)


def test_concurrent_requests_still_respect_the_throttle(tmp_path):
    stamps, lock = [], threading.Lock()

    class Transport:
        def get(self, url, **kwargs):
            with lock:
                stamps.append(time.monotonic())
            return ok()

    source = make(Transport(), tmp_path)
    run_threads(*[(lambda i=i: source.get(f'https://data.sec.gov/submissions/CIK{i:010d}.json'))
                  for i in range(5)])
    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(stamps) == 5 and min(gaps) >= 0.19          # 约 5 次/秒，不因多线程突发


def test_each_thread_books_only_its_own_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar.time, 'sleep', lambda _: None)
    b_done = threading.Event()
    calls = {A: 0}

    class Transport:
        def get(self, url, **kwargs):
            if url == A:
                calls[A] += 1
                if calls[A] == 1:
                    assert b_done.wait(5)                   # B 的整次请求落在 A 的记账区间内
                    return SimpleNamespace(status_code=503, headers={}, content=b'')
            return ok()

    source = make(Transport(), tmp_path)
    results = {}

    def thread_a():
        results['a'] = sec_outcome(source, lambda: source.get(A))

    def thread_b():
        results['b'] = sec_outcome(source, lambda: source.get(B))
        b_done.set()

    run_threads(thread_a, thread_b)
    assert isinstance(results['a'], Fetched) and isinstance(results['b'], Fetched)
    assert len(results['a'].attempts) == 2                  # 503 一次 + 成功一次，不混入 B 的
    assert len(results['b'].attempts) == 1
    assert len(source.fetch_outcomes) == 2                  # 共享列表照旧记下全部请求


def test_company_tickers_are_fetched_once_across_threads(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar.time, 'sleep', lambda _: None)
    seen = []

    class Transport:
        def get(self, url, **kwargs):
            if url == edgar.COMPANY_TICKERS_URL:
                seen.append(url)
                threading.Event().wait(0.05)                 # 让其他线程在加载期间也来要
                return ok({'0': {'ticker': 'NVDA', 'cik_str': 1045810}})
            return ok()

    source = make(Transport(), tmp_path)
    maps = []
    run_threads(*[(lambda: maps.append(source._company_tickers())) for _ in range(4)])
    assert len(seen) == 1 and maps == [{'NVDA': 1045810}] * 4
