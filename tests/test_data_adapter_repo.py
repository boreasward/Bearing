"""Only the explicitly permitted disposable PostgreSQL schema is used."""
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from test_migration_031 import DAY, NOW, apply, migrated_db  # noqa: F401
from investment_assistant.data import repo


@pytest.fixture
def db(migrated_db):
    apply(migrated_db)
    return migrated_db


def bar(day, close='100', adj='98'):
    return dict(price_date=day, open=Decimal(close), high=Decimal(close), low=Decimal(close),
                close=Decimal(close), adj_close=Decimal(adj), volume=1, split_factor=Decimal(1))


def test_ratio_primary_only_latest_visible_date_and_default(db):
    old = DAY - timedelta(days=2)
    repo.store_bars(db, 'NVDA', [bar(old)], now=NOW)
    repo.store_bars(db, 'NVDA', [bar(DAY, adj='80')], now=NOW, source='stooq')
    repo.store_bars(db, 'NVDA', [bar(DAY + timedelta(days=1), adj='50')], now=NOW)
    assert repo.ratio_from_primary(db, 'nvda', DAY) == Decimal('.98')
    assert repo.ratio_from_primary(db, 'EMPTY', DAY) == Decimal(1)
    assert repo.ratio_from_primary(db, 'NVDA', old - timedelta(days=1)) == Decimal(1)


def test_request_targets_and_pending_paper_facts(db):
    for ticker, status in [('QUEUED', 'queued'), ('RUNNING', 'running'), ('WAITING', 'waiting_data'),
                           ('CLOSED', 'closed'), ('DONE', 'concluded')]:
        db.execute('INSERT INTO research_requests (id, request_id, ticker, question, horizon_days, status, source, created_at) '
                   'VALUES (%s,%s,%s,%s,30,%s,%s,%s)', (uuid.uuid4(), uuid.uuid4(), ticker, 'why', status, 'manual', NOW))
    assert repo.collection_request_tickers(db) == ['QUEUED', 'RUNNING', 'WAITING']
    paper = uuid.uuid4()
    db.execute("INSERT INTO accounts (id,name,kind,strategy,status,opening_cash,opened_on) "
               "VALUES (%s,'fixture','paper','follow_plans','active',100000,%s)", (paper, DAY))
    for ticker, status in [('BUY', 'pending'), ('GONE', 'cancelled')]:
        plan = uuid.uuid4()
        db.execute('INSERT INTO trade_plans (id,ticker,signal_date,kind,plan_version,ref_price,atr,'
                   'entry_low,entry_high,stop,target1,valid_from,valid_until,sizing,inputs,status) '
                   "VALUES (%s,%s,%s,'rule','plan-1',100,2,99,101,95,110,%s,%s,'{}','{}','ok')",
                   (plan, ticker, DAY, DAY, DAY + timedelta(days=10)))
        db.execute('INSERT INTO paper_orders (id,account_id,action,ticker,shares,placed_on,placed_by,status,plan_id,chain_signal_date) '
                   "VALUES (%s,%s,'buy',%s,1,%s,'host',%s,%s,%s)", (uuid.uuid4(), paper, ticker, DAY, status, plan, DAY))
    assert repo.pending_paper_tickers(db) == ['BUY']


def test_observation_counts_changes_and_cutoff(db):
    rows = [{'natural_key': 'revenue:2026', 'payload': {'metric': 'revenue', 'value': 1}, 'disclosed_at': NOW}]
    first = repo.store_observation_rows(db, 'US:stock:NVDA', 'financials', rows, now=NOW)
    again = repo.store_observation_rows(db, 'US:stock:NVDA', 'financials', rows, now=NOW + timedelta(days=1))
    assert (first['inserted'], again['inserted'], again['existing']) == (1, 0, 1)
    assert repo.latest_observation_dates(db, ['NVDA'], dataset='financials', cutoff_at=NOW) == {'NVDA': NOW}
    rows[0]['payload']['value'] = 2
    changed = repo.store_observation_rows(db, 'US:stock:NVDA', 'financials', rows, now=NOW + timedelta(days=2))
    assert changed['inserted'] == 1
    assert repo.latest_observation_dates(db, ['NVDA'], dataset='financials', cutoff_at=NOW - timedelta(seconds=1)) == {}


