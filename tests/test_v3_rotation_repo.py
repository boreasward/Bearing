"""rotation_repo SQL against migration 037, in a disposable-schema Postgres (v3_db fixture)."""

from datetime import date

from investment_assistant.facts import plan_repo, rotation_repo

SESSION = date(2026, 11, 9)
NEXT_SESSION = date(2026, 11, 10)
SNAPSHOT = {"cap": 0.6, "holding": {"ticker": "AAA", "weight": 0.2}}


def _plan(conn, ticker="AAA"):
    return plan_repo.insert_plan(
        conn,
        {
            "session": SESSION,
            "ticker": ticker,
            "origin": "brief",
            "entry_type": "pullback",
            "levels_source": "host",
            "max_hold_sessions": 20,
            "valid_from": SESSION,
            "valid_until": date(2026, 11, 16),
            "thesis": "论点",
            "invalidation": "失效条件",
            "agent_session": "as-1",
        },
    )


def _hold(**override):
    row = {
        "session": SESSION,
        "origin": "chat",
        "candidate": "BBB",
        "verdict": "hold",
        "holding_plan_id": None,
        "holding_ticker": None,
        "reasons": "继续持有",
        "weakness": "",
        "counter": "",
        "cost_response": "",
        "refs": [],
        "snapshot": SNAPSHOT,
        "sell_quantity": None,
        "buy_shares": None,
        "agent_session": "as-1",
        "request_id": None,
    }
    return {**row, **override}


def _switch(plan_id, verdict="half", **override):
    return _hold(
        verdict=verdict,
        holding_plan_id=plan_id,
        holding_ticker="AAA",
        reasons="B 更强",
        weakness="A 动能弱",
        counter="B 可能追高",
        cost_response="成本不是理由",
        refs=["r1"],
        sell_quantity=150,
        buy_shares=40,
        **override,
    )


def test_insert_hold_and_read_back(v3_db):
    conn = v3_db
    saved = rotation_repo.insert_rotation(conn, _hold(origin="brief"))
    assert saved["verdict"] == "hold" and saved["origin"] == "brief"
    assert saved["refs"] == []  # 不带 refs 存空列表
    assert saved["snapshot"] == SNAPSHOT  # 快照原样
    assert saved["holding_plan_id"] is None and saved["sell_quantity"] is None and saved["request_id"] is None
    assert rotation_repo.rotation(conn, saved["id"]) == saved
    assert rotation_repo.rotations_on(conn, SESSION) == [saved]
    assert rotation_repo.rotations_on(conn, NEXT_SESSION) == []
    assert rotation_repo.rotation(conn, saved["id"] + 1000) is None


def test_effective_since_only_half_and_full(v3_db):
    conn = v3_db
    plan_id = _plan(conn)["id"]
    held = rotation_repo.insert_rotation(conn, _hold())
    early = rotation_repo.insert_rotation(conn, _switch(plan_id, session=date(2026, 11, 6)))
    first = rotation_repo.insert_rotation(conn, _switch(plan_id, "half"))
    second = rotation_repo.insert_rotation(conn, _switch(plan_id, "full"))
    later = rotation_repo.insert_rotation(conn, _switch(plan_id, "half", session=NEXT_SESSION))
    effective = rotation_repo.effective_since(conn, SESSION)
    assert [row["id"] for row in effective] == [first["id"], second["id"], later["id"]]  # session, id 升序
    assert held["id"] not in {row["id"] for row in effective}  # hold 不在其中
    assert early["id"] not in {row["id"] for row in effective}  # 早于 since 的不在其中
    assert effective[0]["session"] == SESSION  # since 当天包含在内
    assert rotation_repo.effective_since(conn, date(2026, 11, 11)) == []
    assert [row["id"] for row in rotation_repo.rotations_on(conn, SESSION)] == [held["id"], first["id"], second["id"]]
    listed = rotation_repo.rotations(conn)
    assert [row["id"] for row in listed] == [later["id"], second["id"], first["id"], held["id"], early["id"]]
    assert [row["id"] for row in rotation_repo.rotations(conn, limit=2)] == [later["id"], second["id"]]


def test_rotation_by_request(v3_db):
    conn = v3_db
    saved = rotation_repo.insert_rotation(conn, _hold(request_id="req-1"))
    plain = rotation_repo.insert_rotation(conn, _hold())
    assert rotation_repo.rotation_by_request(conn, "req-1") == saved
    assert rotation_repo.rotation_by_request(conn, "req-2") is None
    assert plain["request_id"] is None  # None 不命中任何请求
    assert rotation_repo.rotation_by_request(conn, None) is None
