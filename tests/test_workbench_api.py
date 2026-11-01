import re
from datetime import UTC, date, datetime

import legacy_decisions

import pytest

from investment_assistant.core import tx as core_tx

from investment_assistant.workbench import derivation_view, ledger, service


def raiser(exc):
    def fn(*args, **kwargs):
        raise exc
    return fn


@pytest.mark.parametrize('method, path, target', [
    ('get', '/api/workbench/today', 'today'),
    ('get', '/api/workbench/instruments/NVDA', 'instrument'),
    ('get', '/api/workbench/review/shadow', 'shadow'),
    ('get', '/api/workbench/review/trades', 'trade_review'),
    ('get', '/api/workbench/weekly-reviews', 'list_weekly'),
])
def test_unavailable_database_is_503_not_empty(client, monkeypatch, method, path, target):
    monkeypatch.setattr(service, target, raiser(service.Unavailable('工作台数据表未就绪')))
    resp = getattr(client, method)(path)
    assert resp.status_code == 503 and '未就绪' in resp.json()['error']


@pytest.mark.parametrize('exc, code', [
    (ledger.LedgerError('quantity must be > 0'), 400),
    (ledger.Conflict('plan_stale: 成交时这条链已是第 2 版，请关联第 2 版'), 409),
    (service.Unavailable('down'), 503),
])
def test_trade_errors_map_to_status(client, monkeypatch, exc, code):
    monkeypatch.setattr(service, 'add_trade', raiser(exc))
    assert client.post('/api/workbench/trades', json={}).status_code == code


def test_decisions_post_410(client):
    """v2.9 D24: the decision button is gone; the answer is 410 with the reason code and the sentence, and nothing is
    written (the service raises before it opens a transaction)."""
    resp = client.post('/api/workbench/decisions', json={'reason': 'r'})
    assert resp.status_code == 410
    assert resp.json() == {'error': 'decisions_retired: v2.9 起不再记录决定，采纳由成交推导；可在计划上写备注'}


def test_trade_with_decision_id_is_400_decision_link_retired(client, monkeypatch):
    seen = {}

    def add_trade(payload, *, now):
        seen['payload'] = payload
        return ledger_module.record_trade(None, payload, now=now)
    from investment_assistant.workbench import ledger as ledger_module
    monkeypatch.setattr(service, 'add_trade', add_trade)
    resp = client.post('/api/workbench/trades', json={
        'request_id': '6f1d1c1e-6666-4444-8888-000000000001', 'ticker': 'NVDA', 'side': 'buy', 'quantity': '1',
        'price': '1', 'executed_at': '2026-10-23T14:00:00+00:00', 'decision_id': '6f1d1c1e-6666-4444-8888-000000000002'})
    assert resp.status_code == 400
    assert resp.json()['error'] == 'decision_link_retired: v2.9 起成交不再关联决定，请改为关联计划'


def test_void_unknown_trade_is_404(client, monkeypatch):
    monkeypatch.setattr(service, 'void_trade', raiser(ledger.NotFound('trade not found')))
    assert client.post('/api/workbench/trades/abc/void', json={'reason': 'x'}).status_code == 404


def test_trade_passes_payload_and_server_time(client, monkeypatch):
    seen = {}

    def add_trade(payload, *, now):
        seen.update(payload=payload, now=now)
        return {'status': 'created', 'record': {'id': 't1'}}
    monkeypatch.setattr(service, 'add_trade', add_trade)
    resp = client.post('/api/workbench/trades', json={'ticker': 'MU', 'plan_id': 'p1', 'executed_at': '2000-01-01T00:00:00Z'})
    assert resp.status_code == 200 and resp.json()['status'] == 'created'
    assert seen['payload'] == {'ticker': 'MU', 'plan_id': 'p1', 'executed_at': '2000-01-01T00:00:00Z'}
    assert seen['now'].tzinfo is not None and seen['now'].year >= 2026


def test_weekly_review_post_parses_week(client, monkeypatch):
    calls = {}

    def generate(week_start, *, notes, supersedes_id, now):
        calls.update(week_start=week_start, notes=notes, supersedes_id=supersedes_id)
        return {'id': 'w1'}
    monkeypatch.setattr(service, 'generate_weekly', generate)
    resp = client.post('/api/workbench/weekly-reviews', json={'week_start': '2026-10-19', 'notes': '笔记'})
    assert resp.status_code == 200 and calls == {'week_start': date(2026, 10, 19), 'notes': '笔记', 'supersedes_id': None}
    assert client.post('/api/workbench/weekly-reviews', json={'week_start': 'bad'}).status_code == 400


def test_shadow_since_is_validated(client, monkeypatch):
    monkeypatch.setattr(service, 'shadow', lambda *, now, since: {'since': since})
    assert client.get('/api/workbench/review/shadow?since=2026-09-01').json() == {'since': '2026-09-01'}
    assert client.get('/api/workbench/review/shadow?since=nope').status_code == 400


def test_manual_run_is_submitted_in_background(client, monkeypatch):
    """v2.8 §4.8: the manual run is the daily workflow, with its run id known up front for the detail page."""
    from investment_assistant.tasks import daily, runner
    submitted = []
    monkeypatch.setattr(daily, 'is_running', lambda: False)
    monkeypatch.setattr(runner, 'submit', lambda kind, fn: submitted.append((kind, fn)) or 'daily-20261029030000-aaaaaaaa')
    resp = client.post('/api/workbench/run')
    body = resp.json()
    assert resp.status_code == 200 and body['status'] == 'pending' and body['run_id'] == 'daily-20261029030000-aaaaaaaa'
    assert re.fullmatch(r'daily-\d{14}-[0-9a-f]{8}', body['daily_run_id']) and [k for k, _ in submitted] == ['daily']
    seen = {}
    monkeypatch.setattr(daily, 'run', lambda **kw: seen.update(kw) or {})
    submitted[0][1]()                                           # the background body: a manual run with that id
    assert seen == {'trigger_source': 'manual', 'run_id': body['daily_run_id']}


def test_manual_run_while_daily_is_running_is_409(client, monkeypatch):
    from investment_assistant.tasks import daily, runner
    monkeypatch.setattr(daily, 'is_running', lambda: True)
    monkeypatch.setattr(runner, 'submit', lambda *a, **k: pytest.fail('must not submit'))
    resp = client.post('/api/workbench/run')
    assert resp.status_code == 409 and resp.json() == {'error': 'daily_running'}


def test_existing_keys_unchanged(client, monkeypatch):
    """v2.8 only adds keys: the manual run keeps ``run_id`` / ``status``, a scheduled row and a job report keep theirs,
    and any other job's run answer is still the v2.7 pair (``daily_run_id`` belongs to ``daily`` alone)."""
    from investment_assistant import db
    from investment_assistant.services import jobs
    from investment_assistant.tasks import daily, runner

    monkeypatch.setattr(daily, 'is_running', lambda: False)
    monkeypatch.setattr(runner, 'submit', lambda kind, fn: f'{kind}-20261029030000-aaaaaaaa')
    run = client.post('/api/workbench/run').json()
    assert {'run_id', 'status'} <= set(run) and set(run) - {'run_id', 'status'} == {'daily_run_id'}
    monkeypatch.setattr(jobs, 'REGISTRY', {'metrics': lambda config, **kw: {}})
    monkeypatch.setattr(jobs, 'load_config', lambda: object())
    assert client.post('/api/jobs/metrics/run', json={}).json() == {'run_id': 'metrics-20261029030000-aaaaaaaa',
                                                                    'status': 'pending'}
    monkeypatch.setenv('INVESTMENT_ASSISTANT_DATABASE_URL', 'postgres://x')
    monkeypatch.setattr(jobs, '_with_conn', lambda fn: fn('CONN'))
    scheduled_row = {'name': 'daily', 'time_local': '11:30', 'weekday_mask': '2-6', 'timezone': 'Asia/Shanghai',
                     'enabled': True, 'next_run_at': None, 'last_run_at': None}
    monkeypatch.setattr(db, 'list_scheduled_jobs', lambda conn: [dict(scheduled_row)])
    listed = client.get('/api/jobs/scheduled').json()
    assert set(listed) == {'jobs', 'degraded', 'scheduler'} and set(listed['jobs'][0]) == set(scheduled_row) | {'description'}
    report = {'task': 'daily', 'run_id': 'daily-20261029030000-aaaaaaaa', 'status': 'success', 'started_at': None,
              'finished_at': None, 'summary': {}, 'created_at': None, 'target_start_date': None,
              'target_end_date': None, 'trigger_source': 'manual'}
    monkeypatch.setattr(db, 'list_job_reports', lambda conn, *, task, limit: [dict(report)])
    reports = client.get('/api/jobs/reports').json()
    assert set(reports) == {'reports', 'degraded'} and set(reports['reports'][0]) == set(report)


def test_task_is_registered_with_scheduler():
    from investment_assistant.tasks import daily, scheduler
    assert scheduler.REGISTRY['daily'] is daily.run


def test_trade_list_endpoint(client, monkeypatch):
    monkeypatch.setattr(service, 'list_trades', lambda: [{'id': 't1'}])
    assert client.get('/api/workbench/trades').json() == {'trades': [{'id': 't1'}]}
    monkeypatch.setattr(service, 'list_trades', raiser(service.Unavailable('down')))
    assert client.get('/api/workbench/trades').status_code == 503


