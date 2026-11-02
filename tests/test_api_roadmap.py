"""The roadmap records, proposals, briefs and their HTTP contract (spec §8, §12.1, UI1) against an isolated schema;
no model, no network. The brief job is submitted through ``core.runner`` — recorded here, never run in a thread."""
import json
import uuid
from contextlib import contextmanager, nullcontext
from datetime import UTC, date, datetime, timedelta

import pytest
import yaml
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from investment_assistant.analysis import repo as analysis_repo
from investment_assistant.analysis.agent import tools as agent_tools
from investment_assistant.core import tx as core_tx
from investment_assistant.core.idempotency import Conflict
from investment_assistant.core.llm.model import AgentSettings, _agent_run_row
from investment_assistant.core.versions import BRIEF_AGENT_VERSION
from investment_assistant.optimize import briefs, proposals, roadmap
from investment_assistant.optimize import repo as optimize_repo

NOW = datetime(2026, 10, 30, 22, tzinfo=UTC)
SETTINGS = AgentSettings('chatgpt', 'fake', None, 1000, None)


@pytest.fixture
def api_db(workbench_db, monkeypatch):
    @contextmanager
    def connection():
        yield workbench_db

    monkeypatch.setattr(core_tx, 'connection', connection)
    from investment_assistant.api.routes import roadmap as routes

    monkeypatch.setattr(routes, '_now', lambda: NOW)
    return workbench_db


@pytest.fixture
def submitted(monkeypatch):
    """Every background submission is recorded, none runs."""
    calls = []
    monkeypatch.setattr(briefs.core_runner, 'submit', lambda kind, fn, **kw: calls.append((kind, fn)) or f'{kind}-test')
    return calls


def seed_files(directory, *, versions=None, decisions=None):
    files = {
        'goals.yaml': [{'goal_id': 'process', 'body': '最终目标：稳定执行。\n主指标：流程偏差。'}],
        'versions.yaml': versions or [
            {'version_id': 'v2.9', 'title': '可执行计划', 'theme': '执行', 'scope': '计划口径。', 'depends_on': ['v2.8'],
             'status': 'done', 'exit_conditions': '验收通过。', 'spec_path': 'docs/specs/a.md', 'tag': 'v2.9.0'},
            {'version_id': 'v2.15', 'title': 'roadmap', 'theme': '系统内规划', 'scope': '保留决策原话。',
             'depends_on': ['v2.9'], 'status': 'planned', 'exit_conditions': '完整导出。'},
            {'version_id': 'v2.10', 'title': '模拟账户', 'theme': '账户', 'status': 'done'}],
        'decisions.yaml': decisions or [
            {'decision_no': 1, 'decided_on': date(2026, 11, 1), 'question': '是否合并？', 'decision': '一次发布。',
             'quote': '  原话有空格，不能删。  ', 'version_ids': ['v2.9']},
            {'decision_no': 2, 'decided_on': date(2026, 11, 1), 'question': '研究开链？', 'decision': '只建档案。',
             'quote': '选 B。\n不自动开链。', 'version_ids': ['v2.15']},
            {'decision_no': 3, 'decided_on': date(2026, 11, 2), 'question': '界面要求？', 'decision': '保留原文。',
             'quote': '中文 | `代码` **原话**', 'version_ids': []}],
        'evidence.yaml': [{'evidence_id': 'manual-20261101', 'version_id': 'v2.9', 'kind': 'manual',
                           'body': '备源测试通过。', 'observed_on': date(2026, 11, 1),
                           'source_path': 'docs/roadmap/acceptance.md'}],
    }
    directory.mkdir(exist_ok=True)
    for name, content in files.items():
        (directory / name).write_text(yaml.safe_dump(content, allow_unicode=True, sort_keys=False), encoding='utf-8')
    return directory


