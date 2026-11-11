"""v3.3 rotation pure functions (spec §5, §6, §9): exposure, the short-of-room test, eligibility,
sale numbers, cooldown, fee estimate, the after-the-fact comparison and the summary."""

from datetime import date, datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest

from investment_assistant.config import WorkbenchConfig
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.execution import plan_levels, rotation

NY = ZoneInfo("America/New_York")
SNAPSHOT = {"id": 1, "equity": D(100000), "as_of": date(2026, 11, 10)}


def _size(market_value, *, total_cap=0.6):
    result, _ = plan_levels.size(
        snapshot=SNAPSHOT,
        ref_price=D(50),
        stop=D(46),
        entry_high=D("50.5"),
        ticker_value=D(0),
        total_value=D(market_value),
        params=WorkbenchConfig(total_cap=total_cap),
    )
    return result


def test_size_reports_uncapped_shares():
    sized = _size(55000)
    assert sized["shares"] == 99 and sized["applied_cap"] == "total" and sized["status"] == "capped"
    assert sized["uncapped_shares"] == 250  # min(风险一项 250, 单只一项 495)
    no_equity, _ = plan_levels.size(
        snapshot=None,
        ref_price=D(50),
        stop=D(46),
        entry_high=D("50.5"),
        ticker_value=D(0),
        total_value=D(0),
        params=WorkbenchConfig(),
    )
    assert no_equity["uncapped_shares"] is None and no_equity["status"] == "no_equity"


def test_lower_cap_never_gives_more_shares():
    for market_value in (0, 30000, 55000, 70000):
        assert _size(market_value, total_cap=0.6)["shares"] <= _size(market_value, total_cap=1.0)["shares"]


def test_short_of_room_threshold():
    min_fill = D("0.5")
    assert rotation.short_of_room(_size(55000), min_fill=min_fill) is True  # 99 < 125
    assert rotation.short_of_room(_size(52000), min_fill=min_fill) is False  # 158
    assert rotation.short_of_room(_size(45000), min_fill=min_fill) is False  # 250
    assert rotation.short_of_room(_size(70000), min_fill=min_fill) is True  # shares <= 0
    no_equity = {"status": "no_equity", "shares": None, "uncapped_shares": None}
    assert rotation.short_of_room(no_equity, min_fill=min_fill) is False


def test_exposure_basic():
    row = rotation.exposure(equity=D(100000), market_value=D(55000), cap=D("0.6"))
    assert row == {"pct": D("0.5500"), "cap": D("0.6"), "room": D("5000.00"), "incomplete": False}
    over = rotation.exposure(equity=D(100000), market_value=D(70000), cap=D("0.6"))
    assert over["pct"] == D("0.7000") and over["room"] == D(0)


def test_exposure_without_positive_equity():
    for equity in (None, D(0), D(-5)):
        row = rotation.exposure(equity=equity, market_value=D(100), cap=D("0.6"))
        assert row["pct"] is None and row["room"] is None


def test_exposure_marks_incomplete_when_a_close_is_missing():
    row = rotation.exposure(equity=D(100000), market_value=D(55000), cap=D("0.6"), missing_close=True)
    assert row["incomplete"] is True
    assert rotation.exposure_text(row).endswith("（有持仓缺日线，占比偏低）")
    assert "有持仓缺日线" not in rotation.exposure_text({**row, "incomplete": False})


def test_exposure_text_copy():
    row = rotation.exposure(equity=D(100000), market_value=D(55000), cap=D("0.6"))
    assert rotation.exposure_text(row) == "仓位占比 55.0%（上限 60%），剩余额度 $5000.00"
    empty = rotation.exposure(equity=None, market_value=D(0), cap=D("0.6"))
    assert rotation.exposure_text(empty) == "仓位占比 —（未提供账户资金）"


