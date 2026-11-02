"""Offline contracts: real registry, policy, tally and adapters; I/O only is replaced."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from investment_assistant.data import registry, repo
from investment_assistant.data.adapter_types import AdapterDeps, TargetFacts
from investment_assistant.data.fetch_policy import FetchError, FetchPolicy, Fetched
from investment_assistant.data.tally import Tally

DAY = date(2026, 10, 30)
NOW = datetime(2026, 10, 31, 1, tzinfo=UTC)


@contextmanager
def memory_tx():
    yield object()


def run(adapter, *, features=None, targets=('NVDA',), deps=None):
    return registry.run_adapter(adapter, targets, scope='manual', trigger='manual_family',
        run_id=None, session=DAY, now=NOW,
        deps=deps or AdapterDeps(transaction=memory_tx, sleep=lambda _: None),
        features={'backup_sources': True} if features is None else features)


def detail(tally):
    return tally.row(trigger='manual_family', run_id=None, session=DAY,
        started_at=NOW, finished_at=NOW)['detail']['targets']


def adapter(primary, backup=None, *, store=None):
    sources = [registry.Source('yfinance', primary, FetchPolicy(backoff_s=(0, 0)), 'fixture')]
    if backup:
        sources.append(registry.Source('stooq', backup, FetchPolicy(backoff_s=()), 'fixture'))
    return replace(registry.get('bars'), id='fixture', sources=tuple(sources),
        store=store or (lambda conn, rows, **kw: {'inserted': len(rows), 'existing': 0, 'latest': DAY}))


def failure(kind, reason='primary reason', status=None):
    def fetch(*args, **kwargs):
        raise FetchError(reason, kind=kind, status=status)
    return fetch


def success(*args, **kwargs):
    return [{'price_date': DAY}]


def test_adapter_ids_unique_and_ordered():
    assert registry.ids() == ('bars', 'news', 'sec_filings', 'earnings', 'fred', 'universe',
                              'fundamentals', 'sec_text', 'sector')
    with pytest.raises(LookupError):
        registry.get('missing')
    assert [s.id for s in registry.get('bars').sources] == ['yfinance']
    assert [s.id for s in registry.get('earnings').sources] == ['yfinance', 'nasdaq']


def test_run_adapter_primary_ok_records_source_used():
    item = detail(run(adapter(success)))[0]
    assert (item['ok'], item['source_used'], item['rows'], item['attempts']) == (True, 'yfinance', 1, 1)
    assert item['fallback'] == {'tried': False, 'reason': None}


def test_run_adapter_falls_back_on_network_error():
    item = detail(run(adapter(failure('network'), success)))[0]
    assert item['ok'] and item['source_used'] == 'stooq'
    assert item['fallback'] == {'tried': True, 'reason': 'primary reason (3 attempts)'}


@pytest.mark.parametrize('kind,status', [('http_4xx', 403), ('parse', None), ('other', None)])
def test_run_adapter_does_not_fall_back_on_http_4xx(kind, status):
    item = detail(run(adapter(failure(kind, status=status), success)))[0]
    assert not item['ok'] and item['source_used'] is None
    assert item['attempts'] == 1 and not item['fallback']['tried']


def test_run_adapter_respects_backup_sources_flag():
    item = detail(run(adapter(failure('timeout'), success), features={'backup_sources': False}))[0]
    assert not item['ok'] and item['attempts'] == 3 and not item['fallback']['tried']


def test_run_adapter_backup_failure_keeps_primary_reason():
    item = detail(run(adapter(failure('network'), failure('parse', 'backup bad'))))[0]
    assert (item['ok'], item['error_kind'], item['attempts']) == (False, 'network', 4)
    assert item['reason'] == item['fallback']['reason'] == 'primary reason (3 attempts)'
    assert item['source_used'] is None


def test_attempts_merged_from_both_sources():
    item = detail(run(adapter(failure('http_5xx', status=503), success)))[0]
    assert item['attempts'] == 4 and item['http_status'] == 200


def test_tool_families_only_per_ticker_with_tool():
    assert [a.id for a in registry.tool_families()] == [
        'bars', 'news', 'sec_filings', 'earnings', 'fundamentals', 'sec_text', 'sector']
    assert all(a.per_ticker and a.tool is not None for a in registry.tool_families())


def test_store_failure_is_not_fetch_success_or_fallback():
    def broken_store(*args, **kwargs):
        raise ConnectionError('storage unavailable')
    items = detail(run(adapter(success, success, store=broken_store), targets=('NVDA', 'AAPL')))
    assert len(items) == 2
    assert all(not i['ok'] and i['rows'] == 0 and i['attempts'] == 1 for i in items)
    assert all(i['source_used'] == 'yfinance' and not i['fallback']['tried'] for i in items)


def test_nested_outcomes_keep_real_attempts_and_do_not_retry_twice():
    attempts = [{'n': n, 'kind': 'timeout', 'status': None, 'elapsed_ms': 7} for n in range(1, 4)]
    def failed(*args, **kwargs):
        raise FetchError('inner exhausted', kind='timeout', attempts=attempts)
    item = detail(run(adapter(failed, success)))[0]
    assert item['attempts'] == 4
    def recovered(*args, **kwargs):
        return Fetched([{}], attempts + [{'n': 4, 'status': 200, 'kind': None, 'elapsed_ms': 4}], 25)
    item = detail(run(adapter(recovered)))[0]
    assert (item['attempts'], item['elapsed_ms']) == (4, 25)


def test_tally_keeps_legacy_status_redaction_and_new_fields(monkeypatch):
    monkeypatch.setenv('FRED_API_KEY', 'abc123')
    tally = Tally('bars', 'daily', ['A', 'B'])
    tally.record('A', Fetched(None), rows=0, source_used='yfinance', fallback={'tried': False, 'reason': None})
    tally.record('B', FetchError('secret api_key=abc123', kind='parse'), source_used=None,
                 fallback={'tried': True, 'reason': 'api_key=abc123'})
    row = tally.row(trigger='scheduled', run_id=None, session=DAY, started_at=NOW, finished_at=NOW)
    assert row['status'] == 'partial' and row['target_count'] == 2
    assert 'abc123' not in str(row)


def test_target_daily_family_differences_and_facts_are_not_recomputed(monkeypatch):
    monkeypatch.setattr(repo, 'collection_request_tickers', lambda conn: ['RESEARCH'])
    monkeypatch.setattr(repo, 'pending_paper_tickers', lambda conn: ['ORDER'])
    facts = TargetFacts(pool=('POOL',), holdings=('HELD',), paper_holdings=('PAPER',),
                        active_chains=('CHAIN',))
    load = lambda: SimpleNamespace(tickers=('MEMBER', 'POOL'))
    assert registry.get('bars').targets(None, 'daily', facts=facts, universe_loader=load) == [
        'SPY', 'SMH', 'XLK', 'QQQ', 'CHAIN', 'HELD', 'MEMBER', 'ORDER', 'PAPER', 'POOL', 'RESEARCH']
    for family in ('earnings', 'sector'):
        assert registry.get(family).targets(None, 'daily', facts=facts, universe_loader=load) == [
            'CHAIN', 'HELD', 'MEMBER', 'ORDER', 'PAPER', 'POOL', 'RESEARCH']
    for family in ('news', 'sec_filings', 'fundamentals', 'sec_text'):
        assert registry.get(family).targets(None, 'daily', facts=facts, universe_loader=load) == ['CHAIN', 'HELD', 'POOL', 'RESEARCH']


@pytest.mark.parametrize('scope', ['manual', 'research', 'screen_new_members', 'data_request', 'sec_monitor'])
def test_explicit_targets_never_expand_to_pool(scope):
    for a in registry.tool_families():
        assert a.targets(None, scope, explicit=['nvda', 'NVDA', 'AAPL']) == ['NVDA', 'AAPL']
        assert a.targets(None, scope, explicit=[]) == []
        with pytest.raises(ValueError, match='explicit'):
            a.targets(None, scope)


def test_global_targets_and_missing_daily_facts():
    assert registry.get('fred').targets(None, 'daily') == ['BAMLH0A0HYM2', 'T10Y2Y']
    assert registry.get('universe').targets(None, 'daily') == ['nasdaq100']
    with pytest.raises(ValueError, match='TargetFacts'):
        registry.get('bars').targets(None, 'daily')


def test_coverage_reads_adapters_and_keeps_fred_independent_coverage():
    from investment_assistant.data.families import coverage
    assert coverage('fred', ['A', 'B'], {'A': DAY - timedelta(days=8)}, session=DAY) == {
        'targets': 2, 'covered': 1, 'lagging': ['A', 'B']}
    assert coverage('sector', ['A', 'B'], {'A': NOW}, session=DAY)['covered'] == 1
    assert not registry.get('news').lagging(DAY - timedelta(days=7), DAY)
    assert registry.get('fundamentals').lagging(DAY - timedelta(days=121), DAY)


def test_real_earnings_policy_and_nasdaq_single_attempt(monkeypatch):
    from investment_assistant.data.adapters import earnings
    from investment_assistant.data.sources import nasdaq
    calls = []
    def calendar(ticker):
        calls.append('yf')
        raise ConnectionError('down')
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs):
            calls.append('nasdaq')
            return SimpleNamespace(status_code=200, json=lambda: {'data': {'announcement':
                'Earnings announcement* for NVDA: Nov 17, 2026'}})
    monkeypatch.setattr(earnings, 'FETCH_SLEEP', lambda _: None)
    monkeypatch.setattr(nasdaq.fetch_policy, 'policy_session', lambda *args: Session())
    saved = []
    def store(conn, ticker, row, *, now, source):
        saved.append((ticker, row, source))
        return {'status': 'inserted', 'id': 1}
    monkeypatch.setattr(repo, 'store_earnings', store)
    item = detail(run(registry.get('earnings'), deps=AdapterDeps(transaction=memory_tx,
        earnings_fetcher=calendar, sleep=lambda _: None)))[0]
    assert calls == ['yf', 'yf', 'yf', 'nasdaq']
    assert saved == [('NVDA', {'status': 'known', 'earnings_date': date(2026, 11, 17)}, 'nasdaq')]
    assert (item['rows'], item['attempts'], item['source_used']) == (1, 4, 'nasdaq')


def test_nasdaq_unknown_answer_is_a_failed_backup_and_keeps_the_known_date(monkeypatch):
    """B10: the primary fails (network), Nasdaq has no parsable date -- that is not an answer. Nothing is stored, the
    target fails with the primary's reason and the earlier known date stays the latest answer."""
    from investment_assistant.data.adapters import earnings
    from investment_assistant.data.sources import nasdaq
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, headers={}, json=lambda: {'data': None})
    def calendar(ticker):
        raise ConnectionError('down')
    monkeypatch.setattr(earnings, 'FETCH_SLEEP', lambda _: None)
    monkeypatch.setattr(nasdaq.fetch_policy, 'policy_session', lambda *args: Session())
    known = {'status': 'known', 'earnings_date': date(2026, 11, 17)}
    store = {'rows': [known]}
    def save(conn, ticker, row, *, now, source):
        store['rows'].append(row)
        return {'status': 'inserted', 'id': len(store['rows'])}
    monkeypatch.setattr(repo, 'store_earnings', save)
    monkeypatch.setattr(repo, 'latest_earnings', lambda conn, ticker, cutoff_at: store['rows'][-1])
    tally = run(registry.get('earnings'), deps=AdapterDeps(transaction=memory_tx, earnings_fetcher=calendar,
                                                          sleep=lambda _: None))
    item = detail(tally)[0]
    assert store['rows'] == [known] and repo.latest_earnings(None, 'NVDA', None) == known       # nothing was written
    assert item['ok'] is False and item['rows'] == 0
    assert item['fallback']['tried'] is True and item['fallback']['reason']
    assert item['reason'] and 'Nasdaq' not in item['reason']                                       # the primary's reason