def make_proposal(conn, title='补齐失败证据', **changes):
    conn.commit()
    row = optimize_repo.insert_proposal(conn, {
        'source': 'data', 'title': title, 'change': '新增失败原因记录。', 'rationale': '失败目前不可见。',
        'evidence_refs': ['fact:data_failures#abc'], 'expected': '定位缺口。', 'acceptance': '每次失败有原因。',
        **changes})
    conn.commit()
    return row


def decide(client, proposal_id, *, decision='approved', reason='同意，下个版本做', request_id=None, **extra):
    return client.post(f'/api/roadmap/proposals/{proposal_id}/decide',
                       json={'decision': decision, 'reason': reason, 'request_id': request_id or str(uuid.uuid4()),
                             **extra})


# ------------------------------------------------------------------------------------------------ import / export

def test_import_export_roundtrip_counts(client, api_db, tmp_path):
    directory = seed_files(tmp_path / 'roadmap')
    first = roadmap.import_dir(directory)
    assert first == {'goals': {'inserted': 1, 'skipped': 0}, 'versions': {'inserted': 3, 'skipped': 0},
                     'decisions': {'inserted': 3, 'skipped': 0}, 'evidence': {'inserted': 1, 'skipped': 0}}
    # idempotent: the same keys with the same content are skipped
    again = roadmap.import_dir(directory)
    assert all(v == {'inserted': 0, 'skipped': n} for v, n in zip(again.values(), (1, 3, 3, 1)))
    assert api_db.execute('SELECT count(*) FROM roadmap_versions').fetchone()[0] == 3
    # the same key with other content is an error, nothing is overwritten and nothing else of that import lands
    changed = seed_files(tmp_path / 'changed', versions=[
        {'version_id': 'v2.9', 'title': '被改动的标题', 'status': 'done'},
        {'version_id': 'v3.0', 'title': '新版本', 'status': 'planned'}])
    with pytest.raises(Conflict, match='versions.yaml'):
        roadmap.import_dir(changed)
    assert api_db.execute("SELECT title FROM roadmap_versions WHERE version_id = 'v2.9'").fetchone()[0] == '可执行计划'
    assert api_db.execute("SELECT count(*) FROM roadmap_versions WHERE version_id = 'v3.0'").fetchone()[0] == 0
    # unknown fields and broken files are named
    bad = seed_files(tmp_path / 'bad', versions=[{'version_id': 'v5', 'title': 't', 'status': 'planned', 'oops': 1}])
    with pytest.raises(ValueError, match='roadmap_import_invalid.*versions.yaml'):
        roadmap.import_dir(bad)
    (tmp_path / 'gone').mkdir()
    with pytest.raises(ValueError, match='roadmap_import_invalid.*goals.yaml'):
        roadmap.import_dir(tmp_path / 'gone')

    # export: every version_id, every decision number and every quote verbatim; counts match; natural order
    text = roadmap.export_markdown(now=NOW)
    for version_id in ('v2.9', 'v2.10', 'v2.15'):
        assert version_id in text
    assert text.index('### 版本 v2.9') < text.index('### 版本 v2.10') < text.index('### 版本 v2.15')
    assert text.count('### 版本 ') == 3 and text.count('### 决策 #') == 3
    for decision in yaml.safe_load((directory / 'decisions.yaml').read_text(encoding='utf-8')):
        assert f"### 决策 #{decision['decision_no']}" in text and decision['quote'] in text
    assert '最终目标：稳定执行。\n主指标：流程偏差。' in text and '备源测试通过。' in text
    assert roadmap.export_markdown(now=NOW) == text                      # deterministic
    # the HTTP side is the same function
    response = client.get('/api/roadmap/export.md')
    assert response.status_code == 200 and response.headers['content-type'].startswith('text/markdown')
    assert response.text == text
    view = client.get('/api/roadmap').json()
    assert set(view) == {'goals', 'versions', 'decisions', 'evidence', 'exported_at'}
    assert [len(view[k]) for k in ('goals', 'versions', 'decisions', 'evidence')] == [1, 3, 3, 1]
    assert view['exported_at'] == NOW.isoformat()
    assert [v['version_id'] for v in view['versions']] == ['v2.9', 'v2.10', 'v2.15']   # natural order


