"""Recorder-only requests, cutoff-bound indicator/dossier tools, and the D40/D41 SQL tail."""
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock

import psycopg
import pytest
from _daily_db import daily_db as _daily_wired  # noqa: F401  (registers the fixtures)
from _daily_db import job_reports  # noqa: F401  (registers the fixture)
from test_migration_031 import apply, migrated_db  # noqa: F401  (migrated_db registers the fixture)

from investment_assistant.analysis import repo as analysis_repo
from investment_assistant.analysis.agent import runner, tools
from investment_assistant.core import tx as core_tx
from investment_assistant.core.constants import DATA_REQUEST_MIN_S
from investment_assistant.data import repo as data_repo
from investment_assistant.data.fetch_policy import Fetched, FetchError
from investment_assistant.data.tally import Tally

NOW = datetime(2026, 10, 30, 22, tzinfo=UTC)
SESSION = date(2026, 10, 30)


@pytest.fixture
def wired_db(request):
    """The daily 031 schema + wired transactions, pulled by name so no parameter shadows its import."""
    return request.getfixturevalue('_daily_wired')


@pytest.fixture
def db_031(request):
    """Every migration through 031, unwired: the R14 atomicity test needs no service wiring."""
    db = request.getfixturevalue('migrated_db')
    apply(db)
    return db


def test_queues_request_in_recorder_only():
    read = Mock(side_effect=AssertionError('tool must not read or write'))
    ctx = tools.ToolContext('AMD', SESSION, NOW, read)
    recorder = tools.Recorder()
    result = tools.call(ctx, recorder, 'request_data', {'family': 'fundamentals', 'ticker': 'amd'},
                        now=NOW, tools={'request_data': tools.request_data})
    assert recorder.data_requests == [{'family': 'fundamentals', 'ticker': 'AMD'}]
    assert result == {'ref': 'call:1', 'data': {'family': 'fundamentals', 'ticker': 'AMD',
                     'status': 'queued', 'note': '本轮不可见；宿主补采后开新一轮'}}
    read.assert_not_called()


@pytest.mark.parametrize('family,ticker', [('bars', 'TSLA'), ('unknown', 'AMD'), ('fred', 'AMD'), ('universe', 'AMD')])
def test_out_of_scope_ticker_and_unknown_family_write_nothing(family, ticker):
    read = Mock(side_effect=AssertionError('must not touch read port'))
    ctx = tools.ToolContext('AMD', SESSION, NOW, read)
    recorder = tools.Recorder()
    result = tools.call(ctx, recorder, 'request_data', {'family': family, 'ticker': ticker}, now=NOW,
                        tools={'request_data': tools.request_data})
    assert 'error' in result
    assert recorder.data_requests == []
    read.assert_not_called()


def test_get_indicator_preserves_basis_and_cutoff(monkeypatch):
    seen = []
    rows = [{'price_date': SESSION - timedelta(days=i), 'close': 10, 'adj_close': 20,
             'open': 10, 'high': 11, 'low': 9, 'volume': 100} for i in reversed(range(30))]
    def read(conn, ticker, session, *, cutoff_at, **kwargs):
        seen.append((ticker, session, cutoff_at))
        return rows
    monkeypatch.setattr(data_repo, 'read_bars', read)
    ctx = tools.ToolContext('AMD', SESSION, NOW, lambda fn: fn(None))
    result = tools.get_indicator(ctx, 'AMD', 'ma20_close')
    assert result == {'id': 'ma20_close', 'basis': 'close', 'window': 20, 'value': 10.0,
                      'computed_through': SESSION.isoformat(), 'version': 'indicator-1'}
    assert tools.get_indicator(ctx, 'AMD', 'ma20_adj_close')['value'] == 20.0
    assert all(session == SESSION and cutoff == NOW for _, session, cutoff in seen)


