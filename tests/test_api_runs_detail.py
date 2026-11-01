"""The v2.8 run, resume and rerun routes (§4.8, §5.4, §5.7, §7.1, §11.1): parameter parsing and error mapping. The use
cases are replaced (offline, no database); the error body is always ``{"error": "<code>"}``."""
import re
import uuid
from datetime import date

import psycopg.errors
import pytest

from investment_assistant.core import tx as core_tx

from investment_assistant.tasks import daily, runner
from investment_assistant.workbench import run_detail, service, workflow

RUN_ID = 'daily-20261030033000-1a2b3c4d'
REQUEST_ID = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def no_db(monkeypatch):
    monkeypatch.delenv('INVESTMENT_ASSISTANT_DATABASE_URL', raising=False)


# ---------------------------------------------------------------------------------------------- GET /detail

def test_detail_200_passes_the_use_case_through(client, monkeypatch):
    seen = {}

    def detail(run_id, *, now, checkpointer_factory=None):
        seen.update(run_id=run_id, now=now)
        return {'run_id': run_id, 'kind': 'daily'}

    monkeypatch.setattr(run_detail, 'detail', detail)
    resp = client.get(f'/api/runs/{RUN_ID}/detail')
    assert resp.status_code == 200 and resp.json() == {'run_id': RUN_ID, 'kind': 'daily'}
    assert seen['run_id'] == RUN_ID and seen['now'].tzinfo is not None


def test_detail_400_404_503(client, monkeypatch):
    assert client.get('/api/runs/not-a-run-id/detail').status_code == 400                # the real use case refuses it
    assert client.get('/api/runs/us-research-20261030033000-1a2b3c4d/detail').status_code == 400
    resp = client.get(f'/api/runs/{RUN_ID}/detail')                                       # no database configured
    assert resp.status_code == 503 and '数据库未配置' in resp.json()['error']
    monkeypatch.setattr(run_detail, 'detail', lambda *a, **k: (_ for _ in ()).throw(LookupError('没有这次运行：x')))
    resp = client.get(f'/api/runs/{RUN_ID}/detail')
    assert resp.status_code == 404 and resp.json() == {'error': '没有这次运行：x'}
    monkeypatch.setattr(run_detail, 'detail', lambda *a, **k: (_ for _ in ()).throw(service.Unavailable('down')))
    assert client.get(f'/api/runs/{RUN_ID}/detail').status_code == 503


def test_the_in_memory_run_route_is_unchanged(client, monkeypatch):
    monkeypatch.setattr(runner, 'get', lambda run_id: {'run_id': run_id, 'status': 'done'})
    assert client.get('/api/runs/anything').json() == {'run_id': 'anything', 'status': 'done'}
    monkeypatch.setattr(runner, 'get', lambda run_id: None)
    assert client.get('/api/runs/anything').status_code == 404


# ---------------------------------------------------------------------------------------------- POST /daily/resume

@pytest.fixture
def resumable(monkeypatch):
    submitted = []
    monkeypatch.setattr(workflow, 'resume_check', lambda run_id, node, *, now, checkpointer_factory=None: {
        'run_id': run_id, 'node': node, 'attempt': 2})
    monkeypatch.setattr(daily, 'is_running', lambda: False)
    monkeypatch.setattr(runner, 'submit', lambda kind, fn: submitted.append((kind, fn)) or 'daily_resume-20261030050102-9f8e7d6c')
    return submitted


def test_resume_202_and_the_background_body(client, resumable, monkeypatch):
    resp = client.post('/api/jobs/daily/resume', json={'run_id': RUN_ID, 'node': 'collect', 'request_id': REQUEST_ID})
    assert resp.status_code == 202
    assert resp.json() == {'run_id': RUN_ID, 'node': 'collect', 'status': 'pending', 'attempt': 2,
                           'runner_id': 'daily_resume-20261030050102-9f8e7d6c'}
    assert [kind for kind, _ in resumable] == ['daily_resume']
    seen = {}
    monkeypatch.setattr(daily, 'resume', lambda run_id, node, **kw: seen.update(run_id=run_id, node=node, **kw) or {})
    resumable[0][1]()
    assert seen == {'run_id': RUN_ID, 'node': 'collect'}


