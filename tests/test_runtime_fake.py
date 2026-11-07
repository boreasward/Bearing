"""FakeRuntime：剧本按序回放、cancel 置位以 cancelled 结束、超时以 error('timeout') 结束、
('call', tool, args) 项直接调工具面（spec §9.1 假实现；无 Pi、无网络）。"""

import threading

from investment_assistant.runtime.base import RuntimeEvent, RuntimeSpec
from investment_assistant.runtime.fake import FakeRuntime

from investment_assistant.toolsurface.env import make_env

SPEC = RuntimeSpec(runtime="fake", provider="prov-x", model="model-x")


def run(fake, **overrides):
    kwargs = dict(
        prompt="做简报",
        workdir=None,
        session_id=None,
        timeout_seconds=60,
        cancel=threading.Event(),
        spec=SPEC,
        env_extra={},
    )
    kwargs.update(overrides)
    return list(fake.run(**kwargs))


def test_script_replays_in_order_and_ends_done():
    script = [
        RuntimeEvent("text", text="第一段"),
        RuntimeEvent("tool_start", tool="mcp__ia__get_agenda"),
        RuntimeEvent("tool_end", tool="mcp__ia__get_agenda", ref="call:1"),
        RuntimeEvent("usage", usage={"input": 10, "output": 5, "total": 15}),
    ]
    events = run(FakeRuntime(script))
    assert [(e.kind, e.text, e.tool, e.ref) for e in events] == [
        ("text", "第一段", "", ""),
        ("tool_start", "", "mcp__ia__get_agenda", ""),
        ("tool_end", "", "mcp__ia__get_agenda", "call:1"),
        ("usage", "", "", ""),
        ("done", "", "", ""),
    ]
    assert events[-1].usage == {}


def test_cancel_between_events_ends_cancelled():
    script = [RuntimeEvent("text", text="一"), RuntimeEvent("text", text="二")]
    fake = FakeRuntime(script)
    cancel = threading.Event()
    stream = fake.run(
        prompt="p",
        workdir=None,
        session_id=None,
        timeout_seconds=60,
        cancel=cancel,
        spec=SPEC,
        env_extra={},
    )
    assert next(stream).text == "一"
    cancel.set()
    assert [e.kind for e in stream] == ["cancelled"]


def test_timeout_ends_error_timeout():
    script = [RuntimeEvent("text", text="慢")]
    events = run(FakeRuntime(script), timeout_seconds=0)
    assert [e.kind for e in events] == ["error"] and events[0].text == "timeout"


def test_script_may_end_with_its_own_terminal_event():
    events = run(FakeRuntime([RuntimeEvent("error", text="模型中断")]))
    assert [(e.kind, e.text) for e in events] == [("error", "模型中断")]


def test_call_items_dispatch_real_tools_without_a_subprocess(v3_tx, monkeypatch):
    """('call', tool, args) 项经 toolsurface.dispatch.call 真的写库，tool_end 带 call:N ref。"""

    fake = FakeRuntime(
        [("call", "save_summary", {"text": "今天没有新机会。"}), RuntimeEvent("done")],
        env=lambda: make_env(
            session=__import__("datetime").date(2026, 11, 4), mode="brief", agent_session="fake-runtime-test"
        ),
    )
    events = run(fake)
    kinds = [(e.kind, e.tool, e.ref) for e in events]
    assert kinds == [("tool_start", "save_summary", ""), ("tool_end", "save_summary", "call:1"), ("done", "", "")]
    row = v3_tx.execute("SELECT summary FROM v3_briefs WHERE session = '2026-11-04'").fetchone()
    assert row is not None and row[0] == "今天没有新机会。"


def test_callable_script_sees_the_prompt():
    fake = FakeRuntime(lambda prompt: [RuntimeEvent("text", text=prompt + "！")])
    assert [e.text for e in run(fake)] == ["做简报！", ""]
