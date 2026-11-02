"""Only the explicitly permitted disposable PostgreSQL schema is used."""
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TypedDict

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
               accepted_at=datetime(2026, 10, 30, tzinfo=UTC).isoformat(),
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


# SEC text regressions exercise the real adapter -> repository -> Tally path.
class FilingMeta(TypedDict):
    ticker: str
    form: str
    filing_date: str
    accession_number: str
    primary_document: str
    edgar_url: str
    index_url: str
    accepted_at: str | None
    items: list[str]


def filing_meta(n=1, *, form='10-K', accepted: datetime | None = NOW) -> FilingMeta:
    accession = f'0000000001-26-{n:06d}'
    base = f'https://www.sec.gov/Archives/edgar/data/1/{accession.replace("-", "")}/'
    return {'ticker': 'NVDA', 'form': form, 'filing_date': str(DAY), 'accession_number': accession,
            'primary_document': 'doc.htm', 'edgar_url': base+'doc.htm', 'index_url': base+accession+'-index.html',
            'accepted_at': accepted.isoformat() if accepted else None, 'items': ['2.02'] if form == '8-K' else []}


class FilingSource:
    def __init__(self, metas, *, exhibits=()):
        self.metas, self.exhibits, self.calls = metas, exhibits, []

    def discover_one(self, *args, **kwargs):
        return self.metas  # Deliberately ignores cutoff: adapter must defend injected sources too.

    def get(self, url):
        self.calls.append(url)
        if url.endswith('-index.html'):
            rows = ''.join(f'<tr><td>2</td><td>release</td><td><a href="{name}">exhibit</a></td>'
                           '<td>EX-99.1</td></tr>' for name in self.exhibits)
            return ('<table summary="Document Format Files">'+rows+'</table>').encode()
        if not url.endswith('/doc.htm'):
            return b'<html><p>We expect revenue of $120 million in the next quarter based on the current business outlook.</p></html>'
        return b'<html><body>Item 2.02 see exhibit</body></html>'


def collect_text(db, root, source, *, now=NOW, state_dir=None, session=DAY):
    from contextlib import contextmanager
    from investment_assistant.data import registry
    from investment_assistant.data.adapter_types import AdapterDeps
    @contextmanager
    def transaction():
        with db.transaction():
            yield db
    deps = AdapterDeps(transaction=transaction, filing_source=source, sec_text_dir=root,
                       sec_monitor_state_dir=state_dir)
    tally = registry.run_adapter(registry.get('sec_text'), ['NVDA'], scope='manual', trigger='manual_family',
        run_id=None, session=session, now=now, deps=deps, features={'backup_sources': True})
    return tally.row(trigger='manual_family', run_id=None, session=session, started_at=now, finished_at=now)['detail']['targets'][0]


def index_text(db, root, meta, *, exists=True):
    path = root/'NVDA'/meta['form']/(meta['accession_number']+'-'+meta['primary_document'])
    if exists:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'<html><body>Item 2.02 see exhibit</body></html>')
    db.execute('INSERT INTO filings(ticker,form,filing_date,accession_number,primary_document,file_path,accepted_at) '
        'VALUES (%s,%s,%s,%s,%s,%s,%s)', ('NVDA', meta['form'], DAY, meta['accession_number'],
        meta['primary_document'], str(path), meta['accepted_at']))
    return path


def test_sec_text_filters_post_cutoff_before_selection_download_and_budget(db, tmp_path):
    import json
    frozen = NOW.replace(hour=12)
    future, eligible = filing_meta(2, accepted=frozen+timedelta(seconds=1)), filing_meta(1, accepted=frozen)
    source = FilingSource([future, eligible])
    result = collect_text(db, tmp_path, source, now=frozen)
    assert result['ok'] and result['rows'] == 1
    assert source.calls == [eligible['edgar_url']]
    assert list(repo.indexed_filing_paths(db, 'NVDA')) == [eligible['accession_number']]
    rows = repo.read_rows(db, 'US:stock:NVDA', 'filings', as_of=frozen)
    assert len(rows) == 1 and rows[0]['disclosed_at'] == frozen
    budget = json.loads(next((tmp_path/'.budgets').glob('*.json')).read_text())
    assert len(budget['attempted']) == 1


