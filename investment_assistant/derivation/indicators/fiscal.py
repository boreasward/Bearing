"""Conservative fiscal-period calculations; unknown inputs stay unknown with provenance."""
from __future__ import annotations

from calendar import monthrange
from collections import Counter, defaultdict
from datetime import date, timedelta

from investment_assistant.data.fundamentals import CONCEPTS, INSTANT, NON_ADDITIVE, finite, observation_id, canonical_hash


def _day(value):
    return date.fromisoformat(value)


def _days(start, end):
    return (_day(end) - _day(start)).days + 1


def metric(value=None, *, unit=None, basis=None, method='unavailable', inputs=(), reason=None):
    value = finite(value)
    sources, ids = [], []
    for row in inputs:
        if 'metric' in row:
            ids.append(observation_id(row))
            sources.append({k: row.get(k) for k in ('source_url', 'source_locator', 'source_hash', 'concept', 'accession', 'filed', 'start', 'end')})
        else:
            ids.extend(row.get('input_ids', []))
            sources.extend(row.get('sources', []))
    sources = [dict(t) for t in dict.fromkeys(tuple(sorted(s.items())) for s in sources)]
    return {'value': value, 'unit': unit, 'basis': basis, 'method': method,
            'input_ids': list(dict.fromkeys(ids)), 'sources': sources,
            'missing_reason': None if value is not None else reason or 'No compatible source observation'}


def _select(rows):
    """Resolve latest version inside each concept before comparing taxonomy aliases."""
    if not rows:
        return None, 'No source observation'
    by_concept = defaultdict(list)
    for row in rows:
        by_concept[row.get('concept', row['metric'])].append(row)
    selected = []
    for candidates in by_concept.values():
        latest = max(r.get('filed', '') for r in candidates)
        current = [r for r in candidates if r.get('filed', '') == latest]
        if len({(r['value'], r['unit'], r['basis']) for r in current}) != 1:
            return None, 'Conflicting facts within the same latest filing date'
        selected.append(sorted(current, key=lambda r: (r.get('accession', ''), observation_id(r)))[-1])
    if len({(r['value'], r['unit'], r['basis']) for r in selected}) != 1:
        return None, 'Conflicting overlapping taxonomy aliases or units'
    return max(selected, key=lambda r: r.get('filed', '')), None


def _direct(rows):
    row, reason = _select(rows)
    return metric(row['value'], unit=row['unit'], basis=row['basis'], method='reported', inputs=[row]) if row else metric(reason=reason, inputs=rows)


def _quarter(rows, name, start, end, *, prefer_direct=True):
    direct = [r for r in rows if r['end'] == end and r.get('start') == start]
    if direct and prefer_direct:
        return _direct(direct)
    if name in NON_ADDITIVE:
        return metric(reason='EPS, weighted shares and instant balances cannot be differenced')
    candidates = [r for r in rows if r['end'] == end and r.get('start') and r['start'] < start
                  and 120 < _days(r['start'], end) <= 380]
    candidates = [r for r in candidates if r['start'] == max((x['start'] for x in candidates), default='')]
    current, reason = _select(candidates)
    if not current:
        return metric(method='cumulative_unavailable' if candidates else 'unavailable', reason=reason, inputs=candidates)
    prior_end = (_day(start) - timedelta(days=1)).isoformat()
    prior_boundary = [r for r in rows if r['end'] == prior_end and r.get('start') == current['start']]
    prior = [r for r in prior_boundary if r['unit'] == current['unit'] and r['basis'] == current['basis']]
    if not prior:
        reason = ('Incompatible preceding cumulative unit/basis' if prior_boundary else
                  'Missing preceding cumulative boundary observation')
        return metric(method='cumulative_unavailable', reason=reason, inputs=[current]+prior_boundary)
    same_filing = [r for r in prior if r['accession'] == current['accession']]
    if same_filing:
        previous, reason = _select(same_filing)
    elif len({r['value'] for r in prior}) > 1:
        previous, reason = None, 'Unresolved restatement across cumulative filings'
    else:
        previous, reason = _select(prior)
    if not previous:
        return metric(method='cumulative_unavailable', reason=reason or 'Missing compatible preceding cumulative quarter', inputs=[current]+prior)
    return metric(current['value'] - previous['value'], unit=current['unit'], basis=current['basis'],
                  method='annual_minus_nine_months' if _days(current['start'], end) >= 330 else 'ytd_difference',
                  inputs=[current, previous])


