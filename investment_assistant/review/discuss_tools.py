"""Read-only tools only the plan discussion may call (v2.7 spec §8.3, D20, D23).

``DiscussContext`` is the batch ``ToolContext`` plus what the host read before the run: the unit the
discussion is about and the ids of its plans and Agent runs. The three tools answer only for those
ids (anything else is ``ToolError``), run entirely through ``ctx.read`` (the host gives a read-only
transaction), call no outside source and write nothing.

Nothing here reads a lesson draft or a card's drafted narrative: a card's context is its frozen facts,
whether it was reviewed, the review note and the knowledge adopted from it (D11). A retro run is as
unreadable as a discussion run, because its output *is* that draft.

The host injects derivation and reasoning projections into ``DiscussContext`` so
these tools stay below the view layer.
"""
from __future__ import annotations
import investment_assistant.review.weekly as review_weekly
import investment_assistant.review.shadow as review_shadow
from investment_assistant.execution import chains as execution_chains
import investment_assistant.core.schema as core_schema
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import investment_assistant.execution.repo_legacy as execution_repo_legacy
import investment_assistant.review.repo as review_repo

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

from investment_assistant.core import knowledge as core_knowledge
from investment_assistant.core.constants import BENCHMARK
# Repository consumers use their owning layers below.
from investment_assistant.analysis.agent.tools import ToolContext, ToolError

OUT_OF_UNIT = '只能查本次讨论的计划与运行'
RESULT_CHARS = 2000                  # a get_agent_run view shows each tool-call result up to this many chars
UNREADABLE_MODES = ('discuss', 'retro')


@dataclass(frozen=True)
class DiscussContext(ToolContext):
    """``ticker`` / ``session`` / ``cutoff_at`` / ``read`` as in ``ToolContext`` (cutoff = the question's
    time); exactly one of ``plan_id`` (a plan discussion) and ``card_id`` (a card discussion) is set."""
    signal_date: date
    plan_id: str | None
    card_id: int | None
    unit_plan_ids: frozenset[str]
    unit_run_ids: frozenset[str]
    read_derivation: Callable[[Any, str], dict]
    read_agent_run: Callable[[Any, str], dict]


def _plain(value: Any) -> Any:
    """JSON-safe copy: dates as ISO strings, Decimal and UUID as strings."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (Decimal, uuid.UUID)):
        return str(value)
    return value


def _in_unit(allowed: frozenset[str], value: Any) -> str:
    if not isinstance(value, (str, uuid.UUID)) or str(value) not in {str(a) for a in allowed}:
        raise ToolError(OUT_OF_UNIT)
    return str(value)


# ---------------------------------------------------------------- get_plan_context

def _version_summary(version: dict, plan: dict | None) -> dict:
    levels = ({key: plan.get(key) for key in ('entry_low', 'entry_high', 'stop', 'target1')}
              if plan is not None else {})
    return {'version_no': version['version_no'], 'plan_id': version['plan_id'], 'origin': version['origin'],
            'cutoff_at': version['cutoff_at'], 'as_of_session': version['as_of_session'], 'levels': levels}


def _shadow(conn, ctx: DiscussContext, plan: dict) -> Any:
    try:
        bars = data_repo.read_bars(conn, ctx.ticker, ctx.session, cutoff_at=ctx.cutoff_at, limit=400)
        spy = data_repo.read_bars(conn, BENCHMARK, ctx.session, cutoff_at=ctx.cutoff_at, limit=400)
        splits = data_repo.split_events(conn, ctx.ticker)
        found = review_shadow.shadow_outcomes(plan, bars, spy, splits=splits)
        out = {'version': found['shadow_version'], **found['outcome']}
        if found['shadow_v1'] is not None:
            out['shadow_v1'] = found['shadow_v1']        # the counterfactual: no time stop, the old entry rule
        return out
    except Exception as exc:    # noqa: BLE001 - the context is still useful without the replay
        return {'error': (str(exc).splitlines() or [''])[0][:200] or type(exc).__name__}


def _execution(conn, ctx: DiscussContext, plan: dict, status: dict | None, fills: list[dict]) -> dict:
    """What happened to the discussed plan (v2.9 `execution-1`): its own state, the chain's state and what the first
    fill followed. Plan notes are the user's log and are deliberately not read here (spec D25)."""
    from investment_assistant.execution import timeline
    from investment_assistant.execution import adoption as execution

    buys = status['buys'] if status is not None else fills
    state = status['state']['state'] if status is not None else None
    ended = state is not None and state not in timeline.ACTIVE_STATES
    code = execution.status(plan, buys, session=ctx.session, chain_ended=ended, chain_in_entry=state in (None, 'entry'))
    plans_by_id, version_no = {}, {}
    for buy in buys:
        linked = execution_repo.plan_by_id(conn, buy['plan_id']) if buy.get('plan_id') is not None else None
        if linked is not None:
            plans_by_id[linked['id']] = linked
            found = execution_repo.version_by_plan(conn, linked['id'])
            if found is not None:
                version_no[linked['id']] = found['version_no']
    followed = execution.followed(buys, plans_by_id=plans_by_id, version_no_by_plan=version_no)
    out = {'status': code, 'text': execution.status_text(code, buys=buys, version_plan=plan), 'followed': followed}
    if status is not None:
        versions = [execution_repo.plan_by_id(conn, v['plan_id']) for v in status['versions']]
        chain_code = execution.chain_status(versions, buys, session=ctx.session, chain_ended=ended,
                                            chain_in_entry=state == 'entry')
        out['chain_status'] = chain_code
        out['chain_text'] = execution.EXECUTION_TEXT[chain_code]
    return out


