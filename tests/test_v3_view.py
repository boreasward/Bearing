"""brief_view / today_actions over the v3_tx database (spec §7.4, §11): action order,
candidate skip reasons, change counts and the assembled view shape."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.facts import plan_repo, research_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts.view import actions_by_ticker, brief_view, today_actions

SESSION = date(2026, 11, 4)  # 周三
PREV = date(2026, 11, 3)
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)
CFG = AssistantConfig()


def bars(n=1, *, end=SESSION, close=100.0):
    days = [add_trading_days(end, -(n - 1 - i)) for i in range(n)]
    return [
        {
            "price_date": day,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "adj_close": close,
            "volume": 1000,
            "split_factor": 1.0,
        }
        for day in days
    ]


def plan(ticker, **over):
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
        "valid_from": SESSION,
        "valid_until": date(2026, 11, 10),
        "shares": 10,
        "sizing": {},
        "warnings": [],
        "thesis": "论点",
        "invalidation": "作废条件",
        "counter": "",
        "refs": [],
        "agent_session": "view-test",
        "status": "open",
        "kind": "planned",
        "start_session": PREV,
        "reviewed_through": None,
    }
    row.update(over)
    return row


def buy(conn, ticker, request_id):
    facts_repo.insert_trade(
        conn,
        {
            "request_id": request_id,
            "ticker": ticker,
            "side": "buy",
            "quantity": 10,
            "price": D(100),
            "executed_at": datetime(2026, 11, 3, 15, tzinfo=UTC),
        },
    )


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


def _run_row(run_id, session=SESSION):
    return {
        "run_id": run_id,
        "session": session,
        "trigger": "scheduled",
        "status": "ok",
        "error": None,
        "market": {"label": "neutral"},
        "factors": {},
        "coverage": {"families": {}, "missing_bars": []},
        "holdings": [],
        "draft_md": "",
        "started_at": NOW - timedelta(minutes=5),
        "finished_at": NOW,
    }


def run_view(candidates, *, dropped=None):
    return {
        "run_id": "run-1",
        "session": SESSION,
        "market": {"label": "neutral"},
        "coverage": {"families": {}, "missing_bars": []},
        "dropped": dropped if dropped is not None else [],
        "candidates": candidates,
    }


def test_today_actions_follow_section_7_4_order(v3_tx):
    """四条规则各一只持仓，顺序：止损已触及 → 下次开盘卖出 → 缺日线 → 未确认止损单。"""
    data_repo.store_bars(v3_tx, "AAA", bars(), now=NOW)
    data_repo.store_bars(v3_tx, "BBB", bars(), now=NOW)
    data_repo.store_bars(v3_tx, "CCC", bars(), now=NOW)
    data_repo.store_bars(v3_tx, "DDD", bars(end=PREV), now=NOW)  # DDD 没有 SESSION 的日线
    hit = plan_repo.insert_plan(v3_tx, plan("AAA", stop_hit_session=PREV))
    plan_repo.insert_plan(v3_tx, plan("BBB"))  # 未确认止损单
    plan_repo.insert_plan(v3_tx, plan("CCC", sell_instruction="max_hold"))
    plan_repo.insert_plan(v3_tx, plan("DDD"))
    for ticker in ("AAA", "BBB", "CCC", "DDD"):
        buy(v3_tx, ticker, f"{ticker}-buy")
    v3_tx.commit()

    actions = today_actions(v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG)

    assert [(row["ticker"], row["kind"]) for row in actions] == [
        ("AAA", "stop_hit_unresolved"),
        ("CCC", "sell_next_open"),
        ("DDD", "unchecked"),
        ("BBB", "stop_order"),
    ]
    assert actions[0]["text"] == "止损已触及，未见卖出记录"
    assert actions[1]["text"] == "下次开盘卖出" and actions[1]["reason"] == "持有到期"
    assert actions[2]["text"] == "缺日线，无法检查止损"
    assert actions[3]["text"] == "未确认止损单" and D(str(actions[3]["stop"])) == D(96)
    assert all(row["quantity"] == D(10) and row["plan_id"] for row in actions)
    assert hit["id"] == actions[0]["plan_id"]


def test_candidates_skip_reasons(v3_tx):
    """已有计划 → 「已有计划」；名额已满且不是替换候选 → 「名额已满」；替换候选不带原因。"""
    facts_repo.insert_run(v3_tx, _run_row("run-1"))
    facts_repo.insert_candidates(
        v3_tx, "run-1", [_candidate(t, i + 1) for i, t in enumerate(["AAPL", "MSFT", "NVDA", "MU", "AVGO"])]
    )
    v3_tx.commit()

    # 场景一：名次 1–3 都有计划，替换条件不成立 → 没有计划的都写「名额已满」
    for ticker in ("AAPL", "MSFT", "NVDA"):
        plan_repo.insert_plan(v3_tx, plan(ticker, status="pending", start_session=None))
    v3_tx.commit()
    full = brief_view(
        v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run_view(facts_repo.candidates(v3_tx, "run-1"))
    )
    by_ticker = {row["ticker"]: row for row in full["candidates"]}
    assert by_ticker["AAPL"]["skip_reason"] == "已有计划"
    assert by_ticker["AAPL"]["has_active_plan"] is True and by_ticker["AAPL"]["plan_id"] is not None
    assert by_ticker["MU"]["skip_reason"] == "名额已满"
    assert by_ticker["AVGO"]["skip_reason"] == "名额已满"

    # 场景二：换成 MSFT(2)、NVDA(3)、MU(4) 占满 → 替换候选 AAPL(1) 名次更优，只它不带原因
    v3_tx.execute("DELETE FROM v3_plans WHERE ticker = 'AAPL'")
    plan_repo.insert_plan(v3_tx, plan("MU", status="pending", start_session=None))
    v3_tx.commit()
    replaceable = brief_view(
        v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run_view(facts_repo.candidates(v3_tx, "run-1"))
    )
    again = {row["ticker"]: row for row in replaceable["candidates"]}
    assert again["AAPL"]["skip_reason"] is None  # 替换候选：照常进入二次确认
    assert again["MSFT"]["skip_reason"] == "已有计划"
    assert again["AVGO"]["skip_reason"] == "名额已满"


def test_change_counts_from_continuity(v3_tx):
    """昨天与今天各一份名单：新上榜、掉出的计数进 changes；首次运行写「首次运行」。"""
    facts_repo.insert_run(v3_tx, _run_row("run-0", PREV))
    facts_repo.insert_candidates(v3_tx, "run-0", [_candidate("AAPL", 1, streak=1), _candidate("MSFT", 2, streak=3)])
    facts_repo.insert_run(v3_tx, _run_row("run-1", SESSION))
    today = [_candidate("AAPL", 1), _candidate("NVDA", 2)]
    facts_repo.insert_candidates(v3_tx, "run-1", today)
    v3_tx.commit()

    view = brief_view(
        v3_tx,
        session=SESSION,
        cutoff_at=NOW,
        cfg=CFG,
        run=run_view(today, dropped=[{"ticker": "MSFT", "reasons": ["rank_cut"]}]),
    )
    assert view["first_run"] is False
    assert view["changes"]["new_count"] == 1 and view["changes"]["dropped_count"] == 1
    assert view["dropped"] == [{"ticker": "MSFT", "reasons": ["rank_cut"]}]
    streaks = {row["ticker"]: row["streak"] for row in view["candidates"]}
    assert streaks == {"AAPL": 2, "NVDA": 1}
    assert [row["ticker"] for row in view["candidates"] if row["new"]] == ["NVDA"]

    empty = brief_view(v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run_view([], dropped=[]))
    assert empty["first_run"] is False  # run-0 在，不是首次


def test_view_holds_pending_plans_and_account(v3_tx):
    """待入场计划行与账户条来自计划线与账本；一句话与页脚读当天的简报行。"""
    facts_repo.insert_run(v3_tx, _run_row("run-1"))
    plan_repo.insert_plan(v3_tx, plan("AAPL", status="pending", start_session=None))
    buy(v3_tx, "AAPL", "a-buy")
    data_repo.store_bars(v3_tx, "AAPL", bars(), now=NOW)
    plan_repo.insert_cash_flow(
        v3_tx,
        {
            "request_id": "open-1",
            "kind": "opening",
            "amount": D(10000),
            "effective_at": datetime(2026, 11, 1, tzinfo=UTC),
            "note": "",
        },
    )
    plan_repo.upsert_brief(
        v3_tx,
        {
            "session": SESSION,
            "status": "final",
            "summary": "没有新机会，继续持有。",
            "runtime": "pi",
            "provider": "p1",
            "model": "m1",
            "usage": {"input": 10, "output": 5},
            "elapsed_seconds": 60,
        },
    )
    v3_tx.commit()

    view = brief_view(v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run_view([]))

    assert view["pending_plans"] and view["pending_plans"][0]["ticker"] == "AAPL"
    assert view["pending_plans"][0]["shares"] == 10
    assert D(str(view["account"]["cash"])) == D(9000)  # 10000 − 10×100
    assert view["summary"] == "没有新机会，继续持有。"
    assert view["footer"]["model"] == "m1" and view["footer"]["usage"]["input"] == 10
    assert view["error"] is None and view["session"] == SESSION
    assert [row["ticker"] for row in view["holdings"]] == ["AAPL"]  # 持仓行来自成交
    assert view["holdings"][0]["kind"] == "planned"  # 计划还是 pending，只是持仓在


def test_hold_position_flows_through_real_brief_path(v3_tx):
    """终审 I1：确认过止损单的持仓走真实 brief_view —— 第 4 段开头「继续持有」、Discord 推送
    持仓操作一行；「今日操作」（第 0 段）仍只收第 1–4 条，不含第 5 条。"""
    from investment_assistant.facts.brief import discord_lines, render_brief

    data_repo.store_bars(v3_tx, "AAPL", bars(), now=NOW)
    plan_repo.insert_plan(v3_tx, plan("AAPL", confirmed_stop_order=D(96)))  # 挂单=止损 → 第 5 条
    buy(v3_tx, "AAPL", "hold-buy")
    v3_tx.commit()

    view = brief_view(v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run_view([]))

    assert view["today_actions"] == []  # 第 0 段过滤掉第 5 条（spec §11 第 0 段）
    action = view["holdings"][0]["next_action"]
    assert action["kind"] == "hold" and action["text"] == "继续持有"

    text = render_brief(view, final=True)
    holding_line = next(line for line in text.splitlines() if line.startswith("- AAPL："))
    assert holding_line.startswith("- AAPL：继续持有，")  # §4 每只持仓先写操作（spec §11 第 4 段）

    lines = discord_lines(view, brief_path="/tmp/briefs/2026-11-04.md")
    assert lines.count("AAPL：继续持有") == 1  # Discord：持仓操作各一行，且只一行
    assert lines[-1] == "简报文件：/tmp/briefs/2026-11-04.md"


def test_pending_missing_bar_flagged_in_section_5(v3_tx):
    """终审 F1：待入场计划缺当日日线 → brief_view 标 missing_bar，第 5 段行尾「缺日线，未复查」
    （spec §6.3 末段）；有当天日线的计划不带。"""
    from investment_assistant.facts.brief import render_brief

    data_repo.store_bars(v3_tx, "MSFT", bars(), now=NOW)  # 当天日线在
    data_repo.store_bars(v3_tx, "BBB", bars(end=PREV), now=NOW)  # 日线停在昨天：同样算缺
    plan_repo.insert_plan(v3_tx, plan("AAPL", status="pending", start_session=None))  # 完全没有日线
    plan_repo.insert_plan(v3_tx, plan("BBB", status="pending", start_session=None))
    plan_repo.insert_plan(v3_tx, plan("MSFT", status="pending", start_session=None))
    v3_tx.commit()

    view = brief_view(v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run_view([]))
    flags = {row["ticker"]: row["missing_bar"] for row in view["pending_plans"]}
    assert flags == {"AAPL": True, "BBB": True, "MSFT": False}

    text = render_brief(view, final=True)
    lines = {line[2:].split("（")[0]: line for line in text.splitlines() if line.startswith("- ") and "（第" in line}
    assert lines["AAPL"].endswith("，缺日线，未复查")
    assert lines["BBB"].endswith("，缺日线，未复查")
    assert "缺日线，未复查" not in lines["MSFT"]


def test_pending_plan_day_counts_the_saving_session_as_day_one(v3_tx):
    """2026-11-07 上线冒烟：当天保存的计划显示成「第 2 天」。保存当天是第 1 天，下一个交易日第 2 天。"""
    data_repo.store_bars(v3_tx, "AAPL", bars(), now=NOW)
    data_repo.store_bars(v3_tx, "MSFT", bars(), now=NOW)
    plan_repo.insert_plan(v3_tx, plan("AAPL", status="pending", start_session=None))
    plan_repo.insert_plan(v3_tx, plan("MSFT", status="pending", start_session=None, session=PREV))
    v3_tx.commit()

    view = brief_view(v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run_view([]))

    assert {row["ticker"]: row["day"] for row in view["pending_plans"]} == {"AAPL": 1, "MSFT": 2}


def _research(ticker, **over):
    row = {
        "ticker": ticker,
        "thesis": "论点",
        "origin": "chat",
        "created_session": PREV,
        "valid_until": date(2026, 11, 20),
        "condition": {"kind": "close_above", "level": 105},
        "agent_session": "view-test",
    }
    row.update(over)
    return row


def test_research_rows_states_and_changes(v3_tx):
    """研究行四种状态（triggered / no_condition / missing_bar / watching）、第几天计数、
    当天变化各一句、未完成项与 agent 结论句（spec §8 第 2、5a、8 段）。"""
    data_repo.store_bars(v3_tx, "AAA", bars(), now=NOW)  # 条件已到
    data_repo.store_bars(v3_tx, "BBB", bars(), now=NOW)  # 尚无条件
    data_repo.store_bars(v3_tx, "CCC", bars(end=PREV), now=NOW)  # 当天缺日线
    data_repo.store_bars(v3_tx, "DDD", bars(), now=NOW)  # 观察中
    triggered = research_repo.insert_research(v3_tx, _research("AAA", triggered_session=SESSION))
    research_repo.insert_research(v3_tx, _research("BBB", condition=None))
    research_repo.insert_research(v3_tx, _research("CCC"))
    research_repo.insert_research(v3_tx, _research("DDD", condition_text="收盘站上 105.00"))
    research_repo.insert_research_event(
        v3_tx,
        {
            "research_id": triggered["id"],
            "session": SESSION,
            "event": "triggered",
            "reason": "收盘站上 105.00 已到：收盘 110.00",
            "before": {},
            "after": {},
            "source": "host",
            "refs": [],
        },
    )
    ddd = research_repo.watching_research(v3_tx, "DDD")
    assert ddd is not None
    research_repo.insert_research_event(
        v3_tx,
        {
            "research_id": ddd["id"],
            "session": SESSION,
            "event": "concluded",
            "reason": "值得盯：继续等",
            "before": {},
            "after": {},
            "source": "agent",
            "refs": [],
        },
    )
    v3_tx.commit()

    view = brief_view(v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run_view([]))

    states = {row["ticker"]: row["state"] for row in view["research"]}
    assert states == {"AAA": "triggered", "BBB": "no_condition", "CCC": "missing_bar", "DDD": "watching"}
    assert {row["ticker"]: row["day"] for row in view["research"]} == {"AAA": 2, "BBB": 2, "CCC": 2, "DDD": 2}
    row_ddd = next(row for row in view["research"] if row["ticker"] == "DDD")
    assert row_ddd["conclusion"] == "值得盯：继续等"
    assert row_ddd["condition_text"] == "收盘站上 105.00"
    assert view["changes"]["research"] == [
        "研究 AAA：收盘站上 105.00 已到：收盘 110.00",
        "研究 DDD：值得盯：继续等",
    ]
    assert "研究 BBB 尚无条件" in view["unresolved"]["agenda"]  # 尚无条件
    assert "研究复看 AAA" in view["unresolved"]["agenda"]  # 待办未完成


def test_candidate_marked_研究中(v3_tx):
    """R6：名单行标「研究中」，优先于「已有计划」等其他原因；没有研究的行不带。"""
    facts_repo.insert_run(v3_tx, _run_row("run-1"))
    facts_repo.insert_candidates(v3_tx, "run-1", [_candidate(t, i + 1) for i, t in enumerate(["AAPL", "MSFT"])])
    research_repo.insert_research(v3_tx, _research("AAPL"))
    plan_repo.insert_plan(v3_tx, plan("AAPL", status="pending", start_session=None))  # 同时有计划
    v3_tx.commit()

    view = brief_view(
        v3_tx,
        session=SESSION,
        cutoff_at=NOW,
        cfg=CFG,
        run=run_view(facts_repo.candidates(v3_tx, "run-1")),
    )

    by_ticker = {row["ticker"]: row for row in view["candidates"]}
    assert by_ticker["AAPL"]["has_research"] is True and by_ticker["AAPL"]["skip_reason"] == "研究中"
    assert by_ticker["MSFT"]["has_research"] is False and by_ticker["MSFT"]["skip_reason"] is None


def test_research_ma_insufficient_state(v3_tx):
    """P2-1：close_above_ma 当天有日线但历史不足 ma 根 → 「日线不足，未检查」，不是「观察中」。"""
    from investment_assistant.facts.brief import render_brief

    data_repo.store_bars(v3_tx, "AAA", bars(20), now=NOW)  # 只有 20 根
    research_repo.insert_research(
        v3_tx,
        _research("AAA", condition={"kind": "close_above_ma", "ma": 200}, condition_text="收盘站上 200 日均线"),
    )
    v3_tx.commit()

    view = brief_view(v3_tx, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run_view([]))

    row = next(item for item in view["research"] if item["ticker"] == "AAA")
    assert row["state"] == "insufficient_history"
    assert row["last_close"] is not None  # 当天有日线
    text = render_brief(view, final=True)
    assert "日线不足，未检查" in text


# ---- v3.3：换仓指令、止损单数量、仓位占比与换仓文案（spec §5、§6.6、§8）----


def test_today_actions_rotation_row(v3_db):
    """换仓卖出一行：kind rotation_sell，带 sell_quantity、counterpart；排在 sell_next_open 之后、unchecked 之前；
    止损单数量待改带 stale_quantity。"""
    from _rotation_seed import add_holding, holding_review, instruct, seed_c4

    ids = seed_c4(v3_db)
    sell_open = add_holding(v3_db, "SNO", 10, "40", 40.0, sell_instruction="max_hold")
    unchecked = add_holding(v3_db, "UNC", 10, "40", 40.0, bars_end=PREV)
    stale = add_holding(v3_db, "STL", 10, "40", 40.0, confirmed_stop_order=D(36))
    plan_repo.update_plan(v3_db, stale["id"], stop_quantity_stale=True)
    for plan_ in (sell_open, unchecked, stale):
        holding_review(v3_db, plan_["id"], "holds")
    instruct(v3_db, ids["a"], candidate="BBB", quantity=150)
    v3_db.commit()

    rows = today_actions(v3_db, session=SESSION, cutoff_at=NOW, cfg=CFG)

    kinds = [row["kind"] for row in rows]
    assert kinds.index("sell_next_open") < kinds.index("rotation_sell") < kinds.index("unchecked")
    rotation = next(row for row in rows if row["kind"] == "rotation_sell")
    assert rotation["ticker"] == "AAA" and rotation["sell_quantity"] == D(150) and rotation["counterpart"] == "BBB"
    assert rotation["text"] == "下次开盘卖出 150 股（换仓至 BBB）" and rotation["stale_quantity"] is False
    stale_row = next(row for row in rows if row["ticker"] == "STL")
    assert stale_row["kind"] == "stop_order" and stale_row["stale_quantity"] is True
    assert stale_row["text"] == "止损单数量需改为 10 股" and stale_row["sell_quantity"] is None
    plain = next(row for row in rows if row["ticker"] == "CCC")
    assert plain["sell_quantity"] is None and plain["counterpart"] is None and plain["stale_quantity"] is False
    by_ticker = actions_by_ticker(v3_db, session=SESSION, cutoff_at=NOW, cfg=CFG)
    assert by_ticker["AAA"]["sell_quantity"] == D(150) and by_ticker["AAA"]["counterpart"] == "BBB"
    assert by_ticker["STL"]["stale_quantity"] is True and by_ticker["CCC"]["stale_quantity"] is False


def test_brief_view_rotation_fields(v3_db):
    """account.exposure、candidates[].rotation_text 的三种取值、changes.rotations、unresolved.rotation、view.rotations。"""
    from _rotation_seed import add_rotation, seed_c4

    from investment_assistant.execution.rotation import BLOCKED_TEXT, DRAFT_TEXT

    ids = seed_c4(v3_db, ("BBB", "EEE", "AAA"))
    for ticker in ("EEE",):
        facts_repo.upsert_verdict(
            v3_db,
            {"session": SESSION, "ticker": ticker, "verdict": "confirm", "reasons": "理由", "counter": "",
             "refs": [], "origin": "brief", "agent_session": "view-test"},
        )  # fmt: skip
    add_rotation(v3_db, verdict="hold", origin="chat", candidate="BBB", holding=(ids["a"]["id"], "AAA"))
    v3_db.commit()
    run = {"market": {}, "coverage": {"families": {}, "missing_bars": []}, "dropped": []}

    view = brief_view(v3_db, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run)

    assert view["account"]["exposure"]["pct"] == D("0.5493") and view["account"]["exposure"]["room"] == D("5076.30")
    texts = {row["ticker"]: row["rotation_text"] for row in view["candidates"]}
    assert texts["BBB"] == "换仓比较：继续持有（AAA）"  # 当天有记录
    assert texts["EEE"] == DRAFT_TEXT  # 钱不够、可比较、还没有记录
    assert texts["AAA"] is None  # 已持有的不在待办里
    assert view["changes"]["rotations"] == ["换仓比较 BBB：继续持有（AAA）"]
    assert view["rotations"] == [
        {"session": SESSION, "candidate": "BBB", "verdict": "hold", "holding_ticker": "AAA"}
    ]
    assert view["unresolved"]["rotation"] == ["换仓比较未完成：EEE"]

    # 冷却期内：EEE 不比较，写明原因，不再出现在未完成里；BBB 仍显示当天的记录
    from investment_assistant.data.calendar import add_trading_days

    two_ago = add_trading_days(SESSION, -2)
    add_rotation(v3_db, session=two_ago, verdict="half", origin="chat", holding=(ids["a"]["id"], "AAA"))
    v3_db.commit()
    cooling = brief_view(v3_db, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run)
    texts = {row["ticker"]: row["rotation_text"] for row in cooling["candidates"]}
    assert texts["EEE"] == BLOCKED_TEXT["cooldown"].format(last=two_ago)
    assert texts["BBB"] == "换仓比较：继续持有（AAA）"
    assert cooling["unresolved"]["rotation"] == []


def test_hold_verdict_without_a_holding_has_no_parentheses(v3_db):
    from _rotation_seed import add_rotation, seed_c4

    seed_c4(v3_db, ("BBB",))
    add_rotation(v3_db, verdict="hold", origin="chat", candidate="BBB")
    v3_db.commit()
    run = {"market": {}, "coverage": {"families": {}, "missing_bars": []}, "dropped": []}

    view = brief_view(v3_db, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run)

    assert view["changes"]["rotations"] == ["换仓比较 BBB：继续持有"]
    assert view["candidates"][0]["rotation_text"] == "换仓比较：继续持有"


def test_draft_without_model_has_exposure_and_draft_text(v3_db):
    """C13：没有 agent 的草稿里账户一节有仓位占比与剩余额度，钱不够的候选写「仓位已到上限，换仓比较未做」。"""
    from _rotation_seed import seed_c4

    from investment_assistant.facts.brief import render_brief

    seed_c4(v3_db, ("BBB",))
    v3_db.commit()
    run = {"market": {}, "coverage": {"families": {}, "missing_bars": []}, "dropped": []}

    draft = render_brief(brief_view(v3_db, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run), final=False)

    assert "仓位占比 54.9%（上限 60%），剩余额度 $5076.30" in draft
    assert "仓位已到上限，换仓比较未做" in draft


def test_over_cap_without_candidate_gives_no_sell(v3_db):
    """C12：仓位 70%、名单为空：今日操作里没有任何卖出，账户行如实显示占比。"""
    from _rotation_seed import add_holding, holding_review, seed_opening, seed_run

    seed_run(v3_db, ())
    seed_opening(v3_db)
    held = add_holding(v3_db, "AAA", 700, "100", 100.0)
    holding_review(v3_db, held["id"], "holds")
    v3_db.commit()

    actions = today_actions(v3_db, session=SESSION, cutoff_at=NOW, cfg=CFG)
    assert [row["kind"] for row in actions] == ["stop_order"]  # 只有未确认止损单，没有 sell_next_open / rotation_sell
    run = {"market": {}, "coverage": {"families": {}, "missing_bars": []}, "dropped": []}
    view = brief_view(v3_db, session=SESSION, cutoff_at=NOW, cfg=CFG, run=run)
    assert view["account"]["exposure"]["pct"] == D("0.7000") and view["account"]["exposure"]["room"] == D("0.00")