def test_import_cli_and_export_cli(api_db, tmp_path, capsys):
    directory = seed_files(tmp_path / 'roadmap')
    assert roadmap.main(['import', '--dir', str(directory)]) == 0
    assert '"inserted": 3' in capsys.readouterr().out
    out = tmp_path / 'out' / 'roadmap.md'
    assert roadmap.main(['export', '--out', str(out)]) == 0
    assert out.read_text(encoding='utf-8').startswith('# Roadmap') and '### 决策 #3' in out.read_text(encoding='utf-8')
    with pytest.raises(SystemExit):                      # --help never touches a database
        roadmap.main(['--help'])


def test_versions_and_decisions_detail_include_history(client, api_db, tmp_path):
    roadmap.import_dir(seed_files(tmp_path / 'roadmap'))
    optimize_repo.insert_roadmap_record(api_db, 'roadmap_versions', {
        'version_id': 'v2.15', 'version': 2, 'title': 'roadmap', 'status': 'engineering_done'})
    api_db.commit()
    detail = client.get('/api/roadmap/versions/v2.15').json()
    assert set(detail) == {'version', 'history', 'brief', 'brief_status'}
    assert detail['version']['status'] == 'engineering_done' and [h['version'] for h in detail['history']] == [1, 2]
    assert detail['brief'] is None and detail['brief_status'] == 'not_requested'
    assert client.get('/api/roadmap/versions/nope').status_code == 404
    decision = client.get('/api/roadmap/decisions/2').json()
    assert set(decision) == {'decision', 'history'} and decision['decision']['quote'] == '选 B。\n不自动开链。'
    assert [h['version'] for h in decision['history']] == [1]
    assert client.get('/api/roadmap/decisions/999').status_code == 404
    assert client.get('/api/roadmap/decisions/abc').status_code == 400


def test_migration_missing_is_503(client, api_db):
    api_db.execute('DROP TABLE roadmap_briefs, roadmap_proposals, roadmap_evidence, roadmap_decisions, '
                   'roadmap_versions, roadmap_goals CASCADE')
    api_db.commit()
    for path in ('/api/roadmap', '/api/roadmap/proposals', '/api/roadmap/export.md', '/api/roadmap/versions/v1'):
        response = client.get(path)
        assert response.status_code == 503 and response.json() == {'error': 'migration_031_missing'}, path


# ------------------------------------------------------------------------------------------------ decisions

def test_decide_approved_creates_version_and_brief_task(client, api_db, tmp_path, submitted):
    roadmap.import_dir(seed_files(tmp_path / 'roadmap'))
    proposal = make_proposal(api_db)
    request_id = str(uuid.uuid4())
    response = decide(client, proposal['id'], request_id=request_id)
    assert response.status_code == 200
    assert response.json() == {'status': 'approved', 'version_id': 'v2.16', 'brief_status': 'pending'}
    assert [kind for kind, _ in submitted] == ['brief']
    version = api_db.execute("SELECT title, status, scope, exit_conditions, proposal_id, version, depends_on "
                             "FROM roadmap_versions WHERE version_id = 'v2.16'").fetchall()
    assert version == [('补齐失败证据', 'planned', '新增失败原因记录。', '每次失败有原因。', proposal['id'], 1, [])]
    decision = api_db.execute('SELECT decision_no, question, decision, quote, version_ids FROM roadmap_decisions '
                              'WHERE decision_no = 4').fetchall()
    assert decision == [(4, '补齐失败证据', '批准：同意，下个版本做', '同意，下个版本做', ['v2.16'])]
    row = api_db.execute('SELECT status, decision_reason, request_id, decided_at FROM roadmap_proposals WHERE id = %s',
                         (proposal['id'],)).fetchone()
    assert row[:2] == ('approved', '同意，下个版本做') and str(row[2]) == request_id and row[3] is not None
    # a replay of the same request writes and submits nothing
    replay = decide(client, proposal['id'], request_id=request_id)
    assert replay.status_code == 200 and replay.json() == response.json()
    assert api_db.execute('SELECT count(*) FROM roadmap_versions').fetchone()[0] == 4 and len(submitted) == 1
    assert api_db.execute('SELECT count(*) FROM roadmap_decisions').fetchone()[0] == 4
    # the same request with other content, and a decision of a decided proposal, are conflicts
    assert decide(client, proposal['id'], request_id=request_id, reason='换个理由').status_code == 409
    assert decide(client, proposal['id'], decision='rejected').status_code == 409
    other = make_proposal(api_db, '另一条')
    assert decide(client, other['id'], request_id=request_id).status_code == 409           # request id belongs to the first
    # the version detail now reads pending (approved proposal, no brief yet) and links the proposal
    detail = client.get('/api/roadmap/versions/v2.16').json()
    assert detail['brief_status'] == 'pending' and detail['brief'] is None and detail['version']['proposal_id'] == proposal['id']
    # the list reads the decided state back
    items = client.get('/api/roadmap/proposals', params={'status': 'approved'}).json()['items']
    assert [i['id'] for i in items] == [proposal['id']] and items[0]['decision_reason'] == '同意，下个版本做'
    assert client.get('/api/roadmap/proposals', params={'status': 'bogus'}).status_code == 400


