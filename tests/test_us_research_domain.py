"""Hand-computed accounting fixtures: amounts, dates and basis are independently chosen."""
import importlib
import json
from pathlib import Path

import pytest


def module(name):
    target = {'domain': 'data.fundamentals', 'metrics': 'derivation.indicators.fiscal',
              'sources': 'data.sources.fundamentals', 'repository': 'data.repo',
              'templates': 'analysis.fundamental_templates'}[name]
    path = Path(__file__).parents[1] / 'investment_assistant' / (target.replace('.', '/') + '.py')
    assert path.exists(), f'Missing production feature: {target}'
    return importlib.import_module(f'investment_assistant.{target}')


def obs(metric, value, start, end, **overrides):
    return dict(metric=metric, value=value, unit='USD', basis='us-gaap:consolidated',
                start=start, end=end, period_kind='duration' if start else 'instant',
                form='10-K' if end.endswith('12-31') else '10-Q', accession='0000000001-24-000001',
                filed='2025-02-01', source_url='https://www.sec.gov/example', concept=metric,
                source_locator=f'{metric}/{start}/{end}', source_hash='public-fixture', **{}) | overrides


def year(metric='cfo', values=(10, 25, 45, 70), start='2024-01-01'):
    return [obs(metric, v, start, end) for v, end in zip(values,
            ['2024-03-31', '2024-06-30', '2024-09-30', '2024-12-31'])]


def series(rows, **kwargs):
    return module('metrics').financial_series(rows, **kwargs)


def test_stock_identity_rejects_other_markets_and_paths():
    f = module('domain').stock_id
    assert f(' mu ') == 'US:stock:MU'
    assert f('BRK.B') == 'US:stock:BRK.B'
    for ticker in ['688008.SH', 'CN:stock:MU', '^GSPC', '../MU', 'US:index:SPY', '123', 'A/B', 'MU..']:
        with pytest.raises(ValueError):
            f(ticker)


def test_cumulative_amounts_differenced_but_inventory_is_instant():
    rows = series(year() + [obs('inventory', 80, None, '2024-12-31')])
    assert [r['metrics']['cfo']['value'] for r in rows] == [10, 15, 20, 25]
    assert rows[-1]['metrics']['inventory']['value'] == 80
    assert rows[-1]['ttm']['cfo']['value'] == 70
    assert len(rows[-1]['metrics']['cfo']['input_ids']) == 2
    assert rows[-1]['start'] == '2024-10-01'


def test_missing_q2_prevents_q3_difference_and_ttm():
    rows = series([year()[i] for i in [0, 2, 3]] + year('revenue', (100, 200, 300, 400)))
    assert rows[2]['metrics']['cfo']['value'] is None
    assert rows[2]['metrics']['cfo']['missing_reason']
    assert rows[-1]['ttm']['cfo']['value'] is None
    # Other metrics survive a partial statement.
    assert rows[-1]['ttm']['revenue']['value'] == 400


def test_annual_eps_and_weighted_shares_are_never_differenced():
    rows = series(year('revenue') + year('eps_diluted', (1, 2, 3, 4)) + year('weighted_shares', (50, 51, 52, 53)))
    assert rows[-1]['metrics']['eps_diluted']['value'] is None
    assert rows[-1]['metrics']['weighted_shares']['value'] is None


def test_53_week_fiscal_year_uses_actual_intervals():
    ends = ['2023-04-01', '2023-07-01', '2023-09-30', '2024-01-06']
    rows = series([obs('revenue', v, '2023-01-01', e) for v, e in zip([10, 25, 45, 70], ends)])
    assert [r['days'] for r in rows] == [91, 91, 91, 98]
    assert rows[-1]['ttm']['revenue']['value'] == 70
    assert rows[-1]['period'] == '2023-10-01/2024-01-06'


