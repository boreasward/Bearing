"""v3 repository SQL against migration 032 + 034, in a disposable-schema Postgres (v3_db fixture)."""

from datetime import UTC, date, datetime

from investment_assistant.facts import plan_repo, repo

SESSION = date(2026, 11, 5)


def _run_row(run_id, session=SESSION, **override):
    row = {
        "run_id": run_id,
        "session": session,
        "trigger": "scheduled",
        "status": "ok",
        "error": None,
        "market": {"regime": "favorable"},
        "factors": {"AAPL": {"rs": 1.2}},
        "coverage": {"daily": {"ok": 4, "target": 5}},
        "holdings": [{"ticker": "AAPL", "shares": 10}],
        "draft_md": "# 规则名单草稿",
        "started_at": datetime(2026, 11, 5, 13, 0, tzinfo=UTC),
        "finished_at": datetime(2026, 11, 5, 13, 2, tzinfo=UTC),
    }
    return {**row, **override}


def _candidate(ticker, rank, *, screen_group="trigger"):
    return {
        "ticker": ticker,
        "rank": rank,
        "screen_group": screen_group,
        "trigger_rules": ["t1", "t2"],
        "rs": 1.5,
        "atr_pct": 2.5,
        "setup_tier": "A",
        "industry": "半导体",
        "industry_tier": "A",
        "reason": f"{ticker} 的理由行",
        "detail": {"score": 3},
    }


def _verdict_row(ticker, verdict="unknown", **override):
    row = {
        "session": SESSION,
        "ticker": ticker,
        "verdict": verdict,
        "reasons": "理由",
        "counter": "",
        "refs": ["r1"],
        "origin": "brief",
        "agent_session": "as-1",
    }
    return {**row, **override}


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
        "max_hold_sessions": 5,
        "valid_from": session,
        "valid_until": date(2026, 11, 12),
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


def test_runs_and_candidates(v3_db):
    conn = v3_db
    repo.insert_run(conn, _run_row("run-1"))
    repo.insert_run(conn, _run_row("run-2"))
    latest = repo.latest_run(conn, SESSION)
    assert latest is not None and latest["run_id"] == "run-2"  # 该交易日最新一条
    latest = repo.latest_run(conn)
    assert latest is not None and latest["run_id"] == "run-2"  # 全表最新
    assert repo.latest_run(conn, date(2026, 11, 4)) is None

    rows = [_candidate("AAPL", 1), _candidate("MU", 2, screen_group="setup"), _candidate("NVDA", 3)]
    assert repo.insert_candidates(conn, "run-2", rows) == 3
    got = repo.candidates(conn, "run-2")
    assert [row["rank"] for row in got] == [1, 2, 3]
    assert [row["ticker"] for row in got] == ["AAPL", "MU", "NVDA"]
    assert got[0]["trigger_rules"] == ["t1", "t2"]  # JSONB 读回为 list
    assert got[0]["detail"] == {"score": 3}
    assert all(row["session"] == SESSION for row in got)  # session 取自运行行


def test_verdict_upsert(v3_db):
    conn = v3_db
    repo.upsert_verdict(conn, _verdict_row("AAPL"))
    again = repo.upsert_verdict(conn, _verdict_row("AAPL", "confirm", origin="chat", counter="反方"))
    repo.upsert_verdict(conn, _verdict_row("MU", "veto"))
    assert conn.execute("SELECT count(*) FROM v3_verdicts").fetchone()[0] == 2
    assert again["verdict"] == "confirm" and again["origin"] == "chat" and again["counter"] == "反方"
    assert again["updated_at"] > again["created_at"]
    stored = repo.verdicts(conn, SESSION)
    assert set(stored) == {"AAPL", "MU"} and stored["AAPL"]["verdict"] == "confirm"


def test_plan_reads_stay_until_task3(v3_db):
    """034 后 v3_plans 写入改走 plan_repo；latest_plan / plans 保留到 Task 3 删除，读法不变。"""
    conn = v3_db
    first = plan_repo.insert_plan(conn, _plan_row("AAPL"))
    plan_repo.update_plan(conn, first["id"], status="open", stop=98.5)
    plan_repo.insert_plan(conn, _plan_row("AAPL", session=date(2026, 11, 6), status="expired"))
    latest = repo.latest_plan(conn, "AAPL")
    assert latest is not None and latest["session"] == date(2026, 11, 6)  # session 最新的一份
    assert len(repo.plans(conn, SESSION)) == 1  # 读法仍是 032 列集
    assert repo.plans(conn, SESSION)[0]["ticker"] == "AAPL"
    assert repo.plans(conn, SESSION)[0]["stop"] == 98.5  # update_plan 写的值读得到
    assert repo.latest_plan(conn, "MSFT") is None


