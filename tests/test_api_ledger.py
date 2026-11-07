"""账本接口：POST /api/trades|cash-flows|reconcile|voids 只转发工具面（先复述后写入）与 GET /api/account。"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import investment_assistant.core.tx as core_tx
from investment_assistant.data import repo as data_repo
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo

SESSION = date(2026, 11, 4)
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)


def _bars(n=20, *, close=100.0):
    days = [SESSION - timedelta(days=i) for i in range(n)][::-1]
    return [
        {
            "price_date": d,
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "adj_close": close,
            "volume": 1000,
            "split_factor": 1.0,
        }
        for d in days
    ]


def seed(conn):
    facts_repo.insert_run(
        conn,
        {
            "run_id": "run-1",
            "session": SESSION,
            "trigger": "scheduled",
            "status": "ok",
            "error": None,
            "market": {},
            "factors": {},
            "coverage": {},
            "holdings": [],
            "dropped": [],
            "draft_md": "",
            "started_at": NOW - timedelta(minutes=5),
            "finished_at": NOW,
        },
    )
    data_repo.store_bars(conn, "AAPL", _bars(), now=NOW)


def test_trade_previews_then_records_with_stop_order(client, v3_tx):
    with core_tx._tx() as conn:
        seed(conn)
    payload = {
        "ticker": "AAPL",
        "side": "buy",
        "quantity": 10,
        "price": 100,
        "executed_at": "2026-11-04T15:00:00+00:00",
        "request_id": "w-1",
        "confirmed": False,
    }
    preview = client.post("/api/trades", json=payload)
    assert preview.status_code == 200
    body = preview.json()
    assert body["write"] is False and "AAPL" in body["preview"]  # A18：先复述
    payload["confirmed"] = True
    written = client.post("/api/trades", json=payload)
    assert written.status_code == 200
    row = written.json()
    assert row["trade"]["ticker"] == "AAPL" and row["trade"]["request_id"] == "w-1"
    assert row["plan"]["kind"] == "adopted"  # 没有有效计划的买入补录计划
    assert D(row["stop_order"]["quantity"]) == D(10)  # 买入后立刻返回止损单指令
    replay = client.post("/api/trades", json=payload)
    assert replay.json()["trade"]["request_id"] == "w-1"  # 同一 request_id 幂等


def test_trade_oversell_is_400_position_invalid(client, v3_tx):
    with core_tx._tx() as conn:
        seed(conn)
    resp = client.post(
        "/api/trades",
        json={
            "ticker": "AAPL",
            "side": "sell",
            "quantity": 999,
            "price": 100,
            "executed_at": "2026-11-04T16:00:00+00:00",
            "request_id": "w-2",
            "confirmed": True,
        },
    )
    assert resp.status_code == 400 and resp.json()["error"] == "position_invalid"
    unknown_key = client.post(
        "/api/trades",
        json={
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 1,
            "price": 100,
            "executed_at": "2026-11-04T16:00:00+00:00",
            "request_id": "w-3",
            "confirmed": True,
            "bogus": 1,
        },
    )
    assert unknown_key.status_code == 400 and unknown_key.json()["error"] == "unknown_argument"


def test_cash_flow_records_and_rejects_unknown_kind(client, v3_tx):
    with core_tx._tx() as conn:
        seed(conn)
    written = client.post(
        "/api/cash-flows",
        json={"kind": "opening", "amount": 10000, "request_id": "o-1", "confirmed": True},
    )
    assert written.status_code == 200
    flow = written.json()
    assert flow["kind"] == "opening" and isinstance(flow["amount"], str) and D(flow["amount"]) == D(10000)
    preview = client.post(
        "/api/cash-flows", json={"kind": "deposit", "amount": 500, "request_id": "o-2", "confirmed": False}
    )
    assert preview.json()["write"] is False and "preview" in preview.json()
    bad = client.post("/api/cash-flows", json={"kind": "bogus", "amount": 5, "request_id": "o-3", "confirmed": True})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_kind"


def test_reconcile_returns_difference(client, v3_tx):
    with core_tx._tx() as conn:
        seed(conn)
        plan_repo.insert_cash_flow(
            conn,
            {
                "request_id": "o-1",
                "kind": "opening",
                "amount": D(10000),
                "effective_at": datetime(2026, 11, 3, tzinfo=UTC),
            },
        )
    written = client.post("/api/reconcile", json={"equity": 9500, "request_id": "e-1", "confirmed": True})
    assert written.status_code == 200
    recon = written.json()["reconciliation"]
    assert D(recon["difference"]) == D(-500)  # 9500 − 10000
    bad = client.post("/api/reconcile", json={"equity": "很多", "request_id": "e-2", "confirmed": True})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_argument"


def test_void_marks_a_trade_and_rejects_double_void(client, v3_tx):
    with core_tx._tx() as conn:
        seed(conn)
        trade = facts_repo.insert_trade(
            conn,
            {
                "request_id": "t-1",
                "ticker": "AAPL",
                "side": "buy",
                "quantity": D(10),
                "price": D(100),
                "executed_at": datetime(2026, 11, 4, 15, tzinfo=UTC),
            },
        )
    written = client.post(
        "/api/voids", json={"kind": "trade", "target_id": trade["id"], "request_id": "v-1", "confirmed": True}
    )
    assert written.status_code == 200 and written.json()["void"]["target_id"] == trade["id"]
    again = client.post(
        "/api/voids", json={"kind": "trade", "target_id": trade["id"], "request_id": "v-2", "confirmed": True}
    )
    assert again.status_code == 400 and again.json()["error"] == "already_voided"
    account = client.get("/api/account").json()
    assert account["ledger"]["positions"] == []  # 作废后按剩余记录重算（A19 的后端一半）


def test_account_view_shape(client, v3_tx):
    with core_tx._tx() as conn:
        seed(conn)
    resp = client.get("/api/account")
    assert resp.status_code == 200
    account = resp.json()
    assert set(account) == {"ledger", "latest_equity", "reconciliation", "recent_outcomes", "sizing_snapshot"}
    assert account["ledger"]["cash"] is None and account["ledger"]["positions"] == []
    assert client.get("/api/account").status_code == 200


def test_ledger_endpoints_need_a_run_record(client, v3_tx):
    resp = client.post(
        "/api/trades",
        json={
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 1,
            "price": 100,
            "executed_at": "2026-11-04T15:00:00+00:00",
            "request_id": "w-9",
            "confirmed": True,
        },
    )
    assert resp.status_code == 404 and resp.json()["error"] == "no_run"
