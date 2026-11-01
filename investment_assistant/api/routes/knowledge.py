"""Thin wrappers over the knowledge layer (v2.6 spec §11). Pure file reads: no database, so these keep
working when the database is down. A broken card directory answers 503 with the whole problem list."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from investment_assistant.core import knowledge

router = APIRouter()


def _call(view, *args):
    try:
        return view(knowledge.catalog(), *args)
    except knowledge.KnowledgeError as exc:
        return JSONResponse({'error': f'概念卡加载失败：{exc}', 'problems': exc.problems}, status_code=503)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get('/api/knowledge/concepts')
def concepts():
    return _call(knowledge.index_view)


@router.get('/api/knowledge/concepts/{ref}')
def concept(ref: str):
    return _call(knowledge.card_view, ref)


@router.get('/api/knowledge/principles')
def principles():
    return _call(knowledge.principles_view)
