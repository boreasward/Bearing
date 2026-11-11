"""Host plan review over the v3_tx database (spec §5 补查, §6.3, §7.1, §7.4): reason events,
idempotent reruns, catch-up on missed sessions, missing bars, adopted levels, splits."""

from datetime import UTC, date, datetime
from decimal import Decimal as D

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.execution.plan_line import close_outcome
from investment_assistant.execution.positions import build
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
    """Review Focus 1 / A7：缺日线的持仓只标「无法检查止损」，其他持仓照常检查；
    待入场计划同样不推进复查，日线补上后还能查收盘跌破止损。"""
    data_repo.store_bars(v3_tx, "AAA", bars(20), now=NOW)
    data_repo.store_bars(v3_tx, "BBB", bars(20, end=D2), now=NOW)  # BBB 没有 SESSION 的日线
    data_repo.store_bars(v3_tx, "CCC", bars(20, end=D2), now=NOW)  # CCC（待入场）同样缺 SESSION 日线
    plan_repo.insert_plan(v3_tx, plan_row("AAA", status="open", start_session=D0))
    plan_repo.insert_plan(v3_tx, plan_row("BBB", status="open", start_session=D0))
    ccc = plan_repo.insert_plan(v3_tx, plan_row("CCC"))  # pending：valid_from=D1
    buy(v3_tx, "AAA", "a-buy")
    buy(v3_tx, "BBB", "b-buy")
    v3_tx.commit()

    result = review(v3_tx)

    assert result["unchecked"] == ["BBB"]
    assert active(v3_tx, "AAA")["reviewed_through"] == SESSION
    assert active(v3_tx, "BBB")["reviewed_through"] == D2  # 缺日线那天不推进
    assert active(v3_tx, "CCC")["reviewed_through"] == D2  # 待入场同样不推进（spec §6.3）
    # 日线补上后重跑：CCC 的复查继续，不会永久漏掉收盘跌破止损
    data_repo.store_bars(v3_tx, "CCC", bars(1, end=SESSION, close=90.0, low=89.0), now=NOW)
    v3_tx.commit()
    review(v3_tx)
    ended = plan_repo.plan(v3_tx, ccc["id"])
    assert ended is not None and ended["status"] == "invalidated"
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


def test_split_folds_trailing_window_no_false_stop_hit(v3_tx):
    """拆股后移动止损的窗口按当日口径折算：止损不会跳到现价上方，次日不产生假 stop_hit。"""
    data_repo.store_bars(
        v3_tx,
        "AAA",
        [
            dict(bars(1, end=D0)[0], open=100.0, high=101.0, low=99.0, close=100.0),
            dict(bars(1, end=D1)[0], open=110.0, high=111.0, low=109.0, close=110.0),
            dict(bars(1, end=D2)[0], open=58.0, high=59.0, low=57.0, close=58.0, split_factor=2.0),
            dict(bars(1, end=SESSION)[0], open=59.0, high=60.0, low=56.0, close=59.0),
        ],
        now=NOW,
    )
    plan = plan_repo.insert_plan(v3_tx, plan_row("AAA", status="open", start_session=D0, shares=100))
    buy(v3_tx, "AAA", "a-buy")
    v3_tx.commit()

    review(v3_tx)

    saved = saved_plan(v3_tx, plan["id"])
    raises = [
        (row["session"], D(str(row["after"]["stop"])))
        for row in plan_repo.events(v3_tx, plan["id"])
        if row["event"] == "stop_raised"
    ]
    assert raises == [(D1, D(104)), (D2, D(55)), (SESSION, D(56))]  # D1：110−3×2；D2/D4：折算后高点 58/59 − 3×1
    assert saved["stop"] == D(56)  # 全程低于当日现价（58/59）
    assert events_of(v3_tx, plan["id"], "stop_hit") == []  # 未折算的高点 110 会把止损抬到 107 → 假触及


