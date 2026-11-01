"""Offline acceptance tests: expected arithmetic comes from fixture source bytes."""
import copy
import hashlib
import importlib.util
import json
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/verify_cross_market.py'


@lru_cache
def verifier():
    spec = importlib.util.spec_from_file_location('verify_cross_market', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fixture(tmp_path):
    root = tmp_path / 'raw'
    root.mkdir()
    records = [
        {'val': 100, 'start': '2025-01-01', 'end': '2025-03-31'},
        {'val': 240, 'start': '2025-01-01', 'end': '2025-06-30'},
        {'val': 390, 'start': '2025-01-01', 'end': '2025-09-30'},
        {'val': 560, 'start': '2025-01-01', 'end': '2025-12-31'},
    ]
    for raw in records:
        raw.update(accn='0000000001-25-000001', filed='2026-01-15', form='10-K')
    payload = {'cik': 1, 'facts': {'us-gaap': {'Revenues': {'units': {'USD': records}}}}}
    raw = json.dumps(payload).encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = root / (digest + '.raw')
    path.write_bytes(raw)
    rows = []
    for index, point in enumerate(records):
        rows.append(dict(id=index+10, security_id='US:stock:MU', dataset='financials', metric='revenue',
            value=point['val'], unit='USD', basis='us-gaap:consolidated', start=point['start'], end=point['end'],
            accession=point['accn'], filed=point['filed'], form=point['form'], concept='Revenues',
            source_url='https://www.sec.gov/Archives/edgar/data/1/000000000125000001/0000000001-25-000001-index.html',
            source_hash=digest, snapshot_path=str(path), source_locator=f'/facts/us-gaap/Revenues/units/USD/{index}'))
    quarters = []
    for i, (start, end, amount, method, ids) in enumerate([
        ('2025-01-01', '2025-03-31', 100, 'reported', [10]),
        ('2025-04-01', '2025-06-30', 140, 'ytd_difference', [11, 10]),
        ('2025-07-01', '2025-09-30', 150, 'ytd_difference', [12, 11]),
        ('2025-10-01', '2025-12-31', 170, 'annual_minus_nine_months', [13, 12]),
    ]):
        sources = [{k: rows[n-10][k] for k in ('source_url','source_hash','source_locator','concept','accession','filed','start','end')} for n in ids]
        metric = {'value': amount, 'unit': 'USD', 'basis': 'us-gaap:consolidated', 'method': method,
                  'input_ids': ids, 'sources': sources, 'missing_reason': None}
        quarters.append({'start': start, 'end': end, 'metrics': {'revenue': metric}, 'ttm': {}})
    quarters[-1]['ttm']['revenue'] = {'value': 560, 'unit':'USD', 'basis':'us-gaap:consolidated',
        'method':'sum_four_contiguous_quarters', 'input_ids':[10,11,12,13],
        'sources':[s for q in quarters for s in q['metrics']['revenue']['sources']], 'missing_reason':None}
    view = {'security_id':'US:stock:MU', 'ticker':'MU', 'financials':quarters,
        'market':{'bars':[], 'benchmarks':{'SPY':[], 'QQQ':[]}, 'quote':{}, 'performance':{'windows':{}}},
        'valuation':{name:{'value':None,'missing_reason':'unverified'} for name in ('pe_ttm','ps_ttm','fcf_yield')},
        'guidance':{'comparisons':[]}, 'pit':{'strict':False}}
    bundle = {'active_tickers':['MU'], 'companies':{'MU':{'view':view,'history':{'financials':rows,'market':[],'filings':[],'guidance':[]},'states':{},
                                                       'price_bars':{'MU':[], 'SPY':[], 'QQQ':[]}}}}
    return bundle, root


def verify(bundle, root):
    return verifier().verify_bundle(bundle, raw_root=root, expected_tickers=('MU',), required_provenance=('MU',))


def stored_market(company):
    from investment_assistant.analysis.fundamentals import market_from_bars

    rows = {'MU': [{'id': 901, 'price_date': date(2026, 10, 16),
                    'open': 9, 'high': 11, 'low': 8, 'close': 10, 'adj_close': 5, 'volume': 100}],
            'SPY': [{'id': 902, 'price_date': date(2026, 10, 16),
                     'open': 500, 'high': 510, 'low': 490, 'close': 505, 'adj_close': 500, 'volume': 200}],
            'QQQ': []}
    company['price_bars'] = copy.deepcopy(rows)
    company['view']['market'] = market_from_bars(rows['MU'], {k: rows[k] for k in ('SPY', 'QQQ')})


def test_real_price_bars_projection_passes_independent_readback(tmp_path):
    bundle, root = fixture(tmp_path)
    stored_market(bundle['companies']['MU'])
    assert verify(bundle, root)['passed']


@pytest.mark.parametrize('field,value', [
    ('id', 902), ('date', '2026-10-15'), ('open', 8), ('high', 12), ('low', 7),
    ('close', 5), ('adjusted_close', 10), ('volume', 101), ('source_hash', 'invented'),
])
def test_price_projection_rejects_wrong_ticker_date_or_value(tmp_path, field, value):
    bundle, root = fixture(tmp_path)
    company = bundle['companies']['MU']
    stored_market(company)
    company['view']['market']['bars'][0][field] = value
    with pytest.raises(verifier().VerificationError, match='market_'):
        verify(bundle, root)


@pytest.mark.parametrize('mutation', ['benchmark_ticker', 'benchmark_value', 'missing_bars', 'quote_price', 'quote_date'])
def test_price_projection_checks_benchmarks_coverage_and_daily_quote(tmp_path, mutation):
    bundle, root = fixture(tmp_path)
    company = bundle['companies']['MU']
    stored_market(company)
    market = company['view']['market']
    if mutation == 'benchmark_ticker': market['benchmarks']['QQQ'] = market['benchmarks'].pop('SPY')
    elif mutation == 'benchmark_value': market['benchmarks']['SPY'][0]['close'] = 1
    elif mutation == 'missing_bars': market['bars'] = []
    elif mutation == 'quote_price': market['quote']['price'] = 5
    elif mutation == 'quote_date': market['quote']['date'] = '2026-10-15'
    with pytest.raises(verifier().VerificationError, match='market_'):
        verify(bundle, root)


def test_price_and_financial_ids_have_separate_ownership(tmp_path):
    bundle, root = fixture(tmp_path)
    company = bundle['companies']['MU']
    stored_market(company)
    company['price_bars']['MU'][0]['id'] = 10  # valid collision with an observation ID
    company['view']['market']['bars'][0]['id'] = 10
    assert verify(bundle, root)['passed']
    company['view']['market']['bars'][0]['close'] = 100  # the financial row's value
    with pytest.raises(verifier().VerificationError, match='market_readback'):
        verify(bundle, root)


def test_exact_source_values_and_independent_quarter_arithmetic(tmp_path):
    bundle, root = fixture(tmp_path)
    result = verify(bundle, root)
    assert result['passed']
    assert result['companies']['MU']['quarter_coverage'] == {'target':12,'actual':4,'complete':False}
    assert result['companies']['MU']['arithmetic_checks'] >= 5
    examples = {e['method']:e for e in result['companies']['MU']['quarter_arithmetic_examples']}
    assert examples['ytd_difference']['value'] == 150
    assert [r['value'] for r in examples['ytd_difference']['inputs']] == [390,240]
    assert examples['annual_minus_nine_months']['value'] == 170
    assert result['snapshot_count'] == 1


@pytest.mark.parametrize('mutation,code', [
    ('hash','snapshot_hash'), ('value','source_value'), ('security','input_ownership'),
    ('calculation','quarter_arithmetic'), ('provisional','persisted_id'),
    ('sources','metric_source'), ('identity','active_identities'), ('outside','snapshot_root'),
    ('ttm','ttm_arithmetic'), ('invented','missing_reason'), ('valuation','unverified_valuation'),
])
def test_bad_evidence_fails_without_weakening_live_requirements(tmp_path, mutation, code):
    bundle, root = fixture(tmp_path)
    company = bundle['companies']['MU']
    row = company['history']['financials'][0]
    metric = company['view']['financials'][1]['metrics']['revenue']
    if mutation == 'hash': Path(row['snapshot_path']).write_bytes(b'changed')
    elif mutation == 'value': row['value'] = 999
    elif mutation == 'security': row['security_id'] = 'US:stock:NVDA'
    elif mutation == 'calculation': metric['value'] = 141
    elif mutation == 'provisional': row['id'] = 'f'*64
    elif mutation == 'sources': metric['sources'][0]['source_locator'] = '/wrong'
    elif mutation == 'identity': bundle['active_tickers'].append('NVDA')
    elif mutation == 'outside': row['snapshot_path'] = str(tmp_path/'outside.raw')
    elif mutation == 'ttm': company['view']['financials'][-1]['ttm']['revenue']['value'] = 561
    elif mutation == 'invented': metric.update(value=None, missing_reason=None)
    elif mutation == 'valuation': company['view']['valuation']['pe_ttm']['value'] = 20
    with pytest.raises(verifier().VerificationError) as error: verify(bundle, root)
    assert code in str(error.value)


def test_conflict_group_uses_candidate_locators_and_parent_database_id(tmp_path):
    bundle, root = fixture(tmp_path)
    company = bundle['companies']['MU']
    parent = company['history']['financials'][0]
    candidate = copy.deepcopy(parent)
    candidate.pop('id'); candidate.pop('security_id'); candidate.pop('dataset')
    parent.update(value=None, source_locator=None, conflicts=[candidate])
    assert verify(bundle, root)['passed']
    candidate['value'] = 200
    with pytest.raises(verifier().VerificationError, match='source_value'): verify(bundle, root)


def test_conflicted_unknown_value_requires_reason_and_keeps_candidate_evidence(tmp_path):
    bundle, root = fixture(tmp_path)
    metric = bundle['companies']['MU']['view']['financials'][0]['metrics']['revenue']
    metric.update(value=None, missing_reason='Conflicting facts within the same latest filing date')
    # Missing values with a valid reason are coverage limits, not fabricated completeness.
    bundle['companies']['MU']['view']['financials'][-1]['ttm']['revenue'].update(value=None,missing_reason='Missing quarter input')
    assert verify(bundle, root)['passed']


def test_unknown_guidance_basis_must_never_compare(tmp_path):
    bundle, root = fixture(tmp_path)
    bundle['companies']['MU']['view']['guidance']['comparisons'] = [
        {'status':'in_range','basis':'non-GAAP:unknown','actual':100,'input_ids':[10]}]
    with pytest.raises(verifier().VerificationError, match='guidance_basis'): verify(bundle, root)


def add_market(bundle, root):
    chart = {'chart':{'result':[{'meta':{'symbol':'MU','exchangeTimezoneName':'America/New_York','currency':'USD'},
        'timestamp':[1735833600], 'indicators':{'quote':[{'open':[9],'high':[11],'low':[8],'close':[10],'volume':[100]}], 'adjclose':[{'adjclose':[5]}]}}]}}
    raw = json.dumps(chart).encode(); digest = hashlib.sha256(raw).hexdigest()
    path = root/(digest+'.raw'); path.write_bytes(raw)
    row = {'id':50,'security_id':'US:stock:MU','dataset':'market','kind':'bar','group':'stock','date':'2025-01-02',
        'currency':'USD','open':9,'high':11,'low':8,'close':10,'volume':100,'adjusted_close':5,
        'snapshot_path':str(path),'source_hash':digest,'source_url':'https://query1.finance.yahoo.com/v8/finance/chart/MU'}
    bundle['companies']['MU']['history']['market'].append(row)
    return row


def test_raw_and_adjusted_prices_are_checked_against_distinct_source_arrays(tmp_path):
    bundle, root = fixture(tmp_path); add_market(bundle, root)
    assert verify(bundle, root)['snapshot_count'] == 2
    bundle['companies']['MU']['history']['market'][0]['close'] = 5
    with pytest.raises(verifier().VerificationError, match='raw_price'): verify(bundle, root)


@pytest.mark.parametrize('collision', [False, True])
def test_old_market_history_cannot_authorize_price_bars_output(tmp_path, collision):
    bundle, root = fixture(tmp_path)
    company = bundle['companies']['MU']
    stored_market(company)
    old = add_market(bundle, root)
    if collision:
        old['id'] = 901
    assert verify(bundle, root)['passed']  # historical snapshots are still independently checked
    company['view']['market']['bars'] = [copy.deepcopy(old)]
    with pytest.raises(verifier().VerificationError, match='market_'):
        verify(bundle, root)


def test_live_entrypoint_has_no_collection_or_migration_dependencies():
    text = SCRIPT.read_text()
    assert 'default_transaction_read_only=on' in text
    assert 'load_dotenv' in text
    assert '.sync(' not in text and 'migrate.run' not in text


def test_conflict_group_cannot_be_silently_resolved_to_one_candidate(tmp_path):
    bundle, root = fixture(tmp_path)
    rows = bundle['companies']['MU']['history']['financials']
    parent = rows[0]
    first = {k:v for k,v in parent.items() if k not in {'id','security_id','dataset'}}
    document = json.loads(Path(parent['snapshot_path']).read_bytes())
    points = document['facts']['us-gaap']['Revenues']['units']['USD']
    points.append({**points[0],'val':101})
    raw = json.dumps(document).encode(); digest = hashlib.sha256(raw).hexdigest()
    path = root/(digest+'.raw'); path.write_bytes(raw)
    for row in rows:
        row.update(source_hash=digest,snapshot_path=str(path))
    for q in bundle['companies']['MU']['view']['financials']:
        for scope in ('metrics','ttm'):
            for metric in q[scope].values():
                for source in metric['sources']: source['source_hash'] = digest
    first.update(source_hash=digest,snapshot_path=str(path))
    second = {**first,'value':101,'source_locator':'/facts/us-gaap/Revenues/units/USD/4'}
    parent.update(value=None,source_locator=None,conflicts=[first,second])
    with pytest.raises(verifier().VerificationError,match='conflicted_input'): verify(bundle,root)
    # A real unavailable calculation still refers to its persisted parent and both pointers.
    for q in bundle['companies']['MU']['view']['financials']:
        for scope in ('metrics','ttm'):
            for metric in q[scope].values():
                if 10 in metric['input_ids']:
                    metric.update(value=None,missing_reason='Conflicting facts')
                    metric['sources'].append({k:second.get(k) for k in verifier().SOURCE_KEYS})
    assert verify(bundle,root)['passed']


def test_live_required_cik_is_checked_against_exact_archived_companyfacts(tmp_path):
    bundle, root = fixture(tmp_path)
    with pytest.raises(verifier().VerificationError,match='company_cik'):
        verifier().verify_bundle(bundle,raw_root=root,expected_tickers=('MU',),required_provenance=('MU',),expected_ciks={'MU':723125})


@pytest.mark.parametrize('cutoff,session', [
    (datetime(2026, 10, 19, 20, 59, tzinfo=UTC), date(2026, 10, 16)),
    (datetime(2026, 10, 19, 21, 1, tzinfo=UTC), date(2026, 10, 19)),
])
def test_read_only_reader_uses_one_cutoff_for_get_history_and_price_revisions(monkeypatch, cutoff, session):
    from contextlib import contextmanager
    import psycopg
    import requests
    from investment_assistant import db
    from investment_assistant.data import repo as repository
    calls = []
    clock_calls = []
    class Clock(datetime):
        @classmethod
        def now(cls, tz):
            clock_calls.append(tz)
            return cutoff
    monkeypatch.setattr(verifier(), 'datetime', Clock)
    class Connection:
        def execute(self, sql): calls.append(('sql',sql))
    @contextmanager
    def connection(url, **kwargs):
        calls.append(('connect',kwargs))
        yield Connection()
    class Response:
        status_code = 200
        def json(self): return {'security_id':'US:stock:MU', 'pit':{'as_of':cutoff.isoformat()}}
    def get(url, **kwargs):
        calls.append(('get',url,kwargs)); return Response()
    monkeypatch.setenv('INVESTMENT_ASSISTANT_DATABASE_URL','postgresql://offline.invalid')
    monkeypatch.setattr(psycopg,'connect',connection)
    monkeypatch.setattr(requests,'get',get)
    monkeypatch.setattr(db,'list_watchlist_items',lambda conn:[{'ticker':'MU','status':'active'},{'ticker':'688008.SH','status':'active'}])
    def history(conn, sid, dataset, *, as_of=None):
        calls.append(('history', sid, dataset, as_of))
        return []
    def bars(conn, ticker, end_date, *, cutoff_at, limit):
        calls.append(('bars', ticker, end_date, cutoff_at, limit))
        return [{'id': 901, 'price_date': session, 'revision': 2, 'close': 10}]
    monkeypatch.setattr(repository,'read_history',history)
    monkeypatch.setattr(repository,'read_bars',bars)
    monkeypatch.setattr(repository,'states',lambda *args:{})
    result = verifier().read_bundle('http://127.0.0.1:8787')
    assert result['active_tickers'] == ['MU']
    assert calls[0] == ('connect',{'options':'-c default_transaction_read_only=on'})
    assert 'READ ONLY' in calls[1][1]
    assert len([c for c in calls if c[0] == 'get']) == 8
    assert all(c[2]['allow_redirects'] is False for c in calls if c[0] == 'get')
    assert clock_calls == [UTC]
    assert result['as_of'] == cutoff.isoformat()
    assert all(c[2]['params'] == {'as_of': cutoff.isoformat()} for c in calls if c[0] == 'get')
    assert [c[1:] for c in calls if c[0] == 'bars'] == [
        (symbol, session, cutoff, 2000) for ticker in verifier().EXPECTED_TICKERS for symbol in (ticker, 'SPY', 'QQQ')]
    assert all(c[3] == cutoff for c in calls if c[0] == 'history')
    assert result['companies']['MU']['price_bars'] == {
        symbol: [{'id': 901, 'price_date': session, 'revision': 2, 'close': 10}] for symbol in ('MU', 'SPY', 'QQQ')}


def test_read_bundle_rejects_http_ignoring_the_requested_cutoff(monkeypatch):
    from contextlib import nullcontext
    import psycopg
    import requests
    from investment_assistant import db

    class Connection:
        def execute(self, sql): pass
    class Response:
        status_code = 200
        def json(self): return {'pit': {'as_of': None}}
    monkeypatch.setenv('INVESTMENT_ASSISTANT_DATABASE_URL', 'postgresql://offline.invalid')
    monkeypatch.setattr(psycopg, 'connect', lambda *a, **kw: nullcontext(Connection()))
    monkeypatch.setattr(db, 'list_watchlist_items', lambda conn: [])
    monkeypatch.setattr(requests, 'get', lambda *a, **kw: Response())
    with pytest.raises(verifier().VerificationError, match='view_cutoff'):
        verifier().read_bundle('http://127.0.0.1:8787')


def test_large_integer_source_values_are_exact_not_relative_tolerance():
    assert not verifier().equal(96_221_000_000,96_221_000_001)
    assert verifier().equal(96_221_000_000,96_221_000_000)
