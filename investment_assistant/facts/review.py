"""The host's daily plan review (spec §5 补查, §6.3, §7.1): every active plan, one day at a time.

Each plan catches up from ``reviewed_through + 1`` (or its start anchor) through the session,
over trading days only. All arithmetic lives in ``execution.plan_line``; this module owns the
reads, the writes and the day order. Host events are idempotent per (plan, session, event) —
``plan_repo.insert_event`` returning ``None`` means the day was already processed. The caller
owns the transaction; every ``update_plan`` call carries at least one field.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from investment_assistant.config import AssistantConfig
from investment_assistant.core.texts import price_text
from investment_assistant.data import repo as data_repo
from investment_assistant.data.calendar import add_trading_days, trading_days_between
from investment_assistant.derivation.indicators import price as price_indicators
import investment_assistant.execution.plan_line as plan_line
from investment_assistant.execution.plan_levels import rule_levels
from investment_assistant.execution.positions import PositionError, build
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo

BARS_LIMIT = 400  # 起算日以来的日线：补查窗口远小于一年的交易日


def _emit(
    conn,
    plan_id: int,
    day: date,
    event: str,
    reason: str,
    *,
    source: str = "host",
    before: dict | None = None,
    after: dict | None = None,
) -> dict | None:
    return plan_repo.insert_event(
        conn,
        {
            "plan_id": plan_id,
            "session": day,
            "event": event,
            "reason": reason,
            "before": before or {},
            "after": after or {},
            "source": source,
            "refs": [],
        },
    )


def _start_anchor(plan: dict) -> date | None:
    """复查的起算日：reviewed_through 的下一个交易日，缺省回到起算日或有效期首日。"""
    if plan["reviewed_through"] is not None:
        return add_trading_days(plan["reviewed_through"], 1)
    if plan["start_session"] is not None:
        return plan["start_session"]
    return plan["valid_from"]


def _updatable(fields: dict) -> dict:
    return {key: value for key, value in fields.items() if key in plan_repo.UPDATABLE}


def review_plans(conn, *, session: date, cutoff_at: datetime, cfg: AssistantConfig) -> dict:
    """One pass over every active plan; returns ``{'changes': [events], 'unchecked': [tickers],
    'errors': [{'plan_id', 'ticker', 'error'}]}``. 一份计划一个事务（SAVEPOINT）：单份计划的
    毒数据只回滚它自己，不拖垮其余计划与整次运行（spec §5 步骤 3）。"""
    changes: list[dict] = []
    unchecked: list[str] = []
    errors: list[dict] = []
    for plan in plan_repo.plans_by_status(conn, ("pending", "open")):
        conn.execute("SAVEPOINT v3_review_one_plan")
        try:
            _review_one(conn, plan, session=session, cutoff_at=cutoff_at, cfg=cfg, changes=changes, unchecked=unchecked)
        except Exception as exc:  # noqa: BLE001 - 记录原因后续着复查其余计划；连接级故障在 ROLLBACK 时自然上抛
            conn.execute("ROLLBACK TO SAVEPOINT v3_review_one_plan")
            changes[:] = [row for row in changes if row["plan_id"] != plan["id"]]
            unchecked[:] = [ticker for ticker in unchecked if ticker != plan["ticker"]]
            errors.append({"plan_id": plan["id"], "ticker": plan["ticker"], "error": str(exc)[:200]})
        else:
            conn.execute("RELEASE SAVEPOINT v3_review_one_plan")
    return {"changes": changes, "unchecked": unchecked, "errors": errors}


def _review_one(
    conn, plan: dict, *, session: date, cutoff_at: datetime, cfg: AssistantConfig, changes: list, unchecked: list
) -> None:
    ticker = plan["ticker"]
    bars = data_repo.read_bars(conn, ticker, session, cutoff_at=cutoff_at, limit=BARS_LIMIT)
    bars_by_date = {bar["price_date"]: bar for bar in bars}
    earnings = data_repo.latest_earnings(conn, ticker, cutoff_at=cutoff_at)
    splits = data_repo.split_events(conn, ticker, cutoff_at=cutoff_at)
    trades = facts_repo.list_trades(conn, ticker)
    try:
        position = build(trades, splits=splits)["position"]
    except PositionError:
        position = None
    avg_cost = position["avg_cost"] if position is not None else Decimal(0)
    trail_atr = Decimal(str(cfg.v3.trail_atr))
    anchor = _start_anchor(plan)
    pending_splits = [split for split in splits if anchor is None or split[0] >= anchor]

    for day in trading_days_between(anchor, session) if anchor is not None else []:
        while pending_splits and pending_splits[0][0] <= day:
            split_day, factor = pending_splits.pop(0)
            plan = _apply_split(conn, plan, split_day, factor, changes)
        bar = bars_by_date.get(day)
        if plan["kind"] == "adopted" and plan["stop"] is None and bar is not None:
            plan = _fill_adopted_levels(conn, plan, day, bars, changes)
        if plan["status"] == "pending":
            hit = plan_line.pending_check(plan=plan, bar=bar, earnings=earnings, session=day)
            if hit is not None:
                saved = _emit(conn, plan["id"], day, hit["event"], hit["reason"])
                plan = plan_repo.update_plan(
                    conn,
                    plan["id"],
                    status=hit["event"],
                    ended_session=day,
                    end_reason=hit["reason"],
                    reviewed_through=day,
                )
                if saved is not None:
                    changes.append(saved)
                return  # 终态：本计划到此为止
            if bar is not None:  # 缺日线不推进：日线补上后「收盘跌破止损」还能查（spec §6.3）
                plan = plan_repo.update_plan(conn, plan["id"], reviewed_through=day)
            continue
        if bar is None:  # 缺日线：其他检查不做，不推进复查
            if day == session:
                unchecked.append(ticker)
            continue
        plan = _holding_day(
            conn,
            plan,
            day=day,
            bar=bar,
            bars=bars,
            splits=splits,
            avg_cost=avg_cost,
            trail_atr=trail_atr,
            changes=changes,
        )
    while pending_splits and pending_splits[0][0] <= session:  # 最后一根日线之后、session 之内的拆股
        split_day, factor = pending_splits.pop(0)
        plan = _apply_split(conn, plan, split_day, factor, changes)


def _folded_highest_close(bars: list[dict], splits: list, start: date, day: date) -> Decimal | None:
    """起算日到当日的最高收盘，全部折到当日口径：某根日线之后发生过的拆股，其收盘按累计
    比例除回去（spec §7.1 的 highest_close 与当日 stop/ATR 同基）。选择折算而不是在拆股日
    重置窗口——历史高点仍是有效高点；不折算则混用未复权收盘，移动止损会跳到现价上方，
    次日产生假 stop_hit。"""
    closes = []
    for row in bars:
        bar_day = row["price_date"]
        if not (start <= bar_day <= day):
            continue
        factor = Decimal(1)
        for split_day, split_factor in splits:
            if bar_day < split_day <= day:
                factor *= split_factor
        closes.append(Decimal(str(row["close"])) / factor)
    return max(closes) if closes else None


def _holding_day(
    conn,
    plan: dict,
    *,
    day: date,
    bar: dict,
    bars: list[dict],
    splits: list,
    avg_cost: Decimal,
    trail_atr: Decimal,
    changes: list,
) -> dict:
    start = plan["start_session"]
    highest_close = None
    if start is not None:
        highest_close = _folded_highest_close(bars, splits, start, day)
    result = plan_line.holding_check(
        plan=plan,
        bar=bar,
        session=day,
        highest_close=highest_close,
        avg_cost=avg_cost,
        held_sessions=plan_line.held_sessions(start, day) if start else 0,
        trail_atr=trail_atr,
    )
    fields: dict = {
        "reviewed_through": day,
        "stop": result["stop"],
        "trailing": result["trailing"],
        "sell_instruction": result["sell_instruction"],
    }
    for event in result["events"]:
        if event["event"] == "stop_hit":
            fields["stop_hit_session"] = day
        elif event["event"] == "target_hit":
            fields["target_hit_session"] = day
    plan = plan_repo.update_plan(conn, plan["id"], **fields)
    for event in result["events"]:
        saved = _emit(
            conn, plan["id"], day, event["event"], event["reason"], before=event.get("before"), after=event.get("after")
        )
        if saved is not None:
            changes.append(saved)
    return plan


def _apply_split(conn, plan: dict, split_day: date, factor: Decimal, changes: list) -> dict:
    before, after = plan_line.split_adjust(plan, factor)
    if not before:
        return plan
    reason = f"拆股 {factor}，价位、ATR 与股数按比例调整"
    saved = _emit(conn, plan["id"], split_day, "split_adjusted", reason, before=before, after=after)
    fields = _updatable(after)
    assert fields  # stop/atr/shares 总在 updatable 集合里
    plan = plan_repo.update_plan(conn, plan["id"], **fields)
    if saved is not None:
        changes.append(saved)
    return plan


def _fill_adopted_levels(conn, plan: dict, day: date, bars: list[dict], changes: list) -> dict:
    """补录计划缺价位：到这天为止的日线够算 ATR 就按规则价位补齐（spec §6.6）。"""
    history = [row for row in bars if row["price_date"] <= day]
    atr = price_indicators.atr14(history)
    if atr is None or atr <= 0:
        return plan  # 历史不够：下一天再试
    close = Decimal(str(history[-1]["close"]))
    levels = rule_levels(close, atr)
    reason = f"补录价位：最新收盘 {price_text(close)}，止损 {price_text(levels['stop'])}（2 倍 ATR {price_text(atr)}）"
    after = {**levels, "atr": atr}
    saved = _emit(conn, plan["id"], day, "stop_set", reason, before={}, after=after)
    plan = plan_repo.update_plan(
        conn,
        plan["id"],
        entry_low=levels["entry_low"],
        entry_high=levels["entry_high"],
        stop=levels["stop"],
        target1=levels["target1"],
        atr=atr,
    )
    if saved is not None:
        changes.append(saved)
    return plan
