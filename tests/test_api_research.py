"""/api/research*：两组卡片、单份研究与变化记录、放弃研究（转发 drop_research）。
字段集合等于接口契约表；校验错误 400，资源缺失 404（unknown_research）。"""

import re
from datetime import UTC, date, datetime, timedelta

import investment_assistant.core.tx as core_tx
from investment_assistant.data import repo as data_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts import research_repo

SESSION = date(2026, 11, 4)  # 周三
D2 = date(2026, 11, 3)  # 周二：stale-only 日线与已触发研究用
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


def _bars(n=20, *, close=100.0, end=SESSION):  # end：日线停在哪一天（缺省含当日）
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
    missing = client.post("/api/research/1/drop", json={"request_id": "x"})  # 缺 confirmed
    assert missing.status_code == 400
    assert missing.json() == {"error": "missing_argument: confirmed"}  # 契约：单串，不拆 detail
    extra = client.post(  # 未知键：forward 先拒，不到宿主函数
        "/api/research/1/drop", json={"request_id": "x", "confirmed": True, "bogus": 1}
    )
    assert extra.status_code == 400 and extra.json() == {"error": "unknown_argument: bogus"}


def test_card_display_state_boundaries(client, v3_tx):
    """Task 5 从卡片字段推导显示状态——四种边界：尚无条件、已触发但缺当日日线、
    均线历史不足、只有历史日线（stale-only，要能和完全没有日线区分开）。"""
    with core_tx._tx() as conn:
        _run(conn)
        data_repo.store_bars(conn, "IBM", _bars(), now=NOW)  # 日线齐（含当日）
        data_repo.store_bars(conn, "ORCL", _bars(close=101.0, end=D2), now=NOW)  # 只到 11-03
        data_repo.store_bars(conn, "AMD", _bars(), now=NOW)  # 20 根 < ma 50
        data_repo.store_bars(conn, "INTC", _bars(close=101.0, end=D2), now=NOW)  # 只到 11-03
        research_repo.insert_research(conn, _research("IBM"))  # (a) 尚无条件
        research_repo.insert_research(  # (b) 已触发（11-03 条件已到），当日缺日线
            conn,
            _research(
                "ORCL",
                condition={"kind": "close_above", "level": "100"},
                condition_text="收盘站上 100.00",
                triggered_session=D2,
            ),
        )
        research_repo.insert_research(  # (c) 均线历史不足：20 根日线 < 50 日均线
            conn,
            _research("AMD", condition={"kind": "close_above_ma", "ma": 50}, condition_text="收盘站上 50 日均线"),
        )
        research_repo.insert_research(  # (d) stale-only：有历史日线、没有当日的
            conn,
            _research("INTC", condition={"kind": "close_above", "level": "100"}, condition_text="收盘站上 100.00"),
        )
    cards = {card["ticker"]: card for card in client.get("/api/research?group=watching").json()["research"]}
    # (a) 尚无条件：不判条件、也不算缺日线（宿主不逐日复查它，spec §6.4）
    assert cards["IBM"]["condition"] is None and cards["IBM"]["condition_met_today"] is None
    assert cards["IBM"]["missing_bar"] is False and cards["IBM"]["last_close"] == "100.00"
    # (b) 已触发：显示「条件已到，待复看」——即便当日缺日线也不算 missing_bar
    assert cards["ORCL"]["triggered_session"] == D2.isoformat() and cards["ORCL"]["missing_bar"] is False
    assert cards["ORCL"]["condition_met_today"] is True  # 按最后一根已知日线（11-03 收盘 101）判
    # (c) 均线历史不足：日线齐但根数不够 → 条件判不出（None）、不算缺日线
    assert cards["AMD"]["condition"] == {"kind": "close_above_ma", "level": None, "ma": 50}
    assert cards["AMD"]["condition_met_today"] is None and cards["AMD"]["missing_bar"] is False
    # (d) stale-only：missing_bar 真、last_close 是最后一根历史收盘（非 None，与 SNDK 没有任何日线区分）。
    # 历史收盘 101 ≥ 100，若按最后一根已知日线判会是 True——但当日缺日线时不判（None），
    # 否则未触发的研究会凭昨天的收盘显示「条件已到」（审查 P1-1，与页面缺日线优先级同一口径）
    assert cards["INTC"]["missing_bar"] is True and cards["INTC"]["last_close"] == "101.00"
    assert cards["INTC"]["condition_met_today"] is None


def test_rejected_card_carries_the_new_conclusion(client, v3_tx):
    """F2：reject 后研究行带着新结论——已结束组的卡片显示 verdict=reject 与否决理由，
    不再是旧的「值得盯」或空白；变化记录里 rejected 事件原样。"""
    with core_tx._tx() as conn:
        seed(conn)
        rejected = research_repo.insert_research(
            conn,
            _research(
                "NVDA",
                status="rejected",
                verdict="reject",
                reasons="行业转弱",
                counter="转弱早于站稳",
                refs=["call:1", "call:2"],
                agent_session="brief",
                ended_session=SESSION,
                end_reason="行业转弱",
            ),
        )
        research_repo.insert_research_event(
            conn,
            {
                "research_id": rejected["id"],
                "session": SESSION,
                "event": "rejected",
                "reason": "否决：行业转弱",
                "before": {"status": "watching", "verdict": "watch"},
                "after": {"status": "rejected"},
                "source": "agent",
                "refs": ["call:1", "call:2"],
            },
        )
    ended = {card["ticker"]: card for card in client.get("/api/research?group=ended").json()["research"]}
    card = ended["NVDA"]
    assert card["status"] == "rejected" and card["verdict"] == "reject"  # 新结论在行上，不是旧的「值得盯」
    assert card["reasons"] == "行业转弱" and card["end_reason"] == "行业转弱"
    detail = client.get(f"/api/research/{card['research_id']}").json()
    assert detail["verdict"] == "reject" and detail["reasons"] == "行业转弱"
    assert [event["event"] for event in detail["events"]] == ["rejected"]  # 事件形状不变（F2 裁定）


def test_research_needs_a_run_record(client, v3_tx):
    resp = client.get("/api/research")
    assert resp.status_code == 404 and resp.json()["error"] == "no_run"
