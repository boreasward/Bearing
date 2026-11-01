"""Run detail: one run of the daily workflow (or an older batch) node by node, read-only (v2.8 spec §7).

``detail`` is the use case behind ``GET /api/runs/{run_id}/detail``. It runs entirely inside ``core_tx.read_only_tx()``
(the database itself refuses a write), reads only what earlier runs recorded — ``job_reports``, ``stage_runs``,
``data_fetch_runs``, the rows the stage evidence points at, ``agent_attempt_resets`` — and the checkpoint's
``get_state(config).next`` (a read). It never calls a fetcher, a model or ``settings_from_env`` and never asks the
Agent connection anything: a read of the connection may heal an expired login, which is a write.

The sentences, reason texts and the headline are ``run_overview``'s own (same functions, so the overview on the
Today page and this page cannot drift); the run status and the effective rows are ``workflow``'s.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from investment_assistant.workbench import families as families_module
from investment_assistant.workbench import repository, run_overview, service, workflow
from investment_assistant.workbench import stages as stages_module
from investment_assistant.core import tx as core_tx

logger = logging.getLogger(__name__)

RUN_ID_RE = re.compile(r'^[a-z_]+-\d{14}-[0-9a-f]{8}$')
RESULTS_MAX = 200                                   # items per node's results; ``total`` stays the true count
FETCH_NODES = ('sec_monitor', 'collect', 'screen')  # the nodes whose rows point at ``data_fetch_runs``
CONCEPT = 'system.stages'
TARGET_KEYS = ('target', 'ok', 'attempts', 'error_kind', 'http_status', 'reason', 'rows', 'latest', 'elapsed_ms')
FRESH_RUN = (timedelta(hours=6), timedelta(minutes=5))      # how old / how far ahead a run id may be to be "just started"
RERUN_END = ('error',)                              # a unit can be re-run when its latest run ended like this ...
RERUN_SKIP = 'max_attempts'                         # ... or was skipped for this reason (service.rerun_agent's rule)
PENDING_NOTE = '这一节点还没有执行'


# ---------------------------------------------------------------- small helpers

def _ts(value: Any) -> Any:
    """Datetimes as UTC ISO text, dates as ISO text, anything else as is."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _executing(run_id: str) -> bool:
    """Whether this process is executing the run right now (a resume that holds the lock but has not yet written its
    first new row)."""
    from investment_assistant.tasks import daily

    return daily.executing(run_id)


def _locked() -> bool:
    """Whether some execution holds the ``daily:run`` lock (it is only looked at, never taken)."""
    from investment_assistant.tasks import daily          # imported late: the task shell imports this package

    try:
        return daily.is_running()
    except Exception:       # noqa: BLE001 - no way to ask: the run is shown as it was recorded
        return False


def _get(source: Any, *path: str) -> Any:
    return run_overview._get(source, *path)


# ---------------------------------------------------------------- fetch block

def _family(row: dict, *, history: bool) -> dict:
    """One ``data_fetch_runs`` row as the page's family block: the stored per-target outcomes (``detail.targets``)
    as they are; ``legacy`` when a run older than ``fetch-1`` recorded only the failed targets."""
    detail = row['detail'] if isinstance(row.get('detail'), dict) else {}
    try:
        title = families_module.get(row['family']).title
    except LookupError:
        title = row['family']
    targets = [{key: item.get(key) for key in TARGET_KEYS} for item in detail.get('targets') or []
               if isinstance(item, dict)]
    return {'family': row['family'], 'title': title, 'status': row['status'], 'target_count': row['target_count'],
            'ok_count': row['ok_count'], 'started_at': _ts(row['started_at']), 'finished_at': _ts(row['finished_at']),
            'scope': detail.get('scope'), 'targets': targets, 'failures': list(row.get('failures') or []),
            'legacy': 'targets' not in detail and history}


def _fetch_block(conn, row: dict) -> dict | None:
    """The families a stage row's ``outputs.data_fetch_runs`` lists, in that order; None when it lists none."""
    ids = (row.get('outputs') or {}).get('data_fetch_runs')
    if not isinstance(ids, list) or not ids:
        return None
    history = row.get('stages_version') != stages_module.STAGES_VERSION
    return {'families': [_family(fetch, history=history) for fetch in repository.fetch_runs_by_ids(conn, ids)]}


