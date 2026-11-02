"""Resuming a failed ``daily`` run from its checkpoint (v2.8 §5): completed nodes do not run again, ``attempt`` rows
are appended for the re-executed ones only, the cutoff and session are the original run's, the window closes at the
next session's New York 17:00. SQL tests on the disposable database; the checkpointer is one shared in-memory saver."""
import investment_assistant.workflow.repo as workflow_repo
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from _daily_db import (NotifyCfg, clock, daily_config, daily_db, job_reports, notices, shared_saver)   # noqa: F401
from test_daily_workflow import (HOLIDAY, RUN_ID, effective, fake_sec, flaky, go, no_fetch_backoff, rows, world)   # noqa: F401
from test_workbench_service import NOW, SESSION, Sources, _agent_settings, _ok_responses, agent_deps, seed_watchlist

import investment_assistant.workflow.check as derivation
import investment_assistant.core.tx as service
import investment_assistant.workflow.graph as workflow
from investment_assistant.tasks import filings, ports

NY = ZoneInfo('America/New_York')
LATER = NOW + timedelta(hours=2)


def counting(stage_id, monkeypatch):
    original = workflow.BODIES[stage_id]
    calls = {'n': 0}

    def body(state, ctx, acc):
        calls['n'] += 1
        return original(state, ctx, acc)

    monkeypatch.setitem(workflow.BODIES, stage_id, body)
    return calls


def resume(clock, factory, node, *, now=LATER, d=None, run_id=RUN_ID):
    return workflow.resume(run_id, node, now=now, deps=d or world(clk=clock), checkpointer_factory=factory,
                           sleep=clock.sleep, notify_cfg=NotifyCfg(), config=daily_config(),
                           send=ports.send, sec_scan=filings._scan)


def failed_run(daily_db, clock, monkeypatch, node='screen', **kwargs):
    """A run that stops at ``node`` (it fails twice); returns the saver factory to resume with."""
    saver, factory = shared_saver()
    flaky(node, monkeypatch, fail_times=2)
    result = go(daily_db, clock, factory=factory, **kwargs)
    assert result['failed_node'] == node
    return factory


def test_resume_skips_completed_nodes(daily_db, clock, notices, monkeypatch):
    first = counting('sec_monitor', monkeypatch)
    second = counting('collect', monkeypatch)
    factory = failed_run(daily_db, clock, monkeypatch)
    out = resume(clock, factory, 'screen')
    assert out['failed_node'] is None and out['status'] in ('success', 'degraded')
    assert (first['n'], second['n']) == (1, 1)                         # their bodies ran once, in the first execution
    for stage in ('sec_monitor', 'collect'):
        assert [r['attempt'] for r in rows(daily_db) if r['stage'] == stage] == [1]
    for stage in ('screen', 'regime', 'signals', 'plan', 'agent', 'review', 'accounts', 'push', 'retro'):
        assert [r['attempt'] for r in rows(daily_db) if r['stage'] == stage] == [1, 2]


def test_resume_appends_attempt_rows_only_for_rerun_nodes(daily_db, clock, notices, monkeypatch):
    factory = failed_run(daily_db, clock, monkeypatch, node='signals')
    before = {r['id'] for r in rows(daily_db)}
    resume(clock, factory, 'signals')
    new = [r for r in rows(daily_db) if r['id'] not in before]
    assert [r['stage'] for r in new] == ['signals', 'plan', 'agent', 'review', 'portfolio', 'accounts', 'push', 'retro', 'propose'] and {r['attempt'] for r in new} == {2}
    old = [r for r in rows(daily_db) if r['id'] in before]
    assert {r['stage'] for r in old if r['attempt'] == 1} == set(workflow.NODES)          # the failed run's thirteen rows stay
    eff = effective(daily_db)
    assert len(eff) == 13 and all(r['attempt'] == (1 if i < 4 else 2) for i, r in enumerate(eff.values()))
    assert eff['signals']['status'] in ('success', 'degraded', 'partial')


