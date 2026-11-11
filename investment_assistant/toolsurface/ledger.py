"""Ledger tools (spec §8, §10): the account read, cash-flow recording, equity reconciliation
and voiding of trades or cash flows. Chat-only writes restate first and are idempotent by
``request_id``; voiding appends a ``v3_voids`` row and never touches the original row."""

from __future__ import annotations

from datetime import timedelta

from investment_assistant.data import repo as data_repo
from investment_assistant.execution.account import reconcile
from investment_assistant.execution.plan_line import close_outcome
from investment_assistant.execution.positions import PositionError, build
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts.account import account_view
from investment_assistant.facts.rotation import exposure_row
from investment_assistant.toolsurface.env import ToolEnv
from investment_assistant.toolsurface.validation import (
    confirmed_flag,
    date_arg,
    datetime_arg,
    note_text,
    number_arg,
    signed_number_arg,
    text_arg,
)

CASH_KINDS = ("opening", "deposit", "withdrawal", "adjustment")
KIND_TEXT = {"opening": "期初现金", "deposit": "入金", "withdrawal": "出金", "adjustment": "调整"}
VOID_KINDS = ("trade", "cash_flow")
VOID_TEXT = {"trade": "成交", "cash_flow": "出入金"}
EXIT_TEXT = {
    "initial_stop": "初始止损",
    "trailing_stop": "移动止损",
    "max_hold": "持有到期",
    "thesis_broken": "论点失效",
    "rotation": "换仓",
    "manual": "用户自行卖出",
}


def get_account(env: ToolEnv) -> dict:
    """账户视图：账本、最近对账与差额、最近平仓结果、仓位本金快照（spec §8），v3.3 加仓位占比 ``exposure``。"""
    return env.read(
        lambda conn: {
            **account_view(conn, session=env.session, cutoff_at=env.cutoff_at),
            "exposure": exposure_row(conn, session=env.session, cutoff_at=env.cutoff_at, cfg=env.cfg),
        }
    )


def record_equity(env: ToolEnv, *, equity, request_id, confirmed, as_of=None, note: str = "") -> dict:
    """对账记录（spec §8.5）：写 v3_equity，返回它与算出总资产的差额。"""
    amount = number_arg(equity, "equity")
    request_id = text_arg(request_id, "request_id")
    is_confirmed = confirmed_flag(confirmed)
    note_text_value = note_text(note)
    as_of_date = env.session if as_of is None else date_arg(as_of, "as_of")
    preview = f"将记录账户对账：券商总资产 {amount}（截至 {as_of_date.isoformat()}）。确认后写入。"
    if not is_confirmed:
        return {"write": False, "preview": preview}

    def _work(conn):
        row = facts_repo.insert_equity(
            conn, {"request_id": request_id, "as_of": as_of_date, "equity": amount, "note": note_text_value}
        )
        computed = account_view(conn, session=env.session, cutoff_at=env.cutoff_at)["ledger"]["equity"]
        return {**row, "reconciliation": reconcile(reported=amount, computed=computed)}

    return env.write(_work)


def record_cash_flow(env: ToolEnv, *, kind, amount, request_id, confirmed, effective_at=None, note: str = "") -> dict:
    """记一笔出入金（spec §8.2）：期初 / 入金 / 出金 / 调整；同一时间只有一笔有效期初。"""
    if kind not in CASH_KINDS:
        raise ValueError("invalid_kind: kind 需为 opening / deposit / withdrawal / adjustment")
    if kind == "adjustment":
        value = signed_number_arg(amount, "amount")
    else:
        value = number_arg(amount, "amount")
    request_id = text_arg(request_id, "request_id")
    is_confirmed = confirmed_flag(confirmed)
    note_value = note_text(note)
    effective = env.cutoff_at if effective_at is None else datetime_arg(effective_at, "effective_at")
    preview = f"将记录{KIND_TEXT[kind]} {value}（生效 {effective.isoformat()}）。确认后写入。"
    if not is_confirmed:
        return {"write": False, "preview": preview}

    def _work(conn):
        if kind == "opening":
            live = [
                flow for flow in plan_repo.cash_flows(conn) if flow["kind"] == "opening" and flow["voided_at"] is None
            ]
            if live:
                raise ValueError("opening_exists: 已有有效期初现金，要重记先作废旧的")
        return plan_repo.insert_cash_flow(
            conn,
            {"request_id": request_id, "kind": kind, "amount": value, "effective_at": effective, "note": note_value},
        )

    return env.write(_work)