def test_get_research_dossier_respects_scope_and_cutoff(monkeypatch):
    seen = []
    def read(conn, ticker, *, cutoff_at):
        seen.append((ticker, cutoff_at))
        return {'id': 8, 'cutoff_at': cutoff_at, 'action': 'buy'}
    monkeypatch.setattr(analysis_repo, 'latest_dossier_for', read)
    ctx = tools.ToolContext('AMD', SESSION, NOW, lambda fn: fn(None))
    dossier = tools.get_research_dossier(ctx, 'amd')
    assert dossier is not None and dossier['id'] == 8
    assert seen == [('AMD', NOW)]
    with pytest.raises(tools.ToolError):
        tools.get_research_dossier(ctx, 'TSLA')
    assert len(seen) == 1


# ---------------------------------------------------------------- SQL (R14, §5.5, D40/D41)

def _run_row(*, status='rejected', mode='propose', attempt=1, plan_id=None, reason=None):
    if reason is None and status not in ('ok', 'pass'):      # agent_runs_check1: a failure carries its reason
        reason = 'invalid_citation'
    return {'id': str(uuid.uuid4()), 'ticker': 'AMD', 'signal_date': SESSION, 'pool': 'main',
            'attempt': attempt, 'mode': mode, 'agent_version': 'agent-4', 'provider': 'chatgpt',
            'model': 'gpt-test', 'cutoff_at': NOW, 'status': status, 'reason': reason, 'detail': None,
            'output': None, 'counter': None, 'plan_id': plan_id, 'input_tokens': 0, 'output_tokens': 0,
            'started_at': NOW, 'finished_at': NOW}


def _call_row(seq=1):
    return {'seq': seq, 'phase': 'propose', 'tool': 'request_data', 'args': {'family': 'fundamentals'},
            'result': {'ref': f'call:{seq}'}, 'result_sha256': '0' * 64, 'called_at': NOW}


def _plan_id(conn):
    """A real trade_plans row: an ok propose run points at one (CHECK + FK)."""
    plan = uuid.uuid4()
    conn.execute('INSERT INTO trade_plans (id,ticker,signal_date,kind,plan_version,ref_price,atr,'
                 'entry_low,entry_high,stop,target1,valid_from,valid_until,sizing,inputs,status) '
                 "VALUES (%s,'AMD',%s,'rule','plan-1',100,2,99,101,95,110,%s,%s,'{}','{}','ok')",
                 (plan, SESSION, SESSION, SESSION + timedelta(days=10)))
    return plan


def test_written_with_agent_run_in_same_tx(db_031):
    """R14: run, calls and the queued requests share one caller transaction; a later failure
    inside it rolls the whole run back — requests never outlive their run."""
    workbench_db = db_031
    run = _run_row()
    record = analysis_repo.insert_agent_run(workbench_db, run, [_call_row()], data_requests=[
        {'family': 'fundamentals', 'ticker': 'amd', 'run_id': 'daily-r1'}])
    assert str(record['id']) == run['id']
    workbench_db.commit()
    queued = data_repo.queued_data_requests(workbench_db, 'daily-r1')
    assert len(queued) == 1 and queued[0]['family'] == 'fundamentals'
    assert str(queued[0]['agent_run_id']) == run['id'] and queued[0]['status'] == 'queued'
    assert workbench_db.execute('SELECT count(*) FROM agent_tool_calls WHERE run_id = %s',
                                (run['id'],)).fetchone()[0] == 1

    second = _run_row()      # the same transaction writes the run and its request…
    analysis_repo.insert_agent_run(workbench_db, second, [], data_requests=[{'family': 'news', 'ticker': 'AMD'}])
    with pytest.raises(psycopg.errors.UniqueViolation):
        analysis_repo.insert_agent_run(workbench_db, second, [])   # …then fails: everything rolls back
    workbench_db.rollback()
    assert workbench_db.execute('SELECT count(*) FROM agent_runs WHERE id = %s', (second['id'],)).fetchone()[0] == 0
    assert data_repo.queued_data_requests(workbench_db, 'daily-r1') == queued


