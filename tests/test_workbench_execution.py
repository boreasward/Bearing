"""execution-1: fills -> plans -> chains and the execution status (v2.9 spec §6.1, §6.2, D22, D23; R13, R17)."""
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from investment_assistant.execution import adoption as execution

SD = date(2026, 10, 29)                      # chain signal date
VF, VU = date(2026, 10, 30), date(2026, 11, 3)
KEY = ('MU', SD)


def _at(day, hour=15):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)         # 11:00 New York in October


def _plan(plan_id=None, *, kind='agent', signal_date=SD, version='agent-3', valid_from=VF, valid_until=VU,
          version_no=None):
    row = {'id': plan_id or uuid.uuid4(), 'ticker': 'MU', 'signal_date': signal_date, 'kind': kind,
           'plan_version': version, 'valid_from': valid_from, 'valid_until': valid_until}
    if version_no is not None:
        row['version_no'] = version_no
    return row


def _trade(plan_id=None, *, day=VF, side='buy', decision_id=None, ticker='MU', voided=False, qty=10):
    return {'id': uuid.uuid4(), 'ticker': ticker, 'side': side, 'plan_id': plan_id, 'decision_id': decision_id,
            'executed_at': _at(day), 'voided_at': _at(day) if voided else None, 'quantity': Decimal(qty)}


# ---------------------------------------------------------------- constants

def test_constants():
    assert execution.EXECUTION_VERSION == 'execution-1'
    assert execution.EXECUTION_TEXT == {'adopted': '已采纳（{date} 成交 {quantity} 股）', 'out_of_validity': '有效期外成交',
                                        'pending': '入场期，未成交', 'not_executed': '未执行', 'executed': '已执行'}
    assert execution.LINKABLE_OWN_SESSIONS == 5 and execution.NOTE_MAX_CHARS == 1000


# ---------------------------------------------------------------- status

def test_status_adopted_in_validity():
    plan = _plan()
    buys = [_trade(plan['id'], day=date(2026, 10, 31))]
    assert execution.status(plan, buys, session=date(2026, 11, 2), chain_ended=False) == 'adopted'
    # the boundaries count; one inside fill is enough even when another fill is outside
    edge = [_trade(plan['id'], day=VU), _trade(plan['id'], day=date(2026, 11, 9))]
    assert execution.status(plan, edge, session=date(2026, 11, 9), chain_ended=True) == 'adopted'
    assert execution.status_text('adopted', buys=buys, version_plan=plan) == '已采纳（2026-10-31 成交 10 股）'


def test_status_out_of_validity():
    plan = _plan()
    late = [_trade(plan['id'], day=date(2026, 11, 4))]
    early = [_trade(plan['id'], day=date(2026, 10, 29))]
    assert execution.status(plan, late, session=date(2026, 11, 4), chain_ended=False) == 'out_of_validity'
    assert execution.status(plan, early, session=date(2026, 10, 29), chain_ended=False) == 'out_of_validity'
    assert execution.status_text('out_of_validity') == '有效期外成交'


def test_status_pending_vs_not_executed():
    plan = _plan()
    assert execution.status(plan, [], session=date(2026, 11, 2), chain_ended=False) == 'pending'
    assert execution.status(plan, [], session=VU, chain_ended=False) == 'not_executed'          # valid_until reached
    assert execution.status(plan, [], session=date(2026, 11, 2), chain_ended=True) == 'not_executed'
    other = [_trade(uuid.uuid4(), day=date(2026, 10, 31))]                                  # linked to another version
    assert execution.status(plan, other, session=date(2026, 11, 2), chain_ended=False) == 'pending'
    # M1: a revision of a holding / exiting chain is never waiting for a fill
    assert execution.status(plan, [], session=date(2026, 11, 2), chain_ended=False,
                            chain_in_entry=False) == 'not_executed'
    voided = [_trade(plan['id'], day=date(2026, 10, 31), voided=True)]
    assert execution.status(plan, voided, session=date(2026, 11, 2), chain_ended=False) == 'pending'


def test_chain_status_executed_via_baseline():
    v0 = _plan(version_no=0)
    v1 = _plan(version_no=1, valid_from=date(2026, 11, 3), valid_until=date(2026, 11, 6))
    baseline = _plan(kind='rule', version='plan-2', signal_date=date(2026, 10, 30))       # a merged unit's rule baseline
    buys = [_trade(baseline['id'], day=date(2026, 11, 2))]
    assert execution.chain_status([v0, v1], buys, session=date(2026, 11, 4), chain_ended=False) == 'executed'
    assert execution.chain_status([v0, v1], [], session=date(2026, 11, 4), chain_ended=False) == 'pending'
    assert execution.chain_status([v0, v1], [], session=date(2026, 11, 6), chain_ended=False) == 'not_executed'
    assert execution.chain_status([v0, v1], [_trade(v0['id'], voided=True)], session=date(2026, 11, 4),
                                  chain_ended=True) == 'not_executed'


