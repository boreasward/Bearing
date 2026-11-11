"""Retrospect pure functions (spec §5): validity window, deviation tags, the hypothetical
simulation, the plan record, week bounds, weekly summary and rule statistics."""

from datetime import date, datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from investment_assistant.execution import retrospect

NY = ZoneInfo("America/New_York")


def _plan(**over):
    row = {
        "id": 21,
        "ticker": "AAPL",
        "status": "expired",
        "kind": "planned",
        "session": date(2026, 11, 2),  # 周一
        "entry_low": D("31.80"),
        "entry_high": D("32.10"),
        "stop": D("31.20"),
        "target1": D("34.00"),
        "initial_stop": D("31.20"),
        "atr": D("1.00"),
        "trailing": False,
        "valid_from": date(2026, 11, 3),
        "valid_until": date(2026, 11, 9),
        "shares": 100,
        "max_hold_sessions": 10,
        "outcome": {},
        "research_id": None,
        "ended_session": date(2026, 11, 9),
        "end_reason": "5 个交易日未买入",
    }
    row.update(over)
    return row


def _bar(day, *, open_, high, low, close):
    return {"price_date": day, "open": D(str(open_)), "high": D(str(high)), "low": D(str(low)), "close": D(str(close))}


def _event(event, session, reason=""):
    return {
        "plan_id": 21,
        "session": session,
        "event": event,
        "reason": reason,
        "before": {},
        "after": {},
        "source": "host",
        "refs": [],
    }


def _trade(side, day, price, quantity, *, voided=False, tid=1):
    return {
        "id": tid,
        "side": side,
        "quantity": D(str(quantity)),
        "price": D(str(price)),
        "fee": D(0),
        "executed_at": datetime(day.year, day.month, day.day, 15, tzinfo=NY),
        "voided_at": datetime(2026, 11, 10, 10) if voided else None,
    }


def _record(**over):
    row = {
        "plan_id": 1,
        "ticker": "AAPL",
        "status": "closed",
        "kind": "planned",
        "origin_kind": "rule",
        "session": date(2026, 11, 2),
        "ended_session": date(2026, 11, 5),
        "end_reason": "已平仓：用户自行卖出",
        "planned": {},
        "actual": {
            "first_buy": {"price": D("32.00"), "session": date(2026, 11, 3), "quantity": D(100)},
            "last_sell": None,
            "quantity": D(100),
        },
        "outcome": {"exit_reason": "manual"},
        "r_multiple": None,
        "r_text": None,
        "tags": [],
        "followed": True,
        "hypothetical": None,
        "groups": {"trigger_rules": [], "setup_tier": None, "industry": None},
    }
    row.update(over)
    return row


def test_window_spans_from_first_validity_to_last_valid_until():
    """Review Focus 1：延续覆盖 valid_from，窗口起点仍是保存日的下一个交易日，终点是最终 valid_until。"""
    plan = _plan(valid_from=date(2026, 11, 6), valid_until=date(2026, 11, 13))
    assert retrospect.validity_window(plan) == (date(2026, 11, 3), date(2026, 11, 13))
    bars = [
        _bar(date(2026, 11, 2), open_="32.50", high="32.60", low="32.40", close="32.50"),  # 保存日不算
        _bar(date(2026, 11, 3), open_="31.60", high="31.70", low="31.50", close="31.60"),  # 全天低于区间
        _bar(
            date(2026, 11, 4), open_="32.00", high="32.20", low="31.90", close="32.00"
        ),  # 进过区间（在 valid_from 之前）
    ]
    assert retrospect.touched_zone(plan, bars) is True
    assert retrospect.touched_zone(plan, bars[:2]) is False
    # 复盘行的「计划说的」用同一个窗口（§5.1/R2）：延续过的 valid_from 不再露出被覆盖的值
    record = retrospect.plan_record(
        plan=_plan(valid_from=date(2026, 11, 6), valid_until=date(2026, 11, 13)),
        events=[],
        trades=[],
        bars=bars[:1],
        candidate=None,
        trail_atr=D(2),
    )
    assert record["planned"]["valid_from"] == date(2026, 11, 3) and record["planned"]["valid_until"] == date(
        2026, 11, 13
    )


