"""The daily review stage `review`, the per-chain review use case, the manual recheck, the chain read views
and the ``plan_review`` command line (v2.5 spec §7, §8.2, §12; `stages-2`; v2.9 spec §4, §7).

After the agent stage, every reviewable chain (state ``entry`` or ``holding`` under `timeline-2`, v2.9 R10; an
``exiting`` chain is only listed, its holding status is all it gets) is reviewed once per trading day S: the host
first (a holding chain's stop touched or time stop, then any met invalidation condition, invalidates, D8, D18; a
quiet day with no signal, no new user input and no met review condition maintains, D9), then — inside the
budget gates — one ``agent-review-2`` call, and a revision (proposal + counter + checks, ``mode = 'revise'``)
when the review says ``revise``. An entry chain a unit just merged into (``retrigger`` signal) skips the
single-phase review and goes straight to the revision (D4). Every outcome is one ``plan_reviews`` row; after all
reviews every held ticker gets one ``holding_status`` row, whatever happened to its review (D19).

Transactions: the chain's facts are read in one short transaction holding the chain's advisory lock; the
model runs with no transaction open; everything the review produces (review run, revise run, plan row,
version row, review row) is written in ONE transaction that takes the lock again and re-checks that the
head did not move (a recheck may have written a version meanwhile → ``review_in_progress``, no row).
A busy lock (a recheck running on the chain) skips the chain without a row.

On top of that, the whole review of one chain — read, model calls, write — runs under a session-level
advisory lock ``review-run:<ticker>:<signal_date>`` held on a connection of its own (``analysis_agent_runner._run_lock``), so a
batch review, a recheck and a second recheck of the same chain never run their model calls side by side:
the later one skips (batch, no row) or is a 409 (recheck). The key differs from the transaction-level write
lock, so the review's own write transaction on another connection is not blocked by it.
"""
from __future__ import annotations
import investment_assistant.analysis.agent.runner as analysis_agent_runner
import investment_assistant.review.shadow as review_shadow
from investment_assistant.execution import plan_context as execution_plan_context
from investment_assistant.execution import chains as execution_chains
from investment_assistant.analysis.agent import evidence as agent_evidence
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.core.schema as core_schema
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import investment_assistant.execution.repo_legacy as execution_repo_legacy
import investment_assistant.review.repo as review_repo
import investment_assistant.workflow.repo as workflow_repo

import argparse
import os
import re
import sys
import time
import uuid
from contextlib import ExitStack, contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Callable, Iterable

from investment_assistant.data import identity
from investment_assistant.execution.accounts import REAL_ACCOUNT_ID
from investment_assistant.data.adapters import bars as bars_module
from investment_assistant.data import repo as data_repo
from investment_assistant.derivation.indicators import price
from investment_assistant.core import redact as families, texts as labels
from investment_assistant.review.knowledge import my_knowledge
from investment_assistant.core import tx as service
from investment_assistant.core.tx import Unavailable
from investment_assistant.core.constants import NY, BENCHMARK
from investment_assistant.views.derivation import _check_derivations
from investment_assistant.workflow.state import _moment, agent_window, _stage_finish, _stage_skip
from investment_assistant.workflow.nodes import accounts as paper_accounts
from investment_assistant.execution import timeline
from investment_assistant.execution import adoption as execution
from investment_assistant.execution import exits
from investment_assistant.execution import positions
from investment_assistant.execution import user_inputs
from investment_assistant.execution import simulation as plan_shadow_module
from investment_assistant.execution import plan_levels as plans_module
from investment_assistant.core import stages as stages_module
from investment_assistant.analysis.agent import REASON_TEXT as AGENT_REASON_TEXT
from investment_assistant.execution import conditions as condition_registry
from investment_assistant.analysis.agent import graph as agent_graph
from investment_assistant.core import knowledge
from investment_assistant.core.llm import model as agent_model
from investment_assistant.analysis.agent import tools as agent_tools
from investment_assistant.core import tx as core_tx


REVIEW_MAX_ATTEMPTS = 3
OUTCOMES = ('maintain', 'revise', 'invalidate', 'skipped', 'error')




EMPTY_REASON = '（无理由）'


# ---------------------------------------------------------------- chain facts (timeline-2, R10, R11)

# ---------------------------------------------------------------- objects

# ---------------------------------------------------------------- facts (read phase)

# ---------------------------------------------------------------- holding status (spec §7.3, D19)



def _holding_summary(written: dict[str, Any]) -> dict[str, Any]:
    by_instruction = {kind: 0 for kind in timeline.INSTRUCTIONS}
    for row in written['rows']:
        by_instruction[row['instruction']] += 1
    return {'rows': len(written['rows']), 'by_instruction': by_instruction}


def _write_statuses(session: date, run_id: str | None, *, now: datetime, excluded: dict, unavailable: str | None,
                    summary: dict[str, Any], acc: stages_module.StageAcc | None, detail: dict[str, Any]) -> None:
    """The review stage's last step: the day's holding-status rows. Never fails the stage; a ticker that could not
    be written is a ``failures`` entry of the stage (and ``plan_review check`` reports its missing row)."""
    skipped = {(item['ticker'], item['signal_date']): item['reason'] for item in detail['excluded']}
    try:
        with core_tx._tx() as conn:
            if not core_schema.executable_ready(conn):
                written = {'rows': [], 'failures': [('*', 'migration_029_missing')]}
            else:
                written = execution_chains.write_holding_status(conn, session, run_id, now=now, skipped=skipped,
                                               unavailable=unavailable)
    except service.Unavailable:
        raise
    except Exception as exc:            # noqa: BLE001 - the statuses are accounting of the day; the stage stands
        written = {'rows': [], 'failures': [('*', families.sanitize_reason(exc))]}
    detail['holding_status'] = _holding_summary(written)
    summary['review']['holding_status'] = detail['holding_status']
    for ticker, reason in written['failures']:
        summary['errors'].append(f'{ticker} holding_status: {reason}')
        if acc is not None:
            acc.fail(ticker, f'holding_status: {reason}')
    if acc is not None:
        acc.detail['holding_status'] = detail['holding_status']


