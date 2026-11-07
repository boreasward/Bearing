"""/api/plans*：三组卡片、单份计划与变化记录、三个计划写入口（spec §12 计划与持仓块）。
写入口只把表单转发给工具面的同名宿主函数，同一套校验（spec §10 末段）。"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from investment_assistant.api import deps
from investment_assistant.data import repo as data_repo
from investment_assistant.execution import plan_line
from investment_assistant.execution.plan_levels import cent
from investment_assistant.execution.positions import PositionError, build
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts.holdings import stop_order_status
from investment_assistant.toolsurface import plans as plan_tools

router = APIRouter(prefix="/api/plans")

BODY = Body(...)  # 模块级单例：JSON 请求体（ruff B008）

GROUPS: dict[str, tuple[str, ...]] = {
    "holdings": ("open",),
    "pending": ("pending",),
    "ended": plan_line.ENDED,
}
EVENT_FIELDS = ("session", "event", "reason", "before", "after", "source", "created_at")


def _bad(message: str) -> JSONResponse:
    return JSONResponse({"error": f"invalid_argument: {message}"}, status_code=400)


def _card(conn, env, plan: dict) -> dict:
    """一张计划卡片（接口契约表）：价位与金额 Decimal（序列化成字符串），pending 卡片带
    close_above_entry（最新收盘高于入场区间，spec §6.3 第 4 条）与 missing_bar（缺当日日线，
    spec §6.3 末段「缺日线，未复查」）；非 pending 时两者均为 False。"""
    ticker = plan["ticker"]
    bars = data_repo.read_bars(conn, ticker, env.session, cutoff_at=env.cutoff_at)
    last_close = Decimal(str(bars[-1]["close"])) if bars else None
    try:
        position = build(
            facts_repo.list_trades(conn, ticker), splits=data_repo.split_events(conn, ticker, cutoff_at=env.cutoff_at)
        )["position"]
    except PositionError:
        position = None
    position_row = None
    next_action = None
    if plan["status"] == "open" and position is not None:
        position_row = {
            "quantity": position["quantity"],
            "avg_cost": position["avg_cost"],
            "last_close": last_close,
            "unrealized_pnl": None
            if last_close is None
            else position["quantity"] * (last_close - position["avg_cost"]),
            "unrealized_pct": None if last_close is None else last_close / position["avg_cost"] - 1,
        }
        shown = dict(plan)  # 给用户看的止损价取到分（与今日操作同一口径）
        if shown["stop"] is not None:
            shown["stop"] = cent(shown["stop"])
        if shown.get("confirmed_stop_order") is not None:
            shown["confirmed_stop_order"] = cent(shown["confirmed_stop_order"])
        unchecked = not bars or bars[-1]["price_date"] != env.session
        next_action = plan_line.next_action(plan=shown, position=position, unchecked=unchecked)
    return {
        "plan_id": plan["id"],
        "ticker": ticker,
        "status": plan["status"],
        "kind": plan["kind"],
        "levels": {
            "entry_low": plan["entry_low"],
            "entry_high": plan["entry_high"],
            "stop": plan["stop"],
            "target1": plan["target1"],
        },
        "initial_stop": plan["initial_stop"],
        "trailing": plan["trailing"],
        "shares": plan["shares"],
        "valid_from": plan["valid_from"],
        "valid_until": plan["valid_until"],
        "start_session": plan["start_session"],
        "max_hold_sessions": plan["max_hold_sessions"],
        "next_action": next_action,
        "position": position_row,
        "held_sessions": plan_line.held_sessions(plan["start_session"], env.session)
        if plan["status"] == "open" and plan["start_session"] is not None
        else None,
        "stop_order_status": stop_order_status(plan),
        "confirmed_stop_order": plan["confirmed_stop_order"],
        "outcome": plan["outcome"] or {},
        "thesis": plan["thesis"],
        "created_at": plan["created_at"],
        "ended_session": plan["ended_session"],
        "end_reason": plan["end_reason"],
        "close_above_entry": bool(
            plan["status"] == "pending"
            and plan["entry_high"] is not None
            and last_close is not None
            and last_close > plan["entry_high"]
        ),
        # 待入场计划缺当日日线（spec §6.3 末段，终审 F1）：与 brief_view / today_actions 同一判定
        "missing_bar": bool(plan["status"] == "pending" and (not bars or bars[-1]["price_date"] != env.session)),
    }


@router.get("")
def list_plans(group: str = "holdings", limit: int = 20):
    if group not in GROUPS:
        return _bad(f"group 需为 {'、'.join(GROUPS)} 之一")
    if not 1 <= limit <= 100:
        return _bad("limit 必须是 1–100 之间的整数")

    def _read(env):
        return env.read(
            lambda conn: {
                "plans": [_card(conn, env, row) for row in plan_repo.plans_by_status(conn, GROUPS[group], limit=limit)]
            }
        )

    return deps.ok_or_error(lambda: _read(deps.web_env()))


@router.get("/{plan_id}")
def plan_detail(plan_id: int):
    def _read(env):
        def _work(conn):
            plan = plan_repo.plan(conn, plan_id)
            if plan is None:
                raise ValueError("unknown_plan")
            card = _card(conn, env, plan)
            card["events"] = [{field: row[field] for field in EVENT_FIELDS} for row in plan_repo.events(conn, plan_id)]
            return card

        return env.read(_work)

    return deps.ok_or_error(lambda: _read(deps.web_env()))


@router.post("/{plan_id}/stop-order")
def stop_order(plan_id: int, body: dict = BODY):
    return deps.ok_or_error(
        lambda: deps.forward(plan_tools.confirm_stop_order, deps.web_env(), {**body, "plan_id": plan_id})
    )


@router.post("/{plan_id}/raise-stop")
def raise_stop(plan_id: int, body: dict = BODY):
    return deps.ok_or_error(lambda: deps.forward(plan_tools.raise_stop, deps.web_env(), {**body, "plan_id": plan_id}))


@router.post("/{plan_id}/cancel")
def cancel(plan_id: int, body: dict = BODY):
    return deps.ok_or_error(lambda: deps.forward(plan_tools.cancel_plan, deps.web_env(), {**body, "plan_id": plan_id}))