def test_latest_restatement_selected_and_same_filing_difference_preferred():
    rows = year()
    rows += [obs('cfo', 11, '2024-01-01', '2024-03-31', filed='2025-03-01', accession='0000000001-25-000002'),
             obs('cfo', 27, '2024-01-01', '2024-06-30', filed='2025-03-01', accession='0000000001-25-000002')]
    result = series(rows)
    assert result[0]['metrics']['cfo']['value'] == 11
    assert result[1]['metrics']['cfo']['value'] == 16
    assert result[2]['metrics']['cfo']['value'] == 20  # original nine-month filing contains original six months


def test_unresolved_cross_filing_restatement_is_missing():
    rows = year()
    rows[1]['accession'] = '0000000001-24-000002'
    rows[2]['accession'] = '0000000001-24-000003'
    rows.append(obs('cfo', 26, '2024-01-01', '2024-06-30', filed='2025-03-01', accession='0000000001-25-000004'))
    assert series(rows)[2]['metrics']['cfo']['value'] is None


def test_conflicting_same_version_is_missing_not_arbitrarily_chosen():
    rows = year() + [year()[0] | {'value': 999}]
    assert series(rows)[0]['metrics']['cfo']['value'] is None


def test_currency_and_split_bases_are_not_mixed():
    rows = year()
    rows[1]['unit'] = 'EUR'
    result = series(rows)
    assert result[1]['metrics']['cfo']['value'] is None
    assert result[-1]['ttm']['cfo']['value'] is None
    direct = [obs('eps_diluted', 1, s, e, unit='USD/shares', basis=b) for s, e, b in [
        ('2024-01-01', '2024-03-31', 'split:pre'), ('2024-04-01', '2024-06-30', 'split:post'),
        ('2024-07-01', '2024-09-30', 'split:post'), ('2024-10-01', '2024-12-31', 'split:post')]]
    assert series(direct)[-1]['ttm']['eps_diluted']['value'] is None


def test_fcf_excludes_broad_capex_and_margin_aggregates_amounts():
    rows = series(year('revenue', (10, 30, 60, 100)) + year('gross_profit', (5, 10, 20, 30)) + year() + year('broad_capex'))
    assert rows[-1]['metrics']['fcf']['value'] is None
    assert rows[-1]['metrics']['broad_capex']['value'] == 25
    assert rows[-1]['ttm']['gross_margin']['value'] == pytest.approx(.3)
    rows = series(year() + year('cash_capex', (1, 3, 6, 10)))
    assert rows[-1]['ttm']['fcf']['value'] == 60


def test_turnover_requires_matching_start_balance_not_any_previous_balance():
    rows = year('revenue', (100, 200, 300, 400)) + [obs('receivables', 20, None, '2024-12-31')]
    assert series(rows)[-1]['metrics']['receivable_days']['value'] is None
    rows += [obs('receivables', 10, None, '2024-09-30')]
    assert series(rows)[-1]['metrics']['receivable_days']['value'] == pytest.approx(13.8)


def test_growth_nonpositive_base_and_history_before_display_limit():
    rows = year('revenue', (-10, 0, 10, 20))
    result = series(rows)
    assert result[1]['metrics']['revenue_qoq']['value'] is None
    assert series(year('revenue'), limit=1)[0]['ttm']['revenue']['value'] == 70


def test_valuation_common_earnings_and_independent_ps():
    financials = series(year('revenue', (25, 50, 75, 100)) + year('net_income', (2, 4, 6, 8)))
    market = {'quote': {'market_cap': 1000, 'currency': 'USD', 'date': '2025-03-01',
                       'market_cap_verified': True, 'share_class_basis': 'all_common_equity',
                       'source_url': 'https://example.com/quote'}}
    result = module('metrics').valuation_view(financials, market)
    assert result['pe_ttm']['value'] is None
    assert result['ps_ttm']['value'] == 10
    financials = series(year('revenue', (25, 50, 75, 100)) + [x | {'basis': 'us-gaap:common'} for x in year('common_net_income', (1, 2, 3, 4))])
    assert module('metrics').valuation_view(financials, market)['pe_ttm']['value'] == 250
    market['quote']['market_cap_verified'] = False
    assert module('metrics').valuation_view(financials, market)['ps_ttm']['value'] is None


