"""The three discussion-only tools (v2.7 spec §8.3): unit boundary, shapes, no drafts, truncation, read-only.

Offline: the boundary and the table. SQL (explicit test database): the shapes over a real chain and card."""
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.execution.repo as execution_repo
import investment_assistant.execution.repo_legacy as execution_repo_legacy
import investment_assistant.review.repo as review_repo
import dataclasses
import json
import uuid
from datetime import UTC, date, datetime

import psycopg
import pytest

import investment_assistant.core.tx as service
import investment_assistant.views.derivation as derivation_view
import investment_assistant.views.reasoning as reasoning
import investment_assistant.workflow.batch as d_workflow_batch
from investment_assistant.review import discuss_tools
from investment_assistant.review.discuss_tools import (DISCUSS_DESCRIPTIONS, DISCUSS_TOOLS, DiscussContext,
                                                                get_agent_run, get_derivation, get_plan_context)
from investment_assistant.analysis.agent.tools import TOOLS, Recorder, ToolContext, ToolError, call

S = date(2026, 10, 22)
CUTOFF = datetime(2026, 10, 22, 22, 30, tzinfo=UTC)
OUT = '只能查本次讨论的计划与运行'


def boom_read(fn):
    raise AssertionError('the boundary check must come before any read')


def offline_ctx(**over):
    base = dict(ticker='AAA', session=S, cutoff_at=CUTOFF, read=boom_read, signal_date=S, plan_id='p-1', card_id=None,
                unit_plan_ids=frozenset({'p-1', 'p-2'}), unit_run_ids=frozenset({'r-1'}),
                read_derivation=derivation_view.plan_derivation_in, read_agent_run=reasoning.view_in)
    return DiscussContext(**{**base, **over})


def test_tables():
    assert list(DISCUSS_TOOLS) == ['get_plan_context', 'get_derivation', 'get_agent_run']
    assert set(DISCUSS_DESCRIPTIONS) == set(DISCUSS_TOOLS)
    assert not set(DISCUSS_TOOLS) & set(TOOLS)                       # the batch tools never include them
    assert len(TOOLS | DISCUSS_TOOLS) == 13


def test_context_is_a_frozen_tool_context():
    ctx = offline_ctx()
    assert isinstance(ctx, ToolContext) and dataclasses.is_dataclass(ctx)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.plan_id = 'p-2'
    assert ctx.unit_plan_ids == frozenset({'p-1', 'p-2'})


def test_discuss_tools_reject_out_of_unit():
    ctx = offline_ctx()
    rec = Recorder(phase='discuss')
    table = TOOLS | DISCUSS_TOOLS
    other_plan = str(uuid.uuid4())
    for name, args in (('get_derivation', {'plan_id': other_plan}), ('get_derivation', {'plan_id': 'p-9'}),
                       ('get_agent_run', {'run_id': str(uuid.uuid4())}), ('get_agent_run', {'run_id': 'r-2'})):
        env = call(ctx, rec, name, args, now=CUTOFF, tools=table)
        assert env['error'] == OUT, (name, args)
    with pytest.raises(ToolError, match=OUT):
        get_derivation(ctx, 'nope')
    with pytest.raises(ToolError, match=OUT):
        get_agent_run(ctx, 5)                                          # type confusion is out of unit as well
    assert [c['tool'] for c in rec.calls] == ['get_derivation', 'get_derivation', 'get_agent_run', 'get_agent_run']
    assert all(c['result'].get('error') == OUT for c in rec.calls)      # recorded as envelopes, never raised


def test_get_plan_context_needs_a_plan_or_a_card():
    with pytest.raises(ToolError, match='没有计划或卡'):
        get_plan_context(offline_ctx(plan_id=None, card_id=None))
    with pytest.raises(ToolError, match=OUT):
        get_plan_context(offline_ctx(plan_id='p-9'))                   # a plan outside the unit


def test_clip_truncates_long_results_only():
    short = {'seq': 1, 'result': {'data': 'x' * 100}}
    assert discuss_tools._clip(short) is short
    long = {'seq': 2, 'result': {'data': ['字' * 100] * 40}}
    clipped = discuss_tools._clip(long)
    assert clipped['result_truncated'] is True and isinstance(clipped['result'], str)
    assert len(clipped['result']) == discuss_tools.RESULT_CHARS + 1 and clipped['result'].endswith('…')
    assert clipped['seq'] == 2


# ---------------------------------------------------------------- SQL

from test_workbench_repository_growth import card as make_card, item_with_versions   # noqa: E402
from test_workbench_service import NOW, SESSION, _ok_responses, agent_deps, db   # noqa: E402,F401


