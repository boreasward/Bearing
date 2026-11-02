"""The run detail read model (v2.8 §7): ``workbench.run_detail.detail`` over real runs of the ``daily`` graph (fake
fetchers, scripted model, in-memory checkpoints) and over hand-booked ``stage_runs`` rows for the shapes a run can only
rarely produce (a clean success, a stages-2 / stages-3 run, a run still going). SQL tests on the disposable database."""
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import investment_assistant.workflow.repo as workflow_repo
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from investment_assistant.core import tx as core_tx

from _daily_db import (NotifyCfg, clock, daily_config, daily_db, job_reports, notices, shared_saver)   # noqa: F401
from test_daily_workflow import RUN_ID, effective, fake_sec, flaky, no_fetch_backoff, rows, world   # noqa: F401
from test_workbench_service import NOW, SESSION, _agent_settings, _ok_responses, agent_deps, seed_watchlist

from investment_assistant import db as app_db
from investment_assistant.tasks import daily
from investment_assistant.core import locks
import investment_assistant.views.runs.detail as run_detail
import investment_assistant.views.runs.overview as run_overview
import investment_assistant.core.tx as service
import investment_assistant.workflow.graph as workflow
import investment_assistant.core.stages as stages_module

LATER = NOW + timedelta(hours=2)
DEADLINE = workflow.resume_deadline(SESSION)             # 2026-10-23 17:00 New York
TOP_KEYS = {'run_id', 'task', 'kind', 'graph_version', 'stages_version', 'session', 'cutoff_at', 'trigger', 'status',
            'headline', 'executions', 'resume', 'nodes', 'raw_summary'}
NODE_KEYS = {'id', 'title', 'purpose', 'concept', 'status', 'reason', 'reason_text', 'attempt', 'attempts', 'started_at',
             'finished_at', 'duration_ms', 'did', 'fetch', 'results', 'actions'}
DID_KEYS = {'sentence', 'input_count', 'output_count', 'skips', 'failures', 'degradations', 'retry', 'timings'}
EXECUTION_KEYS = {'report_id', 'trigger_source', 'status', 'started_at', 'finished_at', 'resume_node', 'attempt'}
TARGET_KEYS = {'target', 'ok', 'attempts', 'error_kind', 'http_status', 'reason', 'rows', 'latest', 'elapsed_ms'}


def view(run_id=RUN_ID, *, now=LATER, factory=None):
    return run_detail.detail(run_id, now=now, checkpointer_factory=factory)


def run(clock, *, d=None, factory=None, run_id=RUN_ID, trigger_source='manual'):
    return daily.run(daily_config(), now=NOW, deps=d or world(clk=clock), run_id=run_id, trigger_source=trigger_source,
                     checkpointer_factory=factory or shared_saver()[1], sleep=clock.sleep, notify_cfg=NotifyCfg())


def resume(clock, factory, node, *, d=None):
    return daily.resume(RUN_ID, node, now=LATER, deps=d or world(clk=clock), config=daily_config(),
                        checkpointer_factory=factory, sleep=clock.sleep, notify_cfg=NotifyCfg())


def agent_world(scripted_model, clock, responses=None):
    d = agent_deps(scripted_model, _ok_responses('NVDA') if responses is None else responses)
    d.agent_settings = _agent_settings()
    d.clock = clock.mono
    return d


def book(conn, run_id, *, version='stages-4', job='daily', trigger='manual_batch', session=SESSION, only=None,
         rows_by_stage=None, attempt=1, base=NOW):
    """Hand-book one execution's ``stage_runs`` rows (all stages of the version, or ``only``)."""
    ids = stages_module.STAGE_IDS_BY_VERSION[version]
    for index, stage in enumerate(ids):
        if only is not None and stage not in only:
            continue
        spec = (rows_by_stage or {}).get(stage, {})
        started = base + timedelta(minutes=index + 10 * (attempt - 1))
        workflow_repo.insert_stage_run(conn, {
            'run_id': run_id, 'job': job, 'trigger': trigger, 'session': session, 'stage': stage,
            'stages_version': version, 'status': 'success', 'reason': None, 'input_count': 1, 'output_count': 1,
            'skips': [], 'failures': [], 'degradations': [], 'outputs': {}, 'detail': {}, 'started_at': started,
            'finished_at': started + timedelta(seconds=30), 'duration_ms': 30000, 'attempt': attempt, **spec})
    conn.commit()


def report(conn, run_id, *, task='daily', status='success', trigger_source='manual', summary=None, started=NOW):
    app_db.insert_job_report(conn, task=task, run_id=run_id, status=status, started_at=started,
                             finished_at=started + timedelta(minutes=10), summary=summary or {},
                             target_start_date=SESSION, target_end_date=SESSION, trigger_source=trigger_source)


def node(detail, stage_id):
    return next(n for n in detail['nodes'] if n['id'] == stage_id)


# ------------------------------------------------------------------------------------------------ stages-4 runs

def test_success_run_shape(daily_db):
    book(daily_db, RUN_ID)
    report(daily_db, RUN_ID, summary={'cutoff_at': NOW.isoformat(), 'graph_version': 'daily-1', 'run_status': 'success',
                                      'resumed': 0})
    detail = view()
    assert set(detail) == TOP_KEYS
    assert (detail['run_id'], detail['task'], detail['kind'], detail['graph_version'], detail['stages_version']) == (
        RUN_ID, 'daily', 'daily', 'daily-1', 'stages-4')
    assert (detail['session'], detail['cutoff_at'], detail['trigger'], detail['status']) == (
        '2026-10-22', NOW.isoformat(), 'manual_batch', 'success')
    assert detail['resume'] == {'allowed': False, 'node': None, 'deadline_at': None, 'reason': 'not_failed'}
    assert [n['id'] for n in detail['nodes']] == list(stages_module.STAGE_IDS_BY_VERSION['stages-4'])
    for n in detail['nodes']:
        assert set(n) == NODE_KEYS and set(n['did']) == DID_KEYS and n['concept'] == 'system.stages'
        assert (n['status'], n['attempt'], n['duration_ms']) == ('success', 1, 30000)
        assert n['attempts'] == [{'attempt': 1, 'status': 'success', 'started_at': n['started_at'],
                                  'finished_at': n['finished_at'], 'duration_ms': 30000}]
        assert n['actions'] == {'resume': False, 'rerun_agent': []}
        assert set(n['results']) == {'kind', 'items', 'total', 'truncated'} and n['results']['kind'] == n['id']
    assert [n['fetch'] is not None for n in detail['nodes']] == [True, True, True] + [False] * 7
    assert detail['executions'] == [{'report_id': detail['executions'][0]['report_id'], 'trigger_source': 'manual',
                                     'status': 'success', 'started_at': NOW.isoformat(),
                                     'finished_at': (NOW + timedelta(minutes=10)).isoformat(), 'resume_node': None,
                                     'attempt': 1}]
    assert detail['raw_summary'] == {'cutoff_at': NOW.isoformat(), 'graph_version': 'daily-1', 'run_status': 'success',
                                     'resumed': 0}
    assert node(detail, 'collect')['did']['sentence'] == run_overview.describe(
        effective(daily_db)['collect'], title='x', purpose='y')['sentence']       # the overview's own sentence


