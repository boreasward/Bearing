import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from investment_assistant.core import tx as core_tx


@pytest.fixture
def card_db(monkeypatch):
    """Connection-local TEMP tables for the us_research guidance store."""
    url = os.environ.get('INVESTMENT_ASSISTANT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('explicit URL required; TEMP tables only')
    import psycopg
    with psycopg.connect(url) as conn:
        conn.execute('SET search_path TO pg_temp')
        for name in ('013_ashare_research.sql', '014_us_research.sql'):
            conn.execute(Path('migrations', name).read_text().split('-- Manual verification')[0].replace('CREATE TABLE IF NOT EXISTS', 'CREATE TEMP TABLE'))
        conn.execute('CREATE TEMP TABLE filings(id BIGINT,ticker TEXT,form TEXT,filing_date DATE,accession_number TEXT,primary_document TEXT,file_path TEXT,downloaded_at TIMESTAMPTZ,created_at TIMESTAMPTZ)')
        conn.execute('CREATE TEMP TABLE watchlist_items(ticker TEXT,status TEXT)')
        conn.commit()

        @contextmanager
        def connection():
            yield conn

        from investment_assistant.us_research import guidance, service
        monkeypatch.setattr(guidance, 'connection', connection)
        monkeypatch.setattr(core_tx, 'connection', connection)
        yield conn
        conn.rollback()


def guidance():
    from investment_assistant.us_research import guidance
    return guidance


def guide(**extra):
    return dict(metric='revenue',target_start='2026-01-01',target_end='2026-03-31',basis='us-gaap:consolidated',unit='USD',low=90,high=110,issued_at='2025-12-01T00:00:00Z',source_url='https://issuer.test/outlook',source_locator='p2',note='',**extra)


def actual(**extra):
    metric = {'value':105,'unit':'USD','basis':'us-gaap:consolidated','input_ids':[8]}
    metric.update(extra)
    return [{'start':'2026-01-01','end':'2026-03-31','metrics':{'revenue':metric}}]


@pytest.mark.parametrize('change', [{'basis':'non-gaap:consolidated'},{'unit':'EUR'},{'value':None}])
def test_mismatched_actual_stays_uncomputed(change):
    result = guidance().comparisons([guide()],actual(**change))[0]
    assert result['status'] == 'not_comparable'
    assert result['actual'] is None


def test_exact_interval_and_basis_required():
    values = actual(); values[0]['start'] = '2026-01-02'
    assert guidance().comparisons([guide()],values)[0]['status'] == 'not_comparable'
    eps = guide(); eps.update(metric='eps_diluted',unit='USD/shares',basis='us-gaap:reported_share_basis_unverified')
    values = actual(basis=eps['basis'],unit=eps['unit']); values[0]['metrics']={'eps_diluted':values[0]['metrics']['revenue']}
    assert guidance().comparisons([eps],values)[0]['status'] == 'not_comparable'
    result = guidance().comparisons([guide()],actual())[0]
    assert (result['status'],result['actual'],result['midpoint'],result['input_ids']) == ('in_range',105,100,[8])


@pytest.mark.parametrize('field,value', [('low',111),('high',float('inf')),('source_url',''),('target_end','2025-01-01'),('issued_at','2025-12-01'),('basis','GAAP')])
def test_invalid_guidance_rejected(field,value):
    payload=guide(); payload[field]=value
    with pytest.raises(ValueError): guidance().Guidance.model_validate(payload)


def test_append_revisions_keep_original_and_security_ownership(card_db,client):
    first = client.post('/api/us-research/MU/guidance',json=guide()).json()['record']
    revised = guide(supersedes_id=first['id']); revised['low']=95
    second=client.post('/api/us-research/MU/guidance',json=revised).json()['record']
    assert first['id'] != second['id']
    records=client.get('/api/us-research/MU/guidance').json()['records']
    assert [r['low'] for r in records] == [90,95]
    assert records[0]['issued_at'] == first['issued_at']
    assert records[0]['first_seen_at']
    assert client.post('/api/us-research/NVDA/guidance',json=revised).status_code == 400
    wrong=card_db.execute("INSERT INTO us_research_observations(security_id,dataset,natural_key,revision,content_hash,payload,first_seen_at,last_seen_at) VALUES('US:stock:MU','financials','bad','bad','bad','{}',now(),now()) RETURNING id").fetchone()[0]
    revised['supersedes_id']=wrong
    assert client.post('/api/us-research/MU/guidance',json=revised).status_code == 400


def test_cutoff_checks_seen_and_issued_and_comparison_inputs(card_db):
    from investment_assistant.us_research import repository
    record=repository.store_rows(card_db,'US:stock:MU','guidance',[{'natural_key':'g','payload':guide(),'disclosed_at':'2025-12-01T00:00:00Z'}],now=datetime(2026,4,1,tzinfo=UTC))[0]
    assert guidance().list_records('MU',as_of='2026-03-01T00:00:00Z') == []
    assert guidance().list_records('MU',as_of='2026-04-02T00:00:00Z')[0]['id'] == record['id']
    late=actual(); late[0]['metrics']['revenue']['first_seen_at']='2026-06-01T00:00:00Z'
    assert guidance().comparisons([record],late,as_of='2026-04-02T00:00:00Z')[0]['status'] == 'not_comparable'


def test_read_service_integrates_current_and_cutoff_guidance(card_db,monkeypatch):
    from contextlib import contextmanager
    from investment_assistant.us_research import repository,service
    @contextmanager
    def connection(): yield card_db
    monkeypatch.setattr(service,'connection',connection)
    repository.store_rows(card_db,'US:stock:MU','guidance',[{'natural_key':'g','payload':guide(),'disclosed_at':'2025-12-01T00:00:00Z'}],now=datetime(2026,4,1,tzinfo=UTC))
    repository.store_rows(card_db,'US:stock:MU','financials',[{'natural_key':'r','payload':{'metric':'revenue','value':105,'unit':'USD','basis':'us-gaap:consolidated','start':'2026-01-01','end':'2026-03-31','filed':'2026-04-01','accession':'a','concept':'Revenues'},'disclosed_at':'2026-04-01T00:00:00Z'}],now=datetime(2026,4,2,tzinfo=UTC))
    view=service.view('MU')
    assert 'card' not in view
    assert view['guidance']['comparisons'][0]['status']=='in_range'
    historical=service.view('MU',as_of='2026-04-01T00:00:00Z')
    assert historical['guidance']['comparisons'][0]['status']=='not_comparable'
    assert service.view('MU',as_of='2026-03-01T00:00:00Z')['guidance']['records']==[]


@pytest.mark.parametrize('field,value',[('issued_at','2026-04-01T00:00:00Z'),('low',True)])
def test_incoherent_issuance_and_boolean_amount_rejected(field,value):
    payload=guide(); payload[field]=value
    with pytest.raises(ValueError): guidance().Guidance.model_validate(payload)


def test_comparison_cutoff_rejects_later_disclosed_source():
    values=actual(); values[0]['metrics']['revenue']['sources']=[{'filed':'2026-06-01'}]
    record={**guide(),'first_seen_at':'2026-01-01T00:00:00Z'}
    assert guidance().comparisons([record],values,as_of='2026-04-01T00:00:00Z')[0]['status']=='not_comparable'
