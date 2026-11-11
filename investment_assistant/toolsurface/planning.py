"""Plan tools: compute_plan_levels plus the host-validated save_verdict / save_plan (spec §6
算/写), now writing the plan line of migration 034 with the slot and replacement rules of
spec §6.4."""

from __future__ import annotations

from decimal import Decimal

from investment_assistant.core.texts import NO_EQUITY_TEXT
from investment_assistant.data import repo as data_repo
from investment_assistant.derivation.indicators import price as price_indicators
from investment_assistant.execution.entry_rules import ENTRY_TYPES
from investment_assistant.execution.plan_levels import (
    MAX_HOLD_RANGE,
    earnings_flags,
    rule_levels,
    size,
    validate_levels,
    validity,
)
from investment_assistant.execution.plan_line import free_slots, replacement
from investment_assistant.execution.rotation import HOLD_REJECT_TEXT, NO_ROOM_TEXT, short_of_room
from investment_assistant.facts import plan_repo, research_repo, rotation_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts import rotation as facts_rotation
from investment_assistant.facts.account import account_view
from investment_assistant.toolsurface.env import ToolEnv
from investment_assistant.toolsurface.reads import holdings_rows, scope_tickers, session_bars
from investment_assistant.toolsurface.validation import (
    ToolError,
    check_agent_levels,
    in_scope,
    normalize_ticker,
)

VERDICTS = ("confirm", "veto", "unknown")
INCOMPLETE_WARNING = "有持仓缺日线，仓位占比偏低"  # Review Focus 1：钱够而有持仓缺收盘时计划照常保存，只加这一条警告
NEEDS_ROTATION_TEXT = "仓位已到上限，需先保存换仓结论"


def _equity_snapshot(conn, env: ToolEnv) -> dict | None:
    """仓位本金的来源：有期初现金取账本总资产，否则最近一次对账（spec §8.3）。"""
    return account_view(conn, session=env.session, cutoff_at=env.cutoff_at)["sizing_snapshot"]


def _exposure(conn, env: ToolEnv, ticker: str) -> tuple[Decimal, Decimal]:
    """(该标的市值, 持仓总市值)：持仓数量乘该交易日口径的最新收盘。"""
    ticker_value, total_value = Decimal(0), Decimal(0)
    for row in holdings_rows(conn, env):
        if "error" in row or row.get("last_close") is None:
            continue
        value = row["quantity"] * Decimal(str(row["last_close"]))
        total_value += value
        if row["ticker"] == ticker:
            ticker_value = value
    return ticker_value, total_value


def _sized(sizing: dict) -> dict:
    """``plan_levels.size`` 在持仓已超上限时算出负股数；v3 的约定是没有余量即 0 股（status 仍为 no_room）。"""
    if sizing.get("shares") is not None and sizing["shares"] < 0:
        return {**sizing, "shares": 0}
    return sizing


def _bars_atr(conn, env: ToolEnv, ticker: str) -> tuple[list[dict], Decimal]:
    bars = session_bars(conn, env, ticker)
    if not bars:
        raise ValueError("no_bars")
    atr = price_indicators.atr14(bars)
    if atr is None or atr <= 0:
        raise ValueError("atr_unavailable")
    return bars, atr


def compute_plan_levels(env: ToolEnv, *, ticker: str) -> dict:
    """宿主算术：规则价位、有效期、按风险预算的股数；金额与价位一律转字符串。"""
    ticker = normalize_ticker(ticker)

    def _read(conn):
        bars, atr = _bars_atr(conn, env, ticker)
        ref_price = Decimal(str(bars[-1]["close"]))  # session 当天未复权 close
        levels = rule_levels(ref_price, atr)
        valid_from, valid_until = validity(env.session, env.cfg.v3.valid_sessions)
        snapshot = _equity_snapshot(conn, env)
        ticker_value, total_value = _exposure(conn, env, ticker)
        sizing, _ = size(
            snapshot=snapshot,
            ref_price=ref_price,
            stop=levels["stop"],
            entry_high=levels["entry_high"],
            ticker_value=ticker_value,
            total_value=total_value,
            params=env.cfg.workbench,
        )
        earnings = data_repo.latest_earnings(conn, ticker, cutoff_at=env.cutoff_at)
        invalid, warnings = earnings_flags(
            earnings, signal_date=env.session, valid_from=valid_from, valid_until=valid_until
        )
        sized = _sized(sizing)
        return {
            "ticker": ticker,
            "session": env.session,
            "ref_price": str(ref_price),
            "atr": str(atr),
            "levels": {key: str(value) for key, value in levels.items()},
            "valid_from": valid_from,
            "valid_until": valid_until,
            "sizing": sized,
            "uncapped_shares": sized.get("uncapped_shares"),
            "short_of_room": short_of_room(sized, min_fill=Decimal(str(env.cfg.v3.rotation_min_fill))),
            "exposure": facts_rotation.exposure_row(conn, session=env.session, cutoff_at=env.cutoff_at, cfg=env.cfg),
            "earnings": {"invalid": invalid, "warnings": warnings},
            "entry_types": list(ENTRY_TYPES),
            "max_hold_range": list(MAX_HOLD_RANGE),
        }

    return env.read(_read)