# ---------------------------------------------------------------- the stage

def _empty_review() -> dict[str, Any]:
    return {'objects': 0, 'outcomes': {outcome: 0 for outcome in OUTCOMES}, 'tokens': {'input': 0, 'output': 0}}


def run_stage(session: date, *, now: datetime, deps, summary: dict[str, Any], started: float,
              clock: Callable[[], float], run_id: str | None, acc: stages_module.StageAcc | None,
              timeline_ready: bool | None = None) -> str | None:
    """The batch's review stage (spec §7.1): ``summary['review'] = {objects, outcomes, tokens, holding_status}``
    (plus ``quota_exhausted: True`` once the quota ran out) and, with ``acc``, the stage accounting including the
    derivation check of this run's new versions. After the reviews every held ticker gets its ``holding_status``
    row (D19) — also when the agent is unavailable and nothing is reviewed.

    Returns a whole-stage skip reason for the caller to record — ``migration_026_missing`` (the agent
    stage already reported it in ``summary['errors']``) or ``agent_unavailable: <reason>`` when the agent
    is unavailable and there is nothing to review — else None (finish the stage)."""
    t_stage = clock()
    review = summary['review'] = _empty_review()
    settings = deps.agent_settings if deps.agent_settings is not None else agent_model.settings_from_env()
    with core_tx._tx() as conn:
        ready = core_schema.timeline_ready(conn) if timeline_ready is None else timeline_ready
        objects, excluded = execution_chains.review_objects(conn, session, now=now) if ready else ([], None)
    if not ready:
        return 'migration_026_missing'
    summary['errors'].extend(f'{ticker} review: {reason}' for ticker, reason in excluded['failures'])
    unavailable = agent_model.unavailable_reason(settings)
    review['objects'] = len(objects)
    detail = {'outcomes': dict(review['outcomes']), 'decided_by': {'agent': 0, 'host': 0},
              'signals': {kind: 0 for kind in timeline.SIGNAL_KINDS_V2},
              'modes': {'entry': 0, 'holding': 0}, 'ended': excluded['ended'],
              'exiting': excluded['exiting'], 'excluded': list(excluded['excluded']),
              'holdings_without_chain': excluded['holdings_without_chain'], 'tokens': review['tokens']}
    if acc is not None:
        acc.input(len(objects))
        for reason, ticker in excluded['skips']:
            acc.skip(reason, ticker)
        for ticker, reason in excluded['failures']:
            acc.fail(ticker, reason)
        acc.outputs.update({'review_ids': [], 'plan_ids': [], 'agent_run_ids': []})
        acc.detail.update(detail)
        if unavailable is not None and objects:
            acc.degrade('agent_unavailable', unavailable)
    for obj in objects:
        detail['modes'][obj['mode']] += 1
    if unavailable is not None and not objects:
        _write_statuses(session, run_id, now=now, excluded=excluded, unavailable=unavailable, summary=summary,
                        acc=acc, detail=detail)
        if acc is not None:
            acc.duration_ms += (clock() - t_stage) * 1000
        return f'agent_unavailable: {unavailable}'

    quota = {'exhausted': bool((summary.get('agent') or {}).get('quota_exhausted')),
             'lost': (summary.get('agent') or {}).get('connection_lost')}
    sec_known = not (isinstance(summary.get('filings'), dict) and 'error' in summary['filings'])
    for obj in objects:
        try:
            out = analysis_agent_runner.run_chain_review({**obj, 'filings_known': sec_known}, session=session, now=now, trigger='batch',
                               run_id=run_id, deps=deps, settings=settings, started=started, clock=clock,
                               quota=quota)
        except service.Unavailable:
            raise
        except Exception as exc:    # §13: one chain's surprise never stops the stage
            summary['errors'].append(f"{obj['ticker']} review: {families.sanitize_reason(exc)}")
            detail['excluded'].append(execution_chains._excluded(obj, 'error'))      # no row: say why in the holding status
            if acc is not None:
                acc.fail(obj['ticker'], exc)
            continue
        _record(obj, out, review=review, detail=detail, acc=acc)
        if (out.get('detail') or {}).get('write_error'):
            summary['errors'].append(f"{obj['ticker']} review: {out['detail']['write_error']}")

    if quota['exhausted']:
        review['quota_exhausted'] = True                 # the retro stage after this one honours it (no draft, no attempt)
    detail['outcomes'] = dict(review['outcomes'])
    _write_statuses(session, run_id, now=now, excluded=excluded, unavailable=unavailable, summary=summary, acc=acc,
                    detail=detail)
    if acc is not None:
        acc.detail.update(detail)
        _check_derivations(acc, acc.outputs['plan_ids'], summary)
        acc.duration_ms += (clock() - t_stage) * 1000
    return None


def _record(obj: dict, out: dict, *, review: dict, detail: dict, acc: stages_module.StageAcc | None) -> None:
    """Fold one chain's result into summary['review'] and the stage accumulator."""
    ticker = obj['ticker']
    if 'skipped_without_row' in out:
        detail['excluded'].append(execution_chains._excluded(obj, out['skipped_without_row']))
        if acc is not None:
            acc.skip(out['skipped_without_row'], ticker)
        return
    outcome = out['outcome']
    review['outcomes'][outcome] += 1
    review['tokens']['input'] += out['input_tokens'] or 0
    review['tokens']['output'] += out['output_tokens'] or 0
    detail['decided_by'][out['decided_by']] += 1
    for item in out['signals'] or []:
        if item.get('fired') and item.get('kind') in detail['signals']:
            detail['signals'][item['kind']] += 1
    if acc is None:
        return
    acc.ref('review_ids', out['id'])
    for run in (out['review_agent_run_id'], out['revision_agent_run_id']):
        if run is not None:
            acc.ref('agent_run_ids', str(run))
    if out['new_plan_id'] is not None:
        acc.ref('plan_ids', str(out['new_plan_id']))
    if outcome in execution_repo.EFFECTIVE_REVIEW_OUTCOMES:
        acc.output()
    if outcome == 'skipped':
        acc.skip(out['reason'], ticker)
    elif outcome == 'error':
        acc.fail(ticker, out['reason'])
    elif outcome == 'revise' and out['new_plan_id'] is None:
        acc.degrade('revision_failed', f"{ticker}: {(out['detail'] or {}).get('revision_status')}")


