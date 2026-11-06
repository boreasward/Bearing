from investment_assistant.core import tx as core_tx
import investment_assistant.data.repo as data_repo
import hashlib
import importlib.util
import json
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


def test_market_observation_retains_its_original_snapshot_mapping(research_db, monkeypatch, tmp_path):
    s = module()
    monkeypatch.setattr(s.research_rows, 'RAW_ROOT', tmp_path)
    seed_snapshot(research_db, 'market', market())
    bar = next(r for r in data_repo.read_rows(research_db, 'US:stock:MU', 'market') if r['kind'] == 'bar')
    assert Path(bar['snapshot_path']).read_bytes() == b'{ "chart": 1 }'
    assert hashlib.sha256(Path(bar['snapshot_path']).read_bytes()).hexdigest() == bar['source_hash']


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
