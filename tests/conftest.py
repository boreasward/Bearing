import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from investment_assistant.api.app import create_app


@pytest.fixture(autouse=True)
def isolate_unit_tests_from_local_database(monkeypatch):
    """Individual DB tests opt in explicitly; a local .env must not make mocks use production."""
    monkeypatch.delenv('INVESTMENT_ASSISTANT_DATABASE_URL', raising=False)
    monkeypatch.setenv('SCHEDULER_ENABLED', '0')
    # A test-triggered load_dotenv() can still import keys from .env mid-session
    # (e.g. FRED_API_KEY); the FRED key and the agent switches must never reach the test process.
    monkeypatch.delenv('FRED_API_KEY', raising=False)
    monkeypatch.delenv('WORKBENCH_AGENT_PROVIDER', raising=False)
    monkeypatch.delenv('WORKBENCH_CHATGPT_CALLBACK_PORT', raising=False)
    for name in ('TOOL_SHAPE', 'EXTRA_FORBIDDEN', 'OVERRIDES', 'INSTRUCTIONS_FROM_SYSTEM'):
        monkeypatch.delenv(f'WORKBENCH_CHATGPT_{name}', raising=False)
    # No test may read (or refresh) a real ChatGPT connection: the default reader says "not connected". Tests that
    # need a connected agent pass settings / read_connection explicitly.
    from investment_assistant.workbench import agent_connection
    monkeypatch.setattr(agent_connection, 'read_for_settings',
                        lambda: {'status': 'disconnected', 'scopes_ok': False, 'model': None, 'secret': None})
    # ... and the ChatGPT HTTP layer can only reach the loopback (fake authorization servers), never a real host.
    from urllib.parse import urlsplit

    import httpx

    from investment_assistant.workbench.agent import chatgpt, model as agent_model

    real_http, real_client = chatgpt._http, agent_model._http_client

    def loopback_only_http(method, url, *args, **kwargs):
        if urlsplit(url).hostname not in ('127.0.0.1', 'localhost', '::1'):
            pytest.fail(f'a test tried to reach a real host through chatgpt._http: {urlsplit(url).hostname}')
        return real_http(method, url, *args, **kwargs)

    class NoRealHost(httpx.BaseTransport):
        def handle_request(self, request):
            pytest.fail(f'a test tried to reach a real host through the model client: {request.url.host}')

    monkeypatch.setattr(chatgpt, '_http', loopback_only_http)
    monkeypatch.setattr(agent_model, '_http_client',
                        lambda transport: real_client(transport if transport is not None else NoRealHost()))


@pytest.fixture()
def client():
    return TestClient(create_app())


WORKBENCH_MIGRATIONS = ('002_watchlist_items.sql', '006_job_reports.sql', '007_scheduled_jobs.sql',
                        '010_job_reports_target_dates.sql', '011_filings.sql',
                        '016_us_workbench.sql', '017_trade_plans.sql',
                        '018_pool_and_market_context.sql', '019_agent_track.sql', '020_review_expansion.sql',
                        '023_screen_and_beijing_jobs.sql', '024_data_fetch_runs.sql', '025_stage_runs.sql',
                        '026_plan_timeline.sql', '027_growth_loop.sql', '028_daily_workflow.sql',
                        '029_executable_plans.sql', '030_accounts.sql')


@pytest.fixture
def workbench_db():
    """Workbench schema in a throwaway Postgres schema, dropped afterwards; explicit test URL only."""
    url = os.environ.get('INVESTMENT_ASSISTANT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('explicit test database URL required; disposable schema only')
    import psycopg
    schema = f'wb_test_{uuid.uuid4().hex[:12]}'
    with psycopg.connect(url) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
        conn.execute(f'SET search_path TO {schema}')
        for name in WORKBENCH_MIGRATIONS:
            conn.execute(Path('migrations', name).read_text(encoding='utf-8'))
        conn.commit()
        try:
            yield conn
        finally:
            conn.rollback()
            conn.execute(f'DROP SCHEMA {schema} CASCADE')
            conn.commit()


@pytest.fixture
def scripted_model():
    """Factory for a fake chat model: bind_tools returns itself, replies come in
    order, every invocation's messages are recorded on .seen, each reply carries
    usage_metadata, and running out of replies raises AssertionError."""
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class ScriptedModel(BaseChatModel):
        responses: list = []
        seen: list = []
        index: int = 0

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.seen.append(list(messages))
            if self.index >= len(self.responses):
                raise AssertionError('scripted model ran out of responses')
            message = self.responses[self.index]
            self.index += 1
            return ChatResult(generations=[ChatGeneration(message=message)])

        @property
        def _llm_type(self):
            return 'scripted-test'

    def make(responses):
        for message in responses:
            if not message.usage_metadata:
                message.usage_metadata = {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}
        return ScriptedModel(responses=list(responses))

    return make