def _plan_context(conn, ctx: DiscussContext) -> dict:
    plan_id = str(ctx.plan_id)
    plan = execution_repo.plan_by_id(conn, plan_id)
    if plan is None:
        raise ToolError('计划不存在')
    out: dict[str, Any] = {'kind': 'plan', 'ticker': ctx.ticker, 'signal_date': ctx.signal_date,
                           'cutoff_at': ctx.cutoff_at,
                           'plan': {k: v for k, v in plan.items() if k != 'inputs'}}
    ready = core_schema.timeline_ready(conn)
    version = execution_repo.version_by_plan(conn, plan_id) if ready else None
    run = data_repo.agent_run_by_id(conn, version['agent_run_id']) if version is not None else None
    output = (run or {}).get('output') or {}
    out['version'] = None if version is None else {
        k: version.get(k) for k in ('version_no', 'origin', 'cutoff_at', 'as_of_session', 'diff',
                                    'supersedes_plan_id', 'agent_run_id')}
    out['run'] = None if run is None else {
        k: run.get(k) for k in ('id', 'mode', 'agent_version', 'status', 'reason', 'cutoff_at', 'output', 'counter')}
    out['conditions'] = {'invalidation': output.get('invalidation') or [], 'review_when': output.get('review_when') or []}
    context = execution_chains.ticker_context(conn, ctx.ticker, now=ctx.cutoff_at)      # the fills as the cutoff knew them
    status = (execution_chains.chain_status(conn, ctx.ticker, ctx.signal_date, session=ctx.session, now=ctx.cutoff_at,
                                   context=context) if ready else None)
    if status is None:
        out.update(chain=None, versions=[], reviews=[])
    else:
        plans_by_id = {str(v['plan_id']): execution_repo.plan_by_id(conn, v['plan_id']) for v in status['versions']}
        out['chain'] = {**status['state'], 'head_plan_id': str(status['head']['plan']['id']),
                        'is_head': str(status['head']['plan']['id']) == plan_id}
        out['versions'] = [_version_summary(v, plans_by_id[str(v['plan_id'])]) for v in status['versions']]
        out['reviews'] = [{
            'id': r['id'], 'session': r['session'], 'trigger': r['trigger'], 'outcome': r['outcome'],
            'decided_by': r['decided_by'], 'reason': r['reason'], 'new_plan_id': r['new_plan_id'],
            'created_at': r['created_at'],
            'signals': [s for s in r.get('signals') or [] if isinstance(s, dict) and s.get('fired')]}
            for r in status['reviews']]
    fills = review_weekly.unit_fills(conn, ctx.ticker, ctx.signal_date, context)
    out['execution'] = _execution(conn, ctx, plan, status, fills)
    decision = execution_repo_legacy.decision_head(conn, ctx.ticker, ctx.signal_date)      # old data only (v2.9 retired decisions)
    out['legacy_decision'] = None if decision is None else {
        k: decision.get(k) for k in ('decision', 'final_action', 'reason', 'decided_at', 'plan_id')}
    out['trades'] = [{k: t.get(k) for k in ('id', 'side', 'quantity', 'price', 'fee', 'executed_at', 'voided_at',
                                            'plan_id')}
                     for t in fills]
    cited = review_repo.citations_for_plans(conn, [str(p) for p in ctx.unit_plan_ids]) if core_schema.growth_ready(conn) else []
    out['knowledge_refs'] = [{'ref': core_knowledge.ref(c['knowledge_id'], c['version']), 'field': c['field'],
                              'plan_id': c['plan_id']} for c in cited]
    out['shadow'] = _shadow(conn, ctx, plan)
    return _plain(out)


