"""research_repo SQL against migration 035, in a disposable-schema Postgres (v3_db fixture)."""

from datetime import date

import pytest

from investment_assistant.facts import research_repo

SESSION = date(2026, 11, 9)
NEXT_SESSION = date(2026, 11, 10)


def _research_row(ticker="AAPL", **override):
    row = {
        "ticker": ticker,
        "thesis": "论点",
        "origin": "chat",
        "created_session": SESSION,
        "valid_until": date(2026, 12, 4),
        "agent_session": "chat-ch-1-1",
    }
    return {**row, **override}


def _event_row(research_id, event="concluded", source="host", session=SESSION, **override):
    row = {
        "research_id": research_id,
        "session": session,
        "event": event,
        "reason": "结论 ready",
        "before": {"verdict": None},
        "after": {"verdict": "ready"},
        "source": source,
        "refs": ["call:1"],
    }
    return {**row, **override}


def test_insert_defaults_and_read_back(v3_db):
    conn = v3_db
    saved = research_repo.insert_research(conn, _research_row("AAPL"))
    assert saved["status"] == "watching"  # status 缺省 watching
    assert saved["refs"] == [] and saved["condition"] is None  # 其余列缺省
    assert saved["reasons"] == "" and saved["counter"] == ""
    assert research_repo.research(conn, saved["id"]) == saved
    assert research_repo.research(conn, 999) is None
    watching = research_repo.watching_research(conn, "AAPL")
    assert watching is not None and watching["id"] == saved["id"]
    assert research_repo.watching_research(conn, "MU") is None
    assert [row["ticker"] for row in research_repo.research_by_status(conn, ("watching",))] == ["AAPL"]


def test_update_whitelist_and_jsonb(v3_db):
    conn = v3_db
    saved = research_repo.insert_research(conn, _research_row("AAPL"))
    with pytest.raises(ValueError):  # 未知键拒绝
        research_repo.update_research(conn, saved["id"], ticker="MU")
    first = research_repo.update_research(
        conn,
        saved["id"],
        condition={"kind": "rs_pullback", "days": 5},
        condition_text="5 日内 RS 回踩不破 10 日线",
        verdict="ready",
        reasons="放量突破后缩量回踩",
    )
    assert first["condition"] == {"kind": "rs_pullback", "days": 5}  # dict 走 ::jsonb
    assert first["verdict"] == "ready" and first["condition_text"] == "5 日内 RS 回踩不破 10 日线"
    second = research_repo.update_research(conn, saved["id"], status="rejected")
    assert second["updated_at"] > first["updated_at"]  # 永远刷新 updated_at
    assert second["condition"] == {"kind": "rs_pullback", "days": 5}  # 未传的键不动


def test_event_host_once_agent_many(v3_db):
    conn = v3_db
    research_id = research_repo.insert_research(conn, _research_row("AAPL"))["id"]
    concluded = research_repo.insert_research_event(conn, _event_row(research_id))
    assert concluded is not None and concluded["event"] == "concluded" and concluded["refs"] == ["call:1"]
    assert research_repo.insert_research_event(conn, _event_row(research_id)) is None  # 宿主重复返回 None
    agent_first = research_repo.insert_research_event(conn, _event_row(research_id, "triggered", source="agent"))
    agent_again = research_repo.insert_research_event(conn, _event_row(research_id, "triggered", source="agent"))
    assert agent_first is not None and agent_again is not None  # 非宿主不受限
    later = research_repo.insert_research_event(conn, _event_row(research_id, "dropped", session=NEXT_SESSION))
    assert later is not None
    assert [row["event"] for row in research_repo.research_events(conn, research_id)] == [
        "concluded",
        "triggered",
        "triggered",
        "dropped",
    ]
    assert [row["event"] for row in research_repo.research_events_on(conn, NEXT_SESSION)] == ["dropped"]


def test_watching_tickers_sorted_unique(v3_db):
    conn = v3_db
    research_repo.insert_research(conn, _research_row("MU"))
    research_repo.insert_research(conn, _research_row("AAPL"))
    expired = research_repo.insert_research(conn, _research_row("NVDA"))
    research_repo.update_research(conn, expired["id"], status="rejected")
    research_repo.insert_research(conn, _research_row("NVDA"))  # 终态后同 ticker 再起一份 watching
    assert research_repo.watching_tickers(conn) == ["AAPL", "MU", "NVDA"]  # 排序去重
