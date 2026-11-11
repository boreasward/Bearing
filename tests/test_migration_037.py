"""Migration 037 adds the v3.3 rotation table, plan rotation columns and new plan events; disposable schemas."""

from pathlib import Path

import pytest
from psycopg import errors

MIGRATION_037 = (Path(__file__).resolve().parents[1] / "migrations" / "037_v3_3_rotation.sql").read_text(
    encoding="utf-8"
)

SNAPSHOT = "{}"


def _insert_plan(conn, ticker="AAA", **extra):
    """Raw insert of a v3_plans row; extra columns (rotation columns) go in by name."""
    columns = [
        "session", "ticker", "origin", "entry_type", "levels_source", "max_hold_sessions",
        "valid_from", "valid_until", "thesis", "invalidation", "agent_session",
    ]  # fmt: skip
    values = [
        "2026-11-09", ticker, "brief", "pullback", "host", 20, "2026-11-09", "2026-11-16", "论点", "失效", "as-1",
    ]  # fmt: skip
    for name, value in extra.items():
        columns.append(name)
        values.append(value)
    placeholders = ", ".join(["%s"] * len(columns))
    sql = f"INSERT INTO v3_plans ({', '.join(columns)}) VALUES ({placeholders}) RETURNING id"
    # pi-lens-ignore: python-sql-injection -- column names are literals in this test, values are %s parameters
    return conn.execute(sql, values).fetchone()[0]


def _insert_rotation(
    conn,
    *,
    session="2026-11-09",
    origin="brief",
    verdict="hold",
    holding_plan_id=None,
    holding_ticker=None,
    weakness="",
    cost_response="",
    sell_quantity=None,
    buy_shares=None,
):
    """Raw insert shaped like the v3_rotations row; the repo is not used here on purpose."""
    return conn.execute(
        "INSERT INTO v3_rotations (session, origin, candidate, verdict, holding_plan_id, holding_ticker, "
        "reasons, weakness, cost_response, snapshot, sell_quantity, buy_shares, agent_session) "
        "VALUES (%s, %s, 'BBB', %s, %s, %s, '理由', %s, %s, %s::jsonb, %s, %s, 'as-1') RETURNING id",
        (
            session, origin, verdict, holding_plan_id, holding_ticker, weakness, cost_response,
            SNAPSHOT, sell_quantity, buy_shares,
        ),
    ).fetchone()[0]  # fmt: skip


def test_rerun_is_idempotent(v3_db):
    conn = v3_db
    rotation_id = _insert_rotation(conn)
    plan_id = _insert_plan(conn, sell_instruction="rotation", rotation_id=rotation_id, sell_quantity=150)
    conn.commit()
    conn.execute(MIGRATION_037)  # pi-lens-ignore: python-sql-injection -- trusted repo migration file, rerun on purpose
    conn.commit()
    assert conn.execute("SELECT id, verdict FROM v3_rotations").fetchall() == [(rotation_id, "hold")]
    assert conn.execute("SELECT sell_instruction, rotation_id FROM v3_plans WHERE id = %s", (plan_id,)).fetchone() == (
        "rotation",
        rotation_id,
    )  # 已有数据不被改写
    indexes = conn.execute(
        "SELECT count(*) FROM pg_indexes WHERE schemaname = current_schema() "
        "AND indexname IN ('uq_v3_rotations_brief_once', 'idx_v3_rotations_session')"
    ).fetchone()[0]
    assert indexes == 2  # 索引重跑后仍是各一份
    constraints = conn.execute(
        "SELECT count(*) FROM pg_constraint WHERE conname IN ("
        "'v3_plans_sell_instruction_check', 'v3_plans_rotation_check', 'v3_plan_events_event_check')"
        " AND connamespace = current_schema()::regnamespace"
    ).fetchone()[0]
    assert constraints == 3  # 约束重跑后仍是各一份


