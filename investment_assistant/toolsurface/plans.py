"""Plan-line write tools (spec §6.4, §7.2, §7.3, §9.3): continue / revise / holding review /
summary, plus the chat-only stop-order, raise-stop and cancel tools. All writes are host-
validated; user-facing writes restate first and are idempotent by ``request_id`` stored in
``v3_plan_events.refs``."""

from __future__ import annotations

from decimal import Decimal

from investment_assistant.core.texts import price_text
from investment_assistant.data import repo as data_repo
from investment_assistant.derivation.indicators import price as price_indicators
from investment_assistant.execution.plan_levels import (
    MAX_HOLD_RANGE,
    size,
    validate_levels,
    validity,
)
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.toolsurface.env import ToolEnv
from investment_assistant.toolsurface.planning import NO_EQUITY_TEXT, _exposure, _sized
from investment_assistant.toolsurface.reads import session_bars
from investment_assistant.toolsurface.validation import (
    check_agent_levels,
    confirmed_flag,
    note_text,
    number_arg,
    text_arg,
)

HOLDING_VERDICTS = ("holds", "broken", "unknown")
SIZING_WARNINGS = (NO_EQUITY_TEXT, "no_room")


def _plan_or_error(conn, plan_id) -> dict:
    if not isinstance(plan_id, int) or isinstance(plan_id, bool):
        raise ValueError("invalid_argument: plan_id 必须是整数")
    plan = plan_repo.plan(conn, plan_id)
    if plan is None:
        raise ValueError("unknown_plan")
    return plan


def _known_refs(conn, env: ToolEnv, refs, *, required: bool = False) -> list[str]:
    checked = [ref for ref in (refs or [])]
    if any(not isinstance(ref, str) for ref in checked):
        raise ValueError("invalid_argument: refs 类型不符")
    known = facts_repo.tool_call_refs(conn, env.agent_session)
    if any(ref not in known for ref in checked) or (required and not checked):
        raise ValueError("invalid_refs")
    return checked


def _require_today_confirm(conn, env: ToolEnv, ticker: str) -> None:
    verdict = facts_repo.verdicts(conn, env.session).get(ticker)
    if verdict is None or verdict["verdict"] != "confirm":
        raise ValueError("no_confirm_verdict")


def _replayed_event(conn, plan_id: int, request_id: str, event: str) -> dict | None:
    """同一 request_id 已写过该事件：幂等重放，返回当时的事件行。"""
    for row in plan_repo.events(conn, plan_id):
        if row["event"] == event and request_id in (row["refs"] or []):
            return row
    return None


def _refresh_sizing_warnings(old: list, sizing: dict, invalid: list) -> list:
    kept = [text for text in old if text not in SIZING_WARNINGS]
    refreshed = [*kept, *invalid]
    if sizing.get("status") == "no_equity":
        refreshed.append(NO_EQUITY_TEXT)
    return refreshed


