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
                             'price_scenarios': [{'text': '回落至支撑', 'refs': ['call:1']}],
                             'next_steps': [{'text': '跟踪指引', 'refs': ['call:1']}]}, ensure_ascii=False)
COUNTER_OK = json.dumps({'counter': [{'text': '客户集中', 'refs': ['call:1']}]}, ensure_ascii=False)


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
                            tool('get_news', {'days': 7}), AIMessage(content=CONCLUSION_BUY),
                            AIMessage(content=COUNTER_OK)])
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
    wait = json.dumps({'action': 'wait', 'summary': '等下季指引', 'catalysts': [],
                       'price_scenarios': [], 'next_steps': []}, ensure_ascii=False)
    model = scripted_model([tool('get_news', {'days': 30}), AIMessage(content=blocked),
                            AIMessage(content=wait), AIMessage(content='{"counter": []}')])
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


def _agent_node(budget_left_s, summary, features=None):
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
             'summary': summary, 'features': {} if features is None else features}
    return agent_node.run(state, ctx, None)


def test_agent_node_drains_queue_within_budget(wired_db, monkeypatch, scripted_model):
    no_background(monkeypatch)
    """§10/D26: after the units the agent node drains queued+waiting dossiers FIFO inside the remaining budget."""
    context_monkey(wired_db, monkeypatch)
    first = make(str(uuid.uuid4()), question='第一只')
    second = make(str(uuid.uuid4()), question='第二只')
    _fake_collect(monkeypatch, rows=1)
    ran = []

    def fake_run(request_id, *, now, deps, **kwargs):
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


# ---------------------------------------------------------------- D28: 30 natural days without a new version

def _aged(db, *, created_days=0, version_days=None, status='queued', reason=None, attempts=0):
    """Insert a request ``created_days`` before NOW (and optionally one version ``version_days`` before NOW)."""
    created = NOW - timedelta(days=created_days)
    row = analysis_repo.insert_research_request(db, {
        'id': str(uuid.uuid4()), 'ticker': 'AMD', 'question': None, 'horizon_days': None, 'source': 'manual',
        'request_id': str(uuid.uuid4()), 'created_at': created})
    if version_days is not None:
        stamp = NOW - timedelta(days=version_days)
        analysis_repo.insert_research_version(db, {
            'request_id': row['id'], 'version_no': 1, 'cutoff_at': stamp, 'status': 'concluded', 'action': 'wait',
            'summary': '旧结论', 'created_at': stamp})
    path = {'queued': [], 'waiting_data': ['running', 'waiting_data'], 'concluded': ['running', 'concluded'],
            'closed': ['closed']}[status]
    before = 'queued'
    for step in path:
        fields = {'closed_reason': reason, 'attempts': attempts} if step == 'closed' else {}
        assert analysis_repo.update_research_status(db, row['id'], from_status=before, to_status=step, **fields)
        before = step
    db.commit()
    return row['id']


def _status(db, request_id):
    row = analysis_repo.research_request(db, request_id)
    assert row is not None
    return row['status'], row['closed_reason']


def test_close_stale_boundary_is_thirty_natural_days(wired_db, monkeypatch):
    context_monkey(wired_db, monkeypatch)
    at_30 = _aged(wired_db, created_days=90, version_days=30, status='concluded')
    at_31 = _aged(wired_db, created_days=90, version_days=31, status='concluded')
    never_30 = _aged(wired_db, created_days=30)                      # no version: the creation counts
    never_31 = _aged(wired_db, created_days=31)
    waiting = _aged(wired_db, created_days=90, version_days=45, status='waiting_data')
    recent = _aged(wired_db, created_days=90, version_days=2, status='concluded')   # a new version keeps it open
    assert research_requests.close_stale(now=NOW) == 3
    assert _status(wired_db, at_30)[0] == 'concluded' and _status(wired_db, never_30)[0] == 'queued'
    assert _status(wired_db, recent)[0] == 'concluded'
    for closed in (at_31, never_31, waiting):
        assert _status(wired_db, closed) == ('closed', research_requests.AUTO_CLOSE_REASON)
    assert research_requests.AUTO_CLOSE_REASON not in ('max_attempts', '')
    assert research_requests.close_stale(now=NOW) == 0                  # settled once: a second pass finds nothing


