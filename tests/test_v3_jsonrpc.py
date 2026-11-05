"""toolsurface.jsonrpc: newline-delimited JSON-RPC 2.0 over stdio, no database needed."""
import io
import json
from datetime import date

import pytest
from investment_assistant.toolsurface.env import make_env

from investment_assistant.toolsurface import jsonrpc

SESSION = date(2026, 11, 4)


@pytest.fixture
def env():
    return make_env(session=SESSION, mode='brief', agent_session='t-jsonrpc')


@pytest.fixture(autouse=True)
def fake_dispatch(monkeypatch):
    """dispatch.call 换成假的：ref 按调用序递增，name == 'boom' 时返回错误信封。"""

    seq = 0

    def fake_call(_env, name, args):
        nonlocal seq
        seq += 1
        if name == 'boom':
            return {'ref': f'call:{seq}', 'error': 'no_run'}
        return {'ref': f'call:{seq}', 'data': {'label': 'neutral'}}

    monkeypatch.setattr(jsonrpc.dispatch, 'call', fake_call)
    return fake_call


def test_initialize_list_and_call(env):
    r = jsonrpc.handle(env, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                             'params': {'protocolVersion': '2025-06-18'}})
    assert r['result']['protocolVersion'] == '2025-06-18' and r['result']['capabilities'] == {'tools': {}}
    assert r['result']['serverInfo'] == {'name': 'ia', 'version': '3.0'}
    assert jsonrpc.handle(env, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None
    tools = jsonrpc.handle(env, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})['result']['tools']
    assert {'name', 'description', 'inputSchema'} <= set(tools[0])
    assert all(t['inputSchema']['type'] == 'object' for t in tools)
    assert 'record_trade' not in {t['name'] for t in tools}          # brief 模式不声明 chat 专属工具
    out = jsonrpc.handle(env, {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                               'params': {'name': 'get_market', 'arguments': {}}})['result']
    assert json.loads(out['content'][0]['text'])['ref'] == 'call:1' and out['isError'] is False
    # 工具返回错误信封时 isError is True
    bad = jsonrpc.handle(env, {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                               'params': {'name': 'boom', 'arguments': {}}})['result']
    assert bad['isError'] is True and json.loads(bad['content'][0]['text'])['error'] == 'no_run'
    # 客户端请求的 protocolVersion 不认识 -> 回 '2025-06-18'
    other = jsonrpc.handle(env, {'jsonrpc': '2.0', 'id': 5, 'method': 'initialize',
                                 'params': {'protocolVersion': '2025-99-99'}})
    assert other['result']['protocolVersion'] == '2025-06-18'


def test_unknown_method_and_ping(env):
    assert jsonrpc.handle(env, {'jsonrpc': '2.0', 'id': 4, 'method': 'resources/list'})['error']['code'] == -32601
    assert jsonrpc.handle(env, {'jsonrpc': '2.0', 'id': 5, 'method': 'ping'})['result'] == {}


def test_bad_lines_do_not_stop_the_loop(env):
    # stdin 依次给：一行非 JSON、一行 JSON 数组、一条合法的 tools/list
    stdin = io.StringIO('not json at all\n[1, 2]\n'
                        + json.dumps({'jsonrpc': '2.0', 'id': 7, 'method': 'tools/list'}) + '\n')
    stdout = io.StringIO()
    jsonrpc.serve(env, stdin=stdin, stdout=stdout)
    out = stdout.getvalue()
    lines = out.splitlines()
    assert len(lines) == 3 and out.endswith('\n')                     # 每条回复一行、以换行结尾
    assert json.loads(lines[0])['error']['code'] == -32700 and json.loads(lines[0])['id'] is None
    assert json.loads(lines[1])['error']['code'] == -32600 and json.loads(lines[1])['id'] is None
    assert json.loads(lines[2])['id'] == 7 and 'tools' in json.loads(lines[2])['result']
    # tools/call 的 arguments 不是对象 -> -32602
    r = jsonrpc.handle(env, {'jsonrpc': '2.0', 'id': 8, 'method': 'tools/call',
                             'params': {'name': 'get_market', 'arguments': []}})
    assert r['error']['code'] == -32602


def test_serve_stops_at_eof(env):
    jsonrpc.serve(env, stdin=io.StringIO(''), stdout=io.StringIO())   # 读到 EOF 正常返回
