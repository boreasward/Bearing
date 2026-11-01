"""The data routes are thin wrappers: they delegate to data_view and map its errors."""
import pytest

import investment_assistant.views.data as data_view
import investment_assistant.core.tx as service


def raiser(exc):
    def fn(*args, **kwargs):
        raise exc
    return fn


def test_routes_delegate(client, monkeypatch):
    calls = {}

    def overview(*, now):
        calls['now'] = now
        return {'overview': 'ok'}

    monkeypatch.setattr(data_view, 'overview', overview)
    monkeypatch.setattr(data_view, 'family_detail', lambda family_id, *, now: {'detail': family_id})

    def start_fetch(family_id, ticker):
        calls.update(family_id=family_id, ticker=ticker)
        return {'run_id': 'run-1', 'status': 'pending'}

    monkeypatch.setattr(data_view, 'start_fetch', start_fetch)

    def browse(family_id, *, now, ticker=None, series=None):
        return {'browse': family_id, 'ticker': ticker, 'series': series}

    monkeypatch.setattr(data_view, 'browse', browse)

    resp = client.get('/api/data/families')
    assert resp.status_code == 200 and resp.json() == {'overview': 'ok'}
    assert calls['now'].tzinfo is not None
    assert client.get('/api/data/families/bars').json() == {'detail': 'bars'}

    resp = client.post('/api/data/families/news/fetch', json={'ticker': ' mu '})
    assert resp.json() == {'run_id': 'run-1', 'status': 'pending'}
    assert calls == {'now': calls['now'], 'family_id': 'news', 'ticker': ' mu '}  # 原样传入，规范化在用例层
    client.post('/api/data/families/bars/fetch', json={})
    assert calls['ticker'] is None                     # body 不带 ticker 时传 None

    assert client.get('/api/data/browse/bars?ticker=NVDA').json() == {
        'browse': 'bars', 'ticker': 'NVDA', 'series': None}
    assert client.get('/api/data/browse/fred?series=T10Y2Y').json() == {
        'browse': 'fred', 'ticker': None, 'series': 'T10Y2Y'}
    assert client.get('/api/data/browse/universe').json() == {
        'browse': 'universe', 'ticker': None, 'series': None}
    assert client.get('/api/data/browse/bars?ticker=').json()['ticker'] is None   # 空串按缺省


@pytest.mark.parametrize('exc, code', [
    (service.Unavailable('需执行迁移 024'), 503),
    (LookupError('未知数据族：nope'), 404),
    (ValueError('派生族，请拉取日线'), 400),
    (data_view.FetchBusy('该族正在采集'), 409),
])
def test_error_mapping(client, monkeypatch, exc, code):
    monkeypatch.setattr(data_view, 'overview', raiser(exc))
    resp = client.get('/api/data/families')
    assert resp.status_code == code
    assert resp.json()['error'] == str(exc)


def test_busy_fetch_is_409(client, monkeypatch):
    monkeypatch.setattr(data_view, 'start_fetch', raiser(data_view.FetchBusy('该族正在采集')))
    resp = client.post('/api/data/families/bars/fetch', json={})
    assert resp.status_code == 409 and resp.json()['error'] == '该族正在采集'


# ------------------------------------------------- real-route error contract (I06)

def test_non_string_ticker_is_400(client):
    """非字符串 ticker 是参数类型错误（400），不是 .strip() 的 AttributeError（500）。"""
    for bad in (123, ['MU'], True):
        resp = client.post('/api/data/families/bars/fetch', json={'ticker': bad})
        assert resp.status_code == 400, bad
        assert 'ticker' in resp.json()['error']


def test_unreachable_database_is_503_for_post_too(client, monkeypatch):
    """连接异常在 GET 和 POST 一致映射 Unavailable（503），不返回 500（I06a）。"""
    import psycopg

    monkeypatch.setenv('INVESTMENT_ASSISTANT_DATABASE_URL', 'postgresql://test@127.0.0.1:1/postgres')

    def dead_connect(url):
        raise psycopg.OperationalError('connection refused')

    monkeypatch.setattr(service, 'connect', dead_connect)
    assert client.get('/api/data/families').status_code == 503
    resp = client.post('/api/data/families/bars/fetch', json={})
    assert resp.status_code == 503 and resp.json()['error'] == '数据库不可达'


def test_post_checks_tables_and_024_before_submitting(workbench_db, monkeypatch):
    """提交前在同一安全事务里检查工作台表与 024：缺任一 → 503 且不提交后台任务（I06b）。"""
    import os
    from pathlib import Path

    from fastapi.testclient import TestClient
    from investment_assistant import db
    from investment_assistant.api.app import create_app
    import investment_assistant.views.data as data_view

    url = os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']
    schema = workbench_db.execute('SHOW search_path').fetchone()[0]
    monkeypatch.setenv('INVESTMENT_ASSISTANT_DATABASE_URL',
                       f'{url}?options=-csearch_path%3D{schema}')

    submitted = []
    monkeypatch.setattr('investment_assistant.core.runner.submit',
                        lambda kind, job, **kw: submitted.append(kind) or 'fake-run')
    client = TestClient(create_app())

    # 健康 schema：POST 正常受理并提交后台任务
    resp = client.post('/api/data/families/news/fetch', json={})
    assert resp.status_code == 200 and resp.json() == {'run_id': 'fake-run', 'status': 'pending'}
    assert submitted == ['data:news']

    # 缺 024：503 且不提交
    workbench_db.execute('DROP TABLE data_fetch_runs')
    workbench_db.commit()
    resp = client.post('/api/data/families/news/fetch', json={})
    assert resp.status_code == 503 and '需执行迁移 024' in resp.json()['error']
    assert submitted == ['data:news']

    # 024 在但旧表缺：503 且不提交
    workbench_db.execute(Path('migrations', '024_data_fetch_runs.sql').read_text(encoding='utf-8'))
    workbench_db.execute('DROP TABLE price_bars')
    workbench_db.commit()
    resp = client.post('/api/data/families/bars/fetch', json={})
    assert resp.status_code == 503 and '工作台数据表未就绪' in resp.json()['error']
    assert 'price_bars' in resp.json()['error']
    assert submitted == ['data:news']
    db.close_pools()
