"""v2.9 orchestration (Task 3): the `plan` node's retrigger rows, the `agent` node's skips and first-version guard,
the retrigger revisions and the exit phase of the review, and the daily holding status — end to end against a
disposable schema with the plan-review test harness (fake fetchers, routed scripted model, `now` advancing by
session). A volume spike on a later day makes a second buy unit (`volume_breakout`) for a ticker that already has
a chain: that is the retrigger. No network, no real database."""
import investment_assistant.analysis.agent.runner as analysis_agent_runner
import investment_assistant.analysis.units as analysis_units
from investment_assistant.execution import chains as execution_chains
from investment_assistant.analysis.agent import evidence as agent_evidence
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import hashlib
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from test_workbench_plan_review import (BASE, S1, ConnectionLost, LateClock, Market, counter, day, evening, head,
                                         make_model, proposal, reviews, revision, run, settings, signal, signal_day,
                                         skips, verdict)
from test_workbench_service import db   # noqa: F401  (fixture: service wired to the disposable schema)
from investment_assistant.data.calendar import add_trading_days
import investment_assistant.workflow.nodes.review as plan_review
import investment_assistant.core.tx as service
import investment_assistant.views.agent_runs as d_views_agent_runs
import investment_assistant.workflow.nodes.plan as d_workflow_nodes_plan
from investment_assistant.execution import timeline

NY = ZoneInfo('America/New_York')


# ---------------------------------------------------------------- helpers

def spike(market, ticker, k):
    """A volume spike on day(k): `volume_breakout` (buy) fires on top of the uptrend."""
    market.volumes[(ticker, day(k))] = 5000


def renewal(ticker):
    """A revision that keeps every model field: only the host fields can move (D7)."""
    return proposal(ticker, changes=[])


def fill(db, ticker, quantity, price, k, plan_id, *, recorded_at=None):
    """A buy on day(k) at 10:00 New York linked to a plan version (`trades.plan_id`), recorded that evening."""
    rec = {'request_id': str(uuid.uuid4()), 'ticker': ticker, 'side': 'buy', 'quantity': quantity, 'price': price,
           'fee': 0, 'executed_at': datetime.combine(day(k), time(10, 0), tzinfo=NY), 'plan_id': plan_id, 'note': None}
    out = execution_repo.insert_trade(db, rec, 'digest-' + rec['request_id'], recorded_at=recorded_at or evening(day(k)))
    db.commit()
    return out


def merge(db, k, *, state='entry', created_at=None, ticker='SYNTH', chain=S1):
    """A `chain_retriggers` row for the unit of day(k), as the plan node would write it."""
    row = execution_repo.insert_chain_retrigger(db, {
        'ticker': ticker, 'unit_signal_date': day(k), 'chain_signal_date': chain, 'chain_state': state,
        'rule_ids': ['volume_breakout'], 'baseline_plan_id': None,
        'entry_cap': add_trading_days(day(k), 10) if state == 'entry' else None, 'run_id': f'run-merge-{k}',
        'timeline_version': timeline.TIMELINE_VERSION, 'created_at': created_at or evening(day(k))})
    db.commit()
    return row


def exit_review(db, v0, k, *, reason='stop_touched'):
    """A holding-phase invalidation under timeline-2: the chain's exit instruction (D17)."""
    row = execution_repo.insert_plan_review(db, {
        'ticker': 'SYNTH', 'signal_date': S1, 'head_plan_id': v0['plan']['id'], 'session': day(k),
        'trigger': 'batch', 'run_id': 'run-exit', 'mode': 'holding', 'timeline_version': 'timeline-2',
        'outcome': 'invalidate', 'decided_by': 'host', 'reason': '止损触及（115.00）：应已按止损卖出', 'attempt': 1,
        'cutoff_at': evening(day(k)), 'detail': {'exit_reason': reason}})
    db.commit()
    return row


def status_rows(db, k):
    return execution_repo.holding_status_for(db, session=day(k))


def human(model, route):
    return [m for r, m in model.seen if r == route][0][1].content


def state_at(db, k, ticker='SYNTH', chain=S1):
    return execution_chains.chain_status(db, ticker, chain, session=day(k), now=evening(day(k)))['state']


def holding_chain(db, market, *, max_hold=10, fill_k=1, quantity='10', price='125'):
    """A chain with a fill linked to its first version: holding from day(fill_k)."""
    model = make_model(propose=proposal('SYNTH', max_hold=max_hold), counter=counter())
    run(db, market, 0, tickers=['SYNTH'], model=model)
    v0 = head(db)
    assert v0['version']['version_no'] == 0
    fill(db, 'SYNTH', quantity, price, fill_k, v0['plan']['id'])
    return v0


# ================================================================ the `plan` node (R2)

@pytest.mark.parametrize('state', ['entry', 'holding', 'exiting'])
def test_plan_node_writes_retrigger_for_active_chain(db, state):
    market = Market()
    v0 = signal_day(db, market, ['SYNTH'])[0]['SYNTH']
    queues = {}
    if state != 'entry':
        fill(db, 'SYNTH', '10', '125', 1, v0['plan']['id'])
    if state == 'exiting':
        exit_review(db, v0, 1)
    elif state == 'holding':
        queues = {'review': verdict('SYNTH', 'maintain', '趋势未变', cite=False)}
    else:
        queues = {'revise': renewal('SYNTH'), 'counter': counter()}
    spike(market, 'SYNTH', 2)
    summary, rows, model = run(db, market, 2, tickers=['SYNTH'], model=make_model(**queues))

    [row] = execution_repo.chain_retriggers_for(db, 'SYNTH')
    assert (row['unit_signal_date'], row['chain_signal_date'], row['chain_state']) == (day(2), S1, state)
    assert row['rule_ids'] == ['volume_breakout'] and row['timeline_version'] == 'timeline-2'
    assert row['entry_cap'] == (add_trading_days(day(2), 10) if state == 'entry' else None)
    assert row['created_at'] == evening(day(2))                    # the cutoff of the run that found it (R4)
    baseline = execution_repo.plan_by_id(db, row['baseline_plan_id'])
    assert (baseline['kind'], baseline['plan_version'], baseline['signal_date']) == ('rule', 'plan-2', day(2))
    assert baseline['inputs']['active_chain'] == {'ticker': 'SYNTH', 'signal_date': S1.isoformat()}
    assert (baseline['entry_type'], baseline['max_hold_sessions']) == ('zone_limit', 10)
    by_state = {'entry': 0, 'holding': 0, 'exiting': 0, state: 1}
    assert rows['plan']['detail']['retriggers'] == {'count': 1, 'by_state': by_state, 'tickers': ['SYNTH']}
    assert rows['plan']['status'] in ('success', 'degraded')