def test_decide_with_explicit_version_id_and_validation(client, api_db, tmp_path, submitted):
    roadmap.import_dir(seed_files(tmp_path / 'roadmap'))
    proposal = make_proposal(api_db)
    assert decide(client, proposal['id'], version_id='v2.15').status_code == 409          # already used
    assert decide(client, proposal['id'], version_id='not a version').status_code == 400
    assert decide(client, proposal['id'], reason='  ').status_code == 400
    assert decide(client, proposal['id'], decision='superseded').status_code == 400
    assert decide(client, proposal['id'], request_id='nope').status_code == 400
    assert decide(client, 99999).status_code == 404
    assert api_db.execute("SELECT status FROM roadmap_proposals WHERE id = %s", (proposal['id'],)).fetchone()[0] == 'proposed'
    assert api_db.execute('SELECT count(*) FROM roadmap_versions').fetchone()[0] == 3 and submitted == []
    ok = decide(client, proposal['id'], version_id='v3.0')
    assert ok.json() == {'status': 'approved', 'version_id': 'v3.0', 'brief_status': 'pending'}
    # the default is the next unused v2.x, whatever explicit ids other versions took
    fresh = make_proposal(api_db, '再一条')
    assert decide(client, fresh['id']).json()['version_id'] == 'v2.16'


def test_decide_rejected_only_status(client, api_db, tmp_path, submitted):
    roadmap.import_dir(seed_files(tmp_path / 'roadmap'))
    proposal = make_proposal(api_db)
    response = decide(client, proposal['id'], decision='rejected', reason='现在不需要')
    assert response.status_code == 200
    assert response.json() == {'status': 'rejected', 'version_id': None, 'brief_status': None}
    row = api_db.execute('SELECT status, decision_reason, decided_at FROM roadmap_proposals WHERE id = %s',
                         (proposal['id'],)).fetchone()
    assert row[:2] == ('rejected', '现在不需要') and row[2] is not None
    assert api_db.execute('SELECT count(*) FROM roadmap_versions').fetchone()[0] == 3     # nothing created
    assert api_db.execute('SELECT count(*) FROM roadmap_decisions').fetchone()[0] == 3
    assert submitted == []
    assert decide(client, proposal['id'], decision='approved').status_code == 409