def _fake_adapter_run(monkeypatch, *, rows=2, ok=True):
    calls = []

    def traced(adapter, targets, **kwargs):
        calls.append((adapter.id, list(targets), kwargs['scope'], kwargs['trigger']))
        tally = Tally(adapter.id, 'agent', targets)
        for target in targets:
            if ok:
                tally.record(target, Fetched([{}], [], 0), rows=rows, latest=SESSION)
            else:
                tally.record(target, FetchError('socket down', kind='network'))
        return tally

    monkeypatch.setattr('investment_assistant.data.registry.run_adapter', traced)
    return calls


def test_process_requests_defers_when_budget_short(wired_db, monkeypatch):
    def never(adapter, targets, **kwargs):
        pytest.fail('no fetch may run without budget')

    monkeypatch.setattr('investment_assistant.data.registry.run_adapter', never)
    analysis_repo.insert_agent_run(wired_db, _run_row(), [_call_row()], data_requests=[
        {'family': 'fundamentals', 'ticker': 'amd', 'run_id': 'daily-r1'}])
    wired_db.commit()
    counts = runner.process_data_requests(SESSION, run_id='daily-r1', now=NOW, deps=core_tx.Deps(),
                                          features={'backup_sources': False},
                                          budget_left_s=DATA_REQUEST_MIN_S - 1)
    assert counts == {'queued': 1, 'fetched': 0, 'failed': 0, 'deferred': 1, 'reruns': 0}
    row = wired_db.execute('SELECT status, rows, reason, rerun_run_id, fetch_run_id FROM data_requests').fetchone()
    assert row == ('deferred', None, 'agent_budget', None, None)


def test_process_requests_reruns_once_via_recheck_for_opened_chain(wired_db, monkeypatch):
    rerun = uuid.uuid4()
    analysis_repo.insert_agent_run(wired_db, _run_row(status='ok', plan_id=_plan_id(wired_db)), [])
    analysis_repo.insert_agent_run(wired_db, _run_row(mode='revise', attempt=2), [], data_requests=[
        {'family': 'fundamentals', 'ticker': 'AMD', 'run_id': 'daily-r1'}])
    wired_db.commit()
    traced = _fake_adapter_run(monkeypatch, rows=2)
    reviews = []

    def fake_review(obj, *, session, now, trigger, run_id, deps, settings, started, clock, quota, via,
                    breadth_tickers=None):
        reviews.append((obj['ticker'], obj['signal_date'], trigger, via))
        return {'revision_agent_run_id': rerun}

    monkeypatch.setattr(runner, 'run_chain_review', fake_review)
    counts = runner.process_data_requests(SESSION, run_id='daily-r1', now=NOW, deps=core_tx.Deps(),
                                          features={'backup_sources': False}, budget_left_s=600)
    assert counts == {'queued': 1, 'fetched': 1, 'failed': 0, 'deferred': 0, 'reruns': 1}
    assert reviews == [('AMD', SESSION, 'recheck', 'data_request')]
    assert traced[0][2:] == ('data_request', 'data_request')
    row = wired_db.execute('SELECT status, rows, rerun_run_id, fetch_run_id FROM data_requests').fetchone()
    assert row[0] == 'fetched' and row[1] == 2 and row[2] == rerun and row[3] is not None

    # D40: the unit's one rerun of the day is spent — a second queued request settles but never reruns again
    analysis_repo.insert_agent_run(wired_db, _run_row(mode='revise', attempt=3), [], data_requests=[
        {'family': 'news', 'ticker': 'AMD', 'run_id': 'daily-r1'}])
    wired_db.commit()
    counts = runner.process_data_requests(SESSION, run_id='daily-r1', now=NOW, deps=core_tx.Deps(),
                                          features={'backup_sources': False}, budget_left_s=600)
    assert counts['reruns'] == 0 and counts['fetched'] == 1 and len(reviews) == 1