def test_sector_only_changes_append_and_missing_classification_is_none(monkeypatch):
    from investment_assistant.data.adapters import sector
    saved = {}
    monkeypatch.setattr(repo, 'latest_sectors', lambda conn, targets, cutoff_at: dict(saved))
    def insert(conn, row):
        saved[row['ticker']] = row
        return len(saved)
    monkeypatch.setattr(repo, 'insert_sector_classification', insert)
    rows = [{'sic': '9999', 'sic_description': None, 'group': None}]
    first = sector.ADAPTER.store(None, rows, target='NVDA', source_id='sec', now=NOW)
    second = sector.ADAPTER.store(None, rows, target='NVDA', source_id='sec', now=NOW + timedelta(days=1))
    assert (first['inserted'], second['inserted'], second['existing']) == (1, 0, 1)
    assert saved['NVDA']['group'] is None and second['latest'] == NOW
    changed = sector.ADAPTER.store(None, [{'sic': '3571', 'sic_description': 'hardware', 'group': '科技硬件'}],
                                  target='NVDA', source_id='sec', now=NOW + timedelta(days=2))
    assert changed['inserted'] == 1


def test_sec_company_ticker_failure_records_every_target(tmp_path, monkeypatch):
    from investment_assistant.data.sources.edgar import EdgarSource
    monkeypatch.setenv('SEC_USER_AGENT', 'offline test@example.invalid')
    class Session:
        def get(self, *args, **kwargs): raise ConnectionError('offline')
    source = EdgarSource(tmp_path, session=Session(), policy=FetchPolicy(backoff_s=()), sleep=lambda _: None)
    tally = run(registry.get('sec_filings'), targets=['NVDA', 'AAPL'],
                deps=AdapterDeps(transaction=memory_tx, filing_source=source, sleep=lambda _: None))
    assert [i['target'] for i in detail(tally)] == ['AAPL', 'NVDA']
    assert all(not i['ok'] for i in detail(tally))


