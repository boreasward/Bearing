"""Read-only deployed-data acceptance. Never collects, migrates, saves cards or notifies.

Run in the primary checkout after collection. Only --output writes a sanitized JSON
report; a failed assertion exits nonzero. HTTP GETs and a read-only DB transaction
are the evidence boundaries, not browser inspection or user acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
EXPECTED_TICKERS = ('CRDO', 'GOOG', 'MSFT', 'MU', 'NVDA', 'RKLB', 'SNDK', 'TSLA')
EXPECTED_CIKS = {'MU':723125, 'CRDO':1807794, 'NVDA':1045810}
SOURCE_KEYS = ('source_url', 'source_hash', 'source_locator', 'concept', 'accession', 'filed', 'start', 'end')
VERIFIED_BASES = {'us-gaap:consolidated', 'us-gaap:parent', 'us-gaap:common'}


class VerificationError(ValueError):
    """Only fixed, sanitized check names and validated symbols may be emitted."""


def require(condition, code):
    if not condition:
        raise VerificationError(code)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def equal(actual, expected):
    if isinstance(actual,int) and isinstance(expected,int):
        return actual == expected and not isinstance(actual,bool) and not isinstance(expected,bool)
    return actual is None and expected is None or (number(actual) and number(expected) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-8))


def pointer(document, locator):
    require(isinstance(locator, str) and locator.startswith('/'), 'source_locator')
    value = document
    for key in locator[1:].split('/'):
        key = key.replace('~1', '/').replace('~0', '~')
        try:
            value = value[int(key)] if isinstance(value, list) else value[key]
        except (KeyError, IndexError, TypeError, ValueError):
            raise VerificationError('source_locator') from None
    return value


class Snapshots:
    def __init__(self, root):
        self.root, self.checked, self.documents = Path(root).resolve(), set(), {}

    def read(self, row):
        digest, raw_path = row.get('source_hash'), row.get('snapshot_path')
        require(isinstance(digest, str) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest), 'snapshot_hash')
        require(isinstance(raw_path, str), 'snapshot_path')
        path = Path(raw_path).resolve()
        require(path.is_relative_to(self.root), 'snapshot_root')
        require(path.name == digest + '.raw' and path.is_file(), 'snapshot_path')
        if path not in self.checked:
            require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, 'snapshot_hash')
            self.checked.add(path)
        return path

    def document(self, row):
        path = self.read(row)
        if path not in self.documents:
            try:
                self.documents[path] = json.loads(path.read_bytes())
            except (ValueError, UnicodeError):
                raise VerificationError('snapshot_json') from None
        return self.documents[path]

    def nested(self, value):
        if isinstance(value, dict):
            if value.get('snapshot_path'):
                self.read(value)
            for nested in value.values():
                self.nested(nested)
        elif isinstance(value, list):
            for nested in value:
                self.nested(nested)


def verify_fact(fact, snapshots):
    document = snapshots.document(fact)
    point = pointer(document, fact.get('source_locator'))
    require(equal(fact.get('value'), point.get('val')), 'source_value')
    for saved, raw in [('start','start'), ('end','end'), ('accession','accn'), ('filed','filed'), ('form','form')]:
        require(fact.get(saved) == point.get(raw), 'source_period_identity')
    expected_basis = {'net_income':'us-gaap:parent','common_net_income':'us-gaap:common'}.get(fact['metric'],'us-gaap:consolidated')
    if fact['metric'] in {'eps_diluted','weighted_shares','shares'}:
        expected_basis = 'us-gaap:reported_share_basis_unverified'
    require(fact.get('basis') == expected_basis, 'source_basis')
    tokens = fact['source_locator'].split('/')
    require(len(tokens) == 7 and tokens[1:3] == ['facts','us-gaap'] and tokens[4] == 'units', 'source_locator')
    require(tokens[3] == fact.get('concept') and tokens[5].replace('~1','/').replace('~0','~') == fact.get('unit'), 'source_concept_unit')
    from investment_assistant.data.fundamentals import CONCEPTS
    require(fact.get('concept') in CONCEPTS.get(fact.get('metric'), ()), 'source_metric')
    url = urlsplit(fact.get('source_url') or '')
    require(url.scheme == 'https' and url.hostname in {'www.sec.gov','sec.gov'} and
            url.path.startswith(f'/Archives/edgar/data/{int(document["cik"])}/{fact["accession"].replace("-", "")}/'), 'source_attribution')


def verify_market(row, ticker, snapshots):
    document = snapshots.document(row)
    try:
        result = document['chart']['result'][0]
        meta = result['meta']
        symbol = ticker if row.get('group', 'stock') == 'stock' else row['group']
        require(meta['symbol'].upper() == symbol.replace('.', '-'), 'market_identity')
        require(row.get('currency') == meta.get('currency'), 'market_currency')
        if row['kind'] == 'quote':
            for saved, raw in [('price','regularMarketPrice'),('shares','sharesOutstanding'),('market_cap','marketCap')]:
                value = meta.get(raw)
                require(equal(row.get(saved), value if number(value) else None), 'quote_value')
            require(row.get('market_cap_verified') is False and row.get('share_class_basis') == 'unverified', 'quote_basis')
            return
        zone = ZoneInfo(meta['exchangeTimezoneName'])
        indices = [i for i, ts in enumerate(result['timestamp']) if datetime.fromtimestamp(ts, zone).date().isoformat() == row['date']]
        require(len(indices) == 1, 'market_date')
        index = indices[0]
        quote = result['indicators']['quote'][0]
        for key in ('open','high','low','close','volume'):
            values = quote.get(key) or []
            value = values[index] if index < len(values) else None
            require(equal(row.get(key), value if number(value) else None), 'raw_price')
        adjusted = result['indicators'].get('adjclose', [])
        values = adjusted[0].get('adjclose', []) if adjusted else []
        value = values[index] if index < len(values) else None
        require(equal(row.get('adjusted_close'), value if number(value) else None), 'adjusted_price')
    except (KeyError, IndexError, TypeError, ValueError) as error:
        if isinstance(error, VerificationError):
            raise
        raise VerificationError('market_source_structure') from None


def matched_sources(metric, rows):
    ids = metric.get('input_ids', [])
    require(all(isinstance(i, int) and not isinstance(i, bool) and i in rows for i in ids), 'input_ownership')
    candidates = []
    for identifier in ids:
        row = rows[identifier]
        for candidate in row.get('conflicts') or [row]:
            candidates.append({**candidate, 'id': identifier})
    chosen = []
    for source in metric.get('sources', []):
        matches = [r for r in candidates if all(source.get(k) == r.get(k) for k in SOURCE_KEYS)]
        require(bool(matches), 'metric_source')
        chosen.extend(matches)
    if metric.get('value') is not None:
        require(number(metric['value']) and ids and chosen, 'metric_source')
        for identifier in ids:
            conflicts = rows[identifier].get('conflicts',[])
            require(len({(r.get('value'),r.get('unit'),r.get('basis')) for r in conflicts}) <= 1, 'conflicted_input')
    else:
        require(bool(metric.get('missing_reason')), 'missing_reason')
    require(not ids or {r['id'] for r in chosen} == set(ids), 'metric_source')
    return chosen


def verify_quarter(name, metric, quarter, facts):
    """Hand arithmetic from resolved source observations, never the series function."""
    value, method = metric.get('value'), metric.get('method')
    if value is None:
        return 0
    if method not in {'reported','ytd_difference','annual_minus_nine_months'}:
        return 0
    facts = [r for r in facts if r.get('metric') == name and r.get('unit') == metric.get('unit') and r.get('basis') == metric.get('basis')]
    end, start = quarter['end'], quarter['start']
    if method == 'reported':
        matches = [r for r in facts if r['end'] == end and (not r.get('start') or r['start'] == start)]
        require(bool(matches) and all(equal(value, r['value']) for r in matches), 'quarter_arithmetic')
    else:
        from investment_assistant.data.fundamentals import NON_ADDITIVE
        require(name not in NON_ADDITIVE, 'quarter_arithmetic')
        current = [r for r in facts if r['end'] == end and r.get('start') and r['start'] < start]
        previous_end = (date.fromisoformat(start)-timedelta(days=1)).isoformat()
        pairs = [(a,b) for a in current for b in facts if b['end'] == previous_end and b.get('start') == a['start']]
        require(len(pairs) == 1, 'quarter_arithmetic')
        a,b = pairs[0]
        days = (date.fromisoformat(end)-date.fromisoformat(a['start'])).days+1
        require((method == 'annual_minus_nine_months') == (days >= 330), 'quarter_method')
        require(equal(value, a['value']-b['value']), 'quarter_arithmetic')
    return 1


def derived_checks(metrics):
    count = 0
    formulas = {'revenue_minus_cost':('revenue','cost_of_revenue',lambda a,b:a-b),
        'cfo_minus_cash_ppe':('cfo','cash_capex',lambda a,b:a-b),
        'noncurrent_plus_current_debt_including_finance_leases':('debt_with_leases_noncurrent','debt_with_leases_current',lambda a,b:a+b)}
    ratios = {'gross_margin':'gross_profit','operating_margin':'operating_income','fcf_margin':'fcf'}
    for name, metric in metrics.items():
        if metric.get('value') is None:
            continue
        method = metric.get('method')
        if method in formulas:
            left,right,op = formulas[method]
            a,b = metrics.get(left,{}),metrics.get(right,{})
            require(number(a.get('value')) and number(b.get('value')) and equal(metric['value'],op(a['value'],b['value'])), 'derived_arithmetic')
            count += 1
        elif name in ratios:
            a,b = metrics[ratios[name]],metrics['revenue']
            require(number(a.get('value')) and number(b.get('value')) and b['value'] > 0 and equal(metric['value'],a['value']/b['value']), 'derived_arithmetic')
            count += 1
    return count


def verify_company(ticker, company, snapshots):
    sid = 'US:stock:' + ticker
    view, history = company['view'], company['history']
    require(view.get('security_id') == sid and view.get('ticker') == ticker, 'view_identity')
    require(view.get('pit',{}).get('strict') is False, 'historical_scope')
    rows = {}
    for dataset, records in history.items():
        for row in records:
            require(isinstance(row.get('id'),int) and not isinstance(row['id'],bool) and row['id'] > 0, 'persisted_id')
            require(row.get('security_id') == sid and row.get('dataset') == dataset, 'input_ownership')
            require(row['id'] not in rows, 'duplicate_persisted_id')
            rows[row['id']] = row
            snapshots.nested(row)
            if dataset == 'financials':
                for candidate in row.get('conflicts') or [row]:
                    verify_fact(candidate, snapshots)
            if dataset == 'market':
                verify_market(row, ticker, snapshots)
    snapshots.nested(company.get('states',{}))
    arithmetic, proven, examples = 0, set(), {}
    financials = view.get('financials', [])
    require(bool(financials) and len(financials) <= 12, 'financial_coverage')
    for index, quarter in enumerate(financials):
        require(quarter['start'] <= quarter['end'], 'quarter_interval')
        for scope in ('metrics','ttm'):
            metrics = quarter.get(scope,{})
            for name, metric in metrics.items():
                facts = matched_sources(metric, rows)
                if metric.get('value') is not None:
                    proven.update(metric['input_ids'])
                if scope == 'metrics':
                    checked = verify_quarter(name,metric,quarter,facts)
                    arithmetic += checked
                    if checked and name == 'revenue':
                        examples[metric['method']] = {'metric':name,'start':quarter['start'],'end':quarter['end'],
                            'value':metric['value'],'unit':metric['unit'],'basis':metric['basis'],'method':metric['method'],
                            'inputs':[{k:r.get(k) for k in ('id','value',*SOURCE_KEYS)} for r in facts]}
                elif index >= 3 and metric.get('value') is not None and metric.get('method') == 'sum_four_contiguous_quarters':
                    window = financials[max(0,index-3):index+1]
                    require(len(window) == 4 and all((date.fromisoformat(b['start'])-date.fromisoformat(a['end'])).days == 1 for a,b in zip(window,window[1:])), 'ttm_arithmetic')
                    values = [q['metrics'].get(name,{}).get('value') for q in window]
                    require(all(number(v) for v in values) and equal(metric['value'],sum(values)), 'ttm_arithmetic')
                    arithmetic += 1
            arithmetic += derived_checks(metrics)
    for name, metric in view.get('valuation',{}).items():
        if view.get('market',{}).get('quote',{}).get('market_cap_verified') is not True:
            require(metric.get('value') is None and metric.get('missing_reason'), 'unverified_valuation')
    for comparison in view.get('guidance',{}).get('comparisons',[]):
        if comparison.get('basis') not in VERIFIED_BASES:
            require(comparison.get('status') == 'not_comparable' and comparison.get('actual') is None, 'guidance_basis')
        require(all(i in rows and rows[i]['dataset'] == 'financials' for i in comparison.get('input_ids',[])), 'guidance_ownership')
    # Compare the deployed bars with the actual persisted rows as well as source arrays.
    market = view.get('market',{})
    for group,bars in [('stock',market.get('bars',[])), *market.get('benchmarks',{}).items()]:
        for bar in bars:
            stored = rows.get(bar.get('id'),{})
            require(stored.get('dataset') == 'market' and stored.get('group') == group, 'market_ownership')
            require(all(bar.get(k) == stored.get(k) for k in ('date','close','adjusted_close','source_hash')), 'market_readback')
    return {'quarter_coverage':{'target':12,'actual':len(financials),'complete':len(financials) == 12},
        'arithmetic_checks':arithmetic,'quarter_arithmetic_examples':list(examples.values()),
        'proven_financial_input_ids':sorted(proven),
        'history_rows':{key:len(value) for key,value in history.items()},
        'collection_status':{key:value.get('status') for key,value in company.get('states',{}).items()},
        'latest_missing_metrics':[name for name,m in (financials[-1]['metrics'] if financials else {}).items() if m.get('value') is None]}


def verify_bundle(bundle, *, raw_root, expected_tickers=EXPECTED_TICKERS, required_provenance=('MU','CRDO','NVDA'), expected_ciks=None):
    require(sorted(bundle.get('active_tickers',[])) == sorted(expected_tickers), 'active_identities')
    require(set(bundle.get('companies',{})) == set(expected_tickers), 'company_identities')
    snapshots = Snapshots(raw_root)
    companies = {ticker:verify_company(ticker,bundle['companies'][ticker],snapshots) for ticker in expected_tickers}
    for ticker,cik in (expected_ciks or {}).items():
        for row in bundle['companies'][ticker]['history']['financials']:
            for candidate in row.get('conflicts') or [row]:
                require(int(snapshots.document(candidate)['cik']) == cik, 'company_cik')
    for ticker in required_provenance:
        require(companies[ticker]['arithmetic_checks'] > 0 and companies[ticker]['proven_financial_input_ids'], 'required_provenance')
    return {'passed':True,'active_tickers':list(expected_tickers),'companies':companies,'snapshot_count':len(snapshots.checked),
        'strict_historical_snapshots':False,'scope':'read-only database history, archived source bytes and deployed HTTP GETs; browser and user acceptance pending'}


def read_bundle(api_url):
    """No API mutations; all SQL is forced read-only even if a reader regresses."""
    import os
    import psycopg
    import requests
    from investment_assistant.db import list_watchlist_items
    from investment_assistant.data import repo as repository
    url = os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL')
    require(bool(url), 'database_configuration')
    parsed = urlsplit(api_url)
    require(parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1','localhost'} and not parsed.username and not parsed.password, 'loopback_api')
    bundle = {'companies':{}}
    with psycopg.connect(url, options='-c default_transaction_read_only=on') as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        bundle['active_tickers'] = sorted(r['ticker'] for r in list_watchlist_items(conn) if r['status'] == 'active' and r['ticker'] in EXPECTED_TICKERS)
        # Also retain unexpected active US identities, so exact identity assertions fail.
        from investment_assistant.data.identity import stock_id
        for row in list_watchlist_items(conn):
            if row['status'] != 'active' or row['ticker'] in bundle['active_tickers']:
                continue
            try:
                stock_id(row['ticker'])
            except ValueError:
                continue
            bundle['active_tickers'].append(row['ticker'])
        for ticker in EXPECTED_TICKERS:
            sid = stock_id(ticker)
            response = requests.get(api_url.rstrip('/')+'/api/us-research/'+ticker, timeout=60, allow_redirects=False)
            require(response.status_code == 200, 'deployed_http_read')
            payload = response.json()
            bundle['companies'][ticker] = {'view':payload.get('data',payload),
                'history':{ds:repository.read_history(conn,sid,ds) for ds in ('financials','market','filings','guidance')},
                'states':repository.states(conn,sid)}
    return bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument('--api-url',default='http://127.0.0.1:8787')
    parser.add_argument('--raw-root',type=Path,default=root/'.local/us-research/raw')
    parser.add_argument('--output',type=Path,help='Optional sanitized JSON report; no other writes are performed')
    args = parser.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(root/'.env')
    try:
        result = verify_bundle(read_bundle(args.api_url),raw_root=args.raw_root,expected_ciks=EXPECTED_CIKS)
        exit_code = 0
    except VerificationError as error:
        result,exit_code = {'passed':False,'failed_check':str(error)},1
    except Exception:
        result,exit_code = {'passed':False,'failed_check':'readback_or_source_unavailable'},1
    text = json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(text+'\n')
    print(text)
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