def _rerun_deps(**kwargs):
    """Deps whose agent is available (the unopened rerun is gated like any agent work, R36) and whose clock is fixed."""
    from investment_assistant.core.llm.model import AgentSettings

    return core_tx.Deps(agent_settings=AgentSettings(provider='chatgpt', model='gpt-test', api_key='sk-test',
                                                     daily_tokens=None, config_error=None),
                        clock=lambda: 0.0, **kwargs)


def test_process_requests_reruns_propose_attempt_for_unopened_unit(wired_db, monkeypatch):
    analysis_repo.insert_agent_run(wired_db, _run_row(status='rejected', reason='invalid_citation'), [],
                                   data_requests=[{'family': 'fundamentals', 'ticker': 'AMD',
                                                   'run_id': 'daily-r1'}])
    wired_db.commit()
    _fake_adapter_run(monkeypatch, rows=3)
    rerun, seen = uuid.uuid4(), {}
    monkeypatch.setattr(runner, '_unit_for_request_rerun',
                        lambda conn, ticker, signal_date, *, prior, now:
                        {'ticker': ticker, 'signal_date': signal_date, 'pool': 'main', 'pool_tier': 'core'})

    def fake_propose(unit, *, now, deps, settings, attempt, run_id, features=None,
                     carried_research=None, previous_run_id=None, breadth_tickers=None):
        seen.update(attempt=attempt, run_id=run_id, ticker=unit['ticker'],
                    carried=carried_research, features=features)
        return {'id': rerun}

    monkeypatch.setattr(runner, 'run_agent_unit', fake_propose)
    counts = runner.process_data_requests(SESSION, run_id='daily-r1', now=NOW, deps=_rerun_deps(),
                                          features={'backup_sources': False}, budget_left_s=600)
    assert counts == {'queued': 1, 'fetched': 1, 'failed': 0, 'deferred': 0, 'reruns': 1}
    assert seen == {'attempt': 2, 'run_id': 'daily-r1', 'ticker': 'AMD',      # attempt + 1 (D40)
                    'carried': None,
                    'features': {'backup_sources': False, 'company_researcher': False}}  # no segment: researcher off (D42)
    row = wired_db.execute('SELECT status, rows, rerun_run_id FROM data_requests').fetchone()
    assert row == ('fetched', 3, rerun)


# ---------------------------------------------------------------- fix round 1: gates (R36) and the leftover queue

def _unopened_unit(wired_db, monkeypatch, *, tokens=0):
    analysis_repo.insert_agent_run(wired_db, {**_run_row(status='rejected', reason='invalid_citation'),
                                              'input_tokens': tokens}, [],
                                   data_requests=[{'family': 'fundamentals', 'ticker': 'AMD', 'run_id': 'daily-r1'}])
    wired_db.commit()
    _fake_adapter_run(monkeypatch, rows=3)
    monkeypatch.setattr(runner, '_unit_for_request_rerun',
                        lambda conn, ticker, signal_date, *, prior, now:
                        {'ticker': ticker, 'signal_date': signal_date, 'pool': 'main', 'pool_tier': 'core'})
    ran = []
    monkeypatch.setattr(runner, 'run_agent_unit', lambda unit, **kwargs: ran.append(unit) or {'id': uuid.uuid4()})
    return ran


def test_unopened_rerun_obeys_the_shared_agent_deadline(wired_db, monkeypatch):
    """R36: the rerun is agent work inside the shared AGENT_DEADLINE_S window; past it nothing reruns and no attempt
    is spent (the request stays fetched without a rerun pointer)."""
    ran = _unopened_unit(wired_db, monkeypatch)
    ticks = iter([0.0] + [10_000.0] * 20)
    counts = runner.process_data_requests(SESSION, run_id='daily-r1', now=NOW, deps=_rerun_deps_with(lambda: next(ticks)),
                                          features={}, budget_left_s=600)
    assert counts == {'queued': 1, 'fetched': 1, 'failed': 0, 'deferred': 0, 'reruns': 0} and ran == []
    assert wired_db.execute('SELECT rerun_run_id FROM data_requests').fetchone()[0] is None
    assert wired_db.execute("SELECT count(*) FROM agent_runs WHERE status = 'skipped'").fetchone()[0] == 0


