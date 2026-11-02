"""The in-system roadmap (v2.15 spec §8, §12.1, UI1): the records, the proposals and their decision, the briefs.

Errors follow the shared ``_call`` map (``migration_031_missing`` 503, ``LookupError`` 404, ``ValueError`` 400,
``Conflict`` 409): a decided proposal, a taken ``version_id`` or a reused ``request_id`` with other content is 409."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

from investment_assistant.api.routes.workbench import _call
from investment_assistant.optimize import briefs, proposals, roadmap

router = APIRouter(prefix='/api/roadmap')

PROPOSAL_STATUSES = ('proposed', 'approved', 'rejected', 'superseded')


def _now() -> datetime:
    return datetime.now(UTC)


@router.get('')
def overview():
    return _call(roadmap.view, now=_now())


@router.get('/export.md')
def export():
    text = _call(roadmap.export_markdown, now=_now())
    return Response(content=text, media_type='text/markdown; charset=utf-8')


@router.get('/proposals')
def proposal_list(status: str | None = None):
    if status is not None and status not in PROPOSAL_STATUSES:
        raise HTTPException(status_code=400, detail=f'status must be one of {", ".join(PROPOSAL_STATUSES)}')
    return _call(proposals.proposals_view, status=status)


@router.post('/proposals/{proposal_id}/decide')
def decide(proposal_id: int, payload: dict[str, Any] = Body(default_factory=dict)):
    return _call(proposals.decide, proposal_id, decision=payload.get('decision'), version_id=payload.get('version_id'),
                 reason=payload.get('reason'), request_id=payload.get('request_id'), now=_now())


@router.get('/versions/{version_id}')
def version(version_id: str):
    found = _call(roadmap.version_detail, version_id)
    if found is None:
        raise HTTPException(status_code=404, detail='version_not_found')
    return found


@router.get('/decisions/{decision_no}')
def decision(decision_no: str):
    try:
        number = int(decision_no)
    except ValueError:
        raise HTTPException(status_code=400, detail='decision_no must be an integer') from None
    found = _call(roadmap.decision_detail, number)
    if found is None:
        raise HTTPException(status_code=404, detail='decision_not_found')
    return found


@router.post('/briefs/{version_id}/retry')
def retry_brief(version_id: str):
    return _call(briefs.retry, version_id)