def test_resume_reuses_cutoff_and_session(daily_db, clock, notices, monkeypatch):
    factory = failed_run(daily_db, clock, monkeypatch, node='collect')
    out = resume(clock, factory, 'collect', now=NOW + timedelta(hours=5))
    assert out['summary']['cutoff_at'] == NOW.isoformat() and out['summary']['session'] == SESSION.isoformat()
    assert {r['session'] for r in rows(daily_db)} == {SESSION}
    # what the resumed collect node stored carries the original cutoff as its fetch time, not the later resume time
    assert {r[0] for r in daily_db.execute('SELECT DISTINCT fetched_at FROM news_items')} == {NOW}
    assert out['summary']['resumed'] == 1 and out['summary']['resume_node'] == 'collect'


@pytest.mark.parametrize('session, expected_utc', [
    (date(2026, 10, 23), datetime(2026, 10, 26, 21, 0, tzinfo=UTC)),       # Friday → Monday 17:00 EDT
    (date(2026, 11, 25), datetime(2026, 11, 27, 22, 0, tzinfo=UTC)),       # day before Thanksgiving → Friday (EST)
    (date(2026, 10, 30), datetime(2026, 11, 2, 22, 0, tzinfo=UTC)),        # DST ends on Nov 1: Monday 17:00 EST
    (date(2026, 3, 6), datetime(2026, 3, 9, 21, 0, tzinfo=UTC)),           # DST begins on Mar 8: Monday 17:00 EDT
])
def test_resume_deadline_is_next_session_close(session, expected_utc):
    deadline = workflow.resume_deadline(session)
    assert deadline.astimezone(UTC) == expected_utc and deadline.tzinfo is not None
    assert (deadline.hour, deadline.minute, str(deadline.tzinfo)) == (17, 0, 'America/New_York')


@pytest.mark.parametrize('offset, allowed', [(timedelta(minutes=-1), True), (timedelta(0), False), (timedelta(hours=3), False)])
def test_resume_rejected_after_next_session_close(daily_db, clock, notices, monkeypatch, offset, allowed):
    factory = failed_run(daily_db, clock, monkeypatch, node='regime')
    deadline = workflow.resume_deadline(SESSION)
    if allowed:
        info = workflow.resume_check(RUN_ID, 'regime', now=deadline + offset, checkpointer_factory=factory)
        assert info['node'] == 'regime' and info['attempt'] == 2 and info['deadline_at'] == deadline.isoformat()
    else:
        with pytest.raises(workflow.ResumeRejected) as caught:
            workflow.resume_check(RUN_ID, 'regime', now=deadline + offset, checkpointer_factory=factory)
        assert caught.value.code == 'resume_window_closed' and '纽约 17:00' in caught.value.message
        with pytest.raises(workflow.ResumeRejected):
            resume(clock, factory, 'regime', now=deadline + offset)


def test_resume_check_writes_nothing(daily_db, clock, notices, monkeypatch):
    factory = failed_run(daily_db, clock, monkeypatch, node='regime')
    count = len(rows(daily_db))
    workflow.resume_check(RUN_ID, 'regime', now=LATER, checkpointer_factory=factory)
    assert len(rows(daily_db)) == count and daily_db.execute('SELECT count(*) FROM job_reports').fetchone()[0] == 0


def test_resume_node_mismatch(daily_db, clock, notices, monkeypatch):
    factory = failed_run(daily_db, clock, monkeypatch, node='regime')
    with pytest.raises(workflow.ResumeRejected) as caught:
        workflow.resume_check(RUN_ID, 'collect', now=LATER, checkpointer_factory=factory)
    assert caught.value.code == 'node_mismatch' and 'regime' in caught.value.message
    with pytest.raises(ValueError):
        workflow.resume_check(RUN_ID, 'nonsense', now=LATER, checkpointer_factory=factory)


