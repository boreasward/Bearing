"""Optimisation proposals (v2.15, spec §8.3, §8.4): the Saturday evidence pack and drafting run, the user's decision,
the "no change before approval" check and the list the roadmap page reads.

* ``evidence_pack`` reads the caller-windowed SQL aggregates (7 days for failures / agent quality, 30 days for
  repeated lessons, the stale-version and monthly process-review signals) and reduces them with the pure
  ``optimize.evidence``. Empty is empty: no source rows are invented.
* ``run_propose`` is the model use case behind the ``propose`` node: no evidence → no model call; otherwise one
  ``agent-optimize-1`` run whose output is checked proposal by proposal (``proposal_check``); the run row (with the
  frozen evidence pack, UI1) and the valid proposals are written in one transaction.
* ``decide`` is the user's decision: ``approved`` writes the version (``planned``) and the numbered decision in the
  same transaction as the status change, then submits the brief; ``rejected`` writes the status and reason only.
  The ``request_id`` makes a replay a no-op."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from typing import Any

from investment_assistant.analysis import repo as analysis_repo
from investment_assistant.core import dbutil as core_dbutil
from investment_assistant.core import schema as core_schema
from investment_assistant.core import tx as core_tx
from investment_assistant.core.constants import PROPOSE_DEADLINE_S, SH
from investment_assistant.core.idempotency import Conflict
from investment_assistant.core.versions import OPTIMIZE_AGENT_VERSION
from investment_assistant.data import repo as data_repo
from investment_assistant.execution.citations import Rejected
from investment_assistant.optimize import briefs, evidence, proposal_check, roadmap, runs
from investment_assistant.optimize import repo as optimize_repo
from investment_assistant.review import repo as review_repo

OPTIMIZE_TOOLSET = 'optimize@agent-optimize-1'
FAILURE_WINDOW_DAYS = 7
LESSON_WINDOW_DAYS = 30
VERSION_ID = re.compile(r'^v\d+(\.\d+){0,2}$')
REASON_MAX = 1000
EVIDENCE_MISSING = '缺少该提案生成时冻结的证据记录'

OPTIMIZE_PROMPT = """你是系统优化顾问。程序把过去一周的系统运行证据汇总成五段：[fact:data_failures]（数据抓取失败与备源使用）、[fact:agent_quality]（Agent 拒绝与错误原因、研究员 blocked 原因、补查 deferred、反方意见之后出现成功修订的比例）、[fact:retro_patterns]（近 30 天复盘卡与知识草稿重复出现的教训范围）、[fact:route]（超过 30 天待验收的版本、主指标是否触发流程复查）、[fact:open_proposals]（尚未处理的提案）。每段的每一行都有 id。
你要在证据里找出值得改进系统自身的地方（改数据、prompt、节点或指标），提出 0 到 3 条提案；没有值得提的就返回空列表。不讨论个股买卖，不改任何口径数值。
规则：
1. 每条提案的 evidence_refs 至少一条，写成 "fact:<段名>#<行 id>"（例如 fact:data_failures#0123456789abcdef01234567），只能引用证据里真实存在的行；没有证据支撑的想法不要提。
2. 不要重复 [fact:open_proposals] 里已有的提案标题。
3. 证据为空的段落只表示这段没有观察到数据，不证明没有问题。
4. backup_incomplete_runs 大于 0 时 backup_used 只是已知下界；counter_adoption 是运行级的后续修订比例，不证明逐条反方意见被采纳，也不是因果。
5. 需要了解路线、已有决策或已有提案时，可以调用 get_roadmap（section 为 goals / versions / decisions / proposals）；概念与经验用 get_concept、get_knowledge。
完成后只输出一个 JSON 对象，不要输出其他文字：
{"proposals": [{"source": "data" 或 "agent_quality" 或 "route",
  "title": "…", "change": "…（不超过 500 字）", "rationale": "…（不超过 500 字）",
  "evidence_refs": ["fact:data_failures#…"], "expected": "…（不超过 200 字）", "acceptance": "…（不超过 300 字）"}]}"""


# ---------------------------------------------------------------- the evidence pack

def process_review_rows(conn) -> list[dict]:
    """The route signal of the primary metric (spec §8.3): a row only when the latest monthly review's three-month
    flag says "review"; an unknown or fine flag adds nothing (no zero is invented)."""
    heads = review_repo.monthly_heads(conn, 1)
    review = ((heads[0]['stats'] or {}).get('review') or {}) if heads else {}
    if review.get('flag') != 'review':
        return []
    return [{'kind': 'process_review', 'metric': 'monthly_3m_compound_vs_spy', 'month': heads[0]['month'],
             'months': review.get('months'), 'compound': review.get('compound'),
             'spy_compound': review.get('spy_compound'), 'triggered': True}]


def evidence_pack(conn, *, now: datetime) -> dict[str, list[dict]]:
    """The five sections with row ids, from the windows ending at ``now`` (the caller's connection, read only)."""
    week, month = now - timedelta(days=FAILURE_WINDOW_DAYS), now - timedelta(days=LESSON_WINDOW_DAYS)
    return evidence.summarize(
        fetch_failures=optimize_repo.fetch_failures_since(conn, week) if core_schema.fetch_runs_ready(conn) else [],
        agent_reasons=optimize_repo.agent_reasons_since(conn, week),
        research_blocked=optimize_repo.research_blocked_since(conn, week),
        counter_adoption=optimize_repo.counter_adoption_since(conn, week),
        lesson_repeats=optimize_repo.lesson_repeats_since(conn, month) if core_schema.growth_ready(conn) else [],
        stale_versions=[*optimize_repo.stale_versions(conn, days=30), *process_review_rows(conn)],
        open_proposals=optimize_repo.list_proposals(conn, status='proposed'))


def has_evidence(pack: dict[str, list[dict]]) -> bool:
    """Whether anything was observed. Open proposals only deduplicate, and "no counter opinions in the window"
    (a counter_adoption row with total 0) observed nothing."""
    for key in ('data_failures', 'agent_quality', 'retro_patterns', 'route'):
        for row in pack.get(key, []):
            if row.get('kind') == 'counter_adoption' and not row.get('total'):
                continue
            return True
    return False


# ---------------------------------------------------------------- the drafting run

def _check(pack: dict, open_titles: frozenset):
    def check(text: str) -> dict:
        result = proposal_check.check_proposals(text, pack=pack, open_titles=open_titles)
        dropped = result['dropped']
        if dropped and dropped[0]['index'] is None:
            raise Rejected(dropped[0]['reason'])
        if dropped and not result['proposals']:      # something was proposed, nothing of it usable
            raise Rejected(dropped[0]['reason'])
        return result

    return check


def run_propose(session, *, now: datetime, deps, run_id: str | None, settings=None, clock=None,
                started: float = 0.0) -> dict:
    """One Saturday drafting pass: ``{status, reason, proposals, dropped, agent_run_id, calls}``.

    ``evidence_rows`` counts the observed rows of the pack (the open proposals only deduplicate).
    ``status``: ``no_evidence`` (nothing observed: no model call, no run row), ``skipped`` (the model may not run,
    ``reason`` says why), ``ok`` (``proposals`` = the written rows ``{id, title, source, status}``), ``rejected`` /
    ``error`` (the run is recorded, nothing is written). The run row carries the frozen evidence pack."""
    with core_tx._tx() as conn:
        roadmap.ensure_ready(conn)
        pack = evidence_pack(conn, now=now)
        open_titles = frozenset(row['title'] for row in optimize_repo.list_proposals(conn, status='proposed'))
    base: dict[str, Any] = {'proposals': [], 'dropped': [], 'agent_run_id': None, 'reason': None,
                            'evidence_rows': sum(len(rows) for key, rows in pack.items() if key != 'open_proposals')}
    if not has_evidence(pack):
        return {**base, 'status': 'no_evidence', 'reason': 'no_evidence'}
    settings = settings or runs.settings_for(deps)
    why = runs.unavailable(settings, now=now, clock=clock, started=started, deadline_s=PROPOSE_DEADLINE_S)
    if why:
        return {**base, 'status': 'skipped', 'reason': why}
    agent_run_id = runs.new_run_id()
    human = (f"周度优化提案 · 决策交易日 {session} · 数据截至 {now.isoformat()}\n\n{evidence.fact_blocks(pack)}")
    result, detail = runs.run_phase(phase='optimize', toolset=OPTIMIZE_TOOLSET, system=OPTIMIZE_PROMPT, human=human,
                                    check=_check(pack, open_titles), deps=deps, settings=settings, now=now,
                                    session=session, run_id=agent_run_id)
    checked = result['output'] if result['status'] == 'ok' else None
    output = {'evidence_pack': pack, **(checked or {})}
    row = runs.run_row(mode='optimize', version=OPTIMIZE_AGENT_VERSION, settings=settings, now=now,
                       run_id=agent_run_id, result=result, detail=detail, output=output)
    written = []
    with core_tx._tx() as conn:
        analysis_repo.insert_agent_run(conn, row, result.get('calls') or [])
        for item in (checked or {}).get('proposals', []):
            saved = optimize_repo.insert_proposal(conn, {
                'run_id': run_id, 'agent_run_id': agent_run_id, 'source': item['source'], 'title': item['title'],
                'change': item['change'], 'rationale': item['rationale'], 'evidence_refs': item['evidence_refs'],
                'expected': item['expected'], 'acceptance': item['acceptance']})
            written.append({'id': saved['id'], 'title': saved['title'], 'source': saved['source'],
                            'status': saved['status']})
    return {**base, 'status': result['status'], 'reason': result.get('reason'), 'proposals': written,
            'dropped': (checked or {}).get('dropped', []), 'agent_run_id': agent_run_id,
            'calls': len(result.get('calls') or [])}


# ---------------------------------------------------------------- decisions

def _uuid(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise ValueError('invalid_request_id') from None


def next_version_id(conn) -> str:
    """The next unused ``v2.x`` (spec §8.4): one above the highest existing ``v2.<n>``."""
    used = [int(m.group(1)) for row in optimize_repo.latest_records(conn, 'roadmap_versions')
            if (m := re.fullmatch(r'v2\.(\d+)', row['version_id']))]
    return f'v2.{max(used, default=0) + 1}'


def _linked_version(conn, proposal_id: int) -> str | None:
    row = conn.execute('SELECT version_id FROM roadmap_versions WHERE proposal_id = %s ORDER BY version, id LIMIT 1',
                       (proposal_id,)).fetchone()
    return row[0] if row else None


def _result(conn, proposal: dict) -> dict:
    if proposal['status'] != 'approved':
        return {'status': proposal['status'], 'version_id': None, 'brief_status': None}
    version_id = _linked_version(conn, proposal['id'])
    status = roadmap.brief_state(conn, version_id)[1] if version_id else 'pending'
    return {'status': 'approved', 'version_id': version_id, 'brief_status': status}


def decide(proposal_id: int, *, decision: str, version_id: str | None = None, reason: str, request_id,
           now: datetime) -> dict:
    """Decide a ``proposed`` proposal: ``{status, version_id, brief_status}``.

    ``approved`` — one transaction writes the status, the version (``planned``, ``scope`` = the proposal's change,
    ``exit_conditions`` = its acceptance, linked by ``proposal_id``) and the next numbered decision; the brief is
    submitted after the commit. ``rejected`` — status and reason only. A decided proposal, a taken ``version_id``
    and a ``request_id`` used for other content are conflicts; the same request replays its stored result."""
    if decision not in ('approved', 'rejected'):
        raise ValueError('invalid_decision')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > REASON_MAX:
        raise ValueError('invalid_reason')
    request_id = _uuid(request_id)
    if version_id is not None and (not isinstance(version_id, str) or not VERSION_ID.fullmatch(version_id)):
        raise ValueError('invalid_version_id')
    reason = reason.strip()
    with core_tx._tx() as conn:
        roadmap.ensure_ready(conn)
        core_dbutil.lock(conn, f'roadmap:proposal:{proposal_id}')
        proposal = optimize_repo.proposal_by_id(conn, proposal_id)
        if proposal is None:
            raise LookupError(f'没有这条提案：{proposal_id}')
        known = optimize_repo.proposal_by_request(conn, request_id)
        if known is not None:
            same = (known['id'] == proposal['id'] and known['status'] == decision
                    and known['decision_reason'] == reason
                    and (version_id is None or version_id == _linked_version(conn, known['id'])))
            if not same:
                raise Conflict('request_id was already used with different content')
            return _result(conn, known)                  # a replay writes nothing and submits nothing
        if proposal['status'] != 'proposed':
            raise Conflict('proposal_already_decided')
        if decision == 'approved':
            core_dbutil.lock(conn, 'roadmap:decisions')
            version_id = version_id or next_version_id(conn)
            if optimize_repo.record_history(conn, 'roadmap_versions', version_id):
                raise Conflict('version_id_exists')
        if not optimize_repo.decide_proposal(conn, proposal_id, decision=decision, reason=reason, decided_at=now,
                                             request_id=request_id):
            raise Conflict('proposal_already_decided')
        if decision == 'rejected':
            return {'status': 'rejected', 'version_id': None, 'brief_status': None}
        optimize_repo.insert_roadmap_record(conn, 'roadmap_versions', {
            'version_id': version_id, 'version': 1, 'title': proposal['title'], 'scope': proposal['change'],
            'depends_on': [], 'status': 'planned', 'exit_conditions': proposal['acceptance'],
            'proposal_id': proposal['id']})
        number = conn.execute('SELECT COALESCE(max(decision_no), 0) + 1 FROM roadmap_decisions').fetchone()[0]
        optimize_repo.insert_roadmap_record(conn, 'roadmap_decisions', {
            'decision_no': number, 'version': 1, 'decided_on': now.astimezone(SH).date(),
            'question': proposal['title'], 'decision': f'批准：{reason}', 'quote': reason,
            'version_ids': [version_id]})
    briefs.submit(version_id)
    return {'status': 'approved', 'version_id': version_id, 'brief_status': 'pending'}


def violations(conn) -> list[int]:
    """The "no change before approval" check (spec §8.4, O1): the ``proposed`` proposals that already have a version
    or a brief behind them. ``proposals_without_changes`` returns the CLEAN ids, so the violations are all proposed
    ids minus those."""
    proposed = {row['id'] for row in optimize_repo.list_proposals(conn, status='proposed')}
    return sorted(proposed - set(optimize_repo.proposals_without_changes(conn)))


# ---------------------------------------------------------------- the list the roadmap page reads

def _evidence_rows(refs: list[str], pack: dict | None) -> list[dict]:
    rows = []
    for ref in refs or []:
        section, _, row_id = ref.removeprefix('fact:').partition('#')
        found = next((row for row in (pack or {}).get(section, []) if str(row.get('id')) == row_id), None)
        if found is not None:
            rows.append({'ref': ref, 'body': found})
    return rows


def proposals_view(*, status: str | None = None) -> dict:
    """``GET /api/roadmap/proposals``: the proposals (newest first), each with the evidence rows its refs name, read
    from the pack frozen with its drafting run — never from today's tables (UI1). A proposal whose run or rows are
    gone gets an empty / partial list and an explicit note."""
    with core_tx.read_only_tx() as conn:
        roadmap.ensure_ready(conn)
        items = optimize_repo.list_proposals(conn, status=status)
        packs: dict[Any, dict | None] = {}
        for item in items:
            run_id = item['agent_run_id']
            if run_id is not None and run_id not in packs:
                run = data_repo.agent_run_by_id(conn, run_id)
                packs[run_id] = ((run or {}).get('output') or {}).get('evidence_pack')
    out = []
    for item in items:
        rows = _evidence_rows(item['evidence_refs'], packs.get(item['agent_run_id']))
        out.append({**item, 'evidence_rows': rows,
                    'evidence_note': None if len(rows) == len(item['evidence_refs'] or []) and rows else EVIDENCE_MISSING})
    return core_tx._jsonable({'items': out})
