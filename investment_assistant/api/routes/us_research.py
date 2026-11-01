import investment_assistant.analysis.fundamentals as analysis_fundamentals
from typing import Any

from fastapi import APIRouter, Body

from fastapi.responses import JSONResponse

from investment_assistant.analysis import fundamentals as guidance

router = APIRouter()


@router.post('/api/us-research/sync')
def sync(payload: dict[str, Any] = Body(default_factory=dict)):
    return JSONResponse(status_code=410, content={'error': 'use_data_menu'})


@router.get('/api/us-research/{ticker}')
def view(ticker: str, as_of: str | None = None):
    return analysis_fundamentals.view(ticker, as_of=as_of)


@router.get('/api/us-research/{ticker}/guidance')
def guidance_history(ticker: str, as_of: str | None = None):
    return {'records': guidance.list_records(ticker, as_of=as_of), 'consensus': '未接入一致预期'}


@router.post('/api/us-research/{ticker}/guidance')
def add_guidance(ticker: str, payload: dict[str, Any] = Body(...)):
    return {'record': guidance.append(ticker, payload)}
