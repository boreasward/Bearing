"""The research second entrance (§6.3): guarded lifecycle, quota, FIFO drain, dossier versions."""
import json
import uuid
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta

import pytest
from _daily_db import daily_db as _daily_wired  # noqa: F401  (registers the fixture)
from _daily_db import job_reports  # noqa: F401  (registers the fixture)
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from investment_assistant.analysis import repo as analysis_repo
from investment_assistant.analysis import research_requests
from investment_assistant.analysis.agent import runner
from investment_assistant.core import locks as core_locks
from investment_assistant.core import tx as core_tx
from investment_assistant.core.constants import RESEARCH_DAILY_MAX
from investment_assistant.data import repo as data_repo

NOW = datetime(2026, 10, 30, 22, tzinfo=UTC)
SESSION = date(2026, 10, 30)


@pytest.fixture
def wired_db(request):
    return request.getfixturevalue('_daily_wired')


def context_monkey(db, monkeypatch):
    """Route the runner's pooled transactions at the disposable schema."""
    from contextlib import contextmanager

    @contextmanager
    def connection():
        yield db

    monkeypatch.setattr(core_tx, 'connection', connection)


def tool(name, args):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': name}])


RESEARCH_OK = json.dumps({'facts': [{'text': '财报披露营收增长', 'kind': 'disclosure', 'refs': ['call:1']}],
                          'risks': [], 'blocked': [], 'summary': '基本面已核验'}, ensure_ascii=False)
CONCLUSION_BUY = json.dumps({'action': 'buy', 'summary': '增长可期',
                             'catalysts': [{'text': '下季财报', 'refs': ['call:1']}],
                             'counter': [{'text': '客户集中', 'refs': ['call:1']}],
                             'price_scenarios': [{'text': '回落至支撑', 'refs': ['call:1']}],
                             'next_steps': [{'text': '跟踪指引', 'refs': ['call:1']}]}, ensure_ascii=False)


def make(request_id, **kwargs):
    now = kwargs.pop('now', NOW)
    return research_requests.create(ticker=kwargs.pop('ticker', 'AMD'), request_id=request_id, now=now, **kwargs)


def no_background(monkeypatch):
    """Keep tests single-threaded: no submit, no daily run."""
    monkeypatch.setattr(research_requests.core_runner, 'submit', lambda kind, fn, **kw: 'run-test')
    monkeypatch.setattr(core_locks, 'is_running', lambda: True)


# ---------------------------------------------------------------- create

def test_create_validates_and_replays(wired_db, monkeypatch):
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    made = make(str(uuid.uuid4()), question='为何增长？', horizon_days=30)
    assert made['status'] == 'queued' and made['ticker'] == 'AMD'
    replayed = research_requests.create(ticker='AMD', question='为何增长？', horizon_days=30,
                                        request_id=made['request_id'], now=NOW + timedelta(minutes=1))
    assert replayed['id'] == made['id'] and replayed['status'] == 'queued'          # D51 replay
    with pytest.raises(ValueError, match='invalid_ticker'):
        make(str(uuid.uuid4()), ticker='1NV4LID!')
    with pytest.raises(ValueError, match='invalid_question'):
        make(str(uuid.uuid4()), question='x' * 501)


def test_create_queues_when_daily_running(wired_db, monkeypatch):
    context_monkey(wired_db, monkeypatch)
    submitted = []
    monkeypatch.setattr(research_requests.core_runner, 'submit',
                        lambda kind, fn, **kw: submitted.append(kind) or 'run-1')
    monkeypatch.setattr(core_locks, 'is_running', lambda: True)      # D43: never waits, never submits
    made = make(str(uuid.uuid4()))
    assert made['status'] == 'queued' and submitted == []            # the agent node's drain picks it up
    monkeypatch.setattr(core_locks, 'is_running', lambda: False)
    made = make(str(uuid.uuid4()))
    assert made['status'] == 'queued' and submitted == ['research']  # idle: the background run starts at once


def test_quota_per_day(wired_db, monkeypatch):
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    monkeypatch.setattr(core_locks, 'is_running', lambda: True)
    for _ in range(RESEARCH_DAILY_MAX):
        make(str(uuid.uuid4()))
    with pytest.raises(research_requests.ResearchQuota):
        make(str(uuid.uuid4()))
    next_day = make(str(uuid.uuid4()), now=NOW + timedelta(days=2))
    assert next_day['status'] == 'queued'                           # the cap is per day