@pytest.mark.parametrize('method, path, target, kwargs', [
    ('get', '/api/workbench/account/snapshots', 'account_view', {}),
    ('get', '/api/workbench/account/flows', 'account_view', {}),
    ('post', '/api/workbench/account/snapshots', 'add_snapshot', {'json': {}}),
    ('post', '/api/workbench/account/flows', 'add_flow', {'json': {}}),
    ('post', '/api/workbench/account/flows/x/void', 'void_flow', {'json': {'reason': 'r'}}),
])
def test_account_routes_503_without_db(client, monkeypatch, method, path, target, kwargs):
    monkeypatch.setattr(service, target, raiser(service.Unavailable('工作台数据表未就绪')))
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code == 503 and '未就绪' in resp.json()['error']


def test_post_flow_400_on_zero(client, monkeypatch):
    monkeypatch.setattr(service, 'add_flow', raiser(ledger.LedgerError('amount must be non-zero')))
    assert client.post('/api/workbench/account/flows', json={'amount': 0}).status_code == 400


def test_bad_own_levels_400_and_nothing_written(client, workbench_db, monkeypatch):
    from contextlib import contextmanager
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from investment_assistant.workbench import repository
    from investment_assistant.workbench.rules import Candidate

    @contextmanager
    def connection():
        yield workbench_db

    monkeypatch.setattr(core_tx, 'connection', connection)
    now = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)
    unit_day = date(2026, 10, 22)
    repository.store_candidate(workbench_db, 'NVDA', unit_day,
                               Candidate('trend_reclaim', 'buy', {'signal_date': unit_day.isoformat()}), run_id='r')
    repository.insert_plan(workbench_db, {
        'ticker': 'NVDA', 'signal_date': unit_day, 'kind': 'rule', 'based_on_id': None, 'plan_version': 'plan-1',
        'ref_price': Decimal('100'), 'atr': Decimal('2'), 'entry_low': Decimal('99'), 'entry_high': Decimal('101'),
        'stop': Decimal('94'), 'target1': Decimal('108'), 'trail_atr_multiple': Decimal('3'),
        'valid_from': date(2026, 10, 23), 'valid_until': date(2026, 10, 27), 'sizing': {}, 'status': 'ok',
        'invalid_reasons': [], 'warnings': [], 'inputs': {}, 'request_id': None, 'request_hash': None})

    resp = client.post('/api/workbench/trades', json={
        'request_id': '6f1d1c1e-6666-4444-8888-000000000001', 'ticker': 'NVDA', 'side': 'buy', 'quantity': '5',
        'price': '100.5', 'executed_at': '2026-10-23T14:00:00+00:00',
        'own_plan': {'entry_low': 'abc', 'entry_high': '101.5', 'stop': '94', 'target1': '112'}})
    assert resp.status_code == 400
    assert workbench_db.execute("SELECT count(*) FROM trade_plans WHERE kind = 'own'").fetchone()[0] == 0
    assert workbench_db.execute('SELECT count(*) FROM trades').fetchone()[0] == 0


def test_plan_shadow_503_without_db(client, monkeypatch):
    monkeypatch.setattr(service, 'plan_shadow', raiser(service.Unavailable('工作台数据表未就绪')))
    resp = client.get('/api/workbench/plans/shadow')
    assert resp.status_code == 503 and '未就绪' in resp.json()['error']


def test_plan_shadow_since_is_validated(client):
    assert client.get('/api/workbench/plans/shadow', params={'since': '2026-13-01'}).status_code == 400


def test_discipline_503_without_db(client, monkeypatch):
    monkeypatch.setattr(service, 'discipline', raiser(service.Unavailable('工作台数据表未就绪')))
    assert client.get('/api/workbench/discipline').status_code == 503


def test_monthly_reviews_routes(client, monkeypatch):
    monkeypatch.setattr(service, 'list_monthly', raiser(service.Unavailable('工作台数据表未就绪')))
    assert client.get('/api/workbench/monthly-reviews').status_code == 503
    monkeypatch.setattr(service, 'generate_monthly', raiser(service.Unavailable('工作台数据表未就绪')))
    assert client.post('/api/workbench/monthly-reviews', json={'month': '2026-10-01'}).status_code == 503
    assert client.post('/api/workbench/monthly-reviews', json={}).status_code == 400
    assert client.post('/api/workbench/monthly-reviews', json={'month': '2026-10-15'}).status_code == 400


def test_push_route_forces(client, monkeypatch):
    seen = {}

    def push(*, now, force, notify_cfg=None, client=None):
        seen['force'] = force
        return {'session': '2026-10-22', 'sent': True, 'messages': 4}

    monkeypatch.setattr(service, 'push', push)
    resp = client.post('/api/workbench/push')
    assert resp.status_code == 200 and seen['force'] is True and resp.json()['sent'] is True


def test_backcheck_route_runs_and_lists(client, monkeypatch):
    from investment_assistant.workbench import service
    seen = {}

    def run_backcheck(*, now, tickers=None):
        seen['tickers'] = tickers
        return {'id': 'b1', 'calc_version': 'backcheck-1', 'stats': {'all': {'triggers': 0}}}
    monkeypatch.setattr(service, 'run_backcheck', run_backcheck)
    monkeypatch.setattr(service, 'list_backchecks', lambda limit=10: [{'id': 'b1', 'calc_version': 'backcheck-1'}])
    resp = client.post('/api/workbench/backchecks', json={'tickers': ['NVDA', 'MU']})
    assert resp.status_code == 200 and resp.json()['id'] == 'b1'
    assert seen['tickers'] == ['NVDA', 'MU']
    listing = client.get('/api/workbench/backchecks')
    assert listing.status_code == 200
    assert listing.json() == {'backchecks': [{'id': 'b1', 'calc_version': 'backcheck-1'}]}


def test_agent_run_routes(client, monkeypatch):
    # GET 列表与覆盖率
    monkeypatch.setattr(service, 'agent_runs', lambda *, now, since=None: {
        'since': '2026-10-08', 'runs': [{'id': 'r1', 'ticker': 'NVDA'}],
        'coverage': {'units': 1, 'covered': 1, 'missing': []}})
    resp = client.get('/api/workbench/agent-runs')
    assert resp.status_code == 200 and resp.json()['coverage']['units'] == 1
    assert client.get('/api/workbench/agent-runs?since=2026-10-01').status_code == 200

    # GET 详情：404 与正常
    monkeypatch.setattr(service, 'agent_run_detail', raiser(LookupError('no such run')))
    assert client.get('/api/workbench/agent-runs/abc').status_code == 404
    monkeypatch.setattr(service, 'agent_run_detail', lambda run_id: {'id': run_id, 'tool_calls': []})
    assert client.get('/api/workbench/agent-runs/abc').status_code == 200

    # POST：400（非买入）、409（已有建议）、503（库未就绪）、200（成功）
    body = {'ticker': 'AMD', 'signal_date': '2026-10-21'}
    monkeypatch.setattr(service, 'run_agent', raiser(ValueError('not a buy unit')))
    assert client.post('/api/workbench/agent-runs', json=body).status_code == 400
    monkeypatch.setattr(service, 'run_agent', raiser(service.AgentConflict('already ok')))
    assert client.post('/api/workbench/agent-runs', json=body).status_code == 409
    monkeypatch.setattr(service, 'run_agent', raiser(service.Unavailable('数据库不可达')))
    assert client.post('/api/workbench/agent-runs', json=body).status_code == 503
    monkeypatch.setattr(service, 'run_agent',
                        lambda ticker, signal_date, *, now, deps=None: {'id': 'r9', 'status': 'ok',
                                                                         'reason': None})
    resp = client.post('/api/workbench/agent-runs', json=body)
    assert resp.status_code == 200 and resp.json() == {'id': 'r9', 'status': 'ok', 'reason': None}
    assert client.post('/api/workbench/agent-runs', json={'ticker': 'AMD'}).status_code == 400


def test_tracks_route_bad_date_400(client):
    assert client.get('/api/workbench/review/tracks', params={'since': '2026-10-xx'}).status_code == 400


def test_tracks_route_since_after_until_400(client):
    resp = client.get('/api/workbench/review/tracks',
                      params={'since': '2026-11-01', 'until': '2026-10-01'})
    assert resp.status_code == 400


def test_tracks_route_passes_dates(client, monkeypatch):
    calls = {}

    def tracks(*, now, since=None, until=None):
        calls.update(since=since, until=until)
        return {'units': [], 'summary': {}}
    monkeypatch.setattr(service, 'tracks', tracks)
    resp = client.get('/api/workbench/review/tracks',
                      params={'since': '2026-10-01', 'until': '2026-10-22'})
    assert resp.status_code == 200
    assert calls == {'since': date(2026, 10, 1), 'until': date(2026, 10, 22)}


def test_review_push_route_validation(client, monkeypatch):
    from investment_assistant.workbench import ledger, service

    monkeypatch.setattr(service, 'push_review',
                        raiser(ledger.NotFound('没有快照')))
    resp = client.post('/api/workbench/review-pushes', json={'kind': 'weekly', 'period': '2026-10-19'})
    assert resp.status_code == 404
    monkeypatch.setattr(service, 'push_review', raiser(ledger.LedgerError('period 必须是周一')))
    assert client.post('/api/workbench/review-pushes',
                       json={'kind': 'weekly', 'period': '2026-10-21'}).status_code == 400
    calls = {}

    def push_review(kind, period, **kwargs):
        calls.update(kind=kind, period=period)
        return {'kind': kind, 'period': period, 'sent': True}
    monkeypatch.setattr(service, 'push_review', push_review)
    resp = client.post('/api/workbench/review-pushes', json={'kind': 'weekly', 'period': '2026-10-19'})
    assert resp.status_code == 200
    assert calls == {'kind': 'weekly', 'period': date(2026, 10, 19)}


