"""对话轮次（库测试）：两轮消息 seq 1–4 且 Pi 会话编号不变；MCP 配置的 --session 取最新 ok 运行
（spec §9.1）；read_only 拒绝；Busy 透传；cancel 后消息行记 cancelled；无运行记录报 no_run。"""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from investment_assistant.runtime import chat, lock
from investment_assistant.runtime.base import RuntimeEvent, RuntimeSpec
from investment_assistant.runtime.fake import FakeRuntime

from investment_assistant.config import AssistantConfig, V3Config
from investment_assistant.toolsurface.env import make_env

SESSION = date(2026, 11, 4)
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)
SPEC = RuntimeSpec(runtime="fake", provider="prov-x", model="model-x")


def cfg_at(tmp_path: Path) -> AssistantConfig:
    return AssistantConfig(v3=V3Config(briefs_dir=str(tmp_path / "rb" / "briefs")))


def seed_ok_run(conn, session=SESSION, run_id="run-1"):
    from investment_assistant.facts import repo as facts_repo

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
            "started_at": NOW,
            "finished_at": NOW,
        },
    )


def start_chat(conn, *, spec=SPEC):
    return chat.start_chat(conn, spec=spec)["chat_id"]


def test_two_turns_stream_and_persist_messages(v3_tx, tmp_path):
    import investment_assistant.core.tx as core_tx
    from investment_assistant.facts import plan_repo

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        seed_ok_run(conn)
        chat_id = start_chat(conn)
    seen = []
    fake = FakeRuntime(lambda prompt: [RuntimeEvent("text", text="回答：好"), RuntimeEvent("done")])
    for text in ("今天适合交易什么？", "第二问"):
        list(chat.turn(chat_id=chat_id, text=text, cfg=cfg, runtime=fake, now=NOW))
        seen.append(fake.calls[-1])
    with core_tx.read_only_tx() as conn:
        rows = plan_repo.messages(conn, chat_id)
    assert [(row["seq"], row["role"], row["status"]) for row in rows] == [
        (1, "user", "ok"),
        (2, "assistant", "ok"),
        (3, "user", "ok"),
        (4, "assistant", "ok"),
    ]
    assert [row["content"] for row in rows if row["role"] == "assistant"] == ["回答：好", "回答：好"]
    assert [row["agent_session"] for row in rows if row["role"] == "assistant"] == [
        f"chat-{chat_id}-1",
        f"chat-{chat_id}-3",
    ]
    assert seen[0]["session_id"] == seen[1]["session_id"]  # 同一对话续接同一 Pi 会话
    assert seen[0]["workdir"] == seen[1]["workdir"] == lock.runtime_base(cfg) / "chats" / chat_id


def test_first_turn_prepends_playbook_second_does_not(v3_tx, tmp_path):
    import investment_assistant.core.tx as core_tx

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        seed_ok_run(conn)
        chat_id = start_chat(conn)
    fake = FakeRuntime(lambda prompt: [RuntimeEvent("done")])
    list(chat.turn(chat_id=chat_id, text="第一问", cfg=cfg, runtime=fake, now=NOW))
    list(chat.turn(chat_id=chat_id, text="第二问", cfg=cfg, runtime=fake, now=NOW))
    playbook = (Path(__file__).parents[1] / "agent" / "playbook.md").read_text(encoding="utf-8")
    assert fake.calls[0]["prompt"].startswith(playbook)
    assert fake.calls[1]["prompt"] == "第二问"


def test_mcp_config_session_is_the_latest_ok_run(v3_tx, tmp_path):
    import investment_assistant.core.tx as core_tx

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        seed_ok_run(conn, session=date(2026, 11, 3), run_id="run-old")
        seed_ok_run(conn, session=date(2026, 11, 4), run_id="run-new")
        chat_id = start_chat(conn)
    list(chat.turn(chat_id=chat_id, text="问", cfg=cfg, runtime=FakeRuntime([]), now=NOW))
    config = json.loads((lock.runtime_base(cfg) / "chats" / chat_id / ".pi" / "mcp.json").read_text(encoding="utf-8"))
    entry = config["mcpServers"]["ia"]
    assert entry["command"].endswith("/.venv/bin/python") and entry["exposure"] == "direct"
    args = entry["args"]
    assert args[:2] == ["-m", "investment_assistant.toolsurface"]
    assert args[args.index("--session") + 1] == "2026-11-04"  # 最新 ok 运行的交易日
    assert args[args.index("--mode") + 1] == "chat"
    assert args[args.index("--agent-session") + 1] == f"chat-{chat_id}-1"