def save_verdict(
    env: ToolEnv, *, ticker: str, verdict: str, reasons: str, counter: str = "", refs: list | None = None
) -> dict:
    ticker = normalize_ticker(ticker)
    refs = list(refs or [])
    if verdict not in VERDICTS:
        raise ValueError("invalid_verdict")
    if not isinstance(reasons, str) or not 1 <= len(reasons) <= 2000:
        raise ValueError("invalid_argument: reasons 需为 1–2000 字")
    text = counter if isinstance(counter, str) else ""
    if not isinstance(counter, str) or any(not isinstance(ref, str) for ref in refs):
        raise ValueError("invalid_argument: counter / refs 类型不符")

    def _work(conn):
        if not in_scope(env.mode, ticker, scope_tickers(conn, env)):
            raise ValueError("out_of_scope")
        known = facts_repo.tool_call_refs(conn, env.agent_session)
        if any(ref not in known for ref in refs) or (verdict in ("confirm", "veto") and not refs):
            raise ValueError("invalid_refs")
        saved = facts_repo.upsert_verdict(
            conn,
            {
                "session": env.session,
                "ticker": ticker,
                "verdict": verdict,
                "reasons": reasons,
                "counter": text,
                "refs": refs,
                "origin": env.mode,
                "agent_session": env.agent_session,
            },
        )
        pending = plan_repo.active_plan(conn, ticker)
        if verdict == "veto" and pending is not None and pending["status"] == "pending":
            plan_repo.update_plan(
                conn, pending["id"], status="invalidated", ended_session=env.session, end_reason=reasons
            )
            plan_repo.insert_event(
                conn,
                {
                    "plan_id": pending["id"],
                    "session": env.session,
                    "event": "invalidated",
                    "reason": reasons,
                    "before": {"status": "pending"},
                    "after": {"status": "invalidated"},
                    "source": "agent",
                    "refs": refs,
                },
            )
            facts_rotation.withdraw_orphans(conn, session=env.session)  # 作废的是换仓的 B：撤回 A 的指令
        return saved

    return env.write(_work)


def _levels_of(
    env: ToolEnv,
    conn,
    ticker: str,
    levels: dict | None,
    last_close: Decimal,
    atr: Decimal,
    entry_atr: Decimal = Decimal(2),
) -> tuple[dict[str, Decimal], str, Decimal]:
    """(价位, 来源, 仓位基准价)：agent 价位过两道校验；缺省取宿主规则价位。
    研究计划的入场下限用放宽倍数（spec §6.6 第 3 条）。"""
    if levels is None:
        return rule_levels(last_close, atr), "host", last_close
    try:
        chosen = validate_levels(levels)
    except ValueError as exc:
        raise ValueError(f"levels_invalid: {exc}") from None
    problems = check_agent_levels(chosen, last_close=last_close, atr=atr, entry_atr=entry_atr)
    if problems:
        raise ValueError(f"levels_invalid: {', '.join(problems)}")
    # 与自有计划同口径：风险按入场带中点计（spec §6 价位校验、§7 现有算术原样）
    return chosen, "agent", (chosen["entry_low"] + chosen["entry_high"]) / 2


def _top_rows(conn, env: ToolEnv) -> list[dict]:
    run = facts_repo.latest_run(conn, env.session)
    if run is None:
        return []
    return [
        {"ticker": row["ticker"], "rank": row["rank"]}
        for row in facts_repo.candidates(conn, run["run_id"])
        if row["rank"] <= env.cfg.v3.top_n
    ]


def _rule_pending(conn) -> list[dict]:
    """名额与替换只约束规则名单的计划（spec §6.6 第 1 条）：研究转成的计划不占名额、不做替换受害者。"""
    return [plan for plan in plan_repo.plans_by_status(conn, ("pending",)) if plan["research_id"] is None]


