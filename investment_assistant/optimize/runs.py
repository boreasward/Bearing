"""The shared model-run shell of the two optimize roles (``agent-optimize-1`` proposals, ``agent-impl-brief-1``
briefs): availability, one single-phase run on the role's frozen tool set, and the run row.

The ``get_roadmap`` tool is bound here, at the optimize host, into the run's immutable ``ToolContext`` (R13): the
analysis layer's tool sets only name it. A missing binding fails before the model is called; the callback never
adds a tool that is not in the role's whitelist, and it reads every record as of the run's cutoff."""
from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from investment_assistant.analysis import repo as analysis_repo
from investment_assistant.analysis.agent import toolsets
from investment_assistant.analysis.agent.graph import _single_run
from investment_assistant.analysis.agent.tools import Recorder, ToolContext
from investment_assistant.core import tx as core_tx
from investment_assistant.core.constants import NY
from investment_assistant.core.llm import model as agent_model
from investment_assistant.core.llm.model import _agent_run_row
from investment_assistant.optimize import roadmap

NO_UNIT = {'ticker': None, 'signal_date': None, 'pool': None}      # optimize / brief runs belong to no unit


def settings_for(deps):
    return deps.agent_settings or agent_model.settings_from_env()


def unavailable(settings, *, now: datetime, clock: Callable[[], float] | None = None, started: float = 0.0,
                deadline_s: float | None = None) -> str | None:
    """Why no model call may start (the node's skip reason), or None: the connection, the stage budget and the
    daily token cap — the same three the portfolio manager checks."""
    reason = agent_model.unavailable_reason(settings)
    if reason is None and deadline_s is not None and clock is not None and clock() - started >= deadline_s:
        reason = 'stage_budget'
    if reason is None and settings.daily_tokens is not None:
        with core_tx._tx() as conn:
            spent = analysis_repo.agent_tokens_on(conn, now.astimezone(NY).date())
        if spent >= settings.daily_tokens:
            reason = 'token_budget'
    return reason


def run_phase(*, phase: str, toolset: str, system: str, human: str, check: Callable[[str], dict], deps, settings,
              now: datetime, session: date, run_id: str) -> tuple[dict, str | None]:
    """One checked run of the role on ``toolset``: ``({status, reason, output, calls, input_tokens, output_tokens},
    detail)``. A model failure is classified into ``status='error'`` (never raised); a missing host binding raises
    ``ToolError`` before anything is sent to the model."""
    ctx = ToolContext(ticker='', session=session, cutoff_at=now, read=core_tx._read_only,
                      bindings={'get_roadmap': roadmap.get_roadmap})
    toolsets.require_bindings(ctx, toolset)
    tools = toolsets.tools_for(toolset)
    recorder = Recorder(phase=phase)
    result: dict[str, Any] = {'status': 'error', 'reason': None, 'output': None, 'calls': [],
                              'input_tokens': 0, 'output_tokens': 0}
    detail = None
    try:
        factory = deps.agent_checkpointer or (
            lambda: agent_model.checkpointer(os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL', '')))
        with factory() as checkpointer:
            result = _single_run(
                phase=phase, ctx=ctx, model=deps.agent_model if deps.agent_model is not None
                else agent_model.chat_model(settings),
                checkpointer=checkpointer, run_id=run_id, now=lambda: now, tools=tools, recorder=recorder,
                check=check, system=system, human=human)
    except Exception as exc:    # noqa: BLE001 - a failed model run is a recorded outcome, not an exception
        reason, detail = agent_model.classify_error(exc, settings.api_key)
        result.update(status='error', reason=reason, calls=recorder.calls)
    return result, detail


def run_row(*, mode: str, version: str, settings, now: datetime, run_id: str, result: dict, detail: str | None,
            output: dict | None) -> dict:
    """The ``agent_runs`` row of one finished run (no ticker, signal date, pool or plan)."""
    return _agent_run_row(NO_UNIT, settings=settings, attempt=1, now=now, status=result['status'], run_id=run_id,
                          reason=result.get('reason'), detail=detail, output=output,
                          input_tokens=result.get('input_tokens') or 0, output_tokens=result.get('output_tokens') or 0,
                          mode=mode, agent_version=version)


def new_run_id() -> str:
    return str(uuid.uuid4())
