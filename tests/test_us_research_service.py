from investment_assistant.core import tx as core_tx
from investment_assistant.analysis import fundamentals
from investment_assistant.data.sources import fundamentals as source_module
from investment_assistant.workflow.nodes import sec_monitor
import investment_assistant.data.repo as data_repo
import copy
import hashlib
import importlib.util
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from test_us_research_postgres import research_db


def module():
    assert importlib.util.find_spec('investment_assistant.data.acquisition'), 'missing acquisition normalization'
    from investment_assistant.data import acquisition as service
    return service


def seed_snapshot(conn, dataset, payload):
    """Stored-source fixtures for read/normalization tests, independent of retired sync."""
    s = module()
    rows, details = (s.research_rows.financial_rows if dataset == 'financials' else s._market_rows)(payload)
    records = data_repo.store_rows(conn, payload['identity']['security_id'], dataset, rows)
    status = 'partial' if details.get('conflicting_fact_keys') else 'ready'
    data_repo.collection_state(conn, payload['identity']['security_id'], dataset,
                               status=status, count=len(records), details=details)
    conn.commit()
    return status


def financial(value=10, raw=b'{ "facts": 10 }', ticker='MU'):
    digest = hashlib.sha256(raw).hexdigest()
    return {'identity': {'ticker': ticker, 'security_id': 'US:stock:' + ticker}, 'observations': [
        {'id': 'provisional', 'metric': 'revenue', 'value': value, 'unit': 'USD', 'basis': 'consolidated',
         'start': '2026-01-01', 'end': '2026-03-31', 'period_kind': 'duration', 'filed': '2026-05-01',
         'form': '10-Q', 'accession': '0000000001-26-000001', 'concept': 'Revenues',
         'source_url': 'https://www.sec.gov/filing', 'source_locator': '/facts/0', 'source_hash': digest}],
        'source_url': 'https://data.sec.gov/facts', 'source_hash': digest, 'raw_snapshot': raw,
        'request_params': {}, 'collected_at': '2026-10-16T00:00:00Z', 'coverage': {'complete': None}, 'limitations': []}


def market(ticker='MU'):
    raw = b'{ "chart": 1 }'
    digest = hashlib.sha256(raw).hexdigest()
    return {'identity': {'ticker': ticker, 'security_id': 'US:stock:' + ticker},
        'bars': [{'date': '2026-10-15', 'close': 100, 'adjusted_close': 100, 'currency': 'USD', 'source_hash': digest}],
        'benchmarks': {'SPY': [], 'QQQ': []}, 'quote': {'date': '2026-10-15', 'price': 100, 'market_cap': 1000,
        'currency': 'USD', 'market_cap_verified': False, 'share_class_basis': 'unverified', 'source_hash': digest},
        'source_url': 'https://query1.finance.yahoo.com/chart', 'source_hash': digest,
        'collected_at': '2026-10-16T00:00:00Z', 'limitations': [], 'raw_snapshots': [{'source_url': 'https://query1.finance.yahoo.com/chart',
        'source_hash': digest, 'raw_snapshot': raw, 'collected_at': '2026-10-16T00:00:00Z', 'request_params': {'range': '2y'}}]}


class Source:
    def companyfacts(self, ticker): return financial(ticker=ticker)
    def market(self, ticker): return market(ticker)


def test_acquisition_has_no_legacy_sync_entrypoint():
    s = module()
    assert not hasattr(s, 'sync')
    assert not hasattr(s, 'validate_request')
    # R28: the local-filing refresh is the sec_text adapter's work now (spec D47); nothing keeps a second copy
    assert not hasattr(s, 'refresh_local_filings') and not hasattr(s, '_local_filing_rows')


def test_raw_snapshot_exact_bytes_and_hash(tmp_path):
    s = module()
    raw = b'{ "a" : 1 }\n'
    digest = hashlib.sha256(raw).hexdigest()
    result = s.research_rows.archive_snapshot({'source_url': 'https://data.sec.gov/x', 'source_hash': digest, 'raw_snapshot': raw}, root=tmp_path)
    assert Path(result['snapshot_path']).read_bytes() == raw
    assert 'raw_snapshot' not in result
    json.dumps(result)
    with pytest.raises(ValueError): s.research_rows.archive_snapshot({'source_hash': 'b'*64, 'raw_snapshot': raw}, root=tmp_path)






