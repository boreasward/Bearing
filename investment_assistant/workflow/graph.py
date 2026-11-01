"""The ``daily`` LangGraph workflow (v2.11, ``daily-2``): eleven nodes in a line, checkpoints, node retry, resume.

The stage functions live in ``workflow/nodes``; this module wraps each in a
node that opens the stage's accumulator, runs the body, retries it once after ``NODE_RETRY_WAIT_S`` when it raises,
and writes the node's one ``stage_runs`` row. A node that fails twice writes its ``failed`` row and raises
``NodeFailed``; the executor (``run`` / ``resume``) then books ``skipped / upstream_failed`` rows for the nodes after it.
The checkpoint stays in front of the failed node, so ``resume`` continues exactly there (spec §5).
"""
from __future__ import annotations
import investment_assistant.core.schema as core_schema
import investment_assistant.workflow.repo as workflow_repo

import copy
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

from investment_assistant.data.calendar import add_trading_days, is_trading_day
from investment_assistant.data import collect
from investment_assistant.core import redact as families
from investment_assistant.analysis import pool as pool_module
from investment_assistant.core import tx as service
from investment_assistant.workflow import state as state_helpers
from investment_assistant.workflow.context import NodeContext
from investment_assistant.workflow.nodes import accounts, agent, collect as collect_node, plan, push, regime, retro, review, screen, sec_monitor, signals
from investment_assistant.data.adapters import bars
from investment_assistant.core import stages as stages_module
from investment_assistant.workflow import recorder as stage_recorder
from investment_assistant.core.llm import model as agent_model
from investment_assistant.core import tx as core_tx

DAILY_GRAPH_VERSION = 'daily-2'
NODE_RETRY_WAIT_S = 60
NODES: tuple[str, ...] = stages_module.STAGE_IDS            # the eleven ids of stages-5; the order is the edges
NY = ZoneInfo('America/New_York')
FAULT_ENV = 'INVESTMENT_ASSISTANT_FAULT_ONCE'                # Task 7 drill only: one injected failure of one node
TRIGGERS = ('scheduled', 'manual_batch')


class DailyState(TypedDict, total=False):
    """The graph state (spec §4.2): JSON-able values only; a checkpoint can be loaded by another process."""
    run_id: str
    graph_version: str
    stages_version: str
    trigger: str
    cutoff_at: str
    session: str
    ny_date: str
    trading_day: bool
    explicit: bool
    pool: dict
    held: list[str]
    chain_tickers: list[str]
    collected: list[str]
    universe: dict | None
    manual_pool: dict
    timeline_ready: bool | None
    growth_ready: bool | None
    refs: dict[str, dict]
    agent_started_at: str | None
    summary: dict
    failed_node: str | None
    failure: dict | None




class NodeFailed(RuntimeError):
    """A node failed after its retry (or without one): its ``failed`` row is written, the chain stops."""

    def __init__(self, node: str, reason: str):
        super().__init__(f'{node}: {reason}')
        self.node = node
        self.reason = reason


class ResumeRejected(RuntimeError):
    """A resume the pre-checks refuse (HTTP 409 in the API): ``code`` is ``not_failed`` / ``node_mismatch`` /
    ``resume_window_closed``; ``message`` the Chinese text."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class CheckpointUnavailable(RuntimeError):
    """The checkpoint tables are missing: ``daily`` refuses to run without checkpoints (spec §15)."""


# ---------------------------------------------------------------- pure helpers

def resume_deadline(session: date) -> datetime:
    """A run can be resumed until 17:00 New York of the trading day after its session (spec §5.5)."""
    return datetime.combine(add_trading_days(session, 1), dtime(17, 0), tzinfo=NY)


def effective_rows(rows: list[dict]) -> dict[str, dict]:
    """Per stage the row of the highest ``attempt`` (a row without the field counts as attempt 1; among equals the
    later one wins): the run's ten effective rows (spec D9)."""
    chosen: dict[str, dict] = {}
    for row in rows:
        known = chosen.get(row['stage'])
        if known is None or int(row.get('attempt') or 1) >= int(known.get('attempt') or 1):
            chosen[row['stage']] = row
    return chosen