def test_plan_node_no_retrigger_without_active_chain(db):
    market = Market()
    signal_day(db, market, ['SYNTH'])
    spike(market, 'SYNTH', 8)                                   # chain A expired on day(3) with no fill
    model = make_model(propose=proposal('SYNTH'), counter=counter())
    summary, rows, model = run(db, market, 8, tickers=['SYNTH'], model=model)
    assert execution_repo.chain_retriggers_for(db, 'SYNTH') == []
    assert rows['plan']['detail']['retriggers'] == {'count': 0, 'by_state': {'entry': 0, 'holding': 0, 'exiting': 0},
                                                    'tickers': []}
    baseline = execution_repo.rule_plan(db, 'SYNTH', day(8))
    assert 'active_chain' not in baseline['inputs']
    chain_b = execution_repo.chain_head(db, 'SYNTH', day(8))
    assert chain_b is not None and chain_b['version']['version_no'] == 0
    assert len(analysis_repo.agent_runs_for(db, 'SYNTH', day(8))) == 1 and model.calls['propose'] == 2   # tool call + plan


def test_plan2_rows_marked_active_chain(db):
    market = Market()
    signal_day(db, market, ['SYNTH'])
    spike(market, 'SYNTH', 2)
    run(db, market, 2, tickers=['SYNTH'], model=make_model(revise=renewal('SYNTH'), counter=counter()))
    plan = execution_repo.rule_plan(db, 'SYNTH', day(2))
    assert plan['plan_version'] == 'plan-2' and plan['inputs']['active_chain']['signal_date'] == S1.isoformat()
    assert plan['inputs']['entry_rules_version'] == 'entry-1'
    own = execution_repo.rule_plan(db, 'SYNTH', S1)
    assert own['plan_version'] == 'plan-2' and 'active_chain' not in own['inputs']       # the chain's own unit


def test_retriggers_counted_via_plan_acc(db):
    """R2: the count rides the frozen plan accumulator (refs.plan_acc) and is written by the `plan` node."""
    market = Market()
    signal_day(db, market, ['SYNTH'])
    spike(market, 'SYNTH', 2)
    import investment_assistant.core.stages as stages_module
    summary, rows, _ = run(db, market, 2, tickers=['SYNTH'], model=make_model(revise=renewal('SYNTH'), counter=counter()))
    frozen = rows['plan']['detail']['retriggers']
    assert frozen['count'] == 1 and frozen['tickers'] == ['SYNTH']
    acc = stages_module.StageAcc()
    acc.restore({'detail': {'retriggers': frozen}, 'outputs': {'inserted': [], 'existing': []}})
    ctx = type('Ctx', (), {})()
    d_workflow_nodes_plan.stage_plan_check({'summary': {'errors': []}}, ctx, acc)
    assert acc.detail['retriggers'] == frozen                          # the check keeps what the loop counted
    empty = stages_module.StageAcc()
    empty.outputs.update({'inserted': [], 'existing': []})
    d_workflow_nodes_plan.stage_plan_check({'summary': {'errors': []}}, ctx, empty)
    assert empty.detail['retriggers']['count'] == 0                     # a node restored without it says 0


def test_rerun_session_idempotent_retriggers(db):
    market = Market()
    signal_day(db, market, ['SYNTH'])
    spike(market, 'SYNTH', 2)
    run(db, market, 2, tickers=['SYNTH'], model=make_model(revise=renewal('SYNTH'), counter=counter()))
    again_summary, again_rows, model = run(db, market, 2, tickers=['SYNTH'], model=make_model())
    assert len(execution_repo.chain_retriggers_for(db, 'SYNTH')) == 1
    assert again_rows['plan']['detail']['retriggers']['count'] == 1      # read back from the stored row
    assert len(execution_repo.all_chains(db)) == 1 and not model.calls
    assert skips(again_rows['agent']) == {'active_chain': [f'SYNTH·{S1.isoformat()}']}
    assert summary_merged(again_summary) == {'SYNTH': S1.isoformat()}


def summary_merged(summary):
    return summary['agent']['merged']


# ================================================================ the `agent` node (§4.3, §4.7)

def test_agent_node_skips_active_chain_and_uses_rule_brief(db):
    market = Market()
    signal_day(db, market, ['SYNTH'])
    spike(market, 'SYNTH', 2)
    summary, rows, model = run(db, market, 2, tickers=['SYNTH'],
                               model=make_model(revise=renewal('SYNTH'), counter=counter()))
    assert model.calls.get('propose', 0) == 0
    assert analysis_repo.agent_runs_for(db, 'SYNTH', day(2)) == []                   # no run row for the merged unit
    assert skips(rows['agent']) == {'active_chain': [f'SYNTH·{S1.isoformat()}']}
    assert rows['agent']['detail']['merged'] == 1 and summary['agent']['merged'] == {'SYNTH': S1.isoformat()}
    brief = analysis_repo.latest_brief(db, 'SYNTH', day(2))
    assert (brief['status'], brief['prompt_version'], brief['agent_run_id']) == ('fallback', 'rules-1', None)
    assert 'volume_breakout' in brief['output']['summary']
    assert brief['error'] == '已有进行中的计划链，这次触发已并入'


def test_agent_node_rechecks_active_chain_under_lock(db, monkeypatch):
    """The `plan` node found no chain (simulated), then one is active by the time the first version is written:
    the proposal is kept as a skipped `active_chain` run with its tokens, no second chain, and the merge is recorded."""
    market = Market()
    signal_day(db, market, ['SYNTH'])
    spike(market, 'SYNTH', 2)
    monkeypatch.setattr(analysis_units, '_open_or_merge', lambda *args, **kwargs: None)
    model = make_model(propose=proposal('SYNTH'), revise=renewal('SYNTH'), counter=[*counter(), *counter()])
    summary, rows, model = run(db, market, 2, tickers=['SYNTH'], model=model)
    assert model.calls['propose'] == 2                                        # the tool round and the plan
    [skipped] = analysis_repo.agent_runs_for(db, 'SYNTH', day(2))
    assert (skipped['status'], skipped['reason'], skipped['plan_id']) == ('skipped', 'active_chain', None)
    assert skipped['input_tokens'] > 0 and skipped['agent_version'] == 'agent-3'
    assert len(execution_repo.all_chains(db)) == 1
    [merged] = execution_repo.chain_retriggers_for(db, 'SYNTH')
    assert (merged['unit_signal_date'], merged['chain_signal_date'], merged['chain_state']) == (day(2), S1, 'entry')
    assert merged['created_at'] == evening(day(2))
    assert skips(rows['agent']) == {'active_chain': ['SYNTH']}
    [review] = [r for r in reviews(db) if r['session'] == day(2)]            # the review then sees the trigger
    assert review['detail']['via'] == 'retrigger' and review['new_plan_id'] is not None


