"""The ``daily`` task shell (v2.8 §4.7, §5.9): one ``job_reports`` row per execution, the status mapping, the
``daily:run`` lock, the node table CLI and the manual-only ``sec_backfill``. SQL tests on the disposable database."""
import investment_assistant.core.schema as core_schema
from datetime import UTC, date, datetime, timedelta

import pytest

from _daily_db import (NotifyCfg, clock, daily_config, daily_db, job_reports, notices, shared_saver)   # noqa: F401
from test_daily_workflow import RUN_ID, effective, fake_sec, flaky, no_fetch_backoff, rows, world   # noqa: F401
from test_workbench_service import NOW, SESSION, _agent_settings, _ok_responses, seed_watchlist

from investment_assistant.tasks import daily, filings
from investment_assistant.core import locks
import investment_assistant.core.tx as service
import investment_assistant.workflow.graph as workflow


def run_daily(clock, factory=None, **kwargs):
    kwargs.setdefault('trigger_source', 'manual')
    return daily.run(daily_config(), now=NOW, deps=world(clk=clock), run_id=RUN_ID,
                     checkpointer_factory=factory or shared_saver()[1], sleep=clock.sleep, notify_cfg=NotifyCfg(),
                     **kwargs)


def test_job_report_row_per_execution(daily_db, clock, notices, job_reports, monkeypatch):
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    flaky('regime', monkeypatch, fail_times=2)
    saver, factory = shared_saver()
    run_daily(clock, factory)
    assert len(job_reports.rows()) == 1
    daily.resume(RUN_ID, 'regime', now=NOW, deps=world(clk=clock), config=daily_config(),
                 checkpointer_factory=factory, sleep=clock.sleep, notify_cfg=NotifyCfg())
    reports = job_reports.rows()
    assert [(r['task'], r['run_id']) for r in reports] == [('daily', RUN_ID)] * 2
    assert [r['trigger_source'] for r in reports] == ['manual', 'resume']
    assert all(r['target_start_date'] == SESSION for r in reports)
    assert reports[0]['summary']['graph_version'] == 'daily-3' and reports[0]['summary']['failed_node'] == 'regime'
    assert reports[0]['summary']['stage_status']['written'] == 13


def test_status_mapping_success_degraded_error(daily_db, clock, notices, job_reports, monkeypatch):
    assert daily.JOB_STATUS == {'success': 'success', 'degraded': 'degraded', 'failed': 'error'}
    for run_status, expected in (('success', 'success'), ('degraded', 'degraded'), ('failed', 'error')):
        out = daily._report({'run_id': f'daily-2026102200000{len(job_reports.rows())}-aaaaaaa{len(job_reports.rows())}',
                             'status': run_status, 'summary': {'run_status': run_status}, 'session': '2026-10-22',
                             'failed_node': 'collect' if run_status == 'failed' else None, 'failure': None},
                            started_at=clock.wall(), trigger_source='manual', config=daily_config(),
                            notify_cfg=NotifyCfg())
        assert out['status'] == expected
    assert [r['status'] for r in job_reports.rows()] == ['success', 'degraded', 'error']
    assert len([t for t in notices.titles('workbench') if '每日工作流失败' in t]) == 1       # only the failed one


def test_a_real_degraded_run_maps_to_degraded(daily_db, clock, notices, job_reports):
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    out = run_daily(clock)
    assert out['run_status'] == 'degraded' and out['status'] == 'degraded' and notices.calls == []   # degraded: no notice


def test_daily_running_lock(daily_db, clock, notices, job_reports):
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    with locks.exclusive():
        with pytest.raises(locks.DailyBusy):
            run_daily(clock, trigger_source='manual')
        with pytest.raises(locks.DailyBusy):
            daily.resume(RUN_ID, 'collect', now=NOW, config=daily_config())
        out = run_daily(clock, trigger_source='scheduled')                      # a scheduled tick records and goes on
    assert out['status'] == 'error' and out['summary']['error'] == 'daily_running'
    assert [(r['status'], r['summary']['error']) for r in job_reports.rows()] == [('error', 'daily_running')]
    assert daily_db.execute('SELECT count(*) FROM stage_runs').fetchone()[0] == 0
    assert notices.calls == []


