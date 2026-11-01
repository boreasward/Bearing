"""Three-track comparison pure functions (P4 design §4): statuses, R, summary."""
import investment_assistant.review.weekly as review_weekly
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import investment_assistant.execution.repo_legacy as execution_repo_legacy
import uuid
from datetime import date, timedelta
from decimal import Decimal as D

from investment_assistant.review import tracks

S = date(2026, 10, 22)
PLAN = {'id': uuid.uuid4(), 'kind': 'rule', 'stop': D('95'), 'signal_date': S,
        'valid_from': S + timedelta(days=1), 'valid_until': S + timedelta(days=3)}


def _decision(action='buy', plan_id='p1'):
    return {'id': uuid.uuid4(), 'final_action': action, 'plan_id': plan_id}


def _run(status='ok', reason=None, plan_id=None):
    return {'id': uuid.uuid4(), 'status': status, 'reason': reason, 'plan_id': plan_id}


def _buy(tid='b1'):
    return {'id': tid, 'side': 'buy', 'quantity': D(10), 'price': D('100'), 'executed_at': S + timedelta(days=1)}


def _seg(tid='b1', qty=D(10), cost=D('100'), exit_=D('110')):
    return {'buy_trade_id': tid, 'quantity': qty, 'entry_cost_per_share': cost, 'exit_price': exit_,
            'opened_at': S + timedelta(days=1), 'closed_at': S + timedelta(days=3)}


def _lot(tid='b1', qty=D(10), cost=D('100')):
    return {'buy_trade_id': tid, 'quantity': qty, 'cost_per_share': cost, 'opened_at': S + timedelta(days=1)}


def _outcome(status, r=None):
    return {'status': status, 'r': r}


# --- rule track ---

def test_rule_missing_without_plan():
    assert tracks.plan_track(None, None) == {'plan_id': None, 'status': 'missing', 'r': None}
    assert tracks.plan_track(None, _outcome('closed', 1.0)) == {'plan_id': None, 'status': 'missing', 'r': None}


def test_rule_invalid_plan_is_zero():
    out = tracks.plan_track({**PLAN, 'status': 'invalid'}, _outcome('pending'))
    assert out == {'plan_id': PLAN['id'], 'status': 'not_executable', 'r': 0.0}


def test_rule_pending_has_no_r():
    out = tracks.plan_track(PLAN, _outcome('pending', None))
    assert out == {'plan_id': PLAN['id'], 'status': 'pending', 'r': None}


def test_rule_expired_is_zero():
    out = tracks.plan_track(PLAN, _outcome('expired', None))
    assert out == {'plan_id': PLAN['id'], 'status': 'expired', 'r': 0.0}


def test_rule_open_and_closed_take_simulated_r():
    assert tracks.plan_track(PLAN, _outcome('open', 0.5)) == {'plan_id': PLAN['id'], 'status': 'open', 'r': 0.5}
    assert tracks.plan_track(PLAN, _outcome('closed', 1.5)) == {'plan_id': PLAN['id'], 'status': 'closed', 'r': 1.5}


# --- agent track ---

def test_agent_pass_is_zero():
    out = tracks.agent_track(_run('pass'), None, None)
    assert out['status'] == 'pass' and out['r'] == 0.0
    assert out['plan_id'] is None


def test_agent_rejected_is_missing_with_reason():
    out = tracks.agent_track(_run('rejected', 'invalid_citation'), None, None)
    assert out == {'run_id': out['run_id'], 'run_status': 'rejected', 'run_reason': 'invalid_citation',
                   'plan_id': None, 'status': 'missing', 'r': None, 'version_no': None, 'version_basis': None}


def test_agent_ok_uses_agent_plan_outcome():
    plan = {**PLAN, 'kind': 'agent'}
    out = tracks.agent_track(_run('ok', plan_id=plan['id']), plan, _outcome('closed', 2.0))
    assert out == {'run_id': out['run_id'], 'run_status': 'ok', 'run_reason': None,
                   'plan_id': plan['id'], 'status': 'closed', 'r': 2.0, 'version_no': None, 'version_basis': None}


# --- actual track ---