def _sessions_since_stop_hit(plan: dict, session) -> int | None:
    """止损触及到卖完之间隔的交易日数（spec §8.4）。"""
    from investment_assistant.data.calendar import trading_days_between

    if plan["stop_hit_session"] is None:
        return None
    start = plan["stop_hit_session"] + timedelta(days=1)
    return len(trading_days_between(start, session))


def void_record(env: ToolEnv, *, kind, target_id, request_id, confirmed, reason: str = "") -> dict:
    """作废一笔成交或出入金（spec §8.6）：先验证剩余记录能算出合法持仓，再追加 v3_voids。"""
    if kind not in VOID_KINDS:
        raise ValueError("invalid_kind: kind 需为 trade 或 cash_flow")
    if not isinstance(target_id, int) or isinstance(target_id, bool):
        raise ValueError("invalid_argument: target_id 必须是整数")
    request_id = text_arg(request_id, "request_id")
    reason_value = note_text(reason)
    is_confirmed = confirmed_flag(confirmed)
    preview = (
        f"将作废{VOID_TEXT[kind]}记录 {target_id}"
        + (f"（原因：{reason_value}）" if reason_value else "")
        + "。确认后写入。"
    )
    if not is_confirmed:
        return {"write": False, "preview": preview}

    def _work(conn):
        replayed = next((void for void in plan_repo.voids(conn) if void["request_id"] == request_id), None)
        if replayed is not None:  # 同一 request_id 重发：返回原行，不再重复处理
            plan = None
            if kind == "trade":
                target = facts_repo.trade(conn, replayed["target_id"])
                plan = plan_repo.active_plan(conn, target["ticker"]) if target is not None else None
            return {"void": replayed, "plan": plan, "replay": True}
        if any(void["kind"] == kind and void["target_id"] == target_id for void in plan_repo.voids(conn)):
            raise ValueError("already_voided: 这条记录已被另一个请求作废")
        if kind == "trade":
            return _void_trade(conn, env, target_id, request_id, reason_value)
        if plan_repo.cash_flow(conn, target_id) is None:
            raise ValueError("unknown_target")
        void = plan_repo.insert_void(
            conn, {"request_id": request_id, "kind": kind, "target_id": target_id, "reason": reason_value}
        )
        return {"void": void, "plan": None}

    return env.write(_work)


def _void_trade(conn, env: ToolEnv, target_id: int, request_id: str, reason_value: str) -> dict:
    target = facts_repo.trade(conn, target_id)
    if target is None:
        raise ValueError("unknown_target")
    ticker = target["ticker"]
    trades = facts_repo.list_trades(conn, ticker)
    remaining = [trade for trade in trades if trade["id"] != target_id]
    splits = data_repo.split_events(conn, ticker, cutoff_at=env.cutoff_at)
    try:
        built = build(remaining, splits=splits)
    except PositionError as exc:
        raise ValueError(f"position_invalid: 作废后剩余成交无法核算持仓：{exc}") from None
    void = plan_repo.insert_void(
        conn, {"request_id": request_id, "kind": "trade", "target_id": target_id, "reason": reason_value}
    )
    plan = plan_repo.active_plan(conn, ticker)
    if plan is not None and plan["status"] == "open" and built["position"] is None:
        has_buys = any(trade["side"] == "buy" for trade in remaining if trade["voided_at"] is None)
        if not has_buys:
            plan = plan_repo.update_plan(
                conn, plan["id"], status="cancelled", ended_session=env.session, end_reason="成交作废"
            )
            plan_repo.insert_event(
                conn,
                {
                    "plan_id": plan["id"],
                    "session": env.session,
                    "event": "cancelled",
                    "reason": "成交作废",
                    "before": {"status": "open"},
                    "after": {"status": "cancelled"},
                    "source": "user",
                    "refs": [request_id],
                },
            )
        else:
            outcome = close_outcome(
                plan=plan,
                closed_lots=built["closed"],
                trades=remaining,
                sessions_since_stop_hit=_sessions_since_stop_hit(plan, env.session),
            )
            plan = plan_repo.update_plan(
                conn, plan["id"], status="closed", ended_session=env.session, end_reason="成交作废", outcome=outcome
            )
            plan_repo.insert_event(
                conn,
                {
                    "plan_id": plan["id"],
                    "session": env.session,
                    "event": "closed",
                    "reason": "成交作废（买入部分作废后持仓为零）",
                    "before": {"status": "open"},
                    "after": {"status": "closed"},
                    "source": "user",
                    "refs": [request_id],
                },
            )
    return {"void": void, "plan": plan}