def test_review_pushes_list_route(client, monkeypatch):
    monkeypatch.setattr(service, 'list_review_pushes', lambda limit=20: [{'id': 1}])
    assert client.get('/api/workbench/review-pushes').json() == {'pushes': [{'id': 1}]}
    assert client.get('/api/workbench/review-pushes', params={'limit': 5}).status_code == 200
    assert client.get('/api/workbench/review-pushes', params={'limit': 0}).status_code == 400
    assert client.get('/api/workbench/review-pushes', params={'limit': 101}).status_code == 400


def test_screen_view_passthrough(client, monkeypatch):
    monkeypatch.setattr(service, 'screen_view',
                        lambda *, now, session=None, full=False: {'report': {'full': full, 'session': session}})
    resp = client.get('/api/workbench/screen')
    assert resp.status_code == 200
    assert resp.json()['report'] == {'full': False, 'session': None}
    resp = client.get('/api/workbench/screen?session=2026-10-22&full=1')
    assert resp.status_code == 200
    assert resp.json()['report'] == {'full': True, 'session': date(2026, 10, 22).isoformat()}


def test_screen_replay_route(client, monkeypatch):
    seen = {}

    def replay_screen(*, now, sessions):
        seen['sessions'] = sessions
        return {'reports': [{'session': '2026-10-22'}]}

    monkeypatch.setattr(service, 'replay_screen', replay_screen)
    resp = client.post('/api/workbench/screen/replay', json={'sessions': 21})
    assert resp.status_code == 400
    resp = client.post('/api/workbench/screen/replay', json={})
    assert resp.status_code == 200 and seen['sessions'] == 5
    assert resp.json()['reports'][0]['session'] == '2026-10-22'


PLAN_ID = '6f1d1c1e-6666-4444-8888-000000000001'


def test_plan_derivation_route_delegates(client, monkeypatch):
    seen = {}

    def plan_derivation(plan_id):
        seen['plan_id'] = plan_id
        return {'derivation_version': 'derivation-1', 'plan': {'id': 'p1'},
                'trigger': [{'rule_id': 'trend_reclaim'}], 'inputs': {}, 'steps': [], 'checks': [],
                'consistency': {'status': 'consistent'}, 'evidence': '证据状态',
                'trigger_note': '触发段只复核冻结取值', 'run': {'ready': True, 'run_id': 'r1'}}

    monkeypatch.setattr(derivation_view, 'plan_derivation', plan_derivation)
    resp = client.get(f'/api/workbench/plans/{PLAN_ID}/derivation')
    assert resp.status_code == 200
    assert seen == {'plan_id': PLAN_ID}                                  # id 原样传给用例
    body = resp.json()
    assert body['trigger'] == {'note': '触发段只复核冻结取值',        # 说明收进 trigger 段，不是顶层键
                               'candidates': [{'rule_id': 'trend_reclaim'}]}
    assert 'trigger_note' not in body and body['run'] == {'ready': True, 'run_id': 'r1'}


def test_unit_derivation_route_delegates(client, monkeypatch):
    seen = {}

    def unit_derivation(ticker, signal_date):
        seen.update(ticker=ticker, signal_date=signal_date)
        return {'derivation_version': 'derivation-1', 'trigger': [], 'consistency': {'status': 'consistent'},
                'evidence': '证据状态', 'note': '触发段只复核冻结取值', 'plan_ids': [PLAN_ID]}

    monkeypatch.setattr(derivation_view, 'unit_derivation', unit_derivation)
    resp = client.get('/api/workbench/units/AAPL/2026-10-22/derivation')
    assert resp.status_code == 200 and resp.json()['plan_ids'] == [PLAN_ID]
    assert seen == {'ticker': 'AAPL', 'signal_date': date(2026, 10, 22)}  # ticker 原样、日期已解析
    assert client.get('/api/workbench/units/AAPL/2026-13-01/derivation').status_code == 400


def test_stage_runs_route_delegates_and_serializes_stages(client, monkeypatch):
    from investment_assistant.workbench import stages as stages_module
    seen = {}

    def stage_runs_view(*, session=None, run_id=None):
        seen.update(session=session, run_id=run_id)
        return {'stages': list(stages_module.STAGES[:2]), 'stage_runs_ready': True,
                'runs': [{'run_id': 'r1', 'rows': [{'stage': 'collect', 'session': date(2026, 10, 22),
                                                    'started_at': None}]}]}

    monkeypatch.setattr(derivation_view, 'stage_runs_view', stage_runs_view)
    resp = client.get('/api/workbench/stage-runs', params={'session': '2026-10-22'})
    assert resp.status_code == 200
    assert seen == {'session': date(2026, 10, 22), 'run_id': None}
    body = resp.json()
    assert body['stages'] == [{'id': s.id, 'title': s.title, 'purpose': s.purpose}
                              for s in stages_module.STAGES[:2]]          # StageDef 显式序列化成 dict
    assert body['stage_runs_ready'] is True
    assert body['runs'][0]['rows'][0]['session'] == '2026-10-22'
    assert client.get('/api/workbench/stage-runs', params={'run_id': 'r1'}).status_code == 200
    assert seen == {'session': None, 'run_id': 'r1'}


def test_derivation_bad_uuid_is_400(client):
    assert client.get('/api/workbench/plans/not-a-uuid/derivation').status_code == 400


def test_derivation_unknown_plan_is_404(client, monkeypatch):
    monkeypatch.setattr(derivation_view, 'plan_derivation', raiser(LookupError(f'计划不存在：{PLAN_ID}')))
    assert client.get(f'/api/workbench/plans/{PLAN_ID}/derivation').status_code == 404


def test_unit_derivation_bad_ticker_is_400(client):
    assert client.get('/api/workbench/units/1234/2026-10-22/derivation').status_code == 400


def test_unit_derivation_without_candidates_is_404(client, monkeypatch):
    monkeypatch.setattr(derivation_view, 'unit_derivation', raiser(LookupError('该单元没有候选')))
    assert client.get('/api/workbench/units/NVDA/2026-10-22/derivation').status_code == 404


def test_stage_runs_needs_exactly_one_param(client):
    assert client.get('/api/workbench/stage-runs').status_code == 400                     # 两个都缺
    assert client.get('/api/workbench/stage-runs',
                      params={'session': '2026-10-22', 'run_id': 'r1'}).status_code == 400  # 两个都给


def test_stage_runs_bad_session_is_400(client):
    assert client.get('/api/workbench/stage-runs', params={'session': '2026-13-01'}).status_code == 400


@pytest.mark.parametrize('path, target, kwargs', [
    (f'/api/workbench/plans/{PLAN_ID}/derivation', 'plan_derivation', {}),
    ('/api/workbench/units/NVDA/2026-10-22/derivation', 'unit_derivation', {}),
    ('/api/workbench/stage-runs', 'stage_runs_view', {'params': {'session': '2026-10-22'}}),
])
def test_derivation_routes_503_without_db(client, monkeypatch, path, target, kwargs):
    monkeypatch.setattr(derivation_view, target, raiser(service.Unavailable('工作台数据表未就绪')))
    resp = client.get(path, **kwargs)
    assert resp.status_code == 503 and '未就绪' in resp.json()['error']


# ---------------------------------------------------------------- v2.5 plan timeline (spec §12)

def test_chain_view_route_delegates(client, monkeypatch):
    from investment_assistant.workbench import plan_review
    seen = {}

    def chain_view(ticker, signal_date, *, now):
        seen.update(ticker=ticker, signal_date=signal_date, now=now)
        return {'state': {'state': 'entry'}, 'versions': [], 'reviews': [], 'inputs': []}

    monkeypatch.setattr(plan_review, 'chain_view', chain_view)
    resp = client.get('/api/workbench/chains/SYNTH/2026-09-01')
    assert resp.status_code == 200 and resp.json()['state'] == {'state': 'entry'}
    assert (seen['ticker'], seen['signal_date']) == ('SYNTH', date(2026, 9, 1)) and seen['now'].tzinfo is not None
    assert client.get('/api/workbench/chains/SYNTH/2026-13-01').status_code == 400


def test_plan_reviews_route_delegates(client, monkeypatch):
    from investment_assistant.workbench import plan_review
    monkeypatch.setattr(plan_review, 'reviews_view', lambda session: {'session': session, 'reviews': []})
    assert client.get('/api/workbench/plan-reviews', params={'session': '2026-09-02'}).json() == \
        {'session': '2026-09-02', 'reviews': []}
    assert client.get('/api/workbench/plan-reviews').status_code == 400
    assert client.get('/api/workbench/plan-reviews', params={'session': 'bad'}).status_code == 400