def test_text_store_reuses_index_observations_and_only_counts_new_text(db, tmp_path):
    path = tmp_path/'0000000001-26-000001-doc.htm'
    path.write_text('<html>financial report</html>')
    row = dict(ticker='NVDA', form='10-Q', filing_date=str(DAY), accession_number='0000000001-26-000001',
               primary_document='doc.htm', path=str(path), downloaded=True,
               edgar_url='https://www.sec.gov/Archives/edgar/data/1/000000000126000001/doc.htm')
    first = repo.store_filing_texts(db, [row], target='NVDA', now=NOW)
    second = repo.store_filing_texts(db, [{**row, 'downloaded': False}], target='NVDA', now=NOW)
    assert (first['inserted'], second['inserted']) == (1, 0)
    assert repo.indexed_filing_paths(db, 'NVDA') == {row['accession_number']: str(path)}
    # created_at uses the supplied now, and only K/Q contribute to SEC-text freshness.
    assert repo.latest_text_filing_dates(db, ['NVDA'], cutoff_at=NOW) == {'NVDA': datetime(2026, 10, 30, tzinfo=UTC)}
    assert db.execute('SELECT count(*) FROM filings').fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM us_research_observations WHERE dataset='filings'").fetchone()[0] == 1


def test_real_sector_and_earnings_adapters_only_append_answer_changes(db):
    from investment_assistant.data.adapters import sector, earnings
    for adapter, first, changed in [
        (sector.ADAPTER, {'sic': '9999', 'sic_description': None, 'group': None},
         {'sic': '3571', 'sic_description': 'hardware', 'group': '科技硬件'}),
        (earnings.ADAPTER, {'status': 'unknown', 'earnings_date': None},
         {'status': 'known', 'earnings_date': DAY + timedelta(days=20)}),
    ]:
        source = 'sec' if adapter.id == 'sector' else 'nasdaq'
        counts = [adapter.store(db, [answer], target='NVDA', source_id=source, now=NOW+timedelta(days=n))
                  for n, answer in enumerate([first, first, changed])]
        assert [c['inserted'] for c in counts] == [1, 0, 1]
        assert counts[1]['latest'] == NOW
        assert adapter.latest(db, ['NVDA'], cutoff_at=NOW) == {'NVDA': NOW}


def test_backup_fills_missing_only_and_primary_can_later_append_revision(db):
    from functools import partial
    from investment_assistant.data.adapters import bars
    previous = DAY - timedelta(days=1)
    repo.store_bars(db, 'NVDA', [bar(previous)], now=NOW)
    store = partial(bars.ADAPTER.store, target='NVDA', source_id='stooq', now=NOW)
    rows = [{**bar(previous, adj='100'), 'source': 'stooq'}, {**bar(DAY, adj='100'), 'source': 'stooq'}]
    first = bars.fill_from_backup(db, 'NVDA', session=DAY, rows=rows, store=store)
    again = bars.fill_from_backup(db, 'NVDA', session=DAY, rows=rows, store=store)
    assert (first['inserted'], again['inserted']) == (1, 0)
    assert db.execute('SELECT price_date,source,adj_close,revision FROM price_bars ORDER BY price_date').fetchall() == [
        (previous, 'yfinance', Decimal(98), 1), (DAY, 'stooq', Decimal(98), 1)]
    repo.store_bars(db, 'NVDA', [bar(DAY, close='102', adj='100')], now=NOW+timedelta(days=1))
    assert db.execute('SELECT source,revision FROM price_bars WHERE price_date=%s ORDER BY revision', (DAY,)).fetchall() == [
        ('stooq', 1), ('yfinance', 2)]