def _without_note_bodies(facts: dict) -> dict:
    """A retro-2 card froze the notes you wrote (`execution.notes`); a note is a log for yourself and never reaches an
    Agent (v2.9 D25), so the discussion gets how many there were and when, not what they said."""
    execution = facts.get('execution')
    if not isinstance(execution, dict) or not execution.get('notes'):
        return facts
    notes = [{'id': n.get('id'), 'created_at': n.get('created_at')} for n in execution['notes'] if isinstance(n, dict)]
    return {**facts, 'execution': {**execution, 'notes': notes, 'notes_hidden': '备注只给你自己看，不提供给 Agent'}}


def _card_context(conn, ctx: DiscussContext) -> dict:
    from investment_assistant.review import retro

    card = review_repo.review_card(conn, ctx.card_id)
    if card is None:
        raise ToolError('卡不存在')
    verdict = review_repo.card_verdict(conn, card['id'])
    adopted: list[dict] = []
    versions, statuses = data_repo.knowledge_rows(conn, cutoff=ctx.cutoff_at)
    for row in core_knowledge.visible(versions, statuses, cutoff=ctx.cutoff_at):
        item = review_repo.knowledge_item(conn, row['knowledge_id'])
        if item is not None and item['source_card_id'] == card['id']:
            adopted.append({'ref': core_knowledge.ref(row['knowledge_id'], 1), 'title': row['title'],
                            'current_version': row['version']})
    facts = _without_note_bodies(card['facts'] or {})
    basis = (facts.get('subject') or {}).get('exit_basis')
    return _plain({
        'kind': 'card', 'ticker': card['ticker'], 'signal_date': card['signal_date'], 'cutoff_at': ctx.cutoff_at,
        'card': {'id': card['id'], 'event_key': card['event_key'], 'subject': card['subject'],
                 'end_kind': card['end_kind'], 'end_text': retro.end_text(card['end_kind'], basis),
                 'end_session': card['end_session'], 'created_at': card['created_at']},
        'facts': facts, 'status': 'reviewed' if verdict is not None else 'not_reviewed',
        'verdict': None if verdict is None else {'note': verdict['note'], 'created_at': verdict['created_at']},
        'adopted_knowledge': adopted})


def get_plan_context(ctx: DiscussContext) -> dict:
    if ctx.card_id is not None:
        return ctx.read(lambda conn: _card_context(conn, ctx))
    if ctx.plan_id is not None:
        _in_unit(ctx.unit_plan_ids, ctx.plan_id)          # the host puts the discussed plan in the unit; still checked
        return ctx.read(lambda conn: _plan_context(conn, ctx))
    raise ToolError('本次讨论没有计划或卡的上下文')


# ---------------------------------------------------------------- get_derivation

def get_derivation(ctx: DiscussContext, plan_id: str) -> dict:
    key = _in_unit(ctx.unit_plan_ids, plan_id)

    def read(conn):
        return ctx.read_derivation(conn, key)

    result = ctx.read(read)
    if result is None:
        raise ToolError('计划不存在')
    return _plain(result)


# ---------------------------------------------------------------- get_agent_run

def _clip(call: dict) -> dict:
    """A read-step call with its result cut to ``RESULT_CHARS`` characters of JSON."""
    text = json.dumps(call.get('result'), ensure_ascii=False, default=str)
    if len(text) <= RESULT_CHARS:
        return call
    return {**call, 'result': text[:RESULT_CHARS] + '…', 'result_truncated': True}


def get_agent_run(ctx: DiscussContext, run_id: str) -> dict:
    key = _in_unit(ctx.unit_run_ids, run_id)

    def read(conn):
        return ctx.read_agent_run(conn, key)

    try:
        view = ctx.read(read)
    except LookupError:
        raise ToolError('运行不存在') from None
    if view['run']['mode'] in UNREADABLE_MODES:
        raise ToolError(OUT_OF_UNIT)
    for step in view['steps']:
        if step.get('kind') == 'read':
            step['calls'] = [_clip(c) for c in step.get('calls') or []]
    return view


DISCUSS_TOOLS: dict[str, Callable] = {
    'get_plan_context': get_plan_context,
    'get_derivation': get_derivation,
    'get_agent_run': get_agent_run,
}

DISCUSS_DESCRIPTIONS: dict[str, str] = {
    'get_plan_context': '读本次讨论对象的上下文：计划（该版本的价位、生成运行的输出与反证、条件、全部版本、复查、'
                        '决定与成交、引用的知识、影子结果）或复盘卡（冻结事实、是否已审定、采纳的知识）',
    'get_derivation': '读本次讨论的单元内某个计划 id 的推导：每个价位怎么由输入算出、代入了哪些值、与存档是否一致',
    'get_agent_run': '读本次讨论的单元内某次 Agent 运行的推理步骤（工具调用的结果每条截断到 2000 字）；'
                     '不含讨论运行本身',
}
