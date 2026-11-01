"""The v2.7 ``workbench`` task layer (deleted from the product in v2.8), kept as a test helper.

The v2.7 batch tests (stage evidence, ``derivation stages``, ``job_reports`` rows around ``run_batch``) drive the batch
through this wrapper: a ``stages-3`` recorder, ``service.run_batch`` and the push stage after it. The product now runs
the ``daily`` graph (``tasks/daily.py``), whose own tests are ``test_daily_*.py``."""
from __future__ import annotations
import investment_assistant.workflow.batch as d_workflow_batch
import investment_assistant.workflow.nodes.push as d_workflow_nodes_push
from investment_assistant.tasks import ports
import investment_assistant.core.schema as core_schema

from datetime import UTC, datetime
from typing import Any

from investment_assistant.config import AssistantConfig, load_config
from investment_assistant.tasks._harness import run_task
import investment_assistant.core.tx as service
from investment_assistant.data.adapters import bars
import investment_assistant.workflow.recorder as stages_module
from investment_assistant.core import stages


def _timeline_ready() -> bool:
    """Migration 026 present (v2.5 D22), read on a read-only transaction; any failure counts as not ready."""
    try:
        with service.read_only_tx() as conn:
            return core_schema.timeline_ready(conn)
    except Exception:  # noqa: BLE001 - an unreadable database is booked as stages-1; the batch reports it
        return False


def _growth_ready() -> bool:
    """Migration 027 present (v2.7 D29), read on a read-only transaction; any failure counts as not ready."""
    try:
        with service.read_only_tx() as conn:
            return core_schema.growth_ready(conn)
    except Exception:  # noqa: BLE001 - an unreadable database is booked as the older version; the batch reports it
        return False


def run(config: AssistantConfig | None = None, *, trigger_source: str = 'scheduled', now: datetime | None = None,
        deps: service.Deps | None = None):
    effective = config if config is not None else load_config()
    moment = now or datetime.now(UTC)
    scheduled = trigger_source == 'scheduled'
    # Scheduled runs skip New York holidays; a manual run always targets the latest settled session.
    def batch(run_id: str) -> dict:
        # 阶段证据（v2.4 §4，v2.5 §7.1）：任务层建记录器，批处理记推送之前的阶段，推送阶段在这里记。
        # 迁移 026 未执行（D22）→ 本次按 stages-1 记 7 行；026 就绪、027 未执行（v2.7 D29）→ stages-2 记 8 行；
        # 都就绪 → stages-3 记 9 行。
        ready = _timeline_ready()
        growth = ready and _growth_ready()
        recorder = stages_module.StageRecorder(run_id, 'scheduled' if scheduled else 'manual_batch',
                                               bars.latest_closed_session(moment),
                                               version=stages.STAGES_VERSION_V3 if growth
                                               else stages.STAGES_VERSION_V2 if ready
                                               else stages.STAGES_VERSION_V1)
        failure: BaseException | None = None
        summary: dict | None = None
        try:
            # The batch reads its tiered pool from the watchlist table; the config watchlist stays for other tasks.
            # run_id 来自任务外壳，data_fetch_runs / screen_runs / signal_candidates 与 job_reports 同源（v2.4 §6.1）。
            summary = d_workflow_batch.run_batch(now=moment, deps=deps, run_id=run_id,
                                        skip_non_trading=scheduled,
                                        record_trigger='scheduled' if scheduled else 'manual_batch',
                                        stages=recorder, timeline_ready=ready, growth_ready=growth)
            if not scheduled:
                recorder.skip('push', 'manual_run')
                return summary  # 手动运行不推送
            from investment_assistant.services.settings import effective_notify_config
            notify_cfg = effective_notify_config(effective.notify)
            closed = summary.get('status') == 'skipped'
            acc = recorder.open('push')
            if closed:
                acc.detail['closed'] = True
            try:
                with acc.timing():
                    if closed:
                        summary['push'] = d_workflow_nodes_push.push_closed(now=moment, notify_cfg=notify_cfg, send=ports.send)
                    else:
                        summary['push'] = d_workflow_nodes_push.push(now=moment, force=False, summary=summary, send=ports.send,
                                                       notify_cfg=notify_cfg)
            except BaseException as exc:
                recorder.finish('push', failed=f'aborted: {exc}')
                raise
            d_workflow_nodes_push.account_push(recorder, acc, summary['push'])
            return summary
        except BaseException as exc:
            failure = exc
            raise
        finally:
            stage_status = recorder.close(failure)
            if failure is None and summary is not None:
                summary['stage_status'] = stage_status

    return run_task('workbench', batch, config=effective, trigger_source=trigger_source, notify=False,
                    pass_run_id=True)
