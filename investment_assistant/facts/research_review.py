"""The host's daily research review (spec §6.4): every watching research with a condition,
one day at a time from the day after ``checked_through`` (or ``created_session``) through the
session. All condition arithmetic lives in ``execution.research``; this module owns the reads,
the writes and the day order, mirroring ``facts.review``.

Host events are idempotent per (research, session, event) — ``research_repo.insert_research_event``
returning ``None`` means the day was already processed. The caller owns the transaction; every
``update_research`` call carries at least one field.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.data.calendar import add_trading_days, trading_days_between
from investment_assistant.execution.research import research_check
from investment_assistant.facts import research_repo

BARS_LIMIT = 400  # 与 facts.review 同窗口：均线条件最多 200 根，补查窗口远小于一年


def _emit(conn, research_id: int, day: date, event: str, reason: str, *, before: dict, after: dict) -> dict | None:
    return research_repo.insert_research_event(
        conn,
        {
            "research_id": research_id,
            "session": day,
            "event": event,
            "reason": reason,
            "before": before,
            "after": after,
            "source": "host",
            "refs": [],
        },
    )


def review_research(conn, *, session: date, cutoff_at: datetime, cfg: AssistantConfig) -> dict:
    """One pass over every watching research with a condition; returns ``{'changes': [events],
    'unchecked': [tickers], 'errors': [{'research_id', 'ticker', 'error'}]}``. 一份研究一个事务
    （SAVEPOINT）：毒条件只回滚它自己的研究，不拖垮其余研究与整次运行（同 ``facts.review``）。"""
    del cfg  # 条件检查不用配置；签名与 review_plans 对齐
    changes: list[dict] = []
    unchecked: list[str] = []
    errors: list[dict] = []
    for row in research_repo.research_by_status(conn, ("watching",)):
        conn.execute("SAVEPOINT v3_review_one_research")
        try:
            if row.get("condition"):
                _review_one(conn, row, session=session, cutoff_at=cutoff_at, changes=changes, unchecked=unchecked)
            else:
                # 尚无条件：宿主不逐日检查，但到有效期自然过期（spec §6.3「一直没有结论」）
                _expire_without_condition(conn, row, session=session, changes=changes)
        except Exception as exc:  # noqa: BLE001 - 记录原因后续着复查其余研究；连接级故障在 ROLLBACK 时自然上抛
            conn.execute("ROLLBACK TO SAVEPOINT v3_review_one_research")
            changes[:] = [event for event in changes if event["research_id"] != row["id"]]
            unchecked[:] = [ticker for ticker in unchecked if ticker != row["ticker"]]
            errors.append({"research_id": row["id"], "ticker": row["ticker"], "error": str(exc)[:200]})
        else:
            conn.execute("RELEASE SAVEPOINT v3_review_one_research")
    return {"changes": changes, "unchecked": unchecked, "errors": errors}


def _expire_without_condition(conn, row: dict, *, session: date, changes: list) -> None:
    """尚无条件的研究到有效期自然过期（spec §6.3）：不看日线，日期一到即终态；reason 沿用
    「{n} 个交易日条件未到」口径，n 从 created_session 的下一个日历日起算（同 research_check）。"""
    if session < row["valid_until"]:
        return
    anchor = (
        add_trading_days(row["checked_through"], 1)
        if row["checked_through"] is not None
        else add_trading_days(row["created_session"], 1)
    )
    day = max(row["valid_until"], anchor)
    days = len(trading_days_between(row["created_session"] + timedelta(days=1), day))
    reason = f"{days} 个交易日条件未到"
    research_repo.update_research(
        conn, row["id"], status="expired", ended_session=day, end_reason=reason, checked_through=day
    )
    saved = _emit(
        conn,
        row["id"],
        day,
        "expired",
        reason,
        before={"status": "watching"},
        after={"status": "expired", "ended_session": day},
    )
    if saved is not None:
        changes.append(saved)


def _review_one(conn, row: dict, *, session: date, cutoff_at: datetime, changes: list, unchecked: list) -> None:
    ticker = row["ticker"]
    bars = data_repo.read_bars(conn, ticker, session, cutoff_at=cutoff_at, limit=BARS_LIMIT)
    bars_by_date = {bar["price_date"]: bar for bar in bars}
    anchor = (
        add_trading_days(row["checked_through"], 1)
        if row["checked_through"] is not None
        else add_trading_days(row["created_session"], 1)
    )
    history: list[dict] = []
    index = 0
    for day in trading_days_between(anchor, session):
        while index < len(bars) and bars[index]["price_date"] <= day:
            history.append(bars[index])  # 均线条件要「到当天为止」的日线
            index += 1
        bar = bars_by_date.get(day)
        if bar is None:
            # 缺日线：不判、不推进，且停在缺口——补上后从缺口日重查，不跨过它把游标推到更晚
            # （spec §6.4 第 1 条 / Review Focus 2）；当天缺日线进 unchecked，判定与停游标独立
            if day == session:
                unchecked.append(ticker)
            break
        result = research_check(research=row, bar=bar, bars=history, session=day)
        if result is None:
            row = research_repo.update_research(conn, row["id"], checked_through=day)
            continue
        if result["event"] == "triggered":
            row = research_repo.update_research(conn, row["id"], triggered_session=day, checked_through=day)
            saved = _emit(
                conn,
                row["id"],
                day,
                "triggered",
                result["reason"],
                before={"triggered_session": None},
                after={"triggered_session": day, "checked_through": day},
            )
            if saved is not None:
                changes.append(saved)
        else:  # expired：终态即停（spec §6.4 第 3 条）
            row = research_repo.update_research(
                conn,
                row["id"],
                status="expired",
                ended_session=day,
                end_reason=result["reason"],
                checked_through=day,
            )
            saved = _emit(
                conn,
                row["id"],
                day,
                "expired",
                result["reason"],
                before={"status": "watching"},
                after={"status": "expired", "ended_session": day},
            )
            if saved is not None:
                changes.append(saved)
            return
