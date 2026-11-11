"""v3.3 rotation orchestration over the v3 database (spec §5, §6): exposure, the short-of-room gate,
comparable holdings, the comparison table and the withdrawal of orphaned rotation instructions.

Reference numbers (``_rotation_seed.seed_c4``): equity 100160.50, holdings 55020.00 (AAA 12040 +
CCC 42980), cap 0.6 → room 5076.30. BBB: close 50, ATR 2 → levels 49 / 51 / 46 / 58, risk
1001.605 → 250 shares uncapped, 99 inside the room. Half AAA: sell 150 (6000) → room 11076.30 →
217; full AAA: sell 301 (12040) → room 17116.30 → 335, so the risk term gives 250."""

from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from _rotation_seed import (
    CFG,
    NOW,
    SESSION,
    add_holding,
    add_rotation,
    holding_review,
    plan_row,
    seed_c4,
    seed_opening,
    seed_run,
)

from investment_assistant.config import AssistantConfig, WorkbenchConfig
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.execution.rotation import BLOCKED_TEXT, ENDED_TEXT, INELIGIBLE_TEXT
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts import rotation as facts_rotation

KW = {"session": SESSION, "cutoff_at": NOW, "cfg": CFG}


def test_exposure_row_matches_hand_calculation(v3_db):
    """C1：55000 ÷ 100000 = 0.5500；剩余额度 = 100000 × 0.6 − 55000 = 5000.00；上限 1.0 时额度更大。"""
    seed_run(v3_db)
    seed_opening(v3_db)
    add_holding(v3_db, "AAA", 300, "40", 40.0)
    add_holding(v3_db, "CCC", 1000, "43", 43.0)
    v3_db.commit()

    row = facts_rotation.exposure_row(v3_db, **KW)

    assert row == {"pct": D("0.5500"), "cap": D("0.6"), "room": D("5000.00"), "incomplete": False}
    loose = AssistantConfig(workbench=WorkbenchConfig(total_cap=1.0))
    assert facts_rotation.exposure_row(v3_db, session=SESSION, cutoff_at=NOW, cfg=loose)["room"] == D("45000.00")


def test_gate_room_enough(v3_db):
    """C2：持仓市值 45000 → 额度内 294 股、不受限 250 股 → 不是「钱不够」。"""
    seed_run(v3_db)
    seed_opening(v3_db)
    add_holding(v3_db, "AAA", 300, "40", 40.0)
    add_holding(v3_db, "CCC", 1000, "33", 33.0)
    v3_db.commit()

    gate = facts_rotation.gate(v3_db, ticker="BBB", mode="brief", **KW)

    assert gate == {
        "short": False,
        "compare": False,
        "blocked": None,
        "blocked_text": None,
        "last_session": None,
        "sizing": None,
    }


def test_gate_short_and_comparable(v3_db):
    seed_c4(v3_db)
    v3_db.commit()

    gate = facts_rotation.gate(v3_db, ticker="BBB", mode="brief", **KW)

    assert gate["short"] is True and gate["compare"] is True
    assert gate["blocked"] is None and gate["blocked_text"] is None and gate["last_session"] is None
    assert gate["sizing"]["levels"]["stop"] == D("46.00")
    assert gate["sizing"]["sizing"]["shares"] == 99 and gate["sizing"]["sizing"]["uncapped_shares"] == 250


def test_rule_sizing_subtracts_the_sold_value(v3_db):
    seed_c4(v3_db)
    v3_db.commit()

    assert facts_rotation.rule_sizing(v3_db, ticker="BBB", **KW)["sizing"]["shares"] == 99
    sold = D("6000")
    assert facts_rotation.rule_sizing(v3_db, ticker="BBB", sold_value=sold, **KW)["sizing"]["shares"] == 217
    # 卖出价值多过持仓时额度也只受不受限的风险项约束
    assert facts_rotation.rule_sizing(v3_db, ticker="BBB", sold_value=D("55020"), **KW)["sizing"]["shares"] == 250
    assert facts_rotation.rule_sizing(v3_db, ticker="ZZZ", **KW) is None  # 没有日线