def test_degraded_real_run_has_fetch_targets_and_results(daily_db, clock, notices, job_reports, fake_sec,
                                                         scripted_model):
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    fake_sec.result = {'tickers': ['NVDA'], 'filings': [{'ticker': 'NVDA', 'form': '8-K', 'filing_date': '2026-10-22',
                                                         'accession_number': '0001-26-1'}],
                       'errors': {}, 'downloaded_count': 0, 'event_ids': [],
                       'delivery': {'status': 'sent', 'receipts': [{'status': 'sent'}]}}
    out = run(clock, d=agent_world(scripted_model, clock))
    assert out['run_status'] == 'degraded'
    daily_db.commit()
    detail = view()
    assert detail['status'] == 'degraded' and set(detail) == TOP_KEYS
    assert [n['status'] for n in detail['nodes']].count('failed') == 0 and node(detail, 'agent')['status'] == 'success'
    families = {f['family']: f for f in node(detail, 'collect')['fetch']['families']}
    assert {'bars', 'news', 'sec_filings'} <= set(families) and all(not f['legacy'] for f in families.values())
    bars = families['bars']
    assert bars['title'] == '日线' and bars['target_count'] == bars['ok_count'] == len(bars['targets'])
    assert set(bars['targets'][0]) == TARGET_KEYS and bars['targets'][0]['ok'] is True and bars['scope'] is None
    [sec] = node(detail, 'sec_monitor')['fetch']['families']
    # D47: the monitor node's row is the sec_text family's, scope sec_monitor
    assert sec['family'] == 'sec_text' and sec['scope'] == 'sec_monitor'
    assert node(detail, 'sec_monitor')['results'] == {
        'kind': 'sec_monitor', 'items': [{'ticker': 'NVDA', 'form': '8-K', 'filing_date': '2026-10-22',
                                          'accession': '0001-26-1', 'notified': True}], 'total': 1, 'truncated': False}
    assert node(detail, 'collect')['results'] == {'kind': 'collect', 'items': [], 'total': 0, 'truncated': False}
    screen = node(detail, 'screen')['results']
    assert screen['kind'] == 'screen' and screen['total'] == len(screen['items']) and all(
        set(i) == {'ticker', 'rank', 'group', 'selected', 'new_member'} and i['selected'] for i in screen['items'])
    [regime] = node(detail, 'regime')['results']['items']
    assert set(regime) == {'label', 'regime_id'} and regime['label']
    signals = node(detail, 'signals')['results']['items']
    assert signals and all(set(i) == {'ticker', 'rule_id', 'candidate_id', 'status'} for i in signals)
    plans = node(detail, 'plan')['results']['items']
    assert plans and all(set(p) == {'ticker', 'plan_id', 'status', 'invalid_reasons'} for p in plans)
    agent = node(detail, 'agent')['results']
    assert agent['total'] == len(agent['items']) >= 1
    item = agent['items'][0]
    assert set(item) == {'ticker', 'signal_date', 'mode', 'status', 'reason', 'reason_text', 'attempt', 'input_tokens',
                         'output_tokens', 'model', 'plan_id', 'run_id', 'can_rerun'}
    assert (item['ticker'], item['mode'], item['status'], item['can_rerun']) == ('NVDA', 'propose', 'ok', False)
    assert item['plan_id'] and item['model'] == 'gpt-test'
    assert node(detail, 'push')['results']['items'] == [] and node(detail, 'push')['status'] == 'skipped'    # manual
    assert node(detail, 'retro')['results']['kind'] == 'retro'


def test_failed_run_can_be_resumed_and_resumed_run_shows_both_executions(daily_db, clock, notices, job_reports,
                                                                         monkeypatch):
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    saver, factory = shared_saver()
    flaky('screen', monkeypatch, fail_times=2)
    out = run(clock, factory=factory)
    assert out['run_status'] == 'failed'
    daily_db.commit()
    failed = view(factory=factory)
    assert failed['status'] == 'failed' and failed['resume'] == {
        'allowed': True, 'node': 'screen', 'deadline_at': DEADLINE.astimezone(UTC).isoformat(), 'reason': None}
    assert [n['status'] for n in failed['nodes']][2:] == ['failed'] + ['skipped'] * 10
    assert {failed['nodes'][0]['status'], failed['nodes'][1]['status']} <= {'success', 'degraded'}
    screen = node(failed, 'screen')
    assert screen['reason'] == 'aborted: flaky screen' and screen['reason_text'] == '运行中断：flaky screen'
    assert screen['did']['retry'] == {'first_error': 'flaky screen', 'waited_s': 60}
    assert node(failed, 'regime')['reason_text'] == '上游节点失败，未执行'
    assert [n['id'] for n in failed['nodes'] if n['actions']['resume']] == ['screen']
    assert failed['headline'].startswith(
        '10 月 22 日的运行在「筛选 `screen-1`」失败（已自动重试 1 次）：运行中断：flaky screen；其后 10 个节点未执行')
    assert [e['status'] for e in failed['executions']] == ['error'] and failed['executions'][0]['attempt'] == 1
    assert failed['raw_summary']['failed_node'] == 'screen'
    assert resume(clock, factory, 'screen')['run_status'] in ('success', 'degraded')
    daily_db.commit()
    done = view(factory=factory)
    assert done['status'] in ('success', 'degraded') and done['resume'] == {
        'allowed': False, 'node': None, 'deadline_at': None, 'reason': 'not_failed'}
    assert [(e['trigger_source'], e['status'], e['resume_node'], e['attempt']) for e in done['executions']] == [
        ('manual', 'error', None, 1), ('resume', done['executions'][1]['status'], 'screen', 2)]
    assert [n['attempt'] for n in done['nodes']] == [1, 1] + [2] * 11
    assert [(a['attempt'], a['status']) for a in node(done, 'screen')['attempts']] == [(1, 'failed'),
                                                                                   (2, node(done, 'screen')['status'])]
    assert len(node(done, 'sec_monitor')['attempts']) == 1                       # completed nodes did not run again
    assert done['headline'].endswith('（第 2 次执行，从「筛选 `screen-1`」续跑）')
    assert done['raw_summary']['resume_node'] == 'screen' and done['raw_summary']['resumed'] == 1


