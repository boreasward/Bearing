"""Plan-line pure functions: pending review, holding checks, trailing stop, replacement, outcomes."""

from datetime import date, datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from investment_assistant.execution import plan_line, positions

NY = ZoneInfo("America/New_York")


def _pending_plan(**over):
    row = {
        "id": 7,
        "ticker": "AAPL",
        "status": "pending",
        "kind": "planned",
        "entry_low": D("31.80"),
        "entry_high": D("32.10"),
        "stop": D("31.20"),
        "target1": D("34.00"),
        "initial_stop": D("31.20"),
        "atr": D("1.00"),
        "trailing": False,
        "valid_from": date(2026, 11, 3),
        "valid_until": date(2026, 11, 9),
        "session": date(2026, 11, 2),
    }
    row.update(over)
    return row


def _open_plan(**over):
    row = {
        "id": 8,
        "ticker": "AAPL",
        "status": "open",
        "kind": "planned",
        "entry_low": D(96),
        "entry_high": D(104),
        "stop": D(100),
        "target1": D(130),
        "initial_stop": D(100),
        "atr": D(2),
        "trailing": False,
        "confirmed_stop_order": None,
        "sell_instruction": None,
        "start_session": date(2026, 11, 2),
        "max_hold_sessions": 20,
        "stop_hit_session": None,
    }
    row.update(over)
    return row


def _bar(close, low=None, high=None):
    return {
        "close": D(str(close)),
        "low": D(str(close if low is None else low)),
        "high": D(str(close if high is None else high)),
    }


def test_pending_invalidated_on_close_below_stop():
    result = plan_line.pending_check(
        plan=_pending_plan(), bar=_bar("30.95", low="30.80", high="31.50"), earnings=None, session=date(2026, 11, 3)
    )
    assert result is not None
    assert result["event"] == "invalidated"
    assert "30.95" in result["reason"] and "31.20" in result["reason"]
    assert result["reason"] == "收盘 30.95 跌破止损 31.20，未买入"


def test_pending_expired_on_valid_until():
    result = plan_line.pending_check(plan=_pending_plan(), bar=_bar("32.00"), earnings=None, session=date(2026, 11, 9))
    assert result == {"event": "expired", "reason": "5 个交易日未买入"}


def test_pending_earnings_in_validity_invalidates():
    earnings = {"status": "known", "earnings_date": date(2026, 11, 5)}
    result = plan_line.pending_check(
        plan=_pending_plan(), bar=_bar("32.00"), earnings=earnings, session=date(2026, 11, 3)
    )
    assert result == {"event": "invalidated", "reason": "财报进入有效期"}
    # 顺序：收盘跌破止损优先于财报（spec §6.3）
    both = plan_line.pending_check(
        plan=_pending_plan(), bar=_bar("30.95"), earnings=earnings, session=date(2026, 11, 3)
    )
    assert both is not None
    assert both["reason"] == "收盘 30.95 跌破止损 31.20，未买入"
    # 缺日线时跳过第 1 条，财报照查
    no_bar = plan_line.pending_check(plan=_pending_plan(), bar=None, earnings=earnings, session=date(2026, 11, 3))
    assert no_bar == {"event": "invalidated", "reason": "财报进入有效期"}


def test_pending_missing_bar_only_checks_expiry():
    result = plan_line.pending_check(plan=_pending_plan(), bar=None, earnings=None, session=date(2026, 11, 3))
    assert result is None


def test_stop_hit_uses_close_on_start_session_and_low_after():
    plan = _open_plan()
    start = plan_line.holding_check(
        plan=plan,
        bar=_bar("101", low="99", high="103"),
        session=date(2026, 11, 2),
        highest_close=D(101),
        avg_cost=D(105),
        held_sessions=0,
        trail_atr=D(3),
    )
    assert start["events"] == []  # 起算日只看收盘：low 99 不算触及
    after = plan_line.holding_check(
        plan=plan,
        bar=_bar("102", low="99", high="104"),
        session=date(2026, 11, 3),
        highest_close=D(102),
        avg_cost=D(105),
        held_sessions=1,
        trail_atr=D(3),
    )
    assert [e["event"] for e in after["events"]] == ["stop_hit"]  # 之后看最低价
    assert after["unchecked"] is False and after["stop"] == D(100)