def run_chain(db, scripted_model, ticker='AAA'):
    d_workflow_batch.run_batch(tickers=[ticker], now=NOW, deps=agent_deps(scripted_model, _ok_responses(ticker)))
    db.commit()
    return execution_repo.agent_plan(db, ticker, SESSION), analysis_repo.latest_agent_run(db, ticker, SESSION)


def make_ctx(db, *, ticker='AAA', plan_id=None, card_id=None, extra_runs=()):
    plans = execution_repo.plans_for_unit(db, ticker, SESSION)
    runs = [r for mode in ('propose', 'revise', 'review', 'interpret', 'retro', 'discuss')
            for r in analysis_repo.agent_runs_for(db, ticker, SESSION, mode)]
    return DiscussContext(
        ticker=ticker, session=SESSION, cutoff_at=NOW, read=lambda fn: fn(db), signal_date=SESSION,
        plan_id=str(plan_id) if plan_id else None, card_id=card_id,
        unit_plan_ids=frozenset(str(p['id']) for p in plans),
        unit_run_ids=frozenset([str(r['id']) for r in runs] + [str(r) for r in extra_runs]),
        read_derivation=derivation_view.plan_derivation_in, read_agent_run=reasoning.view_in)


def insert_run(db, *, mode='propose', status='rejected', reason='malformed', output=None, calls=(), ticker='AAA'):
    run_id = str(uuid.uuid4())
    analysis_repo.insert_agent_run(db, {
        'id': run_id, 'ticker': ticker, 'signal_date': SESSION, 'pool': 'main', 'attempt': 1, 'mode': mode,
        'agent_version': 'agent-2', 'provider': 'chatgpt', 'model': 'm', 'cutoff_at': NOW, 'status': status,
        'reason': reason, 'detail': None, 'output': output, 'counter': None, 'plan_id': None, 'input_tokens': 0,
        'output_tokens': 0, 'started_at': NOW, 'finished_at': NOW}, list(calls))
    return run_id


def test_plan_context_shape(db, scripted_model):
    plan, run = run_chain(db, scripted_model)
    ctx = make_ctx(db, plan_id=plan['id'])
    item = item_with_versions(db, [(datetime(2026, 10, 1, tzinfo=UTC), ['AAA'], [])])
    analysis_repo.insert_knowledge_citations(db, [{'agent_run_id': run['id'], 'plan_id': plan['id'], 'review_id': None,
                                                'knowledge_id': item, 'version': 1, 'field': 'thesis', 'call_seq': 1}])
    data = get_plan_context(ctx)
    json.dumps(data, ensure_ascii=False)                                  # JSON all the way down
    assert data['kind'] == 'plan' and data['ticker'] == 'AAA' and data['signal_date'] == SESSION.isoformat()
    assert data['plan']['id'] == str(plan['id']) and data['plan']['kind'] == 'agent' and 'inputs' not in data['plan']
    assert data['plan']['stop'] == str(plan['stop']) and data['plan']['status'] == plan['status']
    assert data['version']['version_no'] == 0 and data['version']['origin'] == 'signal'
    assert data['run']['id'] == str(run['id']) and data['run']['output']['action'] == 'buy'
    assert data['run']['counter']['counter'][0]['text'] == '财报临近'
    assert [c['kind'] for c in data['conditions']['invalidation']] == ['close_below']
    assert data['conditions']['review_when'] and data['chain']['is_head'] is True
    assert data['chain']['head_plan_id'] == str(plan['id']) and data['chain']['state']
    [only] = data['versions']
    assert only['version_no'] == 0 and set(only['levels']) == {'entry_low', 'entry_high', 'stop', 'target1'}
    assert data['reviews'] == [] and data['legacy_decision'] is None and data['trades'] == [] and 'decision' not in data
    assert data['execution']['status'] == 'pending' and data['execution']['followed'] is None
    assert data['knowledge_refs'] == [{'ref': f'know:{item}@1', 'field': 'thesis', 'plan_id': str(plan['id'])}]
    assert data['shadow']['version'] == 'plan-shadow-2' and 'error' not in data['shadow']      # the plan has execution fields
    assert data['shadow']['shadow_v1']['status'] in ('expired', 'pending', 'open', 'closed')          # the counterfactual
    assert data['cutoff_at'] == NOW.isoformat()


