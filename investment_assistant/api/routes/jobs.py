from typing import Any

from fastapi import APIRouter, Body, HTTPException

from investment_assistant.api.http import parse_int
from investment_assistant.services import jobs
from investment_assistant.core import locks
from investment_assistant.core import tx as service
from investment_assistant.workflow import graph as workflow

router = APIRouter()


def _call(fn, *args, **kwargs):
    """Run a use case and map its errors (the error body stays ``{"error": "<code>"}``): ``daily_running`` and the
    resume refusals are 409 with their code as the text, no run is 404, a database / schema not ready is 503."""
    try:
        return fn(*args, **kwargs)
    except jobs.UnknownJobError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc    # 旧契约：未知任务 404
    except locks.DailyBusy as exc:
        raise HTTPException(status_code=409, detail="daily_running") from exc
    except workflow.ResumeRejected as exc:
        raise HTTPException(status_code=409, detail=exc.code) from exc
    except service.Unavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc    # 参数非法（如日期范围）


@router.get("/api/jobs/scheduled")
def scheduled():
    return jobs.scheduled_jobs()


@router.get("/api/jobs/reports")
def reports(task: str | None = None, limit: str | None = None):
    return jobs.job_reports(task=task, limit=parse_int(limit, default=50, minimum=1, maximum=200))


@router.post("/api/jobs/daily/resume", status_code=202)
def resume_daily(payload: dict[str, Any] = Body(default_factory=dict)):
    return _call(jobs.resume_daily, payload)


@router.post("/api/jobs/{name}/run")
def run_job(name: str, payload: dict[str, Any] = Body(default_factory=dict)):
    return _call(jobs.trigger_job, name, payload)


@router.patch("/api/jobs/scheduled/{name}")
def patch_job(name: str, payload: dict[str, Any] = Body(default_factory=dict)):
    return jobs.patch_scheduled_job(name, enabled=payload.get("enabled"), time_local=payload.get("time_local"))
