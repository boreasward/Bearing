"""Research tools (spec §6, §9): start / conclude / drop / read research. User writes
(start, drop, watch-with-a-new-condition) restate first, write only on ``confirmed`` and are
idempotent by ``request_id`` stored in ``v3_research_events.refs``; agent writes cite the tool
calls they read."""

from __future__ import annotations

from investment_assistant.data.calendar import add_trading_days
from investment_assistant.execution.research import (
    RESEARCH_STATUS_TEXT,
    VERDICTS,
    condition_text,
    validate_condition,
)
from investment_assistant.facts import plan_repo, research_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.toolsurface.env import ToolEnv
from investment_assistant.toolsurface.validation import ToolError, confirmed_flag, normalize_ticker, note_text, text_arg

VERDICT_TEXT = {"ready": "可以出计划", "watch": "值得盯", "reject": "否决"}


def _research_or_error(conn, research_id) -> dict:
    if not isinstance(research_id, int) or isinstance(research_id, bool):
        raise ValueError("invalid_argument: research_id 必须是整数")  # noqa: TRY004 - 工具面错误码约定用 ValueError（同 toolsurface.validation）
    row = research_repo.research(conn, research_id)
    if row is None:
        raise ValueError("unknown_research")
    return row


def _replayed_event(conn, research_id: int, request_id: str, event: str) -> dict | None:
    """同一 request_id 已写过该事件：幂等重放，返回当时的事件行。"""
    for row in research_repo.research_events(conn, research_id):
        if row["event"] == event and request_id in (row["refs"] or []):
            return row
    return None


def start_research(env: ToolEnv, *, ticker: str, thesis: str, request_id: str, confirmed) -> dict:
    """用户发起研究（spec §6.2）：建观察中的研究并记 created；已有有效计划或观察中的研究时拒绝。"""
    ticker = normalize_ticker(ticker)
    request_id = text_arg(request_id, "request_id")
    if not isinstance(thesis, str) or not 1 <= len(thesis) <= 2000:
        raise ValueError("invalid_argument: thesis 需为 1–2000 字")
    if not confirmed_flag(confirmed):
        return {"write": False, "preview": f"将为 {ticker} 建立研究，论点：{thesis}。确认后写入。"}

    def _work(conn):
        for event in research_repo.research_events_on(conn, env.session):
            if event["event"] == "created" and request_id in (event["refs"] or []):
                row = research_repo.research(conn, event["research_id"])
                assert row is not None
                return {"research": row, "event": event, "replay": True}
        plan = plan_repo.active_plan(conn, ticker)
        if plan is not None:
            raise ToolError("active_plan_exists: 该股票已有有效计划", detail=plan)
        existing = research_repo.watching_research(conn, ticker)
        if existing is not None:
            raise ToolError("research_exists: 该股票已有观察中的研究", detail=existing)
        saved = research_repo.insert_research(
            conn,
            {
                "ticker": ticker,
                "thesis": thesis,
                "origin": env.origin,
                "created_session": env.session,
                "valid_until": add_trading_days(env.session, env.cfg.v3.research_sessions),
                "agent_session": env.agent_session,
            },
        )
        event = research_repo.insert_research_event(
            conn,
            {
                "research_id": saved["id"],
                "session": env.session,
                "event": "created",
                "reason": thesis,
                "before": {},
                "after": {"status": "watching"},
                "source": "user",
                "refs": [request_id],
            },
        )
        return {"research": saved, "event": event}

    return env.write(_work)