def test_proposed_has_no_changes_check(api_db):
    clean, with_version, with_brief = (make_proposal(api_db, f'p{n}') for n in range(3))
    assert proposals.violations(api_db) == []                       # normal pending proposals pass (O1)
    optimize_repo.insert_roadmap_record(api_db, 'roadmap_versions', {
        'version_id': 'v3', 'version': 1, 'title': 'early', 'status': 'planned', 'proposal_id': with_version['id']})
    optimize_repo.insert_brief(api_db, {'version_id': 'v4', 'proposal_id': with_brief['id'], 'status': 'ok',
                                        'body': '## 验收\n通过'})
    # the violations are all proposed ids minus the clean ones returned by the repository
    assert optimize_repo.proposals_without_changes(api_db) == [clean['id']]
    assert proposals.violations(api_db) == [with_version['id'], with_brief['id']]
    optimize_repo.decide_proposal(api_db, with_version['id'], decision='approved', reason='ok', decided_at=NOW)
    assert proposals.violations(api_db) == [with_brief['id']]       # a decided proposal is no longer "proposed"


# ------------------------------------------------------------------------------------------------ the get_roadmap tool

def tool_ctx(cutoff):
    return agent_tools.ToolContext(ticker='', session=date(2026, 10, 30), cutoff_at=cutoff, read=core_tx._read_only,
                                   bindings={'get_roadmap': roadmap.get_roadmap})


def test_get_roadmap_tool_respects_cutoff(api_db):
    before, after = NOW - timedelta(days=2), NOW + timedelta(days=2)
    add = optimize_repo.insert_roadmap_record
    add(api_db, 'roadmap_goals', {'goal_id': 'process', 'version': 1, 'body': '目标 v1', 'created_at': before})
    add(api_db, 'roadmap_goals', {'goal_id': 'process', 'version': 2, 'body': '目标 v2', 'created_at': after})
    add(api_db, 'roadmap_versions', {'version_id': 'v2.15', 'version': 1, 'title': 'old', 'status': 'planned',
                                     'created_at': before})
    add(api_db, 'roadmap_versions', {'version_id': 'v2.15', 'version': 2, 'title': 'new', 'status': 'done',
                                     'created_at': after})
    add(api_db, 'roadmap_versions', {'version_id': 'v2.16', 'version': 1, 'title': 'later only', 'status': 'planned',
                                     'created_at': after})
    add(api_db, 'roadmap_decisions', {'decision_no': 1, 'version': 1, 'decided_on': date(2026, 10, 1), 'question': '数据怎么存',
                                      'decision': '进数据库', 'quote': '就放库里', 'created_at': before})
    add(api_db, 'roadmap_decisions', {'decision_no': 2, 'version': 1, 'decided_on': date(2026, 10, 2), 'question': '研究开链',
                                      'decision': '只建档案', 'created_at': before})
    add(api_db, 'roadmap_decisions', {'decision_no': 3, 'version': 1, 'decided_on': date(2026, 11, 5), 'question': '未来决策',
                                      'decision': 'x', 'created_at': after})
    made = optimize_repo.insert_proposal(api_db, {'source': 'data', 'title': '提案', 'change': 'c', 'rationale': 'r',
                                                  'evidence_refs': ['fact:data_failures#a'], 'created_at': before})
    late = optimize_repo.insert_proposal(api_db, {'source': 'data', 'title': '迟到提案', 'change': 'c', 'rationale': 'r',
                                                  'evidence_refs': ['fact:data_failures#a'], 'created_at': after})
    optimize_repo.decide_proposal(api_db, made['id'], decision='approved', reason='ok', decided_at=after)
    api_db.commit()
    ctx = tool_ctx(NOW)

    goals = roadmap.get_roadmap(ctx, 'goals')
    assert goals['section'] == 'goals' and goals['cutoff_at'] == NOW.isoformat()
    assert [(i['goal_id'], i['body'], i['ref']) for i in goals['items']] == [
        ('process', '目标 v1', 'roadmap:roadmap_goals:process@1')]
    versions = roadmap.get_roadmap(ctx, 'versions')
    assert [(i['version_id'], i['title'], i['ref']) for i in versions['items']] == [
        ('v2.15', 'old', 'roadmap:roadmap_versions:v2.15@1')]
    assert roadmap.get_roadmap(ctx, 'versions', 'v2.16')['items'] == []              # exists only after the cutoff
    assert [i['version_id'] for i in roadmap.get_roadmap(ctx, 'versions', 'V2.15')['items']] == ['v2.15']
    decisions = roadmap.get_roadmap(ctx, 'decisions')
    assert [(i['decision_no'], i['ref']) for i in decisions['items']] == [
        (1, 'roadmap:roadmap_decisions:1@1'), (2, 'roadmap:roadmap_decisions:2@1')]
    assert [i['decision_no'] for i in roadmap.get_roadmap(ctx, 'decisions', '开链')['items']] == [2]
    assert [i['decision_no'] for i in roadmap.get_roadmap(ctx, 'decisions', '放库里')['items']] == [1]   # the quote counts
    # proposals: created before the cutoff only, and a decision made after the cutoff is not yet known
    items = roadmap.get_roadmap(ctx, 'proposals')['items']
    assert [(i['id'], i['status'], i['decision_reason'], i['decided_at']) for i in items] == [
        (made['id'], 'proposed', None, None)] and late['id'] not in [i['id'] for i in items]
    assert items[0]['ref'] == f"roadmap:roadmap_proposals:{made['id']}@1"
    # a later cutoff sees the later state
    far = roadmap.get_roadmap(tool_ctx(NOW + timedelta(days=5)), 'versions')['items']
    assert [(i['version_id'], i['title']) for i in far] == [('v2.15', 'new'), ('v2.16', 'later only')]
    assert roadmap.get_roadmap(tool_ctx(NOW + timedelta(days=5)), 'proposals')['items'][0]['status'] == 'approved'
    with pytest.raises(agent_tools.ToolError):
        roadmap.get_roadmap(ctx, 'bogus')


