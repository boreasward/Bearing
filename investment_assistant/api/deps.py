"""Page-API deps: the web ToolEnv, transaction helpers and the tool-error envelope mapping.

页面表单自己不做校验（spec §10 末段）：写接口只把 JSON 转发给 toolsurface 的同一批宿主函数，
错误按 `code[: text]` 拆成 {"error": code, "detail": …}，资源缺失码映射 404，其余 400。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from inspect import Parameter, signature
from typing import Any

from investment_assistant.core import tx as core_tx
from investment_assistant.toolsurface.env import ToolEnv, make_env

# 这些码表示「要读写的对象不存在」，映射 404；其余 ValueError（校验拒绝）一律 400。
NOT_FOUND_CODES = frozenset({"no_run", "no_brief", "no_chat", "unknown_plan", "unknown_target", "unknown_research"})


def web_env(*, now: datetime | None = None) -> ToolEnv:
    """页面共用的 ToolEnv：chat 模式、origin='web'、固定 agent_session='web'，交易日取
    ToolEnv.latest_session 的值（最新一次成功运行的交易日）；没有运行记录抛 ValueError('no_run')。"""
    from investment_assistant.facts import repo as facts_repo

    with core_tx.read_only_tx() as conn:
        run = facts_repo.latest_ok_run(conn)
    if run is None:
        raise ValueError("no_run")
    return make_env(session=run["session"], mode="chat", agent_session="web", origin="web", now=now)


def read_tx(fn: Callable[[Any], Any]) -> Any:
    with core_tx.read_only_tx() as conn:
        return fn(conn)


def write_tx(fn: Callable[[Any], Any]) -> Any:
    with core_tx._tx() as conn:
        return fn(conn)


def forward(fn: Callable[..., Any], env: ToolEnv, kwargs: dict) -> Any:
    """把页面 JSON 转发给工具函数：键不齐按 invalid_argument 信封拒绝（与 dispatch 同一套约定），
    正文不做任何校验——那是宿主函数的事。"""
    params = {name: param for name, param in signature(fn).parameters.items() if name != "env"}
    for key in kwargs:
        if key not in params:
            raise ValueError(f"unknown_argument: {key}")
    for name, param in params.items():
        required = param.default is Parameter.empty and param.kind in (
            Parameter.POSITIONAL_OR_KEYWORD,
            Parameter.KEYWORD_ONLY,
        )
        if required and name not in kwargs:
            raise ValueError(f"missing_argument: {name}")
    return fn(env, **kwargs)


def ok(data: Any):
    """成功响应：payload 先过 core_tx._jsonable（datetime→isoformat 的 ISO 8601 T 形式、
    Decimal/UUID→str），再用 CompatJSONResponse；不经过 FastAPI 的 jsonable_encoder（它会把
    Decimal 转成 float，违反“金额为字符串”）。"""
    from investment_assistant.api.app import CompatJSONResponse
    from investment_assistant.core.tx import _jsonable

    return CompatJSONResponse(_jsonable(data))


def ok_or_error(result: Callable[[], Any]):
    """执行一次调用：成功包成 JSON 响应；ValueError 按 `code[: text]` 拆成
    {"error": code, "detail": …}（携带 detail 的 ToolError 用它自己的 detail），no_run 等
    资源缺失码映射 404，其余校验错误 400。"""
    from investment_assistant.api.app import CompatJSONResponse
    from investment_assistant.core.tx import _jsonable

    try:
        data = result()
    except ValueError as exc:
        code, _, text = str(exc).partition(":")
        code = code.strip()
        payload = {"error": code}
        detail = getattr(exc, "detail", None)
        if detail is None:
            detail = text.strip() or None
        if detail is not None:
            payload["detail"] = _jsonable(detail)
        return CompatJSONResponse(payload, status_code=404 if code in NOT_FOUND_CODES else 400)
    return ok(data)