def test_target_hit_then_trailing_raises_never_lowers():
    """验收 A8：目标触及后每天 holding_check，止损单调不降且不低于成本。"""
    plan = _open_plan(max_hold_sessions=50)
    avg_cost = D(105)
    sessions = [
        date(2026, 11, 2),
        date(2026, 11, 3),
        date(2026, 11, 4),
        date(2026, 11, 5),
        date(2026, 11, 6),
        date(2026, 11, 9),
        date(2026, 11, 10),
        date(2026, 11, 11),
    ]
    closes = ["110", "120", "136", "137", "138", "140", "139", "142"]
    events = []
    stop = plan["stop"]
    highest = D(0)
    trailing_seen = False
    for session, close in zip(sessions, closes, strict=True):
        highest = max(highest, D(close))
        result = plan_line.holding_check(
            plan=plan,
            bar=_bar(close, low=str(D(close) - 5), high=str(D(close) + 1)),
            session=session,
            highest_close=highest,
            avg_cost=avg_cost,
            held_sessions=len(sessions[: sessions.index(session)]),
            trail_atr=D(3),
        )
        events.extend(e["event"] for e in result["events"])
        assert result["stop"] >= stop, f"stop lowered on {session}"
        if trailing_seen:  # 进入移动止损后不低于成本（A8）
            assert result["stop"] >= avg_cost
        stop = result["stop"]
        plan = {**plan, "stop": result["stop"], "trailing": result["trailing"]}
        trailing_seen = trailing_seen or result["trailing"]
    assert "target_hit" in events and "stop_raised" in events
    assert trailing_seen
    assert stop == D(136)  # 142 − 3×2


def test_trailing_stop_ignores_cost_floor_above_price():
    """Review Focus 2：成本高于现价时不拿成本抬高止损。"""
    assert plan_line.trailing_stop(
        current_stop=D(114), highest_close=D(120), atr=D(2), trail_atr=D(3), avg_cost=D(125), last_close=D(119)
    ) == D(114)


def test_trailing_stop_formula():
    assert plan_line.trailing_stop(
        current_stop=D(100), highest_close=D(120), atr=D(2), trail_atr=D(3), avg_cost=D(118), last_close=D(121)
    ) == D(118)
    assert plan_line.trailing_stop(
        current_stop=D(100), highest_close=D(120), atr=D(2), trail_atr=D(3), avg_cost=D(110), last_close=D(121)
    ) == D(114)


def test_max_hold_sets_sell_instruction_once():
    plan = _open_plan()
    first = plan_line.holding_check(
        plan=plan,
        bar=_bar("120"),
        session=date(2026, 11, 30),
        highest_close=D(120),
        avg_cost=D(105),
        held_sessions=20,
        trail_atr=D(3),
    )
    assert "sell_instruction" in [e["event"] for e in first["events"]]
    assert first["sell_instruction"] == "max_hold"
    again = plan_line.holding_check(
        plan={**plan, "sell_instruction": "max_hold"},
        bar=_bar("121"),
        session=date(2026, 12, 1),
        highest_close=D(121),
        avg_cost=D(105),
        held_sessions=21,
        trail_atr=D(3),
    )
    assert "sell_instruction" not in [e["event"] for e in again["events"]]
    assert again["sell_instruction"] == "max_hold"


def test_missing_bar_marks_holding_unchecked():
    result = plan_line.holding_check(
        plan=_open_plan(),
        bar=None,
        session=date(2026, 11, 3),
        highest_close=None,
        avg_cost=D(105),
        held_sessions=1,
        trail_atr=D(3),
    )
    assert result == {
        "events": [],
        "stop": D(100),
        "trailing": False,
        "sell_instruction": None,
        "unchecked": True,
        "rotation_superseded": None,
    }