# ---------------------------------------------------------------- recheck (spec §8.2, D14)

MIGRATION_MISSING = '迁移 026 未执行'


class RecheckConflict(RuntimeError):
    """A recheck clashes with the chain's state (HTTP 409); ``reason`` is ``chain_not_active``, ``chain_exiting``
    (the chain has given its exit instruction, R10), ``agent_unavailable: <reason>`` or ``review_in_progress``.
    Nothing is written."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _ticker(ticker: Any) -> str:
    identity.stock_id(ticker if isinstance(ticker, str) else '')     # ValueError for anything not a US ticker
    return ticker.strip().upper()


def _require_ready(conn) -> None:
    if not core_schema.timeline_ready(conn):
        raise service.Unavailable(MIGRATION_MISSING)


def _precheck(ticker: str, signal_date: date, *, session: date, now: datetime, settings,
              run_lock: bool = True) -> None:
    """§8.2's preconditions in order, in one read-only transaction: migration 026 (Unavailable), the chain
    exists (LookupError) and is entry / holding at S (an exiting chain → ``chain_exiting``, R10; any other state
    → ``chain_not_active``), the agent is available; then — with ``run_lock`` — no review is running on the chain
    (RecheckConflict): its run lock is only tried and released again; the recheck itself takes it for its whole
    run. The transaction-level write lock is never probed here: the run lock already excludes a concurrent
    review, and a probe landing during the batch's write transaction would make the batch drop its row after its
    model calls."""
    with core_tx.read_only_tx() as conn:
        _require_ready(conn)
        status = execution_chains.chain_status(conn, ticker, signal_date, session=session, now=now)
        if status is None:
            raise LookupError(f'该单元没有 agent 计划链：{ticker} {signal_date}')
        if status['state']['state'] == 'exiting':
            raise RecheckConflict('chain_exiting')
        if status['state']['state'] not in execution_chains.REVIEWABLE_STATES:
            raise RecheckConflict('chain_not_active')
        unavailable = agent_model.unavailable_reason(settings)
        if unavailable is not None:
            raise RecheckConflict(f'agent_unavailable: {unavailable}')
    if run_lock:
        with analysis_agent_runner._run_lock(ticker, signal_date) as got:
            if not got:
                raise RecheckConflict('review_in_progress')


def recheck_check(ticker: str, signal_date: date, *, now: datetime) -> None:
    """The synchronous part of ``POST …/recheck``: raises service.Unavailable (503), LookupError (404),
    ValueError for a bad ticker (400) or RecheckConflict (409); returns None when a recheck may start.
    S is the latest closed trading day at ``now`` (the click time)."""
    ticker = _ticker(ticker)
    _precheck(ticker, signal_date, session=bars_module.latest_closed_session(now), now=now,
              settings=agent_model.settings_from_env())


def recheck(ticker: str, signal_date: date, *, now: datetime, deps=None, run_id: str | None = None) -> dict:
    """Review one chain now, on request (spec §8.2): ``trigger = 'recheck'``, cutoff = ``now`` — the click
    time captured by the caller, never when this runs — and S = the latest closed trading day at ``now``.
    The quiet-day maintain (§7.4 step 2) is skipped, so the model is always asked unless a met invalidation
    condition (or, holding, a touched stop / a met time stop) decides it for the host; REVIEW_MAX_ATTEMPTS does
    not apply, the token cap and quota do. New versions get ``origin = 'recheck'``. The chain's run lock is
    taken first and held to the end (a background job may start later than its request: a review that got
    there first makes this a RecheckConflict before any model call), then the preconditions are checked again.
    A chain that moved while the model ran raises RecheckConflict too; its model runs are kept as ``skipped``
    runs with their tokens. An exiting chain is a ``chain_exiting`` conflict (R10). Returns the written
    plan_reviews row."""
    ticker = _ticker(ticker)
    deps = deps or service.Deps()
    settings = deps.agent_settings if deps.agent_settings is not None else agent_model.settings_from_env()
    session = bars_module.latest_closed_session(now)
    with analysis_agent_runner._run_lock(ticker, signal_date) as got:
        if not got:
            raise RecheckConflict('review_in_progress')
        _precheck(ticker, signal_date, session=session, now=now, settings=settings, run_lock=False)
        clock = deps.clock or time.monotonic
        out = analysis_agent_runner._review_chain({'ticker': ticker, 'signal_date': signal_date}, session=session, now=now,
                            trigger='recheck', run_id=run_id, deps=deps, settings=settings, started=clock(),
                            clock=clock, quota={'exhausted': False})
    if 'skipped_without_row' in out:
        raise RecheckConflict(out['skipped_without_row'])
    return out


# ---------------------------------------------------------------- read views (spec §12)

def _run_view(run: dict | None) -> dict | None:
    if run is None:
        return None
    keys = ('id', 'mode', 'agent_version', 'model', 'status', 'reason', 'cutoff_at', 'output', 'counter',
            'input_tokens', 'output_tokens', 'created_at')
    return {**{k: run.get(k) for k in keys}, 'id': str(run['id'])}


def _review_row(row: dict, numbers: dict[str, int]) -> dict:
    """A stored plan_reviews row plus the version numbers it reviewed and produced."""
    new = row.get('new_plan_id')
    return {**row, 'head_version_no': numbers.get(str(row['head_plan_id'])),
            'new_version_no': numbers.get(str(new)) if new is not None else None}


def retrigger_rows(conn, ticker: str, signal_date: date, reviews: list[dict], numbers: dict[str, int], *,
                   cutoff: datetime | None = None) -> list[dict]:
    """The chain's ``chain_retriggers`` rows (oldest first, written by ``cutoff`` when given), each with the
    ``version_no`` the merge produced: that of ``new_plan_id`` of the first review whose ``detail.retrigger`` names the
    row's unit signal date (None for holding / exiting merges and for failed revisions). ``numbers`` maps plan id ->
    version number."""
    handled: dict[str, dict] = {}       # unit signal date (ISO) -> the first review that produced a version for it
    for r in reviews:
        if r['new_plan_id'] is not None:
            for unit_day in ((r.get('detail') or {}).get('retrigger') or {}).get('unit_signal_dates') or []:
                handled.setdefault(unit_day, r)
    out = []
    for row in execution_repo.chain_retriggers_for(conn, ticker, chain_signal_date=signal_date, cutoff=cutoff):
        found = handled.get(row['unit_signal_date'].isoformat())
        out.append({**row, 'version_no': numbers.get(str(found['new_plan_id'])) if found else None})
    return out


def _version_execution(plan: dict | None, status: dict, session: date) -> dict | None:
    """What the fills made of one version (R13, `execution-1`): ``{status}`` and, for an adopted one, the day (MM-DD)
    and size of the first fill linked to it. None for a version without a plan row."""
    if plan is None:
        return None
    state = status['state']['state']
    code = execution.status(plan, status['buys'], session=session, chain_ended=state not in execution_chains.ACTIVE_STATES,
                            chain_in_entry=state == 'entry')
    out: dict[str, Any] = {'status': code}
    if code == 'adopted':
        mine = [b for b in status['buys'] if b.get('voided_at') is None and str(b.get('plan_id')) == str(plan['id'])
                and plan['valid_from'] <= b['executed_at'].astimezone(NY).date() <= plan['valid_until']]
        first = min(mine, key=lambda b: (b['executed_at'], str(b['id'])))
        out.update(date=first['executed_at'].astimezone(NY).strftime('%m-%d'),
                   quantity=format(Decimal(str(first['quantity'])).normalize(), 'f'))
    return out


def chain_view(ticker: str, signal_date: date, *, now: datetime) -> dict:
    """One chain read at S = the latest closed trading day at ``now`` (spec §12 GET chains): ``state``
    (``timeline.chain_state_v2``'s ``{state, mode, active, basis, basis_ref, timeline_version, renewable}``),
    every version (plan row, the generating run's output and counter, ``changes``, host ``diff``, the
    shadow simulation of that version's own levels: ``plan-shadow-2`` for a version with execution fields, with the ``plan-shadow-1`` result as ``shadow_v1``, else ``plan-shadow-1``), every review row, and the user inputs that apply
    to the chain. v2.7 adds ``knowledge_refs`` to each version: the knowledge versions it cited. v2.9 adds
    ``retriggers`` (the units merged into the chain, oldest first, each with the version it produced:
    ``version_no`` of the review that handled it), ``holding_status`` (the chain's rows) and ``notes`` (the chain's
    plan notes); the last two are empty without migration 029. Read-only, no external source.
    ValueError for a bad ticker, LookupError without an agent chain, Unavailable without migration 026."""
    ticker = _ticker(ticker)
    session = bars_module.latest_closed_session(now)
    with core_tx.read_only_tx() as conn:
        _require_ready(conn)
        status = execution_chains.chain_status(conn, ticker, signal_date, session=session, now=now)
        if status is None:
            raise LookupError(f'该单元没有 agent 计划链：{ticker} {signal_date}')
        head_id = str(status['head']['plan']['id'])
        ticker_bars = data_repo.read_bars(conn, ticker, session, cutoff_at=now, limit=400)
        spy = data_repo.read_bars(conn, BENCHMARK, session, cutoff_at=now, limit=400)
        splits = data_repo.split_events(conn, ticker)
        numbers = {str(v['plan_id']): v['version_no'] for v in status['versions']}
        versions = []
        for version in status['versions']:
            plan = execution_repo.plan_by_id(conn, version['plan_id'])
            run = data_repo.agent_run_by_id(conn, version['agent_run_id'])
            output = (run or {}).get('output') or {}
            found = (review_shadow.shadow_outcomes(plan, ticker_bars, spy, splits=splits) if plan is not None
                     else {'outcome': None, 'shadow_version': None, 'shadow_v1': None})
            executed = _version_execution(plan, status, session)
            versions.append({
                'version_no': version['version_no'], 'plan_id': str(version['plan_id']),
                'origin': version['origin'],
                'supersedes_plan_id': (str(version['supersedes_plan_id'])
                                       if version['supersedes_plan_id'] is not None else None),
                'agent_run_id': str(version['agent_run_id']), 'cutoff_at': version['cutoff_at'],
                'as_of_session': version['as_of_session'], 'created_at': version['created_at'],
                'is_head': str(version['plan_id']) == head_id, 'diff': version['diff'],
                'changes': output.get('changes') or [], 'plan': plan, 'run': _run_view(run),
                'execution': executed, 'shadow': found['outcome'], 'shadow_version': found['shadow_version'],
                'shadow_v1': found['shadow_v1']})
        reviews = [_review_row(row, numbers) for row in status['reviews']]
        inputs = user_inputs.view(user_inputs.ticker_rows(conn, ticker), signal_date)
        refs = data_repo.plan_knowledge_refs(conn, [v['plan_id'] for v in versions])      # v2.7 §6.2
        for version in versions:
            version['knowledge_refs'] = refs.get(version['plan_id'], [])
        retriggers, holding, notes = [], [], []
        if core_schema.executable_ready(conn):
            retriggers = retrigger_rows(conn, ticker, signal_date, status['reviews'], numbers)
            holding = execution_repo.holding_status_for(conn, ticker=ticker, chain_signal_date=signal_date)
            notes = execution_repo.plan_notes_for(conn, ticker, chain_signal_date=signal_date)
    return {'ticker': ticker, 'signal_date': signal_date, 'session': session,
            'state': status['state'], 'head_plan_id': head_id,
            'version_no': status['head']['version']['version_no'],
            'latest_review': execution_chains._review_view(status['latest_review']),
            'shadow_version': plan_shadow_module.SHADOW_VERSION,
            'versions': versions, 'reviews': reviews, 'inputs': inputs,
            'retriggers': retriggers, 'holding_status': holding, 'notes': notes}


def reviews_view(session: date) -> dict:
    """Every review row of review session S (batch and recheck, oldest first) with the version numbers
    it reviewed and produced. Read-only; Unavailable without migration 026."""
    with core_tx.read_only_tx() as conn:
        _require_ready(conn)
        rows = execution_repo.reviews_on(conn, session)
        numbers: dict[str, int] = {}
        for row in rows:
            for plan_id in (row['head_plan_id'], row['new_plan_id']):
                if plan_id is not None and str(plan_id) not in numbers:
                    version = execution_repo.version_by_plan(conn, plan_id)
                    if version is not None:
                        numbers[str(plan_id)] = version['version_no']
    return {'session': session, 'reviews': [_review_row(row, numbers) for row in rows]}


def ticker_chains(ticker: str, *, now: datetime) -> dict:
    """Every agent chain of one ticker (ended ones included), newest signal date first, read at S = the
    latest closed trading day at ``now`` (spec §11 「标的」页): ``{ticker, chains: [{signal_date, state,
    mode, version_no, head_plan_id, latest_review: {outcome, decided_by, created_at, reason} | None}]}``.
    Read-only; ValueError for a bad ticker, Unavailable without migration 026."""
    ticker = _ticker(ticker)
    session = bars_module.latest_closed_session(now)
    rows = []
    with core_tx.read_only_tx() as conn:
        _require_ready(conn)
        context = execution_chains.ticker_context(conn, ticker, now=now)
        for chain in execution_repo.all_chains(conn):
            if chain['ticker'].upper() != ticker:
                continue
            status = execution_chains.chain_status(conn, ticker, chain['signal_date'], session=session, now=now,
                                          context=context)
            if status is None:
                continue
            latest = status['latest_review']
            rows.append({'signal_date': chain['signal_date'], 'state': status['state']['state'],
                         'mode': status['state']['mode'], 'version_no': status['head']['version']['version_no'],
                         'head_plan_id': str(status['head']['plan']['id']),
                         'latest_review': ({'outcome': latest['outcome'], 'decided_by': latest['decided_by'],
                                            'created_at': latest['created_at'], 'reason': latest['reason']}
                                           if latest is not None else None)})
    rows.sort(key=lambda r: r['signal_date'], reverse=True)
    return {'ticker': ticker, 'chains': rows}


# ---------------------------------------------------------------- check (spec §12)

_CALL_REF = re.compile(r'call:(\d+)')
_USER_REF = re.compile(r'user:(\d+)')


def _refs_in(value: Any) -> list[str]:
    """Every string under a ``refs`` key, anywhere in an output / counter JSON."""
    found: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, inner in item.items():
                if key == 'refs' and isinstance(inner, list):
                    found.extend(str(ref) for ref in inner)
                else:
                    walk(inner)
        elif isinstance(item, list):
            for inner in item:
                walk(inner)

    walk(value)
    return found


def _ref_problem(ref: str, *, calls: set[int], inputs: dict[int, dict], cutoff: datetime) -> str | None:
    """None when ``ref`` holds: ``call:N`` is a tool call of the run itself, ``user:<id>`` an input of the
    ticker created at or before ``cutoff``."""
    if match := _CALL_REF.fullmatch(ref):
        return None if int(match.group(1)) in calls else f'引用 {ref} 不是生成运行的工具调用'
    if match := _USER_REF.fullmatch(ref):
        row = inputs.get(int(match.group(1)))
        if row is None:
            return f'引用 {ref} 不是该标的的用户输入'
        if row['created_at'] > cutoff:
            return (f"引用 {ref} 晚于截止时间（created_at {row['created_at'].isoformat()} > "
                    f'cutoff_at {cutoff.isoformat()}）')
        return None
    return f'引用 {ref} 无法识别'


def _stage_evidence(conn, session: date) -> tuple[datetime | None, dict[tuple[str, str], list[str]]]:
    """(batch start, exclusions) of S from the ``review`` stage rows: the earliest review-stage start time
    (None without stage rows) and (ticker, signal_date ISO) -> the reasons the stage did not review that
    chain (``detail.excluded``: fresh_version, already_reviewed, object_cap and the row-less skips)."""
    if not core_schema.stage_runs_ready(conn):
        return None, {}
    start: datetime | None = None
    out: dict[tuple[str, str], list[str]] = {}
    for row in workflow_repo.stage_runs_for_session(conn, session):
        if row['stage'] != 'review':
            continue
        if row['started_at'] is not None and (start is None or row['started_at'] < start):
            start = row['started_at']
        for item in (row.get('detail') or {}).get('excluded') or []:
            out.setdefault((str(item['ticker']).upper(), str(item['signal_date'])), []).append(item['reason'])
    return start, out


def _before_batch(version: dict, session: date, batch_start: datetime | None) -> bool:
    """Whether the version existed when the batch reviewed session S: anything generated before S, the
    signal-day version 0 of S, and a recheck version of S written before the batch started. Without any
    evidence of the batch's start, a recheck version of S is not taken as the batch's input (it must not
    mask a missing batch review). A batch review's own version (origin review, as_of S) is its output."""
    if version['as_of_session'] < session:
        return True
    if version['as_of_session'] > session:
        return False
    if version['origin'] == 'signal':
        return True
    return version['origin'] == 'recheck' and batch_start is not None and version['created_at'] < batch_start


def _check_chain(conn, chain: dict, session: date, *, now: datetime, contexts: dict[str, dict],
                 stage_start: datetime | None, excluded: dict[tuple[str, str], list[str]],
                 out: Callable[[str], None]) -> str | None:
    """One chain's completeness on S: 'complete', 'fresh', 'missing' or None (not due). The chain is judged by
    `timeline-2` as the batch saw it: the versions it knew and the reviews written before it (R10: only entry /
    holding chains are due; an exiting chain is not reviewed)."""
    ticker, signal_date = chain['ticker'], chain['signal_date']
    versions = execution_repo.chain_versions(conn, ticker, signal_date)
    reviews = execution_repo.chain_reviews(conn, ticker, signal_date)
    batch = [r for r in reviews if r['session'] == session and r['trigger'] == 'batch']
    batch_start = stage_start or min((r['created_at'] for r in batch), default=None)
    known = [v for v in versions if _before_batch(v, session, batch_start)]
    if not known:
        return None                                       # the chain was born after S
    head = max(known, key=lambda v: v['version_no'])
    tag = f"{ticker} {signal_date} 第 {head['version_no']} 版"
    if head['as_of_session'] == session:
        out(f'  {tag}：当天生成（fresh_version）')
        return 'fresh'
    prior = [r for r in reviews if r['session'] < session or (
        r['session'] == session and r['trigger'] == 'recheck' and batch_start is not None
        and r['created_at'] < batch_start)]
    if ticker not in contexts:
        contexts[ticker] = execution_chains.ticker_context(conn, ticker, now=now)
    item = execution_chains.read_chain_facts(conn, chain, session=session, now=now, context=contexts[ticker])
    chain_input = {**item['chain'], 'versions': known, 'head_plan': execution_repo.plan_by_id(conn, head['plan_id']),
                   'reviews': prior}
    state = timeline.chain_state_v2(chain_input, session=session, now=now, facts=item['facts'])['state']
    if state not in execution_chains.REVIEWABLE_STATES:
        out(f'  {tag}：{state}，不需复查')
        return None
    reasons = excluded.get((ticker.upper(), signal_date.isoformat()), [])
    if 'object_cap' in reasons:
        out(f'  {tag} {state}：超出复查上限（object_cap）')
        return None
    effective = [r for r in batch if r['outcome'] in execution_repo.EFFECTIVE_REVIEW_OUTCOMES]
    if len(effective) == 1:
        out(f"  {tag} {state}：有效批处理复查 1 条（{effective[0]['outcome']}）")
        return 'complete'
    why = [f"{'' if r['trigger'] == 'batch' else '重新复查 '}{r['outcome']}（{r['reason']}）"
           for r in reviews if r['session'] == session and r['outcome'] not in execution_repo.EFFECTIVE_REVIEW_OUTCOMES]
    why += [f'阶段跳过（{reason}）' for reason in reasons]
    if len(effective) > 1:
        why.insert(0, f'有效批处理复查 {len(effective)} 条')
    out(f"缺有效复查  {tag} {state}：{'；'.join(why) or '无复查记录'}")
    return 'missing'


def _check_unique_active(conn, session: date, *, now: datetime, contexts: dict[str, dict],
                         out: Callable[[str], None]) -> list[str]:
    """Check ① (spec §9.2): a ticker with an active (entry / holding / exiting) chain whose first version is
    `agent-3` has no other active chain — an older v2.5–v2.8 chain overlapping it counts too (D8, §4.5). Old
    tickers (no `agent-3` chain) keep whatever they had."""
    problems = []
    for ticker in sorted({c['ticker'] for c in execution_repo.all_chains(conn)}):
        if ticker not in contexts:
            contexts[ticker] = execution_chains.ticker_context(conn, ticker, now=now)
        first = {c['signal_date']: c['first_schema_version'] for c in contexts[ticker]['chains']}
        active = [i for i in execution_chains.ticker_chain_items(conn, ticker, session=session, now=now, context=contexts[ticker])
                  if i['state']['state'] in execution_chains.ACTIVE_STATES]
        modern = [i for i in active if first.get(i['chain']['signal_date']) == timeline.AGENT_V3]
        if modern and len(active) > 1:
            names = '、'.join(f"{i['chain']['signal_date']} {i['state']['state']}" for i in active)
            problems.append(f'{ticker}：同一时间有 {len(active)} 条活跃链（{names}）')
    out(f"唯一活跃链：{'通过' if not problems else f'{len(problems)} 处不一致'}")
    return problems


def _check_retriggers(conn, session: date, *, now: datetime, contexts: dict[str, dict],
                      excluded: dict[tuple[str, str], list[str]], out: Callable[[str], None]) -> list[str]:
    """Check ② (spec §9.2): every merge made on S (``chain_retriggers.unit_signal_date = S``) shows up as a
    ``retrigger`` signal on a review row of that chain on S — unless the chain was left out of the review (an
    entry in the stage's ``detail.excluded``) or has given its exit instruction (no review; the status row
    carries the signal)."""
    fired = {(r['ticker'], r['signal_date']) for r in execution_repo.reviews_on(conn, session)
             if any(s.get('kind') == 'retrigger' and s.get('fired') for s in r['signals'] or [])}
    problems = []
    for row in execution_repo.retriggers_on(conn, session):
        key = (row['ticker'], row['chain_signal_date'])
        if key in fired or excluded.get((row['ticker'].upper(), row['chain_signal_date'].isoformat())):
            continue
        ticker = row['ticker']
        if ticker not in contexts:
            contexts[ticker] = execution_chains.ticker_context(conn, ticker, now=now)
        item = execution_chains.read_chain_facts(conn, {'ticker': ticker, 'signal_date': row['chain_signal_date']}, session=session,
                                now=now, context=contexts[ticker])
        if item is not None and item['state']['state'] == 'exiting':
            continue
        problems.append(f"{ticker} 再次触发（{row['unit_signal_date']} 并入 {row['chain_signal_date']}）"
                        '当天没有带 retrigger 信号的复查记录')
    out(f"再次触发：{'通过' if not problems else f'{len(problems)} 处缺复查记录'}")
    return problems


def _run_cutoff(conn, session: date) -> datetime | None:
    """The cutoff the session's latest batch froze: the newest ``cutoff_at`` of its batch review rows, else the
    ``created_at`` of its retrigger rows (the `plan` node stamps them with the cutoff). None when neither exists."""
    found = [r['cutoff_at'] for r in execution_repo.reviews_on(conn, session) if r['trigger'] == 'batch']
    found += [r['created_at'] for r in execution_repo.retriggers_on(conn, session)]
    return max(found) if found else None


def _check_holding_status(conn, session: date, *, cutoff: datetime, out: Callable[[str], None]) -> list[str]:
    """Check ③ (spec §9.2): every ticker with open lots on S has exactly one ``holding_status`` row for S (the
    unique key allows at most one; a missing one is the failure D19 exists to prevent)."""
    have = {r['ticker'] for r in execution_repo.holding_status_for(conn, session=session)}
    held = execution_chains._held_tickers(conn, cutoff)
    problems = [f'{ticker}：{session} 有未平批次但没有持仓状态行' for ticker in held if ticker not in have]
    out(f"持仓状态：{len(held)} 个持仓，{len(held) - len(problems)} 行齐全 → "
        f"{'通过' if not problems else f'缺 {len(problems)} 行'}")
    return problems


def _check_versions(conn, session: date, inputs_of: Callable[[str], dict[int, dict]]) -> tuple[int, list[str]]:
    """(new versions of S, problems): each version generated on S points at the previous version, carries
    its generating run's cutoff_at, and every ref of that run's output and counter holds; every review row
    of S cites only its own review run's calls and inputs created by its cutoff."""
    problems: list[str] = []
    count = 0
    for chain in execution_repo.all_chains(conn):
        ticker, signal_date = chain['ticker'], chain['signal_date']
        versions = execution_repo.chain_versions(conn, ticker, signal_date)
        by_no = {v['version_no']: v for v in versions}
        for version in versions:
            if version['as_of_session'] != session:
                continue
            count += 1
            tag = f"{ticker} {signal_date} 第 {version['version_no']} 版 {str(version['plan_id'])[:8]}"
            previous = by_no.get(version['version_no'] - 1)
            expected = str(previous['plan_id']) if previous is not None else None
            actual = str(version['supersedes_plan_id']) if version['supersedes_plan_id'] is not None else None
            if version['version_no'] >= 1 and actual != expected:
                problems.append(f'{tag}：supersedes_plan_id 没有指向上一版')
            run = data_repo.agent_run_by_id(conn, version['agent_run_id'])
            if run is None or run['cutoff_at'] != version['cutoff_at']:
                problems.append(f'{tag}：cutoff_at 与生成运行不一致')
            if run is None:
                continue
            calls = {c['seq'] for c in analysis_repo.agent_tool_calls(conn, run['id'])}
            for ref in _refs_in(run['output']) + _refs_in(run['counter']):
                if problem := _ref_problem(ref, calls=calls, inputs=inputs_of(ticker), cutoff=version['cutoff_at']):
                    problems.append(f'{tag}：{problem}')
    for row in execution_repo.reviews_on(conn, session):
        tag = f"{row['ticker']} {row['signal_date']} 复查 {row['id']}"
        calls = ({c['seq'] for c in analysis_repo.agent_tool_calls(conn, row['review_agent_run_id'])}
                 if row['review_agent_run_id'] is not None else set())
        refs = [str(r) for r in row['refs'] or []] + [f'user:{i}' for i in row['user_input_ids'] or []]
        for ref in refs:
            if problem := _ref_problem(ref, calls=calls, inputs=inputs_of(row['ticker']), cutoff=row['cutoff_at']):
                problems.append(f'{tag}：{problem}')
    return count, problems


def check(session: date, out: Callable[[str], None] = print, *, now: datetime | None = None) -> int:
    """§12 ``check --session``: every chain due on S — entry / holding as of the head the batch saw (the
    batch's start is the review stage row's ``started_at``, else its first review row), not generated on S
    (``fresh_version``, accepted) and not capped (``object_cap`` in the stage's ``detail.excluded``) — has
    exactly one effective batch review; plus the version and ref checks of ``_check_versions`` and, since v2.9
    (spec §9.2), three more: ① one active chain per ticker (agent-3 chains), ② every merge on S has a review row
    with the ``retrigger`` signal, ③ every held ticker has its ``holding_status`` row. 0 when everything holds, 1
    otherwise. Read-only; Unavailable (exit 2 in ``main``) without a database or migration 026 / 029.
    Decisions and fills are read as stored (``now`` defaults to the wall clock: nothing recorded is cut)."""
    now = now or datetime.now(UTC)
    cache: dict[str, dict[int, dict]] = {}
    contexts: dict[str, dict] = {}
    with core_tx.read_only_tx() as conn:
        _require_ready(conn)
        if not core_schema.executable_ready(conn):
            raise service.Unavailable('迁移 029 未执行')

        def inputs_of(ticker: str) -> dict[int, dict]:
            if ticker not in cache:
                cache[ticker] = {row['id']: row for row in user_inputs.ticker_rows(conn, ticker)}
            return cache[ticker]

        stage_start, excluded = _stage_evidence(conn, session)
        out(f'复查完整性 {session}')
        verdicts = [_check_chain(conn, chain, session, now=now, contexts=contexts, stage_start=stage_start,
                                 excluded=excluded, out=out)
                    for chain in execution_repo.all_chains(conn)]
        new_versions, problems = _check_versions(conn, session, inputs_of)
        if core_schema.accounts_ready(conn):
            problems += paper_accounts.check_accounts(conn, session=session, now=now)
        problems += _check_unique_active(conn, session, now=now, contexts=contexts, out=out)
        problems += _check_retriggers(conn, session, now=now, contexts=contexts, excluded=excluded, out=out)
        problems += _check_holding_status(conn, session, cutoff=_run_cutoff(conn, session) or stage_start or now,
                                           out=out)
    for problem in problems:
        out(f'问题  {problem}')
    complete, missing, fresh = (verdicts.count(v) for v in ('complete', 'missing', 'fresh'))
    passed = not missing and not problems
    out(f"汇总：{session} 应复查 {complete + missing} 条，齐全 {complete} 条，缺 {missing} 条；当天生成 {fresh} 条；"
        f"新版 {new_versions} 份，问题 {len(problems)} 处 → {'通过' if passed else '不通过'}")
    return 0 if passed else 1


# ---------------------------------------------------------------- command line (spec §12)

def _cli_now() -> datetime:
    return datetime.now(UTC)


def _cli_deps():
    return service.Deps()


def _date_arg(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'日期格式应为 YYYY-MM-DD：{text}') from None


def _ticker_arg(text: str) -> str:
    try:
        return _ticker(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'不是美股代码：{text}') from None


def _money(value: Any) -> str:
    return '-' if value is None else f'{Decimal(str(value)):.2f}'


def _beijing(moment: datetime | None) -> str:
    from investment_assistant.review.period_digest import SH      # the workbench's Beijing-time constant

    return moment.astimezone(SH).strftime('%Y-%m-%d %H:%M') if moment is not None else '-'


def _chain_cmd(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    try:
        view = chain_view(args.ticker, args.date, now=_cli_now())
    except LookupError as exc:
        out(str(exc))
        return 1
    state = view['state']
    out(f"链 {view['ticker']} {view['signal_date']}  状态 {state['state']}（{state['basis']}）  "
        f"复查交易日 {view['session']}  最新第 {view['version_no']} 版")
    for v in view['versions']:
        plan = v['plan'] or {}
        shadow = (v['shadow'] or {}).get('status', '-')
        out(f"第 {v['version_no']} 版  {v['origin']}  {v['plan_id'][:8]}  生成 {_beijing(v['created_at'])}  "
            f"截止 {_beijing(v['cutoff_at'])}  入场 {_money(plan.get('entry_low'))}–{_money(plan.get('entry_high'))}  "
            f"止损 {_money(plan.get('stop'))}  T1 {_money(plan.get('target1'))}  "
            f"有效 {plan.get('valid_from')}…{plan.get('valid_until')}  {plan.get('status')}  影子 {shadow}")
        if v['diff']:
            out('  差异：' + '；'.join(f"{d['field']} {d['before']} → {d['after']}" for d in v['diff']))
    for r in view['reviews']:
        produced = f" → 第 {r['new_version_no']} 版" if r['new_version_no'] is not None else ''
        failed = (r['detail'] or {}).get('revision_status')
        out(f"复查 {r['session']}  {r['trigger']}  {r['outcome']}（{r['decided_by']}）  第 {r['head_version_no']} 版"
            f"{produced}  {r['reason']}{f'  修订未通过：{failed}' if failed else ''}")
    for row in view['inputs']:
        body = ' '.join(row['body'].split())[:40]
        out(f"输入 {row['ref']}  {row['kind']}  {row['status']}  {_beijing(row['created_at'])}  {body}")
    for row in view['retriggers']:
        produced = f" → 第 {row['version_no']} 版" if row['version_no'] is not None else ''
        out(f"并入 {row['unit_signal_date']}  {'、'.join(row['rule_ids'])}  链状态 {row['chain_state']}"
            f"{'  入场期上限 ' + str(row['entry_cap']) if row['entry_cap'] else ''}{produced}")
    for row in view['holding_status']:
        out(f"持仓状态 {row['session']}  持有 {row['held_sessions']} 天  止损 {_money(row['stop'])}  "
            f"{timeline.INSTRUCTION_TEXT[row['instruction']]}  {row['reason']}")
    for row in view['notes']:
        out(f"备注 #{row['id']}  {row['status']}  {_beijing(row['created_at'])}  {' '.join(row['body'].split())[:60]}")
    return 0


def _recheck_cmd(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    run_id = f'plan_recheck-cli-{uuid.uuid4().hex[:8]}'
    try:
        row = recheck(args.ticker, args.date, now=_cli_now(), deps=_cli_deps(), run_id=run_id)
    except (RecheckConflict, LookupError) as exc:
        out(f'不能重新复查：{exc}')
        return 1
    out(f"复查记录 {row['id']}  {row['session']}  {row['outcome']}（{row['decided_by']}）  {row['reason']}")
    out(f"新版：{row['new_plan_id'] or '无'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """``chain`` prints one chain (read-only; 1 without a chain); ``recheck`` reviews one chain now
    (writes; 1 on a 409-type conflict or without a chain); ``check`` verifies one session's reviews
    (read-only; 1 on any violation). Exit 2 for bad arguments (argparse, including a ticker that is not a US
    ticker) or an unavailable database / migration 026; any other error is a bug and propagates."""
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.workflow.nodes.review',
                                     description='Agent 计划链：查看、重新复查与复查完整性核对。')
    commands = parser.add_subparsers(dest='command', required=True)
    for name, text in (('chain', '打印链状态、各版本与复查记录（只读）'), ('recheck', '立即重新复查一条链（写）')):
        sub = commands.add_parser(name, help=text)
        sub.add_argument('--ticker', type=_ticker_arg, required=True, help='标的')
        sub.add_argument('--date', type=_date_arg, required=True, help='信号日，YYYY-MM-DD')
    checker = commands.add_parser('check', help='核对一个交易日的复查完整性（只读）')
    checker.add_argument('--session', type=_date_arg, required=True, help='复查交易日，YYYY-MM-DD')
    args = parser.parse_args(argv)
    try:
        if args.command == 'check':
            return check(args.session, print)
        return (_chain_cmd if args.command == 'chain' else _recheck_cmd)(args, print)
    except service.Unavailable as exc:
        print(f'数据库不可用：{exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())


def run(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``review`` node (v2.5 §7): the daily review shares the agent node's budget window; a failure of the whole
    stage is booked as its ``failed`` row and the chain carries on (as in v2.7)."""
    now, session = _moment(state)
    summary = state['summary']
    started, clock = agent_window(state, ctx)
    try:
        skipped = run_stage(session, now=now, deps=ctx.deps, summary=summary, started=started,
                                        clock=clock, run_id=state.get('run_id'), acc=acc,
                                        timeline_ready=state.get('timeline_ready'))
    except Unavailable:
        raise
    except Exception as exc:
        reason = families.sanitize_reason(exc)
        summary['errors'].append(f'review: {reason}')
        _stage_finish(ctx, 'review', failed=reason or type(exc).__name__)
    else:
        if skipped is not None:
            _stage_skip(ctx, 'review', skipped)
    return {'summary': summary}
