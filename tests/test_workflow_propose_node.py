"""The ``propose`` node (v2.15 spec §8.3): Saturday optimisation proposals from the frozen evidence pack.

SQL tests on the disposable database; the model is scripted (no real call). The ``get_roadmap`` tool is bound by the
optimize host through ``ToolContext.bindings`` (R13), so a scripted tool round exercises the real wiring."""
import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.data.repo as data_repo
import investment_assistant.optimize.proposals as optimize_proposals
import investment_assistant.optimize.repo as optimize_repo
import investment_assistant.workflow.graph as workflow
import investment_assistant.workflow.nodes.propose as propose_node
from _daily_db import clock, daily_config, daily_db, job_reports, notices, shared_saver  # noqa: F401
from test_daily_workflow import effective, go
from test_workbench_paper_accounts import END, NOW, conn  # noqa: F401
from investment_assistant.core import tx as service
from investment_assistant.core.llm.model import AgentSettings
from investment_assistant.core.stages import StageAcc
from investment_assistant.core.versions import OPTIMIZE_AGENT_VERSION

ON = {'optimize_proposals': True}
OFF = {'optimize_proposals': False}
SETTINGS = AgentSettings('chatgpt', 'fake', None, 1000, None)
WALL = NOW                                          # 2026-10-23 23:00 UTC = Saturday 07:00 in Beijing
WEDNESDAY = datetime(2026, 10, 21, 12, tzinfo=UTC)


def deps_for(model=None):
    return service.Deps(agent_settings=SETTINGS, agent_model=model,
                        agent_checkpointer=lambda: nullcontext(InMemorySaver()))


class Recorder:
    def __init__(self):
        self.skipped, self.finished = [], []

    def skip(self, stage_id, reason):
        self.skipped.append((stage_id, reason))

    def finish(self, stage_id, failed=None):
        self.finished.append((stage_id, failed))


def make_ctx(model=None, *, wall=WALL, settings=SETTINGS):
    deps = deps_for(model)
    deps.agent_settings = settings
    return SimpleNamespace(recorder=Recorder(), deps=deps, clock=lambda: 0, wall_now=lambda: wall)


def make_state(*, cutoff=NOW, features=ON, **extra):
    return {'run_id': 'r1', 'session': END.isoformat(), 'cutoff_at': cutoff.isoformat(), 'summary': {},
            'features': features, **extra}


def seed_failure(conn, *, when=WALL - timedelta(days=1)):
    """One failed bars fetch yesterday: the data_failures section of the evidence pack."""
    conn.commit()
    data_repo.insert_fetch_run(conn, {
        'family': 'bars', 'job': 'daily', 'trigger': 'scheduled', 'scope': 'family', 'status': 'failed',
        'target_count': 2, 'ok_count': 0, 'started_at': when, 'finished_at': when,
        'detail': {'targets': [{'target': 'AAA', 'ok': False, 'error_kind': 'timeout'},
                               {'target': 'BBB', 'ok': False, 'error_kind': 'timeout'}]}})
    conn.commit()


def pack_of(conn):
    conn.commit()
    return optimize_proposals.evidence_pack(conn, now=WALL)


def ref(pack, key, index=0):
    return f"fact:{key}#{pack[key][index]['id']}"


def draft(title, refs, **overrides):
    return {'source': 'data', 'title': title, 'change': '给 bars 抓取加超时重试并记录原因。', 'rationale': '本周 bars 超时集中。',
            'evidence_refs': refs, 'expected': '超时失败减少。', 'acceptance': '连续一周无同类超时。', **overrides}


def reply(*proposals):
    return AIMessage(content=json.dumps({'proposals': list(proposals)}, ensure_ascii=False))


def tool_round(name='get_roadmap', args=None):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args or {'section': 'versions'}, 'id': f'{name}-1'}])


def run_node(model=None, *, state=None, ctx=None, acc=None):
    acc = acc if acc is not None else StageAcc()
    ctx = ctx if ctx is not None else make_ctx(model)
    out = propose_node.run(state or make_state(), ctx, acc)
    return out, ctx, acc


