from datetime import timedelta
from types import SimpleNamespace

from investment_assistant.data import registry, repo
from investment_assistant.data.adapter_types import AdapterDeps
from investment_assistant.data.adapters import sec_text
from investment_assistant.data.fetch_policy import FetchError, FetchPolicy, Fetched
from investment_assistant.data.sources.edgar import EdgarSource
from test_data_registry import DAY, NOW, detail, memory_tx, run


def metas():
    return [dict(ticker='NVDA', form=form, filing_date=day, accession_number=f'0000000001-26-{n:06d}',
                 primary_document='doc.htm', items=items,
                 edgar_url=f'https://www.sec.gov/Archives/edgar/data/1/000000000126{n:06d}/doc.htm')
            for n, (form, day, items) in enumerate([
                ('10-K', '2026-01-01', None), ('10-K', '2025-01-01', None),
                ('10-Q', '2026-10-29', None), ('10-Q', '2026-07-29', None),
                ('10-Q', '2026-04-29', None), ('8-K', '2026-10-30', ['2.02']),
                ('8-K', '2026-10-30', ['5.02'])], 1)]


def test_selection_latest_one_k_two_q_and_item_202():
    selected = sec_text.select_filings(metas())
    assert {r['accession_number'] for r in selected} == {
        '0000000001-26-000001', '0000000001-26-000003', '0000000001-26-000004', '0000000001-26-000006'}


def test_sec_text_daily_budget_existing_files_and_repeated_calls(tmp_path, monkeypatch):
    downloads = []
    class Source:
        def discover_one(self, *args, **kwargs): return metas()
        def get(self, url):
            if url.endswith('-index.html'):
                return b'<table summary="Document Format Files"></table>'
            downloads.append(url)
            return b'<html><body>Item 2.02 financial report</body></html>'
    monkeypatch.setattr(repo, 'indexed_filing_paths', lambda *args: {})
    monkeypatch.setattr(repo, 'filing_analysis_sources', lambda *args: {})
    monkeypatch.setattr(repo, 'store_filing_texts', lambda conn, rows, **kw: {
        'inserted': len([r for r in rows if r['downloaded']]),
        'existing': len([r for r in rows if not r['downloaded']]), 'latest': DAY})
    deps = AdapterDeps(transaction=memory_tx, filing_source=Source(), sec_text_dir=tmp_path)
    a = registry.get('sec_text')
    first = detail(run(a, deps=deps))[0]
    second = detail(run(a, deps=deps))[0]
    # Fresh deps must still respect the on-disk per-session budget.
    third = detail(run(a, deps=AdapterDeps(transaction=memory_tx, filing_source=Source(), sec_text_dir=tmp_path)))[0]
    assert len(downloads) == 3
    assert (first['rows'], second['rows'], third['rows']) == (3, 0, 0)
    assert len(list(tmp_path.rglob('*.htm'))) == 3


def test_sec_text_timeout_has_no_inner_or_outer_retry(tmp_path, monkeypatch):
    calls = []
    class Session:
        def get(self, url, **kwargs):
            calls.append(url)
            raise TimeoutError('deadline')
    class Source(EdgarSource):
        def discover_one(self, *args, **kwargs): return metas()
    monkeypatch.setenv('SEC_USER_AGENT', 'test@example.invalid')
    monkeypatch.setattr(repo, 'indexed_filing_paths', lambda *args: {})
    monkeypatch.setattr(repo, 'filing_analysis_sources', lambda *args: {})
    source = Source(tmp_path/'evidence', session=Session(), policy=FetchPolicy(backoff_s=(0, 0)))
    deps = AdapterDeps(transaction=memory_tx, filing_source=source, sec_text_dir=tmp_path/'text')
    item = detail(run(registry.get('sec_text'), deps=deps))[0]
    assert not item['ok'] and item['error_kind'] == 'timeout' and item['rows'] == 0
    assert len(calls) == 1 and item['attempts'] == 1
    assert source.policy.backoff_s == (0, 0)
    # Same-day manual/research retries must not redownload a timed-out target.
    assert not detail(run(registry.get('sec_text'), deps=deps))[0]['ok']
    assert len(calls) == 1