def test_view_asof_is_local_and_filters_all_datasets(research_db, monkeypatch, tmp_path):
    s = module()
    monkeypatch.setattr(s.research_rows, 'RAW_ROOT', tmp_path)
    seed_snapshot(research_db, 'financials', financial())
    seed_snapshot(research_db, 'market', market())
    def forbidden(*a, **kw): pytest.fail('GET fetched remote data')
    monkeypatch.setattr(source_module, 'USSources', forbidden)
    v = fundamentals.view('MU', as_of='2026-01-01T00:00:00Z')
    assert v['financials'] == [] and v['filings'] == []
    assert v['market']['bars'] == [] and v['market']['quote'] == {}
    assert v['guidance']['records'] == [] and 'card' not in v
    assert v['pit']['strict'] is False
    assert all(r['scope'] == 'current_collection_state' for r in v['statuses'])
    assert all(r['status'] == 'unavailable_at_cutoff' for r in v['statuses'])
    with pytest.raises(ValueError): fundamentals.view('MU', as_of='2026-01-01')


def _scan_result(tickers, errors=None, **extra):
    return {'summary': {'tickers': tickers, 'filings': [], 'errors': errors or {}, 'delivery': {'status': 'sent'},
                        **extra}}


def _record_with(collect_text, result, monitor, monkeypatch):
    """The monitor row of one scan result with a recording ``collect_text`` hook; returns the booked rows."""
    from investment_assistant.config import AssistantConfig, SecMonitorConfig
    import investment_assistant.data.collect as collect
    rows = []
    monkeypatch.setattr(collect, 'write_runs_with_ids', lambda new_rows: (rows.extend(new_rows) or {'written': 1, 'error': None}, [1]))
    sec_monitor._record_fetch_run(result, config=AssistantConfig(watchlist=['MU', 'NVDA'], sec_monitor=SecMonitorConfig(enabled=monitor)),
                                  trigger_source='scheduled', started_at=datetime(2026, 10, 22, tzinfo=UTC),
                                  collect_text=collect_text)
    return rows


@pytest.mark.parametrize('monitor', [False, True])
def test_sec_success_hook_retains_original_result_and_runs_local_extraction(monkeypatch, monitor):
    """D47 (was ``_local_research_after_success``): after the scan the ``sec_text`` adapter hook gets the tickers the
    scan left standing AND found new filings for (R27); the scan's own result (filings, delivery, errors) is never
    replaced."""
    module()
    seen = []
    original = _scan_result(['MU'] if monitor else None, filings=[{'ticker': 'MU', 'path': 'local'}])
    before = copy.deepcopy(original)
    rows = _record_with(lambda tally, tickers: (seen.append(list(tickers)), [tally.ok(t) for t in tickers]),
                        original, monitor, monkeypatch)
    assert seen == [['MU']]                                  # NVDA (monitor off: watchlist) had no new filing
    assert original == before and rows[0]['family'] == 'sec_text' and rows[0]['detail']['scope'] == 'sec_monitor'


def test_failed_sec_run_does_not_start_extraction_for_the_failed_tickers(monkeypatch):
    seen = []
    result = _scan_result(['MU'], errors={'MU': 'original'})
    rows = _record_with(lambda tally, tickers: seen.append(list(tickers)), result, True, monkeypatch)
    assert seen == [[]]                                       # nothing left standing: no extraction
    assert rows[0]['failures'] == [{'target': 'MU', 'reason': 'original'}] and rows[0]['status'] == 'failed'
    seen.clear()
    whole = {'summary': {'errors': {'collection': 'edgar down'}, 'filings': []}}
    _record_with(lambda tally, tickers: seen.append(list(tickers)), whole, True, monkeypatch)
    assert seen == [[]]                                       # a whole-scan failure: no target is fetched either


def test_sec_local_hook_failure_is_a_failed_result_and_preserves_original_success(monkeypatch):
    """An adapter failure for one ticker is that ticker's failed result (the node then only degrades); the scan's own
    result stands. (The old refresh replaced every failure by a generic sentence; the adapter path books the same
    sanitised reason every family's row carries — ``redact.sanitize_reason``.)"""
    from investment_assistant.data.collect import Tally
    original = _scan_result(['MU', 'NVDA'], filings=[{'ticker': 'MU'}])
    before = copy.deepcopy(original)

    def hook(tally, tickers):
        tally.fail('MU', ValueError('parse failed'))
        tally.ok('NVDA')
    rows = _record_with(hook, original, True, monkeypatch)
    assert original == before and rows[0]['status'] == 'partial'
    tally = Tally('sec_text', 'daily', ['MU', 'NVDA'])
    hook(tally, ['MU', 'NVDA'])
    research = sec_monitor.research_summary(tally, ['MU', 'NVDA'])
    assert [r['status'] for r in research['results']] == ['failed', 'ready']
    assert [e['ticker'] for e in research['errors']] == ['MU'] and research['errors'][0]['error'] == 'parse failed'