def test_tags_each_code_once():
    assert retrospect.DEVIATIONS == (
        "not_bought_in_zone",
        "entry_out_of_zone",
        "over_bought",
        "stop_order_missing",
        "late_sell_after_stop",
        "late_sell_after_instruction",
        "early_sell",
        "over_cap",
    )
    assert retrospect.NOT_FILLED == "not_filled"
    assert retrospect.FOLLOWED_TEXT == "照做"
    assert retrospect.TAG_TEXT["not_bought_in_zone"] == "未买入：价格进过入场区间"
    assert retrospect.TAG_TEXT["not_filled"] == "未买入：价格未进入场区间"
    assert retrospect.TAG_TEXT["entry_out_of_zone"] == "买价出区间"
    assert retrospect.TAG_TEXT["over_bought"] == "多买"
    assert retrospect.TAG_TEXT["stop_order_missing"] == "止损未挂"
    assert retrospect.TAG_TEXT["late_sell_after_stop"] == "止损触及后晚卖 {n} 个交易日"
    assert retrospect.TAG_TEXT["late_sell_after_instruction"] == "卖出指令后晚卖 {n} 个交易日"
    assert retrospect.TAG_TEXT["early_sell"] == "早卖"
    assert retrospect.TAG_TEXT["over_cap"] == "买入后超出仓位上限"
    touch = [_bar(date(2026, 11, 3), open_="32.00", high="32.20", low="31.90", close="32.00")]
    miss = [_bar(date(2026, 11, 3), open_="31.00", high="31.50", low="30.80", close="31.00")]
    # 未买入：价格进过入场区间（expired 且无成交）
    tags = retrospect.deviation_tags(plan=_plan(status="expired"), events=[], trades=[], bars=touch)
    assert tags == [{"code": "not_bought_in_zone", "text": "未买入：价格进过入场区间", "sessions": None}]
    # not_filled 不算偏差：plan_record 记照做
    record = retrospect.plan_record(
        plan=_plan(status="expired", end_reason="5 个交易日未买入"),
        events=[],
        trades=[],
        bars=miss,
        candidate=None,
        trail_atr=D(2),
    )
    assert record["tags"] == [{"code": "not_filled", "text": "未买入：价格未进入场区间", "sessions": None}]
    assert record["followed"] is True
    # 买价出区间：outcome.entry_in_zone 为假
    out_of_zone = retrospect.deviation_tags(
        plan=_plan(
            status="closed", outcome={"entry_in_zone": False, "exit_reason": "max_hold", "stop_delay_sessions": None}
        ),
        events=[
            _event("stop_order_confirmed", date(2026, 11, 3)),
            _event("sell_instruction", date(2026, 11, 5), "持有到期"),
        ],
        trades=[
            _trade("buy", date(2026, 11, 3), "33.00", 100, tid=1),
            _trade("sell", date(2026, 11, 6), "32.00", 100, tid=2),
        ],
        bars=touch,
    )
    assert [tag["code"] for tag in out_of_zone] == ["entry_out_of_zone"]
    # 买价出区间：买入时的 deviation 事件（reason 逐字沿用 plan_line.deviations）
    by_event = retrospect.deviation_tags(
        plan=_plan(
            status="closed", outcome={"entry_in_zone": True, "exit_reason": "max_hold", "stop_delay_sessions": None}
        ),
        events=[
            _event("deviation", date(2026, 11, 3), "成交价 33.00 不在入场区间 31.80–32.10"),
            _event("stop_order_confirmed", date(2026, 11, 3)),
            _event("sell_instruction", date(2026, 11, 5), "持有到期"),
        ],
        trades=[
            _trade("buy", date(2026, 11, 3), "33.00", 100, tid=1),
            _trade("sell", date(2026, 11, 6), "32.00", 100, tid=2),
        ],
        bars=touch,
    )
    assert [tag["code"] for tag in by_event] == ["entry_out_of_zone"]
    # 多买：买入时的 deviation 事件写了「多于计划的股数」
    over_bought = retrospect.deviation_tags(
        plan=_plan(
            status="closed", outcome={"entry_in_zone": True, "exit_reason": "max_hold", "stop_delay_sessions": None}
        ),
        events=[
            _event("deviation", date(2026, 11, 3), "买入 150 股，多于计划的 100 股"),
            _event("stop_order_confirmed", date(2026, 11, 3)),
            _event("sell_instruction", date(2026, 11, 5), "持有到期"),
        ],
        trades=[
            _trade("buy", date(2026, 11, 3), "32.00", 150, tid=1),
            _trade("sell", date(2026, 11, 6), "32.50", 150, tid=2),
        ],
        bars=touch,
    )
    assert [tag["code"] for tag in over_bought] == ["over_bought"]
    # 止损未挂：买入到结束没有任何 stop_order_confirmed 事件
    missing = retrospect.deviation_tags(
        plan=_plan(
            status="closed", outcome={"entry_in_zone": True, "exit_reason": "max_hold", "stop_delay_sessions": None}
        ),
        events=[],
        trades=[
            _trade("buy", date(2026, 11, 3), "32.00", 100, tid=1),
            _trade("sell", date(2026, 11, 10), "31.50", 100, tid=2),
        ],
        bars=touch,
    )
    assert [tag["code"] for tag in missing] == ["stop_order_missing"]
    # 止损触及后晚卖 N 个交易日（N 取 outcome.stop_delay_sessions）
    late_stop = retrospect.deviation_tags(
        plan=_plan(
            status="closed", outcome={"entry_in_zone": True, "exit_reason": "initial_stop", "stop_delay_sessions": 2}
        ),
        events=[_event("stop_order_confirmed", date(2026, 11, 3))],
        trades=[
            _trade("buy", date(2026, 11, 3), "32.00", 100, tid=1),
            _trade("sell", date(2026, 11, 10), "31.00", 100, tid=2),
        ],
        bars=touch,
    )
    assert late_stop == [{"code": "late_sell_after_stop", "text": "止损触及后晚卖 2 个交易日", "sessions": 2}]
    # 卖出指令后晚卖 N 个交易日：X=11-02（周一），Y=11-06（周五），N = 11-04…11-06 共 3 个交易日
    late_instruction = retrospect.deviation_tags(
        plan=_plan(
            status="closed", outcome={"entry_in_zone": True, "exit_reason": "manual", "stop_delay_sessions": None}
        ),
        events=[
            _event("sell_instruction", date(2026, 11, 2), "持有到期"),
            _event("stop_order_confirmed", date(2026, 11, 3)),
        ],
        trades=[
            _trade("buy", date(2026, 11, 3), "32.00", 100, tid=1),
            _trade("sell", date(2026, 11, 6), "31.00", 100, tid=2),
        ],
        bars=touch,
    )
    assert late_instruction == [
        {"code": "late_sell_after_instruction", "text": "卖出指令后晚卖 3 个交易日", "sessions": 3}
    ]
    # 早卖：离场原因 manual 且没有止损触及、没有卖出指令
    early = retrospect.deviation_tags(
        plan=_plan(
            status="closed", outcome={"entry_in_zone": True, "exit_reason": "manual", "stop_delay_sessions": None}
        ),
        events=[_event("stop_order_confirmed", date(2026, 11, 3))],
        trades=[
            _trade("buy", date(2026, 11, 3), "32.00", 100, tid=1),
            _trade("sell", date(2026, 11, 6), "31.00", 100, tid=2),
        ],
        bars=touch,
    )
    assert early == [{"code": "early_sell", "text": "早卖", "sessions": None}]
    # 补录（adopted）不打任何标签
    assert (
        retrospect.deviation_tags(
            plan=_plan(kind="adopted", status="closed"),
            events=[],
            trades=[_trade("buy", date(2026, 11, 3), "33.00", 100)],
            bars=touch,
        )
        == []
    )


