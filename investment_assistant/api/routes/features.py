"""The four feature switches (v2.12–v2.15 spec §9, §12.1): current values with the real change history, and one
change at a time. Opening and closing both need a reason; a repeated ``request_id`` replays the first answer.
Errors follow the shared ``_call`` map (``migration_031_missing`` 503, a malformed body 400, a reused
``request_id`` with other content 409) plus ``unknown_flag`` 404."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from investment_assistant.api.routes.workbench import _call
from investment_assistant.core import features as core_features
from investment_assistant.services import settings

router = APIRouter(prefix='/api/settings/features')


@router.get('')
def read():
    return _call(settings.read_features)


@router.post('/{flag}')
def change(flag: str, payload: dict[str, Any] = Body(default_factory=dict)):
    if flag not in core_features.FLAGS:
        raise HTTPException(status_code=404, detail='unknown_flag')
    return _call(settings.set_feature, flag, enabled=payload.get('enabled'), reason=payload.get('reason'),
                 request_id=payload.get('request_id'))