@pytest.mark.parametrize('body', [{}, {'run_id': RUN_ID}, {'node': 'collect'}, {'run_id': '', 'node': 'collect'},
                                  {'run_id': RUN_ID, 'node': ''}, {'run_id': 7, 'node': 'collect'},
                                  {'run_id': 'daily-bad', 'node': 'collect'}])
def test_resume_400_for_a_bad_body(client, resumable, body):
    resp = client.post('/api/jobs/daily/resume', json=body)
    assert resp.status_code == 400 and 'error' in resp.json() and resumable == []


def test_resume_400_for_a_node_that_is_not_in_stages_4(client, monkeypatch):
    """The real pre-check refuses an unknown node before it touches the database."""
    monkeypatch.setattr(runner, 'submit', lambda *a, **k: pytest.fail('must not submit'))
    resp = client.post('/api/jobs/daily/resume', json={'run_id': RUN_ID, 'node': 'nonsense'})
    assert resp.status_code == 400


@pytest.mark.parametrize('exc, status, error', [
    (LookupError('没有这次运行：x'), 404, '没有这次运行：x'),
    (service.Unavailable('migration_028_missing'), 503, 'migration_028_missing'),
    (workflow.ResumeRejected('not_failed', '这次运行没有失败的节点'), 409, 'not_failed'),
    (workflow.ResumeRejected('node_mismatch', '只能从失败的节点 数据采集（collect）继续'), 409, 'node_mismatch'),
    (workflow.ResumeRejected('resume_window_closed', '已过 2026-10-30 纽约 17:00，只能整次重新运行'), 409,
     'resume_window_closed'),
])
def test_resume_pre_check_errors(client, resumable, monkeypatch, exc, status, error):
    monkeypatch.setattr(workflow, 'resume_check', lambda *a, **k: (_ for _ in ()).throw(exc))
    resp = client.post('/api/jobs/daily/resume', json={'run_id': RUN_ID, 'node': 'collect'})
    assert resp.status_code == status and resp.json() == {'error': error} and resumable == []


def test_resume_409_daily_running_after_the_other_checks(client, resumable, monkeypatch):
    monkeypatch.setattr(daily, 'is_running', lambda: True)
    resp = client.post('/api/jobs/daily/resume', json={'run_id': RUN_ID, 'node': 'collect'})
    assert resp.status_code == 409 and resp.json() == {'error': 'daily_running'} and resumable == []
    monkeypatch.setattr(workflow, 'resume_check', lambda *a, **k: (_ for _ in ()).throw(
        workflow.ResumeRejected('not_failed', 'x')))
    assert client.post('/api/jobs/daily/resume', json={'run_id': RUN_ID, 'node': 'collect'}).json() == {
        'error': 'not_failed'}                                          # the run's own state is reported first


def test_resume_pre_check_writes_nothing(client, resumable, monkeypatch):
    monkeypatch.setattr(daily, 'resume', lambda *a, **k: pytest.fail('only the background body resumes'))
    monkeypatch.setattr(core_tx, '_tx', lambda: pytest.fail('no write transaction'))
    assert client.post('/api/jobs/daily/resume', json={'run_id': RUN_ID, 'node': 'collect'}).status_code == 202


# ---------------------------------------------------------------------------------------------- run entries

@pytest.fixture
def idle(monkeypatch):
    submitted = []
    monkeypatch.setattr(daily, 'is_running', lambda: False)
    monkeypatch.setattr(runner, 'submit', lambda kind, fn: submitted.append((kind, fn)) or f'{kind}-20261030033000-aaaaaaaa')
    return submitted


@pytest.mark.parametrize('path', ['/api/jobs/daily/run', '/api/workbench/run'])
def test_both_manual_entries_return_daily_run_id(client, idle, monkeypatch, path):
    resp = client.post(path, json={'request_id': REQUEST_ID})
    body = resp.json()
    assert resp.status_code == 200 and body['status'] == 'pending' and body['run_id'] == 'daily-20261030033000-aaaaaaaa'
    assert re.fullmatch(r'daily-\d{14}-[0-9a-f]{8}', body['daily_run_id']) and set(body) == {
        'run_id', 'daily_run_id', 'status'}
    seen = {}
    monkeypatch.setattr(daily, 'run', lambda *a, **kw: seen.update(kw) or {})
    from investment_assistant.tasks.scheduler import REGISTRY
    monkeypatch.setitem(REGISTRY, 'daily', daily.run)
    idle[-1][1]()
    assert seen['trigger_source'] == 'manual' and seen['run_id'] == body['daily_run_id']