def test_split_adjusts_initial_stop_for_r_multiple(v3_tx):
    """拆股同步调整 initial_stop（R6）：平仓结果的 r 倍数与拆股前等价（买 100@50、止损 46、
    2 拆 1、卖 200@29 ≈ 拆股前买 100@50 卖 100@58 的 2.0 倍）。"""
    data_repo.store_bars(
        v3_tx,
        "AAA",
        bars(3, end=D2, close=50.0) + bars(1, end=SESSION, close=25.0, high=26.0, low=24.0, split=2.0),
        now=NOW,
    )
    plan = plan_repo.insert_plan(
        v3_tx,
        plan_row(
            "AAA",
            status="open",
            start_session=D0,
            entry_low=D(49),
            entry_high=D(51),
            stop=D(46),
            initial_stop=D(46),
            target1=D(60),
            shares=100,
        ),
    )
    facts_repo.insert_trade(
        v3_tx,
        {
            "request_id": "r-buy",
            "ticker": "AAA",
            "side": "buy",
            "quantity": 100,
            "price": D(50),
            "executed_at": datetime(2026, 10, 30, 15, tzinfo=UTC),
        },
    )
    v3_tx.commit()

    review(v3_tx)

    saved = saved_plan(v3_tx, plan["id"])
    assert saved["initial_stop"] == D(23) and saved["stop"] == D(23)  # 46 / 2
    facts_repo.insert_trade(
        v3_tx,
        {
            "request_id": "r-sell",
            "ticker": "AAA",
            "side": "sell",
            "quantity": 200,
            "price": D(29),
            "executed_at": datetime(2026, 11, 4, 15, tzinfo=UTC),
        },
    )
    v3_tx.commit()
    trades = facts_repo.list_trades(v3_tx, "AAA")
    built = build(trades, splits=data_repo.split_events(v3_tx, "AAA", cutoff_at=NOW))
    outcome = close_outcome(
        plan=saved_plan(v3_tx, plan["id"]), closed_lots=built["closed"], trades=trades, sessions_since_stop_hit=None
    )
    assert D(str(outcome["realized_pnl"])) == D(800)  # 200 × (29 − 25)
    assert outcome["r_multiple"] == D("2.00")  # (800/200) / (25 − 23)


def test_one_plans_failure_does_not_crash_the_run(v3_tx, monkeypatch):
    """一份计划的毒数据只回滚它自己（SAVEPOINT）：其余计划照常复查，运行不中断。"""
    data_repo.store_bars(v3_tx, "AAA", bars(20), now=NOW)
    data_repo.store_bars(v3_tx, "BBB", bars(20), now=NOW)
    plan_repo.insert_plan(v3_tx, plan_row("AAA", status="open", start_session=D0))
    poisoned = plan_repo.insert_plan(v3_tx, plan_row("BBB", status="open", start_session=D0))
    buy(v3_tx, "AAA", "a-buy")
    buy(v3_tx, "BBB", "b-buy")
    v3_tx.commit()
    original = facts_review._review_one

    def poison(conn, plan, **kwargs):
        if plan["ticker"] == "BBB":
            raise RuntimeError("毒数据：无法核算")
        original(conn, plan, **kwargs)

    monkeypatch.setattr(facts_review, "_review_one", poison)

    result = review(v3_tx)

    assert [entry["ticker"] for entry in result["errors"]] == ["BBB"]
    assert result["errors"][0]["plan_id"] == poisoned["id"]
    assert "毒数据" in result["errors"][0]["error"]
    assert active(v3_tx, "AAA")["reviewed_through"] == SESSION  # 好计划照常复查
    untouched = plan_repo.plan(v3_tx, poisoned["id"])  # 坏计划的写回全部回滚
    assert untouched is not None and untouched["reviewed_through"] is None
    assert result["unchecked"] == [] and result["changes"] == []
    # 事务仍可用：失败之后别的写入照常提交
    plan_repo.update_plan(v3_tx, poisoned["id"], reviewed_through=SESSION)
    v3_tx.commit()
    after = plan_repo.plan(v3_tx, poisoned["id"])
    assert after is not None and after["reviewed_through"] == SESSION


# ---- v3.3：换仓指令的取代与撤回（spec §6.6；验收 C8、C9、C17）----
# 库约束 v3_plans_rotation_check：sell_instruction='rotation' ⇔ rotation_id ⇔ sell_quantity，
# 所以取代 / 撤回时三列必须在同一条 UPDATE 里一起改。


def _instructed(conn, *, max_hold=20, bars_override=None):
    """AAA（open，复查已推进到前一日）带 150 股换仓指令，BBB 的计划待入场；返回 (A, 换仓记录, B)。"""
    from _rotation_seed import add_holding, instruct, seed_run

    seed_run(conn, ("BBB",))
    held = add_holding(
        conn, "AAA", 301, "39.5", 40.0, with_bars=bars_override is None, max_hold_sessions=max_hold,
        valid_until=date(2026, 11, 20),
    )  # fmt: skip
    if bars_override is not None:
        data_repo.store_bars(conn, "AAA", bars_override, now=NOW)
    rotation, funded = instruct(conn, held, valid_until=date(2026, 11, 20))
    conn.commit()
    return held, rotation, funded


def _cleared(plan, instruction):
    return (plan["sell_instruction"], plan["sell_quantity"], plan["rotation_id"]) == (instruction, None, None)