# ---------------------------------------------------------------- results

def _page(items: list[dict], total: int | None = None) -> tuple[list[dict], int]:
    total = len(items) if total is None else max(total, len(items))
    return items[:RESULTS_MAX], total


def _ids(outputs: dict, *keys: str) -> list[Any]:
    out: list[Any] = []
    for key in keys:
        value = outputs.get(key)
        if isinstance(value, list):
            out.extend(value)
    return out


def _can_rerun(conn, run: dict, *, enabled: bool, resets_ready: bool) -> bool:
    """The unit's latest run of this mode is this one and it ended in ``error`` or was skipped for ``max_attempts``,
    and no attempt reset came after it (a reset is the answer to exactly that state)."""
    if not enabled or run['mode'] not in service.RERUN_MODES:
        return False
    if not (run['status'] in RERUN_END or (run['status'] == 'skipped' and run['reason'] == RERUN_SKIP)):
        return False
    latest = repository.latest_agent_run(conn, run['ticker'], run['signal_date'], run['mode'])
    if latest is None or str(latest['id']) != str(run['id']):
        return False
    if resets_ready:
        resets = repository.attempt_resets_for(conn, run['ticker'], run['signal_date'], run['mode'])
        if resets and resets[0]['created_at'] >= latest['created_at']:
            return False
    return True


def _tick_items(detail: dict) -> list[dict]:
    items = []
    for kind in ('weekly', 'monthly'):
        tick = detail.get(kind)
        if not isinstance(tick, dict) or tick.get('action') in (None, 'before_start', 'waiting'):
            continue
        push = tick.get('push')
        if isinstance(push, dict):
            push = 'sent' if push.get('sent') else (push.get('reason') or 'not_sent')
        items.append({'kind': kind, 'id': _text(tick.get('period')), 'ticker': None, 'status': tick.get('action'),
                      'push': push})
    return items


