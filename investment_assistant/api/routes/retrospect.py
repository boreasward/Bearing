"""GET /api/retrospect：复盘块的读接口（spec §10 复盘块 / §5）——执行复盘记录、周报与
规则检验两张表一次返回；week 参数切换周报所在周（缺省当前交易日所在周）。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from investment_assistant.api import deps
from investment_assistant.facts.retrospect import retrospect_view

router = APIRouter(prefix="/api")


def _bad(message: str) -> JSONResponse:
    return JSONResponse({"error": f"invalid_argument: {message}"}, status_code=400)


@router.get("/retrospect")
def get_retrospect(limit: int = 20, week: str | None = None):
    if not 1 <= limit <= 100:
        return _bad("limit 必须是 1–100 之间的整数")
    week_end = None  # None → retrospect_view 取当前交易日所在周
    if week is not None:
        try:
            week_end = date.fromisoformat(week)
        except ValueError:
            return _bad("week 需为 YYYY-MM-DD")
        if week_end.isoformat() != week:  # 契约只认 YYYY-MM-DD：fromisoformat 也认 20261104，拦掉
            return _bad("week 需为 YYYY-MM-DD")

    def _read(env):
        return env.read(
            lambda conn: retrospect_view(
                conn, session=env.session, cutoff_at=env.cutoff_at, cfg=env.cfg, limit=limit, week_end=week_end
            )
        )

    return deps.ok_or_error(lambda: _read(deps.web_env()))