def test_close_stale_leaves_manual_max_attempts_and_running_alone(wired_db, monkeypatch):
    context_monkey(wired_db, monkeypatch)
    manual = _aged(wired_db, created_days=100, version_days=60, status='closed', reason='不需要了')
    capped = _aged(wired_db, created_days=100, version_days=60, status='closed', reason='max_attempts', attempts=3)
    running = _aged(wired_db, created_days=100)
    for before, after in (('queued', 'running'),):
        analysis_repo.update_research_status(wired_db, running, from_status=before, to_status=after)
    wired_db.commit()
    assert research_requests.close_stale(now=NOW) == 0
    assert _status(wired_db, manual) == ('closed', '不需要了')
    assert _status(wired_db, capped) == ('closed', 'max_attempts')
    assert _status(wired_db, running)[0] == 'running'                    # never close under an in-flight attempt
    assert analysis_repo.research_request(wired_db, capped)['attempts'] == 3


def test_agent_node_closes_stale_before_draining_and_counts_it(wired_db, monkeypatch):
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    stale = _aged(wired_db, created_days=40)
    fresh = _aged(wired_db, created_days=1)
    ran = []

    def fake_run(request_id, *, now, deps, **kwargs):
        ran.append(request_id)
        return {'id': request_id, 'status': 'concluded', 'version': {'status': 'concluded'}}

    monkeypatch.setattr(research_requests, 'run', fake_run)
    summary = {'agent': {'runs': {}}, 'briefs': {}, 'errors': [], 'sources': {}, 'timings': {}}
    out = _agent_node(900, summary)
    assert out['summary']['agent']['research_auto_closed'] == 1
    assert ran == [fresh] and _status(wired_db, stale) == ('closed', research_requests.AUTO_CLOSE_REASON)
    assert summary['errors'] == []


def test_auto_close_is_housekeeping_and_ignores_the_researcher_flag(wired_db, monkeypatch):
    """Spec D28 is silent on the flag; closing a stale file is not a dossier run, so it happens with the flag off
    (and the frozen value is irrelevant), while the drain itself stays off."""
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    stale = _aged(wired_db, created_days=40)
    fresh = _aged(wired_db, created_days=1)
    ran = []
    monkeypatch.setattr(research_requests, 'run', lambda request_id, **kw: ran.append(request_id))
    summary = {'agent': {'runs': {}}, 'briefs': {}, 'errors': [], 'sources': {}, 'timings': {}}
    out = _agent_node(900, summary, features={'company_researcher': False})
    assert out['summary']['agent']['research_auto_closed'] == 1 and out['summary']['agent']['research_requests'] == 0
    assert ran == [] and _status(wired_db, stale)[0] == 'closed' and _status(wired_db, fresh)[0] == 'queued'


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


# ---------------------------------------------------------------- fix round 1: counter stage, §6.3 ⑦, budgets, wedge

def _deps(model, **kwargs):
    return core_tx.Deps(agent_model=model, agent_checkpointer=lambda: nullcontext(InMemorySaver()), **kwargs)


def _settings(daily_tokens=None):
    from investment_assistant.core.llm.model import AgentSettings

    return AgentSettings(provider='chatgpt', model='gpt-test', api_key='sk-test', daily_tokens=daily_tokens,
                         config_error=None)