def continue_plan(env: ToolEnv, *, plan_id, refs: list | None = None) -> dict:
    """再次上榜且确认：数字不变，有效期从今天重算，股数按当天总资产重算（spec §6.4）。"""

    def _work(conn):
        plan = _plan_or_error(conn, plan_id)
        if plan["status"] != "pending":
            raise ValueError("not_pending")
        if plan["session"] == env.session:
            # 延续是「之后的交易日再次上榜」（spec §6.4）；当天刚保存的计划没有可延续的
            raise ValueError("saved_this_session: 计划今天刚保存，延续只在之后的交易日")
        checked = _known_refs(conn, env, refs, required=True)
        _require_today_confirm(conn, env, plan["ticker"])
        valid_from, valid_until = validity(env.session, env.cfg.v3.valid_sessions)
        from investment_assistant.facts.account import account_view

        snapshot = account_view(conn, session=env.session, cutoff_at=env.cutoff_at)["sizing_snapshot"]
        ticker_value, total_value = _exposure(conn, env, plan["ticker"])
        ref_price = (plan["entry_low"] + plan["entry_high"]) / 2 if plan["entry_low"] is not None else plan["stop"]
        sizing, invalid = size(
            snapshot=snapshot,
            ref_price=ref_price,
            stop=plan["stop"],
            entry_high=plan["entry_high"],
            ticker_value=ticker_value,
            total_value=total_value,
            params=env.cfg.workbench,
        )
        sizing = _sized(sizing)
        warnings = _refresh_sizing_warnings(plan["warnings"] or [], sizing, invalid)
        before = {"valid_from": plan["valid_from"], "valid_until": plan["valid_until"], "shares": plan["shares"]}
        plan = plan_repo.update_plan(
            conn,
            plan_id,
            valid_from=valid_from,
            valid_until=valid_until,
            shares=sizing["shares"],
            sizing=sizing,
            warnings=warnings,
        )
        after = {"valid_from": valid_from, "valid_until": valid_until, "shares": sizing["shares"]}
        shares_text = NO_EQUITY_TEXT if sizing["shares"] is None else f"股数重算为 {sizing['shares']}"
        reason = f"再次上榜，有效期延至 {valid_until:%Y-%m-%d}，{shares_text}"
        plan_repo.insert_event(
            conn,
            {
                "plan_id": plan_id,
                "session": env.session,
                "event": "continued",
                "reason": reason,
                "before": before,
                "after": after,
                "source": "agent",
                "refs": checked,
            },
        )
        return plan

    return env.write(_work)


def revise_plan(
    env: ToolEnv,
    *,
    plan_id,
    reason: str,
    levels: dict | None = None,
    thesis: str | None = None,
    invalidation: str | None = None,
    max_hold_sessions=None,
    refs: list | None = None,
) -> dict:
    """修订待入场计划：必须写明改了什么和原因；价位过宿主校验；只记改了的键（spec §6.4）。"""
    if not isinstance(reason, str) or not 1 <= len(reason) <= 2000:
        raise ValueError("invalid_argument: reason 需为 1–2000 字")

    def _work(conn):
        plan = _plan_or_error(conn, plan_id)
        if plan["status"] != "pending":
            raise ValueError("not_pending")
        checked = _known_refs(conn, env, refs, required=True)
        _require_today_confirm(conn, env, plan["ticker"])
        fields: dict = {}
        if levels is not None:
            if not isinstance(levels, dict):
                raise ValueError("invalid_argument: levels 必须是对象")
            bars = session_bars(conn, env, plan["ticker"])
            if not bars:
                raise ValueError("no_bars")
            atr = price_indicators.atr14(bars)
            if atr is None or atr <= 0:
                raise ValueError("atr_unavailable")
            try:
                chosen = validate_levels(levels)
            except ValueError as exc:
                raise ValueError(f"levels_invalid: {exc}") from None
            problems = check_agent_levels(chosen, last_close=Decimal(str(bars[-1]["close"])), atr=atr)
            if problems:
                raise ValueError(f"levels_invalid: {', '.join(problems)}")
            for key in ("entry_low", "entry_high", "stop", "target1"):
                if chosen[key] != plan[key]:
                    fields[key] = chosen[key]
        for key, value in (("thesis", thesis), ("invalidation", invalidation)):
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError(f"invalid_argument: {key} 必须是字符串")
                if value != plan[key]:
                    fields[key] = value
        if max_hold_sessions is not None:
            if (
                not isinstance(max_hold_sessions, int)
                or isinstance(max_hold_sessions, bool)
                or not MAX_HOLD_RANGE[0] <= max_hold_sessions <= MAX_HOLD_RANGE[1]
            ):
                raise ValueError(f"invalid_max_hold: 需为 {MAX_HOLD_RANGE[0]}–{MAX_HOLD_RANGE[1]} 的整数")
            if max_hold_sessions != plan["max_hold_sessions"]:
                fields["max_hold_sessions"] = max_hold_sessions
        if not fields:
            raise ValueError("nothing_to_revise: 需至少修改一个字段")
        before = {key: plan[key] for key in fields}
        plan = plan_repo.update_plan(conn, plan_id, **fields)
        plan_repo.insert_event(
            conn,
            {
                "plan_id": plan_id,
                "session": env.session,
                "event": "revised",
                "reason": reason,
                "before": before,
                "after": fields,
                "source": "agent",
                "refs": checked,
            },
        )
        return plan

    return env.write(_work)