def test_resume_refuses_a_checkpoint_of_another_graph_version(daily_db, clock, notices, monkeypatch):
    """B12: the day the graph is upgraded, a failed run of the old graph (its checkpoint says ``daily-2``) is still inside
    its resume window -- continuing it on the new nodes would mix stage versions, so it is refused (409 at the API)."""
    factory = failed_run(daily_db, clock, monkeypatch, node='regime')
    assert workflow.resume_check(RUN_ID, 'regime', now=LATER, checkpointer_factory=factory)['node'] == 'regime'
    monkeypatch.setattr(workflow, 'DAILY_GRAPH_VERSION', 'daily-4')       # the checkpoint was written by daily-3
    count = len(rows(daily_db))
    with pytest.raises(workflow.ResumeRejected) as caught:
        workflow.resume_check(RUN_ID, 'regime', now=LATER, checkpointer_factory=factory)
    assert caught.value.code == 'graph_version_changed' and 'daily-3' in caught.value.message
    with pytest.raises(workflow.ResumeRejected) as again:
        resume(clock, factory, 'regime', now=LATER)
    assert again.value.code == 'graph_version_changed' and len(rows(daily_db)) == count      # nothing was written


def test_resume_not_failed(daily_db, clock, notices):
    saver, factory = shared_saver()
    go(daily_db, clock, factory=factory)
    with pytest.raises(workflow.ResumeRejected) as caught:
        workflow.resume_check(RUN_ID, 'collect', now=LATER, checkpointer_factory=factory)
    assert caught.value.code == 'not_failed'


def test_resume_check_with_the_checkpoint_tables_missing_is_unavailable(daily_db, clock, notices):
    import psycopg.errors
    from contextlib import contextmanager

    saver, factory = shared_saver()
    go(daily_db, clock, factory=factory)                                  # the run itself has rows

    @contextmanager
    def no_tables():
        raise psycopg.errors.UndefinedTable('relation "checkpoints" does not exist')
        yield

    with pytest.raises(service.Unavailable, match='checkpoint_unavailable'):
        workflow.resume_check(RUN_ID, 'collect', now=LATER, checkpointer_factory=no_tables)


def test_resume_unknown_run(daily_db, clock, notices):
    saver, factory = shared_saver()
    with pytest.raises(LookupError):
        workflow.resume_check('daily-20261022223000-ffffffff', 'collect', now=LATER, checkpointer_factory=factory)


class LateNews(Sources):
    """NVDA's news has one item published before the cutoff and one after it; its SEC listing likewise."""

    def news(self, ticker):
        items = super().news(ticker)
        if ticker == 'NVDA':
            items.append({'content': {'title': 'NVDA after the cutoff', 'pubDate': '2026-10-23T01:00:00Z',
                                      'canonicalUrl': {'url': 'https://news.example.com/late'}}})
        return items

    def discover(self, tickers, start, end):
        early = super().discover(tickers, start, end)
        late = [{**early[0], 'accession_number': '0001-26-000099', 'accepted_at': '2026-10-22T20:30:00-04:00',
                 'edgar_url': 'https://www.sec.gov/Archives/late.htm'}]
        return [*early, *late]


def test_resume_collect_drops_items_after_cutoff(daily_db, clock, notices, monkeypatch):
    seed = [('NVDA', ['core'])]
    seed_watchlist(daily_db, seed)
    factory = failed_run(daily_db, clock, monkeypatch, node='collect', no_seed=True)
    # collect failed in the first execution, so nothing was stored yet
    assert daily_db.execute('SELECT count(*) FROM news_items').fetchone()[0] == 0
    out = resume(clock, factory, 'collect', d=world(sources=LateNews(), clk=clock))
    titles = {r[0] for r in daily_db.execute('SELECT title FROM news_items')}
    assert 'NVDA after the cutoff' not in titles and 'NVDA headline' in titles
    dropped = effective(daily_db)['collect']['detail']['after_cutoff_dropped']
    assert dropped['news'] >= 1 and dropped['sec'] >= 1
    assert out['status'] in ('success', 'degraded')


