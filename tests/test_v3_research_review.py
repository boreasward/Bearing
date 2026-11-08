"""Host research review over the v3_tx database (spec §6.4): triggered / expired host events,
missing bars neither judged nor advanced, catch-up once the bar arrives, per-research isolation."""

from datetime import UTC, date, datetime

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.facts import research_repo
from investment_assistant.facts.research_review import review_research

SESSION = date(2026, 11, 4)  # 周三
D2 = date(2026, 11, 3)
D1 = date(2026, 11, 2)
D0 = date(2026, 10, 30)  # 周五：研究发起日
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)
CFG = AssistantConfig()


def bars(n=20, *, end=SESSION, close=100.0):
    """n 根连续交易日日线（最后一根是 end），恒定波幅 2，末根收盘 close。"""
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


def review(conn, session=SESSION):
    return review_research(conn, session=session, cutoff_at=NOW, cfg=CFG)


def events_of(conn, research_id, event):
    return [row for row in research_repo.research_events(conn, research_id) if row["event"] == event]


def saved_research(conn, research_id):
    row = research_repo.research(conn, research_id)
    assert row is not None
    return row


def insert(conn, ticker, **over):
    row = {
        "ticker": ticker,
        "thesis": "论点",
        "origin": "chat",
        "created_session": D0,
        "valid_until": add_trading_days(D0, 20),
        "condition": {"kind": "close_above", "level": 105},
        "agent_session": "research-review-test",
    }
    row.update(over)
    return research_repo.insert_research(conn, row)


def test_triggered_once_and_rerun_idempotent(v3_tx):
    """条件到的那天记 triggered、停在待复看（不是终态）；同日重跑不重复记。"""
    data_repo.store_bars(v3_tx, "AAA", bars(19, end=D2) + bars(1, end=SESSION, close=110.0), now=NOW)
    saved = insert(v3_tx, "AAA")
    v3_tx.commit()

    result = review(v3_tx)

    assert [row["event"] for row in result["changes"]] == ["triggered"]
    assert result["unchecked"] == [] and result["errors"] == []
    row = saved_research(v3_tx, saved["id"])
    assert row["status"] == "watching"  # 触发不是终态：等 agent 复看
    assert row["triggered_session"] == SESSION and row["checked_through"] == SESSION
    event = events_of(v3_tx, saved["id"], "triggered")[0]
    assert event["source"] == "host" and "已到" in event["reason"]

    again = review(v3_tx)
    assert again["changes"] == [] and again["unchecked"] == []
    assert len(events_of(v3_tx, saved["id"], "triggered")) == 1
    assert saved_research(v3_tx, saved["id"])["triggered_session"] == SESSION


def test_missing_bar_does_not_advance_and_catches_up(v3_tx):
    """Review Focus 2：当天缺日线不判、不推进、进 unchecked；日线补上后再跑判出 triggered。"""
    data_repo.store_bars(v3_tx, "AAA", bars(20, end=D2), now=NOW)  # 日线停在 D2
    saved = insert(v3_tx, "AAA")
    v3_tx.commit()

    result = review(v3_tx)

    assert result["unchecked"] == ["AAA"] and result["changes"] == []
    row = saved_research(v3_tx, saved["id"])
    assert row["checked_through"] == D2  # D1、D2 照常推进，SESSION 缺日线不动
    assert row["triggered_session"] is None

    data_repo.store_bars(v3_tx, "AAA", bars(1, end=SESSION, close=110.0), now=NOW)
    v3_tx.commit()
    caught = review(v3_tx)
    assert [row["event"] for row in caught["changes"]] == ["triggered"]
    assert caught["unchecked"] == []
    assert saved_research(v3_tx, saved["id"])["triggered_session"] == SESSION


def test_expires_when_valid_until_passes_without_trigger(v3_tx):
    """有效期内条件一直没到：valid_until 当天 expired（终态即停），重跑不再动它。"""
    data_repo.store_bars(v3_tx, "AAA", bars(20), now=NOW)
    saved = insert(v3_tx, "AAA", valid_until=D1, condition={"kind": "close_above", "level": 999})
    v3_tx.commit()

    result = review(v3_tx)

    assert [row["event"] for row in result["changes"]] == ["expired"]
    row = saved_research(v3_tx, saved["id"])
    assert row["status"] == "expired" and row["ended_session"] == D1 and row["checked_through"] == D1
    assert "条件未到" in row["end_reason"]
    assert events_of(v3_tx, saved["id"], "expired")[0]["source"] == "host"
    assert review(v3_tx)["changes"] == []  # 终态研究不再进复查


