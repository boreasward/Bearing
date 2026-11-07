"""PiRuntime：假 Popen 回放 fixtures/pi_events 的事件流——文字增量、工具、用量、done/error；
超时 kill + error('timeout')；命令行、环境白名单与 --no-session / --session-id（pi-unattended.md）。"""

import json
import subprocess
import threading
from pathlib import Path

from investment_assistant.runtime.base import RuntimeSpec
from investment_assistant.runtime.pi import PiRuntime

FIXTURES = Path(__file__).parent / "fixtures" / "pi_events"
SPEC = RuntimeSpec(runtime="pi", provider="prov-x", model="model-x")


class _Stdout:
    """回放固定行；stall=True 时行耗尽后停住不关流（模拟无输出被挂起的进程）。"""

    def __init__(self, lines, dead: threading.Event, *, stall: bool):
        self._lines, self._dead, self._stall = lines, dead, stall

    def __iter__(self):
        yield from self._lines
        if self._stall:
            self._dead.wait(30)


class FakePopen:
    _lines: list  # install_popen 用子类注入每次回放的行
    _stall: bool = False

    def __init__(self, args, *, cwd=None, env=None, stdin=None, stdout=None, stderr=None, **kwargs):
        self.args, self.cwd, self.env, self.stdin = args, cwd, env, stdin
        self.killed = False
        self._dead = threading.Event()
        self.stdout = _Stdout(self._lines, self._dead, stall=self._stall)

    def kill(self):
        self.killed = True
        self._dead.set()

    def wait(self, timeout=None):
        return 0


def install_popen(monkeypatch, fixture: str, *, stall: bool = False) -> list:
    lines = (FIXTURES / fixture).read_text(encoding="utf-8").splitlines(keepends=True)
    instances = []
    cls = type("FakePopen", (FakePopen,), {"_lines": lines, "_stall": stall})

    def popen(*args, **kwargs):
        instance = cls(*args, **kwargs)
        instances.append(instance)
        return instance

    monkeypatch.setattr(subprocess, "Popen", popen)
    return instances


def run(pi, *, prompt="做简报", session_id=None, timeout_seconds=30.0, cancel=None, env_extra=None):
    stream = pi.run(
        prompt=prompt,
        workdir=Path("/tmp/ia-work"),
        session_id=session_id,
        timeout_seconds=timeout_seconds,
        cancel=cancel or threading.Event(),
        spec=SPEC,
        env_extra=env_extra or {},
    )
    return list(stream)


def test_ok_stream_texts_tools_usage_and_done(monkeypatch):
    instances = install_popen(monkeypatch, "ok.jsonl")
    events = run(PiRuntime())
    assert [e.kind for e in events] == [
        "text",
        "text",
        "usage",
        "tool_start",
        "tool_end",
        "text",
        "usage",
        "done",
    ]
    assert "".join(e.text for e in events if e.kind == "text") == "今天先看待办。全部待办已完成。"
    assert events[3].tool == "mcp__ia__get_agenda" and events[4].tool == "mcp__ia__get_agenda"
    assert events[2].usage == {"input": 10, "output": 5, "total": 15}
    assert events[6].usage == {"input": 40, "output": 20, "total": 60}
    assert events[-1].usage == {"input": 50, "output": 25, "total": 75}  # done 带累加用量
    proc = instances[0]
    assert proc.stdin == subprocess.DEVNULL and proc.cwd == "/tmp/ia-work"


def test_command_line_and_session_arguments(monkeypatch):
    instances = install_popen(monkeypatch, "ok.jsonl")
    run(PiRuntime(), prompt="提示词")
    args = instances[0].args
    assert args[0].endswith("/pi") and args[-1] == "提示词"
    for flag in (
        "--mode",
        "json",
        "--approve",
        "--no-extensions",
        "-e",
        "builtin:mcp",
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--no-context-files",
    ):
        assert flag in args
    assert args[args.index("--tools") + 1] == "mcp__ia__*"
    assert args[args.index("--provider") + 1] == "prov-x"
    assert args[args.index("--model") + 1] == "model-x"
    assert "--no-session" in args

    instances = install_popen(monkeypatch, "ok.jsonl")
    run(PiRuntime(), session_id="chat-abc")
    args = instances[0].args
    assert "--no-session" not in args
    assert args[args.index("--session-dir") + 1] == "/tmp/ia-work/sessions"
    assert args[args.index("--session-id") + 1] == "chat-abc"