def test_adapter_fields_and_shared_types_have_one_definition():
    import ast
    from dataclasses import fields
    from pathlib import Path
    from investment_assistant.data import collect
    from investment_assistant.execution import portfolio, portfolio_types
    assert [f.name for f in fields(registry.Source)] == ['id', 'fetch', 'policy', 'note']
    assert [f.name for f in fields(registry.Adapter)] == [
        'id', 'title', 'purpose', 'kind', 'per_ticker', 'table', 'sources', 'fallback_kinds',
        'targets', 'store', 'latest', 'lagging', 'coverage_note', 'tool', 'concept', 'lock']
    assert registry.DEFAULT_FALLBACK_KINDS == frozenset({'timeout', 'network', 'http_5xx', 'http_429', 'empty'})
    assert collect.Tally is registry.Tally is Tally
    assert portfolio.PortfolioResult is portfolio_types.PortfolioResult
    root = Path(__file__).resolve().parents[1] / 'investment_assistant'
    definitions = {name: [] for name in ('Source', 'Adapter', 'Tally', 'PortfolioResult')}
    for path in root.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ClassDef) and node.name in definitions:
                definitions[node.name].append(str(path.relative_to(root)))
    assert definitions == {'Source': ['data/adapter_types.py'], 'Adapter': ['data/adapter_types.py'],
                           'Tally': ['data/tally.py'], 'PortfolioResult': ['execution/portfolio_types.py']}