def test_not_bought_in_zone_only_for_expired():
    """spec §5.2 规则：被替换 / 取消 / 作废的计划不打 not_bought_in_zone——结束不是用户的决定。"""
    touch = [_bar(date(2026, 11, 3), open_="32.00", high="32.20", low="31.90", close="32.00")]
    miss = [_bar(date(2026, 11, 3), open_="31.00", high="31.50", low="30.80", close="31.00")]
    for status in ("replaced", "cancelled", "invalidated"):
        tags = retrospect.deviation_tags(plan=_plan(status=status), events=[], trades=[], bars=touch)
        assert tags == [], status  # 价格进过区间但不打 not_bought_in_zone，也不流 not_filled
        # 价格从未进入场区间 → not_filled（照做）
        absent = retrospect.deviation_tags(plan=_plan(status=status), events=[], trades=[], bars=miss)
        assert [tag["code"] for tag in absent] == [retrospect.NOT_FILLED], status
    # 只有 expired 打 not_bought_in_zone
    expired = retrospect.deviation_tags(plan=_plan(status="expired"), events=[], trades=[], bars=touch)
    assert [tag["code"] for tag in expired] == ["not_bought_in_zone"]


def test_hypothetical_fill_then_initial_stop_gap_exit():
    """成交价 min(open, entry_high)；跳空低开按 min(open, 当日生效止损) 离场；R 与手算一致。"""
    plan = _plan(status="expired")
    bars = [
        _bar(date(2026, 11, 3), open_="32.00", high="32.50", low="31.90", close="32.00"),
        _bar(date(2026, 11, 4), open_="31.00", high="31.10", low="30.80", close="30.90"),
    ]
    out = retrospect.hypothetical(plan=plan, bars=bars, trail_atr=D(2))
    assert out == {
        "status": "exited",
        "reason": None,
        "fill_session": date(2026, 11, 3),
        "fill_price": D("32.00"),  # min(open, entry_high)
        "exit_session": date(2026, 11, 4),
        "exit_price": D("31.00"),  # 跳空低于止损 → 按 open
        "exit_reason": "initial_stop",
        "sessions_held": 1,
        "r_multiple": D("-1.25"),  # (31.00 − 32.00) / (32.00 − 31.20)
    }


