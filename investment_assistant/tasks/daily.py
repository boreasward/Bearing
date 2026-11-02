"""The ``daily`` task shell (v2.8 §4.7, §5.9): the lock, the graph run, ``job_reports`` and the failure notice.
Migrations 029–031 are required before execution; 031 refusals also leave an error report at manual pre-check.

One *execution* — a first run or a resume — takes the ``daily:run`` session lock on a connection of its own, runs
``workflow.run`` / ``workflow.resume``, appends one ``job_reports`` row (``task = 'daily'``, the run's ``run_id``;
resume rows carry ``trigger_source = 'resume'``) and, when the run failed, sends one failure notice. It does not use
``_harness.run_task``: the status comes from the nodes' effective rows (D13), not from ``summary.errors``.
"""
from __future__ import annotations
import investment_assistant.core.schema as core_schema
import investment_assistant.workflow.repo as workflow_repo

import argparse
import json
import logging
import sys
import time
import uuid
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from investment_assistant.config import AssistantConfig, load_config
from investment_assistant.core import locks
from investment_assistant.tasks import _harness
from investment_assistant.core import redact as families, tx as service, stages as stages_module
from investment_assistant.workflow import graph as workflow
from investment_assistant.tasks import filings, ports
from investment_assistant.core import tx as core_tx

logger = logging.getLogger(__name__)

SH = ZoneInfo('Asia/Shanghai')
JOB_STATUS = {'success': 'success', 'degraded': 'degraded', 'failed': 'error'}      # run status → job_reports.status


def new_run_id() -> str:
    """``daily-<UTC yyyymmddHHMMSS>-<8 hex>``: the thread id, ``job_reports.run_id`` and the detail-page id."""
    return f"daily-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"


def ensure_ready() -> None:
    """Manual-entry migration pre-checks raise Unavailable (HTTP 503) before scheduling work.

    A missing 031 also attempts to append an error report, without sending an execution-failure notice.
    If the initial database probes cannot answer, the run itself reports what is wrong.
    """
    try:
        with core_tx.read_only_tx() as conn:
            ready = core_schema.daily_ready(conn)
    except Exception:   # noqa: BLE001 - nothing to ask
        return
    if not ready:
        raise service.Unavailable('migration_028_missing')
    try:
        with core_tx.read_only_tx() as conn:
            executable = core_schema.executable_ready(conn)
    except Exception:   # noqa: BLE001 - nothing to ask
        return
    if not executable:
        raise service.Unavailable('migration_029_missing')       # v2.9 D36
    if not _accounts_ready():
        raise service.Unavailable('migration_030_missing')
    if not _five_layers_ready():
        # A refused manual pre-check has no graph execution, but must leave an auditable error.
        moment = datetime.now(UTC)
        try:
            _harness._record(task='daily', run_id=new_run_id(), status='error', started_at=moment,
                             finished_at=datetime.now(UTC), trigger_source='manual',
                             summary={'error': 'migration_031_missing', 'run_status': 'failed'})
        except Exception as exc:  # noqa: BLE001 - preserve the HTTP readiness error
            logger.error('daily job report not written: %s', type(exc).__name__)
        raise service.Unavailable('migration_031_missing')


def _trigger(trigger_source: str) -> str:
    return 'scheduled' if trigger_source == 'scheduled' else 'manual_batch'


def _failure_payload(config: AssistantConfig, result: dict[str, Any]) -> dict[str, Any]:
    """The inputs of ``templates.daily_failed`` from an execution result: the node that stopped the chain, else the first
    node a stage booked as ``failed`` (``result['failure']``, ``resumable`` false: the chain ran to the end, so the
    notice has no resume line); a run that never started a graph has neither."""
    summary = result.get('summary') or {}
    failure = result.get('failure')
    titles = {stage.id: stage.title for stage in stages_module.STAGES}
    session = result.get('session') or summary.get('session')
    payload: dict[str, Any] = {'session': session, 'run_id': result['run_id'],
                               'public_url': config.workbench.public_url,
                               'reason': str((failure or {}).get('reason') or summary.get('error') or '')[:300]}
    if failure is not None:
        node = failure['node']
        payload.update(node_title=titles.get(node, node), retried=bool(failure.get('retried')),
                       skipped=int(failure.get('skipped') or 0))
        if session and failure.get('resumable', True):          # only a stopped chain has a next node to resume from
            deadline = workflow.resume_deadline(date.fromisoformat(session)).astimezone(SH)
            payload['deadline_text'] = deadline.strftime('%Y-%m-%d %H:%M') + '（北京时间）'
    else:
        payload.update(node_title='每日工作流', retried=False)
    return payload