def test_ignored_unit_is_not_executed_zero():
    out = tracks.actual_track(decision=_decision('ignore'), plan=PLAN, buys=[], segments=[], open_lots=[],
                              last_close=None, splits=[], session=S + timedelta(days=5),
                              unit_valid_until=PLAN['valid_until'])
    assert out['status'] == 'not_executed' and out['r'] == 0.0 and out['open'] is False


def test_buy_decision_without_fill_pending_until_validity_ends():
    until = PLAN['valid_until']
    pending = tracks.actual_track(decision=_decision('buy'), plan=PLAN, buys=[], segments=[], open_lots=[],
                                  last_close=None, splits=[], session=until - timedelta(days=1),
                                  unit_valid_until=until)
    assert pending['status'] == 'pending' and pending['r'] is None
    expired = tracks.actual_track(decision=_decision('buy'), plan=PLAN, buys=[], segments=[], open_lots=[],
                                  last_close=None, splits=[], session=until + timedelta(days=1),
                                  unit_valid_until=until)
    assert expired['status'] == 'not_executed' and expired['r'] == 0.0


def test_undecided_unit_pending_then_not_executed():
    until = PLAN['valid_until']
    pending = tracks.actual_track(decision=None, plan=PLAN, buys=[], segments=[], open_lots=[],
                                  last_close=None, splits=[], session=until - timedelta(days=1),
                                  unit_valid_until=until)
    assert pending['status'] == 'pending' and pending['final_action'] is None
    gone = tracks.actual_track(decision=None, plan=PLAN, buys=[], segments=[], open_lots=[],
                               last_close=None, splits=[], session=until + timedelta(days=1),
                               unit_valid_until=until)
    assert gone['status'] == 'not_executed' and gone['r'] == 0.0


def test_closed_r_priced_at_chosen_plan_stop():
    out = tracks.actual_track(decision=_decision('buy'), plan={**PLAN, 'kind': 'agent'}, buys=[_buy()],
                              segments=[_seg()], open_lots=[], last_close=None, splits=[],
                              session=S + timedelta(days=5), unit_valid_until=PLAN['valid_until'])
    assert out['plan_kind'] == 'agent' and out['r'] == 2.0     # (110-100)/(100-95)
    assert out['status'] == 'closed' and out['open'] is False
    assert out['buy_trade_ids'] == ['b1']


def test_partial_close_weights_open_lot():
    out = tracks.actual_track(decision=_decision('buy'), plan=PLAN,
                              buys=[_buy()],
                              segments=[_seg(qty=D(50), exit_=D('110'))],
                              open_lots=[_lot(qty=D(50), cost=D('100'))],
                              last_close=D('105'), splits=[], session=S + timedelta(days=5),
                              unit_valid_until=PLAN['valid_until'])
    assert out['r'] == 1.5      # (50x2R + 50x1R) / 100
    assert out['status'] == 'open' and out['open'] is True


def test_split_between_buy_and_now():
    splits = [(S + timedelta(days=2), D(2))]
    out = tracks.actual_track(decision=_decision('buy'), plan=PLAN, buys=[_buy()], segments=[],
                              open_lots=[_lot(qty=D(20), cost=D('50'))], last_close=D('52.5'),
                              splits=splits, session=S + timedelta(days=5),
                              unit_valid_until=PLAN['valid_until'])
    assert out['r'] == 1.0      # (52.5-50)/(50-47.5)：拆股后浮动 R 不变


def test_trade_without_plan_is_no_plan():
    out = tracks.actual_track(decision=_decision('buy', plan_id=None), plan=None, buys=[_buy()],
                              segments=[_seg()], open_lots=[], last_close=None, splits=[],
                              session=S + timedelta(days=5), unit_valid_until=PLAN['valid_until'])
    assert out['status'] == 'no_plan' and out['r'] is None and out['plan_kind'] is None


# --- summarize ---

def _row(pool='core', rule=None, agent=None, actual=None):
    return {'ticker': 'X', 'signal_date': S, 'pool': pool,
            'rule': rule or {'plan_id': None, 'status': 'missing', 'r': None},
            'agent': agent or {'run_id': None, 'run_status': None, 'run_reason': None,
                               'plan_id': None, 'status': 'missing', 'r': None},
            'actual': actual or {'decision_id': None, 'final_action': None, 'plan_id': None,
                                 'plan_kind': None, 'buy_trade_ids': [], 'status': 'missing',
                                 'r': None, 'open': False}}


