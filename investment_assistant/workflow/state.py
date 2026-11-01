from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

from investment_assistant.analysis import pool as pool_module
from investment_assistant.data import collect

def _pool_state(pool_: pool_module.Pool) -> dict[str, Any]:
    return {'core': list(pool_.core), 'extended': list(pool_.extended), 'sectors': dict(pool_.sectors),
            'sources': dict(pool_.sources)}

def _pool_from_state(data: dict[str, Any]) -> pool_module.Pool:
    return pool_module.Pool(core=tuple(data.get('core') or ()), extended=tuple(data.get('extended') or ()),
                            sectors=dict(data.get('sectors') or {}), sources=dict(data.get('sources') or {}))

def _moment(state: dict[str, Any]) -> tuple[datetime, date]:
    """The run's cutoff (every computation's ``now``) and session from the state's ISO strings."""
    return datetime.fromisoformat(state['cutoff_at']), date.fromisoformat(state['session'])

def _stage_skip(ctx, stage_id: str, reason: str) -> None:
    if ctx.recorder is not None:
        ctx.recorder.skip(stage_id, reason)

def _stage_finish(ctx, stage_id: str, failed: str | None = None) -> None:
    if ctx.recorder is not None:
        ctx.recorder.finish(stage_id, failed=failed)

def agent_window(state: dict[str, Any], ctx) -> tuple[float, Callable[[], float]]:
    """``(started, clock)`` for the agent budget (v2.8 §5.8): the window opens when the ``agent`` node starts.

    The state keeps that moment as wall time (a monotonic clock does not cross processes); here it becomes
    ``clock() - (wall_now() - agent_started_at)``. The v2.7 ``run_batch`` shell keeps its own batch-start window."""
    clock = ctx.clock
    if ctx.window_start is not None:
        return ctx.window_start, clock
    opened = state.get('agent_started_at')
    if not opened:
        return clock(), clock
    opened_at = datetime.fromisoformat(opened)
    if ctx.resumed and ctx.execution_started is not None:
        opened_at = max(opened_at, ctx.execution_started)     # D24: a resume counts from the resumed execution
    elapsed = (_wall(ctx)() - opened_at).total_seconds()
    return clock() - max(0.0, elapsed), clock

def _wall(ctx) -> Callable[[], datetime]:
    return ctx.wall_now or collect.wall_clock
