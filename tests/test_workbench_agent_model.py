"""Agent model wiring: settings from the environment and the stored connection, availability, no old-provider symbols.

The ChatGPT request / stream / error behaviour is in ``test_agent_responses_model``; the connection itself in
``test_agent_connection``. No network, no database."""
import investment_assistant.analysis.agent.runner as analysis_agent_runner
import dataclasses
import re
from pathlib import Path

import httpx
import pytest

from investment_assistant.core.llm import chatgpt, model

CONNECTED = {'status': 'connected', 'scopes_ok': True, 'model': 'gpt-test', 'secret': 'test-access-9999'}


def reader(**overrides):
    return lambda: {**CONNECTED, **overrides}


def settings(env=None, **overrides):
    return model.settings_from_env(env or {}, read_connection=reader(**overrides))


def test_provider_values():
    assert settings().provider == 'chatgpt' and settings().config_error is None             # the default
    assert settings({'WORKBENCH_AGENT_PROVIDER': 'chatgpt'}).config_error is None
    off = settings({'WORKBENCH_AGENT_PROVIDER': 'off'})
    assert off.provider == 'off' and off.config_error is None and model.unavailable_reason(off) == 'disabled'
    for other in ('openai', 'ChatGPT', 'on', 'anthropic'):
        s = settings({'WORKBENCH_AGENT_PROVIDER': other})
        assert s.config_error == 'WORKBENCH_AGENT_PROVIDER 只能是 chatgpt 或 off'
        assert model.unavailable_reason(s) == 'not_configured'
    assert settings({'WORKBENCH_AGENT_PROVIDER': ''}).provider == 'chatgpt'                  # empty = unset


def test_settings_read_the_connection_not_the_environment():
    s = settings({'WORKBENCH_AGENT_MODEL': 'ignored', 'WORKBENCH_AGENT_BASE_URL': 'https://ignored.test'})
    assert (s.provider, s.model, s.connection, s.api_key) == ('chatgpt', 'gpt-test', 'connected', 'test-access-9999')
    assert not hasattr(s, 'base_url')
    assert 'test-access-9999' not in repr(s)                                                  # the token never prints
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.model = 'other'
    assert [f.name for f in dataclasses.fields(model.AgentSettings)] == [
        'provider', 'model', 'api_key', 'daily_tokens', 'config_error', 'connection']


def test_connection_states_map_to_settings():
    for status in ('disconnected', 'needs_login', 'pending'):
        assert settings(status=status, secret=None).connection == status
    assert settings(scopes_ok=False).connection == 'no_sharing'
    assert settings(status='needs_login', scopes_ok=False).connection == 'needs_login'
    no_model = settings(model=None)
    assert no_model.model == '' and no_model.connection == 'connected'          # '': agent_runs.model is NOT NULL


def test_unavailable_reasons():
    assert model.unavailable_reason(settings()) is None
    assert model.unavailable_reason(settings({'WORKBENCH_AGENT_PROVIDER': 'off'})) == 'disabled'
    assert model.unavailable_reason(settings({'WORKBENCH_AGENT_PROVIDER': 'bogus'})) == 'not_configured'
    assert model.unavailable_reason(settings({'WORKBENCH_AGENT_DAILY_TOKENS': '0'})) == 'not_configured'
    assert model.unavailable_reason(settings(scopes_ok=False)) == 'not_configured'          # no plan-usage sharing
    for status in ('disconnected', 'needs_login', 'pending'):
        assert model.unavailable_reason(settings(status=status)) == 'not_connected'
    assert model.unavailable_reason(settings(model=None)) == 'model_unavailable'
    assert model.unavailable_reason(settings(model='')) == 'model_unavailable'
    # the order: disabled wins over everything, a configuration error over the connection
    off_and_down = settings({'WORKBENCH_AGENT_PROVIDER': 'off'}, status='disconnected')
    assert model.unavailable_reason(off_and_down) == 'disabled'
    # hand-built settings (as the callers' tests do) work too
    assert model.unavailable_reason(model.AgentSettings('chatgpt', None, None, None, None, 'connected')) == 'model_unavailable'
    assert model.unavailable_reason(model.AgentSettings('chatgpt', 'gpt-test', None, None, None)) is None