def test_market_observation_retains_its_original_snapshot_mapping(research_db, monkeypatch, tmp_path):
    s = module()
    monkeypatch.setattr(s.research_rows, 'RAW_ROOT', tmp_path)
    seed_snapshot(research_db, 'market', market())
    bar = next(r for r in data_repo.read_rows(research_db, 'US:stock:MU', 'market') if r['kind'] == 'bar')
    assert Path(bar['snapshot_path']).read_bytes() == b'{ "chart": 1 }'
    assert hashlib.sha256(Path(bar['snapshot_path']).read_bytes()).hexdigest() == bar['source_hash']




def test_dataset_rollback_keeps_prior_values_and_other_commits(research_db, monkeypatch, tmp_path):
    s = module()
    monkeypatch.setattr(s.research_rows, 'RAW_ROOT', tmp_path)
    seed_snapshot(research_db, 'financials', financial())
    class BadFinancial(Source):
        def companyfacts(self, ticker):
            payload = financial(value=20)
            invalid = {**payload['observations'][0], 'metric':'cfo', 'concept':'CFO', 'value':float('nan')}
            payload['observations'].append(invalid)
            return payload
    seed_snapshot(research_db, 'market', market())
    with pytest.raises(ValueError):
        seed_snapshot(research_db, 'financials', BadFinancial().companyfacts('MU'))
    research_db.rollback()
    assert fundamentals.view('MU')['financials'][0]['metrics']['revenue']['value'] == 10
    assert next(r for r in data_repo.read_rows(research_db, 'US:stock:MU', 'market') if r['kind'] == 'bar')['close'] == 100
    assert fundamentals.view('MU')['market']['bars'] == []
    from investment_assistant.data import repo
    assert len(repo.read_history(research_db,'US:stock:MU','financials')) == 1


def test_get_reads_persisted_rows_without_source_or_database_writes(research_db, monkeypatch, tmp_path, client):
    s = module()
    monkeypatch.setattr(s.research_rows, 'RAW_ROOT', tmp_path)
    seed_snapshot(research_db, 'financials', financial())
    seed_snapshot(research_db, 'market', market())
    def forbidden(*a, **kw): pytest.fail('read endpoint touched remote source')
    monkeypatch.setattr(source_module, 'USSources', forbidden)
    before = research_db.execute('SELECT count(*),max(last_seen_at) FROM us_research_observations').fetchone()
    response = client.get('/api/us-research/MU')
    assert response.status_code == 200
    assert response.json()['financials'][0]['metrics']['revenue']['value'] == 10
    after = research_db.execute('SELECT count(*),max(last_seen_at) FROM us_research_observations').fetchone()
    assert after == before


def test_market_view_reads_price_bar_revisions_and_ignores_old_dataset(research_db, monkeypatch, tmp_path):
    s = module()
    monkeypatch.setattr(s.research_rows, 'RAW_ROOT', tmp_path)
    seed_snapshot(research_db, 'market', market())  # old dataset says 100; it is no longer the read source.
    first = datetime(2026, 10, 16, 22, tzinfo=UTC)
    later = datetime(2026, 10, 19, 22, tzinfo=UTC)
    bar = {'price_date': date(2026, 10, 15), 'open': 120, 'high': 130, 'low': 115,
           'close': 125, 'adj_close': 124, 'volume': 10000, 'split_factor': 1}
    data_repo.store_bars(research_db, 'MU', [bar], now=first)
    data_repo.store_bars(research_db, 'SPY', [{**bar, 'close': 500, 'adj_close': 495}], now=first)
    data_repo.store_bars(research_db, 'MU', [{**bar, 'close': 128, 'adj_close': 127}], now=later)
    research_db.commit()
    before = research_db.execute('SELECT count(*) FROM price_bars').fetchone()
    def forbidden(*a, **kw): pytest.fail('read-only projection fetched an external source')
    monkeypatch.setattr(source_module, 'USSources', forbidden)
    early = fundamentals.view('MU', as_of=first)
    late = fundamentals.view('MU', as_of=later)
    assert early['market']['bars'][0]['close'] == 125
    assert early['market']['bars'][0]['adjusted_close'] == 124
    assert early['market']['benchmarks']['SPY'][0]['close'] == 500
    assert early['market']['benchmarks']['QQQ'] == []
    assert late['market']['bars'][0]['close'] == 128
    assert late['market']['quote']['price'] == 128
    assert late['valuation']['ps_ttm']['value'] is None
    assert fundamentals.view('MU', as_of='2026-10-15T22:00:00Z')['market']['bars'] == []
    assert research_db.execute('SELECT count(*) FROM price_bars').fetchone() == before


