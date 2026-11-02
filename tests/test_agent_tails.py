"""v2.13 §5.5 / D40–D42: the agent and review node tails, through the real ``run_batch`` wiring.

The batch's ``agent`` node drains research requests and then data requests with what is left of the shared
``AGENT_DEADLINE_S`` budget; the ``review`` node's tail handles the data requests its revisions queued. Only the
registry fetch (no network) and the research handler (its own tests cover it) are faked; units, tools, recorder,
run-scoped queue rows, the recheck review and the plan_reviews rows are all real, on the disposable schema.
"""
import uuid

import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.data.repo as data_repo
from investment_assistant.analysis import research_requests
from investment_assistant.analysis.agent import runner
from investment_assistant.core import locks as core_locks
from investment_assistant.data.fetch_policy import Fetched, FetchError
from investment_assistant.data.tally import Tally

from test_workbench_plan_review import (  # noqa: F401  (the `db` fixture and the scripted world)
    S1, LateClock, Market, counter, db, evening, make_model, proposal, reviews, revision, run,
    signal_day, tool, verdict)

RUN_ID = 'run-tail-1'


REAL_DAILY = ('bars', 'news', 'sec_filings', 'earnings', 'fred', 'universe')


def _fake_adapter(monkeypatch, *, rows=2, ok=True, order=None):
    from investment_assistant.data import registry as data_registry

    real = data_registry.run_adapter
    calls = []

    def traced(adapter, targets, **kwargs):
        if kwargs['scope'] != 'data_request' and adapter.id in REAL_DAILY:    # the collect node's own families run for real
            return real(adapter, targets, **kwargs)
        if kwargs['scope'] != 'data_request':       # the v2.12 registry families of the daily scope: not under test,
            tally = kwargs.get('tally') or Tally(adapter.id, 'agent', targets)    # booked as stored-nothing successes
            tally.set_targets(targets)
            for target in targets:
                if kwargs.get('observe') is not None:
                    kwargs['observe'](target, 'fake', [], {'inserted': 0, 'existing': 0, 'latest': None})
                tally.record(target, Fetched([{}], [], 0), rows=0)
            return tally
        calls.append((adapter.id, list(targets), kwargs['scope'], kwargs['trigger'], kwargs['run_id']))
        if order is not None:
            order.append('data')
        tally = Tally(adapter.id, 'agent', targets)
        for target in targets:
            if ok:
                tally.record(target, Fetched([{}], [], 0), rows=rows, latest=S1)
            else:
                tally.record(target, FetchError('socket down', kind='network'))
        return tally

    monkeypatch.setattr('investment_assistant.data.registry.run_adapter', traced)
    return calls


def _requests(db):
    return db.execute('SELECT family, ticker, run_id, status, rows, reason, rerun_run_id, agent_run_id '
                      'FROM data_requests ORDER BY id').fetchall()


def _queue_research(db, monkeypatch, order):
    """One queued dossier request while the "daily run" holds the lock; its handler is faked and ordered."""
    monkeypatch.setattr(research_requests.core_runner, 'submit', lambda kind, fn, **kw: 'run-test')
    monkeypatch.setattr(core_locks, 'is_running', lambda: True)
    made = research_requests.create(ticker='AMD', question='为何增长？', horizon_days=None,
                                    request_id=str(uuid.uuid4()), now=evening(S1))

    def fake_run(request_id, *, now, deps, **kwargs):
        order.append('research')
        for before, after in (('queued', 'running'), ('running', 'concluded')):     # the real state machine
            analysis_repo.update_research_status(db, request_id, from_status=before, to_status=after)
        db.commit()
        return {'id': request_id, 'status': 'concluded'}

    monkeypatch.setattr(research_requests, 'run', fake_run)
    return made


def _ask(messages, ticker='SYNTH', family='fundamentals'):
    """A proposal / revision script with a request_data call between the evidence call and the answer."""
    return [messages[0], tool('request_data', {'family': family, 'ticker': ticker}, 't9'), *messages[1:]]


def test_agent_tail_orders_units_research_data_and_rechecks_opened_chain(db, monkeypatch):
    order: list[str] = []
    market = Market()
    _queue_research(db, monkeypatch, order)
    calls = _fake_adapter(monkeypatch, rows=2, order=order)
    model = make_model(propose=_ask(proposal('SYNTH')), counter=counter(),
                       review=verdict('SYNTH', 'maintain', '补采后趋势未变', cite=False))
    summary, rows, model = run(db, market, 0, tickers=['SYNTH'], model=model, run_id=RUN_ID)

    assert order == ['research', 'data']                                  # units → research requests → data requests
    assert summary['errors'] == []
    assert summary['agent']['research_requests'] == 1
    assert summary['agent']['data_requests'] == {'queued': 1, 'fetched': 1, 'failed': 0, 'deferred': 0, 'reruns': 1}
    assert calls == [('fundamentals', ['SYNTH'], 'data_request', 'data_request', RUN_ID)]
    [(family, ticker, run_id, status, written, reason, rerun, agent_run)] = _requests(db)
    assert (family, ticker, run_id, status, written, reason) == ('fundamentals', 'SYNTH', RUN_ID, 'fetched', 2, None)
    assert agent_run is not None and rerun is not None
    # D40: the opened chain is rechecked through the review path, marked ``via = 'data_request'``
    [review] = reviews(db)
    assert (review['trigger'], review['outcome'], review['review_agent_run_id']) == ('recheck', 'maintain', rerun)
    assert review['detail']['via'] == 'data_request'
    assert model.calls['review'] == 1 and rows['agent']['status'] == 'success'
    assert rows['agent']['detail']['data_requests']['reruns'] == 1       # the stage row carries the counts