def test_summarize_excludes_none_from_mean():
    rows = [_row(rule={'plan_id': 'a', 'status': 'pending', 'r': None}),
            _row(rule={'plan_id': 'b', 'status': 'closed', 'r': 2.0})]
    stats = tracks.summarize(rows)['all']['rule']
    assert stats['units'] == 2 and stats['determined'] == 1 and stats['pending'] == 1
    assert stats['mean'] == 2.0 and stats['median'] == 2.0 and stats['sum'] == 2.0 and stats['wins'] == 1


def test_summarize_by_pool_and_plan_kind():
    rows = [_row(pool='core',
                 actual={'decision_id': 'd', 'final_action': 'buy', 'plan_id': 'p', 'plan_kind': 'agent',
                         'buy_trade_ids': ['b'], 'status': 'closed', 'r': 1.0, 'open': False}),
            _row(pool='extended',
                 actual={'decision_id': None, 'final_action': 'ignore', 'plan_id': None, 'plan_kind': None,
                         'buy_trade_ids': [], 'status': 'not_executed', 'r': 0.0, 'open': False})]
    out = tracks.summarize(rows)
    assert out['all']['actual']['units'] == 2 and out['core']['actual']['units'] == 1
    assert out['all']['actual']['by_plan_kind'] == {'agent': 1, 'none': 1}
    assert out['extended']['actual']['by_status'] == {'not_executed': 1}


def test_status_text_covers_every_status():
    assert set(tracks.TRACK_STATUS_TEXT) == {'missing', 'not_executable', 'pending', 'expired',
                                             'open', 'closed', 'pass', 'not_executed', 'no_plan'}
    assert tracks.TRACKS_VERSION == 'tracks-2'      # v2.9: the service builds tracks-2 rows; tracks-1 stays in old snapshots


# --- v2.5 §9.2: which Agent version the agent track takes ---

def test_agent_track_carries_version_keys():
    plan = {**PLAN, 'kind': 'agent'}
    run = _run('ok', plan_id=plan['id'])
    base = tracks.agent_track(run, plan, _outcome('closed', 2.0))
    out = tracks.agent_track(run, plan, _outcome('closed', 2.0), version_no=1, version_basis='decision')
    assert out == {**base, 'version_no': 1, 'version_basis': 'decision'}


# --- v2.9 tracks-2 (spec D27) ---

def _ver(tag, version_no, plan_id=None):
    return {'plan_id': plan_id or f'plan-{tag}', 'version_no': version_no, 'ticker': 'NVDA', 'signal_date': S}


def _fill(tid, plan_id, day=1, voided=False):
    return {'id': tid, 'side': 'buy', 'plan_id': plan_id, 'executed_at': S + timedelta(days=day),
            'voided_at': S if voided else None}


UNIT = {'ticker': 'NVDA', 'signal_date': S}


def test_tracks_versions_and_text():
    assert (tracks.TRACKS_V1, tracks.TRACKS_V2) == ('tracks-1', 'tracks-2')
    assert tracks.TRACKS_VERSION == tracks.TRACKS_V2
    assert tracks.TRACK_STATUS_TEXT_V2['merged'] == '并入其他链' and tracks.TRACK_STATUS_TEXT_V2['voided']
    assert {k: v for k, v in tracks.TRACK_STATUS_TEXT_V2.items() if k in tracks.TRACK_STATUS_TEXT} == \
        tracks.TRACK_STATUS_TEXT
    assert tracks.VERSION_BASES == ('decision', 'fill', 'first', 'merged')


def test_voided_shadow_outcome_scores_zero():
    out = tracks.plan_track(PLAN, {'status': 'voided', 'r': None})
    assert out == {'plan_id': PLAN['id'], 'status': 'voided', 'r': 0.0}