def run_status(rows: list[dict]) -> str:
    """``failed`` / ``degraded`` / ``success`` from the effective rows (spec D13): any ``failed`` → failed; else any
    ``partial`` / ``degraded`` → degraded; ``skipped`` never matters."""
    statuses = {row['status'] for row in effective_rows(rows).values()}
    if 'failed' in statuses:
        return 'failed'
    if statuses & {'partial', 'degraded'}:
        return 'degraded'
    return 'success'


def _pool_arg(explicit_pool: Any) -> dict:
    if isinstance(explicit_pool, pool_module.Pool):
        return state_helpers._pool_state(explicit_pool)
    if isinstance(explicit_pool, dict):
        return state_helpers._pool_state(state_helpers._pool_from_state(explicit_pool))
    return state_helpers._pool_state(pool_module.Pool.all_core(list(explicit_pool)))


def initial_state(*, run_id: str, trigger: str, now: datetime, explicit_pool: Any = None) -> DailyState:
    """The state a new run starts with. A manual run is always a trading day (it targets the latest settled session);
    a scheduled one on a New York holiday starts with the v2.7 ``skipped`` summary, which the nodes honour."""
    if trigger not in TRIGGERS:
        raise ValueError(f'unknown trigger: {trigger!r}')
    ny_date = now.astimezone(NY).date()
    trading_day = trigger == 'manual_batch' or is_trading_day(ny_date)
    state: DailyState = {
        'run_id': run_id, 'graph_version': DAILY_GRAPH_VERSION, 'stages_version': stages_module.STAGES_VERSION,
        'trigger': trigger, 'cutoff_at': now.isoformat(), 'session': bars.latest_closed_session(now).isoformat(),
        'ny_date': ny_date.isoformat(), 'trading_day': trading_day, 'explicit': explicit_pool is not None,
        'held': [], 'chain_tickers': [], 'collected': [], 'universe': None, 'manual_pool': {},
        'timeline_ready': None, 'growth_ready': None, 'refs': {}, 'agent_started_at': None,
        'summary': ({} if trading_day else {'status': 'skipped', 'reason': 'not a trading day in New York',
                                            'ny_date': ny_date.isoformat()}),
        'failed_node': None, 'failure': None}
    if explicit_pool is not None:
        state['pool'] = _pool_arg(explicit_pool)
    return state


# ---------------------------------------------------------------- the node wrapper

def _json(value: Any) -> Any:
    return service._jsonable(value)


def _fault_pending(stage_id: str, state: dict) -> bool:
    """The one-shot drill (``INVESTMENT_ASSISTANT_FAULT_ONCE=<node>``): only while this run has no row of the node."""
    if os.environ.get(FAULT_ENV) != stage_id:
        return False
    with core_tx._tx() as conn:
        return not any(row['stage'] == stage_id for row in workflow_repo.stage_rows_for_run(conn, state['run_id']))


def node(stage_id: str, body: Callable[[dict, NodeContext, stages_module.StageAcc], dict], *,
         sleep: Callable[[float], None] | None = None) -> Callable[[dict, NodeContext], dict]:
    """Wrap a stage body into a graph node ``(state, ctx) -> state increment`` (spec §5.2).

    The body runs on a copy of the state. When it raises (``service.Unavailable`` excepted: an unreachable database
    fails at once), the node waits ``NODE_RETRY_WAIT_S``, takes a fresh accumulator and runs it once more; the first
    error goes to ``detail.retry``. A second failure writes the ``failed`` row (``aborted: <error>``) and raises
    ``NodeFailed``. One row per node however often it ran."""

    def run(state: dict, ctx: NodeContext) -> dict:
        recorder = ctx.recorder
        wait = sleep or ctx.sleep
        inject = _fault_pending(stage_id, state)
        retry: dict[str, Any] | None = None

        def attempt(acc: stages_module.StageAcc) -> dict:
            if inject:
                raise RuntimeError('fault injection')
            return body(copy.deepcopy(state), ctx, acc)

        def fail(exc: BaseException) -> NodeFailed:
            reason = f'aborted: {families.sanitize_reason(exc) or type(exc).__name__}'
            recorder.finish(stage_id, failed=reason)
            return NodeFailed(stage_id, reason)

        acc = recorder.open(stage_id)
        try:
            delta = attempt(acc)
        except service.Unavailable as exc:
            raise fail(exc) from exc
        except Exception as exc:
            if recorder.has_row(stage_id):          # the body had already booked the stage: nothing to retry
                raise fail(exc) from exc
            retry = {'first_error': families.sanitize_reason(exc) or type(exc).__name__,
                     'waited_s': NODE_RETRY_WAIT_S}
            wait(NODE_RETRY_WAIT_S)
            acc = recorder.restart(stage_id)
            try:
                delta = attempt(acc)
            except Exception as second:
                acc.detail['retry'] = retry
                raise fail(second) from second
        if retry is not None:
            acc.detail['retry'] = retry
        if not recorder.has_row(stage_id):
            recorder.finish(stage_id)
        refs = copy.deepcopy(delta['refs'] if 'refs' in delta else (state.get('refs') or {}))
        outputs = _json(acc.outputs)
        if outputs:
            refs[stage_id] = {**refs.get(stage_id, {}), **outputs}
        return {**delta, 'refs': refs}

    return run


