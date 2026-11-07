"""Ledger tools: the get_account shape and the sizing equity source after an opening (A9)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.toolsurface.dispatch import call
from investment_assistant.toolsurface.env import make_env

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


@pytest.fixture
def env(v3_tx):
    facts_repo.insert_run(
        v3_tx,
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
            "draft_md": "",
            "started_at": NOW - timedelta(minutes=5),
            "finished_at": NOW,
            "dropped": [],
        },
    )
    data_repo.store_bars(v3_tx, "AAPL", _bars(), now=NOW)
    v3_tx.commit()
    return make_env(session=SESSION, mode="chat", agent_session="t-ledger", cfg=AssistantConfig(), now=NOW)


def test_get_account_shape(env, v3_tx):
    empty = call(env, "get_account", {})["data"]
    assert empty["ledger"]["cash"] is None and empty["ledger"]["equity"] is None
    assert empty["ledger"]["positions"] == [] and empty["sizing_snapshot"] is None
    assert empty["latest_equity"] is None and empty["reconciliation"] is None
    assert empty["recent_outcomes"] == []

    call(
        env,
        "record_cash_flow",
        {
            "kind": "opening",
            "amount": 10000,
            "request_id": "o-1",
            "confirmed": True,
            "effective_at": "2026-11-03T00:00:00+00:00",
        },
    )
    call(env, "record_equity", {"equity": 9500, "request_id": "e-1", "confirmed": True})
    facts_repo.insert_trade(
        v3_tx,
        {
            "request_id": "t-1",
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 10,
            "price": D(100),
            "executed_at": datetime(2026, 11, 4, 15, tzinfo=UTC),
        },
    )
    plan_repo.insert_plan(
        v3_tx,
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
            "agent_session": "t-ledger",
            "status": "closed",
            "kind": "planned",
            "outcome": {"realized_pnl": D(50), "exit_reason": "manual"},
        },
    )
    v3_tx.commit()

    account = call(env, "get_account", {})["data"]
    ledger = account["ledger"]
    assert D(str(ledger["cash"])) == D(9000)  # 10000 − 10×100
    assert D(str(ledger["market_value"])) == D(1000)
    assert [row["ticker"] for row in ledger["positions"]] == ["AAPL"]
    assert account["latest_equity"] is not None
    assert D(str(account["reconciliation"]["difference"])) == D(-500)  # 9500 − 10000
    assert [row["ticker"] for row in account["recent_outcomes"]] == ["AAPL"]
    assert account["recent_outcomes"][0]["outcome"]["exit_reason"] == "manual"
    assert account["sizing_snapshot"]["source"] == "ledger"
    assert D(str(account["sizing_snapshot"]["equity"])) == D(10000)


def test_sizing_takes_ledger_equity_after_opening(env, v3_tx):
    """A9：记了期初现金后，compute_plan_levels 的仓位本金取账本算出的总资产。"""
    without = call(env, "compute_plan_levels", {"ticker": "AAPL"})["data"]
    assert without["sizing"]["status"] == "no_equity" and without["sizing"]["shares"] is None

    call(
        env,
        "record_cash_flow",
        {
            "kind": "opening",
            "amount": 100000,
            "request_id": "o-1",
            "confirmed": True,
            "effective_at": "2026-11-03T00:00:00+00:00",
        },
    )
    facts_repo.insert_trade(
        v3_tx,
        {
            "request_id": "t-1",
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 100,
            "price": D(100),
            "executed_at": datetime(2026, 11, 4, 15, tzinfo=UTC),
        },
    )
    v3_tx.commit()

    sized = call(env, "compute_plan_levels", {"ticker": "AAPL"})["data"]
    assert sized["sizing"]["status"] == "capped" and sized["sizing"]["applied_cap"] == "single"
    assert D(sized["sizing"]["equity"]) == D(100000)  # 账本：100000 现金 + 10000 市值
    # 风险 1% = 1000 / 每股 4 = 250；单票 (25000 − 10000 已持) / 101 = 148 → 取较小者
    assert sized["sizing"]["shares"] == 148
