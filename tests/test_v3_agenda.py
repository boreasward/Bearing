"""The day's agenda over the v3_tx database (spec §9.3, §6.4): free slots, the single
replacement candidate, per-item done flags and the completion test."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

from investment_assistant.config import AssistantConfig
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.facts import agenda as facts_agenda
from investment_assistant.facts import plan_repo, research_repo
from investment_assistant.facts import repo as facts_repo

SESSION = date(2026, 11, 4)  # 周三
PREV = date(2026, 11, 3)  # 周二：再次上榜的计划是之前的交易日保存的
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)
CFG = AssistantConfig()
TOP5 = ["AAPL", "MSFT", "NVDA", "MU", "AVGO"]


def _run_row(run_id, session=SESSION):
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
        "started_at": NOW - timedelta(minutes=5),
        "finished_at": NOW,
    }


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


def _research(ticker, **over):
    row = {
        "ticker": ticker,
        "thesis": "论点",
        "origin": "chat",
        "created_session": PREV,
        "valid_until": date(2026, 11, 20),
        "condition": {"kind": "close_above", "level": 105},
        "agent_session": "agenda-test",
    }
    row.update(over)
    return row


def _plan(ticker, *, status="pending", session=PREV, **over):
    row = {
        "session": session,
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
        "valid_from": session,
        "valid_until": date(2026, 11, 10),
        "shares": None,
        "sizing": {},
        "warnings": [],
        "thesis": "论点",
        "invalidation": "作废条件",
        "counter": "",
        "refs": [],
        "agent_session": "agenda-test",
        "status": status,
        "kind": "planned",
    }
    row.update(over)
    return row


def seed(conn, *, session=SESSION, run_id="run-1"):
    facts_repo.insert_run(conn, _run_row(run_id, session))
    facts_repo.insert_candidates(conn, run_id, [_candidate(t, i + 1) for i, t in enumerate(TOP5)])


def agenda(conn, session=SESSION):
    return facts_agenda.agenda(conn, session=session, cfg=CFG)


def test_free_slots_and_confirmations_list_all_top_candidates(v3_tx):
    """有空位时：confirmations 按名次列出前 N 只里没有有效计划的股票，数量不限于空位数。"""
    seed(v3_tx)
    pending = plan_repo.insert_plan(v3_tx, _plan("AAPL"))  # 占 1 个名额（共 3）
    v3_tx.commit()

    result = agenda(v3_tx)

    assert result["free_slots"] == 2
    assert result["replacement"] is None
    assert [item["ticker"] for item in result["confirmations"]] == ["MSFT", "NVDA", "MU", "AVGO"]
    assert all(item["mode"] == "slot" and item["victim_plan_id"] is None for item in result["confirmations"])
    assert [item["plan_id"] for item in result["relisted"]] == [pending["id"]]
    assert result["relisted"][0]["rank"] == 1 and result["relisted"][0]["done"] is False
    assert result["summary_done"] is False and result["complete"] is False


def test_full_cap_lists_only_the_replacement_candidate(v3_tx):
    """名额满时：只列替换候选一只；确认它作废的是宿主选定的受害者。"""
    seed(v3_tx)
    victim = plan_repo.insert_plan(v3_tx, _plan("MU"))  # 名次 4：当天最差
    for ticker in ("MSFT", "NVDA"):
        plan_repo.insert_plan(v3_tx, _plan(ticker))
    v3_tx.commit()

    result = agenda(v3_tx)

    assert result["free_slots"] == 0
    assert result["replacement"] == {"candidate": "AAPL", "victim_plan_id": victim["id"], "victim_rank": 4}
    assert [(item["ticker"], item["mode"], item["victim_plan_id"]) for item in result["confirmations"]] == [
        ("AAPL", "replace", victim["id"])
    ]


def test_replacement_rank_not_better_means_none_and_no_confirmations(v3_tx):
    """A2：候选名次不高于被替换者 → replacement 为 None 且 confirmations 为空。"""
    seed(v3_tx)
    for ticker in ("AAPL", "MSFT", "NVDA"):  # 名次 1–3 都有计划
        plan_repo.insert_plan(v3_tx, _plan(ticker))
    v3_tx.commit()

    result = agenda(v3_tx)

    assert result["free_slots"] == 0
    assert result["replacement"] is None and result["confirmations"] == []


def test_done_flags_and_complete(v3_tx):
    """A5：每项 done 各有判定；全部完成（含一句话总结）时 complete 为真。"""
    seed(v3_tx)
    holding = plan_repo.insert_plan(v3_tx, _plan("AAPL", status="open"))
    relisted = plan_repo.insert_plan(v3_tx, _plan("MSFT"))
    plan_repo.insert_plan(v3_tx, _plan("NVDA"))
    plan_repo.upsert_holding_review(
        v3_tx,
        {
            "plan_id": holding["id"],
            "session": SESSION,
            "verdict": "holds",
            "reasons": "论点仍成立",
            "counter": "",
            "refs": [],
            "agent_session": "agenda-test",
        },
    )
    plan_repo.insert_event(
        v3_tx,
        {
            "plan_id": relisted["id"],
            "session": SESSION,
            "event": "continued",
            "reason": "再次上榜，延续",
            "before": {},
            "after": {},
            "source": "agent",
            "refs": [],
        },
    )
    facts_repo.upsert_verdict(
        v3_tx,
        {
            "session": SESSION,
            "ticker": "MU",
            "verdict": "confirm",
            "reasons": "理由",
            "counter": "",
            "refs": [],
            "origin": "brief",
            "agent_session": "agenda-test",
        },
    )
    v3_tx.commit()

    partial = agenda(v3_tx)
    assert partial["holdings"] == [{"plan_id": holding["id"], "ticker": "AAPL", "done": True}]
    assert {row["ticker"]: row["done"] for row in partial["relisted"]} == {"MSFT": True, "NVDA": False}
    confirm_by_ticker = {item["ticker"]: item for item in partial["confirmations"]}
    assert confirm_by_ticker["MU"]["done"] is True and confirm_by_ticker["AVGO"]["done"] is False
    assert partial["summary_done"] is False and partial["complete"] is False

    # 剩下的项也补齐（NVDA 计划不动 = 结论 unknown；AVGO 有了结论），再加一句话 → complete
    facts_repo.upsert_verdict(
        v3_tx,
        {
            "session": SESSION,
            "ticker": "NVDA",
            "verdict": "unknown",
            "reasons": "数据不足",
            "counter": "",
            "refs": [],
            "origin": "brief",
            "agent_session": "agenda-test",
        },
    )
    facts_repo.upsert_verdict(
        v3_tx,
        {
            "session": SESSION,
            "ticker": "AVGO",
            "verdict": "veto",
            "reasons": "行业走弱",
            "counter": "",
            "refs": [],
            "origin": "brief",
            "agent_session": "agenda-test",
        },
    )
    plan_repo.upsert_brief(v3_tx, {"session": SESSION, "status": "draft", "summary": "今天没有新机会。"})
    v3_tx.commit()
    done = agenda(v3_tx)
    assert done["summary_done"] is True and done["complete"] is True


def test_relisted_done_via_unknown_verdict(v3_tx):
    """再次上榜的第三种完成方式：当天结论是「无法判断」，计划不动。"""
    seed(v3_tx)
    pending = plan_repo.insert_plan(v3_tx, _plan("AAPL"))
    facts_repo.upsert_verdict(
        v3_tx,
        {
            "session": SESSION,
            "ticker": "AAPL",
            "verdict": "unknown",
            "reasons": "数据不足",
            "counter": "",
            "refs": [],
            "origin": "brief",
            "agent_session": "agenda-test",
        },
    )
    v3_tx.commit()

    result = agenda(v3_tx)

    assert result["relisted"] == [{"plan_id": pending["id"], "ticker": "AAPL", "rank": 1, "done": True}]


def test_plan_saved_today_is_not_relisted(v3_tx):
    """当天刚保存的计划不算「再次上榜」：它的待办是当天的确认，已经做完；否则 agent 会被逼着
    在同一交易日对它「延续」一次（2026-11-07 上线冒烟里三份计划都多了一行空的 continued）。"""
    seed(v3_tx)
    today = plan_repo.insert_plan(v3_tx, _plan("AAPL", session=SESSION))
    earlier = plan_repo.insert_plan(v3_tx, _plan("MSFT"))
    for ticker in ("AAPL", "MSFT"):
        facts_repo.upsert_verdict(
            v3_tx,
            {
                "session": SESSION,
                "ticker": ticker,
                "verdict": "confirm",
                "reasons": "理由",
                "counter": "",
                "refs": [],
                "origin": "brief",
                "agent_session": "agenda-test",
            },
        )
    v3_tx.commit()

    result = agenda(v3_tx)

    assert [item["plan_id"] for item in result["relisted"]] == [earlier["id"]]
    assert today["id"] not in [item["plan_id"] for item in result["relisted"]]


def test_research_items_and_slots_ignore_research_plans(v3_tx):
    """研究转成的计划不占名额；有观察中研究的股票不进确认清单；研究复看项参与完成判定。"""
    seed(v3_tx)
    research = research_repo.insert_research(v3_tx, _research("MU", triggered_session=SESSION))
    plan_repo.insert_plan(v3_tx, _plan("AAPL"))
    plan_repo.insert_plan(v3_tx, _plan("MSFT", research_id=research["id"]))
    plan_repo.insert_plan(v3_tx, _plan("NVDA"))
    v3_tx.commit()

    result = agenda(v3_tx)

    assert result["free_slots"] == 1  # 只数规则计划（AAPL、NVDA），研究计划不占名额
    assert [item["ticker"] for item in result["confirmations"]] == ["AVGO"]  # MU 有观察中的研究：跳过
    assert result["research"] == [{"research_id": research["id"], "ticker": "MU", "done": False}]

    # 其余待办全部完成、研究复看没做 → 不算完成；补上当天的 agent 结论事件才算
    for ticker in ("AAPL", "MSFT", "NVDA", "AVGO"):
        facts_repo.upsert_verdict(
            v3_tx,
            {
                "session": SESSION,
                "ticker": ticker,
                "verdict": "unknown",
                "reasons": "数据不足",
                "counter": "",
                "refs": [],
                "origin": "brief",
                "agent_session": "agenda-test",
            },
        )
    plan_repo.upsert_brief(v3_tx, {"session": SESSION, "status": "draft", "summary": "今天没有新机会。"})
    v3_tx.commit()
    undone = agenda(v3_tx)
    assert undone["research"][0]["done"] is False and undone["complete"] is False
    research_repo.insert_research_event(
        v3_tx,
        {
            "research_id": research["id"],
            "session": SESSION,
            "event": "concluded",
            "reason": "结论 watch：继续等",
            "before": {},
            "after": {},
            "source": "agent",
            "refs": [],
        },
    )
    v3_tx.commit()
    done = agenda(v3_tx)
    assert done["research"][0]["done"] is True and done["complete"] is True


# ---- v3.3：换仓比较的待办标记与完成判定（spec §6.1、§7.1；验收 C3、C7）----
# 场景同 _rotation_seed.seed_c4：账户 100160.50，持仓 55020.00，BBB / EEE 都是「钱不够」的候选（额度内 99 股 < 125）。


def _rotation_agenda(conn, **kwargs):
    return facts_agenda.agenda(conn, session=SESSION, cfg=CFG, cutoff_at=NOW, **kwargs)


def _confirm(conn, ticker, verdict="confirm", origin="brief"):
    facts_repo.upsert_verdict(
        conn,
        {
            "session": SESSION,
            "ticker": ticker,
            "verdict": verdict,
            "reasons": "理由",
            "counter": "",
            "refs": [],
            "origin": origin,
            "agent_session": "agenda-test",
        },
    )


def _item(result, ticker):
    return next(item for item in result["confirmations"] if item["ticker"] == ticker)


def test_short_candidate_gets_a_rotation_mark(v3_tx):
    """C3 的前半：confirmations 项带 rotation.short / compare；顶层 rotation 给出当天是否已比较与冷却日期。"""
    from _rotation_seed import seed_c4

    seed_c4(v3_tx, ("BBB", "EEE"))
    v3_tx.commit()

    result = _rotation_agenda(v3_tx)

    for ticker in ("BBB", "EEE"):
        item = _item(result, ticker)
        assert item["rotation"] == {"short": True, "compare": True, "blocked": None, "blocked_text": None}
        assert item["done"] is False  # 还没有结论
    assert result["rotation"] == {"compared_today": False, "last_session": None}
    assert result["complete"] is False


def test_room_enough_candidate_keeps_the_old_done_rule(v3_tx):
    """钱够：rotation.short 为假，done 仍只看当天有没有结论（C2）。"""
    from _rotation_seed import add_holding, seed_opening, seed_run

    seed_run(v3_tx, ("BBB",))
    seed_opening(v3_tx)
    add_holding(v3_tx, "AAA", 300, "40", 40.0)  # 市值 12000：额度充足
    v3_tx.commit()

    item = _item(_rotation_agenda(v3_tx), "BBB")
    assert item["rotation"]["short"] is False and item["rotation"]["compare"] is False and item["done"] is False
    _confirm(v3_tx, "BBB")
    assert _item(_rotation_agenda(v3_tx), "BBB")["done"] is True


def test_blocked_short_candidate_counts_as_done(v3_tx):
    """C7：冷却期内「钱不够」的候选标为不比较并算作完成，complete 不被它卡住；顶层给出冷却日期。"""
    from _rotation_seed import add_rotation, seed_c4

    from investment_assistant.execution.rotation import BLOCKED_TEXT

    ids = seed_c4(v3_tx, ("BBB",))
    two_ago = add_trading_days(SESSION, -2)
    add_rotation(v3_tx, session=two_ago, verdict="half", origin="chat", holding=(ids["a"]["id"], "AAA"))
    v3_tx.commit()

    result = _rotation_agenda(v3_tx)

    item = _item(result, "BBB")
    assert item["rotation"]["short"] is True and item["rotation"]["compare"] is False
    assert item["rotation"]["blocked"] == "cooldown"
    assert item["rotation"]["blocked_text"] == BLOCKED_TEXT["cooldown"].format(last=two_ago)
    assert item["done"] is True  # 没有结论也算完成
    assert result["rotation"] == {"compared_today": False, "last_session": two_ago}
    for plan_item in ids.values():  # 持仓复查都已做
        assert plan_item["id"]
    plan_repo.upsert_brief(v3_tx, {"session": SESSION, "status": "draft"})
    plan_repo.update_brief(v3_tx, SESSION, summary="一句话")
    assert _rotation_agenda(v3_tx)["complete"] is True


def test_rotation_item_done_states(v3_tx):
    """确认而没有记录 → 未完成；否决 → 完成；对话里存了这只股票的记录 → 完成。"""
    from _rotation_seed import add_rotation, seed_c4

    ids = seed_c4(v3_tx, ("BBB", "EEE"))
    v3_tx.commit()

    _confirm(v3_tx, "BBB", "confirm")
    _confirm(v3_tx, "EEE", "veto")
    result = _rotation_agenda(v3_tx)
    assert _item(result, "BBB")["done"] is False  # 确认了但还没有换仓记录
    assert _item(result, "EEE")["done"] is True  # 否决不比较

    add_rotation(v3_tx, verdict="hold", origin="chat", holding=(ids["a"]["id"], "AAA"), candidate="BBB")
    after = _rotation_agenda(v3_tx)
    assert _item(after, "BBB")["done"] is True  # 对话里的记录不挡比较，但这只股票已有记录
    assert _item(after, "BBB")["rotation"]["compare"] is True


def test_second_short_candidate_after_a_comparison(v3_tx):
    """C7：第一只存下简报记录后，第二只变为 compared_today 且 done；第一只被否决（没有记录）时第二只仍 compare。"""
    from _rotation_seed import add_rotation, seed_c4

    from investment_assistant.execution.rotation import BLOCKED_TEXT

    ids = seed_c4(v3_tx, ("BBB", "EEE"))
    v3_tx.commit()

    _confirm(v3_tx, "BBB", "veto")
    vetoed = _rotation_agenda(v3_tx)
    assert _item(vetoed, "EEE")["rotation"]["compare"] is True and _item(vetoed, "EEE")["done"] is False
    assert vetoed["rotation"]["compared_today"] is False

    _confirm(v3_tx, "BBB", "confirm")
    add_rotation(v3_tx, verdict="hold", origin="brief", holding=(ids["a"]["id"], "AAA"), candidate="BBB")
    compared = _rotation_agenda(v3_tx)
    second = _item(compared, "EEE")
    assert second["rotation"]["blocked"] == "compared_today"
    assert second["rotation"]["blocked_text"] == BLOCKED_TEXT["compared_today"] and second["done"] is True
    assert _item(compared, "BBB")["done"] is True
    assert compared["rotation"]["compared_today"] is True


# ---- v3.3 review fix：研究复看得出「可以出计划」的 B 也带换仓标记（spec §6.1 条件 1、§7.1）----


def _ready_research(conn, ticker="RRR", *, verdict="ready"):
    """观察中且当天已触发的研究，当天 agent 已给出结论事件（verdict 写在研究行上）；日线是平的，收盘 50。"""
    from _rotation_seed import NOW as SEED_NOW
    from _rotation_seed import bars as seed_bars

    from investment_assistant.data import repo as data_repo

    data_repo.store_bars(conn, ticker, seed_bars(close=50.0), now=SEED_NOW)
    research = research_repo.insert_research(conn, _research(ticker, triggered_session=SESSION, verdict=verdict))
    research_repo.insert_research_event(
        conn,
        {
            "research_id": research["id"],
            "session": SESSION,
            "event": "concluded",
            "reason": "可以出计划：条件已到",
            "before": {},
            "after": {},
            "source": "agent",
            "refs": [],
        },
    )
    return research


def _research_item(result, ticker="RRR"):
    return next(item for item in result["research"] if item["ticker"] == ticker)


def test_ready_research_gets_a_rotation_mark_and_waits_for_the_comparison(v3_tx):
    """研究结论 ready、B 钱不够且可比较：研究项带 rotation 标记，保存换仓记录之前不算完成。"""
    from _rotation_seed import add_rotation, seed_c4

    ids = seed_c4(v3_tx, ("BBB",))
    _ready_research(v3_tx)
    v3_tx.commit()

    item = _research_item(_rotation_agenda(v3_tx))
    assert item["rotation"] == {"short": True, "compare": True, "blocked": None, "blocked_text": None}
    assert item["done"] is False  # 研究结论有了，换仓比较还没做

    _confirm(v3_tx, "RRR", "confirm")
    assert _research_item(_rotation_agenda(v3_tx))["done"] is False  # 确认了但还没有换仓记录

    add_rotation(v3_tx, verdict="hold", origin="brief", holding=(ids["a"]["id"], "AAA"), candidate="RRR")
    assert _research_item(_rotation_agenda(v3_tx))["done"] is True


def test_ready_research_vetoed_or_blocked_counts_as_done(v3_tx):
    """与确认项同一判定：B 被否决、或不比较（冷却期内）都算完成；钱够时只看研究结论。"""
    from _rotation_seed import add_rotation, seed_c4

    ids = seed_c4(v3_tx, ("BBB",))
    _ready_research(v3_tx)
    v3_tx.commit()
    _confirm(v3_tx, "RRR", "veto")
    assert _research_item(_rotation_agenda(v3_tx))["done"] is True

    _confirm(v3_tx, "RRR", "confirm")
    assert _research_item(_rotation_agenda(v3_tx))["done"] is False
    add_rotation(
        v3_tx, session=add_trading_days(SESSION, -2), verdict="half", origin="chat", holding=(ids["a"]["id"], "AAA")
    )
    blocked = _research_item(_rotation_agenda(v3_tx))
    assert blocked["rotation"]["compare"] is False and blocked["rotation"]["blocked"] == "cooldown"
    assert blocked["done"] is True


def test_ready_research_with_enough_room_keeps_the_old_done_rule(v3_tx):
    from _rotation_seed import add_holding, seed_opening, seed_run

    seed_run(v3_tx, ("BBB",))
    seed_opening(v3_tx)
    add_holding(v3_tx, "AAA", 300, "40", 40.0)  # 市值 12000：额度充足
    _ready_research(v3_tx)
    v3_tx.commit()

    item = _research_item(_rotation_agenda(v3_tx))
    assert item["rotation"]["short"] is False and item["done"] is True


def test_research_without_a_ready_conclusion_has_no_rotation_mark(v3_tx):
    """只有当天结论为「可以出计划」的研究才带标记；值得盯的研究项形状不变。"""
    from _rotation_seed import seed_c4

    seed_c4(v3_tx, ("BBB",))
    _ready_research(v3_tx, verdict="watch")
    v3_tx.commit()

    item = _research_item(_rotation_agenda(v3_tx))
    assert "rotation" not in item and item["done"] is True