def _notify_failure(config: AssistantConfig, result: dict[str, Any], notify_cfg: Any) -> dict[str, Any] | None:
    """One failure notice per execution (D14); a notifier error never changes the run's result."""
    from investment_assistant.notify import notifier, templates

    try:
        notify_cfg = notify_cfg if notify_cfg is not None else _effective_notify(config)
        return notifier.send_notifications('workbench', templates.daily_failed(_failure_payload(config, result)),
                                           notify_cfg)
    except Exception as exc:    # noqa: BLE001 - reported in the summary, never raised
        logger.warning('daily failure notice not sent: %s', type(exc).__name__)
        return {'sent': False, 'reason': f'notice_failed: {type(exc).__name__}'}


def _effective_notify(config: AssistantConfig) -> Any:
    from investment_assistant.services.settings import effective_notify_config

    return effective_notify_config(config.notify)


def _hit_not_connected(rows: list[dict[str, Any]], attempt: int) -> bool:
    """Whether this execution's ``agent`` or ``review`` rows (``attempt`` is the execution's own number, so a resume
    that did not re-run them does not count) show the Agent connection as unusable: the node skipped for
    ``agent_unavailable: not_connected``, or a unit that hit ``not_connected`` mid-node."""
    for row in rows:
        if row['stage'] not in ('agent', 'review') or int(row.get('attempt') or 1) != attempt:
            continue
        if row['status'] == 'skipped' and row.get('reason') == 'agent_unavailable: not_connected':
            return True
        reasons = [str(item.get('reason') or '') for item in (row.get('failures') or []) + (row.get('skips') or [])]
        reasons += [str(item.get('detail') or '') for item in row.get('degradations') or []
                    if item.get('kind') == 'agent_unavailable']
        if any(reason == 'not_connected' or reason.endswith(': not_connected') for reason in reasons):
            return True
    return False


def _notify_connection(config: AssistantConfig, result: dict[str, Any], notify_cfg: Any) -> dict[str, Any] | None:
    """The once-per-episode "ChatGPT connection lost" notice (v2.8 §8.8, §13.2): None when this execution's Agent nodes
    did not hit ``not_connected``; otherwise ``{sent, reason, checked_at}``. A failure is told exactly once: the base of
    the look is the check time of the last execution that *sent* the notice (``connection_notice.checked_at``), so an
    execution in between that did not hit (a holiday, a run that stopped before ``agent``, a resume from ``push``)
    cannot move it past an untold failure, and a failure during an execution is not told again by the next one.
    ``reason`` is ``not_failed`` (the connection is not lost any more) or ``already_reported`` when nothing is sent. A
    notice that could not be sent is retried by the next execution. Never raises."""
    from investment_assistant.notify import notifier, templates
    from investment_assistant.core.llm import connection as agent_connection

    attempt = result.get('attempt')
    if not attempt:
        return None                                  # an execution that never started a graph has no Agent nodes
    checked_at = datetime.now(UTC)
    try:
        with core_tx.read_only_tx() as conn:
            rows = workflow_repo.stage_rows_for_run(conn, result['run_id'])
            base = workflow_repo.last_connection_notice_at(conn)
        if not _hit_not_connected(rows, int(attempt)):
            return None
        stamp = checked_at.isoformat()
        if not agent_connection.first_failure_since_ok(since=base):
            try:
                lost = agent_connection.status_view().get('status') == 'needs_login'
            except Exception:    # noqa: BLE001 - only the wording of the reason depends on it
                lost = True
            return {'sent': False, 'reason': 'already_reported' if lost else 'not_failed', 'checked_at': stamp}
        cfg = notify_cfg if notify_cfg is not None else _effective_notify(config)
        out = notifier.send_notifications('workbench', templates.agent_not_connected({'run_id': result['run_id']}), cfg)
        return {'sent': bool(out.get('sent')), 'reason': out.get('reason'), 'checked_at': stamp}
    except Exception as exc:    # noqa: BLE001 - reported in the summary, never raised (and never the exception text)
        logger.warning('agent connection notice not sent: %s', type(exc).__name__)
        return {'sent': False, 'reason': f'notice_failed: {type(exc).__name__}', 'checked_at': checked_at.isoformat()}