def test_sec_text_explicitly_includes_tsla_annual_without_changing_monitor_default(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv('SEC_USER_AGENT', 'offline@example.invalid')
    monkeypatch.setattr(repo, 'indexed_filing_paths', lambda *args: {})
    monkeypatch.setattr(repo, 'filing_analysis_sources', lambda *args: {})
    class Session:
        def get(self, url, **kwargs):
            if '/Archives/' in url:
                raw = b'<html>annual report</html>'
            else:
                payload = {'0': {'ticker': 'TSLA', 'cik_str': 1}} if 'company_tickers' in url else {
                    'filings': {'files': [], 'recent': {
                        'form': ['10-K'], 'filingDate': ['2026-01-01'],
                        'accessionNumber': ['0000000001-26-000001'], 'primaryDocument': ['doc.htm']}}}
                raw = json.dumps(payload).encode()
            return SimpleNamespace(status_code=200, content=raw)
    source = EdgarSource(tmp_path/'evidence', session=Session(), policy=FetchPolicy(backoff_s=()))
    assert source.discover_one('TSLA', '2025-01-01', str(DAY)) == []
    deps = AdapterDeps(transaction=memory_tx, filing_source=source, sec_text_dir=tmp_path/'texts')
    result = sec_text.ADAPTER.sources[0].fetch('TSLA', session=DAY, start=DAY-timedelta(days=420), end=DAY, deps=deps, now=NOW)
    rows = result.value if isinstance(result, Fetched) else result
    assert [r['form'] for r in rows] == ['10-K']


def test_injected_transport_timeout_is_still_timeout_not_parse(tmp_path, monkeypatch):
    class Source:
        def discover_one(self, *args, **kwargs): return metas()
        def get(self, url): raise TimeoutError('transport deadline')
    monkeypatch.setattr(repo, 'indexed_filing_paths', lambda *args: {})
    monkeypatch.setattr(repo, 'filing_analysis_sources', lambda *args: {})
    deps = AdapterDeps(transaction=memory_tx, filing_source=Source(), sec_text_dir=tmp_path)
    item = detail(run(registry.get('sec_text'), deps=deps))[0]
    assert item['error_kind'] == 'timeout' and item['rows'] == 0 and item['attempts'] == 1


def test_sec_text_requires_explicit_persistent_root_before_source_work(monkeypatch):
    class Source:
        def discover_one(self, *args, **kwargs):
            raise AssertionError('missing configured root must be rejected before discovery')
    item = detail(run(registry.get('sec_text'), deps=AdapterDeps(
        transaction=memory_tx, filing_source=Source())))[0]
    assert not item['ok'] and 'sec_text_dir' in item['reason']


def test_sec_text_discovery_timeout_blocks_cross_scope_until_next_session(tmp_path):
    calls = []
    class Source:
        def discover_one(self, *args, **kwargs):
            calls.append(args)
            raise TimeoutError('discovery timeout')
    for day, scope in ((DAY, 'manual'), (DAY, 'research'), (DAY+timedelta(days=3), 'data_request')):
        deps = AdapterDeps(transaction=memory_tx, filing_source=Source(), sec_text_dir=tmp_path)
        tally = registry.run_adapter(registry.get('sec_text'), ['NVDA'], scope=scope, trigger='manual_family',
            run_id=None, session=day, now=NOW, deps=deps, features={'backup_sources': True})
        assert detail(tally)[0]['error_kind'] == 'timeout'
    assert len(calls) == 2  # fresh deps/scope cannot erase the session's timeout fact


def test_real_edgar_receives_frozen_cutoff_without_mutating_source(tmp_path, monkeypatch):
    received = []
    class Source(EdgarSource):
        def discover_one(self, ticker, start, end, *, drop_after=None, include_annual=False):
            received.append((drop_after, include_annual))
            return []
    monkeypatch.setattr(repo, 'indexed_filing_paths', lambda *args: {})
    monkeypatch.setattr(repo, 'filing_analysis_sources', lambda *args: {})
    monkeypatch.setattr(repo, 'store_filing_texts', lambda *args, **kwargs: {'inserted': 0, 'latest': None})
    source = Source(tmp_path/'evidence')
    item = detail(run(sec_text.ADAPTER, deps=AdapterDeps(
        transaction=memory_tx, filing_source=source, sec_text_dir=tmp_path/'text')))[0]
    assert item['ok'] and received == [(NOW, True)]
    assert source.drop_after is None
