"""The three rotation tools over the v3_tx database (spec §6.4–§6.6, §7.3, §10): the comparison table,
save_rotation with its fixed check order and one-transaction write, and withdraw_rotation.

Scenario ``_rotation_seed.seed_c4``: AAA 301 shares (close 40) and CCC 1000 shares (adopted, close 42.98),
BBB the short-of-room candidate. Half AAA → sell 150, BBB 217 shares; full AAA → sell 301, BBB 250 shares."""

from datetime import date
from decimal import Decimal as D

import pytest
from _rotation_seed import (
    CFG,
    NOW,
    SESSION,
    add_holding,
    add_rotation,
    bars as seed_bars,
    holding_review,
    plan_row,
    seed_c4,
    seed_opening,
    seed_run,
)

from investment_assistant.data import repo as data_repo
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.execution.rotation import INELIGIBLE_TEXT
from investment_assistant.facts import plan_repo, rotation_repo
from investment_assistant.toolsurface.dispatch import call
from investment_assistant.toolsurface.env import make_env

TEXTS = {"thesis": "放量突破后回踩入场", "invalidation": "跌破 46 止损"}


def _env(mode="brief", agent="t-rot"):
    return make_env(session=SESSION, mode=mode, agent_session=agent, cfg=CFG, now=NOW)


def _verdict(env, ticker, verdict="confirm"):
    ref = call(env, "get_market", {})["ref"]
    result = call(
        env, "save_verdict", {"ticker": ticker, "verdict": verdict, "reasons": "理由充分", "refs": [ref]}
    )
    assert "data" in result, result
    return result


def _reads(env, holding="AAA", candidate="BBB"):
    """比较表 + 对 A 的单只读取，返回两条 ref。"""
    table = call(env, "get_rotation_table", {"ticker": candidate})
    assert "data" in table, table
    read = call(env, "get_price_bars", {"ticker": holding})
    assert "data" in read, read
    return [table["ref"], read["ref"]]


def _half(env, holding, refs, **over):
    args = {
        "ticker": "BBB",
        "verdict": "half",
        "reasons": "BBB 的相对强度与行业都更强",
        "holding_plan_id": holding["id"],
        "weakness": "AAA 持有数日没有放量，和论点不符",
        "counter": "AAA 行业近期偏弱但没破位",
        "cost_response": "两笔手续费合计很小，相对换仓规模可以接受",
        "refs": refs,
        **TEXTS,
    }
    args.update(over)
    return call(env, "save_rotation", {key: value for key, value in args.items() if value is not None})


def _count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _setup(v3_tx, mode="brief", agent="t-rot", tickers=("BBB",)):
    ids = seed_c4(v3_tx, tickers)
    v3_tx.commit()
    env = _env(mode, agent)
    _verdict(env, "BBB")
    return env, ids


def _unchanged(v3_tx, ids, *, rotations=0, plans=None):
    assert _count(v3_tx, "v3_rotations") == rotations
    if plans is not None:
        assert _count(v3_tx, "v3_plans") == plans
    for key in ("a", "c"):
        plan = plan_repo.plan(v3_tx, ids[key]["id"])
        assert (plan["sell_instruction"], plan["sell_quantity"], plan["rotation_id"]) == (None, None, None)


def test_table_rejects_when_room_is_enough(v3_tx):
    """room_enough：钱够时读表与保存结论都拒绝，不写记录。"""
    seed_run(v3_tx, ("BBB",))
    seed_opening(v3_tx)
    held = add_holding(v3_tx, "AAA", 300, "40", 40.0)
    holding_review(v3_tx, held["id"])
    v3_tx.commit()
    env = _env()
    _verdict(env, "BBB")

    table = call(env, "get_rotation_table", {"ticker": "BBB"})
    assert table["error"] == "room_enough: 额度足够，直接出计划即可"
    saved = _half(env, held, [table["ref"]])
    assert saved["error"] == "room_enough: 额度足够，直接出计划即可"
    assert _count(v3_tx, "v3_rotations") == 0