def test_recheck_route_checks_then_submits_with_request_time(client, monkeypatch):
    from investment_assistant.api.routes import workbench as routes
    from investment_assistant.tasks import runner
    from investment_assistant.workbench import plan_review
    t0 = datetime(2026, 9, 2, 22, 30, tzinfo=UTC)
    clock = iter([t0, t0.replace(minute=35)])
    monkeypatch.setattr(routes, '_now', lambda: next(clock))
    checked, ran, submitted = [], [], []
    monkeypatch.setattr(plan_review, 'recheck_check',
                        lambda ticker, signal_date, *, now: checked.append((ticker, signal_date, now)))
    monkeypatch.setattr(plan_review, 'recheck', lambda ticker, signal_date, *, now, run_id=None, deps=None:
                        ran.append((ticker, signal_date, now, run_id)) or {'id': 1})

    def submit(kind, fn, *, with_run_id=False):
        submitted.append((kind, fn, with_run_id))
        return 'plan_recheck-1'

    monkeypatch.setattr(runner, 'submit', submit)
    resp = client.post('/api/workbench/chains/SYNTH/2026-09-01/recheck')
    assert resp.status_code == 200 and resp.json() == {'run_id': 'plan_recheck-1', 'status': 'pending'}
    assert checked == [('SYNTH', date(2026, 9, 1), t0)]
    [(kind, fn, with_run_id)] = submitted
    assert kind == 'plan_recheck' and ran == []                       # 后台执行，路由里不跑复查
    routes._now()                                                    # 时间往前走：后台晚 5 分钟才开始
    fn('plan_recheck-1') if with_run_id else fn()
    assert ran[0][:3] == ('SYNTH', date(2026, 9, 1), t0)              # 截止时间仍是请求进入时刻
    if with_run_id:
        assert ran[0][3] == 'plan_recheck-1'


@pytest.mark.parametrize('exc, code, message', [
    ('conflict', 409, 'review_in_progress'),
    ('not_active', 409, 'chain_not_active'),
    ('agent', 409, 'agent_unavailable: not_configured'),
    ('lookup', 404, None),
    ('value', 400, None),
    ('unavailable', 503, '迁移 026 未执行'),
])
def test_recheck_route_errors_do_not_submit(client, monkeypatch, exc, code, message):
    from investment_assistant.tasks import runner
    from investment_assistant.workbench import plan_review
    error = {'conflict': plan_review.RecheckConflict('review_in_progress'),
             'not_active': plan_review.RecheckConflict('chain_not_active'),
             'agent': plan_review.RecheckConflict('agent_unavailable: not_configured'),
             'lookup': LookupError('没有计划链'), 'value': ValueError('Invalid US stock ticker'),
             'unavailable': service.Unavailable('迁移 026 未执行')}[exc]
    monkeypatch.setattr(plan_review, 'recheck_check', raiser(error))
    submitted = []
    monkeypatch.setattr(runner, 'submit', lambda *a, **k: submitted.append(a) or 'x')
    resp = client.post('/api/workbench/chains/SYNTH/2026-09-01/recheck')
    assert resp.status_code == code and submitted == []
    if message:
        assert resp.json()['error'] == message
    assert client.post('/api/workbench/chains/SYNTH/nope/recheck').status_code == 400


def test_user_inputs_routes_delegate(client, monkeypatch):
    from investment_assistant.workbench import user_inputs
    seen = {}

    def list_for(ticker, signal_date):
        seen.update(ticker=ticker, signal_date=signal_date)
        return [{'id': 1, 'ref': 'user:1'}]

    def add(payload, *, now):
        seen.update(payload=payload, now=now)
        return {'status': 'created', 'record': {'id': 2}}

    monkeypatch.setattr(user_inputs, 'list_for', list_for)
    monkeypatch.setattr(user_inputs, 'add', add)
    resp = client.get('/api/workbench/user-inputs', params={'ticker': 'SYNTH', 'signal_date': '2026-09-01'})
    assert resp.json() == {'inputs': [{'id': 1, 'ref': 'user:1'}]}
    assert seen == {'ticker': 'SYNTH', 'signal_date': date(2026, 9, 1)}
    client.get('/api/workbench/user-inputs', params={'ticker': 'SYNTH'})
    assert seen['signal_date'] is None
    assert client.get('/api/workbench/user-inputs').status_code == 400
    assert client.get('/api/workbench/user-inputs', params={'ticker': 'SYNTH', 'signal_date': 'x'}).status_code == 400
    body = {'request_id': 'r', 'ticker': 'SYNTH', 'kind': 'news', 'body': '新闻'}
    resp = client.post('/api/workbench/user-inputs', json=body)
    assert resp.json() == {'status': 'created', 'record': {'id': 2}}
    assert seen['payload'] == body and seen['now'].tzinfo is not None


@pytest.mark.parametrize('exc, code', [
    (ValueError('body must be 1–4000 characters'), 400),
    (LookupError('该单元没有候选'), 404),
    (ledger.Conflict('request_id was already used with different content'), 409),
    (service.Unavailable('迁移 026 未执行'), 503),
])
def test_user_inputs_post_errors(client, monkeypatch, exc, code):
    from investment_assistant.workbench import user_inputs
    monkeypatch.setattr(user_inputs, 'add', raiser(exc))
    resp = client.post('/api/workbench/user-inputs', json={})
    assert resp.status_code == code and resp.json()['error'] == str(exc)


@pytest.mark.parametrize('path, module, target', [
    ('/api/workbench/chains/SYNTH/2026-09-01', 'plan_review', 'chain_view'),
    ('/api/workbench/plan-reviews?session=2026-09-02', 'plan_review', 'reviews_view'),
    ('/api/workbench/user-inputs?ticker=SYNTH', 'user_inputs', 'list_for'),
])
def test_timeline_reads_map_errors(client, monkeypatch, path, module, target):
    import importlib
    mod = importlib.import_module(f'investment_assistant.workbench.{module}')
    for exc, code in ((service.Unavailable('迁移 026 未执行'), 503), (LookupError('没有'), 404),
                      (ValueError('Invalid US stock ticker'), 400)):
        monkeypatch.setattr(mod, target, raiser(exc))
        resp = client.get(path)
        assert resp.status_code == code and resp.json()['error'] == str(exc)


def test_ticker_chains_route_delegates_and_maps_errors(client, monkeypatch):
    from investment_assistant.workbench import plan_review
    seen = {}

    def ticker_chains(ticker, *, now):
        seen.update(ticker=ticker, now=now)
        return {'ticker': 'SYNTH', 'chains': [{'signal_date': date(2026, 9, 1), 'state': 'entry'}]}

    monkeypatch.setattr(plan_review, 'ticker_chains', ticker_chains)
    resp = client.get('/api/workbench/chains', params={'ticker': 'synth'})
    assert resp.status_code == 200
    assert resp.json() == {'ticker': 'SYNTH', 'chains': [{'signal_date': '2026-09-01', 'state': 'entry'}]}
    assert seen['ticker'] == 'synth' and seen['now'].tzinfo is not None
    assert client.get('/api/workbench/chains').status_code == 400
    for exc, code in ((ValueError('Invalid US stock ticker'), 400), (service.Unavailable('迁移 026 未执行'), 503)):
        monkeypatch.setattr(plan_review, 'ticker_chains', raiser(exc))
        resp = client.get('/api/workbench/chains', params={'ticker': 'SYNTH'})
        assert resp.status_code == code and resp.json()['error'] == str(exc)


# --- v2.6 run overview, reasoning view, instrument page additions ---

def test_run_overview_endpoint(client, monkeypatch):
    from investment_assistant.workbench import run_overview
    seen = {}

    def overview(*, session, run_id, now):
        seen.update(session=session, run_id=run_id, now=now)
        return {'session': '2026-10-27', 'ready': True, 'stages': []}
    monkeypatch.setattr(run_overview, 'overview', overview)
    resp = client.get('/api/workbench/run-overview?session=2026-10-27&run_id=run-a')
    assert resp.status_code == 200 and resp.json()['ready'] is True
    assert seen['session'] == date(2026, 10, 27) and seen['run_id'] == 'run-a' and seen['now'].tzinfo is not None
    assert client.get('/api/workbench/run-overview').status_code == 200
    assert seen['session'] is None and seen['run_id'] is None
    assert client.get('/api/workbench/run-overview?session=nope').status_code == 400
    monkeypatch.setattr(run_overview, 'overview', raiser(LookupError('run_id 不属于该日')))
    assert client.get('/api/workbench/run-overview?run_id=zzz').status_code == 404
    monkeypatch.setattr(run_overview, 'overview', raiser(service.Unavailable('数据库不可达')))
    assert client.get('/api/workbench/run-overview').status_code == 503


def test_reasoning_endpoint(client, monkeypatch):
    from investment_assistant.workbench import reasoning
    monkeypatch.setattr(reasoning, 'reasoning', lambda run_id: {'run': {'id': run_id}, 'steps': []})
    resp = client.get('/api/workbench/agent-runs/6f1d1c1e-6666-4444-8888-000000000001/reasoning')
    assert resp.status_code == 200 and resp.json()['run']['id'] == '6f1d1c1e-6666-4444-8888-000000000001'
    monkeypatch.setattr(reasoning, 'reasoning', raiser(ValueError('运行 id 不是合法的 UUID')))
    assert client.get('/api/workbench/agent-runs/nope/reasoning').status_code == 400
    monkeypatch.setattr(reasoning, 'reasoning', raiser(LookupError('运行不存在')))
    assert client.get('/api/workbench/agent-runs/6f1d1c1e-6666-4444-8888-000000000001/reasoning').status_code == 404
    monkeypatch.setattr(reasoning, 'reasoning', raiser(service.Unavailable('数据库不可达')))
    assert client.get('/api/workbench/agent-runs/6f1d1c1e-6666-4444-8888-000000000001/reasoning').status_code == 503


def test_agent_run_detail_route_still_wins_for_plain_ids(client, monkeypatch):
    monkeypatch.setattr(service, 'agent_run_detail', lambda run_id: {'id': run_id})
    assert client.get('/api/workbench/agent-runs/abc').json() == {'id': 'abc'}


