from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from investment_assistant.core import runner, tx as service
from investment_assistant.views.runs import detail as run_detail

router = APIRouter()


@router.get("/api/runs/{run_id}")
def get_run(run_id: str):
    rec = runner.get(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return rec


@router.get("/api/runs/{run_id}/detail")
def get_run_detail(run_id: str):
    """v2.8 run detail (spec §7.1): read-only; 400 malformed id, 404 no such run, 503 database / schema not ready."""
    try:
        return run_detail.detail(run_id, now=datetime.now(UTC))
    except service.Unavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