def test_hypothetical_close_below_stop_before_fill():
    bars = [_bar(date(2026, 11, 3), open_="32.50", high="32.60", low="32.40", close="31.00")]
    out = retrospect.hypothetical(plan=_plan(status="invalidated"), bars=bars, trail_atr=D(2))
    assert out["status"] == "not_filled" and out["reason"] == "close_below_stop"
    assert out["fill_session"] is None and out["r_multiple"] is None


def test_hypothetical_never_in_zone():
    bars = [
        _bar(date(2026, 11, day), open_="32.50", high="32.80", low="32.30", close="32.60")
        for day in (3, 4, 5, 6)  # 窗口到 11-09，走完没成交
    ]
    out = retrospect.hypothetical(plan=_plan(status="expired"), bars=bars, trail_atr=D(2))
    assert out["status"] == "not_filled" and out["reason"] == "never_in_zone"


def test_hypothetical_trailing_and_max_hold_next_open():
    trail_bars = [
        _bar(date(2026, 11, 3), open_="32.00", high="32.50", low="31.90", close="32.00"),
        _bar(
            date(2026, 11, 4), open_="32.50", high="34.50", low="32.40", close="34.50"
        ),  # 触及目标 → 移动止损上移到 34.50 − 2×1 = 32.50
        _bar(date(2026, 11, 5), open_="32.45", high="32.60", low="32.30", close="32.40"),  # 低点 32.30 触及 32.50
    ]
    out = retrospect.hypothetical(plan=_plan(status="expired"), bars=trail_bars, trail_atr=D(2))
    assert out["status"] == "exited" and out["exit_reason"] == "trailing_stop"
    assert out["exit_session"] == date(2026, 11, 5) and out["exit_price"] == D("32.45")
    assert out["r_multiple"] == D("0.56")  # (32.45 − 32.00) / 0.80 = 0.5625 → 0.56
    assert out["sessions_held"] == 2
    # 持有到期 → 下一根日线的 open 离场
    hold_bars = [
        _bar(date(2026, 11, 3), open_="32.00", high="32.50", low="31.90", close="32.00"),
        _bar(date(2026, 11, 4), open_="32.20", high="32.50", low="32.10", close="32.50"),
        _bar(date(2026, 11, 5), open_="32.40", high="32.60", low="32.30", close="32.40"),  # held_sessions=2 → 卖出指令
        _bar(date(2026, 11, 6), open_="33.10", high="33.20", low="32.90", close="33.00"),
    ]
    out = retrospect.hypothetical(plan=_plan(status="expired", max_hold_sessions=2), bars=hold_bars, trail_atr=D(2))
    assert out["status"] == "exited" and out["exit_reason"] == "max_hold"
    assert out["exit_session"] == date(2026, 11, 6) and out["exit_price"] == D("33.10")
    assert out["r_multiple"] == D("1.38")  # (33.10 − 32.00) / 0.80 = 1.375 → 1.38
    assert out["sessions_held"] == 3
    # 卖出指令落在最后一根日线上（没有下一根）→ 进行中
    out = retrospect.hypothetical(plan=_plan(status="expired", max_hold_sessions=2), bars=hold_bars[:3], trail_atr=D(2))
    assert out["status"] == "unfinished" and out["exit_reason"] is None


def test_hypothetical_unfinished_marks_to_last_close():
    bars = [
        _bar(date(2026, 11, 3), open_="32.00", high="32.50", low="31.90", close="32.00"),
        _bar(date(2026, 11, 4), open_="32.30", high="32.80", low="32.20", close="32.80"),
        _bar(date(2026, 11, 5), open_="32.80", high="33.10", low="32.70", close="33.00"),
    ]
    out = retrospect.hypothetical(plan=_plan(status="expired"), bars=bars, trail_atr=D(2))
    assert out["status"] == "unfinished" and out["reason"] is None
    assert out["exit_session"] == date(2026, 11, 5) and out["exit_price"] == D("33.00")
    assert out["sessions_held"] == 2 and out["r_multiple"] == D("1.25")


