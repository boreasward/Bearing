"""Newline-delimited JSON-RPC 2.0 for the MCP tool surface over stdio (Pi 1.5.0 实测口径).

One reply per line, flushed immediately; notifications get no reply; a bad line never
stops the loop. `initialize` must answer `serverInfo` or real clients reject the server."""
from __future__ import annotations

import json
import sys

from investment_assistant.toolsurface import dispatch
from investment_assistant.toolsurface.env import ToolEnv

KNOWN_PROTOCOL_VERSIONS = ('2024-11-05', '2025-03-26', '2025-06-18')
DEFAULT_PROTOCOL_VERSION = '2025-06-18'
SERVER_INFO = {'name': 'ia', 'version': '3.0'}
CAPABILITIES = {'tools': {}}


def handle(env: ToolEnv, message: dict) -> dict | None:
    """One request object; None for notifications (no reply expected)."""
    if not isinstance(message, dict):
        return _error(None, -32600, 'Invalid Request')
    if 'id' not in message:                       # 通知不回复（notifications/initialized 等）
        return None
    msg_id = message['id']
    params = message.get('params') or {}
    method = message.get('method')
    if method == 'initialize':
        requested = params.get('protocolVersion') if isinstance(params, dict) else None
        version = requested if requested in KNOWN_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
        return _result(msg_id, {'protocolVersion': version, 'capabilities': CAPABILITIES,
                                'serverInfo': SERVER_INFO})
    if method == 'ping':
        return _result(msg_id, {})
    if method == 'tools/list':
        tools = [{'name': tool.name, 'description': tool.description,
                  'inputSchema': tool.input_schema} for tool in dispatch.tool_defs(env.mode)]
        return _result(msg_id, {'tools': tools})
    if method == 'tools/call':
        if not isinstance(params, dict) or not isinstance(params.get('name'), str) \
                or ('arguments' in params and not isinstance(params['arguments'], dict)):
            return _error(msg_id, -32602, 'Invalid params')
        envelope = dispatch.call(env, params['name'], params.get('arguments') or {})
        return _result(msg_id, {'content': [{'type': 'text',
                                             'text': json.dumps(envelope, ensure_ascii=False)}],
                                'isError': 'error' in envelope})
    return _error(msg_id, -32601, 'Method not found')


def serve(env: ToolEnv, stdin=sys.stdin, stdout=sys.stdout) -> None:
    """读 stdin 直到 EOF；每条回复一行并立即 flush。坏行回复错误后继续，不停循环。"""
    for line in stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            message = json.loads(stripped)
        except json.JSONDecodeError:
            _emit(stdout, _error(None, -32700, 'Parse error'))
            continue
        if not isinstance(message, dict):
            _emit(stdout, _error(None, -32600, 'Invalid Request'))
            continue
        reply = handle(env, message)
        if reply is not None:
            _emit(stdout, reply)


def _emit(stdout, reply: dict) -> None:
    stdout.write(json.dumps(reply, ensure_ascii=False) + '\n')
    stdout.flush()


def _result(msg_id, result: dict) -> dict:
    return {'jsonrpc': '2.0', 'id': msg_id, 'result': result}


def _error(msg_id, code: int, message: str) -> dict:
    return {'jsonrpc': '2.0', 'id': msg_id, 'error': {'code': code, 'message': message}}
