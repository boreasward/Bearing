"""My knowledge (v2.7 spec §5, §7, §12): write, revise, archive, read, performance and the discussion lesson drafts.

Items and versions are append-only (``my-knowledge-1``): a revision is a new version pointing at the previous one,
archiving is a status record, and what a plan could see at its cutoff is decided by ``knowledge.visible``
alone. Reading goes through ``data_repo.knowledge_rows``; this module never UPDATEs or DELETEs, and a lesson
draft is invisible to every Agent tool (D11) — only the pages here read ``knowledge_drafts``.

A few small SELECTs the repository has no function for (which card or discussion a draft belongs to, the verdict
row of a draft, the get_knowledge calls the cutoff check replays) are made here.
"""
from __future__ import annotations
import investment_assistant.review.shadow as review_shadow
import investment_assistant.core.dbutil as core_dbutil
import investment_assistant.core.schema as core_schema
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import investment_assistant.review.repo as review_repo

import argparse
import sys
import uuid
from datetime import UTC, date, datetime, time
from typing import Any, Callable

import psycopg

from investment_assistant.review.knowledge import items as knowledge_items

from investment_assistant.review.knowledge import perf as knowledge_perf

from investment_assistant.execution import simulation as plan_shadow
from investment_assistant.execution import ledger
from investment_assistant.data.adapters import bars as bars_module
from investment_assistant.core import knowledge
from investment_assistant.core.constants import BENCHMARK, AGENT_DEADLINE_S
from investment_assistant.core import tx as core_tx

MIGRATION_MISSING = '迁移 027 未执行'
REASON_MAX = 300
DRAFT_KEYS = ('title', 'body', 'scope', 'counter_examples', 'concepts', 'tickers')


def require_ready(conn) -> None:
    if not core_schema.growth_ready(conn):
        raise core_tx.Unavailable(MIGRATION_MISSING)


def concept_versions() -> dict[str, int] | None:
    """Concept id -> current card version, or None when the card directory cannot be loaded (spec §15)."""
    try:
        return {cid: card.version for cid, card in knowledge.catalog().concepts.items()}
    except knowledge.KnowledgeError:
        return None


def _validated(payload: dict[str, Any], *, require_counter: bool = False) -> dict[str, Any]:
    """The normalised fields of a version; ValueError (400) for a bad field or unknown concept, Unavailable (503)
    when a concept is attached and the card directory is unreadable (the host cannot check the attachment)."""
    try:
        return knowledge_items.validate(payload, concept_versions=concept_versions(), require_counter=require_counter)
    except LookupError:
        raise core_tx.Unavailable('概念卡不可用：请检查 docs/knowledge 目录') from None


def _request_id(payload: Any) -> uuid.UUID:
    if not isinstance(payload, dict):
        raise ValueError('body must be a JSON object')
    try:
        return uuid.UUID(str(payload.get('request_id')))
    except ValueError:
        raise ValueError('request_id must be a UUID') from None