def test_read_only_chat_is_rejected(v3_tx, tmp_path):
    import investment_assistant.core.tx as core_tx
    from investment_assistant.facts import plan_repo

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        seed_ok_run(conn)
        chat_id = start_chat(conn)
        plan_repo.update_chat(conn, chat_id, read_only=True)
    with pytest.raises(ValueError, match="chat_read_only"):
        list(chat.turn(chat_id=chat_id, text="问", cfg=cfg, runtime=FakeRuntime([]), now=NOW))


def test_busy_lock_propagates(v3_tx, tmp_path):
    import investment_assistant.core.tx as core_tx

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        seed_ok_run(conn)
        chat_id = start_chat(conn)
    with lock.acquire("brief", cfg=cfg):  # 简报会话占着唯一的运行时
        with pytest.raises(lock.Busy) as exc:
            list(chat.turn(chat_id=chat_id, text="问", cfg=cfg, runtime=FakeRuntime([]), now=NOW))
        assert exc.value.kind == "chat"


def test_cancel_marks_the_message_cancelled(v3_tx, tmp_path):
    import investment_assistant.core.tx as core_tx
    from investment_assistant.facts import plan_repo

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        seed_ok_run(conn)
        chat_id = start_chat(conn)
    fake = FakeRuntime([RuntimeEvent("text", text="第一段"), RuntimeEvent("text", text="第二段")])
    stream = chat.turn(chat_id=chat_id, text="问", cfg=cfg, runtime=fake, now=NOW)
    assert next(stream).text == "第一段"
    chat.cancel(chat_id)
    rest = list(stream)
    assert [e.kind for e in rest] == ["cancelled"]
    with core_tx.read_only_tx() as conn:
        rows = plan_repo.messages(conn, chat_id)
    assert [(row["seq"], row["role"], row["status"], row["content"]) for row in rows] == [
        (1, "user", "ok", "问"),
        (2, "assistant", "cancelled", "第一段"),
    ]


def test_timeout_marks_the_message_timeout(v3_tx, tmp_path):
    import investment_assistant.core.tx as core_tx
    from investment_assistant.facts import plan_repo

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        seed_ok_run(conn)
        chat_id = start_chat(conn)
    fake = FakeRuntime([RuntimeEvent("text", text="慢"), RuntimeEvent("error", text="timeout")])
    cfg = AssistantConfig(v3=V3Config(briefs_dir=str(tmp_path / "rb" / "briefs"), chat_turn_seconds=30))
    events = list(chat.turn(chat_id=chat_id, text="问", cfg=cfg, runtime=fake, now=NOW))
    assert [e.kind for e in events] == ["text", "error"] and events[-1].text == "timeout"
    with core_tx.read_only_tx() as conn:
        rows = plan_repo.messages(conn, chat_id)
    assert rows[-1]["status"] == "timeout" and rows[-1]["error"] == "timeout"


def test_no_run_records_rejected(v3_tx, tmp_path):
    import investment_assistant.core.tx as core_tx

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        chat_id = start_chat(conn)
    with pytest.raises(ValueError, match="no_run"):
        list(chat.turn(chat_id=chat_id, text="问", cfg=cfg, runtime=FakeRuntime([]), now=NOW))


def test_turn_records_usage_and_tool_refs(v3_tx, tmp_path):
    import investment_assistant.core.tx as core_tx
    from investment_assistant.facts import plan_repo

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        seed_ok_run(conn)
        chat_id = start_chat(conn)
    fake = FakeRuntime(
        lambda prompt: [
            ("call", "get_market", {}),
            RuntimeEvent("usage", usage={"input": 7, "output": 3, "total": 10}),
            RuntimeEvent("text", text="结论"),
            RuntimeEvent("done"),
        ],
        env=lambda: make_env(session=SESSION, mode="chat", agent_session=f"chat-{chat_id}-1"),
    )
    events = list(chat.turn(chat_id=chat_id, text="问", cfg=cfg, runtime=fake, now=NOW))
    assert [e.kind for e in events] == ["tool_start", "tool_end", "usage", "text", "done"]
    with core_tx.read_only_tx() as conn:
        rows = plan_repo.messages(conn, chat_id)
    assistant = rows[-1]
    assert assistant["usage"] == {"input": 7, "output": 3, "total": 10}
    assert assistant["tool_refs"] == ["call:1"]
