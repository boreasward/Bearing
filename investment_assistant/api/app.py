from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from investment_assistant.api import auth


class CompatJSONResponse(JSONResponse):
    """JSON 序列化：ensure_ascii=False, default=str, indent=2。"""

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
    app = FastAPI(
        title="Hermes Investment Assistant",
        default_response_class=CompatJSONResponse,
    )

    # Add auth middleware
    app.add_middleware(AuthMiddleware)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else "error"
        if exc.status_code == 404 and detail == "Not Found":
            detail = "not found"  # 未知路径统一返回 {"error": "not found"}
        return CompatJSONResponse({"error": detail}, status_code=exc.status_code,
                                  headers=getattr(exc, "headers", None))

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError):
        return CompatJSONResponse({"error": str(exc)}, status_code=400)  # ValueError 返回 400

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return CompatJSONResponse({"error": f"invalid request: {exc}"}, status_code=400)  # 校验错误返回 400，不是 FastAPI 默认的 422

    # 两个兜底路由必须最后注册：新的 /api/... 路由要在它们之前 include_router，否则会被拦成 404。
    @app.get("/{full_path:path}", include_in_schema=False)
    def _static(full_path: str):
        from investment_assistant.api import static_files

        resp = static_files.static_response_for_path("/" + full_path)
        if resp is None:
            raise HTTPException(status_code=404, detail="not found")
        return Response(content=resp.body, media_type=resp.content_type, status_code=resp.status)

    @app.api_route("/{full_path:path}", methods=["POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def _catch_all_methods(full_path: str):
        raise HTTPException(status_code=404, detail="not found")

    return app
