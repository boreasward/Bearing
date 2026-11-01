"""Chart payload for the instrument view: split-adjusted bars with simple moving averages."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import investment_assistant.views.chart as chart
from investment_assistant.views.chart import KEEP, READ_LIMIT, WINDOWS, build


def _bars(n, *, start=date(2026, 1, 1)):
    return [{'price_date': start + timedelta(days=i), 'open': i + 1.0, 'high': i + 1.0,
             'low': i + 1.0, 'close': float(i + 1), 'volume': 1000 + i} for i in range(n)]


def test_constants():
    assert (READ_LIMIT, KEEP, WINDOWS) == (450, 250, (20, 50, 200))


def test_ma_values_are_simple_close_averages():
    out = build(_bars(300))
    last = out['bars'][-1]
    assert last['ma20'] == sum(range(281, 301)) / 20
    assert last['ma50'] == sum(range(251, 301)) / 50
    assert last['ma200'] == sum(range(101, 301)) / 200


def test_keeps_last_250_but_uses_full_history_for_ma():
    out = build(_bars(450))
    assert len(out['bars']) == KEEP
    # The first returned bar is input #201; its ma200 needs the 200 bars dropped from the payload.
    assert out['bars'][0]['price_date'] == date(2026, 1, 1) + timedelta(days=200)
    assert out['bars'][0]['ma200'] is not None
    assert out['bars'][-1]['price_date'] == date(2026, 1, 1) + timedelta(days=449)
    assert out['adjustment'] == 'split_only'


def test_chart_short_history_keeps_nulls():
    out = build(_bars(120))
    assert len(out['bars']) == 120
    assert all(bar['ma200'] is None for bar in out['bars'])
    assert all(bar['ma50'] is None for bar in out['bars'][:49])
    assert out['bars'][49]['ma50'] is not None
    candle_keys = {'price_date', 'open', 'high', 'low', 'close', 'volume', 'ma20', 'ma50', 'ma200'}
    assert candle_keys <= set(out['bars'][0])


def test_empty_bars():
    assert build([]) == {'bars': [], 'adjustment': 'split_only'}


# ---------------------------------------------------------------- annotate (v2.6 spec §9.3)

def _weekday_bars(n=20, start=date(2026, 10, 5)):
    """n consecutive weekdays from Monday 2026-10-05 (no exchange holidays in the range)."""
    days, day = [], start
    while len(days) < n:
        if day.isoweekday() < 6:
            days.append(day)
        day += timedelta(days=1)
    return [{'price_date': d, 'open': 100.0, 'high': 101.0, 'low': 99.0, 'close': 100.0, 'volume': 1000}
            for d in days]


def _candidate(cid, day, rule_id, action):
    return {'id': cid, 'signal_date': day, 'rule_id': rule_id, 'action': action}


def _plan(pid, *, valid_from, valid_until, kind='rule', plan_version='plan-1', level=100, status='ok'):
    return {'id': pid, 'status': status, 'kind': kind, 'plan_version': plan_version, 'entry_low': Decimal(level - 1),
            'entry_high': Decimal(level + 1), 'stop': Decimal(level - 5), 'target1': Decimal(level + 8),
            'valid_from': valid_from, 'valid_until': valid_until, 'created_at': datetime(2026, 10, 1, tzinfo=UTC)}


def _trade(tid, side, executed_at, qty=10, voided_at=None):
    return {'id': tid, 'side': side, 'quantity': qty, 'executed_at': executed_at, 'voided_at': voided_at}


def annotate(bars=None, **kwargs):
    base = dict(bars=bars if bars is not None else _weekday_bars(), candidates=[], plans=[], versions={}, trades=[])
    base.update(kwargs)
    return chart.annotate(**base)


def test_max_plans_constant():
    assert chart.MAX_PLANS == 12


def test_annotate_empty():
    assert annotate() == {'markers': [], 'segments': [], 'omitted': 0}
    assert annotate(bars=[]) == {'markers': [], 'segments': [], 'omitted': 0}


def test_annotate_markers_sorted_and_merged():
    d = lambda n: date(2026, 10, n)                                    # noqa: E731
    out = annotate(
        candidates=[_candidate('c3', d(13), 'trend_break', 'sell'),
                    _candidate('c1', d(8), 'trend_reclaim', 'buy'), _candidate('c2', d(8), 'volume_breakout', 'buy'),
                    _candidate('c4', d(14), 'trend_reclaim', 'buy'), _candidate('c5', d(14), 'trend_break', 'sell'),
                    _candidate('c6', d(15), 'new_filing', 'watch'),
                    _candidate('c7', date(2026, 9, 1), 'trend_reclaim', 'buy')],          # before the window
        trades=[_trade('t1', 'buy', datetime(2026, 10, 9, 15, 0, tzinfo=UTC), qty=Decimal('10'))])
    markers = out['markers']
    assert [m['time'] for m in markers] == sorted(m['time'] for m in markers)
    assert markers[0] == {'time': '2026-10-08', 'position': 'belowBar', 'shape': 'arrowUp', 'text': '回踩+突破',
                          'kind': 'candidate', 'refs': ['c1', 'c2']}
    by_time = {(m['time'], m['kind']): m for m in markers}
    assert by_time[('2026-10-13', 'candidate')]['shape'] == 'arrowDown'
    assert by_time[('2026-10-13', 'candidate')]['position'] == 'aboveBar'
    assert by_time[('2026-10-13', 'candidate')]['text'] == '破位'
    conflict = by_time[('2026-10-14', 'candidate')]
    assert (conflict['shape'], conflict['position'], conflict['text']) == ('circle', 'aboveBar', '回踩+破位')
    assert by_time[('2026-10-15', 'candidate')]['shape'] == 'circle' and by_time[('2026-10-15', 'candidate')]['text'] == '财报'
    trade = by_time[('2026-10-09', 'trade')]
    assert (trade['text'], trade['position'], trade['refs']) == ('B 10', 'belowBar', ['t1'])
    assert len(markers) == 5                                                           # the 2026-09-01 candidate is out


def test_annotate_same_day_candidate_and_trade_both_kept_in_order():
    out = annotate(candidates=[_candidate('c1', date(2026, 10, 8), 'trend_reclaim', 'buy')],
                   trades=[_trade('t1', 'buy', datetime(2026, 10, 8, 15, 0, tzinfo=UTC))])
    assert [(m['time'], m['kind']) for m in out['markers']] == [('2026-10-08', 'candidate'), ('2026-10-08', 'trade')]


def test_annotate_weekend_fill_snaps_forward():
    saturday = datetime(2026, 10, 10, 14, 0, tzinfo=UTC)
    out = annotate(trades=[_trade('t1', 'sell', saturday, qty=5)])
    [marker] = out['markers']
    assert marker['time'] == '2026-10-12' and marker['text'] == 'S 5' and marker['position'] == 'aboveBar'
    # New York date, not UTC date: 01:00 UTC on the 13th is still the evening of the 12th
    late = annotate(trades=[_trade('t2', 'buy', datetime(2026, 10, 13, 1, 0, tzinfo=UTC))])
    assert late['markers'][0]['time'] == '2026-10-12'
    # a fill after the last bar, or before the first, has no bar to sit on
    assert annotate(trades=[_trade('t3', 'buy', datetime(2026, 12, 1, 15, 0, tzinfo=UTC))])['markers'] == []
    assert annotate(trades=[_trade('t4', 'buy', datetime(2026, 9, 1, 15, 0, tzinfo=UTC))])['markers'] == []


def test_annotate_voided_trade_ignored():
    out = annotate(trades=[_trade('t1', 'buy', datetime(2026, 10, 8, 15, 0, tzinfo=UTC),
                                  voided_at=datetime(2026, 10, 9, tzinfo=UTC))])
    assert out['markers'] == []


def test_annotate_window_and_validity_clip():
    bars = _weekday_bars()                                             # 2026-10-05 .. 2026-11-30? 20 weekdays -> 10-30
    last = bars[-1]['price_date']
    assert last == date(2026, 10, 30)
    straddling = _plan('p-start', valid_from=date(2026, 10, 1), valid_until=date(2026, 10, 8))
    inside = _plan('p-inside', valid_from=date(2026, 10, 12), valid_until=date(2026, 10, 14))
    runs_past = _plan('p-end', valid_from=date(2026, 10, 28), valid_until=date(2026, 11, 3))
    before = _plan('p-before', valid_from=date(2026, 9, 20), valid_until=date(2026, 10, 2))
    after = _plan('p-after', valid_from=date(2026, 11, 2), valid_until=date(2026, 11, 5))
    out = annotate(bars=bars, plans=[straddling, inside, runs_past, before, after])
    spans = {}
    for seg in out['segments']:
        spans.setdefault(seg['plan_id'], set()).add((seg['from'], seg['to']))
    assert spans == {'p-start': {('2026-10-05', '2026-10-08')}, 'p-inside': {('2026-10-12', '2026-10-14')},
                     'p-end': {('2026-10-28', '2026-10-30')}}
    assert out['omitted'] == 0
    one = [s for s in out['segments'] if s['plan_id'] == 'p-inside']
    assert [s['level'] for s in one] == ['entry_high', 'entry_low', 'stop', 'target1']
    assert [s['value'] for s in one] == [101.0, 99.0, 95.0, 108.0]
    assert all(s['kind'] == 'rule' and s['label'] == '规则' for s in one)


def test_annotate_max_plans():
    plans = [_plan(f'p{n:02d}', valid_from=date(2026, 10, 5), valid_until=date(2026, 10, 5) + timedelta(days=n))
             for n in range(14)]
    out = annotate(plans=plans)
    drawn = {s['plan_id'] for s in out['segments']}
    assert len(out['segments']) == 12 * 4 and len(drawn) == 12 and out['omitted'] == 2
    assert drawn == {f'p{n:02d}' for n in range(2, 14)}               # the two earliest-ending are dropped


def test_annotate_kinds_and_labels():
    own = _plan('own1', valid_from=date(2026, 10, 12), valid_until=date(2026, 10, 14), kind='own')
    agent = _plan('ag1', valid_from=date(2026, 10, 12), valid_until=date(2026, 10, 14), kind='agent',
                  plan_version='agent-2')
    legacy = _plan('ag0', valid_from=date(2026, 10, 12), valid_until=date(2026, 10, 14), kind='agent',
                   plan_version='agent-1')
    unversioned = _plan('ag9', valid_from=date(2026, 10, 12), valid_until=date(2026, 10, 14), kind='agent',
                        plan_version='agent-2')
    out = annotate(plans=[own, agent, legacy, unversioned], versions={'ag1': {'version_no': 2}})
    label = {s['plan_id']: (s['kind'], s['label']) for s in out['segments']}
    assert label == {'own1': ('own', '自定'), 'ag1': ('agent', 'Agent v2'), 'ag0': ('agent', 'Agent'),
                     'ag9': ('agent', 'Agent')}


def test_annotate_agent_label_uses_version_no():
    plan = _plan('ag1', valid_from=date(2026, 10, 12), valid_until=date(2026, 10, 14), kind='agent',
                 plan_version='agent-2')
    out = annotate(plans=[plan], versions={'ag1': {'version_no': 0}})
    assert {s['label'] for s in out['segments']} == {'Agent v0'}
    out = annotate(plans=[{**plan, 'id': 'ag7'}], versions={'ag7': {'version_no': 3}})
    assert {s['label'] for s in out['segments']} == {'Agent v3'}


def test_annotate_segments_carry_the_plan_status():
    ok = _plan('p-ok', valid_from=date(2026, 10, 12), valid_until=date(2026, 10, 14))
    voided = _plan('p-void', valid_from=date(2026, 10, 12), valid_until=date(2026, 10, 14), status='invalid')
    out = annotate(plans=[ok, voided])
    status = {}
    for seg in out['segments']:
        status.setdefault(seg['plan_id'], set()).add(seg['status'])
    assert status == {'p-ok': {'ok'}, 'p-void': {'invalid'}}
    legacy = {k: v for k, v in ok.items() if k != 'status'}                 # a row without the column reads as ok
    assert {s['status'] for s in annotate(plans=[legacy])['segments']} == {'ok'}