def _results(conn, stage_id: str, row: dict, *, rerun: bool = False, resets_ready: bool = False,
             summary: dict | None = None) -> dict:
    """The result list of one node (spec §7.1 table): at most ``RESULTS_MAX`` items, ``total`` the real count.

    ``rerun`` lets the ``agent`` items carry ``can_rerun`` (only daily runs that are not running); ``summary`` is the
    run's latest ``job_reports.summary`` (the push channel is only recorded there)."""
    outputs = row.get('outputs') if isinstance(row.get('outputs'), dict) else {}
    detail = row.get('detail') if isinstance(row.get('detail'), dict) else {}
    items: list[dict] = []
    total: int | None = None
    truncated: bool | None = None      # id-based lists: cut by the cap, not by a reference that has no row (never happens)
    if row.get('status') == 'skipped' and stage_id != 'push':
        return {'kind': stage_id, 'items': [], 'total': 0, 'truncated': False}
    if stage_id == 'sec_monitor':
        items = [dict(item) for item in outputs.get('filings') or [] if isinstance(item, dict)]
        total = row.get('output_count')
    elif stage_id == 'screen':
        run_id = outputs.get('screen_run_id')
        new_members = set(detail.get('new_members') or [])
        picked = [r for r in repository.screen_results(conn, run_id) if r['selected']] if run_id is not None else []
        items = [{'ticker': r['ticker'], 'rank': r['rank'], 'group': r['group'], 'selected': True,
                  'new_member': r['ticker'] in new_members} for r in picked]
        shown = {item['ticker'] for item in items}
        items += [{'ticker': t, 'rank': None, 'group': None, 'selected': False, 'new_member': True}
                  for t in sorted(new_members - shown)]
    elif stage_id == 'regime':
        regime = repository.regime_by_id(conn, outputs['market_regime_id']) if outputs.get('market_regime_id') else None
        items = [{'label': regime['label'], 'regime_id': str(regime['id'])}] if regime else []
    elif stage_id == 'signals':
        status_of = {str(i): 'inserted' for i in outputs.get('inserted') or []}
        status_of.update({str(i): 'existing' for i in outputs.get('existing') or [] if str(i) not in status_of})
        ordered = list(status_of)
        total = len(ordered)
        truncated = total > RESULTS_MAX
        found = {str(r['id']): r for r in repository.candidate_refs_by_ids(conn, ordered[:RESULTS_MAX])}
        items = [{'ticker': found[i]['ticker'], 'rule_id': found[i]['rule_id'], 'candidate_id': i,
                  'status': status_of[i]} for i in ordered[:RESULTS_MAX] if i in found]
    elif stage_id == 'plan':
        ordered = [str(i) for i in _ids(outputs, 'inserted', 'existing')]
        total = len(ordered)
        truncated = total > RESULTS_MAX
        found = {str(r['id']): r for r in repository.plans_by_ids(conn, ordered[:RESULTS_MAX])}
        items = [{'ticker': found[i]['ticker'], 'plan_id': i, 'status': found[i]['status'],
                  'invalid_reasons': list(found[i]['invalid_reasons'] or [])}
                 for i in ordered[:RESULTS_MAX] if i in found]
    elif stage_id == 'agent':
        ordered = [str(i) for i in outputs.get('agent_run_ids') or []]
        total = len(ordered)
        truncated = total > RESULTS_MAX
        found = {str(r['id']): r for r in repository.agent_runs_by_ids(conn, ordered[:RESULTS_MAX])}
        for run_id in ordered[:RESULTS_MAX]:
            run = found.get(run_id)
            if run is None:
                continue
            items.append({'ticker': run['ticker'], 'signal_date': _ts(run['signal_date']), 'mode': run['mode'],
                          'status': run['status'], 'reason': run['reason'],
                          'reason_text': run_overview.reason_text(run['reason']), 'attempt': run['attempt'],
                          'input_tokens': run['input_tokens'], 'output_tokens': run['output_tokens'],
                          'model': run['model'], 'plan_id': _text(run['plan_id']), 'run_id': run_id,
                          'can_rerun': _can_rerun(conn, run, enabled=rerun, resets_ready=resets_ready)})
    elif stage_id == 'review':
        ordered = list(outputs.get('review_ids') or [])
        total = len(ordered)
        truncated = total > RESULTS_MAX
        found = {int(r['id']): r for r in repository.plan_reviews_by_ids(conn, ordered[:RESULTS_MAX])} \
            if ordered and repository.timeline_ready(conn) else {}
        items = [{'ticker': r['ticker'], 'signal_date': _ts(r['signal_date']), 'outcome': r['outcome'],
                  'review_id': r['id'], 'plan_id': _text(r['new_plan_id'] or r['head_plan_id'])}
                 for i in ordered[:RESULTS_MAX] if (r := found.get(int(i))) is not None]
    elif stage_id == 'accounts':
        items = [dict(item) for item in detail.get('accounts') or [] if isinstance(item, dict)]
    elif stage_id == 'push':
        push = outputs.get('push')
        if isinstance(push, dict):
            items = [{'sent': bool(push.get('sent')), 'messages': push.get('messages'),
                      'channel': _get(summary, 'push', 'channel'), 'reason': push.get('reason')}]
    elif stage_id == 'retro':
        ordered = list(outputs.get('card_ids') or [])
        cards: list[dict] = []
        if ordered and repository.growth_ready(conn):
            from investment_assistant.workbench import retro_cards      # late: it imports ``service`` like this module

            for card in repository.review_card_refs_by_ids(conn, ordered[:RESULTS_MAX]):
                cards.append({'kind': 'card', 'id': card['id'], 'ticker': card['ticker'],
                              'status': retro_cards.card_state(conn, card['id'])['status'], 'push': None})
        items = cards + _tick_items(detail)
        total = len(ordered) + len(_tick_items(detail))
        truncated = len(ordered) > RESULTS_MAX
    # 'collect' lists nothing: the fetch block already has every target
    page, count = _page(items, total)
    return {'kind': stage_id, 'items': page, 'total': count,
            'truncated': count > len(page) if truncated is None else truncated}


# ---------------------------------------------------------------- resume