def test_checkpoint_tables_missing_refuses(daily_db, clock, notices, job_reports):
    import psycopg.errors
    from contextlib import contextmanager

    @contextmanager
    def no_tables():
        raise psycopg.errors.UndefinedTable('relation "checkpoints" does not exist')
        yield

    seed_watchlist(daily_db, [('NVDA', ['core'])])
    out = run_daily(clock, no_tables)
    assert out['status'] == 'error' and out['summary']['error'] == 'checkpoint_unavailable'


def test_cli_show_has_no_raw_summary(daily_db, clock, notices, job_reports, capsys):
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    run_daily(clock)
    assert daily.main(['show', '--run-id', RUN_ID]) == 0
    out = capsys.readouterr().out
    for node in workflow.NODES:
        assert node in out
    assert 'attempt 1' in out and '失败目标' in out
    assert '{' not in out and 'cutoff_at' not in out and 'summary' not in out          # the node table, not the raw JSON
    assert daily.main(['show', '--run-id', 'daily-20260101000000-00000000']) == 2


def test_cli_resume_reports_rejections(daily_db, clock, notices, capsys, monkeypatch):  # noqa: F811 - shared fixtures
    monkeypatch.setattr(workflow, '_default_checkpointer', shared_saver()[1])
    run_id = 'daily-20260101000000-00000000'
    assert daily.main(['resume', '--run-id', run_id, '--node', 'collect']) == 2
    assert capsys.readouterr().err.strip() == f'没有这次运行：{run_id}'


def test_sec_backfill_job_report_task(daily_db, clock, notices, job_reports, fake_sec):
    fake_sec.result = {'tickers': ['NVDA'], 'filings': [], 'errors': {}, 'downloaded_count': 0, 'event_ids': []}
    class Source:
        def discover_one(self, *args, **kwargs):
            return []
    out = filings.backfill(daily_config(), start_date=date(2026, 10, 1), end_date=date(2026, 10, 5),
                           deps=service.Deps(filing_source=Source()))
    assert out['task'] == 'sec_backfill' and out['status'] == 'success'
    assert [(r['task'], r['trigger_source']) for r in job_reports.rows()] == [('sec_backfill', 'manual')]
    assert job_reports.rows()[0]['target_start_date'] == date(2026, 10, 1)
    fetch = daily_db.execute("SELECT job, trigger, run_id, family, detail->>'scope' FROM data_fetch_runs").fetchall()
    assert fetch == [('sec_backfill', 'manual_family', out['run_id'], 'sec_text', 'sec_monitor')]       # D47
    assert daily_db.execute('SELECT count(*) FROM stage_runs').fetchone()[0] == 0           # outside the graph
    with locks.exclusive():                                                                  # and under the daily lock
        with pytest.raises(locks.DailyBusy):
            filings.backfill(daily_config(), start_date=date(2026, 10, 1))


from langgraph.checkpoint.memory import InMemorySaver


class _BrokenSaver(InMemorySaver):
    """An in-memory saver whose Nth checkpoint write fails like a dropped database connection."""

    def __init__(self, fail_on):
        super().__init__()
        self.fail_on, self.puts = fail_on, 0

    def put(self, *args, **kwargs):
        import psycopg

        self.puts += 1
        if self.puts == self.fail_on:
            raise psycopg.OperationalError('server closed the connection unexpectedly')
        return super().put(*args, **kwargs)