def test_sec_text_future_only_does_not_consume_budget_or_write(db, tmp_path):
    source = FilingSource([filing_meta(accepted=NOW+timedelta(seconds=1))])
    result = collect_text(db, tmp_path, source)
    assert result['rows'] == 0 and source.calls == []
    assert repo.indexed_filing_paths(db, 'NVDA') == {}
    assert repo.read_rows(db, 'US:stock:NVDA', 'filings') == []
    assert not list((tmp_path/'.budgets').glob('*.json'))


def test_sec_text_unknown_acceptance_remains_unknown_not_midnight(db, tmp_path):
    source = FilingSource([filing_meta(accepted=None)])
    assert collect_text(db, tmp_path, source)['ok']
    row = repo.read_rows(db, 'US:stock:NVDA', 'filings')[0]
    assert row['disclosed_at'] is None and row['first_seen_at'] == NOW
    assert row['limitations']


def test_sec_text_local_index_without_observation_reports_actual_insert_and_replay(db, tmp_path):
    meta = filing_meta()
    path = index_text(db, tmp_path, meta)
    source = FilingSource([meta])
    first, replay = collect_text(db, tmp_path, source), collect_text(db, tmp_path, source)
    assert (first['rows'], replay['rows'], source.calls) == (1, 0, [])
    # A real local economic change is a new observation even without any download.
    path.write_bytes(b'<html><body><p>We expect revenue of $120 million in the next quarter based on the current business outlook.</p></body></html>')
    assert collect_text(db, tmp_path, source)['rows'] == 1
    assert len(repo.read_history(db, 'US:stock:NVDA', 'filings')) == 2


def test_sec_text_restores_missing_owned_index_path_and_reader(db, tmp_path, monkeypatch):
    from pathlib import Path
    meta = filing_meta()
    # The indexed legacy directory differs from the adapter's default root/form layout.
    old_path = index_text(db, tmp_path, meta, exists=False)
    path = tmp_path/'NVDA'/'legacy'/old_path.name
    db.execute('UPDATE filings SET file_path=%s WHERE accession_number=%s', (str(path), meta['accession_number']))
    source = FilingSource([meta])
    result = collect_text(db, tmp_path, source)
    assert result['ok'] and result['rows'] == 1
    indexed = repo.indexed_filing_paths(db, 'NVDA')[meta['accession_number']]
    assert indexed == str(path) and Path(indexed).is_file()
    assert repo.read_rows(db, 'US:stock:NVDA', 'filings')[0]['path'] == indexed


def test_sec_text_reuses_monitor_exhibit_and_does_not_publish_poorer_successor(db, tmp_path, monkeypatch):
    """The monitor's archived exhibit is reused (nothing downloaded but the index pages); when its metadata later
    disappears, the rerun that only sees the primary document does not publish a poorer analysis over it (R28: the
    baseline used to come from the retired ``_local_filing_rows``; it is the adapter's own first result now)."""
    import json
    from investment_assistant.data import research_rows
    root, state_dir = tmp_path/'library', tmp_path/'state'
    meta = filing_meta(form='8-K')
    primary = index_text(db, root, meta)
    exhibit = primary.with_name(meta['accession_number']+'-ex99.htm')
    source = FilingSource([meta], exhibits=('ex99.htm',))
    exhibit.write_bytes(source.get(meta['edgar_url'].replace('doc.htm', 'ex99.htm')))
    archived = {**meta, 'path': str(primary), 'attachments': [str(exhibit)], 'document_urls': {
        str(primary): meta['edgar_url'], str(exhibit): meta['edgar_url'].replace('doc.htm', 'ex99.htm')}}
    state_dir.mkdir()
    (state_dir/'state.json').write_text(json.dumps({'version': 1, 'files': {
        'NVDA:'+meta['accession_number']: {'meta': archived, 'status': 'ready'}}}))
    monkeypatch.setattr(research_rows, 'RAW_ROOT', tmp_path/'raw')
    source.calls.clear()
    result = collect_text(db, root, source, state_dir=state_dir)
    before = repo.read_rows(db, 'US:stock:NVDA', 'filings')[0]
    assert result['ok'] and result['rows'] == 1
    assert before['analysis']['guidance'][0]['url'].endswith('/ex99.htm')
    assert len(before['analysis']['sources']) == 2
    assert all(url.endswith('-index.html') for url in source.calls)
    (state_dir/'state.json').write_text(json.dumps({'version': 1, 'files': {}}))     # the exhibit's provenance is gone
    again = collect_text(db, root, FilingSource([meta]), state_dir=state_dir)
    after = repo.read_rows(db, 'US:stock:NVDA', 'filings')[0]
    assert again['ok']
    assert after['analysis'] == before['analysis'] and len(after['analysis']['sources']) == 2
    assert len(repo.read_history(db, 'US:stock:NVDA', 'filings')) == 1