def test_plan_record_origin_kinds_and_groups():
    bars = [
        _bar(date(2026, 11, 3), open_="32.00", high="32.20", low="31.90", close="32.00"),
        _bar(date(2026, 11, 6), open_="32.50", high="32.70", low="32.40", close="32.50"),
    ]
    candidate = {"trigger_rules": ["pullback", "trend_reclaim"], "setup_tier": "favorable", "industry": "半导体"}
    record = retrospect.plan_record(
        plan=_plan(
            status="closed",
            ended_session=date(2026, 11, 6),
            end_reason="已平仓：持有到期",
            outcome={
                "entry_in_zone": True,
                "exit_reason": "max_hold",
                "r_multiple": D("0.63"),
                "r_text": None,
                "stop_delay_sessions": None,
            },
        ),
        events=[
            _event("stop_order_confirmed", date(2026, 11, 3)),
            _event("sell_instruction", date(2026, 11, 5), "持有到期"),
        ],
        trades=[
            _trade("buy", date(2026, 11, 3), "32.00", 100, tid=1),
            _trade("sell", date(2026, 11, 6), "32.50", 100, tid=2),
        ],
        bars=bars,
        candidate=candidate,
        trail_atr=D(2),
    )
    assert record == {
        "plan_id": 21,
        "ticker": "AAPL",
        "status": "closed",
        "kind": "planned",
        "origin_kind": "rule",
        "session": date(2026, 11, 2),
        "ended_session": date(2026, 11, 6),
        "end_reason": "已平仓：持有到期",
        "planned": {
            "entry_low": D("31.80"),
            "entry_high": D("32.10"),
            "initial_stop": D("31.20"),
            "target1": D("34.00"),
            "valid_from": date(2026, 11, 3),
            "valid_until": date(2026, 11, 9),
            "shares": 100,
            "max_hold_sessions": 10,
        },
        "actual": {
            "first_buy": {"price": D("32.00"), "session": date(2026, 11, 3), "quantity": D(100)},
            "last_sell": {"price": D("32.50"), "session": date(2026, 11, 6)},
            "quantity": D(100),
        },
        "outcome": record["outcome"],
        "r_multiple": D("0.63"),
        "r_text": None,
        "tags": [],
        "followed": True,
        "hypothetical": None,  # closed 不算假设结果
        "groups": {
            "trigger_rules": ["pullback", "trend_reclaim"],
            "setup_tier": "favorable",
            "industry": "半导体",
        },
    }
    # candidate 缺失 → 空分组
    no_candidate = retrospect.plan_record(
        plan=_plan(status="expired"), events=[], trades=[], bars=bars[:1], candidate=None, trail_atr=D(2)
    )
    assert no_candidate["origin_kind"] == "rule"
    assert no_candidate["groups"] == {"trigger_rules": [], "setup_tier": None, "industry": None}
    assert no_candidate["hypothetical"] is not None  # 没买的计划算假设结果
    # 研究转成的计划 → 三项「用户研究」
    research = retrospect.plan_record(
        plan=_plan(status="expired", research_id=5), events=[], trades=[], bars=bars[:1], candidate=None, trail_atr=D(2)
    )
    assert research["origin_kind"] == "research"
    assert research["groups"] == {"trigger_rules": ["用户研究"], "setup_tier": "用户研究", "industry": "用户研究"}
    # 补录 → origin_kind adopted、R 倍数「补录，不计」、无标签、无假设结果
    adopted = retrospect.plan_record(
        plan=_plan(kind="adopted", status="closed", outcome={"r_multiple": None, "r_text": "补录，不计"}),
        events=[],
        trades=[_trade("buy", date(2026, 11, 3), "32.00", 100), _trade("sell", date(2026, 11, 6), "32.50", 100)],
        bars=bars,
        candidate=None,
        trail_atr=D(2),
    )
    assert adopted["origin_kind"] == "adopted" and adopted["r_text"] == "补录，不计"
    assert adopted["tags"] == [] and adopted["hypothetical"] is None


def test_is_week_end_and_bounds():
    assert retrospect.is_week_end(date(2026, 11, 6)) is True  # 周五
    assert retrospect.is_week_end(date(2026, 11, 5)) is False
    assert retrospect.is_week_end(date(2026, 11, 25)) is False  # 周三：26 日感恩节休市，27 日仍是本周交易日
    assert retrospect.is_week_end(date(2026, 11, 27)) is True  # 周五：下一交易日 30 日落下周
    assert retrospect.week_bounds(date(2026, 11, 4)) == (date(2026, 11, 2), date(2026, 11, 4))
    assert retrospect.week_bounds(date(2026, 11, 8)) == (date(2026, 11, 2), date(2026, 11, 8))