def rows(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


# ------------------------------------------------------------------------------------------------ the gates

def test_skipped_on_non_proposal_day(conn, scripted_model):
    seed_failure(conn, when=WEDNESDAY - timedelta(hours=3))
    model = scripted_model([])
    out, ctx, acc = run_node(model, state=make_state(cutoff=WEDNESDAY), ctx=make_ctx(model, wall=WEDNESDAY))
    assert ctx.recorder.skipped == [('propose', 'not_proposal_day')] and out['summary'].get('propose') is None
    assert model.seen == [] and conn.execute('SELECT count(*) FROM agent_runs').fetchone()[0] == 0
    assert conn.execute('SELECT count(*) FROM roadmap_proposals').fetchone()[0] == 0
    # the Beijing day decides, not the New York one: Saturday 07:00 Beijing is still Friday night in New York
    assert propose_node.is_proposal_day(WALL) and not propose_node.is_proposal_day(WEDNESDAY)
    assert not propose_node.is_proposal_day(WALL + timedelta(days=1))


def test_proposal_day_has_one_definition_in_both_dst_regimes():
    """R25: the node and the evidence module share ONE rule — Saturday in Beijing at the run's cutoff — so the node's
    gate and any other reader of "is it the proposal day" cannot disagree (the old session-date helper is gone)."""
    from investment_assistant.optimize import evidence
    assert propose_node.is_proposal_day is evidence.is_proposal_day
    for friday_close_run in (datetime(2026, 7, 10, 22, 30, tzinfo=UTC),       # summer: Fri 18:30 EDT = Sat 06:30 Beijing
                             datetime(2026, 11, 6, 23, 30, tzinfo=UTC)):       # winter: Fri 18:30 EST = Sat 07:30 Beijing
        assert propose_node.is_proposal_day(friday_close_run)
        assert not propose_node.is_proposal_day(friday_close_run - timedelta(days=1))
        assert not propose_node.is_proposal_day(friday_close_run + timedelta(days=1))


def test_skipped_when_flag_off(conn, scripted_model):
    seed_failure(conn)
    model = scripted_model([])
    out, ctx, acc = run_node(model, state=make_state(features=OFF), ctx=make_ctx(model))
    assert ctx.recorder.skipped == [('propose', 'feature_disabled')] and model.seen == []
    assert conn.execute('SELECT count(*) FROM agent_runs').fetchone()[0] == 0
    # off beats force: a switch that is off is never overridden by the day override
    out, ctx, acc = run_node(model, state=make_state(features=OFF, cutoff=WEDNESDAY, force_propose=True),
                             ctx=make_ctx(model, wall=WEDNESDAY))
    assert ctx.recorder.skipped == [('propose', 'feature_disabled')]


def test_no_evidence_success_without_model_call(conn, scripted_model):
    conn.commit()
    model = scripted_model([])
    out, ctx, acc = run_node(model)
    assert ctx.recorder.skipped == [] and ctx.recorder.finished == []          # a success, not a skip
    assert model.seen == [] and conn.execute('SELECT count(*) FROM agent_runs').fetchone()[0] == 0
    assert acc.detail['reason'] == 'no_evidence' and acc.detail['proposals'] == [] and acc.output_count == 0
    assert out['summary']['propose']['proposals'] == [] and out['summary']['propose']['reason'] == 'no_evidence'
    # an open proposal alone is no evidence either (it only deduplicates), and neither is "no counter opinions"
    optimize_repo.insert_proposal(conn, {'source': 'data', 'title': 'Existing', 'change': 'c', 'rationale': 'r',
                                         'evidence_refs': ['fact:data_failures#x']})
    conn.commit()
    out, ctx, acc = run_node(model)
    assert acc.detail['reason'] == 'no_evidence' and model.seen == []


# ------------------------------------------------------------------------------------------------ the happy path

def test_writes_validated_proposals_with_evidence_refs(conn, scripted_model):
    seed_failure(conn)
    optimize_repo.insert_roadmap_record(conn, 'roadmap_versions', {
        'version_id': 'v2.15', 'version': 1, 'title': 'roadmap', 'status': 'planned', 'created_at': WALL - timedelta(days=3)})
    optimize_repo.insert_roadmap_record(conn, 'roadmap_versions', {          # after the cutoff: invisible to the tool
        'version_id': 'v9.9', 'version': 1, 'title': 'future', 'status': 'planned', 'created_at': WALL + timedelta(days=3)})
    optimize_repo.insert_proposal(conn, {'source': 'data', 'title': 'Already open', 'change': 'c', 'rationale': 'r',
                                         'evidence_refs': ['fact:data_failures#x']})
    pack = pack_of(conn)
    ref_ok = ref(pack, 'data_failures')
    model = scripted_model([
        tool_round(),
        reply(draft('bars 超时重试', [ref_ok]),
              draft('引用不存在', ['fact:data_failures#nope']),           # unknown evidence row → dropped alone
              draft('already  open', [ref_ok]),                           # duplicate of an open title → dropped
              draft('第二条有效提案', [ref_ok], source='data'))])
    out, ctx, acc = run_node(model)
    assert ctx.recorder.skipped == [] and acc.failures == []
    saved = rows(conn, "SELECT title, source, status, evidence_refs, agent_run_id, run_id FROM roadmap_proposals "
                       "WHERE title <> 'Already open' ORDER BY id")
    assert [(r[0], r[1], r[2], r[3]) for r in saved] == [('bars 超时重试', 'data', 'proposed', [ref_ok]),
                                                          ('第二条有效提案', 'data', 'proposed', [ref_ok])]
    run_id = saved[0][4]
    assert saved[0][4] == saved[1][4] and saved[0][5] == 'r1'
    run = rows(conn, 'SELECT mode, agent_version, status, ticker, signal_date, plan_id, output FROM agent_runs '
                     'WHERE id = %s', run_id)[0]
    assert run[:6] == ('optimize', OPTIMIZE_AGENT_VERSION, 'ok', None, None, None)
    output = run[6]
    assert [p['title'] for p in output['proposals']] == ['bars 超时重试', '第二条有效提案']
    assert {d['reason'] for d in output['dropped']} == {'proposal_refs_invalid', 'proposal_duplicate_title'}
    # the pack the proposals were drafted from is frozen with the run, with the same ids the refs point at (UI1)
    assert output['evidence_pack']['data_failures'][0]['id'] == pack['data_failures'][0]['id']
    # the tool round went through the real binding: phase optimize, call:1, only records created before the cutoff
    [call] = [c for c in analysis_repo.agent_tool_calls(conn, run_id)]
    assert (call['phase'], call['tool'], call['seq']) == ('optimize', 'get_roadmap', 1)
    assert [i['version_id'] for i in call['result']['data']['items']] == ['v2.15']
    # the model saw the five fact blocks and nothing else of the database
    human = model.seen[0][1].content
    assert all(f'[fact:{key}]' in human for key in ('data_failures', 'agent_quality', 'retro_patterns', 'route',
                                                    'open_proposals')) and ref_ok.split('#')[1] in human
    assert acc.output_count == 2 and acc.input_count >= 1
    assert [{k: p[k] for k in ('title', 'source', 'status')} for p in acc.detail['proposals']] == [
        {'title': 'bars 超时重试', 'source': 'data', 'status': 'proposed'},
        {'title': '第二条有效提案', 'source': 'data', 'status': 'proposed'}]
    assert all(isinstance(p['id'], int) for p in acc.detail['proposals'])
    assert out['summary']['propose']['proposals'] == acc.detail['proposals']
    assert analysis_repo.agent_tokens_on(conn, END) == 30                  # both model rounds count into the daily tokens


def test_all_invalid_output_is_a_rejected_run_without_proposals(conn, scripted_model):
    seed_failure(conn)
    pack = pack_of(conn)
    model = scripted_model([reply(draft('t', ['fact:data_failures#nope']))])
    out, ctx, acc = run_node(model)
    run = rows(conn, "SELECT status, reason, output FROM agent_runs WHERE mode = 'optimize'")[0]
    assert run[:2] == ('rejected', 'proposal_refs_invalid') and run[2]['evidence_pack']['data_failures']
    assert conn.execute('SELECT count(*) FROM roadmap_proposals').fetchone()[0] == 0
    assert acc.output_count == 0 and [d['kind'] for d in acc.degradations] == ['propose_rejected']
    # malformed JSON is the same: one rejected run, nothing written, nothing raised
    out, ctx, acc = run_node(scripted_model([AIMessage(content='不是 JSON')]))
    assert rows(conn, "SELECT status, reason FROM agent_runs WHERE mode = 'optimize' ORDER BY created_at")[-1] == (
        'rejected', 'proposal_malformed')


def test_zero_proposals_is_a_valid_answer(conn, scripted_model):
    seed_failure(conn)
    out, ctx, acc = run_node(scripted_model([reply()]))
    assert rows(conn, "SELECT status FROM agent_runs WHERE mode = 'optimize'") == [('ok',)]
    assert acc.output_count == 0 and acc.detail['proposals'] == [] and acc.degradations == []


def test_model_error_is_recorded_not_raised(conn):
    class Boom:
        def bind_tools(self, *a, **k):
            return self

        def invoke(self, *a, **k):
            raise RuntimeError('model down')

    seed_failure(conn)
    out, ctx, acc = run_node(Boom())
    assert rows(conn, "SELECT status, reason FROM agent_runs WHERE mode = 'optimize'") == [('error', 'model_error')]
    assert [d['kind'] for d in acc.degradations] == ['propose_error'] and acc.output_count == 0
    assert ctx.recorder.finished == []                                        # the node itself did not fail


def test_unavailable_model_skips_the_node_with_the_connection_reason(conn, scripted_model):
    seed_failure(conn)
    off = AgentSettings('off', '', None, None, None)
    model = scripted_model([])
    out, ctx, acc = run_node(model, ctx=make_ctx(model, settings=off))
    assert ctx.recorder.skipped == [('propose', 'agent_unavailable: disabled')]    # tasks.daily._hit_not_connected reads this
    assert model.seen == [] and conn.execute("SELECT count(*) FROM agent_runs WHERE mode = 'optimize'").fetchone()[0] == 0


def test_failure_of_the_node_is_its_own_failed_row(conn, monkeypatch, scripted_model):
    seed_failure(conn)
    monkeypatch.setattr(optimize_proposals, 'run_propose', lambda *a, **k: (_ for _ in ()).throw(ValueError('pack boom')))
    out, ctx, acc = run_node(scripted_model([]))
    assert ctx.recorder.finished == [('propose', 'pack boom')]
    assert 'propose: pack boom' in out['summary']['errors']


# ------------------------------------------------------------------------------------------------ --force-propose

def test_force_propose_cli_flag(conn, scripted_model, monkeypatch):
    # the state carries the override: a Wednesday run proposes when forced
    seed_failure(conn, when=WEDNESDAY - timedelta(hours=3))
    pack = optimize_proposals.evidence_pack(conn, now=WEDNESDAY)
    model = scripted_model([reply(draft('周中强制提案', [ref(pack, 'data_failures')]))])
    out, ctx, acc = run_node(model, state=make_state(cutoff=WEDNESDAY, force_propose=True),
                             ctx=make_ctx(model, wall=WEDNESDAY))
    assert ctx.recorder.skipped == [] and acc.output_count == 1 and acc.detail['forced'] is True
    assert rows(conn, 'SELECT title FROM roadmap_proposals') == [('周中强制提案',)]

    # the initial state freezes it (a resume reuses it); the default is off
    assert workflow.initial_state(run_id='r', trigger='manual_batch', now=WALL, force_propose=True)['force_propose'] is True
    assert workflow.initial_state(run_id='r', trigger='manual_batch', now=WALL)['force_propose'] is False

    # the command line: ``run --force-propose`` reaches ``tasks.daily.run``; without it the override is off
    from investment_assistant.tasks import daily

    seen = []
    monkeypatch.setattr(daily, 'run', lambda *a, **k: seen.append(k) or {'run_id': 'x', 'status': 'success',
                                                                         'run_status': 'success'})
    assert daily.main(['run', '--manual', '--force-propose']) == 0
    assert daily.main(['run', '--manual']) == 0
    assert [k['force_propose'] for k in seen] == [True, False]


def test_force_propose_through_the_graph(daily_db, clock, notices):
    go(daily_db, clock, force_propose=True)                                    # the daily test run is not a Saturday
    row = effective(daily_db)['propose']
    # the daily world has its Agent off: with evidence the node is skipped for the connection, without it it succeeds
    assert row['reason'] != 'not_proposal_day' and row['status'] != 'failed'


def test_off_day_through_the_graph_is_skipped(daily_db, clock, notices):
    go(daily_db, clock)
    row = effective(daily_db)['propose']
    assert (row['status'], row['reason']) == ('skipped', 'not_proposal_day')