def test_first_version_writes_agent3_rows(db):
    market = Market()
    signal_day(db, market, ['SYNTH'])
    run_row = analysis_repo.latest_agent_run(db, 'SYNTH', S1)
    plan = execution_repo.agent_plan(db, 'SYNTH', S1)
    version = execution_repo.chain_head(db, 'SYNTH', S1)['version']
    assert run_row['agent_version'] == 'agent-3' and run_row['status'] == 'ok'
    assert (plan['plan_version'], plan['entry_type'], plan['max_hold_sessions']) == ('agent-3', 'zone_limit', 10)
    assert plan['inputs']['chain_mode'] == 'entry' and plan['inputs']['entry_rules_version'] == 'entry-1'
    assert plan['inputs']['chain_entry_cap'] == add_trading_days(S1, 10).isoformat()
    assert version['schema_version'] == 'agent-3' and version['diff'] is None and version['version_no'] == 0
    assert run_row['output']['entry_type'] == 'zone_limit' and run_row['output']['max_hold_sessions'] == 10


def test_first_version_prompt_has_previous_chain_block(db):
    market = Market()
    signal_day(db, market, ['SYNTH'])
    spike(market, 'SYNTH', 8)
    model = make_model(propose=proposal('SYNTH'), counter=counter())
    summary, rows, model = run(db, market, 8, tickers=['SYNTH'], model=model)
    text = human(model, 'propose')
    assert text.startswith('标的 SYNTH · 信号日') and agent_evidence.PREVIOUS_CHAIN_HEADER in text
    assert f'- 链 SYNTH·{S1.isoformat()}（共 1 版），结束：已过期未执行（{day(3).isoformat()}）' in text
    assert agent_evidence.PREVIOUS_CHAIN_HEADER not in human(model, 'counter')            # the proposer only
    run_row = analysis_repo.latest_agent_run(db, 'SYNTH', day(8))
    digest = run_row['output']['input_digest']['previous_chain']
    assert run_row['detail'] is None
    assert digest['text'] in text and digest['sha256'] == hashlib.sha256(digest['text'].encode()).hexdigest()
    assert run_row['status'] == 'ok'


def test_first_version_without_a_previous_chain(db):
    market = Market()
    model = make_model(propose=proposal('SYNTH'), counter=counter())
    summary, rows, model = run(db, market, 0, tickers=['SYNTH'], model=model)
    assert agent_evidence.PREVIOUS_CHAIN_NONE in human(model, 'propose')


def test_coverage_counts_merged(db):
    market = Market()
    signal_day(db, market, ['SYNTH'])
    spike(market, 'SYNTH', 2)
    run(db, market, 2, tickers=['SYNTH'], model=make_model(revise=renewal('SYNTH'), counter=counter()))
    coverage = d_views_agent_runs.agent_runs(now=evening(day(2)))['coverage']
    assert coverage['units'] == 2 and coverage['covered'] == 2 and coverage['missing'] == []
    assert coverage['merged'] == 1
    assert coverage['merged_units'] == [{'ticker': 'SYNTH', 'signal_date': day(2), 'chain_signal_date': S1}]


def _merged_unit(db):
    """A chain on S1, a merged unit on day(2); the agent is switched off so nothing else happens."""
    market = Market()
    signal_day(db, market, ['SYNTH'])
    spike(market, 'SYNTH', 2)
    run(db, market, 2, tickers=['SYNTH'], model=make_model(), agent=settings(provider='off'))
    assert len(execution_repo.chain_retriggers_for(db, 'SYNTH')) == 1
    return market


def _deps(model):
    from contextlib import nullcontext

    from langgraph.checkpoint.memory import InMemorySaver
    deps = service.Deps()
    deps.agent_model = model
    deps.agent_checkpointer = lambda: nullcontext(InMemorySaver())
    deps.agent_settings = settings()
    return deps


def test_manual_rerun_rejects_active_chain(db):
    _merged_unit(db)
    now = evening(day(2))
    with pytest.raises(analysis_agent_runner.AgentConflict) as raised:
        analysis_agent_runner.run_agent('SYNTH', day(2), now=now, deps=_deps(make_model()))
    assert str(raised.value) == 'active_chain' and raised.value.chain_signal_date == S1
    with pytest.raises(analysis_agent_runner.AgentConflict, match='active_chain'):
        analysis_agent_runner.rerun_agent('SYNTH', day(2), 'propose', request_id=str(uuid.uuid4()), origin='api', now=now,
                            deps=_deps(make_model()))
    assert db.execute('SELECT count(*) FROM agent_attempt_resets').fetchone()[0] == 0     # refused before the reset
    assert len(execution_repo.all_chains(db)) == 1


def test_rerun_allowed_when_merged_chain_ended(db):
    """R17: the merge row only counts while the chain it points at is active; the unit may open its own chain."""
    _merged_unit(db)
    head_a = head(db)
    execution_repo.insert_plan_review(db, {
        'ticker': 'SYNTH', 'signal_date': S1, 'head_plan_id': head_a['plan']['id'], 'session': day(2),
        'trigger': 'batch', 'run_id': 'run-x', 'mode': 'entry', 'timeline_version': 'timeline-2',
        'outcome': 'invalidate', 'decided_by': 'host', 'reason': '失效条件成立', 'attempt': 1,
        'cutoff_at': evening(day(2))})
    db.commit()
    now = evening(day(2)) + timedelta(hours=1)
    out = analysis_agent_runner.rerun_agent('SYNTH', day(2), 'propose', request_id=str(uuid.uuid4()), origin='api', now=now,
                              deps=_deps(make_model(propose=proposal('SYNTH'), counter=counter())))
    assert out['run_id'] is not None
    own = execution_repo.chain_head(db, 'SYNTH', day(2))
    assert own is not None and own['version']['version_no'] == 0              # its own chain, whatever the merge row says