def test_weekly_summary_counts_and_lists():
    records = [
        _record(
            ticker="AAPL",
            ended_session=date(2026, 11, 5),
            followed=False,
            tags=[{"code": "early_sell", "text": "早卖", "sessions": None}],
            r_multiple=D("1.25"),
            outcome={"exit_reason": "manual"},
        ),
        _record(
            ticker="MSFT",
            ended_session=date(2026, 11, 4),
            followed=True,
            tags=[],
            r_multiple=D("-0.50"),
            outcome={"exit_reason": "initial_stop"},
        ),
        _record(
            ticker="TSLA",
            status="expired",
            ended_session=date(2026, 11, 6),
            end_reason="5 个交易日未买入",
            followed=True,
            tags=[{"code": "not_filled", "text": "未买入：价格未进入场区间", "sessions": None}],
            r_multiple=None,
            outcome={},
            hypothetical={"status": "not_filled", "reason": "never_in_zone"},
        ),
        _record(
            ticker="NVDA", ended_session=date(2026, 11, 13), r_multiple=D("9.99"), outcome={"exit_reason": "manual"}
        ),  # 下周，不计
    ]
    candidates_week = [
        {"session": date(2026, 11, 2), "ticker": "MSFT", "detail": {"streak": 1}},
        {"session": date(2026, 11, 3), "ticker": "MSFT", "detail": {"streak": 2}},
        {"session": date(2026, 11, 4), "ticker": "MSFT", "detail": {"streak": 3}},
        {"session": date(2026, 11, 5), "ticker": "MSFT", "detail": {"streak": 4}},
        {"session": date(2026, 11, 6), "ticker": "MSFT", "detail": {"streak": 5}},
        {"session": date(2026, 11, 2), "ticker": "NVDA", "detail": {"streak": 3}},
        {"session": date(2026, 11, 3), "ticker": "NVDA", "detail": {"streak": 4}},
        {"session": date(2026, 11, 2), "ticker": "ORCL", "detail": {"streak": 5}},
        {"session": date(2026, 11, 4), "ticker": "ORCL", "detail": {"streak": 1}},  # 掉出又回来
        {"session": date(2026, 11, 6), "ticker": "ORCL", "detail": {"streak": 1}},  # 又一次
    ]
    weekly = retrospect.weekly_summary(
        records=records,
        week_start=date(2026, 11, 2),
        week_end=date(2026, 11, 6),
        equity_start=D("12000.00"),
        equity_end=D("12500.50"),
        realized_pnl=D("300.00"),
        candidates_week=candidates_week,
    )
    assert weekly["ended"] == 3 and weekly["followed"] == 2 and weekly["deviated"] == 1
    assert weekly["tag_counts"] == {"早卖": 1}  # not_filled 不算偏差标签
    assert weekly["closed"] == [
        {"plan_id": 1, "ticker": "AAPL", "r_multiple": D("1.25"), "exit_reason": "manual", "tags": ["早卖"]},
        {"plan_id": 1, "ticker": "MSFT", "r_multiple": D("-0.50"), "exit_reason": "initial_stop", "tags": []},
    ]
    assert weekly["total_r"] == D("0.75")
    assert weekly["not_bought"] == [
        {
            "plan_id": 1,
            "ticker": "TSLA",
            "end_reason": "5 个交易日未买入",
            "hypothetical": {"status": "not_filled", "reason": "never_in_zone"},
        }
    ]
    assert weekly["equity_start"] == D("12000.00") and weekly["equity_end"] == D("12500.50")
    assert weekly["realized_pnl"] == D("300.00")
    assert weekly["listing"] == {"new": ["MSFT"], "dropped": ["NVDA"], "most_relisted": "ORCL"}
    assert weekly["week_start"] == date(2026, 11, 2) and weekly["week_end"] == date(2026, 11, 6)


def test_plan_record_normalizes_repo_round_tripped_r_multiple():
    """仓储的 jsonb 编码把 outcome 里的 Decimal 存成字符串（core.dbutil._plan_json），
    回读后的 r_multiple 是 "1.25" 这样的字符串；复盘行归一回 Decimal，聚合不炸。"""
    from json import loads

    from investment_assistant.core.dbutil import _plan_json

    outcome = {
        "entry_in_zone": True,
        "exit_reason": "manual",
        "r_multiple": D("1.25"),
        "r_text": None,
        "stop_delay_sessions": None,
    }
    persisted = loads(_plan_json(outcome))  # 与 v3_plans.outcome 的 jsonb 往返同构
    assert persisted["r_multiple"] == "1.25"  # 存的确实是字符串
    record = retrospect.plan_record(
        plan=_plan(status="closed", outcome=persisted, ended_session=date(2026, 11, 6), end_reason="已平仓"),
        events=[_event("stop_order_confirmed", date(2026, 11, 3))],
        trades=[
            _trade("buy", date(2026, 11, 3), "32.00", 100, tid=1),
            _trade("sell", date(2026, 11, 6), "33.00", 100, tid=2),
        ],
        bars=[],
        candidate={"trigger_rules": ["pullback"], "setup_tier": "favorable", "industry": "半导体"},
        trail_atr=D(2),
    )
    assert isinstance(record["r_multiple"], D) and record["r_multiple"] == D("1.25")
    weekly = retrospect.weekly_summary(
        records=[record],
        week_start=date(2026, 11, 2),
        week_end=date(2026, 11, 6),
        equity_start=None,
        equity_end=None,
        realized_pnl=D(0),
        candidates_week=[],
    )
    assert weekly["total_r"] == D("1.25")
    stats = retrospect.rule_stats([record], key="trigger_rules", realized=True, min_sample=1)
    assert stats[0]["total_r"] == D("1.25") and stats[0]["win_rate"] == D(1)


