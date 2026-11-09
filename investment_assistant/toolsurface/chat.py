"""Chat-only tools: confirmed trade recording with plan-line progression (spec §6.5, §7.2)
and manual family backfill (spec §8). Equity/cash-flow/void tools live in ledger.py."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from investment_assistant.data import repo as data_repo
from investment_assistant.data.identity import stock_id
from investment_assistant.derivation.indicators import price as price_indicators
from investment_assistant.execution.plan_levels import rule_levels, validity
from investment_assistant.execution.plan_line import close_outcome, deviations
from investment_assistant.execution.positions import PositionError, build
from investment_assistant.facts import collect as facts_collect
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts.coverage import coverage, coverage_lines
from investment_assistant.toolsurface.env import ToolEnv
from investment_assistant.toolsurface.validation import (
    confirmed_flag,
    datetime_arg,
    normalize_ticker,
    note_text,
    number_arg,
    text_arg,
)

ENSURE_FAMILIES = ("bars", "news", "sec_filings", "earnings", "fundamentals")
SIDE_TEXT = {"buy": "买入", "sell": "卖出"}
NY = ZoneInfo("America/New_York")
ADOPTED_THESIS = "补录持仓，没有入场论点"
EXIT_TEXT = {
    "initial_stop": "初始止损",
    "trailing_stop": "移动止损",
    "max_hold": "持有到期",
    "thesis_broken": "论点失效",
    "manual": "用户自行卖出",
}


def record_trade(
    env: ToolEnv,
    *,
    ticker: str,
    side: str,
    quantity,
    price,
    executed_at: str,
    request_id: str,
    confirmed,
    fee=0,
    plan_id=None,
    note: str = "",
) -> dict:
    """记一笔成交（spec §6.5）：买入关联/补录/加仓计划，卖出推进到平仓；买入返回止损单指令。"""
    ticker = normalize_ticker(ticker)
    side_text = side if isinstance(side, str) and side in SIDE_TEXT else ""
    if not side_text:
        raise ValueError("invalid_argument: side 必须是 buy 或 sell")
    qty = number_arg(quantity, "quantity")
    deal_price = number_arg(price, "price")
    deal_fee = number_arg(fee, "fee", allow_zero=True)
    request_id = text_arg(request_id, "request_id")
    is_confirmed = confirmed_flag(confirmed)
    note_value = note_text(note)
    executed = datetime_arg(executed_at, "executed_at")
    linked_plan = plan_id if isinstance(plan_id, int) and not isinstance(plan_id, bool) else None
    if plan_id is not None and linked_plan is None:
        raise ValueError("invalid_argument: plan_id 必须是整数")
    preview = (
        f"将记录成交：{ticker} {SIDE_TEXT[side_text]} {qty} 股，价格 {deal_price}，"
        f"时间 {executed.isoformat()}。确认后写入。"
    )
    if not is_confirmed:
        return {"write": False, "preview": preview}

    def _work(conn):
        replayed = facts_repo.trade_by_request(conn, request_id)
        if replayed is not None:  # 同一 request_id 重发：返回原行，不重复推进计划
            return _respond(conn, env, replayed, transition=False)
        if linked_plan is not None:
            found = conn.execute("SELECT ticker FROM v3_plans WHERE id = %s", (linked_plan,)).fetchone()
            if found is None:
                raise ValueError(f"invalid_argument: plan_id {linked_plan} 不存在")
            if found[0] != ticker:  # 跨标的计划编号是接错线，不是加仓：拒绝且不写成交
                raise ValueError(
                    f"plan_ticker_mismatch: plan_id {linked_plan} 属于 {found[0]}，与成交标的 {ticker} 不符"
                )
        row = {
            "id": None,
            "request_id": request_id,
            "ticker": ticker,
            "side": side_text,
            "quantity": qty,
            "price": deal_price,
            "fee": deal_fee,
            "executed_at": executed,
            "plan_id": linked_plan,
            "note": note_value,
        }
        # 先把这笔并入现有成交试算 FIFO；卖出超过持仓则拒绝且不写入
        trades = [*facts_repo.list_trades(conn, ticker), row]
        try:
            build(trades, splits=data_repo.split_events(conn, ticker, cutoff_at=env.cutoff_at))
        except PositionError as exc:
            raise ValueError(f"position_invalid: {exc}") from None
        trade = facts_repo.insert_trade(conn, row)
        return _respond(conn, env, trade, transition=True)

    return env.write(_work)


def _respond(conn, env: ToolEnv, trade: dict, *, transition: bool) -> dict:
    """按 spec §6.5 推进计划线并拼出返回：trade / plan / stop_order / outcome。"""
    ticker = trade["ticker"]
    plan = plan_repo.active_plan(conn, ticker)
    outcome = None
    trades = facts_repo.list_trades(conn, ticker)
    splits = data_repo.split_events(conn, ticker, cutoff_at=env.cutoff_at)
    built = build(trades, splits=splits)
    if transition and trade["side"] == "buy":
        if plan is None:
            plan = _adopt(conn, env, ticker, opening_request_id=trade["request_id"])
        elif plan["status"] == "pending":
            sentences = deviations(plan=plan, price=trade["price"], quantity=trade["quantity"])
            started = trade["executed_at"].astimezone(NY).date()
            plan = plan_repo.update_plan(conn, plan["id"], status="open", start_session=started, reviewed_through=None)
            plan_repo.insert_event(
                conn,
                {
                    "plan_id": plan["id"],
                    "session": env.session,
                    "event": "bought",
                    "reason": f"买入 {trade['quantity']} 股 @ {trade['price']}",
                    "before": {"status": "pending"},
                    "after": {"status": "open"},
                    "source": "user",
                    "refs": [trade["request_id"]],
                },
            )
            for sentence in sentences:
                plan_repo.insert_event(
                    conn,
                    {
                        "plan_id": plan["id"],
                        "session": env.session,
                        "event": "deviation",
                        "reason": sentence,
                        "before": {},
                        "after": {},
                        "source": "user",
                        "refs": [trade["request_id"]],
                    },
                )
        elif plan["status"] == "open":
            plan_repo.insert_event(
                conn,
                {
                    "plan_id": plan["id"],
                    "session": env.session,
                    "event": "added",
                    "reason": f"加仓 {trade['quantity']} 股 @ {trade['price']}",
                    "before": {},
                    "after": {},
                    "source": "user",
                    "refs": [trade["request_id"]],
                },
            )
    elif transition and trade["side"] == "sell" and plan is not None:
        plan_repo.insert_event(
            conn,
            {
                "plan_id": plan["id"],
                "session": env.session,
                "event": "sold",
                "reason": f"卖出 {trade['quantity']} 股 @ {trade['price']}",
                "before": {},
                "after": {},
                "source": "user",
                "refs": [trade["request_id"]],
            },
        )
        if plan["status"] == "open" and built["position"] is None:
            outcome = close_outcome(
                plan=plan,
                closed_lots=built["closed"],
                trades=trades,
                sessions_since_stop_hit=_sessions_since_stop_hit(plan, env.session),
            )
            reason = EXIT_TEXT.get(outcome["exit_reason"], outcome["exit_reason"])
            plan = plan_repo.update_plan(
                conn, plan["id"], status="closed", ended_session=env.session, end_reason=reason, outcome=outcome
            )
            plan_repo.insert_event(
                conn,
                {
                    "plan_id": plan["id"],
                    "session": env.session,
                    "event": "closed",
                    "reason": f"已平仓：{reason}",
                    "before": {"status": "open"},
                    "after": {"status": "closed"},
                    "source": "user",
                    "refs": [trade["request_id"]],
                },
            )
    stop_order = None
    if trade["side"] == "buy" and plan is not None and plan["stop"] is not None and built["position"] is not None:
        stop_order = {"price": plan["stop"], "quantity": built["position"]["quantity"]}
    return {"trade": trade, "plan": plan, "stop_order": stop_order, "outcome": outcome}


def _sessions_since_stop_hit(plan: dict, session) -> int | None:
    from datetime import timedelta

    from investment_assistant.data.calendar import trading_days_between

    if plan["stop_hit_session"] is None:
        return None
    return len(trading_days_between(plan["stop_hit_session"] + timedelta(days=1), session))


def _adopt(conn, env: ToolEnv, ticker: str, opening_request_id: str | None = None) -> dict:
    """没有有效计划的买入：宿主补录一份已买入计划（spec §6.6），价位按最新收盘的规则价位。
    ``opening_request_id`` 是触发补录的那笔买入：成交表只增不改（更正走作废），把它写进
    adopted 事件的 refs，复盘按事件归属时这笔开仓成交才对得上号（F1）。"""
    bars = data_repo.read_bars(conn, ticker, env.session, cutoff_at=env.cutoff_at)
    atr = price_indicators.atr14(bars) if bars else None
    if bars and atr is not None and atr > 0:
        levels = rule_levels(Decimal(str(bars[-1]["close"])), atr)
        atr_value = atr
    else:
        levels = {key: None for key in ("entry_low", "entry_high", "stop", "target1")}
        atr_value = None
    valid_from, valid_until = validity(env.session, env.cfg.v3.valid_sessions)
    plan = plan_repo.insert_plan(
        conn,
        {
            "session": env.session,
            "ticker": ticker,
            "origin": "host",
            "entry_type": "next_open",
            "entry_low": levels["entry_low"],
            "entry_high": levels["entry_high"],
            "stop": levels["stop"],
            "target1": levels["target1"],
            "initial_stop": levels["stop"],
            "atr": atr_value,
            "levels_source": "host",
            "max_hold_sessions": env.cfg.v3.max_hold_default,
            "valid_from": valid_from,
            "valid_until": valid_until,
            "shares": None,
            "sizing": {},
            "warnings": [],
            "thesis": ADOPTED_THESIS,
            "invalidation": "",
            "counter": "",
            "refs": [],
            "agent_session": env.agent_session,
            "status": "open",
            "kind": "adopted",
            "start_session": env.session,
        },
    )
    plan_repo.insert_event(
        conn,
        {
            "plan_id": plan["id"],
            "session": env.session,
            "event": "adopted",
            "reason": "没有有效计划的买入，补录为已买入持仓",
            "before": {},
            "after": {"status": "open"},
            "source": "host",
            "refs": [opening_request_id] if opening_request_id else [],
        },
    )
    return plan


def ensure_data(env: ToolEnv, *, ticker: str) -> dict:
    """给名单外的股票补五个族的数据（chat 专用，spec §8）；返回按族整理的覆盖率。"""
    ticker = normalize_ticker(ticker)
    try:
        stock_id(ticker)
    except ValueError:
        raise ValueError("invalid_ticker: 非美股代码") from None
    now = datetime.now(UTC)
    rows = facts_collect.collect_extra(
        [ticker],
        session=env.session,
        now=now,
        run_id=facts_collect.new_run_id(now),
        trigger="manual_family",
        cfg=env.cfg,
        families=ENSURE_FAMILIES,
    )
    cov = coverage(rows)
    return {"families": cov["families"], "lines": coverage_lines(cov)}
