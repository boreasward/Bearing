"""plan_repo SQL against migration 034, in a disposable-schema Postgres (v3_db fixture)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from investment_assistant.facts import plan_repo, repo, research_repo, rotation_repo

SESSION = date(2026, 11, 9)
NEXT_SESSION = date(2026, 11, 10)
PUSHED_AT = datetime(2026, 11, 9, 21, 0, tzinfo=UTC)


def _run_row(run_id, session=SESSION):
    return {
        "run_id": run_id,
        "session": session,
        "trigger": "scheduled",
        "status": "ok",
        "error": None,
        "draft_md": "",
        "started_at": datetime(2026, 11, 9, 13, 0, tzinfo=UTC),
        "finished_at": datetime(2026, 11, 9, 13, 2, tzinfo=UTC),
    }


def _plan_row(ticker, session=SESSION, **override):
    row = {
        "session": session,
        "ticker": ticker,
        "origin": "brief",
        "entry_type": "pullback",
        "entry_low": 100.0,
        "entry_high": 101.0,
        "stop": 99.0,
        "target1": 110.0,
        "levels_source": "host",
        "max_hold_sessions": 20,
        "valid_from": session,
        "valid_until": date(2026, 11, 16),
        "shares": 100,
        "sizing": {"risk_pct": 0.01},
        "warnings": [],
        "thesis": "论点",
        "invalidation": "失效条件",
        "counter": "",
        "refs": ["r1"],
        "agent_session": "as-1",
    }
    return {**row, **override}


def _event_row(plan_id, event="created", source="host", session=SESSION, **override):
    row = {
        "plan_id": plan_id,
        "session": session,
        "event": event,
        "reason": "新建：放量突破",
        "before": {},
        "after": {},
        "source": source,
        "refs": ["call:1"],
    }
    return {**row, **override}


def test_insert_plan_defaults_and_reads(v3_db):
    conn = v3_db
    saved = plan_repo.insert_plan(conn, _plan_row("AAPL"))
    assert saved["status"] == "pending" and saved["kind"] == "planned"  # 缺省值
    assert saved["trailing"] is False and saved["outcome"] == {}
    assert saved["entry_low"] == 100.0 and saved["sizing"] == {"risk_pct": 0.01}
    assert plan_repo.plan(conn, saved["id"]) == saved
    assert plan_repo.plan(conn, 999) is None
    active = plan_repo.active_plan(conn, "AAPL")
    assert active is not None and active["id"] == saved["id"]
    assert plan_repo.active_plan(conn, "MSFT") is None  # 无有效计划

    plan_repo.update_plan(conn, saved["id"], status="expired")  # 终态后不再是有效计划
    assert plan_repo.active_plan(conn, "AAPL") is None
    plan_repo.insert_plan(conn, _plan_row("MU"))
    assert [row["ticker"] for row in plan_repo.plans_by_status(conn, ("pending", "open"))] == ["MU"]
    assert [row["ticker"] for row in plan_repo.plans_by_status(conn, ("pending",), limit=5)] == ["MU"]
    assert [row["ticker"] for row in plan_repo.plans_by_status(conn, ("expired",))] == ["AAPL"]
    assert plan_repo.plans_by_status(conn, ("pending",), limit=0) == []  # limit 生效


def test_update_plan_whitelist_and_updated_at(v3_db):
    conn = v3_db
    saved = plan_repo.insert_plan(conn, _plan_row("AAPL"))
    with pytest.raises(ValueError):  # 未知键拒绝
        plan_repo.update_plan(conn, saved["id"], session=NEXT_SESSION)
    first = plan_repo.update_plan(
        conn, saved["id"], status="open", stop=98.5, trailing=True, outcome={"realized_pnl": "800"}
    )
    assert first["status"] == "open" and first["stop"] == 98.5
    assert first["trailing"] is True and first["outcome"] == {"realized_pnl": "800"}
    second = plan_repo.update_plan(conn, saved["id"], stop=97.5)
    assert second["updated_at"] > first["updated_at"]  # 永远刷新 updated_at
    assert second["outcome"] == {"realized_pnl": "800"}  # 未传的键不动


def test_events_host_duplicate_returns_none(v3_db):
    conn = v3_db
    plan_id = plan_repo.insert_plan(conn, _plan_row("AAPL"))["id"]
    created = plan_repo.insert_event(conn, _event_row(plan_id))
    assert created is not None and created["event"] == "created" and created["refs"] == ["call:1"]
    assert plan_repo.insert_event(conn, _event_row(plan_id)) is None  # 宿主重复返回 None，不抛
    agent_first = plan_repo.insert_event(conn, _event_row(plan_id, "revised", source="agent"))
    agent_again = plan_repo.insert_event(conn, _event_row(plan_id, "revised", source="agent"))
    assert agent_first is not None and agent_again is not None  # 非宿主不受限
    later = plan_repo.insert_event(conn, _event_row(plan_id, "stop_hit", session=NEXT_SESSION))
    assert later is not None
    assert [row["event"] for row in plan_repo.events(conn, plan_id)] == ["created", "revised", "revised", "stop_hit"]
    assert [row["event"] for row in plan_repo.events_on(conn, NEXT_SESSION)] == ["stop_hit"]


def test_holding_reviews_upsert_and_by_session(v3_db):
    conn = v3_db
    plan_id = plan_repo.insert_plan(conn, _plan_row("AAPL"))["id"]
    review = plan_repo.upsert_holding_review(
        conn,
        {
            "plan_id": plan_id,
            "session": SESSION,
            "verdict": "holds",
            "reasons": "理由仍成立",
            "counter": "无反证",
            "refs": ["call:2"],
            "agent_session": "as-1",
        },
    )
    assert review["verdict"] == "holds"
    again = plan_repo.upsert_holding_review(
        conn,
        {
            "plan_id": plan_id,
            "session": SESSION,
            "verdict": "unknown",
            "reasons": "资讯不足",
            "counter": "",
            "refs": [],
            "agent_session": "as-2",
        },
    )
    assert again["verdict"] == "unknown" and again["agent_session"] == "as-2"
    assert conn.execute("SELECT count(*) FROM v3_holding_reviews").fetchone()[0] == 1
    stored = plan_repo.holding_reviews(conn, SESSION)
    assert list(stored) == [plan_id] and stored[plan_id]["verdict"] == "unknown"
    assert plan_repo.holding_reviews(conn, NEXT_SESSION) == {}


def test_cash_flows_idempotent_and_voided_at(v3_db):
    conn = v3_db
    row = {
        "request_id": "cf-1",
        "kind": "deposit",
        "amount": 2000,
        "effective_at": datetime(2026, 11, 5, 13, 0, tzinfo=UTC),
        "note": "入金",
    }
    first = plan_repo.insert_cash_flow(conn, dict(row))
    replay = plan_repo.insert_cash_flow(conn, dict(row))  # 同 request_id 返回原行
    assert replay["id"] == first["id"]
    second = plan_repo.insert_cash_flow(
        conn,
        {
            **row,
            "request_id": "cf-2",
            "kind": "opening",
            "amount": 10000,
            "effective_at": datetime(2026, 11, 1, 13, 0, tzinfo=UTC),
        },
    )
    assert [r["request_id"] for r in plan_repo.cash_flows(conn)] == ["cf-2", "cf-1"]  # effective_at 升序
    assert plan_repo.cash_flows(conn)[0]["voided_at"] is None
    before_void = plan_repo.cash_flow(conn, second["id"])
    assert before_void is not None and before_void["voided_at"] is None
    assert plan_repo.cash_flow(conn, 999) is None

    voided = plan_repo.insert_void(
        conn, {"request_id": "vd-1", "kind": "cash_flow", "target_id": second["id"], "reason": "录错金额"}
    )
    replayed = plan_repo.insert_void(
        conn, {"request_id": "vd-1", "kind": "cash_flow", "target_id": second["id"], "reason": "录错金额"}
    )
    assert replayed["id"] == voided["id"]  # 同 request_id 返回原行
    assert [v["request_id"] for v in plan_repo.voids(conn)] == ["vd-1"]
    flows = {row_["request_id"]: row_ for row_ in plan_repo.cash_flows(conn)}
    assert flows["cf-2"]["voided_at"] is not None  # 作废后非空
    assert flows["cf-1"]["voided_at"] is None
    after_void = plan_repo.cash_flow(conn, second["id"])
    assert after_void is not None and after_void["voided_at"] is not None


def test_briefs_upsert_preserves_pushed_and_update_changes_given_keys_only(v3_db):
    conn = v3_db
    repo.insert_run(conn, _run_row("run-1"))  # v3_briefs.run_id 有外键
    draft = plan_repo.upsert_brief(
        conn,
        {
            "session": SESSION,
            "run_id": None,
            "status": "draft",
            "draft_md": "# 草稿一",
            "runtime": "pi",
            "provider": "p1",
            "model": "m1",
        },
    )
    assert draft["agenda"] == {} and draft["usage"] == {}
    pushed = plan_repo.update_brief(conn, SESSION, pushed_final_at=PUSHED_AT)
    assert pushed["pushed_final_at"] == PUSHED_AT
    again = plan_repo.upsert_brief(
        conn,
        {
            "session": SESSION,
            "run_id": "run-1",
            "status": "draft",
            "draft_md": "# 草稿二",
            "runtime": "pi",
            "provider": "p2",
            "model": "m2",
            "agenda": {"holdings": 1},
            "usage": {"total": 100},
            "elapsed_seconds": 42,
        },
    )
    assert again["id"] == draft["id"]  # 同 session 覆盖
    assert again["draft_md"] == "# 草稿二" and again["run_id"] == "run-1"
    assert again["agenda"] == {"holdings": 1} and again["elapsed_seconds"] == 42
    assert again["pushed_final_at"] == PUSHED_AT  # 推送时间保留
    final = plan_repo.update_brief(conn, SESSION, status="final", final_md="# 最终")
    assert final["status"] == "final" and final["final_md"] == "# 最终"
    assert final["draft_md"] == "# 草稿二"  # 只改传入的键
    with pytest.raises(ValueError):
        plan_repo.update_brief(conn, SESSION, pushed=1)  # 未知键拒绝
    final_brief = plan_repo.brief(conn, SESSION)
    assert final_brief is not None and final_brief["final_md"] == "# 最终"
    assert plan_repo.brief(conn, NEXT_SESSION) is None
    plan_repo.upsert_brief(conn, {"session": NEXT_SESSION, "status": "error", "error": "数据库不可达"})
    assert [row["session"] for row in plan_repo.briefs(conn)] == [NEXT_SESSION, SESSION]
    assert [row["session"] for row in plan_repo.briefs(conn, limit=1)] == [NEXT_SESSION]


def test_chats_messages_and_seq(v3_db):
    conn = v3_db
    chat = plan_repo.insert_chat(conn, {"chat_id": "ch-1", "runtime": "pi", "provider": "p1", "model": "m1"})
    assert chat["title"] == "" and chat["read_only"] is False
    assert plan_repo.chat(conn, "ch-1") == chat
    assert plan_repo.chat(conn, "nope") is None
    plan_repo.insert_chat(
        conn, {"chat_id": "ch-2", "runtime": "pi", "provider": "p1", "model": "m1", "title": "持仓问题"}
    )
    assert [row["chat_id"] for row in plan_repo.chats(conn)] == ["ch-2", "ch-1"]
    assert [row["chat_id"] for row in plan_repo.chats(conn, limit=1)] == ["ch-2"]
    updated = plan_repo.update_chat(conn, "ch-1", read_only=True)
    assert updated["read_only"] is True and updated["title"] == ""  # 只改传入的键
    with pytest.raises(ValueError):
        plan_repo.update_chat(conn, "ch-1", model="m9")  # 未知键拒绝

    assert plan_repo.next_message_seq(conn, "ch-1") == 1  # 从 1 起
    asked = plan_repo.insert_message(
        conn,
        {
            "chat_id": "ch-1",
            "seq": 1,
            "role": "user",
            "content": "MU 还该持有吗",
            "session": SESSION,
            "agent_session": "chat-ch-1-1",
            "status": "ok",
            "usage": {},
            "tool_refs": [],
        },
    )
    assert asked["seq"] == 1 and asked["status"] == "ok"
    answered = plan_repo.insert_message(
        conn,
        {
            "chat_id": "ch-1",
            "seq": 2,
            "role": "assistant",
            "content": "按止损持有",
            "session": SESSION,
            "agent_session": "chat-ch-1-1",
            "status": "ok",
            "usage": {"total": 10},
            "tool_refs": ["call:1"],
        },
    )
    assert plan_repo.next_message_seq(conn, "ch-1") == 3
    assert [row["seq"] for row in plan_repo.messages(conn, "ch-1")] == [1, 2]
    assert answered["tool_refs"] == ["call:1"]
    assert plan_repo.messages(conn, "ch-2") == []


def test_plan_row_carries_research_id(v3_db):
    conn = v3_db
    research = research_repo.insert_research(
        conn,
        {
            "ticker": "AAPL",
            "thesis": "论点",
            "origin": "chat",
            "created_session": SESSION,
            "valid_until": date(2026, 11, 20),
            "agent_session": "chat-ch-1-1",
        },
    )
    saved = plan_repo.insert_plan(conn, _plan_row("AAPL", research_id=research["id"]))
    assert saved["research_id"] == research["id"]  # 计划行携带研究关联
    assert plan_repo.plan(conn, saved["id"]) == saved
    plain = plan_repo.insert_plan(conn, _plan_row("MU"))
    assert plain["research_id"] is None  # 不带研究的计划也可以


def _rotation_row(plan_id):
    return {
        "session": SESSION,
        "origin": "chat",
        "candidate": "MU",
        "verdict": "half",
        "holding_plan_id": plan_id,
        "holding_ticker": "AAPL",
        "reasons": "MU 更强",
        "weakness": "AAPL 动能弱",
        "counter": "MU 可能追高",
        "cost_response": "成本不是理由",
        "refs": [],
        "snapshot": {"cap": 0.6},
        "sell_quantity": Decimal("150"),
        "buy_shares": Decimal("40"),
        "agent_session": "as-1",
        "request_id": None,
    }


def test_plan_row_carries_rotation_columns(v3_db):
    conn = v3_db
    saved = plan_repo.insert_plan(conn, _plan_row("AAPL"))
    assert saved["sell_quantity"] is None and saved["rotation_id"] is None
    assert saved["funded_by_rotation_id"] is None
    assert saved["stop_quantity_stale"] is False  # 缺省 False
    rid = rotation_repo.insert_rotation(conn, _rotation_row(saved["id"]))["id"]
    updated = plan_repo.update_plan(
        conn, saved["id"], sell_instruction="rotation", sell_quantity=Decimal("150"), rotation_id=rid
    )
    assert updated["sell_instruction"] == "rotation"
    assert updated["sell_quantity"] == Decimal("150")
    assert updated["rotation_id"] == rid
    assert plan_repo.plan(conn, saved["id"]) == updated  # 读回一致
    stale = plan_repo.update_plan(conn, saved["id"], stop_quantity_stale=True)
    assert stale["stop_quantity_stale"] is True


def test_plan_funded_by(v3_db):
    conn = v3_db
    holding = plan_repo.insert_plan(conn, _plan_row("AAPL"))
    rid = rotation_repo.insert_rotation(conn, _rotation_row(holding["id"]))["id"]
    assert plan_repo.plan_funded_by(conn, rid) is None  # 还没有计划由这次换仓出资
    funded = plan_repo.insert_plan(conn, _plan_row("MU", funded_by_rotation_id=rid))
    assert funded["funded_by_rotation_id"] == rid
    assert plan_repo.plan_funded_by(conn, rid) == funded
    assert plan_repo.plan_funded_by(conn, rid + 1000) is None


def test_brief_weekly_columns_update_and_survive_upsert(v3_db):
    conn = v3_db
    repo.insert_run(conn, _run_row("run-w"))  # v3_briefs.run_id 有外键
    plan_repo.upsert_brief(conn, {"session": SESSION, "run_id": None, "status": "draft", "draft_md": "# 草稿"})
    weekly = plan_repo.update_brief(conn, SESSION, weekly_md="# 周报", pushed_weekly_at=PUSHED_AT)
    assert weekly["weekly_md"] == "# 周报" and weekly["pushed_weekly_at"] == PUSHED_AT
    again = plan_repo.upsert_brief(
        conn,
        {"session": SESSION, "run_id": "run-w", "status": "draft", "draft_md": "# 草稿二"},
    )
    assert again["weekly_md"] == "# 周报"  # upsert_brief 不写这两列，重跑不清空
    assert again["pushed_weekly_at"] == PUSHED_AT


def test_settings_empty_and_round_trip(v3_db):
    conn = v3_db
    assert plan_repo.settings(conn) == {}  # 空库返回 {}
    plan_repo.set_setting(conn, "runtime", "pi")
    plan_repo.set_setting(conn, "provider", "p1")
    assert plan_repo.settings(conn) == {"runtime": "pi", "provider": "p1"}
    plan_repo.set_setting(conn, "runtime", "fake")  # 覆盖同一键
    assert plan_repo.settings(conn) == {"runtime": "fake", "provider": "p1"}
