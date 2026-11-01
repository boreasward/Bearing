"""Path A of the agent model layer (v2.8 spec §8.5, §8.8): ``SiwcChatOpenAI`` against ``httpx.MockTransport`` with
scripted SSE, and ``classify_error``. Offline: no network, no database, no real token."""
import json
from typing import Annotated, TypedDict

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from investment_assistant.core.llm import chatgpt, model
from investment_assistant.core.llm.chatgpt import ChatGPTError

TOKEN = 'test-access-1111111111'
SETTINGS = model.AgentSettings(provider='chatgpt', model='gpt-test', api_key=None, daily_tokens=None,
                               config_error=None, connection='connected')
USAGE = {'input_tokens': 12, 'input_tokens_details': {'cached_tokens': 3}, 'output_tokens': 7,
         'output_tokens_details': {'reasoning_tokens': 2}, 'total_tokens': 19}


# ---------------------------------------------------------------------------------------------- scripted SSE

def _sse(events):
    return ''.join(f"event: {e['type']}\ndata: {json.dumps({**e, 'sequence_number': i})}\n\n"
                   for i, e in enumerate(events))


def _response(output, status='completed'):
    return {'id': 'resp_1', 'object': 'response', 'created_at': 0, 'model': 'gpt-test', 'status': status,
            'output': output, 'parallel_tool_calls': False, 'tool_choice': 'auto', 'tools': [], 'usage': USAGE}