def test_create_gates(wired_db, monkeypatch):
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    monkeypatch.setattr(core_locks, 'is_running', lambda: True)
    wired_db.execute("INSERT INTO feature_flag_changes (flag, enabled_before, enabled_after, reason, request_id) "
                     "VALUES ('company_researcher', true, false, '停用验证', %s)", (str(uuid.uuid4()),))
    wired_db.commit()
    with pytest.raises(research_requests.FeatureDisabled):
        make(str(uuid.uuid4()))
    wired_db.execute('DROP TABLE feature_flag_changes CASCADE')     # pre-031: the entrance refuses, 503 at the API
    wired_db.commit()
    with pytest.raises(core_tx.Unavailable, match='migration_031_missing'):
        make(str(uuid.uuid4()))


# ---------------------------------------------------------------- run / versions

def _fake_collect(monkeypatch, rows=1):
    calls = []

    def traced(adapter, targets, **kwargs):
        calls.append((adapter.id, list(targets), kwargs['scope'], kwargs['trigger']))
        _fake_collect.features.append(kwargs['features'])
        from investment_assistant.data.fetch_policy import Fetched
        from investment_assistant.data.tally import Tally
        tally = Tally(adapter.id, 'workbench', targets)
        for target in targets:
            tally.record(target, Fetched([{}], [], 0), rows=rows, latest=SESSION)
        return tally

    monkeypatch.setattr('investment_assistant.data.registry.run_adapter', traced)
    return calls


_fake_collect.features = []


def test_collection_reads_the_current_flags_not_an_empty_dict(wired_db, monkeypatch):
    """R20: research collection follows the runtime switches (a flag without history is on). ``features={}`` would
    silently switch ``backup_sources`` off for every dossier collection."""
    from investment_assistant.core import features as core_features

    context_monkey(wired_db, monkeypatch)
    _fake_collect.features.clear()
    _fake_collect(monkeypatch)
    research_requests._collect('AMD', now=NOW, deps=core_tx.Deps())
    assert len(_fake_collect.features) == 7
    assert all(f == {'backup_sources': True, 'company_researcher': True, 'portfolio_manager': True,
                     'optimize_proposals': True} for f in _fake_collect.features)
    core_features.record_change(wired_db, flag='backup_sources', enabled=False, reason='测试', request_id=uuid.uuid4())
    wired_db.commit()
    _fake_collect.features.clear()
    research_requests._collect('AMD', now=NOW, deps=core_tx.Deps())
    assert all(f['backup_sources'] is False and f['portfolio_manager'] is True for f in _fake_collect.features)


def test_run_collects_then_runs_chain_and_writes_version(wired_db, monkeypatch, scripted_model):
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    made = make(str(uuid.uuid4()), question='增长是否可持续？', horizon_days=30)
    collected = _fake_collect(monkeypatch)
    model = scripted_model([tool('get_news', {'days': 30}), AIMessage(content=RESEARCH_OK),
                            tool('get_news', {'days': 7}), AIMessage(content=CONCLUSION_BUY)])
    out = research_requests.run(made['id'], now=NOW,
                                deps=core_tx.Deps(agent_model=model,
                                                  agent_checkpointer=lambda: nullcontext(InMemorySaver())))
    assert out['version']['status'] == 'concluded' and out['version']['action'] == 'buy'
    assert {family for family, _, scope, trigger in collected} == {
        'bars', 'news', 'sec_filings', 'earnings', 'fundamentals', 'sec_text', 'sector'}
    assert all(scope == 'research' and trigger == 'manual_research' for _, _, scope, trigger in collected)
    versions = analysis_repo.research_versions(wired_db, made['id'])
    assert len(versions) == 1 and versions[0]['agent_run_id'] and versions[0]['tokens'] > 0
    run = data_repo.agent_run_by_id(wired_db, versions[0]['agent_run_id'])
    assert run is not None
    assert run['mode'] == 'research' and run['agent_version'] == 'agent-4' and run['plan_id'] is None
    request = analysis_repo.research_request(wired_db, made['id'])
    assert request is not None and request['status'] == 'concluded'
    dossier = research_requests.latest_dossier(wired_db, 'AMD', cutoff_at=NOW)
    assert dossier is not None and dossier['id'] == versions[0]['id']


def test_waiting_data_when_blocked_and_wait(wired_db, monkeypatch, scripted_model):
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    made = make(str(uuid.uuid4()), question='指引缺多少？')
    _fake_collect(monkeypatch, rows=0)
    blocked = json.dumps({'facts': [{'text': '已披露增长', 'kind': 'disclosure', 'refs': ['call:1']}],
                          'risks': [], 'blocked': [{'what': '最新指引', 'why': '工具返回 not_found'}],
                          'summary': '指引缺失'}, ensure_ascii=False)
    wait = json.dumps({'action': 'wait', 'summary': '等下季指引', 'catalysts': [], 'counter': [],
                       'price_scenarios': [], 'next_steps': []}, ensure_ascii=False)
    model = scripted_model([tool('get_news', {'days': 30}), AIMessage(content=blocked),
                            AIMessage(content=wait)])
    out = research_requests.run(made['id'], now=NOW,
                                deps=core_tx.Deps(agent_model=model,
                                                  agent_checkpointer=lambda: nullcontext(InMemorySaver())))
    assert out['version']['status'] == 'waiting_data' and out['version']['action'] == 'wait'
    request = analysis_repo.research_request(wired_db, made['id'])
    assert request is not None and request['status'] == 'waiting_data'        # queued again by the drain, never re-run without new data