# ================================================================ retrigger revisions (§4.3, D4, D7)

def test_retrigger_entry_goes_straight_to_revise(db):
    market = Market()
    v0 = signal_day(db, market, ['SYNTH'])[0]['SYNTH']
    spike(market, 'SYNTH', 2)
    model = make_model(revise=revision('SYNTH'), counter=counter())
    summary, rows, model = run(db, market, 2, tickers=['SYNTH'], model=model)
    assert model.calls.get('review', 0) == 0                                  # no single-phase review
    [row] = [r for r in reviews(db) if r['session'] == day(2)]
    assert (row['outcome'], row['decided_by'], row['trigger'], row['mode']) == ('revise', 'agent', 'batch', 'entry')
    assert row['detail']['via'] == 'retrigger' and row['review_agent_run_id'] is None
    assert row['revision_agent_run_id'] is not None and row['timeline_version'] == 'timeline-2'
    assert signal(row, 'retrigger')['fired'] is True and row['reason'].startswith('再次触发（')
    assert row['detail']['retrigger'] == {'unit_signal_dates': [day(2).isoformat()], 'rule_ids': ['volume_breakout']}
    new = head(db)
    assert new['version']['version_no'] == 1 and str(row['new_plan_id']) == str(new['plan']['id'])
    assert new['plan']['plan_version'] == 'agent-3' and new['plan']['stop'] == 117
    assert [(d['field'], d['host_only']) for d in new['version']['diff']] == [
        ('stop', False), ('valid_until', True), ('entry_cap', True)]
    assert rows['review']['detail']['signals']['retrigger'] == 1


def test_retrigger_renewal_version(db):
    market = Market()
    v0 = signal_day(db, market, ['SYNTH'])[0]['SYNTH']
    spike(market, 'SYNTH', 2)
    run(db, market, 2, tickers=['SYNTH'], model=make_model(revise=renewal('SYNTH'), counter=counter()))
    new = head(db)
    assert new['version']['version_no'] == 1
    assert [(d['field'], d['host_only']) for d in new['version']['diff']] == [('valid_until', True), ('entry_cap', True)]
    cap = add_trading_days(day(2), 10)
    assert new['plan']['inputs']['chain_entry_cap'] == cap.isoformat()
    assert new['plan']['valid_from'] == day(3) and new['plan']['valid_until'] == add_trading_days(day(2), 3)
    run_row = data_repo.agent_run_by_id(db, new['version']['agent_run_id'])
    assert run_row['output']['changes'] == []                                 # the model changed nothing
    assert (new['plan']['stop'], new['plan']['entry_low']) == (v0['plan']['stop'], v0['plan']['entry_low'])
    [review] = [r for r in reviews(db) if r['session'] == day(2)]
    assert review['outcome'] == 'revise' and review['detail']['via'] == 'retrigger'


def test_retrigger_pass_invalidates(db):
    market = Market()
    signal_day(db, market, ['SYNTH'])
    spike(market, 'SYNTH', 2)
    run(db, market, 2, tickers=['SYNTH'], model=make_model(revise=revision('SYNTH', action='pass'),
                                                           counter=counter()))
    [row] = [r for r in reviews(db) if r['session'] == day(2)]
    assert (row['outcome'], row['decided_by']) == ('invalidate', 'agent')
    assert row['detail']['via'] == 'revision_pass' and row['new_plan_id'] is None
    assert row['review_agent_run_id'] is None and row['revision_agent_run_id'] is not None
    assert state_at(db, 3)['state'] == 'invalidated'


def test_retrigger_invalidation_met_first(db):
    market = Market()
    signal_day(db, market, ['SYNTH'])
    merge(db, 2)
    market.jump('SYNTH', day(2), 110.0 / BASE[day(2)])                        # closes under the 112 invalidation
    summary, rows, model = run(db, market, 2, tickers=['SYNTH'], model=make_model())
    [row] = [r for r in reviews(db) if r['session'] == day(2)]
    assert (row['outcome'], row['decided_by']) == ('invalidate', 'host')
    assert row['reason'] == '失效条件成立：收盘跌破 112.00' and signal(row, 'retrigger')['fired'] is True
    assert not {'review', 'revise', 'propose'} & set(model.calls) and row['revision_agent_run_id'] is None


def test_retrigger_holding_is_evidence_only(db):
    market = Market()
    holding_chain(db, market)
    merge(db, 2, state='holding')
    model = make_model(review=verdict('SYNTH', 'maintain', '趋势未变', cite=False))
    summary, rows, model = run(db, market, 2, tickers=['SYNTH'], model=model)
    assert model.calls == {'review': 1}                                       # evidence for the review, no revision
    [row] = [r for r in reviews(db) if r['session'] == day(2)]
    assert (row['mode'], row['outcome'], row['decided_by']) == ('holding', 'maintain', 'agent')
    assert 'via' not in row['detail'] and row['detail']['retrigger']['rule_ids'] == ['volume_breakout']
    assert '## 再次触发' in human(model, 'review') and '- 2026-09-03' in human(model, 'review')
    assert head(db)['version']['version_no'] == 0


def test_retrigger_evidence_respects_cutoff(db):
    """A merge recorded after the review's cutoff is neither a signal nor part of the evidence (R4, Review Focus 1)."""
    market = Market()
    signal_day(db, market, ['SYNTH'])
    merge(db, 2, created_at=evening(day(2)) + timedelta(hours=1))
    model = make_model(review=verdict('SYNTH', 'maintain', '趋势未变', cite=False))
    run(db, market, 2, tickers=['SYNTH'], model=model)
    [row] = [r for r in reviews(db) if r['session'] == day(2)]
    assert signal(row, 'retrigger')['fired'] is False and 'retrigger' not in row['detail']
    assert row['detail'].get('via') != 'retrigger' and '## 再次触发' not in human(model, 'review')
    assert head(db)['version']['version_no'] == 0