def test_unopened_rerun_obeys_the_daily_token_cap(wired_db, monkeypatch):
    ran = _unopened_unit(wired_db, monkeypatch, tokens=500)
    deps = _rerun_deps_with(lambda: 0.0, daily_tokens=500)
    counts = runner.process_data_requests(SESSION, run_id='daily-r1', now=NOW, deps=deps, features={},
                                          budget_left_s=600)
    assert counts['fetched'] == 1 and counts['reruns'] == 0 and ran == []
    # under the cap the same rerun happens
    wired_db.execute('DELETE FROM data_requests')
    analysis_repo.insert_agent_run(wired_db, _run_row(status='rejected', attempt=2), [], data_requests=[
        {'family': 'news', 'ticker': 'AMD', 'run_id': 'daily-r1'}])
    wired_db.commit()
    counts = runner.process_data_requests(SESSION, run_id='daily-r1', now=NOW,
                                          deps=_rerun_deps_with(lambda: 0.0, daily_tokens=10_000), features={},
                                          budget_left_s=600)
    assert counts['reruns'] == 1 and len(ran) == 1


def _rerun_deps_with(clock, daily_tokens=None):
    from investment_assistant.core.llm.model import AgentSettings

    return core_tx.Deps(agent_settings=AgentSettings(provider='chatgpt', model='gpt-test', api_key='sk-test',
                                                     daily_tokens=daily_tokens, config_error=None), clock=clock)


def test_defer_leftover_settles_only_this_runs_queue(wired_db):
    """§5.5: what the review tail's own reruns queued after the last handler pass settles ``deferred`` — this run's
    only; another run's and the manual (NULL) flows' rows are not its to close."""
    run = _run_row()
    analysis_repo.insert_agent_run(wired_db, run, [], data_requests=[
        {'family': 'fundamentals', 'ticker': 'AMD', 'run_id': 'daily-r1'},
        {'family': 'news', 'ticker': 'AMD', 'run_id': 'daily-r2'},
        {'family': 'sector', 'ticker': 'AMD'}])
    wired_db.commit()
    assert runner.defer_leftover_data_requests('daily-r1') == 1
    rows = wired_db.execute('SELECT family, status, reason FROM data_requests ORDER BY id').fetchall()
    assert rows == [('fundamentals', 'deferred', 'run_ended'), ('news', 'queued', None), ('sector', 'queued', None)]
    assert runner.defer_leftover_data_requests(None) == 0              # manual flows are nobody's run
    assert runner.defer_leftover_data_requests('daily-r1') == 0         # idempotent
    # the check's one-day rule now sees only the rows nobody owns
    stale = data_repo.stale_queued_data_requests(wired_db, before=NOW + timedelta(days=2))
    assert sorted(r['family'] for r in stale) == ['news', 'sector']


def test_review_node_defers_what_its_tail_leaves_queued(wired_db, monkeypatch):
    """The review node ends with the deferral, whatever its stage did; its summary counts move with it."""
    from investment_assistant.workflow.nodes import review as review_node

    analysis_repo.insert_agent_run(wired_db, _run_row(), [], data_requests=[
        {'family': 'fundamentals', 'ticker': 'AMD', 'run_id': 'daily-r1'}])
    wired_db.commit()
    summary = {'errors': [], 'review': {'data_requests': {'queued': 2, 'fetched': 1, 'failed': 0, 'deferred': 1,
                                                          'reruns': 1}}}
    review_node._defer_leftover_requests({'run_id': 'daily-r1'}, summary, None)
    assert summary['review']['data_requests'] == {'queued': 3, 'fetched': 1, 'failed': 0, 'deferred': 2, 'reruns': 1}
    assert wired_db.execute("SELECT count(*) FROM data_requests WHERE status = 'queued'").fetchone()[0] == 0
    review_node._defer_leftover_requests({'run_id': None}, summary, None)          # a shell without a run id: nothing
    assert summary['errors'] == []