def _combine(left, right, op, method, unit=None, basis=None, positive=False):
    inputs = [left, right]
    if left['value'] is None or right['value'] is None:
        return metric(method=method, inputs=inputs, reason='Missing compatible calculation input')
    # Earnings attribution may differ for a margin; currency must still agree.
    if left['unit'] != right['unit'] or left['basis'] != right['basis']:
        return metric(method=method, inputs=inputs, reason='Incompatible input units or accounting basis')
    if positive and right['value'] <= 0:
        return metric(method=method, inputs=inputs, reason='Nonpositive denominator')
    return metric(op(left['value'], right['value']), unit=unit or left['unit'], basis=basis or left['basis'],
                  method=method, inputs=inputs)


def _derived(m):
    if m['gross_profit']['value'] is None:
        m['gross_profit'] = _combine(m['revenue'], m['cost_of_revenue'], lambda a, b: a-b, 'revenue_minus_cost')
    m['fcf'] = _combine(m['cfo'], m['cash_capex'], lambda a, b: a-b, 'cfo_minus_cash_ppe')
    for output, numerator in [('gross_margin', 'gross_profit'), ('operating_margin', 'operating_income'), ('fcf_margin', 'fcf')]:
        m[output] = _combine(m[numerator], m['revenue'], lambda a, b: a/b, 'aggregate_amount_ratio', unit='ratio', positive=True)


def _contiguous(rows):
    return all(_day(b['start']) == _day(a['end']) + timedelta(days=1) for a, b in zip(rows, rows[1:]))


