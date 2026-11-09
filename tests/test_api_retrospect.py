"""GET /api/retrospect：复盘块的读接口——记录、周报与规则检验一次返回；week 切换周报所在周。
校验错误 400（week / limit）；真正的空库（没有运行记录）是合法的空视图（F5/B5/RF6）。"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import investment_assistant.core.tx as core_tx
from investment_assistant.data import repo as data_repo
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo

SESSION = date(2026, 11, 4)  # 周三；本周 = 11/2–11/4
D0 = date(2026, 10, 30)  # 周五：计划保存日
D1 = date(2026, 11, 2)
D2 = date(2026, 11, 3)
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)


def _run(conn, run_id, session):
    facts_repo.insert_run(
        conn,
        {
            "run_id": run_id,
            "session": session,
            "trigger": "scheduled",
            "status": "ok",
            "error": None,
            "market": {},
            "factors": {},
            "coverage": {},
            "holdings": [],
            "dropped": [],
            "draft_md": "",
            "started_at": NOW - timedelta(minutes=5),
            "finished_at": NOW,
        },
    )


def _candidate(ticker):
    return {
        "ticker": ticker,
        "rank": 1,
        "screen_group": "trigger",
        "trigger_rules": ["pullback"],
        "rs": 1.5,
        "atr_pct": 2.0,
        "setup_tier": "favorable",
        "industry": "半导体",
        "industry_tier": "favorable",
        "reason": f"{ticker} 的理由行",
    }


def _bar(day, close, *, open=None, low=None):
    return {
        "price_date": day,
        "open": close if open is None else open,
        "high": close + 1,
        "low": close - 1 if low is None else low,
        "close": close,
        "adj_close": close,
        "volume": 1000,
        "split_factor": 1.0,
    }


def _plan_row(ticker, **over):
    row = {
        "session": D0,
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
        "valid_from": D1,
        "valid_until": date(2026, 11, 9),
        "shares": None,
        "sizing": {},
        "warnings": [],
        "thesis": "论点",
        "invalidation": "",
        "counter": "",
        "refs": [],
        "agent_session": "seed",
        "status": "expired",
        "kind": "planned",
        "ended_session": D2,
        "end_reason": "5 个交易日未买入",
    }
    row.update(over)
    return row


def seed(conn):
    """两个运行（保存日 + 当前交易日）+ 一份 expired 计划。"""
    _run(conn, "run-0", D0)  # 保存日的运行（候选行挂它）：先插，别抢了 latest_ok_run
    _run(conn, "run-1", SESSION)
    _seed_one_expired(conn)


def _seed_one_expired(conn):
    """一份 expired 计划（运行记录由调用方先入库）：有效期首日进过入场区间、次日收盘跌破
    止损——假设结果离场（R = −1），规则检验的假设表出现一个样本不足的组。"""
    facts_repo.insert_candidates(conn, "run-0", [_candidate("AAPL")])
    data_repo.store_bars(
        conn,
        "AAPL",
        [
            _bar(D0, 102.0),  # 有效期之前
            _bar(D1, 100.0, low=99.0),  # 低点 99 进过 99–101 区间：假设成交于开盘 100
            _bar(D2, 95.0, open=96.0),  # 收盘跌破止损 96：假设离场于 96，R = −1
            _bar(SESSION, 100.0),
        ],
        now=NOW,
    )
    plan_repo.insert_plan(conn, _plan_row("AAPL"))


def test_retrospect_empty_and_with_records(client, v3_tx):
    empty_db = client.get("/api/retrospect")  # 真正的空库（无运行记录）：合法的空视图（F5/B5/RF6）
    assert empty_db.status_code == 200
    empty_body = empty_db.json()
    assert empty_body["empty"] is True and empty_body["records"] == []
    assert date.fromisoformat(empty_body["weekly"]["week_start"]).weekday() == 0  # 周边界在（ISO 周一）
    assert empty_body["weekly"]["week_start"] <= empty_body["weekly"]["week_end"]
    assert empty_body["rules"]["realized"] == {"trigger_rules": [], "setup_tier": [], "industry": []}

    with core_tx._tx() as conn:
        _run(conn, "run-0", D0)
        _run(conn, "run-1", SESSION)
    empty = client.get("/api/retrospect")
    assert empty.status_code == 200
    body = empty.json()
    assert body["empty"] is True and body["records"] == []
    assert body["weekly"]["week_start"] == D1.isoformat() and body["weekly"]["week_end"] == SESSION.isoformat()
    assert body["rules"]["realized"] == {"trigger_rules": [], "setup_tier": [], "industry": []}

    with core_tx._tx() as conn:
        _seed_one_expired(conn)
    filled = client.get("/api/retrospect").json()
    assert filled["empty"] is False and len(filled["records"]) == 1
    record = filled["records"][0]
    assert record["ticker"] == "AAPL" and record["status"] == "expired"
    assert record["hypothetical"]["status"] == "exited"
    # 样本 1 < min_sample 10：假设表该组 insufficient、三项为 None（spec §5.5）
    assert filled["rules"]["hypothetical"]["trigger_rules"] == [
        {"group": "pullback", "sample": 1, "win_rate": None, "avg_r": None, "total_r": None, "insufficient": True}
    ]
    assert filled["weekly"]["ended"] == 1  # 结束日 11/3 落在本周


def test_week_parameter_switches_weekly(client, v3_tx):
    with core_tx._tx() as conn:
        seed(conn)
    default = client.get("/api/retrospect").json()["weekly"]
    assert default["week_start"] == D1.isoformat() and default["week_end"] == SESSION.isoformat()
    assert default["ended"] == 1
    prior = client.get("/api/retrospect?week=2026-10-30").json()["weekly"]  # 上一个周五：切到 10/26–10/30
    assert prior["week_start"] == "2026-10-26" and prior["week_end"] == "2026-10-30"
    assert prior["ended"] == 0  # 那一周没有已结束的计划


def test_holiday_monday_week_normalizes_to_its_calendar_week(client, v3_tx):
    """F8：week 落在休市的周一（2026-02-16 总统日）不再 500——归一到该日历周的最后一个
    交易日（周五 02-20），周界 02-16–02-20；周内任一天都归同一周。"""
    with core_tx._tx() as conn:
        seed(conn)
    weekly = client.get("/api/retrospect?week=2026-02-16").json()["weekly"]
    assert weekly["week_start"] == "2026-02-16" and weekly["week_end"] == "2026-02-20"
    midweek = client.get("/api/retrospect?week=2026-02-17").json()["weekly"]
    assert midweek["week_start"] == "2026-02-16" and midweek["week_end"] == "2026-02-20"


def test_bad_week_400(client, v3_tx):
    assert client.get("/api/retrospect?week=not-a-date").status_code == 400
    assert client.get("/api/retrospect?week=2026-13-99").status_code == 400
    assert client.get("/api/retrospect?week=20261104").status_code == 400  # 契约只认 YYYY-MM-DD，基本格式拦掉
    assert client.get("/api/retrospect?limit=0").status_code == 400
    assert client.get("/api/retrospect?limit=101").status_code == 400