def test_ineligible_reason_order():
    plan = {"stop_hit_session": None, "sell_instruction": None}
    holds = {"verdict": "holds"}

    def reason(plan_over=None, *, unchecked=False, review=holds, require_review=True):
        return rotation.ineligible_reason(
            plan={**plan, **(plan_over or {})}, unchecked=unchecked, review=review, require_review=require_review
        )

    assert reason({"stop_hit_session": date(2026, 11, 9), "sell_instruction": "max_hold"}, unchecked=True) == "stop_hit"
    assert reason({"sell_instruction": "max_hold"}) == "sell_instruction"
    assert reason({"sell_instruction": "rotation"}) == "sell_instruction"
    assert reason(unchecked=True, review={"verdict": "broken"}) == "unchecked"
    assert reason(review={"verdict": "broken"}) == "review_broken"
    assert reason(review=None) == "review_missing"
    assert reason(review=None, require_review=False) is None
    assert reason() is None
    assert reason(review={"verdict": "unknown"}) is None


def test_sell_quantity_floor_and_minimum():
    assert rotation.sell_quantity("half", D(301)) == D(150)
    assert rotation.sell_quantity("half", D("3.5")) == D(1)
    for quantity in (D("1.5"), D(1)):
        with pytest.raises(ValueError, match="half_needs_two_shares"):
            rotation.sell_quantity("half", quantity)
    assert rotation.sell_quantity("full", D(301)) == D(301)
    assert rotation.sell_quantity("full", D("1.5")) == D("1.5")
    with pytest.raises(ValueError):
        rotation.sell_quantity("hold", D(10))


def test_cooldown_window():
    monday = date(2026, 11, 9)
    records = [{"verdict": "half", "session": monday}]
    assert rotation.cooldown_last(records, session=monday, cooldown_sessions=5) == monday
    assert rotation.cooldown_last(records, session=date(2026, 11, 13), cooldown_sessions=5) == monday
    assert rotation.cooldown_last(records, session=date(2026, 11, 16), cooldown_sessions=5) is None
    assert rotation.cooldown_last([{"verdict": "hold", "session": monday}], session=monday, cooldown_sessions=5) is None
    later = [*records, {"verdict": "full", "session": date(2026, 11, 11)}]
    assert rotation.cooldown_last(later, session=date(2026, 11, 13), cooldown_sessions=5) == date(2026, 11, 11)


def _fee_trade(fee, day, *, voided=None):
    return {"fee": D(str(fee)), "executed_at": datetime(2026, 11, day, 10, tzinfo=NY), "voided_at": voided}


def test_fee_estimate_median_and_empty():
    voided = datetime(2026, 11, 6, tzinfo=NY)
    trades = [_fee_trade(0, 2), _fee_trade(5, 3), _fee_trade(1, 4), _fee_trade(99, 5, voided=voided)]
    estimate = rotation.fee_estimate(trades, lookback=20)
    assert estimate["per_trade"] == D(1) and estimate["total"] == D(2) and estimate["sample"] == 3
    assert estimate["note"] is None
    recent = rotation.fee_estimate(trades, lookback=1)  # 只取最近一笔未作废成交（5 号作废，4 号的费用 1）
    assert recent["per_trade"] == D(1) and recent["sample"] == 1
    empty = rotation.fee_estimate([], lookback=20)
    assert empty["per_trade"] == D(0) and empty["total"] == D(0) and empty["sample"] == 0
    assert empty["note"] == "没有费用记录，按 0 计"


def test_sale_numbers():
    half = rotation.sale(quantity=D(301), avg_cost=D(38), last_close=D(40), verdict="half")
    assert half == {"sell_quantity": D(150), "proceeds": D("6000.00"), "realized_pnl": D("300.00")}
    full = rotation.sale(quantity=D(301), avg_cost=D(38), last_close=D(40), verdict="full")
    assert full == {"sell_quantity": D(301), "proceeds": D("12040.00"), "realized_pnl": D("602.00")}
    assert rotation.sale(quantity=D(1), avg_cost=D(38), last_close=D(40), verdict="half") is None


