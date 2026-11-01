from datetime import UTC, datetime
from typing import Any

import psycopg
from fastapi import APIRouter, Body, HTTPException

from investment_assistant.services import settings
from investment_assistant.core import tx as service
from investment_assistant.core.llm import connection as agent_connection
from investment_assistant.core.llm.chatgpt import ChatGPTError

router = APIRouter()


@router.get("/api/settings/notify")
def get_notify():
    return settings.read_notify_view()


@router.patch("/api/settings/notify")
def patch_notify(payload: dict[str, Any] = Body(default_factory=dict)):
    return settings.update_notify(payload)


@router.post("/api/settings/notify/test")
def test_notify(payload: dict[str, Any] = Body(default_factory=dict)):
    channel = payload.get("channel")
    if not channel:
        raise HTTPException(status_code=400, detail="channel required")
    return settings.test_notify_channel(channel, url=payload.get("url"), email=payload.get("email"))


@router.get("/api/settings/env")
def env_status():
    return settings.read_env_status()


# --- Agent connection (v2.8 §8.9, §11.1) ---
# A response carries the status view, a login URL or a model list — never a token, ``client_id``, ``host_id``,
# ``subject`` or the whole e-mail — and an error carries only its safe code (never a response text).

_CODE_STATUS = {'not_connected': 409, 'login_pending': 409, 'model_not_listed': 400, 'migration_028_missing': 503}


def _connection(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ChatGPTError as exc:
        raise HTTPException(status_code=_CODE_STATUS.get(exc.code, 502), detail=exc.code) from None
    except service.Unavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except psycopg.OperationalError:
        raise HTTPException(status_code=503, detail='数据库不可达') from None


def _now() -> datetime:
    return datetime.now(UTC)


@router.get("/api/settings/agent-connection")
def get_agent_connection():
    return _connection(agent_connection.status_view)


@router.post("/api/settings/agent-connection/login")
def start_agent_login(payload: dict[str, Any] = Body(default_factory=dict)):
    """Open the one-shot loopback listener and answer with the authorize URL; 409 ``login_pending`` while a login is
    still waiting (cancel it first), 502 ``discovery_failed`` when the issuer's configuration cannot be verified."""
    if _connection(agent_connection.status_view)['pending'] is not None:
        raise HTTPException(status_code=409, detail='login_pending')
    return _connection(agent_connection.start_login, now=_now())


@router.post("/api/settings/agent-connection/cancel")
def cancel_agent_login(payload: dict[str, Any] = Body(default_factory=dict)):
    return _connection(agent_connection.cancel_login)


@router.delete("/api/settings/agent-connection")
def disconnect_agent():
    """Revoke at the issuer and clear the local credentials: ``{revoked}``; 409 ``not_connected`` when there is
    neither a connection nor a login to drop."""
    if _connection(agent_connection.status_view)['status'] == 'disconnected':
        raise HTTPException(status_code=409, detail='not_connected')
    return _connection(agent_connection.disconnect, now=_now())


@router.get("/api/settings/agent-connection/models")
def agent_models():
    return {"models": _connection(agent_connection.models)}


@router.put("/api/settings/agent-connection/model")
def select_agent_model(payload: dict[str, Any] = Body(default_factory=dict)):
    slug = payload.get("slug")
    if not isinstance(slug, str) or not slug.strip():
        raise HTTPException(status_code=400, detail="slug required")
    return _connection(agent_connection.select_model, slug.strip(), now=_now())