@pytest.mark.parametrize('path', ['/api/jobs/daily/run', '/api/workbench/run'])
def test_manual_run_entries_are_503_without_migration_028(client, idle, monkeypatch, path):
    from contextlib import contextmanager

    from investment_assistant.workbench import repository

    @contextmanager
    def tx():
        yield object()

    monkeypatch.setattr(core_tx, 'read_only_tx', tx)
    monkeypatch.setattr(repository, 'daily_ready', lambda conn: False)
    resp = client.post(path, json={})
    assert resp.status_code == 503 and resp.json() == {'error': 'migration_028_missing'} and idle == []
    monkeypatch.setattr(repository, 'daily_ready', lambda conn: True)
    assert client.post(path, json={}).status_code == 200 and len(idle) == 1


@pytest.mark.parametrize('path', ['/api/jobs/daily/run', '/api/jobs/sec_backfill/run', '/api/workbench/run'])
def test_manual_entries_are_409_while_the_lock_is_held(client, monkeypatch, path):
    monkeypatch.setattr(daily, 'is_running', lambda: True)
    monkeypatch.setattr(runner, 'submit', lambda *a, **k: pytest.fail('must not submit'))
    resp = client.post(path, json={'date': date.today().isoformat()})
    assert resp.status_code == 409 and resp.json() == {'error': 'daily_running'}


def test_a_lock_that_turns_up_inside_the_background_body_is_still_a_409_for_the_caller(client, monkeypatch):
    """``DailyBusy`` raised synchronously by a use case maps the same way (``daily.run`` raises it for manual runs)."""
    from investment_assistant.services import jobs

    monkeypatch.setattr(jobs, 'trigger_job', lambda name, payload: (_ for _ in ()).throw(daily.DailyBusy()))
    resp = client.post('/api/jobs/daily/run', json={})
    assert resp.status_code == 409 and resp.json() == {'error': 'daily_running'}


def test_sec_backfill_needs_dates(client, idle, monkeypatch):
    assert client.post('/api/jobs/sec_backfill/run', json={}).status_code == 400
    assert client.post('/api/jobs/sec_backfill/run', json={'request_id': REQUEST_ID}).status_code == 400
    assert client.post('/api/jobs/sec_backfill/run', json={'from': '2026-01-01'}).status_code == 400
    too_long = client.post('/api/jobs/sec_backfill/run', json={'from': '2026-01-01', 'to': '2026-06-01'})
    assert too_long.status_code == 400 and '92' in too_long.json()['error']
    assert client.post('/api/jobs/sec_backfill/run', json={'from': '2026-02-01', 'to': '2026-01-01'}).status_code == 400
    assert idle == []
    ok = client.post('/api/jobs/sec_backfill/run', json={'date': '2026-10-01', 'request_id': REQUEST_ID})
    assert ok.status_code == 200 and ok.json() == {'run_id': 'sec_backfill-20261030033000-aaaaaaaa', 'status': 'pending'}
    seen = {}
    from investment_assistant.services import jobs
    monkeypatch.setitem(jobs.REGISTRY, 'sec_backfill', lambda config, **kw: seen.update(kw) or {})
    idle[0][1]()
    assert seen == {'trigger_source': 'manual', 'start_date': date(2026, 10, 1), 'end_date': date(2026, 10, 1)}
    assert client.post('/api/jobs/sec_backfill/run', json={'from': '2026-10-01', 'to': '2026-10-05'}).status_code == 200


def test_old_job_names_are_404(client, idle):
    for name in ('workbench', 'workbench_review', 'filings'):
        resp = client.post(f'/api/jobs/{name}/run', json={})
        assert resp.status_code == 404 and resp.json() == {'error': f'unknown job: {name}'}


# ---------------------------------------------------------------------------------------------- POST /agent-runs/rerun

RERUN_BODY = {'ticker': 'nvda', 'signal_date': '2026-10-22', 'mode': 'propose', 'request_id': REQUEST_ID,
              'origin': 'run_detail'}


