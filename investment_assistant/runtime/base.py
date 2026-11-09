"""The runtime interface (spec §9.1 / v3 设计 §15.3): start or continue one conversation, hand it
a prompt, get the answer back as an event stream (text deltas, tool-call start/end, terminal
event, token usage). The page service and the daily job depend on this, never on Pi itself."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# 事件流必须以恰好一个这些种类的事件收尾（fake / pi 两个实现都遵守）。
TERMINAL_KINDS = ("done", "error", "cancelled")

# 仓库根（daily 的 LaunchAgent 以 <repo>/.venv/bin/python -m investment_assistant.daily 运行，
# 运行时层从这里定位 agent 工作区与 MCP 配置要用的仓库绝对路径）。
REPO_ROOT = Path(__file__).resolve().parents[2]


def workspace_file(name: str) -> Path:
    """The agent workspace (spec §9.5): `agent/` in the repository, passed in explicitly by
    the host — never discovered through parent directories."""
    return REPO_ROOT / "agent" / name


@dataclass(frozen=True)
class RuntimeEvent:
    kind: str  # 'start' | 'text' | 'tool_start' | 'tool_end' | 'usage' | 'done' | 'error' | 'cancelled'
    text: str = ""  # start 的 agent_session / text 增量 / error 原因
    tool: str = ""  # tool_start / tool_end 的工具名
    ref: str = ""  # tool_end 的 call:N（从 MCP 服务的返回里取不到时为空）
    usage: dict = field(default_factory=dict)  # usage / done：input, output, total


@dataclass(frozen=True)
class RuntimeSpec:
    runtime: str
    provider: str
    model: str


class Runtime(Protocol):
    name: str

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
    ) -> Iterator[RuntimeEvent]: ...
