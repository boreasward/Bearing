"""Thin wrappers over the Data menu use cases (design 2026-10-26 §7–8).

No business logic here: parse the path/query/body, pass now, map data_view's
errors to status codes — everything else lives in workbench/data_view.py."""
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from investment_assistant.views import data as data_view
from investment_assistant.core import tx as service

router = APIRouter()


def _now() -> datetime:
    return datetime.now(UTC)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except service.Unavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except data_view.FetchBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _opt(value: str | None) -> str | None:
    return value or None   # '' 与 None 同义；校验留给用例层


@router.get('/api/data/families')
def overview():
    return _call(data_view.overview, now=_now())


@router.get('/api/data/families/{family_id}')
def family_detail(family_id: str):
    return _call(data_view.family_detail, family_id, now=_now())


@router.post('/api/data/families/{family_id}/fetch')
def start_fetch(family_id: str, payload: dict[str, Any] = Body(default_factory=dict)):
    return _call(data_view.start_fetch, family_id, payload.get('ticker'))


@router.get('/api/data/browse/{family_id}')
def browse(family_id: str, ticker: str | None = None, series: str | None = None):
    return _call(data_view.browse, family_id, now=_now(),
                 ticker=_opt(ticker), series=_opt(series))
