"""The chat turns (spec §9.1): every turn rewrites the chat's MCP config for the latest ok run's
trading session, runs one runtime turn under the shared lock and persists the user and assistant
message rows. The page service (Task 5) streams these events; ``Busy`` maps to 409 there."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from datetime import datetime

from investment_assistant.runtime import lock, mcpconfig
from investment_assistant.runtime.base import (
    REPO_ROOT,
    TERMINAL_KINDS,
    Runtime,
    RuntimeEvent,
    RuntimeSpec,
    workspace_file,
)

# 每个对话一个取消事件：cancel(chat_id) 置位，turn 开头清零。
_cancels: dict[str, threading.Event] = {}


def start_chat(conn, *, spec: RuntimeSpec) -> dict:
    """A new chat row carrying the runtime/provider/model it was opened with (v3 设计验收 17)."""
    from investment_assistant.facts import plan_repo

    return plan_repo.insert_chat(
        conn,
        {
            "chat_id": uuid.uuid4().hex,
            "runtime": spec.runtime,
            "provider": spec.provider,
            "model": spec.model,
        },
    )


def cancel(chat_id: str) -> None:
    """Ask the running turn of this chat to stop; the next turn clears the flag itself."""
    _cancels.setdefault(chat_id, threading.Event()).set()


def turn(
    *,
    chat_id: str,
    text: str,
    cfg,
    runtime: Runtime,
    now: datetime,
) -> Iterator[RuntimeEvent]:
    """One user turn: stream the runtime events, then write the assistant row with the terminal
    status (ok | error | cancelled | timeout), the session it read, the usage and the tool refs."""
    import investment_assistant.core.tx as core_tx
    from investment_assistant.facts import plan_repo
    from investment_assistant.facts import repo as facts_repo

    del now  # 消息行的时间戳由数据库缺省；参数保留给调用方对齐
    with core_tx.read_only_tx() as conn:
        chat_row = plan_repo.chat(conn, chat_id)
        if chat_row is None:
            raise ValueError("no_chat")
        if chat_row["read_only"]:
            raise ValueError("chat_read_only")
        latest = facts_repo.latest_ok_run(conn)
        if latest is None:
            raise ValueError("no_run")
        session = latest["session"]
        seq = plan_repo.next_message_seq(conn, chat_id)
    agent_session = f"chat-{chat_id}-{seq}"
    with core_tx._tx() as conn:
        plan_repo.insert_message(
            conn,
            {
                "chat_id": chat_id,
                "seq": seq,
                "role": "user",
                "content": text,
                "session": session,
                "status": "ok",
            },
        )
    workdir = lock.runtime_base(cfg) / "chats" / chat_id
    mcpconfig.write(workdir, repo_root=REPO_ROOT, session=session, mode="chat", agent_session=agent_session)
    prompt = text
    if seq == 1:  # 首轮把操作手册正文接在提示词前（spec §9.5）
        prompt = workspace_file("playbook.md").read_text(encoding="utf-8").strip() + "\n\n" + text
    spec = RuntimeSpec(runtime=chat_row["runtime"], provider=chat_row["provider"], model=chat_row["model"])
    cancel_event = _cancels.setdefault(chat_id, threading.Event())
    cancel_event.clear()
    parts: list[str] = []
    tool_refs: list[str] = []
    usage = {"input": 0, "output": 0, "total": 0}
    terminal = RuntimeEvent("error", text="runtime_ended_without_terminal")
    with lock.acquire("chat", cfg=cfg):
        for event in runtime.run(
            prompt=prompt,
            workdir=workdir,
            session_id=f"chat-{chat_id}",  # 同一对话续接同一 Pi 会话（spec §9.1）
            timeout_seconds=cfg.v3.chat_turn_seconds,
            cancel=cancel_event,
            spec=spec,
            env_extra={},
        ):
            if event.kind == "text":
                parts.append(event.text)
            elif event.kind == "tool_end" and event.ref:
                tool_refs.append(event.ref)
            elif event.kind == "usage" and event.usage:
                for key in usage:
                    usage[key] += int(event.usage.get(key, 0) or 0)
            if event.kind in TERMINAL_KINDS:
                terminal = event
            yield event
    status, error = _status_of(terminal)
    with core_tx._tx() as conn:
        plan_repo.insert_message(
            conn,
            {
                "chat_id": chat_id,
                "seq": plan_repo.next_message_seq(conn, chat_id),
                "role": "assistant",
                "content": "".join(parts),
                "session": session,
                "agent_session": agent_session,
                "status": status,
                "error": error,
                "usage": usage,
                "tool_refs": tool_refs,
            },
        )


def _status_of(terminal: RuntimeEvent) -> tuple[str, str | None]:
    """结束事件 -> (消息行状态, 原因)；error('timeout') 记为 timeout。"""
    if terminal.kind == "done":
        return "ok", None
    if terminal.kind == "cancelled":
        return "cancelled", None
    if terminal.text == "timeout":
        return "timeout", "timeout"
    return "error", terminal.text or "error"