@pytest.mark.parametrize('offset, allowed, reason', [(timedelta(minutes=-1), True, None),
                                                      (timedelta(0), False, 'resume_window_closed'),
                                                      (timedelta(hours=5), False, 'resume_window_closed')])
def test_resume_block_matches_resume_check(daily_db, clock, notices, job_reports, monkeypatch, offset, allowed, reason):
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    saver, factory = shared_saver()
    flaky('regime', monkeypatch, fail_times=2)
    run(clock, factory=factory)
    daily_db.commit()
    now = DEADLINE + offset
    block = view(now=now, factory=factory)['resume']
    assert (block['allowed'], block['node'], block['reason']) == (allowed, 'regime', reason)
    assert block['deadline_at'] == DEADLINE.astimezone(UTC).isoformat()
    try:
        info = workflow.resume_check(RUN_ID, 'regime', now=now, checkpointer_factory=factory)
        assert allowed and datetime.fromisoformat(info['deadline_at']) == datetime.fromisoformat(block['deadline_at'])
    except workflow.ResumeRejected as exc:
        assert not allowed and exc.code == reason


def test_resume_is_not_offered_while_the_lock_is_held_or_without_checkpoints(daily_db, clock, notices, job_reports,
                                                                             monkeypatch):
    import psycopg.errors

    seed_watchlist(daily_db, [('NVDA', ['core'])])
    saver, factory = shared_saver()
    flaky('regime', monkeypatch, fail_times=2)
    run(clock, factory=factory)
    daily_db.commit()
    with locks.exclusive():
        block = view(factory=factory)['resume']
        assert (block['allowed'], block['node'], block['reason']) == (False, 'regime', 'daily_running')
        assert block['deadline_at'] is not None
        assert view(factory=factory)['status'] == 'failed'         # a finished run stays failed while something else runs

    @contextmanager
    def no_tables():
        raise psycopg.errors.UndefinedTable('relation "checkpoints" does not exist')
        yield

    block = view(factory=no_tables)['resume']
    assert (block['allowed'], block['node'], block['reason']) == (False, None, 'checkpoint_unavailable')
    saver2, empty = shared_saver()                                  # a checkpointer that knows no such thread
    assert view(factory=empty)['resume']['reason'] == 'not_failed'


def test_resume_is_not_offered_for_a_node_that_never_wrote_a_row(daily_db):
    """Killed mid-node: the checkpoint says ``regime`` is next but there is no failed row to resume from."""
    book(daily_db, RUN_ID, only=('sec_monitor', 'collect', 'screen'))
    report(daily_db, RUN_ID, status='error')
    saver, factory = shared_saver()
    graph = workflow.build_graph(checkpointer=saver, ctx_factory=lambda: None)
    graph.update_state({'configurable': {'thread_id': RUN_ID}}, {'run_id': RUN_ID, 'session': SESSION.isoformat()},
                       as_node='screen')
    detail = view(factory=factory)
    assert detail['status'] == 'failed' and detail['resume']['reason'] == 'interrupted' and not detail['resume']['allowed']
    assert [n['status'] for n in detail['nodes']] == ['success'] * 3 + ['pending'] * 7
    assert node(detail, 'regime')['attempt'] is None and node(detail, 'regime')['actions']['resume'] is False


def test_running_status_when_locked(daily_db):
    book(daily_db, RUN_ID, only=('sec_monitor', 'collect'))
    assert view()['status'] == 'failed'                           # rows stop at collect and nobody is running: interrupted
    with locks.exclusive():
        detail = view()
    assert detail['status'] == 'running' and [n['status'] for n in detail['nodes']] == ['success'] * 2 + ['pending'] * 8
    assert detail['resume'] == {'allowed': False, 'node': None, 'deadline_at': None, 'reason': 'daily_running'}
    assert node(detail, 'screen')['did']['sentence'] and node(detail, 'screen')['attempt'] is None
    assert all(n['actions'] == {'resume': False, 'rerun_agent': []} for n in detail['nodes'])


def test_resume_in_progress_is_running(daily_db):
    run_id = 'daily-20261022223200-0b0b0b0b'
    book(daily_db, run_id, rows_by_stage={'screen': {'status': 'failed', 'reason': 'aborted: x'}, **{
        s: {'status': 'skipped', 'reason': 'upstream_failed'} for s in stages_module.STAGE_IDS[3:]}})
    report(daily_db, run_id, status='error')
    book(daily_db, run_id, only=('screen',), attempt=2)
    assert view(run_id)['status'] == 'failed'                      # nobody holds the lock: the resume died
    with locks.exclusive():
        detail = view(run_id)
    assert detail['status'] == 'running' and detail['resume']['reason'] == 'daily_running'
    assert node(detail, 'screen')['attempt'] == 2 and node(detail, 'regime')['attempt'] == 1


