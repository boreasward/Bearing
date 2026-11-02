"""Analysis model use cases and their atomic writes."""
from __future__ import annotations

import os
import time
import uuid
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5

from investment_assistant.analysis import briefs, synthesis, units
from investment_assistant.analysis import repo as analysis_repo
from investment_assistant.analysis.agent import evidence as agent_evidence
from investment_assistant.analysis.agent import graph as agent_graph
from investment_assistant.analysis.agent import pm as agent_pm
from investment_assistant.analysis.agent import tools as agent_tools
from investment_assistant.core import dbutil as core_dbutil
from investment_assistant.core import features as core_features
from investment_assistant.core import knowledge, locks
from investment_assistant.core import schema as core_schema
from investment_assistant.core import texts as labels
from investment_assistant.core import tx as core_tx
from investment_assistant.core.constants import (
    ACCOUNTS_AGENT_DEADLINE_S,
    AGENT_DEADLINE_S,
    BENCHMARK,
    CONNECTION_LOST,
    DATA_REQUEST_MIN_S,
    NY,
    PM_DEADLINE_S,
    RESEARCH_DEADLINE_S,
)
from investment_assistant.core.llm import model as agent_model
from investment_assistant.core.llm.model import _agent_run_row, _checkpoint_missing
from investment_assistant.core.tx import Deps, Unavailable, _read_only
from investment_assistant.core.versions import AGENT_RUN_VERSION, PM_AGENT_VERSION
from investment_assistant.data import fetch_policy as families
from investment_assistant.data import repo as data_repo
from investment_assistant.data.adapters import bars
from investment_assistant.execution import accounts, timeline
from investment_assistant.execution import portfolio_check as portfolio_evidence
from investment_assistant.execution import chains as execution_chains
from investment_assistant.execution import plan_context as execution_plan_context
from investment_assistant.execution import repo as execution_repo
from investment_assistant.execution.paper import accounts as execution_paper_accounts

AGENT_MAX_ATTEMPTS = 3
REVIEW_MAX_ATTEMPTS = 3
EMPTY_REASON = '（无理由）'

def _brief_run(run: dict) -> dict:
    """The run as briefs.for_run should see it. briefs.for_run takes contra from ``counter.risks`` (the
    agent-1 shape); an agent-2 brief's contra is the first per-thesis counter instead (v2.5 Task 6), so the
    agent-2 counter is presented with ``counter.counter[:1]`` in that slot. Other runs pass through."""
    counter = run.get('counter')
    if (run.get('agent_version') not in (agent_graph.AGENT_V2, agent_graph.AGENT_V3, AGENT_RUN_VERSION)
            or not isinstance(counter, dict)):
        return run
    return {**run, 'counter': {'status': counter.get('status'), 'risks': list(counter.get('counter') or [])[:1]}}


def _skip(unit: dict, reason: str, *, now: datetime, settings, attempt: int, mode: str = 'propose') -> dict:
    """A skipped unit still gets its agent_runs row: every candidate has an answer or
    a reason. A skipped main-list unit also gets its rule-only brief (§7.3)."""
    run_row = _agent_run_row(unit, settings=settings, attempt=attempt, now=now,
                             status='skipped', reason=reason, mode=mode)
    with core_tx._tx() as conn:
        record = analysis_repo.insert_agent_run(conn, run_row, [])
        if unit['pool'] == 'main':
            analysis_repo.store_brief(conn, briefs.for_run(run_row, [], unit=unit,
                                                        candidates=unit['candidates']))
        return record


def _merged_brief(unit: dict, *, now: datetime) -> dict[str, Any]:
    """The rule-only brief of a unit merged into an active chain (v2.9 §4.3): the unit is not proposed, so nothing
    but the rules speaks for it; no agent run stands behind it."""
    return {'ticker': unit['ticker'], 'signal_date': unit['signal_date'], 'model': None, 'prompt_version': 'rules-1',
            'cutoff_at': now, 'input_refs': [], 'output': briefs.rule_summary(unit, unit['candidates']),
            'status': 'fallback', 'error': agent_reason_text('active_chain'), 'agent_run_id': None}


def agent_reason_text(reason: str) -> str:
    from investment_assistant.analysis.agent import REASON_TEXT

    return REASON_TEXT.get(reason, reason)


def _previous_chain_facts(conn, ticker: str, signal_date: date, *, now: datetime) -> dict[str, Any]:
    """``agent_evidence.previous_chain_block``'s input for the ticker's chains opened before ``signal_date``, read as the
    cutoff ``now`` knew them (spec §4.6): fills with ``execution_trades.list_trades(cutoff=now)`` (R11), bars up to the last session
    closed by ``now``, the version plan rows with the model's ``changes`` text, the closed segments of the chain's
    fills with the sale's recording time (so a sale recorded after the cutoff cannot show)."""


    session = bars.latest_closed_session(now)
    ctx = execution_chains.ticker_context(conn, ticker, now=now)
    sale_recorded = {t['id']: t['recorded_at'] for t in ctx['trades'] if t['side'] == 'sell'}
    items = []
    for chain in ctx['chains']:
        if chain['signal_date'] >= signal_date:
            continue
        item = execution_chains.read_chain_facts(conn, chain, session=session, now=now, context=ctx, stamped=True)
        if item is None:
            continue
        rows = []
        for version in item['chain']['versions']:
            plan = execution_repo.plan_by_id(conn, version['plan_id'])
            run = data_repo.agent_run_by_id(conn, version['agent_run_id'])
            rows.append({**plan, 'version_no': version['version_no'], 'created_at': version['created_at'],
                         'changes': ((run or {}).get('output') or {}).get('changes') or []})
        buy_ids = {str(b['id']) for b in item['buys']}
        segments = [{**seg, 'sell_recorded_at': sale_recorded.get(seg['sell_trade_id'])}
                    for seg in ctx['book']['closed'] if str(seg['buy_trade_id']) in buy_ids]
        items.append({'chain': item['chain'], 'facts': item['facts'], 'plans': rows, 'segments': segments})
    return {'as_of_session': session, 'bars': data_repo.read_bars(conn, ticker, session, cutoff_at=now, limit=400),
            'spy_bars': data_repo.read_bars(conn, BENCHMARK, session, cutoff_at=now, limit=400),
            'splits': data_repo.split_events(conn, ticker), 'chains': items}


def _first_version_guard(conn, unit: dict, *, now: datetime) -> dict[str, Any] | None:
    """The check a first version needs under the ``chain-open:<ticker>`` lock just before it is written (v2.9
    §4.7): None when the ticker has no active chain, else ``{'found': the active chain}``. Judged at
    ``max(signal date, latest closed session)``. A merge row of the unit counts only through the chain it points
    at being active (R17: a unit merged into a chain that has ended may open its own)."""


    found = execution_chains.active_chain_of(conn, unit['ticker'],
                                        session=max(unit['signal_date'], bars.latest_closed_session(now)), now=now)
    return {'found': found} if found is not None else None


