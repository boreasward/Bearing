"""The research second entrance (v2.13 spec §6.3, §12.1): company dossiers on request.

``POST /api/research`` queues one dossier (a busy ``daily`` run only queues it — D43 — the request is still
accepted and answers ``queued``); the read side lists requests and shows one request's version chain.
Errors follow the shared ``_call`` map (``invalid_ticker`` 400, ``migration_031_missing`` 503) plus the two
guards of this entrance: ``feature_disabled`` and ``research_quota`` are 409."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from investment_assistant.analysis import research_requests
from investment_assistant.api.routes.workbench import _call
from investment_assistant.core import tx as service

router = APIRouter(prefix='/api/research')

STATUSES = ('queued', 'running', 'waiting_data', 'concluded', 'closed')
CLOSE_REASON = '手动结束'


def _now() -> datetime:
    return datetime.now(UTC)


def _id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(status_code=400, detail='id must be a UUID') from None


@router.post('')
def create(payload: dict[str, Any] = Body(default_factory=dict)):
    try:
        made = _call(research_requests.create, ticker=payload.get('ticker'), question=payload.get('question'),
                     horizon_days=payload.get('horizon_days'), request_id=payload.get('request_id'), now=_now())
    except research_requests.FeatureDisabled as exc:
        raise HTTPException(status_code=409, detail='feature_disabled') from exc
    except research_requests.ResearchQuota as exc:
        raise HTTPException(status_code=409, detail='research_quota') from exc
    return {'id': made['id'], 'status': made['status']}


@router.get('')
def listing(status: str | None = None):
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=400, detail=f'status must be one of {", ".join(STATUSES)}')
    return service._jsonable({'items': _call(research_requests.list_requests, status=status, now=_now())})


@router.get('/{request_id}')
def detail(request_id: str):
    found = _call(research_requests.detail, _id(request_id), now=_now())
    if found is None:
        raise HTTPException(status_code=404, detail='research_request_not_found')
    return service._jsonable(found)


@router.post('/{request_id}/close')
def close(request_id: str, payload: dict[str, Any] = Body(default_factory=dict)):
    reason = payload.get('reason')
    row = _call(research_requests.close, request_id=_id(request_id), now=_now(),
                reason=reason.strip() if isinstance(reason, str) and reason.strip() else CLOSE_REASON)
    return service._jsonable({'id': row['id'], 'status': row['status'], 'closed_reason': row['closed_reason']})