def test_a_run_this_process_executes_is_running_before_its_next_row(daily_db, clock, notices, job_reports,
                                                                   monkeypatch):
    """A resume holds the lock but writes its first new row only when its node ends: the page must not say ``failed``."""
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    saver, factory = shared_saver()
    flaky('regime', monkeypatch, fail_times=2)
    run(clock, factory=factory)
    daily_db.commit()
    assert view(factory=factory)['status'] == 'failed'
    seen = []
    real = workflow.resume

    def resuming(*args, **kwargs):
        daily_db.commit()
        detail = view(factory=factory)
        seen.append((locks.executing(RUN_ID), detail['status'], detail['resume']['reason'],
                     len(detail['executions'])))
        return real(*args, **kwargs)

    monkeypatch.setattr(workflow, 'resume', resuming)
    assert resume(clock, factory, 'regime')['run_status'] in ('success', 'degraded')
    assert seen == [(True, 'running', 'daily_running', 1)]
    assert not locks.executing(RUN_ID)                              # released with the lock
    daily_db.commit()
    assert view(factory=factory)['status'] in ('success', 'degraded')


def test_executing_run_without_any_row_is_a_skeleton_whatever_its_age(daily_db):
    old = 'daily-20200101000000-0c0c0c0c'
    with locks._active(old):
        detail = view(old, now=NOW)
    assert detail['status'] == 'running' and [n['status'] for n in detail['nodes']] == ['pending'] * 13


def test_just_started_run_is_a_running_skeleton(daily_db):
    fresh = 'daily-' + NOW.strftime('%Y%m%d%H%M%S') + '-0c0c0c0c'
    with pytest.raises(LookupError):
        view(fresh, now=NOW)                                       # nothing recorded and nothing running
    with locks.exclusive():
        detail = view(fresh, now=NOW)
        with pytest.raises(LookupError):
            view('daily-20200101000000-0c0c0c0c', now=NOW)         # a stale id is not "just started"
        with pytest.raises(LookupError):
            view('workbench-' + NOW.strftime('%Y%m%d%H%M%S') + '-0c0c0c0c', now=NOW)
    assert detail['status'] == 'running' and detail['kind'] == 'daily' and set(detail) == TOP_KEYS
    assert [n['status'] for n in detail['nodes']] == ['pending'] * 13 and detail['executions'] == []


def test_refused_run_without_rows(daily_db):
    report(daily_db, RUN_ID, status='error', summary={'error': 'migration_028_missing', 'run_status': 'failed'})
    detail = view()
    assert detail['kind'] == 'daily' and detail['status'] == 'failed' and detail['resume']['allowed'] is False
    assert [n['status'] for n in detail['nodes']] == ['pending'] * 13
    assert 'migration_028_missing' in detail['headline'] and detail['raw_summary']['error'] == 'migration_028_missing'


# ------------------------------------------------------------------------------------------------ old runs and others

def old_fetch_row(conn, family='bars'):
    return data_repo.insert_fetch_run(conn, {
        'family': family, 'job': 'workbench', 'trigger': 'manual_batch', 'run_id': None, 'scope': 'family',
        'session': SESSION, 'status': 'partial', 'target_count': 3, 'ok_count': 2,
        'failures': [{'target': 'MU', 'reason': 'provider down'}], 'detail': {}, 'started_at': NOW,
        'finished_at': NOW + timedelta(seconds=5)})['id']


@pytest.mark.parametrize('version, count, last', [('stages-3', 9, 'push'), ('stages-2', 8, 'push'), ('stages-1', 7, 'push')])
def test_legacy_batch_runs_are_drawn_with_their_own_stage_list(daily_db, version, count, last):
    run_id = 'workbench-20261022223100-0a0a0a0a'
    fetch_id = old_fetch_row(daily_db)
    book(daily_db, run_id, version=version, job='workbench', trigger='manual_batch',
         rows_by_stage={'collect': {'outputs': {'data_fetch_runs': [fetch_id]}, 'status': 'partial',
                                    'failures': [{'target': 'bars:MU', 'reason': 'provider down'}]}})
    report(daily_db, run_id, task='workbench', status='degraded', trigger_source='manual',
           summary={'sources': {}})
    detail = view(run_id)
    assert set(detail) == TOP_KEYS and detail['kind'] == 'batch' and detail['task'] == 'workbench'
    assert detail['graph_version'] is None and detail['stages_version'] == version
    assert len(detail['nodes']) == count and detail['nodes'][-1]['id'] == last
    assert [n['id'] for n in detail['nodes']] == list(stages_module.STAGE_IDS_BY_VERSION[version])
    assert detail['status'] == 'degraded'                                   # the latest job_reports status
    assert detail['resume'] == {'allowed': False, 'node': None, 'deadline_at': None, 'reason': None}
    assert all(n['actions'] == {'resume': False, 'rerun_agent': []} for n in detail['nodes'])
    [family] = node(detail, 'collect')['fetch']['families']
    assert family['legacy'] is True and family['targets'] == [] and family['failures'] == [
        {'target': 'MU', 'reason': 'provider down'}] and family['status'] == 'partial'
    assert detail['headline'] == run_overview.overview(session=SESSION, run_id=run_id, now=NOW)['headline']
    if version == 'stages-3':
        assert node(detail, 'retro')['title'] == '复盘卡'                   # what the node was called back then


def test_other_runs_have_no_nodes(daily_db):
    run_id = 'filings-20261022223100-0a0a0a0a'
    report(daily_db, run_id, task='filings', status='success', trigger_source='scheduled',
           summary={'filings': [], 'cutoff_at': None})
    detail = view(run_id)
    assert set(detail) == TOP_KEYS and detail['kind'] == 'other' and detail['nodes'] == [] and detail['task'] == 'filings'
    assert detail['status'] == 'success' and detail['graph_version'] is None and detail['stages_version'] is None
    assert detail['resume']['allowed'] is False and detail['trigger'] == 'scheduled' and detail['session'] == '2026-10-22'
    assert detail['raw_summary'] == {'filings': [], 'cutoff_at': None} and len(detail['executions']) == 1
    assert 'filings' in detail['headline']


