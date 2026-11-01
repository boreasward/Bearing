from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Any, Callable

from investment_assistant import db
from investment_assistant.config import load_config
from investment_assistant.db import connect
from investment_assistant.core import locks, runner
from investment_assistant.tasks.scheduler import MANUAL_ONLY, REGISTRY


def _with_conn(fn: Callable[[Any], Any]) -> Any:
    database_url = os.environ["INVESTMENT_ASSISTANT_DATABASE_URL"]
    with connect(database_url) as conn:
        return fn(conn)


def _has_db() -> bool:
    return bool(os.environ.get("INVESTMENT_ASSISTANT_DATABASE_URL"))


def scheduled_jobs() -> dict[str, Any]:
    from investment_assistant.services.health import check_scheduler
    from investment_assistant.tasks import catalog

    scheduler_status = check_scheduler()
    if not _has_db():
        return {"jobs": [], "degraded": True, "scheduler": scheduler_status}
    jobs = _with_conn(db.list_scheduled_jobs)
    for job in jobs:
        job["description"] = catalog.describe(job["name"])
    # Manual-only registry jobs have no scheduled_jobs row: list them so the task center can run them by date.
    present = {job["name"] for job in jobs}
    for name in sorted(MANUAL_ONLY - present):
        jobs.append({"name": name, "manual_only": True, "enabled": False, "next_run_at": None,
                     "time_local": None, "weekday_mask": None, "timezone": None,
                     "description": catalog.describe(name)})
    return {"jobs": jobs, "degraded": False, "scheduler": scheduler_status}


def job_reports(task: str | None = None, limit: int = 50) -> dict[str, Any]:
    if not _has_db():
        return {"reports": [], "degraded": True}
    reports = _with_conn(lambda conn: db.list_job_reports(conn, task=task, limit=limit))
    return {"reports": reports, "degraded": False}


class UnknownJobError(ValueError):
    """Requested job name is not registered."""


MAX_RANGE_DAYS = 92  # 所有任务共用：一次最多回补约一个季度


def _date_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    raw_date = payload.get("date")
    raw_from = payload.get("from") or payload.get("start_date")
    raw_to = payload.get("to") or payload.get("end_date")
    if raw_date:
        start = end = date.fromisoformat(str(raw_date))
    elif raw_from or raw_to:
        if not raw_from or not raw_to:
            raise ValueError("from and to are both required for a range")
        start = date.fromisoformat(str(raw_from))
        end = date.fromisoformat(str(raw_to))
    else:
        return {}
    if end < start:
        raise ValueError("to must be greater than or equal to from")
    if (end - start).days > MAX_RANGE_DAYS:
        raise ValueError(f"date range is limited to {MAX_RANGE_DAYS} days")
    if end > date.today():
        raise ValueError("date must not be in the future")
    return {"start_date": start, "end_date": end}


def trigger_job(name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run a registered job in the background. ``daily`` and ``sec_backfill`` share the ``daily:run`` lock: while it is
    held ``locks.DailyBusy`` is raised (the route turns it into 409 ``daily_running``). A ``daily`` run gets its id up
    front and returns it as ``daily_run_id`` (the id of ``job_reports`` / the detail page)."""
    if name not in REGISTRY:
        raise UnknownJobError(f"unknown job: {name}")
    config = load_config()
    kwargs = _date_kwargs(payload or {})
    if name == "sec_backfill" and not kwargs:
        raise ValueError("date or from/to is required for sec_backfill")
    if name in ("daily", "sec_backfill"):
        from investment_assistant.tasks import daily

        try:
            busy = locks.is_running()
        except Exception:   # noqa: BLE001 - no database to ask: the run itself reports what is wrong
            busy = False
        if busy:
            raise locks.DailyBusy()
    if name == "daily":
        daily.ensure_ready()        # 503 migration_028_missing instead of a 200 and a run that only records it
        rid = daily.new_run_id()    # the date arguments do not apply: it always targets the latest settled session
        run_id = runner.submit(name, lambda: REGISTRY[name](config, trigger_source="manual", run_id=rid))
        return {"run_id": run_id, "status": "pending", "daily_run_id": rid}
    run_id = runner.submit(name, lambda: REGISTRY[name](config, trigger_source="manual", **kwargs))
    return {"run_id": run_id, "status": "pending"}


def patch_scheduled_job(name: str, *, enabled=None, time_local=None) -> dict[str, Any]:
    if not _has_db():
        return {"updated": False, "degraded": True}
    _with_conn(lambda conn: db.update_scheduled_job(conn, name, enabled=enabled, time_local=time_local))
    return {"updated": True}


def resume_daily(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Continue a failed ``daily`` run from its failed node (spec §5.4): every pre-check runs here, synchronously and
    without writing a row; the continuation runs in the background. Returns the 202 body.

    ``ValueError`` (body without ``run_id`` / ``node``, a malformed id, a node not of ``stages-4``), ``LookupError``
    (no such run), ``workflow.ResumeRejected`` (``not_failed`` / ``node_mismatch`` / ``resume_window_closed``),
    ``locks.DailyBusy`` (the lock is taken) and ``service.Unavailable`` (migration 028 / 029 missing, no database) are the
    caller's to map."""
    from investment_assistant.tasks import daily
    from investment_assistant.views.runs import detail as run_detail
    from investment_assistant.workflow import graph as workflow

    body = payload or {}
    run_id, node = body.get("run_id"), body.get("node")
    if not isinstance(run_id, str) or not run_id or not isinstance(node, str) or not node:
        raise ValueError("run_id and node are required")
    if not run_detail.RUN_ID_RE.match(run_id):
        raise ValueError("run_id 格式不合法")
    info = workflow.resume_check(run_id, node, now=datetime.now(UTC))     # raises before anything is written
    daily.ensure_ready()        # migration 029 too (the background resume checks it as well): 503, not a lost 202
    if locks.is_running():
        raise locks.DailyBusy()
    runner_id = runner.submit("daily_resume", lambda: daily.resume(run_id, node))
    return {"run_id": run_id, "node": node, "status": "pending", "attempt": info["attempt"], "runner_id": runner_id}