def test_unreadable_connection_never_raises():
    def broken():
        raise RuntimeError('boom with postgresql://user:password@host/db')
    s = model.settings_from_env({}, read_connection=broken)
    assert s.connection == 'unknown' and s.config_error == '连接状态不可读' and s.model == ''
    assert 'password' not in repr(s) and model.unavailable_reason(s) == 'not_configured'
    # an earlier configuration error is not replaced
    s = model.settings_from_env({'WORKBENCH_AGENT_DAILY_TOKENS': 'x'}, read_connection=broken)
    assert s.config_error == 'WORKBENCH_AGENT_DAILY_TOKENS 必须是正整数'


def test_disabled_agent_does_not_read_the_connection():
    def never():
        raise AssertionError('a disabled agent must not touch the connection')
    s = model.settings_from_env({'WORKBENCH_AGENT_PROVIDER': 'off'}, read_connection=never)
    assert s.provider == 'off' and s.config_error is None


def test_default_reader_is_the_stored_connection_and_tests_get_a_fake_one():
    # conftest swaps agent_connection.read_for_settings for "not connected": no test reads a real connection
    s = model.settings_from_env({})
    assert s.connection == 'disconnected' and model.unavailable_reason(s) == 'not_connected'


def test_daily_tokens_parse():
    s = settings({'WORKBENCH_AGENT_DAILY_TOKENS': '500000'})
    assert s.daily_tokens == 500000 and model.unavailable_reason(s) is None
    for bad in ('0', 'abc', '-5'):
        s = settings({'WORKBENCH_AGENT_DAILY_TOKENS': bad})
        assert s.config_error == 'WORKBENCH_AGENT_DAILY_TOKENS 必须是正整数'
        assert s.daily_tokens is None and model.unavailable_reason(s) == 'not_configured'


def test_chat_model_uses_the_selected_model_and_a_fresh_token():
    s = settings()
    m = model.chat_model(s, token_provider=lambda: 'test-access-1234567')
    assert m.model_name == 'gpt-test' and m.openai_api_key.get_secret_value() == 'test-access-1234567'
    assert str(m.openai_api_base) == chatgpt.RESOURCE
    seen = []
    model.chat_model(s, token_provider=lambda: seen.append(1) or 'test-access-1234567', transport=httpx.MockTransport(
        lambda request: httpx.Response(200)))
    assert seen == [1]


def test_no_old_provider_symbols():
    source = Path(model.__file__).read_text(encoding='utf-8').lower()
    # the old provider's names are assembled here so this file itself stays free of them
    for word in ('zhi' + 'pu', 'big' + 'model', 'gl' + 'm', '11' + '13'):
        assert word not in source, word
    for gone in ('DEFAULT_MODEL', 'DEFAULT_BASE_URL', 'TEMPERATURE', 'REQUEST_TIMEOUT_S', 'CLIENT_RETRIES'):
        assert not hasattr(model, gone), gone
    assert model.QUOTA_CODES == {'subscription_sharing_usage_limit_exceeded'}
    names = set(re.findall(r"env\.get\('([A-Z_]+)'", Path(model.__file__).read_text(encoding='utf-8')))
    assert names == {'WORKBENCH_AGENT_DAILY_TOKENS', 'WORKBENCH_AGENT_PROVIDER'}        # the only variables it reads


def test_cli_dispatch(monkeypatch, capsys):
    import json as _json
    import uuid as _uuid
    from datetime import UTC, date, datetime
    from investment_assistant.analysis.agent import __main__ as cli
    import investment_assistant.core.tx as service

    monkeypatch.setenv('INVESTMENT_ASSISTANT_DATABASE_URL', 'postgresql://agent.test/db')
    seen = {}
    monkeypatch.setattr(model, 'setup_checkpoints', lambda url: seen.setdefault('setup', url))
    assert cli.main(['setup']) == 0
    assert seen['setup'] == 'postgresql://agent.test/db'
    assert 'checkpoint tables ready' in capsys.readouterr().out

    def fake_run_agent(ticker, signal_date, *, now, deps=None):
        seen['run'] = (ticker, signal_date, isinstance(now, datetime))
        return {'id': _uuid.uuid4(), 'status': 'ok', 'reason': None}

    monkeypatch.setattr(analysis_agent_runner, 'run_agent', fake_run_agent)
    assert cli.main(['run', '--ticker', 'nvda', '--date', '2026-10-21']) == 0
    assert seen['run'] == ('NVDA', date(2026, 10, 21), True)
    out = _json.loads(capsys.readouterr().out.strip())
    assert out['status'] == 'ok' and out['reason'] is None and out['id']
