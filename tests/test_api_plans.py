"""/api/plans*：三组卡片（含 close_above_entry）、单份计划与变化记录、stop-order / raise-stop / cancel。
写入口只转发给工具面，校验错误 400，资源缺失 404（接口契约表）。"""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import investment_assistant.core.tx as core_tx
from investment_assistant.data import repo as data_repo
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo

SESSION = date(2026, 11, 4)
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)

CARD_KEYS = {
    "plan_id",
    "ticker",
    "status",
    "kind",
    "levels",
    "initial_stop",
    "trailing",
    "shares",
    "valid_from",
    "valid_until",
    "start_session",
    "max_hold_sessions",
    "next_action",
    "position",
    "held_sessions",
    "stop_order_status",
    "confirmed_stop_order",
    "outcome",
    "thesis",
    "created_at",
    "ended_session",
    "end_reason",
    "close_above_entry",
}


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


def _run(conn):
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


def _plan_row(ticker, **over):
    row = {
        "session": SESSION,
        "ticker": ticker,
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
        "thesis": "放量突破",
        "invalidation": "",
        "counter": "",
        "refs": [],
        "agent_session": "seed",
        "status": "pending",
        "kind": "planned",
    }
    row.update(over)
    return row


def seed(conn):
    _run(conn)
    data_repo.store_bars(conn, "AAPL", _bars(), now=NOW)
    data_repo.store_bars(conn, "MSFT", _bars(), now=NOW)
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
    open_plan = plan_repo.insert_plan(conn, _plan_row("AAPL", status="open", start_session=SESSION))
    plan_repo.insert_event(
        conn,
        {
            "plan_id": open_plan["id"],
            "session": SESSION,
            "event": "bought",
            "reason": "买入 10 股 @ 100",
            "before": {"status": "pending"},
            "after": {"status": "open"},
            "source": "user",
            "refs": ["t-1"],
        },
    )
    plan_repo.insert_plan(conn, _plan_row("MSFT", entry_low=D(95), entry_high=D(98)))  # 收盘 100 > 98
    plan_repo.insert_plan(conn, _plan_row("ZZZ", entry_low=D(90), entry_high=D(95)))  # 没有日线
    plan_repo.insert_plan(
        conn,
        _plan_row(
            "NVDA",
            status="closed",
            ended_session=SESSION,
            end_reason="用户自行卖出",
            outcome={"realized_pnl": D(50), "r_multiple": D("1.2"), "exit_reason": "manual"},
        ),
    )
    return open_plan


ISO_STAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^+Z]*(?:Z|[+-]\d{2}:\d{2})"  # ISO 8601 T 形式且带时区


def test_holdings_group_card_fields(client, v3_tx):
    with core_tx._tx() as conn:
        seed(conn)
    resp = client.get("/api/plans?group=holdings")
    assert resp.status_code == 200
    plans = resp.json()["plans"]
    assert [card["ticker"] for card in plans] == ["AAPL"]
    card = plans[0]
    assert set(card) == CARD_KEYS
    assert set(card["levels"]) == {"entry_low", "entry_high", "stop", "target1"}
    assert isinstance(card["levels"]["entry_low"], str) and D(card["levels"]["stop"]) == D(96)
    assert set(card["position"]) == {"quantity", "avg_cost", "last_close", "unrealized_pnl", "unrealized_pct"}
    assert D(card["position"]["quantity"]) == D(10) and D(card["position"]["last_close"]) == D(100)
    assert card["next_action"]["kind"] == "stop_order"  # 未确认止损单
    assert card["stop_order_status"] == "none" and card["held_sessions"] == 0
    assert card["close_above_entry"] is False  # 非 pending 一律 False
    assert re.fullmatch(ISO_STAMP, card["created_at"])  # 时间是 ISO 8601 的 T 形式且带时区


def test_pending_group_marks_close_above_entry(client, v3_tx):
    with core_tx._tx() as conn:
        seed(conn)
    plans = client.get("/api/plans?group=pending").json()["plans"]
    by_ticker = {card["ticker"]: card for card in plans}
    assert set(by_ticker) == {"MSFT", "ZZZ"}
    assert by_ticker["MSFT"]["close_above_entry"] is True  # 最新收盘 100 > 入场区间上沿 98（R7 / spec §6.3 第 4 条）
    assert by_ticker["MSFT"]["position"] is None and by_ticker["MSFT"]["next_action"] is None
    assert by_ticker["ZZZ"]["close_above_entry"] is False  # 缺日线：false/omitted-safe


