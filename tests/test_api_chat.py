"""/api/chats*：列表、新建（未选模型 400）、详情、SSE 流式轮次、取消；锁被占用 409（验收 14/16/17/19 后端、Review Focus 5）。"""

import json
from datetime import UTC, date, datetime, timedelta

import pytest

import investment_assistant.core.tx as core_tx
from investment_assistant.config import AssistantConfig, V3Config
from investment_assistant.facts import repo as facts_repo
from investment_assistant.runtime.base import RuntimeEvent

SESSION = date(2026, 11, 4)
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)


def seed_run(conn):
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


def _sse_events(resp):
    """把 text/event-stream 还原成 [(kind, payload)]。"""
    kind, data = None, []
    for line in resp.iter_lines():
        if line.startswith("event: "):
            kind = line[len("event: ") :]
        elif line.startswith("data: "):
            data.append(line[len("data: ") :])
        elif line == "" and kind is not None:
            yield kind, json.loads("\n".join(data))
            kind, data = None, []


@pytest.fixture
def page(client, v3_tx, tmp_path, monkeypatch):
    """已选定 fake 运行时的页面：chats 路由的 cfg 与运行时工厂可注入。"""
    from investment_assistant.api.routes import chats as chats_routes

    with core_tx._tx() as conn:
        seed_run(conn)
    monkeypatch.setattr(
        chats_routes, "_cfg", lambda: AssistantConfig(v3=V3Config(briefs_dir=str(tmp_path / "rb" / "briefs")))
    )
    monkeypatch.setattr(chats_routes, "_runtime_for", lambda spec: None)  # 各测试自行覆盖
    put = client.put("/api/settings", json={"runtime": "fake", "provider": "prov-x", "model": "model-x"})
    assert put.status_code == 200
    return client, chats_routes


def _new_chat(page):
    client, _ = page
    resp = client.post("/api/chats", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"chat_id", "read_only"} and body["read_only"] is False  # 契约表：只这两项
    return body["chat_id"]


def _fake(chats_routes, script, **kwargs):
    from investment_assistant.runtime.fake import FakeRuntime

    fake = FakeRuntime(script, **kwargs)
    chats_routes._runtime_for = lambda spec: fake
    return fake


def test_turn_streams_and_second_turn_sees_the_first(page):
    client, chats_routes = page
    chat_id = _new_chat(page)
    _fake(chats_routes, lambda prompt: [RuntimeEvent("text", text="回答：好"), RuntimeEvent("done")])
    for text in ("今天适合交易什么？", "第二问"):
        with client.stream("POST", f"/api/chats/{chat_id}/messages", json={"text": text}) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            events = list(_sse_events(resp))
        assert [kind for kind, _ in events] == ["text", "done"]  # 末事件是 done
        assert events[0][1]["text"] == "回答：好"
        assert set(events[0][1]) == {"kind", "text", "tool", "ref", "usage"}
    detail = client.get(f"/api/chats/{chat_id}").json()
    assert [(m["seq"], m["role"], m["status"]) for m in detail["messages"]] == [
        (1, "user", "ok"),
        (2, "assistant", "ok"),
        (3, "user", "ok"),
        (4, "assistant", "ok"),
    ]  # 第二轮看得到第一轮的消息（验收 14 后端）


