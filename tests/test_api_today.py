"""GET /api/today：今日操作与账户条；没有运行记录时页面照常打开（接口契约表第一行）。"""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import investment_assistant.core.tx as core_tx
from investment_assistant.config import load_config
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
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^+Z]*(?:Z|[+-]\d{2}:\d{2})", body["cutoff_at"]
    )  # 时间是 ISO 8601 的 T 形式且带时区（契约表）
    # 未确认止损单的持仓进「今日操作」（spec §7.4 第 4 条）
    assert [row["kind"] for row in body["actions"]] == ["stop_order"]
    action = body["actions"][0]
    assert set(action) == {
        "plan_id",
        "ticker",
        "kind",
        "text",
        "stop",
        "quantity",
        "reason",
        "sell_quantity",
        "counterpart",
        "stale_quantity",
    }
    assert action["sell_quantity"] is None and action["counterpart"] is None and action["stale_quantity"] is False
    assert action["ticker"] == "AAPL" and action["stop"] is not None
    account = body["account"]
    assert set(account) == {"cash", "equity", "unrealized_pnl", "realized_pnl", "cash_negative", "notes", "exposure"}
    assert isinstance(account["cash"], str) and D(account["cash"]) == D(9000)  # 金额是字符串
    # 金额取到分、股数去掉存储尾零：页面原样显示这些字符串（2026-11-07 用户截图）
    assert account["cash"] == "9000.00" and account["equity"] == "10000.00"
    assert account["unrealized_pnl"] == "0.00" and account["realized_pnl"] == "0.00"
    assert str(action["quantity"]) == "10"
    assert D(account["equity"]) == D(10000)  # 9000 + 10 × 100


def test_today_without_run_is_empty_but_account_present(client, v3_tx):
    resp = client.get("/api/today")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session"] is None and body["actions"] == []
    assert body["account"]["cash"] is None and body["account"]["notes"] == []
    assert body["counts"] == {"actions": 0, "holdings": 0, "pending": 0, "watching": 0}


def test_counts_reflect_actions_plans_and_research(client, v3_tx):
    from investment_assistant.facts import research_repo

    seed(v3_tx)
    holding = plan_repo.active_plan(v3_tx, "AAPL")
    assert holding is not None
    for ticker in ("MSFT", "NVDA"):
        plan_repo.insert_plan(v3_tx, {**holding, "ticker": ticker, "status": "pending"})
    research_repo.insert_research(
        v3_tx,
        {
            "ticker": "MU",
            "thesis": "观察",
            "origin": "chat",
            "created_session": SESSION,
            "valid_until": SESSION + timedelta(days=7),
        },
    )
    v3_tx.commit()
    body = client.get("/api/today").json()
    assert body["counts"] == {"actions": 1, "holdings": 1, "pending": 2, "watching": 1}
    assert len(body["actions"]) == body["counts"]["actions"]


def test_today_has_exposure_and_rotation_action(client, v3_tx):
    """C18：account.exposure 的四个键（字符串）；换仓卖出行带 sell_quantity / counterpart。"""
    from _rotation_seed import instruct, seed_c4

    ids = seed_c4(v3_tx)
    instruct(v3_tx, ids["a"], candidate="BBB", quantity=150)
    v3_tx.commit()
    body = client.get("/api/today").json()
    exposure = body["account"]["exposure"]
    assert set(exposure) == {"pct", "cap", "room", "incomplete"}
    # 总资产 100160.50，持仓市值 301×40 + 1000×42.98 = 55020.00：占比 0.5493，上限 0.6，剩余 100160.50×0.6 − 55020
    cap = f"{D(str(load_config().workbench.total_cap)):f}"  # 不依赖本机默认配置
    assert exposure == {"pct": "0.5493", "cap": cap, "room": "5076.30", "incomplete": False}
    rotation = next(row for row in body["actions"] if row["kind"] == "rotation_sell")
    assert rotation["ticker"] == "AAA" and rotation["sell_quantity"] == "150" and rotation["counterpart"] == "BBB"
    assert rotation["text"] == "下次开盘卖出 150 股（换仓至 BBB）" and rotation["stale_quantity"] is False
    plain = [row for row in body["actions"] if row["ticker"] == "CCC"]
    assert plain and plain[0]["sell_quantity"] is None and plain[0]["counterpart"] is None


def test_today_stale_stop_quantity_row(client, v3_tx):
    """C10：止损单数量待改的持仓行 stale_quantity 为真，文字写明新数量。"""
    from _rotation_seed import add_holding, holding_review, seed_c4

    seed_c4(v3_tx)
    stale = add_holding(v3_tx, "STL", 151, "40", 40.0, confirmed_stop_order=D(36))
    plan_repo.update_plan(v3_tx, stale["id"], stop_quantity_stale=True)
    holding_review(v3_tx, stale["id"], "holds")
    v3_tx.commit()
    row = next(row for row in client.get("/api/today").json()["actions"] if row["ticker"] == "STL")
    assert row["kind"] == "stop_order" and row["stale_quantity"] is True
    assert row["text"] == "止损单数量需改为 151 股" and row["sell_quantity"] is None


def test_today_without_run_has_exposure_without_capital(client, v3_tx):
    """没有运行记录也没有账户资金：占比与额度为空，上限照给。"""
    exposure = client.get("/api/today").json()["account"]["exposure"]
    cap = f"{D(str(load_config().workbench.total_cap)):f}"
    assert exposure == {"pct": None, "cap": cap, "room": None, "incomplete": False}