def _checkpoint_next(run_id: str, checkpointer_factory) -> tuple[tuple[str, ...], bool]:
    """``(next, known)``: the node the checkpoint stopped before (``get_state(config).next``) and whether the saver
    holds a checkpoint of this run at all — a read of the saver."""
    config = {'configurable': {'thread_id': run_id}}
    with (checkpointer_factory or workflow._default_checkpointer)() as saver:
        graph = workflow.build_graph(checkpointer=saver, ctx_factory=lambda: None)
        snapshot = graph.get_state(config)
        return tuple(snapshot.next), bool(snapshot.values)


def _resume(run_id: str, *, status: str, effective: dict[str, dict], session: date | None, now: datetime,
            locked: bool, interrupted: bool, checkpointer_factory) -> dict:
    """``{'allowed', 'node', 'deadline_at', 'reason'}`` — the same conditions as ``workflow.resume_check`` (the failed
    node is the checkpoint's ``next[0]`` and its effective row is ``failed`` or ``skipped / upstream_failed``; the
    window closes at the next session's New York 17:00), without raising, plus ``daily_running`` while the lock is held.
    ``reason`` is one of ``not_failed``, ``stage_failed`` (a stage booked its own ``failed`` row and the chain ran to the
    end: no next node, only a rerun in full helps), ``interrupted`` (the run stopped without a failed node to resume
    from: the rows are incomplete or stale, only a rerun in full helps), ``resume_window_closed``, ``daily_running``,
    ``checkpoint_unavailable``."""
    nothing_to_resume = 'interrupted' if interrupted else 'not_failed'
    block: dict[str, Any] = {'allowed': False, 'node': None, 'deadline_at': None, 'reason': None}
    if status == 'running':
        block['reason'] = 'daily_running'
        return block
    if status != 'failed' or session is None:
        block['reason'] = 'not_failed'
        return block
    try:
        pending, known = _checkpoint_next(run_id, checkpointer_factory)
    except Exception as exc:     # noqa: BLE001 - missing tables, no checkpoint, unreachable: nothing to resume from
        logger.info('run detail: checkpoint not readable (%s)', type(exc).__name__)
        block['reason'] = 'checkpoint_unavailable'
        return block
    last = effective.get(pending[0]) if pending else None
    if last is None or not (last['status'] == 'failed' or (last['status'] == 'skipped'
                                                          and last.get('reason') == 'upstream_failed')):
        # a failed run whose checkpoint (it exists) has no next node and whose ten rows are all there: a stage booked its own
        # ``failed`` row and the chain ran to the end — nothing to continue from
        block['reason'] = 'stage_failed' if known and not pending and not interrupted else nothing_to_resume
        return block
    deadline = workflow.resume_deadline(session)
    block.update(node=pending[0], deadline_at=deadline.astimezone(UTC).isoformat())
    if now >= deadline:
        block['reason'] = 'resume_window_closed'
    elif locked:
        block['reason'] = 'daily_running'
    else:
        block['allowed'] = True
    return block


# ---------------------------------------------------------------- the node blocks

def _pending_view(stage_id: str, version: str, sentence: str) -> dict:
    stage = run_overview._stage_def(stage_id, version)
    return {'id': stage_id, 'title': stage.title, 'purpose': stage.purpose, 'status': 'pending', 'sentence': sentence,
            'input_count': None, 'output_count': None, 'skips': [], 'failures': [], 'degradations': [],
            'reason': None, 'reason_text': None, 'duration_ms': None}


