"""Rotation tools (spec §6.4–§6.6, §7.3, §10): the comparison table the agent reads, the host-validated
``save_rotation`` that turns a conclusion into one transaction (record → B's plan → A's instruction)
and the chat-only ``withdraw_rotation``.

The agent never writes a number: the tools take no share, price or amount field, the host computes
the sell quantity, B's share count and B's levels."""

from __future__ import annotations

from investment_assistant.core.texts import quantity_value
from investment_assistant.execution.rotation import SINGLE_READS, VERDICT_TEXT, VERDICTS, sell_quantity
from investment_assistant.facts import plan_repo, rotation_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts import rotation as facts_rotation
from investment_assistant.toolsurface import planning
from investment_assistant.toolsurface.env import ToolEnv
from investment_assistant.toolsurface.plans import _known_refs, _plan_or_error, _replayed_event
from investment_assistant.toolsurface.reads import scope_tickers
from investment_assistant.toolsurface.validation import (
    ToolError,
    confirmed_flag,
    in_scope,
    normalize_ticker,
    note_text,
    text_arg,
)

ROOM_ENOUGH_TEXT = "额度足够，直接出计划即可"
NO_HOLDING_TEXT = "仓位已到上限，没有可比的持仓"
WITHDRAW_TEXT = "换仓指令撤回：用户撤回"


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _scoped_confirmed(conn, env: ToolEnv, ticker: str) -> None:
    """B 在范围内，且当天的结论是确认（spec §6.1 条件 1）。"""
    if not in_scope(env.mode, ticker, scope_tickers(conn, env)):
        raise ValueError("out_of_scope")
    verdict = facts_repo.verdicts(conn, env.session).get(ticker)
    if verdict is None or verdict["verdict"] != "confirm":
        raise ValueError("no_confirm_verdict")


def _gated(conn, env: ToolEnv, ticker: str) -> dict:
    """闸门（spec §6.1）：钱够 → room_enough；钱不够而不比较 → 原因代码与文案。"""
    gate = facts_rotation.gate(
        conn, session=env.session, cutoff_at=env.cutoff_at, cfg=env.cfg, ticker=ticker, mode=env.mode
    )
    if not gate["short"]:
        raise ValueError(f"room_enough: {ROOM_ENOUGH_TEXT}")
    if not gate["compare"]:
        raise ToolError(f"{gate['blocked']}: {gate['blocked_text']}")
    return gate


def get_rotation_table(env: ToolEnv, *, ticker: str) -> dict:
    """换仓比较表（spec §6.3）：候选 B、账户占比、每只可比持仓及半仓 / 全仓各自的账、手续费与换仓频率。"""
    ticker = normalize_ticker(ticker)

    def _read(conn):
        _scoped_confirmed(conn, env, ticker)
        _gated(conn, env, ticker)
        return facts_rotation.comparison_table(
            conn, session=env.session, cutoff_at=env.cutoff_at, cfg=env.cfg, candidate=ticker
        )

    return env.read(_read)


def _read_holding(conn, env: ToolEnv, refs: list[str], holding_ticker: str) -> bool:
    """引用里至少有一条对 A 的单只读取（spec §6.4 / R6）：本会话成功的调用，工具在 SINGLE_READS 里且参数是 A。"""
    for ref in refs:
        _, _, seq = ref.partition(":")
        if not seq.isdigit():
            continue
        call = facts_repo.tool_call(conn, env.agent_session, int(seq))
        if call is None or call["tool"] not in SINGLE_READS or call["error"] is not None:
            continue
        shown = (call["args"] or {}).get("ticker")
        if isinstance(shown, str) and shown.strip().upper() == holding_ticker:
            return True
    return False


def _preview(conn, env: ToolEnv, *, ticker, verdict, holding, sell, shares) -> dict:
    count = facts_rotation.recent_switch_count(conn, session=env.session, cfg=env.cfg)
    recent = f"最近 {env.cfg.v3.rotation_cooldown_sessions} 个交易日已换仓 {count} 次"
    if verdict == "hold":
        named = f"（最接近被换的是 {holding['ticker']}）" if holding is not None else ""
        text = f"将保存换仓结论：{ticker} {VERDICT_TEXT[verdict]}{named}。{recent}。确认后写入。"
    else:
        text = (
            f"将保存换仓结论：{VERDICT_TEXT[verdict]}。卖出 {holding['ticker']} {quantity_value(sell)} 股，"
            f"买入 {ticker} {shares} 股（按最新收盘估算）。{recent}。确认后写入。"
        )
    return {"write": False, "preview": text}


def _replayed(conn, row: dict) -> dict:
    plan = plan_repo.plan_funded_by(conn, row["id"])
    holding = plan_repo.plan(conn, row["holding_plan_id"]) if row["holding_plan_id"] is not None else None
    return {"rotation": row, "plan": plan, "holding": holding, "replay": True}