def _on_trading_day(stage_id: str, body: Callable) -> Callable:
    """A holiday skips the node (``not_trading_day``); ``push`` and ``retro`` judge the day themselves."""

    def wrapped(state: dict, ctx: NodeContext, acc: stages_module.StageAcc) -> dict:
        if not state.get('trading_day', True):
            ctx.recorder.skip(stage_id, 'not_trading_day')
            return {}
        return body(state, ctx, acc)

    return wrapped


BODIES: dict[str, Callable] = {
    'sec_monitor': _on_trading_day('sec_monitor', sec_monitor.run),
    'collect': _on_trading_day('collect', collect_node.run),
    'screen': _on_trading_day('screen', screen.run),
    'regime': _on_trading_day('regime', regime.run),
    'signals': _on_trading_day('signals', signals.run),
    'plan': _on_trading_day('plan', plan.run),
    'agent': _on_trading_day('agent', agent.run),
    'review': _on_trading_day('review', review.run),
    'accounts': _on_trading_day('accounts', accounts.run),
    'push': push.run,
    'retro': retro.run,
}


def build_graph(*, checkpointer, ctx_factory: Callable[[], NodeContext]):
    """START → the ten nodes in order → END, compiled with ``checkpointer`` (no conditional edges, spec §4.1)."""
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(DailyState)
    for stage_id in NODES:
        run = node(stage_id, BODIES[stage_id])
        graph.add_node(stage_id, lambda state, run=run: run(state, ctx_factory()))
    graph.add_edge(START, NODES[0])
    for before, after in zip(NODES, NODES[1:]):
        graph.add_edge(before, after)
    graph.add_edge(NODES[-1], END)
    return graph.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------- executing