def _replacement_for(conn, env: ToolEnv) -> dict | None:
    """名额满时的替换候选（spec §6.4）：与 agenda 同一口径——有观察中研究的股票不做候选（R6），
    受害者只从规则计划里选（spec §6.6）；宿主按名次规则选定，agent 不能挑。"""
    watching = set(research_repo.watching_tickers(conn))
    return replacement(
        top_rows=[row for row in _top_rows(conn, env) if row["ticker"] not in watching],
        pending=_rule_pending(conn),
        active_tickers={plan["ticker"] for plan in plan_repo.plans_by_status(conn, ("pending", "open"))},
        replaced_today=False,
    )


def _check_slot(conn, env: ToolEnv, ticker: str) -> dict | None:
    """返回 None 表示有空位；否则抛 no_free_slot / replacement_rank_not_better / replaced_today，
    满足替换条件时返回替换上下文（受害者计划编号）。"""
    pending = _rule_pending(conn)
    if free_slots(pending_count=len(pending), cap=env.cfg.v3.pending_cap) > 0:
        return None
    detail = {"pending": pending}
    if any(row["event"] == "replaced" for row in plan_repo.events_on(conn, env.session)):
        raise ToolError("replaced_today: 名额已满，且今天已替换过一次", detail=detail)
    if not _top_rows(conn, env):
        raise ToolError("no_free_slot: 名额已满", detail=detail)
    slot = _replacement_for(conn, env)
    if slot is None:
        raise ToolError("replacement_rank_not_better: 候选名次不高于当前名次最差的待入场计划", detail=detail)
    if slot["candidate"] != ticker:
        raise ToolError("no_free_slot: 名额已满", detail={**detail, "replacement": slot})
    return slot


def _prepare_plan(
    conn,
    env: ToolEnv,
    *,
    ticker: str,
    entry_type: str,
    hold: int | None,
    levels: dict | None,
    funding: dict | None,
) -> dict:
    """``save_plan`` 与 ``save_rotation`` 共用的全部检查与算数，不写任何东西（spec §6.5）：入场类型 →
    最长持有 → 价位 → 财报 → 已有有效计划 → 名额与替换 → （非换仓的）仓位闸门 → 股数。
    ``funding`` 给出时价位只用宿主规则价位，股数按 ``total_value − funding['sold_value']`` 算。"""
    if entry_type not in ENTRY_TYPES:
        allowed = "、".join(ENTRY_TYPES)
        raise ValueError(f"invalid_entry_type: 需为 {allowed} 之一")
    chosen_hold = hold if hold is not None else env.cfg.v3.max_hold_default  # 缺省最长持有取配置
    if not MAX_HOLD_RANGE[0] <= chosen_hold <= MAX_HOLD_RANGE[1]:
        raise ValueError(f"invalid_max_hold: 需为 {MAX_HOLD_RANGE[0]}–{MAX_HOLD_RANGE[1]} 的整数")
    if levels is not None and not isinstance(levels, dict):
        raise ValueError("invalid_argument: levels 必须是对象")
    if funding is not None and levels is not None:
        raise ValueError("rotation_levels_fixed: 换仓的计划只用宿主规则价位")
    bars, atr = _bars_atr(conn, env, ticker)
    last_close = Decimal(str(bars[-1]["close"]))
    linked = research_repo.watching_research(conn, ticker)
    is_research = linked is not None and linked["verdict"] == "ready"  # 仅 ready 算关联（spec §6.6）
    chosen, source, sizing_ref = _levels_of(
        env,
        conn,
        ticker,
        levels,
        last_close,
        atr,
        entry_atr=Decimal(str(env.cfg.v3.research_entry_atr)) if is_research else Decimal(2),
    )
    valid_from, valid_until = validity(env.session, env.cfg.v3.valid_sessions)
    earnings = data_repo.latest_earnings(conn, ticker, cutoff_at=env.cutoff_at)
    invalid, warnings = earnings_flags(earnings, signal_date=env.session, valid_from=valid_from, valid_until=valid_until)
    if "earnings_in_validity" in invalid:
        raise ValueError("earnings_in_validity")
    existing = plan_repo.active_plan(conn, ticker)
    if existing is not None:
        raise ToolError("active_plan_exists: 该股票已有有效计划", detail=existing)
    if linked is not None and not is_research:
        raise ValueError("research_not_ready: 研究尚未给出「可以出计划」的结论")
    slot_replacement = None if is_research else _check_slot(conn, env, ticker)
    if funding is None:
        _check_room_gate(conn, env, ticker)
    snapshot = _equity_snapshot(conn, env)
    ticker_value, total_value = _exposure(conn, env, ticker)
    sold_value = funding["sold_value"] if funding is not None else Decimal(0)
    sizing, size_invalid = size(
        snapshot=snapshot,
        ref_price=sizing_ref,
        stop=chosen["stop"],
        entry_high=chosen["entry_high"],
        ticker_value=ticker_value,
        total_value=total_value - sold_value,
        params=env.cfg.workbench,
    )
    sizing = _sized(sizing)
    if funding is not None:
        if sizing["shares"] is None or sizing["shares"] < 1:
            raise ValueError("no_room_after_rotation: 换仓之后额度仍不够买 1 股，换仓结论不成立")
    elif sizing["shares"] == 0:  # 待办判为「钱够」而自报价位算出 0 股：不留 0 股的计划（spec §5.2）
        raise ToolError(f"no_room: {NO_ROOM_TEXT}")
    plan_warnings = [*warnings, *size_invalid]
    if sizing["status"] == "no_equity":
        plan_warnings.append(NO_EQUITY_TEXT)
    if facts_rotation.exposure_row(conn, session=env.session, cutoff_at=env.cutoff_at, cfg=env.cfg)["incomplete"]:
        plan_warnings.append(INCOMPLETE_WARNING)
    return {
        "chosen": chosen,
        "source": source,
        "atr": atr,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "hold": chosen_hold,
        "sizing": sizing,
        "warnings": plan_warnings,
        "linked": linked,
        "is_research": is_research,
        "slot_replacement": slot_replacement,
    }