def save_rotation(
    env: ToolEnv,
    *,
    ticker: str,
    verdict: str,
    reasons: str,
    holding_plan_id=None,
    weakness: str = "",
    counter: str = "",
    cost_response: str = "",
    refs: list | None = None,
    thesis: str | None = None,
    invalidation: str | None = None,
    entry_type: str = "zone_limit",
    max_hold_sessions=None,
    request_id: str | None = None,
    confirmed=None,
) -> dict:
    """保存换仓结论（spec §6.4、§6.5）。检查顺序固定：入参 → 范围与确认结论 → 闸门 → 简报当天一次 / 对话要
    request_id → 可比持仓 → 继续持有直接写；半仓 / 全仓再查 A 的问题、成本回应、对 A 的读取、论点 → 卖出股数 →
    B 的计划检查与股数 → 对话未确认返回复述 → 一个事务里写（换仓记录、B 的计划、A 的指令）。"""
    ticker = normalize_ticker(ticker)
    if verdict not in VERDICTS:
        raise ValueError("invalid_verdict")
    if not isinstance(reasons, str) or not 1 <= len(reasons) <= 2000:
        raise ValueError("invalid_argument: reasons 需为 1–2000 字")
    refs = list(refs or [])
    if (
        any(not isinstance(text, str) for text in (weakness, counter, cost_response, entry_type))
        or any(not (text is None or isinstance(text, str)) for text in (thesis, invalidation, request_id))
        or any(not isinstance(ref, str) for ref in refs)
    ):
        raise ValueError("invalid_argument: 文本字段类型不符")
    if holding_plan_id is not None and not _is_int(holding_plan_id):
        raise ValueError("invalid_argument: holding_plan_id 必须是整数")
    if max_hold_sessions is not None and not _is_int(max_hold_sessions):
        raise ValueError("invalid_argument: max_hold_sessions 必须是整数")
    write = confirmed_flag(confirmed) if confirmed is not None else False
    write = write or env.mode == "brief"  # 简报会话直接写；对话先复述后写
    hold_sessions = max_hold_sessions

    def _work(conn, write_now: bool):
        if env.mode == "chat" and request_id:
            replay = rotation_repo.rotation_by_request(conn, request_id)
            if replay is not None:  # 同一 request_id 重发：返回原行，不重复写（Review Focus 4）
                if (replay["candidate"], replay["verdict"], replay.get("holding_plan_id")) != (
                    ticker, verdict, holding_plan_id
                ):
                    raise ValueError(f"request_id_reused: 该 request_id 已用于 {replay['candidate']} 的换仓结论")
                return _replayed(conn, replay)
        _scoped_confirmed(conn, env, ticker)
        _gated(conn, env, ticker)
        if env.mode == "chat" and not request_id:
            raise ValueError("invalid_argument: 对话模式需带 request_id")
        comp = facts_rotation.comparable(
            conn, session=env.session, cutoff_at=env.cutoff_at, cfg=env.cfg, require_review=True
        )
        if not comp["holdings"]:
            raise ToolError(f"no_holding: {NO_HOLDING_TEXT}")
        holding = None
        if holding_plan_id is not None:
            holding = next((row for row in comp["holdings"] if row["id"] == holding_plan_id), None)
            if holding is None:
                plan = plan_repo.plan(conn, holding_plan_id)
                if plan is None:
                    raise ValueError("unknown_plan")
                if plan["status"] != "open":
                    raise ValueError("not_open")
                excluded = next((row for row in comp["excluded"] if row["plan_id"] == holding_plan_id), None)
                raise ValueError(f"not_comparable: {excluded['text'] if excluded else '没有可核算的持仓'}")
        checked = _known_refs(conn, env, refs, required=(verdict == "hold"))
        sell = shares = ctx = funding = None
        if verdict != "hold":
            if holding is None:
                raise ValueError("holding_required: 半仓或全仓需用 holding_plan_id 指明要换掉的持仓")
            if not weakness.strip() or len(weakness) > 2000:
                raise ValueError("weakness_required: 需写明持仓哪里没按预期走（1–2000 字）")
            if not cost_response.strip():
                raise ValueError("cost_response_required: 需回应表里换仓的那笔账")
            if not _read_holding(conn, env, checked, holding["ticker"]):
                raise ValueError(f"no_holding_read: 引用里需有一次以 {holding['ticker']} 为参数的单只股票读取")
            if not thesis or not invalidation:
                raise ValueError("invalid_argument: 半仓或全仓需同时带 B 的 thesis 与 invalidation")
            try:
                sell = sell_quantity(verdict, holding["position"]["quantity"])
            except ValueError:
                raise ValueError("half_needs_two_shares: 持仓不足 2 股，不能半仓换") from None
            funding = {
                "rotation_id": None,
                "holding_ticker": holding["ticker"],
                "sell_quantity": sell,
                "sold_value": sell * holding["last_close"],
            }
            ctx = planning._prepare_plan(
                conn, env, ticker=ticker, entry_type=entry_type, hold=hold_sessions, levels=None, funding=funding
            )
            shares = ctx["sizing"]["shares"]
        if not write_now:
            return _preview(conn, env, ticker=ticker, verdict=verdict, holding=holding, sell=sell, shares=shares)
        table = facts_rotation.comparison_table(
            conn, session=env.session, cutoff_at=env.cutoff_at, cfg=env.cfg, candidate=ticker
        )
        row = rotation_repo.insert_rotation(
            conn,
            {
                "session": env.session,
                "origin": env.mode,
                "candidate": ticker,
                "verdict": verdict,
                "holding_plan_id": holding["id"] if holding is not None else None,
                "holding_ticker": holding["ticker"] if holding is not None else None,
                "reasons": reasons,
                "weakness": weakness,
                "counter": counter,
                "cost_response": cost_response,
                "refs": checked,
                "snapshot": table,
                "sell_quantity": sell,
                "buy_shares": shares,
                "agent_session": env.agent_session,
                "request_id": request_id,
            },
        )
        if verdict == "hold":
            return {"rotation": row, "plan": None, "holding": None}
        funding["rotation_id"] = row["id"]
        plan_b = planning._commit_plan(
            conn,
            env,
            ctx,
            ticker=ticker,
            thesis=thesis,
            invalidation=invalidation,
            entry_type=entry_type,
            counter=counter,
            refs=checked,
            funding=funding,
        )
        # 一条 UPDATE 同时设三列，满足库约束（sell_instruction='rotation' ⇔ rotation_id ⇔ sell_quantity）
        plan_a = plan_repo.update_plan(
            conn, holding["id"], sell_instruction="rotation", sell_quantity=sell, rotation_id=row["id"]
        )
        plan_repo.insert_event(
            conn,
            {
                "plan_id": holding["id"],
                "session": env.session,
                "event": "rotation_instruction",
                "reason": f"换仓指令：卖出 {quantity_value(sell)} 股，换至 {ticker}",
                "before": {},
                "after": {"sell_instruction": "rotation", "sell_quantity": sell, "rotation_id": row["id"]},
                "source": "user" if env.mode == "chat" else "agent",
                "refs": [request_id] if env.mode == "chat" else checked,
            },
        )
        return {"rotation": row, "plan": plan_b, "holding": plan_a}

    if write:
        return env.write(lambda conn: _work(conn, True))
    return env.read(lambda conn: _work(conn, False))


