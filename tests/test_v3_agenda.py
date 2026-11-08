"""The day's agenda over the v3_tx database (spec §9.3, §6.4): free slots, the single
replacement candidate, per-item done flags and the completion test."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

from investment_assistant.config import AssistantConfig
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