def test_chain_has_an_independent_counter_phase_and_price_tools_for_the_writer(wired_db, monkeypatch, scripted_model):
    """R35 / §6.3 ④: researcher + proposer + counter + check. The writer holds the propose toolset (price tools for
    ``price_scenarios``), the counter is its own model phase and fills ``output.counter``; the writer never writes it."""
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    made = make(str(uuid.uuid4()), question='增长是否可持续？')
    _fake_collect(monkeypatch)
    monkeypatch.setattr(data_repo, 'read_bars', lambda conn, symbol, session, **kw: [
        {'price_date': SESSION, 'open': 100, 'high': 101, 'low': 99, 'close': 100, 'adj_close': 100, 'volume': 5}])
    scenarios = json.dumps({'action': 'buy', 'summary': '增长可期', 'catalysts': [{'text': '下季财报', 'refs': ['call:1']}],
                            'price_scenarios': [{'text': '回落至 95', 'refs': ['call:2']}],
                            'next_steps': []}, ensure_ascii=False)
    model = scripted_model([tool('get_news', {'days': 30}), AIMessage(content=RESEARCH_OK),
                            tool('get_price_bars', {'ticker': 'AMD'}), AIMessage(content=scenarios),
                            tool('get_news', {'days': 7}), AIMessage(content=COUNTER_OK)])
    out = research_requests.run(made['id'], now=NOW, deps=_deps(model))
    assert out['version']['status'] == 'concluded'
    run = data_repo.agent_run_by_id(wired_db, out['agent_run_id'])
    assert run is not None and run['mode'] == 'research'
    calls = analysis_repo.agent_tool_calls(wired_db, out['agent_run_id'])
    assert [(c['seq'], c['phase'], c['tool']) for c in calls] == [
        (1, 'research', 'get_news'), (2, 'propose', 'get_price_bars'), (3, 'counter', 'get_news')]
    assert 'error' not in calls[1]['result']              # the writer's price tool answered: price_scenarios has evidence
    output = run['output']
    assert output['price_scenarios'] == [{'text': '回落至 95', 'refs': ['call:2']}]
    assert output['counter'] == [{'text': '客户集中', 'refs': ['call:1']}] and output['counter_status'] == 'ok'
    # independence: the counter's prompt is its own and it never saw the writer's chat history
    counter_seen = model.seen[-2]                     # the counter's first call (the last holds its tool round)
    assert '反方审查员' in str(counter_seen[0].content) and len(counter_seen) == 2
    assert '增长可期' in str(counter_seen[1].content) and '基本面已核验' in str(counter_seen[1].content)
    assert 'get_price_bars' not in ''.join(str(m.content) for m in counter_seen)
    # the dossier version carries the same independent field (UI1)
    version = analysis_repo.research_versions(wired_db, made['id'])[0]
    assert version['output']['counter'] == output['counter']
    # a writer that writes its own counter is malformed (its reply is checked before the counter phase)
    made = make(str(uuid.uuid4()))
    model = scripted_model([AIMessage(content=RESEARCH_OK.replace('call:1', 'call:1')),
                            AIMessage(content=json.dumps({**json.loads(CONCLUSION_BUY), 'counter': []}))])
    bad = research_requests.run(made['id'], now=NOW, deps=_deps(model))
    assert bad['version']['status'] == 'error' and 'conclusion_malformed' in bad['version']['summary']


def test_counter_failures_keep_the_conclusion(wired_db, monkeypatch, scripted_model):
    """A malformed counter reply / entries citing nothing real are counted, never fatal; the dossier stays concluded."""
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    _fake_collect(monkeypatch)
    made = make(str(uuid.uuid4()))
    model = scripted_model([tool('get_news', {'days': 30}), AIMessage(content=RESEARCH_OK),
                            AIMessage(content=CONCLUSION_BUY), AIMessage(content='不是 JSON')])
    out = research_requests.run(made['id'], now=NOW, deps=_deps(model))
    assert out['version']['status'] == 'concluded'
    assert out['version']['output']['counter'] == [] and out['version']['output']['counter_status'] == 'malformed'
    made = make(str(uuid.uuid4()))
    mixed = json.dumps({'counter': [{'text': '虚构', 'refs': ['call:99']}, {'text': '真实', 'refs': ['call:1']}]},
                       ensure_ascii=False)
    model = scripted_model([tool('get_news', {'days': 30}), AIMessage(content=RESEARCH_OK),
                            AIMessage(content=CONCLUSION_BUY), AIMessage(content=mixed)])
    out = research_requests.run(made['id'], now=NOW, deps=_deps(model))
    assert out['version']['output']['counter'] == [{'text': '真实', 'refs': ['call:1']}]