def test_resume_agent_skips_completed_units(daily_db, clock, notices, scripted_model, monkeypatch):
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    d = agent_deps(scripted_model, _ok_responses('NVDA'))
    d.agent_settings = _agent_settings()
    d.clock = clock.mono
    original = workflow.BODIES['agent']
    calls = {'n': 0}

    def agent_then_die(state, ctx, acc):
        calls['n'] += 1
        out = original(state, ctx, acc)
        if calls['n'] <= 2:
            raise RuntimeError('died after the units')
        return out

    monkeypatch.setitem(workflow.BODIES, 'agent', agent_then_die)
    saver, factory = shared_saver()
    first = go(daily_db, clock, d=d, factory=factory, no_seed=True)
    assert first['failed_node'] == 'agent'
    runs = daily_db.execute('SELECT status FROM agent_runs').fetchall()
    assert [r[0] for r in runs] in (['ok'], ['pass'])
    resume(clock, factory, 'agent', d=d)
    assert daily_db.execute('SELECT count(*) FROM agent_runs').fetchone()[0] == 1           # no second run for the unit
    agent = effective(daily_db)['agent']
    assert agent['attempt'] == 2 and {s['reason'] for s in agent['skips']} == {'already_completed'}


def test_resume_appends_job_report_row(daily_db, clock, notices, job_reports, monkeypatch):
    from investment_assistant.tasks import daily

    flaky('regime', monkeypatch, fail_times=2)
    saver, factory = shared_saver()
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    first = daily.run(daily_config(), trigger_source='manual', now=NOW, deps=world(clk=clock), run_id=RUN_ID,
                      checkpointer_factory=factory, sleep=clock.sleep, notify_cfg=NotifyCfg())
    assert first['status'] == 'error'
    second = daily.resume(RUN_ID, 'regime', now=LATER, deps=world(clk=clock), config=daily_config(),
                          checkpointer_factory=factory, sleep=clock.sleep, notify_cfg=NotifyCfg())
    assert second['status'] in ('success', 'degraded')
    reports = job_reports.rows()
    assert [(r['task'], r['run_id'], r['trigger_source']) for r in reports] == [
        ('daily', RUN_ID, 'manual'), ('daily', RUN_ID, 'resume')]
    assert reports[1]['summary']['resumed'] == 1 and reports[1]['summary']['resume_node'] == 'regime'
    assert reports[1]['target_start_date'] == SESSION
    assert len([t for t in notices.titles('workbench') if '每日工作流失败' in t]) == 1       # the resume succeeded: no 2nd


def test_derivation_stages_accepts_resumed_runs(daily_db, clock, notices, job_reports, monkeypatch):
    from types import SimpleNamespace

    from investment_assistant.tasks import daily

    flaky('regime', monkeypatch, fail_times=2)
    saver, factory = shared_saver()
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    daily.run(daily_config(), trigger_source='manual', now=NOW, deps=world(clk=clock), run_id=RUN_ID,
              checkpointer_factory=factory, sleep=clock.sleep, notify_cfg=NotifyCfg())
    daily.resume(RUN_ID, 'regime', now=LATER, deps=world(clk=clock), config=daily_config(),
                 checkpointer_factory=factory, sleep=clock.sleep, notify_cfg=NotifyCfg())
    lines: list[str] = []
    assert derivation._stages(SimpleNamespace(session=SESSION), lines.append) == 0, '\n'.join(lines)
    assert any('stages-6' in line and '13 行' in line and '含重试' in line for line in lines)
    # a counterfeit: a completed node "ran again" with attempt 2
    row = {key: value for key, value in effective(daily_db)['collect'].items() if key not in ('id', 'created_at')}
    workflow_repo.insert_stage_run(daily_db, {**row, 'attempt': 2})
    lines.clear()
    assert derivation._stages(SimpleNamespace(session=SESSION), lines.append) == 1
    assert any('collect 的 attempt 2 不该存在' in line for line in lines)


