"""Worker-pool contracts of registry.run_adapter: same booking in the caller thread, several fetches in flight."""
import time

import pytest
from test_data_registry import DAY, NOW, adapter, memory_tx, success

from investment_assistant.core.tx import Unavailable
from investment_assistant.data import registry
from investment_assistant.data.adapter_types import AdapterDeps
from investment_assistant.data.fetch_policy import FetchError

TARGETS = [f'T{i:02d}' for i in range(1, 13)]


def run(primary, *, workers=None, targets=('NVDA',), store=None, whole_call_target=None):
    kwargs = {} if workers is None else {'workers': workers}
    return registry.run_adapter(adapter(primary, store=store), targets, scope='manual', trigger='manual_family',
                                run_id=None, session=DAY, now=NOW,
                                deps=AdapterDeps(transaction=memory_tx, sleep=lambda _: None),
                                features={'backup_sources': True}, whole_call_target=whole_call_target, **kwargs)


def row_of(tally):
    return tally.row(trigger='manual_family', run_id=None, session=DAY, started_at=NOW, finished_at=NOW)


def without_elapsed(value):
    """A full row minus per-target timings, which a pooled run cannot reproduce bit-for-bit."""
    if isinstance(value, dict):
        return {key: without_elapsed(item) for key, item in value.items() if key != 'elapsed_ms'}
    if isinstance(value, list):
        return [without_elapsed(item) for item in value]
    return value


def test_workers_book_every_target_once_in_input_order():
    def slow(target, *args, **kwargs):
        time.sleep(0.02)
        return [{'price_date': DAY}]

    started = time.perf_counter()
    sequential = run(slow, workers=1, targets=TARGETS)
    sequential_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    parallel = run(slow, workers=4, targets=TARGETS)
    parallel_ms = (time.perf_counter() - started) * 1000
    assert list(parallel.outcomes) == TARGETS == list(sequential.outcomes)   # booked once, in input order
    assert without_elapsed(row_of(parallel)) == without_elapsed(row_of(sequential))
    assert parallel_ms < sequential_ms / 2


def test_one_failing_target_does_not_affect_others():
    def mostly_ok(target, *args, **kwargs):
        if target == 'T05':
            raise FetchError('T05 down', kind='network')
        return [{'price_date': DAY}]

    row = row_of(run(mostly_ok, workers=3, targets=TARGETS))
    assert (row['target_count'], row['ok_count']) == (12, 11)
    assert [failure['target'] for failure in row['failures']] == ['T05']


def test_unavailable_propagates():
    def down(conn, rows, **kwargs):
        raise Unavailable('数据库不可达')

    with pytest.raises(Unavailable):
        run(success, workers=2, targets=TARGETS[:4], store=down)


def test_whole_call_failure_books_star_once():
    def directory_down(target, **kwargs):
        raise FetchError('dns down', kind='network', target='sec:company_tickers',
                         attempts=[{'n': 1, 'kind': 'network', 'status': None, 'elapsed_ms': 1}])

    row = row_of(run(directory_down, workers=3, targets=TARGETS[:6], whole_call_target='sec:company_tickers'))
    assert [failure['target'] for failure in row['failures']] == ['*'] and row['ok_count'] == 0
    assert not row_of_targets(row)          # neither a success nor a failure per target after the whole-call one


def row_of_targets(row):
    return [item['target'] for item in row['detail'].get('targets') or []]


def test_default_does_not_create_a_pool(monkeypatch):
    class NoPool:
        def __init__(self, *args, **kwargs):
            raise AssertionError('workers=1 (and the default) must not build a thread pool')

    monkeypatch.setattr(registry, 'ThreadPoolExecutor', NoPool)
    default = run(success, targets=('NVDA', 'AAPL'))
    explicit = run(success, targets=('NVDA',), workers=1)
    assert not any(default.outcomes.values()) and not any(explicit.outcomes.values())