# the instrument tests below run the real batch against the disposable schema
from test_workbench_service import NOW as BATCH_NOW, SESSION as BATCH_SESSION, _ok_responses, agent_deps, db  # noqa: E402,F401,E501
from investment_assistant.api.routes import workbench as workbench_routes  # noqa: E402

OLD_KEYS = {'ticker', 'session', 'bars', 'chart', 'filings', 'news', 'candidates', 'decisions', 'trades',
            'position', 'closed'}
NEW_KEYS = {'focus', 'key_data', 'context', 'plans', 'holding', 'errors'}   # v2.7 adds 'knowledge' (checked beside)


@pytest.fixture
def seeded(db, client, monkeypatch, scripted_model):
    from investment_assistant.workbench import service as service_module
    service_module.run_batch(tickers=['AAA'], now=BATCH_NOW, deps=agent_deps(scripted_model, _ok_responses('AAA')))
    db.commit()
    monkeypatch.setattr(workbench_routes, '_now', lambda: BATCH_NOW)
    return db


def test_instrument_keeps_existing_keys(seeded, client):
    body = client.get('/api/workbench/instruments/AAA').json()
    assert OLD_KEYS <= set(body) and NEW_KEYS <= set(body)
    assert body['ticker'] == 'AAA' and body['session'] == BATCH_SESSION.isoformat()
    assert isinstance(body['bars'], list) and body['bars'] and set(body['bars'][-1]) >= {'price_date', 'close'}
    assert set(body['chart']) == {'bars', 'adjustment', 'annotations'} and body['chart']['adjustment'] == 'split_only'
    assert set(body['chart']['bars'][-1]) >= {'price_date', 'ma20', 'ma50', 'ma200'}
    for key in ('filings', 'news', 'candidates', 'decisions', 'trades'):
        assert isinstance(body[key], list), key
    assert body['position'] is None and body['closed'] == []
    assert {c['rule_id'] for c in body['candidates']} >= {'trend_reclaim'}


def test_instrument_new_keys(seeded, client):
    body = client.get('/api/workbench/instruments/AAA').json()
    assert body['errors'] == {}
    kinds = [item['kind'] for item in body['focus']]
    assert kinds == ['agent_plan', 'trigger']
    agent_item, trigger_item = body['focus']
    assert agent_item['text'].startswith('Agent 第 0 版 · 入场期 · 入场 ') and agent_item['text'].endswith('尚未复查')
    assert agent_item['link'].startswith('#plan/')
    assert trigger_item['text'].startswith('今天触发 ') and '趋势回踩修复（买入）' in trigger_item['text']
    key_data = body['key_data']
    assert key_data['as_of'] == BATCH_SESSION.isoformat() and key_data['values']['last_close'] is not None
    assert set(key_data['values']) == set(key_data['concepts'])
    assert set(body['context']) == {'earnings', 'regime', 'sector'}
    assert body['context']['sector'] is None                                 # no sector tag on this ticker
    plans = {p['kind']: p for p in body['plans']}
    assert set(plans) == {'rule', 'agent'}
    assert set(plans['rule']) == {'id', 'kind', 'plan_version', 'signal_date', 'entry_low', 'entry_high', 'stop',
                                  'target1', 'valid_from', 'valid_until', 'status', 'version_no'}
    assert plans['rule']['version_no'] is None and plans['agent']['version_no'] == 0
    annotations = body['chart']['annotations']
    assert set(annotations) == {'markers', 'segments', 'omitted'} and annotations['omitted'] == 0
    marker = annotations['markers'][-1]
    assert marker['time'] == BATCH_SESSION.isoformat() and marker['kind'] == 'candidate'
    assert marker['shape'] == 'arrowUp' and marker['position'] == 'belowBar' and '回踩' in marker['text']
    assert marker['refs']
    # plans start the session after the last bar, so there is nothing to draw on the existing bars yet
    assert annotations['segments'] == []


def test_instrument_without_timeline_version_no_null(seeded, client):
    seeded.execute('DROP TABLE plan_reviews, user_inputs, agent_plan_versions CASCADE')
    seeded.commit()
    resp = client.get('/api/workbench/instruments/AAA')
    assert resp.status_code == 200
    body = resp.json()
    assert OLD_KEYS <= set(body) and body['errors'] == {}
    assert {p['version_no'] for p in body['plans']} == {None}
    assert [item['kind'] for item in body['focus']] == ['trigger']


def test_instrument_new_key_failure_is_isolated(seeded, client, monkeypatch):
    from investment_assistant.workbench import instrument_view

    def broken(*args, **kwargs):
        raise RuntimeError('指标算坏了')
    monkeypatch.setattr(instrument_view, 'key_data', broken)
    body = client.get('/api/workbench/instruments/AAA').json()
    assert body['key_data'] is None and body['errors'] == {'key_data': '指标算坏了'}
    assert body['focus'] and body['plans'] and OLD_KEYS <= set(body)
    assert body['chart']['annotations']['markers']                          # other new keys unaffected


def test_instrument_unknown_ticker_still_returns_empty_view(seeded, client):
    body = client.get('/api/workbench/instruments/ZZZZ').json()
    assert body['bars'] == [] and body['chart']['bars'] == []
    assert body['chart']['annotations'] == {'markers': [], 'segments': [], 'omitted': 0}
    assert body['key_data']['values']['last_close'] is None and body['plans'] == []
    assert [item['kind'] for item in body['focus']] == ['none']


def test_instrument_holding_key(seeded, client):
    from test_workbench_service import _decide
    from investment_assistant.workbench import service as service_module
    from investment_assistant.workbench import repository as repo

    # not held -> null; the existing `position` key is unchanged (also null)
    body = client.get('/api/workbench/instruments/AAA').json()
    assert body['holding'] is None and body['position'] is None

    unit = next(u for u in service_module.unit_cards(now=BATCH_NOW)['units'] if u['ticker'] == 'AAA')
    head = repo.chain_head(seeded, 'AAA', BATCH_SESSION)
    decision = _decide(unit, 'buy', plan_id=head['plan']['id'], now=BATCH_NOW)
    legacy_decisions.add_trade({'request_id': '6f1d1c1e-9999-4444-8888-000000000001', 'ticker': 'AAA', 'side': 'buy',
                              'quantity': '10', 'price': '100.00', 'executed_at': '2026-10-22T15:00:00+00:00',
                              'decision_id': str(decision['id'])}, now=BATCH_NOW)
    seeded.commit()

    body = client.get('/api/workbench/instruments/AAA').json()
    holding = body['holding']
    today_row = next(h for h in client_json(service.unit_cards(now=BATCH_NOW))['holdings'] if h['ticker'] == 'AAA')
    assert set(holding) == {'quantity', 'avg_cost', 'plan_id', 'stop', 'mode', 't1_reached', 'target1', 'last_close',
                            'to_stop_pct', 'to_target_pct'}
    assert holding == {k: today_row[k] for k in holding}            # same row as 「今日」, field by field
    assert holding['plan_id'] == str(head['plan']['id']) and holding['stop'] is not None
    assert holding['target1'] is not None and holding['to_stop_pct'] is not None
    assert body['position']['quantity'] is not None and body['errors'] == {}
    focus = {item['kind']: item['text'] for item in body['focus']}
    assert f"当前止损 {float(holding['stop']):.2f}（按你决定的计划）" in focus['position']


def client_json(value):
    """The JSON a route would send for `value` (FastAPI's encoder), so service rows compare with HTTP bodies."""
    import json

    from fastapi.encoders import jsonable_encoder
    return json.loads(json.dumps(jsonable_encoder(value)))


def test_instrument_holding_failure_is_isolated(seeded, client, monkeypatch):
    from test_workbench_service import _decide
    from investment_assistant.workbench import service as service_module
    from investment_assistant.workbench import repository as repo

    unit = next(u for u in service_module.unit_cards(now=BATCH_NOW)['units'] if u['ticker'] == 'AAA')
    decision = _decide(unit, 'buy', plan_id=repo.chain_head(seeded, 'AAA', BATCH_SESSION)['plan']['id'], now=BATCH_NOW)
    legacy_decisions.add_trade({'request_id': '6f1d1c1e-9999-4444-8888-000000000002', 'ticker': 'AAA', 'side': 'buy',
                              'quantity': '10', 'price': '100.00', 'executed_at': '2026-10-22T15:00:00+00:00',
                              'decision_id': str(decision['id'])}, now=BATCH_NOW)
    seeded.commit()

    def broken(*args, **kwargs):
        raise RuntimeError('止损算坏了')
    monkeypatch.setattr(service_module, '_position_plan_state', broken)
    body = client.get('/api/workbench/instruments/AAA').json()
    assert body['holding'] is None and body['errors']['holding'] == '止损算坏了'
    assert body['focus'] is None and 'holding' in body['errors']['focus']       # no silent "无计划" line
    assert body['plans'] and body['key_data'] and body['position']['quantity'] is not None


# ---------------------------------------------------------------- v2.7 growth loop (spec §12), use cases mocked

from investment_assistant.workbench import discussion, my_knowledge, retro_cards  # noqa: E402