def test_next_action_priority():
    position = {"quantity": D(10), "avg_cost": D(95), "opened_at": None}
    base = _open_plan()
    # 1. 止损已触及，未见卖出记录（优先于一切）
    hit = plan_line.next_action(
        plan={
            **base,
            "stop_hit_session": date(2026, 11, 2),
            "sell_instruction": "max_hold",
            "confirmed_stop_order": None,
        },
        position=position,
        unchecked=True,
    )
    assert hit == {"kind": "stop_hit_unresolved", "text": "止损已触及，未见卖出记录", "stop": D(100), "reason": None}
    # 持仓为零后不再提醒
    assert (
        plan_line.next_action(plan={**base, "stop_hit_session": date(2026, 11, 2)}, position=None, unchecked=False)[
            "kind"
        ]
        == "stop_order"
    )
    # 2. 下次开盘卖出（持有到期 / 论点失效优先于缺日线）
    assert plan_line.next_action(plan={**base, "sell_instruction": "max_hold"}, position=position, unchecked=True) == {
        "kind": "sell_next_open",
        "text": "下次开盘卖出",
        "stop": None,
        "reason": "持有到期",
    }
    assert (
        plan_line.next_action(plan={**base, "sell_instruction": "thesis_broken"}, position=position, unchecked=False)[
            "reason"
        ]
        == "论点失效"
    )
    # 3. 缺日线，无法检查止损
    assert plan_line.next_action(plan=base, position=position, unchecked=True) == {
        "kind": "unchecked",
        "text": "缺日线，无法检查止损",
        "stop": None,
        "reason": None,
    }
    # 4. 未确认止损单 / 止损单需上调
    assert plan_line.next_action(plan=base, position=position, unchecked=False) == {
        "kind": "stop_order",
        "text": "未确认止损单",
        "stop": D(100),
        "reason": None,
    }
    raise_needed = plan_line.next_action(
        plan={**base, "confirmed_stop_order": D(95)}, position=position, unchecked=False
    )
    assert raise_needed["kind"] == "stop_order"
    assert raise_needed["text"] == "止损单需上调至 100.00（当前挂单 95.00）"
    # 5. 继续持有（挂单与止损一致）
    assert plan_line.next_action(plan={**base, "confirmed_stop_order": D(100)}, position=position, unchecked=False) == {
        "kind": "hold",
        "text": "继续持有",
        "stop": D(100),
        "reason": None,
    }
    assert plan_line.NEXT_ACTION_TEXT["hold"] == "继续持有"
    assert plan_line.ACTIVE == ("pending", "open")
    assert plan_line.ENDED == ("closed", "expired", "invalidated", "replaced", "cancelled")


def test_replacement_rules():
    top = [{"ticker": "MSFT", "rank": 1}, {"ticker": "NVDA", "rank": 2}, {"ticker": "TSLA", "rank": 3}]
    t1, t2 = datetime(2026, 11, 2, 10), datetime(2026, 11, 3, 10)
    # 有空位（没有待入场计划可被替换）→ None
    assert plan_line.replacement(top_rows=top, pending=[], active_tickers=set(), replaced_today=False) is None
    # 已在当天替换过 → None
    assert (
        plan_line.replacement(
            top_rows=top,
            pending=[{"id": 11, "ticker": "AAPL", "created_at": t1}],
            active_tickers={"AAPL"},
            replaced_today=True,
        )
        is None
    )
    # 候选名次不高于名单里最差的待入场 → None
    assert (
        plan_line.replacement(
            top_rows=[{"ticker": "MSFT", "rank": 1}, {"ticker": "NVDA", "rank": 2}],
            pending=[{"id": 11, "ticker": "MSFT", "created_at": t1}],
            active_tickers={"MSFT"},
            replaced_today=False,
        )
        is None
    )
    # 不在名单的待入场视为最差（10**6），名次并列（都不在名单）时取最早建立的作受害者
    pending = [{"id": 12, "ticker": "ORCL", "created_at": t2}, {"id": 11, "ticker": "AAPL", "created_at": t1}]
    result = plan_line.replacement(top_rows=top, pending=pending, active_tickers={"AAPL", "ORCL"}, replaced_today=False)
    assert result == {"candidate": "MSFT", "victim_plan_id": 11, "victim_rank": 10**6}
    # 名单内的待入场作受害者时返回它的当天名次
    in_list = plan_line.replacement(
        top_rows=top,
        pending=[{"id": 13, "ticker": "NVDA", "created_at": t1}],
        active_tickers={"NVDA"},
        replaced_today=False,
    )
    assert in_list == {"candidate": "MSFT", "victim_plan_id": 13, "victim_rank": 2}