def test_nonpositive_common_profit_disallows_pe_and_json_is_finite():
    rows = series([x | {'basis': 'us-gaap:common'} for x in year('common_net_income', (-1, -2, -3, -4))])
    market = {'quote': {'market_cap': 1000, 'currency': 'USD', 'date': '2025-03-01',
                       'market_cap_verified': True, 'share_class_basis': 'all_common_equity'}}
    assert module('metrics').valuation_view(rows, market)['pe_ttm']['value'] is None
    json.dumps(series(year() + [obs('revenue', float('nan'), '2024-01-01', '2024-03-31')]), allow_nan=False)


def test_calendar_month_performance_and_common_date_comparison():
    bars = [{'date': d, 'close': c * 2, 'adjusted_close': c, 'volume': 5} for d, c in [
        ('2024-10-16', 100), ('2025-01-16', 80), ('2025-04-16', 120), ('2025-07-16', 150), ('2025-10-16', 200)]]
    from datetime import date, timedelta
    events = {b['date']: b for b in bars}
    d, last, bars = date(2024, 10, 16), None, []
    while d <= date(2025, 10, 16):
        last = events.get(d.isoformat(), last)
        if d.weekday() < 5:
            bars.append(last | {'date': d.isoformat()})
        d += timedelta(days=1)
    bench = [{'date': b['date'], 'adjusted_close': 100, 'close': 100} for b in bars]
    result = module('metrics').performance_view(bars, {'SPY': bench})
    assert result['windows']['12m']['return']['value'] == 1
    assert result['windows']['12m']['max_drawdown']['value'] == pytest.approx(-.2)
    assert result['windows']['3m']['return']['value'] == pytest.approx(1/3)
    assert result['windows']['12m']['relative']['SPY']['value'] == 1
    assert module('metrics').performance_view(bars[1:], {})['windows']['12m']['return']['value'] is None


def test_missing_adjusted_close_is_not_replaced_by_raw_close():
    bars = [{'date': '2025-01-01', 'close': 10}, {'date': '2025-04-01', 'close': 20}]
    assert module('metrics').performance_view(bars, {})['windows']['3m']['return']['value'] is None


def test_normalizer_preserves_alias_history_and_income_attribution():
    facts = {'RevenueFromContractWithCustomerExcludingAssessedTax': [10, '2021-03-31'],
             'Revenues': [20, '2024-03-31'], 'NetIncomeLoss': [5, '2024-03-31'],
             'ProfitLoss': [6, '2024-03-31'], 'NetIncomeLossAvailableToCommonStockholdersBasic': [4, '2024-03-31']}
    payload = {'cik': 1, 'entityName': 'Example', 'facts': {'us-gaap': {
        k: {'units': {'USD': [{'val': v, 'start': e[:4]+'-01-01', 'end': e, 'form': '10-Q',
                              'accn': '0000000001-24-000001', 'filed': '2024-05-01', 'fy': 2024, 'fp': 'Q1'}]}}
        for k, (v, e) in facts.items()}}}
    norm = module('domain').normalize_companyfacts(payload, ticker='MU')
    assert len(norm) == 5
    assert {r['basis'] for r in norm if 'income' in r['metric']} == {'us-gaap:parent', 'us-gaap:common', 'us-gaap:consolidated'}
    assert [r['metrics']['revenue']['value'] for r in series(norm)] == [10, 20]
    assert all(r['source_locator'] and r['source_hash'] and r['id'] for r in norm)


def test_normalizer_preserves_forms_non_usd_and_versions_but_rejects_nonfinite():
    row = {'val': 10, 'start': '2024-01-01', 'end': '2024-03-31', 'form': '10-Q',
           'accn': '0000000001-24-000001', 'filed': '2024-05-01'}
    payload = {'cik': 1, 'entityName': 'Example', 'facts': {'us-gaap': {'Revenues': {'units': {'EUR': [row,
        row | {'filed': '2024-06-01', 'accn': '0000000001-24-000002', 'val': 12}, row | {'val': float('inf')},
        row | {'form': '10-K', 'val': 40}]}}}}}
    norm = module('domain').normalize_companyfacts(payload, ticker='TSLA')
    assert len(norm) == 2
    assert all(r['unit'] == 'EUR' for r in norm)
    assert len(module('domain').normalize_companyfacts(payload, ticker='MU', allowed_forms=['10-K'])) == 1