def test_table_returns_the_host_table(v3_tx):
    env, ids = _setup(v3_tx, tickers=("BBB", "EEE"))

    table = call(env, "get_rotation_table", {"ticker": "BBB"})["data"]

    assert table["candidate"]["ticker"] == "BBB" and table["candidate"]["room_shares"] == 99
    assert [row["ticker"] for row in table["holdings"]] == ["AAA", "CCC"]
    assert table["holdings"][0]["half"]["buy_shares"] == 217 and table["holdings"][0]["full"]["buy_shares"] == 250
    # 表里没有 agent 可写的数字字段；单只读取之外读表本身不要求 B 之外的范围
    assert call(env, "get_rotation_table", {"ticker": "ZZZ"})["error"].startswith("out_of_scope")
    assert call(env, "get_rotation_table", {"ticker": "EEE"})["error"].startswith("no_confirm_verdict")
    assert ids["a"]["id"]


def test_hold_saves_a_record_and_blocks_the_plan(v3_tx):
    """C3：存「继续持有」→ 记录一行；之后 save_plan → no_room「仓位已到上限，比较后继续持有」；A 没有任何指令。"""
    env, ids = _setup(v3_tx)
    plain = call(env, "save_plan", {"ticker": "BBB", **TEXTS})
    assert plain["error"].startswith("needs_rotation: 仓位已到上限，需先保存换仓结论")
    assert plain["detail"]["short"] is True and plain["detail"]["compare"] is True

    refs = _reads(env)
    saved = call(
        env,
        "save_rotation",
        {"ticker": "BBB", "verdict": "hold", "reasons": "AAA 仍然强势", "counter": "BBB 也不错", "refs": refs,
         "holding_plan_id": ids["a"]["id"]},
    )  # fmt: skip

    assert "data" in saved, saved
    row = saved["data"]["rotation"]
    assert row["verdict"] == "hold" and row["origin"] == "brief" and row["holding_ticker"] == "AAA"
    assert saved["data"]["plan"] is None and saved["data"]["holding"] is None
    assert row["snapshot"]["candidate"]["ticker"] == "BBB" and row["sell_quantity"] is None
    assert call(env, "save_plan", {"ticker": "BBB", **TEXTS})["error"] == "no_room: 仓位已到上限，比较后继续持有"
    _unchanged(v3_tx, ids, rotations=1, plans=2)


def test_hold_requires_refs_and_a_valid_holding(v3_tx):
    env, ids = _setup(v3_tx)
    missing = call(env, "save_rotation", {"ticker": "BBB", "verdict": "hold", "reasons": "继续拿着"})
    assert missing["error"].startswith("invalid_refs")
    unknown = call(
        env, "save_rotation", {"ticker": "BBB", "verdict": "hold", "reasons": "继续拿着", "refs": ["call:999"]}
    )
    assert unknown["error"].startswith("invalid_refs")
    refs = _reads(env)
    nameless = call(env, "save_rotation", {"ticker": "BBB", "verdict": "hold", "reasons": "继续拿着", "refs": refs})
    assert nameless["data"]["rotation"]["holding_ticker"] is None  # 继续持有可以不指明 A
    _unchanged(v3_tx, ids, rotations=1)