def test_get_roadmap_through_the_toolset_is_whitelisted_and_recorded(api_db):
    """The real dispatch path: the optimize toolset's ``get_roadmap`` reaches the bound host function, a role
    without the tool never does, and a missing binding fails closed."""
    from investment_assistant.analysis.agent import toolsets

    optimize_repo.insert_roadmap_record(api_db, 'roadmap_versions', {
        'version_id': 'v2.15', 'version': 1, 'title': 'old', 'status': 'planned', 'created_at': NOW - timedelta(days=1)})
    api_db.commit()
    recorder = agent_tools.Recorder(phase='optimize')
    ctx = tool_ctx(NOW)
    result = agent_tools.call(ctx, recorder, 'get_roadmap', {'section': 'versions'}, now=NOW,
                              tools=toolsets.tools_for('optimize@agent-optimize-1'))
    assert result['ref'] == 'call:1' and [i['version_id'] for i in result['data']['items']] == ['v2.15']
    assert recorder.calls[0]['phase'] == 'optimize' and recorder.calls[0]['result_sha256']
    outside = agent_tools.call(ctx, recorder, 'get_roadmap', {'section': 'versions'}, now=NOW,
                               tools=toolsets.tools_for('propose@agent-4'))
    assert 'error' in outside
    with pytest.raises(agent_tools.ToolError, match='get_roadmap'):
        toolsets.require_bindings(agent_tools.ToolContext(ticker='', session=date(2026, 10, 30), cutoff_at=NOW,
                                                          read=core_tx._read_only), 'optimize@agent-optimize-1')


# ------------------------------------------------------------------------------------------------ evidence rows (UI1)

def frozen_run(conn, pack):
    run = _agent_run_row({'ticker': None, 'signal_date': None, 'pool': None}, settings=SETTINGS, attempt=1, now=NOW,
                         status='ok', mode='optimize', agent_version='agent-optimize-1',
                         output={'proposals': [], 'dropped': [], 'evidence_pack': pack})
    analysis_repo.insert_agent_run(conn, run, [])
    conn.commit()
    return run['id']