def test_plan_context_includes_reviews_decision_and_trades(db, scripted_model):
    plan, _ = run_chain(db, scripted_model)
    review = execution_repo.insert_plan_review(db, {
        'ticker': 'AAA', 'signal_date': SESSION, 'head_plan_id': plan['id'], 'session': SESSION, 'trigger': 'batch',
        'run_id': 'run-x', 'mode': 'entry', 'timeline_version': 'timeline-1',
        'signals': [{'kind': 'new_8k', 'fired': True, 'detail': 'x'}, {'kind': 'abnormal_move', 'fired': False}],
        'conditions': [], 'user_input_ids': [], 'outcome': 'maintain', 'decided_by': 'host', 'reason': '维持',
        'refs': [], 'detail': {}, 'attempt': 1, 'cutoff_at': NOW})
    dec = execution_repo_legacy._insert_decision_for_tests(db, {
        'request_id': uuid.uuid4(), 'ticker': 'AAA', 'signal_date': SESSION, 'candidate_ids': [uuid.uuid4()],
        'brief_id': None, 'proposed_action': 'buy', 'final_action': 'buy', 'decision': 'accept', 'reason': '照做',
        'supersedes_id': None, 'plan_id': plan['id']}, 'h', decided_at=NOW)
    execution_repo.insert_trade(db, {'request_id': uuid.uuid4(), 'ticker': 'AAA', 'side': 'buy', 'quantity': 10,
                                 'price': 100, 'fee': 0, 'executed_at': NOW, 'decision_id': dec['id'], 'note': None},
                            'h', recorded_at=NOW)
    db.commit()
    data = get_plan_context(make_ctx(db, plan_id=plan['id']))
    [r] = data['reviews']
    assert r['id'] == review['id'] and r['outcome'] == 'maintain' and r['reason'] == '维持'
    assert [s['kind'] for s in r['signals']] == ['new_8k']                 # only the fired signals
    assert data['legacy_decision']['decision'] == 'accept' and data['legacy_decision']['reason'] == '照做'      # old data
    assert [t['side'] for t in data['trades']] == ['buy']


def test_plan_context_has_execution(db, scripted_model):
    """v2.9: the discussion context carries what happened to the plan from the fills (`execution-1`), the old decision
    under `legacy_decision`; a plan note of yours is never in it (D25)."""
    from datetime import timedelta

    plan, _ = run_chain(db, scripted_model)
    fill_at = datetime(2026, 10, 23, 14, 0, tzinfo=UTC)
    execution_repo.insert_trade(db, {'request_id': uuid.uuid4(), 'ticker': 'AAA', 'side': 'buy', 'quantity': 10,
                                 'price': 100, 'fee': 0, 'executed_at': fill_at, 'decision_id': None,
                                 'plan_id': plan['id'], 'note': None}, 'h', recorded_at=fill_at + timedelta(hours=6))
    db.execute('INSERT INTO plan_notes (request_id, request_hash, ticker, chain_signal_date, kind, body) '
               "VALUES (%s, 'h', 'AAA', %s, 'note', '只给自己看的备注')", [uuid.uuid4(), SESSION])
    db.commit()
    ctx = dataclasses.replace(make_ctx(db, plan_id=plan['id']), cutoff_at=fill_at + timedelta(days=1))
    data = get_plan_context(ctx)
    assert data['execution']['status'] == 'adopted' and data['execution']['text'] == '已采纳（2026-10-23 成交 10 股）'
    assert data['execution']['followed']['text'] == 'Agent 第 0 版' and data['execution']['chain_status'] == 'executed'
    assert data['execution']['chain_text'] == '已执行'
    assert [t['plan_id'] for t in data['trades']] == [str(plan['id'])] and data['legacy_decision'] is None
    assert '只给自己看的备注' not in json.dumps(data, ensure_ascii=False)


def test_card_context_hides_note_bodies(db, scripted_model):
    """D25: a retro-2 card froze the notes you wrote; the discussion tool hands the Agent their count, never the text."""
    plan, _ = run_chain(db, scripted_model)
    secret = '只给自己看的备注原文'
    cid = make_card(db, key='unit:AAA:2026-10-22:closed:x', ticker='AAA', signal_date=SESSION, end_kind='position_closed',
                    end_session=SESSION,
                    facts={'subject': {'kind': 'unit', 'exit_basis': None},
                           'execution': {'status': 'adopted', 'text': '采纳（照规则基线）',
                                         'notes': [{'id': 7, 'body': secret, 'created_at': '2026-10-22T10:00:00Z'}]}})
    db.commit()
    data = get_plan_context(dataclasses.replace(make_ctx(db, card_id=cid), plan_id=None))
    text = json.dumps(data, ensure_ascii=False)
    assert secret not in text
    assert data['facts']['execution']['notes'] == [{'id': 7, 'created_at': '2026-10-22T10:00:00Z'}]
    assert data['facts']['execution']['text'] == '采纳（照规则基线）'
    assert plan is not None