def test_unknown_and_malformed_ids(daily_db):
    with pytest.raises(LookupError):
        view('daily-20261022223100-ffffffff')
    for bad in ('', 'daily', 'daily-2026-0a0a0a0a', 'Daily-20261022223100-0a0a0a0a', 'daily-20261022223100-0A0A0A0A',
                'us-research-20261022223100-0a0a0a0a', "daily-20261022223100-0a0a0a0a'; --", None):
        with pytest.raises(ValueError):
            view(bad)
    assert run_detail.RUN_ID_RE.pattern == r'^[a-z_]+-\d{14}-[0-9a-f]{8}$'


def test_database_unavailable(monkeypatch):
    monkeypatch.delenv('INVESTMENT_ASSISTANT_DATABASE_URL', raising=False)
    with pytest.raises(service.Unavailable):
        view()


def test_without_migration_028_old_runs_still_show(workbench_db, monkeypatch):
    """A database that stops at 027: no ``attempt`` column, no reset table, no checkpoint look-up needed."""
    @contextmanager
    def connection():
        yield workbench_db

    monkeypatch.setattr(core_tx, 'connection', connection)
    run_id = 'workbench-20261022223100-0a0a0a0a'
    book_old = [(i, s) for i, s in enumerate(stages_module.STAGE_IDS_V3)]
    for index, stage in book_old:
        workflow_repo.insert_stage_run(workbench_db, {
            'run_id': run_id, 'job': 'workbench', 'trigger': 'scheduled', 'session': SESSION, 'stage': stage,
            'stages_version': 'stages-3', 'status': 'success', 'input_count': 1, 'output_count': 1,
            'started_at': NOW + timedelta(minutes=index), 'finished_at': NOW + timedelta(minutes=index, seconds=9),
            'duration_ms': 9000})
    app_db.insert_job_report(workbench_db, task='workbench', run_id=run_id, status='success', started_at=NOW,
                             finished_at=NOW + timedelta(minutes=12), summary={}, target_start_date=SESSION,
                             target_end_date=SESSION, trigger_source='scheduled')
    workbench_db.commit()
    detail = view(run_id)
    assert detail['kind'] == 'batch' and len(detail['nodes']) == 9 and detail['status'] == 'success'
    assert all(n['attempt'] == 1 for n in detail['nodes'])


# ------------------------------------------------------------------------------------------------ results

def booked_agent_run(conn, ticker, status, reason, *, mode='propose', attempt=1, minutes=0, plan_id=None):
    run_id = str(uuid.uuid4())
    started = NOW + timedelta(minutes=minutes)
    analysis_repo.insert_agent_run(conn, {
        'id': run_id, 'ticker': ticker, 'signal_date': SESSION, 'pool': 'main', 'attempt': attempt, 'mode': mode,
        'agent_version': 'agent-2', 'provider': 'chatgpt', 'model': 'gpt-test', 'cutoff_at': NOW, 'status': status,
        'reason': reason, 'detail': None, 'output': None, 'counter': None, 'plan_id': plan_id, 'input_tokens': 7,
        'output_tokens': 3, 'started_at': started, 'finished_at': started + timedelta(seconds=5)}, [])
    conn.commit()
    return run_id


def agent_row(ids):
    return {'status': 'partial', 'outputs': {'agent_run_ids': ids}, 'detail': {}}


def test_agent_results_can_rerun(daily_db):
    exhausted = booked_agent_run(daily_db, 'AAA', 'skipped', 'max_attempts', attempt=4)
    failed = booked_agent_run(daily_db, 'BBB', 'error', 'model_error')
    fine = booked_agent_run(daily_db, 'CCC', 'pass', None)
    old = booked_agent_run(daily_db, 'DDD', 'error', 'model_error', minutes=0)
    newer = booked_agent_run(daily_db, 'DDD', 'pass', None, attempt=2, minutes=5)
    read = booked_agent_run(daily_db, 'EEE', 'skipped', 'max_attempts', mode='interpret', attempt=4)
    review = booked_agent_run(daily_db, 'FFF', 'error', 'model_error', mode='review')
    ids = [exhausted, failed, fine, old, newer, read, review]
    with service.read_only_tx() as conn:
        out = run_detail._results(conn, 'agent', agent_row(ids), rerun=True, resets_ready=True)
        again = {(i['ticker'], i['mode']): i['can_rerun'] for i in out['items']}
        off = run_detail._results(conn, 'agent', agent_row(ids), rerun=False, resets_ready=True)
    assert again == {('AAA', 'propose'): True, ('BBB', 'propose'): True, ('CCC', 'propose'): False,
                     ('DDD', 'propose'): False, ('EEE', 'interpret'): True, ('FFF', 'review'): False}
    assert out['total'] == 7 and [i['reason_text'] for i in out['items'] if i['ticker'] == 'AAA'] == [
        run_overview.reason_text('max_attempts')]
    assert not any(i['can_rerun'] for i in off['items'])                      # a running / legacy run offers nothing
    # a reset after the latest run is the answer to that state: the button is gone for exactly that unit and mode
    daily_db.commit()
    analysis_repo.insert_attempt_reset(daily_db, {'ticker': 'AAA', 'signal_date': SESSION, 'mode': 'propose',
                                               'request_id': str(uuid.uuid4()), 'origin': 'run_detail'})
    daily_db.commit()
    with service.read_only_tx() as conn:
        after = {(i['ticker'], i['mode']): i['can_rerun']
                 for i in run_detail._results(conn, 'agent', agent_row(ids), rerun=True, resets_ready=True)['items']}
    assert after[('AAA', 'propose')] is False and after[('BBB', 'propose')] is True and after[('EEE', 'interpret')] is True


def test_agent_node_lists_rerunnable_units_in_actions(daily_db):
    failed = booked_agent_run(daily_db, 'BBB', 'error', 'model_error')
    book(daily_db, RUN_ID, only=stages_module.STAGE_IDS_V4[:-1], rows_by_stage={'agent': agent_row([failed])})
    detail = view()                                               # the run stopped before its last node: interrupted
    assert detail['status'] == 'failed'
    assert node(detail, 'agent')['actions']['rerun_agent'] == [
        {'ticker': 'BBB', 'signal_date': '2026-10-22', 'mode': 'propose'}]
    assert node(detail, 'agent')['results']['items'][0]['can_rerun'] is True
    with locks.exclusive():                                       # the same rows while the run is going: nothing to rerun
        going = view()
    assert going['status'] == 'running' and node(going, 'agent')['results']['items'][0]['can_rerun'] is False
    assert node(going, 'agent')['actions']['rerun_agent'] == []