def _node(conn, stage_id: str, version: str, view: dict, members: list[dict], effective: dict[str, dict], *,
          resume: dict, rerun: bool, resets_ready: bool, summary: dict | None) -> dict:
    row = effective.get(stage_id)
    status = 'pending' if view['status'] in ('missing', 'not_recorded') else view['status']
    detail = (row or {}).get('detail') if isinstance((row or {}).get('detail'), dict) else {}
    results = (_results(conn, stage_id, row, rerun=rerun, resets_ready=resets_ready, summary=summary)
               if row is not None else {'kind': stage_id, 'items': [], 'total': 0, 'truncated': False})
    fetch = None
    if stage_id in FETCH_NODES:
        fetch = (_fetch_block(conn, row) if row is not None else None) or {'families': []}
    again = [{'ticker': item['ticker'], 'signal_date': item['signal_date'], 'mode': item['mode']}
             for item in results['items'] if stage_id == 'agent' and item.get('can_rerun')]
    return {
        'id': stage_id, 'title': view['title'], 'purpose': view['purpose'], 'concept': CONCEPT,
        'status': status, 'reason': view.get('reason'), 'reason_text': view.get('reason_text'),
        'attempt': int(row.get('attempt') or 1) if row is not None else None,
        'attempts': [{'attempt': int(m.get('attempt') or 1), 'status': m['status'], 'started_at': _ts(m['started_at']),
                      'finished_at': _ts(m['finished_at']), 'duration_ms': m['duration_ms']}
                     for m in members if m['stage'] == stage_id],
        'started_at': _ts(row['started_at']) if row is not None else None,
        'finished_at': _ts(row['finished_at']) if row is not None else None,
        'duration_ms': row['duration_ms'] if row is not None else None,
        'did': {'sentence': view['sentence'], 'input_count': view['input_count'], 'output_count': view['output_count'],
                'skips': view['skips'], 'failures': view['failures'], 'degradations': view['degradations'],
                'retry': detail.get('retry'), 'timings': detail.get('timings') or {}},
        'fetch': fetch, 'results': results,
        'actions': {'resume': bool(resume['allowed'] and resume['node'] == stage_id), 'rerun_agent': again},
    }


# ---------------------------------------------------------------- assembling

def _executions(reports: list[dict]) -> list[dict]:
    return [{'report_id': r['id'], 'trigger_source': r['trigger_source'], 'status': r['status'],
             'started_at': _ts(r['started_at']), 'finished_at': _ts(r['finished_at']),
             'resume_node': (r['summary'] or {}).get('resume_node'),
             'attempt': int((r['summary'] or {}).get('resumed') or 0) + 1 if 'resumed' in (r['summary'] or {})
             else index + 1} for index, r in enumerate(reports)]


def _no_resume() -> dict:
    return {'allowed': False, 'node': None, 'deadline_at': None, 'reason': None}


def _other_headline(task: str, report: dict | None) -> str:
    if report is None:
        return f'{task} 运行'
    started = report['started_at'].astimezone(run_overview.SH)
    return (f"{started.month} 月 {started.day} 日 {started:%H:%M}（北京时间）的 {task} 运行："
            f"{run_overview.STATUS_TEXT.get(report['status'], report['status'])}")


def _fresh(run_id: str, now: datetime) -> bool:
    """A ``daily-…`` id made a moment ago, before its first node has written a row."""
    if not run_id.startswith('daily-'):
        return False
    try:
        made = datetime.strptime(run_id.split('-')[1], '%Y%m%d%H%M%S').replace(tzinfo=UTC)
    except ValueError:
        return False
    return now - FRESH_RUN[0] <= made <= now + FRESH_RUN[1]


def detail(run_id: str, *, now: datetime, checkpointer_factory=None) -> dict:
    """The §7.1 response for one run id. ``ValueError``: the id is not of the form ``<kind>-<14 digits>-<8 hex>``;
    ``LookupError``: no run of that id; ``service.Unavailable``: no database / schema. Writes nothing, calls no
    external source."""
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise ValueError('run_id 格式不合法')
    with core_tx.read_only_tx() as conn:
        ready = repository.stage_runs_ready(conn)
        modern = ready and repository.daily_ready(conn)
        rows = ([] if not ready else repository.stage_rows_for_run(conn, run_id) if modern
                else repository.stage_runs_for_run(conn, run_id))
        reports = repository.job_reports_for_run(conn, run_id)
        daily_rows = bool(rows) and rows[0]['stages_version'] in ('stages-4', 'stages-5')
        busy = _executing(run_id)
        locked = _locked() if (daily_rows or (not rows and not reports and _fresh(run_id, now))) else False
        if not rows and not reports and not (busy or (locked and _fresh(run_id, now))):
            raise LookupError(f'没有这次运行：{run_id}')
        return _assemble(conn, run_id, rows, reports, now=now, locked=locked or busy, busy=busy, modern=modern,
                         checkpointer_factory=checkpointer_factory)