def test_half_rotation_end_to_end(v3_tx):
    """C4：A sell_instruction rotation、sell_quantity 150；B 待入场、shares 217、funded_by_rotation_id；两条事件原因逐字。"""
    env, ids = _setup(v3_tx)

    saved = _half(env, ids["a"], _reads(env))

    assert "data" in saved, saved
    data = saved["data"]
    rotation, plan_b, plan_a = data["rotation"], data["plan"], data["holding"]
    assert rotation["verdict"] == "half" and rotation["sell_quantity"] == "150.000000"
    assert rotation["buy_shares"] == "217.000000" and rotation["holding_ticker"] == "AAA"
    assert rotation["weakness"] and rotation["cost_response"] and rotation["origin"] == "brief"
    assert rotation["snapshot"]["holdings"][0]["half"]["buy_shares"] == 217  # 当时摆出的那张表原样存下
    assert plan_a["sell_instruction"] == "rotation" and plan_a["sell_quantity"] == "150.000000"
    assert plan_a["rotation_id"] == rotation["id"]
    assert plan_b["status"] == "pending" and plan_b["shares"] == 217 and plan_b["ticker"] == "BBB"
    assert plan_b["funded_by_rotation_id"] == rotation["id"] and plan_b["levels_source"] == "host"
    assert plan_b["sizing"]["funded"] == {"rotation_id": rotation["id"], "holding_ticker": "AAA", "sell_quantity": "150"}
    assert plan_b["sizing"]["shares"] == 217
    instruction = [e for e in plan_repo.events(v3_tx, ids["a"]["id"]) if e["event"] == "rotation_instruction"]
    assert [(e["reason"], e["source"]) for e in instruction] == [("换仓指令：卖出 150 股，换至 BBB", "agent")]
    created = [e for e in plan_repo.events(v3_tx, plan_b["id"]) if e["event"] == "created"]
    assert [e["reason"] for e in created] == [f"{TEXTS['thesis']}；资金来自换仓 AAA"]
    stored = plan_repo.plan(v3_tx, ids["a"]["id"])
    assert stored["stop"] == ids["a"]["stop"]  # 换仓不改 A 的止损价
    # 表里的买入股数就是最终计划的股数（同一个口径、同一个数）
    assert plan_repo.plan_funded_by(v3_tx, rotation["id"])["id"] == plan_b["id"]


def test_full_rotation_end_to_end(v3_tx):
    """C5 前半：全仓卖出 301 股，B 买 250（风险项）。"""
    env, ids = _setup(v3_tx)

    saved = _half(env, ids["a"], _reads(env), verdict="full")["data"]

    assert saved["rotation"]["verdict"] == "full" and saved["holding"]["sell_quantity"] == "301.000000"
    assert saved["plan"]["shares"] == 250
    event = [e for e in plan_repo.events(v3_tx, ids["a"]["id"]) if e["event"] == "rotation_instruction"][0]
    assert event["reason"] == "换仓指令：卖出 301 股，换至 BBB"


def test_half_rejected_for_single_share(v3_tx):
    """C4：A 只有 1 股时半仓被拒绝，什么都不写。"""
    seed_run(v3_tx, ("BBB",))
    seed_opening(v3_tx)
    one = add_holding(v3_tx, "AAA", 1, "40", 40.0)
    big = add_holding(v3_tx, "CCC", 1340, "40", 40.0)  # 市值 53640：额度内 124 股 < 125 → 钱不够
    holding_review(v3_tx, one["id"])
    holding_review(v3_tx, big["id"])
    v3_tx.commit()
    env = _env()
    _verdict(env, "BBB")

    rejected = _half(env, one, _reads(env))

    assert rejected["error"].startswith("half_needs_two_shares")
    assert _count(v3_tx, "v3_rotations") == 0
    assert plan_repo.plan(v3_tx, one["id"])["sell_instruction"] is None


