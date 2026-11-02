"""Real adapter fetch/normalization/policy behavior with offline HTTP and in-memory stores."""
import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd

from investment_assistant.data import registry, repo, families
from investment_assistant.data.adapter_types import AdapterDeps, Source
from investment_assistant.data.adapters import bars, fred, universe
from investment_assistant.data.fetch_policy import FetchError, FetchPolicy
from investment_assistant.data.sources.edgar import EdgarSource
from test_data_registry import DAY, NOW, detail, memory_tx, run


def test_bars_normalization_and_backup_full_pipeline(monkeypatch):
    saved = []
    monkeypatch.setattr(repo, 'missing_bar_sessions', lambda conn, target, sessions: [DAY])
    monkeypatch.setattr(repo, 'ratio_from_primary', lambda conn, ticker, day: Decimal('.98'))
    def store(conn, target, rows, *, source, now):
        saved.extend((source, r) for r in rows)
        return {'inserted': len(rows), 'unchanged': 0}
    monkeypatch.setattr(repo, 'store_bars', store)
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, text='Date,Open,High,Low,Close,Volume\n2026-10-30,100,101,99,100,1000\n')
    monkeypatch.setattr(bars.stooq.fetch_policy, 'policy_session', lambda *args: Session())
    def failed(*args): raise ConnectionError('offline Yahoo')
    a = replace(bars.ADAPTER, sources=(*bars.ADAPTER.sources,
        Source('stooq', bars.fetch_backup, FetchPolicy(backoff_s=()), 'test-only unavailable source')))
    item = detail(run(a, deps=AdapterDeps(transaction=memory_tx, bar_fetcher=failed, sleep=lambda _: None)))[0]
    assert (item['source_used'], item['rows'], item['attempts']) == ('stooq', 1, 4)
    assert saved[0][0] == saved[0][1]['source'] == 'stooq'
    assert saved[0][1]['adj_close'] == Decimal(98)
    frame = pd.DataFrame({'Open': [100], 'High': [101], 'Low': [99], 'Close': [100],
                          'Adj Close': [98], 'Volume': [1000]}, index=pd.to_datetime([str(DAY)]))
    item = detail(run(bars.ADAPTER, deps=AdapterDeps(transaction=memory_tx, bar_fetcher=lambda *args: frame)))[0]
    assert item['source_used'] == 'yfinance' and saved[-1][1]['adj_close'] == Decimal(98)


def test_news_cutoff_store_counts_are_new_not_fetched(monkeypatch):
    kept = []
    def store(conn, entries, *, now):
        kept.extend(entries)
        return {'inserted': 0, 'existing': len(entries)}
    monkeypatch.setattr(repo, 'store_news', store)
    rows = [{'title': 'known', 'link': 'https://example.invalid/1', 'providerPublishTime': int(NOW.timestamp())},
            {'title': 'future', 'link': 'https://example.invalid/2', 'providerPublishTime': int((NOW + timedelta(days=1)).timestamp())}]
    item = detail(run(registry.get('news'), deps=AdapterDeps(transaction=memory_tx, news_fetcher=lambda _: rows)))[0]
    assert item['rows'] == 0 and [i[0]['title'] for i in kept] == ['known']


def test_fred_preserves_success_retries_and_missing_key_skips(monkeypatch):
    monkeypatch.setattr(repo, 'store_macro', lambda conn, target, rows, now: {'inserted': 0, 'unchanged': len(rows)})
    attempts = [{'n': 1, 'kind': 'timeout', 'status': None, 'elapsed_ms': 2},
                {'n': 2, 'kind': None, 'status': 200, 'elapsed_ms': 3}]
    def fetcher(url, *, params):
        return {'observations': [{'date': '2026-10-30', 'value': '1.2'}]}, {'ok': True, 'attempts': attempts}
    item = detail(run(fred.ADAPTER, targets=['T10Y2Y'], deps=AdapterDeps(
        transaction=memory_tx, macro_fetcher=fetcher, fred_api_key='offline')))[0]
    assert (item['rows'], item['attempts'], item['elapsed_ms']) == (0, 2, 5)
    tally = run(fred.ADAPTER, targets=['T10Y2Y'])
    row = tally.row(trigger='scheduled', run_id=None, session=DAY, started_at=NOW, finished_at=NOW)
    assert row['status'] == 'skipped' and row['detail']['targets'][0]['rows'] == 0