def _report(result: dict[str, Any], *, started_at: datetime, trigger_source: str, config: AssistantConfig,
            notify_cfg: Any) -> dict[str, Any]:
    """Append the execution's ``job_reports`` row, send the one failure notice of a failed run and the connection-lost
    notice when this execution's Agent nodes were skipped for ``not_connected`` (once per failure episode)."""
    status = JOB_STATUS[result['status']]
    summary = result['summary']
    if result['status'] == 'failed':
        summary['failure_notice'] = _notify_failure(config, result, notify_cfg)
    connection_notice = _notify_connection(config, result, notify_cfg)
    if connection_notice is not None:
        summary['connection_notice'] = connection_notice
    session = result.get('session')
    day = date.fromisoformat(session) if session else None
    try:
        _harness._record(task='daily', run_id=result['run_id'], status=status, started_at=started_at,
                         finished_at=datetime.now(UTC), summary=summary, target_start_date=day, target_end_date=day,
                         trigger_source=trigger_source)
    except Exception as exc:    # noqa: BLE001 - the execution happened; its record failing is logged, not raised
        logger.error('daily job report not written: %s', type(exc).__name__)
    return {'run_id': result['run_id'], 'task': 'daily', 'status': status, 'summary': summary,
            'run_status': result['status']}


def _refused(run_id: str, reason: str, *, started_at: datetime, trigger_source: str, config: AssistantConfig,
             notify_cfg: Any, notice: bool = True, session: str | None = None) -> dict[str, Any]:
    """An execution that never started a graph (migration 028 missing, no checkpoints, lock busy): one error row."""
    result = {'run_id': run_id, 'status': 'failed', 'summary': {'error': reason, 'run_status': 'failed'},
              'failed_node': None, 'failure': None, 'session': session}
    if not notice:
        status_row = {'run_id': run_id, 'task': 'daily', 'status': 'error', 'summary': result['summary'],
                      'run_status': 'failed'}
        try:
            _harness._record(task='daily', run_id=run_id, status='error', started_at=started_at,
                             finished_at=datetime.now(UTC), summary=result['summary'], trigger_source=trigger_source)
        except Exception as exc:    # noqa: BLE001
            logger.error('daily job report not written: %s', type(exc).__name__)
        return status_row
    return _report(result, started_at=started_at, trigger_source=trigger_source, config=config, notify_cfg=notify_cfg)


def _abort_reason(exc: BaseException) -> str:
    return f'aborted: {families.sanitize_reason(exc) or type(exc).__name__}'


def _ready() -> bool:
    try:
        with core_tx.read_only_tx() as conn:
            return core_schema.daily_ready(conn)
    except Exception:    # noqa: BLE001 - an unreadable database cannot run the workflow either
        return False


def _executable_ready() -> bool:
    """Migration 029 (v2.9 D36): the plan / trade reads of the new code need its columns and tables, so a database
    without it is refused before anything is read — no half-new, half-old run (no timeline-1 fallback)."""
    try:
        with core_tx.read_only_tx() as conn:
            return core_schema.executable_ready(conn)
    except Exception:    # noqa: BLE001 - an unreadable database cannot run the workflow either
        return False


