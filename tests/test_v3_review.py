"""Host plan review over the v3_tx database (spec §5 补查, §6.3, §7.1, §7.4): reason events,
idempotent reruns, catch-up on missed sessions, missing bars, adopted levels, splits."""

from datetime import UTC, date, datetime
from decimal import Decimal as D

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts import review as facts_review
from investment_assistant.facts.view import today_actions

SESSION = date(2026, 11, 4)  # 周三
D2 = date(2026, 11, 3)
D1 = date(2026, 11, 2)
D0 = date(2026, 10, 30)  # 周五：计划建立日
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)
CFG = AssistantConfig()


def bars(n=20, *, end=SESSION, close=100.0, low=None, high=None, split=1.0):
    """n 根连续交易日日线（最后一根是 end），恒定波幅 2，末根可覆写高低收与拆股比例。"""
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
            "split_factor": split,
        }
        for day in days
    ]


def plan_row(ticker, **over):
    """一份 034 形状的 v3_plans 行；测试用 over 覆写任意字段。"""
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
        "agent_session": "review-test",
        "status": "pending",
        "kind": "planned",
        "start_session": None,
        "reviewed_through": None,
    }
    row.update(over)
    return row


def buy(conn, ticker, request_id, *, quantity=10, price=D(100)):
    facts_repo.insert_trade(
        conn,
        {
            "request_id": request_id,
            "ticker": ticker,
            "side": "buy",
            "quantity": quantity,
            "price": price,
            "executed_at": datetime(2026, 10, 30, 15, tzinfo=UTC),
        },
    )


def review(conn, session=SESSION):
    return facts_review.review_plans(conn, session=session, cutoff_at=NOW, cfg=CFG)


def events_of(conn, plan_id, event):
    return [row for row in plan_repo.events(conn, plan_id) if row["event"] == event]


def saved_plan(conn, plan_id):
    row = plan_repo.plan(conn, plan_id)
    assert row is not None
    return row


def active(conn, ticker):
    row = plan_repo.active_plan(conn, ticker)
    assert row is not None
    return row


def test_pending_three_endings_leave_reason_events(v3_tx):
    """A3：过期、收盘跌破止损、财报进入有效期各留一行带原因的变化记录。"""
    data_repo.store_bars(v3_tx, "AAA", bars(19, end=D2) + bars(1, end=SESSION, close=90.0, low=89.0), now=NOW)
    data_repo.store_bars(v3_tx, "BBB", bars(20), now=NOW)
    data_repo.store_bars(v3_tx, "CCC", bars(20), now=NOW)
    data_repo.store_earnings(v3_tx, "BBB", {"status": "known", "earnings_date": SESSION}, now=NOW)
    below_stop = plan_repo.insert_plan(v3_tx, plan_row("AAA"))
    earnings_hit = plan_repo.insert_plan(v3_tx, plan_row("BBB"))
    expired = plan_repo.insert_plan(
        v3_tx,
        plan_row("CCC", session=date(2026, 10, 23), valid_from=date(2026, 10, 26), valid_until=date(2026, 10, 30)),
    )
    v3_tx.commit()

    result = review(v3_tx)

    invalidated = saved_plan(v3_tx, below_stop["id"])
    assert invalidated["status"] == "invalidated" and invalidated["ended_session"] == SESSION
    reason = events_of(v3_tx, below_stop["id"], "invalidated")[0]["reason"]
    assert "90" in reason and "96" in reason and "未买入" in reason

    earnings_plan = saved_plan(v3_tx, earnings_hit["id"])
    assert earnings_plan["status"] == "invalidated"
    assert events_of(v3_tx, earnings_hit["id"], "invalidated")[0]["reason"] == "财报进入有效期"

    expired_plan = saved_plan(v3_tx, expired["id"])
    assert expired_plan["status"] == "expired" and expired_plan["ended_session"] == date(2026, 10, 30)
    assert "未买入" in events_of(v3_tx, expired["id"], "expired")[0]["reason"]
    assert len(result["changes"]) == 3 and result["unchecked"] == []