def test_conflicting_same_filing_facts_remain_unavailable_until_correction(research_db, monkeypatch, tmp_path):
    s = module()
    from investment_assistant.data import repo
    monkeypatch.setattr(s.research_rows, 'RAW_ROOT', tmp_path)
    class Frozen(datetime):
        current = datetime(2026,6,1,tzinfo=UTC)
        @classmethod
        def now(cls, tz=None): return cls.current
    monkeypatch.setattr(repo,'datetime',Frozen)
    class Conflict(Source):
        def companyfacts(self, ticker):
            payload = financial(raw=b'{"facts":[10,20]}')
            payload['observations'].append({**payload['observations'][0], 'id':'other-provisional', 'value':20, 'source_locator':'/facts/1'})
            return payload
    assert seed_snapshot(research_db, 'financials', Conflict().companyfacts('MU')) == 'partial'
    current = fundamentals.view('MU')['financials'][0]['metrics']['revenue']
    assert current['value'] is None and 'Conflicting' in current['missing_reason']
    assert len(current['input_ids']) == 1 and isinstance(current['input_ids'][0], int)
    assert {r['source_locator'] for r in current['sources']} == {'/facts/0','/facts/1'}
    group = repo.read_rows(research_db,'US:stock:MU','financials')[0]
    assert group['value'] is None
    assert {r['value'] for r in group['conflicts']} == {10,20}
    for fact in group['conflicts']:
        assert hashlib.sha256(Path(fact['snapshot_path']).read_bytes()).hexdigest() == fact['source_hash']
    Frozen.current = datetime(2026,6,2,tzinfo=UTC)
    seed_snapshot(research_db, 'financials', financial())
    assert fundamentals.view('MU')['financials'][0]['metrics']['revenue']['value'] == 10
    historical = fundamentals.view('MU',as_of='2026-06-01T12:00:00Z')['financials'][0]['metrics']['revenue']
    assert historical['value'] is None and historical['input_ids'] == current['input_ids']
    assert len(repo.read_history(research_db,'US:stock:MU','financials')) == 2


def test_unrelated_fact_change_updates_acquisition_without_revising_existing_fact(research_db, monkeypatch, tmp_path):
    s = module()
    from investment_assistant.data import repo
    monkeypatch.setattr(s.research_rows, 'RAW_ROOT', tmp_path)
    seed_snapshot(research_db, 'financials', financial())
    before = repo.read_rows(research_db,'US:stock:MU','financials')[0]
    class AddedFact(Source):
        def companyfacts(self,ticker):
            payload=financial(raw=b'{"facts":[{"revenue":10},{"cfo":5}]}')
            payload['observations'][0]['source_locator']='/facts/9'
            payload['observations'].append({**payload['observations'][0], 'metric':'cfo','concept':'CFO','value':5,'source_locator':'/facts/1'})
            return payload
    seed_snapshot(research_db, 'financials', AddedFact().companyfacts('MU'))
    records=repo.read_rows(research_db,'US:stock:MU','financials')
    revenue=next(r for r in records if r['metric']=='revenue')
    assert revenue['id']==before['id'] and revenue['source_locator']=='/facts/0'
    assert Path(revenue['snapshot_path']).read_bytes()==b'{ "facts": 10 }'
    assert len(repo.read_history(research_db,'US:stock:MU','financials'))==2
    acquisition=repo.states(research_db,'US:stock:MU')['financials']['details']['acquisition']
    assert Path(acquisition['snapshot_path']).read_bytes()==b'{"facts":[{"revenue":10},{"cfo":5}]}'