def test_version_basis_fill():
    versions = [_ver('a', 0), _ver('b', 1), _ver('c', 2)]
    fills = [_fill('t2', 'plan-c', day=2), _fill('t1', 'plan-b', day=1), _fill('rule', 'rule-plan', day=0)]
    pick = tracks.agent_pick(UNIT, legacy_decision=None, chain_buys=fills, versions=versions, merged=False)
    assert pick['version_basis'] == 'fill' and pick['version_no'] == 1 and pick['plan'] is versions[1]
    assert pick['plan_id'] == 'plan-b'                               # the first fill that follows one of the chain's versions
    voided = [_fill('t1', 'plan-b', voided=True), _fill('t2', 'plan-c', day=2)]
    assert tracks.agent_pick(UNIT, legacy_decision=None, chain_buys=voided, versions=versions,
                             merged=False)['version_no'] == 2


def test_version_basis_legacy_decision():
    versions = [_ver('a', 0), _ver('b', 1)]
    decision = {'id': 1, 'agent_plan_id': 'plan-b'}
    fills = [_fill('t1', 'plan-a')]
    pick = tracks.agent_pick(UNIT, legacy_decision=decision, chain_buys=fills, versions=versions, merged=False)
    assert (pick['version_basis'], pick['version_no']) == ('decision', 1)       # the old decision wins over the fill
    beside = tracks.agent_pick(UNIT, legacy_decision={'id': 1, 'agent_plan_id': 'agent-1-plan'}, chain_buys=[],
                               versions=versions, merged=False)
    assert beside == {'plan': None, 'plan_id': 'agent-1-plan', 'version_no': None, 'version_basis': 'decision'}
    no_agent_id = tracks.agent_pick(UNIT, legacy_decision={'id': 1, 'agent_plan_id': None}, chain_buys=[],
                                    versions=versions, merged=False)
    assert no_agent_id['version_basis'] == 'first'


def test_version_basis_first():
    versions = [_ver('b', 1), _ver('a', 0)]
    pick = tracks.agent_pick(UNIT, legacy_decision=None, chain_buys=[], versions=versions, merged=False)
    assert (pick['version_basis'], pick['version_no'], pick['plan_id']) == ('first', 0, 'plan-a')
    unrelated = tracks.agent_pick(UNIT, legacy_decision=None, chain_buys=[_fill('t1', 'rule-plan')],
                                  versions=versions, merged=False)
    assert unrelated['version_basis'] == 'first'                     # a fill linked elsewhere says nothing about versions
    none = tracks.agent_pick(UNIT, legacy_decision=None, chain_buys=[], versions=[], merged=False)
    assert none == {'plan': None, 'plan_id': None, 'version_no': None, 'version_basis': None}


def test_merged_unit_excluded_from_mean():
    pick = tracks.agent_pick(UNIT, legacy_decision=None, chain_buys=[], versions=[], merged=True)
    assert pick['version_basis'] == 'merged' and pick['plan'] is None
    # a unit that opened its own chain is not merged (D22 / R17)
    own = tracks.agent_pick(UNIT, legacy_decision=None, chain_buys=[], versions=[_ver('a', 0)], merged=True)
    assert own['version_basis'] == 'first'
    merged = tracks.merged_agent_track()
    assert merged['status'] == 'merged' and merged['r'] is None and merged['version_basis'] == 'merged'

    def row(agent_r, status='closed'):
        agent = merged if status == 'merged' else {'status': status, 'r': agent_r}
        return {'pool': 'core', 'rule': {'status': 'closed', 'r': 1.0}, 'agent': agent,
                'actual': {'status': 'not_executed', 'r': 0.0, 'plan_kind': None}}
    rows = [row(2.0), row(None, 'merged'), row(4.0)]
    out = tracks.summarize_v2(rows)
    agent = out['all']['agent']
    assert (agent['units'], agent['determined'], agent['mean'], agent['merged']) == (3, 2, 3.0, 1)
    assert out['all']['rule']['merged'] == 0 and out['all']['actual']['by_plan_kind'] == {'none': 3}
    assert 'merged' not in tracks.summarize(rows)['all']['agent']    # tracks-1 summaries keep their shape


def _plan_row(pid='p1', kind='agent', stop=95):
    return {'id': pid, 'kind': kind, 'stop': D(stop), 'signal_date': S}