def test_weekly_deviated_counts_only_tagged_plans():
    """§5.4 的「有偏差」只数打了偏差标签的记录：被替换且价格进过区间的未买计划不算偏差。"""
    touch = [_bar(date(2026, 11, 3), open_="32.00", high="32.20", low="31.90", close="32.00")]
    record = retrospect.plan_record(
        plan=_plan(status="replaced", ended_session=date(2026, 11, 5), end_reason="被更好的候选替换"),
        events=[_event("replaced", date(2026, 11, 5), "被更好的候选替换")],
        trades=[],
        bars=touch,
        candidate=None,
        trail_atr=D(2),
    )
    assert record["tags"] == [] and record["followed"] is False  # 没标签、也不算照做
    weekly = retrospect.weekly_summary(
        records=[record],
        week_start=date(2026, 11, 2),
        week_end=date(2026, 11, 6),
        equity_start=None,
        equity_end=None,
        realized_pnl=D(0),
        candidates_week=[],
    )
    assert weekly["ended"] == 1 and weekly["followed"] == 0
    assert weekly["deviated"] == 0  # 有偏差 ≠ 不照做：只有打了偏差标签才算


def test_listing_uses_trading_calendar_across_empty_sessions():
    """名单推导用交易日本历而不是候选行的日期集：最后一个交易日整天空缺照报掉出；周中空缺算再上榜。"""
    # 周一在榜、之后整周没有候选行 → 掉出（只看候选行日期的实现会漏）
    final_empty = retrospect.weekly_summary(
        records=[],
        week_start=date(2026, 11, 2),
        week_end=date(2026, 11, 6),
        equity_start=None,
        equity_end=None,
        realized_pnl=D(0),
        candidates_week=[{"session": date(2026, 11, 2), "ticker": "AMD", "detail": {"streak": 3}}],
    )
    assert final_empty["listing"]["dropped"] == ["AMD"]
    # 周一在榜、周二空缺、周三回榜 → 周二的空缺算一次再上榜
    middle_empty = retrospect.weekly_summary(
        records=[],
        week_start=date(2026, 11, 2),
        week_end=date(2026, 11, 6),
        equity_start=None,
        equity_end=None,
        realized_pnl=D(0),
        candidates_week=[
            {"session": date(2026, 11, 2), "ticker": "AMD", "detail": {"streak": 3}},
            {"session": date(2026, 11, 4), "ticker": "AMD", "detail": {"streak": 1}},
            {"session": date(2026, 11, 5), "ticker": "AMD", "detail": {"streak": 2}},
            {"session": date(2026, 11, 6), "ticker": "AMD", "detail": {"streak": 3}},
        ],
    )
    assert middle_empty["listing"]["most_relisted"] == "AMD"
    assert middle_empty["listing"]["dropped"] == []


