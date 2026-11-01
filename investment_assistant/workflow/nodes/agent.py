from __future__ import annotations

from typing import Any

from investment_assistant.core import stages as stages_module
from investment_assistant.analysis.agent import runner as analysis_agent_runner
from investment_assistant.views.derivation import _check_derivations
from investment_assistant.workflow.state import _moment, _wall, agent_window

def run(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``agent`` node: the budget window opens now (``agent_started_at``, wall time, v2.8 D24)."""
    now, session = _moment(state)
    summary = state['summary']
    opened = None if ctx.legacy else _wall(ctx)().isoformat()
    started, clock = agent_window({**state, 'agent_started_at': opened}, ctx)
    unavailable = analysis_agent_runner.agent_stage(session, now=now, deps=ctx.deps, summary=summary, started=started, clock=clock,
                               acc=acc, timeline_ready=state.get('timeline_ready'), run_id=state.get('run_id'))
    if ctx.recorder is not None:
        if unavailable is not None:    # 不可用压过「空跑成功」：整阶段 skipped（v2.4 §4）
            ctx.recorder.skip('agent', f'agent_unavailable: {unavailable}')
        else:
            _check_derivations(acc, acc.outputs.get('plan_ids', []), summary)
    return {'summary': summary} if opened is None else {'summary': summary, 'agent_started_at': opened}