def test_three_failures_close(wired_db, monkeypatch, scripted_model):
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    made = make(str(uuid.uuid4()))
    _fake_collect(monkeypatch, rows=0)
    bad = scripted_model([])                           # every model call fails: three error versions close the file
    for attempt in range(3):
        out = research_requests.run(made['id'], now=NOW + timedelta(minutes=attempt),
                                    deps=core_tx.Deps(agent_model=bad,
                                                      agent_checkpointer=lambda: nullcontext(InMemorySaver())))
        assert out['version']['status'] == 'error'
    request = analysis_repo.research_request(wired_db, made['id'])
    assert request is not None
    assert request['status'] == 'closed' and request['closed_reason'] == 'max_attempts'
    assert request['attempts'] == 3
    with pytest.raises(LookupError):
        research_requests.run(made['id'], now=NOW, deps=core_tx.Deps())


def _agent_node(budget_left_s, summary):
    """The real ``agent`` node on an empty day: no units, so only the tail runs, with ``budget_left_s`` of the
    shared AGENT_DEADLINE_S left (the legacy shell's ``window_start`` fixes when the window opened)."""
    from investment_assistant.core.llm.model import AgentSettings
    from investment_assistant.core.constants import AGENT_DEADLINE_S
    from investment_assistant.workflow.context import NodeContext
    from investment_assistant.workflow.nodes import agent as agent_node

    deps = core_tx.Deps(clock=lambda: 10_000.0, agent_settings=AgentSettings(
        provider='chatgpt', model='gpt-test', api_key='sk-test', daily_tokens=None, config_error=None))
    ctx = NodeContext(recorder=None, deps=deps, clock=deps.clock, wall_now=None, notify_cfg=None, legacy=True,
                      window_start=10_000.0 - (AGENT_DEADLINE_S - budget_left_s))
    state = {'cutoff_at': NOW.isoformat(), 'session': SESSION.isoformat(), 'run_id': 'daily-r1',
             'summary': summary, 'features': {}}
    return agent_node.run(state, ctx, None)


def test_agent_node_drains_queue_within_budget(wired_db, monkeypatch, scripted_model):
    no_background(monkeypatch)
    """§10/D26: after the units the agent node drains queued+waiting dossiers FIFO inside the remaining budget."""
    context_monkey(wired_db, monkeypatch)
    first = make(str(uuid.uuid4()), question='第一只')
    second = make(str(uuid.uuid4()), question='第二只')
    _fake_collect(monkeypatch, rows=1)
    ran = []

    def fake_run(request_id, *, now, deps):
        row = analysis_repo.research_request(wired_db, request_id)
        assert row is not None
        ran.append(row['ticker'])
        for before, after in (('queued', 'running'), ('running', 'concluded')):     # the real state machine
            analysis_repo.update_research_status(wired_db, request_id, from_status=before, to_status=after)
        wired_db.commit()
        return {'id': request_id, 'status': 'concluded', 'version': {'status': 'concluded'}}

    monkeypatch.setattr(research_requests, 'run', fake_run)
    summary = {'agent': {'runs': {}}, 'briefs': {}, 'errors': [], 'sources': {}, 'timings': {}}
    # no budget left: the queue waits (the frozen ``features`` flag is on, the queue is not empty)
    assert _agent_node(0, summary)['summary']['agent']['research_requests'] == 0 and ran == []
    out = _agent_node(900, summary)
    assert ran == [first['ticker'], second['ticker']] and out['summary']['agent']['research_requests'] == 2
    assert summary['errors'] == []
    assert _agent_node(900, summary)['summary']['agent']['research_requests'] == 0      # drained: nothing left


# ---------------------------------------------------------------- read side

def test_list_and_detail(wired_db, monkeypatch):
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    monkeypatch.setattr(core_locks, 'is_running', lambda: True)
    made = make(str(uuid.uuid4()), question='为何增长？', horizon_days=30)
    items = research_requests.list_requests(now=NOW)
    assert items and items[0]['id'] == made['id']
    assert items[0]['ticker'] == 'AMD' and items[0]['status'] == 'queued'
    detail = research_requests.detail(made['id'], now=NOW)
    assert detail is not None
    assert detail['request']['id'] == made['id'] and detail['versions'] == [] and detail['chain'] is None
    assert research_requests.detail(str(uuid.uuid4()), now=NOW) is None