def test_target_schema_capabilities_are_explicit_not_missing_table_fallback(monkeypatch):
    def unsupported(conn):
        pytest.fail('host marked the schema capability unavailable')
    monkeypatch.setattr(repo, 'collection_request_tickers', unsupported)
    monkeypatch.setattr(repo, 'pending_paper_tickers', unsupported)
    facts = TargetFacts(pool=('MU',), research_requests_ready=False, paper_accounts_ready=False)
    assert registry.get('bars').targets(None, 'daily', facts=facts,
        universe_loader=lambda: SimpleNamespace(tickers=('AAPL',))) == ['SPY', 'SMH', 'XLK', 'QQQ', 'AAPL', 'MU']


def test_source_receives_immutable_run_cutoff():
    seen = []
    def fetch(target, *, session, start, end, deps, now):
        seen.append(now)
        return []
    assert detail(run(adapter(fetch)))[0]['ok']
    assert seen == [NOW]


def test_observe_sees_each_stored_target_before_it_is_booked():
    seen = []
    tally = registry.run_adapter(adapter(failure('network'), success), ['NVDA', 'AAPL'], scope='daily', trigger='scheduled',
        run_id=None, session=DAY, now=NOW, deps=AdapterDeps(transaction=memory_tx, sleep=lambda _: None),
        features={'backup_sources': True}, observe=lambda *args: seen.append(args))
    assert [(t, source, rows, result['inserted']) for t, source, rows, result in seen] == [
        ('NVDA', 'stooq', [{'price_date': DAY}], 1), ('AAPL', 'stooq', [{'price_date': DAY}], 1)]
    assert sorted(tally.outcomes) == ['AAPL', 'NVDA'] and not any(tally.outcomes.values())


def test_whole_call_target_books_one_sentinel_and_stops():
    attempted = []

    def directory_down(target, **kwargs):
        attempted.append(target)
        raise FetchError('dns down', kind='network', target='sec:company_tickers',     # an inner fetch already retried
                         attempts=[{'n': 1, 'kind': 'network', 'status': None, 'elapsed_ms': 1}])

    def per_target(adapter_, **kwargs):
        return registry.run_adapter(adapter_, ['NVDA', 'AAPL', 'MU'], scope='daily', trigger='scheduled', run_id=None,
            session=DAY, now=NOW, deps=AdapterDeps(transaction=memory_tx, sleep=lambda _: None),
            features={'backup_sources': True}, **kwargs)

    tally = per_target(adapter(directory_down), whole_call_target='sec:company_tickers')
    row = tally.row(trigger='scheduled', run_id=None, session=DAY, started_at=NOW, finished_at=NOW)
    assert attempted == ['NVDA']                                       # the first target only, then stop
    assert [f['target'] for f in row['failures']] == ['*'] and row['status'] == 'failed'
    assert (row['target_count'], row['ok_count']) == (3, 0) and row['detail']['failed_targets'] == ['AAPL', 'MU', 'NVDA']
    attempted.clear()
    every = per_target(adapter(directory_down))                        # without it every target records its own failure
    assert sorted(every.outcomes) == ['AAPL', 'MU', 'NVDA'] and attempted == ['NVDA', 'AAPL', 'MU']


def test_unavailable_storage_is_not_a_target_failure():
    from investment_assistant.core.tx import Unavailable

    def down(*args, **kwargs):
        raise Unavailable('数据库不可达')

    with pytest.raises(Unavailable):
        run(adapter(success, store=down))