def test_close_outcome():
    trades = [
        {
            "id": 1,
            "ticker": "AAPL",
            "side": "buy",
            "quantity": D(100),
            "price": D(50),
            "fee": D(0),
            "executed_at": datetime(2026, 11, 2, 15, tzinfo=NY),
            "voided_at": None,
        },
        {
            "id": 2,
            "ticker": "AAPL",
            "side": "sell",
            "quantity": D(100),
            "price": D(58),
            "fee": D(0),
            "executed_at": datetime(2026, 11, 16, 15, tzinfo=NY),
            "voided_at": None,
        },
    ]
    closed = positions.build(trades)["closed"]
    plan = {
        "kind": "planned",
        "initial_stop": D(46),
        "stop_hit_session": None,
        "trailing": False,
        "sell_instruction": None,
        "entry_low": D(48),
        "entry_high": D(52),
    }
    out = plan_line.close_outcome(plan=plan, closed_lots=closed, trades=trades, sessions_since_stop_hit=None)
    assert out["realized_pnl"] == D(800)
    assert out["realized_pct"] == D("0.16")
    assert out["r_multiple"] == D(2)
    assert out["r_text"] is None
    assert out["exit_reason"] == "manual"
    assert out["stop_delay_sessions"] is None
    assert out["entry_in_zone"] is True
    # 补录计划不计倍数
    adopted = plan_line.close_outcome(
        plan={**plan, "kind": "adopted"}, closed_lots=closed, trades=trades, sessions_since_stop_hit=None
    )
    assert adopted["r_multiple"] is None and adopted["r_text"] == "补录，不计"
    # 离场原因：初始止损 / 移动止损 / 持有到期 / 论点失效
    assert (
        plan_line.close_outcome(
            plan={**plan, "stop_hit_session": date(2026, 11, 10)},
            closed_lots=closed,
            trades=trades,
            sessions_since_stop_hit=2,
        )["exit_reason"]
        == "initial_stop"
    )
    assert (
        plan_line.close_outcome(
            plan={**plan, "stop_hit_session": date(2026, 11, 10), "trailing": True},
            closed_lots=closed,
            trades=trades,
            sessions_since_stop_hit=2,
        )["exit_reason"]
        == "trailing_stop"
    )
    assert (
        plan_line.close_outcome(
            plan={**plan, "sell_instruction": "max_hold"},
            closed_lots=closed,
            trades=trades,
            sessions_since_stop_hit=None,
        )["exit_reason"]
        == "max_hold"
    )
    assert (
        plan_line.close_outcome(
            plan={**plan, "sell_instruction": "thesis_broken"},
            closed_lots=closed,
            trades=trades,
            sessions_since_stop_hit=None,
        )["exit_reason"]
        == "thesis_broken"
    )
    # 止损触及比卖出早 2 个交易日 → stop_delay_sessions=2；没触及为 None
    delayed = plan_line.close_outcome(
        plan={**plan, "stop_hit_session": date(2026, 11, 12)},
        closed_lots=closed,
        trades=trades,
        sessions_since_stop_hit=2,
    )
    assert delayed["stop_delay_sessions"] == 2
    # 买入价在区间外
    outside = plan_line.close_outcome(
        plan={**plan, "entry_low": D(53)}, closed_lots=closed, trades=trades, sessions_since_stop_hit=None
    )
    assert outside["entry_in_zone"] is False