def test_ended_group_carries_outcome(client, v3_tx):
    with core_tx._tx() as conn:
        seed(conn)
    plans = client.get("/api/plans?group=ended").json()["plans"]
    assert [card["ticker"] for card in plans] == ["NVDA"]
    assert plans[0]["outcome"]["exit_reason"] == "manual" and plans[0]["end_reason"] == "用户自行卖出"
    assert plans[0]["position"] is None


def test_invalid_group_and_limit_are_400(client, v3_tx):
    with core_tx._tx() as conn:
        _run(conn)
    assert client.get("/api/plans?group=bogus").status_code == 400
    assert client.get("/api/plans?limit=0").status_code == 400


def test_plans_need_a_run_record(client, v3_tx):
    resp = client.get("/api/plans")
    assert resp.status_code == 404 and resp.json()["error"] == "no_run"


def test_plan_detail_card_and_events(client, v3_tx):
    with core_tx._tx() as conn:
        open_plan = seed(conn)
    resp = client.get(f"/api/plans/{open_plan['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == CARD_KEYS | {"events"}
    assert set(body["events"][0]) == {"session", "event", "reason", "before", "after", "source", "created_at"}
    assert body["events"][0]["event"] == "bought" and body["events"][0]["source"] == "user"
    assert re.fullmatch(ISO_STAMP, body["events"][0]["created_at"])  # 同上：ISO 8601 带时区
    assert client.get("/api/plans/999999").status_code == 404


def test_stop_order_previews_then_takes_effect(client, v3_tx):
    with core_tx._tx() as conn:
        open_plan = seed(conn)
    preview = client.post(
        f"/api/plans/{open_plan['id']}/stop-order", json={"price": 96, "request_id": "s-1", "confirmed": False}
    )
    assert preview.status_code == 200 and preview.json()["write"] is False and "preview" in preview.json()
    confirmed = client.post(
        f"/api/plans/{open_plan['id']}/stop-order", json={"price": 96, "request_id": "s-1", "confirmed": True}
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["plan"]["confirmed_stop_order"] == "96.000000"
    holdings = client.get("/api/plans?group=holdings").json()["plans"]
    assert holdings[0]["stop_order_status"] == "confirmed"
    assert holdings[0]["next_action"]["kind"] == "hold"  # 确认后「今日操作」里的提示消失
    actions = client.get("/api/today").json()["actions"]
    assert actions == []


def test_stop_order_price_mismatch_is_400(client, v3_tx):
    with core_tx._tx() as conn:
        open_plan = seed(conn)
    resp = client.post(
        f"/api/plans/{open_plan['id']}/stop-order", json={"price": 90, "request_id": "s-2", "confirmed": True}
    )
    assert resp.status_code == 400 and resp.json()["error"] == "stop_mismatch"
    unknown = client.post("/api/plans/999999/stop-order", json={"price": 90, "request_id": "s-3", "confirmed": True})
    assert unknown.status_code == 404 and unknown.json()["error"] == "unknown_plan"


def test_raise_stop_only_goes_up(client, v3_tx):
    with core_tx._tx() as conn:
        open_plan = seed(conn)
    ok = client.post(
        f"/api/plans/{open_plan['id']}/raise-stop", json={"stop": 98, "request_id": "r-1", "confirmed": True}
    )
    assert ok.status_code == 200 and D(ok.json()["plan"]["stop"]) == D(98)
    lower = client.post(
        f"/api/plans/{open_plan['id']}/raise-stop", json={"stop": 97, "request_id": "r-2", "confirmed": True}
    )
    assert lower.status_code == 400 and lower.json()["error"] == "stop_not_higher"


def test_cancel_pending_plan(client, v3_tx):
    with core_tx._tx() as conn:
        seed(conn)
    msft = next(row for row in plan_repo.plans_by_status(v3_tx, ("pending",)) if row["ticker"] == "MSFT")
    preview = client.post(f"/api/plans/{msft['id']}/cancel", json={"request_id": "c-1", "confirmed": False})
    assert preview.json()["write"] is False and "preview" in preview.json()
    done = client.post(
        f"/api/plans/{msft['id']}/cancel", json={"request_id": "c-1", "confirmed": True, "reason": "改变主意"}
    )
    assert done.status_code == 200 and done.json()["plan"]["status"] == "cancelled"
    not_pending = client.post(f"/api/plans/{msft['id']}/cancel", json={"request_id": "c-2", "confirmed": True})
    assert not_pending.status_code == 400 and not_pending.json()["error"] == "not_pending"