def _actual_v2(**over):
    kwargs = dict(plans_by_trade={}, buys=[], segments=[], open_lots=[], last_close=None, splits=[],
                  session=S + timedelta(days=5), unit_valid_until=S + timedelta(days=3))
    kwargs.update(over)
    return tracks.actual_track_v2(**kwargs)


def test_actual_track_from_linked_plan():
    buy = {**_buy('b1'), 'plan_id': 'p1'}
    out = _actual_v2(plans_by_trade={'b1': _plan_row()}, buys=[buy], segments=[_seg('b1')])
    assert out['status'] == 'closed' and out['plan_kind'] == 'agent' and out['plan_id'] == 'p1'
    assert out['r'] == 2.0                                            # (110 - 100) / (100 - 95)
    assert out['buy_trade_ids'] == ['b1'] and out['open'] is False
    assert out['decision_id'] is None and out['final_action'] is None
    # the plan and kind come from the first buy that has one
    second = {**_buy('b2'), 'plan_id': 'p2', 'executed_at': S + timedelta(days=2)}
    out = _actual_v2(plans_by_trade={'b2': _plan_row('p2', 'own', 90), 'b1': _plan_row('p1', 'rule', 95)},
                     buys=[second, buy], segments=[_seg('b1')])
    assert out['plan_kind'] == 'rule' and out['plan_id'] == 'p1'
    open_pos = _actual_v2(plans_by_trade={'b1': _plan_row()}, buys=[buy], open_lots=[_lot('b1')],
                          last_close=D('105'))
    assert open_pos['status'] == 'open' and open_pos['open'] is True and open_pos['r'] == 1.0


def test_not_executed_without_fills():
    gone = _actual_v2()
    assert gone['status'] == 'not_executed' and gone['r'] == 0.0 and gone['plan_id'] is None
    pending = _actual_v2(session=S + timedelta(days=2))
    assert pending['status'] == 'pending' and pending['r'] is None
    no_validity = _actual_v2(unit_valid_until=None)
    assert no_validity['status'] == 'not_executed'
    rejected = _actual_v2(legacy_decision={'id': 1, 'final_action': 'ignore'}, session=S + timedelta(days=2))
    assert rejected['status'] == 'not_executed' and rejected['final_action'] == 'ignore' and rejected['decision_id'] == 1
    accepted = _actual_v2(legacy_decision={'id': 1, 'final_action': 'buy'}, session=S + timedelta(days=2))
    assert accepted['status'] == 'pending'


def test_no_plan_buy():
    out = _actual_v2(buys=[_buy('b1')], segments=[_seg('b1')])
    assert out['status'] == 'no_plan' and out['r'] is None and out['plan_kind'] is None and out['plan_id'] is None
    assert out['buy_trade_ids'] == ['b1']
    unresolved = _actual_v2(buys=[_buy('b1')], plans_by_trade={'b1': None})
    assert unresolved['status'] == 'no_plan'


# SQL (disposable schema): review_weekly._track_rows over real rows.

import pytest  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

import investment_assistant.core.tx as service
from investment_assistant.execution import simulation as plan_shadow# noqa: E402

NOW = datetime(2026, 10, 28, 1, 0, tzinfo=UTC)
SESSION = date(2026, 10, 27)
NEW_KEYS = {'version_no', 'version_basis'}


def _bars(conn, ticker, rows):
    for day, o, h, l, c in rows:
        conn.execute('INSERT INTO price_bars (ticker, price_date, open, high, low, close, volume, adj_close, '
                     'revision, fetched_at) VALUES (%s, %s, %s, %s, %s, %s, 1000, %s, 1, %s)',
                     [ticker, day, o, h, l, c, c, NOW - timedelta(days=10)])


def _market(conn, *tickers):
    days = [S, date(2026, 10, 23), date(2026, 10, 26), date(2026, 10, 27)]
    _bars(conn, 'SPY', [(d, 500, 501, 499, 500) for d in days])
    for ticker in tickers:      # 10-23 opens at 100 inside 99–101, never trades below 99
        _bars(conn, ticker, [(S, 100, 101, 99.5, 100), (days[1], 100, 103, 99, 102),
                             (days[2], 102, 104, 101, 103), (days[3], 103, 105, 102, 104)])


