"""Implementation briefs of approved proposals (v2.15, spec §8.4).

``run(version_id)`` writes one Markdown brief for the version an approved proposal created: evidence = the proposal's
full text and its frozen evidence rows, plus the ``get_roadmap`` / ``get_concept`` / ``get_knowledge`` tools of the
``agent-impl-brief-1`` role. Every attempt appends one ``roadmap_briefs`` row (``ok`` with the body, or ``error``
with the reason) — the page retries an ``error`` by running it again, nothing is overwritten. ``submit`` queues the
run on the shared background runner (``decide`` does it after the approval commits)."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from investment_assistant.analysis import repo as analysis_repo
from investment_assistant.core import runner as core_runner
from investment_assistant.core import tx as core_tx
from investment_assistant.core.idempotency import Conflict
from investment_assistant.core.versions import BRIEF_AGENT_VERSION
from investment_assistant.data import repo as data_repo
from investment_assistant.execution.citations import Rejected
from investment_assistant.optimize import brief_check, roadmap, runs
from investment_assistant.optimize import repo as optimize_repo

BRIEF_TOOLSET = 'brief@agent-impl-brief-1'

BRIEF_PROMPT = """你是实施简报撰写者。用户已批准一条系统优化提案并为它建了一个路线版本。请写一份交给实施者的 Markdown 简报，只依据下面给出的提案全文、它当时冻结的证据行，以及你用工具读到的路线记录和概念卡。
简报必须包含这些二级标题的小节：
## 背景与证据：为什么要做，引用提案的证据行（写明 fact:… 引用）；
## 改什么：分别指出建议改动的数据、prompt、节点、指标，每一项写明建议动的模块与 prompt 段；
## 不改什么：明确不碰的口径、数值和已冻结的内容；
## 验收：可检查的验收条件，对应提案的验收与预期；
## 风险：可能的副作用与回退办法。
不要编造证据里没有的数字或模块名；不确定的地方写「未确认」。可以调用 get_roadmap（section 为 goals / versions / decisions / proposals）核对路线与已有决策，用 get_concept、get_knowledge 查概念与经验。
只输出简报的 Markdown 正文，不要输出其他文字。"""


def _run_job(version_id: str):
    def job() -> dict[str, Any]:
        return run(version_id, now=datetime.now(UTC), deps=core_tx.Deps())

    return job


def submit(version_id: str) -> str:
    """Queue the brief of ``version_id`` on the background runner; returns the runner's task id."""
    return core_runner.submit('brief', _run_job(version_id))


def retry(version_id: str) -> dict:
    """The page's retry: ``{version_id, brief_status: 'pending', run_id}`` after queueing a new attempt.
    An unknown version is ``LookupError``, one without an approved proposal behind it ``ValueError``, and one whose
    latest brief is already ``ok`` a ``Conflict`` (a brief that is there is not regenerated)."""
    with core_tx.read_only_tx() as conn:
        roadmap.ensure_ready(conn)
        history = optimize_repo.record_history(conn, 'roadmap_versions', version_id)
        if not history:
            raise LookupError(f'没有这个版本：{version_id}')
        if not any(row.get('proposal_id') is not None for row in history):
            raise ValueError('brief_requires_proposal')
        _, status = roadmap.brief_state(conn, version_id, history)
    if status == 'ok':
        raise Conflict('brief_already_ok')
    return {'version_id': version_id, 'brief_status': 'pending', 'run_id': submit(version_id)}


def _human(version: dict, proposal: dict, pack: dict | None, now: datetime) -> str:
    frozen = []
    for ref in proposal['evidence_refs'] or []:
        section, _, row_id = ref.removeprefix('fact:').partition('#')
        row = next((r for r in (pack or {}).get(section, []) if str(r.get('id')) == row_id), None)
        frozen.append(f'{ref}\n' + (json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
                                    if row is not None else '（缺少该证据行的冻结记录）'))
    return '\n\n'.join([
        f"版本 {version['version_id']} · {version['title']} · 数据截至 {now.isoformat()}",
        f"提案 #{proposal['id']}（来源 {proposal['source']}）：{proposal['title']}",
        f"改动：{proposal['change']}", f"理由：{proposal['rationale']}", f"预期：{proposal['expected'] or '未写'}",
        f"验收：{proposal['acceptance'] or '未写'}", '提案当时冻结的证据行：\n' + '\n\n'.join(frozen or ['（无）'])])


def _check(text: str) -> dict:
    try:
        return brief_check.check_brief(text)
    except ValueError as exc:
        raise Rejected(str(exc)) from None


def _store(version_id: str, proposal_id: int, *, status: str, reason: str | None, body: str | None,
           run: dict | None = None, calls: list | None = None) -> dict:
    with core_tx._tx() as conn:
        if run is not None:
            analysis_repo.insert_agent_run(conn, run, calls or [])
        brief = optimize_repo.insert_brief(conn, {
            'version_id': version_id, 'proposal_id': proposal_id,
            'agent_run_id': run['id'] if run is not None else None, 'body': body, 'status': status,
            'reason': reason})
    return {'status': status, 'reason': reason, 'brief_id': brief['id'],
            'agent_run_id': run['id'] if run is not None else None}


def run(version_id: str, *, now: datetime, deps) -> dict:
    """One brief attempt for ``version_id``: ``{status: 'ok' | 'error', reason, brief_id, agent_run_id}``.

    A model that may not run (not connected, token cap) or fails is an ``error`` row with its reason; a model
    answer that fails the structural check (no acceptance section, empty, too long) is an ``error`` row too, and the
    rejected ``agent_runs`` row stays beside it."""
    with core_tx._tx() as conn:
        roadmap.ensure_ready(conn)
        history = optimize_repo.record_history(conn, 'roadmap_versions', version_id)
        if not history:
            raise LookupError(f'没有这个版本：{version_id}')
        proposal_id = next((row['proposal_id'] for row in reversed(history) if row.get('proposal_id') is not None), None)
        if proposal_id is None:
            raise ValueError('brief_requires_proposal')
        proposal = optimize_repo.proposal_by_id(conn, proposal_id)
        if proposal is None:
            raise LookupError(f'没有这条提案：{proposal_id}')
        source = data_repo.agent_run_by_id(conn, proposal['agent_run_id']) if proposal['agent_run_id'] else None
    pack = ((source or {}).get('output') or {}).get('evidence_pack')
    settings = runs.settings_for(deps)
    why = runs.unavailable(settings, now=now)
    if why:
        return _store(version_id, proposal_id, status='error', reason=f'agent_unavailable: {why}', body=None)
    agent_run_id = runs.new_run_id()
    result, detail = runs.run_phase(
        phase='brief', toolset=BRIEF_TOOLSET, system=BRIEF_PROMPT, human=_human(history[-1], proposal, pack, now),
        check=_check, deps=deps, settings=settings, now=now, session=now.date(), run_id=agent_run_id)
    ok = result['status'] == 'ok'
    row = runs.run_row(mode='brief', version=BRIEF_AGENT_VERSION, settings=settings, now=now, run_id=agent_run_id,
                       result=result, detail=detail, output=result['output'] if ok else None)
    return _store(version_id, proposal_id, status='ok' if ok else 'error', reason=None if ok else result['reason'],
                  body=result['output']['body'] if ok else None, run=row, calls=result.get('calls') or [])