def _check_room_gate(conn, env: ToolEnv, ticker: str) -> None:
    """仓位闸门（spec §6.5）：「钱不够」时没有换仓结论就不出计划；当天已比较过且继续持有 → 拒绝并写明。"""
    gate = facts_rotation.gate(
        conn, session=env.session, cutoff_at=env.cutoff_at, cfg=env.cfg, ticker=ticker, mode=env.mode
    )
    if not gate["short"]:
        return
    held_today = any(
        row["candidate"] == ticker and row["verdict"] == "hold" for row in rotation_repo.rotations_on(conn, env.session)
    )
    if held_today:
        raise ToolError(f"no_room: {HOLD_REJECT_TEXT}")
    if gate["compare"]:
        raise ToolError(f"needs_rotation: {NEEDS_ROTATION_TEXT}", detail=gate)
    raise ToolError(f"no_room: {gate['blocked_text']}")


def _commit_plan(
    conn,
    env: ToolEnv,
    ctx: dict,
    *,
    ticker: str,
    thesis: str,
    invalidation: str,
    entry_type: str,
    counter: str,
    refs: list,
    funding: dict | None,
) -> dict:
    """写入 ``_prepare_plan`` 算好的计划：计划行、created 事件、研究关联、被替换的待入场计划。
    ``funding`` 带上 ``rotation_id`` 时计划记资金来源（spec §6.5）。"""
    sizing, reason = ctx["sizing"], thesis
    funded_by = None
    if funding is not None:
        funded_by = funding["rotation_id"]
        sizing = {
            **sizing,
            "funded": {
                "rotation_id": funded_by,
                "holding_ticker": funding["holding_ticker"],
                "sell_quantity": funding["sell_quantity"],
            },
        }
        reason = f"{thesis}；资金来自换仓 {funding['holding_ticker']}"
    chosen, linked = ctx["chosen"], ctx["linked"]
    is_research = ctx["is_research"]
    saved = plan_repo.insert_plan(
        conn,
        {
            "session": env.session,
            "ticker": ticker,
            "origin": env.origin,
            "entry_type": entry_type,
            "entry_low": chosen["entry_low"],
            "entry_high": chosen["entry_high"],
            "stop": chosen["stop"],
            "target1": chosen["target1"],
            "initial_stop": chosen["stop"],
            "atr": ctx["atr"],
            "levels_source": ctx["source"],
            "max_hold_sessions": ctx["hold"],
            "valid_from": ctx["valid_from"],
            "valid_until": ctx["valid_until"],
            "shares": sizing["shares"],
            "sizing": sizing,
            "warnings": ctx["warnings"],
            "thesis": thesis,
            "invalidation": invalidation,
            "counter": counter,
            "refs": refs,
            "agent_session": env.agent_session,
            "research_id": linked["id"] if linked is not None and is_research else None,
            "funded_by_rotation_id": funded_by,
        },
    )
    plan_repo.insert_event(
        conn,
        {
            "plan_id": saved["id"],
            "session": env.session,
            "event": "created",
            "reason": reason,
            "before": {},
            "after": {"status": "pending"},
            "source": "agent",
            "refs": refs,
        },
    )
    if linked is not None and is_research:
        # 研究记 planned 并关联计划（spec §6.3）：之后这份计划与别的计划一样
        research_repo.update_research(
            conn,
            linked["id"],
            status="planned",
            plan_id=saved["id"],
            ended_session=env.session,
            end_reason="已转计划",
        )
        research_repo.insert_research_event(
            conn,
            {
                "research_id": linked["id"],
                "session": env.session,
                "event": "planned",
                "reason": f"已转计划 {saved['id']}",
                "before": {"status": "watching"},
                "after": {"status": "planned", "plan_id": saved["id"]},
                "source": "agent",
                "refs": refs,
            },
        )
    slot_replacement = ctx["slot_replacement"]
    if slot_replacement is not None:
        reason = f"名额替换：换成 {ticker}"
        plan_repo.update_plan(
            conn,
            slot_replacement["victim_plan_id"],
            status="replaced",
            ended_session=env.session,
            end_reason=reason,
        )
        plan_repo.insert_event(
            conn,
            {
                "plan_id": slot_replacement["victim_plan_id"],
                "session": env.session,
                "event": "replaced",
                "reason": reason,
                "before": {"status": "pending"},
                "after": {"status": "replaced"},
                "source": "host",
                "refs": [],
            },
        )
        facts_rotation.withdraw_orphans(conn, session=env.session)  # 被替换的可能是别的换仓的 B
    return saved


