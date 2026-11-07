"""GET /api/today：今日操作与账户条；没有运行记录时页面照常打开（接口契约表第一行）。"""

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
    plan_repo.insert_cash_flow(
        conn,
        {"request_id": "o-1", "kind": "opening", "amount": D(10000), "effective_at": datetime(2026, 11, 3, tzinfo=UTC)},
    )
    facts_repo.insert_trade(
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
    plan_repo.insert_plan(
        conn,
        {
            "session": SESSION,
            "ticker": "AAPL",
            "origin": "chat",
            "entry_type": "zone_limit",
            "entry_low": D(99),
            "entry_high": D(101),
            "stop": D(96),
            "target1": D(108),
            "initial_stop": D(96),
            "atr": D(2),
            "levels_source": "host",
            "max_hold_sessions": 20,
            "valid_from": SESSION,
            "valid_until": SESSION,
            "shares": 10,
            "sizing": {},
            "warnings": [],
            "thesis": "",
            "invalidation": "",
            "counter": "",
            "refs": [],
            "agent_session": "seed",
            "status": "open",
            "kind": "planned",
            "start_session": SESSION,
        },
    )


def test_today_returns_actions_and_account(client, v3_tx):
    with core_tx._tx() as conn:
        seed(conn)
    resp = client.get("/api/today")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session"] == "2026-11-04"
    assert isinstance(body["cutoff_at"], str)
    # 未确认止损单的持仓进「今日操作」（spec §7.4 第 4 条）
    assert [row["kind"] for row in body["actions"]] == ["stop_order"]
    action = body["actions"][0]
    assert set(action) == {"plan_id", "ticker", "kind", "text", "stop", "quantity", "reason"}
    assert action["ticker"] == "AAPL" and action["stop"] is not None
    account = body["account"]
    assert set(account) == {"cash", "equity", "unrealized_pnl", "realized_pnl", "cash_negative", "notes"}
    assert isinstance(account["cash"], str) and D(account["cash"]) == D(9000)  # 金额是字符串
    assert D(account["equity"]) == D(10000)  # 9000 + 10 × 100


def test_today_without_run_is_empty_but_account_present(client, v3_tx):
    resp = client.get("/api/today")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session"] is None and body["actions"] == []
    assert body["account"]["cash"] is None and body["account"]["notes"] == []