def test_rejections(v3_tx):
    """C6，逐条：每条之后库里没有新记录、A 没有指令。"""
    ids = seed_c4(v3_tx, ("EEE", "FFF", "GGG", "BBB"))  # 名额占满的场景在最后一步
    others = {
        "stop_hit": add_holding(v3_tx, "STP", 10, "40", 40.0, stop_hit_session=SESSION),
        "sell_instruction": add_holding(v3_tx, "INS", 10, "40", 40.0, sell_instruction="max_hold"),
        "unchecked": add_holding(v3_tx, "UNC", 10, "40", 40.0, bars_end=add_trading_days(SESSION, -1)),
        "review_broken": add_holding(v3_tx, "BRK", 10, "40", 40.0),
        "review_missing": add_holding(v3_tx, "NRV", 10, "40", 40.0),
    }
    for key in ("stop_hit", "sell_instruction", "unchecked"):
        holding_review(v3_tx, others[key]["id"], "holds")
    holding_review(v3_tx, others["review_broken"]["id"], "broken")
    v3_tx.commit()
    env = _env()
    plans = _count(v3_tx, "v3_plans")

    def check(result, code):
        assert result["error"].startswith(code), result
        _unchanged(v3_tx, ids, plans=plans)

    # B 当天没有确认结论
    check(_half(env, ids["a"], []), "no_confirm_verdict")
    _verdict(env, "BBB")
    refs = _reads(env)
    for reason, plan in others.items():
        check(_half(env, plan, refs), f"not_comparable: {INELIGIBLE_TEXT[reason]}")
    check(_half(env, ids["a"], refs, weakness=""), "weakness_required")
    check(_half(env, ids["a"], refs, cost_response=""), "cost_response_required")
    check(_half(env, ids["a"], refs, holding_plan_id=None), "holding_required")
    check(_half(env, ids["a"], [refs[0]]), "no_holding_read")  # 引用里只有比较表
    other_read = call(env, "get_price_bars", {"ticker": "CCC"})["ref"]
    check(_half(env, ids["a"], [refs[0], other_read]), "no_holding_read")  # 读的不是 A
    check(_half(env, ids["a"], refs, thesis=None), "invalid_argument")  # 半仓必须带 B 的论点
    check(_half(env, ids["a"], refs, verdict="maybe"), "invalid_verdict")
    check(_half(env, ids["a"], refs, reasons=""), "invalid_argument")
    check(_half(env, {"id": 99999}, refs), "unknown_plan")
    check(_half(env, ids["a"], refs, entry_type="market"), "invalid_entry_type")
    check(_half(env, ids["a"], refs, max_hold_sessions=40), "invalid_max_hold")

    # 名额已满且不满足替换（EEE、FFF、GGG 名次都在 BBB 之前）
    for ticker in ("EEE", "FFF", "GGG"):
        plan_repo.insert_plan(v3_tx, plan_row(ticker, valid_until=date(2026, 11, 20), entry_low=D(49), entry_high=D(51),
                                              stop=D(46), initial_stop=D(46), target1=D(58)))  # fmt: skip
    v3_tx.commit()
    plans = _count(v3_tx, "v3_plans")
    check(_half(env, ids["a"], refs), "replacement_rank_not_better")
    v3_tx.execute("DELETE FROM v3_plans WHERE status = 'pending'")
    # 财报进入有效期
    data_repo.store_earnings(v3_tx, "BBB", {"status": "known", "earnings_date": add_trading_days(SESSION, 2)}, now=NOW)
    v3_tx.commit()
    plans = _count(v3_tx, "v3_plans")
    check(_half(env, ids["a"], refs), "earnings_in_validity")


def test_no_comparable_holding_after_review_requirement(v3_tx):
    """闸门按前三条放行，但保存时要求当天复查结论：清单为空 → no_holding，不写记录。"""
    env, ids = _setup(v3_tx)
    refs = _reads(env)
    v3_tx.execute("DELETE FROM v3_holding_reviews")
    v3_tx.commit()

    result = _half(env, ids["a"], refs)

    assert result["error"] == "no_holding: 仓位已到上限，没有可比的持仓"
    _unchanged(v3_tx, ids)


def test_numeric_fields_are_rejected_by_schema(v3_tx):
    """C6：dispatch.call 带 shares / stop / price 任一 → 参数错误，不写入。"""
    env, ids = _setup(v3_tx)
    refs = _reads(env)
    for field, value in (("shares", 100), ("stop", 45), ("price", 50), ("quantity", 10), ("levels", {})):
        result = _half(env, ids["a"], refs, **{field: value})
        assert result["error"].startswith("unknown_argument"), (field, result)
    assert call(env, "withdraw_rotation", {"plan_id": 1, "request_id": "x", "confirmed": True, "shares": 1})[
        "error"
    ].startswith("not_available_in_mode")
    _unchanged(v3_tx, ids)