def test_gate_blocked_reasons(v3_db):
    """no_holding / compared_today / cooldown 的顺序与口径：四个交易日前的半仓仍在冷却期，五个交易日前可比较；
    只有继续持有不冷却；对话模式不被「当天已比较」与冷却挡。"""
    ids = seed_c4(v3_db)
    v3_db.commit()
    holding = (ids["a"]["id"], "AAA")

    def gate(mode="brief"):
        return facts_rotation.gate(v3_db, ticker="BBB", mode=mode, **KW)

    add_rotation(v3_db, session=SESSION, verdict="hold", origin="brief", holding=holding)
    brief = gate()
    assert brief["compare"] is False and brief["blocked"] == "compared_today"
    assert brief["blocked_text"] == BLOCKED_TEXT["compared_today"]
    assert gate("chat")["compare"] is True  # 对话里的比较不受当天一次限制

    v3_db.execute("DELETE FROM v3_rotations")  # 只有继续持有 → 不冷却
    add_rotation(v3_db, session=add_trading_days(SESSION, -2), verdict="hold", origin="brief", holding=holding)
    assert gate()["compare"] is True

    four_ago = add_trading_days(SESSION, -4)  # 到当天（两端都算）共 5 个交易日
    add_rotation(v3_db, session=four_ago, verdict="half", origin="chat", holding=holding)
    cooling = gate()
    assert cooling["compare"] is False and cooling["blocked"] == "cooldown"
    assert cooling["last_session"] == four_ago and f"{four_ago:%m-%d}" in cooling["blocked_text"]
    assert gate("chat")["compare"] is True

    v3_db.execute("DELETE FROM v3_rotations")
    add_rotation(v3_db, session=add_trading_days(SESSION, -5), verdict="half", origin="chat", holding=holding)
    assert gate()["compare"] is True  # 五个交易日前：冷却结束

    # 没有可比持仓：两只都已有卖出指令（须在没有记录挡住之前判）
    v3_db.execute("DELETE FROM v3_rotations")
    for key in ("a", "c"):
        plan_repo.update_plan(v3_db, ids[key]["id"], sell_instruction="max_hold")
    empty = gate()
    assert empty["short"] is True and empty["compare"] is False and empty["blocked"] == "no_holding"
    assert empty["blocked_text"] == BLOCKED_TEXT["no_holding"]


def test_missing_close_blocks_comparison_and_warns(v3_db):
    """Review Focus 1：一只持仓没有日线 → exposure_row.incomplete；「钱不够」时闸门原因 incomplete。"""
    seed_c4(v3_db)
    add_holding(v3_db, "DDD", 10, "10", 10.0, with_bars=False)
    v3_db.commit()

    assert facts_rotation.exposure_row(v3_db, **KW)["incomplete"] is True
    gate = facts_rotation.gate(v3_db, ticker="BBB", mode="brief", **KW)
    assert gate["short"] is True and gate["compare"] is False
    assert gate["blocked"] == "incomplete" and gate["blocked_text"] == BLOCKED_TEXT["incomplete"]


def test_no_equity_never_short(v3_db):
    """C19：没有任何资金记录 → 不算占比、不判钱不够。"""
    seed_run(v3_db)
    add_holding(v3_db, "AAA", 300, "40", 40.0)
    add_holding(v3_db, "CCC", 1000, "43", 43.0)
    v3_db.commit()

    row = facts_rotation.exposure_row(v3_db, **KW)
    assert row["pct"] is None and row["room"] is None
    assert facts_rotation.gate(v3_db, ticker="BBB", mode="brief", **KW)["short"] is False


def test_comparable_excludes_each_reason(v3_db):
    """止损触及、已有卖出指令、缺日线、复查不成立各一只；缺复查只在 require_review 时排除。"""
    seed_run(v3_db)
    seed_opening(v3_db)
    ok = add_holding(v3_db, "OKK", 100, "40", 40.0)
    stop_hit = add_holding(v3_db, "STP", 100, "40", 40.0, stop_hit_session=SESSION)
    instructed = add_holding(v3_db, "INS", 100, "40", 40.0, sell_instruction="max_hold")
    unchecked = add_holding(v3_db, "UNC", 100, "40", 40.0, bars_end=add_trading_days(SESSION, -1))
    broken = add_holding(v3_db, "BRK", 100, "40", 40.0)
    unreviewed = add_holding(v3_db, "NRV", 100, "40", 40.0)
    for plan in (ok, stop_hit, instructed, unchecked):
        holding_review(v3_db, plan["id"], "holds")
    holding_review(v3_db, broken["id"], "broken")
    v3_db.commit()

    strict = facts_rotation.comparable(v3_db, require_review=True, **KW)

    assert [row["ticker"] for row in strict["holdings"]] == ["OKK"]
    reasons = {row["ticker"]: row["reason"] for row in strict["excluded"]}
    assert reasons == {
        "STP": "stop_hit",
        "INS": "sell_instruction",
        "UNC": "unchecked",
        "BRK": "review_broken",
        "NRV": "review_missing",
    }
    assert {row["text"] for row in strict["excluded"]} == {INELIGIBLE_TEXT[code] for code in reasons.values()}
    assert all({"plan_id", "ticker", "reason", "text"} == set(row) for row in strict["excluded"])
    lenient = facts_rotation.comparable(v3_db, require_review=False, **KW)
    assert sorted(row["ticker"] for row in lenient["holdings"]) == ["NRV", "OKK"]
    row = strict["holdings"][0]
    assert row["position"]["quantity"] == D(100) and row["last_close"] == D("40.0") and row["id"] == ok["id"]


