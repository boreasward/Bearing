"""Retrospect view over the v3_tx database (spec §5): ended-plan records joined with
candidates and research origin, the weekly summary's account snapshots, the empty view."""

from datetime import UTC, date, datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.data.calendar import add_trading_days, trading_days_between
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


def _bar(day, close, *, split=1.0):
    return {
        "price_date": day,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "adj_close": close,
        "volume": 1000,
        "split_factor": split,
    }


def test_weekly_view_splits_truncated_per_snapshot(v3_tx):
    """P1-5：周内拆股不回灌进周初快照——周初 10 股按拆股前收盘估值，期末按拆股后股数与价。"""
    plan_repo.insert_cash_flow(
        v3_tx,
        {
            "request_id": "open-1",
            "kind": "opening",
            "amount": D(20000),
            "effective_at": datetime(2026, 10, 20, tzinfo=UTC),
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
            "executed_at": datetime(2026, 10, 28, 15, tzinfo=NY),
        },
    )
    pre = [_bar(day, 100.0) for day in trading_days_between(date(2026, 10, 26), date(2026, 11, 3))]
    split_day = _bar(SESSION, 50.0, split=2.0)  # 周三 2:1 拆股，收盘价折半
    data_repo.store_bars(v3_tx, "AAPL", pre + [split_day], now=NOW)
    v3_tx.commit()

    weekly = weekly_view(v3_tx, week_end=SESSION, cutoff_at=NOW, cfg=CFG)

    # 周初（11/1 前的快照）：10 股 × 拆股前收盘 100 + 现金 19000 = 20000；拆股不该把它变成 20 股 × 100
    assert weekly["equity_start"] == D(20000)
    # 周末：拆股生效，20 股 × 50 + 现金 19000 = 20000
    assert weekly["equity_end"] == D(20000)
    assert weekly["realized_pnl"] == D(0)


def test_weekly_start_snapshot_includes_holiday_monday_flow(v3_tx):
    """P1-6：周一休市（2026-02-16 总统日）时本周首个实际交易日是周二——周一生效的期初入金
    属于「本周之前」，要进周初快照；周报的日历周边界不变。"""
    FRI = date(2026, 2, 13)
    HOLIDAY_MON = date(2026, 2, 16)  # NYSE 休市
    TUE = date(2026, 2, 17)
    WED = date(2026, 2, 18)  # 本周最后一个交易日
    FEB_NOW = datetime(2026, 2, 19, 3, 30, tzinfo=UTC)
    plan_repo.insert_cash_flow(
        v3_tx,
        {
            "request_id": "open-1",
            "kind": "opening",
            "amount": D(10000),
            "effective_at": datetime(2026, 2, 16, 12, tzinfo=NY),  # 休市的周一生效
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
            "executed_at": datetime(2026, 2, 17, 15, tzinfo=NY),  # 本周首个交易日买入
        },
    )
    data_repo.store_bars(
        v3_tx, "AAPL", [_bar(day, 100.0) for day in [FRI, TUE, WED]], now=FEB_NOW
    )
    v3_tx.commit()

    weekly = weekly_view(v3_tx, week_end=WED, cutoff_at=FEB_NOW, cfg=CFG)

    assert weekly["week_start"] == HOLIDAY_MON and weekly["week_end"] == WED  # 日历周边界不变
    assert weekly["equity_start"] == D(10000)  # 休市周一的入金进周初快照（修复前 through=周日会漏掉）
    assert weekly["equity_end"] == D(10000)  # 9000 现金 + 10 股 × 100