def test_split_adjust():
    plan = {
        "entry_low": D("31.80"),
        "entry_high": D("32.10"),
        "stop": D("30.00"),
        "target1": D("36.00"),
        "initial_stop": D("30.00"),
        "confirmed_stop_order": D("30.00"),
        "atr": D("2.00"),
        "shares": 100,
    }
    before, after = plan_line.split_adjust(plan, D(2))
    assert before["stop"] == D("30.00") and before["shares"] == 100
    assert after["entry_low"] == D("15.90") and after["entry_high"] == D("16.05")
    assert after["stop"] == D("15.00") and after["target1"] == D("18.00")
    assert after["initial_stop"] == D("15.00") and after["confirmed_stop_order"] == D("15.00")
    assert after["atr"] == D("1.00") and after["shares"] == 200
    # 原字典不被改动
    assert plan["stop"] == D("30.00") and plan["shares"] == 100


def test_held_sessions():
    assert plan_line.held_sessions(date(2026, 11, 6), date(2026, 11, 9)) == 1  # 周五 → 下周一
    assert plan_line.held_sessions(date(2026, 11, 2), date(2026, 11, 6)) == 4


def test_free_slots_and_deviations():
    assert plan_line.free_slots(pending_count=2, cap=3) == 1
    assert plan_line.free_slots(pending_count=3, cap=3) == 0
    plan = _pending_plan(shares=100)
    assert plan_line.deviations(plan=plan, price=D("32.00"), quantity=D(100)) == []
    price_out = plan_line.deviations(plan=plan, price=D("33.00"), quantity=D(100))
    assert len(price_out) == 1 and "33.00" in price_out[0]
    both = plan_line.deviations(plan=plan, price=D("33.00"), quantity=D(150))
    assert len(both) == 2 and "150" in both[1]


def test_stop_order_raise_text_trims_storage_zeros():
    position = {"quantity": D(10), "avg_cost": D(95), "opened_at": None}
    plan = {**_open_plan(), "stop": D("100.500000"), "confirmed_stop_order": D("95.000000")}
    action = plan_line.next_action(plan=plan, position=position, unchecked=False)
    assert action["text"] == "止损单需上调至 100.50（当前挂单 95.00）"


# ---- v3.3：换仓指令（spec §6.5–§6.6） ----


def _rotation_plan(**over):
    row = {"sell_instruction": "rotation", "sell_quantity": D(150), "rotation_id": 5, "confirmed_stop_order": D(100)}
    return _open_plan(**{**row, **over})


def test_next_action_rotation_sell_ranks_after_other_sells():
    plan = _rotation_plan()
    position = {"quantity": D(300)}
    action = plan_line.next_action(plan=plan, position=position, unchecked=False, counterpart="BBB")
    assert action == {
        "kind": "rotation_sell",
        "text": "下次开盘卖出 150 股（换仓至 BBB）",
        "stop": None,
        "reason": "换仓",
        "sell_quantity": D(150),
        "counterpart": "BBB",
    }
    # 止损触及仍排在最前
    hit = plan_line.next_action(
        plan={**plan, "stop_hit_session": date(2026, 11, 5)}, position=position, unchecked=False, counterpart="BBB"
    )
    assert hit["kind"] == "stop_hit_unresolved"
    # 缺日线时换仓指令仍排在「缺日线」之前
    unchecked = plan_line.next_action(plan=plan, position=position, unchecked=True, counterpart="BBB")
    assert unchecked["kind"] == "rotation_sell"
    # 其他卖出指令的返回形状不变（没有新键）
    sell = plan_line.next_action(plan=_open_plan(sell_instruction="max_hold"), position=position, unchecked=False)
    assert sell == {"kind": "sell_next_open", "text": "下次开盘卖出", "stop": None, "reason": "持有到期"}