def test_proposal_list_reads_evidence_rows_from_the_frozen_pack(client, api_db):
    pack = {'data_failures': [{'id': 'aaa', 'family': 'bars', 'error_kind': 'timeout', 'failed_targets': 2}],
            'agent_quality': [{'id': 'bbb', 'kind': 'agent_reason', 'count': 3}], 'retro_patterns': [], 'route': [],
            'open_proposals': []}
    run_id = frozen_run(api_db, pack)
    with_pack = make_proposal(api_db, '有证据', agent_run_id=run_id,
                              evidence_refs=['fact:data_failures#aaa', 'fact:agent_quality#bbb'])
    missing_row = make_proposal(api_db, '证据行缺失', agent_run_id=run_id, evidence_refs=['fact:data_failures#zzz'])
    legacy = make_proposal(api_db, '没有证据包')
    items = {i['title']: i for i in client.get('/api/roadmap/proposals').json()['items']}
    assert items['有证据']['evidence_rows'] == [
        {'ref': 'fact:data_failures#aaa', 'body': pack['data_failures'][0]},
        {'ref': 'fact:agent_quality#bbb', 'body': pack['agent_quality'][0]}]
    assert items['有证据']['evidence_note'] is None
    assert items['证据行缺失']['evidence_rows'] == [] and '缺少' in items['证据行缺失']['evidence_note']
    assert items['没有证据包']['evidence_rows'] == [] and '缺少' in items['没有证据包']['evidence_note']
    assert {'id', 'title', 'change', 'rationale', 'evidence_refs', 'expected', 'acceptance', 'status', 'source',
            'created_at', 'decided_at', 'decision_reason', 'agent_run_id', 'run_id'} <= set(items['有证据'])
    assert [i['title'] for i in client.get('/api/roadmap/proposals', params={'status': 'proposed'}).json()['items']] \
        == ['没有证据包', '证据行缺失', '有证据']                                 # newest first
    # the rows are the frozen ones: changing the live tables changes nothing
    assert with_pack['id'] and missing_row['id'] and legacy['id']


# ------------------------------------------------------------------------------------------------ briefs

BRIEF_MD = ('## 背景与证据\n失败不可见。\n\n## 改什么\n数据：bars 适配器；prompt：OPTIMIZE_PROMPT 证据段。\n\n'
            '## 不改什么\n口径数值。\n\n## 验收\n连续一周每次失败有原因。\n\n## 风险\n记录量增加。')


def deps_for(model=None, settings=SETTINGS):
    return core_tx.Deps(agent_settings=settings, agent_model=model,
                        agent_checkpointer=lambda: nullcontext(InMemorySaver()))


def approved_version(conn, client, submitted, tmp_path):
    roadmap.import_dir(seed_files(tmp_path / 'roadmap'))
    proposal = make_proposal(conn)
    version_id = decide(client, proposal['id']).json()['version_id']
    return proposal, version_id


def test_brief_run_writes_ok_row_and_run(client, api_db, submitted, tmp_path, scripted_model):
    proposal, version_id = approved_version(api_db, client, submitted, tmp_path)
    model = scripted_model([
        AIMessage(content='', tool_calls=[{'name': 'get_roadmap', 'args': {'section': 'decisions', 'query': '合并'},
                                           'id': 'g1'}]),
        AIMessage(content=BRIEF_MD)])
    result = briefs.run(version_id, now=NOW, deps=deps_for(model))
    assert result['status'] == 'ok' and result['reason'] is None
    [brief] = optimize_repo.briefs_for(api_db, version_id)
    assert (brief['status'], brief['body'], brief['proposal_id']) == ('ok', BRIEF_MD, proposal['id'])
    run = api_db.execute('SELECT mode, agent_version, status, ticker FROM agent_runs WHERE id = %s',
                         (brief['agent_run_id'],)).fetchone()
    assert run == ('brief', BRIEF_AGENT_VERSION, 'ok', None)
    [call] = analysis_repo.agent_tool_calls(api_db, brief['agent_run_id'])
    assert (call['phase'], call['tool']) == ('brief', 'get_roadmap')
    human = model.seen[0][1].content                             # the proposal's full text is the brief's evidence
    assert '补齐失败证据' in human and '新增失败原因记录。' in human and '每次失败有原因。' in human and version_id in human
    detail = client.get(f'/api/roadmap/versions/{version_id}').json()
    assert detail['brief_status'] == 'ok' and detail['brief']['body'] == BRIEF_MD