def _assemble(conn, run_id: str, rows: list[dict], reports: list[dict], *, now: datetime, locked: bool, busy: bool,
              modern: bool, checkpointer_factory) -> dict:
    latest = reports[-1] if reports else None
    summary = (latest or {}).get('summary') or {}
    version = rows[0]['stages_version'] if rows else None
    is_daily = version in ('stages-4', 'stages-5') or (not rows and (latest is None or latest['task'] == 'daily'))
    task = latest['task'] if latest else (rows[0]['job'] if rows else 'daily')
    base = {'run_id': run_id, 'task': task, 'executions': _executions(reports), 'resume': _no_resume(),
            'raw_summary': summary, 'nodes': []}
    if not rows and not is_daily:      # other tasks (filings, sec_backfill, workbench_review, ...): no node evidence
        first = reports[0]
        return {**base, 'kind': 'other', 'graph_version': None, 'stages_version': None,
                'session': _ts(first['target_start_date']), 'cutoff_at': summary.get('cutoff_at'),
                'trigger': first['trigger_source'], 'status': latest['status'],
                'headline': _other_headline(task, latest)}
    version = version or stages_module.STAGES_VERSION
    ids = stages_module.STAGE_IDS_BY_VERSION.get(version, stages_module.STAGE_IDS)
    first_report = reports[0] if reports else None
    cutoff = next((r['summary']['cutoff_at'] for r in reports if (r['summary'] or {}).get('cutoff_at')), None)
    session = rows[0]['session'] if rows else (first_report or {}).get('target_start_date')
    effective = workflow.effective_rows(rows)
    attempts_seen = max((int(r.get('attempt') or 1) for r in rows), default=0)
    unfinished = bool(rows) and (len(effective) < len(ids) or len(reports) < attempts_seen)
    running = busy or (locked and (unfinished or not rows))
    # Interrupted: a node never wrote its row, or a node is still "upstream failed" although nothing failed any more
    # (a resume died after fixing the failed node but before reaching it).
    stale = (bool(rows) and not any(r['status'] == 'failed' for r in effective.values())
             and any(r.get('reason') == 'upstream_failed' for r in effective.values()))
    resets_ready = modern
    if not is_daily:                                    # an older batch (stages-1 .. stages-3)
        status = latest['status'] if latest else workflow.run_status(rows)
    elif running:
        status = 'running'
    elif not rows:
        status = 'failed' if latest is not None and latest['status'] == 'error' else (latest or {}).get('status', 'failed')
    elif len(effective) < len(ids) or stale:
        status = 'failed'                               # the run stopped without finishing (killed): not a success
    else:
        status = workflow.run_status(rows)
    interrupted = len(effective) < len(ids) or stale
    resume = (_resume(run_id, status=status, effective=effective, session=session, now=now, locked=locked,
                      interrupted=interrupted, checkpointer_factory=checkpointer_factory) if is_daily and rows else _no_resume())
    if rows:
        run = run_overview._group(rows)[0]
        views = {view['id']: view for view in run_overview._stage_views(run)}
        headline = run_overview._headline(run, list(views.values()))
        members = rows
    else:
        views, members = {}, []
        reason = summary.get('error')
        headline = ('正在运行，还没有完成的节点' if running
                    else f"这次运行没有执行任何节点：{run_overview.reason_text(reason) or '未知原因'}")
    nodes = []
    for stage_id in ids:
        view = views.get(stage_id) or _pending_view(stage_id, version, PENDING_NOTE)
        nodes.append(_node(conn, stage_id, version, view, members, effective, resume=resume,
                           rerun=is_daily and not locked, resets_ready=resets_ready, summary=summary))
    trigger = rows[0]['trigger'] if rows else (first_report or {}).get('trigger_source')
    return {**base, 'kind': 'daily' if is_daily else 'batch',
            'graph_version': (summary.get('graph_version') or ('daily-1' if version == 'stages-4' else workflow.DAILY_GRAPH_VERSION)) if is_daily else None, 'stages_version': version,
            'session': _ts(session), 'cutoff_at': cutoff, 'trigger': trigger, 'status': status,
            'headline': headline, 'resume': resume, 'nodes': nodes}