def test_no_rerun_while_any_daily_execution_holds_the_lock(daily_db):
    """A finished run viewed while another execution runs (maybe the same session): the unit could be in work."""
    failed = booked_agent_run(daily_db, 'BBB', 'error', 'model_error')
    book(daily_db, RUN_ID, rows_by_stage={'agent': agent_row([failed])})
    report(daily_db, RUN_ID)
    assert node(view(), 'agent')['results']['items'][0]['can_rerun'] is True
    with locks.exclusive():
        detail = view()
    assert detail['status'] == 'degraded'                      # finished, not running: only the lock is someone else's
    assert node(detail, 'agent')['results']['items'][0]['can_rerun'] is False
    assert node(detail, 'agent')['actions']['rerun_agent'] == []


def test_results_are_capped_at_200_with_the_true_total(daily_db):
    ids = []
    for index in range(205):
        row = daily_db.execute(
            "INSERT INTO signal_candidates (ticker, signal_date, action, rule_id, rule_version, inputs) "
            "VALUES (%s, %s, 'buy', 'trend_reclaim', 'rules-1', '{}'::jsonb) RETURNING id",
            (f'T{index:03d}', SESSION)).fetchone()
        ids.append(str(row[0]))
    daily_db.commit()
    assert run_detail.RESULTS_MAX == 200
    with service.read_only_tx() as conn:
        out = run_detail._results(conn, 'signals', {'status': 'success', 'outputs': {'inserted': ids[:100],
                                                                                      'existing': ids[100:]},
                                                    'detail': {}})
    assert out['total'] == 205 and len(out['items']) == 200 and out['truncated'] is True
    assert [i['status'] for i in out['items']].count('inserted') == 100 and out['items'][0]['ticker'] == 'T000'
    assert set(out['items'][0]) == {'ticker', 'rule_id', 'candidate_id', 'status'}
    with service.read_only_tx() as conn:       # a reference without a row is no cap: ``truncated`` follows the cap only
        missing = run_detail._results(conn, 'signals', {'status': 'success', 'outputs': {'inserted': [
            *ids[:2], str(uuid.uuid4())]}, 'detail': {}})
    assert missing['total'] == 3 and len(missing['items']) == 2 and missing['truncated'] is False
    with service.read_only_tx() as conn:
        small = run_detail._results(conn, 'signals', {'status': 'success', 'outputs': {'inserted': ids[:3]},
                                                      'detail': {}})
    assert small['total'] == 3 and small['truncated'] is False


def test_results_of_review_push_and_retro(daily_db, clock, notices, job_reports, scripted_model):
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    run(clock, d=agent_world(scripted_model, clock))
    plan = execution_repo.agent_plan(daily_db, 'NVDA', SESSION)
    reviewed = execution_repo.insert_plan_review(daily_db, {
        'ticker': 'NVDA', 'signal_date': SESSION, 'head_plan_id': plan['id'], 'session': SESSION, 'trigger': 'batch',
        'run_id': RUN_ID, 'mode': 'entry', 'timeline_version': 'timeline-1', 'outcome': 'maintain',
        'decided_by': 'host', 'reason': '维持', 'attempt': 1, 'cutoff_at': NOW})
    card = daily_db.execute(
        "INSERT INTO review_cards (event_key, subject, ticker, signal_date, end_kind, end_session, retro_version, "
        "facts) VALUES ('k1', 'unit', 'NVDA', %s, 'stop', %s, 'retro-1', '{}'::jsonb) RETURNING id",
        (SESSION, SESSION)).fetchone()[0]
    daily_db.commit()
    with service.read_only_tx() as conn:
        review = run_detail._results(conn, 'review', {'status': 'success', 'outputs': {'review_ids': [reviewed['id']]},
                                                      'detail': {}})
        push = run_detail._results(conn, 'push', {'status': 'success', 'detail': {},
                                                  'outputs': {'push': {'sent': True, 'messages': 5, 'reason': None}}},
                                   summary={'push': {'channel': 'discord'}})
        retro = run_detail._results(conn, 'retro', {'status': 'success', 'outputs': {'card_ids': [card]}, 'detail': {
            'weekly': {'period': '2026-10-19', 'action': 'generated', 'push': {'sent': True, 'messages': 1}},
            'monthly': {'period': '2026-10-01', 'action': 'waiting'}, 'cards': True}})
        skipped = run_detail._results(conn, 'retro', {'status': 'skipped', 'reason': 'not_due', 'outputs': {},
                                                      'detail': {}})
    assert review['items'] == [{'ticker': 'NVDA', 'signal_date': '2026-10-22', 'outcome': 'maintain',
                                'review_id': reviewed['id'], 'plan_id': str(plan['id'])}]
    assert push['items'] == [{'sent': True, 'messages': 5, 'channel': 'discord', 'reason': None}]
    [card_item, weekly_item] = retro['items']
    assert (card_item['kind'], card_item['id'], card_item['ticker'], card_item['push']) == ('card', card, 'NVDA', None)
    assert card_item['status'] in ('to_review', 'drafting', 'draft_failed', 'reviewed') and retro['total'] == 2
    assert weekly_item == {'kind': 'weekly', 'id': '2026-10-19', 'ticker': None, 'status': 'generated', 'push': 'sent'}
    assert skipped == {'kind': 'retro', 'items': [], 'total': 0, 'truncated': False}