def test_same_day_recheck_then_batch_still_fires_retrigger(db):
    """R4: the window is by created_at, not by session — a recheck earlier the same day does not swallow the trigger
    that the batch's plan node writes a little later."""
    market = Market()
    signal_day(db, market, ['SYNTH'])
    run(db, market, 2, tickers=['SYNTH'], model=make_model(), agent=settings(provider='off'), run_id='run-bars')
    recheck_at = evening(day(2)) + timedelta(minutes=10)
    row = plan_review.recheck('SYNTH', S1, now=recheck_at,
                              deps=_deps(make_model(review=verdict('SYNTH', 'maintain', '先看一眼', cite=False))))
    assert signal(row, 'retrigger')['fired'] is False and row['trigger'] == 'recheck'
    spike(market, 'SYNTH', 2)
    model = make_model(revise=renewal('SYNTH'), counter=counter())
    summary, rows, model = run(db, market, 2, tickers=['SYNTH'], model=model, run_id='run-batch',
                               at=evening(day(2)) + timedelta(minutes=20))
    [batch] = [r for r in reviews(db) if r['trigger'] == 'batch' and r['outcome'] != 'skipped' and r['session'] == day(2)]
    assert signal(batch, 'retrigger')['fired'] is True and batch['detail']['via'] == 'retrigger'
    [merge_row] = execution_repo.chain_retriggers_for(db, 'SYNTH')
    assert recheck_at < merge_row['created_at'] == evening(day(2)) + timedelta(minutes=20)


def test_last_day_retrigger_renews_not_new_chain(db):
    """R5: a trigger on the last valid day renews the chain instead of opening a second one."""
    market = Market()
    signal_day(db, market, ['SYNTH'])
    spike(market, 'SYNTH', 3)                                                   # day(3) is the first version's valid_until
    summary, rows, model = run(db, market, 3, tickers=['SYNTH'], model=make_model(revise=renewal('SYNTH'),
                                                                                 counter=counter()))
    assert len(execution_repo.all_chains(db)) == 1 and model.calls.get('propose', 0) == 0
    [row] = execution_repo.chain_retriggers_for(db, 'SYNTH')
    assert (row['chain_state'], row['entry_cap']) == ('entry', add_trading_days(day(3), 10))
    new = head(db)
    assert new['version']['version_no'] == 1 and new['plan']['valid_from'] == day(4)
    [review] = [r for r in reviews(db) if r['session'] == day(3)]
    assert review['mode'] == 'entry' and review['outcome'] == 'revise' and review['detail']['via'] == 'retrigger'
    assert state_at(db, 4)['state'] == 'entry'


def test_legacy_two_entry_chains_older_superseded(db, monkeypatch):
    """D8, R1: two entry-phase chains of one ticker (pre-v2.9 data) — the older one is `superseded`, the newer one
    keeps the review."""
    market = Market()
    signal_day(db, market, ['SYNTH'])
    spike(market, 'SYNTH', 1)
    monkeypatch.setattr(analysis_units, '_open_or_merge', lambda *args, **kwargs: None)
    monkeypatch.setattr(analysis_agent_runner, '_first_version_guard', lambda *args, **kwargs: None)
    run(db, market, 1, tickers=['SYNTH'], model=make_model(propose=proposal('SYNTH'), counter=counter()))
    assert len(execution_repo.all_chains(db)) == 2
    old = execution_chains.chain_status(db, 'SYNTH', S1, session=day(1), now=evening(day(1)))
    assert old['state']['state'] == 'superseded' and old['state']['basis_ref'] == {'superseded_by': day(1).isoformat()}
    assert execution_chains.chain_status(db, 'SYNTH', day(1), session=day(1), now=evening(day(1)))['state']['state'] == 'entry'
    monkeypatch.undo()


# ================================================================ the exit phase (D17, D18, §7.2)

def test_holding_stop_touched_host_exit(db):
    market = Market()
    holding_chain(db, market)
    market.overrides[('SYNTH', day(2))] = {'low': 114.0}                        # the 115 stop trades
    summary, rows, model = run(db, market, 2, tickers=['SYNTH'], model=make_model())
    [row] = [r for r in reviews(db) if r['session'] == day(2)]
    assert (row['mode'], row['outcome'], row['decided_by'], row['timeline_version']) == (
        'holding', 'invalidate', 'host', 'timeline-2')
    assert row['reason'] == '止损触及（115.00）：应已按止损卖出；仍持有则次一交易日开盘卖出'
    assert row['detail']['exit_reason'] == 'stop_touched'
    assert not {'review', 'revise', 'propose'} & set(model.calls)      # (the plan_stop sell unit only gets an interpretation)
    assert state_at(db, 3)['state'] == 'exiting'
    [status] = status_rows(db, 2)
    assert (status['instruction'], status['exit_reason'], status['review_id']) == ('exit', 'stop_touched', row['id'])
    assert status['reason'].startswith('退出指令自 2026-09-03 起（止损触及），仍持有 10 股')


def test_holding_time_stop_host_exit(db):
    market = Market()
    holding_chain(db, market, max_hold=5)
    summary, rows, model = run(db, market, 5, tickers=['SYNTH'], model=make_model())
    [row] = [r for r in reviews(db) if r['session'] == day(5)]
    assert (row['outcome'], row['decided_by']) == ('invalidate', 'host')
    assert row['reason'] == '时间止损：持有 5 个交易日仍未到 1R，次一交易日开盘卖出'
    assert row['detail']['exit_reason'] == 'time_stop' and not model.calls
    injected = next(c for c in row['conditions'] if c['index'] == 'max_hold')
    assert (injected['list'], injected['kind'], injected['source'], injected['result']) == (
        'invalidation', 'held_sessions_reached', 'max_hold', 'met')
    assert injected['params'] == {'sessions': 5}


def test_holding_agent_invalidate_is_exiting(db):
    market = Market()
    holding_chain(db, market)
    model = make_model(review=verdict('SYNTH', 'invalidate', '论点被证伪'))
    run(db, market, 2, tickers=['SYNTH'], model=model)
    [row] = [r for r in reviews(db) if r['session'] == day(2)]
    assert (row['outcome'], row['decided_by'], row['mode']) == ('invalidate', 'agent', 'holding')
    assert row['detail']['exit_reason'] == 'agent_invalidate'
    assert state_at(db, 3)['state'] == 'exiting'                                 # not `invalidated`: the lots are open
    [status] = status_rows(db, 2)
    assert (status['instruction'], status['exit_reason']) == ('exit', 'agent_invalidate')