def text_events(text, *, last='response.completed'):
    item = {'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'status': 'completed',
            'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}
    events = [
        {'type': 'response.created', 'response': _response([], 'in_progress')},
        {'type': 'response.output_item.added', 'output_index': 0,
         'item': {**item, 'content': [], 'status': 'in_progress'}},
        {'type': 'response.content_part.added', 'item_id': 'msg_1', 'output_index': 0, 'content_index': 0,
         'part': {'type': 'output_text', 'text': '', 'annotations': []}},
        {'type': 'response.output_text.delta', 'item_id': 'msg_1', 'output_index': 0, 'content_index': 0,
         'delta': text[:3]},
        {'type': 'response.output_text.delta', 'item_id': 'msg_1', 'output_index': 0, 'content_index': 0,
         'delta': text[3:]},
        {'type': 'response.output_text.done', 'item_id': 'msg_1', 'output_index': 0, 'content_index': 0, 'text': text},
        {'type': 'response.content_part.done', 'item_id': 'msg_1', 'output_index': 0, 'content_index': 0,
         'part': item['content'][0]},
        {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
    ]
    if last:
        events.append({'type': last, 'response': _response(
            [item], 'incomplete' if last == 'response.incomplete' else 'completed')})
    return events


def call_events(namespace=None):
    item = {'id': 'fc_1', 'type': 'function_call', 'status': 'completed', 'call_id': 'call_1', 'name': 'get_price',
            'arguments': '{"ticker": "NVDA"}', **({'namespace': namespace} if namespace else {})}
    return [
        {'type': 'response.created', 'response': _response([], 'in_progress')},
        {'type': 'response.output_item.added', 'output_index': 0,
         'item': {**item, 'arguments': '', 'status': 'in_progress'}},
        {'type': 'response.function_call_arguments.delta', 'item_id': 'fc_1', 'output_index': 0,
         'delta': '{"ticker": "NVDA"}'},
        {'type': 'response.function_call_arguments.done', 'item_id': 'fc_1', 'output_index': 0,
         'arguments': '{"ticker": "NVDA"}', 'name': 'get_price'},
        {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
        {'type': 'response.completed', 'response': _response([item])},
    ]


def failed_events(code):
    return [{'type': 'response.created', 'response': _response([], 'in_progress')},
            {'type': 'response.failed', 'response': {**_response([], 'failed'),
                                                     'error': {'code': code, 'message': 'sensitive server text'}}}]


class Wire:
    """A MockTransport that answers with the next scripted SSE body and records every request."""

    def __init__(self, *scripts, status=200, body=None):
        self.scripts, self.requests, self.status, self.body = list(scripts), [], status, body

    def __call__(self, request):
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json=self.body, request=request)
        script = self.scripts[len(self.requests) - 1]
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=_sse(script),
                              request=request)

    def bodies(self):
        return [json.loads(r.content) for r in self.requests]

    def model(self):
        return model.chat_model(SETTINGS, token_provider=lambda: TOKEN, transport=httpx.MockTransport(self))


@tool
def get_price(ticker: str) -> str:
    """Latest close of a ticker."""
    return json.dumps({'ticker': ticker, 'close': 123.45})


MESSAGES = [SystemMessage(content='你是投研助手。'), HumanMessage(content='NVDA 现在多少钱？')]


# ---------------------------------------------------------------------------------------------- request body

def test_request_body_is_rewritten_for_the_direct_route(monkeypatch):
    monkeypatch.setenv('OPENAI_ORG_ID', 'org-from-env')
    monkeypatch.setenv('OPENAI_PROJECT_ID', 'proj-from-env')
    wire = Wire(text_events('你好，世界'))
    reply = wire.model().invoke(MESSAGES)
    (request,), (body,) = wire.requests, wire.bodies()
    assert str(request.url) == 'https://api.openai.com/v1/responses'
    assert request.headers['authorization'] == f'Bearer {TOKEN}'
    assert request.headers['accept'] == 'text/event-stream'
    assert 'openai-organization' not in request.headers and 'openai-project' not in request.headers
    assert body['model'] == 'gpt-test' and body['store'] is False and body['stream'] is True
    assert body['instructions'] == '你是投研助手。'
    assert all(item.get('role') != 'system' for item in body['input'])
    assert [item['role'] for item in body['input']] == ['user']
    assert set(body).isdisjoint(chatgpt.FORBIDDEN_FIELDS)
    assert reply.content == '你好，世界' and isinstance(reply.content, str)


def test_forbidden_fields_added_by_the_caller_are_removed():
    wire = Wire(text_events('ok'))
    bound = wire.model().bind(user='u', top_p=0.5, truncation='auto', max_output_tokens=50,
                              previous_response_id='resp_x', temperature=0.2)
    bound.invoke(MESSAGES)
    (body,) = wire.bodies()
    assert set(body).isdisjoint(chatgpt.FORBIDDEN_FIELDS)
    assert body['store'] is False and body['stream'] is True


def test_client_has_no_hidden_retry_and_the_documented_timeout():
    m = model.chat_model(SETTINGS, token_provider=lambda: TOKEN, transport=httpx.MockTransport(Wire()))
    assert m.max_retries == 0 and m.streaming is True and m.store is False
    assert m.output_version == 'v0' and m.temperature is None
    assert m.request_timeout == httpx.Timeout(180, connect=10)
    assert str(m.root_client.base_url).rstrip('/') == chatgpt.RESOURCE


def test_conftest_keeps_the_model_client_off_real_hosts():
    m = model.chat_model(SETTINGS, token_provider=lambda: TOKEN)          # no transport: the real client, blocked here
    with pytest.raises(pytest.fail.Exception):
        m.invoke(MESSAGES)


def test_chat_model_asks_for_a_token_every_time():
    calls = []
    for _ in range(2):
        model.chat_model(SETTINGS, token_provider=lambda: calls.append(1) or TOKEN)
    assert len(calls) == 2


# ---------------------------------------------------------------------------------------------- tool shapes

def _round_trip(shape, monkeypatch):
    monkeypatch.setenv('WORKBENCH_CHATGPT_TOOL_SHAPE', shape)
    namespace = 'workbench' if shape == 'namespace' else None
    wire = Wire(call_events(namespace), text_events('收盘价 123.45'))
    bound = wire.model().bind_tools([get_price])
    first = bound.invoke(MESSAGES)
    second = bound.invoke([*MESSAGES, first, ToolMessage(content='{"close": 123.45}', tool_call_id='call_1')])
    return wire, first, second


@pytest.mark.parametrize('shape', ['top_level', 'namespace', 'additional_tools'])
def test_tool_shapes(shape, monkeypatch):
    wire, first, second = _round_trip(shape, monkeypatch)
    one, two = wire.bodies()
    for body in (one, two):
        if shape == 'top_level':
            assert [t['type'] for t in body['tools']] == ['function'] and body['tools'][0]['name'] == 'get_price'
        elif shape == 'namespace':
            (ns,) = body['tools']
            assert ns['type'] == 'namespace' and ns['name'] == 'workbench'
            assert ns['description'] == '投研工作台的只读数据工具'
            assert [t['name'] for t in ns['tools']] == ['get_price']
        else:
            assert 'tools' not in body
            assert body['input'][0]['type'] == 'additional_tools' and body['input'][0]['role'] == 'developer'
            assert [t['name'] for t in body['input'][0]['tools']] == ['get_price']
        assert body['instructions'] == '你是投研助手。'
        assert all(item.get('role') != 'system' for item in body['input'])
    # the tool call comes back as a LangChain tool call, the final answer as a string
    assert first.tool_calls == [{'name': 'get_price', 'args': {'ticker': 'NVDA'}, 'id': 'call_1',
                                 'type': 'tool_call'}]
    assert first.content == '' and second.content == '收盘价 123.45'
    # the second request replays the call without a stored id (store is false) and then the tool's output
    replay = [item for item in two['input'] if item.get('type') == 'function_call']
    assert len(replay) == 1 and 'id' not in replay[0] and replay[0]['call_id'] == 'call_1'
    assert replay[0].get('namespace') == ('workbench' if shape == 'namespace' else None)
    assert two['input'][-1] == {'type': 'function_call_output', 'output': '{"close": 123.45}', 'call_id': 'call_1'}


def test_usage_metadata_comes_from_response_completed():
    wire = Wire(text_events('ok'))
    reply = wire.model().invoke(MESSAGES)
    assert reply.usage_metadata == {'input_tokens': 12, 'output_tokens': 7, 'total_tokens': 19,
                                    'input_token_details': {'cache_read': 3},
                                    'output_token_details': {'reasoning': 2}}


class _State(TypedDict):
    messages: Annotated[list, add_messages]


def test_two_round_tool_loop_through_toolnode(monkeypatch):
    """The subgraphs' shape: model -> ToolNode -> model, ``message.content`` read as a string at the end."""
    monkeypatch.setenv('WORKBENCH_CHATGPT_TOOL_SHAPE', 'namespace')
    wire = Wire(call_events('workbench'), text_events('NVDA 收盘价 123.45'))
    bound = wire.model().bind_tools([get_price])
    builder = StateGraph(_State)
    builder.add_node('model', lambda state: {'messages': [bound.invoke(state['messages'])]})
    builder.add_node('tools', ToolNode([get_price]))
    builder.add_edge(START, 'model')
    builder.add_conditional_edges('model', lambda s: 'tools' if s['messages'][-1].tool_calls else END)
    builder.add_edge('tools', 'model')
    final = builder.compile().invoke({'messages': list(MESSAGES)})
    last = final['messages'][-1]
    assert isinstance(last, AIMessage) and last.content == 'NVDA 收盘价 123.45'
    assert [type(m).__name__ for m in final['messages']] == ['SystemMessage', 'HumanMessage', 'AIMessage',
                                                            'ToolMessage', 'AIMessage']
    assert json.loads(final['messages'][3].content)['close'] == 123.45
    assert len(wire.requests) == 2


# ---------------------------------------------------------------------------------------------- failures

def test_failed_event_with_quota_code_is_quota_exhausted():
    wire = Wire(failed_events('subscription_sharing_usage_limit_exceeded'))
    with pytest.raises(ChatGPTError) as caught:
        wire.model().invoke(MESSAGES)
    assert caught.value.code == 'subscription_sharing_usage_limit_exceeded'
    reason, detail = model.classify_error(caught.value, TOKEN)
    assert reason == 'quota_exhausted' and 'subscription_sharing_usage_limit_exceeded' in detail
    assert 'sensitive server text' not in detail and 'sensitive server text' not in str(caught.value)


def test_incomplete_and_truncated_streams_are_model_errors():
    with pytest.raises(ChatGPTError) as incomplete:
        Wire(text_events('partial', last='response.incomplete')).model().invoke(MESSAGES)
    assert incomplete.value.code == 'response_incomplete'
    with pytest.raises(ChatGPTError) as cut:
        Wire(text_events('partial', last=None)).model().invoke(MESSAGES)
    assert cut.value.code == 'stream_interrupted'
    assert model.classify_error(incomplete.value, None)[0] == 'model_error'
    assert model.classify_error(cut.value, None)[0] == 'model_error'


def test_http_error_is_classified_from_the_openai_exception():
    wire = Wire(status=429, body={'error': {'code': 'subscription_sharing_usage_limit_exceeded',
                                            'message': 'quota text with test-access-1111111111'}})
    with pytest.raises(openai.RateLimitError) as caught:
        wire.model().invoke(MESSAGES)
    reason, detail = model.classify_error(caught.value, TOKEN)
    assert reason == 'quota_exhausted' and TOKEN not in detail
    assert len(wire.requests) == 1                              # max_retries=0: the attempt gate retries, not the client


# ---------------------------------------------------------------------------------------------- classify_error

def _http_error(status, body):
    response = httpx.Response(status, request=httpx.Request('POST', 'https://api.openai.com/v1/responses'))
    cls = {400: openai.BadRequestError, 401: openai.AuthenticationError, 404: openai.NotFoundError,
           422: openai.UnprocessableEntityError, 429: openai.RateLimitError}.get(status, openai.InternalServerError)
    return cls('Error code: %d - %s' % (status, body), response=response, body=body)


CLASSIFY_ROWS = [
    # (status, body code, expected reason)
    (429, 'subscription_sharing_usage_limit_exceeded', 'quota_exhausted'),
    (429, 'subscription_sharing_usage_unavailable', 'model_error'),
    (503, 'subscription_sharing_user_unavailable', 'model_error'),
    (404, 'model_not_found', 'model_unavailable'),
    (403, 'subscription_sharing_user_not_eligible', 'not_connected'),
    (403, 'subscription_sharing_invalid_user', 'not_connected'),
    (403, 'chatpass_v2_scope_not_authorized', 'not_connected'),
    (403, 'chatpass_v2_invalid_authorization_context', 'not_connected'),
    (401, 'invalid_token', 'not_connected'),
    (401, 'invalid_api_key', 'not_connected'),
    (401, None, 'not_connected'),
    (400, 'subscription_sharing_unsupported_capability', 'model_error'),
    (400, 'subscription_sharing_route_not_supported', 'model_error'),
    (400, 'invalid_request_error', 'model_error'),
    (422, None, 'model_error'),
    (429, 'rate_limit_exceeded', 'model_error'),
    (500, None, 'model_error'),
    (403, 'subscription_sharing_v2_user_not_eligible', 'not_connected'),
]


@pytest.mark.parametrize('status,code,reason', CLASSIFY_ROWS)
def test_classify_error_table(status, code, reason):
    body = {'error': {'code': code, 'message': 'x', 'param': 'tools[0]'}} if code else {'detail': 'text'}
    got_reason, detail = model.classify_error(_http_error(status, body), None)
    assert got_reason == reason
    assert len(detail) <= 300 and 'x' != detail
    if code:
        assert code in detail


def test_classify_error_param_is_kept_for_a_rejected_request():
    reason, detail = model.classify_error(
        _http_error(400, {'error': {'code': 'invalid_request_error', 'param': 'tools[0].name', 'message': 'echo: secret input'}}), None)
    assert reason == 'model_error' and 'param=tools[0].name' in detail and 'secret input' not in detail


@pytest.mark.parametrize('code', sorted(chatgpt.REAUTH_CODES))
def test_classify_error_reauth_codes_mean_not_connected(code):
    assert model.classify_error(ChatGPTError(code, '固定文案'), None)[0] == 'not_connected'


def test_classify_error_for_the_connection_layer_codes():
    assert model.classify_error(chatgpt.error_for('not_connected'), None)[0] == 'not_connected'
    for code in ('refresh_not_ready', 'network_error', 'invalid_token_response', 'migration_028_missing'):
        assert model.classify_error(ChatGPTError(code, '固定文案'), None)[0] == 'model_error', code
    # found along the cause chain too
    try:
        try:
            raise ChatGPTError('invalid_grant', '固定文案')
        except ChatGPTError as inner:
            raise RuntimeError('wrapper') from inner
    except RuntimeError as outer:
        assert model.classify_error(outer, None)[0] == 'not_connected'


def test_classify_error_for_plain_exceptions_is_model_error():
    reason, detail = model.classify_error(TimeoutError('read timed out'), None)
    assert reason == 'model_error' and detail == 'TimeoutError: read timed out'


def test_classify_error_redacts_bearer():
    jwt = 'eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.c2lnbmF0dXJl'
    exc = RuntimeError(f'upstream said Authorization: Bearer abc.def-123 and {jwt} and other-secret-x')
    reason, detail = model.classify_error(exc, 'other-secret-x')
    assert reason == 'model_error'
    for leaked in ('abc.def-123', jwt, 'other-secret-x'):
        assert leaked not in detail
    assert 'Bearer ***' in detail and len(detail) <= 300
    # a token this process handed out (chat_model registers it) is scrubbed even when the caller passes no secret
    model.chat_model(SETTINGS, token_provider=lambda: TOKEN)
    assert TOKEN not in model.classify_error(RuntimeError(f'boom {TOKEN}'), None)[1]
    assert TOKEN not in model.classify_error(ChatGPTError('api_error', f'text {TOKEN}'), None)[1]