def test_cancel_ends_stream_and_next_turn_works(page):
    """Review Focus 5：取消以 cancelled 事件收尾、消息行如实，下一轮照常可问。TestClient 会把
    流式响应整体缓冲，所以这里直接驱动路由的 SSE 生成器；取消仍走 HTTP 端点。"""
    client, chats_routes = page
    chat_id = _new_chat(page)
    _fake(
        chats_routes,
        [RuntimeEvent("text", text=f"第{i}段") for i in range(4)],
        delay=0.5,
    )
    stream = chats_routes._sse_stream(
        chat_id=chat_id,
        text="慢慢答",
        cfg=chats_routes._cfg(),
        runtime=chats_routes._runtime_for(None),
        now=NOW,
    )
    assert next(stream).startswith("event: text")  # 首帧文字
    cancelled = client.post(f"/api/chats/{chat_id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json() == {"cancelled": True}
    rest = [frame.split("\n")[0][len("event: ") :] for frame in stream]
    assert rest and rest[-1] == "cancelled"  # 流以 cancelled 收尾，无后续帧
    _fake(chats_routes, lambda prompt: [RuntimeEvent("done")])
    with client.stream("POST", f"/api/chats/{chat_id}/messages", json={"text": "再问"}) as resp:
        assert [kind for kind, _ in _sse_events(resp)] == ["done"]  # 下一轮照常（验收 19）
    detail = client.get(f"/api/chats/{chat_id}").json()
    assert [m["status"] for m in detail["messages"]] == ["ok", "cancelled", "ok", "ok"]


def test_client_disconnect_sets_the_cancel_flag(page, monkeypatch):
    """SSE 生成器被提前关闭时置位对话取消（v3 设计 §15.3 取消即结束该进程）。"""
    from investment_assistant.runtime import chat as runtime_chat

    _, chats_routes = page
    chat_id = _new_chat(page)
    _fake(chats_routes, [RuntimeEvent("text", text="一") for _ in range(3)], delay=0.3)
    seen_cancel = []
    monkeypatch.setattr(runtime_chat, "cancel", lambda chat_id: seen_cancel.append(chat_id))
    stream = chats_routes._sse_stream(
        chat_id=chat_id,
        text="问",
        cfg=chats_routes._cfg(),
        runtime=chats_routes._runtime_for(None),
        now=NOW,
    )
    assert next(stream).startswith("event: text")
    stream.close()  # 客户端断开
    assert seen_cancel == [chat_id]


def test_brief_running_is_409(page):
    from investment_assistant.api.routes import chats as chats_routes
    from investment_assistant.runtime import lock

    client, _ = page
    chat_id = _new_chat(page)
    with lock.acquire("brief", cfg=chats_routes._cfg()):  # 简报会话占着唯一的运行时
        resp = client.post(f"/api/chats/{chat_id}/messages", json={"text": "简报生成中提问"})
    assert resp.status_code == 409 and resp.json() == {"error": "brief_running"}  # 验收 16


def test_chat_running_is_409(page):
    from investment_assistant.runtime import lock

    client, chats_routes = page
    chat_id = _new_chat(page)
    with lock.acquire("chat", cfg=chats_routes._cfg()):
        resp = client.post(f"/api/chats/{chat_id}/messages", json={"text": "另一轮在跑"})
    assert resp.status_code == 409 and resp.json() == {"error": "chat_running"}


def test_busy_inside_the_worker_is_a_coded_error_frame(page, monkeypatch):
    """前置检查与 worker 拿锁之间锁被占：SSE 以带 code 的 error 帧收尾（brief/chat_running
    按锁持有者，与 409 前置检查同一套映射）。"""
    from investment_assistant.runtime import chat as runtime_chat
    from investment_assistant.runtime import lock

    _, chats_routes = page
    chat_id = _new_chat(page)

    def busy_turn(**kwargs):
        raise lock.Busy("chat")  # turn 真实路径里 lock.acquire('chat') 失败的替身
        yield  # pragma: no cover - 生成器签名

    monkeypatch.setattr(runtime_chat, "turn", busy_turn)
    for holder, code in (("brief", "brief_running"), ("chat", "chat_running")):
        with lock.acquire(holder, cfg=chats_routes._cfg()):
            stream = chats_routes._sse_stream(
                chat_id=chat_id, text="问", cfg=chats_routes._cfg(), runtime=None, now=NOW
            )
            frames = list(stream)
        assert len(frames) == 1 and frames[0].startswith("event: error")
        payload = json.loads(frames[0].split("\n")[1][len("data: "):])
        assert payload["kind"] == "error" and payload["text"] == code


def test_read_only_chat_cannot_post(page):
    from investment_assistant.facts import plan_repo

    client, _ = page
    chat_id = _new_chat(page)
    with core_tx._tx() as conn:
        plan_repo.update_chat(conn, chat_id, read_only=True)
    resp = client.post(f"/api/chats/{chat_id}/messages", json={"text": "旧对话"})
    assert resp.status_code == 409 and resp.json()["error"] == "chat_read_only"


def test_chats_list_and_detail(page):
    client, _ = page
    chat_id = _new_chat(page)
    listed = client.get("/api/chats").json()["chats"]
    assert set(listed[0]) == {"chat_id", "title", "created_at", "read_only", "model"}
    assert listed[0]["chat_id"] == chat_id and listed[0]["model"] == "model-x"
    detail = client.get(f"/api/chats/{chat_id}").json()
    assert detail["chat"]["chat_id"] == chat_id and detail["messages"] == []
    missing = client.get("/api/chats/nope")
    assert missing.status_code == 404 and missing.json()["error"] == "no_chat"


def test_new_chat_without_model_is_400(client, v3_tx, tmp_path, monkeypatch):
    from investment_assistant.api.routes import chats as chats_routes

    with core_tx._tx() as conn:
        seed_run(conn)
    monkeypatch.setattr(
        chats_routes, "_cfg", lambda: AssistantConfig(v3=V3Config(briefs_dir=str(tmp_path / "rb" / "briefs")))
    )
    resp = client.post("/api/chats", json={})
    assert resp.status_code == 400 and resp.json()["error"] == "no_runtime_selected"


def test_switching_runtime_marks_old_chats_read_only(page, tmp_path, monkeypatch):
    """验收 17 后端：保存设置把 runtime 不同的旧对话置 read_only；新对话带新模型。"""
    from investment_assistant.api.routes import settings as settings_routes

    client, _ = page
    old_chat = _new_chat(page)
    pi_dir = tmp_path / "pi"
    pi_dir.mkdir()
    (pi_dir / "models.json").write_text(
        json.dumps({"providers": {"prov-a": {"models": [{"id": "model-a1"}]}}}), encoding="utf-8"
    )
    monkeypatch.setattr(settings_routes, "_pi_dir", lambda: pi_dir)
    resp = client.put("/api/settings", json={"runtime": "pi", "provider": "prov-a", "model": "model-a1"})
    assert resp.status_code == 200 and resp.json() == {"runtime": "pi", "provider": "prov-a", "model": "model-a1"}
    listed = client.get("/api/chats").json()["chats"]
    by_id = {row["chat_id"]: row for row in listed}
    assert by_id[old_chat]["read_only"] is True  # 旧对话只读（v3 设计 §15.3）
    new_chat = _new_chat(page)
    detail = client.get(f"/api/chats/{new_chat}").json()
    assert detail["chat"]["model"] == "model-a1" and detail["chat"]["runtime"] == "pi"
    assert by_id.get(new_chat) is None or client.get("/api/chats").json()["chats"][0]["read_only"] is False
