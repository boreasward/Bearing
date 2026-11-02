"""Plan and card discussions (v2.7 spec §8): create, ask (synchronous checks + your turn), answer (background body),
read.

The host writes only four kinds of rows for a discussion (D23): your turn and the answer (``discussion_turns``), the
answering run and its tool calls (``agent_runs`` / ``agent_tool_calls``) and, when the answer carries a lesson draft
that passed its checks, one ``knowledge_drafts`` row. The model has no write tool; every discussion tool reads
through a real read-only transaction (``core_tx.read_only_tx``), never ``core_tx._read_only``.

Waiting and interrupted turns (§8.2.4) are decided when a turn is read: a turn without an answer whose background
run (``runner.get``) is still ``pending`` is waiting; no run record (the service restarted) or an ``error`` run
means interrupted, and an interrupted turn does not make the discussion busy. The turn's ``run_id`` column holds the
Agent run id the answer will carry (the reasoning view finds the discussion through it); the background runner id is
kept in process memory next to it (``track``), which is exactly as durable as ``runner`` itself.
"""
from __future__ import annotations
import investment_assistant.data.repo as data_repo
import investment_assistant.review.agent_runs as review_agent_runs
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.core.dbutil as core_dbutil
import investment_assistant.core.schema as core_schema
import investment_assistant.execution.repo as execution_repo
import investment_assistant.review.repo as review_repo

import os
import threading
import uuid
from contextlib import ExitStack
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from investment_assistant.core import runner
from investment_assistant.review.knowledge import items as knowledge_items
from investment_assistant.review.knowledge import my_knowledge
from investment_assistant.execution import ledger
from investment_assistant.execution import user_inputs
from investment_assistant.data.adapters import bars as bars_module
from investment_assistant.core import knowledge
from investment_assistant.analysis.agent import graph as agent_graph
from investment_assistant.core.llm import model as agent_model
from investment_assistant.review.discuss_tools import DISCUSS_TOOLS, DiscussContext
from investment_assistant.core.constants import BENCHMARK, AGENT_DEADLINE_S
from investment_assistant.core import tx as core_tx

NY = ZoneInfo('America/New_York')
MIGRATION_MISSING = '迁移 027 未执行'
DISCUSS_HISTORY_TURNS = 10            # the last ten turn rows (questions and answers) go to the prompt (§8.4)
DISCUSS_HISTORY_CHARS = 12000         # ... at most this many characters, oldest dropped first
BODY_MAX = 4000
UNIT_RUN_MODES = ('propose', 'interpret', 'review', 'revise')       # runs of the unit a discussion may read back

_LOCK = threading.Lock()
_RUNNERS: dict[str, str] = {}         # turn run_id (Agent run uuid) -> background runner id; '' while submitting


def user_input_request_id(turn_request_id: str) -> str:
    """The ``user_inputs.request_id`` of 「存为补充信息」 for a turn (spec §11 补充说明), the same algorithm as the
    front end's ``userInputRequestId``: ``uuid5(NAMESPACE_URL, 'discussion-turn:' + <turn request id>)``."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'discussion-turn:' + str(turn_request_id)))


def track(run_key: str, runner_id: str) -> None:
    """Remember which background run answers the turn whose ``run_id`` is ``run_key``; ignored once the turn is
    answered and forgotten."""
    with _LOCK:
        if run_key in _RUNNERS:
            _RUNNERS[run_key] = runner_id


def release(run_key: str | None) -> None:
    """Forget a turn's background run (it finished, or could not be started)."""
    if run_key is not None:
        with _LOCK:
            _RUNNERS.pop(run_key, None)



def _require_ready(conn) -> None:
    if not core_schema.growth_ready(conn):
        raise core_tx.Unavailable(MIGRATION_MISSING)


def _read_ro(fn):
    with core_tx.read_only_tx() as conn:
        return fn(conn)