def test_overlapping_debt_total_and_components_are_not_double_counted():
    rows = year('revenue') + [obs(k, v, None, '2024-12-31') for k, v in [('debt', 100), ('long_term_debt', 80), ('short_term_debt', 20), ('short_term_borrowings', 0)]]
    assert series(rows)[-1]['metrics']['debt']['value'] == 100
    rows = [r for r in rows if r['metric'] != 'debt']
    assert series(rows)[-1]['metrics']['debt']['value'] == 100
    rows = [r for r in rows if r['metric'] != 'short_term_debt']
    assert series(rows)[-1]['metrics']['debt']['value'] is None


def test_cross_basis_cashflow_and_capex_cannot_form_fcf():
    rows = year() + [r | {'basis': 'us-gaap:parent'} for r in year('cash_capex')]
    assert series(rows)[-1]['metrics']['fcf']['value'] is None


def test_valuation_rejects_quote_before_financial_disclosure():
    rows = series(year('revenue', (25, 50, 75, 100)))
    market = {'quote': {'market_cap': 1000, 'currency': 'USD', 'date': '2024-12-31',
                       'market_cap_verified': True, 'share_class_basis': 'all_common_equity'}}
    assert module('metrics').valuation_view(rows, market)['ps_ttm']['value'] is None


def test_missing_common_earnings_reason_identifies_attribution():
    rows = series(year('revenue') + year('net_income'))
    market = {'quote': {'market_cap': 1000, 'currency': 'USD', 'date': '2025-03-01',
                       'market_cap_verified': True, 'share_class_basis': 'all_common_equity'}}
    result = module('metrics').valuation_view(rows, market)
    assert 'common' in result['pe_ttm']['missing_reason'].lower()
    assert result['ps_ttm']['value'] is not None


def test_performance_carries_both_security_and_benchmark_provenance():
    bars = [{'date': d, 'adjusted_close': c, 'source_hash': 'stock-hash', 'source_url': 'https://example.com/MU'}
            for d, c in [('2025-01-01', 10), ('2025-04-01', 20)]]
    bench = [r | {'source_hash': 'benchmark-hash', 'source_url': 'https://example.com/SPY'} for r in bars]
    result = module('metrics').performance_view(bars, {'SPY': bench})['windows']['3m']
    assert result['return']['sources'][0]['source_hash'] == 'stock-hash'
    assert {s['source_hash'] for s in result['relative']['SPY']['sources']} == {'stock-hash', 'benchmark-hash'}
    assert result['max_drawdown']['value'] is None  # Two endpoints cannot establish a quarter's maximum drawdown.


def test_conflicting_price_on_same_date_is_not_silently_overwritten():
    bars = [{'date': d, 'adjusted_close': c} for d, c in [('2025-01-01', 10), ('2025-01-01', 100), ('2025-04-01', 20)]]
    assert module('metrics').performance_view(bars, {})['windows']['3m']['return']['value'] is None


def test_direct_quarter_is_used_ahead_of_cumulative_difference():
    rows = year() + [obs('cfo', 16, '2024-04-01', '2024-06-30')]
    assert series(rows)[1]['metrics']['cfo']['value'] == 16


def test_current_alias_conflict_is_missing_and_historical_alias_still_available():
    rows = year('revenue') + [year('revenue')[0] | {'concept': 'alternate_revenue', 'value': 99}]
    assert series(rows)[0]['metrics']['revenue']['value'] is None
    assert series(rows)[-1]['metrics']['revenue']['value'] == 25