def test_universe_reports_members_but_no_new_rows():
    loaded = universe.Universe('Nasdaq', DAY, 'file', tuple(f'T{i}' for i in range(100)))
    item = detail(run(universe.ADAPTER, targets=['nasdaq100'], deps=AdapterDeps(
        transaction=memory_tx, universe=loaded)))[0]
    assert item['rows'] == 0 and item['source_used'] == 'file'
    assert families.coverage('universe', ['nasdaq100'], {'nasdaq100': DAY}, session=DAY,
                                universe_members=loaded.tickers) == {
        'targets': 100, 'covered': 100, 'lagging': []}


def test_universe_latest_keeps_registry_scalar_time_contract(monkeypatch):
    loaded = universe.Universe('Nasdaq', DAY, 'file', tuple(f'T{i}' for i in range(100)))
    monkeypatch.setattr(universe, 'load', lambda: loaded)
    assert universe.ADAPTER.latest(None, ['nasdaq100'], cutoff_at=NOW) == {'nasdaq100': DAY}
    assert universe.ADAPTER.latest(None, [], cutoff_at=NOW) == {}


def test_sec_successful_request_retry_attempts_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv('SEC_USER_AGENT', 'offline@example.invalid')
    calls = []
    class Session:
        def get(self, url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise ConnectionError('transient')
            payload = {'0': {'ticker': 'NVDA', 'cik_str': 1}} if 'company_tickers' in url else {
                'sic': '3571', 'sicDescription': 'hardware'}
            return SimpleNamespace(status_code=200, content=json.dumps(payload).encode())
    source = EdgarSource(tmp_path, session=Session(), policy=FetchPolicy(backoff_s=(0, 0)), sleep=lambda _: None)
    monkeypatch.setattr(repo, 'latest_sectors', lambda *args, **kwargs: {})
    saved = []
    monkeypatch.setattr(repo, 'insert_sector_classification', lambda conn, row: saved.append(row) or 1)
    item = detail(run(registry.get('sector'), deps=AdapterDeps(transaction=memory_tx, filing_source=source)))[0]
    assert item['attempts'] == 3 and item['rows'] == 1
    assert saved[0]['group'] == '科技硬件'


def test_fundamentals_reuses_real_parser_and_sec_policy(tmp_path, monkeypatch):
    monkeypatch.setenv('SEC_USER_AGENT', 'offline@example.invalid')
    calls = []
    class Session:
        def get(self, url, **kwargs):
            calls.append(url)
            payload = ({'0': {'ticker': 'NVDA', 'cik_str': 1}} if 'company_tickers' in url else {
                'cik': 1, 'entityName': 'Nvidia', 'facts': {'us-gaap': {'Revenues': {'units': {'USD': [
                    {'val': 10, 'form': '10-Q', 'start': '2026-07-01', 'end': '2026-09-30',
                     'filed': '2026-10-30', 'accn': '0000000001-26-000001'}]}}}}})
            return SimpleNamespace(status_code=200, content=json.dumps(payload).encode())
    source = EdgarSource(tmp_path, session=Session(), policy=FetchPolicy(backoff_s=()), sleep=lambda _: None)
    saved = []
    def store(conn, sid, dataset, rows, *, now):
        assert (sid, dataset) == ('US:stock:NVDA', 'financials')
        saved.extend(rows)
        return {'inserted': len(rows), 'existing': 0, 'latest': DAY}
    monkeypatch.setattr(repo, 'store_observation_rows', store)
    item = detail(run(registry.get('fundamentals'), deps=AdapterDeps(transaction=memory_tx, filing_source=source)))[0]
    assert item['rows'] == 1 and item['attempts'] == 2
    assert saved[0]['payload']['metric'] == 'revenue' and saved[0]['payload']['value'] == 10
    assert len(list(tmp_path.glob('*.raw'))) == 1
