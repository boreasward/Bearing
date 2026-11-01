import investment_assistant.analysis.repo as analysis_repo
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from investment_assistant.analysis import synthesis
from investment_assistant.analysis.rules import Candidate

T = date(2026, 10, 22)  # Thursday


def cand(rule_id, action):
    return {'id': uuid.uuid4(), 'rule_id': rule_id, 'action': action, 'rule_version': '1', 'inputs': {}}


def unit(*cands, holding=False, now=datetime(2026, 10, 23, 1, 0, tzinfo=UTC)):
    return synthesis.synthesize('NVDA', T, list(cands), holding=holding, now=now)


def test_buy_and_sell_together_is_a_conflict_defaulting_to_watch():
    u = unit(cand('trend_reclaim', 'buy'), cand('trend_break', 'sell'), holding=True)
    assert u['proposed_action'] == 'watch' and u['labels'] == ['conflict']


def test_sell_without_position_is_avoid():
    u = unit(cand('trend_break', 'sell'))
    assert u['proposed_action'] == 'sell' and u['labels'] == ['avoid']


def test_sell_with_position_has_no_avoid_label():
    assert unit(cand('trend_break', 'sell'), holding=True)['labels'] == []


def test_same_direction_candidates_merge():
    u = unit(cand('trend_reclaim', 'buy'), cand('volume_breakout', 'buy'), cand('new_filing', 'watch'))
    assert u['proposed_action'] == 'buy'
    assert u['rule_ids'] == ['trend_reclaim', 'volume_breakout', 'new_filing']
    assert len(u['candidate_ids']) == 3


def test_watch_only():
    assert unit(cand('abnormal_move', 'watch'))['proposed_action'] == 'watch'


@pytest.mark.parametrize('now, passed', [
    (datetime(2026, 10, 23, 13, 29, tzinfo=UTC), False),  # Fri 09:29 NY, before T+1 open
    (datetime(2026, 10, 23, 13, 30, tzinfo=UTC), True),   # Fri 09:30 NY
    (datetime(2026, 10, 27, 1, 0, tzinfo=UTC), True),
])
def test_entry_passed_after_next_session_open(now, passed):
    assert unit(cand('trend_reclaim', 'buy'), now=now)['entry_passed'] is passed


def test_empty_unit_is_rejected():
    with pytest.raises(ValueError):
        unit()


# --- persistence (disposable schema) ---

def rule_cand(close=101.0):
    return Candidate('trend_reclaim', 'buy', {'signal_date': T.isoformat(), 'bar_ids': [1], 'values': {'close': close}})


def test_store_candidate_is_idempotent(workbench_db):
    first = analysis_repo.store_candidate(workbench_db, 'nvda', T, rule_cand(), run_id='r1')
    again = analysis_repo.store_candidate(workbench_db, 'NVDA', T, rule_cand(), run_id='r2')
    assert first['status'] == 'inserted' and again == {'status': 'existing', 'id': first['id']}
    assert workbench_db.execute('SELECT count(*), min(run_id) FROM signal_candidates').fetchone() == (1, 'r1')


def test_store_candidate_never_overwrites_different_inputs(workbench_db):
    first = analysis_repo.store_candidate(workbench_db, 'NVDA', T, rule_cand(), run_id='r1')
    clash = analysis_repo.store_candidate(workbench_db, 'NVDA', T, rule_cand(close=99.0), run_id='r2')
    assert clash['status'] == 'conflict' and clash['id'] == first['id']
    stored = workbench_db.execute('SELECT inputs FROM signal_candidates').fetchone()[0]
    assert stored['values']['close'] == 101.0


def test_open_units_exclude_decided_ones(workbench_db):
    a = analysis_repo.store_candidate(workbench_db, 'NVDA', T, rule_cand(), run_id='r')
    analysis_repo.store_candidate(workbench_db, 'MU', T, rule_cand(), run_id='r')
    workbench_db.execute(
        "INSERT INTO signal_decisions (request_id, request_hash, ticker, signal_date, candidate_ids, proposed_action, "
        "final_action, decision, reason) VALUES (%s, 'h', 'MU', %s, '{}', 'buy', 'buy', 'accept', 'ok')",
        [uuid.uuid4(), T])
    units = analysis_repo.open_candidate_groups(workbench_db, since=T - timedelta(days=7))
    assert [(u['ticker'], u['signal_date']) for u in units] == [('NVDA', T)]
    assert [c['id'] for c in units[0]['candidates']] == [a['id']]


def test_unit_has_no_market_status():
    u = synthesis.synthesize('NVDA', T, [cand('trend_reclaim', 'buy')],
                             holding=False, now=datetime(2026, 10, 23, 1, 0, tzinfo=UTC))
    assert 'market_status' not in u