def test_rerun_200_and_what_the_service_receives(client, monkeypatch):
    seen = {}

    def rerun(ticker, signal_date, mode, *, request_id, origin, now, deps=None):
        seen.update(ticker=ticker, signal_date=signal_date, mode=mode, request_id=request_id, origin=origin, now=now)
        return {'reset_id': 5, 'run_id': 'abc', 'replayed': False}

    monkeypatch.setattr(service, 'rerun_agent', rerun)
    resp = client.post('/api/workbench/agent-runs/rerun', json=RERUN_BODY)
    assert resp.status_code == 200 and resp.json() == {'reset_id': 5, 'run_id': 'abc', 'replayed': False}
    assert (seen['ticker'], seen['signal_date'], seen['mode'], seen['request_id'], seen['origin']) == (
        'NVDA', date(2026, 10, 22), 'propose', REQUEST_ID, 'run_detail')
    client.post('/api/workbench/agent-runs/rerun', json={k: v for k, v in RERUN_BODY.items() if k != 'origin'})
    assert seen['origin'] == 'api'                                              # no origin: the API caller's own


def test_rerun_replay_is_the_same_answer(client, monkeypatch):
    answers = iter([{'reset_id': 5, 'run_id': 'abc', 'replayed': False}, {'reset_id': 5, 'run_id': None, 'replayed': True}])
    monkeypatch.setattr(service, 'rerun_agent', lambda *a, **k: next(answers))
    first = client.post('/api/workbench/agent-runs/rerun', json=RERUN_BODY).json()
    again = client.post('/api/workbench/agent-runs/rerun', json=RERUN_BODY).json()
    assert first['reset_id'] == again['reset_id'] and again['replayed'] is True and again['run_id'] is None


@pytest.mark.parametrize('body', [{}, {'ticker': 'NVDA'}, {'signal_date': '2026-10-22'},
                                  {'ticker': 'NVDA', 'signal_date': 'not-a-date', 'mode': 'propose'}])
def test_rerun_400_for_a_missing_ticker_or_date(client, monkeypatch, body):
    monkeypatch.setattr(service, 'rerun_agent', lambda *a, **k: pytest.fail('must not be called'))
    assert client.post('/api/workbench/agent-runs/rerun', json=body).status_code == 400


@pytest.mark.parametrize('change', [{'mode': 'review'}, {'mode': ''}, {'mode': None}, {'origin': 'plan'},
                                    {'origin': 'cron'}, {'request_id': 'not-a-uuid'}, {'request_id': None}])
def test_rerun_400_from_the_real_service_checks(client, monkeypatch, change):
    """Bad mode / origin / request id are refused by ``service.rerun_agent`` before it reads anything."""
    resp = client.post('/api/workbench/agent-runs/rerun', json={**RERUN_BODY, **change})
    assert resp.status_code == 400 and 'error' in resp.json()


@pytest.mark.parametrize('exc, status, error', [
    (LookupError('NVDA 2026-10-22 没有候选，不是决策单元'), 404, 'NVDA 2026-10-22 没有候选，不是决策单元'),
    (service.AgentConflict('not_needed'), 409, 'not_needed'),
    (daily.DailyBusy(), 409, 'daily_running'),
    (ValueError('NVDA 2026-10-22 不是无冲突的买入单元'), 400, 'NVDA 2026-10-22 不是无冲突的买入单元'),
    (service.Unavailable('数据库不可达'), 503, '数据库不可达'),
    (psycopg.errors.UndefinedTable('relation "agent_attempt_resets" does not exist'), 503, 'migration_028_missing'),
])
def test_rerun_error_mapping(client, monkeypatch, exc, status, error):
    monkeypatch.setattr(service, 'rerun_agent', lambda *a, **k: (_ for _ in ()).throw(exc))
    resp = client.post('/api/workbench/agent-runs/rerun', json=RERUN_BODY)
    assert resp.status_code == status and resp.json() == {'error': error}


def test_the_existing_agent_run_routes_still_win(client, monkeypatch):
    """``POST /agent-runs`` (single unit) and ``GET /agent-runs/{id}`` are not shadowed by ``/agent-runs/rerun``."""
    monkeypatch.setattr(service, 'run_agent', lambda ticker, signal_date, *, now, deps=None: {'id': 'r9'})
    monkeypatch.setattr(service, 'agent_run_detail', lambda run_id: {'id': run_id})
    assert client.post('/api/workbench/agent-runs', json={'ticker': 'AMD', 'signal_date': '2026-10-21'}).json() == {'id': 'r9'}
    assert client.get('/api/workbench/agent-runs/rerun').json() == {'id': 'rerun'}