def test_failure_inside_the_transaction_leaves_nothing(v3_tx, monkeypatch):
    """C14：B 的计划写入失败（打桩 insert_plan 抛错）→ 没有记录、A 没有指令。"""
    env, ids = _setup(v3_tx)
    refs = _reads(env)

    def boom(conn, row):
        raise RuntimeError("boom")

    original = plan_repo.insert_plan
    monkeypatch.setattr(plan_repo, "insert_plan", boom)
    failed = _half(env, ids["a"], refs)

    assert "error" in failed and "boom" in failed["error"]
    monkeypatch.setattr(plan_repo, "insert_plan", original)
    _unchanged(v3_tx, ids, plans=2)
    retry = _half(env, ids["a"], refs)  # 重试重新做这一步
    assert "data" in retry and retry["data"]["plan"]["shares"] == 217


def test_brief_second_comparison_is_blocked(v3_tx):
    """C7：同一交易日简报里第一次存下记录后，第二只「钱不够」的候选 → compared_today。"""
    env, ids = _setup(v3_tx, tickers=("BBB", "EEE"))
    _verdict(env, "EEE")
    refs = _reads(env)
    first = call(env, "save_rotation", {"ticker": "BBB", "verdict": "hold", "reasons": "继续拿着", "refs": refs})
    assert "data" in first, first

    second = call(env, "save_rotation", {"ticker": "EEE", "verdict": "hold", "reasons": "继续拿着", "refs": refs})

    assert second["error"] == "compared_today: 仓位已到上限，当天已比较过一次"
    _unchanged(v3_tx, ids, rotations=1)


def test_rerun_brief_does_not_duplicate(v3_tx):
    """C17：同一交易日重跑（再次存同一个结论）：不产生第二行记录、不重复下指令、不新建第二份 B 的计划。"""
    env, ids = _setup(v3_tx)
    refs = _reads(env)
    first = _half(env, ids["a"], refs)
    assert "data" in first

    again = _half(env, ids["a"], refs)

    assert again["error"].startswith("compared_today")
    assert _count(v3_tx, "v3_rotations") == 1 and _count(v3_tx, "v3_plans") == 3
    events = [e for e in plan_repo.events(v3_tx, ids["a"]["id"]) if e["event"] == "rotation_instruction"]
    assert len(events) == 1


def test_chat_preview_then_write(v3_tx):
    """C16：未确认返回复述（含「最近 5 个交易日已换仓 1 次」），确认后写入；冷却期内与当天已比较过也能写。"""
    env, ids = _setup(v3_tx, mode="chat", agent="t-chat")
    two_ago = add_trading_days(SESSION, -2)
    add_rotation(v3_tx, session=two_ago, verdict="half", origin="chat", holding=(ids["a"]["id"], "AAA"))
    add_rotation(v3_tx, verdict="hold", origin="brief", candidate="EEE")  # 简报当天已比较过
    v3_tx.commit()
    refs = _reads(env)

    preview = _half(env, ids["a"], refs, request_id="chat-1", confirmed=False)

    assert "error" not in preview, preview
    text = preview["data"]["preview"]
    assert preview["data"]["write"] is False
    for part in ("半仓换", "AAA", "150 股", "BBB", "217 股", "最近 5 个交易日已换仓 1 次"):
        assert part in text, (part, text)
    assert _count(v3_tx, "v3_rotations") == 2 and plan_repo.active_plan(v3_tx, "BBB") is None
    assert _half(env, ids["a"], refs, confirmed=False)["error"].startswith("invalid_argument")  # 对话要 request_id

    written = _half(env, ids["a"], refs, request_id="chat-1", confirmed=True)["data"]
    assert written["rotation"]["origin"] == "chat" and written["rotation"]["request_id"] == "chat-1"
    assert written["plan"]["shares"] == 217 and written["holding"]["sell_quantity"] == "150.000000"
    instruction = [e for e in plan_repo.events(v3_tx, ids["a"]["id"]) if e["event"] == "rotation_instruction"][0]
    assert instruction["source"] == "user"


