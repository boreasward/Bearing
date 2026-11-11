"""/api/plans*：三组卡片、单份计划与变化记录、三个计划写入口（spec §12 计划与持仓块）。
写入口只把表单转发给工具面的同名宿主函数，同一套校验（spec §10 末段）。"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from investment_assistant.api import deps
from investment_assistant.data import repo as data_repo
from investment_assistant.execution import plan_line
from investment_assistant.core.texts import money_value, price_value, quantity_value
from investment_assistant.execution.plan_levels import cent
from investment_assistant.execution.positions import PositionError, build
from investment_assistant.facts import plan_repo, rotation_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts.holdings import stop_order_status
from investment_assistant.toolsurface import plans as plan_tools
from investment_assistant.toolsurface import rotation as rotation_tools

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


def _rotation_block(conn, plan: dict) -> tuple[dict | None, str | None]:
    """A 上还在的换仓指令（rotation 三个键，没有指令为 None）与 B 的名字（next_action 用）。A 卖光平仓后三列
    仍保留（算 exit_reason 用），但指令已执行完，只有 open 的计划才算「指令还在」（与 withdraw_rotation 的守卫一致）。"""
    if plan["status"] != "open" or plan["sell_instruction"] != "rotation" or plan["rotation_id"] is None:
        return None, None
    record = rotation_repo.rotation(conn, plan["rotation_id"])
    counterpart = record["candidate"] if record is not None else None
    return {
        "rotation_id": plan["rotation_id"],
        "sell_quantity": quantity_value(plan["sell_quantity"]),
        "counterpart": counterpart,
    }, counterpart


def _funded_by_block(conn, plan: dict) -> dict | None:
    """这份计划是 B：来自哪次换仓、A 是哪只、计划卖多少（funded_by 三个键）。"""
    if plan["funded_by_rotation_id"] is None:
        return None
    record = rotation_repo.rotation(conn, plan["funded_by_rotation_id"])
    if record is None:
        return None
    return {
        "rotation_id": record["id"],
        "holding_ticker": record["holding_ticker"],
        "sell_quantity": quantity_value(record["sell_quantity"]),
    }


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
    rotation, counterpart = _rotation_block(conn, plan)
    if plan["status"] == "open" and position is not None:
        position_row = {  # 页面原样显示：股数去尾零、价格去尾零、金额取到分（core.texts）
            "quantity": quantity_value(position["quantity"]),
            "avg_cost": price_value(position["avg_cost"]),
            "last_close": price_value(last_close),
            "unrealized_pnl": None
            if last_close is None
            else money_value(position["quantity"] * (last_close - position["avg_cost"])),
            "unrealized_pct": None if last_close is None else last_close / position["avg_cost"] - 1,
        }
        shown = dict(plan)  # 给用户看的止损价取到分（与今日操作同一口径）
        if shown["stop"] is not None:
            shown["stop"] = cent(shown["stop"])
        if shown.get("confirmed_stop_order") is not None:
            shown["confirmed_stop_order"] = cent(shown["confirmed_stop_order"])
        unchecked = not bars or bars[-1]["price_date"] != env.session
        next_action = plan_line.next_action(plan=shown, position=position, unchecked=unchecked, counterpart=counterpart)
        if next_action["kind"] == "rotation_sell":  # 股数去存储尾零（与今日操作同一口径）
            next_action["sell_quantity"] = quantity_value(next_action["sell_quantity"])
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
        # v3.3：换仓指令（这份计划是 A 且指令还在）、资金来源（这份计划是 B）、止损单数量待改、保存时的警告
        "rotation": rotation,
        "funded_by": _funded_by_block(conn, plan),
        "stop_quantity_stale": bool(plan["stop_quantity_stale"]),
        "warnings": list(plan["warnings"] or []),
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


@router.post("/{plan_id}/rotation/withdraw")
def withdraw_rotation(plan_id: int, body: dict = BODY):
    return deps.ok_or_error(
        lambda: deps.forward(rotation_tools.withdraw_rotation, deps.web_env(), {**body, "plan_id": plan_id})
    )