def conclude_research(
    env: ToolEnv,
    *,
    research_id,
    verdict: str,
    reasons: str,
    counter: str = "",
    refs: list | None = None,
    condition: dict | None = None,
    request_id: str | None = None,
    confirmed=None,
) -> dict:
    """研究结论三选一（spec §6.3）：ready / watch / reject；watch 带新条件是用户写入——
    先复述条件、confirmed 为真才写；简报模式不能改条件（condition_needs_user）。"""
    if verdict not in VERDICTS:
        raise ValueError("invalid_verdict")
    if not isinstance(reasons, str) or not 1 <= len(reasons) <= 2000:
        raise ValueError("invalid_argument: reasons 需为 1–2000 字")
    if not isinstance(counter, str):
        raise ValueError("invalid_argument: counter 必须是字符串")  # noqa: TRY004 - 工具面错误码约定用 ValueError（同 toolsurface.validation）
    checked_refs = list(refs or [])
    if any(not isinstance(ref, str) for ref in checked_refs):
        raise ValueError("invalid_argument: refs 类型不符")
    normalized = None
    if condition is not None:
        if not isinstance(condition, dict):
            raise ValueError("invalid_argument: condition 必须是对象")
        normalized = validate_condition(condition)
    condition_sentence = condition_text(normalized) if normalized is not None else None
    if request_id is not None:
        request_id = text_arg(request_id, "request_id")
    if verdict == "watch" and normalized is not None:
        if env.mode == "brief":
            raise ValueError("condition_needs_user: 简报模式不能改研究条件，要换条件去对话里做")
        if request_id is None:
            raise ValueError("invalid_argument: 带条件时需要 request_id")
        if not confirmed_flag(confirmed):
            peeked = env.read(lambda conn: _research_or_error(conn, research_id))
            return {
                "write": False,
                "preview": f"将把 {peeked['ticker']} 的研究条件定为「{condition_sentence}」。确认后写入。",
            }

    def _work(conn):
        row = _research_or_error(conn, research_id)
        if row["status"] != "watching":
            raise ValueError("not_watching")
        if verdict in ("ready", "reject"):
            known = facts_repo.tool_call_refs(conn, env.agent_session)
            if not checked_refs or any(ref not in known for ref in checked_refs):
                raise ValueError("invalid_refs")
        if verdict == "watch" and condition is None and not row["condition"]:
            raise ValueError("condition_required: watch 结论需要一个条件；研究尚无条件时必须带一个")
        event_refs = [*checked_refs, request_id] if request_id is not None else checked_refs
        if verdict == "watch" and normalized is not None and request_id is not None:
            replay = _replayed_event(conn, research_id, request_id, "concluded")
            if replay is not None:
                return {"research": research_repo.research(conn, research_id), "event": replay, "replay": True}
        if verdict == "reject":
            updated = research_repo.update_research(
                conn, research_id, status="rejected", ended_session=env.session, end_reason=reasons
            )
            event = research_repo.insert_research_event(
                conn,
                {
                    "research_id": research_id,
                    "session": env.session,
                    "event": "rejected",
                    "reason": f"{VERDICT_TEXT['reject']}：{reasons}",
                    "before": {"status": "watching"},
                    "after": {"status": "rejected"},
                    "source": "agent",
                    "refs": checked_refs,
                },
            )
            return {"research": updated, "event": event}
        fields: dict = {
            "verdict": verdict,
            "reasons": reasons,
            "counter": counter,
            "refs": checked_refs,
            "agent_session": env.agent_session,
        }
        after: dict = {"verdict": verdict}
        if verdict == "watch":
            valid_until = add_trading_days(env.session, env.cfg.v3.research_sessions)
            fields.update(valid_until=valid_until, triggered_session=None, checked_through=env.session)
            after.update(valid_until=valid_until, triggered_session=None, checked_through=env.session)
            if normalized is not None:
                fields.update(condition=normalized, condition_text=condition_sentence)
                after.update(condition=normalized, condition_text=condition_sentence)
        updated = research_repo.update_research(conn, research_id, **fields)
        event = research_repo.insert_research_event(
            conn,
            {
                "research_id": research_id,
                "session": env.session,
                "event": "concluded",
                "reason": f"{VERDICT_TEXT[verdict]}：{reasons}",
                "before": {"verdict": row["verdict"]},
                "after": after,
                "source": "agent",
                "refs": event_refs,
            },
        )
        return {"research": updated, "event": event}

    return env.write(_work)


def drop_research(env: ToolEnv, *, research_id, request_id: str, confirmed, reason: str = "") -> dict:
    """用户放弃研究（spec §6.1）；只对观察中的研究，幂等重放返回原事件。"""
    request_id = text_arg(request_id, "request_id")
    note = note_text(reason)
    if not confirmed_flag(confirmed):
        peeked = env.read(lambda conn: _research_or_error(conn, research_id))
        suffix = f"（{note}）" if note else ""
        return {"write": False, "preview": f"将放弃研究 {peeked['ticker']}{suffix}。确认后写入。"}

    def _work(conn):
        replay = _replayed_event(conn, research_id, request_id, "dropped")
        if replay is not None:
            return {"research": research_repo.research(conn, research_id), "event": replay, "replay": True}
        row = _research_or_error(conn, research_id)
        if row["status"] != "watching":
            raise ValueError("not_watching")
        text = note or "用户放弃"
        updated = research_repo.update_research(
            conn, research_id, status="dropped", ended_session=env.session, end_reason=text
        )
        event = research_repo.insert_research_event(
            conn,
            {
                "research_id": research_id,
                "session": env.session,
                "event": "dropped",
                "reason": text,
                "before": {"status": "watching"},
                "after": {"status": "dropped"},
                "source": "user",
                "refs": [request_id],
            },
        )
        return {"research": updated, "event": event}

    return env.write(_work)


def get_research(
    env: ToolEnv, *, status: str | None = None, ticker: str | None = None, research_id: int | None = None
) -> dict:
    """读研究：缺省返回全部观察中的研究；可按状态、股票或编号过滤，每份带变化记录。"""
    if ticker is not None:
        ticker = normalize_ticker(ticker)
    if status is not None and not isinstance(status, str):
        raise ValueError("invalid_argument: status 必须是字符串")
    if status is not None and status not in RESEARCH_STATUS_TEXT:
        allowed = "、".join(RESEARCH_STATUS_TEXT)
        raise ValueError(f"invalid_status: 需为 {allowed} 之一")
    if research_id is not None and (not isinstance(research_id, int) or isinstance(research_id, bool)):
        raise ValueError("invalid_argument: research_id 必须是整数")

    def _read(conn):
        if research_id is not None:
            found = research_repo.research(conn, research_id)
            rows = [] if found is None else [found]
        else:
            rows = research_repo.research_by_status(conn, (status or "watching",))
            if ticker is not None:
                rows = [row for row in rows if row["ticker"] == ticker]
        return {"research": [{**row, "events": research_repo.research_events(conn, row["id"])} for row in rows]}

    return env.read(_read)
