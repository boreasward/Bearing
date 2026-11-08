"""Research tools over the v3_tx database (spec §6, §9): start / conclude / drop / read,
the watch-condition user confirmation, brief-mode rejection and the plan linkage."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.facts import plan_repo, research_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.toolsurface.dispatch import call, tool_defs
from investment_assistant.toolsurface.env import make_env

SESSION = date(2026, 11, 4)  # 周三
DAY2 = date(2026, 11, 5)  # 周四：跨日重放用
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


def _run_row(run_id="run-1"):
    return {
        "run_id": run_id,
        "session": SESSION,
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


def _env(v3_tx, mode="brief", agent="t-research"):
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
    """直接插一份 034 形状的规则计划（不走工具）。"""
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


def _research(ticker, **over):
    """直接插一份观察中的研究（不走工具）。"""
    row = {
        "ticker": ticker,
        "thesis": "论点",
        "origin": "chat",
        "created_session": date(2026, 11, 3),
        "valid_until": date(2026, 11, 20),
        "agent_session": "seed",
    }
    row.update(over)
    return row


def _start(env, ticker, request_id, thesis="最近跌很多，我认为是入场的时间"):
    r = call(env, "start_research", {"ticker": ticker, "thesis": thesis, "request_id": request_id, "confirmed": True})
    assert "data" in r, r
    return r["data"]["research"]


def test_start_research_preview_confirm_idempotent_and_rejections(v3_tx):
    """预览文案逐字；重放同一 request_id 返回原行；active_plan_exists / research_exists 各带 detail。"""
    _seed(v3_tx)
    env = _env(v3_tx, mode="chat", agent="t-start")
    brief = _env(v3_tx, agent="t-start-brief")
    assert call(brief, "start_research", {"ticker": "SNDK", "thesis": "论点", "request_id": "r1", "confirmed": True})[
        "error"
    ].startswith("not_available_in_mode")
    preview = call(
        env, "start_research", {"ticker": "SNDK", "thesis": "最近跌很多", "request_id": "r1", "confirmed": False}
    )["data"]
    assert preview == {"write": False, "preview": "将为 SNDK 建立研究，论点：最近跌很多。确认后写入。"}

    saved = call(
        env, "start_research", {"ticker": "SNDK", "thesis": "最近跌很多", "request_id": "r1", "confirmed": True}
    )["data"]
    row = saved["research"]
    assert row["status"] == "watching" and row["origin"] == "chat" and row["created_session"] == SESSION.isoformat()
    assert row["valid_until"] == add_trading_days(SESSION, env.cfg.v3.research_sessions).isoformat()
    assert saved["event"]["event"] == "created" and saved["event"]["source"] == "user"
    assert saved["event"]["refs"] == ["r1"] and saved["event"]["reason"] == "最近跌很多"

    replay = call(
        env, "start_research", {"ticker": "SNDK", "thesis": "最近跌很多", "request_id": "r1", "confirmed": True}
    )["data"]
    assert replay["replay"] is True and replay["research"]["id"] == row["id"]  # 同 request_id 幂等

    rejected = call(
        env, "start_research", {"ticker": "SNDK", "thesis": "再来一份", "request_id": "r2", "confirmed": True}
    )
    assert rejected["error"].startswith("research_exists") and rejected["detail"]["id"] == row["id"]

    plan = _plan(v3_tx, "MSFT")
    v3_tx.commit()
    blocked = call(env, "start_research", {"ticker": "MSFT", "thesis": "论点", "request_id": "r3", "confirmed": True})
    assert blocked["error"].startswith("active_plan_exists") and blocked["detail"]["id"] == plan["id"]


def test_conclude_watch_needs_condition_and_user_confirmation(v3_tx):
    """chat 模式：无条件的研究 watch 不带条件 → condition_required；带条件先预览，确认后写条件与有效期。"""
    _seed(v3_tx)
    env = _env(v3_tx, mode="chat", agent="t-watch")
    created = _start(env, "SNDK", "w0")
    assert call(env, "conclude_research", {"research_id": created["id"], "verdict": "watch", "reasons": "值得盯"})[
        "error"
    ].startswith("condition_required")

    condition = {"kind": "close_above", "level": 105}
    preview = call(
        env,
        "conclude_research",
        {
            "research_id": created["id"],
            "verdict": "watch",
            "reasons": "等站上 105 再看",
            "condition": condition,
            "request_id": "w1",
            "confirmed": False,
        },
    )["data"]
    assert preview == {"write": False, "preview": "将把 SNDK 的研究条件定为「收盘站上 105.00」。确认后写入。"}
    saved = call(
        env,
        "conclude_research",
        {
            "research_id": created["id"],
            "verdict": "watch",
            "reasons": "等站上 105 再看",
            "condition": condition,
            "request_id": "w1",
            "confirmed": True,
        },
    )["data"]
    row = saved["research"]
    assert row["verdict"] == "watch" and row["condition_text"] == "收盘站上 105.00"
    assert row["valid_until"] == add_trading_days(SESSION, env.cfg.v3.research_sessions).isoformat()  # 从当天重新起算
    assert row["checked_through"] == SESSION.isoformat() and row["triggered_session"] is None
    assert saved["event"]["event"] == "concluded" and saved["event"]["source"] == "agent"
    assert saved["event"]["after"]["condition_text"] == "收盘站上 105.00"


def test_brief_mode_watch_with_new_condition_is_rejected(v3_tx):
    """Review Focus 3 / B9：简报模式不能改条件（condition_needs_user）；保留原条件的 watch 可以。"""
    _seed(v3_tx)
    env = _env(v3_tx, agent="t-brief-watch")
    watching = research_repo.insert_research(
        v3_tx,
        _research(
            "SNDK",
            condition={"kind": "close_above", "level": 105},
            condition_text="收盘站上 105.00",
            checked_through=SESSION,
        ),
    )
    triggered = research_repo.insert_research(
        v3_tx,
        _research(
            "MU",
            condition={"kind": "close_above", "level": 105},
            condition_text="收盘站上 105.00",
            checked_through=SESSION,
            triggered_session=SESSION,
        ),
    )
    v3_tx.commit()

    rejected = call(
        env,
        "conclude_research",
        {
            "research_id": watching["id"],
            "verdict": "watch",
            "reasons": "换个条件",
            "condition": {"kind": "close_below", "level": 90},
        },
    )
    assert rejected["error"].startswith("condition_needs_user")

    kept = call(env, "conclude_research", {"research_id": triggered["id"], "verdict": "watch", "reasons": "继续等"})[
        "data"
    ]
    row = kept["research"]
    assert row["condition_text"] == "收盘站上 105.00"  # 原条件保留
    assert row["triggered_session"] is None and row["checked_through"] == SESSION.isoformat()
    assert row["valid_until"] == add_trading_days(SESSION, env.cfg.v3.research_sessions).isoformat()


def test_conclude_reject_ends_research(v3_tx):
    """reject 要有已知引用；写 rejected 终态与事件；终态研究再给结论 → not_watching。"""
    _seed(v3_tx)
    env = _env(v3_tx, mode="chat", agent="t-reject")
    created = _start(env, "SNDK", "j0")
    ref = call(env, "get_research", {})["ref"]
    assert call(env, "conclude_research", {"research_id": created["id"], "verdict": "reject", "reasons": "不成立"})[
        "error"
    ].startswith("invalid_refs")
    saved = call(
        env,
        "conclude_research",
        {"research_id": created["id"], "verdict": "reject", "reasons": "行业转弱", "refs": [ref]},
    )["data"]
    row = saved["research"]
    assert (
        row["status"] == "rejected" and row["ended_session"] == SESSION.isoformat() and row["end_reason"] == "行业转弱"
    )
    assert saved["event"]["event"] == "rejected" and saved["event"]["source"] == "agent"
    again = call(
        env,
        "conclude_research",
        {"research_id": created["id"], "verdict": "ready", "reasons": "再给结论", "refs": [ref]},
    )
    assert again["error"].startswith("not_watching")


def test_ready_then_save_plan_links_research_and_skips_slots(v3_tx):
    """ready 后照常 save_verdict + save_plan：计划带 research_id、研究记 planned；名额满也照常保存。"""
    _seed(v3_tx)
    data_repo.store_bars(v3_tx, "SNDK", _bars(), now=NOW)
    for ticker in ("MSFT", "NVDA", "MU"):  # 3 份规则计划占满名额
        _plan(v3_tx, ticker)
    v3_tx.commit()
    env = _env(v3_tx, mode="chat", agent="t-ready")
    created = _start(env, "SNDK", "p0", thesis="跌很多，可能是入场时机")
    ref = call(env, "get_research", {})["ref"]
    concluded = call(
        env,
        "conclude_research",
        {"research_id": created["id"], "verdict": "ready", "reasons": "可以出计划", "refs": [ref]},
    )["data"]
    assert concluded["research"]["verdict"] == "ready" and concluded["research"]["status"] == "watching"

    confirm(env, "SNDK")
    saved = call(env, "save_plan", {"ticker": "SNDK", **TEXTS})["data"]  # 名额满：研究计划不做名额检查
    assert saved["research_id"] == created["id"] and saved["status"] == "pending"
    row = research_repo.research(v3_tx, created["id"])
    assert row is not None
    assert row["status"] == "planned" and row["plan_id"] == saved["id"] and row["end_reason"] == "已转计划"
    events = [event["event"] for event in research_repo.research_events(v3_tx, created["id"])]
    assert events == ["created", "concluded", "planned"]
    for ticker in ("MSFT", "NVDA", "MU"):  # 没有规则计划被替换
        victim = plan_repo.active_plan(v3_tx, ticker)
        assert victim is not None and victim["status"] == "pending"


def test_drop_research_only_watching(v3_tx):
    """预览带原因的两种文案；dropped 终态与默认原因；幂等重放；非 watching → not_watching。"""
    _seed(v3_tx)
    env = _env(v3_tx, mode="chat", agent="t-drop")
    created = _start(env, "SNDK", "d0")
    preview = call(env, "drop_research", {"research_id": created["id"], "request_id": "d1", "confirmed": False})["data"]
    assert preview == {"write": False, "preview": "将放弃研究 SNDK。确认后写入。"}
    with_reason = call(
        env, "drop_research", {"research_id": created["id"], "request_id": "d2", "confirmed": False, "reason": "不看了"}
    )["data"]
    assert with_reason == {"write": False, "preview": "将放弃研究 SNDK（不看了）。确认后写入。"}
    saved = call(env, "drop_research", {"research_id": created["id"], "request_id": "d3", "confirmed": True})["data"]
    assert saved["research"]["status"] == "dropped" and saved["research"]["end_reason"] == "用户放弃"
    assert saved["event"]["event"] == "dropped" and saved["event"]["source"] == "user"
    replay = call(env, "drop_research", {"research_id": created["id"], "request_id": "d3", "confirmed": True})["data"]
    assert replay["replay"] is True and replay["research"]["id"] == created["id"]
    again = call(env, "drop_research", {"research_id": created["id"], "request_id": "d4", "confirmed": True})
    assert again["error"].startswith("not_watching")
    unknown = call(env, "drop_research", {"research_id": 999, "request_id": "d5", "confirmed": True})
    assert unknown["error"].startswith("unknown_research")


def test_get_research_filters(v3_tx):
    """缺省全部观察中；按状态、股票、编号过滤，每份带变化记录；非法状态 → invalid_status。"""
    _seed(v3_tx)
    env = _env(v3_tx, mode="chat", agent="t-get")
    dropped = _start(env, "SNDK", "g0")
    call(env, "drop_research", {"research_id": dropped["id"], "request_id": "g1", "confirmed": True})
    watching = _start(env, "MU", "g2")

    default = call(env, "get_research", {})["data"]["research"]
    assert [row["id"] for row in default] == [watching["id"]]
    assert [event["event"] for event in default[0]["events"]] == ["created"]

    by_status = call(env, "get_research", {"status": "dropped"})["data"]["research"]
    assert [row["id"] for row in by_status] == [dropped["id"]]
    assert [event["event"] for event in by_status[0]["events"]] == ["created", "dropped"]

    by_ticker = call(env, "get_research", {"status": "dropped", "ticker": "SNDK"})["data"]["research"]
    assert [row["id"] for row in by_ticker] == [dropped["id"]]
    by_id = call(env, "get_research", {"research_id": dropped["id"]})["data"]["research"]
    assert [row["id"] for row in by_id] == [dropped["id"]]
    assert call(env, "get_research", {"status": "wat"})["error"].startswith("invalid_status")


def test_tool_counts_35_chat_23_brief(v3_tx):
    """四个新工具登记后：chat 35、brief 23；start/drop 仅对话模式。"""
    brief_tools = {tool.name for tool in tool_defs("brief")}
    chat_tools = {tool.name for tool in tool_defs("chat")}
    assert len(chat_tools) == 35 and len(brief_tools) == 23
    assert {"start_research", "conclude_research", "drop_research", "get_research"} <= chat_tools
    assert {"conclude_research", "get_research"} <= brief_tools
    assert "start_research" not in brief_tools and "drop_research" not in brief_tools


def test_start_research_replay_across_days(v3_tx):
    """P1-4/B7：request_id 幂等不限同日——次日重放返回原行；原研究已结束后重放也返回原行、不新建。"""
    _seed(v3_tx)
    day1 = _env(v3_tx, mode="chat", agent="t-replay1")
    created = _start(day1, "SNDK", "x1", thesis="最近跌很多")
    day2 = make_env(session=DAY2, mode="chat", agent_session="t-replay2", cfg=AssistantConfig(), now=NOW2)

    replay = call(
        day2, "start_research", {"ticker": "SNDK", "thesis": "最近跌很多", "request_id": "x1", "confirmed": True}
    )["data"]
    assert replay["replay"] is True and replay["research"]["id"] == created["id"]

    call(day1, "drop_research", {"research_id": created["id"], "request_id": "drop-1", "confirmed": True})
    again = call(
        day2, "start_research", {"ticker": "SNDK", "thesis": "最近跌很多", "request_id": "x1", "confirmed": True}
    )["data"]
    assert again["replay"] is True and again["research"]["id"] == created["id"]
    assert again["research"]["status"] == "dropped"  # 返回原行（已终态），不是误拒也不是新建
    rows = research_repo.research_by_status(v3_tx, ("watching", "planned", "rejected", "expired", "dropped"))
    assert [row["id"] for row in rows] == [created["id"]]  # 全库仍只有这一份