def test_card_context_excludes_drafts(db, scripted_model):
    plan, _ = run_chain(db, scripted_model)
    cid = make_card(db, key='unit:AAA:2026-10-22:ended', ticker='AAA', signal_date=SESSION, end_kind='stop',
                    end_session=SESSION, facts={'subject': {'kind': 'unit', 'exit_basis': None}, 'plans': [{'key': 'plan:x'}]})
    retro_run = insert_run(db, mode='retro', status='ok', reason=None,
                           output={'headline': '叙述标题XYZ', 'lessons': [{'title': '教训标题QRS'}]})
    draft = review_repo.insert_card_draft(db, {
        'card_id': cid, 'attempt': 1, 'trigger': 'batch', 'agent_run_id': retro_run, 'status': 'ok', 'reason': None,
        'output': {'headline': '叙述标题XYZ', 'lessons': [{'title': '教训标题QRS'}]}})
    lesson_draft = review_repo.insert_knowledge_draft(db, {
        'origin': 'review_card', 'card_draft_id': draft, 'lesson_index': 0, 'discussion_turn_id': None,
        'target_knowledge_id': None, 'target_version': None, 'title': '教训标题QRS', 'body': '教训正文QRS',
        'scope': 's', 'counter_examples': 'c', 'concepts': [], 'tickers': ['AAA'], 'refs': ['fact:plans']})
    other = make_card(db, key='unit:AAA:2026-10-22:other', ticker='AAA', signal_date=SESSION)
    adopted = review_repo.insert_knowledge_item(db, {'origin': 'review_card', 'source_card_id': cid})
    version = review_repo.insert_knowledge_version(db, {
        'knowledge_id': adopted, 'version': 1, 'supersedes_id': None, 'request_id': uuid.uuid4(), 'request_hash': 'h',
        'draft_id': lesson_draft, 'title': '已采纳的经验', 'body': 'b', 'scope': 's', 'counter_examples': 'c',
        'concepts': [], 'tickers': ['AAA'], 'schema_version': 'my-knowledge-1'})
    db.execute('UPDATE knowledge_versions SET created_at = %s WHERE id = %s', (datetime(2026, 10, 22, 20, tzinfo=UTC),
                                                                           version))
    elsewhere = review_repo.insert_knowledge_item(db, {'origin': 'review_card', 'source_card_id': other})
    review_repo.insert_knowledge_version(db, {
        'knowledge_id': elsewhere, 'version': 1, 'supersedes_id': None, 'request_id': uuid.uuid4(),
        'request_hash': 'h', 'draft_id': None, 'title': '别的卡的经验', 'body': 'b', 'scope': 's',
        'counter_examples': 'c', 'concepts': [], 'tickers': ['AAA'], 'schema_version': 'my-knowledge-1'})
    db.execute('UPDATE knowledge_versions SET created_at = %s WHERE knowledge_id = %s',
               (datetime(2026, 10, 22, 20, tzinfo=UTC), elsewhere))
    ctx = make_ctx(db, card_id=cid)
    unreviewed = get_plan_context(ctx)
    assert unreviewed['status'] == 'not_reviewed' and unreviewed['verdict'] is None
    review_repo.insert_card_verdict(db, {'card_id': cid, 'request_id': uuid.uuid4(), 'request_hash': 'h',
                                        'draft_id': draft, 'note': '我的复盘说明'})
    data = get_plan_context(ctx)
    text = json.dumps(data, ensure_ascii=False)
    for marker in ('叙述标题XYZ', '教训标题QRS', '教训正文QRS'):
        assert marker not in text, marker
    for key in ('output', 'lessons', 'knowledge_drafts', 'drafts', 'lesson_drafts'):
        assert key not in data and f'"{key}"' not in text, key
    assert data['kind'] == 'card' and data['status'] == 'reviewed' and data['verdict']['note'] == '我的复盘说明'
    assert data['card']['id'] == cid and data['card']['end_kind'] == 'stop' and data['card']['end_text'] == '止损'
    assert data['facts']['plans'] == [{'key': 'plan:x'}]
    assert data['adopted_knowledge'] == [{'ref': f'know:{adopted}@1', 'title': '已采纳的经验', 'current_version': 1}]
    assert str(plan['id']) in ctx.unit_plan_ids


