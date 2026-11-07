"""GET/PUT /api/settings*：运行时与模型设置（spec §9.2 / v3 设计 §15.4）。保存成功后把
runtime 不同的旧对话置为只读（v3 设计 §15.3 切换边界：旧对话不迁移、只读）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from investment_assistant.api import deps
from investment_assistant.facts import plan_repo
from investment_assistant.runtime import settings as runtime_settings
from investment_assistant.runtime.base import RuntimeSpec

router = APIRouter(prefix="/api/settings")

REQUIRED_BODY = Body(...)  # 模块级单例：JSON 请求体（ruff B008）
ALL_CHATS = 1_000_000  # plan_repo.chats 的 limit 是必填整数；翻全部旧对话用一个大数


def _pi_dir() -> Path:
    """专用 Pi 配置目录；测试注入替身。"""
    return runtime_settings.default_pi_dir()


@router.get("")
def get_settings():
    def _read(conn):
        stored = plan_repo.settings(conn)
        return {key: stored.get(key) for key in ("runtime", "provider", "model")}

    return deps.ok(deps.read_tx(_read))


@router.get("/options")
def get_options():
    return deps.ok(runtime_settings.options(_pi_dir()))


@router.put("")
def put_settings(body: dict = REQUIRED_BODY):
    missing = [key for key in ("runtime", "provider", "model") if not str(body.get(key) or "").strip()]
    if missing:
        return JSONResponse({"error": f"invalid_argument: 缺少设置项 {('、'.join(missing))}"}, status_code=400)
    spec = RuntimeSpec(
        runtime=str(body["runtime"]),
        provider=str(body["provider"]),
        model=str(body["model"]),
    )

    def _save(conn):
        runtime_settings.save(conn, spec, pi_dir=_pi_dir())  # 不可用抛 ValueError，原因即 error
        for row in plan_repo.chats(conn, limit=ALL_CHATS):
            if row["runtime"] != spec.runtime:
                plan_repo.update_chat(conn, row["chat_id"], read_only=True)
        return spec

    try:
        saved = deps.write_tx(_save)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return deps.ok({"runtime": saved.runtime, "provider": saved.provider, "model": saved.model})