def test_comparison_table_numbers(v3_db):
    """C4 的手算：AAA 301 股收盘 40 → 半仓卖 150（6000，赚 75）、BBB 买 217；全仓卖 301、买 250。"""
    ids = seed_c4(v3_db)
    v3_db.commit()

    table = facts_rotation.comparison_table(v3_db, candidate="BBB", **KW)

    assert table["session"] == SESSION
    assert table["candidate"] == {
        "ticker": "BBB",
        "rank": 1,
        "reason": "BBB 的理由行",
        "rs": "1.5000",
        "industry": "半导体",
        "industry_tier": "有利",
        "setup_tier": "有利",
        "levels": {"entry_low": "49.00", "entry_high": "51.00", "stop": "46.00", "target1": "58.00"},
        "risk_amount": "1001.61",
        "uncapped_shares": 250,
        "room_shares": 99,
    }
    assert table["account"] == {
        "equity": "100160.50",
        "market_value": "55020.00",
        "pct": "0.5493",
        "cap": "0.6",
        "room": "5076.30",
    }
    aaa, ccc = table["holdings"]
    assert (aaa["plan_id"], aaa["ticker"], aaa["kind"]) == (ids["a"]["id"], "AAA", "planned")
    assert (aaa["quantity"], aaa["avg_cost"], aaa["last_close"]) == ("301", "39.50", "40.00")
    assert (aaa["market_value"], aaa["weight_pct"]) == ("12040.00", "0.1202")
    assert (aaa["unrealized_pnl"], aaa["unrealized_pct"]) == ("150.50", "0.0127")
    assert (aaa["r_multiple"], aaa["r_text"]) == ("0.14", None)  # (40 − 39.5) ÷ (39.5 − 36)
    assert (aaa["stop"], aaa["to_stop_pct"], aaa["trailing"]) == ("36.00", "0.1000", False)
    assert (aaa["held_sessions"], aaa["sessions_left"]) == (5, 15)
    assert aaa["review"] == {"verdict": "holds", "reasons": "论点仍成立"}
    assert aaa["rs"] == "0.0000" and aaa["industry"] == "未归类" and aaa["industry_tier"] == "样本不足"
    assert aaa["half"] == {"sell_quantity": "150", "proceeds": "6000.00", "realized_pnl": "75.00", "buy_shares": 217}
    assert aaa["full"] == {"sell_quantity": "301", "proceeds": "12040.00", "realized_pnl": "150.50", "buy_shares": 250}
    assert ccc["kind"] == "adopted" and ccc["r_multiple"] is None and ccc["r_text"] == "补录，不计"
    assert ccc["review"]["verdict"] == "unknown"
    assert ccc["half"]["sell_quantity"] == "500" and ccc["half"]["buy_shares"] == 250
    assert table["excluded"] == []
    assert table["fees"] == {"per_trade": "0.00", "total": "0.00", "sample": 2, "note": None}
    assert table["history"] == {"last_session": None, "recent_count": 0, "window_sessions": 20}


def test_comparison_table_history_and_excluded(v3_db):
    ids = seed_c4(v3_db)
    stopped = add_holding(v3_db, "STP", 10, "40", 40.0, stop_hit_session=SESSION)
    holding_review(v3_db, stopped["id"], "holds")
    last = add_trading_days(SESSION, -7)
    add_rotation(v3_db, session=last, verdict="full", origin="chat", holding=(ids["c"]["id"], "CCC"))
    add_rotation(v3_db, session=SESSION - timedelta(days=60), verdict="half", origin="chat", holding=(ids["a"]["id"], "AAA"))
    v3_db.commit()

    table = facts_rotation.comparison_table(v3_db, candidate="BBB", **KW)

    assert table["history"] == {"last_session": last, "recent_count": 1, "window_sessions": 20}
    assert table["excluded"] == [
        {"plan_id": stopped["id"], "ticker": "STP", "reason": "stop_hit", "text": INELIGIBLE_TEXT["stop_hit"]}
    ]
    assert [row["ticker"] for row in table["holdings"]] == ["AAA", "CCC"]