def test_sec_text_new_exhibits_share_budget_and_keep_provenance(db, tmp_path):
    import json
    meta = filing_meta(form='8-K')
    source = FilingSource([meta], exhibits=('ex99.htm', 'ex99b.htm'))
    result = collect_text(db, tmp_path, source)
    assert result['ok'] and result['rows'] == 1
    row = repo.read_rows(db, 'US:stock:NVDA', 'filings')[0]
    assert len(row['analysis']['sources']) == 3
    assert row['analysis']['guidance'][0]['url'].endswith('/ex99.htm')
    budget = json.loads(next((tmp_path/'.budgets').glob('*.json')).read_text())
    assert len(budget['attempted']) == 3
    assert len([u for u in source.calls if not u.endswith('-index.html')]) == 3


def test_sec_text_budget_exhaustion_does_not_publish_incomplete_exhibit(db, tmp_path):
    import json
    meta = filing_meta(form='8-K')
    source = FilingSource([meta], exhibits=('ex99.htm',))
    budget = tmp_path/'.budgets'/f'NVDA-{DAY}.json'
    budget.parent.mkdir()
    # Existing v1 accession-only reservations must not be reset by the new format.
    budget.write_text(json.dumps({'attempted': ['0000000001-26-000010', '0000000001-26-000011'], 'timeout': False}))
    result = collect_text(db, tmp_path, source)
    assert not result['ok'] and result['rows'] == 0
    assert len(json.loads(budget.read_text())['attempted']) <= 3
    assert len([u for u in source.calls if not u.endswith('-index.html')]) <= 1
    assert repo.read_rows(db, 'US:stock:NVDA', 'filings') == []


def test_sec_text_refuses_unowned_missing_index_path(db, tmp_path):
    meta = filing_meta()
    path = index_text(db, tmp_path/'foreign', meta, exists=False)
    source = FilingSource([meta])
    result = collect_text(db, tmp_path/'owned', source)
    assert not result['ok'] and source.calls == [] and not path.exists()