def test_env_whitelist_passes_proxy_and_pi_vars_only(monkeypatch):
    monkeypatch.setenv("HOME", "/tmp/fake-home")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("no_proxy", "localhost")
    monkeypatch.setenv("SECRET_TOKEN", "must-not-leak")
    monkeypatch.delenv("INVESTMENT_ASSISTANT_PI_DIR", raising=False)
    instances = install_popen(monkeypatch, "ok.jsonl")
    run(PiRuntime(), env_extra={"INVESTMENT_ASSISTANT_DATABASE_URL": "postgresql://x/y"})
    env = instances[0].env
    assert env["HOME"] == "/tmp/fake-home"
    assert env["PATH"] == "/tmp/fake-home/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    assert env["HTTP_PROXY"] == "http://proxy.example:3128" and env["no_proxy"] == "localhost"
    assert env["PI_SKIP_VERSION_CHECK"] == "1"
    assert env["PI_CODING_AGENT_DIR"] == str(Path.home() / ".pi-ia")
    assert env["INVESTMENT_ASSISTANT_DATABASE_URL"] == "postgresql://x/y"
    assert "SECRET_TOKEN" not in env and "PYTHONPATH" not in env

    monkeypatch.setenv("INVESTMENT_ASSISTANT_PI_DIR", "/tmp/other-pi")
    instances = install_popen(monkeypatch, "ok.jsonl")
    run(PiRuntime())
    assert instances[0].env["PI_CODING_AGENT_DIR"] == "/tmp/other-pi"


def test_error_stream_ends_error_with_message(monkeypatch):
    install_popen(monkeypatch, "error.jsonl")
    events = run(PiRuntime())
    assert [e.kind for e in events] == ["text", "usage", "error"]
    assert events[-1].text == "model request failed: 500"


def test_stalled_stream_times_out_and_kills(monkeypatch):
    instances = install_popen(monkeypatch, "partial.jsonl", stall=True)
    events = run(PiRuntime(), timeout_seconds=0.4)
    assert [e.kind for e in events] == ["text", "error"] and events[-1].text == "timeout"
    assert instances[0].killed


def test_cancel_kills_and_ends_cancelled(monkeypatch):
    instances = install_popen(monkeypatch, "partial.jsonl", stall=True)
    cancel = threading.Event()
    cancel.set()
    events = run(PiRuntime(), timeout_seconds=30, cancel=cancel)
    assert [e.kind for e in events] == ["cancelled"]
    assert instances[0].killed


def test_pi_event_field_names_match_the_recorded_fixtures():
    """fixtures 记录的就是 pi.py 依赖的字段名（Pi 1.5.0 docs/json.md）。"""
    ok = [json.loads(line) for line in (FIXTURES / "ok.jsonl").read_text(encoding="utf-8").splitlines()]
    updates = [e for e in ok if e["type"] == "message_update"]
    assert updates[0]["assistantMessageEvent"]["type"] == "text_delta"
    assert updates[0]["assistantMessageEvent"]["delta"] == "今天先看"
    assistant_ends = [e["message"] for e in ok if e["type"] == "message_end" and e["message"]["role"] == "assistant"]
    assert assistant_ends[-1]["stopReason"] == "stop" and assistant_ends[-1]["usage"]["totalTokens"] == 60
    tools = [e for e in ok if e["type"] in ("tool_execution_start", "tool_execution_end")]
    assert all(e["toolName"] == "mcp__ia__get_agenda" for e in tools)
    err = [json.loads(line) for line in (FIXTURES / "error.jsonl").read_text(encoding="utf-8").splitlines()]
    failed = next(e["message"] for e in err if e["type"] == "message_end" and e["message"]["role"] == "assistant")
    assert failed["stopReason"] == "error" and failed["errorMessage"] == "model request failed: 500"