def financial_series(observations: list[dict], *, limit: int = 12) -> list[dict]:
    if limit <= 0:
        return []
    rows = []
    for row in observations:
        if row.get('metric') not in CONCEPTS or finite(row.get('value')) is None:
            continue
        try:
            _day(row['end'])
            if row.get('start'):
                _day(row['start'])
        except (ValueError, KeyError, TypeError):
            continue
        rows.append(row)
    by_id = {observation_id(r): r for r in rows}
    by_metric = {name: [r for r in rows if r['metric'] == name] for name in CONCEPTS}
    # Fiscal dates come from durations, never filing fy/fp or calendar-quarter Frames.
    intervals = defaultdict(Counter)
    for row in rows:
        start, end = row.get('start'), row['end']
        if start and 60 <= _days(start, end) <= 120:
            intervals[end][start] += 1
    for row in rows:
        if not row.get('start') or row['metric'] in NON_ADDITIVE:
            continue
        if not 120 < _days(row['start'], row['end']) <= 380:
            continue
        previous = [r for r in by_metric[row['metric']] if r.get('start') == row['start'] and r['end'] < row['end']]
        for p in previous:
            start = (_day(p['end']) + timedelta(days=1)).isoformat()
            if 60 <= _days(start, row['end']) <= 120 and row['end'] not in intervals:
                intervals[row['end']][start] += 1
    results = []
    cumulative_conflicts = {}
    for end, starts in sorted(intervals.items()):
        start = max(starts, key=lambda s: (starts[s], s))
        m = {}
        for name in CONCEPTS:
            records = by_metric[name]
            m[name] = _direct([r for r in records if r['end'] == end and not r.get('start')]) if name in INSTANT else _quarter(records, name, start, end)
        if m['debt']['value'] is None:
            # DebtCurrent already includes short borrowing/current leases. Do not add them again.
            inclusive = _combine(m['debt_with_leases_noncurrent'], m['debt_with_leases_current'],
                                 lambda a, b: a+b, 'noncurrent_plus_current_debt_including_finance_leases')
            if inclusive['value'] is not None:
                inclusive['basis'] = 'us-gaap:debt_including_finance_leases'
                m['debt'] = inclusive
            else:
                total = m['long_term_debt_total']
                if total['value'] is None:
                    total = _combine(m['long_term_debt'], m['short_term_debt'], lambda a, b: a+b,
                                     'noncurrent_plus_current_long_term_debt')
                m['debt'] = _combine(total, m['short_term_borrowings'], lambda a, b: a+b,
                                     'long_term_total_plus_short_borrowings')
                m['debt']['basis'] = 'us-gaap:debt_excluding_finance_leases'
                if m['debt']['value'] is None:
                    m['debt']['missing_reason'] = 'Incomplete mutually exclusive debt components; missing borrowing/lease coverage is unknown'
        # A later direct quarter can disagree with older overlapping YTD observations
        # even though no two input facts have identical intervals. Preserve the direct
        # report, but do not sum it into TTM alongside those cumulative versions.
        for name, current in m.items():
            if name in NON_ADDITIVE or current['method'] != 'reported' or current['value'] is None:
                continue
            cumulative = _quarter(by_metric[name], name, start, end, prefer_direct=False)
            if cumulative['method'] == 'cumulative_unavailable' or (cumulative['value'] is not None and (
                current['value'], current['unit'], current['basis']
            ) != (cumulative['value'], cumulative['unit'], cumulative['basis'])):
                cumulative_conflicts[(end, name)] = cumulative
        _derived(m)
        for out, balance, denominator in [('receivable_days', 'receivables', 'revenue'), ('inventory_days', 'inventory', 'cost_of_revenue')]:
            opening_date = (_day(start) - timedelta(days=1)).isoformat()
            opening = _direct([r for r in by_metric[balance] if r['end'] == opening_date and not r.get('start')])
            average = _combine(opening, m[balance], lambda a, b: (a+b)/2, 'average_opening_closing_balance')
            m[out] = _combine(average, m[denominator], lambda a, b: a/b*_days(start, end), 'average_balance_over_quarter_flow_times_days', unit='days', positive=True)
        row = {'period': f'{start}/{end}', 'start': start, 'end': end, 'days': _days(start, end), 'metrics': m,
               'ttm': {}, 'warnings': ['Historical backfill of currently available filing versions; not strict point-in-time']}
        for metric_name in CONCEPTS:
            if (end, metric_name) in cumulative_conflicts:
                row['warnings'].append(f'{metric_name}: direct quarter conflicts with overlapping cumulative filings')
        if len(starts) > 1:
            row['warnings'].append('Multiple reported quarter starts; most-supported actual interval selected')
        results.append(row)
        window = results[-4:]
        for name in CONCEPTS:
            inputs = [r['metrics'][name] for r in window]
            reason = None
            if name in NON_ADDITIVE:
                reason = 'Non-additive metric; TTM summation unavailable without verified share basis'
            elif len(window) != 4 or not _contiguous(window):
                reason = 'Four contiguous fiscal quarters required'
            elif any(x['value'] is None for x in inputs):
                reason = 'Missing quarter input'
            elif len({(x['unit'], x['basis']) for x in inputs}) != 1:
                reason = 'Incompatible quarter currency or basis'
            versions = defaultdict(set)
            for x in inputs:
                for oid in x['input_ids']:
                    fact = by_id.get(oid)
                    if fact:
                        versions[(fact['metric'], fact.get('start'), fact['end'], fact['unit'], fact['basis'])].add(fact['value'])
            if any(len(values) > 1 for values in versions.values()):
                reason = 'Conflicting restatement inputs across TTM quarters'
            conflicts = [cumulative_conflicts[(r['end'], name)] for r in window if (r['end'], name) in cumulative_conflicts]
            if conflicts:
                reason = 'Direct quarter and overlapping cumulative versions are inconsistent or unresolved: ' + '; '.join(dict.fromkeys(c['missing_reason'] or 'Value/unit/basis mismatch' for c in conflicts))
            row['ttm'][name] = metric(None if reason else sum(x['value'] for x in inputs),
                unit=inputs[-1]['unit'], basis=inputs[-1]['basis'], method='sum_four_contiguous_quarters', inputs=inputs+conflicts, reason=reason)
        _derived(row['ttm'])
        for suffix, previous in [('qoq', results[-2] if len(results) >= 2 else None), ('yoy', results[-5] if len(results) >= 5 else None)]:
            reason = None
            if previous is None:
                reason = 'Comparable prior fiscal quarter unavailable'
            elif suffix == 'qoq' and not _contiguous([previous, row]):
                reason = 'Prior quarter is not contiguous'
            elif suffix == 'yoy' and (not _contiguous(results[-5:]) or not 350 <= (_day(end) - _day(previous['end'])).days <= 380):
                reason = 'Prior-year fiscal period is not comparable'
            m['revenue_'+suffix] = metric(reason=reason) if reason else _combine(m['revenue'], previous['metrics']['revenue'], lambda a, b: a/b-1, 'comparable_quarter_growth', unit='ratio', positive=True)
    return results[-limit:]


def _month_before(day, months):
    n = day.year * 12 + day.month - 1 - months
    year, month = n // 12, n % 12 + 1
    return day.replace(year=year, month=month, day=min(day.day, monthrange(year, month)[1]))


def _prices(bars):
    out, conflicts = {}, set()
    for bar in bars:
        value = finite(bar.get('adjusted_close'))
        try:
            d = _day(bar['date'])
        except (KeyError, TypeError, ValueError):
            continue
        if value is not None and value > 0:
            if d in out and out[d] != value:
                conflicts.add(d)
            out[d] = value
    return {d: v for d, v in out.items() if d not in conflicts}


def _bar_evidence(bars, start, end):
    if not start or not end:
        return []
    inputs = []
    for bar in bars:
        if start.isoformat() <= str(bar.get('date', '')) <= end.isoformat():
            source = {k: bar.get(k) for k in ('source_url', 'source_hash', 'date')}
            inputs.append({'input_ids': [bar.get('id') or canonical_hash(source)], 'sources': [source]})
    return inputs