def test_request_data_is_settled_synchronously_and_reruns_the_chain_once(wired_db, monkeypatch, scripted_model):
    """§6.3 ⑦: the first version's ``request_data`` rows carry ``research:<request id>`` (never NULL), are fetched
    in-entry, a second version runs on the new rows (at most one), the rerun pointer lands on it, and what the second
    version queues settles ``deferred`` — nothing stays ``queued``."""
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    made = make(str(uuid.uuid4()), question='缺哪些基本面？')
    calls = _fake_collect(monkeypatch, rows=2)
    ask = lambda family: tool('request_data', {'family': family, 'ticker': 'AMD'})       # noqa: E731
    model = scripted_model([
        ask('fundamentals'), AIMessage(content=RESEARCH_OK), AIMessage(content=CONCLUSION_BUY),
        AIMessage(content=COUNTER_OK),
        ask('sec_text'), AIMessage(content=RESEARCH_OK), AIMessage(content=CONCLUSION_BUY),
        AIMessage(content=COUNTER_OK)])
    out = research_requests.run(made['id'], now=NOW, deps=_deps(model))
    versions = analysis_repo.research_versions(wired_db, made['id'])
    assert [v['version_no'] for v in versions] == [1, 2] and out['version']['version_no'] == 2
    assert out['status'] == 'concluded' and str(out['agent_run_id']) == str(versions[1]['agent_run_id'])
    rows = wired_db.execute('SELECT family, run_id, agent_run_id, status, rows, reason, rerun_run_id '
                            'FROM data_requests ORDER BY id').fetchall()
    assert [(r[0], r[1], r[3]) for r in rows] == [('fundamentals', f"research:{made['id']}", 'fetched'),
                                                   ('sec_text', f"research:{made['id']}", 'deferred')]
    assert str(rows[0][2]) == str(versions[0]['agent_run_id']) and rows[0][4] == 2
    assert str(rows[0][6]) == str(versions[1]['agent_run_id'])             # the second version is the rerun
    assert rows[1][5] == 'research_rerun_limit' and rows[1][6] is None      # at most one rerun
    assert ('fundamentals', ['AMD'], 'data_request', 'data_request') in [c for c in calls]
    assert wired_db.execute("SELECT count(*) FROM data_requests WHERE status = 'queued'").fetchone()[0] == 0


def test_request_data_without_new_rows_does_not_rerun(wired_db, monkeypatch, scripted_model):
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    made = make(str(uuid.uuid4()))
    _fake_collect(monkeypatch, rows=0)
    model = scripted_model([tool('request_data', {'family': 'fundamentals', 'ticker': 'AMD'}),
                            AIMessage(content=RESEARCH_OK), AIMessage(content=CONCLUSION_BUY),
                            AIMessage(content=COUNTER_OK)])
    out = research_requests.run(made['id'], now=NOW, deps=_deps(model))
    assert out['version']['version_no'] == 1 and out['status'] == 'concluded'
    row = wired_db.execute('SELECT status, rows, rerun_run_id FROM data_requests').fetchone()
    assert row == ('fetched', 0, None)


def test_research_run_obeys_its_900_second_deadline(wired_db, monkeypatch, scripted_model):
    """R36 / §6.3 预算: no model phase starts after RESEARCH_DEADLINE_S; the attempt is an error version
    (``stage_budget``) like every failure class, with no model call."""
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    made = make(str(uuid.uuid4()))
    _fake_collect(monkeypatch)
    ticks = iter([0.0] * 8 + [901.0] * 50)          # the start and the seven collection checks, then the model gate
    model = scripted_model([])                       # a model call would raise: the gate must stop first
    out = research_requests.run(made['id'], now=NOW, deps=_deps(model, clock=lambda: next(ticks)))
    assert out['version']['status'] == 'error' and 'stage_budget' in out['version']['summary']
    run = data_repo.agent_run_by_id(wired_db, out['version']['agent_run_id'])
    assert run is not None and run['status'] == 'error' and run['reason'] == 'stage_budget'
    request = analysis_repo.research_request(wired_db, made['id'])
    assert request is not None and request['status'] == 'queued' and request['attempts'] == 1


