"""The fake runtime (spec §9.1): replays a scripted event stream — fixed text, tool calls and
errors, no model, no subprocess. For automated tests only (page streaming, tool-call expansion,
timeout and cancel); daily runs never select it."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from investment_assistant.runtime.base import TERMINAL_KINDS, RuntimeEvent, RuntimeSpec

ScriptItem = object  # RuntimeEvent 或 ('call', tool, args)


class FakeRuntime:
    name = "fake"

    def __init__(self, script: list | Callable[[str], list], *, delay: float = 0, env: Any = None):
        """``script`` is a list of items or a callable taking the prompt and returning one.
        Items are RuntimeEvents or ``('call', tool, args)`` tuples — the latter dispatch a real
        MCP tool through ``toolsurface.dispatch.call`` (no subprocess); they need ``env``, a
        ToolEnv or a zero-argument callable returning one, so tests can pin the agent session.
        ``delay`` seconds pass before every item, making timeouts and cancels testable."""
        self._script = script
        self._delay = delay
        self._env = env
        self.calls: list[dict] = []  # 每次 run 的 prompt / workdir / session_id，供测试断言

    def run(
        self,
        *,
        prompt: str,
        workdir: Path,
        session_id: str | None,
        timeout_seconds: int,
        cancel: threading.Event,
        spec: RuntimeSpec,
        env_extra: dict[str, str],
    ):
        self.calls.append(
            {"prompt": prompt, "workdir": workdir, "session_id": session_id, "spec": spec, "env_extra": env_extra}
        )
        script = self._script(prompt) if callable(self._script) else self._script
        started = time.monotonic()
        terminal_seen = False
        env_once: list[Any] = []  # 本次 run 只解析一次 env：一个会话编号对应一次运行
        for item in script:
            if cancel.is_set():
                yield RuntimeEvent("cancelled")
                return
            if time.monotonic() - started >= timeout_seconds:
                yield RuntimeEvent("error", text="timeout")
                return
            if self._delay:
                time.sleep(self._delay)
            if isinstance(item, tuple) and item and item[0] == "call":
                yield from self._call(env_once, item[1], item[2])
                continue
            if not isinstance(item, RuntimeEvent):
                raise TypeError(f"剧本项需为 RuntimeEvent 或 ('call', tool, args)：{item!r}")
            yield item
            if item.kind in TERMINAL_KINDS:
                terminal_seen = True
        if not terminal_seen:
            yield RuntimeEvent("done")

    def _call(self, env_once: list, tool: str, args: dict):
        from investment_assistant.toolsurface import dispatch

        if self._env is None:
            raise RuntimeError("FakeRuntime 需要 env 才能执行剧本里的 ('call', tool, args) 项")
        if not env_once:
            env_once.append(self._env() if callable(self._env) else self._env)
        yield RuntimeEvent("tool_start", tool=tool)
        envelope = dispatch.call(env_once[0], tool, args)  # type: ignore[arg-type]  # env 由测试按 ToolEnv 提供
        ref = envelope.get("ref", "") if isinstance(envelope, dict) else ""
        yield RuntimeEvent("tool_end", tool=tool, ref=ref)
        if isinstance(envelope, dict) and envelope.get("error"):
            yield RuntimeEvent("text", text=f"[{tool} 失败] {envelope['error']}")