def test_rerun_same_session_no_duplicate_events(v3_tx):
    """A16：同一交易日重跑不产生重复事件，计划字段不再变化。"""
    data_repo.store_bars(
        v3_tx,
        "AAA",
        bars(19, end=D2)
        + [
            dict(
                bars(1, end=D1, close=110.0, high=111.0)[0],
                price_date=D2,
                open=110.0,
                high=121.0,
                low=109.0,
                close=120.0,
            )
        ]
        + bars(1, end=SESSION, close=130.0, high=131.0, low=129.0),
        now=NOW,
    )
    plan = plan_repo.insert_plan(v3_tx, plan_row("AAA", status="open", start_session=D0))
    buy(v3_tx, "AAA", "a-buy")
    v3_tx.commit()

    first = review(v3_tx)
    plan_after_first = saved_plan(v3_tx, plan["id"])
    counts_first = [(row["session"], row["event"]) for row in plan_repo.events(v3_tx, plan["id"])]

    second = review(v3_tx)

    assert second["changes"] == []
    assert [(row["session"], row["event"]) for row in plan_repo.events(v3_tx, plan["id"])] == counts_first
    assert saved_plan(v3_tx, plan["id"]) == plan_after_first
    assert plan_after_first["stop"] == D(124)  # 130 − 3×2，止损只上不下
    assert {(row["event"], row["session"]) for row in plan_repo.events(v3_tx, plan["id"])} >= {
        ("target_hit", D2),
        ("stop_raised", D2),
        ("stop_raised", SESSION),
    }
    assert first["unchecked"] == []


def test_catch_up_finds_stop_hit_on_missed_session(v3_tx):
    """A21：reviewed_through 之后漏跑的交易日里触及止损，补查出来且事件日期是它实际发生的交易日。"""
    data_repo.store_bars(
        v3_tx,
        "AAA",
        [
            dict(bars(1, end=D0)[0], open=105.0, high=106.0, low=104.0, close=105.0),
            dict(bars(1, end=D1)[0], open=104.0, high=105.0, low=103.0, close=104.0),
            dict(bars(1, end=D2)[0], open=103.0, high=104.0, low=90.0, close=102.0),
            dict(bars(1, end=SESSION)[0], open=101.0, high=102.0, low=100.0, close=101.0),
        ],
        now=NOW,
    )
    plan = plan_repo.insert_plan(v3_tx, plan_row("AAA", status="open", start_session=D0, reviewed_through=D1))
    buy(v3_tx, "AAA", "a-buy")
    v3_tx.commit()

    review(v3_tx)

    hits = events_of(v3_tx, plan["id"], "stop_hit")
    assert [row["session"] for row in hits] == [D2]  # D2 的低点 90 穿止损 96
    saved = saved_plan(v3_tx, plan["id"])
    assert saved["stop_hit_session"] == D2 and saved["reviewed_through"] == SESSION


def test_missing_bar_marks_holding_unchecked(v3_tx):
    """Review Focus 1 / A7：缺日线的持仓只标「无法检查止损」，其他持仓照常检查。"""
    data_repo.store_bars(v3_tx, "AAA", bars(20), now=NOW)
    data_repo.store_bars(v3_tx, "BBB", bars(20, end=D2), now=NOW)  # BBB 没有 SESSION 的日线
    plan_repo.insert_plan(v3_tx, plan_row("AAA", status="open", start_session=D0))
    plan_repo.insert_plan(v3_tx, plan_row("BBB", status="open", start_session=D0))
    buy(v3_tx, "AAA", "a-buy")
    buy(v3_tx, "BBB", "b-buy")
    v3_tx.commit()

    result = review(v3_tx)

    assert result["unchecked"] == ["BBB"]
    assert active(v3_tx, "AAA")["reviewed_through"] == SESSION
    assert active(v3_tx, "BBB")["reviewed_through"] == D2  # 缺日线那天不推进
    actions = today_actions(v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG)
    assert [(row["ticker"], row["kind"]) for row in actions] == [("BBB", "unchecked"), ("AAA", "stop_order")]
    assert actions[0]["text"] == "缺日线，无法检查止损"
    assert actions[1]["text"] == "未确认止损单"