def test_max_hold_supersedes_rotation(v3_tx):
    """C9：持有到期（起算日 10-28、已持有 5 个交易日、最长持有 3）→ sell_instruction 变 max_hold，
    sell_quantity / rotation_id 同一条 UPDATE 清空，留 rotation_superseded 一行。"""
    held, rotation, _ = _instructed(v3_tx, max_hold=3)

    result = review(v3_tx)

    plan = saved_plan(v3_tx, held["id"])
    assert _cleared(plan, "max_hold")
    superseded = events_of(v3_tx, held["id"], "rotation_superseded")
    assert [row["reason"] for row in superseded] == ["换仓指令被取代：持有到期"]
    assert superseded[0]["before"] == {"rotation_id": rotation["id"], "sell_quantity": "150"}
    assert [row["reason"] for row in events_of(v3_tx, held["id"], "sell_instruction")] == ["持有到期"]
    assert {"rotation_superseded", "sell_instruction"} <= {row["event"] for row in result["changes"]}
    assert events_of(v3_tx, held["id"], "rotation_withdrawn") == []  # 取代了就不再算孤儿


def test_stop_hit_supersedes_rotation(v3_tx):
    """C9：最低价触及止损 → 止损按原规则处理，换仓指令被取代（sell_instruction 清空，没有持有到期）。"""
    from _rotation_seed import bars as seed_bars

    day = seed_bars(80, end=SESSION, close=40.0)
    day[-1] = {**day[-1], "low": 35.0}  # 止损 36
    held, rotation, _ = _instructed(v3_tx, bars_override=day)

    review(v3_tx)

    plan = saved_plan(v3_tx, held["id"])
    assert plan["stop_hit_session"] == SESSION and _cleared(plan, None)
    assert [row["reason"] for row in events_of(v3_tx, held["id"], "rotation_superseded")] == [
        "换仓指令被取代：止损触及"
    ]
    assert len(events_of(v3_tx, held["id"], "stop_hit")) == 1
    assert rotation["id"]


def test_stop_hit_and_max_hold_same_day(v3_tx):
    """止损触及与持有到期同日成立：原因记「止损触及」，sell_instruction 为 max_hold，三列照样清空。"""
    from _rotation_seed import bars as seed_bars

    day = seed_bars(80, end=SESSION, close=40.0)
    day[-1] = {**day[-1], "low": 35.0}
    held, _, _ = _instructed(v3_tx, max_hold=3, bars_override=day)

    review(v3_tx)

    plan = saved_plan(v3_tx, held["id"])
    assert _cleared(plan, "max_hold")
    assert [row["reason"] for row in events_of(v3_tx, held["id"], "rotation_superseded")] == [
        "换仓指令被取代：止损触及"
    ]


def test_review_withdraws_when_candidate_plan_expires(v3_tx):
    """C8：同一次 review_plans 里 B 的待入场计划过期，A 的换仓指令随即撤回（三列一起清空）。"""
    from _rotation_seed import add_holding, instruct, seed_run

    seed_run(v3_tx, ("BBB",))
    held = add_holding(v3_tx, "AAA", 301, "39.5", 40.0)
    rotation, funded = instruct(v3_tx, held)  # 默认 B：有效期到 11-02，当天早已过期
    v3_tx.commit()

    result = review(v3_tx)

    assert saved_plan(v3_tx, funded["id"])["status"] == "expired"
    plan = saved_plan(v3_tx, held["id"])
    assert _cleared(plan, None) and plan["status"] == "open"
    withdrawn = events_of(v3_tx, held["id"], "rotation_withdrawn")
    assert [row["reason"] for row in withdrawn] == ["换仓指令撤回：BBB 的计划已过期"]
    assert withdrawn[0]["before"] == {"rotation_id": rotation["id"], "sell_quantity": "150"}
    assert {row["event"] for row in result["changes"]} >= {"expired", "rotation_withdrawn"}


def test_rerun_same_session_is_idempotent(v3_tx):
    """C17：同一个交易日再跑一次复查，不产生第二条撤回 / 取代事件。"""
    from _rotation_seed import add_holding, instruct, seed_run

    seed_run(v3_tx, ("BBB", "EEE"))
    stale = add_holding(v3_tx, "AAA", 301, "39.5", 40.0)
    instruct(v3_tx, stale)  # B 过期 → 撤回
    superseded = add_holding(v3_tx, "CCC", 100, "39.5", 40.0, max_hold_sessions=3, valid_until=date(2026, 11, 20))
    instruct(v3_tx, superseded, candidate="EEE", valid_until=date(2026, 11, 20))  # 持有到期 → 取代
    v3_tx.commit()

    review(v3_tx)
    second = review(v3_tx)

    assert len(events_of(v3_tx, stale["id"], "rotation_withdrawn")) == 1
    assert len(events_of(v3_tx, superseded["id"], "rotation_superseded")) == 1
    assert [row for row in second["changes"] if row["event"].startswith("rotation_")] == []