def test_chat_replay_returns_same_rotation(v3_tx):
    """Review Focus 4：同一个 request_id 重发 → 返回原来那一行，不产生第二行、不重复下指令、不新建第二份 B。"""
    env, ids = _setup(v3_tx, mode="chat", agent="t-replay")
    refs = _reads(env)
    first = _half(env, ids["a"], refs, request_id="chat-r", confirmed=True)["data"]

    again = _half(env, ids["a"], refs, request_id="chat-r", confirmed=True)

    assert again["data"]["rotation"]["id"] == first["rotation"]["id"] and again["data"]["replay"] is True
    assert again["data"]["plan"]["id"] == first["plan"]["id"] and again["data"]["holding"]["id"] == ids["a"]["id"]
    assert _count(v3_tx, "v3_rotations") == 1 and _count(v3_tx, "v3_plans") == 3
    other = _half(env, ids["a"], refs, request_id="chat-r", confirmed=True, ticker="EEE")
    assert other["error"].startswith("request_id_reused")
    changed = _half(env, ids["a"], refs, request_id="chat-r", confirmed=True, verdict="full")
    assert changed["error"].startswith("request_id_reused")


def test_chat_user_names_the_holding(v3_tx):
    """用户指定 A：传 holding_plan_id；不在可比清单 → not_comparable。"""
    env, ids = _setup(v3_tx, mode="chat", agent="t-name")
    blocked = add_holding(v3_tx, "STP", 10, "40", 40.0, stop_hit_session=SESSION)
    holding_review(v3_tx, blocked["id"])
    v3_tx.commit()
    refs = _reads(env)

    refused = _half(env, blocked, refs, request_id="c-1", confirmed=True)
    assert refused["error"] == f"not_comparable: {INELIGIBLE_TEXT['stop_hit']}"
    read = call(env, "get_price_bars", {"ticker": "CCC"})["ref"]
    chosen = _half(env, ids["c"], [refs[0], read], request_id="c-2", confirmed=True)["data"]
    assert chosen["rotation"]["holding_ticker"] == "CCC" and chosen["holding"]["sell_quantity"] == "500.000000"
    assert chosen["plan"]["shares"] == 250
    not_open = _half(env, {"id": chosen["plan"]["id"]}, refs, request_id="c-3", confirmed=True)
    assert not_open["error"].startswith("not_open")


def test_research_ready_candidate_goes_through_rotation(v3_tx):
    """研究结论 ready 且钱不够：save_plan → needs_rotation；save_rotation 半仓后 B 的计划带 research_id 与 funded_by_rotation_id。"""
    ids = seed_c4(v3_tx)
    data_repo.store_bars(v3_tx, "RRR", seed_bars(close=50.0), now=NOW)
    v3_tx.commit()
    env = _env("chat", "t-research")
    started = call(
        env,
        "start_research",
        {"ticker": "RRR", "thesis": "跌很多，可能是入场时机", "request_id": "rs-1", "confirmed": True},
    )["data"]["research"]
    ref = call(env, "get_research", {})["ref"]
    concluded = call(
        env, "conclude_research", {"research_id": started["id"], "verdict": "ready", "reasons": "可以出计划", "refs": [ref]}
    )  # fmt: skip
    assert "data" in concluded, concluded
    _verdict(env, "RRR")
    needs = call(env, "save_plan", {"ticker": "RRR", **TEXTS})
    assert needs["error"].startswith("needs_rotation")

    refs = _reads(env, candidate="RRR")
    saved = _half(env, ids["a"], refs, ticker="RRR", request_id="rr-1", confirmed=True)

    assert "data" in saved, saved
    plan_b = saved["data"]["plan"]
    assert plan_b["research_id"] == started["id"] and plan_b["funded_by_rotation_id"] == saved["data"]["rotation"]["id"]
    assert plan_b["ticker"] == "RRR" and plan_b["shares"] > 0


