"""Chat-only tools: confirmed writes of cash flows, equity and trades with plan-line
progression (spec §6.5, §7.2, §8), voids and the watchlist (spec §10)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.facts import collect as facts_collect
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts.view import today_actions
from investment_assistant.toolsurface.dispatch import call
from investment_assistant.toolsurface.env import make_env

SESSION = date(2026, 11, 4)
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)
TRADE = {
    "ticker": "AAPL",
    "side": "buy",
    "quantity": 10,
    "price": 100,
    "executed_at": "2026-11-05T14:00:00+00:00",
    "request_id": "t1",
}


def _bars(n=20, *, end=SESSION, close=100.0):
    days = [end - timedelta(days=i) for i in range(n)][::-1]
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
def chat_env(v3_tx):
    facts_repo.insert_run(
        v3_tx,
        {
            "run_id": "run-1",
            "session": SESSION,
            "trigger": "scheduled",
            "status": "ok",
            "error": None,
            "market": {"label": "neutral"},
            "factors": {},
            "coverage": {},
            "holdings": [],
            "draft_md": "# 草稿",
            "started_at": NOW - timedelta(minutes=5),
            "finished_at": NOW,
        },
    )
    data_repo.store_bars(v3_tx, "AAPL", _bars(), now=NOW)
    v3_tx.commit()  # 事实已入库，写入工具的事务不应带走它
    return make_env(session=SESSION, mode="chat", agent_session="t-chat", cfg=AssistantConfig(), now=NOW)


def test_record_equity_and_trade_need_confirmation(chat_env, v3_tx):
    r = call(chat_env, "record_equity", {"equity": 50000, "request_id": "e1", "confirmed": False})["data"]
    assert r["write"] is False and "50000" in r["preview"]
    assert v3_tx.execute("SELECT count(*) FROM v3_equity").fetchone()[0] == 0  # 表里没有行
    row = call(chat_env, "record_equity", {"equity": 50000, "request_id": "e1", "confirmed": True})["data"]
    assert float(row["equity"]) == 50000.0
    call(chat_env, "record_equity", {"equity": 50000, "request_id": "e1", "confirmed": True})  # 重放仍一行
    assert v3_tx.execute("SELECT count(*) FROM v3_equity").fetchone()[0] == 1

    prev = call(chat_env, "record_trade", {**TRADE, "confirmed": False})["data"]
    assert prev["write"] is False and all(x in prev["preview"] for x in ("AAPL", "买入", "10", "100"))
    assert v3_tx.execute("SELECT count(*) FROM v3_trades").fetchone()[0] == 0
    done = call(chat_env, "record_trade", {**TRADE, "confirmed": True})["data"]
    assert float(done["trade"]["quantity"]) == 10.0 and done["trade"]["side"] == "buy"
    assert done["plan"] is not None and done["plan"]["kind"] == "adopted"  # 无计划买入 → 补录
    assert v3_tx.execute("SELECT count(*) FROM v3_trades").fetchone()[0] == 1
    # 卖出超过持仓 -> 'position_invalid' 且不写入
    assert call(
        chat_env,
        "record_trade",
        {
            "ticker": "AAPL",
            "side": "sell",
            "quantity": 20,
            "price": 100,
            "executed_at": "2026-11-05T15:00:00+00:00",
            "request_id": "t2",
            "confirmed": True,
        },
    )["error"].startswith("position_invalid")
    assert v3_tx.execute("SELECT count(*) FROM v3_trades").fetchone()[0] == 1
    # 全部卖出后原样重发：返回原行，不报 position_invalid，表里不多行
    sell = {
        "ticker": "AAPL",
        "side": "sell",
        "quantity": 10,
        "price": 105,
        "executed_at": "2026-11-05T15:00:00+00:00",
        "request_id": "s1",
        "confirmed": True,
    }
    sold = call(chat_env, "record_trade", sell)["data"]
    again = call(chat_env, "record_trade", sell)
    assert "error" not in again and again["data"]["trade"]["id"] == sold["trade"]["id"]
    assert v3_tx.execute("SELECT count(*) FROM v3_trades").fetchone()[0] == 2
    # equity <= 0、quantity <= 0、side 不是 buy / sell -> 'invalid_argument'
    assert call(chat_env, "record_equity", {"equity": 0, "request_id": "e2", "confirmed": True})["error"].startswith(
        "invalid_argument"
    )
    assert call(chat_env, "record_trade", {**TRADE, "request_id": "t3", "quantity": 0, "confirmed": True})[
        "error"
    ].startswith("invalid_argument")
    assert call(chat_env, "record_trade", {**TRADE, "request_id": "t4", "side": "hold", "confirmed": True})[
        "error"
    ].startswith("invalid_argument")
    # plan_id 指向不存在的计划 -> 'invalid_argument'
    assert call(chat_env, "record_trade", {**TRADE, "request_id": "t5", "plan_id": 999, "confirmed": True})[
        "error"
    ].startswith("invalid_argument")


def _plan(v3_tx, ticker="AAPL", **over):
    """直接插一份计划行，给 record_trade / stop 工具做关联对象。"""
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
        "valid_from": date(2026, 11, 5),
        "valid_until": date(2026, 11, 11),
        "shares": 10,
        "sizing": {},
        "warnings": [],
        "thesis": "论点",
        "invalidation": "作废条件",
        "counter": "",
        "refs": [],
        "agent_session": "seed",
        "status": "pending",
        "kind": "planned",
    }
    row.update(over)
    return plan_repo.insert_plan(v3_tx, row)


def _actions(chat_env, v3_tx):
    return today_actions(v3_tx, session=SESSION, cutoff_at=NOW, cfg=chat_env.cfg)


def _events(v3_tx, plan_id):
    return [row["event"] for row in plan_repo.events(v3_tx, plan_id)]


def test_buy_with_pending_plan_returns_stop_order_and_flow(chat_env, v3_tx):
    """A10：买入返回止损单指令；确认前有「未确认止损单」，确认后消失，上调后出现「需上调」。"""
    plan = _plan(v3_tx)
    v3_tx.commit()
    done = call(chat_env, "record_trade", {**TRADE, "plan_id": plan["id"], "confirmed": True})["data"]
    assert D(done["stop_order"]["price"]) == D(96) and D(done["stop_order"]["quantity"]) == D(10)
    assert done["plan"]["status"] == "open" and done["plan"]["start_session"] == "2026-11-05"
    assert _events(v3_tx, plan["id"]) == ["bought"]  # 价在区间内、10 股不多于计划：无偏离行
    assert any(a["ticker"] == "AAPL" and a["text"] == "未确认止损单" for a in _actions(chat_env, v3_tx))
    confirmed = call(
        chat_env, "confirm_stop_order", {"plan_id": plan["id"], "price": 96, "request_id": "so-1", "confirmed": True}
    )["data"]
    assert D(str(confirmed["plan"]["confirmed_stop_order"])) == D(96)
    assert not any(a["ticker"] == "AAPL" for a in _actions(chat_env, v3_tx))
    assert call(
        chat_env, "confirm_stop_order", {"plan_id": plan["id"], "price": 95, "request_id": "so-2", "confirmed": True}
    )["error"].startswith("stop_mismatch")
    raised = call(chat_env, "raise_stop", {"plan_id": plan["id"], "stop": 98, "request_id": "rs-1", "confirmed": True})[
        "data"
    ]
    assert D(str(raised["plan"]["stop"])) == D(98)
    texts = [a["text"] for a in _actions(chat_env, v3_tx) if a["ticker"] == "AAPL"]
    assert len(texts) == 1 and texts[0].startswith("止损单需上调至 98")
    assert "当前挂单 96" in texts[0]


def test_buy_with_open_plan_records_added_and_deviations(chat_env, v3_tx):
    plan = _plan(v3_tx)  # pending：第一笔买入推进到 open 并记偏离
    v3_tx.commit()
    # 成交价在区间外、股数多于计划 → bought 后各记一条 deviation
    done = call(
        chat_env,
        "record_trade",
        {**TRADE, "plan_id": plan["id"], "quantity": 20, "price": 105, "request_id": "t-dev", "confirmed": True},
    )["data"]
    assert done["plan"]["status"] == "open" and D(done["plan"]["stop"]) == D(96)
    events = _events(v3_tx, plan["id"])
    assert events == ["bought", "deviation", "deviation"]
    reasons = [row["reason"] for row in plan_repo.events(v3_tx, plan["id"]) if row["event"] == "deviation"]
    assert any("不在入场区间" in text for text in reasons)
    assert any("多于计划" in text for text in reasons)
    # 再买一笔：加仓，只记 added
    again = call(chat_env, "record_trade", {**TRADE, "plan_id": plan["id"], "request_id": "t-add", "confirmed": True})[
        "data"
    ]
    assert _events(v3_tx, plan["id"]) == ["bought", "deviation", "deviation", "added"]
    assert D(again["stop_order"]["quantity"]) == D(30)


def test_buy_without_plan_creates_adopted_plan(chat_env, v3_tx):
    """A11：无计划买入补录为 adopted；有日线按规则价位，缺日线四空并提示。"""
    done = call(chat_env, "record_trade", {**TRADE, "confirmed": True})["data"]
    plan = done["plan"]
    assert plan["kind"] == "adopted" and plan["status"] == "open" and plan["origin"] == "host"
    assert D(str(plan["stop"])) == D(96) and D(str(plan["entry_low"])) == D(99)
    assert plan["thesis"] == "补录持仓，没有入场论点" and plan["start_session"] == SESSION.isoformat()
    assert plan["max_hold_sessions"] == 20
    assert _events(v3_tx, plan["id"]) == ["adopted"]
    assert D(done["stop_order"]["price"]) == D(96)
    bare = call(
        chat_env,
        "record_trade",
        {
            "ticker": "ORCL",
            "side": "buy",
            "quantity": 5,
            "price": 70,
            "executed_at": TRADE["executed_at"],
            "request_id": "t-orcl",
            "confirmed": True,
        },
    )["data"]
    assert bare["plan"]["stop"] is None and bare["stop_order"] is None
    assert _events(v3_tx, bare["plan"]["id"]) == ["adopted"]
    assert any(a["ticker"] == "ORCL" and a["text"] == "缺日线，未能给出止损" for a in _actions(chat_env, v3_tx))


def test_raise_stop_only_upward_and_below_close(chat_env, v3_tx):
    """A4：对话里把止损调低被拒；高于最新收盘也被拒。"""
    plan = _plan(v3_tx, status="open", start_session=SESSION, confirmed_stop_order=D(96))
    v3_tx.commit()
    assert call(chat_env, "raise_stop", {"plan_id": plan["id"], "stop": 90, "request_id": "rs-lo", "confirmed": True})[
        "error"
    ].startswith("stop_not_higher")
    assert call(chat_env, "raise_stop", {"plan_id": plan["id"], "stop": 96, "request_id": "rs-eq", "confirmed": True})[
        "error"
    ].startswith("stop_not_higher")
    assert call(chat_env, "raise_stop", {"plan_id": plan["id"], "stop": 105, "request_id": "rs-hi", "confirmed": True})[
        "error"
    ].startswith("stop_above_close")


def test_sell_all_closes_plan_and_clears_actions(chat_env, v3_tx):
    """A6 后半：卖完后计划 closed、带平仓结果，今日操作里消失。"""
    plan = _plan(v3_tx)
    v3_tx.commit()
    call(chat_env, "record_trade", {**TRADE, "plan_id": plan["id"], "confirmed": True})
    sold = call(
        chat_env,
        "record_trade",
        {
            "ticker": "AAPL",
            "side": "sell",
            "quantity": 10,
            "price": 105,
            "executed_at": "2026-11-05T15:00:00+00:00",
            "request_id": "s-all",
            "confirmed": True,
        },
    )["data"]
    assert sold["plan"]["status"] == "closed" and sold["plan"]["ended_session"] == SESSION.isoformat()
    assert D(str(sold["outcome"]["realized_pnl"])) == D(50)
    assert sold["outcome"]["exit_reason"] == "manual" and sold["outcome"]["entry_in_zone"] is True
    assert _events(v3_tx, plan["id"]) == ["bought", "sold", "closed"]
    assert not any(a["ticker"] == "AAPL" for a in _actions(chat_env, v3_tx))


def test_chat_writes_preview_before_confirming(chat_env, v3_tx):
    """A19：记出入金、对账、取消计划、作废、关注列表增删都先复述。"""
    plan = _plan(v3_tx)
    trade = facts_repo.insert_trade(
        v3_tx,
        {"request_id": "pv-1", "ticker": "AAPL", "side": "buy", "quantity": 10, "price": D(100), "executed_at": NOW},
    )
    flow = plan_repo.insert_cash_flow(
        v3_tx, {"request_id": "pv-2", "kind": "deposit", "amount": D(500), "effective_at": NOW, "note": ""}
    )
    v3_tx.commit()
    previews = [
        ("record_cash_flow", {"kind": "deposit", "amount": 2000, "request_id": "cf-1", "confirmed": False}),
        ("record_equity", {"equity": 50000, "request_id": "e-1", "confirmed": False}),
        ("cancel_plan", {"plan_id": plan["id"], "request_id": "c-1", "confirmed": False}),
        ("void_record", {"kind": "trade", "target_id": trade["id"], "request_id": "v-1", "confirmed": False}),
        ("watchlist_add", {"ticker": "NFLX", "request_id": "w-1", "confirmed": False, "thesis": "观察"}),
        ("watchlist_remove", {"ticker": "NFLX", "request_id": "w-2", "confirmed": False}),
    ]
    for tool, args in previews:
        result = call(chat_env, tool, args)["data"]
        assert result["write"] is False and result["preview"], (tool, result)
    counts = {
        table: v3_tx.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("v3_cash_flows", "v3_equity", "v3_voids", "watchlist_items")
    }
    assert counts == {"v3_cash_flows": 1, "v3_equity": 0, "v3_voids": 0, "watchlist_items": 0}
    assert flow["id"] == 1
    untouched = plan_repo.plan(v3_tx, plan["id"])
    assert untouched is not None and untouched["status"] == "pending"  # 取消没有发生


def test_void_buy_that_breaks_fifo_is_rejected(chat_env, v3_tx):
    """Review Focus 3：作废买入后剩余卖出超仓 → position_invalid，不写作废行。"""
    first = facts_repo.insert_trade(
        v3_tx,
        {"request_id": "f-1", "ticker": "AAPL", "side": "buy", "quantity": 10, "price": D(100), "executed_at": NOW},
    )
    facts_repo.insert_trade(
        v3_tx,
        {"request_id": "f-2", "ticker": "AAPL", "side": "buy", "quantity": 10, "price": D(100), "executed_at": NOW},
    )
    facts_repo.insert_trade(
        v3_tx,
        {"request_id": "f-3", "ticker": "AAPL", "side": "sell", "quantity": 15, "price": D(100), "executed_at": NOW},
    )
    v3_tx.commit()
    rejected = call(
        chat_env, "void_record", {"kind": "trade", "target_id": first["id"], "request_id": "v-fifo", "confirmed": True}
    )
    assert rejected["error"].startswith("position_invalid")
    assert v3_tx.execute("SELECT count(*) FROM v3_voids").fetchone()[0] == 0
    after = facts_repo.trade(v3_tx, first["id"])
    assert after is not None and after["voided_at"] is None


def test_void_buy_cancels_plan_and_restores_cash(chat_env, v3_tx):
    """A19：作废唯一买入 → 计划 cancelled（成交作废），账本现金复原。"""
    call(chat_env, "record_cash_flow", {"kind": "opening", "amount": 10000, "request_id": "o-1", "confirmed": True})
    plan = _plan(v3_tx)
    v3_tx.commit()
    done = call(chat_env, "record_trade", {**TRADE, "plan_id": plan["id"], "confirmed": True})["data"]
    account = call(chat_env, "get_account", {})["data"]
    assert D(str(account["ledger"]["cash"])) == D(9000)  # 10000 − 10×100
    voided = call(
        chat_env,
        "void_record",
        {"kind": "trade", "target_id": done["trade"]["id"], "request_id": "v-2", "confirmed": True},
    )["data"]
    assert voided["plan"]["status"] == "cancelled" and voided["plan"]["end_reason"] == "成交作废"
    assert _events(v3_tx, plan["id"]) == ["bought", "cancelled"]
    restored = call(chat_env, "get_account", {})["data"]
    assert D(str(restored["ledger"]["cash"])) == D(10000)
    # 同一 request_id 重发：幂等返回原作废行，不再重复处理；换一个 request_id 才报 already_voided
    replay = call(
        chat_env,
        "void_record",
        {"kind": "trade", "target_id": done["trade"]["id"], "request_id": "v-2", "confirmed": True},
    )["data"]
    assert replay["replay"] is True and replay["void"]["id"] == voided["void"]["id"]
    assert v3_tx.execute("SELECT count(*) FROM v3_voids").fetchone()[0] == 1
    assert call(
        chat_env,
        "void_record",
        {"kind": "trade", "target_id": done["trade"]["id"], "request_id": "v-9", "confirmed": True},
    )["error"].startswith("already_voided")


def test_record_cash_flow_kinds_and_opening_once(chat_env, v3_tx):
    assert call(chat_env, "record_cash_flow", {"kind": "bonus", "amount": 1, "request_id": "k-0", "confirmed": True})[
        "error"
    ].startswith("invalid_kind")
    assert call(chat_env, "record_cash_flow", {"kind": "deposit", "amount": 0, "request_id": "k-1", "confirmed": True})[
        "error"
    ].startswith("invalid_argument")
    opening = call(
        chat_env, "record_cash_flow", {"kind": "opening", "amount": 10000, "request_id": "o-1", "confirmed": True}
    )["data"]
    assert opening["kind"] == "opening" and D(str(opening["amount"])) == D(10000)
    assert call(chat_env, "record_cash_flow", {"kind": "opening", "amount": 1, "request_id": "o-2", "confirmed": True})[
        "error"
    ].startswith("opening_exists")
    assert (
        call(chat_env, "record_cash_flow", {"kind": "deposit", "amount": 2000, "request_id": "o-3", "confirmed": True})[
            "data"
        ]["kind"]
        == "deposit"
    )
    voided = call(
        chat_env,
        "void_record",
        {"kind": "cash_flow", "target_id": opening["id"], "request_id": "v-3", "confirmed": True},
    )["data"]
    assert voided["void"]["target_id"] == opening["id"]
    again = call(
        chat_env, "record_cash_flow", {"kind": "opening", "amount": 5000, "request_id": "o-4", "confirmed": True}
    )["data"]
    assert again["kind"] == "opening"  # 作废旧的之后可以重记


def test_record_equity_returns_reconciliation(chat_env, v3_tx):
    empty = call(chat_env, "record_equity", {"equity": 50000, "request_id": "e-1", "confirmed": True})["data"]
    assert empty["reconciliation"]["computed"] is None and empty["reconciliation"]["difference"] is None
    call(chat_env, "record_cash_flow", {"kind": "opening", "amount": 50000, "request_id": "o-1", "confirmed": True})
    again = call(chat_env, "record_equity", {"equity": 50100, "request_id": "e-2", "confirmed": True})["data"]
    assert D(again["reconciliation"]["computed"]) == D(50000)
    assert D(again["reconciliation"]["difference"]) == D(100)


def test_cancel_plan_only_pending_and_idempotent(chat_env, v3_tx):
    pending = _plan(v3_tx)
    opened = _plan(v3_tx, ticker="MSFT", status="open", start_session=SESSION)
    v3_tx.commit()
    assert call(chat_env, "cancel_plan", {"plan_id": opened["id"], "request_id": "c-0", "confirmed": True})[
        "error"
    ].startswith("not_pending")
    cancelled = call(
        chat_env, "cancel_plan", {"plan_id": pending["id"], "request_id": "c-1", "confirmed": True, "reason": "不做了"}
    )["data"]
    assert cancelled["plan"]["status"] == "cancelled" and cancelled["plan"]["end_reason"] == "不做了"
    assert _events(v3_tx, pending["id"]) == ["cancelled"]
    event = plan_repo.events(v3_tx, pending["id"])[0]
    assert event["source"] == "user" and "c-1" in event["refs"]
    replay = call(chat_env, "cancel_plan", {"plan_id": pending["id"], "request_id": "c-1", "confirmed": True})["data"]
    assert replay["replay"] is True and _events(v3_tx, pending["id"]) == ["cancelled"]


def test_watchlist_add_and_remove(chat_env, v3_tx):
    added = call(
        chat_env, "watchlist_add", {"ticker": "NFLX", "request_id": "w-1", "confirmed": True, "thesis": "观察流媒体"}
    )["data"]
    assert added["item"]["ticker"] == "NFLX" and added["item"]["status"] == "active"
    assert v3_tx.execute("SELECT count(*) FROM watchlist_items WHERE ticker = 'NFLX'").fetchone()[0] == 1
    # 重放同一次添加：幂等，返回同样的成功形状，仍只有一行
    again = call(
        chat_env, "watchlist_add", {"ticker": "NFLX", "request_id": "w-1b", "confirmed": True, "thesis": "观察流媒体"}
    )["data"]
    assert again["item"]["ticker"] == "NFLX" and again["item"]["status"] == "active"
    assert v3_tx.execute("SELECT count(*) FROM watchlist_items").fetchone()[0] == 1
    removed = call(chat_env, "watchlist_remove", {"ticker": "nflx", "request_id": "w-2", "confirmed": True})["data"]
    assert removed["ticker"] == "NFLX" and removed["deleted"] is True
    assert v3_tx.execute("SELECT count(*) FROM watchlist_items").fetchone()[0] == 0
    # 再删一次：不再报错，如实返回 deleted: False
    twice = call(chat_env, "watchlist_remove", {"ticker": "NFLX", "request_id": "w-3", "confirmed": True})["data"]
    assert twice["ticker"] == "NFLX" and twice["deleted"] is False


def test_ensure_data_calls_collect_extra(chat_env, monkeypatch):
    seen = []

    def fake_collect_extra(tickers, *, session, now, run_id, trigger, cfg, families, deps=None):
        seen.append({"tickers": tickers, "session": session, "trigger": trigger, "families": families})
        return [{"family": "bars", "status": "ok", "target_count": 1, "ok_count": 1, "failures": [], "detail": {}}]

    monkeypatch.setattr(facts_collect, "collect_extra", fake_collect_extra)
    r = call(chat_env, "ensure_data", {"ticker": "NFLX"})["data"]
    assert len(seen) == 1 and seen[0]["tickers"] == ["NFLX"]
    assert seen[0]["families"] == ("bars", "news", "sec_filings", "earnings", "fundamentals")
    assert seen[0]["trigger"] == "manual_family" and seen[0]["session"] == SESSION
    assert r["families"] == {"bars": {"total": 1, "ok": 1, "missing": [], "skipped": None}}
    assert r["lines"] == ["日线：1/1"]
    # 非美股代码 -> 'invalid_ticker'，collect_extra 不被调用
    assert call(chat_env, "ensure_data", {"ticker": "600519.SH"})["error"].startswith("invalid_ticker")
    assert len(seen) == 1