def performance_view(bars: list[dict], benchmarks: dict[str, list[dict]]) -> dict:
    prices = _prices(bars)
    end = max(prices) if prices else None
    windows = {}
    for months in [3, 6, 12]:
        target = _month_before(end, months) if end else None
        dates = sorted(prices)
        starts = [d for d in dates if d <= target] if target else []
        start = starts[-1] if starts else None
        # At most seven calendar days accommodates ordinary market holidays; stale windows are missing.
        covered = bool(start and (target-start).days <= 7)
        reason = None if covered else 'Insufficient adjusted-price calendar coverage'
        selected = [d for d in dates if covered and start <= d <= end]
        ret = prices[end]/prices[start]-1 if covered else None
        inputs = _bar_evidence(bars, start, end)
        max_gap = max(((b-a).days for a, b in zip(selected, selected[1:])), default=0)
        drawdown_covered = covered and max_gap <= 7
        peak, dd = 0, 0
        for d in selected:
            peak = max(peak, prices[d])
            dd = min(dd, prices[d]/peak-1)
        item = {'target_start': target.isoformat() if target else None,
                'start': start.isoformat() if start else None, 'end': end.isoformat() if end else None,
                'observations': len(selected), 'max_gap_days': max_gap if covered else None, 'return': metric(ret, unit='ratio', basis='adjusted_close', method='end_over_start_minus_one', inputs=inputs, reason=reason),
                'max_drawdown': metric(dd if drawdown_covered else None, unit='ratio', basis='adjusted_close', method='minimum_close_over_running_peak_minus_one', inputs=inputs, reason=reason or 'Interior daily price coverage is incomplete'), 'relative': {}}
        for symbol, bench_bars in benchmarks.items():
            bench = _prices(bench_bars)
            common = sorted(set(prices) & set(bench))
            finishes = [d for d in common if end and d <= end]
            beginnings = [d for d in common if target and d <= target]
            a, b = beginnings[-1] if beginnings else None, finishes[-1] if finishes else None
            compatible = bool(a and b and b > a and (target-a).days <= 7 and (end-b).days <= 7)
            relative = prices[b]/prices[a] - bench[b]/bench[a] if compatible else None
            item['relative'][symbol] = metric(relative, unit='ratio', basis='adjusted_close_common_dates', method='stock_return_minus_benchmark_return', inputs=_bar_evidence(bars, a, b)+_bar_evidence(bench_bars, a, b), reason='Insufficient common-date calendar coverage')
            item['relative'][symbol].update(start=a.isoformat() if a else None, end=b.isoformat() if b else None)
        windows[f'{months}m'] = item
    return {'windows': windows, 'basis': 'adjusted_close', 'limitations': ['Adjusted prices reflect the provider current split/dividend history; sparse coverage is disclosed by observation count']}


def valuation_view(financials: list[dict], market: dict) -> dict:
    quote = market.get('quote') or {}
    ttm = financials[-1]['ttm'] if financials else {}
    cap = finite(quote.get('market_cap'))
    compatible = bool(cap and cap > 0 and quote.get('market_cap_verified') is True and quote.get('share_class_basis') == 'all_common_equity' and quote.get('date'))
    result = {}
    for name, source in [('pe_ttm', 'common_net_income'), ('ps_ttm', 'revenue'), ('fcf_yield', 'fcf')]:
        denom = ttm.get(source, metric())
        reason = None
        if not compatible:
            reason = 'Market capitalization date/currency/share-class compatibility unverified'
        elif denom['value'] is None:
            reason = 'Verified common-shareholder TTM earnings unavailable' if source == 'common_net_income' else 'Compatible TTM metric unavailable'
        elif denom['unit'] != quote.get('currency'):
            reason = 'Market capitalization and financial currency differ or are unknown'
        elif any(s.get('filed', '') > quote['date'] for s in denom['sources']) or (financials and financials[-1]['end'] > quote['date']):
            reason = 'Financial period or filing version is later than the market quote'
        elif source == 'common_net_income' and denom['basis'] != 'us-gaap:common':
            reason = 'Earnings attribution to common shareholders unverified'
        elif name != 'fcf_yield' and denom['value'] <= 0:
            reason = 'Nonpositive TTM denominator; conventional valuation multiple unavailable'
        value = None if reason else (denom['value']/cap if name == 'fcf_yield' else cap/denom['value'])
        result[name] = metric(value, unit='ratio' if name == 'fcf_yield' else 'multiple', basis=denom['basis'], method='fcf_over_market_cap' if name == 'fcf_yield' else 'market_cap_over_ttm', inputs=[denom], reason=reason)
        result[name]['market_date'] = quote.get('date')
        if quote.get('source_url'):
            result[name]['sources'].append({'source_url': quote['source_url'], 'date': quote.get('date'), 'source_hash': quote.get('source_hash'), 'share_class_basis': quote.get('share_class_basis')})
    return result
