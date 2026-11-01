"""Real storage behavior; explicit test URL, connection-local TEMP tables only."""
import importlib.util
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from investment_assistant.core import tx as core_tx

URL = os.environ.get('INVESTMENT_ASSISTANT_TEST_DATABASE_URL')


@pytest.fixture
def research_db(monkeypatch):
    if not URL:
        pytest.skip('explicit test database URL required; TEMP tables only')
    import psycopg
    assert importlib.util.find_spec('investment_assistant.data.repo'), 'missing durable US repository'
    with psycopg.connect(URL) as conn:
        conn.execute('SET search_path TO pg_temp')
        sql = Path('migrations/014_us_research.sql').read_text().replace('CREATE TABLE IF NOT EXISTS', 'CREATE TEMP TABLE')
        conn.execute(sql)
        conn.execute('CREATE TEMP TABLE watchlist_items(ticker TEXT PRIMARY KEY,status TEXT,thesis TEXT,tags TEXT[],created_at TIMESTAMPTZ,updated_at TIMESTAMPTZ)')
        conn.execute('CREATE TEMP TABLE filings(id BIGINT,ticker TEXT,form TEXT,filing_date DATE,accession_number TEXT,primary_document TEXT,file_path TEXT,downloaded_at TIMESTAMPTZ,created_at TIMESTAMPTZ)')
        conn.execute(Path('migrations/016_us_workbench.sql').read_text().split('-- SEC acceptance')[0]
                     .replace('CREATE TABLE IF NOT EXISTS', 'CREATE TEMP TABLE'))
        conn.commit()
        @contextmanager
        def connection():
            yield conn
        monkeypatch.setattr(core_tx, 'connection', connection)
        yield conn
        conn.rollback()


def row(value, **payload):
    return {'natural_key': 'revenue:2026Q1', 'payload': {'metric': 'revenue', 'value': value, **payload},
            'disclosed_at': '2026-05-01T00:00:00Z', 'source_url': 'https://www.sec.gov/example', 'source_hash': 'a' * 64}


def test_append_only_aba_dedup_and_ids(research_db):
    from investment_assistant.data import repo
    ids = []
    for day, value in [(1, 10), (2, 10), (3, 20), (4, 10)]:
        records = repo.store_rows(research_db, 'US:stock:MU', 'financials', [row(value)], now=datetime(2026, 6, day, tzinfo=UTC))
        ids.append(records[0]['id'])
        research_db.commit()
    assert ids[0] == ids[1]
    assert len(set(ids)) == 3
    history = repo.read_history(research_db, 'US:stock:MU', 'financials')
    assert [r['value'] for r in history] == [10, 20, 10]
    assert len({r['revision'] for r in history}) == 3
    assert repo.read_rows(research_db, 'US:stock:MU', 'financials')[0]['id'] == ids[-1]


def test_new_whole_response_hash_does_not_revise_unchanged_economic_fact(research_db):
    from investment_assistant.data import repo
    first = repo.store_rows(research_db, 'US:stock:MU', 'financials', [row(10, source_locator='/facts/0', source_hash='a'*64, id='provisional-a')])[0]
    second = repo.store_rows(research_db, 'US:stock:MU', 'financials', [row(10, source_locator='/facts/9', source_hash='b'*64, id='provisional-b')])[0]
    assert second['id'] == first['id']
    assert (second['source_locator'], second['source_hash']) == ('/facts/0', 'a'*64)
    assert len(repo.read_history(research_db, 'US:stock:MU', 'financials')) == 1


def test_historical_cutoff_checks_seen_disclosure_and_identity(research_db):
    from investment_assistant.data import repo
    repo.store_rows(research_db, 'US:stock:MU', 'guidance', [row(10)], now=datetime(2026, 4, 1, tzinfo=UTC))
    repo.store_rows(research_db, 'US:stock:NVDA', 'guidance', [row(999)], now=datetime(2026, 4, 1, tzinfo=UTC))
    repo.store_rows(research_db, 'US:stock:MU', 'guidance', [row(20)], now=datetime(2026, 6, 1, tzinfo=UTC))
    assert repo.read_rows(research_db, 'US:stock:MU', 'guidance', as_of='2026-04-20T00:00:00Z') == []
    assert [r['value'] for r in repo.read_rows(research_db, 'US:stock:MU', 'guidance', as_of='2026-05-20T00:00:00Z')] == [10]
    assert [r['value'] for r in repo.read_history(research_db, 'US:stock:MU', 'guidance', as_of='2026-05-20T00:00:00Z')] == [10]


def test_dataset_failure_retains_last_success_state(research_db):
    from investment_assistant.data import repo
    repo.store_rows(research_db, 'US:stock:MU', 'market', [row(10)])
    repo.collection_state(research_db, 'US:stock:MU', 'market', status='ready', count=1, details={'coverage': 'one'})
    research_db.commit()
    before = repo.states(research_db, 'US:stock:MU')['market']
    repo.collection_state(research_db, 'US:stock:MU', 'market', status='failed', error='safe failure')
    research_db.commit()
    after = repo.states(research_db, 'US:stock:MU')['market']
    assert after['succeeded_at'] == before['succeeded_at']
    assert after['row_count'] == 1
    assert after['last_success_details'] == {'coverage': 'one'}
    assert repo.read_rows(research_db, 'US:stock:MU', 'market')[0]['value'] == 10


def test_guidance_source_only_correction_appends_revision(research_db):
    from investment_assistant.data import repo
    first_row = row(100, source_url='https://issuer.example/original', source_locator='page 2')
    first_row['source_url'] = 'https://issuer.example/original'
    first = repo.store_rows(research_db, 'US:stock:MU', 'guidance', [first_row])[0]
    corrected = row(100, source_url='https://issuer.example/correct', source_locator='page 3')
    corrected['source_url'] = 'https://issuer.example/correct'
    second = repo.store_rows(research_db, 'US:stock:MU', 'guidance', [corrected])[0]
    assert second['id'] != first['id']
    assert [(r['source_url'],r['source_locator']) for r in repo.read_history(research_db,'US:stock:MU','guidance')] == [
        ('https://issuer.example/original','page 2'), ('https://issuer.example/correct','page 3')]