def test_research_run_checks_the_daily_token_cap_before_the_model(wired_db, monkeypatch, scripted_model):
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    made = make(str(uuid.uuid4()))
    _fake_collect(monkeypatch)
    spent = {'id': str(uuid.uuid4()), 'ticker': 'AMD', 'signal_date': SESSION, 'pool': 'main', 'attempt': 1,
             'mode': 'propose', 'agent_version': 'agent-4', 'provider': 'chatgpt', 'model': 'm', 'cutoff_at': NOW,
             'status': 'rejected', 'reason': 'invalid_citation', 'detail': None, 'output': None, 'counter': None,
             'plan_id': None, 'input_tokens': 900, 'output_tokens': 200, 'started_at': NOW, 'finished_at': NOW}
    analysis_repo.insert_agent_run(wired_db, spent, [])
    wired_db.commit()
    out = research_requests.run(made['id'], now=NOW, deps=_deps(
        scripted_model([]), agent_settings=_settings(daily_tokens=1000)))
    assert out['version']['status'] == 'error' and 'token_budget' in out['version']['summary']


def test_a_failure_after_running_never_leaves_the_request_running(wired_db, monkeypatch):
    """I5: collection / configuration / write failures become an error version with the reason and the same
    attempts bookkeeping as a model failure; the third closes it ``max_attempts``."""
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    made = make(str(uuid.uuid4()))

    def boom(*args, **kwargs):
        raise RuntimeError('collection exploded')

    monkeypatch.setattr(research_requests, '_collect', boom)
    for attempt in (1, 2):
        out = research_requests.run(made['id'], now=NOW + timedelta(minutes=attempt), deps=core_tx.Deps())
        assert out['version']['status'] == 'error' and out['error'] == 'model_error' and out['status'] == 'queued'
        request = analysis_repo.research_request(wired_db, made['id'])
        assert request is not None and request['status'] == 'queued' and request['attempts'] == attempt
    research_requests.run(made['id'], now=NOW + timedelta(minutes=3), deps=core_tx.Deps())
    request = analysis_repo.research_request(wired_db, made['id'])
    assert request is not None and request['status'] == 'closed' and request['closed_reason'] == 'max_attempts'
    versions = analysis_repo.research_versions(wired_db, made['id'])
    assert [v['status'] for v in versions] == ['error'] * 3
    assert 'collection exploded' not in json.dumps(versions, default=str)        # the reason class, not the raw text


def test_a_failed_final_write_is_recorded_too(wired_db, monkeypatch):
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    made = make(str(uuid.uuid4()))
    monkeypatch.setattr(research_requests, '_collect', lambda *a, **k: {})

    def boom(row, **kwargs):
        raise OSError('disk full')

    monkeypatch.setattr(research_requests.agent_runner, 'run_research', boom)
    out = research_requests.run(made['id'], now=NOW, deps=core_tx.Deps())
    assert out['version']['status'] == 'error'
    request = analysis_repo.research_request(wired_db, made['id'])
    assert request is not None and request['status'] == 'queued' and request['attempts'] == 1


def test_requests_of_a_rejected_attempt_do_not_stay_queued(wired_db, monkeypatch, scripted_model):
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    made = make(str(uuid.uuid4()))
    _fake_collect(monkeypatch)
    model = scripted_model([tool('request_data', {'family': 'fundamentals', 'ticker': 'AMD'}),
                            AIMessage(content=RESEARCH_OK), AIMessage(content='不是 JSON')])
    out = research_requests.run(made['id'], now=NOW, deps=_deps(model))
    assert out['version']['status'] == 'error'
    row = wired_db.execute('SELECT run_id, status, reason FROM data_requests').fetchone()
    assert row == (f"research:{made['id']}", 'deferred', 'research_not_followed')


# ---------------------------------------------------------------- final fix wave: B2 / B3 / B4 / B5

def _running(db, request_id, *, updated_at=None):
    assert analysis_repo.update_research_status(db, request_id, from_status='queued', to_status='running',
                                                updated_at=updated_at)
    db.commit()