def test_latest_ok_run_skips_errors_and_previous_ok_run(v3_db):
    conn = v3_db
    assert repo.latest_ok_run(conn) is None  # 空表返回 None
    repo.insert_run(conn, _run_row("run-1", session=date(2026, 11, 3)))
    repo.insert_run(conn, _run_row("run-2", session=date(2026, 11, 4), status="error", error="数据缺失"))
    repo.insert_run(conn, _run_row("run-3", session=date(2026, 11, 4)))
    repo.insert_run(conn, _run_row("run-4", session=SESSION))
    latest = repo.latest_ok_run(conn)
    assert latest is not None and latest["run_id"] == "run-4"  # 跳过 error 运行
    previous = repo.previous_ok_run(conn, SESSION)
    assert previous is not None and previous["run_id"] == "run-3"  # 只看更早的交易日
    earlier = repo.previous_ok_run(conn, date(2026, 11, 4))
    assert earlier is not None and earlier["run_id"] == "run-1"
    assert repo.previous_ok_run(conn, date(2026, 11, 3)) is None


def test_insert_run_dropped_round_trip(v3_db):
    conn = v3_db
    saved = repo.insert_run(conn, _run_row("run-d", dropped=[{"ticker": "AAA", "reasons": ["illiquid"]}]))
    assert saved["dropped"] == [{"ticker": "AAA", "reasons": ["illiquid"]}]
    plain = repo.insert_run(conn, _run_row("run-e"))
    assert plain["dropped"] == []  # 缺省空列表


def test_trade_and_equity_idempotent(v3_db):
    conn = v3_db
    row = {
        "request_id": "req-1",
        "ticker": "AAPL",
        "side": "buy",
        "quantity": 10,
        "price": 100.5,
        "fee": 0.5,
        "executed_at": datetime(2026, 11, 5, 14, 0, tzinfo=UTC),
        "plan_id": None,
        "note": "首次",
    }
    first = repo.insert_trade(conn, dict(row))
    replay = repo.insert_trade(conn, dict(row))  # request_id 重放返回原行
    assert first["id"] == replay["id"]
    assert conn.execute("SELECT count(*) FROM v3_trades").fetchone()[0] == 1
    repo.insert_trade(
        conn, dict(row, request_id="req-2", ticker="MU", executed_at=datetime(2026, 11, 5, 13, 0, tzinfo=UTC))
    )
    assert [t["request_id"] for t in repo.list_trades(conn)] == ["req-2", "req-1"]  # executed_at 升序
    assert [t["request_id"] for t in repo.list_trades(conn, "AAPL")] == ["req-1"]

    assert repo.latest_equity(conn) is None  # 空表返回 None
    repo.insert_equity(conn, {"request_id": "eq-1", "as_of": date(2026, 11, 4), "equity": 10000, "note": ""})
    second = repo.insert_equity(conn, {"request_id": "eq-2", "as_of": date(2026, 11, 5), "equity": 10100, "note": ""})
    replayed = repo.insert_equity(conn, {"request_id": "eq-2", "as_of": date(2026, 11, 5), "equity": 10100, "note": ""})
    assert replayed["id"] == second["id"]
    assert conn.execute("SELECT count(*) FROM v3_equity").fetchone()[0] == 2
    latest = repo.latest_equity(conn)
    assert latest is not None
    assert latest["as_of"] == date(2026, 11, 5) and latest["equity"] == 10100  # as_of 最新


def test_list_trades_and_trade_carry_voided_at(v3_db):
    conn = v3_db
    row = {
        "request_id": "req-v",
        "ticker": "AAPL",
        "side": "buy",
        "quantity": 10,
        "price": 100.5,
        "fee": 0.5,
        "executed_at": datetime(2026, 11, 5, 14, 0, tzinfo=UTC),
        "plan_id": None,
        "note": "",
    }
    saved = repo.insert_trade(conn, row)
    assert saved["voided_at"] is None
    before_void = repo.trade(conn, saved["id"])
    assert before_void is not None and before_void["voided_at"] is None  # 作废前为空
    assert repo.trade(conn, 999) is None
    plan_repo.insert_void(conn, {"request_id": "vd-t", "kind": "trade", "target_id": saved["id"], "reason": "录错股数"})
    trades = repo.list_trades(conn)
    assert trades[0]["request_id"] == "req-v"
    assert trades[0]["voided_at"] is not None  # 作废后非空（取自 v3_voids）
    after_void = repo.trade(conn, saved["id"])
    assert after_void is not None and after_void["voided_at"] is not None


def test_tool_calls(v3_db):
    conn = v3_db
    assert repo.next_seq(conn, "as-1") == 1  # 从 1 开始
    call = repo.insert_tool_call(
        conn,
        {
            "agent_session": "as-1",
            "mode": "brief",
            "session": SESSION,
            "seq": 1,
            "tool": "get_candidates",
            "args": {"top_n": 5},
            "result_sha256": "ab" * 32,
            "error": None,
        },
    )
    assert call["seq"] == 1 and call["args"] == {"top_n": 5}
    assert repo.next_seq(conn, "as-1") == 2  # 插入后变 2
    assert repo.tool_call_refs(conn, "as-1") == {"call:1"}
    assert repo.tool_call_refs(conn, "as-2") == set()