def test_agent_tail_defers_every_request_when_the_shared_budget_is_spent(db, monkeypatch):
    market = Market()
    signal_day(db, market, ['SYNTH'])                                      # S1: chain opened, no requests yet
    order: list[str] = []
    _queue_research(db, monkeypatch, order)
    calls = _fake_adapter(monkeypatch)
    run_row = {'id': str(uuid.uuid4()), 'ticker': 'SYNTH', 'signal_date': S1, 'pool': 'main', 'attempt': 9,
               'mode': 'revise', 'agent_version': 'agent-4', 'provider': 'chatgpt', 'model': 'gpt-test',
               'cutoff_at': evening(S1), 'status': 'error', 'reason': 'x', 'detail': None, 'output': None,
               'counter': None, 'plan_id': None, 'input_tokens': 0, 'output_tokens': 0,
               'started_at': evening(S1), 'finished_at': evening(S1)}
    analysis_repo.insert_agent_run(db, run_row, [], data_requests=[
        {'family': 'news', 'ticker': 'SYNTH', 'run_id': RUN_ID}])
    db.commit()
    summary, _, _ = run(db, market, 1, tickers=['SYNTH'], clock=LateClock(), run_id=RUN_ID)

    assert order == [] and calls == []                                     # no budget left: nothing runs
    assert summary['agent']['research_requests'] == 0
    assert summary['agent']['data_requests'] == {'queued': 1, 'fetched': 0, 'failed': 0, 'deferred': 1, 'reruns': 0}
    [(_, _, _, status, written, reason, rerun, _)] = _requests(db)
    assert (status, written, reason, rerun) == ('deferred', None, 'agent_budget', None)
    # the research request keeps waiting for the next window
    assert [r['status'] for r in analysis_repo.queued_research_requests(db)] == ['queued']


def test_review_tail_stamps_revision_requests_with_the_run_and_processes_them(db, monkeypatch):
    market = Market()
    signal_day(db, market, ['SYNTH'])
    calls = _fake_adapter(monkeypatch, rows=3)
    revise = _ask(revision('SYNTH'))
    model = make_model(review=[*verdict('SYNTH', 'revise', '止损应上移'),
                               *verdict('SYNTH', 'maintain', '补采后维持', cite=False)],
                       revise=revise, counter=counter())
    # the day's batch review decides "revise" (default next-session condition): its revision asks for data
    summary, rows, model = run(db, market, 1, tickers=['SYNTH'], model=model, run_id=RUN_ID)

    assert summary['agent']['data_requests']['queued'] == 0                # nothing was asked before the review
    assert summary['review']['data_requests'] == {'queued': 1, 'fetched': 1, 'failed': 0, 'deferred': 0,
                                                  'reruns': 1}
    [(family, ticker, run_id, status, written, _, rerun, agent_run)] = _requests(db)
    assert (family, ticker, run_id, status, written) == ('fundamentals', 'SYNTH', RUN_ID, 'fetched', 3)
    assert calls == [('fundamentals', ['SYNTH'], 'data_request', 'data_request', RUN_ID)]
    revise_run = data_repo.agent_run_by_id(db, agent_run)
    assert revise_run['mode'] == 'revise'                                  # the request hangs on the revision run
    by_trigger = {r['trigger']: r for r in reviews(db)}
    assert set(by_trigger) == {'batch', 'recheck'}
    assert by_trigger['recheck']['detail']['via'] == 'data_request'
    assert by_trigger['recheck']['review_agent_run_id'] == rerun
    assert rows['review']['status'] == 'success'
    assert rows['review']['detail']['data_requests']['reruns'] == 1


def test_tail_fetch_failure_settles_failed_without_rerun(db, monkeypatch):
    market = Market()
    _fake_adapter(monkeypatch, ok=False)
    model = make_model(propose=_ask(proposal('SYNTH')), counter=counter())
    summary, _, model = run(db, market, 0, tickers=['SYNTH'], model=model, run_id=RUN_ID)
    assert summary['agent']['data_requests'] == {'queued': 1, 'fetched': 0, 'failed': 1, 'deferred': 0, 'reruns': 0}
    [(_, _, _, status, written, reason, rerun, _)] = _requests(db)
    assert status == 'failed' and written is None and reason and rerun is None
    assert reviews(db) == [] and model.calls.get('review', 0) == 0


def test_tails_are_inert_without_the_031_schema(db, monkeypatch):
    """Before migration 031 the node tails touch no new table and do not fail the stages."""
    market = Market()
    db.execute('ALTER TABLE data_requests RENAME TO data_requests_off')
    db.execute('ALTER TABLE research_requests RENAME TO research_requests_off')
    db.commit()
    monkeypatch.setattr(runner.core_schema, 'five_layers_ready', lambda conn: False)
    model = make_model(propose=proposal('SYNTH'), counter=counter())
    summary, rows, _ = run(db, market, 0, tickers=['SYNTH'], model=model, run_id=RUN_ID)
    assert summary['agent']['data_requests'] == {'queued': 0, 'fetched': 0, 'failed': 0, 'deferred': 0, 'reruns': 0}
    assert summary['agent']['research_requests'] == 0
    assert rows['agent']['status'] == 'success' and rows['review']['status'] == 'success'