def _default_checkpointer():
    return agent_model.checkpointer(os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL', ''))


def _wall() -> datetime:
    return collect.wall_clock()       # looked up per call: tests swap the wall clock


def _make_context(state: dict, deps: service.Deps, *, attempt: int, resumed: bool, sleep, notify_cfg,
                  seen: list[dict], config: Any = None, send=None, sec_scan=None) -> NodeContext:
    clock = deps.clock or time.monotonic

    def writer(row: dict) -> None:
        seen.append(row)
        stage_recorder.write_stage_row(row)

    recorder = stage_recorder.StageRecorder(
        state['run_id'], state['trigger'], date.fromisoformat(state['session']), writer=writer, wall_now=_wall,
        clock=clock, version=stages_module.STAGES_VERSION, attempt=attempt, job='daily')
    return NodeContext(recorder=recorder, deps=deps, clock=clock, wall_now=_wall, notify_cfg=notify_cfg,
                       send=send, sec_scan=sec_scan,
                       resumed=resumed, sleep=sleep, record_trigger=state['trigger'], job='daily',
                       config=config, execution_started=_wall())


def _book_abort(ctx: NodeContext, resume_node: str | None, exc: BaseException) -> NodeFailed:
    """An exception outside any node (checkpoint write, state read, the graph itself): book what this execution
    leaves undone so the run still has its ten effective rows — the first node without a row of this execution as
    ``failed / aborted``, the ones after it as ``skipped / upstream_failed`` (writes may themselves fail; the
    recorder swallows that)."""
    reason = f'aborted: {families.sanitize_reason(exc) or type(exc).__name__}'
    start = NODES.index(resume_node) if resume_node in NODES else 0
    pending = [node_id for node_id in NODES[start:] if not ctx.recorder.has_row(node_id)]
    if not pending:
        return NodeFailed(NODES[-1], reason)
    ctx.recorder.finish(pending[0], failed=reason)
    for node_id in pending[1:]:
        ctx.recorder.skip(node_id, 'upstream_failed')
    return NodeFailed(pending[0], reason)


def _execute(state: dict, graph_input: dict | None, *, deps: service.Deps, attempt: int, resumed: bool, sleep,
             notify_cfg, checkpointer_factory, resume_node: str | None = None, config: Any = None,
             send=None, sec_scan=None) -> dict:
    run_id = state['run_id']
    seen: list[dict] = []
    ctx = _make_context(state, deps, attempt=attempt, resumed=resumed, sleep=sleep, notify_cfg=notify_cfg, seen=seen,
                        config=config, send=send, sec_scan=sec_scan)
    graph_config = {'configurable': {'thread_id': run_id}}
    failure: NodeFailed | None = None
    final: dict = state
    started_at = _wall()
    try:
        with (checkpointer_factory or _default_checkpointer)() as saver:
            graph = build_graph(checkpointer=saver, ctx_factory=lambda: ctx)
            try:
                final = graph.invoke(graph_input, graph_config, durability='sync')
            except NodeFailed as exc:
                failure = exc
                final = graph.get_state(graph_config).values
    except Exception as exc:
        # A node already stopped the chain (only reading the state afterwards failed): its failure stands, the nodes
        # after it are booked below. Otherwise the exception itself is the failure.
        if failure is None:
            if agent_model._checkpoint_missing(exc):
                raise CheckpointUnavailable('checkpoint_unavailable') from exc
            failure = _book_abort(ctx, resume_node, exc)
    if failure is not None:
        after = NODES.index(failure.node) + 1
        for stage_id in NODES[after:]:
            ctx.recorder.skip(stage_id, 'upstream_failed')
    summary = dict(final.get('summary') or state.get('summary') or {})
    try:
        with core_tx.read_only_tx() as conn:
            rows = workflow_repo.stage_rows_for_run(conn, run_id)
    except Exception:  # noqa: BLE001 - the status falls back to this execution's own rows
        rows = seen
    status = run_status(rows)
    if failure is not None:
        status = 'failed'
    # The failed node: the one that stopped the chain, else the first node a stage booked as ``failed`` (a whole-stage
    # failure such as ``review`` / ``retro`` carries on without stopping the chain, D13).
    effective = effective_rows(rows)
    node = failure.node if failure is not None else next(
        (n for n in NODES if effective.get(n, {}).get('status') == 'failed'), None)
    info = None
    if node is not None:
        row = effective.get(node) or {}
        info = {'node': node, 'reason': failure.reason if failure is not None else row.get('reason'),
                'retried': bool((row.get('detail') or {}).get('retry')), 'at': _wall().isoformat(),
                'skipped': sum(1 for r in effective.values() if r.get('reason') == 'upstream_failed'),
                'resumable': failure is not None}      # a stage's own ``failed`` row leaves no next node to resume
    summary.update(cutoff_at=state['cutoff_at'], session=state['session'], graph_version=DAILY_GRAPH_VERSION, run_status=status, failed_node=node, resumed=attempt - 1,
                   stage_status={'written': ctx.recorder.written, 'error': ctx.recorder.error})
    if resume_node is not None:
        summary['resume_node'] = resume_node
    if info is not None:
        summary['error'] = f"{info['node']}: {info['reason']}"
    return {'run_id': run_id, 'status': status, 'summary': summary, 'attempt': attempt,
            'trigger': state['trigger'], 'session': state['session'], 'started_at': started_at,
            'failed_node': node, 'failure': info}


def run(*, run_id: str, trigger: str, now: datetime, deps: service.Deps | None = None, explicit_pool=None,
        checkpointer_factory=None, sleep=time.sleep, notify_cfg=None, config=None, send=None, sec_scan=None) -> dict:
    """A new run: the ten nodes from the start. Returns ``{'run_id', 'status', 'summary', 'attempt', 'failed_node',
    'failure', ...}``; ``status`` is ``success`` / ``degraded`` / ``failed`` (D13). Raises ``CheckpointUnavailable``
    when the checkpoint tables are missing."""
    state = initial_state(run_id=run_id, trigger=trigger, now=now, explicit_pool=explicit_pool)
    return _execute(state, dict(state), deps=deps or service.Deps(), attempt=1, resumed=False, sleep=sleep,
                    notify_cfg=notify_cfg, checkpointer_factory=checkpointer_factory, config=config,
                    send=send, sec_scan=sec_scan)


def _checked(run_id: str, node_id: str, *, now: datetime, checkpointer_factory) -> tuple[dict, int]:
    if node_id not in NODES:
        raise ValueError(f'unknown node: {node_id!r}')
    with core_tx.read_only_tx() as conn:
        if not core_schema.daily_ready(conn):
            raise service.Unavailable('migration_028_missing')
        rows = workflow_repo.stage_rows_for_run(conn, run_id)
        attempt = workflow_repo.max_stage_attempt(conn, run_id) + 1
    graph_config = {'configurable': {'thread_id': run_id}}
    try:
        with (checkpointer_factory or _default_checkpointer)() as saver:
            snapshot = build_graph(checkpointer=saver, ctx_factory=lambda: None).get_state(graph_config)
    except Exception as exc:
        if agent_model._checkpoint_missing(exc):
            raise service.Unavailable('checkpoint_unavailable') from exc       # 503, like the other pre-checks
        raise
    values = dict(snapshot.values or {})
    if not rows or not values:
        raise LookupError(f'没有这次运行：{run_id}')
    pending = tuple(snapshot.next)
    if not pending:
        raise ResumeRejected('not_failed', '这次运行没有失败的节点')
    if node_id != pending[0]:
        title = next(stage.title for stage in stages_module.STAGES if stage.id == pending[0])
        raise ResumeRejected('node_mismatch', f'只能从失败的节点 {title}（{pending[0]}）继续')
    # A node the process died in (killed mid-node, no row) is not a failure to resume from: its next row would leave a
    # gap in the attempts. Such a run is rerun in full (known limitation).
    last = effective_rows(rows).get(pending[0])
    if last is None or not (last['status'] == 'failed' or (last['status'] == 'skipped'
                                                          and last.get('reason') == 'upstream_failed')):
        raise ResumeRejected('not_failed', '这次运行没有失败的节点')
    session = date.fromisoformat(values['session'])
    deadline = resume_deadline(session)
    if now >= deadline:
        raise ResumeRejected('resume_window_closed',
                             f'已过 {add_trading_days(session, 1).isoformat()} 纽约 17:00，只能整次重新运行')
    return {'run_id': run_id, 'node': node_id, 'attempt': attempt, 'session': values['session'],
            'cutoff_at': values['cutoff_at'], 'trigger': values['trigger'], 'deadline_at': deadline.isoformat()}, attempt


def resume_check(run_id: str, node: str, *, now: datetime, checkpointer_factory=None) -> dict:
    """The resume pre-checks, writing nothing (spec §5.4): ``LookupError`` (no such run), ``ResumeRejected(code,
    message)`` (``not_failed`` / ``node_mismatch`` / ``resume_window_closed``), ``service.Unavailable`` (migration 028
    missing / checkpoint tables missing / database unreachable), ``ValueError`` (node not of ``stages-4``). Returns ``{'run_id', 'node', 'attempt',
    'session', 'cutoff_at', 'trigger', 'deadline_at'}``."""
    return _checked(run_id, node, now=now, checkpointer_factory=checkpointer_factory)[0]


def resume(run_id: str, node: str, *, now: datetime, deps: service.Deps | None = None, checkpointer_factory=None,
           sleep=time.sleep, notify_cfg=None, config=None, send=None, sec_scan=None) -> dict:
    """Continue a failed run from its failed node: the checks of ``resume_check``, then the graph from the checkpoint
    (the completed nodes do not run again). The new rows carry ``attempt = max + 1``; ``cutoff_at`` and ``session``
    stay the original run's. The caller holds the ``daily:run`` lock."""
    info, attempt = _checked(run_id, node, now=now, checkpointer_factory=checkpointer_factory)
    graph_config = {'configurable': {'thread_id': run_id}}
    with (checkpointer_factory or _default_checkpointer)() as saver:
        values = dict(build_graph(checkpointer=saver, ctx_factory=lambda: None).get_state(graph_config).values)
    return _execute(values, None, deps=deps or service.Deps(), attempt=attempt, resumed=True, sleep=sleep,
                    notify_cfg=notify_cfg, checkpointer_factory=checkpointer_factory, resume_node=info['node'],
                    config=config, send=send, sec_scan=sec_scan)