def _by_item(versions: list[dict[str, Any]], statuses: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    items: dict[int, dict[str, Any]] = {}
    for row in sorted(versions, key=lambda v: (v['knowledge_id'], v['version'])):
        items.setdefault(row['knowledge_id'], {'versions': [], 'statuses': []})['versions'].append(row)
    for row in sorted(statuses, key=lambda s: (s['created_at'], s['id'])):
        if row['knowledge_id'] in items:
            items[row['knowledge_id']]['statuses'].append(row)
    return items


def _find_version_by_request(conn, request_id: uuid.UUID) -> dict[str, Any] | None:
    versions, _ = data_repo.knowledge_rows(conn, cutoff=None)
    return next((v for v in versions if v['request_id'] == request_id), None)


def _version_view(row: dict[str, Any], previous: dict[str, Any] | None, origin: str) -> dict[str, Any]:
    return {'id': row['id'], 'knowledge_id': row['knowledge_id'], 'version': row['version'],
            'supersedes_id': row['supersedes_id'], 'draft_id': row['draft_id'], 'title': row['title'],
            'body': row['body'], 'scope': row['scope'], 'counter_examples': row['counter_examples'],
            'concepts': list(row['concepts']), 'tickers': list(row['tickers']), 'created_at': row['created_at'],
            'schema_version': row['schema_version'],
            'changed_fields': knowledge_items.changed_fields(previous, row) if previous else [],
            'source': origin if row['version'] == 1 else ('discussion' if row['draft_id'] else 'manual')}


# ---------------------------------------------------------------- write

def create(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """A new item with its version 1 (spec §5.1). ``payload = {request_id, title, body, scope, counter_examples,
    concepts, tickers}``. ``{'status': 'created' | 'replayed', 'record': version}``; ValueError (400), Unavailable
    (503), ``ledger.Conflict('request_id_conflict')`` (409) for a reused ``request_id`` with other content."""
    request_id = _request_id(payload)
    fields = _validated(payload)
    digest = ledger.request_hash({'op': 'create', **fields})
    with core_tx._tx() as conn:
        require_ready(conn)
        core_dbutil.lock(conn, f'knowledge-request:{request_id}')
        existing = _find_version_by_request(conn, request_id)
        if existing is not None:
            if existing['request_hash'] != digest:
                raise ledger.Conflict('request_id_conflict')
            return {'status': 'replayed', 'record': _version_view(existing, None, 'manual')}
        knowledge_id = review_repo.insert_knowledge_item(conn, {'origin': 'manual'})
        version_id = review_repo.insert_knowledge_version(conn, {
            'knowledge_id': knowledge_id, 'version': 1, 'supersedes_id': None, 'request_id': request_id,
            'request_hash': digest, 'draft_id': None, **fields,
            'schema_version': knowledge_items.MY_KNOWLEDGE_VERSION})
        versions, _ = data_repo.knowledge_rows(conn, cutoff=None, knowledge_id=knowledge_id)
        return {'status': 'created', 'record': _version_view(next(v for v in versions if v['id'] == version_id),
                                                              None, 'manual')}


def _new_version(conn, knowledge_id: int, fields: dict[str, Any], *, request_id: uuid.UUID, digest: str,
                 base_version: int, draft_id: int | None, draft_base: int | None = None) -> dict[str, Any]:
    """Version n+1 of an item under its advisory lock: 404 unknown, 409 ``knowledge_archived``, 409
    ``knowledge_stale: <sentence>`` when ``base_version`` is not the highest version. A discussion draft passes the
    version it rewrote as ``draft_base``: refreshing cannot help it, so its sentence says what can."""
    core_dbutil.lock(conn, f'knowledge:{knowledge_id}')
    versions, statuses = data_repo.knowledge_rows(conn, cutoff=None, knowledge_id=knowledge_id)
    if not versions:
        raise LookupError(f'知识 {knowledge_id} 不存在')
    top = max(versions, key=lambda v: v['version'])
    if knowledge.status_at(statuses, None) == 'archived':
        raise ledger.Conflict('knowledge_archived')
    if base_version != top['version']:
        if draft_base is not None:
            raise ledger.Conflict(f"knowledge_stale: 草稿基于第 {draft_base} 版，现在是第 {top['version']} 版："
                                  f"只能不采纳，或在讨论里基于第 {top['version']} 版重新改写")
        raise ledger.Conflict(f"knowledge_stale: 这条知识已更新到第 {top['version']} 版，请刷新后再改")
    version_id = review_repo.insert_knowledge_version(conn, {
        'knowledge_id': knowledge_id, 'version': top['version'] + 1, 'supersedes_id': top['id'],
        'request_id': request_id, 'request_hash': digest, 'draft_id': draft_id, **fields,
        'schema_version': knowledge_items.MY_KNOWLEDGE_VERSION})
    fresh, _ = data_repo.knowledge_rows(conn, cutoff=None, knowledge_id=knowledge_id)
    return next(v for v in fresh if v['id'] == version_id)


def _base_version(payload: dict[str, Any], default: int | None = None) -> int:
    value = payload.get('base_version', default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError('base_version 必须是页面加载时看到的最高版本号')
    return value


def revise(knowledge_id: int, payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """A new version of an existing item (spec §5.1). ``payload`` as ``create`` plus ``base_version`` (the highest
    version the page saw). Replays win over everything else; then 409 ``knowledge_archived`` and 409
    ``knowledge_stale: 这条知识已更新到第 n 版，请刷新后再改``. Two concurrent revisions: the advisory lock lets one win,
    the other sees the new version and is stale."""
    request_id = _request_id(payload)
    base = _base_version(payload)
    fields = _validated(payload)
    digest = ledger.request_hash({'op': 'revise', 'knowledge_id': knowledge_id, 'base_version': base, **fields})
    with core_tx._tx() as conn:
        require_ready(conn)
        core_dbutil.lock(conn, f'knowledge-request:{request_id}')
        existing = _find_version_by_request(conn, request_id)
        if existing is not None:
            if existing['request_hash'] != digest or existing['knowledge_id'] != knowledge_id:
                raise ledger.Conflict('request_id_conflict')
            return {'status': 'replayed', 'record': _version_view(existing, None, 'manual')}
        row = _new_version(conn, knowledge_id, fields, request_id=request_id, digest=digest, base_version=base,
                           draft_id=None)
        return {'status': 'created', 'record': _version_view(row, None, 'manual')}


def set_status(knowledge_id: int, payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """Archive or restore an item (spec §5.1, D13): a status record, never a new version. ``payload =
    {request_id, status: 'archived' | 'active', reason}``; 409 ``status_unchanged`` when it is already that."""
    request_id = _request_id(payload)
    status = payload.get('status')
    if status not in ('archived', 'active'):
        raise ValueError('status 必须是 archived 或 active')
    reason = payload.get('reason') or ''
    if not isinstance(reason, str) or len(reason.strip()) > REASON_MAX:
        raise ValueError(f'reason 最多 {REASON_MAX} 字')
    reason = reason.strip()
    digest = ledger.request_hash({'op': 'status', 'knowledge_id': knowledge_id, 'status': status, 'reason': reason})
    with core_tx._tx() as conn:
        require_ready(conn)
        core_dbutil.lock(conn, f'knowledge:{knowledge_id}')
        versions, statuses = data_repo.knowledge_rows(conn, cutoff=None, knowledge_id=knowledge_id)
        if not versions:
            raise LookupError(f'知识 {knowledge_id} 不存在')
        existing = next((s for s in statuses if s['request_id'] == request_id), None)
        if existing is not None:
            if existing['request_hash'] != digest:
                raise ledger.Conflict('request_id_conflict')
            return {'status': 'replayed', 'record': _status_view(existing)}
        if knowledge.status_at(statuses, None) == status:
            raise ledger.Conflict('status_unchanged')
        try:
            review_repo.insert_knowledge_status(conn, {'knowledge_id': knowledge_id, 'status': status,
                                                      'reason': reason, 'request_id': request_id,
                                                      'request_hash': digest})
        except psycopg.errors.UniqueViolation:
            raise ledger.Conflict('request_id_conflict') from None
        _, fresh = data_repo.knowledge_rows(conn, cutoff=None, knowledge_id=knowledge_id)
        return {'status': 'created', 'record': _status_view(next(s for s in fresh if s['request_id'] == request_id))}


def _status_view(row: dict[str, Any]) -> dict[str, Any]:
    return {'knowledge_id': row['knowledge_id'], 'status': row['status'], 'reason': row['reason'],
            'created_at': row['created_at']}


# ---------------------------------------------------------------- read

def _citation_stats(conn, knowledge_id: int) -> tuple[int, int]:
    """(citation rows, distinct units of the plan citations)."""
    citations = review_repo.citations_of(conn, knowledge_id)
    units = set()
    for plan_id in {c['plan_id'] for c in citations if c['plan_id']}:
        plan = execution_repo.plan_by_id(conn, plan_id)
        if plan is not None:
            units.add((plan['ticker'], plan['signal_date']))
    return len(citations), len(units)


def _entry(conn, knowledge_id: int, parts: dict[str, Any]) -> dict[str, Any]:
    item = review_repo.knowledge_item(conn, knowledge_id)
    latest, first = parts['versions'][-1], parts['versions'][0]
    cited, sampled = _citation_stats(conn, knowledge_id)
    return {'id': knowledge_id, 'origin': item['origin'], 'title': latest['title'], 'scope': latest['scope'],
            'concepts': list(latest['concepts']), 'tickers': list(latest['tickers']),
            'current_version': latest['version'], 'status': knowledge.status_at(parts['statuses'], None),
            'written_at': first['created_at'], 'updated_at': latest['created_at'], 'citation_count': cited,
            'sample_count': sampled}


def _entries(conn, *, ticker: str | None = None, concept: str | None = None,
             status: str = 'active') -> list[dict[str, Any]]:
    if status not in ('active', 'archived', 'all'):
        raise ValueError('status 必须是 active、archived 或 all')
    concept_id = None
    if concept:
        match = knowledge.REF_RE.fullmatch(concept.strip())
        if match is None:
            raise ValueError(f'不是概念 id：{concept}')
        concept_id = match.group(1)
    versions, statuses = data_repo.knowledge_rows(conn, cutoff=None, ticker=ticker.strip().upper() if ticker else None,
                                                   concept=concept_id)
    out = [_entry(conn, kid, parts) for kid, parts in sorted(_by_item(versions, statuses).items())]
    return [e for e in out if status == 'all' or e['status'] == status]


def list_view(*, ticker: str | None = None, concept: str | None = None, status: str = 'active') -> dict[str, Any]:
    """``GET /my-knowledge`` (spec §5.5): ``{items: [...]}`` newest write first; each item's latest version, state,
    citation count and sample count (distinct units among the plan citations)."""
    with core_tx.read_only_tx() as conn:
        require_ready(conn)
        items = _entries(conn, ticker=ticker, concept=concept, status=status)
    return {'items': sorted(items, key=lambda e: (e['updated_at'], e['id']), reverse=True)}


def for_ticker(conn, ticker: str) -> list[dict[str, Any]]:
    """The 「标的」页 block: active items attached to the ticker (their latest version), in an open transaction.
    Unavailable without migration 027."""
    require_ready(conn)
    return sorted(_entries(conn, ticker=ticker, status='active'), key=lambda e: (e['updated_at'], e['id']),
                  reverse=True)


def _citation_view(conn, row: dict[str, Any], cache: dict[Any, Any]) -> dict[str, Any] | None:
    if row['plan_id'] is not None:
        plan = cache.setdefault(row['plan_id'], execution_repo.plan_by_id(conn, row['plan_id']))
        if plan is None:
            return None
        version = execution_repo.version_by_plan(conn, row['plan_id']) if core_schema.timeline_ready(conn) else None
        return {'ticker': plan['ticker'], 'signal_date': plan['signal_date'], 'plan_id': str(row['plan_id']),
                'head_plan_id': None, 'version_no': version['version_no'] if version else None,
                'review_id': None, 'cited_version': row['version'], 'field': row['field']}
    review_row = execution_repo.review_by_run(conn, row['agent_run_id'])
    if review_row is None:
        return None
    return {'ticker': review_row['ticker'], 'signal_date': review_row['signal_date'], 'plan_id': None,
            'head_plan_id': str(review_row['head_plan_id']), 'version_no': None, 'review_id': review_row['id'],
            'cited_version': row['version'], 'field': row['field']}


def item_view(knowledge_id: int, *, version: int | None = None) -> dict[str, Any]:
    """``GET /my-knowledge/{id}[?version=]`` (spec §5.5, §12): ``{item, versions (every version, full text),
    status_history, citations, current}`` where ``current`` is the requested version, else the newest.
    LookupError (404) for an unknown item or version."""
    with core_tx.read_only_tx() as conn:
        require_ready(conn)
        item = review_repo.knowledge_item(conn, knowledge_id)
        versions, statuses = data_repo.knowledge_rows(conn, cutoff=None, knowledge_id=knowledge_id)
        if item is None or not versions:
            raise LookupError(f'知识 {knowledge_id} 不存在')
        ordered = sorted(versions, key=lambda v: v['version'])
        views = [_version_view(row, ordered[index - 1] if index else None, item['origin'])
                 for index, row in enumerate(ordered)]
        current = views[-1]
        if version is not None:
            current = next((v for v in views if v['version'] == version), None)
            if current is None:
                raise LookupError(f'知识 {knowledge_id} 没有第 {version} 版')
        cache: dict[Any, Any] = {}
        citations = [c for c in (_citation_view(conn, row, cache) for row in review_repo.citations_of(conn, knowledge_id))
                     if c is not None]
        return {'item': {'id': item['id'], 'origin': item['origin'], 'source_card_id': item['source_card_id'],
                         'source_discussion_id': item['source_discussion_id'], 'created_at': item['created_at'],
                         'status': knowledge.status_at(statuses, None)},
                'versions': views,
                'status_history': [{'status': s['status'], 'reason': s['reason'], 'created_at': s['created_at']}
                                   for s in sorted(statuses, key=lambda s: (s['created_at'], s['id']))],
                'citations': citations, 'current': current}


# ---------------------------------------------------------------- performance (spec §7)

def perf_summary(conn, knowledge_id: int, *, session: date, now: datetime) -> dict[str, Any]:
    """``knowledge_perf.summarize`` of one item: the plan-shadow outcomes of its cited plans, simulated the way
    ``review_shadow.plan_shadow`` does (split-adjusted daily bars, SPY, ``plan-shadow-1``)."""
    citations = review_repo.citations_of(conn, knowledge_id)
    spy = data_repo.read_bars(conn, BENCHMARK, session, cutoff_at=now, limit=400)
    ready = core_schema.timeline_ready(conn)
    plans: dict[str, dict[str, Any]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    bars_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for plan_id in sorted({str(c['plan_id']) for c in citations if c['plan_id']}):
        plan = execution_repo.plan_by_id(conn, plan_id)
        if plan is None:
            continue
        version = execution_repo.version_by_plan(conn, plan_id) if ready else None
        plans[plan_id] = {**plan, 'version_no': version['version_no'] if version else None}
        if plan['ticker'] not in bars_by_ticker:
            bars_by_ticker[plan['ticker']] = data_repo.read_bars(conn, plan['ticker'], session, cutoff_at=now,
                                                                  limit=400)
        outcomes[plan_id] = plan_shadow.simulate(plan, bars_by_ticker[plan['ticker']], spy,
                                                 splits=data_repo.split_events(conn, plan['ticker']))
    reviews_cited = len({c['review_id'] for c in citations if c['plan_id'] is None and c['review_id'] is not None})
    return knowledge_perf.summarize(citations=citations, plans=plans, outcomes=outcomes, reviews_cited=reviews_cited)


def performance(knowledge_id: int, *, now: datetime) -> dict[str, Any]:
    """``GET /my-knowledge/{id}/performance`` (spec §7). Read-only; LookupError (404) for an unknown item."""
    session = bars_module.latest_closed_session(now)
    with core_tx.read_only_tx() as conn:
        require_ready(conn)
        if review_repo.knowledge_item(conn, knowledge_id) is None:
            raise LookupError(f'知识 {knowledge_id} 不存在')
        return perf_summary(conn, knowledge_id, session=session, now=now)


def learning_perf(conn, *, session: date, now: datetime) -> dict[int, dict[str, Any]]:
    """For the weekly 「你学到了什么」: every item that has a plan citation -> ``{ref (newest version), title,
    summary}`` (``summary`` = ``perf_summary``)."""
    versions, statuses = data_repo.knowledge_rows(conn, cutoff=None)
    out: dict[int, dict[str, Any]] = {}
    for knowledge_id, parts in sorted(_by_item(versions, statuses).items()):
        if not any(c['plan_id'] for c in review_repo.citations_of(conn, knowledge_id)):
            continue
        latest = parts['versions'][-1]
        out[knowledge_id] = {'ref': knowledge.ref(knowledge_id, latest['version']), 'title': latest['title'],
                             'summary': perf_summary(conn, knowledge_id, session=session, now=now)}
    return out


# ---------------------------------------------------------------- what plans cite (today / chain views)

def refs_for_review(conn, review_id: int) -> list[dict[str, Any]]:
    """The same entries for the knowledge one review row cited (spec §6.2); empty without migration 027."""
    if not core_schema.growth_ready(conn):
        return []
    rows = review_repo.citations_for_review(conn, review_id)
    if not rows:
        return []
    versions, _ = data_repo.knowledge_rows(conn, cutoff=None)
    return knowledge.ref_entries([{**row, 'group': review_id} for row in rows], versions).get(review_id, [])


# ---------------------------------------------------------------- lesson drafts

def _draft_rows(conn, *, open_only: bool, ids: list[int] | None = None) -> list[dict[str, Any]]:
    return review_repo.knowledge_drafts(conn, open_only=open_only, ids=ids)


def _draft_views(conn, drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not drafts:
        return []
    open_ids = {d['id'] for d in review_repo.knowledge_drafts(conn, open_only=True)}
    versions, _ = data_repo.knowledge_rows(conn, cutoff=None)
    approved = {v['draft_id']: v for v in versions if v['draft_id'] is not None}
    newest: dict[int, int] = {}
    for v in versions:
        newest[v['knowledge_id']] = max(newest.get(v['knowledge_id'], 0), v['version'])
    card_of = {row[0]: row[1] for row in conn.execute(
        'SELECT id, card_id FROM review_card_drafts WHERE id = ANY(%s)',
        ([d['card_draft_id'] for d in drafts if d['card_draft_id']],)).fetchall()}
    discussion_of = {row[0]: row[1] for row in conn.execute(
        'SELECT id, discussion_id FROM discussion_turns WHERE id = ANY(%s)',
        ([d['discussion_turn_id'] for d in drafts if d['discussion_turn_id']],)).fetchall()}
    out = []
    for d in drafts:
        mine = approved.get(d['id'])
        decided = d['id'] not in open_ids
        out.append({
            'id': d['id'], 'origin': d['origin'], 'card_id': card_of.get(d['card_draft_id']),
            'discussion_id': discussion_of.get(d['discussion_turn_id']), 'discussion_turn_id': d['discussion_turn_id'],
            'target_knowledge_id': d['target_knowledge_id'], 'target_version': d['target_version'],
            'target_current_version': newest.get(d['target_knowledge_id']),
            'title': d['title'], 'body': d['body'], 'scope': d['scope'], 'counter_examples': d['counter_examples'],
            'concepts': list(d['concepts']), 'tickers': list(d['tickers']), 'refs': d['refs'],
            'created_at': d['created_at'],
            'verdict': ({'action': 'approve' if mine else 'dismiss',
                         'ref': knowledge.ref(mine['knowledge_id'], mine['version']) if mine else None}
                        if decided else None)})
    return out


def drafts_view(*, open_only: bool) -> dict[str, Any]:
    """``GET /my-knowledge-drafts`` (spec §5.4): both sources, open ones only or all with their decision."""
    with core_tx.read_only_tx() as conn:
        require_ready(conn)
        return {'drafts': _draft_views(conn, _draft_rows(conn, open_only=open_only))}


def drafts_for_turns(conn, turn_ids: list[int]) -> dict[int, dict[str, Any]]:
    """``answer turn id -> its lesson draft view`` for the discussion page, in an open transaction."""
    if not turn_ids:
        return {}
    wanted = set(turn_ids)
    drafts = [d for d in _draft_rows(conn, open_only=False) if d['discussion_turn_id'] in wanted]
    return {view['discussion_turn_id']: view for view in _draft_views(conn, drafts)}


def draft_verdict(draft_id: int, payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """Approve (fields editable) or dismiss a discussion lesson draft (spec §5.4, §12). ``payload = {request_id,
    action: 'approve' | 'dismiss', <edited fields>, base_version?}``. Approving a draft without a target creates a
    new item (origin ``discussion``); with a target it becomes the target's next version, ``base_version``
    defaulting to the draft's ``target_version`` (409 ``knowledge_archived``, or 409 ``knowledge_stale: 草稿基于第 k 版，
    现在是第 n 版：只能不采纳，或在讨论里基于第 n 版重新改写`` once the target has a newer version). A card's draft
    is 409 ``review_on_card``; a draft already decided by another request is 409 ``draft_decided`` (not in spec §12's
    code list; the page shows its sentence); the same request again replays (``replayed``), the same ``request_id``
    with other content is 409 ``request_id_conflict``."""
    request_id = _request_id(payload)
    action = payload.get('action')
    if action not in ('approve', 'dismiss'):
        raise ValueError('action 必须是 approve 或 dismiss')
    with core_tx._tx() as conn:
        require_ready(conn)
        core_dbutil.lock(conn, f'knowledge-draft:{draft_id}')
        found = _draft_rows(conn, open_only=False, ids=[draft_id])
        if not found:
            raise LookupError(f'草稿 {draft_id} 不存在')
        draft = found[0]
        if draft['origin'] == 'review_card':
            raise ledger.Conflict('review_on_card')
        decided = conn.execute('SELECT request_id, action FROM knowledge_draft_verdicts WHERE draft_id = %s',
                               (draft_id,)).fetchone()
        if decided is not None and decided[0] != request_id:
            raise ledger.Conflict('draft_decided: 这条草稿已经审定过了')
        if decided is not None and decided[1] != action:
            raise ledger.Conflict('request_id_conflict')            # the same id now says something else
        merged = fields = digest = None
        if action == 'approve':
            merged = {key: payload[key] if key in payload else (list(draft[key]) if key in ('concepts', 'tickers')
                                                                else draft[key]) for key in DRAFT_KEYS}
            fields = _validated(merged, require_counter=True)
            digest = ledger.request_hash({'op': 'draft', 'draft_id': draft_id, **fields})
        if decided is not None:
            if action == 'approve':                                   # compare the full content, as the other writes do
                versions, _ = data_repo.knowledge_rows(conn, cutoff=None)
                stored = next((v for v in versions if v['draft_id'] == draft_id), None)
                if stored is None or stored['request_hash'] != digest:
                    raise ledger.Conflict('request_id_conflict')
            return {'status': 'replayed', 'record': _draft_views(conn, found)[0]['verdict'] | {'draft_id': draft_id}}
        if action == 'dismiss':
            review_repo.insert_draft_verdict(conn, {'draft_id': draft_id, 'action': 'dismiss', 'request_id': request_id})
            return {'status': 'created', 'record': {'draft_id': draft_id, 'action': 'dismiss', 'ref': None}}
        if draft['target_knowledge_id'] is not None:
            base = _base_version(payload, draft['target_version'])
            row = _new_version(conn, draft['target_knowledge_id'], fields, request_id=request_id, digest=digest,
                               base_version=base, draft_id=draft_id, draft_base=draft['target_version'])
            knowledge_id = draft['target_knowledge_id']
        else:
            discussion_id = conn.execute('SELECT discussion_id FROM discussion_turns WHERE id = %s',
                                         (draft['discussion_turn_id'],)).fetchone()[0]
            knowledge_id = review_repo.insert_knowledge_item(conn, {'origin': 'discussion',
                                                                   'source_discussion_id': discussion_id})
            try:
                version_id = review_repo.insert_knowledge_version(conn, {
                    'knowledge_id': knowledge_id, 'version': 1, 'supersedes_id': None, 'request_id': request_id,
                    'request_hash': digest, 'draft_id': draft_id, **fields,
                    'schema_version': knowledge_items.MY_KNOWLEDGE_VERSION})
            except psycopg.errors.UniqueViolation:
                raise ledger.Conflict('request_id_conflict') from None
            versions, _ = data_repo.knowledge_rows(conn, cutoff=None, knowledge_id=knowledge_id)
            row = next(v for v in versions if v['id'] == version_id)
        review_repo.insert_draft_verdict(conn, {'draft_id': draft_id, 'action': 'approve',
                                               'knowledge_version_id': row['id'], 'request_id': request_id})
        return {'status': 'created', 'record': {'draft_id': draft_id, 'action': 'approve',
                                                'ref': knowledge.ref(knowledge_id, row['version'])}}


# ---------------------------------------------------------------- cutoff check (spec §12)

def check_cutoff(since: date, out: Callable[[str], None] = print) -> int:
    """``check-cutoff --since D``: replay the cutoff rule on real data. Every successful ``get_knowledge`` call since
    ``since`` is recomputed with ``knowledge.visible`` at its run's ``cutoff_at``: each returned entry must be
    visible with the same version and the number of entries must be what the cutoff allows. Every
    ``knowledge_citations`` row since ``since`` must be backed by a ``get_knowledge`` call of its run that returned
    that ``know:<id>@<version>``, written at or before the run's cutoff. Returns 0 when everything holds, 1
    otherwise. Read-only; Unavailable (exit 2 in ``main``) without a database or migration 027."""
    start = datetime.combine(since, time.min, tzinfo=UTC)
    problems = 0
    with core_tx.read_only_tx() as conn:
        require_ready(conn)
        calls = conn.execute(
            "SELECT c.run_id, c.seq, c.args, c.result, r.cutoff_at FROM agent_tool_calls c "
            "JOIN agent_runs r ON r.id = c.run_id WHERE c.tool = 'get_knowledge' AND c.called_at >= %s "
            'ORDER BY c.called_at, c.run_id, c.seq', (start,)).fetchall()
        returned: dict[Any, set[str]] = {}
        out(f'知识截止时间核对 {since} 起')
        checked = 0
        for run_id, seq, args, result, cutoff in calls:
            envelope = result if isinstance(result, dict) else {}
            if 'error' in envelope:
                continue
            checked += 1
            entries = (envelope.get('data') or {}).get('entries') or []
            refs = {e['ref'] for e in entries}
            returned.setdefault(run_id, set()).update(refs)
            ticker, concept = (args or {}).get('ticker'), (args or {}).get('concept')
            concept_id = None
            if concept:
                match = knowledge.REF_RE.fullmatch(str(concept).strip())
                concept_id = match.group(1) if match else None
            versions, statuses = data_repo.knowledge_rows(conn, cutoff=cutoff, ticker=ticker.upper() if ticker else None,
                                                           concept=concept_id)
            visible = {knowledge.ref(v['knowledge_id'], v['version'])
                       for v in knowledge.visible(versions, statuses, cutoff=cutoff)}
            limit = knowledge.KNOWLEDGE_RESULT_MAX if (ticker or concept) else knowledge.INDEX_MAX
            bad = sorted(refs - visible)
            if bad:
                problems += 1
                out(f"问题  运行 {run_id} 调用 #{seq} 返回了截止时间 {cutoff.isoformat()} 时不可见的条目：{', '.join(bad)}")
            elif len(refs) != min(limit, len(visible)):
                problems += 1
                out(f'问题  运行 {run_id} 调用 #{seq} 返回 {len(refs)} 条，截止时间下应有 {min(limit, len(visible))} 条')
        citations = conn.execute(
            'SELECT k.id, k.agent_run_id, k.knowledge_id, k.version, r.cutoff_at FROM knowledge_citations k '
            'JOIN agent_runs r ON r.id = k.agent_run_id WHERE k.created_at >= %s ORDER BY k.id', (start,)).fetchall()
        for citation_id, run_id, knowledge_id, version, cutoff in citations:
            ref = knowledge.ref(knowledge_id, version)
            if ref not in returned.get(run_id, set()):
                problems += 1
                out(f'问题  引用 #{citation_id} {ref} 所属运行 {run_id} 没有任何 get_knowledge 调用返回过它')
                continue
            versions, _ = data_repo.knowledge_rows(conn, cutoff=None, knowledge_id=knowledge_id)
            written = next((v['created_at'] for v in versions if v['version'] == version), None)
            if written is None or written > cutoff:
                problems += 1
                out(f'问题  引用 #{citation_id} {ref} 的版本写于运行截止时间之后')
    out(f"汇总：核对 {checked} 次 get_knowledge 调用、{len(citations)} 条引用，问题 {problems} 处 → "
        f"{'通过' if not problems else '不通过'}")
    return 0 if not problems else 1


# ---------------------------------------------------------------- command line

def _date_arg(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'日期格式应为 YYYY-MM-DD：{text}') from None


def _show(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    try:
        view = item_view(args.id, version=args.version)
    except LookupError as exc:
        out(str(exc))
        return 1
    item, current = view['item'], view['current']
    out(f"知识 #{item['id']}  来源 {item['origin']}  状态 {item['status']}  共 {len(view['versions'])} 版")
    out(f"第 {current['version']} 版  {current['created_at'].isoformat()}  {current['title']}")
    out(f"适用范围：{current['scope']}")
    out(f"反例：{current['counter_examples'] or '无'}")
    out(f"挂靠：{'、'.join([*current['concepts'], *current['tickers']])}")
    out(current['body'])
    for citation in view['citations']:
        out(f"被引用  {citation['ticker']} {citation['signal_date']}  第 {citation['cited_version']} 版  {citation['field']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """``show --id N [--version V]`` prints an item (read-only; 1 for an unknown item); ``check-cutoff --since D``
    replays the cutoff rule on real data (read-only; 1 on a violation). Exit 2 for bad arguments or an
    unavailable database / migration 027."""
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.review.knowledge.my_knowledge',
                                     description='我的知识：查看与截止时间核对。')
    commands = parser.add_subparsers(dest='command', required=True)
    shower = commands.add_parser('show', help='打印一条知识（只读）')
    shower.add_argument('--id', type=int, required=True, help='知识 id')
    shower.add_argument('--version', type=int, help='版本号（默认最新）')
    checker = commands.add_parser('check-cutoff', help='按真实数据核对知识的截止时间（只读）')
    checker.add_argument('--since', type=_date_arg, required=True, help='起，YYYY-MM-DD')
    args = parser.parse_args(argv)
    try:
        if args.command == 'show':
            return _show(args, print)
        return check_cutoff(args.since, print)
    except core_tx.Unavailable as exc:
        print(f'数据库不可用：{exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