def _save_plan_tx(
    conn,
    env: ToolEnv,
    *,
    ticker: str,
    thesis: str,
    invalidation: str,
    entry_type: str,
    hold: int | None,
    levels: dict | None,
    counter: str,
    refs: list,
    funding: dict | None = None,
) -> dict:
    """事务内的保存计划（``save_plan`` 与 ``save_rotation`` 共用，spec §6.5）：入场类型 → 最长持有 → 价位 →
    财报 → 已有有效计划 → 名额与替换 → 股数 → 写入。``funding = {rotation_id, holding_ticker, sell_quantity,
    sold_value}``；有 ``funding`` 时 ``levels`` 必须为 ``None``。"""
    ctx = _prepare_plan(conn, env, ticker=ticker, entry_type=entry_type, hold=hold, levels=levels, funding=funding)
    return _commit_plan(
        conn,
        env,
        ctx,
        ticker=ticker,
        thesis=thesis,
        invalidation=invalidation,
        entry_type=entry_type,
        counter=counter,
        refs=refs,
        funding=funding,
    )


def save_plan(
    env: ToolEnv,
    *,
    ticker: str,
    thesis: str,
    invalidation: str,
    entry_type: str = "zone_limit",
    max_hold_sessions: int | None = None,
    levels: dict | None = None,
    counter: str = "",
    refs: list | None = None,
) -> dict:
    """检查顺序固定（spec §6、§6.4）：范围 → 确认结论 → 入场类型 → 最长持有（缺省取配置）→
    价位 → 财报日 → 已有有效计划 → 名额与替换 → 仓位闸门（v3.3）→ 股数。"""
    ticker = normalize_ticker(ticker)
    refs = list(refs or [])
    if (
        not isinstance(thesis, str)
        or not isinstance(invalidation, str)
        or not isinstance(counter, str)
        or any(not isinstance(ref, str) for ref in refs)
    ):
        raise ValueError("invalid_argument: thesis / invalidation / counter / refs 类型不符")
    hold = max_hold_sessions if isinstance(max_hold_sessions, int) and not isinstance(max_hold_sessions, bool) else None

    def _work(conn):
        if not in_scope(env.mode, ticker, scope_tickers(conn, env)):
            raise ValueError("out_of_scope")
        verdict = facts_repo.verdicts(conn, env.session).get(ticker)
        if verdict is None or verdict["verdict"] != "confirm":
            raise ValueError("no_confirm_verdict")
        return _save_plan_tx(
            conn,
            env,
            ticker=ticker,
            thesis=thesis,
            invalidation=invalidation,
            entry_type=entry_type,
            hold=hold,
            levels=levels,
            counter=counter,
            refs=refs,
        )

    return env.write(_work)