def test_drain_hands_each_dossier_the_smaller_of_900_and_what_is_left(wired_db, monkeypatch):
    """B2: the shared budget bounds every dossier; with less than DATA_REQUEST_MIN_S left none starts."""
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    made = make(str(uuid.uuid4()))
    seen = []

    def fake_run(request_id, *, now, deps, deadline_s=None, features=None):
        seen.append((deadline_s, features))
        return {'id': request_id, 'status': 'concluded', 'version': {'status': 'concluded'}}

    monkeypatch.setattr(research_requests, 'run', fake_run)
    summary = {'agent': {'runs': {}}, 'briefs': {}, 'errors': [], 'sources': {}, 'timings': {}}
    assert _agent_node(30, summary)['summary']['agent']['research_requests'] == 0 and seen == []     # 30 s: not started
    assert _agent_node(119, summary)['summary']['agent']['research_requests'] == 0 and seen == []
    assert _agent_node(300, summary)['summary']['agent']['research_requests'] == 1
    assert seen == [(300.0, {})]
    assert analysis_repo.research_request(wired_db, made['id']) is not None


def test_collection_that_runs_out_of_the_deadline_stops_before_the_model(wired_db, monkeypatch, scripted_model):
    """B2: enough left to start, but the (stubbed) clock runs out during collection -- an error version
    ``stage_budget``, no model call, the request back to ``queued`` and no attempt spent."""
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    made = make(str(uuid.uuid4()))
    collected = _fake_collect(monkeypatch)
    ticks = iter([0.0, 0.0, 0.0, 0.0] + [131.0] * 50)       # the start, then four families fit in 130 s
    out = research_requests.run(made['id'], now=NOW, deps=_deps(scripted_model([]), clock=lambda: next(ticks)),
                                deadline_s=130)
    assert out['version']['status'] == 'error' and 'stage_budget' in out['version']['summary']
    assert out['error'] == 'stage_budget' and out['agent_run_id'] is None
    assert [family for family, *_ in collected] == ['bars', 'news', 'sec_filings']        # the rest never started
    request = analysis_repo.research_request(wired_db, made['id'])
    assert request is not None and request['status'] == 'queued' and request['attempts'] == 0
    assert wired_db.execute("SELECT count(*) FROM agent_runs WHERE mode = 'research'").fetchone()[0] == 0


def test_a_shared_budget_stop_before_the_model_spends_no_attempt(wired_db, monkeypatch, scripted_model):
    """B2: collection fits the drain's smaller budget but the model gate does not -- the refusal is the budget's, not
    the dossier's, so no attempt is spent (the own 900-second refusal still is, see above)."""
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    made = make(str(uuid.uuid4()))
    _fake_collect(monkeypatch)
    ticks = iter([0.0] * 8 + [200.0] * 50)
    out = research_requests.run(made['id'], now=NOW, deps=_deps(scripted_model([]), clock=lambda: next(ticks)),
                                deadline_s=150)
    assert out['version']['status'] == 'error' and 'stage_budget' in out['version']['summary']
    request = analysis_repo.research_request(wired_db, made['id'])
    assert request is not None and request['status'] == 'queued' and request['attempts'] == 0


