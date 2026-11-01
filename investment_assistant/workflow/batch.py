from __future__ import annotations

import time as _time_module
from datetime import datetime
from typing import Any, Callable

from investment_assistant.core.tx import Deps, Unavailable
from investment_assistant.core.constants import NY
from investment_assistant.data.calendar import is_trading_day
from investment_assistant.data.adapters import bars
from investment_assistant.analysis import pool as pool_module
from investment_assistant.workflow import recorder as stages_module
from investment_assistant.workflow.context import NodeContext
from investment_assistant.workflow.state import _pool_state
from investment_assistant.workflow.nodes.collect import run as stage_collect
from investment_assistant.workflow.nodes.screen import run as stage_screen
from investment_assistant.workflow.nodes.regime import run as stage_regime
from investment_assistant.workflow.nodes.signals import stage_rules
from investment_assistant.workflow.nodes.plan import stage_plan_check
from investment_assistant.workflow.nodes.agent import run as stage_agent
from investment_assistant.workflow.nodes.review import run as stage_review
from investment_assistant.workflow.nodes.retro import run as stage_retro

def run_batch(*, now: datetime, tickers: list[str] | None = None, pool: pool_module.Pool | None = None,
              deps: Deps | None = None, skip_non_trading: bool = False, run_id: str | None = None,
              breadth_tickers: list[str] | None = None,
              record_trigger: str | None = None,
              wall_now: Callable[[], datetime] | None = None,
              stages: stages_module.StageRecorder | None = None,
              timeline_ready: bool | None = None, growth_ready: bool | None = None) -> dict[str, Any]:
    """Collect -> market context -> rules -> units -> briefs for the latest settled session. Idempotent per session.

    Since v2.8 a thin shell: the stage functions above, called in order (the ``daily`` graph calls the same functions
    as nodes). It is what the tests and the ``derivation`` CLI use, and with ``stages=None`` it behaves as in v2.7.

    The batch runs over the tiered watch pool read from the watchlist (holdings
    join regardless of tier); explicit tickers build an all-core pool for tests
    and manual overrides.

    ``record_trigger`` set ('scheduled' / 'manual_batch') holds the per-family
    advisory locks for the collection stage and appends one data_fetch_runs row
    per recorded family afterwards; None keeps the batch exactly as before (v2.3 §6.2).
    ``wall_now`` (default real wall clock, I07) timestamps those rows' start and
    finish; ``now`` keeps its old meaning as trading session, data fetched_at and
    every computation cutoff.
    ``stages`` (v2.4 §6.2) is a stage recorder: accounting next to the existing code for the stages, never changing
    the control flow; None keeps the batch exactly as before.
    ``timeline_ready`` (v2.5 D22, migration 026; read in the first transaction when None) is handed to the
    agent and review stages. The review stage (v2.5 §7) runs after the agent stage; tickers of active
    agent-2 chains that left the pool and holdings are collected but never run through the rules (D21).
    ``growth_ready`` (v2.7 D29, migration 027; read in the first transaction when None) decides whether the retro
    stage (v2.7 §9) runs after the review stage; without it the batch notes ``growth: migration_027_missing`` in
    ``summary['errors']`` and the recorder was a ``stages-2`` one (no retro row). The weekly / monthly review and
    the push are nodes of the ``daily`` graph only.
    """
    deps = deps or Deps()
    clock = deps.clock or _time_module.monotonic
    ny_date = now.astimezone(NY).date()
    if skip_non_trading and not is_trading_day(ny_date):
        if stages is not None:   # 非交易日：push 之前的阶段整段跳过，push 留给任务层（v2.4 §4）
            ids = stages.stage_ids
            stages.skip_many(ids[:ids.index('push')] if 'push' in ids else ids, 'not_trading_day')
        return {'status': 'skipped', 'reason': 'not a trading day in New York', 'ny_date': ny_date.isoformat()}
    session = bars.latest_closed_session(now)
    started = clock()
    explicit = pool is not None or tickers is not None
    state: dict[str, Any] = {
        'run_id': run_id, 'session': session.isoformat(), 'cutoff_at': now.isoformat(), 'ny_date': ny_date.isoformat(),
        'trading_day': True, 'explicit': explicit, 'trigger': record_trigger or 'scheduled',
        'timeline_ready': timeline_ready, 'growth_ready': growth_ready, 'summary': {}}
    if explicit:
        state['pool'] = _pool_state(pool if pool is not None else pool_module.Pool.all_core(tickers))
    ctx = NodeContext(recorder=stages, deps=deps, clock=clock, wall_now=wall_now, notify_cfg=None,
                               record_trigger=record_trigger, job=stages.job if stages is not None else 'workbench',
                               window_start=started, breadth_tickers=breadth_tickers, legacy=True)

    def open_(stage_id: str):
        return stages.open(stage_id) if stages is not None else None

    def finish(stage_id: str) -> None:
        if stages is not None:
            stages.finish(stage_id)

    # v2.4 §4: collect 的窗口从 SPY 采集前开始，止于 fetch 行落库。
    state.update(stage_collect(state, ctx, open_('collect')))
    finish('collect')
    state.update(stage_screen(state, ctx, None if explicit else open_('screen')))
    finish('screen')
    r_acc = open_('regime')
    try:
        state.update(stage_regime(state, ctx, r_acc))
    except Unavailable:
        raise
    except Exception as exc:   # 区制异常：failed，批处理继续（v2.4 §4；图里改为停链，v2.8 §5.2）
        if stages is not None:
            stages.finish('regime', failed=str(exc) or type(exc).__name__)
    else:
        finish('regime')
    sig_acc, plan_acc = open_('signals'), open_('plan')
    state.update({k: v for k, v in stage_rules(state, ctx, sig_acc, plan_acc).items() if k != 'refs'})
    if stages is not None:
        stages.finish('signals')
        stage_plan_check(state, ctx, plan_acc)
        stages.finish('plan')
    a_acc = open_('agent')
    state.update(stage_agent(state, ctx, a_acc))
    finish('agent')
    state.update(stage_review(state, ctx, open_('review')))
    finish('review')
    ro_acc = open_('retro')
    state.update(stage_retro(state, ctx, ro_acc, ticks=False))
    finish('retro')
    summary = state['summary']
    summary['timings']['total'] = round(clock() - started, 1)
    return summary