CARD_BODY = {'request_id': 'r1', 'note': '', 'lessons': []}
GROWTH_CALLS = [
    ('get', '/api/workbench/review-cards', None, retro_cards, 'list_view'),
    ('get', '/api/workbench/review-cards/7', None, retro_cards, 'card_view'),
    ('post', '/api/workbench/review-cards/7/verdict', CARD_BODY, retro_cards, 'verdict'),
    ('get', '/api/workbench/my-knowledge', None, my_knowledge, 'list_view'),
    ('post', '/api/workbench/my-knowledge', {'request_id': 'r2'}, my_knowledge, 'create'),
    ('get', '/api/workbench/my-knowledge/3', None, my_knowledge, 'item_view'),
    ('post', '/api/workbench/my-knowledge/3/versions', {'request_id': 'r3', 'base_version': 1}, my_knowledge, 'revise'),
    ('post', '/api/workbench/my-knowledge/3/status', {'request_id': 'r4'}, my_knowledge, 'set_status'),
    ('get', '/api/workbench/my-knowledge/3/performance', None, my_knowledge, 'performance'),
    ('get', '/api/workbench/my-knowledge-drafts', None, my_knowledge, 'drafts_view'),
    ('post', '/api/workbench/my-knowledge-drafts/4/verdict', {'request_id': 'r5', 'action': 'dismiss'},
     my_knowledge, 'draft_verdict'),
    ('post', '/api/workbench/discussions', {'request_id': 'r6', 'context': {}}, discussion, 'create'),
    ('get', '/api/workbench/discussions?card_id=2', None, discussion, 'list_for'),
    ('get', '/api/workbench/discussions/5', None, discussion, 'view'),
    ('post', '/api/workbench/discussions/5/turns', {'request_id': 'r7', 'body': '问'}, discussion, 'ask_check'),
    ('post', '/api/workbench/review-cards/7/redraft', None, retro_cards, 'redraft_check'),
]


@pytest.mark.parametrize('exc, code, message', [
    (ValueError('字段不对'), 400, '字段不对'),
    (LookupError('不存在'), 404, '不存在'),
    (ledger.Conflict('card_reviewed'), 409, 'card_reviewed'),
    (ledger.Conflict('knowledge_stale: 这条知识已更新到第 2 版，请刷新后再改'), 409,
     'knowledge_stale: 这条知识已更新到第 2 版，请刷新后再改'),
    (service.Unavailable('迁移 027 未执行'), 503, '迁移 027 未执行'),
])
@pytest.mark.parametrize('method, path, body, module, target', GROWTH_CALLS,
                         ids=[f'{c[0]}-{c[1].split("/api/workbench/")[1]}' for c in GROWTH_CALLS])
def test_growth_routes_map_errors(client, monkeypatch, method, path, body, module, target, exc, code, message):
    monkeypatch.setattr(module, target, raiser(exc))
    resp = getattr(client, method)(path, **({'json': body} if body is not None else {}))
    assert resp.status_code == code and resp.json() == {'error': message}