def test_exiting_chain_not_reviewed_only_status(db):
    market = Market()
    v0 = holding_chain(db, market)
    exit_review(db, v0, 1)
    before = len(reviews(db))
    summary, rows, model = run(db, market, 2, tickers=['SYNTH'], model=make_model())
    assert not model.calls and len(reviews(db)) == before                       # no model, no plan_reviews row
    assert summary['review']['objects'] == 0
    assert rows['review']['detail']['exiting'] == [{'ticker': 'SYNTH', 'signal_date': S1.isoformat()}]
    [status] = status_rows(db, 2)
    assert (status['instruction'], status['exit_reason'], status['chain_signal_date']) == ('exit', 'stop_touched', S1)
    assert status['reason'] == '退出指令自 2026-09-02 起（止损触及），仍持有 10 股'
    assert status['detail']['exit_since'] == day(1).isoformat()


def test_recheck_exiting_409(db):
    market = Market()
    v0 = holding_chain(db, market)
    exit_review(db, v0, 1)
    with pytest.raises(plan_review.RecheckConflict) as raised:
        plan_review.recheck_check('SYNTH', S1, now=evening(day(2)))
    assert raised.value.reason == 'chain_exiting'
    with pytest.raises(plan_review.RecheckConflict, match='chain_exiting'):
        plan_review.recheck('SYNTH', S1, now=evening(day(2)), deps=_deps(make_model()))


def test_holding_revision_raises_stop_above_entry(db):
    """R3: a holding-phase revision may lift the stop above the entry zone (below the close); the migration allows
    it for an agent row whose `inputs.chain_mode` is `holding`."""
    market = Market()
    holding_chain(db, market)
    model = make_model(review=verdict('SYNTH', 'revise', '上移止损锁定利润'),
                       revise=revision('SYNTH', stop=125.0, changes=[{'field': 'stop', 'text': '止损上移到 125',
                                                                      'refs': ['call:1']}]),
                       counter=counter())
    run(db, market, 2, tickers=['SYNTH'], model=model)
    new = head(db)
    assert new['version']['version_no'] == 1
    assert (new['plan']['stop'], new['plan']['entry_low']) == (125, 120)         # stop above the entry floor
    assert new['plan']['inputs']['chain_mode'] == 'holding' and new['plan']['inputs']['chain_entry_cap'] is None
    [row] = [r for r in reviews(db) if r['session'] == day(2)]
    assert (row['mode'], row['outcome']) == ('holding', 'revise') and str(row['new_plan_id']) == str(new['plan']['id'])
    assert 'stop_lowered' not in str(row['detail'])


def test_holding_stop_uses_chain_head(db):
    """D20: the stop of a position is the chain's latest version's; 1R is priced by the first fill's version."""
    market = Market()
    holding_chain(db, market)
    run(db, market, 2, tickers=['SYNTH'],
        model=make_model(review=verdict('SYNTH', 'revise', '上移止损'),
                         revise=revision('SYNTH', stop=125.0, changes=[{'field': 'stop', 'text': '止损上移到 125',
                                                                        'refs': ['call:1']}]), counter=counter()))
    run(db, market, 3, tickers=['SYNTH'], model=make_model(review=verdict('SYNTH', 'maintain', '继续', cite=False)))
    [status] = status_rows(db, 3)
    assert status['stop'] == 125 and status['plan_id'] == head(db)['plan']['id']
    assert float(status['r_now']) == pytest.approx(0.125, abs=0.01)                    # (126.25 - 125) / (125 - 115)


def test_agent_review2_prompt_has_holding_block(db):
    market = Market()
    holding_chain(db, market)
    model = make_model(review=verdict('SYNTH', 'maintain', '趋势未变', cite=False))
    run(db, market, 2, tickers=['SYNTH'], model=model)
    text = human(model, 'review')
    assert '## 持仓状态' in text and '持有 2/10 个交易日' in text and '时间止损余量：8 个交易日' in text
    assert '有效止损 115.00（initial）' in text
    system = [m for r, m in model.seen if r == 'review'][0][0].content
    assert system == plan_review.agent_graph.REVIEW_PROMPT_V2


def test_review_holding_facts_respect_cutoff(db):
    """R11: a fill executed earlier but recorded after the cutoff is not in the position the review sees."""
    market = Market()
    v0 = holding_chain(db, market)
    fill(db, 'SYNTH', '7', '126', 2, v0['plan']['id'], recorded_at=evening(day(2)) + timedelta(hours=1))
    model = make_model(review=verdict('SYNTH', 'maintain', '趋势未变', cite=False))
    run(db, market, 2, tickers=['SYNTH'], model=model)
    assert '持仓 10 股，成本 125.00' in human(model, 'review')
    [status] = status_rows(db, 2)
    assert status['quantity'] == 10 and status['avg_cost'] == 125


# ================================================================ holding status (D19)

def test_holding_status_written_when_review_skipped(db):
    market = Market()
    holding_chain(db, market)
    run(db, market, 2, tickers=['SYNTH'], model=make_model(), clock=LateClock())
    [row] = [r for r in reviews(db) if r['session'] == day(2)]
    assert (row['outcome'], row['reason']) == ('skipped', 'stage_budget')
    [status] = status_rows(db, 2)
    assert status['instruction'] == 'hold' and status['review_id'] == row['id']
    assert status['reason'] == '今日复查未完成（超出时限），按第 0 版继续持有'


def test_holding_status_written_when_review_errors(db):
    market = Market()
    holding_chain(db, market)
    run(db, market, 2, tickers=['SYNTH'], model=make_model(review=[ConnectionLost()]))
    [row] = [r for r in reviews(db) if r['session'] == day(2)]
    assert row['outcome'] == 'error'
    [status] = status_rows(db, 2)
    assert status['instruction'] == 'hold' and status['reason'].startswith('今日复查未完成（')


def test_holding_status_written_when_agent_unavailable(db):
    market = Market()
    holding_chain(db, market)
    summary, rows, model = run(db, market, 2, tickers=['SYNTH'], model=make_model(), agent=settings(provider='off'))
    [status] = status_rows(db, 2)
    assert status['instruction'] == 'hold'
    assert status['reason'] == '今日复查未完成（Agent 不可用：已关闭），按第 0 版继续持有'
    assert rows['review']['detail']['holding_status'] == {'rows': 1, 'by_instruction': {
        'hold': 1, 'sell_half': 0, 'exit': 0, 'no_plan': 0}}


def test_holding_status_written_when_no_review_object(db):
    """The agent is unavailable and no chain is reviewable (nothing to review): the stage is skipped, the row exists."""
    market = Market()
    v0 = holding_chain(db, market)
    exit_review(db, v0, 1)
    summary, rows, model = run(db, market, 2, tickers=['SYNTH'], model=make_model(), agent=settings(provider='off'))
    assert [r['instruction'] for r in status_rows(db, 2)] == ['exit']
    assert rows['review']['status'] == 'skipped'


