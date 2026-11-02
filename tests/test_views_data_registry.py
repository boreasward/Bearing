"""The Data menu generated from the adapter registry (v2.12, spec §5.6, Task 4 Step 15): nine adapter cards plus the
indicator card, per-family sources and 「今日来源」, the source column of the target rows and bars, and the raw views of
the families v2.12 added. All data is synthetic; the reads never collect."""
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

import investment_assistant.core.tx as service
import investment_assistant.data.repo as data_repo
import investment_assistant.views.data as data_view
from investment_assistant.core import tx as core_tx
from investment_assistant.data import registry
from investment_assistant.data.identity import stock_id
from investment_assistant.derivation.indicators import registry as indicator_registry

SESSION = date(2026, 10, 22)                         # Thursday
NOW = datetime(2026, 10, 22, 22, 30, tzinfo=UTC)     # 18:30 New York


@pytest.fixture
def db(workbench_db, monkeypatch):
    # migration 014 (US research tables) is in every real database; the shared fixture list omits it
    workbench_db.execute(Path('migrations/014_us_research.sql').read_text())
    workbench_db.commit()

    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(core_tx, 'connection', connection)
    return workbench_db


@pytest.fixture
def db_pre031(workbench_db_pre031, monkeypatch):
    @contextmanager
    def connection():
        yield workbench_db_pre031
    monkeypatch.setattr(core_tx, 'connection', connection)
    return workbench_db_pre031


def _run(conn, **overrides):
    row = {'family': 'earnings', 'job': 'daily', 'trigger': 'scheduled', 'run_id': 'r1', 'scope': 'family',
           'ticker': None, 'session': SESSION, 'status': 'success', 'target_count': 5, 'ok_count': 5,
           'failures': [], 'detail': {}, 'started_at': NOW, 'finished_at': NOW}
    row.update(overrides)
    data_repo.insert_fetch_run(conn, row)


def test_cards_from_registry_and_indicator_card(db):
    out = data_view.overview(now=NOW)
    cards = {card['id']: card for card in out['families']}
    assert [card['id'] for card in out['families']] == [*registry.ids(), 'indicators']
    assert 'atr' not in cards                                  # D12: ATR is an indicator, not a data family

    for adapter in registry.ADAPTERS:                          # every adapter card lists its sources
        card = cards[adapter.id]
        assert card['title'] == adapter.title and card['purpose'] == adapter.purpose
        assert card['sources'] == [{'id': source.id, 'note': source.note} for source in adapter.sources]
        assert set(card['today_sources']) == {'primary', 'backup', 'failed'}
        assert card['job']['name'] == 'daily'                  # the daily collect node runs all nine
    assert [s['id'] for s in cards['earnings']['sources']] == ['yfinance', 'nasdaq']    # the one backup source
    assert [s['id'] for s in cards['news']['sources']] == ['yfinance']

    indicators = cards['indicators']
    assert indicators['kind'] == 'derived' and indicators['version'] == 'indicator-1'
    assert [item['id'] for item in indicators['items']] == [i.id for i in indicator_registry.INDICATORS]
    atr14 = next(item for item in indicators['items'] if item['id'] == 'atr14')
    assert atr14 == {'id': 'atr14', 'title': 'ATR14', 'basis': 'high_low', 'window': 15,
                     'used_by': ['plan-2', 'screen-1', 'agent-tools'], 'concept': 'indicator.atr14'}
    assert all(set(item) == {'id', 'title', 'basis', 'window', 'used_by', 'concept'} for item in indicators['items'])

    # the special card and the list endpoint carry the same items; the card's detail has no runs or rows
    assert data_view.indicators() == {'version': 'indicator-1', 'items': indicators['items']}
    assert data_view.family_detail('indicators', now=NOW) == {'card': indicators, 'runs': [], 'rows': []}


def test_legacy_pre031_overview_keeps_atr_projection_without_the_new_cards(db_pre031):
    """Spec §15: before migration 031 the old interfaces work as before — six cards, the ATR card derived from bars,
    no indicator card and no table of the new layers read; every card still lists its sources."""
    out = data_view.overview(now=NOW)
    assert [card['id'] for card in out['families']] == ['bars', 'sec_filings', 'news', 'atr', 'fred', 'universe']
    atr = next(card for card in out['families'] if card['id'] == 'atr')
    assert atr['kind'] == 'derived' and atr['fetch_via'] == 'bars'
    assert atr['sources'] == [] and atr['today_sources'] is None        # a projection has no source of its own
    bars = out['families'][0]
    assert bars['sources'] == [{'id': 'yfinance', 'note': registry.get('bars').sources[0].note}]
    with pytest.raises(service.Unavailable, match='031'):
        data_view.family_detail('indicators', now=NOW)
    assert data_view.family_detail('atr', now=NOW)['card']['id'] == 'atr'


