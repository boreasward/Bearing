"""GET /api/retrospect：复盘块的读接口（spec §10 复盘块 / §5）——执行复盘记录、周报与
规则检验两张表一次返回；week 参数切换周报所在周（缺省当前交易日所在周）。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from investment_assistant.api import deps
from investment_assistant.core.texts import money_value, quantity_value
from investment_assistant.data.calendar import last_completed_trading_day
from investment_assistant.facts.retrospect import retrospect_view
from investment_assistant.toolsurface.env import make_env

router = APIRouter(prefix="/api")


def _tidy_rotations(view: dict) -> dict:
    """换仓块的数字取页面口径（与其余接口一致）：股数去存储尾零，手续费取到分（core.texts）。"""
    block = view["rotations"]
    for row in block["records"]:
        row["sell_quantity"] = quantity_value(row["sell_quantity"])
        row["buy_shares"] = quantity_value(row["buy_shares"])
        row["fee_estimate"] = money_value(row["fee_estimate"])
    block["summary"]["fee_total"] = money_value(block["summary"]["fee_total"])
    return view


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
            lambda conn: _tidy_rotations(
                retrospect_view(
                    conn, session=env.session, cutoff_at=env.cutoff_at, cfg=env.cfg, limit=limit, week_end=week_end
                )
            )
        )

    def _view():
        try:
            env = deps.web_env()
        except ValueError as exc:
            if str(exc).partition(":")[0].strip() != "no_run":
                raise
            # 真正的空库（连运行记录都没有）在这里是合法的空复盘视图（spec §10「还没有已结束
            # 的计划」/B5/RF6）：交易日退到日历上最近一个已收盘的交易日；其他路由维持 no_run 404
            env = make_env(session=last_completed_trading_day(), mode="chat", agent_session="web", origin="web")
        return _read(env)

    return deps.ok_or_error(_view)