def test_holding_status_written_for_positions_without_chain(db):
    market = Market()
    run(db, market, 0, tickers=['SYNTH'], model=make_model(propose=proposal('SYNTH'), counter=counter()))
    baseline = execution_repo.rule_plan(db, 'SYNTH', S1)
    fill(db, 'SYNB', '5', '90', 1, None)                                       # no plan at all
    fill(db, 'SYNTH', '3', '126', 1, baseline['id'])                           # the rule baseline (the unit has a chain)
    summary, rows, model = run(db, market, 2, tickers=['SYNTH', 'SYNB'], model=make_model(
        review=verdict('SYNTH', 'maintain', '趋势未变', cite=False)))
    by_ticker = {r['ticker']: r for r in status_rows(db, 2)}
    assert set(by_ticker) == {'SYNB', 'SYNTH'}
    assert by_ticker['SYNB']['instruction'] == 'no_plan' and by_ticker['SYNB']['chain_signal_date'] is None
    assert by_ticker['SYNB']['plan_id'] is None and by_ticker['SYNB']['reason'] == '无计划持仓（回撤规则看护）'
    assert by_ticker['SYNTH']['chain_signal_date'] == S1             # the rule-baseline fill belongs to the unit's chain
    assert rows['review']['detail']['holdings_without_chain'] == ['SYNB']


def test_holding_status_for_chainless_rule_baseline_position(db):
    """A position whose fill follows a rule baseline and whose unit never opened a chain: the host steps alone."""
    market = Market()
    run(db, market, 0, tickers=['SYNB'], model=make_model(), agent=settings(provider='off'))
    baseline = execution_repo.rule_plan(db, 'SYNB', S1)
    assert baseline is not None and execution_repo.chain_head(db, 'SYNB', S1) is None
    fill(db, 'SYNB', '4', str(baseline['ref_price']), 1, baseline['id'])
    run(db, market, 2, tickers=['SYNB'], model=make_model(), agent=settings(provider='off'))
    [status] = status_rows(db, 2)
    assert (status['chain_signal_date'], status['plan_id'], status['instruction']) == (None, baseline['id'], 'hold')
    assert status['stop'] == baseline['stop'] and status['max_hold_sessions'] == 10
    assert status['reason'] == '无 Agent 链，按 规则基线 的止损与最长持有看护'


def test_holding_status_written_for_exiting_chain(db):
    market = Market()
    v0 = holding_chain(db, market)
    exit_review(db, v0, 1)
    run(db, market, 2, tickers=['SYNTH'], model=make_model())
    [status] = status_rows(db, 2)
    assert status['instruction'] == 'exit'


def test_holding_status_idempotent_same_session(db):
    market = Market()
    holding_chain(db, market)
    run(db, market, 2, tickers=['SYNTH'], model=make_model(review=verdict('SYNTH', 'maintain', '趋势未变', cite=False)),
        run_id='run-a')
    [first] = status_rows(db, 2)
    run(db, market, 2, tickers=['SYNTH'], model=make_model(), run_id='run-b')
    [again] = status_rows(db, 2)
    assert again['id'] == first['id'] and again['run_id'] == 'run-a'            # the first row wins


# ================================================================ position plans follow D20 / D22 (review I1)

def _candidate_rules(db, ticker, k):
    return {c['rule_id'] for c in data_repo.unit_candidates(db, ticker, day(k))}


def test_plan_stop_for_a_fill_linked_by_plan_id_only(db):
    """A fill that carries only `trades.plan_id` (no decision) still follows its plan: the stop is judged."""
    market = Market()
    run(db, market, 0, tickers=['SYNB'], model=make_model(), agent=settings(provider='off'))
    baseline = execution_repo.rule_plan(db, 'SYNB', S1)
    fill(db, 'SYNB', '4', str(baseline['ref_price']), 1, baseline['id'])
    assert db.execute('SELECT decision_id FROM trades').fetchone()[0] is None
    market.overrides[('SYNB', day(2))] = {'low': float(baseline['stop']) - 1}
    run(db, market, 2, tickers=['SYNB'], model=make_model(), agent=settings(provider='off'))
    assert 'plan_stop' in _candidate_rules(db, 'SYNB', 2)
    candidate = next(c for c in data_repo.unit_candidates(db, 'SYNB', day(2)) if c['rule_id'] == 'plan_stop')
    assert candidate['inputs']['values']['stop'] == pytest.approx(float(baseline['stop']))


def test_plan_stop_follows_the_chain_head_after_a_holding_revision(db):
    """D20: the stop lifted by a holding-phase revision is the one the rule engine judges."""
    market = Market()
    holding_chain(db, market)
    run(db, market, 2, tickers=['SYNTH'],
        model=make_model(review=verdict('SYNTH', 'revise', '上移止损'),
                         revise=revision('SYNTH', stop=125.0, changes=[{'field': 'stop', 'text': '止损上移到 125',
                                                                        'refs': ['call:1']}]), counter=counter()))
    assert head(db)['plan']['stop'] == 125
    market.overrides[('SYNTH', day(3))] = {'low': 124.5}          # under the new stop, over the first version's 115
    run(db, market, 3, tickers=['SYNTH'], model=make_model())
    assert 'plan_stop' in _candidate_rules(db, 'SYNTH', 3)
    market.overrides[('SYNTH', day(4))] = {'low': 126.0}          # the same ticker one day later stays quiet over 125
    run(db, market, 4, tickers=['SYNTH'], model=make_model())
    assert 'plan_stop' not in _candidate_rules(db, 'SYNTH', 4)


# ================================================================ review fixes (task-3-review M2 M7 M8)

def _sell(db, ticker, quantity, price, k, *, recorded_at):
    rec = {'request_id': str(uuid.uuid4()), 'ticker': ticker, 'side': 'sell', 'quantity': quantity, 'price': price,
           'fee': 0, 'executed_at': datetime.combine(day(k), time(10, 0), tzinfo=NY), 'plan_id': None, 'note': None}
    execution_repo.insert_trade(db, rec, 'digest-' + rec['request_id'], recorded_at=recorded_at)
    db.commit()