def _uuid(value: Any, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ValueError(f'{field} must be a UUID') from None


# ---------------------------------------------------------------- turn state

def wait_state(turn: dict[str, Any]) -> str:
    """``pending`` or ``interrupted`` for a user turn that has no answer (spec §8.2 step 4)."""
    key = turn.get('run_id')
    with _LOCK:
        runner_id = _RUNNERS.get(key) if key is not None else None
        known = key in _RUNNERS
    if not known:
        return 'interrupted'
    if runner_id == '':
        return 'pending'                          # submitted a moment ago: the runner id is not registered yet
    record = runner.get(runner_id)
    if record is None or record.get('status') in ('error', 'done'):
        return 'interrupted'
    return 'pending'


def _replies(turns: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {t['reply_to']: t for t in turns if t['role'] == 'assistant' and t['reply_to'] is not None}


def _unanswered(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    answered = _replies(turns)
    return [t for t in turns if t['role'] == 'user' and t['id'] not in answered]


# ---------------------------------------------------------------- create

def _discussion_by_request(conn, request_id: uuid.UUID) -> dict[str, Any] | None:
    row = conn.execute('SELECT id FROM discussions WHERE request_id = %s', (request_id,)).fetchone()
    return review_repo.discussion(conn, row[0]) if row else None


def _trade_day(conn, card: dict[str, Any]) -> date:
    buy = next((t for t in execution_repo.list_trades(conn, card['ticker']) if t['id'] == card['buy_trade_id']), None)
    return buy['executed_at'].astimezone(NY).date() if buy else card['end_session']


def _list_item(conn, discussion: dict[str, Any]) -> dict[str, Any]:
    turns = review_repo.turns(conn, discussion['id'])
    questions = [t for t in turns if t['role'] == 'user']
    return {'id': discussion['id'], 'created_at': discussion['created_at'], 'turn_count': len(questions),
            'first_question': questions[0]['body'] if questions else None}


def _discussion_view(discussion: dict[str, Any]) -> dict[str, Any]:
    return {'id': discussion['id'], 'context_kind': discussion['context_kind'], 'ticker': discussion['ticker'],
            'signal_date': discussion['signal_date'],
            'plan_id': str(discussion['plan_id']) if discussion['plan_id'] else None,
            'card_id': discussion['card_id'], 'created_at': discussion['created_at']}


def create(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """Open a discussion on a plan version or a review card (spec §8.1). ``payload = {request_id, context:
    {kind: 'plan', plan_id} | {kind: 'card', card_id}}``. ``{'status': 'created' | 'replayed', 'record': …}``;
    ValueError (400) for a bad body, LookupError (404) for an unknown plan or card, ``ledger.Conflict``
    ``request_id_conflict`` (409) for a reused id with other content, Unavailable (503) without migration 027."""
    if not isinstance(payload, dict):
        raise ValueError('body must be a JSON object')
    request_id = _uuid(payload.get('request_id'), 'request_id')
    context = payload.get('context')
    if not isinstance(context, dict) or context.get('kind') not in ('plan', 'card'):
        raise ValueError("context.kind 必须是 plan 或 card")
    if context['kind'] == 'plan':
        plan_id, card_id = _uuid(context.get('plan_id'), 'context.plan_id'), None
    else:
        plan_id = None
        card_id = context.get('card_id')
        if isinstance(card_id, bool) or not isinstance(card_id, int) or card_id < 1:
            raise ValueError('context.card_id 必须是卡 id')
    digest = ledger.request_hash({'context_kind': context['kind'], 'plan_id': plan_id, 'card_id': card_id})
    with core_tx._tx() as conn:
        _require_ready(conn)
        core_dbutil.lock(conn, f'discussion-new:{request_id}')
        existing = _discussion_by_request(conn, request_id)
        if existing is not None:
            if existing['request_hash'] != digest:
                raise ledger.Conflict('request_id_conflict')
            return {'status': 'replayed', 'record': _discussion_view(existing)}
        if plan_id is not None:
            plan = execution_repo.plan_by_id(conn, plan_id)
            if plan is None:
                raise LookupError(f'计划 {plan_id} 不存在')
            ticker, signal_date = plan['ticker'], plan['signal_date']
        else:
            card = review_repo.review_card(conn, card_id)
            if card is None:
                raise LookupError(f'复盘卡 {card_id} 不存在')
            ticker = card['ticker']
            signal_date = card['signal_date'] or _trade_day(conn, card)
        try:
            row = review_repo.insert_discussion(conn, {
                'request_id': request_id, 'request_hash': digest, 'context_kind': context['kind'],
                'ticker': ticker, 'signal_date': signal_date, 'plan_id': plan_id, 'card_id': card_id})
        except psycopg.errors.UniqueViolation:
            raise ledger.Conflict('request_id_conflict') from None
        return {'status': 'created', 'record': _discussion_view(row)}


# ---------------------------------------------------------------- ask (synchronous part)

def _validate_ask(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError('body must be a JSON object')
    request_id = _uuid(payload.get('request_id'), 'request_id')
    body = payload.get('body')
    if not isinstance(body, str) or not 1 <= len(body.strip()) <= BODY_MAX:
        raise ValueError(f'body 必须是 1–{BODY_MAX} 字')
    attachment = payload.get('attachment')
    if attachment is not None:
        if (not isinstance(attachment, dict) or attachment.get('kind') not in ('draft', 'knowledge')
                or isinstance(attachment.get('id'), bool) or not isinstance(attachment.get('id'), int)
                or attachment['id'] < 1):
            raise ValueError("attachment 必须是 {kind: 'draft' | 'knowledge', id, version?}")
        version = attachment.get('version')
        if version is not None and (isinstance(version, bool) or not isinstance(version, int) or version < 1):
            raise ValueError('attachment.version 必须是正整数')
        attachment = {'kind': attachment['kind'], 'id': attachment['id'],
                      **({'version': version} if version is not None else {})}
    return {'request_id': request_id, 'body': body.strip(), 'attachment': attachment}


def _check_attachment(conn, attachment: dict[str, Any] | None) -> dict[str, Any] | None:
    """The attachment as stored (a knowledge item carries the version it was attached at), 404 when it does
    not exist, ValueError for a version that does not."""
    if attachment is None:
        return None
    if attachment['kind'] == 'draft':
        if not review_repo.knowledge_drafts(conn, open_only=False, ids=[attachment['id']]):
            raise LookupError(f"草稿 {attachment['id']} 不存在")
        return {'kind': 'draft', 'id': attachment['id']}
    versions, _ = data_repo.knowledge_rows(conn, cutoff=None, knowledge_id=attachment['id'])
    if not versions:
        raise LookupError(f"知识 {attachment['id']} 不存在")
    top = max(v['version'] for v in versions)
    version = attachment.get('version') or top
    if version not in {v['version'] for v in versions}:
        raise ValueError(f"知识 {attachment['id']} 没有第 {version} 版")
    return {'kind': 'knowledge', 'id': attachment['id'], 'version': version}


def ask_check(discussion_id: int, payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """The synchronous part of ``POST …/turns`` (spec §8.2.1): validate the body, then in one write transaction
    check migration 027 (503), the discussion (404), a replay of the same ``request_id`` (same content returns the
    stored turn, other content is 409 ``request_id_conflict``), the Agent (409 ``agent_unavailable: <reason>``),
    the daily token limit (409 ``token_budget``) and an unanswered, non-interrupted earlier turn (409
    ``discussion_busy``); then write your turn. Returns ``{turn_id, run_id, replayed}`` — ``run_id`` is the Agent
    run id the answer will carry. Nothing else is written; the caller starts the background run."""
    rec = _validate_ask(payload)
    settings = agent_model.settings_from_env()
    ny_date = now.astimezone(NY).date()
    run_key = str(uuid.uuid4())
    try:
        turn = _ask_in_tx(discussion_id, rec, settings, ny_date, run_key)
    except BaseException:
        release(run_key)                       # the busy marker is registered inside the lock; undo it on any failure
        raise
    if 'replayed' in turn:                      # the replay result, not a freshly written row
        return turn
    return {'turn_id': turn['id'], 'run_id': run_key, 'replayed': False}


def _ask_in_tx(discussion_id: int, rec: dict[str, Any], settings, ny_date, run_key: str):
    """The body of ``ask_check`` under the discussion lock. Returns the new turn row, or the replay result dict.
    The ``pending`` marker for the new turn is registered here, before the turn is written and while the lock is
    held, so a request waiting on the same lock already sees the question as running (never as interrupted)."""
    with core_tx._tx() as conn:
        _require_ready(conn)
        core_dbutil.lock(conn, f'discussion:{discussion_id}')
        discussion = review_repo.discussion(conn, discussion_id)
        if discussion is None:
            raise LookupError(f'讨论 {discussion_id} 不存在')
        turns = review_repo.turns(conn, discussion_id)
        attachment = _check_attachment(conn, rec['attachment'])
        digest = ledger.request_hash({'discussion_id': discussion_id, 'body': rec['body'], 'attachment': attachment})
        mine = next((t for t in turns if t['request_id'] == rec['request_id']), None)
        if mine is not None:
            if mine['request_hash'] != digest:
                raise ledger.Conflict('request_id_conflict')
            return {'turn_id': mine['id'], 'run_id': mine['run_id'], 'replayed': True}
        unavailable = agent_model.unavailable_reason(settings)
        if unavailable is not None:
            raise ledger.Conflict(f'agent_unavailable: {unavailable}')
        if settings.daily_tokens is not None and analysis_repo.agent_tokens_on(conn, ny_date) >= settings.daily_tokens:
            raise ledger.Conflict('token_budget')
        if any(wait_state(t) == 'pending' for t in _unanswered(turns)):
            raise ledger.Conflict('discussion_busy')
        with _LOCK:
            _RUNNERS[run_key] = ''
        try:
            return review_repo.insert_turn(conn, {
                'discussion_id': discussion_id, 'seq': max((t['seq'] for t in turns), default=0) + 1, 'role': 'user',
                'request_id': rec['request_id'], 'request_hash': digest, 'body': rec['body'],
                'attachment': attachment, 'run_id': run_key})
        except psycopg.errors.UniqueViolation:
            raise ledger.Conflict('request_id_conflict') from None


# ---------------------------------------------------------------- answer (background body)

class _Recording:
    """Wraps the chat model so the raw text of a rejected answer can be kept (D25); delegates everything else."""

    def __init__(self, model):
        self._model, self._bound, self.last = model, model, None

    def bind_tools(self, tools, **kwargs):
        self._bound = self._model.bind_tools(tools, **kwargs)
        return self

    def invoke(self, messages, *args, **kwargs):
        message = self._bound.invoke(messages, *args, **kwargs)
        if not getattr(message, 'tool_calls', None):
            content = getattr(message, 'content', '')
            self.last = content if isinstance(content, str) else str(content)
        return message


def _unit_ids(conn, ticker: str, signal_date: date) -> tuple[frozenset[str], frozenset[str]]:
    plan_ids = frozenset(str(p['id']) for p in execution_repo.plans_for_unit(conn, ticker, signal_date))
    runs = {str(run['id']) for mode in UNIT_RUN_MODES for run in analysis_repo.agent_runs_for(conn, ticker, signal_date,
                                                                                            mode=mode)}
    return plan_ids, frozenset(runs)


def _input_lines(conn, ticker: str, signal_date: date, cutoff: datetime) -> tuple[list[str], frozenset[str]]:
    """The user inputs that apply to the unit and existed at the cutoff, as prompt lines, and their ``user:`` refs."""
    if not core_schema.timeline_ready(conn):
        return [], frozenset()
    rows = [r for r in user_inputs.view(user_inputs.ticker_rows(conn, ticker), signal_date)
            if r['status'] == 'active' and r['created_at'] <= cutoff]
    lines = [f"[{r['ref']}] {r['kind']} · {r['body']}" for r in rows]
    return lines, frozenset(r['ref'] for r in rows)


def _context_text(conn, discussion: dict[str, Any], cutoff: datetime, input_lines: list[str]) -> str:
    head = [f"标的 {discussion['ticker']} · 单元信号日 {discussion['signal_date']}",
            f'本次讨论的截止时间 = 提问时刻 {cutoff.isoformat()}；工具读到的是截至这一刻的数据，请区分「当时看到的」与「之后发生的」']
    if discussion['context_kind'] == 'plan':
        plan = execution_repo.plan_by_id(conn, discussion['plan_id'])
        version = execution_repo.version_by_plan(conn, discussion['plan_id']) if core_schema.timeline_ready(conn) else None
        number = f"，第 {version['version_no']} 版" if version else ''
        head.append(f"讨论对象：计划 {discussion['plan_id']}（{plan['kind']}{number}）")
        head.append(f"计划当时的截止时间：{(version['cutoff_at'] if version else plan['created_at']).isoformat()}")
        head.append(f"价位：入场 {plan['entry_low']}–{plan['entry_high']}，止损 {plan['stop']}，T1 {plan['target1']}；"
                    f"有效 {plan['valid_from']} 至 {plan['valid_until']}；状态 {plan['status']}")
    else:
        card = review_repo.review_card(conn, discussion['card_id'])
        verdict = review_repo.card_verdict(conn, card['id'])
        head.append(f"讨论对象：复盘卡 #{card['id']}（结束类型 {card['end_kind']}，结束日 {card['end_session']}，"
                    f"{'已审定' if verdict else '未审定'}）；卡上的冻结事实用 get_plan_context 读取")
    if input_lines:
        head.append('用户输入（可用 user:<id> 引用）：')
        head.extend(input_lines)
    return '\n'.join(head)


def _history_text(turns: list[dict[str, Any]], current_id: int) -> str:
    """The last ``DISCUSS_HISTORY_TURNS`` turns before the current one, bodies only (questions and answers that
    passed), cut to ``DISCUSS_HISTORY_CHARS`` characters from the oldest side."""
    earlier = [t for t in turns if t['id'] < current_id and (t['role'] == 'user' or t['status'] == 'ok')]
    lines = [f"{'你' if t['role'] == 'user' else '回答'}：{t['body']}" for t in earlier[-DISCUSS_HISTORY_TURNS:]]
    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        if total + len(line) > DISCUSS_HISTORY_CHARS:
            break
        kept.append(line)
        total += len(line)
    return '\n'.join(reversed(kept))


def _attachment_text(conn, attachment: dict[str, Any] | None) -> tuple[str | None, set[str]]:
    """The attachment's original text for the prompt (marked 「用户提供，非证据，不可引用」 by the graph) and the
    ``know:`` refs a rewriting draft may target."""
    if attachment is None:
        return None, set()
    if attachment['kind'] == 'draft':
        draft = review_repo.knowledge_drafts(conn, open_only=False, ids=[attachment['id']])[0]
        return _fields_text(draft, f"经验草稿 #{draft['id']}"), set()
    versions, _ = data_repo.knowledge_rows(conn, cutoff=None, knowledge_id=attachment['id'])
    row = next(v for v in versions if v['version'] == attachment['version'])
    ref = knowledge.ref(attachment['id'], row['version'])
    return _fields_text(row, f'我的知识 {ref}'), {ref}


def _fields_text(row: dict[str, Any], label: str) -> str:
    return (f"{label}\n标题：{row['title']}\n正文：{row['body']}\n适用范围：{row['scope']}\n"
            f"反例：{row['counter_examples']}\n概念：{'、'.join(row['concepts']) or '无'}\n"
            f"标的：{'、'.join(row['tickers']) or '无'}")


def _load(discussion_id: int, turn_id: int) -> dict[str, Any]:
    """Everything the run needs, read in one read-only transaction at the question's moment."""
    with core_tx.read_only_tx() as conn:
        _require_ready(conn)
        discussion = review_repo.discussion(conn, discussion_id)
        turns = review_repo.turns(conn, discussion_id)
        turn = next((t for t in turns if t['id'] == turn_id and t['role'] == 'user'), None)
        if discussion is None or turn is None:
            raise LookupError(f'讨论 {discussion_id} 的提问 {turn_id} 不存在')
        cutoff = turn['created_at']
        ticker, signal_date = discussion['ticker'], discussion['signal_date']
        plan_ids, run_ids = _unit_ids(conn, ticker, signal_date)
        card = review_repo.review_card(conn, discussion['card_id']) if discussion['card_id'] else None
        if card is not None and card['subject'] == 'trade':
            plan_ids, run_ids = frozenset(), frozenset()
        input_lines, user_refs = _input_lines(conn, ticker, signal_date, cutoff)
        attachment_text, targets = _attachment_text(conn, turn['attachment'])
        first = analysis_repo.agent_runs_for(conn, ticker, signal_date)
        return {
            'discussion': discussion, 'turn': turn, 'cutoff': cutoff,
            'session': bars_module.latest_closed_session(cutoff),
            'plan_ids': plan_ids, 'run_ids': run_ids, 'user_refs': user_refs, 'targets': targets,
            'context_text': _context_text(conn, discussion, cutoff, input_lines),
            'history_text': _history_text(turns, turn['id']), 'attachment_text': attachment_text,
            'pool': first[0]['pool'] if first else 'main', 'seq': turn['seq']}


def _next_seq(conn, discussion_id: int) -> int:
    return max((t['seq'] for t in review_repo.turns(conn, discussion_id)), default=0) + 1


def _write_reply(discussion_id: int, turn_id: int, *, status: str, reason: str | None, body: str, refs: list[str],
                 detail: dict[str, Any], cutoff: datetime, run_row: dict[str, Any] | None,
                 calls: list[dict[str, Any]], draft: dict[str, Any] | None) -> dict[str, Any] | None:
    """The answer in one write transaction: the run and its tool calls, the answer turn and the optional lesson
    draft. A turn that already has its reply is left alone (None)."""
    with core_tx._tx() as conn:
        core_dbutil.lock(conn, f'discussion:{discussion_id}')
        if any(t['reply_to'] == turn_id for t in review_repo.turns(conn, discussion_id)):
            return None
        if run_row is not None:
            analysis_repo.insert_agent_run(conn, run_row, calls)
        reply = review_repo.insert_turn(conn, {
            'discussion_id': discussion_id, 'seq': _next_seq(conn, discussion_id), 'role': 'assistant',
            'body': body, 'reply_to': turn_id, 'status': status, 'reason': reason, 'refs': refs, 'detail': detail,
            'agent_run_id': run_row['id'] if run_row else None, 'cutoff_at': cutoff})
        if draft is not None:
            target = knowledge.parse_knowledge_ref(draft['target']) if draft.get('target') else (None, None)
            review_repo.insert_knowledge_draft(conn, {
                'origin': 'discussion', 'discussion_turn_id': reply['id'], 'target_knowledge_id': target[0],
                'target_version': target[1], **{k: draft[k] for k in my_knowledge.DRAFT_KEYS},
                'refs': draft.get('refs') or []})
        return reply


def _error_reply(discussion_id: int, turn_id: int, *, reason: str, detail: str | None, cutoff: datetime,
                 run_row: dict[str, Any] | None = None) -> None:
    _write_reply(discussion_id, turn_id, status='error', reason=reason, body='',
                 refs=[], detail={'error': detail} if detail else {}, cutoff=cutoff, run_row=run_row, calls=[],
                 draft=None)


def answer(discussion_id: int, turn_id: int, *, now: datetime, deps: core_tx.Deps | None = None,
           run_id: str, read_derivation, read_agent_run, get_roadmap) -> dict[str, Any]:
    """The background body of ``POST …/turns`` (spec §8.2.2): read the context at the question's moment, run
    ``agent-discuss-1`` and write the answer. ``run_id`` is the runner's id (kept for the waiting / interrupted
    reading). ``read_derivation`` / ``read_agent_run`` / ``get_roadmap`` are the launching edge host's projections of
    the layers above ``review`` (R13, R26): they are frozen into the run's tool bindings; ``get_roadmap=None`` leaves
    the tool unbound, so the run fails closed (an ``error`` answer, no model call). Whatever goes wrong, an ``error`` answer is written, so no turn is left hanging. Returns
    ``{status, reason, turn_id}``."""
    deps = deps or core_tx.Deps()
    settings = deps.agent_settings if deps.agent_settings is not None else agent_model.settings_from_env()
    run_key: str | None = None
    cutoff: datetime | None = None
    try:
        try:
            loaded = _load(discussion_id, turn_id)
        except Exception as exc:                            # noqa: BLE001 - becomes an error answer below
            with core_tx.read_only_tx() as conn:
                turn = next((t for t in review_repo.turns(conn, discussion_id) if t['id'] == turn_id), None)
            run_key, cutoff = (turn['run_id'], turn['created_at']) if turn else (None, now)
            track(run_key or '', run_id)
            reason, detail = agent_model.classify_error(exc, settings.api_key)
            _error_reply(discussion_id, turn_id, reason=reason, detail=detail, cutoff=cutoff)
            return {'status': 'error', 'reason': reason, 'turn_id': turn_id}
        run_key, cutoff = loaded['turn']['run_id'], loaded['cutoff']
        track(run_key, run_id)
        return _answer(discussion_id, turn_id, loaded, now=now, deps=deps, settings=settings,
                       read_derivation=read_derivation, read_agent_run=read_agent_run, get_roadmap=get_roadmap)
    except Exception as exc:                                # noqa: BLE001 - never leave a turn without an answer
        reason, detail = agent_model.classify_error(exc, settings.api_key)
        try:
            _error_reply(discussion_id, turn_id, reason=reason, detail=detail, cutoff=cutoff or now)
        except Exception:                                   # noqa: BLE001 - the runner record keeps the original
            raise exc from None
        return {'status': 'error', 'reason': reason, 'turn_id': turn_id}
    finally:
        release(run_key)


def _answer(discussion_id: int, turn_id: int, loaded: dict[str, Any], *, now: datetime, deps: core_tx.Deps,
            settings, read_derivation, read_agent_run, get_roadmap) -> dict[str, Any]:
    discussion, turn, cutoff = loaded['discussion'], loaded['turn'], loaded['cutoff']
    run_key = turn['run_id']
    unit = {'ticker': discussion['ticker'], 'signal_date': discussion['signal_date'], 'pool': loaded['pool']}
    ctx = DiscussContext(
        ticker=discussion['ticker'], session=loaded['session'], cutoff_at=cutoff, read=_read_ro,
        signal_date=discussion['signal_date'],
        plan_id=str(discussion['plan_id']) if discussion['plan_id'] else None, card_id=discussion['card_id'],
        unit_plan_ids=loaded['plan_ids'], unit_run_ids=loaded['run_ids'],
        read_derivation=read_derivation, read_agent_run=read_agent_run,
        bindings={**DISCUSS_TOOLS, **({'get_roadmap': get_roadmap} if get_roadmap is not None else {})})
    recording: _Recording | None = None
    result: dict[str, Any] | None = None
    failure: tuple[str, str | None] | None = None
    with ExitStack() as stack:
        try:
            factory = deps.agent_checkpointer or (
                lambda: agent_model.checkpointer(os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL', '')))
            checkpointer = stack.enter_context(factory())
        except Exception as exc:                            # noqa: BLE001
            failure = ('checkpoint_unavailable', agent_model.classify_error(exc, settings.api_key)[1])
        else:
            model_obj = deps.agent_model if deps.agent_model is not None else agent_model.chat_model(settings)
            recording = _Recording(model_obj)
            try:
                concept_ids = set(knowledge.catalog().concepts)
            except knowledge.KnowledgeError:
                concept_ids = set()
            try:
                result = review_agent_runs.run_discuss(
                    discussion=discussion, ctx=ctx, model=recording, checkpointer=checkpointer, run_id=run_key,
                    now=lambda: datetime.now(UTC), context_text=loaded['context_text'],
                    history_text=loaded['history_text'], question=turn['body'],
                    attachment_text=loaded['attachment_text'], user_refs=loaded['user_refs'],
                    targets=loaded['targets'], concept_ids=concept_ids)
            except core_tx.Unavailable:
                raise
            except Exception as exc:                        # noqa: BLE001 - classified like every agent run
                reason, detail = agent_model.classify_error(exc, settings.api_key)
                failure = ('checkpoint_unavailable' if agent_model._checkpoint_missing(exc) else reason, detail)
    moment = datetime.now(UTC)
    if failure is not None or result is None:
        reason, detail = failure or ('model_error', None)
        run_row = agent_model._agent_run_row(unit, settings=settings, attempt=loaded['seq'], now=moment, status='error',
                                         run_id=run_key, reason=reason, detail=detail, mode='discuss')
        run_row['cutoff_at'] = cutoff
        _write_reply(discussion_id, turn_id, status='error', reason=reason, body='', refs=[],
                     detail={'error': detail} if detail else {}, cutoff=cutoff, run_row=run_row, calls=[], draft=None)
        return {'status': 'error', 'reason': reason, 'turn_id': turn_id}
    status = result['status']
    output = result['output'] if status == 'ok' else None
    run_row = agent_model._agent_run_row(
        unit, settings=settings, attempt=loaded['seq'], now=moment, status=status, run_id=run_key,
        reason=None if status == 'ok' else result['reason'], output=output,
        input_tokens=result['input_tokens'], output_tokens=result['output_tokens'], mode='discuss')
    run_row['cutoff_at'] = cutoff
    detail: dict[str, Any] = {}
    if status == 'ok' and result.get('draft_dropped'):
        detail['draft_dropped'] = result['draft_dropped']
    _write_reply(
        discussion_id, turn_id, status=status, reason=None if status == 'ok' else result['reason'],
        body=output['answer'] if output else (recording.last if recording and recording.last else ''),
        refs=output['refs'] if output else [], detail=detail, cutoff=cutoff, run_row=run_row, calls=result['calls'],
        draft=output.get('knowledge_draft') if output else None)
    return {'status': status, 'reason': None if status == 'ok' else result['reason'], 'turn_id': turn_id}


# ---------------------------------------------------------------- read

def list_in(conn, *, plan_id=None, card_id: int | None = None) -> list[dict[str, Any]]:
    """The discussions of a plan or card in an open transaction: ``[{id, created_at, turn_count, first_question}]``
    (``turn_count`` = questions asked); empty without migration 027."""
    if not core_schema.growth_ready(conn):
        return []
    return [_list_item(conn, d) for d in review_repo.discussions_for(conn, plan_id=plan_id, card_id=card_id)]


def list_for(*, plan_id=None, card_id: int | None = None) -> list[dict[str, Any]]:
    """``GET /discussions?plan_id=|card_id=``; Unavailable (503) without migration 027, ValueError without a context."""
    if plan_id is None and card_id is None:
        raise ValueError('plan_id 或 card_id 必须给一个')
    if plan_id is not None:
        plan_id = _uuid(plan_id, 'plan_id')
    with core_tx.read_only_tx() as conn:
        _require_ready(conn)
        return list_in(conn, plan_id=plan_id, card_id=card_id)


def _attachment_view(conn, attachment: dict[str, Any] | None) -> dict[str, Any] | None:
    if attachment is None:
        return None
    if attachment['kind'] == 'knowledge':
        versions, _ = data_repo.knowledge_rows(conn, cutoff=None, knowledge_id=attachment['id'])
        row = next((v for v in versions if v['version'] == attachment.get('version')), None)
    else:
        found = review_repo.knowledge_drafts(conn, open_only=False, ids=[attachment['id']])
        row = found[0] if found else None
    return {**attachment, 'title': row['title'] if row else None}


def view(discussion_id: int) -> dict[str, Any]:
    """``GET /discussions/{id}`` (spec §8.1, §12): the discussion with every turn in order, each answer right
    after its question. A question without an answer carries ``state`` ``pending`` / ``interrupted``; ``user_input_id``
    is the user input saved from the question (found through the derived ``request_id``). Read-only;
    LookupError (404) for an unknown discussion, Unavailable (503) without migration 027."""
    with core_tx.read_only_tx() as conn:
        _require_ready(conn)
        discussion = review_repo.discussion(conn, discussion_id)
        if discussion is None:
            raise LookupError(f'讨论 {discussion_id} 不存在')
        turns = review_repo.turns(conn, discussion_id)
        replies = _replies(turns)
        ready = core_schema.timeline_ready(conn)
        drafts = my_knowledge.drafts_for_turns(conn, [t['id'] for t in turns if t['role'] == 'assistant'])
        out_turns: list[dict[str, Any]] = []
        for turn in (t for t in turns if t['role'] == 'user'):
            saved = (execution_repo.user_input_by_request(conn, user_input_request_id(turn['request_id']))
                     if ready else None)
            reply = replies.get(turn['id'])
            out_turns.append({
                'id': turn['id'], 'seq': turn['seq'], 'role': 'user', 'body': turn['body'],
                'created_at': turn['created_at'], 'request_id': str(turn['request_id']),
                'attachment': _attachment_view(conn, turn['attachment']),
                'state': None if reply else wait_state(turn),
                'user_input_id': saved['id'] if saved else None})
            if reply is not None:
                out_turns.append({
                    'id': reply['id'], 'seq': reply['seq'], 'role': 'assistant', 'body': reply['body'],
                    'created_at': reply['created_at'], 'status': reply['status'], 'reason': reply['reason'],
                    'refs': reply['refs'], 'detail': reply['detail'],
                    'agent_run_id': str(reply['agent_run_id']) if reply['agent_run_id'] else None,
                    'cutoff_at': reply['cutoff_at'], 'knowledge_draft': drafts.get(reply['id'])})
        view_ = _discussion_view(discussion)
        view_['context'] = _context_view(conn, discussion)
        view_['turns'] = out_turns
        return view_


def _context_view(conn, discussion: dict[str, Any]) -> dict[str, Any]:
    if discussion['context_kind'] == 'plan':
        plan = execution_repo.plan_by_id(conn, discussion['plan_id'])
        version = execution_repo.version_by_plan(conn, discussion['plan_id']) if core_schema.timeline_ready(conn) else None
        return {'version_no': version['version_no'] if version else None,
                'generated_at': plan['created_at'] if plan else None,
                'cutoff_at': version['cutoff_at'] if version else None, 'end_kind': None, 'end_session': None}
    card = review_repo.review_card(conn, discussion['card_id'])
    return {'version_no': None, 'generated_at': None, 'cutoff_at': None,
            'end_kind': card['end_kind'] if card else None, 'end_session': card['end_session'] if card else None}