def test_partial_sec_collection_derives_successful_documents_and_preserves_error(monkeypatch):
    module()
    seen = []
    original = _scan_result(['MU', 'NVDA'], errors={'NVDA': 'original source failure'}, filings=[{'ticker': 'MU'}])
    before = copy.deepcopy(original)
    rows = _record_with(lambda tally, tickers: (seen.append(list(tickers)), [tally.ok(t) for t in tickers]),
                        original, True, monkeypatch)
    assert seen == [['MU']] and original == before            # the successful ticker is extracted, NVDA keeps its error
    assert rows[0]['status'] == 'partial' and rows[0]['failures'] == [{'target': 'NVDA', 'reason': 'original source failure'}]


@pytest.mark.parametrize('monitor_enabled', [False, True])
def test_sec_hook_uses_supplied_config_for_real_local_files_and_exhibits(workbench_db, monkeypatch, tmp_path, monitor_enabled):
    """D47: the sec_text adapter (scope sec_monitor) indexes the originals the monitor archived — the primary and its
    verified exhibit, under the SUPPLIED config's library and state directory, not the process default's — without any
    remote research source or download."""
    from contextlib import contextmanager
    from investment_assistant.config import AssistantConfig, FilingsConfig, SecMonitorConfig
    from investment_assistant.data import registry
    import investment_assistant.core.tx as service
    workbench_db.execute(Path('migrations/014_us_research.sql').read_text())
    workbench_db.commit()

    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(core_tx, 'connection', connection)
    root, state = tmp_path/'supplied-library', tmp_path/'supplied-state'
    primary = root/'MU'/'8-K'/'0000999999-26-000001-report.html'
    exhibit = primary.with_name('0000999999-26-000001-ex99.html')
    primary.parent.mkdir(parents=True)
    primary.write_bytes(b'<html><body>Item 2.02 see exhibit</body></html>')
    exhibit.write_bytes(b'<html><body><p>We expect revenue of $120 million in the next quarter based on the current business outlook.</p></body></html>')
    state.mkdir()
    primary_url = 'https://www.sec.gov/Archives/edgar/data/723125/000099999926000001/report.html'
    exhibit_url = 'https://www.sec.gov/Archives/edgar/data/723125/000099999926000001/ex99.html'
    (state/'state.json').write_text(json.dumps({'version':1,'files':{'MU:0000999999-26-000001':{'status':'ready','meta':{
        'ticker':'MU','form':'8-K','accession_number':'0000999999-26-000001','path':str(primary),
        'attachments':[str(exhibit)],'document_urls':{str(primary):primary_url,str(exhibit):exhibit_url}}}}}))
    supplied = AssistantConfig(filings=FilingsConfig(output_dir=root),
        sec_monitor=SecMonitorConfig(enabled=monitor_enabled,state_dir=str(state)))
    default = AssistantConfig(filings=FilingsConfig(output_dir=tmp_path/'different-default-library'),
        sec_monitor=SecMonitorConfig(state_dir=str(tmp_path/'different-default-state')))
    monkeypatch.setattr('investment_assistant.config.load_config', lambda *a, **k: default)

    class Source:
        downloads = []

        def discover_one(self, ticker, start, end, **kwargs):
            return [{'ticker': 'MU', 'form': '8-K', 'filing_date': '2026-10-01', 'items': ['2.02'],
                     'accession_number': '0000999999-26-000001', 'primary_document': 'report.html',
                     'edgar_url': primary_url}]

        def get(self, url):
            if url.endswith('-index.html'):
                return b'<table summary="Document Format Files"></table>'
            self.downloads.append(url)
            raise AssertionError('the archived originals must not be downloaded again')

    def forbidden(*a,**kw): pytest.fail('local SEC hook invoked a remote research source')
    monkeypatch.setattr(source_module,'USSources',forbidden)
    from investment_assistant.workflow.nodes.sec_monitor import text_collector
    from investment_assistant.data.collect import Tally
    tally = Tally('sec_text', 'daily', ['MU'])
    text_collector(deps=service.Deps(filing_source=Source()), config=supplied, trigger='scheduled', run_id='r1',
                   session=date(2026, 10, 22), now=datetime(2026, 10, 22, 22, 30, tzinfo=UTC), features={})(tally, ['MU'])
    assert tally.outcomes == {'MU': None} and Source.downloads == []
    assert sec_monitor.research_summary(tally, ['MU'])['results'][0]['status'] == 'ready'
    assert tally.record_for('MU')['source_used'] == 'sec'
    parsed = fundamentals.view('MU')['filings']
    assert len(parsed)==1 and parsed[0]['path']==str(primary.resolve())
    assert [x['url'] for x in parsed[0]['analysis']['sources']]==[primary_url,exhibit_url]
    assert parsed[0]['analysis']['guidance'][0]['url']==exhibit_url
    assert parsed[0]['analysis']['guidance'][0]['sha256']==hashlib.sha256(exhibit.read_bytes()).hexdigest()
    assert Path(parsed[0]['snapshots'][1]['snapshot_path']).read_bytes()==exhibit.read_bytes()
    assert not (tmp_path/'different-default-library').exists()          # the default config's library is never touched





