"""The ``propose`` node (v2.15, spec §8.3): Saturday optimisation proposals.

It judges its own day, like ``push`` and ``retro``: the ``optimize_proposals`` switch off → ``skipped /
feature_disabled`` (the switch beats ``--force-propose``); not Saturday in Beijing → ``skipped / not_proposal_day``
unless the run was started with ``--force-propose`` (frozen into the state, so a resume keeps it). Then the host
reads the evidence pack; with nothing observed the node succeeds without a model call (``detail.reason =
'no_evidence'``), otherwise one ``agent-optimize-1`` run drafts 0–3 proposals that are checked one by one and
written with the run (``optimize.proposals``). A model that may not run skips the node with
``agent_unavailable: <reason>`` (the connection-lost notice reads it); a rejected or failed run degrades it; a
failure of the node itself is its own ``failed`` row and never stops the chain (``propose`` is the last node)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from investment_assistant.core import redact as families
from investment_assistant.core import stages as stages_module
from investment_assistant.optimize import proposals as optimize_proposals
from investment_assistant.optimize.evidence import is_proposal_day      # the one proposal-day rule (R25)
from investment_assistant.workflow.state import _moment, _stage_finish, _stage_skip, _wall, feature_enabled

FLAG = 'optimize_proposals'
def run_stage(session, *, now: datetime, deps, run_id: str | None, acc: stages_module.StageAcc | None, settings=None,
              clock=None, started: float = 0.0, forced: bool = False) -> dict:
    """The node's work: the drafting pass of ``optimize.proposals`` with the stage accounting."""
    out = optimize_proposals.run_propose(session, now=now, deps=deps, run_id=run_id, settings=settings, clock=clock,
                                         started=started)
    if acc is not None:
        acc.detail['proposals'] = list(out['proposals'])
        acc.detail['dropped'] = list(out['dropped'])
        acc.detail['agent_run_id'] = out['agent_run_id']
        if forced:
            acc.detail['forced'] = True
        if out['status'] == 'no_evidence':
            acc.detail['reason'] = 'no_evidence'
        acc.input(out['evidence_rows'])
        acc.output(len(out['proposals']))
        if out['status'] in ('rejected', 'error'):
            acc.degrade(f"propose_{out['status']}", out['reason'])
    return out


def run(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The graph node: the switch, the day, then the drafting pass (see the module docstring)."""
    cutoff, session = _moment(state)
    summary = state['summary']
    forced = bool(state.get('force_propose'))
    if not feature_enabled(state, FLAG):
        _stage_skip(ctx, 'propose', 'feature_disabled')
        return {'summary': summary}
    if not forced and not is_proposal_day(cutoff):
        _stage_skip(ctx, 'propose', 'not_proposal_day')
        return {'summary': summary}
    now = _wall(ctx)()            # the evidence windows end now: everything the run wrote before this node counts
    try:
        out = run_stage(session, now=now, deps=ctx.deps, run_id=state.get('run_id'), acc=acc,
                        settings=ctx.deps.agent_settings, clock=ctx.clock, started=ctx.clock(), forced=forced)
    except Exception as exc:     # noqa: BLE001 - recorded as the node's failure, never raised into the chain
        reason = families.sanitize_reason(exc)
        summary.setdefault('errors', []).append(f'propose: {reason}')
        _stage_finish(ctx, 'propose', failed=reason or type(exc).__name__)
        return {'summary': summary}
    if out['status'] == 'skipped':
        _stage_skip(ctx, 'propose', f"agent_unavailable: {out['reason']}")
    summary['propose'] = {'status': out['status'], 'reason': out['reason'], 'proposals': out['proposals'],
                          'dropped': out['dropped'], 'agent_run_id': out['agent_run_id']}
    return {'summary': summary}
