"""brief_view / today_actions over the v3_tx database (spec §7.4, §11): action order,
candidate skip reasons, change counts and the assembled view shape."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

from investment_assistant.facts.view import brief_view, today_actions

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo

SESSION = date(2026, 11, 4)  # 周三
PREV = date(2026, 11, 3)
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)
CFG = AssistantConfig()


def bars(n=1, *, end=SESSION, close=100.0):
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


def plan(ticker, **over):
    row = {
        "session": SESSION,
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
        "valid_from": SESSION,
        "valid_until": date(2026, 11, 10),
        "shares": 10,
        "sizing": {},
        "warnings": [],
        "thesis": "论点",
        "invalidation": "作废条件",
        "counter": "",
        "refs": [],
        "agent_session": "view-test",
        "status": "open",
        "kind": "planned",
        "start_session": PREV,
        "reviewed_through": None,
    }
    row.update(over)
    return row


def buy(conn, ticker, request_id):
    facts_repo.insert_trade(
        conn,
        {
            "request_id": request_id,
            "ticker": ticker,
            "side": "buy",
            "quantity": 10,
            "price": D(100),
            "executed_at": datetime(2026, 11, 3, 15, tzinfo=UTC),
        },
    )


def _candidate(ticker, rank, *, streak=None):
    detail = {} if streak is None else {"streak": streak}
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
        "detail": detail,
    }


def _run_row(run_id, session=SESSION):
    return {
        "run_id": run_id,
        "session": session,
        "trigger": "scheduled",
        "status": "ok",
        "error": None,
        "market": {"label": "neutral"},
        "factors": {},
        "coverage": {"families": {}, "missing_bars": []},
        "holdings": [],
        "draft_md": "",
        "started_at": NOW - timedelta(minutes=5),
        "finished_at": NOW,
    }


def run_view(candidates, *, dropped=None):
    return {
        "run_id": "run-1",
        "session": SESSION,
        "market": {"label": "neutral"},
        "coverage": {"families": {}, "missing_bars": []},
        "dropped": dropped if dropped is not None else [],
        "candidates": candidates,
    }


def test_today_actions_follow_section_7_4_order(v3_tx):
    """四条规则各一只持仓，顺序：止损已触及 → 下次开盘卖出 → 缺日线 → 未确认止损单。"""
    data_repo.store_bars(v3_tx, "AAA", bars(), now=NOW)
    data_repo.store_bars(v3_tx, "BBB", bars(), now=NOW)
    data_repo.store_bars(v3_tx, "CCC", bars(), now=NOW)
    data_repo.store_bars(v3_tx, "DDD", bars(end=PREV), now=NOW)  # DDD 没有 SESSION 的日线
    hit = plan_repo.insert_plan(v3_tx, plan("AAA", stop_hit_session=PREV))
    plan_repo.insert_plan(v3_tx, plan("BBB"))  # 未确认止损单
    plan_repo.insert_plan(v3_tx, plan("CCC", sell_instruction="max_hold"))
    plan_repo.insert_plan(v3_tx, plan("DDD"))
    for ticker in ("AAA", "BBB", "CCC", "DDD"):
        buy(v3_tx, ticker, f"{ticker}-buy")
    v3_tx.commit()

    actions = today_actions(v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG)

    assert [(row["ticker"], row["kind"]) for row in actions] == [
        ("AAA", "stop_hit_unresolved"),
        ("CCC", "sell_next_open"),
        ("DDD", "unchecked"),
        ("BBB", "stop_order"),
    ]
    assert actions[0]["text"] == "止损已触及，未见卖出记录"
    assert actions[1]["text"] == "下次开盘卖出" and actions[1]["reason"] == "持有到期"
    assert actions[2]["text"] == "缺日线，无法检查止损"
    assert actions[3]["text"] == "未确认止损单" and D(str(actions[3]["stop"])) == D(96)
    assert all(row["quantity"] == D(10) and row["plan_id"] for row in actions)
    assert hit["id"] == actions[0]["plan_id"]


def test_candidates_skip_reasons(v3_tx):
    """已有计划 → 「已有计划」；名额已满且不是替换候选 → 「名额已满」；替换候选不带原因。"""
    facts_repo.insert_run(v3_tx, _run_row("run-1"))
    facts_repo.insert_candidates(
        v3_tx, "run-1", [_candidate(t, i + 1) for i, t in enumerate(["AAPL", "MSFT", "NVDA", "MU", "AVGO"])]
    )
    v3_tx.commit()

    # 场景一：名次 1–3 都有计划，替换条件不成立 → 没有计划的都写「名额已满」
    for ticker in ("AAPL", "MSFT", "NVDA"):
        plan_repo.insert_plan(v3_tx, plan(ticker, status="pending", start_session=None))
    v3_tx.commit()
    full = brief_view(
        v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run_view(facts_repo.candidates(v3_tx, "run-1"))
    )
    by_ticker = {row["ticker"]: row for row in full["candidates"]}
    assert by_ticker["AAPL"]["skip_reason"] == "已有计划"
    assert by_ticker["AAPL"]["has_active_plan"] is True and by_ticker["AAPL"]["plan_id"] is not None
    assert by_ticker["MU"]["skip_reason"] == "名额已满"
    assert by_ticker["AVGO"]["skip_reason"] == "名额已满"

    # 场景二：换成 MSFT(2)、NVDA(3)、MU(4) 占满 → 替换候选 AAPL(1) 名次更优，只它不带原因
    v3_tx.execute("DELETE FROM v3_plans WHERE ticker = 'AAPL'")
    plan_repo.insert_plan(v3_tx, plan("MU", status="pending", start_session=None))
    v3_tx.commit()
    replaceable = brief_view(
        v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run_view(facts_repo.candidates(v3_tx, "run-1"))
    )
    again = {row["ticker"]: row for row in replaceable["candidates"]}
    assert again["AAPL"]["skip_reason"] is None  # 替换候选：照常进入二次确认
    assert again["MSFT"]["skip_reason"] == "已有计划"
    assert again["AVGO"]["skip_reason"] == "名额已满"


def test_change_counts_from_continuity(v3_tx):
    """昨天与今天各一份名单：新上榜、掉出的计数进 changes；首次运行写「首次运行」。"""
    facts_repo.insert_run(v3_tx, _run_row("run-0", PREV))
    facts_repo.insert_candidates(v3_tx, "run-0", [_candidate("AAPL", 1, streak=1), _candidate("MSFT", 2, streak=3)])
    facts_repo.insert_run(v3_tx, _run_row("run-1", SESSION))
    today = [_candidate("AAPL", 1), _candidate("NVDA", 2)]
    facts_repo.insert_candidates(v3_tx, "run-1", today)
    v3_tx.commit()

    view = brief_view(
        v3_tx,
        session=SESSION,
        cutoff_at=NOW,
        cfg=CFG,
        run=run_view(today, dropped=[{"ticker": "MSFT", "reasons": ["rank_cut"]}]),
    )
    assert view["first_run"] is False
    assert view["changes"]["new_count"] == 1 and view["changes"]["dropped_count"] == 1
    assert view["dropped"] == [{"ticker": "MSFT", "reasons": ["rank_cut"]}]
    streaks = {row["ticker"]: row["streak"] for row in view["candidates"]}
    assert streaks == {"AAPL": 2, "NVDA": 1}
    assert [row["ticker"] for row in view["candidates"] if row["new"]] == ["NVDA"]

    empty = brief_view(v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run_view([], dropped=[]))
    assert empty["first_run"] is False  # run-0 在，不是首次


def test_view_holds_pending_plans_and_account(v3_tx):
    """待入场计划行与账户条来自计划线与账本；一句话与页脚读当天的简报行。"""
    facts_repo.insert_run(v3_tx, _run_row("run-1"))
    plan_repo.insert_plan(v3_tx, plan("AAPL", status="pending", start_session=None))
    buy(v3_tx, "AAPL", "a-buy")
    data_repo.store_bars(v3_tx, "AAPL", bars(), now=NOW)
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
    plan_repo.upsert_brief(
        v3_tx,
        {
            "session": SESSION,
            "status": "final",
            "summary": "没有新机会，继续持有。",
            "runtime": "pi",
            "provider": "p1",
            "model": "m1",
            "usage": {"input": 10, "output": 5},
            "elapsed_seconds": 60,
        },
    )
    v3_tx.commit()

    view = brief_view(v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run_view([]))

    assert view["pending_plans"] and view["pending_plans"][0]["ticker"] == "AAPL"
    assert view["pending_plans"][0]["shares"] == 10
    assert D(str(view["account"]["cash"])) == D(9000)  # 10000 − 10×100
    assert view["summary"] == "没有新机会，继续持有。"
    assert view["footer"]["model"] == "m1" and view["footer"]["usage"]["input"] == 10
    assert view["error"] is None and view["session"] == SESSION
    assert [row["ticker"] for row in view["holdings"]] == ["AAPL"]  # 持仓行来自成交
    assert view["holdings"][0]["kind"] == "planned"  # 计划还是 pending，只是持仓在