def test_waiting_data_reruns_count_and_the_third_closes(wired_db, monkeypatch, scripted_model):
    """B3: the first wait is not a rerun; each rerun that waits again spends an attempt and the third closes the
    file ``waiting_data_limit`` (``MAX_ATTEMPTS``)."""
    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    made = make(str(uuid.uuid4()))
    _fake_collect(monkeypatch, rows=0)
    blocked = json.dumps({'facts': [{'text': '已披露增长', 'kind': 'disclosure', 'refs': ['call:1']}],
                          'risks': [], 'blocked': [{'what': '最新指引', 'why': '工具返回 not_found'}],
                          'summary': '指引缺失'}, ensure_ascii=False)
    wait = json.dumps({'action': 'wait', 'summary': '等下季指引', 'catalysts': [],
                       'price_scenarios': [], 'next_steps': []}, ensure_ascii=False)

    def attempt(minutes):
        model = scripted_model([tool('get_news', {'days': 30}), AIMessage(content=blocked),
                                AIMessage(content=wait), AIMessage(content='{"counter": []}')])
        return research_requests.run(made['id'], now=NOW + timedelta(minutes=minutes), deps=_deps(model))

    observed = []
    for minutes in range(4):
        assert attempt(minutes)['version']['status'] == 'waiting_data'
        request = analysis_repo.research_request(wired_db, made['id'])
        assert request is not None
        observed.append((request['status'], request['attempts'], request['closed_reason']))
    assert observed == [('waiting_data', 0, None), ('waiting_data', 1, None), ('waiting_data', 2, None),
                        ('closed', 3, 'waiting_data_limit')]
    assert [row['id'] for row in analysis_repo.queued_research_requests(wired_db)] == []      # nothing left to drain


def test_close_stale_returns_a_dead_running_request_to_the_queue(wired_db, monkeypatch):
    """B4: ``running`` older than 2 x RESEARCH_DEADLINE_S is a dossier whose process died -- queued again with one more
    attempt, ``closed / max_attempts`` when that was the last; a live one (younger) is left alone."""
    context_monkey(wired_db, monkeypatch)
    dead_at = NOW - timedelta(seconds=2 * 900 + 1)
    dead = _aged(wired_db, created_days=1)
    last = _aged(wired_db, created_days=1)
    alive = _aged(wired_db, created_days=1)
    wired_db.execute('UPDATE research_requests SET attempts = 2 WHERE id = %s', (last,))
    _running(wired_db, dead, updated_at=dead_at)
    _running(wired_db, last, updated_at=dead_at)
    _running(wired_db, alive, updated_at=NOW - timedelta(seconds=2 * 900 - 1))
    assert research_requests.close_stale(now=NOW) == 0                       # none is a 30-day close
    request = analysis_repo.research_request(wired_db, dead)
    assert request is not None and (request['status'], request['attempts']) == ('queued', 1)
    request = analysis_repo.research_request(wired_db, last)
    assert request is not None and (request['status'], request['attempts'], request['closed_reason']) == (
        'closed', 3, 'max_attempts')
    assert _status(wired_db, alive)[0] == 'running'


def test_drain_researches_with_the_runs_frozen_switches(wired_db, monkeypatch, scripted_model):
    """B5: a switch flipped during the run changes nothing for the daily drain's collection -- it uses the frozen
    values; outside the daily run (``features=None``) the current ones still apply."""
    from investment_assistant.core import features as core_features

    no_background(monkeypatch)
    context_monkey(wired_db, monkeypatch)
    made = make(str(uuid.uuid4()))
    _fake_collect(monkeypatch, rows=0)
    _fake_collect.features.clear()
    frozen = {'backup_sources': True, 'company_researcher': True, 'portfolio_manager': True,
              'optimize_proposals': True}
    core_features.record_change(wired_db, flag='backup_sources', enabled=False, reason='运行中翻动',
                                request_id=uuid.uuid4())
    wired_db.commit()
    summary = {'agent': {'runs': {}}, 'briefs': {}, 'errors': [], 'sources': {}, 'timings': {}}
    model = scripted_model([tool('get_news', {'days': 30}), AIMessage(content=RESEARCH_OK),
                            AIMessage(content=CONCLUSION_BUY), AIMessage(content=COUNTER_OK)])
    counts = runner.drain_research_requests(SESSION, now=NOW, deps=_deps(model), summary=summary,
                                            budget_left_s=900, features=frozen)
    assert counts == {'requests': 1} and summary.get('errors', []) == []
    assert len(_fake_collect.features) == 7 and all(f['backup_sources'] is True for f in _fake_collect.features)
    assert analysis_repo.research_request(wired_db, made['id'])['status'] == 'concluded'
    _fake_collect.features.clear()
    research_requests._collect('AMD', now=NOW, deps=core_tx.Deps())              # a non-daily entrance
    assert all(f['backup_sources'] is False for f in _fake_collect.features)
