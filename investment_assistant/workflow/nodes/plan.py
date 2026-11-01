from __future__ import annotations

from typing import Any

from investment_assistant.core import stages as stages_module
from investment_assistant.analysis import units as analysis_units
from investment_assistant.views.derivation import _check_derivations


def run(state, ctx, acc):
    refs = dict(state.get('refs') or {})
    frozen = refs.pop('plan_acc', None)
    if frozen:
        acc.restore(frozen)
    return {**stage_plan_check(state, ctx, acc), 'refs': refs}

def stage_plan_check(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``plan`` node's body: re-derive the plans the rule loop wrote (v2.4 §8). ``acc`` already carries the loop's
    accounting (the shell passes the live accumulator, the graph restores it from ``refs['plan_acc']``)."""
    summary = state['summary']
    if acc is not None:
        acc.detail.setdefault('retriggers', analysis_units._empty_retriggers())     # written by the rule loop; a node restored without it says 0
        _check_derivations(acc, acc.outputs['inserted'] + acc.outputs['existing'], summary)
    return {'summary': summary}