def _plan(conn, ticker, *, kind='agent', version='agent-1', **levels):
    plan = {'ticker': ticker, 'signal_date': S, 'kind': kind, 'based_on_id': None, 'plan_version': version,
            'ref_price': D('100'), 'atr': D('2'), 'entry_low': D('99'), 'entry_high': D('101'), 'stop': D('94'),
            'target1': D('108'), 'trail_atr_multiple': D('3'), 'valid_from': date(2026, 10, 23),
            'valid_until': date(2026, 10, 27), 'sizing': {'status': 'no_equity'}, 'status': 'ok',
            'invalid_reasons': [], 'warnings': [], 'inputs': {'bar_ids': [1]}, 'request_id': None,
            'request_hash': None}
    plan.update(levels)
    return execution_repo.insert_plan(conn, plan)


def _run_row(conn, ticker, *, status='ok', plan=None, mode='propose', version='agent-1', reason=None, at=None):
    at = at or NOW - timedelta(days=5)
    return analysis_repo.insert_agent_run(conn, {
        'id': uuid.uuid4(), 'ticker': ticker, 'signal_date': S, 'pool': 'main', 'attempt': 1, 'mode': mode,
        'agent_version': version, 'provider': 'chatgpt', 'model': 'gpt-test', 'cutoff_at': at, 'status': status,
        'reason': reason, 'output': {'action': 'buy'}, 'counter': None,
        'plan_id': plan['id'] if plan else None, 'input_tokens': 0, 'output_tokens': 0,
        'started_at': at, 'finished_at': at}, [])


def _version(conn, ticker, version_no, supersedes=None, **levels):
    plan = _plan(conn, ticker, version='agent-2', **levels)
    run = _run_row(conn, ticker, plan=plan, mode='propose' if version_no == 0 else 'revise', version='agent-2',
                   at=NOW - timedelta(days=5 - version_no))
    execution_repo.insert_plan_version(conn, {
        'plan_id': plan['id'], 'ticker': ticker, 'signal_date': S, 'version_no': version_no,
        'supersedes_plan_id': supersedes, 'origin': 'signal' if version_no == 0 else 'review',
        'agent_run_id': run['id'], 'cutoff_at': run['cutoff_at'], 'as_of_session': S,
        'diff': None if version_no == 0 else [{'field': 'entry', 'before': '99–101', 'after': '96–98'}],
        'schema_version': 'agent-2'})
    return plan, run


def _chain(conn, ticker='NVDA'):
    """v0 fills on 10-23 (zone 99–101); v1 (zone 96–98, never reached) expires."""
    v0, r0 = _version(conn, ticker, 0)
    v1, r1 = _version(conn, ticker, 1, v0['id'], entry_low=D('96'), entry_high=D('98'), stop=D('92'),
                      target1=D('106'))
    return (v0, r0), (v1, r1)


def _decide(conn, ticker, *, plan_id=None, agent_plan_id=None, final='buy'):
    rec = {'request_id': uuid.uuid4(), 'ticker': ticker, 'signal_date': S, 'candidate_ids': [uuid.uuid4()],
           'brief_id': None, 'proposed_action': 'buy', 'final_action': final,
           'decision': 'accept' if final == 'buy' else 'reject', 'reason': '测试', 'supersedes_id': None,
           'plan_id': plan_id, 'agent_plan_id': agent_plan_id, 'review_view': None}
    return execution_repo_legacy._insert_decision_for_tests(conn, rec, 'h', decided_at=NOW - timedelta(days=4))


def _rows(conn):
    return {row['ticker']: row for row in review_weekly._track_rows(conn, S - timedelta(days=5), SESSION,
                                                                session=SESSION, now=NOW)}


def test_version_basis_decision(workbench_db):
    _market(workbench_db, 'NVDA')
    _, (v1, r1) = _chain(workbench_db)
    _decide(workbench_db, 'NVDA', plan_id=v1['id'], agent_plan_id=v1['id'])
    agent = _rows(workbench_db)['NVDA']['agent']
    assert agent['plan_id'] == str(v1['id']) and agent['run_id'] == str(r1['id'])
    assert agent['version_no'] == 1 and agent['version_basis'] == 'decision'
    assert agent['status'] == 'expired' and agent['r'] == 0.0


