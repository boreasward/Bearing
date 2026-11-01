"""Knowledge performance look-back (v2.7 spec §7): one sample per unit, shadow_block summary. Pure, offline."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

from investment_assistant.review.knowledge import perf
from investment_assistant.review import outcomes as review

T0 = datetime(2026, 10, 20, 12, tzinfo=UTC)


def plan(pid, ticker='NVDA', signal=date(2026, 10, 20), hours=0, version_no=0, kind='agent', valid_until=date(2026, 10, 28)):
    return {'id': pid, 'ticker': ticker, 'signal_date': signal, 'kind': kind, 'version_no': version_no,
            'created_at': T0 + timedelta(hours=hours), 'valid_until': valid_until}


def cite(pid, version=1, field='thesis', knowledge_id=3, run='r'):
    return {'agent_run_id': run, 'plan_id': pid, 'review_id': None, 'knowledge_id': knowledge_id, 'version': version,
            'field': field, 'call_seq': 1}


def closed(r, exit_date='2026-10-27', fill=date(2026, 10, 21)):
    return {'status': 'closed', 'fill_date': fill, 'fill_price': D('100'),
            'legs': [{'fraction': 1.0, 'exit_date': exit_date, 'exit_price': D('105'), 'reason': 'target1'}], 'r': r}


def summarize(citations, plans, outcomes, reviews_cited=0):
    return perf.summarize(citations=citations, plans=plans, outcomes=outcomes, reviews_cited=reviews_cited)


def test_constants():
    assert perf.KNOWLEDGE_PERF_VERSION == 'knowledge-perf-1'


def test_one_sample_per_unit_earliest_version():
    plans = {'p0': plan('p0', hours=0, version_no=0), 'p1': plan('p1', hours=3, version_no=1),
             'p2': plan('p2', hours=6, version_no=2)}
    citations = [cite('p2', version=2), cite('p1', version=2, field='changes'), cite('p0', version=1),
                 cite('p0', version=1, field='counter')]
    outcomes = {'p0': closed(1.0), 'p1': closed(2.0), 'p2': closed(3.0)}
    out = summarize(citations, plans, outcomes)
    assert out['calc_version'] == 'knowledge-perf-1' and out['min_sample'] == 5
    assert out['sample']['count'] == 1                                 # a unit counts once
    assert len(out['rows']) == 1
    row = out['rows'][0]
    assert row['plan_id'] == 'p0' and row['version_no'] == 0           # the earliest plan version that cited it
    assert row['cited_version'] == 1 and row['fields'] == ['counter', 'thesis']
    assert row['unit'] == {'ticker': 'NVDA', 'signal_date': '2026-10-20'}
    assert (row['kind'], row['status'], row['status_text'], row['r']) == ('agent', 'closed', '已平仓', 1.0)
    assert row['fill_date'] == '2026-10-21' and row['outcome_date'] == '2026-10-27'
    # it does not matter in which order the citations arrive
    assert summarize(list(reversed(citations)), plans, outcomes)['rows'][0]['plan_id'] == 'p0'


def test_two_units_two_samples():
    plans = {'a': plan('a'), 'b': plan('b', ticker='AMD'), 'c': plan('c', signal=date(2026, 10, 21))}
    out = summarize([cite('a'), cite('b'), cite('c')], plans, {'a': closed(1.0), 'b': closed(-1.0), 'c': closed(0.5)})
    assert out['sample']['count'] == 3
    assert [(r['unit']['signal_date'], r['unit']['ticker']) for r in out['rows']] \
        == [('2026-10-20', 'AMD'), ('2026-10-20', 'NVDA'), ('2026-10-21', 'NVDA')]


def test_below_min_sample_no_mean():
    plans = {f'p{i}': plan(f'p{i}', ticker=f'T{i}') for i in range(6)}
    outcomes = {f'p{i}': closed(float(i)) for i in range(4)}
    outcomes['p4'] = {'status': 'expired', 'fill_date': None, 'fill_price': None, 'legs': [], 'r': None}
    outcomes['p5'] = {'status': 'pending', 'fill_date': None, 'fill_price': None, 'legs': [], 'r': None}
    out = summarize([cite(f'p{i}') for i in range(6)], plans, outcomes)
    sample = out['sample']
    assert (sample['count'], sample['determined'], sample['expired']) == (6, 4, 1)
    assert sample['r_mean'] is None and sample['r_median'] is None
    assert out['note'] == '样本 6 个（有结果 4 个），少于 5 个不给均值'
    by_status = {r['plan_id']: r for r in out['rows']}
    assert by_status['p4']['status'] == 'expired' and by_status['p4']['outcome_date'] == '2026-10-28'
    assert by_status['p4']['status_text'] == '到期未成交' and by_status['p4']['r'] is None
    assert by_status['p5']['outcome_date'] is None and by_status['p5']['fill_date'] is None


def test_enough_sample_gives_mean_and_note():
    plans = {f'p{i}': plan(f'p{i}', ticker=f'T{i}') for i in range(5)}
    outcomes = {f'p{i}': closed(float(i)) for i in range(5)}                   # 0..4
    out = summarize([cite(f'p{i}') for i in range(5)], plans, outcomes)
    assert out['sample']['determined'] == 5
    assert out['sample']['r_mean'] == 2.0 and out['sample']['r_median'] == 2.0
    assert out['note'] == '描述性统计，样本小且重叠，不代表这条经验有效'
    # same function and threshold as the plan-shadow view
    assert out['sample'] == review.shadow_block(list(outcomes.values()))
    assert out['min_sample'] == review.SHADOW_MIN_SAMPLE


def test_reviews_cited_separate():
    plans = {'a': plan('a')}
    review_only = {**cite(None), 'review_id': 11}
    out = summarize([cite('a'), review_only, {**review_only, 'review_id': 12}], plans, {'a': closed(1.0)},
                    reviews_cited=2)
    assert out['reviews_cited'] == 2
    assert out['sample']['count'] == 1                                         # review-only citations are no sample
    assert summarize([review_only], {}, {}, reviews_cited=1)['sample']['count'] == 0
    assert summarize([], {}, {})['rows'] == [] and summarize([], {}, {})['by_version'] == []


def test_outcome_date():
    late = {'status': 'closed', 'fill_date': date(2026, 10, 21), 'fill_price': D('100'), 'r': 1.0,
            'legs': [{'fraction': 0.5, 'exit_date': '2026-10-23', 'exit_price': D('110'), 'reason': 'target1'},
                     {'fraction': 0.5, 'exit_date': '2026-10-27', 'exit_price': D('104'), 'reason': 'trail'}]}
    expired = {'status': 'expired', 'fill_date': None, 'fill_price': None, 'legs': [], 'r': None}
    opened = {'status': 'open', 'fill_date': date(2026, 10, 21), 'fill_price': D('100'), 'r': 0.3,
              'legs': [{'fraction': 0.5, 'exit_date': '2026-10-23', 'exit_price': D('110'), 'reason': 'target1'}]}
    plans = {'a': plan('a'), 'b': plan('b', ticker='AMD', valid_until=date(2026, 10, 30)),
             'c': plan('c', ticker='MSFT')}
    out = summarize([cite('a'), cite('b'), cite('c')], plans, {'a': late, 'b': expired, 'c': opened})
    rows = {r['plan_id']: r for r in out['rows']}
    assert rows['a']['outcome_date'] == '2026-10-27'                          # closed: the last leg
    assert rows['b']['outcome_date'] == '2026-10-30'                          # expired: valid_until
    assert rows['c']['outcome_date'] is None and rows['c']['status_text'] == '未平仓'


def test_missing_outcome_is_missing_not_zero():
    out = summarize([cite('a')], {'a': plan('a')}, {})
    row = out['rows'][0]
    assert (row['status'], row['r']) == ('missing', None)
    assert out['sample']['count'] == 1 and out['sample']['determined'] == 0


def test_by_version_counts():
    plans = {'a0': plan('a0', hours=0), 'a1': plan('a1', hours=2, version_no=1),
             'b0': plan('b0', ticker='AMD'), 'c0': plan('c0', ticker='MSFT')}
    citations = [cite('a0', version=1), cite('a1', version=1), cite('a1', version=2, field='changes'),
                 cite('b0', version=2), cite('c0', version=2), cite('c0', version=2, field='risks')]
    out = summarize(citations, plans, {})
    assert out['by_version'] == [{'version': 1, 'plans': 2, 'units': 1}, {'version': 2, 'plans': 3, 'units': 3}]
