from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from investment_assistant.api import auth

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """启动时按 env 拉起调度守护线程；关闭时置位 stop_event 优雅退出。"""
    from investment_assistant.config import load_config
    from investment_assistant.tasks import scheduler as _scheduler

    stop_event: threading.Event | None = None
    database_url = os.environ.get("INVESTMENT_ASSISTANT_DATABASE_URL")
    if os.environ.get("SCHEDULER_ENABLED", "1") != "0" and database_url:
        stop_event = threading.Event()
        threading.Thread(
            target=_scheduler.run_forever,
            kwargs={
                "stop_event": stop_event,
                "database_url": database_url,
                "config": load_config(None),
                "tick_seconds": int(os.environ.get("SCHEDULER_TICK_SECONDS", "60")),
            },
            name="scheduler",
            daemon=True,
        ).start()
        logger.info("scheduler thread launched")
    else:
        logger.info("scheduler thread disabled (SCHEDULER_ENABLED=0 或未配置数据库)")
    try:
        yield
    finally:
        if stop_event is not None:
            stop_event.set()


def event_stream(q):
    """SSE 帧生成器：事件转 data: 帧，空闲 15s 发心跳后继续等待（永不主动断连，对齐旧 server 行为）。"""
    import queue as _queue

    from investment_assistant.core import runner

    try:
        while True:
            try:
                event = q.get(timeout=15)
            except _queue.Empty:
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
    finally:
        runner.unsubscribe(q)


class CompatJSONResponse(JSONResponse):
    """对齐旧 server 的序列化：ensure_ascii=False, default=str, indent=2。"""

    def render(self, content: Any) -> bytes:
        return json.dumps(content, ensure_ascii=False, default=str, indent=2).encode("utf-8")


class AuthMiddleware(BaseHTTPMiddleware):
    """检查 HTTP Basic 认证的 middleware。"""

    async def dispatch(self, request: Request, call_next):
        if not auth.authorize(request.headers.get("Authorization")):
            return CompatJSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Hermes Investment Assistant"'},
            )
        return await call_next(request)


def create_app() -> FastAPI:
    from investment_assistant.api.routes import ALL_ROUTERS

    app = FastAPI(
        title="Hermes Investment Assistant",
        default_response_class=CompatJSONResponse,
        lifespan=_lifespan,
    )

    # Add auth middleware
    app.add_middleware(AuthMiddleware)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else "error"
        if exc.status_code == 404 and detail == "Not Found":
            detail = "not found"  # 旧契约
        return CompatJSONResponse({"error": detail}, status_code=exc.status_code,
                                  headers=getattr(exc, "headers", None))

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError):
        return CompatJSONResponse({"error": str(exc)}, status_code=400)  # 旧 router 契约

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return CompatJSONResponse({"error": f"invalid request: {exc}"}, status_code=400)  # 旧 400 契约（非 422）

    for router in ALL_ROUTERS:
        app.include_router(router)

    from fastapi.responses import Response, StreamingResponse

    @app.get("/api/events")
    def _events():
        from investment_assistant.core import runner

        return StreamingResponse(event_stream(runner.subscribe()), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.get("/{full_path:path}", include_in_schema=False)
    def _static(full_path: str):
        from fastapi import HTTPException

        from investment_assistant.api import static_files

        resp = static_files.static_response_for_path("/" + full_path)
        if resp is None:
            raise HTTPException(status_code=404, detail="not found")
        return Response(content=resp.body, media_type=resp.content_type, status_code=resp.status)

    @app.api_route("/{full_path:path}", methods=["POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def _catch_all_methods(full_path: str):
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="not found")

    return app