def test_today_sources_counts(db):
    """「今日来源」: the session's newest family-scope run, final successful sources split primary / backup, plus the
    targets that got nothing; no run of this session counts nothing."""
    _run(db, family='earnings', target_count=6, ok_count=5,
         failures=[{'target': 'ZZ', 'reason': 'boom'}],
         detail={'source_counts': {'yfinance': 3, 'nasdaq': 2}, 'source_counts_complete': True})
    _run(db, family='news', target_count=4, ok_count=4, detail={})                 # a row from before v2.12
    _run(db, family='sector', target_count=2, ok_count=2, session=SESSION - timedelta(days=1),
         detail={'source_counts': {'sec': 2}})                                   # yesterday's run is not today's
    _run(db, family='fred', target_count=3, ok_count=0, status='skipped', detail={'reason': 'no key'})
    _run(db, family='fundamentals', target_count=3, ok_count=3, scope='ticker', ticker='MU',
         detail={'source_counts': {'sec': 3}})                                    # a ticker probe is no family run
    db.commit()

    cards = {card['id']: card for card in data_view.overview(now=NOW)['families']}
    assert cards['earnings']['today_sources'] == {'primary': 3, 'backup': 2, 'failed': 1}
    assert cards['news']['today_sources'] == {'primary': 4, 'backup': 0, 'failed': 0}
    assert cards['sector']['today_sources'] == {'primary': 0, 'backup': 0, 'failed': 0}
    assert cards['fred']['today_sources'] == {'primary': 0, 'backup': 0, 'failed': 0}
    assert cards['fundamentals']['today_sources'] == {'primary': 0, 'backup': 0, 'failed': 0}
    assert cards['bars']['today_sources'] == {'primary': 0, 'backup': 0, 'failed': 0}


def test_detail_rows_show_the_source_each_target_was_last_taken_from(db):
    db.execute("INSERT INTO watchlist_items (ticker, status, tags) VALUES ('MU', 'active', '{}')")
    db.execute("INSERT INTO watchlist_items (ticker, status, tags) VALUES ('NVDA', 'active', '{}')")
    _run(db, family='earnings', run_id='old', detail={'targets': [
        {'target': 'MU', 'ok': True, 'source_used': 'yfinance', 'fallback': {'tried': False, 'reason': None}}]},
        finished_at=NOW - timedelta(hours=2))
    _run(db, family='earnings', run_id='new', detail={'targets': [
        {'target': 'MU', 'ok': True, 'source_used': 'nasdaq', 'fallback': {'tried': True, 'reason': 'timeout'}}]})
    db.commit()

    rows = {row['target']: row for row in data_view.family_detail('earnings', now=NOW)['rows']}
    assert rows['MU']['source_used'] == 'nasdaq'
    assert rows['MU']['fallback'] == {'tried': True, 'reason': 'timeout'}
    assert rows['NVDA']['source_used'] is None and rows['NVDA']['fallback'] is None


def test_browse_bars_has_the_source_column(db):
    for day, source in ((SESSION - timedelta(days=1), 'yfinance'), (SESSION, 'stooq')):
        db.execute('INSERT INTO price_bars (ticker, price_date, open, high, low, close, volume, adj_close, '
                   'revision, fetched_at, source) VALUES (%s, %s, 100, 105, 99, 104, 1000, 104, 1, %s, %s)',
                   ['NVDA', day, NOW, source])
    db.commit()
    rows = data_view.browse('bars', now=NOW, ticker='NVDA')['rows']
    assert [(r['price_date'], r['source']) for r in rows] == [(SESSION, 'stooq'),
                                                              (SESSION - timedelta(days=1), 'yfinance')]


def test_browse_earnings_and_sector_history_respect_the_cutoff(db):
    data_repo.store_earnings(db, 'NVDA', {'status': 'unknown', 'earnings_date': None},
                             now=NOW - timedelta(days=3))
    data_repo.store_earnings(db, 'NVDA', {'status': 'known', 'earnings_date': date(2026, 11, 19)},
                             now=NOW - timedelta(days=1), source='nasdaq')
    data_repo.store_earnings(db, 'NVDA', {'status': 'known', 'earnings_date': date(2026, 12, 3)}, now=NOW + timedelta(days=1))
    data_repo.insert_sector_classification(db, dict(ticker='NVDA', sic='3674', sic_description='semiconductors',
        group='半导体', source='sec', fetched_at=NOW - timedelta(days=40)))
    data_repo.insert_sector_classification(db, dict(ticker='NVDA', sic='3571', sic_description='computers',
        group='科技硬件', source='sec', fetched_at=NOW - timedelta(days=1)))
    data_repo.insert_sector_classification(db, dict(ticker='NVDA', sic='9999', sic_description='later',
        group='其他', source='sec', fetched_at=NOW + timedelta(days=1)))
    db.commit()

    earnings = data_view.browse('earnings', now=NOW, ticker='nvda')
    assert earnings['family'] == 'earnings' and earnings['key'] == 'NVDA' and earnings['has_data'] is True
    assert [(r['status'], r['source']) for r in earnings['rows']] == [('known', 'nasdaq'), ('unknown', 'yfinance')]
    assert earnings['rows'][0]['earnings_date'] == date(2026, 11, 19)         # newest first, nothing after the cutoff

    sector = data_view.browse('sector', now=NOW, ticker='NVDA')
    assert [(r['sic'], r['group']) for r in sector['rows']] == [('3571', '科技硬件'), ('3674', '半导体')]
    assert set(sector['rows'][0]) == {'ticker', 'sic', 'sic_description', 'group', 'source', 'fetched_at'}