def test_no_condition_is_skipped(v3_tx):
    """尚无条件的研究宿主不检查（spec §6.3）：不推进、不产生事件、不报错。"""
    data_repo.store_bars(v3_tx, "AAA", bars(20), now=NOW)
    saved = insert(v3_tx, "AAA", condition=None)
    v3_tx.commit()

    result = review(v3_tx)

    assert result == {"changes": [], "unchecked": [], "errors": []}
    row = saved_research(v3_tx, saved["id"])
    assert row["checked_through"] is None and row["status"] == "watching"
    assert research_repo.research_events(v3_tx, saved["id"]) == []


def test_one_bad_research_does_not_stop_the_rest(v3_tx):
    """条件非法的研究只回滚它自己（一份一个 SAVEPOINT），其余研究照常复查。"""
    data_repo.store_bars(v3_tx, "AAA", bars(20), now=NOW)
    data_repo.store_bars(v3_tx, "BBB", bars(19, end=D2) + bars(1, end=SESSION, close=110.0), now=NOW)
    bad = insert(v3_tx, "AAA", condition={"kind": "wat", "level": 10})
    good = insert(v3_tx, "BBB")
    v3_tx.commit()

    result = review(v3_tx)

    assert [row["event"] for row in result["changes"]] == ["triggered"]  # BBB 照常触发
    assert len(result["errors"]) == 1
    assert result["errors"][0]["research_id"] == bad["id"] and result["errors"][0]["ticker"] == "AAA"
    assert "invalid_condition" in result["errors"][0]["error"]
    assert saved_research(v3_tx, bad["id"])["checked_through"] is None  # 回滚：没推进
    assert saved_research(v3_tx, good["id"])["triggered_session"] == SESSION


def test_midweek_gap_stops_cursor_and_catches_up(v3_tx):
    """P1-1：窗口中间缺日线 → 游标停在缺口，不跨过；补上缺口后从缺口日重查仍能判出 triggered。"""
    gap_bars = [bar for bar in bars(19, end=D2) if bar["price_date"] != D1]  # D1 缺线，D2（=session）有线
    data_repo.store_bars(v3_tx, "AAA", gap_bars, now=NOW)
    saved = insert(v3_tx, "AAA")
    v3_tx.commit()

    first = review(v3_tx, session=D2)
    # D1 缺线：不判、不推进、不跨过 D1 去推 D2；当天（D2）有线所以不进 unchecked
    assert first["changes"] == [] and first["unchecked"] == []
    row = saved_research(v3_tx, saved["id"])
    assert row["checked_through"] is None and row["triggered_session"] is None

    data_repo.store_bars(v3_tx, "AAA", bars(1, end=D1, close=110.0), now=NOW)  # 补 D1：条件在 D1 就到
    v3_tx.commit()
    caught = review(v3_tx, session=D2)
    assert [event["event"] for event in caught["changes"]] == ["triggered"]
    row = saved_research(v3_tx, saved["id"])
    assert row["triggered_session"] == D1  # 触发日之后、待复看期间游标照常推进到 D2
    assert row["checked_through"] == D2


def test_unconditioned_research_expires_at_valid_until(v3_tx):
    """P1-2：尚无条件的研究到 valid_until 自然过期（spec §6.3「一直没有结论」）；不看日线；重跑幂等。"""
    saved = insert(v3_tx, "AAA", condition=None, valid_until=D1)  # 该股票完全没有日线
    v3_tx.commit()

    result = review(v3_tx)

    assert [event["event"] for event in result["changes"]] == ["expired"]
    row = saved_research(v3_tx, saved["id"])
    assert row["status"] == "expired" and row["ended_session"] == D1
    assert "条件未到" in row["end_reason"] and row["checked_through"] == D1
    event = events_of(v3_tx, saved["id"], "expired")[0]
    assert event["source"] == "host" and event["session"] == D1

    again = review(v3_tx)
    assert again["changes"] == [] and len(events_of(v3_tx, saved["id"], "expired")) == 1
