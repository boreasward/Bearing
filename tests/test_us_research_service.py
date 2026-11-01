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
    rows, details = (s._financial_rows if dataset == 'financials' else s._market_rows)(payload)
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


def test_raw_snapshot_exact_bytes_and_hash(tmp_path):
    s = module()
    raw = b'{ "a" : 1 }\n'
    digest = hashlib.sha256(raw).hexdigest()
    result = s.archive_snapshot({'source_url': 'https://data.sec.gov/x', 'source_hash': digest, 'raw_snapshot': raw}, root=tmp_path)
    assert Path(result['snapshot_path']).read_bytes() == raw
    assert 'raw_snapshot' not in result
    json.dumps(result)
    with pytest.raises(ValueError): s.archive_snapshot({'source_hash': 'b'*64, 'raw_snapshot': raw}, root=tmp_path)






def test_view_asof_is_local_and_filters_all_datasets(research_db, monkeypatch, tmp_path):
    s = module()
    monkeypatch.setattr(s, 'RAW_ROOT', tmp_path)
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


def test_local_filing_parser_missing_file_is_per_document_and_sources_bound(research_db, monkeypatch, tmp_path):
    s = module()
    from investment_assistant.config import AssistantConfig, FilingsConfig, SecMonitorConfig
    root = tmp_path / 'library'
    path = root / 'MU' / '8-K' / '0000000001-26-000001-report.html'
    path.parent.mkdir(parents=True)
    path.write_text('<html><body>Business update</body></html>')
    config = AssistantConfig(filings=FilingsConfig(output_dir=root), sec_monitor=SecMonitorConfig(state_dir=str(tmp_path / 'state')))
    monkeypatch.setattr(s, 'load_config', lambda: config)
    monkeypatch.setattr(s, 'RAW_ROOT', tmp_path / 'raw')
    for id_, ticker, accession, file_path in [(1, 'MU', '0000000001-26-000001', path),
            (2, 'MU', '0000000001-26-000002', root/'MU'/'missing.html'),
            (3, 'MU', '0000000001-26-000003', root/'NVDA'/'bad.html')]:
        research_db.execute('INSERT INTO filings(id,ticker,form,filing_date,accession_number,file_path) VALUES (%s,%s,%s,%s,%s,%s)', (id_,ticker,'8-K','2026-10-01',accession,str(file_path)))
    research_db.commit()
    result = s.refresh_local_filings(['MU'])
    assert result['results'][0]['status'] == 'partial'
    v = fundamentals.view('MU')
    assert len(v['filings']) == 1
    assert v['filings'][0]['analysis']['sources'][0]['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    state = next(r for r in v['statuses'] if r['dataset']=='filings')
    assert len(state['details']['document_errors']) == 2
    assert v['filings'][0]['limitations']  # no fabricated URL from accession prefix
    assert fundamentals.view('MU', as_of='2026-10-02T00:00:00Z')['filings'] == []




def test_lock_covers_source_io_and_is_released_after_failure(research_db, monkeypatch, tmp_path):
    s = module()
    from test_us_research_postgres import URL
    import psycopg
    from contextlib import contextmanager
    monkeypatch.setattr(s, 'RAW_ROOT', tmp_path)
    outer = core_tx.connection
    def local_rows(conn, ticker, **kwargs):
            @contextmanager
            def second_connection():
                with psycopg.connect(URL) as other:
                    other.execute('SET search_path TO pg_temp')
                    yield other
            monkeypatch.setattr(core_tx, 'connection', second_connection)
            try:
                with pytest.raises(ValueError, match='正在运行'):
                    s.refresh_local_filings(['MU'])
            finally:
                monkeypatch.setattr(core_tx, 'connection', outer)
            raise RuntimeError('secret source exception')
    monkeypatch.setattr(s, '_local_filing_rows', local_rows)
    result = s.refresh_local_filings(['MU'])
    assert result['results'][0]['status'] == 'failed'
    with psycopg.connect(URL) as other:
        assert other.execute("SELECT pg_try_advisory_lock(hashtext('us-research-sync'))").fetchone() == (True,)
        other.execute("SELECT pg_advisory_unlock(hashtext('us-research-sync'))")




def test_original_monitor_exhibit_metadata_is_used_without_url_inference(research_db, monkeypatch, tmp_path):
    s = module()
    from investment_assistant.config import AssistantConfig, FilingsConfig, SecMonitorConfig
    root, state = tmp_path/'library', tmp_path/'state'
    primary = root/'MU'/'8-K'/'0000999999-26-000001-report.html'
    exhibit = primary.with_name('0000999999-26-000001-ex99.html')
    primary.parent.mkdir(parents=True)
    primary.write_text('<html><body>Item 2.02 see exhibit</body></html>')
    exhibit.write_text('<html><body><p>We expect revenue of $120 million in the next quarter based on the current business outlook.</p></body></html>')
    state.mkdir()
    primary_url = 'https://www.sec.gov/Archives/edgar/data/723125/000099999926000001/report.html'
    exhibit_url = 'https://www.sec.gov/Archives/edgar/data/723125/000099999926000001/ex99.html'
    (state/'state.json').write_text(json.dumps({'version':1,'files':{'MU:0000999999-26-000001':{'status':'ready','meta':{
        'ticker':'MU','form':'8-K','accession_number':'0000999999-26-000001','path':str(primary),
        'attachments':[str(exhibit)],'document_urls':{str(primary):primary_url,str(exhibit):exhibit_url}}}}}))
    monkeypatch.setattr(s, 'load_config', lambda: AssistantConfig(filings=FilingsConfig(output_dir=root),sec_monitor=SecMonitorConfig(state_dir=str(state))))
    monkeypatch.setattr(s, 'RAW_ROOT', tmp_path/'raw')
    research_db.execute('INSERT INTO filings(id,ticker,form,filing_date,accession_number,file_path) VALUES (1,%s,%s,%s,%s,%s)', ('MU','8-K','2026-10-01','0000999999-26-000001',str(primary)))
    research_db.commit()
    s.refresh_local_filings(['MU'])
    filing = fundamentals.view('MU')['filings'][0]
    assert [x['url'] for x in filing['analysis']['sources']] == [primary_url,exhibit_url]
    assert filing['analysis']['guidance'][0]['url'] == exhibit_url
    assert Path(filing['snapshots'][1]['snapshot_path']).read_bytes() == exhibit.read_bytes()


@pytest.mark.parametrize('monitor', [False, True])
def test_sec_success_hook_retains_original_result_and_runs_local_extraction(monkeypatch, monitor):
    module()
    from dataclasses import replace
    from investment_assistant.tasks import filings
    from investment_assistant.config import AssistantConfig, SecMonitorConfig
    from investment_assistant.data import acquisition as service
    original = {'run_id':'existing','status':'success','summary':{'downloaded_count':1,'filings':[{'ticker':'MU','path':'local'}], 'errors':{},'delivery':{'status':'sent'}}}
    # Local refresh is the I/O boundary here; its database/parser path has real TEMP tests above.
    monkeypatch.setattr(sec_monitor, 'refresh_local_filings', lambda tickers, **kw: {'tickers':tickers,'notifications':'disabled'})
    # v2.8: the hook is ``filings._local_research_after_success``, run inside the sec_monitor node after the SEC pass
    result = sec_monitor._local_research_after_success(copy.deepcopy(original), AssistantConfig(sec_monitor=SecMonitorConfig(enabled=monitor)))
    assert {k:v for k,v in result.items() if k not in ('research','data_status')} == original
    assert result['research'] == {'tickers':['MU'],'notifications':'disabled'}


def test_failed_sec_run_does_not_add_research_or_replace_original_error(monkeypatch):
    module()
    from investment_assistant.tasks import filings
    from investment_assistant.config import AssistantConfig
    from investment_assistant.data import acquisition as service
    original = {'run_id':'existing','status':'error','summary':{'errors':{'MU':'original'}, 'filings':[]}}
    def forbidden(*a): pytest.fail('failed original SEC job started extraction')
    monkeypatch.setattr(sec_monitor, 'refresh_local_filings', forbidden)
    result = sec_monitor._local_research_after_success(copy.deepcopy(original), AssistantConfig())
    assert {k: v for k, v in result.items() if k != 'data_status'} == original


def test_sec_local_hook_failure_is_sanitized_and_preserves_original_success(monkeypatch):
    module()
    from investment_assistant.tasks import filings
    from investment_assistant.config import AssistantConfig
    from investment_assistant.data import acquisition as service
    original = {'status':'success','summary':{'filings':[{'ticker':'MU'}], 'errors':{}}}
    def bad(*a, **kw): raise ValueError('proxy://user:secret@host')
    monkeypatch.setattr(sec_monitor, 'refresh_local_filings', bad)
    result = sec_monitor._local_research_after_success(copy.deepcopy(original), AssistantConfig())
    assert result['status']=='success' and result['summary']==original['summary']
    assert result['research']['errors'] and 'secret' not in json.dumps(result)


def test_market_observation_retains_its_original_snapshot_mapping(research_db, monkeypatch, tmp_path):
    s = module()
    monkeypatch.setattr(s, 'RAW_ROOT', tmp_path)
    seed_snapshot(research_db, 'market', market())
    bar = next(r for r in data_repo.read_rows(research_db, 'US:stock:MU', 'market') if r['kind'] == 'bar')
    assert Path(bar['snapshot_path']).read_bytes() == b'{ "chart": 1 }'
    assert hashlib.sha256(Path(bar['snapshot_path']).read_bytes()).hexdigest() == bar['source_hash']




def test_dataset_rollback_keeps_prior_values_and_other_commits(research_db, monkeypatch, tmp_path):
    s = module()
    monkeypatch.setattr(s, 'RAW_ROOT', tmp_path)
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
    monkeypatch.setattr(s, 'RAW_ROOT', tmp_path)
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
    monkeypatch.setattr(s, 'RAW_ROOT', tmp_path)
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
    monkeypatch.setattr(s, 'RAW_ROOT', tmp_path)
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
    monkeypatch.setattr(s, 'RAW_ROOT', tmp_path)
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
    from investment_assistant.tasks import filings
    from investment_assistant.config import AssistantConfig
    from investment_assistant.data import acquisition as service
    original={'status':'error','summary':{'filings':[{'ticker':'MU'}],'errors':{'NVDA':'original source failure'}}}
    monkeypatch.setattr(sec_monitor,'refresh_local_filings',lambda tickers,**kw:{'tickers':tickers,'notifications':'disabled'})
    result=sec_monitor._local_research_after_success(copy.deepcopy(original), AssistantConfig())
    assert result['status']=='error' and result['summary']==original['summary']
    assert result['research']['tickers']==['MU']


@pytest.mark.parametrize('monitor_enabled', [False, True])
def test_sec_hook_uses_supplied_config_for_real_local_files_and_exhibits(research_db, monkeypatch, tmp_path, monitor_enabled):
    s = module()
    from investment_assistant.config import AssistantConfig, FilingsConfig, SecMonitorConfig
    from investment_assistant.tasks import filings
    from investment_assistant.tasks import filings as monitor_module
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
    monkeypatch.setattr(s,'load_config',lambda:default)
    monkeypatch.setattr(s,'RAW_ROOT',tmp_path/'raw')
    def forbidden(*a,**kw): pytest.fail('local SEC hook invoked a remote research source')
    monkeypatch.setattr(source_module,'USSources',forbidden)
    research_db.execute('INSERT INTO filings(id,ticker,form,filing_date,accession_number,file_path) VALUES (1,%s,%s,%s,%s,%s)',
        ('MU','8-K','2026-10-01','0000999999-26-000001',str(primary)))
    research_db.commit()
    original = {'run_id':'original-sec-job','status':'success','summary':{'filings':[{'ticker':'MU','path':str(primary)}],'errors':{},'delivery':{'status':'sent'}}}
    result = sec_monitor._local_research_after_success(copy.deepcopy(original), supplied)
    assert result['research']['results'][0]['status']=='ready'
    assert {k:v for k,v in result.items() if k not in ('research','data_status')}==original
    parsed = fundamentals.view('MU')['filings']
    assert len(parsed)==1 and parsed[0]['path']==str(primary)
    assert [x['url'] for x in parsed[0]['analysis']['sources']]==[primary_url,exhibit_url]
    assert parsed[0]['analysis']['guidance'][0]['url']==exhibit_url
    assert parsed[0]['analysis']['guidance'][0]['sha256']==hashlib.sha256(exhibit.read_bytes()).hexdigest()
    assert Path(parsed[0]['snapshots'][1]['snapshot_path']).read_bytes()==exhibit.read_bytes()




def test_financial_source_identity_survives_storage_view_and_watchlist_enrichment(research_db, monkeypatch, tmp_path):
    from contextlib import nullcontext
    from investment_assistant.services import watchlist as wl
    from investment_assistant.data.sources.fundamentals import USSources
    s = module()
    monkeypatch.setattr(s, 'RAW_ROOT', tmp_path)
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







def test_manual_task_explicit_config_controls_actual_local_source_root(research_db, monkeypatch, tmp_path):
    from investment_assistant.config import AssistantConfig, FilingsConfig
    s = module()
    root = tmp_path / 'supplied-library'
    primary = root / 'MU' / '8-K' / '0000999999-26-000001-report.html'
    primary.parent.mkdir(parents=True)
    primary.write_bytes(b'<html><body>Company outlook and results</body></html>')
    supplied = AssistantConfig(filings=FilingsConfig(output_dir=root))
    monkeypatch.setattr(s, 'load_config', lambda: AssistantConfig(filings=FilingsConfig(output_dir=tmp_path / 'wrong-root')))
    monkeypatch.setattr(s, 'RAW_ROOT', tmp_path / 'raw')
    research_db.execute('INSERT INTO filings(id,ticker,form,filing_date,accession_number,file_path) VALUES (1,%s,%s,%s,%s,%s)',
        ('MU', '8-K', '2026-10-01', '0000999999-26-000001', str(primary)))
    research_db.commit()
    result = s.refresh_local_filings(['MU'], config=supplied)
    assert result['results'][0]['status'] == 'ready'
    assert fundamentals.view('MU')['filings'][0]['path'] == str(primary)