def test_over_cap_sentence():
    assert rotation.over_cap_sentence(D("0.632"), D("0.6")) == "买入后仓位占比 63.2%，高于上限 60%"


def _bars(count, *, open_first, close_of, start=date(2026, 11, 10)):
    """`count` bars on the sessions after `start`, plus one bar on `start` itself that must be ignored."""
    rows = [
        {"price_date": start, "open": D(1), "close": D(1), "adj_close": D(1)},
    ]
    for index in range(1, count + 1):
        close = D(str(close_of(index)))
        rows.append(
            {
                "price_date": add_trading_days(start, index),
                "open": D(str(open_first)) if index == 1 else close,
                "close": close,
                "adj_close": close,
            }
        )
    return rows


def test_compare_returns_done_and_unfinished():
    session = date(2026, 11, 10)
    bars_a = _bars(21, open_first=100, close_of=lambda i: 100 + 2 * i)  # 入场 100；5 日 110、10 日 120、20 日 140
    bars_b = _bars(21, open_first=50, close_of=lambda i: D("50") + D("0.5") * i)  # 入场 50；5 日 52.5、10 日 55、20 日 60
    rows = rotation.compare_returns(bars_a=bars_a, bars_b=bars_b, session=session, horizons=(5, 10, 20))
    assert [row["horizon"] for row in rows] == [5, 10, 20] and all(row["status"] == "done" for row in rows)
    assert rows[0] == {"horizon": 5, "status": "done", "a_pct": D("0.1000"), "b_pct": D("0.0500"), "diff": D("-0.0500")}
    assert (rows[1]["a_pct"], rows[1]["b_pct"], rows[1]["diff"]) == (D("0.2000"), D("0.1000"), D("-0.1000"))
    assert (rows[2]["a_pct"], rows[2]["b_pct"], rows[2]["diff"]) == (D("0.4000"), D("0.2000"), D("-0.2000"))
    short_b = _bars(7, open_first=50, close_of=lambda i: D("50") + D("0.5") * i)
    partial = rotation.compare_returns(bars_a=bars_a, bars_b=short_b, session=session, horizons=(5, 10, 20))
    assert partial[0]["status"] == "done" and partial[0]["diff"] == D("-0.0500")
    for row in partial[1:]:
        assert row == {"horizon": row["horizon"], "status": "unfinished", "a_pct": None, "b_pct": None, "diff": None}


def test_compare_returns_uses_adjusted_open():
    session = date(2026, 11, 10)
    first = add_trading_days(session, 1)
    second = add_trading_days(session, 2)
    # 第一天 close 100、adj_close 50（之后拆股）：入场价 = open × adj_close ÷ close = 100 × 50 ÷ 100 = 50
    bars = [
        {"price_date": first, "open": D(100), "close": D(100), "adj_close": D(50)},
        {"price_date": second, "open": D(52), "close": D(55), "adj_close": D(55)},
    ]
    rows = rotation.compare_returns(bars_a=bars, bars_b=bars, session=session, horizons=(2,))
    assert rows[0]["a_pct"] == D("0.1000") and rows[0]["b_pct"] == D("0.1000") and rows[0]["diff"] == D("0.0000")