def test_screen_results_mark_new_members(daily_db):
    run_id = daily_db.execute(
        "INSERT INTO screen_runs (session, calc_version, universe_name, universe_as_of, cutoff_at, status, summary) "
        "VALUES (%s, 'screen-1', 'ndx100', %s, %s, 'ok', '{}'::jsonb) RETURNING id", (SESSION, SESSION, NOW)).fetchone()[0]
    for rank, (ticker, selected) in enumerate((('AAA', True), ('BBB', True), ('CCC', False)), start=1):
        daily_db.execute("INSERT INTO screen_results (screen_run_id, ticker, screen_group, trigger_rules, filters, rs, "
                         "atr_pct, dollar_volume_20, rank, selected) VALUES (%s, %s, 'trigger', '{}', '{}'::jsonb, 1, 2, "
                         "3, %s, %s)", (run_id, ticker, rank, selected))
    daily_db.commit()
    with service.read_only_tx() as conn:
        out = run_detail._results(conn, 'screen', {'status': 'success', 'outputs': {'screen_run_id': run_id},
                                                   'detail': {'new_members': ['BBB', 'ZZZ']}})
    assert out['items'] == [
        {'ticker': 'AAA', 'rank': 1, 'group': 'trigger', 'selected': True, 'new_member': False},
        {'ticker': 'BBB', 'rank': 2, 'group': 'trigger', 'selected': True, 'new_member': True},
        {'ticker': 'ZZZ', 'rank': None, 'group': None, 'selected': False, 'new_member': True}] and out['total'] == 3


def test_fetch_block_keeps_the_stored_target_rows(daily_db):
    fetch_id = data_repo.insert_fetch_run(daily_db, {
        'family': 'news', 'job': 'daily', 'trigger': 'manual_batch', 'run_id': RUN_ID, 'scope': 'family',
        'session': SESSION, 'status': 'partial', 'target_count': 2, 'ok_count': 1,
        'failures': [{'target': 'MU', 'reason': 'timeout'}], 'started_at': NOW, 'finished_at': NOW + timedelta(seconds=9),
        'detail': {'scope': 'screen_new_members', 'policy': 'fetch-1', 'targets': [
            {'target': 'MU', 'ok': False, 'attempts': 3, 'error_kind': 'timeout', 'http_status': None,
             'reason': 'timeout', 'rows': None, 'latest': None, 'elapsed_ms': 100412, 'extra': 'dropped'},
            {'target': 'NVDA', 'ok': True, 'attempts': 1, 'error_kind': None, 'http_status': 200, 'reason': None,
             'rows': 4, 'latest': '2026-10-22T12:00:00+00:00', 'elapsed_ms': 80}]}})['id']
    daily_db.commit()
    with service.read_only_tx() as conn:
        block = run_detail._fetch_block(conn, {'stages_version': 'stages-4', 'outputs': {'data_fetch_runs': [fetch_id]}})
        none = run_detail._fetch_block(conn, {'stages_version': 'stages-4', 'outputs': {}})
    [family] = block['families']
    assert family['scope'] == 'screen_new_members' and family['legacy'] is False and family['title'] == '资讯'
    assert [t['target'] for t in family['targets']] == ['MU', 'NVDA'] and all(set(t) == TARGET_KEYS for t in family['targets'])
    assert family['targets'][0]['elapsed_ms'] == 100412 and family['failures'] == [{'target': 'MU', 'reason': 'timeout'}]
    assert none is None


# ------------------------------------------------------------------------------------------------ read-only, no fetchers

def test_headline_reuses_run_overview(daily_db, clock, notices, job_reports, monkeypatch):
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    saver, factory = shared_saver()
    flaky('screen', monkeypatch, fail_times=2)
    run(clock, factory=factory)
    daily_db.commit()
    assert view(factory=factory)['headline'] == run_overview.overview(session=SESSION, run_id=RUN_ID, now=NOW)['headline']
    resume(clock, factory, 'screen')
    daily_db.commit()
    shown = view(factory=factory)['headline']
    assert shown == run_overview.overview(session=SESSION, run_id=RUN_ID, now=NOW)['headline'] and '第 2 次执行' in shown
    for n in view(factory=factory)['nodes']:
        stage = next(s for s in run_overview.build(rows(daily_db), session=SESSION, run_id=RUN_ID, ready=True)['stages']
                     if s['id'] == n['id'])
        assert (n['did']['sentence'], n['reason_text'], n['did']['skips']) == (stage['sentence'], stage['reason_text'],
                                                                                stage['skips'])


