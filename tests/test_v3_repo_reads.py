"""Workbench repository reads against disposable PostgreSQL schemas."""

from datetime import UTC, date, datetime

import pytest

from investment_assistant.facts import plan_repo, repo

SESSION = date(2026, 11, 9)
NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "result,truncated",
    [
        (None, False),
        ({"a": 1}, False),
        ([{"a": 1}], False),
        ({"truncated": True, "bytes": 600000}, True),
    ],
)
def test_insert_tool_call_stores_result_and_truncated(v3_tx, result, truncated):
    row = {
        "agent_session": "call-session",
        "mode": "chat",
        "session": SESSION,
        "seq": 1,
        "tool": "get_market",
        "args": {},
        "result": result,
        "truncated": truncated,
    }
    saved = repo.insert_tool_call(v3_tx, row)
    assert saved["result"] == result
    assert saved["result_available"] is True
    assert saved["truncated"] is truncated
    assert repo.tool_call(v3_tx, "call-session", 1) == saved
    row.pop("result")
    row.pop("truncated")
    row["seq"] = 2
    repo.insert_tool_call(v3_tx, row)
    old = repo.tool_call(v3_tx, "call-session", 2)
    assert old is not None
    assert old["result"] is None and old["truncated"] is False
    assert old["result_available"] is False
    assert repo.tool_call(v3_tx, "x", 9) is None


def _run(conn, run_id, session, started_at=NOW):
    return repo.insert_run(
        conn,
        {
            "run_id": run_id,
            "session": session,
            "trigger": "scheduled",
            "status": "ok",
            "draft_md": "draft",
            "started_at": started_at,
            "finished_at": started_at,
        },
    )


def test_run_by_id_returns_that_run_or_none(v3_tx):
    first = _run(v3_tx, "first-run", date(2026, 11, 7))
    _run(v3_tx, "second-run", SESSION, NOW.replace(hour=13))
    found = repo.run_by_id(v3_tx, first["run_id"])
    assert found is not None and found["session"] == first["session"]
    assert repo.run_by_id(v3_tx, "missing") is None


def test_runs_with_briefs_left_joins_by_session(v3_tx):
    older = _run(v3_tx, "older", date(2026, 11, 6))
    newer = _run(v3_tx, "newer", SESSION)
    plan_repo.upsert_brief(
        v3_tx,
        {
            "session": SESSION,
            "run_id": "newer",
            "status": "final",
            "runtime": "fake",
            "usage": {"input": 4},
            "elapsed_seconds": 4,
        },
    )
    plan_repo.update_brief(v3_tx, SESSION, weekly_md="weekly", pushed_weekly_at=NOW, pushed_final_at=NOW)
    rows = repo.runs_with_briefs(v3_tx, limit=20)
    assert [{key: row[key] for key in newer} for row in rows] == [newer, older]
    assert rows[1]["brief"] is None
    assert rows[0]["brief"] == {
        "status": "final",
        "error": None,
        "runtime": "fake",
        "provider": None,
        "model": None,
        "usage": {"input": 4},
        "elapsed_seconds": 4,
        "pushed_final_at": NOW,
        "pushed_error_at": None,
        "weekly_present": True,
        "pushed_weekly_at": NOW,
    }
    # A brief belongs to the trading session, including its earlier reruns.
    rerun = _run(v3_tx, "earlier-rerun", SESSION, NOW.replace(hour=11))
    rows = repo.runs_with_briefs(v3_tx, limit=2)
    assert [row["run_id"] for row in rows] == [newer["run_id"], rerun["run_id"]]
    assert rows[0]["brief"] == rows[1]["brief"]
    assert repo.runs_with_briefs(v3_tx, limit=0) == []


def test_recent_ledger_rows_carry_void_info(v3_tx):
    trades, flows, equities = [], [], []
    for i in range(3):
        trades.append(
            repo.insert_trade(
                v3_tx,
                {
                    "request_id": f"trade-{i}",
                    "ticker": "AAA",
                    "side": "buy",
                    "quantity": 1,
                    "price": 10,
                    "executed_at": NOW,
                },
            )
        )
        flows.append(
            plan_repo.insert_cash_flow(
                v3_tx,
                {
                    "request_id": f"flow-{i}",
                    "kind": "deposit",
                    "amount": 100,
                    "effective_at": NOW,
                },
            )
        )
        equities.append(repo.insert_equity(v3_tx, {"request_id": f"equity-{i}", "as_of": NOW, "equity": 100}))
    trade_void = plan_repo.insert_void(
        v3_tx,
        {
            "request_id": "void-trade",
            "kind": "trade",
            "target_id": trades[-1]["id"],
            "reason": "wrong trade",
        },
    )
    flow_void = plan_repo.insert_void(
        v3_tx,
        {
            "request_id": "void-flow",
            "kind": "cash_flow",
            "target_id": flows[-1]["id"],
            "reason": "wrong flow",
        },
    )
    for reader, inserted, void in (
        (repo.recent_trades, trades, trade_void),
        (plan_repo.recent_cash_flows, flows, flow_void),
    ):
        rows = reader(v3_tx, limit=2)
        assert [row["id"] for row in rows] == [inserted[2]["id"], inserted[1]["id"]]
        assert rows[0]["voided_by"] == void["id"] and rows[0]["void_reason"] == void["reason"]
        assert rows[1]["voided_by"] is None and rows[1]["void_reason"] is None
        assert reader(v3_tx, limit=0) == []
    assert [row["id"] for row in repo.recent_equity(v3_tx, limit=2)] == [equities[2]["id"], equities[1]["id"]]
    assert plan_repo.recent_voids(v3_tx, limit=1) == [flow_void]
    assert plan_repo.recent_voids(v3_tx, limit=2) == [flow_void, trade_void]
    assert repo.recent_equity(v3_tx, limit=0) == []
    assert plan_repo.recent_voids(v3_tx, limit=0) == []


def test_chats_order_by_last_message_and_touch(v3_tx):
    first = plan_repo.insert_chat(v3_tx, {"chat_id": "first", "runtime": "fake", "provider": "test", "model": "test"})
    second = plan_repo.insert_chat(v3_tx, {"chat_id": "second", "runtime": "fake", "provider": "test", "model": "test"})
    # Distinct past creation times keep this independent of the wall clock and transaction defaults.
    v3_tx.execute("UPDATE v3_chats SET created_at = %s WHERE chat_id = 'first'", (NOW.replace(year=2000, hour=10),))
    v3_tx.execute("UPDATE v3_chats SET created_at = %s WHERE chat_id = 'second'", (NOW.replace(year=2000, hour=11),))
    assert [row["chat_id"] for row in plan_repo.chats(v3_tx)] == ["second", "first"]
    message = plan_repo.insert_message(
        v3_tx,
        {
            "chat_id": "first",
            "seq": 1,
            "role": "user",
            "content": "hello",
            "status": "ok",
        },
    )
    rows = plan_repo.chats(v3_tx)
    assert [row["chat_id"] for row in rows] == [first["chat_id"], second["chat_id"]]
    assert rows[0]["last_message_at"] == message["created_at"]
    assert rows[1]["last_message_at"] == rows[1]["created_at"]
    assert len(plan_repo.chats(v3_tx, limit=1)) == 1
    plan_repo.touch_chat(v3_tx, "first")
    touched = plan_repo.chat(v3_tx, "first")
    assert touched is not None
    assert touched["updated_at"] > first["updated_at"]
    assert plan_repo.chats(v3_tx)[0]["last_message_at"] == message["created_at"]
