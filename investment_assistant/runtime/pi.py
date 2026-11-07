"""The Pi runtime (spec §9.1, docs/operations/pi-unattended.md): one Pi process per run — the
brief session every attempt starts a fresh process with ``--no-session``, chats continue a fixed
session id. Success is judged from the event stream, never from the exit code."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from investment_assistant.runtime.base import RuntimeEvent, RuntimeSpec

# Pi 1.5.0 事件流的字段名，依据 Pi 自带的 docs/json.md 与 docs/message-types.md（安装目录
# ~/.pi/agent/install/releases/1.5.0/node_modules/@earendil-works/pi-coding-agent/docs/），并逐字
# 记录在 tests/fixtures/pi_events/*.jsonl：
# - 文字增量：message_update.assistantMessageEvent == {'type': 'text_delta', 'delta': <str>}
# - 工具：tool_execution_start / tool_execution_end 的 toolName（toolCallId 是 Pi 自己的编号，
#   不是本服务工具面的 call:N，所以 tool_end 的 ref 留空）
# - 一条助手消息完成：message_end.message{role, content[].text, stopReason, errorMessage, usage}
# - usage 的 input/output/totalTokens 映射为本层事件 usage 的 input/output/total
TEXT_DELTA_EVENT = "text_delta"  # assistantMessageEvent.type
TEXT_DELTA_FIELD = "delta"  # assistantMessageEvent.delta
STOP_REASON_ERROR = "error"  # message.stopReason 的失败值
ERROR_MESSAGE_FIELD = "errorMessage"  # 同一条消息上的失败原因
USAGE_TOKENS_FIELD = "totalTokens"  # Pi 的 usage 键 -> 本层的 total

POLL_SECONDS = 0.1  # 读流空转时的轮询间隔：超时与取消的判定粒度
REAP_WAIT_SECONDS = 5  # 杀进程后/流关闭后等它退出的上界

DEFAULT_PI_BIN = "~/.local/bin/pi"
DEFAULT_PI_DIR = "~/.pi-ia"
PATH_TEMPLATE = "{home}/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"  # pi-unattended.md 实测够用


def pi_binary() -> Path:
    """The launcher path: $INVESTMENT_ASSISTANT_PI_BIN overrides the default."""
    return Path(os.environ.get("INVESTMENT_ASSISTANT_PI_BIN", DEFAULT_PI_BIN)).expanduser()


def pi_dir() -> Path:
    """The dedicated configuration directory (spec §7 隔离用户配置)：$INVESTMENT_ASSISTANT_PI_DIR
    overrides the default."""
    return Path(os.environ.get("INVESTMENT_ASSISTANT_PI_DIR", DEFAULT_PI_DIR)).expanduser()


def build_env(env_extra: dict[str, str]) -> dict[str, str]:
    """The allow-list of pi-unattended.md: HOME, the fixed PATH, inherited proxy variables
    (name contains 'proxy', case-insensitive) and the two PI_ variables; ``env_extra`` last."""
    home = os.environ.get("HOME", "")
    env = {"HOME": home, "PATH": PATH_TEMPLATE.format(home=home)}
    for name, value in os.environ.items():
        if "proxy" in name.lower():
            env[name] = value
    env["PI_CODING_AGENT_DIR"] = str(pi_dir())
    env["PI_SKIP_VERSION_CHECK"] = "1"
    env.update(env_extra)
    return env


class PiRuntime:
    name = "pi"

    def __init__(self, binary: Path | None = None):
        self._binary = binary

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
    ) -> Iterator[RuntimeEvent]:
        binary = self._binary if self._binary is not None else pi_binary()
        argv = [
            str(binary),
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
            "--tools",
            "mcp__ia__*",
            "--provider",
            spec.provider,
            "--model",
            spec.model,
        ]
        if session_id is None:
            argv += ["--no-session"]
        else:
            argv += ["--session-dir", str(Path(workdir) / "sessions"), "--session-id", session_id]
        argv.append(prompt)
        proc = subprocess.Popen(
            argv,
            cwd=str(workdir),
            env=build_env(env_extra),
            stdin=subprocess.DEVNULL,  # stdin 关闭：无人值守
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, args=(proc, lines), daemon=True).start()
        usage_total = {"input": 0, "output": 0, "total": 0}
        stop_reason: str | None = None
        error_message: str | None = None
        deadline = time.monotonic() + timeout_seconds
        reaped = False

        def reap() -> None:
            """保证进程被收尸：还在跑就杀，然后有界等待；幂等（正常路径已退出的不再杀）。"""
            nonlocal reaped
            if reaped:
                return
            reaped = True
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=REAP_WAIT_SECONDS)
            except subprocess.TimeoutExpired:  # pragma: no cover - kill 后仍不退出的兜底
                pass

        try:
            while True:
                if cancel.is_set():
                    reap()
                    yield RuntimeEvent("cancelled")
                    return
                try:
                    raw = lines.get(timeout=POLL_SECONDS)
                except queue.Empty:
                    if cancel.is_set():
                        reap()
                        yield RuntimeEvent("cancelled")
                        return
                    if time.monotonic() >= deadline:
                        reap()
                        yield RuntimeEvent("error", text="timeout")
                        return
                    continue
                if raw is None:  # 流关闭：进程应随即退出，等不到就杀掉——wait 必须有界（修复轮 Minor 2）
                    try:
                        proc.wait(timeout=REAP_WAIT_SECONDS)
                    except subprocess.TimeoutExpired:
                        reap()
                        yield RuntimeEvent("error", text="timeout")
                        return
                    break
                update = self._parse(raw)
                if update is None:
                    continue
                kind, event, extra = update
                if kind == "text" or kind == "tool":
                    yield event
                elif kind == "usage":
                    usage_total["input"] += event.usage["input"]
                    usage_total["output"] += event.usage["output"]
                    usage_total["total"] += event.usage["total"]
                    yield event
                    stop_reason = extra.get("stopReason")
                    error_message = extra.get(ERROR_MESSAGE_FIELD)
                if cancel.is_set():
                    reap()
                    yield RuntimeEvent("cancelled")
                    return
            if stop_reason == STOP_REASON_ERROR:
                yield RuntimeEvent("error", text=error_message or "error")
            else:
                yield RuntimeEvent("done", usage=dict(usage_total))
        finally:
            # 消费方提前关闭生成器（GeneratorExit，如页面 SSE 断开）时杀掉进程防遗弃：
            # 锁随上层 with 释放，进程不随它活着（修复轮 Important 1）。正常结束已退出则幂等空转。
            reap()

    def _pump(self, proc, lines: queue.Queue) -> None:
        """Move stdout lines into the queue; None marks end-of-stream. A stalled process just
        blocks here until killed — the main loop decides timeout/cancel, not this thread."""
        assert proc.stdout is not None
        for raw in proc.stdout:
            lines.put(raw)
        lines.put(None)

    @staticmethod
    def _parse(raw: bytes) -> tuple[str, RuntimeEvent, dict] | None:
        """One JSONL record -> (stream kind, event, extra)；extra 只在 usage 事件上带原文消息。"""
        try:
            event = json.loads(raw)
        except ValueError:
            return None  # 诊断行或残行：stdout 只应承载 JSONL，跳过坏行
        if not isinstance(event, dict):
            return None
        kind = event.get("type")
        if kind == "message_update":
            delta_event = event.get("assistantMessageEvent") or {}
            if delta_event.get("type") == TEXT_DELTA_EVENT and delta_event.get(TEXT_DELTA_FIELD):
                return "text", RuntimeEvent("text", text=delta_event[TEXT_DELTA_FIELD]), {}
            return None
        if kind in ("tool_execution_start", "tool_execution_end"):
            tool = str(event.get("toolName") or "")
            if kind == "tool_execution_start":
                return "tool", RuntimeEvent("tool_start", tool=tool), {}
            return "tool", RuntimeEvent("tool_end", tool=tool), {}  # ref 见模块头注释：取不到，留空
        if kind == "message_end":
            message = event.get("message") or {}
            if message.get("role") != "assistant":
                return None
            usage_raw = message.get("usage") or {}
            usage = {
                "input": int(usage_raw.get("input") or 0),
                "output": int(usage_raw.get("output") or 0),
                "total": int(usage_raw.get(USAGE_TOKENS_FIELD) or 0),
            }
            return "usage", RuntimeEvent("usage", usage=usage), message
        return None