def test_run_detail_read_only(daily_db, clock, notices, job_reports, scripted_model, monkeypatch):
    """Everything is read inside ``read_only_tx`` (the database refuses a write there) and no row appears."""
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    saver, factory = shared_saver()
    run(clock, d=agent_world(scripted_model, clock), factory=factory)
    daily_db.commit()
    tables = [r[0] for r in daily_db.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() "
        "AND table_type = 'BASE TABLE'").fetchall()]
    daily_db.commit()

    def counts():
        found = {t: daily_db.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
        daily_db.commit()
        return found

    before = counts()
    entered = []
    real_tx = service.read_only_tx

    @contextmanager
    def spy():
        with real_tx() as conn:
            entered.append(conn.execute('SHOW transaction_read_only').fetchone()[0])
            yield conn

    monkeypatch.setattr(core_tx, 'read_only_tx', spy)
    monkeypatch.setattr(core_tx, '_tx', lambda: pytest.fail('a write transaction was opened'))
    reads = []
    for repository, name in ((workflow_repo, 'job_reports_for_run'), (workflow_repo, 'stage_rows_for_run'),
                             (data_repo, 'fetch_runs_by_ids'), (analysis_repo, 'candidate_refs_by_ids'),
                             (execution_repo, 'plans_by_ids'), (analysis_repo, 'agent_runs_by_ids'),
                             (analysis_repo, 'screen_results'), (analysis_repo, 'regime_by_id'),
                             (analysis_repo, 'latest_agent_run'), (analysis_repo, 'attempt_resets_for')):
        original = getattr(repository, name)

        def wrapped(conn, *args, _original=original, _name=name, **kwargs):
            reads.append((_name, conn.execute('SHOW transaction_read_only').fetchone()[0]))
            return _original(conn, *args, **kwargs)

        monkeypatch.setattr(repository, name, wrapped)
    view(factory=factory)
    assert entered == ['on'] and {state for _, state in reads} == {'on'}
    assert {name for name, _ in reads} >= {'job_reports_for_run', 'stage_rows_for_run', 'fetch_runs_by_ids',
                                           'candidate_refs_by_ids', 'agent_runs_by_ids', 'regime_by_id'}
    assert counts() == before


def test_run_detail_never_calls_fetchers(daily_db, clock, notices, job_reports, scripted_model, monkeypatch):
    import httpx
    import requests

    from investment_assistant.data import fetch_policy
    import investment_assistant.data.registry as data_registry
    from investment_assistant.core.llm import connection as agent_connection, model as agent_model

    seed_watchlist(daily_db, [('NVDA', ['core'])])
    saver, factory = shared_saver()
    run(clock, d=agent_world(scripted_model, clock), factory=factory)
    daily_db.commit()

    def forbidden(*args, **kwargs):
        raise AssertionError('the run detail must not reach an external source or the Agent connection')

    for owner, names in ((agent_model, ('settings_from_env', 'chat_model', 'unavailable_reason')),
                         (agent_connection, ('read_for_settings', 'status_view', 'access_token', 'models',
                                             'first_failure_since_ok')),
                         (fetch_policy, ('fetch', 'policy_session')),
                         (data_registry, ('run_adapter',)),
                         (httpx.Client, ('send',)), (requests.Session, ('request',))):
        for name in names:
            monkeypatch.setattr(owner, name, forbidden)
    detail = view(factory=factory)
    assert detail['status'] == 'degraded' and len(detail['nodes']) == 13


def test_detail_json_is_serialisable(daily_db, clock, notices, job_reports, scripted_model):
    import json

    seed_watchlist(daily_db, [('NVDA', ['core'])])
    saver, factory = shared_saver()
    run(clock, d=agent_world(scripted_model, clock), factory=factory)
    daily_db.commit()
    text = json.dumps(view(factory=factory), ensure_ascii=False, default=str)
    assert 'NVDA' in text and '\\u' not in text


def test_accounts_results_expose_settlement_equity_holdings_and_decision(daily_db):
    record={'account_id':'paper','name':'模拟','settled':{'filled':1,'voided':0,'expired':0,'cancelled':0,'exits':0},
            'equity':{'equity':'100100'},'holdings':[{'ticker':'NVDA'}],
            'decision':{'status':'ok','orders_placed':1,'orders_rejected':0,'skipped':[]}}
    book(daily_db,RUN_ID,version='stages-5',rows_by_stage={'accounts':{'detail':{'accounts':[record]}}})
    report(daily_db,RUN_ID,summary={'graph_version':'daily-2','run_status':'success'})
    result=view()
    assert len(result['nodes'])==11 and node(result,'accounts')['results']['items']==[record]


# ------------------------------------------------------------------------------------------------ stages-6 runs (daily-3)

@pytest.mark.parametrize('version, graph', [('stages-4', 'daily-1'), ('stages-5', 'daily-2'), ('stages-6', 'daily-3')])
def test_graph_version_follows_the_stages_version(daily_db, version, graph):
    book(daily_db, RUN_ID, version=version)
    report(daily_db, RUN_ID, summary={'cutoff_at': NOW.isoformat()})            # a report without a graph_version
    detail = view()
    assert (detail['kind'], detail['stages_version'], detail['graph_version']) == ('daily', version, graph)
    assert [n['id'] for n in detail['nodes']] == list(stages_module.STAGE_IDS_BY_VERSION[version])


def test_stages_6_portfolio_proposals_and_agent_request_counts(daily_db):
    accounts = [{'account_id': 'real', 'name': '真实账户', 'ok': 2, 'blocked': 1, 'deferred': 0,
                 'commentary_status': 'ok', 'commentary_reason': None},
                {'account_id': 'paper', 'name': '模拟', 'ok': 1, 'blocked': 0, 'deferred': 1,
                 'commentary_status': None}]
    proposals = [{'id': 7, 'title': '补采财报', 'source': 'data_failures', 'status': 'proposed', 'body': 'x'}]
    requests = {'queued': 3, 'fetched': 2, 'failed': 1, 'deferred': 0, 'reruns': 1}
    book(daily_db, RUN_ID, version='stages-6', rows_by_stage={
        'portfolio': {'detail': {'accounts': accounts}, 'input_count': 2, 'output_count': 2},
        'propose': {'detail': {'proposals': proposals}, 'output_count': 1},
        'agent': {'detail': {'data_requests': requests, 'research_requests': 2}}})
    report(daily_db, RUN_ID, summary={'cutoff_at': NOW.isoformat()})
    detail = view()
    assert len(detail['nodes']) == 13
    assert node(detail, 'portfolio')['results'] == {
        'kind': 'portfolio', 'total': 2, 'truncated': False,
        'items': [{key: item.get(key) for key in ('account_id', 'name', 'ok', 'blocked', 'deferred',
                                                   'commentary_status')} for item in accounts]}
    assert node(detail, 'propose')['results'] == {
        'kind': 'proposals', 'total': 1, 'truncated': False,
        'items': [{'id': 7, 'title': '补采财报', 'source': 'data_failures', 'status': 'proposed'}]}
    did = node(detail, 'agent')['did']
    assert did['data_requests'] == requests and did['research_requests'] == 2
    assert 'data_requests' not in node(detail, 'review')['did']          # the counts belong to the agent node
    assert 'portfolio' in node(detail, 'portfolio')['did']['sentence'] or '账户' in node(detail, 'portfolio')['did']['sentence']


def test_skipped_stages_6_nodes_list_nothing_with_their_kind(daily_db):
    book(daily_db, RUN_ID, version='stages-6', rows_by_stage={
        'portfolio': {'status': 'skipped', 'reason': 'feature_disabled'},
        'propose': {'status': 'skipped', 'reason': 'not_proposal_day'}})
    report(daily_db, RUN_ID, summary={'cutoff_at': NOW.isoformat()})
    detail = view()
    assert node(detail, 'portfolio')['results'] == {'kind': 'portfolio', 'items': [], 'total': 0, 'truncated': False}
    assert node(detail, 'propose')['results'] == {'kind': 'proposals', 'items': [], 'total': 0, 'truncated': False}
    assert node(detail, 'portfolio')['reason_text'] == '功能开关已关闭'
    assert node(detail, 'propose')['reason_text'] == '今天不是提案日（北京时间周六）'
