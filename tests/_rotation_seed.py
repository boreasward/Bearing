"""Shared seeding for the v3.3 rotation tests: one account, a few open holdings, a candidate run.

The reference scenario (``seed_c4``): opening cash 100000, AAA 301 shares at cost 39.50 (close 40),
CCC 1000 shares at cost 42.97 (adopted, close 42.98) and the candidate BBB at close 50 with ATR 2.
Equity is 100160.50, holdings are worth 55020.00; half-rotating AAA leaves room for 217 shares of
BBB and the full rotation for 250 (the risk term). Numbers are worked out in the comments of the
tests that use them.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts import rotation_repo

SESSION = date(2026, 11, 4)  # 周三
PREV = date(2026, 11, 3)
OPEN_DAY = date(2026, 10, 28)  # 周三：持仓的起算日
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)
CFG = AssistantConfig()


def bars(n=80, *, end=SESSION, close=100.0, low=None, high=None):
    """n 根连续交易日日线（最后一根是 end），恒定波幅 2（ATR 14 = 2）。"""
    days = [add_trading_days(end, -(n - 1 - i)) for i in range(n)]
    return [
        {
            "price_date": day,
            "open": close,
            "high": close + 1.0 if high is None else high,
            "low": close - 1.0 if low is None else low,
            "close": close,
            "adj_close": close,
            "volume": 1000,
            "split_factor": 1.0,
        }
        for day in days
    ]


def run_row(run_id="run-1", session=SESSION, *, factors=None):
    return {
        "run_id": run_id,
        "session": session,
        "trigger": "scheduled",
        "status": "ok",
        "error": None,
        "market": {"label": "neutral"},
        "factors": factors or {},
        "coverage": {},
        "holdings": [],
        "dropped": [],
        "draft_md": "# 草稿",
        "started_at": NOW - timedelta(minutes=5),
        "finished_at": NOW,
    }


def candidate_row(ticker, rank):
    return {
        "ticker": ticker,
        "rank": rank,
        "screen_group": "trigger",
        "trigger_rules": ["volume_breakout"],
        "rs": 1.5,
        "atr_pct": 2.0,
        "setup_tier": "favorable",
        "industry": "半导体",
        "industry_tier": "favorable",
        "reason": f"{ticker} 的理由行",
    }


def seed_run(conn, tickers=("BBB",), *, session=SESSION, run_id="run-1", factors=None):
    """当天的运行记录与名单（按顺序排名次），名单股票各补一份平的日线（收盘 50）。"""
    facts_repo.insert_run(conn, run_row(run_id, session, factors=factors))
    facts_repo.insert_candidates(conn, run_id, [candidate_row(t, i + 1) for i, t in enumerate(tickers)])
    for ticker in tickers:
        data_repo.store_bars(conn, ticker, bars(close=50.0, end=session), now=NOW)
    data_repo.store_bars(conn, "QQQ", bars(close=300.0, end=session), now=NOW)  # 相对强度的基准


def seed_opening(conn, amount=100000):
    plan_repo.insert_cash_flow(
        conn,
        {
            "request_id": "seed-opening",
            "kind": "opening",
            "amount": D(amount),
            "effective_at": datetime(2026, 10, 1, 14, tzinfo=UTC),
            "note": "",
        },
    )


def plan_row(ticker, **over):
    row = {
        "session": date(2026, 10, 27),
        "ticker": ticker,
        "origin": "brief",
        "entry_type": "zone_limit",
        "entry_low": D(99),
        "entry_high": D(101),
        "stop": D(96),
        "target1": D(108),
        "initial_stop": D(96),
        "atr": D(2),
        "levels_source": "host",
        "max_hold_sessions": 20,
        "valid_from": OPEN_DAY,
        "valid_until": date(2026, 11, 2),
        "shares": None,
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
    return row


def add_holding(
    conn,
    ticker,
    quantity,
    cost,
    close,
    *,
    kind="planned",
    with_bars=True,
    bars_end=SESSION,
    reviewed_through=PREV,
    **plan_over,
):
    """一只已买入的持仓：成交、日线（平的，收盘 ``close``）与 open 计划（止损 = 收盘 − 4）。返回计划行。"""
    facts_repo.insert_trade(
        conn,
        {
            "request_id": f"seed-buy-{ticker}",
            "ticker": ticker,
            "side": "buy",
            "quantity": D(quantity),
            "price": D(cost),
            "fee": D(0),
            "executed_at": datetime(2026, 10, 28, 15, tzinfo=UTC),
        },
    )
    if with_bars:
        data_repo.store_bars(conn, ticker, bars(end=bars_end, close=float(close)), now=NOW)
    stop = D(str(close)) - 4
    fields = {
        "status": "open",
        "kind": kind,
        "start_session": OPEN_DAY,
        "reviewed_through": reviewed_through,
        "entry_low": D(str(cost)) - 1,
        "entry_high": D(str(cost)) + 1,
        "stop": stop,
        "initial_stop": stop,
        "target1": D(str(close)) + 8,
    }
    fields.update(plan_over)
    return plan_repo.insert_plan(conn, plan_row(ticker, **fields))


def holding_review(conn, plan_id, verdict="holds", *, session=SESSION):
    return plan_repo.upsert_holding_review(
        conn,
        {
            "plan_id": plan_id,
            "session": session,
            "verdict": verdict,
            "reasons": "论点仍成立" if verdict != "broken" else "论点不成立",
            "counter": "",
            "refs": [],
            "agent_session": "seed",
        },
    )


def seed_c4(conn, tickers=("BBB",)):
    """参考场景：返回 {'a': AAA 计划, 'c': CCC 计划}；两只当天都有「成立 / 无法判断」的复查结论。"""
    seed_run(conn, tickers)
    seed_opening(conn)
    a = add_holding(conn, "AAA", 301, "39.50", 40.0)
    c = add_holding(conn, "CCC", 1000, "42.97", 42.98, kind="adopted")
    holding_review(conn, a["id"], "holds")
    holding_review(conn, c["id"], "unknown")
    return {"a": a, "c": c}


def add_rotation(
    conn,
    *,
    session=SESSION,
    verdict="half",
    origin="brief",
    candidate="BBB",
    holding=None,
    request_id=None,
    sell_quantity=150,
    buy_shares=217,
):
    """直接写一条换仓记录；``holding`` 是 (计划编号, 股票)。半仓 / 全仓必须带它。"""
    row = {
        "session": session,
        "origin": origin,
        "candidate": candidate,
        "verdict": verdict,
        "reasons": "理由",
        "snapshot": {"session": session, "fees": {"total": "2.00"}},
        "agent_session": "seed",
        "request_id": request_id,
    }
    if holding is not None:
        row["holding_plan_id"], row["holding_ticker"] = holding
    if verdict != "hold":
        row.update(
            weakness="A 没按预期走",
            cost_response="手续费可以接受",
            sell_quantity=D(sell_quantity),
            buy_shares=D(buy_shares),
        )
    return rotation_repo.insert_rotation(conn, row)


def instruct(conn, holding, *, candidate="BBB", quantity=150, funded_status="pending", **funded_over):
    """给 A 一条换仓指令（同时落库换仓记录与资金来自这次换仓的 B 计划）；返回 (换仓记录, B 的计划)。"""
    rotation = add_rotation(
        conn,
        origin="chat",
        candidate=candidate,
        holding=(holding["id"], holding["ticker"]),
        sell_quantity=quantity,
        request_id=f"seed-rotation-{holding['ticker']}",
    )
    plan_repo.update_plan(
        conn, holding["id"], sell_instruction="rotation", sell_quantity=D(quantity), rotation_id=rotation["id"]
    )
    fields = {  # B 的计划按候选收盘 50 的规则价位，复查时不会因价位被作废
        "status": funded_status,
        "funded_by_rotation_id": rotation["id"],
        "entry_low": D(49),
        "entry_high": D(51),
        "stop": D(46),
        "initial_stop": D(46),
        "target1": D(58),
    }
    if funded_status not in ("pending", "open"):
        fields.update(ended_session=SESSION, end_reason="结束")
    fields.update(funded_over)
    return rotation, plan_repo.insert_plan(conn, plan_row(candidate, **fields))