def test_rule_stats_threshold_and_separation():
    records = [
        _record(
            ticker="A",
            r_multiple=D("1.50"),
            groups={"trigger_rules": ["pullback"], "setup_tier": None, "industry": None},
        ),
        _record(
            ticker="B",
            r_multiple=D("-0.50"),
            groups={"trigger_rules": ["pullback"], "setup_tier": None, "industry": None},
        ),
        _record(
            ticker="C",
            r_multiple=D("2.00"),
            groups={"trigger_rules": ["trend_reclaim"], "setup_tier": None, "industry": None},
        ),
        # 没买的计划只有假设结果：realized 不计、hypothetical 计
        _record(
            ticker="D",
            status="expired",
            r_multiple=None,
            hypothetical={"status": "exited", "r_multiple": D("3.00")},
            groups={"trigger_rules": ["pullback"], "setup_tier": None, "industry": None},
        ),
        # 假设结果未离场 / 补录已平仓：两边都不计
        _record(
            ticker="E",
            status="expired",
            hypothetical={"status": "unfinished", "r_multiple": D("9.99")},
            groups={"trigger_rules": ["trend_reclaim"], "setup_tier": None, "industry": None},
        ),
        _record(
            ticker="F",
            kind="adopted",
            r_multiple=None,
            groups={"trigger_rules": ["pullback"], "setup_tier": None, "industry": None},
        ),
        # 一份计划进多组（trigger_rules 两条）
        _record(
            ticker="G",
            r_multiple=D("1.00"),
            groups={"trigger_rules": ["a_rule", "b_rule"], "setup_tier": None, "industry": None},
        ),
    ]
    realized = retrospect.rule_stats(records, key="trigger_rules", realized=True, min_sample=2)
    assert realized == [
        {
            "group": "pullback",
            "sample": 2,
            "win_rate": D("0.5"),
            "avg_r": D("0.50"),
            "total_r": D("1.00"),
            "insufficient": False,
        },
        {"group": "a_rule", "sample": 1, "win_rate": None, "avg_r": None, "total_r": None, "insufficient": True},
        {"group": "b_rule", "sample": 1, "win_rate": None, "avg_r": None, "total_r": None, "insufficient": True},
        {"group": "trend_reclaim", "sample": 1, "win_rate": None, "avg_r": None, "total_r": None, "insufficient": True},
    ]
    # 假设结果表与真实表互不相加：pullback 只有 D 一份（不足门槛）
    hypothetical = retrospect.rule_stats(records, key="trigger_rules", realized=False, min_sample=2)
    assert hypothetical == [
        {"group": "pullback", "sample": 1, "win_rate": None, "avg_r": None, "total_r": None, "insufficient": True}
    ]
    # setup_tier / industry 单值分组照常
    tiers = retrospect.rule_stats(
        [_record(r_multiple=D("1.00"), groups={"trigger_rules": [], "setup_tier": "favorable", "industry": None})],
        key="setup_tier",
        realized=True,
        min_sample=1,
    )
    assert tiers == [
        {
            "group": "favorable",
            "sample": 1,
            "win_rate": D(1),
            "avg_r": D("1.00"),
            "total_r": D("1.00"),
            "insufficient": False,
        }
    ]


def test_over_cap_tag_from_deviation_event():
    """v3.3：买入后仓位占比高于上限的 deviation 事件 → 标签 over_cap（spec §5.3 / §9）。"""
    plan = _plan(
        status="closed", outcome={"entry_in_zone": True, "exit_reason": "max_hold", "stop_delay_sessions": None}
    )
    trades = [
        _trade("buy", date(2026, 11, 3), "32.00", 100, tid=1),
        _trade("sell", date(2026, 11, 6), "32.50", 100, tid=2),
    ]
    events = [
        _event("deviation", date(2026, 11, 3), "买入后仓位占比 63.2%，高于上限 60%"),
        _event("stop_order_confirmed", date(2026, 11, 3)),
        _event("sell_instruction", date(2026, 11, 5), "持有到期"),
    ]
    tags = retrospect.deviation_tags(plan=plan, events=events, trades=trades, bars=[])
    assert tags == [{"code": "over_cap", "text": "买入后超出仓位上限", "sessions": None}]
    assert "over_cap" in retrospect.DEVIATIONS
    # 别的偏差原因不会误打这个标签
    other = [_event("deviation", date(2026, 11, 3), "成交价 33.00 不在入场区间 31.80–32.10"), *events[1:]]
    codes = [tag["code"] for tag in retrospect.deviation_tags(plan=plan, events=other, trades=trades, bars=[])]
    assert "over_cap" not in codes


def test_rotation_instruction_is_not_a_late_sell():
    """换仓指令不是纪律：只有 rotation_instruction 事件、几天后才卖，不打「卖出指令后晚卖」。"""
    plan = _plan(
        status="closed", outcome={"entry_in_zone": True, "exit_reason": "rotation", "stop_delay_sessions": None}
    )
    tags = retrospect.deviation_tags(
        plan=plan,
        events=[
            _event("rotation_instruction", date(2026, 11, 2), "换仓指令：卖出 150 股，换至 BBB"),
            _event("stop_order_confirmed", date(2026, 11, 3)),
        ],
        trades=[
            _trade("buy", date(2026, 11, 3), "32.00", 300, tid=1),
            _trade("sell", date(2026, 11, 10), "31.00", 300, tid=2),
        ],
        bars=[],
    )
    assert "late_sell_after_instruction" not in [tag["code"] for tag in tags]
    assert tags == []