def test_followed_text():
    agent, rule, own = _plan(version_no=2), _plan(kind='rule', version='plan-2'), _plan(kind='own', version='plan-2')
    plans = {a['id']: a for a in (agent, rule, own)}
    versions = {agent['id']: 2}
    for plan, text in ((agent, 'Agent 第 2 版'), (rule, '规则基线'), (own, '自定')):
        assert execution.followed([_trade(plan['id'])], plans_by_id=plans, version_no_by_plan=versions)['text'] == text
    assert execution.followed([_trade(None)], plans_by_id=plans, version_no_by_plan=versions) is None


# ---------------------------------------------------------------- plan -> chain

def test_chain_of_agent_version():
    version_plan = _plan(version_no=1)
    versions = {version_plan['id']: {'plan_id': version_plan['id'], 'ticker': 'MU', 'signal_date': SD,
                                     'version_no': 1}}
    got = execution.chain_of_plan(version_plan, versions_by_plan=versions, retriggers=[], chains=[])
    assert got == KEY
    got_str = execution.chain_of_plan(version_plan, versions_by_plan={str(version_plan['id']): versions[version_plan['id']]},
                                      retriggers=[], chains=[])
    assert got_str == KEY                                                             # string and UUID keys mix


def test_chain_of_rule_plan_own_chain():
    baseline = _plan(kind='rule', version='plan-2', signal_date=SD)
    got = execution.chain_of_plan(baseline, versions_by_plan={}, retriggers=[],
                                  chains=[{'ticker': 'MU', 'signal_date': SD}])
    assert got == KEY
    own = _plan(kind='own', version='plan-2', signal_date=SD)
    assert execution.chain_of_plan(own, versions_by_plan={}, retriggers=[],
                                   chains=[{'ticker': 'MU', 'signal_date': SD}]) == KEY


def test_chain_of_rule_plan_merged_unit():
    unit = date(2026, 10, 30)
    baseline = _plan(kind='rule', version='plan-2', signal_date=unit)
    retrigger = {'ticker': 'MU', 'unit_signal_date': unit, 'chain_signal_date': SD}
    chains = [{'ticker': 'MU', 'signal_date': SD}]
    assert execution.chain_of_plan(baseline, versions_by_plan={}, retriggers=[retrigger], chains=chains) == KEY
    # R17: a unit that opened a chain of its own belongs to that chain even though a merge row also exists
    chains.append({'ticker': 'MU', 'signal_date': unit})
    assert execution.chain_of_plan(baseline, versions_by_plan={}, retriggers=[retrigger],
                                   chains=chains) == ('MU', unit)


def test_chain_of_rule_plan_no_chain():
    baseline = _plan(kind='rule', version='plan-2', signal_date=date(2026, 10, 30))
    assert execution.chain_of_plan(baseline, versions_by_plan={}, retriggers=[],
                                   chains=[{'ticker': 'MU', 'signal_date': SD}]) is None
    foreign = {'ticker': 'AMD', 'unit_signal_date': date(2026, 10, 30), 'chain_signal_date': SD}
    assert execution.chain_of_plan(baseline, versions_by_plan={}, retriggers=[foreign], chains=[]) is None


# ---------------------------------------------------------------- fills -> chain

def test_chain_buys_union_with_legacy_decisions_dedup():
    linked_plan, other_plan = uuid.uuid4(), uuid.uuid4()
    linked = _trade(linked_plan, day=date(2026, 10, 31))
    legacy = _trade(None, day=date(2026, 10, 30), decision_id=7)
    both = _trade(linked_plan, day=date(2026, 11, 2), decision_id=7)            # impossible in the DB, deduped anyway
    foreign = _trade(other_plan, day=date(2026, 10, 30))                        # another chain
    sell = _trade(linked_plan, side='sell')
    voided = _trade(linked_plan, voided=True)
    other_ticker = _trade(linked_plan, ticker='AMD')
    got = execution.chain_buys(KEY, [linked, legacy, both, foreign, sell, voided, other_ticker, linked],
                               plan_chain={linked_plan: KEY, str(other_plan): ('MU', date(2026, 10, 1))},
                               legacy_decision_ids={7})
    assert [t['id'] for t in got] == [legacy['id'], linked['id'], both['id']]    # oldest first, no duplicates


def test_unplanned_trade():
    assert execution.unplanned(_trade(None), legacy_plan_id=None) is True
    assert execution.unplanned(_trade(uuid.uuid4()), legacy_plan_id=None) is False
    assert execution.unplanned(_trade(None, decision_id=3), legacy_plan_id=uuid.uuid4()) is False
    assert execution.unplanned(_trade(None, side='sell'), legacy_plan_id=None) is False