def test_ttm_does_not_cross_a_missing_whole_quarter():
    rows = [obs('revenue', 10, s, e) for s, e in [('2023-10-01', '2023-12-31'),
        ('2024-01-01', '2024-03-31'), ('2024-07-01', '2024-09-30'), ('2024-10-01', '2024-12-31')]]
    assert series(rows)[-1]['ttm']['revenue']['value'] is None


def test_ttm_rejects_conflicting_restatement_inputs_even_when_each_quarter_is_valid():
    rows = year() + [obs('cfo', 11, '2024-01-01', '2024-03-31', filed='2025-03-01', accession='0000000001-25-000002'),
                     obs('cfo', 27, '2024-01-01', '2024-06-30', filed='2025-03-01', accession='0000000001-25-000002')]
    assert series(rows)[-1]['ttm']['cfo']['value'] is None


def test_disclosed_short_borrowings_are_added_once_to_separate_current_maturities():
    rows = year('revenue') + [obs(k, v, None, '2024-12-31') for k, v in [
        ('long_term_debt', 80), ('short_term_debt', 20), ('short_term_borrowings', 10)]]
    assert series(rows)[-1]['metrics']['debt']['value'] == 110


def test_amount_taxonomy_rejects_per_share_unit_in_revenue():
    payload = {'cik': 1, 'facts': {'us-gaap': {'Revenues': {'units': {'USD/shares': [
        {'val': 10, 'start': '2024-01-01', 'end': '2024-03-31', 'form': '10-Q',
         'accn': '0000000001-24-000001', 'filed': '2024-05-01'}]}}}}}
    assert module('domain').normalize_companyfacts(payload, ticker='MU') == []


def debt_facts():
    return json.loads((Path(__file__).parent / 'fixtures/us_research/debt-scopes.json').read_text())


def test_debt_taxonomy_total_does_not_double_count_current_maturities():
    rows = module('domain').normalize_companyfacts(debt_facts(), ticker='MU')
    m = series(rows)[-1]['metrics']
    assert m['long_term_debt_total']['value'] == 100
    assert m['long_term_debt']['value'] == 80
    assert m['debt']['value'] == 110  # 100 total long term + 10 short borrowings, not 100+80+20+10.
    assert 'excluding_finance_leases' in m['debt']['basis']


def test_debt_missing_short_borrowings_cannot_claim_total_interest_bearing_debt():
    payload = debt_facts()
    del payload['facts']['us-gaap']['ShortTermBorrowings']
    rows = module('domain').normalize_companyfacts(payload, ticker='MU')
    m = series(rows)[-1]['metrics']
    assert m['long_term_debt_total']['value'] == 100
    assert m['debt']['value'] is None


def test_debt_current_includes_borrowings_and_leases_and_is_not_added_twice():
    payload = debt_facts()
    facts = payload['facts']['us-gaap']
    row = facts['LongTermDebtCurrent']['units']['USD'][0]
    facts['DebtCurrent'] = {'units': {'USD': [row | {'val': 35}]}}
    facts['LongTermDebtAndCapitalLeaseObligations'] = {'units': {'USD': [row | {'val': 85}]}}
    m = series(module('domain').normalize_companyfacts(payload, ticker='MU'))[-1]['metrics']
    assert m['debt']['value'] == 120  # 35 current inclusive + 85 noncurrent inclusive; never add short borrowings again.
    assert 'including_finance_leases' in m['debt']['basis']


def test_direct_quarter_restatement_cannot_mix_with_old_cumulative_ttm():
    rows = year() + [obs('cfo', 16, '2024-04-01', '2024-06-30',
                        filed='2025-03-01', accession='0000000001-25-000002')]
    result = series(rows)
    assert [r['metrics']['cfo']['value'] for r in result] == [10, 16, 20, 25]
    assert result[-1]['ttm']['cfo']['value'] is None
    assert 'cumulative' in result[-1]['ttm']['cfo']['missing_reason'].lower()
    # This conflict must also survive a rolling TTM whose first quarter is the restated Q2.
    rows.append(obs('cfo', 12, '2025-01-01', '2025-03-31'))
    assert series(rows)[-1]['ttm']['cfo']['value'] is None