def test_resume_with_the_real_postgres_saver(daily_db, clock, notices, monkeypatch):
    """The checkpoint round trip through ``PostgresSaver`` (the production saver): every value the nodes put into the
    state serializes, and a resume in a fresh saver connection continues from the failed node."""
    import os
    import uuid

    from investment_assistant.core.llm import model as agent_model

    url = os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']
    agent_model.setup_checkpoints(url)
    run_id = f'daily-20261022223000-{uuid.uuid4().hex[:8]}'
    factory = lambda: agent_model.checkpointer(url)             # noqa: E731 - a new connection per use, as in production
    flaky('plan', monkeypatch, fail_times=2)
    first = go(daily_db, clock, factory=factory, run_id=run_id)
    assert first['failed_node'] == 'plan'
    out = resume(clock, factory, 'plan', run_id=run_id)
    assert out['failed_node'] is None and out['status'] in ('success', 'degraded')
    assert [r['attempt'] for r in rows(daily_db, run_id) if r['stage'] == 'plan'] == [1, 2]
    assert out['summary']['session'] == SESSION.isoformat() and 'NVDA' in out['summary']['tickers']


def test_resume_from_sec_monitor(daily_db, clock, notices, monkeypatch):
    """The SEC node is the likeliest to fail twice; its resume must work (the node reads the config from its context)."""
    factory = failed_run(daily_db, clock, monkeypatch, node='sec_monitor')
    out = resume(clock, factory, 'sec_monitor')
    assert out['failed_node'] is None and out['status'] in ('success', 'degraded')
    for stage in ('sec_monitor', 'collect'):
        assert [r['attempt'] for r in rows(daily_db) if r['stage'] == stage] == [1, 2]


def test_resume_at_review_restarts_agent_window(daily_db, clock, notices, monkeypatch):
    """D24: a resume counts the Agent window from the resumed execution, not from the first run's ``agent`` node."""
    import investment_assistant.workflow.nodes.review as plan_review

    seen = {}

    def spy(session, **kwargs):
        seen['elapsed'] = kwargs['clock']() - kwargs['started']

    def down(state, ctx, acc):
        raise service.Unavailable('数据库不可达')           # no retry: the node fails at once

    original = workflow.BODIES['review']
    monkeypatch.setitem(workflow.BODIES, 'review', down)
    saver, factory = shared_saver()
    first = go(daily_db, clock, factory=factory)
    assert first['failed_node'] == 'review'
    clock.advance(3 * 3600)                                   # the resume comes three hours later
    monkeypatch.setitem(workflow.BODIES, 'review', original)
    monkeypatch.setattr(plan_review, 'run_stage', spy)
    out = resume(clock, factory, 'review', now=NOW + timedelta(hours=3))
    assert out['failed_node'] is None and seen['elapsed'] < 5          # not 10 800 s, which would skip every review
    assert [r['attempt'] for r in rows(daily_db) if r['stage'] == 'agent'] == [1]      # agent did not run again


def test_resume_rejected_when_the_node_never_wrote_a_row(daily_db, clock, notices, monkeypatch):
    """M3: a process killed inside a node leaves the checkpoint in front of it but no row: rerun the run in full."""
    class Died(BaseException):
        pass

    def dies(state, ctx, acc):
        raise Died()

    monkeypatch.setitem(workflow.BODIES, 'regime', dies)
    saver, factory = shared_saver()
    with pytest.raises(Died):
        go(daily_db, clock, factory=factory)
    assert [r['stage'] for r in rows(daily_db)] == ['sec_monitor', 'collect', 'screen']       # regime has no row
    with pytest.raises(workflow.ResumeRejected) as caught:
        workflow.resume_check(RUN_ID, 'regime', now=LATER, checkpointer_factory=factory)
    assert caught.value.code == 'not_failed'