def _fact(metric, end, start, value, *, form='10-Q', filed='2026-08-01', unit='USD', kind='duration', **extra):
    payload = {'metric': metric, 'value': value, 'unit': unit, 'basis': 'us-gaap', 'start': start, 'end': end,
               'period_kind': kind, 'form': form, 'accession': '0001-26-000001', 'filed': filed, 'concept': metric,
               **extra}
    return {'natural_key': f'{metric}:{start}:{end}', 'payload': payload, 'disclosed_at': filed,
            'source_url': 'https://data.sec.gov/x', 'source_hash': 'h'}


def test_browse_fundamentals_gives_the_latest_eight_fiscal_quarters(db):
    quarters = [(date(2024, 1, 1) + timedelta(days=91 * i)) for i in range(10)]
    rows = []
    for i, start in enumerate(quarters):
        end = start + timedelta(days=89)
        rows.append(_fact('revenue', end.isoformat(), start.isoformat(), 100 + i))
    rows.append(_fact('revenue', '2026-12-31', '2026-01-01', 999, form='10-K'))              # a full year: not a quarter
    rows.append(_fact('cash', quarters[-1].isoformat(), None, 5, kind='instant'))              # instant fact, not a quarter end
    last_end = (quarters[-1] + timedelta(days=89)).isoformat()
    rows.append(_fact('cash', last_end, None, 7, kind='instant'))                              # instant on a quarter end
    rows.append(_fact('revenue', '2030-03-31', '2030-01-01', 1, filed='2030-04-01'))           # disclosed after the cutoff
    data_repo.store_observation_rows(db, stock_id('NVDA'), 'financials', rows, now=NOW)
    db.commit()

    out = data_view.browse('fundamentals', now=NOW, ticker='NVDA')
    periods = sorted({r['period'] for r in out['rows']}, reverse=True)
    assert len(periods) == 8 and periods[0] == last_end                       # the newest eight, newest first
    assert out['rows'][0]['period'] == last_end
    assert {r['metric'] for r in out['rows']} == {'revenue', 'cash'}
    assert not any(r['period'] in ('2026-12-31', '2030-03-31') for r in out['rows'])
    assert set(out['rows'][0]) == {'metric', 'value', 'unit', 'period', 'start', 'form', 'filed', 'conflict'}
    assert out['has_data'] is True


def test_browse_sec_text_lists_originals_with_the_extracted_facts(db):
    for accession, form, day, downloaded in (('0001045810-26-000001', '10-Q', '2026-08-28', NOW - timedelta(days=50)),
                                             ('0001045810-26-000002', '8-K', '2026-10-21', NOW - timedelta(days=1)),
                                             ('0001045810-26-000003', '10-K', '2026-10-23', NOW + timedelta(days=1))):
        db.execute('INSERT INTO filings (ticker, form, filing_date, accession_number, primary_document, file_path, '
                   'downloaded_at, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                   ['NVDA', form, day, accession, 'd.htm', f'/x/{accession}', downloaded, downloaded])
    analysis = {'kind': 'financial_results', 'coverage': 'partial', 'warnings': ['w'],
                'facts': {'revenue': {'display': '$30.0B', 'period': '2026-05-01 至 2026-07-30', 'value': '30000000000'}},
                'sources': [{'path': '/x', 'url': 'u', 'sha256': 'a'}, {'path': '/y', 'url': 'v', 'sha256': 'b'}]}
    data_repo.store_observation_rows(db, stock_id('NVDA'), 'filings', [
        {'natural_key': '0001045810-26-000002', 'payload': {'analysis': analysis}, 'disclosed_at': '2026-10-21',
         'source_url': 'u', 'source_hash': 'a'}], now=NOW - timedelta(days=1))
    db.commit()

    out = data_view.browse('sec_text', now=NOW, ticker='NVDA')
    assert [r['accession_number'] for r in out['rows']] == ['0001045810-26-000002', '0001045810-26-000001']
    newest, oldest = out['rows']
    assert newest['analyzed'] is True and newest['documents'] == 2 and newest['coverage'] == 'partial'
    assert newest['facts'] == {'revenue': {'display': '$30.0B', 'period': '2026-05-01 至 2026-07-30'}}
    assert oldest['analyzed'] is False and oldest['facts'] == {}              # stored but never analysed: said so


def test_indicators_route_lists_the_registry_and_needs_no_database(client):
    out = client.get('/api/data/indicators')
    assert out.status_code == 200
    assert out.json()['version'] == 'indicator-1'
    assert [item['id'] for item in out.json()['items']] == [i.id for i in indicator_registry.INDICATORS]
