"""Retrospect view over the v3_tx database (spec §5): ended-plan records joined with
candidates and research origin, the weekly summary's account snapshots, the empty view."""

from datetime import UTC, date, datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.facts import plan_repo, research_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts.retrospect import records, retrospect_view, weekly_view

NY = ZoneInfo("America/New_York")
SESSION = date(2026, 11, 4)  # 周三；本周 = 11/2–11/4
D1 = date(2026, 11, 2)
D0 = date(2026, 10, 30)  # 周五：计划保存日
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)
CFG = AssistantConfig()


def bars(n=20, *, end=SESSION, close=100.0):
    """n 根连续交易日日线（最后一根是 end），恒定波幅 2。"""
    days = [add_trading_days(end, -(n - 1 - i)) for i in range(n)]
    return [
        {
            "price_date": day,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "adj_close": close,
            "volume": 1000,
            "split_factor": 1.0,
        }
        for day in days
    ]


def plan_row(ticker, **over):
    """一份 034 形状的已结束计划（缺省 expired），over 覆写任意字段。"""
    row = {
        "session": D0,
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
        "valid_from": D1,
        "valid_until": date(2026, 11, 9),
        "shares": None,
        "sizing": {},
        "warnings": [],
        "thesis": "论点",
        "invalidation": "作废条件",
        "counter": "",
        "refs": [],
        "agent_session": "retrospect-test",
        "status": "expired",
        "kind": "planned",
        "ended_session": date(2026, 11, 3),
        "end_reason": "5 个交易日未买入",
    }
    row.update(over)
    return row


def _run_row(run_id, session):
    return {
        "run_id": run_id,
        "session": session,
        "trigger": "scheduled",
        "status": "ok",
        "error": None,
        "market": {},
        "factors": {},
        "coverage": {},
        "holdings": [],
        "draft_md": "",
        "started_at": NOW,
        "finished_at": NOW,
    }


def _candidate(ticker, rank=1):
    return {
        "ticker": ticker,
        "rank": rank,
        "screen_group": "trigger",
        "trigger_rules": ["pullback"],
        "rs": 1.5,
        "atr_pct": 2.0,
        "setup_tier": "favorable",
        "industry": "半导体",
        "industry_tier": "favorable",
        "reason": f"{ticker} 的理由行",
    }


def test_empty_database_gives_empty_view(v3_tx):
    """Review Focus 6 / B5：空库不报错——records 空、empty 真、规则各组空、weekly 有周边界。"""
    view = retrospect_view(v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG)

    assert view["records"] == [] and view["empty"] is True
    assert view["rules"]["realized"] == {"trigger_rules": [], "setup_tier": [], "industry": []}
    assert view["rules"]["hypothetical"] == {"trigger_rules": [], "setup_tier": [], "industry": []}
    assert view["weekly"]["week_start"] == D1 and view["weekly"]["week_end"] == SESSION
    assert view["min_sample"] == CFG.v3.retro_min_sample and view["note"]


def test_records_join_candidate_groups_and_research_origin(v3_tx):
    """规则计划取保存当天候选行的分组键；研究计划记「用户研究」；没买的 planned 计划带假设结果。"""
    facts_repo.insert_run(v3_tx, _run_row("run-0", D0))
    facts_repo.insert_candidates(v3_tx, "run-0", [_candidate("AAPL")])
    data_repo.store_bars(v3_tx, "AAPL", bars(), now=NOW)
    data_repo.store_bars(v3_tx, "MSFT", bars(), now=NOW)
    plan_repo.insert_plan(v3_tx, plan_row("AAPL"))
    research = research_repo.insert_research(
        v3_tx,
        {
            "ticker": "MSFT",
            "thesis": "论点",
            "origin": "chat",
            "created_session": D0,
            "valid_until": date(2026, 11, 9),
            "agent_session": "retrospect-test",
        },
    )
    msft = plan_repo.insert_plan(v3_tx, plan_row("MSFT", research_id=research["id"], status="invalidated"))
    v3_tx.commit()

    rows = records(v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG)

    rule_row = next(row for row in rows if row["ticker"] == "AAPL")
    assert rule_row["origin_kind"] == "rule"
    assert rule_row["groups"] == {"trigger_rules": ["pullback"], "setup_tier": "favorable", "industry": "半导体"}
    assert rule_row["hypothetical"] is not None  # 没买的 planned 计划算「如果照做」
    research_row = next(row for row in rows if row["ticker"] == "MSFT")
    assert research_row["origin_kind"] == "research" and research_row["plan_id"] == msft["id"]
    assert research_row["groups"] == {"trigger_rules": ["用户研究"], "setup_tier": "用户研究", "industry": "用户研究"}
    assert [row["ticker"] for row in records(v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG, limit=1)] == ["MSFT"]


def test_weekly_view_equity_and_realized(v3_tx):
    """周初/周末总资产与本周已实现盈亏来自两个 as-of 账本快照（spec §5.4；周内成交不进周初快照）。"""
    plan_repo.insert_cash_flow(
        v3_tx,
        {
            "request_id": "open-1",
            "kind": "opening",
            "amount": D(10000),
            "effective_at": datetime(2026, 11, 1, tzinfo=UTC),
            "note": "",
        },
    )
    facts_repo.insert_trade(
        v3_tx,
        {
            "request_id": "buy-1",
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 10,
            "price": D(100),
            "executed_at": datetime(2026, 11, 2, 15, tzinfo=NY),
        },
    )
    facts_repo.insert_trade(
        v3_tx,
        {
            "request_id": "sell-1",
            "ticker": "AAPL",
            "side": "sell",
            "quantity": 10,
            "price": D(110),
            "executed_at": datetime(2026, 11, 4, 15, tzinfo=NY),
        },
    )
    data_repo.store_bars(v3_tx, "AAPL", bars(), now=NOW)
    plan_repo.insert_plan(v3_tx, plan_row("AAPL"))
    v3_tx.commit()

    weekly = weekly_view(v3_tx, week_end=SESSION, cutoff_at=NOW, cfg=CFG)

    assert weekly["week_start"] == D1 and weekly["week_end"] == SESSION
    assert weekly["equity_start"] == D(10000)  # 周初快照不含本周的成交
    assert weekly["equity_end"] == D(10100) and weekly["realized_pnl"] == D(100)
    assert weekly["ended"] == 1 and weekly["not_bought"] and weekly["not_bought"][0]["ticker"] == "AAPL"
