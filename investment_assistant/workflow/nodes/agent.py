from __future__ import annotations

from typing import Any

from investment_assistant.core import stages as stages_module
from investment_assistant.core import redact as families
from investment_assistant.core.constants import AGENT_DEADLINE_S
from investment_assistant.core.tx import Unavailable
from investment_assistant.analysis import research_requests
from investment_assistant.analysis.agent import runner as analysis_agent_runner
from investment_assistant.views.derivation import _check_derivations
from investment_assistant.workflow.state import _moment, _wall, agent_window, breadth_sample

def _tail(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None, *, now, session, summary: dict[str, Any],
          started: float, clock, sample=None) -> None:
    """After the units: stale dossiers closed (D28), research requests FIFO, then the data requests (§5.5), each inside what is left of the
    shared budget. A failure of a tail is a ``summary['errors']`` line; the units already written stand."""
    features = state.get('features')              # None: no frozen run state (the v2.7 shell) → current flags
    research = {'requests': 0}
    auto_closed = 0
    requests = dict.fromkeys(('queued', 'fetched', 'failed', 'deferred', 'reruns'), 0)
    try:                        # D28 housekeeping first: a stale dossier is closed, not drained
        auto_closed = research_requests.close_stale(now=now)
    except Unavailable:
        raise
    except Exception as exc:    # noqa: BLE001 - housekeeping never fails the agent stage
        summary['errors'].append(f'research_auto_close: {families.sanitize_reason(exc)}')
    try:
        research = analysis_agent_runner.drain_research_requests(
            session, now=now, deps=ctx.deps, summary=summary,
            budget_left_s=AGENT_DEADLINE_S - (clock() - started), features=features)
    except Unavailable:
        raise
    except Exception as exc:    # noqa: BLE001 - the dossiers never fail the agent stage
        summary['errors'].append(f'research_requests: {families.sanitize_reason(exc)}')
    try:
        requests = analysis_agent_runner.process_data_requests(
            session, run_id=state.get('run_id'), now=now, deps=ctx.deps, features=features,
            budget_left_s=AGENT_DEADLINE_S - (clock() - started), breadth_tickers=sample)
    except Unavailable:
        raise
    except Exception as exc:    # noqa: BLE001 - nor do the supplementary fetches
        summary['errors'].append(f'data_requests: {families.sanitize_reason(exc)}')
    summary.setdefault('agent', {})['research_requests'] = research['requests']
    summary['agent']['research_auto_closed'] = auto_closed
    summary['agent']['data_requests'] = requests
    if acc is not None:
        acc.detail['research_requests'] = research['requests']
        acc.detail['research_auto_closed'] = auto_closed
        acc.detail['data_requests'] = dict(requests)


def run(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``agent`` node: the budget window opens now (``agent_started_at``, wall time, v2.8 D24).

    v2.13 §10/D26: after the units, the remaining shared budget drains the research-request queue
    (FIFO) and then the data-request queue (§5.5); D42 lets the review tail reuse the same handler."""
    now, session = _moment(state)
    summary = state['summary']
    opened = None if ctx.legacy else _wall(ctx)().isoformat()
    started, clock = agent_window({**state, 'agent_started_at': opened}, ctx)
    sample = breadth_sample(state, ctx)             # R15: the regime node's sample, frozen into the agent tools
    unavailable = analysis_agent_runner.agent_stage(session, now=now, deps=ctx.deps, summary=summary, started=started, clock=clock,
                               acc=acc, timeline_ready=state.get('timeline_ready'), run_id=state.get('run_id'),
                               features=state.get('features'), breadth_tickers=sample)
    if unavailable is None:
        _tail(state, ctx, acc, now=now, session=session, summary=summary, started=started, clock=clock,
              sample=sample)
    if ctx.recorder is not None:
        if unavailable is not None:    # 不可用压过「空跑成功」：整阶段 skipped（v2.4 §4）
            ctx.recorder.skip('agent', f'agent_unavailable: {unavailable}')
        else:
            _check_derivations(acc, acc.outputs.get('plan_ids', []), summary)
    return {'summary': summary} if opened is None else {'summary': summary, 'agent_started_at': opened}
