"""/api/chats*：对话列表、新建、详情、SSE 流式轮次与取消（spec §12 对话块 / v3 设计 §15.3）。
每轮 runtime.chat.turn 在工作线程里跑、事件经队列转成 SSE 帧；连接断开时置位该对话的取消。"""

from __future__ import annotations

import json
import queue
import re
import threading
from datetime import UTC, datetime

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse, StreamingResponse

from investment_assistant.api import deps
from investment_assistant.config import AssistantConfig, load_config
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.runtime import chat as runtime_chat
from investment_assistant.runtime import lock
from investment_assistant.runtime import settings as runtime_settings
from investment_assistant.runtime.base import RuntimeEvent, RuntimeSpec

router = APIRouter(prefix="/api/chats")

OPTIONAL_BODY = Body(default=None)  # 模块级单例：可选的空 JSON 请求体（ruff B008）
REQUIRED_BODY = Body(...)  # 模块级单例：必需的 JSON 请求体
MESSAGE_FIELDS = ("seq", "role", "content", "status", "session", "agent_session", "tool_refs", "usage", "created_at")
CHAT_LIST_FIELDS = ("chat_id", "title", "created_at", "last_message_at", "read_only", "model")


def _cfg() -> AssistantConfig:
    """页面服务用的配置；测试注入替身。"""
    return load_config()


def _runtime_for(spec: RuntimeSpec):
    """对话行指定的运行时实现；测试注入剧本。"""
    return runtime_settings.build(spec)


def _busy_code(holder_kind: str | None) -> str:
    """锁被谁占着，error 码就是谁在跑（v3 设计验收 16）；前置 409 与 worker 的 error 帧共用。"""
    return "brief_running" if holder_kind == "brief" else "chat_running"


def _sse_frame(event: RuntimeEvent) -> str:
    payload = (
        {"kind": "start", "agent_session": event.text}
        if event.kind == "start"
        else {"kind": event.kind, "text": event.text, "tool": event.tool, "ref": event.ref, "usage": event.usage}
    )
    return f"event: {event.kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sse_stream(*, chat_id: str, text: str, cfg: AssistantConfig, runtime, now: datetime):
    """turn 在工作线程里跑，事件经队列转成 SSE 帧；末事件必为 done/error/cancelled。客户端
    断开（生成器被关闭）时置位该对话的取消，让这一轮以 cancelled 收尾并如实记账。"""
    events: queue.SimpleQueue = queue.SimpleQueue()
    finished = threading.Event()

    def _work():
        try:
            for event in runtime_chat.turn(chat_id=chat_id, text=text, cfg=cfg, runtime=runtime, now=now):
                events.put(event)
        except lock.Busy:  # 前置检查与拿锁之间被占：以带 code 的 error 帧收尾
            events.put(RuntimeEvent("error", text=_busy_code(lock.holder(cfg))))
        except ValueError as exc:  # 首事件前的前置拒绝（如并发时 no_run）以 error 事件收尾
            events.put(RuntimeEvent("error", text=str(exc)))
        except Exception as exc:  # noqa: BLE001 - 出错也必须有收尾事件，不悬挂连接
            events.put(RuntimeEvent("error", text=str(exc)))
        finally:
            finished.set()
            events.put(None)

    threading.Thread(target=_work, daemon=True).start()
    try:
        while (event := events.get()) is not None:
            yield _sse_frame(event)
    finally:
        if not finished.is_set():
            runtime_chat.cancel(chat_id)


@router.get("")
def list_chats(limit: int = 30):
    if not 1 <= limit <= 100:
        return JSONResponse({"error": "invalid_argument: limit 必须是 1–100 之间的整数"}, status_code=400)
    rows = deps.read_tx(lambda conn: plan_repo.chats(conn, limit=limit))
    return deps.ok({"chats": [{field: row[field] for field in CHAT_LIST_FIELDS} for row in rows]})


@router.post("")
def create_chat(body: dict | None = OPTIONAL_BODY):
    del body  # 无参数；允许空对象体

    def _work(conn):
        spec = runtime_settings.current(conn)
        if spec is None:
            raise ValueError("no_runtime_selected")
        row = runtime_chat.start_chat(conn, spec=spec)
        return {"chat_id": row["chat_id"], "read_only": row["read_only"]}  # 契约表只这两项

    return deps.ok_or_error(lambda: deps.write_tx(_work))


@router.get("/{chat_id}")
def chat_detail(chat_id: str):
    def _read(conn):
        row = plan_repo.chat(conn, chat_id)
        if row is None:
            raise ValueError("no_chat")
        messages = [{field: row[field] for field in MESSAGE_FIELDS} for row in plan_repo.messages(conn, chat_id)]
        return {"chat": row, "messages": messages}

    return deps.ok_or_error(lambda: deps.read_tx(_read))


@router.get("/{chat_id}/calls")
def tool_call(chat_id: str, agent_session: str, ref: str):
    def _read(conn):
        if not agent_session.startswith(f"chat-{chat_id}-") or re.fullmatch(r"call:\d+", ref, flags=re.ASCII) is None:
            raise ValueError("unknown_call")
        seq_text = ref.partition(":")[2].lstrip("0") or "0"
        # PostgreSQL sequences are signed BIGINT; reject oversized refs before binding a SQL integer.
        if len(seq_text) > 19 or int(seq_text) > 9223372036854775807:
            raise ValueError("unknown_call")
        row = facts_repo.tool_call(conn, agent_session, int(seq_text))
        if row is None:
            raise ValueError("unknown_call")
        return {
            "ref": ref,
            **{field: row[field] for field in ("tool", "args", "result", "error", "called_at", "truncated", "result_available")},
        }

    return deps.ok_or_error(lambda: deps.read_tx(_read))


@router.post("/{chat_id}/messages")
def post_message(chat_id: str, body: dict = REQUIRED_BODY):
    cfg = _cfg()

    def _preflight(conn):
        row = plan_repo.chat(conn, chat_id)
        if row is None:
            raise ValueError("no_chat")
        if row["read_only"]:
            raise ValueError("chat_read_only")  # 切换运行时后的旧对话：可看不可写（v3 设计 §15.3）
        if facts_repo.latest_ok_run(conn) is None:
            raise ValueError("no_run")
        return row

    try:
        chat_row = deps.read_tx(_preflight)
    except ValueError as exc:
        code = str(exc)
        status = 404 if code in ("no_chat", "no_run") else 409
        return JSONResponse({"error": code}, status_code=status)
    if not isinstance(body.get("text"), str) or not body["text"].strip():
        return JSONResponse({"error": "invalid_argument: text 必须是非空字符串"}, status_code=400)
    holder_kind = lock.holder(cfg)  # 锁持有者是谁，409 的 error 码就是谁在跑（v3 设计验收 16）
    if holder_kind is not None:
        return JSONResponse({"error": _busy_code(holder_kind)}, status_code=409)
    spec = RuntimeSpec(runtime=chat_row["runtime"], provider=chat_row["provider"], model=chat_row["model"])
    return StreamingResponse(
        _sse_stream(chat_id=chat_id, text=body["text"], cfg=cfg, runtime=_runtime_for(spec), now=datetime.now(UTC)),
        media_type="text/event-stream",
    )


@router.post("/{chat_id}/cancel")
def cancel_turn(chat_id: str):
    runtime_chat.cancel(chat_id)
    return deps.ok({"cancelled": True})