def test_withdraw_rotation(v3_tx):
    """C8：复述 → 确认 → 三列清空、事件原因逐字；重发幂等；没有换仓指令 → no_rotation_instruction；简报模式不可用。"""
    env, ids = _setup(v3_tx, mode="chat", agent="t-withdraw")
    saved = _half(env, ids["a"], _reads(env), request_id="w-0", confirmed=True)["data"]
    plan_a, rotation_id = ids["a"]["id"], saved["rotation"]["id"]

    assert call(_env("brief", "t-brief-w"), "withdraw_rotation", {"plan_id": plan_a, "request_id": "w-1", "confirmed": True})[
        "error"
    ].startswith("not_available_in_mode")  # fmt: skip
    preview = call(env, "withdraw_rotation", {"plan_id": plan_a, "request_id": "w-1", "confirmed": False})["data"]
    assert preview == {"write": False, "preview": "将撤回 AAA 的换仓指令（卖出 150 股，换至 BBB）。确认后写入。"}
    assert plan_repo.plan(v3_tx, plan_a)["sell_instruction"] == "rotation"

    done = call(env, "withdraw_rotation", {"plan_id": plan_a, "request_id": "w-1", "confirmed": True})["data"]

    plan = done["plan"]
    assert (plan["sell_instruction"], plan["sell_quantity"], plan["rotation_id"]) == (None, None, None)
    assert done["event"]["event"] == "rotation_withdrawn" and done["event"]["source"] == "user"
    assert done["event"]["reason"] == "换仓指令撤回：用户撤回"
    assert done["event"]["before"] == {"rotation_id": rotation_id, "sell_quantity": "150"}
    assert done["event"]["refs"] == ["w-1"]
    replay = call(env, "withdraw_rotation", {"plan_id": plan_a, "request_id": "w-1", "confirmed": True})["data"]
    assert replay["replay"] is True and replay["event"]["id"] == done["event"]["id"]
    again = call(env, "withdraw_rotation", {"plan_id": plan_a, "request_id": "w-2", "confirmed": True})
    assert again["error"].startswith("no_rotation_instruction")
    assert call(env, "withdraw_rotation", {"plan_id": 99999, "request_id": "w-3", "confirmed": True})[
        "error"
    ].startswith("unknown_plan")
    # B 的计划不动；撤回之后换仓记录仍在，冷却照算（spec §6.1 条件 5）
    assert plan_repo.plan_funded_by(v3_tx, rotation_id)["status"] == "pending"
    assert rotation_repo.rotation(v3_tx, rotation_id) is not None


def test_withdraw_rotation_with_reason(v3_tx):
    env, ids = _setup(v3_tx, mode="chat", agent="t-withdraw-2")
    _half(env, ids["a"], _reads(env), request_id="w-0", confirmed=True)
    preview = call(
        env, "withdraw_rotation", {"plan_id": ids["a"]["id"], "request_id": "w-1", "confirmed": False, "reason": "不换了"}
    )["data"]
    assert "不换了" in preview["preview"]
    done = call(
        env, "withdraw_rotation", {"plan_id": ids["a"]["id"], "request_id": "w-1", "confirmed": True, "reason": "不换了"}
    )["data"]
    assert done["event"]["reason"] == "换仓指令撤回：用户撤回（不换了）"


@pytest.mark.parametrize("mode", ["brief", "chat"])
def test_tool_availability(v3_tx, mode):
    from investment_assistant.toolsurface.dispatch import tool_defs

    names = {tool.name for tool in tool_defs(mode)}
    assert {"get_rotation_table", "save_rotation"} <= names
    assert ("withdraw_rotation" in names) is (mode == "chat")