def test_rerun_allowed_for_a_unit_the_guard_set_aside(db, monkeypatch):
    """M2 (R17): a proposal set aside as `skipped / active_chain` may be re-run once the chain it joined is over."""
    market = Market()
    signal_day(db, market, ['SYNTH'])
    spike(market, 'SYNTH', 2)
    monkeypatch.setattr(analysis_units, '_open_or_merge', lambda *args, **kwargs: None)       # the plan node misses the chain
    model = make_model(propose=proposal('SYNTH'), revise=renewal('SYNTH'), counter=[*counter(), *counter()])
    run(db, market, 2, tickers=['SYNTH'], model=model)
    [skipped] = analysis_repo.agent_runs_for(db, 'SYNTH', day(2))
    assert (skipped['status'], skipped['reason']) == ('skipped', 'active_chain')
    now = evening(day(2)) + timedelta(hours=1)
    with pytest.raises(analysis_agent_runner.AgentConflict, match='active_chain'):                    # the chain still stands
        analysis_agent_runner.rerun_agent('SYNTH', day(2), 'propose', request_id=str(uuid.uuid4()), origin='api', now=now,
                            deps=_deps(make_model()))
    head_a = head(db)
    execution_repo.insert_plan_review(db, {
        'ticker': 'SYNTH', 'signal_date': S1, 'head_plan_id': head_a['plan']['id'], 'session': day(2),
        'trigger': 'recheck', 'run_id': 'run-x', 'mode': 'entry', 'timeline_version': 'timeline-2',
        'outcome': 'invalidate', 'decided_by': 'host', 'reason': '失效条件成立', 'attempt': 1,
        'cutoff_at': evening(day(2))})
    db.commit()
    out = analysis_agent_runner.rerun_agent('SYNTH', day(2), 'propose', request_id=str(uuid.uuid4()), origin='api', now=now,
                              deps=_deps(make_model(propose=proposal('SYNTH'), counter=counter())))
    assert out['run_id'] is not None and execution_repo.chain_head(db, 'SYNTH', day(2)) is not None


def test_set_aside_proposal_is_done_for_the_session(db):
    """M7: a `skipped / active_chain` run counts as completed: re-running the session does not call the model again."""
    market = Market()
    run(db, market, 0, tickers=['SYNB'], model=make_model(), agent=settings(provider='off'))
    unit_run = analysis_repo.latest_agent_run(db, 'SYNB', S1)
    analysis_repo.insert_agent_run(db, {
        'id': str(uuid.uuid4()), 'ticker': 'SYNB', 'signal_date': S1, 'pool': 'main', 'attempt': 5, 'mode': 'propose',
        'agent_version': 'agent-3', 'provider': 'chatgpt', 'model': 'gpt-test', 'cutoff_at': evening(S1),
        'status': 'skipped', 'reason': 'active_chain', 'detail': None, 'output': None, 'counter': None,
        'plan_id': None, 'input_tokens': 10, 'output_tokens': 5, 'started_at': evening(S1),
        'finished_at': evening(S1)}, [])
    db.commit()
    assert unit_run is not None
    summary, rows, model = run(db, market, 0, tickers=['SYNB'], model=make_model(propose=proposal('SYNB'),
                                                                                 counter=counter()))
    assert model.calls.get('propose', 0) == 0
    assert skips(rows['agent']).get('already_completed') == ['SYNB']


def test_holding_status_for_a_chain_skipped_without_a_row(db, monkeypatch):
    """M8: object_cap leaves no review row; the status says why."""
    market = Market()
    run(db, market, 0, tickers=['SYNA', 'SYNTH'], model=make_model(propose=[*proposal('SYNA'), *proposal('SYNTH')],
                                                                    counter=[*counter(), *counter()]))
    for ticker in ('SYNA', 'SYNTH'):
        fill(db, ticker, '10', '125', 1, head(db, ticker)['plan']['id'])
    monkeypatch.setattr(execution_chains, 'REVIEW_MAX_OBJECTS', 1)
    summary, rows, model = run(db, market, 2, tickers=['SYNA', 'SYNTH'],
                               model=make_model(review=verdict('SYNA', 'maintain', '照旧', cite=False)))
    assert rows['review']['detail']['excluded'] == [{'ticker': 'SYNTH', 'signal_date': S1.isoformat(),
                                                     'reason': 'object_cap'}]
    by_ticker = {r['ticker']: r for r in status_rows(db, 2)}
    assert by_ticker['SYNTH']['reason'] == '今日复查未完成（超出复查对象上限），按第 0 版继续持有'
    assert by_ticker['SYNA']['reason'] == '照旧'


def test_holding_status_for_a_chain_whose_review_raised(db, monkeypatch):
    """M8: a review that blows up (no row, not capped) is reported as an error in the status, not as 「复查未执行」."""
    market = Market()
    holding_chain(db, market)

    def boom(*args, **kwargs):
        raise RuntimeError('db hiccup')

    monkeypatch.setattr(analysis_agent_runner, 'run_chain_review', boom)
    summary, rows, model = run(db, market, 2, tickers=['SYNTH'], model=make_model())
    assert any('SYNTH review: db hiccup' in e for e in summary['errors'])
    [status] = status_rows(db, 2)
    assert status['reason'] == '今日复查未完成（复查出错），按第 0 版继续持有'


def test_previous_chain_block_ignores_a_sale_recorded_after_the_cutoff(db):
    """M8 (integration, Review Focus 1): a sale executed earlier but recorded after the cutoff did not exist yet:
    the chain is still held at the cutoff, so it is not the 「上一条链」; recorded before, it is."""
    market = Market()
    v0 = holding_chain(db, market)
    cutoff = evening(day(8))
    _sell(db, 'SYNTH', '10', '127', 3, recorded_at=cutoff + timedelta(hours=1))
    with service._tx() as conn:
        facts = analysis_agent_runner._previous_chain_facts(conn, 'SYNTH', day(8), now=cutoff)
    assert agent_evidence.previous_chain_block(facts, now=cutoff).endswith(agent_evidence.PREVIOUS_CHAIN_NONE)
    with service._tx() as conn:
        late = analysis_agent_runner._previous_chain_facts(conn, 'SYNTH', day(8), now=cutoff + timedelta(hours=2))
    block = agent_evidence.previous_chain_block(late, now=cutoff + timedelta(hours=2))
    assert f'- 链 SYNTH·{S1.isoformat()}（共 1 版），结束：已平仓（{day(3).isoformat()}，实际 ' in block
    assert str(v0['plan']['id']) not in block