def save_holding_review(
    env: ToolEnv, *, plan_id, verdict: str, reasons: str, counter: str = "", refs: list | None = None
) -> dict:
    """持仓复查（spec §9.3 第 1 项）：只对 open；broken 由宿主转成下次开盘卖出（spec §7.4）。"""
    if verdict not in HOLDING_VERDICTS:
        raise ValueError("invalid_verdict")
    if not isinstance(reasons, str) or not 1 <= len(reasons) <= 2000:
        raise ValueError("invalid_argument: reasons 需为 1–2000 字")
    if not isinstance(counter, str):
        raise ValueError("invalid_argument: counter 必须是字符串")

    def _work(conn):
        plan = _plan_or_error(conn, plan_id)
        if plan["status"] != "open":
            raise ValueError("not_open")
        checked = _known_refs(conn, env, refs, required=(verdict == "broken"))
        review = plan_repo.upsert_holding_review(
            conn,
            {
                "plan_id": plan_id,
                "session": env.session,
                "verdict": verdict,
                "reasons": reasons,
                "counter": counter,
                "refs": checked,
                "agent_session": env.agent_session,
            },
        )
        if verdict == "broken" and plan["sell_instruction"] is None:
            plan = plan_repo.update_plan(conn, plan_id, sell_instruction="thesis_broken")
            plan_repo.insert_event(
                conn,
                {
                    "plan_id": plan_id,
                    "session": env.session,
                    "event": "sell_instruction",
                    "reason": "论点失效",
                    "before": {},
                    "after": {"sell_instruction": "thesis_broken"},
                    "source": "agent",
                    "refs": checked,
                },
            )
        return {"review": review, "plan": plan}

    return env.write(_work)


def save_summary(env: ToolEnv, *, text: str) -> dict:
    """一句话总结（spec §9.3 第 4 项）：写当天的简报行，1–300 字。"""
    if not isinstance(text, str) or not 1 <= len(text) <= 300:
        raise ValueError("invalid_argument: text 需为 1–300 字")

    def _work(conn):
        if plan_repo.brief(conn, env.session) is None:
            plan_repo.upsert_brief(conn, {"session": env.session, "status": "draft"})
        return plan_repo.update_brief(conn, env.session, summary=text)

    return env.write(_work)


def confirm_stop_order(env: ToolEnv, *, plan_id, price, request_id, confirmed) -> dict:
    """确认已挂止损单（spec §7.2）：挂单价必须等于计划当前止损。"""
    request_id = text_arg(request_id, "request_id")
    if not confirmed_flag(confirmed):

        def _peek(conn):
            plan = _plan_or_error(conn, plan_id)
            return plan

        plan = env.read(_peek)
        return {"write": False, "preview": f"将确认 {plan['ticker']} 的止损单挂单价 {plan['stop']}。确认后写入。"}

    def _work(conn):
        plan = _plan_or_error(conn, plan_id)
        if plan["status"] != "open":
            raise ValueError("not_open")
        replay = _replayed_event(conn, plan_id, request_id, "stop_order_confirmed")
        if replay is not None:
            return {"plan": plan_repo.plan(conn, plan_id), "event": replay, "replay": True}
        amount = number_arg(price, "price")
        if plan["stop"] is None or amount != plan["stop"]:
            raise ValueError(f"stop_mismatch: 挂单价需等于当前止损 {plan['stop']}")
        plan = plan_repo.update_plan(conn, plan_id, confirmed_stop_order=amount)
        event = plan_repo.insert_event(
            conn,
            {
                "plan_id": plan_id,
                "session": env.session,
                "event": "stop_order_confirmed",
                "reason": f"已确认挂单，价位 {amount}",
                "before": {},
                "after": {"confirmed_stop_order": amount},
                "source": "user",
                "refs": [request_id],
            },
        )
        return {"plan": plan, "event": event}

    return env.write(_work)