def test_version_basis_pre_v25_decision_is_first(workbench_db):
    _market(workbench_db, 'NVDA')
    (v0, r0), (v1, _) = _chain(workbench_db)
    _decide(workbench_db, 'NVDA', plan_id=v1['id'])          # v2.5 之前的写法：没有 agent_plan_id
    agent = _rows(workbench_db)['NVDA']['agent']
    assert agent['plan_id'] == str(v0['id']) and agent['run_id'] == str(r0['id'])
    assert agent['version_no'] == 0 and agent['version_basis'] == 'first'
    assert agent['status'] == 'open'


def test_version_basis_undecided_is_first(workbench_db):
    _market(workbench_db, 'NVDA')
    (v0, r0), _ = _chain(workbench_db)
    row = _rows(workbench_db)['NVDA']
    assert row['agent']['plan_id'] == str(v0['id']) and row['agent']['run_id'] == str(r0['id'])
    assert row['agent']['version_no'] == 0 and row['agent']['version_basis'] == 'first'
    assert row['actual']['status'] == 'pending'      # 最新版有效至 10-27，未过期


def _old_agent(conn, ticker):
    """The pre-v2.5 reading: latest propose run and the unit's agent plan."""
    spy = data_repo.read_bars(conn, 'SPY', SESSION, cutoff_at=NOW, limit=400)
    run = analysis_repo.latest_agent_run(conn, ticker, S)
    plan = execution_repo.agent_plan(conn, ticker, S)
    outcome = (plan_shadow.simulate(plan, data_repo.read_bars(conn, ticker, SESSION, cutoff_at=NOW, limit=400),
                                    spy, splits=[]) if plan is not None and plan['status'] == 'ok' else None)
    old = {k: v for k, v in tracks.agent_track(run, plan, outcome).items() if k not in NEW_KEYS}
    return {**old, 'run_id': str(old['run_id']) if old['run_id'] else None,
            'plan_id': str(old['plan_id']) if old['plan_id'] else None}


def test_agent1_units_unchanged(workbench_db):
    conn = workbench_db
    _market(conn, 'NVDA', 'MU', 'AMD', 'INTC', 'QCOM')
    for ticker in ('NVDA', 'MU', 'AMD', 'INTC', 'QCOM'):
        _plan(conn, ticker, kind='rule', version='plan-1')
    nvda = _plan(conn, 'NVDA')
    _run_row(conn, 'NVDA', plan=nvda)
    _decide(conn, 'NVDA', plan_id=nvda['id'], agent_plan_id=nvda['id'])      # 决定时看到的就是 agent-1 计划
    mu = _plan(conn, 'MU', stop=D('96'))
    _run_row(conn, 'MU', plan=mu)                                               # 未决定
    _run_row(conn, 'AMD', status='pass')
    _run_row(conn, 'INTC', status='error', reason='model_error')
    qcom = _plan(conn, 'QCOM')
    _run_row(conn, 'QCOM', plan=qcom)
    _decide(conn, 'QCOM', plan_id=qcom['id'])                                   # v2.5 之前的决定
    rows = _rows(conn)
    expected_basis = {'NVDA': 'decision', 'MU': 'first', 'AMD': None, 'INTC': None, 'QCOM': 'first'}
    for ticker, basis in expected_basis.items():
        agent = rows[ticker]['agent']
        assert {k: v for k, v in agent.items() if k not in NEW_KEYS} == _old_agent(conn, ticker), ticker
        assert agent['version_no'] is None and agent['version_basis'] == basis, ticker
    assert rows['NVDA']['agent']['status'] == 'open' and rows['MU']['agent']['status'] == 'open'
    assert rows['AMD']['agent']['status'] == 'pass'
    assert rows['INTC']['agent']['run_status'] == 'error' and rows['INTC']['agent']['status'] == 'missing'
    # tracks-2 (D27): the actual track takes its plan from the fills' link; this decision has no fill yet
    assert rows['NVDA']['actual']['plan_kind'] is None and rows['NVDA']['actual']['plan_id'] is None
    assert rows['MU']['actual']['status'] == 'pending'
    assert set(rows['NVDA']) == {'ticker', 'signal_date', 'pool', 'rule', 'agent', 'actual'}
