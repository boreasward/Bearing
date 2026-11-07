"""The brief session (spec §9.3–9.4): the agent works through the day's agenda inside a total
time budget. Killed, failed or timed-out attempts leave the saved conclusions alone; a retry
picks up whatever the agenda still lists as undone."""

from __future__ import annotations

import threading
import time
from datetime import date, datetime
from pathlib import Path

import investment_assistant.core.tx as core_tx
from investment_assistant.config import AssistantConfig
from investment_assistant.facts.agenda import agenda
from investment_assistant.runtime import lock, mcpconfig
from investment_assistant.runtime.base import (
    REPO_ROOT,
    TERMINAL_KINDS,
    Runtime,
    RuntimeEvent,
    RuntimeSpec,
    workspace_file,
)

# 提示词末尾的固定说明（spec §9.3：范围由待办决定，不由 agent 决定）
TASK_HEADER = "交易日 {session}；先调 `get_agenda`，按它逐项完成；每项完成后再调一次确认"
BUDGET_EXHAUSTED = "预算耗尽"
# 尝试之间的指数退避（修复轮 Minor 3）：0.5s 起、每次翻倍、封顶 30s。真实的尝试本身耗时
# 分钟级，退避几乎不可感；它只约束「每次尝试立即报 error」的运行时不在预算里热转几千次
# （20 分钟预算下最多十几次尝试）。选退避而不是尝试次数上限：上限会误伤慢节奏的正常重试。
BACKOFF_START_SECONDS = 0.5
BACKOFF_CAP_SECONDS = 30.0


def _prompt(session: date) -> str:
    """The workspace files passed in explicitly (spec §9.5), plus the fixed task header."""
    instructions = workspace_file("instructions.md").read_text(encoding="utf-8").strip()
    playbook = workspace_file("playbook.md").read_text(encoding="utf-8").strip()
    return f"{instructions}\n\n{playbook}\n\n{TASK_HEADER.format(session=session.isoformat())}"


def _run_attempt(
    runtime: Runtime, *, prompt: str, workdir: Path, timeout_seconds: float, spec: RuntimeSpec
) -> tuple[RuntimeEvent, dict, bool]:
    """Consume one attempt's event stream; returns (终点事件, 本次用量, 是否运行时自身崩溃).
    A runtime that raises (cannot start, transport died) is a reason to stop the whole session,
    not something to retry inside the same budget (v3 设计验收 2)."""
    usage = {"input": 0, "output": 0, "total": 0}
    terminal = RuntimeEvent("error", text="runtime_ended_without_terminal")
    try:
        for event in runtime.run(
            prompt=prompt,
            workdir=workdir,
            session_id=None,  # 简报每次尝试新进程，不落盘（spec §9.1）
            timeout_seconds=timeout_seconds,  # type: ignore[arg-type]  # 协议签名按接口约定是 int；两个实现都接受秒的小数，剩余预算不舍入
            cancel=threading.Event(),
            spec=spec,
            env_extra={},
        ):
            if event.kind == "usage" and event.usage:
                for name in usage:
                    usage[name] += int(event.usage.get(name, 0) or 0)
            if event.kind in TERMINAL_KINDS:
                terminal = event
    except Exception as exc:  # noqa: BLE001 - 运行时起不来是原因，不是异常
        return RuntimeEvent("error", text=str(exc) or exc.__class__.__name__), usage, True
    return terminal, usage, False


def run_brief_session(
    *,
    session: date,
    cfg: AssistantConfig,
    runtime: Runtime,
    spec: RuntimeSpec,
    now: datetime,
    budget_seconds: int,
    env_file: Path | None = None,
) -> dict:
    """Run attempts until the agenda is complete or the budget is gone; returns
    ``{'attempts', 'complete', 'usage', 'elapsed_seconds', 'error', 'agenda'}`` with usage
    summed over attempts and ``error`` carrying the last attempt's reason (or the budget one)."""
    del now  # 会话内部只看单调钟；参数保留给调用方对齐
    started = time.monotonic()
    attempts = 0
    error: str | None = None
    usage_total = {"input": 0, "output": 0, "total": 0}
    result_agenda: dict = {}
    while True:
        with core_tx.read_only_tx() as conn:
            result_agenda = agenda(conn, session=session, cfg=cfg)
        if result_agenda["complete"]:
            break
        remaining = budget_seconds - (time.monotonic() - started)
        if remaining <= 0:
            error = error or BUDGET_EXHAUSTED
            break
        attempts += 1
        agent_session = f"brief-{session}-{attempts}"
        workdir = lock.runtime_base(cfg) / "briefs" / session.isoformat() / f"attempt-{attempts}"
        workdir.mkdir(parents=True, exist_ok=True)
        mcpconfig.write(
            workdir,
            repo_root=REPO_ROOT,
            session=session,
            mode="brief",
            agent_session=agent_session,
            env_file=env_file,
        )
        terminal, usage, crashed = _run_attempt(
            runtime,
            prompt=_prompt(session),
            workdir=workdir,
            timeout_seconds=min(remaining, budget_seconds),
            spec=spec,
        )
        for name in usage_total:
            usage_total[name] += usage[name]
        error = None  # error 是最后一次尝试的原因；成功尝试清零
        if terminal.kind == "error":
            error = terminal.text or "error"
        elif terminal.kind == "cancelled":
            error = "cancelled"
        if crashed:
            break  # 运行时本身起不来：同预算里重试无意义，记下原因收工
        # 议程未完成即再试一次：先退避，避免即时失败热转（修复轮 Minor 3）
        backoff = min(BACKOFF_START_SECONDS * 2 ** (attempts - 1), BACKOFF_CAP_SECONDS)
        time.sleep(min(backoff, max(0.0, budget_seconds - (time.monotonic() - started))))
    return {
        "attempts": attempts,
        "complete": result_agenda.get("complete", False),
        "usage": usage_total,
        "elapsed_seconds": time.monotonic() - started,
        "error": error,
        "agenda": result_agenda,
    }