def raise_stop(env: ToolEnv, *, plan_id, stop, request_id, confirmed) -> dict:
    """调高止损（spec §7.3 第 2 条）：只对 open；新值必须高于当前止损且低于最新收盘。"""
    request_id = text_arg(request_id, "request_id")
    if not confirmed_flag(confirmed):

        def _peek(conn):
            plan = _plan_or_error(conn, plan_id)
            return plan

        plan = env.read(_peek)
        return {"write": False, "preview": f"将把 {plan['ticker']} 的止损从 {plan['stop']} 上调。确认后写入。"}
    amount = number_arg(stop, "stop")

    def _work(conn):
        plan = _plan_or_error(conn, plan_id)
        if plan["status"] != "open":
            raise ValueError("not_open")
        replay = _replayed_event(conn, plan_id, request_id, "stop_raised")
        if replay is not None:
            return {"plan": plan_repo.plan(conn, plan_id), "event": replay, "replay": True}
        bars = data_repo.read_bars(conn, plan["ticker"], env.session, cutoff_at=env.cutoff_at)
        if not bars:
            raise ValueError("no_bars")
        last_close = Decimal(str(bars[-1]["close"]))
        if plan["stop"] is not None and amount <= plan["stop"]:
            raise ValueError("stop_not_higher: 止损只上调，不下调")
        if amount >= last_close:
            raise ValueError(f"stop_above_close: 止损需低于最新收盘 {last_close}")
        before = {"stop": plan["stop"]}
        plan = plan_repo.update_plan(conn, plan_id, stop=amount)
        event = plan_repo.insert_event(
            conn,
            {
                "plan_id": plan_id,
                "session": env.session,
                "event": "stop_raised",
                "reason": f"用户上调止损 {price_text(before['stop'])} → {price_text(amount)}",
                "before": before,
                "after": {"stop": amount},
                "source": "user",
                "refs": [request_id],
            },
        )
        return {"plan": plan, "event": event}

    return env.write(_work)


def cancel_plan(env: ToolEnv, *, plan_id, request_id, confirmed, reason: str = "") -> dict:
    """取消待入场计划（spec §10）：用户在对话或页面发起。"""
    request_id = text_arg(request_id, "request_id")
    reason_text = note_text(reason)
    if not confirmed_flag(confirmed):
        suffix = f"（原因：{reason_text}）" if reason_text else ""
        return {"write": False, "preview": f"将取消计划 {plan_id}{suffix}。确认后写入。"}

    def _work(conn):
        plan = _plan_or_error(conn, plan_id)
        replay = _replayed_event(conn, plan_id, request_id, "cancelled")
        if replay is not None:
            return {"plan": plan_repo.plan(conn, plan_id), "event": replay, "replay": True}
        if plan["status"] != "pending":
            raise ValueError("not_pending")
        text = reason_text or "用户取消"
        plan = plan_repo.update_plan(conn, plan_id, status="cancelled", ended_session=env.session, end_reason=text)
        event = plan_repo.insert_event(
            conn,
            {
                "plan_id": plan_id,
                "session": env.session,
                "event": "cancelled",
                "reason": text,
                "before": {"status": "pending"},
                "after": {"status": "cancelled"},
                "source": "user",
                "refs": [request_id],
            },
        )
        return {"plan": plan, "event": event}

    return env.write(_work)