@pytest.mark.parametrize('timeout', [False, True])
def test_sec_text_unavailable_exhibit_preserves_rich_observation_and_reservation(db, tmp_path, timeout):
    import json
    from investment_assistant.data.fetch_policy import FetchError
    meta = filing_meta(form='8-K')
    primary = index_text(db, tmp_path, meta)
    exhibit = primary.with_name(meta['accession_number']+'-ex99.htm')
    class Source(FilingSource):
        failing = True
        def get(self, url):
            if url.endswith('/ex99.htm') and self.failing:
                self.calls.append(url)
                if timeout:
                    raise TimeoutError('exhibit timeout')
                raise FetchError('exhibit unavailable', kind='http_4xx', status=404)
            return super().get(url)
    source = Source([meta], exhibits=('ex99.htm',))
    source.failing = False
    exhibit.write_bytes(source.get(meta['edgar_url'].replace('doc.htm', 'ex99.htm')))
    row = {**meta, 'path': str(primary), 'attachments': [str(exhibit)], 'document_urls': {
        str(exhibit): meta['edgar_url'].replace('doc.htm', 'ex99.htm')}, 'downloaded': False}
    repo.store_filing_texts(db, [row], target='NVDA', now=NOW)
    old = repo.read_rows(db, 'US:stock:NVDA', 'filings')[0]
    exhibit.unlink()
    source.calls.clear()
    source.failing = True
    for _ in range(2):
        failed = collect_text(db, tmp_path, source)
        assert not failed['ok'] and failed['rows'] == 0
    assert len([u for u in source.calls if u.endswith('/ex99.htm')]) == 1
    assert repo.read_rows(db, 'US:stock:NVDA', 'filings')[0] == old
    budget = json.loads((tmp_path/'.budgets'/f'NVDA-{DAY}.json').read_text())
    assert len(budget['attempted']) == 1 and budget['timeout'] is timeout
    source.failing = False
    # A fresh session may restore the exhibit; existing observation is still deduplicated.
    resumed = collect_text(db, tmp_path, source, session=DAY+timedelta(days=3), now=NOW+timedelta(days=3))
    assert resumed['ok'] and resumed['rows'] == 0 and exhibit.is_file()
    assert len(repo.read_history(db, 'US:stock:NVDA', 'filings')) == 1


def test_sec_text_large_exhibit_set_resumes_without_exceeding_daily_document_cap(db, tmp_path):
    import json
    meta = filing_meta(form='8-K')
    source = FilingSource([meta], exhibits=('ex99.htm', 'ex99b.htm', 'ex99c.htm'))
    failed = collect_text(db, tmp_path, source)
    assert not failed['ok'] and repo.read_rows(db, 'US:stock:NVDA', 'filings') == []
    assert len([u for u in source.calls if not u.endswith('-index.html')]) == 3
    resumed = collect_text(db, tmp_path, source, session=DAY+timedelta(days=3), now=NOW+timedelta(days=3))
    assert resumed['ok'] and resumed['rows'] == 1
    assert len(repo.read_rows(db, 'US:stock:NVDA', 'filings')[0]['analysis']['sources']) == 4
    budgets = [json.loads(p.read_text()) for p in (tmp_path/'.budgets').glob('*.json')]
    assert sorted(len(b['attempted']) for b in budgets) == [1, 3]


@pytest.mark.parametrize('bad', ['unowned', 'symlink', 'url', 'corrupt_state'])
def test_sec_text_rejects_unverified_archived_metadata_before_document_io(db, tmp_path, bad):
    import json
    meta = filing_meta(form='8-K')
    root, state_dir = tmp_path/'library', tmp_path/'state'
    primary = index_text(db, root, meta)
    exhibit = primary.with_name(meta['accession_number']+'-ex99.htm')
    url = meta['edgar_url'].replace('doc.htm', 'ex99.htm')
    if bad == 'unowned':
        exhibit = tmp_path/(meta['accession_number']+'-ex99.htm')
    if bad == 'symlink':
        foreign = tmp_path/'foreign.htm'
        foreign.write_text('foreign')
        exhibit.symlink_to(foreign)
    else:
        exhibit.write_text('untrusted')
    if bad == 'url':
        url = 'https://evil.invalid/ex99.htm'
    state_dir.mkdir()
    archived = {**meta, 'path': str(primary), 'attachments': [str(exhibit)],
                'document_urls': {str(exhibit): url}}
    (state_dir/'state.json').write_text('not json' if bad == 'corrupt_state' else json.dumps({
        'version': 1, 'files': {'NVDA:'+meta['accession_number']: {'meta': archived}}}))
    source = FilingSource([meta], exhibits=('ex99.htm',))
    result = collect_text(db, root, source, state_dir=state_dir)
    assert not result['ok'] and source.calls == []
    assert repo.read_rows(db, 'US:stock:NVDA', 'filings') == []