def test_other_exceptions_still_get_a_report_rows_and_one_notice(daily_db, clock, notices, job_reports):
    from contextlib import nullcontext

    seed_watchlist(daily_db, [('NVDA', ['core'])])
    saver = _BrokenSaver(fail_on=4)                                   # after a few nodes the connection drops
    out = run_daily(clock, lambda: nullcontext(saver))
    assert out['status'] == 'error' and 'aborted' in out['summary']['error'] and 'closed the connection' in out['summary']['error']
    assert [r['status'] for r in job_reports.rows()] == ['error']                       # the execution has its row
    eff = workflow.effective_rows(rows(daily_db))
    assert list(eff) == list(workflow.NODES)                                              # still thirteen effective rows
    assert [n for n in workflow.NODES if eff[n]['status'] == 'failed'] and eff['retro']['reason'] == 'upstream_failed'
    assert len([t for t in notices.titles('workbench') if '每日工作流失败' in t]) == 1


def test_a_failure_before_the_graph_starts_is_reported_too(daily_db, clock, notices, job_reports, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError('config unreadable')

    monkeypatch.setattr(workflow, '_make_context', boom)
    out = run_daily(clock)
    assert out['status'] == 'error' and out['summary']['error'] == 'aborted: config unreadable'
    assert [r['status'] for r in job_reports.rows()] == ['error']
    assert len(notices.titles('workbench')) == 1 and daily_db.execute('SELECT count(*) FROM stage_runs').fetchone()[0] == 0


def test_resume_aborting_outside_a_node_is_reported(daily_db, clock, notices, job_reports, monkeypatch):
    from contextlib import nullcontext

    seed_watchlist(daily_db, [('NVDA', ['core'])])
    flaky('regime', monkeypatch, fail_times=2)
    saver = _BrokenSaver(fail_on=10 ** 9)
    run_daily(clock, lambda: nullcontext(saver))
    saver.fail_on = saver.puts + 2
    out = daily.resume(RUN_ID, 'regime', now=NOW, deps=world(clk=clock), config=daily_config(),
                       checkpointer_factory=lambda: nullcontext(saver), sleep=clock.sleep, notify_cfg=NotifyCfg())
    assert out['status'] == 'error' and 'aborted' in out['summary']['error']
    assert [r['trigger_source'] for r in job_reports.rows()] == ['manual', 'resume']
    assert len([t for t in notices.titles('workbench') if '每日工作流失败' in t]) == 2     # one per failed execution


def test_failure_notice_names_the_booked_failed_stage(daily_db, clock, notices, job_reports, monkeypatch):
    """M1: a stage that books ``failed`` without stopping the chain (retro cards) still names itself in the notice."""
    from investment_assistant.review import retro_cards

    def boom(*args, **kwargs):
        raise RuntimeError('retro exploded')

    monkeypatch.setattr(retro_cards, 'run_stage', boom)
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    out = run_daily(clock, trigger_source='scheduled')
    assert out['status'] == 'error' and out['summary']['failed_node'] == 'retro'
    [notice] = [n for t, items in notices.calls if t == 'workbench' for n in items if '每日工作流失败' in n.title]
    fields = {f.name: f.value for f in notice.fields}
    assert fields['失败节点'] == '复盘' and 'retro exploded' in fields['原因']
    assert '未执行' not in fields and '续跑' not in fields          # nothing after retro, and a finished chain: no resume


def test_is_running_only_looks_at_the_lock(daily_db):
    assert locks.is_running() is False
    with locks.exclusive():
        assert locks.is_running() is True
        assert locks.is_running() is True                         # probing never takes or releases it
    assert locks.is_running() is False
    with locks.exclusive():                                       # and a probe leaves nothing behind
        pass


# ---------------------------------------------------------------- connection-lost notice (v2.8 §8.8, §13.2)

class Episode:
    """``agent_connection.first_failure_since_ok`` with one failure at ``failure_at``: true for a look whose base is not
    after it (the real rule, minus the events table); ``status`` is what ``status_view`` says."""

    def __init__(self):
        self.failure_at = datetime(2000, 1, 1, tzinfo=UTC)
        self.status = 'needs_login'
        self.calls: list = []

    def __call__(self, *, since=None):
        self.calls.append(since)
        return since is None or self.failure_at >= since


@pytest.fixture
def episode(monkeypatch):
    from investment_assistant.core.llm import connection as agent_connection

    fake = Episode()
    monkeypatch.setattr(agent_connection, 'first_failure_since_ok', fake)
    monkeypatch.setattr(agent_connection, 'status_view', lambda **kw: {'status': fake.status})
    return fake


def run_with_connection(clock, number, connection, *, now=NOW, trigger_source='manual', scripted=None):  # noqa: F811
    d = world(clk=clock, agent=True, scripted=scripted)
    d.agent_settings = _agent_settings(connection=connection)
    return daily.run(daily_config(), now=now, deps=d, run_id=f'daily-2026102300000{number}-aaaaaaa{number}',
                     checkpointer_factory=shared_saver()[1], sleep=clock.sleep, notify_cfg=NotifyCfg(),
                     trigger_source=trigger_source)


def lost_titles(notices):
    return [t for t in notices.titles('workbench') if t == '⚠️ ChatGPT 连接已失效']


def checked(out):
    return datetime.fromisoformat(out['summary']['connection_notice']['checked_at'])


def test_not_connected_notifies_once_per_episode(daily_db, clock, notices, job_reports, episode):
    """A failure during run 1 is told by run 1 and not again by run 2; a failure after that (reconnected, lost again)
    is a new episode told by the next execution."""
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    episode.failure_at = datetime.now(UTC)                                # it happens while run 1 is going
    first = run_with_connection(clock, 1, 'needs_login')
    assert first['status'] == 'degraded'                                  # the agent being unavailable never fails the run
    eff = effective(daily_db, 'daily-20261023000001-aaaaaaa1')
    assert eff['agent']['status'] == 'skipped' and eff['agent']['reason'] == 'agent_unavailable: not_connected'
    assert len(lost_titles(notices)) == 1 and episode.calls == [None]    # nothing was ever sent: any failure counts
    assert first['summary']['connection_notice']['sent'] is True and first['summary']['connection_notice']['reason'] is None
    [fields] = [n.fields for t, items in notices.calls for n in items if n.title == '⚠️ ChatGPT 连接已失效']
    assert [(f.name, f.value) for f in fields] == [('影响', '今天的 Agent 建议与复查已跳过'),
                                                  ('处理', '到「设置 → Agent 连接」重新连接')]
    second = run_with_connection(clock, 2, 'needs_login')                 # still lost, already told
    assert len(lost_titles(notices)) == 1 and episode.calls[-1] == checked(first)
    assert second['summary']['connection_notice'] == {'sent': False, 'reason': 'already_reported',
                                                      'checked_at': second['summary']['connection_notice']['checked_at']}
    episode.failure_at = datetime.now(UTC)                                # reconnected, then lost again after run 2
    third = run_with_connection(clock, 3, 'needs_login')
    assert len(lost_titles(notices)) == 2 and third['summary']['connection_notice']['sent'] is True
    assert episode.calls[-1] == checked(first)                            # the base is the last notice actually sent
    fourth = run_with_connection(clock, 4, 'needs_login')
    assert len(lost_titles(notices)) == 2 and episode.calls[-1] == checked(third)
    assert fourth['summary']['connection_notice']['reason'] == 'already_reported'


def test_an_execution_that_does_not_hit_in_between_does_not_hide_the_failure(daily_db, clock, notices, job_reports,
                                                                           episode, monkeypatch, scripted_model):
    """Lost -> an execution that never reaches the Agent (connection fine as far as it can tell, or stopped before
    ``agent``) -> the execution that hits: exactly one notice, the in-between ones never moved the base."""
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    ok = run_with_connection(clock, 1, 'connected', scripted=scripted_model(_ok_responses()))
    assert ok['summary']['agent']['runs'] == {'NVDA': 'ok'}
    assert 'connection_notice' not in ok['summary']
    flaky('collect', monkeypatch, fail_times=2)                           # a run that stops before ``agent``
    stopped = run_with_connection(clock, 2, 'needs_login')
    assert stopped['run_status'] == 'failed' and 'connection_notice' not in stopped['summary']
    holiday = run_with_connection(clock, 3, 'needs_login', now=datetime(2026, 10, 24, 22, 30, tzinfo=UTC),
                                  trigger_source='scheduled')
    assert 'connection_notice' not in holiday['summary']                  # a closed market: ``agent`` is skipped for that
    assert episode.calls == [] and lost_titles(notices) == []
    hit = run_with_connection(clock, 4, 'needs_login')
    assert lost_titles(notices) == ['⚠️ ChatGPT 连接已失效'] and episode.calls == [None]
    assert hit['summary']['connection_notice']['sent'] is True
    again = run_with_connection(clock, 5, 'needs_login')
    assert len(lost_titles(notices)) == 1 and again['summary']['connection_notice']['reason'] == 'already_reported'


def test_a_look_that_finds_the_connection_fine_again_says_not_failed(daily_db, clock, notices, job_reports, episode):
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    episode.failure_at = datetime(2000, 1, 1, tzinfo=UTC)
    first = run_with_connection(clock, 1, 'needs_login')
    episode.status = 'connected'                                          # reconnected before the next look
    second = run_with_connection(clock, 2, 'needs_login')
    assert first['summary']['connection_notice']['sent'] is True
    assert second['summary']['connection_notice']['reason'] == 'not_failed' and len(lost_titles(notices)) == 1


def test_connection_notice_is_retried_until_it_was_sent(daily_db, clock, notices, job_reports, episode):
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    notices.result = {'sent': False, 'messages': 0, 'reason': 'discord_disabled'}
    first = run_with_connection(clock, 1, 'needs_login')
    assert first['summary']['connection_notice']['sent'] is False
    assert first['summary']['connection_notice']['reason'] == 'discord_disabled'
    notices.result = {'sent': True, 'messages': 1}
    second = run_with_connection(clock, 2, 'needs_login')
    assert second['summary']['connection_notice']['sent'] is True         # nothing was sent: the base is still None
    assert episode.calls == [None, None]
    third = run_with_connection(clock, 3, 'needs_login')
    assert episode.calls[-1] == checked(second) and third['summary']['connection_notice']['reason'] == 'already_reported'


def test_no_connection_notice_without_not_connected(daily_db, clock, notices, job_reports, episode,  # noqa: F811
                                                    scripted_model):
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    ok = run_with_connection(clock, 1, 'connected', scripted=scripted_model(_ok_responses()))
    assert ok['summary']['agent']['runs'] == {'NVDA': 'ok'}
    assert 'connection_notice' not in ok['summary'] and episode.calls == [] and lost_titles(notices) == []
    off = run_daily(clock)                                                # provider off: disabled, not a lost connection
    assert 'connection_notice' not in off['summary'] and episode.calls == []


def test_connection_notice_failure_never_changes_the_run(daily_db, clock, notices, job_reports, monkeypatch):
    from investment_assistant.core.llm import connection as agent_connection
    from investment_assistant.core.llm.chatgpt import ChatGPTError

    def broken(*, since=None):
        raise ChatGPTError('migration_028_missing', '迁移 028 未执行。')

    monkeypatch.setattr(agent_connection, 'first_failure_since_ok', broken)
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    out = run_with_connection(clock, 1, 'needs_login')
    notice = out['summary']['connection_notice']
    assert out['status'] == 'degraded' and (notice['sent'], notice['reason']) == (False, 'notice_failed: ChatGPTError')
    assert lost_titles(notices) == []


def test_a_resume_that_does_not_rerun_the_agent_does_not_notify(daily_db, clock, notices, job_reports, episode,
                                                                monkeypatch):
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    flaky('push', monkeypatch, fail_times=2)
    saver, factory = shared_saver()
    d = world(clk=clock, agent=True)
    d.agent_settings = _agent_settings(connection='needs_login')
    first = daily.run(daily_config(), now=NOW, deps=d, run_id=RUN_ID, checkpointer_factory=factory, sleep=clock.sleep,
                      notify_cfg=NotifyCfg(), trigger_source='manual')
    assert first['run_status'] == 'failed' and len(lost_titles(notices)) == 1
    episode.calls.clear()
    out = daily.resume(RUN_ID, 'push', now=NOW, deps=d, config=daily_config(), checkpointer_factory=factory,
                       sleep=clock.sleep, notify_cfg=NotifyCfg())
    assert out['run_status'] != 'failed' and episode.calls == [] and 'connection_notice' not in out['summary']
    assert len(lost_titles(notices)) == 1


def failure_notice_fields(notices):
    calls = [items for task, items in notices.calls if any('每日工作流失败' in n.title for n in items)]
    assert len(calls) == 1
    return {field.name: field.value for field in calls[0][0].fields}


def test_a_stage_failed_run_says_it_cannot_be_resumed(daily_db, clock, notices, job_reports):
    """A push the notifier could not send (a 502, no retry) is booked ``failed`` by the stage while the chain runs on to
    the end: the run is failed, but the checkpoint has no next node — the notice carries no 续跑 field and the detail
    says ``stage_failed`` (v2.8 final review I1)."""
    import investment_assistant.views.runs.detail as run_detail

    seed_watchlist(daily_db, [('NVDA', ['core'])])
    notices.result = {'sent': False, 'reason': 'http_502'}
    saver, factory = shared_saver()
    out = run_daily(clock, factory, trigger_source='scheduled')
    assert out['status'] == 'error' and out['summary']['failed_node'] == 'push'
    assert job_reports.rows()[0]['status'] == 'error'
    fields = failure_notice_fields(notices)
    assert '推送' in fields['失败节点'] and 'http_502' in fields['原因']
    assert '续跑' not in fields and '未执行' not in fields          # nothing after push was left undone
    daily_db.commit()
    detail = run_detail.detail(RUN_ID, now=NOW + timedelta(hours=1), checkpointer_factory=factory)
    assert detail['status'] == 'failed'
    assert detail['resume'] == {'allowed': False, 'node': None, 'deadline_at': None, 'reason': 'stage_failed'}


def test_a_chain_stopping_failure_still_tells_when_to_resume(daily_db, clock, notices, job_reports, monkeypatch):
    from test_daily_workflow import flaky

    seed_watchlist(daily_db, [('NVDA', ['core'])])
    flaky('regime', monkeypatch, fail_times=2)
    run_daily(clock)
    assert '续跑' in failure_notice_fields(notices)


def test_daily_refuses_without_029(daily_db, clock, notices, job_reports, monkeypatch):
    """v2.9 D36: no graph, no stage rows, one `error / migration_029_missing` report and one failure notice —
    checked before any plan or trade is read."""
    import investment_assistant.core.tx as service

    seed_watchlist(daily_db, [('NVDA', ['core'])])
    monkeypatch.setattr(core_schema, 'executable_ready', lambda conn: False)
    out = run_daily(clock)
    assert out['status'] == 'error' and out['summary']['error'] == 'migration_029_missing'
    assert [(r['status'], r['summary']['error']) for r in job_reports.rows()] == [('error', 'migration_029_missing')]
    assert daily_db.execute('SELECT count(*) FROM stage_runs').fetchone()[0] == 0
    assert len([t for t in notices.titles('workbench') if '每日工作流失败' in t]) == 1
    with pytest.raises(service.Unavailable, match='migration_029_missing'):        # the manual entry's pre-check
        daily.ensure_ready()


def test_daily_resume_refuses_without_029(daily_db, clock, notices, job_reports, monkeypatch):
    """Review M4: a resume is gated like a first run — 503 `migration_029_missing`, nothing written."""
    import investment_assistant.core.tx as service

    monkeypatch.setattr(core_schema, 'executable_ready', lambda conn: False)
    with pytest.raises(service.Unavailable, match='migration_029_missing'):
        daily.resume(RUN_ID, 'collect', now=NOW, config=daily_config(), notify_cfg=NotifyCfg())
    assert job_reports.rows() == [] and daily_db.execute('SELECT count(*) FROM stage_runs').fetchone()[0] == 0


@pytest.mark.parametrize('entry', ['run', 'resume', 'ensure_ready'])
def test_refuses_without_migration_031(daily_db, clock, notices, job_reports, entry):
    """A real missing table must stop every entry before stage/checkpoint reads and leave an error report."""
    from investment_assistant.core.tx import Unavailable

    # Remove a real required table rather than mocking the readiness result.
    daily_db.execute('DROP TABLE feature_flag_changes')
    daily_db.commit()
    if entry == 'run':
        out = run_daily(clock)
        assert (out['status'], out['summary']['error']) == ('error', 'migration_031_missing')
    else:
        with pytest.raises(Unavailable, match='migration_031_missing'):
            if entry == 'resume':
                daily.resume(RUN_ID, 'collect', now=NOW, config=daily_config(), notify_cfg=NotifyCfg())
            else:
                daily.ensure_ready()
    [report] = job_reports.rows()
    assert (report['status'], report['summary']['error']) == ('error', 'migration_031_missing')
    assert report['trigger_source'] == ('resume' if entry == 'resume' else 'manual')
    if entry == 'ensure_ready':
        assert report['run_id'].startswith('daily-')
        assert notices.calls == []
    else:
        assert report['run_id'] == RUN_ID
        assert len([t for t in notices.titles('workbench') if '每日工作流失败' in t]) == 1
    assert daily_db.execute('SELECT count(*) FROM stage_runs').fetchone()[0] == 0


def test_migration_031_ready_allows_precheck_run_and_resume(daily_db, clock, notices, job_reports, monkeypatch):
    """A fully migrated schema reaches the real graph and can continue a stopped run."""
    assert core_schema.five_layers_ready(daily_db)
    daily.ensure_ready()
    assert job_reports.rows() == []
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    flaky('regime', monkeypatch, fail_times=2)
    saver, factory = shared_saver()
    first = run_daily(clock, factory)
    assert first['summary']['failed_node'] == 'regime'
    resumed = daily.resume(RUN_ID, 'regime', now=NOW, deps=world(clk=clock), config=daily_config(),
                           checkpointer_factory=factory, sleep=clock.sleep, notify_cfg=NotifyCfg())
    assert resumed['run_status'] != 'failed'
    assert [r['trigger_source'] for r in job_reports.rows()] == ['manual', 'resume']
    assert set(effective(daily_db)) == set(workflow.NODES)


def test_not_connected_is_detected_in_every_model_calling_node():
    """spec §10: ``portfolio`` (the PM), ``accounts`` and ``propose`` join ``agent`` / ``review`` in the lost-connection look."""
    def row(stage, **kw):
        return {'stage': stage, 'attempt': 1, 'status': 'success', 'reason': None, 'failures': [], 'skips': [],
                'degradations': [], **kw}

    assert daily._hit_not_connected([row('portfolio', skips=[{'reason': 'not_connected', 'count': 1}])], 1)
    assert daily._hit_not_connected([row('accounts', failures=[{'target': 'a', 'reason': 'x: not_connected'}])], 1)
    assert daily._hit_not_connected([row('propose', status='skipped', reason='agent_unavailable: not_connected')], 1)
    assert not daily._hit_not_connected([row('portfolio', skips=[{'reason': 'disabled', 'count': 1}])], 1)
    assert not daily._hit_not_connected([row('portfolio', skips=[{'reason': 'not_connected', 'count': 1}], attempt=2)], 1)
    assert not daily._hit_not_connected([row('push', skips=[{'reason': 'not_connected', 'count': 1}])], 1)