def test_direct_quarter_consistent_with_cumulative_has_available_ttm():
    rows = year() + [obs('cfo', 15, '2024-04-01', '2024-06-30',
                        filed='2025-03-01', accession='0000000001-25-000002')]
    assert series(rows)[-1]['ttm']['cfo']['value'] == 70


def test_unresolved_cumulative_restatement_cannot_hide_behind_missing_validation_value():
    rows = [row | {'accession': f'0000000001-24-{i:06d}'} for i, row in enumerate(year(), 1)]
    rows += [obs('cfo', 11, '2024-01-01', '2024-03-31', filed='2025-03-01', accession='0000000001-25-000010'),
             obs('cfo', 16, '2024-04-01', '2024-06-30', filed='2025-03-01', accession='0000000001-25-000010')]
    result = series(rows)
    assert [r['metrics']['cfo']['value'] for r in result] == [11, 16, 20, 25]
    ttm = result[-1]['ttm']['cfo']
    assert ttm['value'] is None
    assert 'restatement' in ttm['missing_reason'].lower()
    # The older Q1 source must be available to inspect the unresolved 10 vs 11 baseline.
    assert '0000000001-24-000001' in {s['accession'] for s in ttm['sources']}
    assert '0000000001-25-000010' in {s['accession'] for s in ttm['sources']}


def test_conflicting_cumulative_current_versions_invalidate_direct_quarter_ttm():
    rows = year() + [year()[1] | {'value': 26}]
    rows.append(obs('cfo', 15, '2024-04-01', '2024-06-30', filed='2025-03-01', accession='0000000001-25-000010'))
    # Keep every quarter directly available so missing quarter inputs cannot mask this regression.
    rows += [obs('cfo', 20, '2024-07-01', '2024-09-30'), obs('cfo', 25, '2024-10-01', '2024-12-31')]
    result = series(rows)
    assert all(r['metrics']['cfo']['value'] is not None for r in result)
    assert result[-1]['ttm']['cfo']['value'] is None


def test_direct_only_quarters_do_not_require_nonexistent_cumulative_disclosures():
    rows = [obs('cfo', value, start, end) for value, start, end in [
        (10, '2024-01-01', '2024-03-31'), (15, '2024-04-01', '2024-06-30'),
        (20, '2024-07-01', '2024-09-30'), (25, '2024-10-01', '2024-12-31')]]
    assert series(rows)[-1]['ttm']['cfo']['value'] == 70


def test_incompatible_cumulative_baseline_is_retained_as_evidence():
    rows = [obs('cfo', value, start, end, id=oid) for value, start, end, oid in [
        (10, '2024-01-01', '2024-03-31', 'baseline-usd'), (15, '2024-04-01', '2024-06-30', 'direct-q2'),
        (20, '2024-07-01', '2024-09-30', 'direct-q3'), (25, '2024-10-01', '2024-12-31', 'direct-q4')]]
    rows.append(obs('cfo', 25, '2024-01-01', '2024-06-30', unit='EUR', id='cumulative-eur'))
    ttm = series(rows)[-1]['ttm']['cfo']
    assert ttm['value'] is None
    assert 'Incompatible' in ttm['missing_reason']
    assert {'baseline-usd', 'cumulative-eur'} <= set(ttm['input_ids'])


def test_existing_cumulative_without_matching_baseline_is_unverified_not_absent():
    rows = [obs('cfo', value, start, end) for value, start, end in [
        (10, '2024-01-02', '2024-03-31'), (15, '2024-04-01', '2024-06-30'),
        (20, '2024-07-01', '2024-09-30'), (25, '2024-10-01', '2024-12-31')]]
    rows.append(obs('cfo', 25, '2024-01-01', '2024-06-30', id='cumulative-without-baseline'))
    ttm = series(rows)[-1]['ttm']['cfo']
    assert ttm['value'] is None
    assert 'Missing preceding cumulative boundary' in ttm['missing_reason']
    assert 'cumulative-without-baseline' in ttm['input_ids']