def withdraw_rotation(env: ToolEnv, *, plan_id, request_id, confirmed, reason: str = "") -> dict:
    """撤回 A 上还没执行完的换仓指令（仅对话，spec §6.6）：先复述，确认后三列一起清空并记一行；
    按 request_id 幂等。B 的计划不动。"""
    request_id = text_arg(request_id, "request_id")
    reason_text = note_text(reason)
    is_confirmed = confirmed_flag(confirmed)

    def _instruction(conn) -> tuple[dict, str]:
        plan = _plan_or_error(conn, plan_id)
        if plan["status"] != "open" or plan["sell_instruction"] != "rotation":
            raise ValueError("no_rotation_instruction: 该计划没有未执行完的换仓指令")
        record = rotation_repo.rotation(conn, plan["rotation_id"])
        return plan, record["candidate"] if record is not None else "?"

    if not is_confirmed:

        def _peek(conn):
            plan, candidate = _instruction(conn)
            suffix = f"，原因：{reason_text}" if reason_text else ""
            return {
                "write": False,
                "preview": f"将撤回 {plan['ticker']} 的换仓指令（卖出 {quantity_value(plan['sell_quantity'])} 股，"
                f"换至 {candidate}）{suffix}。确认后写入。",
            }

        return env.read(_peek)

    def _work(conn):
        replay = _replayed_event(conn, _plan_or_error(conn, plan_id)["id"], request_id, "rotation_withdrawn")
        if replay is not None:
            return {"plan": plan_repo.plan(conn, plan_id), "event": replay, "replay": True}
        plan, _ = _instruction(conn)
        text = WITHDRAW_TEXT + (f"（{reason_text}）" if reason_text else "")
        before = {"rotation_id": plan["rotation_id"], "sell_quantity": plan["sell_quantity"]}
        updated = plan_repo.update_plan(conn, plan["id"], sell_instruction=None, sell_quantity=None, rotation_id=None)
        event = plan_repo.insert_event(
            conn,
            {
                "plan_id": plan["id"],
                "session": env.session,
                "event": "rotation_withdrawn",
                "reason": text,
                "before": before,
                "after": {},
                "source": "user",
                "refs": [request_id],
            },
        )
        return {"plan": updated, "event": event}

    return env.write(_work)