def _orphan_pair(conn, a_ticker, b_ticker, status, *, a_quantity=100):
    """一份带换仓指令的 A（open）与资金来自这次换仓的 B（``status``）。"""
    holding = add_holding(conn, a_ticker, a_quantity, "40", 40.0)
    rotation = add_rotation(
        conn, session=SESSION, origin="chat", candidate=b_ticker, holding=(holding["id"], a_ticker),
        sell_quantity=50, request_id=f"orphan-{a_ticker}",
    )  # fmt: skip
    plan_repo.update_plan(
        conn, holding["id"], sell_instruction="rotation", sell_quantity=D(50), rotation_id=rotation["id"]
    )
    extra = {} if status in ("pending", "open") else {"ended_session": SESSION, "end_reason": "结束"}
    funded = plan_repo.insert_plan(
        conn, plan_row(b_ticker, status=status, funded_by_rotation_id=rotation["id"], **extra)
    )
    return holding, rotation, funded


def test_withdraw_orphans_for_each_ending(v3_db):
    """C8：B 的计划 expired / invalidated / replaced / cancelled 各一例 → A 的三列清空，事件原因逐字；
    B 仍待入场或已买入时不动。"""
    seed_opening(v3_db)
    pairs = {
        status: _orphan_pair(v3_db, a, b, status)
        for status, a, b in (
            ("expired", "AEX", "BEX"),
            ("invalidated", "AIN", "BIN"),
            ("replaced", "ARE", "BRE"),
            ("cancelled", "ACA", "BCA"),
            ("pending", "APE", "BPE"),
            ("open", "AOP", "BOP"),
        )
    }
    v3_db.commit()

    events = facts_rotation.withdraw_orphans(v3_db, session=SESSION)

    assert len(events) == 4
    for status in ("expired", "invalidated", "replaced", "cancelled"):
        holding, rotation, funded = pairs[status]
        plan = plan_repo.plan(v3_db, holding["id"])
        assert (plan["sell_instruction"], plan["sell_quantity"], plan["rotation_id"]) == (None, None, None)
        event = [e for e in plan_repo.events(v3_db, holding["id"]) if e["event"] == "rotation_withdrawn"][0]
        assert event["reason"] == f"换仓指令撤回：{funded['ticker']} 的计划{ENDED_TEXT[status]}"
        assert event["source"] == "host" and event["session"] == SESSION
        assert event["before"] == {"rotation_id": rotation["id"], "sell_quantity": "50"}
    for status in ("pending", "open"):
        holding, rotation, _ = pairs[status]
        plan = plan_repo.plan(v3_db, holding["id"])
        assert plan["sell_instruction"] == "rotation" and plan["rotation_id"] == rotation["id"]
        assert [e for e in plan_repo.events(v3_db, holding["id"]) if e["event"] == "rotation_withdrawn"] == []
    assert facts_rotation.withdraw_orphans(v3_db, session=SESSION) == []  # 重跑不再产生事件


def test_withdraw_orphans_without_a_funded_plan(v3_db):
    """B 的计划不存在（不应出现，但不能留下孤立的指令）：同样清空并写原因。"""
    seed_opening(v3_db)
    holding = add_holding(v3_db, "AAA", 100, "40", 40.0)
    rotation = add_rotation(v3_db, holding=(holding["id"], "AAA"), sell_quantity=50)
    plan_repo.update_plan(
        v3_db, holding["id"], sell_instruction="rotation", sell_quantity=D(50), rotation_id=rotation["id"]
    )
    v3_db.commit()

    events = facts_rotation.withdraw_orphans(v3_db, session=SESSION)

    assert len(events) == 1 and events[0]["reason"] == "换仓指令撤回：BBB 的计划不存在"
    assert plan_repo.plan(v3_db, holding["id"])["rotation_id"] is None


def test_verdicts_helper_is_unaffected(v3_db):
    """复查结论读法与 plan_repo 一致（防止 comparable 读错当天）。"""
    ids = seed_c4(v3_db)
    v3_db.commit()
    assert set(plan_repo.holding_reviews(v3_db, SESSION)) == {ids["a"]["id"], ids["c"]["id"]}
    assert facts_repo.verdicts(v3_db, SESSION) == {}


@pytest.mark.parametrize("mode", ["brief", "chat"])
def test_gate_without_candidate_bars_is_not_short(v3_db, mode):
    seed_c4(v3_db)
    v3_db.commit()
    assert facts_rotation.gate(v3_db, ticker="ZZZ", mode=mode, **KW)["short"] is False