def test_adopted_plan_gets_levels_when_bars_arrive(v3_tx):
    """A11 后半：补录计划缺价位，有日线的那天按 rule_levels 补齐四个价位与 ATR，记 stop_set。"""
    data_repo.store_bars(v3_tx, "AAA", bars(20), now=NOW)
    plan = plan_repo.insert_plan(
        v3_tx,
        plan_row(
            "AAA",
            status="open",
            start_session=SESSION,
            stop=None,
            entry_low=None,
            entry_high=None,
            target1=None,
            initial_stop=None,
            atr=None,
            kind="adopted",
            thesis="补录持仓，没有入场论点",
        ),
    )
    v3_tx.commit()

    review(v3_tx)

    saved = saved_plan(v3_tx, plan["id"])
    assert saved["stop"] == D(96) and saved["entry_low"] == D(99) and saved["entry_high"] == D(101)
    assert saved["target1"] == D(108) and saved["atr"] == D(2)
    stop_set = events_of(v3_tx, plan["id"], "stop_set")
    assert len(stop_set) == 1 and stop_set[0]["session"] == SESSION
    assert "止损" in stop_set[0]["reason"]


def test_split_adjusts_levels_with_event(v3_tx):
    """A20：拆股后有效计划的价位按比例调整并留 split_adjusted 一行。"""
    data_repo.store_bars(
        v3_tx,
        "AAA",
        bars(19, end=D2, close=100.0) + bars(1, end=SESSION, close=50.0, high=51.0, low=49.0, split=2.0),
        now=NOW,
    )
    plan = plan_repo.insert_plan(
        v3_tx, plan_row("AAA", status="open", start_session=D0, reviewed_through=D2, shares=100)
    )
    v3_tx.commit()

    review(v3_tx)

    saved = saved_plan(v3_tx, plan["id"])
    assert saved["entry_low"] == D("49.50") and saved["entry_high"] == D("50.50")
    assert saved["stop"] == D(48) and saved["target1"] == D(54)
    assert saved["atr"] == D(1) and saved["shares"] == 200
    adjusted = events_of(v3_tx, plan["id"], "split_adjusted")
    assert len(adjusted) == 1 and adjusted[0]["session"] == SESSION
    assert D(str(adjusted[0]["after"]["stop"])) == D(48)


def test_stop_hit_persists_until_sold(v3_tx):
    """A6 前半：触及后没有卖出记录，第二天再跑 today_actions 第一条仍是止损已触及。"""
    data_repo.store_bars(
        v3_tx,
        "AAA",
        [
            dict(bars(1, end=D0)[0], open=105.0, high=106.0, low=104.0, close=105.0),
            dict(bars(1, end=D1)[0], open=104.0, high=105.0, low=90.0, close=101.0),
            dict(bars(1, end=D2)[0], open=101.0, high=102.0, low=99.0, close=101.0),
        ],
        now=NOW,
    )
    plan = plan_repo.insert_plan(v3_tx, plan_row("AAA", status="open", start_session=D0))
    buy(v3_tx, "AAA", "a-buy")
    v3_tx.commit()

    review(v3_tx, session=D1)
    review(v3_tx, session=D2)

    saved = saved_plan(v3_tx, plan["id"])
    assert saved["stop_hit_session"] == D1
    assert len(events_of(v3_tx, plan["id"], "stop_hit")) == 1  # D2 不再重复记
    actions = today_actions(v3_tx, session=D2, cutoff_at=NOW, cfg=CFG)
    assert actions and actions[0]["kind"] == "stop_hit_unresolved"
    assert actions[0]["text"] == "止损已触及，未见卖出记录"
