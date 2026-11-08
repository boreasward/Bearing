"""/api/research*：两组卡片、单份研究与变化记录、放弃研究（spec §10 研究块 / v3.2 接口契约表）。
放弃只把表单转发给工具面的 drop_research，同一套校验（spec §10 末段）。"""

from __future__ import annotations

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from investment_assistant.api import deps
from investment_assistant.core.texts import price_value
from investment_assistant.data import repo as data_repo
from investment_assistant.data.calendar import trading_days_between
from investment_assistant.execution.research import condition_met, validate_condition
from investment_assistant.facts import research_repo
from investment_assistant.toolsurface import research as research_tools

router = APIRouter(prefix="/api/research")

BODY = Body(...)  # 模块级单例：JSON 请求体（ruff B008）

GROUPS: dict[str, tuple[str, ...]] = {
    "watching": ("watching",),
    "ended": ("planned", "rejected", "expired", "dropped"),  # 四个终态（spec §6.1）
}
EVENT_FIELDS = ("session", "event", "reason", "before", "after", "source", "created_at")


def _bad(message: str) -> JSONResponse:
    return JSONResponse({"error": f"invalid_argument: {message}"}, status_code=400)


def _ok_or_contract_error(result):
    """同 deps.ok_or_error，但错误按接口契约表返回单串 {"error": "code[: 说明]"}——forward 的
    missing_argument / unknown_argument 带着「: 参数名」，拆成 detail 键会违反契约。只用于本模块的
    写入口（deps.ok_or_error 的既有调用方不受影响）。"""
    try:
        data = result()
    except ValueError as exc:
        code = str(exc).partition(":")[0].strip()
        return JSONResponse({"error": str(exc)}, status_code=404 if code in deps.NOT_FOUND_CODES else 400)
    return deps.ok(data)


def _condition(condition: dict | None) -> dict | None:
    """存储的条件 JSON 转成契约形状 {"kind","level","ma"}：level 过 price_value 取到分
    （价位是字符串），close_above_ma 的 ma 原样是整数。"""
    if not condition:
        return None
    return {
        "kind": condition["kind"],
        "level": None if condition.get("level") is None else price_value(condition["level"]),
        "ma": condition.get("ma"),
    }


def _card(conn, env, row: dict) -> dict:
    """一张研究卡片（接口契约表，字段以它为准、不用简报研究段的 state 枚举）：last_close
    取到分；condition_met_today 用 execution.research.condition_met 对读到交易日的日线判定
    （无条件、缺日线、均线历史不足时 None）；day 两端都算（发起当天是第 1 天，已结束的冻结
    在结束日）；missing_bar 只对仍待每日复查的观察中研究（有条件且未触发）为真——缺当日日线
    （spec §6.4「缺日线，未复查」），与计划卡片 missing_bar 同一判定。"""
    ticker = row["ticker"]
    bars = data_repo.read_bars(conn, ticker, env.session, cutoff_at=env.cutoff_at)
    condition = row["condition"]
    return {
        "research_id": row["id"],
        "ticker": ticker,
        "status": row["status"],
        "thesis": row["thesis"],
        "created_session": row["created_session"],
        "day": len(trading_days_between(row["created_session"], row["ended_session"] or env.session)),
        "valid_until": row["valid_until"],
        "condition": _condition(condition),
        "condition_text": row["condition_text"],
        "last_close": price_value(bars[-1]["close"]) if bars else None,
        "condition_met_today": condition_met(validate_condition(condition), bars) if condition else None,
        "triggered_session": row["triggered_session"],
        "verdict": row["verdict"],
        "reasons": row["reasons"],
        "plan_id": row["plan_id"],
        "ended_session": row["ended_session"],
        "end_reason": row["end_reason"],
        "missing_bar": bool(
            row["status"] == "watching"
            and condition
            and row["triggered_session"] is None
            and (not bars or bars[-1]["price_date"] != env.session)
        ),
        "created_at": row["created_at"],
    }


@router.get("")
def list_research(group: str = "watching", limit: int = 20):
    if group not in GROUPS:
        return _bad(f"group 需为 {'、'.join(GROUPS)} 之一")
    if not 1 <= limit <= 100:
        return _bad("limit 必须是 1–100 之间的整数")

    def _read(env):
        return env.read(
            lambda conn: {
                "research": [
                    _card(conn, env, row) for row in research_repo.research_by_status(conn, GROUPS[group], limit=limit)
                ]
            }
        )

    return deps.ok_or_error(lambda: _read(deps.web_env()))


@router.get("/{research_id}")
def research_detail(research_id: int):
    def _read(env):
        def _work(conn):
            row = research_repo.research(conn, research_id)
            if row is None:
                raise ValueError("unknown_research")
            card = _card(conn, env, row)
            card["events"] = [
                {field: event[field] for field in EVENT_FIELDS}
                for event in research_repo.research_events(conn, research_id)
            ]
            return card

        return env.read(_work)

    return deps.ok_or_error(lambda: _read(deps.web_env()))


@router.post("/{research_id}/drop")
def drop(research_id: int, body: dict = BODY):
    return _ok_or_contract_error(
        lambda: deps.forward(research_tools.drop_research, deps.web_env(), {**body, "research_id": research_id})
    )