def run_agent_unit(unit: dict, *, now: datetime, deps: Deps, settings, attempt: int,
                    run_id: str | None = None, features: dict | None = None,
                    carried_research: dict | None = None, previous_run_id: str | None = None) -> dict:
    """One unit through the agent-3 graph: the chain's first version (v2.5 §5.1, v2.9 §5.5). Plan, run, calls,
    version 0 and the brief share one transaction taken under the ``chain-open:<ticker>`` lock, which first checks
    again that the ticker has no active chain and the unit was not merged (§4.7; otherwise the proposal is recorded
    as a ``skipped / active_chain`` run with its tokens and the rule-only brief, and a merge row is written when
    the active chain is an earlier one). The proposer's message carries the 「同标的上一条链」 block (§4.6); its
    text and hash are kept in the run's ``output`` as ``input_digest.previous_chain`` (``agent_runs`` has no digest
    column; ``detail`` is the error text of a failed run and stays empty for an ok one). A model or checkpoint
    failure, or any failure while writing, lands as this unit's error row and never propagates. Needs migration
    026 (and 029 for the guard); ``run_id`` is the daily run's, stamped on a merge row written here."""
    from investment_assistant.config import load_config

    if features is None:      # D3: without a frozen run state, read the current flags (031-gated)
        with core_tx._tx() as conn:
            features = core_features.frozen(conn) if core_schema.five_layers_ready(conn) else {}
    research_flag = features.get('company_researcher', True)
    agent_run_id = str(uuid.uuid4())
    ticker, signal_date = unit['ticker'], unit['signal_date']

    def row(status: str, **kwargs) -> dict:
        return _agent_run_row(unit, settings=settings, attempt=attempt, now=now, status=status,
                              run_id=agent_run_id, agent_version=AGENT_RUN_VERSION, **kwargs)

    def write(run_row: dict, calls: list[dict] | None = None,
              data_requests: list[dict] | None = None) -> dict:
        with core_tx._tx() as conn:
            record = analysis_repo.insert_agent_run(conn, run_row, calls or [],
                                                    data_requests=data_requests or [])
            if unit['pool'] == 'main':   # §7.1: main-list brief in the run's transaction
                analysis_repo.store_brief(conn, briefs.for_run(_brief_run(run_row), calls or [], unit=unit,
                                                            candidates=unit['candidates']))
            return record

    try:
        with core_tx._tx() as conn:
            bars_ = data_repo.read_bars(conn, ticker, signal_date, cutoff_at=now)
            block = agent_evidence.previous_chain_block(_previous_chain_facts(conn, ticker, signal_date, now=now), now=now)
        digest = {'input_digest': {'previous_chain': agent_evidence.previous_chain_digest(block)}}
        last_close = Decimal(str(bars_[-1]['close']))
        ctx = agent_tools.ToolContext(ticker=ticker, session=signal_date, cutoff_at=now, read=_read_only)
        with ExitStack() as stack:
            try:
                factory = deps.agent_checkpointer or (
                    lambda: agent_model.checkpointer(os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL', '')))
                checkpointer = stack.enter_context(factory())
            except Exception as exc:
                _, detail = agent_model.classify_error(exc, settings.api_key)
                return write(row('error', reason='checkpoint_unavailable', detail=detail))
            model_obj = deps.agent_model if deps.agent_model is not None else agent_model.chat_model(settings)
            try:
                result = agent_graph.run_unit_v2(unit=unit, ctx=ctx, model=model_obj,
                                                 checkpointer=checkpointer, run_id=agent_run_id,
                                                 last_close=last_close, as_of_session=signal_date,
                                                 now=lambda: now, version=AGENT_RUN_VERSION,
                                                 research=research_flag,
                                                 carried_research=carried_research,
                                                 previous_run_id=previous_run_id,
                                                 previous_chain=block)
            except Unavailable:
                raise
            except Exception as exc:
                reason, detail = agent_model.classify_error(exc, settings.api_key)
                if _checkpoint_missing(exc):
                    reason = 'checkpoint_unavailable'
                return write(row('error', reason=reason, detail=detail))
        if result['status'] == 'ok':
            with core_tx._tx() as conn:   # v2.5 §5.1: plan → run → calls → version 0 → brief, one transaction
                core_dbutil.lock(conn, f'chain-open:{ticker}')
                guard = _first_version_guard(conn, unit, now=now) if core_schema.executable_ready(conn) else None
                if guard is not None:       # §4.7: a chain opened (or the unit merged) since the plan node ran
                    found = guard.get('found')
                    if (found is not None and found['chain']['signal_date'] < signal_date
                            and execution_repo.retrigger_for_unit(conn, ticker, signal_date) is None):
                        baseline = execution_repo.rule_plan(conn, ticker, signal_date)
                        execution_repo.insert_chain_retrigger(conn, {
                            'ticker': ticker, 'unit_signal_date': signal_date,
                            'chain_signal_date': found['chain']['signal_date'], 'chain_state': found['chain_state'],
                            'rule_ids': [c['rule_id'] for c in unit['candidates']],
                            'baseline_plan_id': baseline['id'] if baseline else None,
                            'entry_cap': timeline.retrigger_cap(found['chain_state'], signal_date),
                            'run_id': run_id or 'agent', 'timeline_version': timeline.TIMELINE_VERSION,
                            'created_at': now})
                    skipped = row('skipped', reason='active_chain', input_tokens=result['input_tokens'],
                                  output_tokens=result['output_tokens'])
                    record = analysis_repo.insert_agent_run(conn, skipped, result['calls'],
                                                            data_requests=[{**r, 'run_id': run_id} for r in result.get('data_requests') or []])
                    if unit['pool'] == 'main':
                        analysis_repo.store_brief(conn, briefs.for_run(_brief_run(skipped), result['calls'], unit=unit,
                                                                    candidates=unit['candidates']))
                    return record
                equity_source = execution_plan_context.sizing_equity(conn, signal_date, now=now)
                plan = timeline.build_version_plan(
                    output=result['output'], agent_run_id=agent_run_id, model=settings.model, ticker=ticker,
                    signal_date=signal_date, as_of_session=signal_date, version_no=0, supersedes_plan_id=None,
                    mode='entry', bars=bars_,
                    earnings_row=data_repo.latest_earnings(conn, ticker, cutoff_at=now),
                    snapshot=equity_source,
                    exposure=execution_plan_context.exposure(conn, signal_date, cutoff_at=now, ticker=ticker),
                    params=load_config().workbench,
                    context=execution_plan_context.plan_context(conn, ticker, signal_date, now=now), version=timeline.AGENT_V3)
                execution_plan_context.mark_sizing_source(plan, equity_source)
                plan_id = execution_repo.store_plan(conn, plan)['record']['id']
                run_row = row('ok', plan_id=plan_id, output={**result['output'], **digest},
                              counter=result['counter'], input_tokens=result['input_tokens'],
                              output_tokens=result['output_tokens'])
                record = analysis_repo.insert_agent_run(conn, run_row, result['calls'],
                                                        data_requests=[{**r, 'run_id': run_id} for r in result.get('data_requests') or []])
                execution_repo.insert_plan_version(conn, {
                    'plan_id': plan_id, 'ticker': ticker, 'signal_date': signal_date, 'version_no': 0,
                    'supersedes_plan_id': None, 'origin': 'signal', 'agent_run_id': agent_run_id,
                    'cutoff_at': run_row['cutoff_at'], 'as_of_session': signal_date, 'diff': None,
                    'schema_version': timeline.AGENT_V3})
                # v2.7 D18: the knowledge this plan cited, in the plan's own transaction (none: nothing written)
                analysis_repo.insert_knowledge_citations(conn, [
                    {**cited, 'agent_run_id': agent_run_id, 'plan_id': plan_id}
                    for cited in knowledge.knowledge_citations(
                        output=result['output'], counter=result['counter'], mode='propose', calls=result['calls'])])
                if unit['pool'] == 'main':
                    analysis_repo.store_brief(conn, briefs.for_run(_brief_run(run_row), result['calls'], unit=unit,
                                                                candidates=unit['candidates']))
                return record
        return write(row(result['status'], reason=result['reason'],
                         output={**result['output'], **digest} if result['output'] else result['output'],
                         counter=result['counter'], input_tokens=result['input_tokens'],
                         output_tokens=result['output_tokens']), result['calls'],
                     data_requests=[{**r, 'run_id': run_id} for r in result.get('data_requests') or []])
    except Unavailable:
        raise
    except Exception as exc:   # the unexpected: this unit only, never the batch
        reason, detail = agent_model.classify_error(exc, settings.api_key)
        return write(row('error', reason=reason, detail=detail))


def run_interpret_unit(unit: dict, *, now: datetime, deps: Deps, settings, attempt: int) -> dict:
    """One unit through the interpret graph (§7.2); error classification mirrors
    run_agent_unit. The run (mode='interpret', no plan) and its brief share
    one transaction; model exceptions become this unit's error row."""
    run_id = str(uuid.uuid4())
    ticker, signal_date = unit['ticker'], unit['signal_date']

    def row(status: str, **kwargs) -> dict:
        return _agent_run_row(unit, settings=settings, attempt=attempt, now=now, status=status,
                              run_id=run_id, mode='interpret', **kwargs)

    def write(run_row: dict, calls: list[dict] | None = None) -> dict:
        with core_tx._tx() as conn:
            record = analysis_repo.insert_agent_run(conn, run_row, calls or [])
            analysis_repo.store_brief(conn, briefs.for_run(run_row, calls or [], unit=unit,
                                                        candidates=unit['candidates']))
            return record

    try:
        ctx = agent_tools.ToolContext(ticker=ticker, session=signal_date, cutoff_at=now, read=_read_only)
        with ExitStack() as stack:
            try:
                factory = deps.agent_checkpointer or (
                    lambda: agent_model.checkpointer(os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL', '')))
                checkpointer = stack.enter_context(factory())
            except Exception as exc:
                _, detail = agent_model.classify_error(exc, settings.api_key)
                return write(row('error', reason='checkpoint_unavailable', detail=detail))
            model_obj = deps.agent_model if deps.agent_model is not None else agent_model.chat_model(settings)
            try:
                result = agent_graph.run_interpret(unit=unit, ctx=ctx, model=model_obj,
                                                   checkpointer=checkpointer, run_id=run_id,
                                                   now=lambda: now)
            except Unavailable:
                raise
            except Exception as exc:
                reason, detail = agent_model.classify_error(exc, settings.api_key)
                if _checkpoint_missing(exc):
                    reason = 'checkpoint_unavailable'
                return write(row('error', reason=reason, detail=detail))
        return write(row(result['status'], reason=result['reason'], output=result['output'],
                         input_tokens=result['input_tokens'], output_tokens=result['output_tokens']),
                     result['calls'])
    except Unavailable:
        raise
    except Exception as exc:   # the unexpected: this unit only, never the batch
        reason, detail = agent_model.classify_error(exc, settings.api_key)
        return write(row('error', reason=reason, detail=detail))


def agent_stage(session: date, *, now: datetime, deps: Deps, summary: dict[str, Any],
                 started: float, clock: Callable[[], float],
                 acc: Any | None = None, timeline_ready: bool | None = None,
                 run_id: str | None = None, features: dict | None = None) -> str | None:
    """The agent track after all rule plans (§8.1): skip rules, then one unit at a time.

    v2.9 §4.3: a unit the `plan` node merged into an active chain (a ``chain_retriggers`` row) is not proposed — no
    run row, stage skip ``active_chain`` naming the chain — and gets the rule-only brief; ``summary['agent']
    ['merged']`` maps ticker -> chain signal date. ``run_id`` (the daily run's) stamps a retrigger row the
    first-version guard has to write itself (§4.7).

    ``acc`` (v2.4 §4) collects the stage accounting next to ``record`` and the skip branches. Returns the
    unavailable reason (disabled / not_configured) or None, so the caller can close an unavailable stage
    as skipped rather than as an empty success.

    ``timeline_ready`` (migration 026; read here when None): without it no first version can be written,
    so every propose unit gets a ``skipped / migration_026_missing`` run (main-list units still get their
    rule-only brief), interpretations run as usual, the stage is degraded and ``summary['errors']`` gets
    ``timeline: migration_026_missing`` once (v2.5 §6.5)."""
    t_agent = clock()
    settings = deps.agent_settings if deps.agent_settings is not None else agent_model.settings_from_env()
    if settings.config_error:
        summary['errors'].append(f'agent_config: {settings.config_error}')
    unavailable = agent_model.unavailable_reason(settings)
    ny_date = now.astimezone(NY).date()
    runs: dict[str, str] = {}
    briefs: dict[str, str] = {}
    tokens = {'input': 0, 'output': 0}
    quota_exhausted = False
    connection_lost: str | None = None     # §8.8: set by the first unit that finds the connection / model unusable

    def record(unit: dict, run: dict) -> None:
        nonlocal quota_exhausted, connection_lost
        if run.get('mode', 'propose') == 'propose':    # 7.3: runs only records propose
            runs[unit['ticker']] = run['status'] if run['status'] in ('ok', 'pass') else (
                f"{run['status']}: {run['reason']}")
        if unit['pool'] == 'main':
            # 7.3: agent.briefs is the run view; the top-level briefs key carries the brief row's status
            briefs[unit['ticker']] = run['status'] if run['status'] in ('ok', 'pass') else (
                f"{run['status']}: {run['reason']}")
            summary['briefs'][unit['ticker']] = brief_status(run)
        tokens['input'] += run['input_tokens'] or 0
        tokens['output'] += run['output_tokens'] or 0
        if acc is not None:
            acc.ref('agent_run_ids', run['id'])
            if run['status'] in ('ok', 'pass'):
                acc.output()
            if run['status'] == 'ok' and run.get('mode', 'propose') == 'propose' and run.get('plan_id') is not None:
                acc.ref('plan_ids', run['plan_id'])      # 只收本次 ok 运行新增的 Agent 计划
            elif run['status'] == 'skipped':
                acc.skip(run['reason'], unit['ticker'])
            elif run['status'] == 'error':
                acc.fail(unit['ticker'], run['reason'])
        if run['status'] == 'error':
            summary['sources']['agent'].append(unit['ticker'])
            if run['reason'] == 'quota_exhausted':
                quota_exhausted = True
            elif run['reason'] in CONNECTION_LOST:
                connection_lost = run['reason']

    def brief_status(run: dict) -> str:
        mode, status = run.get('mode', 'propose'), run['status']
        if mode == 'propose' and status in ('ok', 'pass'):
            return 'ok'
        if mode == 'interpret' and status == 'ok':
            return 'ok'
        if mode == 'interpret' and status == 'rejected':
            return 'error'
        return 'fallback'

    def run_one(unit: dict, mode: str, runner: Callable) -> None:
        """The three shared gates (§7.3: deadline, provider, quota, token budget);
        completion and attempts are counted per mode."""
        try:
            with core_tx._tx() as conn:
                history = analysis_repo.agent_runs_for(conn, unit['ticker'], unit['signal_date'], mode=mode)
                # D26: only the runs after the unit's latest reset count towards ``max_attempts``; the history
                # still counts for the ``attempt`` number, which keeps counting.
                resets = (analysis_repo.attempt_resets_for(conn, unit['ticker'], unit['signal_date'], mode)
                          if resets_ready else [])
            prior = [r for r in history if not resets or r['created_at'] > resets[0]['created_at']]
            done = ('ok', 'pass') if mode == 'propose' else ('ok',)
            if any(r['status'] in done for r in history) or (
                    mode == 'propose' and any(r['status'] == 'skipped' and r['reason'] == 'active_chain'
                                              for r in prior)):
                # a proposal set aside by the first-version guard (v2.9 §4.7) is done for the session: another
                # run would pay for the same model call and be set aside again (a reset re-opens it)
                if acc is not None:
                    acc.skip('already_completed', unit['ticker'])
                return
            if any(r['status'] == 'skipped' and r['reason'] == 'max_attempts' for r in prior):
                if acc is not None:
                    acc.skip('max_attempts', unit['ticker'])
                return
            attempt = len(history) + 1
            if clock() - started >= AGENT_DEADLINE_S:
                run = _skip(unit, 'stage_budget', now=now, settings=settings, attempt=attempt, mode=mode)
            elif unavailable is not None:
                run = _skip(unit, unavailable, now=now, settings=settings, attempt=attempt, mode=mode)
            elif quota_exhausted:
                run = _skip(unit, 'quota_exhausted', now=now, settings=settings, attempt=attempt, mode=mode)
            elif connection_lost:       # the rest of the node is skipped for the same reason as the unit that found out
                run = _skip(unit, connection_lost, now=now, settings=settings, attempt=attempt, mode=mode)
            elif len(prior) >= AGENT_MAX_ATTEMPTS:
                run = _skip(unit, 'max_attempts', now=now, settings=settings, attempt=attempt, mode=mode)
            elif settings.daily_tokens is not None:
                with core_tx._tx() as conn:
                    used = analysis_repo.agent_tokens_on(conn, ny_date)
                if used >= settings.daily_tokens:
                    run = _skip(unit, 'token_budget', now=now, settings=settings, attempt=attempt, mode=mode)
                else:
                    run = runner(unit, attempt)
            else:
                run = runner(unit, attempt)
            record(unit, run)
        except Unavailable:
            raise
        except Exception as exc:   # §2: an agent failure never drags the rule track
            summary['errors'].append(f"{unit['ticker']} agent: {exc}")
            if acc is not None:
                acc.fail(unit['ticker'], exc)

    merged: dict[str, str] = {}
    with core_tx._tx() as conn:
        groups = units._session_units(conn, session, now=now)
        ready = core_schema.timeline_ready(conn) if timeline_ready is None else timeline_ready
        resets_ready = core_schema.daily_ready(conn)     # migration 028: the attempt-reset table exists
        merges_ready = core_schema.executable_ready(conn)        # migration 029: chain_retriggers exists
    if acc is not None:
        acc.input(sum(len(units) for units in groups.values()))
    if not ready:
        summary['errors'].append('timeline: migration_026_missing')
        if acc is not None:
            acc.degrade('migration_026_missing')

    def propose(unit: dict, attempt: int) -> dict:
        if not ready:       # the shared gates above still come first (deadline, provider, quota, attempts)
            return _skip(unit, 'migration_026_missing', now=now, settings=settings, attempt=attempt)
        return run_agent_unit(unit, now=now, deps=deps, settings=settings, attempt=attempt, run_id=run_id,
                              features=features)

    def propose_or_merge(unit: dict) -> None:
        """§4.3: a merged unit is not proposed; everything else goes through the shared gates."""
        if merges_ready:
            try:
                with core_tx._tx() as conn:
                    row = execution_repo.retrigger_for_unit(conn, unit['ticker'], unit['signal_date'])
                    if row is not None and unit['pool'] == 'main' and analysis_repo.latest_brief(
                            conn, unit['ticker'], unit['signal_date']) is None:
                        analysis_repo.store_brief(conn, _merged_brief(unit, now=now))
            except Unavailable:
                raise
            except Exception as exc:   # §2: an agent failure never drags the rule track
                summary['errors'].append(f"{unit['ticker']} agent: {exc}")
                if acc is not None:
                    acc.fail(unit['ticker'], exc)
                return
            if row is not None:
                merged[unit['ticker']] = row['chain_signal_date'].isoformat()
                if acc is not None:
                    acc.skip('active_chain', f"{unit['ticker']}·{row['chain_signal_date'].isoformat()}")
                return
        run_one(unit, 'propose', propose)

    def interpret(unit: dict, attempt: int) -> dict:
        return run_interpret_unit(unit, now=now, deps=deps, settings=settings, attempt=attempt)

    for unit in groups['main_buy']:          # §7.3: main propose, main interpret, extended propose
        propose_or_merge(unit)
    for unit in groups['main_other']:
        run_one(unit, 'interpret', interpret)
    for unit in groups['extended']:
        propose_or_merge(unit)

    summary['agent'] = {'runs': runs, 'briefs': briefs, 'tokens': tokens,
                       'quota_exhausted': quota_exhausted, 'merged': merged}
    if connection_lost:
        summary['agent']['connection_lost'] = connection_lost      # the review stage after this one honours it
    summary['timings']['agent'] = round(clock() - t_agent, 1)
    if acc is not None:
        acc.detail['tokens'] = dict(tokens)
        acc.detail['merged'] = len(merged)
        acc.duration_ms += (clock() - t_agent) * 1000
    return unavailable


class AgentConflict(RuntimeError):
    """The unit already has an ok/pass agent run (``already_completed``), needs no re-run (``not_needed``), or —
    v2.9 §4.7 — the ticker already has an active plan chain (``active_chain``; ``chain_signal_date`` names it)."""

    def __init__(self, message: str = '', *, chain_signal_date: date | None = None, version_no: int | None = None):
        super().__init__(message)
        self.chain_signal_date = chain_signal_date
        self.version_no = version_no


def _assert_unit_may_open(conn, ticker: str, signal_date: date, *, now: datetime) -> None:
    """A manual run (``run_agent``, ``rerun_agent``) may only open a chain when the ticker has no active one
    (v2.9 §4.7, D2): ``AgentConflict('active_chain')`` otherwise. The unit's own chain does not count (the
    already-ok guard answers for that), and a unit merged into a chain that has since ended may open its own
    (R17: the merge row only counts while the chain it points at is active). A no-op before migration 029."""


    if not core_schema.executable_ready(conn):
        return
    found = execution_chains.active_chain_of(conn, ticker, session=max(signal_date, bars.latest_closed_session(now)),
                                        now=now)
    if found is not None and found['chain']['signal_date'] != signal_date:
        raise AgentConflict('active_chain', chain_signal_date=found['chain']['signal_date'],
                            version_no=found['chain']['versions'][-1]['version_no'])


def run_agent(ticker: str, signal_date: date, *, now: datetime, deps: Deps | None = None) -> dict[str, Any]:
    """Manual single-unit run (§10.3): not a conflict-free buy unit -> ValueError,
    already ok/pass -> AgentConflict; otherwise one run at the given cutoff.

    v2.5 §5.1: like the batch it only ever produces version 0. An ok run is exactly what writes
    version 0, so the existing ok/pass guard is also the "chain already exists" guard; revisions only
    come from reviews. Without migration 026 the run is recorded as ``skipped / migration_026_missing``."""
    deps = deps or Deps()
    settings = deps.agent_settings if deps.agent_settings is not None else agent_model.settings_from_env()
    with core_tx._tx() as conn:
        group = data_repo.unit_candidates(conn, ticker, signal_date)
        if not group:
            raise ValueError(f'{ticker} {signal_date} 没有候选，不是决策单元')
        book = execution_plan_context._book(conn, ticker)
        unit = synthesis.synthesize(ticker, signal_date, group, holding=book['position'] is not None,
                                    now=now)
        if unit is None or unit['proposed_action'] != 'buy' or 'conflict' in unit['labels']:
            raise ValueError(f'{ticker} {signal_date} 不是无冲突的买入单元')
        prior = analysis_repo.agent_runs_for(conn, ticker, signal_date)
        if any(r['status'] in ('ok', 'pass') for r in prior):
            raise AgentConflict(f'{ticker} {signal_date} 已有 Agent 建议')
        _assert_unit_may_open(conn, ticker, signal_date, now=now)
        extended = units._unit_is_extended(conn, ticker, signal_date, held=book['position'] is not None, now=now)
        entry = data_repo.pool_entry_at(conn, ticker, signal_date, cutoff_at=now)
        agent_unit = {'ticker': unit['ticker'], 'signal_date': signal_date,
                      'pool': 'extended' if extended else 'main',
                      'pool_tier': entry['tier'] if entry else 'core',
                      'proposed_action': unit['proposed_action'], 'labels': unit['labels'],
                      'candidates': group}
        ready = core_schema.timeline_ready(conn)
    unavailable = agent_model.unavailable_reason(settings)
    if unavailable is not None:
        return _skip(agent_unit, unavailable, now=now, settings=settings, attempt=len(prior) + 1)
    if not ready:
        return _skip(agent_unit, 'migration_026_missing', now=now, settings=settings, attempt=len(prior) + 1)
    return run_agent_unit(agent_unit, now=now, deps=deps, settings=settings, attempt=len(prior) + 1)


RERUN_MODES = ('propose', 'interpret')
RERUN_ORIGINS = ('run_detail', 'plan_page', 'api')


def rerun_agent(ticker: str, signal_date: date, mode: str, *, request_id: str, origin: str, now: datetime,
                deps: Deps | None = None) -> dict[str, Any]:
    """Re-run the Agent of one unit whose attempts ran out or whose last run failed (v2.8 D26, §5.7).

    Appends an ``agent_attempt_resets`` row (``request_id`` makes a repeat harmless: the stored reset comes back with
    ``replayed: True`` and nothing runs again); ``propose`` then runs the unit once (``run_agent``), ``interpret``
    only resets — the next run or resume executes it. Returns ``{'reset_id', 'run_id' | None, 'replayed'}``.

    ``ValueError``: bad mode / origin / request id, or a ``propose`` unit that is not a conflict-free buy;
    ``LookupError``: no such unit; ``AgentConflict('not_needed')``: the unit's latest run of this mode is neither
    ``skipped / max_attempts`` nor ``error``; ``tasks.daily.DailyBusy`` (``propose`` only): a ``daily`` execution holds
    the lock and may be working on this very unit."""
    if mode not in RERUN_MODES:
        raise ValueError(f'mode 必须是 {" / ".join(RERUN_MODES)}')
    if origin not in RERUN_ORIGINS:
        raise ValueError(f'origin 必须是 {" / ".join(RERUN_ORIGINS)}')
    try:
        request_uuid = str(uuid.UUID(str(request_id)))
    except ValueError:
        raise ValueError('request_id 必须是 UUID') from None
    ticker = ticker.upper()
    if mode == 'propose':


        try:
            busy = locks.is_running()
        except Exception:      # noqa: BLE001 - no database to ask: the checks below report what is wrong
            busy = False
        if busy:               # the node being re-run may be working on this very unit
            raise locks.DailyBusy()
    with core_tx._tx() as conn:
        group = data_repo.unit_candidates(conn, ticker, signal_date)
        if not group:
            raise LookupError(f'{ticker} {signal_date} 没有候选，不是决策单元')
        for earlier in analysis_repo.attempt_resets_for(conn, ticker, signal_date, mode):
            if str(earlier['request_id']) == request_uuid:
                return {'reset_id': earlier['id'], 'run_id': None, 'replayed': True}
        if mode == 'propose':
            book = execution_plan_context._book(conn, ticker)
            unit = synthesis.synthesize(ticker, signal_date, group, holding=book['position'] is not None, now=now)
            if unit is None or unit['proposed_action'] != 'buy' or 'conflict' in unit['labels']:
                raise ValueError(f'{ticker} {signal_date} 不是无冲突的买入单元')
            _assert_unit_may_open(conn, ticker, signal_date, now=now)     # before the reset row is written
        latest = analysis_repo.latest_agent_run(conn, ticker, signal_date, mode)
        merged_before = (mode == 'propose' and core_schema.executable_ready(conn)
                         and execution_repo.retrigger_for_unit(conn, ticker, signal_date) is not None)
        if merged_before and (latest is None or (latest['status'] == 'skipped' and latest['reason'] == 'active_chain')):
            pass        # R17: merged (never proposed, or proposed and set aside), and the chain it joined is over
        elif latest is None or not (latest['status'] == 'error'
                                    or (latest['status'] == 'skipped' and latest['reason'] == 'max_attempts')):
            raise AgentConflict('not_needed')
        reset = analysis_repo.insert_attempt_reset(conn, {'ticker': ticker, 'signal_date': signal_date, 'mode': mode,
                                                       'request_id': request_uuid, 'origin': origin})
    run_id = None
    if mode == 'propose' and not reset['replayed']:      # a request id already used (by another unit): nothing runs
        run_id = str(run_agent(ticker, signal_date, now=now, deps=deps)['id'])
    return {'reset_id': reset['id'], 'run_id': run_id, 'replayed': bool(reset['replayed'])}


def _prompt_conditions(conditions: list[dict]) -> list[dict]:
    """The conditions as ``agent_evidence.prompt_evidence`` numbers them: the injected time stop gets the next free
    invalidation number (its stored index is the text ``max_hold``)."""
    base = sum(1 for c in conditions if c['list'] == 'invalidation' and c.get('index') != 'max_hold')
    return [{**c, 'index': base} if c.get('index') == 'max_hold' else c for c in conditions]


def _retrigger_hits(signals: list[dict]) -> list[dict]:
    return next((s['evidence'] for s in signals if s['kind'] == 'retrigger' and s.get('fired')), [])


def _retrigger_text(hits: list[dict]) -> str:
    """「再次触发（<规则>，<信号日>）」 for the newest hit (§4.3 wording, also the revision's change line)."""
    last = hits[-1]
    rules = '、'.join(labels.RULE_TEXT.get(r, r) for r in last.get('rule_ids') or [])
    return f"再次触发（{rules}，{last['unit_signal_date']}）"


def _retrigger_block(hits: list[dict]) -> str:
    """The 「再次触发」 block of the evidence (agent-review-2, D32): each hit's rules, frozen values and the
    unit's rule-baseline levels. Host numbers only; the rows were cut at the cutoff (R4)."""
    lines = ['## 再次触发', '同一只股票又触发了买入规则，已并入这条链（宿主算好的数字，不可改写）：']
    for hit in hits:
        rules = '、'.join(labels.RULE_TEXT.get(r, r) for r in hit.get('rule_ids') or [])
        base = hit.get('baseline') or {}
        levels = (f"规则基线 入场 {base.get('entry_low')}–{base.get('entry_high')}，止损 {base.get('stop')}，"
                  f"T1 {base.get('target1')}" if base else '规则基线（无）')
        lines.append(f"- {hit['unit_signal_date']}：{rules}；{levels}")
        for rule_id, values in (hit.get('frozen_values') or {}).items():
            lines.append(f'    冻结取值 {rule_id}：{agent_evidence._json(values)}')
    return '\n'.join(lines) + '\n'


def _holding_block(values: dict, *, mode_note: str = '') -> str:
    """The 「持仓状态」 block (agent-review-2, D32; spec §7.3's fields before the instruction): all numbers are the
    host's, computed from the fills the cutoff knew (R11)."""
    def pct(value) -> str:
        return '—' if value is None else f'{value:+.2f}%'

    def money(value) -> str:
        return '—' if value is None else f'{Decimal(str(value)):.2f}'

    reached = {True: '是', False: '否', None: '无法判定'}[values['reached_1r']]
    max_hold = values['max_hold_sessions']
    remaining = ('已到 1R 或 T1，时间止损解除' if values['remaining_sessions'] is None
                 else f"{values['remaining_sessions']} 个交易日")
    lines = ['## 持仓状态',
             f"持仓 {format(Decimal(values['quantity']).normalize(), 'f')} 股，成本 {money(values['avg_cost'])}；"
             f"收盘 {money(values['last_close'])}",
             f"持有 {values['held_sessions']}/{max_hold if max_hold is not None else '—'} 个交易日；"
             f"开仓以来收盘到过 1R：{reached}；时间止损余量：{remaining}",
             f"有效止损 {money(values['stop'])}（{values['stop_mode'] or '—'}），距止损 {pct(values['to_stop_pct'])}；"
             f"T1 {money(values['target1'])}，距 T1 {pct(values['to_t1_pct'])}"
             + ('（已触及）' if values['t1_reached'] else ''),
             f"现在 {'—' if values['r_now'] is None else format(values['r_now'], '+.2f')}R"]
    if values['detail'].get('bars_missing'):
        lines.append('缺复查交易日的日线，以上收盘相关数字为空。')
    if mode_note:
        lines.append(mode_note)
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------- model calls

def _invoke(call: Callable[[Any, Any], dict], *, deps, settings) -> tuple[dict | None, tuple[str, str] | None]:
    """(result, None) or (None, (reason, detail)): checkpointer, model and error classification exactly
    as run_agent_unit does them."""
    with ExitStack() as stack:
        try:
            factory = deps.agent_checkpointer or (
                lambda: agent_model.checkpointer(os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL', '')))
            checkpointer = stack.enter_context(factory())
        except Exception as exc:
            _, detail = agent_model.classify_error(exc, settings.api_key)
            return None, ('checkpoint_unavailable', detail)
        try:
            model = deps.agent_model if deps.agent_model is not None else agent_model.chat_model(settings)
            return call(model, checkpointer), None
        except core_tx.Unavailable:
            raise
        except Exception as exc:
            reason, detail = agent_model.classify_error(exc, settings.api_key)
            if agent_model._checkpoint_missing(exc):
                reason = 'checkpoint_unavailable'
            return None, (reason, detail)


def _reason(text: Any, settings) -> str:
    """A review reason as stored: model key and FRED key redacted, at most 300 characters, never blank."""
    cleaned = families.sanitize_reason(agent_model.redact(str(text or ''), settings.api_key)).strip()
    return cleaned or EMPTY_REASON


# ---------------------------------------------------------------- one chain

def _lock_connection():
    """A connection of its own for the run lock, opened outside the process pool (autocommit): the run
    lock is held for minutes (model calls), so it must not take one of the pool's few connections away
    from the batch and the API, and a session-level lock must not ride on a connection whose transactions
    commit and roll back underneath it."""
    import psycopg

    url = os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL')
    if not url:
        raise core_tx.Unavailable('数据库未配置')
    return psycopg.connect(url, autocommit=True)


@contextmanager
def _run_lock(ticker: str, signal_date: date):
    """Try the chain's session-level run lock ``review-run:<ticker>:<signal_date>``; yields whether it is
    held. Held for the whole ``with`` block on a dedicated, non-pooled connection, released on the way out
    and the connection always closed (closing releases its session locks too, so a failed unlock cannot
    leave the lock behind)."""
    import psycopg

    key = f'review-run:{ticker.upper()}:{signal_date.isoformat()}'
    try:
        conn = _lock_connection()
        try:
            got = bool(conn.execute('SELECT pg_try_advisory_lock(hashtext(%s))', (key,)).fetchone()[0])
            try:
                yield got
            finally:
                if got:
                    try:
                        conn.execute('SELECT pg_advisory_unlock(hashtext(%s))', (key,))
                    except Exception:      # noqa: BLE001 - the close below releases it anyway
                        pass
        finally:
            conn.close()
    except psycopg.OperationalError:
        raise core_tx.Unavailable('数据库不可达') from None


def run_chain_review(obj: dict, *, session: date, now: datetime, trigger: str, run_id: str | None, deps, settings,
                 started: float, clock: Callable[[], float], quota: dict, via: str | None = None) -> dict:
    """Review one chain under its run lock (a busy lock → ``{'skipped_without_row': 'review_in_progress'}``,
    nothing read, no model call); see ``_review_chain`` for the review itself. ``via`` ('data_request')
    marks a D40 data-request recheck in the review row's detail."""
    with _run_lock(obj['ticker'], obj['signal_date']) as got:
        if not got:
            return {'skipped_without_row': 'review_in_progress'}
        return _review_chain(obj, session=session, now=now, trigger=trigger, run_id=run_id, deps=deps,
                             settings=settings, started=started, clock=clock, quota=quota, via=via)


def _review_chain(obj: dict, *, session: date, now: datetime, trigger: str, run_id: str | None, deps, settings,
                  started: float, clock: Callable[[], float], quota: dict, via: str | None = None) -> dict:
    """Review one chain at session S and cutoff ``now`` (spec §7.3–§7.4); returns the written plan_reviews
    row, or ``{'skipped_without_row': reason}`` (review_in_progress, fresh_version, already_reviewed,
    max_attempts, chain_not_active).

    ``obj`` needs ticker and signal_date (pool and filings_known optional: the SEC listing of this batch
    failed → filings unknown). ``trigger`` is 'batch' or 'recheck' (a recheck skips the quiet-day maintain,
    is not limited by REVIEW_MAX_ATTEMPTS and writes origin 'recheck' versions). ``quota`` is the run's
    shared ``{'exhausted': bool}``, set here when a call hits the quota. Model and checkpoint failures
    become an ``error`` row; so does a failure inside the write transaction (it is rolled back, then an
    ``error`` row with ``detail.write_error`` and the model runs as ``error`` rows, tokens kept, is written in
    a new one, so attempts and the token cap still count). Failures before any write propagate (the stage
    isolates them per chain)."""
    ticker, signal_date = obj['ticker'], obj['signal_date']
    recheck = trigger == 'recheck'
    with core_tx._tx() as conn:
        if not execution_repo.try_review_lock(conn, ticker, signal_date):
            return {'skipped_without_row': 'review_in_progress'}
        seen = execution_chains._read(conn, ticker, signal_date, session=session, now=now, trigger=trigger,
                     filings_known=obj.get('filings_known', True))
    if 'skip' in seen:
        return {'skipped_without_row': seen['skip']}
    pool = obj.get('pool') or seen['pool']
    mode = seen['mode']
    signals, conditions = execution_chains._assess(seen, session=session, now=now)
    values = execution_chains._holding_values(seen, session)              # None for an entry chain
    hits = _retrigger_hits(signals)
    included, omitted = timeline.select_new_inputs(seen['inputs_new'])
    detail: dict[str, Any] = {}
    if not any(b['price_date'] == session for b in seen['bars']):
        detail['bars_missing'] = True                 # §13: signals / conditions stay unknown or quiet
    if omitted:
        detail['inputs_omitted'] = omitted
    if hits:                                          # who merged into the chain: the push and the page word it
        detail['retrigger'] = {'unit_signal_dates': [h['unit_signal_date'] for h in hits],
                               'rule_ids': sorted({r for h in hits for r in h.get('rule_ids') or []})}
    base = {'ticker': ticker, 'signal_date': signal_date, 'head_plan_id': seen['plan']['id'], 'session': session,
            'trigger': trigger, 'run_id': run_id, 'mode': mode, 'timeline_version': timeline.TIMELINE_VERSION,
            'signals': signals, 'conditions': conditions, 'user_input_ids': [r['id'] for r in included],
            'cutoff_at': now, 'detail': detail}
    if via is not None:
        detail['via'] = via
    unit = {'ticker': ticker, 'signal_date': signal_date, 'pool': pool}
    spent: dict[str, tuple[str, int, int]] = {}      # model runs made so far: run id -> (mode, in, out)

    def run_row(run_id_: str, run_mode: str, status: str, attempt: int, **kwargs) -> dict:
        return agent_model._agent_run_row(unit, settings=settings, attempt=attempt, now=now, status=status,
                                      run_id=run_id_, mode=run_mode, **kwargs)

    def commit(build: Callable[[Any, int], dict]) -> dict:
        try:
            out = _commit(seen, ticker=ticker, signal_date=signal_date, session=session, now=now, trigger=trigger,
                          build=build)
        except core_tx.Unavailable:
            raise
        except Exception as exc:      # the write failed and rolled back: record it, so attempts and tokens count
            out = _commit(seen, ticker=ticker, signal_date=signal_date, session=session, now=now, trigger=trigger,
                          build=lambda conn, attempt: _write_failed(conn, attempt, exc=exc, base=base,
                                                                    spent=spent, run_row=run_row,
                                                                    settings=settings))
        if 'skipped_without_row' in out and spent:
            _record_lost_runs(spent, reason=out['skipped_without_row'], run_row=run_row,
                              attempt=len(seen['attempts']) + 1)
        return out

    # §7.2 steps 2-3 (holding chains, D18): a touched stop and a met time stop invalidate before anything else;
    # then §7.4 steps 1-2 as in v2.5. A holding-mode invalidation is the exit instruction, never the chain's end.
    verdict = None
    if values is not None and values['exit_trigger'] == 'stop_touched':
        verdict = {'outcome': 'invalidate', 'exit_reason': 'stop_touched',
                   'reason': f"止损触及（{values['stop']:.2f}）：应已按止损卖出；仍持有则次一交易日开盘卖出"}
    elif values is not None and values['exit_trigger'] == 'time_stop':
        verdict = {'outcome': 'invalidate', 'exit_reason': 'time_stop',
                   'reason': f"时间止损：持有 {values['held_sessions']} 个交易日仍未到 1R，次一交易日开盘卖出"}
    else:
        verdict = timeline.host_verdict(signals=signals, conditions=conditions, recheck=recheck)
        if verdict is not None and verdict['outcome'] == 'invalidate' and mode == 'holding':
            verdict = {**verdict, 'exit_reason': 'invalidation'}
    if verdict is not None:                           # the host decides, no model call
        extra = {'exit_reason': verdict['exit_reason']} if verdict.get('exit_reason') else {}
        return commit(lambda conn, attempt: {**base, 'detail': {**base['detail'], **extra},
                                             'outcome': verdict['outcome'], 'decided_by': 'host',
                                             'reason': _reason(verdict['reason'], settings), 'attempt': attempt})

    if not recheck and len(seen['attempts']) >= REVIEW_MAX_ATTEMPTS:
        return {'skipped_without_row': 'max_attempts'}
    gate = _gate(settings, started=started, clock=clock, quota=quota, now=now)
    if gate is not None:                              # §7.4 step 3
        return commit(lambda conn, attempt: {**base, 'outcome': 'skipped', 'decided_by': 'host',
                                             'reason': gate, 'attempt': attempt})

    output = (seen['run'] or {}).get('output') or {}
    evidence = agent_evidence.prompt_evidence(
        head={'version': seen['version'], 'plan': seen['plan'], 'output': output,
              'counter': (seen['run'] or {}).get('counter')},
        trigger=seen['trigger_rows'], signals=signals, conditions=_prompt_conditions(conditions),
        inputs_new=seen['inputs_new'], inputs_old=seen['inputs_old'], last_review=seen['last_review'])
    if hits:
        evidence += '\n' + _retrigger_block(hits)
    if values is not None:
        evidence += '\n' + _holding_block(values)
    user_refs = frozenset(f"user:{r['id']}" for r in [*included, *seen['inputs_old']])
    context = (seen['plan'].get('inputs') or {}).get('context') or {}
    graph_unit = {'ticker': ticker, 'signal_date': signal_date, 'pool_tier': context.get('pool_tier')}
    ctx = agent_tools.ToolContext(ticker=ticker, session=session, cutoff_at=now, read=core_tx._read_only)
    exit_detail = {'exit_reason': 'agent_invalidate'} if mode == 'holding' else {}

    retriggered = mode == 'entry' and bool(hits)      # D4: a merged-in trigger skips the single-phase review
    if retriggered:
        said_reason = f'{_retrigger_text(hits)}，直接修订'
        review_base = {**base, 'review_agent_run_id': None, 'input_tokens': 0, 'output_tokens': 0}
        said_base = {**review_base, 'decided_by': 'agent', 'reason': _reason(said_reason, settings), 'refs': [],
                     'knowledge_citations': [], 'detail': {**base['detail'], 'via': 'retrigger'}}

        def review_run(conn, attempt: int) -> None:
            return None
    else:
        review_id = str(uuid.uuid4())                 # §7.4 step 4: agent-review-2, single phase
        reviewed, failure = _invoke(
            lambda model, checkpointer: agent_graph.run_review(
                unit=graph_unit, ctx=ctx, model=model, checkpointer=checkpointer, run_id=review_id,
                now=lambda: now, evidence=evidence, user_refs=user_refs, prompt=agent_graph.REVIEW_PROMPT_V2),
            deps=deps, settings=settings)
        spent[review_id] = ('review', 0, 0) if failure is not None else (
            'review', reviewed['input_tokens'] or 0, reviewed['output_tokens'] or 0)
        if failure is not None:
            reason, error_detail = failure
            if reason == 'quota_exhausted':
                quota['exhausted'] = True
            elif reason in CONNECTION_LOST:
                quota['lost'] = reason                # §8.8: the rest of the node is skipped for the same reason

            def failed(conn, attempt: int) -> dict:
                analysis_repo.insert_agent_run(conn, run_row(review_id, 'review', 'error', attempt, reason=reason,
                                                          detail=error_detail), [])
                return {**base, 'outcome': 'error', 'decided_by': 'agent', 'reason': reason,
                        'review_agent_run_id': review_id, 'attempt': attempt}
            return commit(failed)

        review_tokens = (reviewed['input_tokens'] or 0, reviewed['output_tokens'] or 0)

        def review_run(conn, attempt: int) -> None:
            analysis_repo.insert_agent_run(conn, run_row(review_id, 'review', reviewed['status'], attempt,
                                                      reason=reviewed['reason'], output=reviewed['output'],
                                                      input_tokens=review_tokens[0], output_tokens=review_tokens[1]),
                                        reviewed['calls'])

        review_base = {**base, 'review_agent_run_id': review_id, 'input_tokens': review_tokens[0],
                       'output_tokens': review_tokens[1]}
        if reviewed['status'] != 'ok':                # review_malformed / invalid_citation / tool_rounds_exceeded
            def rejected(conn, attempt: int) -> dict:
                review_run(conn, attempt)
                return {**review_base, 'outcome': 'error', 'decided_by': 'agent', 'reason': reviewed['reason'],
                        'attempt': attempt}
            return commit(rejected)

        said = reviewed['output']
        said_reason = said['reason']
        said_base = {**review_base, 'decided_by': 'agent', 'reason': _reason(said['reason'], settings),
                     'refs': said.get('refs') or [],
                     # v2.7 D27: the knowledge the verdict cited; _commit writes it after the review row (it needs
                     # the review id) and takes the key off the row first
                     'knowledge_citations': [
                         {**cited, 'agent_run_id': review_id}
                         for cited in knowledge.knowledge_citations(output=said, counter=None, mode='review',
                                                                       calls=reviewed['calls'])]}
        if said['outcome'] in ('maintain', 'invalidate'):  # §7.4 step 5
            invalid = said['outcome'] == 'invalidate'

            def concluded(conn, attempt: int) -> dict:
                review_run(conn, attempt)
                return {**said_base, 'outcome': said['outcome'], 'attempt': attempt,
                        'detail': {**said_base['detail'], **(exit_detail if invalid else {})}}
            return commit(concluded)

    # §7.4 step 6: revise — the full proposal + counter + checks, mode 'revise', at most once
    revise_id = str(uuid.uuid4())
    bars = seen['bars']
    # D42: a revision never reruns the researcher — run_unit_v2 carries ``output['research']``
    # (or falls back to disabled when the chain predates agent-4), the ORIGINAL owner kept (R16).
    research_owner = str(seen['run']['id']) if seen.get('run') else None
    if bars:
        revised, failure = _invoke(
            lambda model, checkpointer: agent_graph.run_unit_v2(
                unit=graph_unit, ctx=ctx, model=model, checkpointer=checkpointer, run_id=revise_id,
                last_close=Decimal(str(bars[-1]['close'])), as_of_session=session, now=lambda: now,
                previous=output, evidence=evidence, user_refs=user_refs, review_reason=said_reason,
                version=AGENT_RUN_VERSION, mode=mode, via='retrigger' if retriggered else None,
                previous_run_id=research_owner),
            deps=deps, settings=settings)
    else:
        revised, failure = None, ('bars_missing', 'no bars through the review session')
    if revised is not None and run_id is not None and revised.get('data_requests'):
        # D40/D42: the daily run owns the requests its revision queued, so the review node's tail finds them
        revised = {**revised, 'data_requests': [{**r, 'run_id': run_id} for r in revised['data_requests']]}
    if failure is not None and failure[0] == 'quota_exhausted':
        quota['exhausted'] = True
    elif failure is not None and failure[0] in CONNECTION_LOST:
        quota['lost'] = failure[0]
    spent[revise_id] = ('revise', 0, 0) if revised is None else (
        'revise', revised['input_tokens'] or 0, revised['output_tokens'] or 0)
    return commit(lambda conn, attempt: _revision(
        conn, attempt, seen=seen, said_base=said_base, review_run=review_run, run_row=run_row,
        revise_id=revise_id, revised=revised, failure=failure, session=session, now=now, trigger=trigger,
        settings=settings, retriggered=retriggered, exit_detail=exit_detail))


def _gate(settings, *, started: float, clock: Callable[[], float], quota: dict, now: datetime) -> str | None:
    """§7.4 step 3, in the agent stage's order: stage budget, provider, quota, daily tokens."""
    unavailable = agent_model.unavailable_reason(settings)
    if clock() - started >= AGENT_DEADLINE_S:
        return 'stage_budget'
    if unavailable is not None:
        return unavailable
    if quota.get('exhausted'):
        return 'quota_exhausted'
    if quota.get('lost'):
        return quota['lost']
    if settings.daily_tokens is not None:
        with core_tx._tx() as conn:
            used = analysis_repo.agent_tokens_on(conn, now.astimezone(NY).date())
        if used >= settings.daily_tokens:
            return 'token_budget'
    return None


def _revision(conn, attempt: int, *, seen: dict, said_base: dict, review_run: Callable, run_row: Callable,
              revise_id: str, revised: dict | None, failure: tuple[str, str] | None, session: date,
              now: datetime, trigger: str, settings, retriggered: bool = False,
              exit_detail: dict | None = None) -> dict:
    """Inside the write transaction: the revise run, and on success the new plan + version (spec §5.2, agent-3);
    returns the review row. Failed revision (D10, D23) → outcome revise without new_plan_id; pass → the
    review becomes an invalidation (D11; in a holding chain that is the exit instruction, ``exit_reason
    agent_invalidate``). A renewal (``retriggered``, no model change) is a version whose host diff is
    ``valid_until`` / ``entry_cap`` only (D7); one that moves neither is refused as ``no_change``."""
    from investment_assistant.config import load_config

    review_run(conn, attempt)
    row = {**said_base, 'outcome': 'revise', 'revision_agent_run_id': revise_id, 'attempt': attempt}
    if failure is not None:
        reason, error_detail = failure
        analysis_repo.insert_agent_run(conn, run_row(revise_id, 'revise', 'error', attempt, reason=reason,
                                                  detail=error_detail, agent_version=AGENT_RUN_VERSION), [])
        return {**row, 'detail': {**row['detail'], 'revision_status': f'error: {reason}'}}
    assert revised is not None                  # the failure guard returned: a revision always has its result
    tokens = {'input_tokens': revised['input_tokens'] or 0, 'output_tokens': revised['output_tokens'] or 0}
    row['input_tokens'] += tokens['input_tokens']
    row['output_tokens'] += tokens['output_tokens']
    payload = {'output': revised['output'], 'counter': revised['counter'], **tokens}
    if revised['status'] == 'pass':
        analysis_repo.insert_agent_run(conn, run_row(revise_id, 'revise', 'pass', attempt,
                                                  agent_version=AGENT_RUN_VERSION, **payload),
                                    revised['calls'], data_requests=revised.get('data_requests') or [])
        summary = (revised['output'] or {}).get('summary')
        return {**row, 'outcome': 'invalidate', 'reason': _reason(summary or row['reason'], settings),
                'detail': {**row['detail'], 'via': 'revision_pass', **(exit_detail or {})}}
    if revised['status'] != 'ok':
        analysis_repo.insert_agent_run(conn, run_row(revise_id, 'revise', revised['status'], attempt,
                                                  reason=revised['reason'], agent_version=AGENT_RUN_VERSION,
                                                  **payload), revised['calls'],
                                        data_requests=revised.get('data_requests') or [])
        return {**row, 'detail': {**row['detail'],
                                  'revision_status': f"{revised['status']}: {revised['reason']}"}}

    ticker, signal_date, mode = seen['version']['ticker'], seen['version']['signal_date'], seen['mode']
    head_plan, head_version = seen['plan'], seen['version']
    equity_source = execution_plan_context.sizing_equity(conn, session, now=now)
    plan = timeline.build_version_plan(
        output=revised['output'], agent_run_id=revise_id, model=settings.model, ticker=ticker,
        signal_date=signal_date, as_of_session=session, version_no=head_version['version_no'] + 1,
        supersedes_plan_id=str(head_plan['id']), mode=mode, bars=seen['bars'],
        earnings_row=data_repo.latest_earnings(conn, ticker, cutoff_at=now),
        snapshot=equity_source,
        exposure=execution_plan_context.exposure(conn, session, cutoff_at=now, ticker=ticker),
        params=load_config().workbench, context=execution_plan_context.plan_context(conn, ticker, session, now=now),
        version=timeline.AGENT_V3, entry_cap=seen['entry_cap'] if mode == 'entry' else None)
    execution_plan_context.mark_sizing_source(plan, equity_source)
    # D7: the host diff = the model's changed fields plus the host fields (valid_until, entry_cap) that moved
    before = {**((seen['run'] or {}).get('output') or {}), 'valid_until': head_plan['valid_until'],
              'entry_cap': (head_plan.get('inputs') or {}).get('chain_entry_cap')}
    after = {**revised['output'], 'valid_until': plan['valid_until'], 'entry_cap': plan['inputs']['chain_entry_cap']}
    host_diff = timeline.host_diff(before, after)
    if retriggered and not host_diff:               # a renewal that renews nothing is no version
        analysis_repo.insert_agent_run(conn, run_row(revise_id, 'revise', 'rejected', attempt, reason='no_change',
                                                  agent_version=AGENT_RUN_VERSION, **payload), revised['calls'],
                                        data_requests=revised.get('data_requests') or [])
        return {**row, 'detail': {**row['detail'], 'revision_status': 'rejected: no_change'}}
    if mode == 'entry' and plan['status'] == 'invalid':      # D23: an entry revision must be adoptable
        reasons = ', '.join(plan['invalid_reasons'])
        analysis_repo.insert_agent_run(conn, run_row(revise_id, 'revise', 'rejected', attempt, reason='plan_invalid',
                                                  detail=reasons, agent_version=AGENT_RUN_VERSION, **payload),
                                        revised['calls'], data_requests=revised.get('data_requests') or [])
        return {**row, 'detail': {**row['detail'], 'revision_status': f'plan_invalid: {reasons}'}}
    plan_id = execution_repo.store_plan(conn, plan)['record']['id']
    analysis_repo.insert_agent_run(conn, run_row(revise_id, 'revise', 'ok', attempt, plan_id=plan_id,
                                                agent_version=AGENT_RUN_VERSION, **payload),
                                revised['calls'], data_requests=revised.get('data_requests') or [])
    execution_repo.insert_plan_version(conn, {
        'plan_id': plan_id, 'ticker': ticker, 'signal_date': signal_date,
        'version_no': head_version['version_no'] + 1, 'supersedes_plan_id': head_plan['id'],
        'origin': 'recheck' if trigger == 'recheck' else 'review', 'agent_run_id': revise_id, 'cutoff_at': now,
        'as_of_session': session, 'diff': host_diff, 'schema_version': timeline.AGENT_V3})
    # v2.7 D18: the knowledge the new version cited, with the version in the same transaction
    analysis_repo.insert_knowledge_citations(conn, [
        {**cited, 'agent_run_id': revise_id, 'plan_id': plan_id}
        for cited in knowledge.knowledge_citations(output=revised['output'], counter=revised['counter'],
                                                      mode='revise', calls=revised['calls'])])
    return {**row, 'new_plan_id': plan_id}


def _record_lost_runs(spent: dict, *, reason: str, run_row: Callable, attempt: int) -> None:
    """The chain moved while the model ran (a decision, or a review written without the run lock), so the
    review writes no row; its model runs are still recorded — ``skipped`` with the reason and their tokens —
    so the daily token cap counts them."""
    with core_tx._tx() as conn:
        for run_id, (mode, tokens_in, tokens_out) in spent.items():
            analysis_repo.insert_agent_run(conn, run_row(run_id, mode, 'skipped', attempt, reason=reason,
                                                      input_tokens=tokens_in, output_tokens=tokens_out), [])


def _write_failed(conn, attempt: int, *, exc: BaseException, base: dict, spent: dict, run_row: Callable,
                  settings) -> dict:
    """The review row after a failed write: every model run made so far as an ``error`` run with its
    tokens, outcome ``error`` with the classified reason and ``detail.write_error``."""
    reason, error_detail = agent_model.classify_error(exc, settings.api_key)
    ids = {'review': None, 'revise': None}
    for run_id, (mode, tokens_in, tokens_out) in spent.items():
        analysis_repo.insert_agent_run(conn, run_row(run_id, mode, 'error', attempt, reason=reason, detail=error_detail,
                                                  input_tokens=tokens_in, output_tokens=tokens_out), [])
        ids[mode] = run_id
    return {**base, 'outcome': 'error', 'decided_by': 'agent' if ids['review'] else 'host', 'reason': reason,
            'review_agent_run_id': ids['review'], 'revision_agent_run_id': ids['revise'],
            'input_tokens': sum(v[1] for v in spent.values()), 'output_tokens': sum(v[2] for v in spent.values()),
            'attempt': attempt, 'detail': {**base['detail'], 'write_error': error_detail}}


def _commit(seen: dict, *, ticker: str, signal_date: date, session: date, now: datetime, trigger: str,
            build: Callable[[Any, int], dict]) -> dict:
    """The one write transaction of a review: lock, then re-check what the read saw — the chain is still
    active in the same mode, the head is the same version and no effective review of it was written since
    (a recheck or a decision may have landed while the model ran; otherwise ``review_in_progress``, no row) —
    and (batch) not yet reviewed on S; then ``build(conn, attempt)`` inserts its runs / plan / version and
    returns the review row, inserted last."""
    with core_tx._tx() as conn:
        if not execution_repo.try_review_lock(conn, ticker, signal_date):
            return {'skipped_without_row': 'review_in_progress'}
        status = execution_chains.chain_status(conn, ticker, signal_date, session=session, now=now)
        if (status is None or status['state']['state'] not in execution_chains.REVIEWABLE_STATES
                or status['state']['mode'] != seen['mode']
                or str(status['head']['plan']['id']) != str(seen['plan']['id'])):
            return {'skipped_without_row': 'review_in_progress'}       # someone else moved the chain meanwhile
        if trigger == 'batch':
            attempts = execution_repo.batch_attempts(conn, ticker, signal_date, session)
            if any(r['outcome'] in execution_repo.EFFECTIVE_REVIEW_OUTCOMES for r in attempts):
                return {'skipped_without_row': 'already_reviewed'}
        else:
            attempts = [r for r in status['reviews'] if r['session'] == session and r['trigger'] == trigger]
        last = execution_repo.last_effective_review(conn, seen['plan']['id'])
        if (last or {}).get('id') != (seen['last_review'] or {}).get('id'):
            return {'skipped_without_row': 'review_in_progress'}       # the head was reviewed meanwhile
        row = build(conn, len(attempts) + 1)
        cited = row.pop('knowledge_citations', None) or []
        record = execution_repo.insert_plan_review(conn, row)
        analysis_repo.insert_knowledge_citations(conn, [{**c, 'review_id': record['id']} for c in cited])
        return record


def run_portfolio_decision(conn,account,*,session,now,run_id,settings,deps,clock,started,recent_closed,host_cancellations=None,portfolio_check=None):
    """One Agent-managed account decision (``agent-portfolio-2``). ``portfolio_check`` is the account's
    ``execution_paper_accounts.portfolio_check_state`` — the accounts node resolves it from the run's frozen
    ``portfolio_manager`` switch (K2: off → ``disabled``, no stored row consulted); without one the current row of the
    session is read as if the switch were on."""
    core_dbutil.lock(conn,f"account:{account['id']}")
    account=execution_repo.account_by_id(conn,account['id'])
    if account['status']!='active' or account['opened_on']>session: return None
    existing=execution_repo.decision_for(conn,account['id'],session)
    if existing: return existing
    if host_cancellations is None:
        host_cancellations=execution_paper_accounts.reconcile_pending_buys(conn,account,session=session,now=now)
    host_detail={'host_cancellations':host_cancellations} if host_cancellations else {}
    settings=settings or deps.agent_settings or agent_model.settings_from_env()
    reason='stage_budget' if clock()-started>=ACCOUNTS_AGENT_DEADLINE_S else agent_model.unavailable_reason(settings)
    if reason is None and settings.daily_tokens is not None and analysis_repo.agent_tokens_on(conn,now.astimezone(accounts.NY).date())>=settings.daily_tokens: reason='token_budget'
    if reason:
        return execution_repo.insert_decision(conn,{'account_id':account['id'],'session':session,'status':'skipped',
            'status_reason':reason,'run_id':run_id,'detail':{'cutoff_at':now,**host_detail}})[0]
    check_state=portfolio_check if portfolio_check is not None else execution_paper_accounts.portfolio_check_state(
        conn,account,session=session,enabled=True)
    facts=execution_paper_accounts.decision_facts(conn,account,session=session,now=now,recent_closed=recent_closed,check_state=check_state)
    evidence=portfolio_evidence.build_evidence({k:v for k,v in facts.items() if not k.startswith('_')}|{'secrets':[settings.api_key]})
    rid=str(uuid5(NAMESPACE_URL,f"portfolio:{account['id']}:{session}"))
    result={'input_tokens':0,'output_tokens':0,'calls':[],'output':None,'status':'error','reason':None}
    detail=None
    try:
        factory=deps.agent_checkpointer or (lambda:agent_model.checkpointer(os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL','')))
        with factory() as checkpointer:
            ctx=agent_tools.ToolContext(ticker='',session=session,cutoff_at=now,read=lambda fn:fn(conn))
            result=agent_graph.run_portfolio(account=account,ctx=ctx,model=deps.agent_model if deps.agent_model is not None else agent_model.chat_model(settings),
                checkpointer=checkpointer,run_id=rid,now=lambda:now,evidence=evidence)
    except Exception as exc:
        reason,detail=agent_model.classify_error(exc,settings.api_key)
        result.update(status='error',reason=reason)
    run=agent_model._agent_run_row({'ticker':None,'signal_date':None,'pool':None},settings=settings,attempt=1,now=now,
        status=result['status'],run_id=rid,mode='portfolio',agent_version=agent_graph.PORTFOLIO_AGENT_VERSION,
        reason=result.get('reason'),detail=detail,output=result.get('output'),input_tokens=result.get('input_tokens') or 0,
        output_tokens=result.get('output_tokens') or 0)
    analysis_repo.insert_agent_run(conn,run,result.get('calls') or [])
    status='ok' if result['status']=='ok' else ('rejected' if result['status']=='rejected' else 'skipped')
    try:
        with conn.transaction():
            return execution_paper_accounts._save_decision(conn,account,session=session,now=now,run_id=run_id,output=result.get('output'),facts=facts,
                status=status,reason='schema_invalid' if status=='rejected' else result.get('reason'),agent_run_id=rid,evidence=evidence,
                host_cancellations=host_cancellations,check_state=check_state)
    except Exception as exc:
        _,safe=agent_model.classify_error(exc,settings.api_key)
        return execution_repo.insert_decision(conn,{'account_id':account['id'],'session':session,'status':'skipped',
            'status_reason':'host_error','agent_run_id':rid,'agent_version':agent_graph.PORTFOLIO_AGENT_VERSION,
            'evidence':portfolio_evidence.evidence_facts(evidence),'output':result.get('output'),'run_id':run_id,
            'detail':{'cutoff_at':now,'error':safe,'input_tokens':run['input_tokens'],'output_tokens':run['output_tokens'],**host_detail}})[0]


def run_pm_commentary(account: dict, check: dict, *, session: date, now: datetime, settings, deps: Deps,
                      clock: Callable[[], float], started: float, facts: dict | None = None) -> dict:
    """The PM commentary of the real account's stored portfolio check (spec §7.2 ④): ``{status, reason}``.

    ``ok`` — validated and written to ``portfolio_checks.commentary`` together with its ``agent_runs(mode='pm')``
    row (one transaction); ``rejected`` / ``error`` — the run is recorded, the hard result stands without commentary;
    ``skipped`` — no model call (unavailable, over the token cap or the 300 s PM budget), no run row. One attempt per
    account and session: a stored run (any outcome) is not repeated."""
    if check.get('commentary') is not None:
        return {'status': 'ok', 'reason': None}
    run_id = str(uuid5(NAMESPACE_URL, f"pm:{account['id']}:{session}"))
    with core_tx._tx() as conn:
        done = data_repo.agent_run_by_id(conn, run_id)
        spent = analysis_repo.agent_tokens_on(conn, now.astimezone(accounts.NY).date())
    if done is not None:
        return {'status': done['status'], 'reason': done['reason']}
    settings = settings or deps.agent_settings or agent_model.settings_from_env()
    reason = 'stage_budget' if clock() - started >= PM_DEADLINE_S else agent_model.unavailable_reason(settings)
    if reason is None and settings.daily_tokens is not None and spent >= settings.daily_tokens:
        reason = 'token_budget'
    if reason:
        return {'status': 'skipped', 'reason': reason}
    result: dict = {'input_tokens': 0, 'output_tokens': 0, 'calls': [], 'output': None, 'status': 'error', 'reason': None}
    detail = None
    try:
        factory = deps.agent_checkpointer or (
            lambda: agent_model.checkpointer(os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL', '')))
        with factory() as checkpointer:
            ctx = agent_tools.ToolContext(ticker='', session=session, cutoff_at=now, read=_read_only)
            result = agent_pm.run_pm(
                execution_paper_accounts.check_result(check), account=account, ctx=ctx,
                model=deps.agent_model if deps.agent_model is not None else agent_model.chat_model(settings),
                checkpointer=checkpointer, run_id=run_id, now=lambda: now, facts=facts)
    except Exception as exc:    # noqa: BLE001 - a failed commentary never touches the hard result
        reason, detail = agent_model.classify_error(exc, settings.api_key)
        result.update(status='error', reason=reason)
    run = _agent_run_row({'ticker': None, 'signal_date': None, 'pool': None}, settings=settings, attempt=1, now=now,
                         status=result['status'], run_id=run_id, mode='pm', agent_version=PM_AGENT_VERSION,
                         reason=result.get('reason'), detail=detail, output=result.get('output'),
                         input_tokens=result.get('input_tokens') or 0, output_tokens=result.get('output_tokens') or 0)
    with core_tx._tx() as conn:
        analysis_repo.insert_agent_run(conn, run, result.get('calls') or [])
        if result['status'] == 'ok':
            execution_repo.set_portfolio_commentary(conn, check['id'], result['output'], run_id)
    return {'status': result['status'], 'reason': result.get('reason')}


# ---------------------------------------------------------------- v2.13 data-request tail (§5.5, D40–D42)

def process_data_requests(session: date, *, run_id: str | None, now: datetime, deps: Deps,
                          features: dict | None, budget_left_s: float) -> dict[str, int]:
    """The shared agent/review tail: settle this run's queued tool requests in the remaining budget.

    §5.5: read the run's queued ``data_requests`` and dedupe by (family, ticker); with less than
    ``DATA_REQUEST_MIN_S`` left every request settles ``deferred`` (terminal — the next day's review
    revises through its own path). Otherwise each family runs under its lock through the registry
    (scope ``data_request``), one ``data_fetch_runs`` row each, and every request row settles
    ``fetched`` / ``failed`` with its fetch run and rows. Each unit whose fetch gained rows reruns
    once (D40): an opened chain through the existing recheck review (``detail.via='data_request'``),
    an unopened unit as a new propose attempt bounded by ``AGENT_MAX_ATTEMPTS``;
    ``attach_data_request_rerun`` points the requests at the new run. Returns the node's
    ``data_requests`` detail counts. Before migration 031 there is no queue: all-zero counts, no table
    touched. ``features=None`` (a state without frozen flags) reads the current flags."""
    from investment_assistant.data import collect as data_collect
    from investment_assistant.data import registry as data_registry
    from investment_assistant.data.adapter_support import adapter_deps_from_service

    counts = {'queued': 0, 'fetched': 0, 'failed': 0, 'deferred': 0, 'reruns': 0}
    with core_tx._tx() as conn:
        if not core_schema.five_layers_ready(conn):
            return counts
        rows = data_repo.queued_data_requests(conn, run_id)   # NULL run_id pairs with a NULL query (manual flows)
        if rows and features is None:      # D3: no frozen run state → the current flags (default on)
            features = core_features.frozen(conn)
    if not rows:
        return counts
    features = features or {}
    counts['queued'] = len(rows)
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault((row['family'], row['ticker'].upper()), []).append(row)
    if budget_left_s < DATA_REQUEST_MIN_S:
        with core_tx._tx() as conn:
            for group in groups.values():
                for row in group:
                    data_repo.settle_data_request(conn, row['id'], status='deferred', fetch_run_id=None,
                                                  rows=None, reason='agent_budget', rerun_run_id=None)
        counts['deferred'] = len(rows)
        return counts

    projected = adapter_deps_from_service(deps, transaction=core_tx._tx, sleep=data_collect.FETCH_SLEEP)
    settled: list[dict] = []                      # fetched requests with new rows → their units rerun
    for (family, ticker), group in sorted(groups.items()):
        adapter = data_registry.get(family)
        lock = data_collect.family_locks([family]) if adapter.lock else nullcontext()
        with lock:
            tally = data_registry.run_adapter(adapter, [ticker], scope='data_request', trigger='data_request',
                                              run_id=run_id, session=session, now=now, deps=projected,
                                              features=features)
        fetch_row = tally.row(trigger='data_request', run_id=run_id, session=session,
                              started_at=now, finished_at=now, scope='ticker', ticker=ticker)
        _, fetch_ids = data_collect.write_runs_with_ids([fetch_row])
        fetch_run_id = fetch_ids[0] if fetch_ids else None
        target = next((item for item in (fetch_row.get('detail') or {}).get('targets') or []
                       if item.get('target') == ticker), None)
        ok, reason, written = (target or {}).get('ok', False), (target or {}).get('reason'), (target or {}).get('rows') or 0
        with core_tx._tx() as conn:
            for row in group:
                if ok:
                    data_repo.settle_data_request(conn, row['id'], status='fetched', fetch_run_id=fetch_run_id,
                                                  rows=written, reason=None, rerun_run_id=None)
                    counts['fetched'] += 1
                    if written:
                        settled.append(row)
                else:
                    data_repo.settle_data_request(conn, row['id'], status='failed', fetch_run_id=fetch_run_id,
                                                  rows=None, reason=reason or 'fetch_failed', rerun_run_id=None)
                    counts['failed'] += 1

    if not settled:
        return counts
    settings = deps.agent_settings if deps.agent_settings is not None else agent_model.settings_from_env()
    clock = deps.clock or time.monotonic
    started = clock() - (AGENT_DEADLINE_S - budget_left_s)     # the shared agent+review budget clock
    by_unit: dict[tuple[str, date], list[dict]] = {}
    with core_tx._tx() as conn:
        for row in settled:
            source = data_repo.agent_run_by_id(conn, row['agent_run_id']) if row['agent_run_id'] else None
            if source is None:
                continue
            by_unit.setdefault((source['ticker'], source['signal_date']), []).append(row)
    ny_day = now.astimezone(NY).date()
    for (ticker, signal_date), requests in sorted(by_unit.items()):
        # the reruns call the model: no transaction is open across them, only short reads before and the
        # pointer write after
        with core_tx._tx() as conn:
            if data_repo.unit_data_request_rerun_today(conn, ticker, signal_date, ny_day=ny_day):
                continue
            prior = analysis_repo.agent_runs_for(conn, ticker, signal_date, mode='propose')
            opened = any(run['status'] == 'ok' for run in prior)
            unit = None
            if not opened and len(prior) < AGENT_MAX_ATTEMPTS:
                unit = _unit_for_request_rerun(conn, ticker, signal_date, prior=prior, now=now)
        rerun_id = None
        if opened:
            review = run_chain_review({'ticker': ticker, 'signal_date': signal_date, 'pool':
                                       (prior[0] or {}).get('pool')}, session=session, now=now,
                                      trigger='recheck', run_id=run_id, deps=deps, settings=settings,
                                      started=started, clock=clock, quota={}, via='data_request')
            rerun_id = review.get('revision_agent_run_id') or review.get('review_agent_run_id')
        elif unit is not None:
            # D42: a rerun never reruns the researcher — carry the unit's last segment when
            # one exists; without one the rerun proceeds researcher-disabled, not researcher-fresh.
            last = prior[-1] if prior else None
            carried = ((last or {}).get('output') or {}).get('research')
            carried = carried if carried and carried.get('status') in ('ok', 'carried') else None
            rerun_features = (features if carried is not None
                              else {**features, 'company_researcher': False})
            rerun_id = run_agent_unit(unit, now=now, deps=deps, settings=settings,
                                      attempt=len(prior) + 1, run_id=run_id, features=rerun_features,
                                      carried_research=carried,
                                      previous_run_id=str(last['id']) if last else None).get('id')
        if rerun_id is None:
            continue
        counts['reruns'] += 1
        with core_tx._tx() as conn:
            for request in requests:
                data_repo.attach_data_request_rerun(conn, request['id'], rerun_id)
    return counts


def _unit_for_request_rerun(conn, ticker: str, signal_date: date, *, prior: list[dict], now: datetime) -> dict | None:
    """The unopened unit behind a data request (D40): the same synthesis ``run_agent`` does, without its
    ok/pass guard — the handler already knows the first version ended rejected / pass / error."""
    group = data_repo.unit_candidates(conn, ticker, signal_date)
    if not group:
        return None
    book = execution_plan_context._book(conn, ticker)
    unit = synthesis.synthesize(ticker, signal_date, group, holding=book['position'] is not None, now=now)
    if unit is None or unit['proposed_action'] != 'buy' or 'conflict' in unit['labels']:
        return None
    extended = units._unit_is_extended(conn, ticker, signal_date, held=book['position'] is not None, now=now)
    entry = data_repo.pool_entry_at(conn, ticker, signal_date, cutoff_at=now)
    return {'ticker': ticker, 'signal_date': signal_date,
            'pool': 'extended' if extended else 'main',
            'pool_tier': entry['tier'] if entry else 'core',
            'proposed_action': unit['proposed_action'], 'labels': unit['labels'], 'candidates': group}


# ---------------------------------------------------------------- v2.13 research second entrance (§6.3)

def run_research(row: dict, *, now: datetime, deps: Deps, collection: dict | None = None,
                 started: float | None = None) -> dict:
    """One dossier attempt's model half (§6.3 ④⑤): the agent-4 dossier chain, its ``agent_runs``
    row, one ``research_versions`` row and the request's state-machine outcome — all in one write
    transaction. ``waiting_data`` only when the researcher found blocked data AND the conclusion
    says ``wait``; every failure class becomes an error version, the request returns to ``queued``
    until the third attempt closes it (D51). Dossiers never open chains or write plans (D39 B)."""
    from investment_assistant.analysis import research_requests
    from investment_assistant.analysis.agent import research as research_phase
    from investment_assistant.config import load_config

    clock = deps.clock or time.monotonic
    started = started if started is not None else clock()
    agent_run_id = str(uuid.uuid4())
    session = bars.latest_closed_session(now)
    settings = deps.agent_settings if deps.agent_settings is not None else agent_model.settings_from_env()
    unit = {'ticker': row['ticker'], 'signal_date': session, 'pool_tier': None}
    sec_text_dir = Path(load_config().filings.output_dir)
    ctx = agent_tools.ToolContext(ticker=row['ticker'], session=session, cutoff_at=now,
                                  read=_read_only, sec_text_dir=sec_text_dir)
    result: dict = {'status': 'error', 'reason': None, 'output': None, 'research': None,
                    'calls': [], 'data_requests': [], 'input_tokens': 0, 'output_tokens': 0}
    failure: tuple[str, str] | None = None
    try:
        with ExitStack() as stack:
            try:
                factory = deps.agent_checkpointer or (
                    lambda: agent_model.checkpointer(os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL', '')))
                checkpointer = stack.enter_context(factory())
            except Exception as exc:   # noqa: BLE001 — classified below, like every model call here
                _, detail = agent_model.classify_error(exc, settings.api_key)
                failure = ('checkpoint_unavailable', detail)
            else:
                model_obj = deps.agent_model if deps.agent_model is not None else agent_model.chat_model(settings)
                try:
                    result = research_phase.run_chain(unit=unit, ctx=ctx, model=model_obj,
                                                      checkpointer=checkpointer, run_id=agent_run_id,
                                                      question=row.get('question') or '', now=lambda: now)
                except core_tx.Unavailable:
                    raise
                except Exception as exc:   # noqa: BLE001 — classify_error names the reason
                    reason, detail = agent_model.classify_error(exc, settings.api_key)
                    failure = (reason, detail)
    except core_tx.Unavailable:
        raise
    except Exception as exc:   # noqa: BLE001 — the batch classifies, never propagates a dossier failure
        reason, detail = agent_model.classify_error(exc, settings.api_key)
        failure = failure or (reason, detail)

    segment = result.get('research') or {}
    blocked = bool(segment.get('status') == 'ok' and (segment.get('output') or {}).get('blocked'))
    if failure is not None:
        version_status, action, summary = 'error', None, f"运行失败：{failure[0]}"
        run_status, reason, detail = 'error', failure[0], failure[1]
    elif result['status'] == 'rejected':
        version_status, action, summary = 'error', None, f"校验未过：{result['reason']}"
        run_status, reason, detail = 'rejected', result['reason'], None
    else:
        output = result['output'] or {}
        action, summary = output.get('action'), output.get('summary')
        version_status = 'waiting_data' if (blocked and action == 'wait') else 'concluded'
        run_status, reason, detail = result['status'], result.get('reason'), None
    tokens = {'input_tokens': result.get('input_tokens') or 0, 'output_tokens': result.get('output_tokens') or 0}
    run_row = agent_model._agent_run_row(
        {'ticker': row['ticker'], 'signal_date': session, 'pool': 'main'}, settings=settings, attempt=1,
        now=now, status=run_status, run_id=agent_run_id, mode='research', agent_version=AGENT_RUN_VERSION,
        reason=reason, detail=detail, output=result.get('output'), input_tokens=tokens['input_tokens'],
        output_tokens=tokens['output_tokens'])
    with core_tx._tx() as conn:
        analysis_repo.insert_agent_run(conn, run_row, result.get('calls') or [],
                                       data_requests=result.get('data_requests') or [])
        versions = analysis_repo.research_versions(conn, row['id'])
        version = analysis_repo.insert_research_version(conn, {
            'request_id': row['id'], 'version_no': len(versions) + 1, 'agent_run_id': agent_run_id,
            'cutoff_at': now, 'status': version_status, 'action': action, 'summary': summary,
            'output': result.get('output'), 'review_when': None,
            'tokens': tokens['input_tokens'] + tokens['output_tokens'],
            'elapsed_ms': int((clock() - started) * 1000), 'created_at': now})
        attempts = (row.get('attempts') or 0)
        if version_status == 'concluded':
            analysis_repo.update_research_status(conn, row['id'], from_status='running', to_status='concluded')
        elif version_status == 'waiting_data':
            analysis_repo.update_research_status(conn, row['id'], from_status='running', to_status='waiting_data')
        else:
            attempts += 1
            if attempts >= research_requests.MAX_ATTEMPTS:
                analysis_repo.update_research_status(conn, row['id'], from_status='running', to_status='closed',
                                                     attempts=attempts, closed_reason='max_attempts')
            else:
                analysis_repo.update_research_status(conn, row['id'], from_status='running', to_status='queued',
                                                     attempts=attempts)
        request = analysis_repo.research_request(conn, row['id'])
        assert request is not None
    return {'id': row['id'], 'status': request['status'], 'version': version,
            'agent_run_id': agent_run_id}


def drain_research_requests(session: date, *, now: datetime, deps: Deps, summary: dict[str, Any],
                            budget_left_s: float, features: dict | None = None) -> dict[str, int]:
    """§10/D26: the agent node's dossier tail — queued and waiting-data requests FIFO, inside the
    run's remaining shared budget (capped at RESEARCH_DEADLINE_S); the review tail reuses this
    handler. Degrades to a no-op before 031, with the flag off or without budget left."""
    from investment_assistant.analysis import research_requests

    counts = {'requests': 0}
    with core_tx._tx() as conn:
        if not core_schema.five_layers_ready(conn):
            return counts
        flags = features if features is not None else core_features.frozen(conn)
        queue = analysis_repo.queued_research_requests(conn)
    if not flags.get('company_researcher', True) or budget_left_s <= 0 or not queue:
        return counts
    clock = deps.clock or time.monotonic
    deadline = clock() + min(budget_left_s, RESEARCH_DEADLINE_S)
    for row in queue:
        if clock() >= deadline:
            break
        try:
            research_requests.run(row['id'], now=now, deps=deps)
        except core_tx.Unavailable:
            raise
        except Exception as exc:   # noqa: BLE001 — one dossier never drags the node
            summary.setdefault('errors', []).append(f"research {row['ticker']}: {exc}")
        counts['requests'] += 1
    return counts