def _accounts_ready() -> bool:
    try:
        with core_tx.read_only_tx() as conn:
            return core_schema.accounts_ready(conn)
    except Exception:
        return False


def _five_layers_ready() -> bool:
    try:
        with core_tx.read_only_tx() as conn:
            return core_schema.five_layers_ready(conn)
    except Exception:  # noqa: BLE001 - an unreadable database cannot run the workflow either
        return False


def run(config: AssistantConfig | None = None, *, trigger_source: str = 'scheduled', now: datetime | None = None,
        deps: service.Deps | None = None, run_id: str | None = None, checkpointer_factory=None, sleep=time.sleep,
        notify_cfg: Any = None, explicit_pool=None) -> dict[str, Any]:
    """One ``daily`` execution (the scheduler's and the manual entry's): lock → graph → report. ``DailyBusy`` for a
    manual run while another execution holds the lock; a scheduled one records ``error / daily_running`` instead.
    Returns ``{'run_id', 'task', 'status', 'summary', 'run_status'}`` (``status`` = the ``job_reports`` status)."""
    effective = config if config is not None else load_config()
    moment = now or datetime.now(UTC)
    rid = run_id or new_run_id()
    started_at = datetime.now(UTC)
    scheduled = trigger_source == 'scheduled'
    try:
        with locks.exclusive(), locks._active(rid):
            if not _ready():            # D36: no half-new, half-old runs
                return _refused(rid, 'migration_028_missing', started_at=started_at, trigger_source=trigger_source,
                                config=effective, notify_cfg=notify_cfg)
            if not _executable_ready():     # v2.9 D36: the same rule for migration 029, checked before any plan / trade read
                return _refused(rid, 'migration_029_missing', started_at=started_at, trigger_source=trigger_source,
                                config=effective, notify_cfg=notify_cfg)
            if not _accounts_ready():
                return _refused(rid, 'migration_030_missing', started_at=started_at, trigger_source=trigger_source,
                                config=effective, notify_cfg=notify_cfg)
            if not _five_layers_ready():
                return _refused(rid, 'migration_031_missing', started_at=started_at, trigger_source=trigger_source,
                                config=effective, notify_cfg=notify_cfg)
            try:
                result = workflow.run(run_id=rid, trigger=_trigger(trigger_source), now=moment, deps=deps,
                                      explicit_pool=explicit_pool, checkpointer_factory=checkpointer_factory,
                                      sleep=sleep, notify_cfg=notify_cfg if notify_cfg is not None else _effective_notify(effective),
                                      config=effective, send=ports.send, sec_scan=filings._scan)
            except workflow.CheckpointUnavailable:
                return _refused(rid, 'checkpoint_unavailable', started_at=started_at, trigger_source=trigger_source,
                                config=effective, notify_cfg=notify_cfg)
            except Exception as exc:    # noqa: BLE001 - whatever else: the execution still gets its row and notice
                return _refused(rid, _abort_reason(exc), started_at=started_at, trigger_source=trigger_source,
                                config=effective, notify_cfg=notify_cfg)
            return _report(result, started_at=started_at, trigger_source=trigger_source, config=effective,
                           notify_cfg=notify_cfg)
    except locks.DailyBusy:
        if not scheduled:
            raise
        return _refused(rid, 'daily_running', started_at=started_at, trigger_source=trigger_source,
                        config=effective, notify_cfg=notify_cfg, notice=False)