def test_execution_state():
    record = {"id": 5, "sell_quantity": D(150)}

    def sell(quantity, day):
        return {"quantity": D(quantity), "executed_at": datetime(2026, 11, day, 10, tzinfo=NY)}

    done = rotation.execution_state(rotation=record, events_a=[], sells_a=[sell(150, 12)], plan_b=None)
    assert done == {"state": "done", "sold_quantity": D(150), "sold_session": date(2026, 11, 12), "bought": False}
    split = rotation.execution_state(rotation=record, events_a=[], sells_a=[sell(100, 12), sell(50, 13)], plan_b=None)
    assert split["state"] == "done" and split["sold_quantity"] == D(150) and split["sold_session"] == date(2026, 11, 13)
    pending = rotation.execution_state(rotation=record, events_a=[], sells_a=[sell(100, 12)], plan_b={"status": "open"})
    assert pending["state"] == "pending" and pending["sold_quantity"] == D(100) and pending["bought"] is True
    nothing = rotation.execution_state(rotation=record, events_a=[], sells_a=[], plan_b={"status": "pending"})
    assert nothing == {"state": "pending", "sold_quantity": D(0), "sold_session": None, "bought": False}
    withdrawn_event = {"event": "rotation_withdrawn", "before": {"rotation_id": 5}, "after": {}}
    state = rotation.execution_state(rotation=record, events_a=[withdrawn_event], sells_a=[], plan_b=None)
    assert state["state"] == "withdrawn"
    before = {"rotation_id": 5, "sell_quantity": D(150)}
    superseded_event = {"event": "rotation_superseded", "before": before, "after": {}}
    state = rotation.execution_state(rotation=record, events_a=[superseded_event], sells_a=[], plan_b=None)
    assert state["state"] == "superseded"
    other = {"event": "rotation_withdrawn", "before": {"rotation_id": 9}, "after": {}}
    assert rotation.execution_state(rotation=record, events_a=[other], sells_a=[], plan_b=None)["state"] == "pending"
    closed = rotation.execution_state(rotation=record, events_a=[], sells_a=[], plan_b={"status": "closed"})
    assert closed["bought"] is True


def _record(verdict, diff, *, state="done", fee="2.00", status="done"):
    horizons = [{"horizon": 5, "status": "done", "a_pct": D("0.01"), "b_pct": D("0.02"), "diff": D("0.01")}]
    if status == "done":
        pct_b = D("0.10") + diff
        horizons.append({"horizon": 20, "status": "done", "a_pct": D("0.10"), "b_pct": pct_b, "diff": diff})
    else:
        horizons.append({"horizon": 20, "status": "unfinished", "a_pct": None, "b_pct": None, "diff": None})
    return {"verdict": verdict, "state": state, "fee": D(fee), "returns": horizons}


def test_rotation_summary_sample_gate():
    nine = [_record("half", D("0.05") if index % 3 else D("-0.02")) for index in range(9)]
    summary = rotation.rotation_summary(nine, min_sample=10)
    assert summary["switched"] == {"sample": 9, "enough": False, "b_win_rate": None, "mean_diff": None}
    assert summary["held"] == {"sample": 0, "enough": False, "b_win_rate": None, "mean_diff": None}

    ten = [_record("half" if index % 2 else "full", D("0.05") if index < 7 else D("-0.02")) for index in range(10)]
    held = [_record("hold", D("0.04"), state=None, fee="0"), _record("hold", D("-0.04"), state=None, fee="0")]
    unfinished = [
        _record("full", D("0.01"), status="unfinished"),
        _record("half", D("0.01"), state="withdrawn", fee="3.00", status="unfinished"),
    ]
    summary = rotation.rotation_summary([*ten, *held, *unfinished], min_sample=10)
    assert summary["counts"] == {"hold": 2, "half": 6, "full": 6}
    assert summary["executed"] == 11  # 十条已执行 + 一条未完成但状态是 done 的 full（state 默认 done）
    assert summary["fee_total"] == D("22.00")  # 11 条 state=done 各 2.00；撤回的那条不算
    assert summary["switched"]["sample"] == 10 and summary["switched"]["enough"] is True
    assert summary["switched"]["b_win_rate"] == D("0.7")
    assert summary["switched"]["mean_diff"] == D("0.029")  # (7 × 0.05 − 3 × 0.02) ÷ 10
    assert summary["held"] == {"sample": 2, "enough": False, "b_win_rate": None, "mean_diff": None}
    enough_hold = rotation.rotation_summary(held * 5, min_sample=10)
    assert enough_hold["held"]["enough"] is True
    assert enough_hold["held"]["b_win_rate"] == D("0.5") and enough_hold["held"]["mean_diff"] == D("0")