def test_review_card_routes_delegate(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(retro_cards, 'list_view', lambda **kw: seen.update(list=kw) or {'cards': [], 'counts': {}})
    monkeypatch.setattr(retro_cards, 'card_view', lambda card_id, *, now: seen.update(card=(card_id, now)) or {'ok': 1})
    monkeypatch.setattr(retro_cards, 'verdict', lambda card_id, payload, *, now: seen.update(
        verdict=(card_id, payload, now)) or {'status': 'created'})
    assert client.get('/api/workbench/review-cards?status=to_review&ticker=nvda&since=2026-10-01').status_code == 200
    assert seen['list'] == {'status': 'to_review', 'ticker': 'NVDA', 'since': date(2026, 10, 1)}
    client.get('/api/workbench/review-cards')
    assert seen['list'] == {'status': None, 'ticker': None, 'since': None}
    assert client.get('/api/workbench/review-cards?since=nope').status_code == 400
    assert client.get('/api/workbench/review-cards/abc').status_code == 400
    assert client.get('/api/workbench/review-cards/7').json() == {'ok': 1} and seen['card'][0] == 7
    assert seen['card'][1].tzinfo is not None
    resp = client.post('/api/workbench/review-cards/7/verdict', json=CARD_BODY)
    assert resp.json() == {'status': 'created'} and seen['verdict'][:2] == (7, CARD_BODY)


def test_redraft_route_checks_then_submits_with_request_time(client, monkeypatch):
    from investment_assistant.tasks import runner
    t0 = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
    monkeypatch.setattr(workbench_routes, '_now', lambda: t0)
    checked, ran, submitted = [], [], []
    monkeypatch.setattr(retro_cards, 'redraft_check', lambda card_id, *, now=None: checked.append((card_id, now)))
    monkeypatch.setattr(retro_cards, 'redraft', lambda card_id, *, now, deps=None, run_id=None:
                        ran.append((card_id, now, run_id)) or {'status': 'ok'})
    monkeypatch.setattr(runner, 'submit', lambda kind, fn, *, with_run_id=False:
                        submitted.append((kind, fn, with_run_id)) or 'retro_draft-1')
    resp = client.post('/api/workbench/review-cards/7/redraft')
    assert resp.status_code == 200 and resp.json() == {'run_id': 'retro_draft-1', 'status': 'pending'}
    assert checked == [(7, t0)]
    [(kind, fn, with_run_id)] = submitted
    assert kind == 'retro_draft' and with_run_id is True and ran == []
    fn('retro_draft-1')
    assert ran == [(7, t0, 'retro_draft-1')]


@pytest.mark.parametrize('exc, code, message', [
    (ledger.Conflict('draft_exists'), 409, 'draft_exists'),
    (ledger.Conflict('card_reviewed'), 409, 'card_reviewed'),
    (ledger.Conflict('agent_unavailable: not_configured'), 409, 'agent_unavailable: not_configured'),
    (ledger.Conflict('token_budget'), 409, 'token_budget'),
    (LookupError('复盘卡 7 不存在'), 404, '复盘卡 7 不存在'),
    (service.Unavailable('迁移 027 未执行'), 503, '迁移 027 未执行'),
])
def test_redraft_route_errors_do_not_submit(client, monkeypatch, exc, code, message):
    from investment_assistant.tasks import runner
    monkeypatch.setattr(retro_cards, 'redraft_check', raiser(exc))
    submitted = []
    monkeypatch.setattr(runner, 'submit', lambda *a, **k: submitted.append(a) or 'x')
    resp = client.post('/api/workbench/review-cards/7/redraft')
    assert (resp.status_code, resp.json()['error']) == (code, message) and submitted == []


def test_my_knowledge_routes_delegate(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(my_knowledge, 'list_view', lambda **kw: seen.update(list=kw) or {'items': []})
    monkeypatch.setattr(my_knowledge, 'item_view', lambda knowledge_id, *, version=None:
                        seen.update(item=(knowledge_id, version)) or {'ok': 1})
    monkeypatch.setattr(my_knowledge, 'drafts_view', lambda *, open_only: seen.update(open=open_only) or {'drafts': []})
    monkeypatch.setattr(my_knowledge, 'revise', lambda knowledge_id, payload, *, now: seen.update(
        revise=(knowledge_id, payload)) or {'status': 'created'})
    client.get('/api/workbench/my-knowledge')
    assert seen['list'] == {'ticker': None, 'concept': None, 'status': 'active'}
    client.get('/api/workbench/my-knowledge?ticker=AMD&concept=plan.stop&status=all')
    assert seen['list'] == {'ticker': 'AMD', 'concept': 'plan.stop', 'status': 'all'}
    client.get('/api/workbench/my-knowledge/3')
    assert seen['item'] == (3, None)
    client.get('/api/workbench/my-knowledge/3?version=2')
    assert seen['item'] == (3, 2)
    assert client.get('/api/workbench/my-knowledge/3?version=x').status_code == 400
    assert client.get('/api/workbench/my-knowledge-drafts').json() == {'drafts': []} and seen['open'] is True
    client.get('/api/workbench/my-knowledge-drafts?status=all')
    assert seen['open'] is False
    assert client.get('/api/workbench/my-knowledge-drafts?status=closed').status_code == 400
    client.post('/api/workbench/my-knowledge/3/versions', json={'request_id': 'r', 'base_version': 4})
    assert seen['revise'] == (3, {'request_id': 'r', 'base_version': 4})


def test_discussion_routes_delegate(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(discussion, 'list_for', lambda *, plan_id=None, card_id=None: seen.update(
        list=(plan_id, card_id)) or [{'id': 1}])
    assert client.get('/api/workbench/discussions?card_id=2').json() == {'discussions': [{'id': 1}]}
    assert seen['list'] == (None, 2)
    client.get('/api/workbench/discussions?plan_id=abc')
    assert seen['list'] == ('abc', None)
    assert client.get('/api/workbench/discussions').status_code == 400                    # exactly one of the two
    assert client.get('/api/workbench/discussions?plan_id=abc&card_id=2').status_code == 400


def test_discussion_turn_route_checks_submits_and_tracks(client, monkeypatch):
    from investment_assistant.tasks import runner
    t0 = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
    monkeypatch.setattr(workbench_routes, '_now', lambda: t0)
    asked, ran, submitted, tracked = [], [], [], []
    monkeypatch.setattr(discussion, 'ask_check', lambda discussion_id, payload, *, now: asked.append(
        (discussion_id, payload, now)) or {'turn_id': 11, 'run_id': 'uuid-1', 'replayed': False})
    monkeypatch.setattr(discussion, 'answer', lambda discussion_id, turn_id, *, now, deps=None, run_id:
                        ran.append((discussion_id, turn_id, now, run_id)) or {'status': 'ok'})
    monkeypatch.setattr(discussion, 'track', lambda key, runner_id: tracked.append((key, runner_id)))
    monkeypatch.setattr(runner, 'submit', lambda kind, fn, *, with_run_id=False:
                        submitted.append((kind, fn, with_run_id)) or 'discuss-1')
    body = {'request_id': 'r', 'body': '这个止损怎么来的？'}
    resp = client.post('/api/workbench/discussions/5/turns', json=body)
    assert resp.status_code == 202 and resp.json() == {'turn_id': 11, 'run_id': 'discuss-1', 'status': 'pending'}
    assert asked == [(5, body, t0)] and tracked == [('uuid-1', 'discuss-1')]
    [(kind, fn, with_run_id)] = submitted
    assert kind == 'discuss' and with_run_id is True and ran == []
    fn('discuss-1')
    assert ran == [(5, 11, t0, 'discuss-1')]


def test_discussion_turn_replay_and_failed_submit(client, monkeypatch):
    from investment_assistant.tasks import runner
    monkeypatch.setattr(discussion, 'ask_check', lambda *a, **k: {'turn_id': 11, 'run_id': 'uuid-1', 'replayed': True})
    monkeypatch.setattr(runner, 'submit', lambda *a, **k: pytest.fail('a replay does not start another run'))
    resp = client.post('/api/workbench/discussions/5/turns', json={'request_id': 'r', 'body': 'x'})
    assert resp.status_code == 202 and resp.json() == {'turn_id': 11, 'run_id': 'uuid-1', 'status': 'replayed'}
    released = []
    monkeypatch.setattr(discussion, 'ask_check', lambda *a, **k: {'turn_id': 12, 'run_id': 'uuid-2', 'replayed': False})
    monkeypatch.setattr(discussion, 'release', released.append)

    def broken(*a, **k):
        raise RuntimeError('no threads')

    monkeypatch.setattr(runner, 'submit', broken)
    with pytest.raises(RuntimeError):
        client.post('/api/workbench/discussions/5/turns', json={'request_id': 'r2', 'body': 'x'})
    assert released == ['uuid-2']                                  # the turn does not stay "pending" forever


# ---------------------------------------------------------------- v2.7 growth loop, real database

def _grow(client, path, body):
    resp = client.post(path, json=body)
    return resp


def test_growth_http_flow(seeded, client, monkeypatch, scripted_model):
    """Knowledge written, revised, archived and read back over HTTP; a discussion asked and answered; the
    error bodies are `{error: <code>}` as the page expects."""
    import json
    import uuid
    from langchain_core.messages import AIMessage
    from investment_assistant.tasks import runner
    from test_workbench_retro_cards import stage_deps

    from investment_assistant.workbench import agent_connection

    # v2.8: the agent is available when the ChatGPT connection is (the API-key variable is gone)
    monkeypatch.setattr(agent_connection, 'read_for_settings', lambda: {
        'status': 'connected', 'scopes_ok': True, 'model': 'gpt-test', 'secret': 'test-access-token'})
    made = _grow(client, '/api/workbench/my-knowledge', {
        'request_id': str(uuid.uuid4()), 'title': '回踩不追', 'body': '正文', 'scope': '范围', 'counter_examples': '',
        'concepts': ['plan.stop'], 'tickers': ['AAA']})
    assert made.status_code == 200 and made.json()['status'] == 'created'
    kid = made.json()['record']['knowledge_id']
    listed = client.get('/api/workbench/my-knowledge').json()['items']
    assert [(i['id'], i['title'], i['current_version'], i['status']) for i in listed] == [(kid, '回踩不追', 1, 'active')]
    assert client.get('/api/workbench/my-knowledge?ticker=AAA').json()['items'][0]['id'] == kid
    assert client.get('/api/workbench/my-knowledge?ticker=MU').json() == {'items': []}
    revised = _grow(client, f'/api/workbench/my-knowledge/{kid}/versions', {
        'request_id': str(uuid.uuid4()), 'base_version': 1, 'title': '回踩不追二', 'body': '正文二', 'scope': '范围',
        'counter_examples': '反例', 'concepts': ['plan.stop'], 'tickers': ['AAA']})
    assert revised.json()['record']['version'] == 2
    stale = _grow(client, f'/api/workbench/my-knowledge/{kid}/versions', {
        'request_id': str(uuid.uuid4()), 'base_version': 1, 'title': 'x', 'body': 'x', 'scope': 'x', 'tickers': ['AAA']})
    assert stale.status_code == 409 and stale.json() == {'error': 'knowledge_stale: 这条知识已更新到第 2 版，请刷新后再改'}
    flip = {'request_id': str(uuid.uuid4()), 'status': 'archived', 'reason': '过时'}
    assert _grow(client, f'/api/workbench/my-knowledge/{kid}/status', flip).status_code == 200
    again = _grow(client, f'/api/workbench/my-knowledge/{kid}/status', {**flip, 'request_id': str(uuid.uuid4())})
    assert again.status_code == 409 and again.json() == {'error': 'status_unchanged'}
    item = client.get(f'/api/workbench/my-knowledge/{kid}?version=1').json()
    assert item['current']['version'] == 1 and len(item['versions']) == 2 and item['item']['status'] == 'archived'
    assert client.get(f'/api/workbench/my-knowledge/{kid}/performance').json()['sample']['count'] == 0
    assert client.get('/api/workbench/my-knowledge/999').status_code == 404
    bad = _grow(client, '/api/workbench/my-knowledge', {'request_id': str(uuid.uuid4()), 'title': ''})
    assert bad.status_code == 400 and 'title' in bad.json()['error']

    from investment_assistant.workbench import repository as repo
    plan_id = str(repo.chain_head(seeded, 'AAA', BATCH_SESSION)['plan']['id'])
    created = _grow(client, '/api/workbench/discussions', {'request_id': str(uuid.uuid4()),
                                                            'context': {'kind': 'plan', 'plan_id': plan_id}})
    discussion_id = created.json()['record']['id']
    d = stage_deps(scripted_model, [AIMessage(content=json.dumps(
        {'answer': '止损来自 ATR。', 'refs': ['fact:context'], 'knowledge_draft': None}, ensure_ascii=False))])
    real_answer = discussion.answer
    monkeypatch.setattr(discussion, 'answer', lambda *a, **k: real_answer(*a, **{**k, 'deps': d}))
    submitted = []

    def inline(kind, fn, *, with_run_id=False):
        submitted.append(kind)
        run_id = f'{kind}-inline'
        discussion.track(next(iter(discussion._RUNNERS)), run_id)
        fn(run_id)
        return run_id

    monkeypatch.setattr(runner, 'submit', inline)
    asked = _grow(client, f'/api/workbench/discussions/{discussion_id}/turns', {
        'request_id': str(uuid.uuid4()), 'body': '这个止损怎么来的？'})
    assert asked.status_code == 202 and asked.json()['status'] == 'pending' and submitted == ['discuss']
    view = client.get(f'/api/workbench/discussions/{discussion_id}').json()
    assert [t['role'] for t in view['turns']] == ['user', 'assistant'] and view['turns'][1]['status'] == 'ok'
    assert client.get(f'/api/workbench/discussions?plan_id={plan_id}').json()['discussions'][0]['turn_count'] == 1
    assert client.get('/api/workbench/discussions/999').status_code == 404


def test_growth_routes_503_without_migration_027(seeded, client):
    seeded.execute('DROP TABLE knowledge_citations, knowledge_draft_verdicts, review_card_verdicts, knowledge_drafts, '
                   'knowledge_versions, knowledge_status, discussion_turns, knowledge_items, discussions, '
                   'review_card_drafts, review_cards CASCADE')
    seeded.commit()
    for method, path in (('get', '/api/workbench/review-cards'), ('get', '/api/workbench/my-knowledge'),
                         ('get', '/api/workbench/my-knowledge-drafts'), ('get', '/api/workbench/discussions/1'),
                         ('get', '/api/workbench/discussions?card_id=1')):
        resp = getattr(client, method)(path)
        assert resp.status_code == 503 and '迁移 027' in resp.json()['error'], path
    assert client.post('/api/workbench/my-knowledge', json={'request_id': 'x'}).status_code in (400, 503)
    body = client.get('/api/workbench/instruments/AAA').json()
    assert body['knowledge'] is None and body['errors'] == {'knowledge': '迁移 027 未执行'}
    assert client.get('/api/workbench/today').status_code == 200
    unit = next(u for u in client_json(service.unit_cards(now=BATCH_NOW))['units'] if u['ticker'] == 'AAA')
    assert unit['agent']['knowledge_refs'] == []
    chain = client.get(f'/api/workbench/chains/AAA/{BATCH_SESSION.isoformat()}').json()
    assert [v['knowledge_refs'] for v in chain['versions']] == [[]]


def _cite_head_plan(seeded, title='引用过的经验'):
    from investment_assistant.workbench import repository as repo
    kid = my_knowledge.create({'request_id': '6f1d1c1e-6666-4444-8888-000000000009', 'title': title, 'body': '正文',
                               'scope': '范围', 'counter_examples': '', 'concepts': [], 'tickers': ['AAA']},
                              now=BATCH_NOW)['record']['knowledge_id']
    head = repo.chain_head(seeded, 'AAA', BATCH_SESSION)
    repo.insert_knowledge_citations(seeded, [
        {'agent_run_id': head['version']['agent_run_id'], 'plan_id': head['plan']['id'], 'knowledge_id': kid,
         'version': 1, 'field': 'thesis', 'call_seq': 1},
        {'agent_run_id': head['version']['agent_run_id'], 'plan_id': head['plan']['id'], 'knowledge_id': kid,
         'version': 1, 'field': 'counter', 'call_seq': 1}])
    seeded.commit()
    return kid, str(head['plan']['id'])


def test_today_and_chain_knowledge_refs(seeded, client):
    body = client_json(service.unit_cards(now=BATCH_NOW))        # 「今日」 itself no longer carries unit cards (D34)
    unit = next(u for u in body['units'] if u['ticker'] == 'AAA')
    assert unit['agent']['knowledge_refs'] == []                       # a plan that cited nothing
    kid, plan_id = _cite_head_plan(seeded)
    my_knowledge.revise(kid, {'request_id': '6f1d1c1e-6666-4444-8888-00000000000a', 'base_version': 1,
                              'title': '第二版标题', 'body': '正文', 'scope': '范围', 'tickers': ['AAA']},
                        now=BATCH_NOW)
    expected = [{'ref': f'know:{kid}@1', 'title': '引用过的经验', 'fields': ['thesis', 'counter'], 'current_version': 2}]
    unit = next(u for u in client_json(service.unit_cards(now=BATCH_NOW))['units'] if u['ticker'] == 'AAA')
    assert unit['agent']['knowledge_refs'] == expected
    chain = client.get(f'/api/workbench/chains/AAA/{BATCH_SESSION.isoformat()}').json()
    assert [v['knowledge_refs'] for v in chain['versions']] == [expected]
    assert [v['plan_id'] for v in chain['versions']] == [plan_id]
    assert {'ticker', 'signal_date', 'session', 'state', 'head_plan_id', 'version_no', 'latest_review',
            'shadow_version', 'versions', 'reviews', 'inputs', 'retriggers', 'holding_status', 'notes'} == set(chain)
    # every block that shows a plan carries the key; status-only blocks (error, skipped, rejected) keep their shape
    assert all(('knowledge_refs' in u['agent']) == ('plan' in u['agent'])
               for u in client_json(service.unit_cards(now=BATCH_NOW))['units'] if u.get('agent'))


def test_instrument_knowledge_key(seeded, client):
    body = client.get('/api/workbench/instruments/AAA').json()
    assert body['knowledge'] == [] and body['errors'] == {}
    kid, _ = _cite_head_plan(seeded)
    body = client.get('/api/workbench/instruments/AAA').json()
    [entry] = body['knowledge']
    assert (entry['id'], entry['title'], entry['current_version'], entry['status'], entry['citation_count'],
            entry['sample_count']) == (kid, '引用过的经验', 1, 'active', 2, 1)
    assert client.get('/api/workbench/instruments/MU').json()['knowledge'] == []
    my_knowledge.set_status(kid, {'request_id': '6f1d1c1e-6666-4444-8888-00000000000b', 'status': 'archived',
                                  'reason': ''}, now=BATCH_NOW)
    assert client.get('/api/workbench/instruments/AAA').json()['knowledge'] == []      # archived: not on the page


def test_instrument_knowledge_failure_is_isolated(seeded, client, monkeypatch):
    def broken(conn, ticker):
        raise RuntimeError('知识读坏了')

    monkeypatch.setattr(my_knowledge, 'for_ticker', broken)
    body = client.get('/api/workbench/instruments/AAA').json()
    assert body['knowledge'] is None and body['errors'] == {'knowledge': '知识读坏了'}
    assert OLD_KEYS <= set(body) and body['focus'] and body['plans']


def test_existing_keys_unchanged(seeded, client):
    """v2.7 only adds keys: the instrument page, today and the chain view keep every earlier key."""
    instrument = client.get('/api/workbench/instruments/AAA').json()
    assert OLD_KEYS | NEW_KEYS <= set(instrument) and set(instrument) == OLD_KEYS | NEW_KEYS | {'knowledge'}
    today = client.get('/api/workbench/today').json()
    assert {'session', 'units', 'extended_units', 'holdings', 'market', 'screen', 'chains', 'timeline_ready',
            'retired'} == set(today)
    assert today['units'] == [] and today['extended_units'] == [] and today['retired'] is True      # D34
    unit = next(u for u in client_json(service.unit_cards(now=BATCH_NOW))['units'] if u['ticker'] == 'AAA')
    assert {'id', 'status', 'model', 'agent_version', 'output', 'counter', 'plan', 'input_tokens', 'output_tokens',
            'created_at', 'chain'} <= set(unit['agent'])
    assert set(unit['agent']) - {'id', 'status', 'model', 'agent_version', 'output', 'counter', 'plan', 'input_tokens',
                                 'output_tokens', 'created_at', 'chain', 'legacy'} == {'knowledge_refs'}


def test_plan_table_history_and_linkable_endpoints(seeded, client):
    """v2.9 §9.1 over a real chain: the table row, the history, the link list and the holding status."""
    table = client.get('/api/workbench/plan-table').json()
    assert set(table) == {'session', 'groups', 'other_units', 'counts', 'errors'}
    [row] = table['groups']['entry']
    assert row['ticker'] == 'AAA' and row['chain']['state'] == 'entry' and row['plan']['plan_version'] == 'agent-3'
    assert table['session'] == BATCH_SESSION.isoformat() and table['counts']['entry'] == 1
    history = client.get('/api/workbench/instruments/AAA/plan-history').json()
    assert [r['kind'] for r in history['rows']] == ['chain_open', 'version'] and history['chains'][0]['state'] == 'entry'
    assert client.get('/api/workbench/holding-status').json() == {'session': BATCH_SESSION.isoformat(), 'rows': []}
    when = '2026-10-23T14:00:00+00:00'
    linkable = client.get('/api/workbench/trades/linkable-plans', params={'ticker': 'aaa', 'executed_at': when}).json()
    assert linkable['ticker'] == 'AAA'            # the version was written "now" (the real clock): none existed then
    assert linkable['default_plan_id'] is None


def test_trade_link_errors_and_the_note_endpoint(seeded, client):
    head = next(r for r in client.get('/api/workbench/plan-table').json()['groups']['entry'])['plan']['plan_id']
    body = {'request_id': '6f1d1c1e-6666-4444-8888-0000000000a1', 'ticker': 'AAA', 'side': 'buy', 'quantity': '5',
            'price': '100', 'fee': '0', 'executed_at': '2026-10-22T15:00:00+00:00', 'plan_id': head}
    resp = client.post('/api/workbench/trades', json=body)
    assert resp.status_code == 409 and resp.json()['error'].startswith('plan_after_fill: ')
    seeded.execute('UPDATE agent_plan_versions SET created_at = %s', ['2026-10-22T10:00:00+00:00'])
    seeded.commit()
    done = client.post('/api/workbench/trades', json=body)
    assert done.status_code == 200 and done.json()['record']['plan_id'] == head
    assert client.post('/api/workbench/trades', json={**body, 'request_id': '6f1d1c1e-6666-4444-8888-0000000000a2',
                                                      'decision_id': head}).status_code == 400
    note = {'request_id': '6f1d1c1e-6666-4444-8888-0000000000b1', 'ticker': 'AAA',
            'chain_signal_date': BATCH_SESSION.isoformat(), 'body': '等回踩'}
    created = client.post('/api/workbench/plan-notes', json=note)
    assert created.status_code == 200 and created.json()['status'] == 'created'
    assert client.post('/api/workbench/plan-notes', json=note).json()['status'] == 'existing'
    assert client.post('/api/workbench/plan-notes', json={**note, 'body': '改了'}).status_code == 409
    listed = client.get('/api/workbench/plan-notes', params={'ticker': 'AAA',
                                                              'chain_signal_date': BATCH_SESSION.isoformat()}).json()
    assert [n['body'] for n in listed['notes']] == ['等回踩']
    assert client.post('/api/workbench/plan-notes', json={**note, 'request_id': 'x'}).status_code == 400


def test_notes_on_a_plan_need_no_ticker(seeded, client):
    """I1: the page writes and reads a non-chain plan's notes with the plan id alone; the ticker is the plan's."""
    from investment_assistant.workbench import repository as repo

    rule = repo.rule_plan(seeded, 'AAA', BATCH_SESSION)
    body = {'request_id': '6f1d1c1e-6666-4444-8888-0000000000c1', 'body': '规则基线上的备注', 'plan_id': str(rule['id'])}
    created = client.post('/api/workbench/plan-notes', json=body)
    assert created.status_code == 200 and created.json()['record']['ticker'] == 'AAA'
    assert client.post('/api/workbench/plan-notes', json=body).json()['status'] == 'existing'
    listed = client.get('/api/workbench/plan-notes', params={'plan_id': str(rule['id'])})
    assert listed.status_code == 200 and [n['body'] for n in listed.json()['notes']] == ['规则基线上的备注']
    assert client.post('/api/workbench/plan-notes', json={**body, 'request_id': '6f1d1c1e-6666-4444-8888-0000000000c2',
                                                          'ticker': 'MU'}).status_code == 400
    missing = '6f1d1c1e-6666-4444-8888-0000000000ff'
    assert client.get('/api/workbench/plan-notes', params={'plan_id': missing}).status_code == 404
    assert client.post('/api/workbench/plan-notes', json={**body, 'request_id': '6f1d1c1e-6666-4444-8888-0000000000c3',
                                                          'plan_id': missing}).status_code == 404
    assert client.get('/api/workbench/plan-notes').status_code == 400
