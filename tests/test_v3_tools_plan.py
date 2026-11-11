"""Plan tools: host-validated verdicts and plans, plan-line writes (continue / revise /
holding review / summary) and the slot & replacement rules of spec §6.4."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.facts import plan_repo, research_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts.agenda import agenda
from investment_assistant.facts.view import today_actions
from investment_assistant.toolsurface.dispatch import call
from investment_assistant.toolsurface.env import make_env

SESSION = date(2026, 11, 4)  # 周三；T+1..T+3 = 11/5、11/6、11/9
DAY2 = date(2026, 11, 5)  # 周四：A1 的第二天
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)
NOW2 = datetime(2026, 11, 6, 3, 30, tzinfo=UTC)
TOP5 = ["AAPL", "MSFT", "NVDA", "MU", "AVGO"]
TEXTS = {"thesis": "回调到 99 附近买入", "invalidation": "跌破 96 止损"}


def _bars(n=20, *, end=SESSION, close=100.0):
    days = [end - timedelta(days=i) for i in range(n)][::-1]
    return [
        {
            "price_date": d,
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "adj_close": close,
            "volume": 1000,
            "split_factor": 1.0,
        }
        for d in days
    ]


def _run_row(run_id="run-1", *, session=SESSION):
    return {
        "run_id": run_id,
        "session": session,
        "trigger": "scheduled",
        "status": "ok",
        "error": None,
        "market": {"label": "neutral"},
        "factors": {},
        "coverage": {},
        "holdings": [],
        "draft_md": "# 草稿",
        "started_at": NOW - timedelta(minutes=5),
        "finished_at": NOW,
        "dropped": [],
    }


def _candidate(ticker, rank):
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


def _seed(v3_tx):
    facts_repo.insert_run(v3_tx, _run_row())
    facts_repo.insert_candidates(v3_tx, "run-1", [_candidate(t, i + 1) for i, t in enumerate(TOP5)])
    for ticker in TOP5:
        data_repo.store_bars(v3_tx, ticker, _bars(), now=NOW)
    v3_tx.commit()  # 事实已入库，保存被拒的回滚不应带走它


def _env(v3_tx, mode="brief", agent="t-plan"):
    return make_env(session=SESSION, mode=mode, agent_session=agent, cfg=AssistantConfig(), now=NOW)


def confirm(env, ticker, verdict="confirm"):
    """保存一条已确认结论；先做一次真实读调用，refs 指向它的 ref。"""
    ref = call(env, "get_market", {})["ref"]
    r = call(
        env,
        "save_verdict",
        {"ticker": ticker, "verdict": verdict, "reasons": "理由充分，形态与行业共振", "refs": [ref]},
    )
    assert "data" in r, r
    return r


def _plan(v3_tx, ticker, **over):
    """直接插一份 034 形状的计划（不走工具），用于钉住名额与状态校验。"""
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
        "valid_from": date(2026, 11, 5),
        "valid_until": date(2026, 11, 11),
        "shares": None,
        "sizing": {},
        "warnings": [],
        "thesis": "论点",
        "invalidation": "作废条件",
        "counter": "",
        "refs": [],
        "agent_session": "seed",
        "status": "pending",
        "kind": "planned",
    }
    row.update(over)
    return plan_repo.insert_plan(v3_tx, row)


def _events(v3_tx, plan_id):
    return [row["event"] for row in plan_repo.events(v3_tx, plan_id)]


def test_compute_plan_levels_uses_host_arithmetic(v3_tx):
    _seed(v3_tx)
    env = _env(v3_tx)
    d = call(env, "compute_plan_levels", {"ticker": TOP5[0]})["data"]
    assert d["ticker"] == "AAPL" and d["session"] == SESSION.isoformat()
    assert d["levels"] == {"entry_low": "99.00", "entry_high": "101.00", "stop": "96.00", "target1": "108.00"}
    assert d["sizing"]["status"] == "no_equity" and d["sizing"]["shares"] is None
    assert d["entry_types"] == ["zone_limit", "breakout_stop", "next_open", "pullback"] and d["max_hold_range"] == [
        3,
        30,
    ]


def test_save_plan_check_order_scope_and_verdict_first(v3_tx):
    """钉住检查顺序：范围 → 确认结论 → 入场类型 → 最长持有（spec §6）。"""
    _seed(v3_tx)
    env = _env(v3_tx, agent="t-order")
    # 名单外标的带非法入场类型 -> 先报范围，不报 invalid_entry_type
    assert call(env, "save_plan", {"ticker": "ZZZZ", **TEXTS, "entry_type": "market"})["error"].startswith(
        "out_of_scope"
    )
    # 名单内但未确认的标的带越界最长持有 -> 先报没有确认结论，不报 invalid_max_hold
    assert call(env, "save_plan", {"ticker": TOP5[0], **TEXTS, "max_hold_sessions": 40})["error"].startswith(
        "no_confirm_verdict"
    )


def test_save_plan_rejections(v3_tx):
    _seed(v3_tx)
    env = _env(v3_tx)
    t = TOP5[0]
    assert call(env, "save_plan", {"ticker": "ZZZZ", **TEXTS})["error"].startswith("out_of_scope")
    assert call(env, "save_plan", {"ticker": t, **TEXTS})["error"].startswith("no_confirm_verdict")
    confirm(env, t)  # 这次保存本身记为 call:3
    assert call(env, "save_plan", {"ticker": t, **TEXTS, "shares": 100})["error"].startswith("shares_not_allowed")
    assert call(env, "save_plan", {"ticker": t, **TEXTS, "quantity": 100})["error"].startswith("shares_not_allowed")
    assert call(env, "save_plan", {"ticker": t, **TEXTS, "entry_type": "market"})["error"].startswith(
        "invalid_entry_type"
    )
    assert call(env, "save_plan", {"ticker": t, **TEXTS, "max_hold_sessions": 40})["error"].startswith(
        "invalid_max_hold"
    )
    bad = {"entry_low": 99, "entry_high": 101, "stop": 99.5, "target1": 108}  # 止损不在入场下方
    assert call(env, "save_plan", {"ticker": t, **TEXTS, "levels": bad})["error"].startswith("levels_invalid")
    assert v3_tx.execute("SELECT count(*) FROM v3_plans").fetchone()[0] == 0  # 每次之后 v3_plans 仍为空


def test_save_plan_host_and_agent_levels(v3_tx):
    _seed(v3_tx)
    env = _env(v3_tx)
    t = TOP5[0]
    confirm(env, t)
    host = call(env, "compute_plan_levels", {"ticker": t})["data"]
    saved = call(env, "save_plan", {"ticker": t, **TEXTS})["data"]
    assert saved["levels_source"] == "host"
    assert {k: D(str(saved[k])) for k in ("entry_low", "entry_high", "stop", "target1")} == {
        k: D(v) for k, v in host["levels"].items()
    }  # 价位等于 compute_plan_levels 的结果
    agent_levels = {"entry_low": 99.5, "entry_high": 100.5, "stop": 97, "target1": 106}
    revised = call(
        env, "revise_plan", {"plan_id": saved["id"], "reason": "回踩更浅", "levels": agent_levels, "refs": ["call:3"]}
    )["data"]
    assert D(str(revised["stop"])) == D(97) and D(str(revised["entry_low"])) == D("99.5")
    assert v3_tx.execute("SELECT count(*) FROM v3_plans").fetchone()[0] == 1  # 同一股票仍只有一份
    # 财报日落在有效期内 -> 'earnings_in_validity'
    data_repo.store_earnings(v3_tx, TOP5[1], {"status": "known", "earnings_date": date(2026, 11, 6)}, now=NOW)
    confirm(env, TOP5[1])
    assert call(env, "save_plan", {"ticker": TOP5[1], **TEXTS})["error"].startswith("earnings_in_validity")


def test_plan_without_equity_and_without_room(v3_tx):
    """股数由宿主按仓位本金算：没资金记录不算股数；有对账记录按风险与上限取小；超上限 0 股。"""
    _seed(v3_tx)
    env = _env(v3_tx, agent="t-room")
    t = TOP5[0]
    confirm(env, t)
    saved = call(env, "save_plan", {"ticker": t, **TEXTS})["data"]
    assert saved["shares"] is None and "未提供账户资金，未算股数" in saved["warnings"]
    # 下面用 continue_plan 重算股数：把它当作前一个交易日保存的计划（延续只在之后的交易日发生）
    v3_tx.execute("UPDATE v3_plans SET session = %s WHERE id = %s", (date(2026, 11, 3), saved["id"]))
    facts_repo.insert_equity(v3_tx, {"request_id": "eq-1", "as_of": SESSION, "equity": D(100000), "note": ""})
    v3_tx.commit()
    again = call(env, "continue_plan", {"plan_id": saved["id"], "refs": ["call:3"]})["data"]
    # 按风险 1% = 1000、每股风险 4 -> 250 股；单票上限 25%：25000 / 101 取整 247 -> 取较小者
    assert again["shares"] == 247 and again["sizing"]["status"] == "capped"
    assert again["sizing"]["applied_cap"] == "single"
    # 另一只股票已有 1000 股、最新收盘 100（市值等于本金，占满总仓位上限）
    facts_repo.insert_trade(
        v3_tx,
        {
            "request_id": "tr-1",
            "ticker": TOP5[1],
            "side": "buy",
            "quantity": 1000,
            "price": D(100),
            "executed_at": datetime(2026, 11, 3, 15, tzinfo=UTC),
        },
    )
    v3_tx.commit()
    third = call(env, "continue_plan", {"plan_id": saved["id"], "refs": ["call:3"]})["data"]
    assert third["shares"] == 0 and "no_room" in third["warnings"]
    # 持仓再涨到超过本金（110000 > 100000）：仍是 0 股，不是负数
    facts_repo.insert_trade(
        v3_tx,
        {
            "request_id": "tr-2",
            "ticker": TOP5[1],
            "side": "buy",
            "quantity": 100,
            "price": D(100),
            "executed_at": datetime(2026, 11, 3, 16, tzinfo=UTC),
        },
    )
    v3_tx.commit()
    over = call(env, "continue_plan", {"plan_id": saved["id"], "refs": ["call:3"]})["data"]
    assert over["shares"] == 0 and over["sizing"]["status"] == "no_room" and "no_room" in over["warnings"]
    levels = call(env, "compute_plan_levels", {"ticker": t})["data"]
    assert levels["sizing"]["shares"] == 0 and levels["sizing"]["status"] == "no_room"


def test_verdict_rules(v3_tx):
    _seed(v3_tx)
    env = _env(v3_tx, agent="t-verdict")
    t = TOP5[0]
    assert call(env, "save_verdict", {"ticker": t, "verdict": "maybe", "reasons": "r", "refs": []})["error"].startswith(
        "invalid_verdict"
    )
    assert call(env, "save_verdict", {"ticker": t, "verdict": "unknown", "reasons": "", "refs": []})[
        "error"
    ].startswith("invalid_argument")
    assert call(env, "save_verdict", {"ticker": t, "verdict": "unknown", "reasons": "x" * 2001, "refs": []})[
        "error"
    ].startswith("invalid_argument")
    call(env, "get_market", {})  # 记为 call:4，给下面的 refs 用
    assert call(env, "save_verdict", {"ticker": t, "verdict": "confirm", "reasons": "理由", "refs": ["call:99"]})[
        "error"
    ].startswith("invalid_refs")
    assert call(env, "save_verdict", {"ticker": t, "verdict": "confirm", "reasons": "理由", "refs": []})[
        "error"
    ].startswith("invalid_refs")
    assert "data" in call(
        env, "save_verdict", {"ticker": t, "verdict": "unknown", "reasons": "理由", "refs": []}
    )  # unknown 可以没有 ref
    second = call(env, "save_verdict", {"ticker": t, "verdict": "veto", "reasons": "第二次的理由", "refs": ["call:4"]})[
        "data"
    ]
    assert v3_tx.execute("SELECT count(*) FROM v3_verdicts").fetchone()[0] == 1  # 同一标的一行
    assert second["verdict"] == "veto" and second["reasons"] == "第二次的理由"  # 内容是第二次的


def test_brief_scope_empty_top_n(v3_tx):
    facts_repo.insert_run(v3_tx, _run_row())  # 运行里没有候选
    env = _env(v3_tx)
    assert call(env, "save_verdict", {"ticker": "AAPL", "verdict": "unknown", "reasons": "r"})["error"].startswith(
        "out_of_scope"
    )
    assert call(env, "save_plan", {"ticker": "AAPL", **TEXTS})["error"].startswith("out_of_scope")


def test_chat_mode_allows_named_ticker(v3_tx):
    facts_repo.insert_run(v3_tx, _run_row())
    facts_repo.insert_candidates(v3_tx, "run-1", [_candidate("AAPL", 1)])
    data_repo.store_bars(v3_tx, "MSFT", _bars(), now=NOW)  # 名单外但有日线
    v3_tx.commit()
    env = _env(v3_tx, mode="chat", agent="t-chat")
    assert (
        call(env, "save_verdict", {"ticker": "MSFT", "verdict": "unknown", "reasons": "r"})["data"]["origin"] == "chat"
    )
    assert "data" in call(
        env, "save_verdict", {"ticker": "MSFT", "verdict": "confirm", "reasons": "r", "refs": ["call:1"]}
    )
    plan = call(env, "save_plan", {"ticker": "MSFT", **TEXTS})["data"]
    assert plan["origin"] == "chat" and float(plan["entry_low"]) == 99.0


# ---------------------------------------------------------------- v3.1 计划线写入工具


def test_second_day_save_plan_hits_active_plan_exists_and_continue(v3_tx):
    """A1：第二天同名股票保存被拒并返回原计划；continue_plan 留 continued 事件。"""
    _seed(v3_tx)
    env = _env(v3_tx, agent="t-a1")
    t = TOP5[0]
    confirm(env, t)
    first = call(env, "save_plan", {"ticker": t, **TEXTS, "refs": ["call:2"]})["data"]
    assert first["status"] == "pending" and D(str(first["initial_stop"])) == D(96)
    assert D(str(first["atr"])) == D(2)
    assert _events(v3_tx, first["id"]) == ["created"]
    # 第二天：新运行记录、新日线、新结论
    facts_repo.insert_run(v3_tx, _run_row("run-2", session=DAY2))
    facts_repo.insert_candidates(v3_tx, "run-2", [_candidate(t, i + 1) for i, t in enumerate(TOP5)])
    data_repo.store_bars(v3_tx, t, _bars(end=DAY2), now=NOW2)
    v3_tx.commit()
    day2 = make_env(session=DAY2, mode="brief", agent_session="t-a1b", cfg=AssistantConfig(), now=NOW2)
    ref = call(day2, "get_market", {})["ref"]
    call(day2, "save_verdict", {"ticker": t, "verdict": "confirm", "reasons": "仍成立", "refs": [ref]})
    rejected = call(day2, "save_plan", {"ticker": t, **TEXTS, "refs": [ref]})
    assert rejected["error"].startswith("active_plan_exists")
    assert rejected["detail"]["id"] == first["id"] and rejected["detail"]["ticker"] == t
    assert v3_tx.execute("SELECT count(*) FROM v3_plans").fetchone()[0] == 1  # 不产生第二份
    continued = call(day2, "continue_plan", {"plan_id": first["id"], "refs": [ref]})["data"]
    assert continued["status"] == "pending" and continued["valid_from"] == "2026-11-06"
    assert continued["valid_until"] == "2026-11-12"
    assert _events(v3_tx, first["id"]) == ["created", "continued"]


def test_save_plan_rejected_when_slots_full(v3_tx):
    """A2：名额满且不是替换候选 → no_free_slot。"""
    _seed(v3_tx)
    for ticker in ("MSFT", "NVDA", "MU"):  # 名次 2/3/4；替换候选是 AAPL(1)
        _plan(v3_tx, ticker)
    v3_tx.commit()
    env = _env(v3_tx, agent="t-slot")
    confirm(env, "AVGO")
    rejected = call(env, "save_plan", {"ticker": "AVGO", **TEXTS})
    assert rejected["error"].startswith("no_free_slot")
    assert v3_tx.execute("SELECT count(*) FROM v3_plans WHERE ticker = 'AVGO'").fetchone()[0] == 0


def test_save_plan_rejected_when_replacement_rank_not_better(v3_tx):
    """A2：候选名次不高于名次最差的待入场计划 → replacement_rank_not_better。"""
    _seed(v3_tx)
    for ticker in ("AAPL", "MSFT", "NVDA"):  # 名次 1–3；候选 MU(4) 不优于 NVDA(3)
        _plan(v3_tx, ticker)
    v3_tx.commit()
    env = _env(v3_tx, agent="t-rank")
    confirm(env, "MU")
    assert call(env, "save_plan", {"ticker": "MU", **TEXTS})["error"].startswith("replacement_rank_not_better")


def test_save_plan_replacement_marks_victim_then_rejects_second(v3_tx):
    """A2：替换候选保存成功并把受害者记 replaced；同日第二次替换被拒。"""
    _seed(v3_tx)
    victim = _plan(v3_tx, "MU")  # 名次 4：当天最差
    for ticker in ("MSFT", "NVDA"):
        _plan(v3_tx, ticker)
    v3_tx.commit()
    env = _env(v3_tx, agent="t-replace")
    confirm(env, "AAPL")
    saved = call(env, "save_plan", {"ticker": "AAPL", **TEXTS})["data"]
    assert saved["ticker"] == "AAPL" and saved["status"] == "pending"
    replaced = plan_repo.plan(v3_tx, victim["id"])
    assert replaced is not None and replaced["status"] == "replaced"
    assert replaced["ended_session"] == SESSION and "AAPL" in replaced["end_reason"]
    assert _events(v3_tx, victim["id"]) == ["replaced"]
    confirm(env, "AVGO")
    assert call(env, "save_plan", {"ticker": "AVGO", **TEXTS})["error"].startswith("replaced_today")


def test_revise_plan_only_pending_and_records_changed_keys(v3_tx):
    """A4：open 计划拒绝修订；正常修订只记改了的键，reason 必填。"""
    _seed(v3_tx)
    env = _env(v3_tx, agent="t-revise")
    t = TOP5[0]
    confirm(env, t)
    saved = call(env, "save_plan", {"ticker": t, **TEXTS})["data"]
    plan_repo.update_plan(v3_tx, saved["id"], status="open", start_session=SESSION)
    v3_tx.commit()
    assert call(env, "revise_plan", {"plan_id": saved["id"], "reason": "理由"})["error"].startswith("not_pending")
    plan_repo.update_plan(v3_tx, saved["id"], status="pending")
    v3_tx.commit()
    ref = call(env, "get_market", {})["ref"]
    levels = {"entry_low": 98.5, "entry_high": 100.5, "stop": 95, "target1": 107}
    revised = call(env, "revise_plan", {"plan_id": saved["id"], "reason": "回踩更深", "levels": levels, "refs": [ref]})[
        "data"
    ]
    assert D(str(revised["stop"])) == D(95) and revised["thesis"] == TEXTS["thesis"]
    event = next(row for row in plan_repo.events(v3_tx, saved["id"]) if row["event"] == "revised")
    assert set(event["after"]) == {
        "entry_low",
        "entry_high",
        "stop",
        "target1",
        "initial_stop",
    }  # R3：待入场改止损同步 initial_stop
    assert event["reason"] == "回踩更深" and event["source"] == "agent"
    assert call(env, "revise_plan", {"plan_id": saved["id"], "reason": ""})["error"].startswith("invalid_argument")
    assert call(env, "revise_plan", {"plan_id": saved["id"], "reason": "没有改动", "refs": [ref]})["error"].startswith(
        "nothing_to_revise"
    )


def test_continue_plan_requires_pending_and_today_confirm(v3_tx):
    _seed(v3_tx)
    env = _env(v3_tx, agent="t-cont")
    plan = _plan(v3_tx, TOP5[0], session=date(2026, 11, 3))
    v3_tx.commit()
    ref = call(env, "get_market", {})["ref"]
    assert call(env, "continue_plan", {"plan_id": plan["id"], "refs": [ref]})["error"].startswith("no_confirm_verdict")
    confirm(env, TOP5[0])
    again = call(env, "continue_plan", {"plan_id": plan["id"], "refs": [ref]})["data"]
    assert again["valid_from"] == "2026-11-05" and _events(v3_tx, plan["id"]) == ["continued"]


def test_save_holding_review_broken_sets_sell_instruction(v3_tx):
    """A13：论点失效由宿主转成下次开盘卖出，事件 source=agent。"""
    _seed(v3_tx)
    env = _env(v3_tx, agent="t-hold")
    open_plan = _plan(v3_tx, "AAPL", status="open", start_session=SESSION)
    pending = _plan(v3_tx, "MSFT")
    facts_repo.insert_trade(
        v3_tx,
        {
            "request_id": "h-buy",
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 10,
            "price": D(100),
            "executed_at": datetime(2026, 11, 4, 15, tzinfo=UTC),
        },
    )
    v3_tx.commit()
    assert call(env, "save_holding_review", {"plan_id": pending["id"], "verdict": "holds", "reasons": "成立"})[
        "error"
    ].startswith("not_open")
    assert call(env, "save_holding_review", {"plan_id": open_plan["id"], "verdict": "maybe", "reasons": "x"})[
        "error"
    ].startswith("invalid_verdict")
    ref = call(env, "get_market", {})["ref"]
    result = call(
        env,
        "save_holding_review",
        {"plan_id": open_plan["id"], "verdict": "broken", "reasons": "放量跌穿均线，论点失效", "refs": [ref]},
    )["data"]
    saved = plan_repo.plan(v3_tx, open_plan["id"])
    assert saved is not None and saved["sell_instruction"] == "thesis_broken"
    events = plan_repo.events(v3_tx, open_plan["id"])
    assert [row["event"] for row in events][-1] == "sell_instruction"
    assert events[-1]["source"] == "agent" and events[-1]["reason"] == "论点失效"
    assert result["review"]["verdict"] == "broken"
    actions = today_actions(v3_tx, session=SESSION, cutoff_at=NOW, cfg=AssistantConfig())
    aapl = [row for row in actions if row["ticker"] == "AAPL"]
    assert aapl and aapl[0]["text"] == "下次开盘卖出" and aapl[0]["reason"] == "论点失效"


def test_save_verdict_veto_invalidates_pending_plan(v3_tx):
    """再次上榜被否决：宿主把待入场计划作废，原因取结论理由（spec §6.4）。"""
    _seed(v3_tx)
    env = _env(v3_tx, agent="t-veto")
    t = TOP5[0]
    plan = _plan(v3_tx, t)
    v3_tx.commit()
    ref = call(env, "get_market", {})["ref"]
    call(env, "save_verdict", {"ticker": t, "verdict": "veto", "reasons": "行业转弱", "refs": [ref]})
    saved = plan_repo.plan(v3_tx, plan["id"])
    assert saved is not None and saved["status"] == "invalidated"
    assert saved["end_reason"] == "行业转弱" and saved["ended_session"] == SESSION
    events = plan_repo.events(v3_tx, plan["id"])
    assert [row["event"] for row in events] == ["invalidated"]
    assert events[0]["source"] == "agent" and events[0]["reason"] == "行业转弱"


def test_save_summary_writes_the_days_brief_row(v3_tx):
    _seed(v3_tx)
    env = _env(v3_tx)
    assert call(env, "save_summary", {"text": ""})["error"].startswith("invalid_argument")
    assert call(env, "save_summary", {"text": "长" * 301})["error"].startswith("invalid_argument")
    row = call(env, "save_summary", {"text": "名单无变化，持仓照旧。"})["data"]
    assert row["summary"] == "名单无变化，持仓照旧。" and row["status"] == "draft"
    assert call(env, "save_summary", {"text": "第二次的一句话。"})["data"]["summary"] == "第二次的一句话。"


def test_continue_plan_rejects_a_plan_saved_the_same_session(v3_tx):
    """延续只发生在之后的交易日（spec §6.4 再次上榜）；同一交易日刚保存的计划拒绝，不写事件。"""
    _seed(v3_tx)
    env = _env(v3_tx, agent="t-same")
    t = TOP5[0]
    confirm(env, t)
    saved = call(env, "save_plan", {"ticker": t, **TEXTS, "refs": ["call:2"]})["data"]
    assert call(env, "continue_plan", {"plan_id": saved["id"], "refs": ["call:2"]})["error"].startswith(
        "saved_this_session"
    )
    assert _events(v3_tx, saved["id"]) == ["created"]


# ---------------------------------------------------------------- v3.2 研究计划


def _research(ticker, **over):
    """直接插一份研究（不走工具），缺省观察中且结论 ready。"""
    row = {
        "ticker": ticker,
        "thesis": "论点",
        "origin": "chat",
        "created_session": date(2026, 11, 3),
        "valid_until": date(2026, 11, 20),
        "verdict": "ready",
        "reasons": "可以出计划",
        "agent_session": "seed",
    }
    row.update(over)
    return row


def test_research_plan_ignores_slots_and_replacement(v3_tx):
    """Review Focus 4 / B10：3 份规则计划满员且当天已替换过一次时，研究计划照常保存、不记 replaced。"""
    _seed(v3_tx)
    research = research_repo.insert_research(v3_tx, _research("AAPL"))
    for ticker in ("MSFT", "NVDA", "MU"):  # 名次 2/3/4：规则计划占满 3 个名额
        _plan(v3_tx, ticker)
    replaced = _plan(v3_tx, "AVGO")
    plan_repo.update_plan(
        v3_tx, replaced["id"], status="replaced", ended_session=SESSION, end_reason="名额替换：换成别的"
    )
    plan_repo.insert_event(
        v3_tx,
        {
            "plan_id": replaced["id"],
            "session": SESSION,
            "event": "replaced",
            "reason": "名额替换",
            "before": {"status": "pending"},
            "after": {"status": "replaced"},
            "source": "host",
            "refs": [],
        },
    )
    v3_tx.commit()
    env = _env(v3_tx, agent="t-rslot")
    confirm(env, "AAPL")
    saved = call(env, "save_plan", {"ticker": "AAPL", **TEXTS})["data"]  # 换规则计划这里会 replaced_today
    assert saved["research_id"] == research["id"] and saved["status"] == "pending"
    for ticker in ("MSFT", "NVDA", "MU"):  # 没有规则计划被替换
        victim = plan_repo.active_plan(v3_tx, ticker)
        assert victim is not None and victim["status"] == "pending"
    row = research_repo.research(v3_tx, research["id"])
    assert row is not None
    assert row["status"] == "planned" and row["plan_id"] == saved["id"] and row["end_reason"] == "已转计划"
    events = [event["event"] for event in research_repo.research_events(v3_tx, research["id"])]
    assert events == ["planned"]


def test_research_plan_entry_floor_relaxed(v3_tx):
    """B11 三例：研究计划入场下沿收盘 −3.5 ATR 通过、−4.5 ATR 被拒；规则名单计划 −2.5 ATR 仍被拒。"""
    _seed(v3_tx)
    research_repo.insert_research(v3_tx, _research("AAPL"))
    research_repo.insert_research(v3_tx, _research("MSFT"))
    v3_tx.commit()
    env = _env(v3_tx, agent="t-floor")
    confirm(env, "AAPL")
    confirm(env, "MSFT")
    confirm(env, "NVDA")
    # 研究计划（research_entry_atr=4，ATR=2，收盘 100）：下沿 93 = 100 − 3.5×2，通过
    relaxed = call(
        env,
        "save_plan",
        {"ticker": "AAPL", **TEXTS, "levels": {"entry_low": 93, "entry_high": 94, "stop": 90, "target1": 99}},
    )["data"]
    assert relaxed["research_id"] is not None
    # 研究计划：下沿 91 = 100 − 4.5×2，低于放宽后的下限 92，拒绝
    too_low = call(
        env,
        "save_plan",
        {"ticker": "MSFT", **TEXTS, "levels": {"entry_low": 91, "entry_high": 92, "stop": 88, "target1": 97}},
    )
    assert too_low["error"].startswith("levels_invalid") and "entry_too_low" in too_low["error"]
    # 规则名单计划（下限收盘 − 2 ATR = 96）：下沿 95 = 100 − 2.5×2，仍拒绝
    rule = call(
        env,
        "save_plan",
        {"ticker": "NVDA", **TEXTS, "levels": {"entry_low": 95, "entry_high": 96, "stop": 92, "target1": 101}},
    )
    assert rule["error"].startswith("levels_invalid") and "entry_too_low" in rule["error"]


def test_revise_pending_stop_updates_initial_stop(v3_tx):
    """B18/R3：待入场计划修订止损时 initial_stop 同步，事件 before/after 都带它。"""
    _seed(v3_tx)
    env = _env(v3_tx, agent="t-istop")
    t = TOP5[0]
    confirm(env, t)
    saved = call(env, "save_plan", {"ticker": t, **TEXTS})["data"]
    ref = call(env, "get_market", {})["ref"]
    revised = call(
        env,
        "revise_plan",
        {
            "plan_id": saved["id"],
            "reason": "止损上调",
            "levels": {"entry_low": 99, "entry_high": 101, "stop": 97, "target1": 108},
            "refs": [ref],
        },
    )["data"]
    assert D(str(revised["stop"])) == D(97) and D(str(revised["initial_stop"])) == D(97)
    event = next(row for row in plan_repo.events(v3_tx, saved["id"]) if row["event"] == "revised")
    assert "initial_stop" in event["after"] and "initial_stop" in event["before"]


def test_save_plan_with_watching_research_not_ready_is_rejected(v3_tx):
    """观察中的研究没有 ready 结论时，给它出计划被拒（research_not_ready），按普通计划也不行。"""
    _seed(v3_tx)
    research_repo.insert_research(v3_tx, _research("AAPL", verdict=None, reasons=""))  # 还没给结论
    v3_tx.commit()
    env = _env(v3_tx, agent="t-notready")
    confirm(env, "AAPL")
    rejected = call(env, "save_plan", {"ticker": "AAPL", **TEXTS})
    assert rejected["error"].startswith("research_not_ready")
    assert v3_tx.execute("SELECT count(*) FROM v3_plans WHERE ticker = 'AAPL'").fetchone()[0] == 0


def test_replacement_skips_watching_ticker_like_agenda(v3_tx):
    """P1-3：排名 1 是观察中研究股票时，agenda 与 save_plan 的替换候选同口径——选排名 2，保存能完成替换。"""
    _seed(v3_tx)
    research_repo.insert_research(v3_tx, _research("AAPL"))  # 排名 1：观察中的研究
    victim = _plan(v3_tx, "AVGO")  # 名次 5：当天最差
    for ticker in ("NVDA", "MU"):  # 名次 3/4：3 份规则计划占满名额
        _plan(v3_tx, ticker)
    v3_tx.commit()

    day_agenda = agenda(v3_tx, session=SESSION, cfg=AssistantConfig())
    assert day_agenda["replacement"] == {"candidate": "MSFT", "victim_plan_id": victim["id"], "victim_rank": 5}

    env = _env(v3_tx, agent="t-rskip")
    confirm(env, "MSFT")
    saved = call(env, "save_plan", {"ticker": "MSFT", **TEXTS})["data"]  # 修复前：no_free_slot（候选仍是 AAPL）
    assert saved["status"] == "pending" and saved["research_id"] is None
    replaced = plan_repo.plan(v3_tx, victim["id"])
    assert replaced is not None and replaced["status"] == "replaced"


# ---- v3.3：保存计划的仓位闸门、换仓的 B 与换仓指令的取代 / 撤回（spec §5.2、§6.5、§6.6）----
# 参考场景同 _rotation_seed.seed_c4：账户 100160.50，持仓 55020.00（AAA 301 股、CCC 1000 股），BBB 钱不够。


def _rot_env(mode="brief", agent="t-v33"):
    import _rotation_seed as rot

    return make_env(session=rot.SESSION, mode=mode, agent_session=agent, cfg=rot.CFG, now=rot.NOW)


def _count_plans(conn):
    return conn.execute("SELECT count(*) FROM v3_plans").fetchone()[0]


def test_save_plan_needs_rotation_when_short(v3_tx):
    """钱不够且可比较：needs_rotation，detail 带闸门；不比较的原因（冷却期）写进 no_room。"""
    import _rotation_seed as rot

    from investment_assistant.data.calendar import add_trading_days
    from investment_assistant.execution.rotation import BLOCKED_TEXT

    ids = rot.seed_c4(v3_tx)
    v3_tx.commit()
    env = _rot_env()
    confirm(env, "BBB")

    short = call(env, "save_plan", {"ticker": "BBB", **TEXTS})

    assert short["error"] == "needs_rotation: 仓位已到上限，需先保存换仓结论"
    assert short["detail"]["short"] is True and short["detail"]["compare"] is True
    assert short["detail"]["blocked"] is None and short["detail"]["sizing"]["sizing"]["shares"] == 99
    assert _count_plans(v3_tx) == 2

    two_ago = add_trading_days(rot.SESSION, -2)
    rot.add_rotation(v3_tx, session=two_ago, verdict="half", origin="chat", holding=(ids["a"]["id"], "AAA"))
    v3_tx.commit()
    blocked = call(env, "save_plan", {"ticker": "BBB", **TEXTS})
    assert blocked["error"] == "no_room: " + BLOCKED_TEXT["cooldown"].format(last=two_ago)
    assert _count_plans(v3_tx) == 2


def test_save_plan_rejects_zero_shares(v3_tx):
    """C21：待办判为「钱够」（额度内 1 股 ≥ 不受限 2 股的一半）而自报价位算出 0 股 → no_room「仓位已到上限」，不留 0 股计划。"""
    import _rotation_seed as rot

    rot.seed_run(v3_tx, ("BBB",))
    rot.seed_opening(v3_tx, 1000)
    rot.add_holding(v3_tx, "AAA", 9, "61", 61.0)  # 市值 549，上限 600，额度 51
    v3_tx.commit()
    env = _rot_env()
    confirm(env, "BBB")

    agent_levels = {"entry_low": 50, "entry_high": 52, "stop": 46, "target1": 60}  # 入场上沿 52：额度 51 买不起 1 股
    rejected = call(env, "save_plan", {"ticker": "BBB", **TEXTS, "levels": agent_levels})

    assert rejected["error"] == "no_room: 仓位已到上限"
    assert _count_plans(v3_tx) == 1
    saved = call(env, "save_plan", {"ticker": "BBB", **TEXTS})["data"]  # 规则价位入场上沿 51：恰好 1 股
    assert saved["shares"] == 1


def test_save_plan_without_equity_still_saves(v3_tx):
    """C19：没有任何资金记录：不判钱不够，照旧保存（股数为空，警告写明）。"""
    import _rotation_seed as rot

    from investment_assistant.core.texts import NO_EQUITY_TEXT

    rot.seed_run(v3_tx, ("BBB",))
    rot.add_holding(v3_tx, "AAA", 300, "40", 40.0)
    v3_tx.commit()
    env = _rot_env()
    confirm(env, "BBB")

    saved = call(env, "save_plan", {"ticker": "BBB", **TEXTS})

    assert "data" in saved, saved
    assert saved["data"]["shares"] is None and NO_EQUITY_TEXT in saved["data"]["warnings"]


def test_save_plan_warns_when_exposure_is_incomplete(v3_tx):
    """Review Focus 1：钱够、有持仓缺收盘 → 计划照常保存，warnings 含「有持仓缺日线，仓位占比偏低」。"""
    import _rotation_seed as rot

    rot.seed_run(v3_tx, ("BBB",))
    rot.seed_opening(v3_tx)
    rot.add_holding(v3_tx, "AAA", 300, "40", 40.0)
    rot.add_holding(v3_tx, "DDD", 10, "10", 10.0, with_bars=False)
    v3_tx.commit()
    env = _rot_env()
    confirm(env, "BBB")

    saved = call(env, "save_plan", {"ticker": "BBB", **TEXTS})["data"]

    # 缺日线的 DDD 市值算不进去，总资产也偏低 100（成本已扣、市值为 0）：风险项 999 ÷ 4 = 249 股
    assert saved["shares"] == 249 and "有持仓缺日线，仓位占比偏低" in saved["warnings"]


def test_compute_plan_levels_reports_room(v3_tx):
    """uncapped_shares、short_of_room、exposure：钱不够与钱够各一例。"""
    import _rotation_seed as rot

    rot.seed_c4(v3_tx)
    v3_tx.commit()
    env = _rot_env()

    short = call(env, "compute_plan_levels", {"ticker": "BBB"})["data"]

    assert short["uncapped_shares"] == 250 and short["short_of_room"] is True
    assert short["sizing"]["shares"] == 99 and short["sizing"]["uncapped_shares"] == 250
    assert short["exposure"] == {"pct": "0.5493", "cap": "0.6", "room": "5076.30", "incomplete": False}

    v3_tx.execute("DELETE FROM v3_trades WHERE ticker = 'CCC'")
    v3_tx.commit()
    roomy = call(env, "compute_plan_levels", {"ticker": "BBB"})["data"]
    assert roomy["short_of_room"] is False and roomy["sizing"]["shares"] == 250


def test_rotation_plan_levels_are_fixed(v3_tx):
    """C20：换仓的 B：revise_plan 带 levels → rotation_levels_fixed；只改论点可以。"""
    import _rotation_seed as rot

    ids = rot.seed_c4(v3_tx)
    _, funded = rot.instruct(v3_tx, ids["a"], valid_until=date(2026, 11, 20))
    v3_tx.commit()
    env = _rot_env()
    confirm(env, "BBB")
    ref = call(env, "get_market", {})["ref"]
    new_levels = {"entry_low": 49.5, "entry_high": 50.5, "stop": 47, "target1": 56}

    refused = call(
        env, "revise_plan", {"plan_id": funded["id"], "reason": "想改价位", "levels": new_levels, "refs": [ref]}
    )
    assert refused["error"] == "rotation_levels_fixed: 换仓的计划只用宿主规则价位"
    revised = call(
        env, "revise_plan", {"plan_id": funded["id"], "reason": "补充论点", "thesis": "新论点", "refs": [ref]}
    )["data"]
    assert revised["thesis"] == "新论点" and D(str(revised["stop"])) == D(46)


def test_continue_rotation_plan_keeps_funded_basis(v3_tx):
    """C20：A 的换仓指令还在：延续时股数仍按「待卖 150 股已卖出」算（217）；指令撤回或 A 卖完后按普通口径（99）。"""
    import _rotation_seed as rot

    ids = rot.seed_c4(v3_tx)
    _, funded = rot.instruct(v3_tx, ids["a"], valid_until=date(2026, 11, 20))
    v3_tx.commit()
    env = _rot_env()
    confirm(env, "BBB")
    ref = call(env, "get_market", {})["ref"]

    kept = call(env, "continue_plan", {"plan_id": funded["id"], "refs": [ref]})["data"]
    assert kept["shares"] == 217 and kept["sizing"]["funded"]["holding_ticker"] == "AAA"

    plan_repo.update_plan(v3_tx, ids["a"]["id"], sell_instruction=None, sell_quantity=None, rotation_id=None)
    v3_tx.commit()  # 指令撤回
    withdrawn = call(env, "continue_plan", {"plan_id": funded["id"], "refs": [ref]})["data"]
    assert withdrawn["shares"] == 99 and "funded" not in withdrawn["sizing"]

    plan_repo.update_plan(
        v3_tx, ids["a"]["id"], sell_instruction="rotation", sell_quantity=D(150), rotation_id=funded["funded_by_rotation_id"]
    )
    plan_repo.update_plan(v3_tx, ids["a"]["id"], status="closed", ended_session=rot.SESSION, end_reason="换仓")
    v3_tx.commit()  # A 已平仓（指令随计划留在库里，但已不再待卖）
    closed = call(env, "continue_plan", {"plan_id": funded["id"], "refs": [ref]})["data"]
    assert closed["shares"] == 99


def test_broken_review_supersedes_rotation(v3_tx):
    """C9：论点失效覆盖换仓指令（三列同一条 UPDATE 清空），事件原因逐字；成立的复查不动指令。"""
    import _rotation_seed as rot

    ids = rot.seed_c4(v3_tx)
    rotation, _ = rot.instruct(v3_tx, ids["a"], valid_until=date(2026, 11, 20))
    v3_tx.commit()
    env = _rot_env()
    ref = call(env, "get_market", {})["ref"]

    kept = call(env, "save_holding_review", {"plan_id": ids["a"]["id"], "verdict": "holds", "reasons": "仍成立"})["data"]
    assert kept["plan"]["sell_instruction"] == "rotation" and kept["plan"]["rotation_id"] == rotation["id"]

    broken = call(
        env,
        "save_holding_review",
        {"plan_id": ids["a"]["id"], "verdict": "broken", "reasons": "论点不成立", "refs": [ref]},
    )["data"]

    plan = broken["plan"]
    assert (plan["sell_instruction"], plan["sell_quantity"], plan["rotation_id"]) == ("thesis_broken", None, None)
    events = {e["event"]: e for e in plan_repo.events(v3_tx, ids["a"]["id"])}
    assert events["sell_instruction"]["reason"] == "论点失效"
    assert events["rotation_superseded"]["reason"] == "换仓指令被取代：论点失效"
    assert events["rotation_superseded"]["source"] == "agent"
    assert events["rotation_superseded"]["before"] == {"rotation_id": rotation["id"], "sell_quantity": "150"}


def _small_world(v3_tx):
    """A（AAA，持 100 股、换仓指令 50 股）与资金来自这次换仓的 B（BBB，待入场）；名额被 B、FFF、GGG 占满，
    名次 EEE 1、FFF 2、GGG 3、BBB 4。账户很小的仓位：钱够，不触发换仓闸门。"""
    import _rotation_seed as rot

    rot.seed_run(v3_tx, ("EEE", "FFF", "GGG", "BBB"))
    rot.seed_opening(v3_tx)
    held = rot.add_holding(v3_tx, "AAA", 100, "40", 40.0)
    rot.holding_review(v3_tx, held["id"])
    _, funded = rot.instruct(v3_tx, held, quantity=50, valid_until=date(2026, 11, 20))
    for ticker in ("FFF", "GGG"):
        _plan(v3_tx, ticker, valid_until=date(2026, 11, 20))
    v3_tx.commit()
    return held, funded


def _withdrawn(conn, plan_id):
    plan = plan_repo.plan(conn, plan_id)
    assert (plan["sell_instruction"], plan["sell_quantity"], plan["rotation_id"]) == (None, None, None)
    events = [e for e in plan_repo.events(conn, plan_id) if e["event"] == "rotation_withdrawn"]
    assert len(events) == 1 and events[0]["source"] == "host"
    return events[0]["reason"]


def test_ending_a_candidate_plan_withdraws_on_cancel(v3_tx):
    """C8：cancel_plan 取消 B 的待入场计划 → A 的换仓指令撤回。"""
    held, funded = _small_world(v3_tx)
    env = _rot_env("chat", "t-cancel")

    call(env, "cancel_plan", {"plan_id": funded["id"], "request_id": "c-1", "confirmed": True})

    assert _withdrawn(v3_tx, held["id"]) == "换仓指令撤回：BBB 的计划已取消"


def test_ending_a_candidate_plan_withdraws_on_veto(v3_tx):
    """C8：save_verdict 否决使 B 的待入场计划作废 → A 的换仓指令撤回。"""
    held, funded = _small_world(v3_tx)
    env = _rot_env(agent="t-veto")
    ref = call(env, "get_market", {})["ref"]

    vetoed = call(env, "save_verdict", {"ticker": "BBB", "verdict": "veto", "reasons": "不再看好", "refs": [ref]})

    assert "data" in vetoed, vetoed
    assert plan_repo.plan(v3_tx, funded["id"])["status"] == "invalidated"
    assert _withdrawn(v3_tx, held["id"]) == "换仓指令撤回：BBB 的计划已作废"


def test_ending_a_candidate_plan_withdraws_on_replacement(v3_tx):
    """C8：名额替换使 B 的待入场计划被替换 → A 的换仓指令撤回。"""
    held, funded = _small_world(v3_tx)
    env = _rot_env(agent="t-replace")
    confirm(env, "EEE")

    saved = call(env, "save_plan", {"ticker": "EEE", **TEXTS})

    assert "data" in saved, saved
    assert plan_repo.plan(v3_tx, funded["id"])["status"] == "replaced"
    assert _withdrawn(v3_tx, held["id"]) == "换仓指令撤回：BBB 的计划被替换"