def resume(run_id: str, node: str, *, now: datetime | None = None, deps: service.Deps | None = None,
           config: AssistantConfig | None = None, checkpointer_factory=None, sleep=time.sleep,
           notify_cfg: Any = None) -> dict[str, Any]:
    """Continue a failed run from ``node`` (spec §5.4): lock → ``workflow.resume`` → a ``job_reports`` row with
    ``trigger_source = 'resume'``. ``DailyBusy``, ``workflow.ResumeRejected``, ``LookupError`` and
    ``service.Unavailable`` propagate without starting the graph. A missing 031 additionally records an error
    execution and sends the existing failure notice before raising; earlier migration refusals write nothing."""
    effective = config if config is not None else load_config()
    started_at = datetime.now(UTC)
    with locks.exclusive(), locks._active(run_id):
        if not _executable_ready():         # v2.9 D36: the same gate as a first run; refused before anything is written
            raise service.Unavailable('migration_029_missing')
        if not _accounts_ready():           # the current graph includes accounts even when entered through the CLI
            raise service.Unavailable('migration_030_missing')
        if not _five_layers_ready():
            _refused(run_id, 'migration_031_missing', started_at=started_at, trigger_source='resume',
                     config=effective, notify_cfg=notify_cfg)
            raise service.Unavailable('migration_031_missing')
        try:
            result = workflow.resume(run_id, node, now=now or datetime.now(UTC), deps=deps,
                                     checkpointer_factory=checkpointer_factory, sleep=sleep,
                                     notify_cfg=notify_cfg if notify_cfg is not None else _effective_notify(effective),
                                     config=effective, send=ports.send, sec_scan=filings._scan)
        except (workflow.ResumeRejected, LookupError, ValueError, service.Unavailable):
            raise                       # refused before anything ran: the caller maps these, nothing is written
        except workflow.CheckpointUnavailable:
            return _refused(run_id, 'checkpoint_unavailable', started_at=started_at, trigger_source='resume',
                            config=effective, notify_cfg=notify_cfg)
        except Exception as exc:    # noqa: BLE001
            return _refused(run_id, _abort_reason(exc), started_at=started_at, trigger_source='resume',
                            config=effective, notify_cfg=notify_cfg)
        return _report(result, started_at=started_at, trigger_source='resume', config=effective,
                       notify_cfg=notify_cfg)


# ---------------------------------------------------------------- command line

def _show(run_id: str) -> int:
    """The node table of a run — status, duration, attempt, failures — and no raw summary."""
    with core_tx.read_only_tx() as conn:
        rows = workflow_repo.stage_rows_for_run(conn, run_id)
    if not rows:
        print(f'没有这次运行：{run_id}', file=sys.stderr)
        return 2
    effective = workflow.effective_rows(rows)
    print(f'{run_id}  {workflow.run_status(rows)}')
    for stage_id in workflow.NODES:
        row = effective.get(stage_id)
        if row is None:
            print(f'  {stage_id:<12} 未开始')
            continue
        seconds = (row['duration_ms'] or 0) / 1000
        print(f"  {stage_id:<12} {row['status']:<9} {seconds:>8.1f}s  attempt {row.get('attempt', 1)}  "
              f"失败目标 {len(row.get('failures') or [])}  {row.get('reason') or ''}".rstrip())
    return 0


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.tasks.daily',
                                     description='每日工作流：运行、续跑、查看。')
    commands = parser.add_subparsers(dest='command', required=True)
    run_cmd = commands.add_parser('run', help='前台跑一次')
    run_cmd.add_argument('--manual', action='store_true', help='按手动运行（不推送）')
    resume_cmd = commands.add_parser('resume', help='从失败的节点续跑')
    resume_cmd.add_argument('--run-id', required=True)
    resume_cmd.add_argument('--node', required=True)
    show_cmd = commands.add_parser('show', help='打印一次运行的节点表')
    show_cmd.add_argument('--run-id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'show':
            return _show(args.run_id)
        if args.command == 'run':
            out = run(trigger_source='manual' if args.manual else 'scheduled')
        else:
            out = resume(args.run_id, args.node)
    except locks.DailyBusy:
        print('每日工作流正在运行（daily_running）', file=sys.stderr)
        return 2
    except workflow.ResumeRejected as exc:
        print(f'{exc.code}：{exc.message}', file=sys.stderr)
        return 2
    except (LookupError, ValueError, service.Unavailable) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({key: out[key] for key in ('run_id', 'status', 'run_status')}, ensure_ascii=False))
    return 0 if out['status'] != 'error' else 1


if __name__ == '__main__':
    raise SystemExit(main())