def test_financial_source_identity_survives_storage_view_and_watchlist_enrichment(research_db, monkeypatch, tmp_path):
    from contextlib import nullcontext
    from investment_assistant.services import watchlist as wl
    from investment_assistant.data.sources.fundamentals import USSources
    s = module()
    monkeypatch.setattr(s.research_rows, 'RAW_ROOT', tmp_path)
    raw = {'cik': 723125, 'entityName': 'Micron Technology', 'facts': {'us-gaap': {'Revenues': {'units': {'USD': [
        {'start': '2026-01-01', 'end': '2026-03-31', 'val': 10, 'filed': '2026-05-01', 'form': '10-Q', 'accn': '0000723125-26-000001'}]}}}}}
    adapter = USSources()
    encoded = json.dumps(raw).encode()
    monkeypatch.setenv('SEC_USER_AGENT', 'Test test@example.com')
    monkeypatch.setattr(adapter, '_get', lambda url, **kw: (raw, {'source_url': url, 'source_hash': hashlib.sha256(encoded).hexdigest(), 'raw_snapshot': encoded, 'request_params': {}, 'collected_at': '2026-10-16T00:00:00Z'}))
    # Use the actual adapter identity normalization, then the actual persistence path.
    monkeypatch.setattr(adapter.downloader, '_resolve_cik', lambda ticker, ua: '0000723125')
    seed_snapshot(research_db, 'financials', adapter.companyfacts('MU'))
    assert fundamentals.view('MU')['statuses'][0]['last_success_details']['identity']['name'] == 'Micron Technology'
    monkeypatch.setenv('INVESTMENT_ASSISTANT_DATABASE_URL', 'TEMP-test-boundary')
    monkeypatch.setattr(wl, 'connect', lambda _: nullcontext(research_db))
    # The research schema has no workbench tables; the earnings lookup is not under test here.
    # Repository consumers use their owning layers below.
    monkeypatch.setattr(data_repo, 'latest_earnings', lambda conn, ticker, *, cutoff_at: None)
    research_db.execute("INSERT INTO watchlist_items(ticker,status) VALUES ('MU','active'),('AAPL.US','active'),('TSLA.SH','active'),('ABC.DEF','active')")
    listed = wl.watchlist_rows(enriched=True)
    assert next(r for r in listed if r['ticker'] == 'MU')['name'] == 'Micron Technology'
    assert {r['ticker'] for r in listed if r.get('identity_error')} == {'AAPL.US', 'TSLA.SH', 'ABC.DEF'}
    assert wl.current_watchlist() == ['MU']







def test_manual_task_explicit_config_controls_actual_local_source_root(monkeypatch, tmp_path):
    """The supplied config (not the loaded default) decides where the ``sec_text`` adapter reads and writes originals
    (R28: this was ``refresh_local_filings(config=)``; the monitor, the backfill and the Data menu all project the host's
    config through ``adapter_deps_from_service``)."""
    import investment_assistant.config as config_module
    from investment_assistant.config import AssistantConfig, FilingsConfig
    from investment_assistant.data.adapter_support import adapter_deps_from_service
    root = tmp_path / 'supplied-library'
    supplied = AssistantConfig(filings=FilingsConfig(output_dir=root))
    monkeypatch.setattr(config_module, 'load_config',
                        lambda: AssistantConfig(filings=FilingsConfig(output_dir=tmp_path / 'wrong-root')))
    projected = adapter_deps_from_service(core_tx.Deps(), transaction=core_tx._tx, config=supplied)
    assert projected.sec_text_dir == root
    assert adapter_deps_from_service(core_tx.Deps(), transaction=core_tx._tx).sec_text_dir == tmp_path / 'wrong-root'
