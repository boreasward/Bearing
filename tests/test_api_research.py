"""/api/research*：两组卡片、单份研究与变化记录、放弃研究（转发 drop_research）。
字段集合等于接口契约表；校验错误 400，资源缺失 404（unknown_research）。"""

import re
from datetime import UTC, date, datetime, timedelta

import investment_assistant.core.tx as core_tx
from investment_assistant.data import repo as data_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts import research_repo

SESSION = date(2026, 11, 4)  # 周三
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)

CARD_KEYS = {
    "research_id",
    "ticker",
    "status",
    "thesis",
    "created_session",
    "day",
    "valid_until",
    "condition",
    "condition_text",
    "last_close",
    "condition_met_today",
    "triggered_session",
    "verdict",
    "reasons",
    "plan_id",
    "ended_session",
    "end_reason",
    "missing_bar",
    "created_at",
}
EVENT_FIELDS = {"session", "event", "reason", "before", "after", "source", "created_at"}
ISO_STAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^+Z]*(?:Z|[+-]\d{2}:\d{2})"  # ISO 8601 T 形式且带时区


def _bars(n=20, *, close=100.0):
    days = [SESSION - timedelta(days=i) for i in range(n)][::-1]
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


def _run(conn):
    facts_repo.insert_run(
        conn,
        {
            "run_id": "run-1",
            "session": SESSION,
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


def _research(ticker, **over):
    row = {
        "ticker": ticker,
        "thesis": "回调到 99 附近再买",
        "origin": "chat",
        "created_session": SESSION,
        "valid_until": date(2026, 11, 20),
        "agent_session": "seed",
    }
    row.update(over)
    return row


def seed(conn):
    """一次运行、两只有日线的股票；三份观察中（一份没有日线）、一份已放弃的研究。"""
    _run(conn)
    data_repo.store_bars(conn, "AAPL", _bars(), now=NOW)  # 收盘 100
    data_repo.store_bars(conn, "MSFT", _bars(), now=NOW)
    rows = {
        "AAPL": research_repo.insert_research(
            conn, _research("AAPL", condition={"kind": "close_above", "level": "105"}, condition_text="收盘站上 105.00")
        ),
        "MSFT": research_repo.insert_research(
            conn, _research("MSFT", condition={"kind": "close_above", "level": "99"}, condition_text="收盘站上 99.00")
        ),
        "SNDK": research_repo.insert_research(  # 没有任何日线
            conn, _research("SNDK", condition={"kind": "close_above", "level": "105"}, condition_text="收盘站上 105.00")
        ),
        "MU": research_repo.insert_research(
            conn,
            _research(
                "MU",
                status="dropped",
                created_session=date(2026, 11, 2),
                ended_session=date(2026, 11, 3),
                end_reason="用户放弃",
            ),
        ),
    }
    research_repo.insert_research_event(  # 变化记录时间线用（同 start_research 的 created 事件形状）
        conn,
        {
            "research_id": rows["AAPL"]["id"],
            "session": SESSION,
            "event": "created",
            "reason": "回调到 99 附近再买",
            "before": {},
            "after": {"status": "watching"},
            "source": "user",
            "refs": ["seed-1"],
        },
    )
    return rows


def test_list_watching_card_fields_and_condition_met_today(client, v3_tx):
    with core_tx._tx() as conn:
        seed(conn)
    resp = client.get("/api/research?group=watching")
    assert resp.status_code == 200
    by_ticker = {card["ticker"]: card for card in resp.json()["research"]}
    assert set(by_ticker) == {"AAPL", "MSFT", "SNDK"}  # 已结束的不在观察中组
    card = by_ticker["AAPL"]
    assert set(card) == CARD_KEYS
    assert card["status"] == "watching" and card["thesis"] == "回调到 99 附近再买"
    assert card["created_session"] == SESSION.isoformat()
    assert card["day"] == 1  # 两端都算：发起当天是第 1 天
    assert card["valid_until"] == "2026-11-20"
    assert card["condition"] == {"kind": "close_above", "level": "105.00", "ma": None}  # 价位是两位小数字符串
    assert card["condition_text"] == "收盘站上 105.00"
    assert card["last_close"] == "100.00" and card["condition_met_today"] is False  # 收盘 100 < 105
    assert card["missing_bar"] is False  # 日线齐
    assert by_ticker["MSFT"]["condition_met_today"] is True  # 收盘 100 ≥ 99
    sndk = by_ticker["SNDK"]
    assert sndk["last_close"] is None and sndk["condition_met_today"] is None
    assert sndk["missing_bar"] is True  # 缺日线：未复查（spec §6.4）
    assert card["triggered_session"] is None and card["verdict"] is None and card["reasons"] == ""
    assert card["plan_id"] is None and card["ended_session"] is None and card["end_reason"] is None
    assert re.fullmatch(ISO_STAMP, card["created_at"])  # ISO 8601 的 T 形式且带时区

    ended = {card["ticker"]: card for card in client.get("/api/research?group=ended").json()["research"]}
    assert set(ended) == {"MU"}  # ended = 四个终态
    assert ended["MU"]["status"] == "dropped" and ended["MU"]["end_reason"] == "用户放弃"
    assert ended["MU"]["day"] == 2  # 已结束的研究冻结在结束日（11-02 发起、11-03 结束）
    assert ended["MU"]["missing_bar"] is False  # 非观察中一律 False


def test_detail_with_events_and_unknown_404(client, v3_tx):
    with core_tx._tx() as conn:
        rows = seed(conn)
    resp = client.get(f"/api/research/{rows['AAPL']['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == CARD_KEYS | {"events"}
    assert [event["event"] for event in body["events"]] == ["created"]
    assert set(body["events"][0]) == EVENT_FIELDS
    assert body["events"][0]["source"] == "user" and body["events"][0]["session"] == SESSION.isoformat()
    assert re.fullmatch(ISO_STAMP, body["events"][0]["created_at"])
    unknown = client.get("/api/research/999999")
    assert unknown.status_code == 404 and unknown.json()["error"] == "unknown_research"


def test_drop_previews_then_writes(client, v3_tx):
    with core_tx._tx() as conn:
        rows = seed(conn)
    research_id = rows["AAPL"]["id"]
    preview = client.post(
        f"/api/research/{research_id}/drop", json={"request_id": "d-1", "confirmed": False, "reason": "不看了"}
    )
    assert preview.status_code == 200
    assert preview.json() == {"write": False, "preview": "将放弃研究 AAPL（不看了）。确认后写入。"}
    written = client.post(
        f"/api/research/{research_id}/drop", json={"request_id": "d-1", "confirmed": True, "reason": "不看了"}
    )
    assert written.status_code == 200
    assert written.json()["research"]["status"] == "dropped"
    assert written.json()["research"]["end_reason"] == "不看了"
    assert written.json()["event"]["event"] == "dropped" and written.json()["event"]["source"] == "user"
    assert {card["ticker"] for card in client.get("/api/research?group=ended").json()["research"]} == {"AAPL", "MU"}
    assert {card["ticker"] for card in client.get("/api/research?group=watching").json()["research"]} == {
        "MSFT",
        "SNDK",
    }
    again = client.post(f"/api/research/{research_id}/drop", json={"request_id": "d-2", "confirmed": True})
    assert again.status_code == 400 and again.json()["error"] == "not_watching"  # 只对观察中的研究
    unknown = client.post("/api/research/999999/drop", json={"request_id": "d-3", "confirmed": True})
    assert unknown.status_code == 404 and unknown.json()["error"] == "unknown_research"


def test_bad_group_and_limit_400(client, v3_tx):
    assert client.get("/api/research?group=bogus").status_code == 400
    assert client.get("/api/research?limit=0").status_code == 400
    assert client.get("/api/research?limit=101").status_code == 400
    with core_tx._tx() as conn:  # 有运行记录才轮得到表单校验（web_env 先于 forward，同计划接口）
        _run(conn)
    assert client.post("/api/research/1/drop", json={"request_id": "x"}).status_code == 400  # 缺 confirmed


def test_research_needs_a_run_record(client, v3_tx):
    resp = client.get("/api/research")
    assert resp.status_code == 404 and resp.json()["error"] == "no_run"