def test_one_brief_rotation_per_session(v3_db):
    conn = v3_db
    _insert_rotation(conn, origin="brief")
    conn.commit()
    with pytest.raises(errors.UniqueViolation):
        _insert_rotation(conn, origin="brief")  # 同交易日第二条简报换仓记录
    conn.rollback()
    _insert_rotation(conn, origin="chat")
    _insert_rotation(conn, origin="chat")  # 聊天来源可以多条
    _insert_rotation(conn, origin="brief", session="2026-11-10")  # 另一个交易日仍可有一条
    conn.commit()
    assert conn.execute("SELECT count(*) FROM v3_rotations").fetchone()[0] == 4


def test_half_and_full_need_the_holding_fields(v3_db):
    conn = v3_db
    plan_id = _insert_plan(conn)
    conn.commit()
    full_fields = dict(
        holding_plan_id=plan_id, holding_ticker="AAA", weakness="弱点", cost_response="成本回应",
        sell_quantity=150, buy_shares=40,
    )  # fmt: skip
    for broken in (
        {"holding_plan_id": None},
        {"holding_ticker": None},
        {"weakness": ""},
        {"cost_response": ""},
        {"sell_quantity": 0},
        {"buy_shares": 0},
    ):
        with pytest.raises(errors.CheckViolation):
            _insert_rotation(conn, verdict="half", **{**full_fields, **broken})
        conn.rollback()
    _insert_rotation(conn, verdict="half", origin="chat", **full_fields)
    _insert_rotation(conn, verdict="full", origin="chat", **full_fields)
    _insert_rotation(conn, verdict="hold", origin="chat")  # hold 不带任何持仓字段
    conn.commit()
    assert conn.execute("SELECT count(*) FROM v3_rotations").fetchone()[0] == 3


def test_rotation_instruction_columns_move_together(v3_db):
    conn = v3_db
    rotation_id = _insert_rotation(conn)
    conn.commit()
    with pytest.raises(errors.CheckViolation):  # rotation 指令没有 rotation_id
        _insert_plan(conn, "AAA", sell_instruction="rotation", sell_quantity=150)
    conn.rollback()
    with pytest.raises(errors.CheckViolation):  # rotation_id 有而 sell_quantity 空
        _insert_plan(conn, "AAA", sell_instruction="rotation", rotation_id=rotation_id)
    conn.rollback()
    with pytest.raises(errors.CheckViolation):  # rotation_id 有而指令不是 rotation
        _insert_plan(conn, "AAA", sell_instruction="max_hold", rotation_id=rotation_id, sell_quantity=150)
    conn.rollback()
    with pytest.raises(errors.CheckViolation):  # sell_quantity 有而 rotation_id 空
        _insert_plan(conn, "AAA", sell_instruction="max_hold", sell_quantity=150)
    conn.rollback()
    _insert_plan(conn, "AAA", sell_instruction="max_hold")  # 三列全空可以
    _insert_plan(conn, "BBB", sell_instruction="rotation", rotation_id=rotation_id, sell_quantity=150)
    _insert_plan(conn, "CCC")  # 没有任何指令也可以
    conn.commit()
    stale = conn.execute("SELECT stop_quantity_stale FROM v3_plans WHERE ticker = 'CCC'").fetchone()[0]
    assert stale is False  # stop_quantity_stale 缺省 False


def test_new_plan_events_are_accepted(v3_db):
    conn = v3_db
    plan_id = _insert_plan(conn)
    conn.commit()
    for event in ("rotation_instruction", "rotation_withdrawn", "rotation_superseded", "stop_quantity_stale"):
        conn.execute(
            "INSERT INTO v3_plan_events (plan_id, session, event, reason, source) "
            "VALUES (%s, '2026-11-09', %s, '换仓', 'host')",
            (plan_id, event),
        )
    conn.execute(
        "INSERT INTO v3_plan_events (plan_id, session, event, reason, source) "
        "VALUES (%s, '2026-11-09', 'created', '旧事件仍可用', 'host')",
        (plan_id,),
    )
    conn.commit()
    assert conn.execute("SELECT count(*) FROM v3_plan_events").fetchone()[0] == 5
    with pytest.raises(errors.CheckViolation):
        conn.execute(
            "INSERT INTO v3_plan_events (plan_id, session, event, reason, source) "
            "VALUES (%s, '2026-11-09', 'nonsense', 'x', 'agent')",
            (plan_id,),
        )
    conn.rollback()
