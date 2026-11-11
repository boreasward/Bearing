"""Read tools over the v3_tx database: stored facts, env-fixed cutoff, error envelopes."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.facts import plan_repo, research_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.toolsurface.dispatch import call, tool_defs
from investment_assistant.toolsurface.env import make_env

SESSION = date(2026, 11, 4)  # 周三
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)  # 数据截止时点
TOP5 = ["AAPL", "MSFT", "NVDA", "MU", "AVGO"]


def _bars(n=20, *, end=SESSION, close=100.0):
    """n 根日线（含 end 当天），真实波幅恒为 2，最新收盘 close。"""
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
        "market": {
            "label": "neutral",
            "regime_tier": "中性",
            "breadth": 0.5,
            "spy_ret20": 0.01,
            "etf_table": [{"etf": "XLK", "ret20": 0.02}],
            "macro": {"DGS10": 0.04},
        },
        "factors": {},
        "coverage": {"families": {"bars": {"total": 5, "ok": 5, "missing": [], "skipped": None}}, "missing_bars": []},
        "holdings": [],
        "draft_md": "# 草稿",
        "dropped": [{"ticker": "INTC", "reasons": ["rank_cut"]}],
        "started_at": NOW - timedelta(minutes=5),
        "finished_at": NOW,
    }


def _candidate(ticker, rank, *, streak=2):
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
        "detail": {"streak": streak},
    }


@pytest.fixture
def env(v3_tx):
    facts_repo.insert_run(v3_tx, _run_row())
    facts_repo.insert_candidates(
        v3_tx, "run-1", [_candidate(t, i + 1) for i, t in enumerate(TOP5)] + [_candidate("AMD", 6)]
    )  # 第 6 只不在前 N
    for ticker in TOP5:
        data_repo.store_bars(v3_tx, ticker, _bars(), now=NOW)
    data_repo.store_news(
        v3_tx,
        [
            (
                {
                    "source": "yfinance",
                    "title": f"新闻 {i}",
                    "url": f"https://n.example/{i}",
                    "published_at": NOW - timedelta(hours=i),
                },
                ["AAPL"],
            )
            for i in range(25)
        ],
        now=NOW,
    )
    for i in range(25):
        v3_tx.execute(
            "INSERT INTO filings (ticker, form, filing_date, accession_number, "
            "primary_document, file_path, accepted_at, items, created_at) "
            "VALUES (%s, '10-Q', %s, %s, %s, %s, %s, %s, %s)",
            (
                "AAPL",
                SESSION - timedelta(days=i),
                f"acc-{i:03d}",
                f"doc-{i}.htm",
                "/tmp/f",
                NOW - timedelta(days=i),
                ["Item 2"],
                NOW - timedelta(days=i),
            ),
        )
    v3_tx.commit()  # 事实已入库，工具的事务回滚不应带走它
    return make_env(session=SESSION, mode="brief", agent_session="t-read", cfg=AssistantConfig(), now=NOW)


def test_get_market_carries_calc_version(v3_tx):
    run = _run_row()
    run["market"]["calc_version"] = "regime-v3"
    facts_repo.insert_run(v3_tx, run)
    v3_tx.commit()
    env = make_env(session=SESSION, mode="brief", agent_session="t-market-version", cfg=AssistantConfig(), now=NOW)
    assert call(env, "get_market", {})["data"]["calc_version"] == "regime-v3"


def test_read_tools_return_stored_facts(env, v3_tx):
    out = [
        call(env, "get_market", {}),
        call(env, "get_candidates", {}),
        call(env, "get_price_bars", {"ticker": "aapl", "lookback": 3}),
    ]
    assert [o["ref"] for o in out] == ["call:1", "call:2", "call:3"]  # ref 递增
    assert out[0]["data"]["label"] == "neutral"
    assert out[0]["data"]["session"] == SESSION.isoformat()
    cands = out[1]["data"]
    assert [c["ticker"] for c in cands["candidates"] if c["in_top_n"]] == TOP5 and cands["top_n"] == 5
    assert cands["candidates"][0]["verdict"] is None  # 还没有结论
    assert len(out[2]["data"]) == 3
    assert out[2]["data"][-1] == {
        "date": SESSION.isoformat(),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.0,
        "volume": 1000,
    }
    assert len(call(env, "get_news", {"ticker": "AAPL"})["data"]) == 20  # 各自不超过 20 条
    assert len(call(env, "get_filings", {"ticker": "AAPL"})["data"]) == 20
    assert call(env, "get_earnings", {"ticker": "AAPL"})["data"] is None  # 没有数据返回 None
    assert len(call(env, "get_positions", {})["data"]) == 0
    assert v3_tx.execute("SELECT count(*) FROM v3_tool_calls").fetchone()[0] == 7  # 每次调用一行


def test_cutoff_and_session_are_fixed_by_env(env, v3_tx):
    data_repo.store_bars(v3_tx, "AAPL", _bars(1, end=SESSION + timedelta(days=1)), now=NOW)
    later = datetime(2026, 11, 5, 5, 0, tzinfo=UTC)
    data_repo.store_bars(
        v3_tx,
        "AAPL",
        [
            {
                "price_date": SESSION,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 90.0,
                "adj_close": 90.0,
                "volume": 1,
                "split_factor": 1.0,
            }
        ],
        now=later,
    )
    bars = call(env, "get_price_bars", {"ticker": "AAPL", "lookback": 60})["data"]
    assert bars[-1]["date"] == SESSION.isoformat()  # 最后一根仍是 env.session
    assert bars[-1]["close"] == 100.0  # 晚于 cutoff 的修订不被读到
    assert len(bars) == 20  # 之后一天的日线也不出现


def test_bad_arguments_return_error_envelope(env, v3_tx):
    errs = [
        call(env, "nope", {}),
        call(env, "get_price_bars", {"ticker": "AAPL", "lookback": 100000}),
        call(env, "get_price_bars", {"ticker": "aa pl"}),
        call(env, "get_price_bars", {"ticker": "AAPL", "extra": 1}),
        call(env, "get_price_bars", {}),
    ]
    assert errs[0]["error"].startswith("unknown_tool")
    assert errs[1]["error"].startswith("invalid_argument")
    assert errs[2]["error"].startswith("invalid_ticker")
    assert errs[3]["error"].startswith("unknown_argument")
    assert errs[4]["error"].startswith("missing_argument")
    assert call(env, "get_price_bars", {"ticker": "ZZZZ"})["data"] == []  # 没有日线不算错误
    assert call(env, "get_indicators", {"ticker": "ZZZZ"})["error"].startswith("no_bars")
    rows = v3_tx.execute("SELECT error FROM v3_tool_calls ORDER BY seq").fetchall()
    assert len(rows) == 7  # 每次调用（含出错的）记一行
    assert all(r[0] for r in rows[:5])  # 出错的调用 error 列有值
    assert call(env, "get_market", {})["data"]["label"] == "neutral"  # 之后的正常调用照常成功


def test_chat_only_tools_hidden_in_brief(env):
    assert "record_trade" not in {t.name for t in tool_defs("brief")}
    assert "record_trade" in {t.name for t in tool_defs("chat")}
    assert call(env, "record_equity", {"equity": 1, "request_id": "r", "confirmed": True})["error"].startswith(
        "not_available_in_mode"
    )


def test_no_run_for_session(v3_tx):
    env = make_env(session=SESSION, mode="brief", agent_session="t-none", cfg=AssistantConfig(), now=NOW)
    assert call(env, "get_market", {})["error"].startswith("no_run")
    assert call(env, "get_candidates", {})["error"].startswith("no_run")
    assert call(env, "get_coverage", {})["error"].startswith("no_run")


def test_env_origin_defaults_to_mode_and_latest_session_reads_ok_runs(v3_tx):
    """Task 4/5 依赖的 ToolEnv 接口：origin 缺省等于 mode；latest_session 只认 ok 运行。"""
    env = make_env(session=SESSION, mode="brief", agent_session="t-env", cfg=AssistantConfig(), now=NOW)
    assert env.origin == "brief"  # 页面表单显式传 "web"
    assert (
        make_env(
            session=SESSION, mode="chat", agent_session="t-env2", cfg=AssistantConfig(), now=NOW, origin="web"
        ).origin
        == "web"
    )

    def _peek(conn):
        return env.latest_session(conn)

    assert env.read(_peek) is None  # 没有任何运行记录
    row = _run_row("run-err")
    row["status"] = "error"
    row["error"] = "基准日线缺失"
    facts_repo.insert_run(v3_tx, row)
    v3_tx.commit()
    assert env.read(_peek) is None  # error 运行不算
    facts_repo.insert_run(v3_tx, _run_row())
    v3_tx.commit()
    assert env.read(_peek) == SESSION


# --- get_concept reads the real concept-card catalog in docs/knowledge; it never touches the environment ---


class _NoEnv:
    def __getattr__(self, name):
        raise AssertionError(f"get_concept must not use env.{name}")


def test_get_concept_without_id_lists_the_42_kept_cards():
    from investment_assistant.toolsurface.reads import get_concept

    out = get_concept(_NoEnv())  # type: ignore[arg-type]  # 离线测试替身：get_concept 不读 env 的任何字段
    assert out["knowledge_version"] == "knowledge-1" and out["point_in_time"] is False
    ids = [item["id"] for item in out["concepts"]]
    assert len(ids) == 42 and len(set(ids)) == 42
    assert {item["group"] for item in out["concepts"]} == {"indicator", "rule", "plan"}
    assert "plan.stop" in ids and set(out["concepts"][0]) == {"id", "title", "group"}


def test_get_concept_returns_the_card_body():
    from investment_assistant.toolsurface.reads import get_concept

    out = get_concept(_NoEnv(), concept_id="plan.stop")  # type: ignore[arg-type]  # 离线测试替身
    assert out["id"] == "plan.stop" and out["group"] == "plan" and out["concept_ref"] == "concept:plan.stop@1"
    assert out["title"] and out["summary"] and out["body"].strip()
    assert len(out["content_sha256"]) == 64
    assert out["knowledge_version"] == "knowledge-1" and out["point_in_time"] is False and out["note"]


def test_get_concept_treats_an_archived_card_like_an_unknown_one():
    from pathlib import Path

    from investment_assistant.toolsurface.reads import get_concept

    archived = sorted(Path("docs/archive/v2/knowledge/concepts").glob("system.*.md"))
    assert archived, "the archive keeps the v2 system cards"
    archived_id = archived[0].stem
    with pytest.raises(LookupError) as unknown:
        get_concept(_NoEnv(), concept_id="no.such.card")  # type: ignore[arg-type]  # 离线测试替身
    with pytest.raises(LookupError) as gone:
        get_concept(_NoEnv(), concept_id=archived_id)  # type: ignore[arg-type]  # 离线测试替身
    assert str(gone.value) == str(unknown.value).replace("no.such.card", archived_id)
    assert "未知概念" in str(gone.value)


# ---------------------------------------------------------------- v3.1 计划线读工具

CHAT_ONLY = {
    "record_trade",
    "record_equity",
    "record_cash_flow",
    "confirm_stop_order",
    "raise_stop",
    "cancel_plan",
    "void_record",
    "watchlist_add",
    "watchlist_remove",
    "ensure_data",
    "start_research",
    "drop_research",
    "withdraw_rotation",
}


def test_tool_surface_has_38_tools_with_13_chat_only():
    brief_tools = {tool.name for tool in tool_defs("brief")}
    chat_tools = {tool.name for tool in tool_defs("chat")}
    assert len(chat_tools) == 38
    assert chat_tools - brief_tools == CHAT_ONLY
    assert len(brief_tools) == 25  # v3.3 加 get_rotation_table、save_rotation


def test_get_candidates_carries_streak_plan_and_dropped(env, v3_tx):
    plan = plan_repo.insert_plan(
        v3_tx,
        {
            "session": SESSION,
            "ticker": "NVDA",
            "origin": "brief",
            "entry_type": "zone_limit",
            "entry_low": None,
            "entry_high": None,
            "stop": None,
            "target1": None,
            "levels_source": "host",
            "max_hold_sessions": 20,
            "valid_from": SESSION,
            "valid_until": SESSION,
            "shares": None,
            "sizing": {},
            "warnings": [],
            "thesis": "",
            "invalidation": "",
            "counter": "",
            "refs": [],
            "agent_session": "seed",
            "status": "pending",
            "kind": "planned",
        },
    )
    v3_tx.commit()
    data = call(env, "get_candidates", {})["data"]
    by_ticker = {row["ticker"]: row for row in data["candidates"]}
    assert by_ticker["AAPL"]["streak"] == 2 and by_ticker["NVDA"]["has_active_plan"] is True
    assert by_ticker["NVDA"]["plan_id"] == plan["id"] and by_ticker["AAPL"]["plan_id"] is None
    assert data["dropped"] == [{"ticker": "INTC", "reasons": ["rank_cut"]}]


def test_get_candidates_has_research(env, v3_tx):
    """R6：有观察中研究的名单行带 has_research（页面与简报据此标「研究中」）。"""
    research_repo.insert_research(
        v3_tx,
        {
            "ticker": "AAPL",
            "thesis": "论点",
            "origin": "chat",
            "created_session": SESSION,
            "valid_until": SESSION,
            "agent_session": "seed",
        },
    )
    v3_tx.commit()
    data = call(env, "get_candidates", {})["data"]
    by_ticker = {row["ticker"]: row for row in data["candidates"]}
    assert by_ticker["AAPL"]["has_research"] is True
    assert by_ticker["MSFT"]["has_research"] is False


def test_get_plans_filters_and_carries_events(env, v3_tx):
    plan_repo.insert_plan(v3_tx, _plan_row("AAPL"))
    open_plan = plan_repo.insert_plan(v3_tx, _plan_row("MSFT", status="open"))
    closed = plan_repo.insert_plan(v3_tx, _plan_row("ORCL", status="closed"))
    plan_repo.insert_event(
        v3_tx,
        {
            "plan_id": closed["id"],
            "session": SESSION,
            "event": "closed",
            "reason": "用户自行卖出",
            "before": {},
            "after": {},
            "source": "user",
            "refs": [],
        },
    )
    v3_tx.commit()

    default = call(env, "get_plans", {})["data"]["plans"]
    assert {row["ticker"] for row in default} == {"AAPL", "MSFT"}  # 缺省：有效计划
    assert all("events" in row for row in default)

    by_status = call(env, "get_plans", {"status": "closed"})["data"]["plans"]
    assert [row["ticker"] for row in by_status] == ["ORCL"]
    assert [row["event"] for row in by_status[0]["events"]] == ["closed"]

    by_ticker = call(env, "get_plans", {"ticker": "MSFT"})["data"]["plans"]
    assert [row["id"] for row in by_ticker] == [open_plan["id"]]

    by_id = call(env, "get_plans", {"plan_id": closed["id"]})["data"]["plans"]
    assert [row["ticker"] for row in by_id] == ["ORCL"]
    # 状态只能是七个计划状态之一
    assert call(env, "get_plans", {"status": "weird"})["error"].startswith("invalid_status")
    assert call(env, "get_plans", {"status": "paused"})["error"].startswith("invalid_status")


def _plan_row(ticker, *, status="pending", **over):
    row = {
        "session": SESSION,
        "ticker": ticker,
        "origin": "brief",
        "entry_type": "zone_limit",
        "entry_low": None,
        "entry_high": None,
        "stop": None,
        "target1": None,
        "levels_source": "host",
        "max_hold_sessions": 20,
        "valid_from": SESSION,
        "valid_until": SESSION,
        "shares": None,
        "sizing": {},
        "warnings": [],
        "thesis": "",
        "invalidation": "",
        "counter": "",
        "refs": [],
        "agent_session": "seed",
        "status": status,
        "kind": "planned",
    }
    row.update(over)
    return row


def test_get_positions_carries_hold_action(v3_tx):
    """终审 I1：确认过止损单的持仓在 get_positions 里带「继续持有」（spec §10：每只持仓含
    下一个交易日的操作及原因），不再是 None。"""
    data_repo.store_bars(v3_tx, "AAPL", _bars(), now=NOW)
    plan_repo.insert_plan(
        v3_tx,
        _plan_row(
            "AAPL",
            status="open",
            entry_low=D(94),
            entry_high=D(98),
            stop=D(96),
            target1=D(104),
            initial_stop=D(96),
            confirmed_stop_order=D(96),  # 挂单 = 止损 → 第 5 条「继续持有」
            start_session=SESSION,
        ),
    )
    facts_repo.insert_trade(
        v3_tx,
        {
            "request_id": "hold-buy",
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 10,
            "price": D(100),
            "executed_at": NOW,
        },
    )
    v3_tx.commit()
    env = make_env(session=SESSION, mode="brief", agent_session="t-hold", cfg=AssistantConfig(), now=NOW)
    rows = call(env, "get_positions", {})["data"]
    assert [row["ticker"] for row in rows] == ["AAPL"]
    assert rows[0]["next_action"]["kind"] == "hold"
    assert rows[0]["next_action"]["text"] == "继续持有"


def test_get_agenda_reads_the_day(env, v3_tx):
    data = call(env, "get_agenda", {})["data"]
    assert set(data) == {
        "holdings",
        "relisted",
        "confirmations",
        "research",
        "summary_done",
        "free_slots",
        "replacement",
        "rotation",
        "complete",
    }
    assert data["free_slots"] == 3 and data["replacement"] is None
    assert [item["ticker"] for item in data["confirmations"]] == TOP5
    assert data["complete"] is False
    assert data["rotation"] == {"compared_today": False, "last_session": None}
    assert all(item["rotation"]["short"] is False for item in data["confirmations"])  # 没有资金记录：从不判钱不够