@pytest.mark.parametrize('text,reason', [('没有验收段落', 'brief_acceptance_missing'), ('', 'brief_empty')])
def test_brief_rejected_output_is_an_error_row_and_retry_appends(client, api_db, submitted, tmp_path, scripted_model,
                                                                   text, reason):
    proposal, version_id = approved_version(api_db, client, submitted, tmp_path)
    briefs.run(version_id, now=NOW, deps=deps_for(scripted_model([AIMessage(content=text or ' ')])))
    [bad] = optimize_repo.briefs_for(api_db, version_id)
    assert (bad['status'], bad['body'], bad['reason']) == ('error', None, reason) and bad['agent_run_id'] is not None
    assert api_db.execute('SELECT status, reason FROM agent_runs WHERE id = %s', (bad['agent_run_id'],)).fetchone() == (
        'rejected', reason)
    assert client.get(f'/api/roadmap/versions/{version_id}').json()['brief_status'] == 'error'
    # the retry endpoint submits a job; running it appends a new row (append-only) and the version reads ok
    response = client.post(f'/api/roadmap/briefs/{version_id}/retry')
    assert response.status_code == 200
    assert response.json() == {'version_id': version_id, 'brief_status': 'pending', 'run_id': 'brief-test'}
    assert submitted[-1][0] == 'brief' and callable(submitted[-1][1])
    briefs.run(version_id, now=NOW + timedelta(minutes=5), deps=deps_for(scripted_model([AIMessage(content=BRIEF_MD)])))
    assert [b['status'] for b in optimize_repo.briefs_for(api_db, version_id)] == ['error', 'ok']
    assert client.get(f'/api/roadmap/versions/{version_id}').json()['brief_status'] == 'ok'
    assert client.post(f'/api/roadmap/briefs/{version_id}/retry').status_code == 409         # an ok brief is not retried


def test_brief_unavailable_model_and_model_error_are_error_rows(client, api_db, submitted, tmp_path):
    proposal, version_id = approved_version(api_db, client, submitted, tmp_path)
    off = AgentSettings('off', '', None, None, None)
    out = briefs.run(version_id, now=NOW, deps=deps_for(settings=off))
    assert out['status'] == 'error' and out['reason'] == 'agent_unavailable: disabled'

    class Boom:
        def bind_tools(self, *a, **k):
            return self

        def invoke(self, *a, **k):
            raise RuntimeError('model down')

    out = briefs.run(version_id, now=NOW, deps=deps_for(Boom()))
    assert out['reason'] == 'model_error'
    rows = optimize_repo.briefs_for(api_db, version_id)
    assert [(b['status'], b['reason']) for b in rows] == [('error', 'agent_unavailable: disabled'), ('error', 'model_error')]
    assert rows[0]['agent_run_id'] is None and rows[1]['agent_run_id'] is not None      # no model call, no run row


def test_retry_guards(client, api_db, submitted, tmp_path):
    roadmap.import_dir(seed_files(tmp_path / 'roadmap'))
    assert client.post('/api/roadmap/briefs/nope/retry').status_code == 404
    assert client.post('/api/roadmap/briefs/v2.15/retry').status_code == 400                # no proposal behind it
    assert submitted == []
    proposal, version_id = approved_version(api_db, client, submitted, tmp_path)
    pending = client.post(f'/api/roadmap/briefs/{version_id}/retry')
    assert pending.status_code == 200 and pending.json()['brief_status'] == 'pending'     # pending may be retried too