def test_get_derivation(db, scripted_model):
    plan, _ = run_chain(db, scripted_model)
    ctx = make_ctx(db, plan_id=plan['id'])
    data = get_derivation(ctx, str(plan['id']))
    json.dumps(data, ensure_ascii=False)
    assert data['consistency']['status'] == 'consistent' and data['trigger_note']
    assert 'run' in data
    with pytest.raises(ToolError, match=OUT):
        get_derivation(ctx, str(uuid.uuid4()))
    missing = str(uuid.uuid4())
    ghost = dataclasses.replace(ctx, unit_plan_ids=ctx.unit_plan_ids | {missing})
    with pytest.raises(ToolError, match='计划不存在'):
        get_derivation(ghost, missing)


def test_get_agent_run_view_and_boundary(db, scripted_model):
    plan, run = run_chain(db, scripted_model)
    ctx = make_ctx(db, plan_id=plan['id'])
    view = get_agent_run(ctx, str(run['id']))
    json.dumps(view, ensure_ascii=False)
    assert view['run']['id'] == str(run['id']) and view['run']['mode'] == 'propose'
    kinds = [s['kind'] for s in view['steps']]
    assert kinds[:2] == ['previous_chain', 'research'] and 'proposal' in kinds[2:]               # v2.9 D9; agent-4 research
    retro_run = insert_run(db, mode='retro', status='ok', reason=None, output={'headline': 'x'})
    discuss_run = insert_run(db, mode='discuss', status='ok', reason=None, output={'answer': 'x'})
    ctx = make_ctx(db, plan_id=plan['id'])
    assert {retro_run, discuss_run} <= ctx.unit_run_ids
    for run_id in (retro_run, discuss_run):                               # the draft run and the discussion's own
        with pytest.raises(ToolError, match=OUT):
            get_agent_run(ctx, run_id)
    ghost = str(uuid.uuid4())
    with pytest.raises(ToolError, match='运行不存在'):
        get_agent_run(dataclasses.replace(ctx, unit_run_ids=ctx.unit_run_ids | {ghost}), ghost)
    with pytest.raises(ToolError, match=OUT):
        get_agent_run(ctx, str(uuid.uuid4()))


def test_get_agent_run_truncates(db, scripted_model):
    plan, _ = run_chain(db, scripted_model)
    big = [{'date': f'2026-01-{i % 28 + 1:02d}', 'note': '字' * 40} for i in range(200)]
    small = {'last_close': 100.0}
    calls = [{'seq': 1, 'phase': 'propose', 'tool': 'get_price_bars', 'args': {'ticker': 'AAA'},
              'result': {'ref': 'call:1', 'data': big}, 'result_sha256': 'a' * 64, 'called_at': NOW},
             {'seq': 2, 'phase': 'propose', 'tool': 'get_indicators', 'args': {'ticker': 'AAA'},
              'result': {'ref': 'call:2', 'data': small}, 'result_sha256': 'b' * 64, 'called_at': NOW}]
    run_id = insert_run(db, calls=calls)
    view = get_agent_run(make_ctx(db, plan_id=plan['id']), run_id)
    read = next(s for s in view['steps'] if s['kind'] == 'read')
    first, second = read['calls']
    assert first['result_truncated'] is True and isinstance(first['result'], str)
    assert len(first['result']) == discuss_tools.RESULT_CHARS + 1
    assert second['result'] == small and 'result_truncated' not in second       # short results are left alone
    assert discuss_tools.RESULT_CHARS == 2000
    # only the tool's own copy is cut: the stored call is unchanged
    assert len(json.dumps(analysis_repo.agent_tool_calls(db, run_id)[0]['result'], ensure_ascii=False)) > 2000


def test_discuss_tools_read_only(db, scripted_model):
    plan, run = run_chain(db, scripted_model)
    cid = make_card(db, key='unit:AAA:2026-10-22:ended', ticker='AAA', signal_date=SESSION)
    plan_ctx = make_ctx(db, plan_id=plan['id'])
    card_ctx = make_ctx(db, card_id=cid)
    tables = ('agent_runs', 'agent_tool_calls', 'trade_plans', 'plan_reviews', 'review_cards', 'knowledge_citations',
              'knowledge_versions', 'signal_decisions', 'trades', 'price_bars', 'agent_plan_versions')

    def counts():
        return [db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in tables]

    db.commit()
    before = counts()
    db.execute('SET TRANSACTION READ ONLY')                                # the host's read transaction
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):             # a write would fail here
        with db.transaction():
            db.execute("INSERT INTO knowledge_items (origin) VALUES ('manual')")
    get_plan_context(plan_ctx)
    get_plan_context(card_ctx)
    get_derivation(plan_ctx, str(plan['id']))
    get_agent_run(plan_ctx, str(run['id']))
    assert counts() == before
    db.rollback()