def test_next_action_stale_stop_quantity():
    position = {"quantity": D(151)}
    plan = _open_plan(confirmed_stop_order=D(100), stop_quantity_stale=True)
    action = plan_line.next_action(plan=plan, position=position, unchecked=False)
    assert action == {"kind": "stop_order", "text": "止损单数量需改为 151 股", "stop": D(100), "reason": None}
    # 价位同时需要上调：仍是原来的「止损单需上调至 …」文案
    raise_first = plan_line.next_action(
        plan={**plan, "stop": D(104), "confirmed_stop_order": D(100)}, position=position, unchecked=False
    )
    assert raise_first["text"] == "止损单需上调至 104.00（当前挂单 100.00）"
    # 数量没有待改标记时照旧继续持有
    fresh = plan_line.next_action(plan=_open_plan(confirmed_stop_order=D(100)), position=position, unchecked=False)
    assert fresh["kind"] == "hold"


def _check(plan, bar, *, session=date(2026, 11, 10), held=3):
    return plan_line.holding_check(
        plan=plan,
        bar=bar,
        session=session,
        highest_close=D(105),
        avg_cost=D(95),
        held_sessions=held,
        trail_atr=D(3),
    )


def test_holding_check_max_hold_supersedes_rotation():
    check = _check(_rotation_plan(max_hold_sessions=20), _bar("105"), held=20)
    assert check["sell_instruction"] == "max_hold" and check["rotation_superseded"] == "持有到期"
    assert [e["event"] for e in check["events"]] == ["sell_instruction", "rotation_superseded"]
    assert check["events"][1] == {
        "event": "rotation_superseded",
        "reason": "换仓指令被取代：持有到期",
        "before": {"rotation_id": 5, "sell_quantity": D(150)},
        "after": {},
    }


def test_holding_check_stop_hit_supersedes_rotation():
    check = _check(_rotation_plan(), _bar("102", low="99", high="104"))
    assert check["sell_instruction"] is None and check["rotation_superseded"] == "止损触及"
    assert [e["event"] for e in check["events"]] == ["stop_hit", "rotation_superseded"]
    assert check["events"][1]["reason"] == "换仓指令被取代：止损触及"
    assert check["events"][1]["before"] == {"rotation_id": 5, "sell_quantity": D(150)}


def test_holding_check_keeps_rotation_on_a_quiet_day():
    check = _check(_rotation_plan(), _bar("105", low="103", high="106"))
    assert check["sell_instruction"] == "rotation" and check["rotation_superseded"] is None
    assert check["events"] == []
    # 缺日线：什么都不做，指令保留
    missing = _check(_rotation_plan(), None)
    assert missing["sell_instruction"] == "rotation" and missing["rotation_superseded"] is None
    # 其他指令不受影响：持有到期时没有换仓可取代
    plain = _check(_open_plan(max_hold_sessions=20), _bar("105"), held=20)
    assert plain["sell_instruction"] == "max_hold" and plain["rotation_superseded"] is None
    assert [e["event"] for e in plain["events"]] == ["sell_instruction"]


def test_close_outcome_rotation_exit_reason():
    lots = [{"quantity": D(100), "entry_cost_per_share": D(50), "realized_pnl": D(800)}]
    plan = {"kind": "planned", "initial_stop": D(46), "sell_instruction": "rotation", "stop_hit_session": None}
    outcome = plan_line.close_outcome(plan=plan, closed_lots=lots, trades=[], sessions_since_stop_hit=None)
    assert outcome["exit_reason"] == "rotation"
    hit = plan_line.close_outcome(
        plan={**plan, "stop_hit_session": date(2026, 11, 5), "trailing": False},
        closed_lots=lots,
        trades=[],
        sessions_since_stop_hit=0,
    )
    assert hit["exit_reason"] == "initial_stop"


def test_split_adjust_scales_rotation_sell_quantity():
    plan = {"stop": D("30.00"), "shares": 100, "sell_quantity": D(150)}
    before, after = plan_line.split_adjust(plan, D(2))
    assert before["sell_quantity"] == D(150) and after["sell_quantity"] == D(300)
    assert after["shares"] == 200
    none_before, none_after = plan_line.split_adjust({"stop": D("30.00"), "sell_quantity": None}, D(2))
    assert "sell_quantity" not in none_before and "sell_quantity" not in none_after
