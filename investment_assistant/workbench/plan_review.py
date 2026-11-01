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
advisory lock ``review-run:<ticker>:<signal_date>`` held on a connection of its own (``_run_lock``), so a
batch review, a recheck and a second recheck of the same chain never run their model calls side by side:
the later one skips (batch, no row) or is a 409 (recheck). The key differs from the transaction-level write
lock, so the review's own write transaction on another connection is not blocked by it.
"""
from __future__ import annotations

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
from investment_assistant.workbench.accounts import REAL_ACCOUNT_ID
from investment_assistant.workbench import bars as bars_module
from investment_assistant.workbench import (execution, exits, families, labels, my_knowledge, positions, repository,
                                               service, timeline, user_inputs)
from investment_assistant.workbench import plan_shadow as plan_shadow_module
from investment_assistant.workbench import plans as plans_module
from investment_assistant.workbench import stages as stages_module
from investment_assistant.workbench.agent import REASON_TEXT as AGENT_REASON_TEXT
from investment_assistant.workbench.agent import conditions as condition_registry
from investment_assistant.workbench.agent import graph as agent_graph
from investment_assistant.workbench.agent import growth_check
from investment_assistant.workbench.agent import model as agent_model
from investment_assistant.workbench.agent import tools as agent_tools

REVIEW_MAX_OBJECTS = 20
REVIEW_MAX_ATTEMPTS = 3
OUTCOMES = ('maintain', 'revise', 'invalidate', 'skipped', 'error')
REVIEWABLE_STATES = timeline.REVIEWABLE_STATES      # reviewed daily and rechecked (R10)
ACTIVE_STATES = timeline.ACTIVE_STATES              # at most one per ticker; a new trigger merges into it
ENDED_STATES = ('expired', 'invalidated', 'position_closed', 'superseded', 'closed_by_decision')
DEFAULT_REVIEW_WHEN = [{'kind': 'next_session', 'params': {}}]
EMPTY_REASON = '（无理由）'


# ---------------------------------------------------------------- chain facts (timeline-2, R10, R11)

def ticker_context(conn, ticker: str, *, now: datetime, point_in_time: bool = False, account_id=REAL_ACCOUNT_ID) -> dict[str, Any]:
    """What every chain of one ticker is judged from, read once at cutoff ``now``: the fills as the cutoff knew
    them (``list_trades(cutoff=now)``, R11), the position they build, the ticker's chains and retrigger rows, and
    the plan -> chain map of the fills' linked plans (D22). Raises ``positions.PositionError`` on a broken fill
    history; the callers read it under a savepoint.

    ``point_in_time=True`` also cuts the chains: only those whose first version existed by ``now`` (a chain opened
    later is not there yet). Readers of a past day (the plan table of an older session, the plans a back-filled fill
    may follow) read this way together with ``read_chain_facts(point_in_time=True)``."""
    ticker = ticker.upper()
    trades = repository.list_trades(conn, ticker, cutoff=now, account_id=account_id)
    book = positions.build(trades, splits=repository.split_events(conn, ticker, **({'cutoff_at': now} if str(account_id) != str(REAL_ACCOUNT_ID) else {})))
    chains = repository.chains_for_ticker(conn, ticker, cutoff=now if point_in_time else None)
    retriggers = repository.chain_retriggers_for(conn, ticker, cutoff=now)
    versions_by_plan = {v['plan_id']: v for c in chains for v in repository.chain_versions(conn, ticker, c['signal_date'])}
    plan_chain: dict[Any, Any] = {}
    for plan_id in {t['plan_id'] for t in trades if t.get('plan_id') is not None}:
        plan = repository.plan_by_id(conn, plan_id)
        if plan is not None:
            plan_chain[plan_id] = execution.chain_of_plan(plan, versions_by_plan=versions_by_plan,
                                                          retriggers=retriggers, chains=chains)
    return {'ticker': ticker, 'trades': trades, 'book': book, 'chains': chains, 'retriggers': retriggers,
            'plan_chain': plan_chain}


def read_chain_facts(conn, chain: dict[str, Any], *, session: date, now: datetime,
                     context: dict[str, Any] | None = None, point_in_time: bool = False,
                     stamped: bool = False) -> dict[str, Any] | None:
    """One chain read for ``timeline.chain_state_v2`` at session S and cutoff ``now`` (spec §4.2; the `plan` node and
    the review share it). ``chain`` needs ``ticker`` and ``signal_date``. Returns ``{'chain', 'facts', 'state',
    'buys'}`` — ``chain`` / ``facts`` are `chain_state_v2`'s inputs, ``buys`` the chain's live fills — or None
    when the unit has no agent chain. Fills come from ``list_trades(cutoff=now)`` (R11), retriggers are cut at
    ``now`` too (their ``created_at`` is the cutoff of the run that found them); ``context`` (``ticker_context``)
    saves the ticker-wide reads.

    Review rows are read *as stored* — the operational state (what the batch, a recheck and the `plan` node act
    on) must see a review a concurrent run has just written, exactly as the v2.5 reading did — so their
    ``created_at`` is not offered to the state function. ``point_in_time=True`` keeps it and reads the whole chain as
    ``now`` knew it — versions, reviews and the old decision created after the cutoff are left out, so the head is
    the version that was the head then — for a reader that asks what a past cutoff knew (a past day's plan table,
    a back-filled fill). ``stamped=True`` only keeps the reviews' ``created_at`` without cutting anything — the
    「同标的上一条链」 block, whose pure function does the cutting itself."""
    ticker, signal_date = chain['ticker'].upper(), chain['signal_date']
    versions = repository.chain_versions(conn, ticker, signal_date)
    if point_in_time:
        versions = [v for v in versions if v['created_at'] <= now]
    if not versions:
        return None
    ctx = context or ticker_context(conn, ticker, now=now, point_in_time=point_in_time)
    head_plan = repository.plan_by_id(conn, versions[-1]['plan_id'])
    buys = execution.chain_buys((ticker, signal_date), ctx['trades'], plan_chain=ctx['plan_chain'],
                                legacy_decision_ids=repository.unit_decision_ids(conn, ticker, signal_date))
    decision = repository.decision_head(conn, ticker, signal_date)
    if point_in_time and decision is not None and decision['decided_at'] > now:
        decision = None
    facts = {'decision': decision, 'buys': buys,
             'open_lots': ctx['book']['open'],
             'retriggers': [r for r in ctx['retriggers'] if r['chain_signal_date'] == signal_date],
             'later_chains': [{'signal_date': c['signal_date']} for c in ctx['chains']
                              if signal_date < c['signal_date'] <= session]}
    reviews = repository.chain_reviews(conn, ticker, signal_date)
    if point_in_time:
        reviews = [r for r in reviews if r['created_at'] <= now]
    chain_input = {'ticker': ticker, 'signal_date': signal_date, 'legacy': False, 'versions': versions,
                   'head_plan': head_plan,
                   'reviews': reviews if (point_in_time or stamped) else [{**r, 'created_at': None} for r in reviews]}
    return {'chain': chain_input, 'facts': facts, 'buys': buys,
            'state': timeline.chain_state_v2(chain_input, session=session, now=now, facts=facts)}


def ticker_chain_items(conn, ticker: str, *, session: date, now: datetime, before: date | None = None,
                       context: dict[str, Any] | None = None, point_in_time: bool = False) -> list[dict[str, Any]]:
    """``read_chain_facts`` of every chain of the ticker (oldest first); ``before`` keeps only chains whose signal
    date is earlier (the `plan` node's view: a unit never merges into its own or a later chain)."""
    ctx = context or ticker_context(conn, ticker, now=now, point_in_time=point_in_time)
    items = []
    for chain in ctx['chains']:
        if before is not None and chain['signal_date'] >= before:
            continue
        item = read_chain_facts(conn, chain, session=session, now=now, context=ctx, point_in_time=point_in_time)
        if item is not None:
            items.append(item)
    return items


def active_chain_of(conn, ticker: str, *, session: date, now: datetime, before: date | None = None,
                    context: dict[str, Any] | None = None, point_in_time: bool = False) -> dict[str, Any] | None:
    """The ticker's active chain at S (D2, R5): ``timeline.active_chain`` over its chains, or None. Result keys
    are ``timeline.active_chain``'s (``chain``, ``state``, ``chain_state``, ``renewable``)."""
    items = ticker_chain_items(conn, ticker, session=session, now=now, before=before, context=context,
                               point_in_time=point_in_time)
    return timeline.active_chain([i['chain'] for i in items], session=session, now=now,
                                 facts_by_chain={(i['chain']['ticker'], i['chain']['signal_date']): i['facts']
                                                 for i in items})


# ---------------------------------------------------------------- objects

def _chain_pool(conn, status: dict[str, Any]) -> str:
    """`pool` of the run that produced version 0 (main / extended)."""
    first = status['versions'][0] if status['versions'] else None
    run = repository.agent_run_by_id(conn, first['agent_run_id']) if first is not None else None
    return (run or {}).get('pool') or 'main'


def review_object(conn, ticker: str, signal_date: date, *, session: date, pool: str | None = None,
                  book: dict[str, Any] | None = None, now: datetime | None = None,
                  context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """One agent chain read at S as a review object, or None without a chain. Keys: ticker,
    signal_date, state, mode, pool (version 0's), head ({'plan', 'version'}), version_no, as_of_session,
    valid_until. The caller decides whether the state is reviewable (``REVIEWABLE_STATES``). With ``now`` the
    chain is judged by `timeline-2` (the review stage always passes it)."""
    status = service.chain_status(conn, ticker, signal_date, session=session, book=book, now=now, context=context)
    if status is None:
        return None
    head = status['head']
    return {'ticker': head['version']['ticker'], 'signal_date': signal_date,
            'state': status['state']['state'], 'mode': status['state']['mode'],
            'pool': pool or _chain_pool(conn, status), 'head': head,
            'version_no': head['version']['version_no'], 'as_of_session': head['version']['as_of_session'],
            'valid_until': head['plan']['valid_until']}


def _order(obj: dict[str, Any]) -> tuple:
    """Holding first, then main-list entry, extended entry last (D6); each by valid_until, ticker."""
    rank = 0 if obj['mode'] == 'holding' else (2 if obj['pool'] == 'extended' else 1)
    return rank, obj['valid_until'], obj['ticker']


def review_objects(conn, session: date, *, now: datetime) -> tuple[list[dict], dict]:
    """(objects in review order, exclusions) for session S (spec §7.2).

    Objects are the chains in state entry / holding (``REVIEWABLE_STATES``), minus a head generated on S
    (``fresh_version``) and a chain already holding an effective batch review for S (``already_reviewed``),
    capped at REVIEW_MAX_OBJECTS (``object_cap``, the tail of the order: extended entry chains go first).
    Exclusions: ``{'skips': [(reason, ticker)], 'excluded': [{ticker, signal_date, reason}] (the same exclusions
    per chain, uncapped), 'ended': {expired, invalidated, position_closed, superseded, closed_by_decision},
    'exiting': [{ticker, signal_date}] (chains in the exit phase: not reviewed, only their holding status is
    written, D17), 'failures': [(ticker, reason)], 'holdings_without_chain': [ticker]}`` (held tickers whose open
    lots belong to no agent chain at all, D7; a chain that ended under the holding still counts). A chain whose
    reading raises is read under a savepoint and only listed in ``failures``; the other chains go on. ``now``
    is the batch cutoff: fills, retriggers and reviews are read as it knew them."""
    objects: list[dict] = []
    skips: list[tuple[str, str]] = []
    excluded: list[dict] = []
    failures: list[tuple[str, str]] = []
    exiting: list[dict] = []
    ended = {state: 0 for state in ENDED_STATES}
    contexts: dict[str, dict] = {}
    chained: set[str] = set()                  # tickers whose open lots belong to some agent chain
    for chain in repository.all_chains(conn):
        ticker = chain['ticker']
        try:
            with conn.transaction():        # savepoint: a broken chain must not abort the others' reads
                if ticker not in contexts:
                    contexts[ticker] = ticker_context(conn, ticker, now=now)
                ctx = contexts[ticker]
                obj = review_object(conn, ticker, chain['signal_date'], session=session, pool=chain['pool'],
                                    now=now, context=ctx)
                reviewed = obj is not None and any(
                    r['outcome'] in repository.EFFECTIVE_REVIEW_OUTCOMES
                    for r in repository.batch_attempts(conn, obj['ticker'], obj['signal_date'], session))
                if obj is not None and _owns_open_lots(conn, ctx, chain):
                    chained.add(ticker)
        except service.Unavailable:
            raise
        except Exception as exc:            # §7.4 / §13: one chain's surprise only affects that chain
            failures.append((ticker, families.sanitize_reason(exc)))
            continue
        if obj is None:
            continue
        if obj['state'] in ended:
            ended[obj['state']] += 1
            continue
        if obj['state'] == 'exiting':
            exiting.append({'ticker': obj['ticker'], 'signal_date': obj['signal_date'].isoformat()})
            continue
        if obj['state'] not in REVIEWABLE_STATES:
            continue
        reason = ('fresh_version' if obj['as_of_session'] == session
                  else 'already_reviewed' if reviewed else None)
        if reason is not None:
            skips.append((reason, obj['ticker']))
            excluded.append(_excluded(obj, reason))
            continue
        objects.append(obj)
    objects.sort(key=_order)
    skips += [('object_cap', obj['ticker']) for obj in objects[REVIEW_MAX_OBJECTS:]]
    excluded += [_excluded(obj, 'object_cap') for obj in objects[REVIEW_MAX_OBJECTS:]]
    without = repository.open_position_tickers(conn) - chained     # D7: a chain that ended under a holding is still its chain
    return objects[:REVIEW_MAX_OBJECTS], {'skips': skips, 'excluded': excluded, 'ended': ended,
                                          'exiting': exiting, 'failures': failures,
                                          'holdings_without_chain': sorted(without)}


def _owns_open_lots(conn, ctx: dict[str, Any], chain: dict[str, Any]) -> bool:
    """Whether some open lot of the ticker was bought on this chain (its linked fills, D22, or the old route)."""
    if ctx['book']['position'] is None:
        return False
    open_ids = {str(lot['buy_trade_id']) for lot in ctx['book']['open']}
    buys = execution.chain_buys((ctx['ticker'], chain['signal_date']), ctx['trades'], plan_chain=ctx['plan_chain'],
                                legacy_decision_ids=repository.unit_decision_ids(conn, ctx['ticker'],
                                                                                 chain['signal_date']))
    return any(str(b['id']) in open_ids for b in buys)


def _excluded(obj: dict, reason: str) -> dict:
    """One chain the stage did not review on S, as recorded in the stage's ``detail.excluded``."""
    return {'ticker': obj['ticker'], 'signal_date': obj['signal_date'].isoformat(), 'reason': reason}


def chain_tickers(conn, session: date, *, now: datetime, exclude: Iterable[str],
                  failures: list[tuple[str, str]] | None = None) -> list[str]:
    """Tickers of chains in state entry / holding / exiting at S that are not in ``exclude`` (the batch passes its
    pool and holdings): collected only, never run through the rules (D21). A chain whose state cannot be
    read (under a savepoint) is appended to ``failures`` as (ticker, reason) and skipped."""
    skip = {t.upper() for t in exclude}
    out: set[str] = set()
    for chain in repository.all_chains(conn):
        if chain['ticker'].upper() in skip or chain['ticker'] in out:
            continue
        try:
            with conn.transaction():
                status = service.chain_status(conn, chain['ticker'], chain['signal_date'], session=session, now=now)
        except service.Unavailable:
            raise
        except Exception as exc:
            if failures is not None:
                failures.append((chain['ticker'], families.sanitize_reason(exc)))
            continue
        if status is not None and status['state']['state'] in ACTIVE_STATES:
            out.add(chain['ticker'])
    return sorted(out)


# ---------------------------------------------------------------- facts (read phase)

def _dec_bars(rows: list[dict]) -> list[dict]:
    return [{**b, **{k: Decimal(str(b[k])) for k in ('open', 'high', 'low', 'close')}} for b in rows]


def _filing_in(filing: dict, *, after_session: date, after_at: datetime, session: date, now: datetime) -> bool:
    """Accepted in (after_at, now]; without an acceptance time, filed in (after_session, session]."""
    accepted = filing.get('accepted_at')
    if accepted is not None:
        return after_at < accepted <= now
    return after_session < filing['filing_date'] <= session


def _retrigger_rows(conn, ticker: str, signal_date: date, *, now: datetime) -> list[dict]:
    """The chain's ``chain_retriggers`` rows written by ``now`` (R4), each with the unit's rule-baseline levels
    (``baseline``) and the candidates' frozen values (``frozen_values``) — the evidence of the ``retrigger`` signal."""
    out = []
    for row in repository.chain_retriggers_for(conn, ticker, chain_signal_date=signal_date, cutoff=now):
        baseline: dict[str, Any] = {}
        if row.get('baseline_plan_id') is not None:
            plan = repository.plan_by_id(conn, row['baseline_plan_id'])
            baseline = {key: plan[key] for key in plans_module.LEVEL_KEYS} if plan is not None else {}
        wanted = set(row.get('rule_ids') or [])
        frozen = {c['rule_id']: {k: v for k, v in (c.get('inputs') or {}).items() if k not in agent_tools._PLAN_LEVEL_KEYS}
                  for c in repository.unit_candidates(conn, ticker, row['unit_signal_date']) if c['rule_id'] in wanted}
        out.append({**row, 'baseline': baseline, 'frozen_values': frozen})
    return out


def _plan_of_buy(conn, trade: dict) -> dict | None:
    """The plan a fill follows: its ``plan_id``, else (old data) the plan its decision confirmed."""
    if trade.get('plan_id') is not None:
        return repository.plan_by_id(conn, trade['plan_id'])
    if trade.get('decision_id') is not None:
        return repository.plan_for_buy_trades(conn, [trade['id']]).get(trade['id'])
    return None


def _holding_input(conn, ctx: dict, *, ticker: str, buys: list[dict], plan: dict | None, bars: list[dict]) -> dict | None:
    """``timeline.holding_facts``' input for the ticker's position at the cutoff (R11): quantity and cost from the
    open lots, the first open buy's New York day, the bars, the effective ``plan`` and the ``initial_plan`` whose
    stop prices 1R (the plan of the chain's first fill, D20)."""
    position = ctx['book']['position']
    if position is None:
        return None
    return {'ticker': ticker, 'quantity': position['quantity'], 'avg_cost': position['avg_cost'],
            'first_buy_session': position['opened_at'].astimezone(service.NY).date(), 'bars': bars,
            'splits': repository.split_events(conn, ticker), 'plan': plan,
            'initial_plan': _plan_of_buy(conn, buys[0]) if buys else None}


def _read(conn, ticker: str, signal_date: date, *, session: date, now: datetime, trigger: str,
          filings_known: bool) -> dict[str, Any]:
    """Everything the host needs, read at cutoff ``now``; ``{'skip': reason}`` when the chain is not
    reviewable right now (not active, fresh head, already reviewed by the batch on S)."""
    context = ticker_context(conn, ticker, now=now)
    status = service.chain_status(conn, ticker, signal_date, session=session, now=now, context=context)
    if status is None or status['state']['state'] not in REVIEWABLE_STATES:
        return {'skip': 'chain_not_active'}
    head = status['head']
    version, plan = head['version'], head['plan']
    if trigger == 'batch' and version['as_of_session'] == session:
        return {'skip': 'fresh_version'}
    attempts = (repository.batch_attempts(conn, ticker, signal_date, session) if trigger == 'batch'
                else [r for r in status['reviews'] if r['session'] == session and r['trigger'] == trigger])
    if trigger == 'batch' and any(r['outcome'] in repository.EFFECTIVE_REVIEW_OUTCOMES for r in attempts):
        return {'skip': 'already_reviewed'}
    last = repository.last_effective_review(conn, plan['id'])
    since_session, since_at = timeline.since_window(version, last)
    regime_row = repository.regime_for(conn, session, cutoff_at=now)
    inner = (plan.get('inputs') or {}).get('context') or {}
    entry = repository.pool_entry_at(conn, ticker, session, cutoff_at=now)
    etf = (entry or {}).get('sector_etf') or (inner.get('sector') or {}).get('etf')
    sector = ((regime_row or {}).get('sectors') or {}).get(etf) if etf else None
    earnings = repository.latest_earnings(conn, ticker, cutoff_at=now)
    events, _ = service._filing_events(conn, ticker, session, now, since=version['as_of_session'])
    inputs = timeline.effective_inputs(repository.user_inputs_for(conn, ticker, signal_date), now)
    bars = repository.read_bars(conn, ticker, session, cutoff_at=now)
    mode = status['state']['mode']
    seen = {
        'status': status, 'head': head, 'version': version, 'plan': plan, 'mode': mode,
        'pool': _chain_pool(conn, status),
        'run': repository.agent_run_by_id(conn, version['agent_run_id']), 'last_review': last,
        'attempts': attempts, 'since_session': since_session, 'since_at': since_at,
        'bars': bars,
        'spy': repository.read_bars(conn, service.BENCHMARK, session, cutoff_at=now),
        'filings': sorted(events, key=lambda f: (f['filing_date'], str(f['id']))) if filings_known else None,
        'abnormal': [c for c in repository.read_candidates(conn, ticker, since=since_session)
                     if c['rule_id'] == 'abnormal_move'],
        'regime_label': regime_row['label'] if regime_row is not None else None,
        'sector_state': (sector or {}).get('state'),
        'earnings_date': (earnings['earnings_date'] if earnings and earnings['status'] == 'known' else None),
        'inputs_new': [r for r in inputs if r['created_at'] > since_at],
        'inputs_old': [r for r in inputs if r['created_at'] <= since_at],
        'trigger_rows': repository.unit_candidates(conn, ticker, signal_date),
        'retriggers': _retrigger_rows(conn, ticker, signal_date, now=now),
        'chain': status['chain'], 'buys': status['buys'],
        'entry_cap': timeline.entry_cap(status['chain'], status['facts']['retriggers']),
        'holding_input': (_holding_input(conn, context, ticker=ticker, buys=status['buys'], plan=plan, bars=bars)
                          if mode == 'holding' else None),
    }
    return seen


def _holding_values(seen: dict[str, Any], session: date) -> dict | None:
    """``timeline.holding_facts`` of a holding chain at S (the numbers of the status block and the host exit
    steps), or None for an entry chain."""
    if seen.get('holding_input') is None:
        return None
    return timeline.holding_facts(seen['holding_input'], session=session)


def _position_dict(values: dict | None) -> dict | None:
    if values is None:
        return None
    return {'held_sessions': values['held_sessions'], 'reached_1r': values['reached_1r'],
            't1_reached': bool(values['t1_reached']), 'mode': 'holding'}


def _assess(seen: dict[str, Any], *, session: date, now: datetime) -> tuple[list, list]:
    """(signals, conditions) of spec §7.3 from the read facts. timeline-2 adds the ``retrigger`` signal and, for a
    holding chain, the host-injected time-stop condition (``source: max_hold``, ``index: 'max_hold'``, D16)."""
    version, plan, mode = seen['version'], seen['plan'], seen['mode']
    as_of, since_session, since_at = version['as_of_session'], seen['since_session'], seen['since_at']
    bars = _dec_bars(seen['bars'])
    after_version = [b for b in bars if as_of < b['price_date'] <= session]
    since_review = [b for b in bars if since_session < b['price_date'] <= session]
    indicators = agent_tools.indicators(seen['bars'], seen['spy'])
    filings = seen['filings']
    values = _holding_values(seen, session)

    def window(after_session: date, after_at: datetime) -> list[dict] | None:
        if filings is None:
            return None
        return [f for f in filings if _filing_in(f, after_session=after_session, after_at=after_at,
                                                 session=session, now=now)]

    common = dict(session=session, mode=mode, bars_since_version=after_version,
                  entry_low=Decimal(str(plan['entry_low'])), entry_high=Decimal(str(plan['entry_high'])),
                  regime_label=seen['regime_label'], sector_state=seen['sector_state'],
                  earnings_date=seen['earnings_date'], rs={20: indicators['rs20'], 60: indicators['rs60']},
                  as_of_session=as_of, position=_position_dict(values))
    output = (seen['run'] or {}).get('output') or {}
    invalidation_facts = condition_registry.Facts(bars=after_version, filings=window(as_of, version['cutoff_at']),
                                                  **common)
    conditions = timeline.condition_results(
        invalidation=output.get('invalidation') or [],
        review_when=output.get('review_when') or DEFAULT_REVIEW_WHEN,
        invalidation_facts=invalidation_facts,
        review_facts=condition_registry.Facts(bars=since_review, filings=window(since_session, since_at), **common))
    if mode == 'holding' and values is not None:
        item = condition_registry.max_hold_condition(values['max_hold_sessions'] or plans_module.MAX_HOLD_DEFAULT)
        result, detail = condition_registry.evaluate(item, invalidation_facts)
        injected = {'list': 'invalidation', 'index': 'max_hold', 'kind': item['kind'], 'params': item['params'],
                    'source': 'max_hold', 'result': result, 'detail': detail}
        if values['detail'].get('max_hold_default'):
            injected['max_hold_default'] = True
        at = sum(1 for c in conditions if c['list'] == 'invalidation')
        conditions.insert(at, injected)
    signals = timeline.signals(head_plan=plan, mode=mode, session=session, since_session=since_session,
                               since_at=since_at, now=now, bars=seen['bars'], filings=filings,
                               abnormal=seen['abnormal'], regime_label=seen['regime_label'],
                               new_inputs=seen['inputs_new'], retriggers=seen['retriggers'])
    return signals, conditions


def _prompt_conditions(conditions: list[dict]) -> list[dict]:
    """The conditions as ``timeline.prompt_evidence`` numbers them: the injected time stop gets the next free
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
            lines.append(f'    冻结取值 {rule_id}：{timeline._json(values)}')
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
    as service._run_agent_unit does them."""
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
        except service.Unavailable:
            raise
        except Exception as exc:
            reason, detail = agent_model.classify_error(exc, settings.api_key)
            if service._checkpoint_missing(exc):
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
        raise service.Unavailable('数据库未配置')
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
        raise service.Unavailable('数据库不可达') from None


def review_chain(obj: dict, *, session: date, now: datetime, trigger: str, run_id: str | None, deps, settings,
                 started: float, clock: Callable[[], float], quota: dict) -> dict:
    """Review one chain under its run lock (a busy lock → ``{'skipped_without_row': 'review_in_progress'}``,
    nothing read, no model call); see ``_review_chain`` for the review itself."""
    with _run_lock(obj['ticker'], obj['signal_date']) as got:
        if not got:
            return {'skipped_without_row': 'review_in_progress'}
        return _review_chain(obj, session=session, now=now, trigger=trigger, run_id=run_id, deps=deps,
                             settings=settings, started=started, clock=clock, quota=quota)


def _review_chain(obj: dict, *, session: date, now: datetime, trigger: str, run_id: str | None, deps, settings,
                  started: float, clock: Callable[[], float], quota: dict) -> dict:
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
    with service._tx() as conn:
        if not repository.try_review_lock(conn, ticker, signal_date):
            return {'skipped_without_row': 'review_in_progress'}
        seen = _read(conn, ticker, signal_date, session=session, now=now, trigger=trigger,
                     filings_known=obj.get('filings_known', True))
    if 'skip' in seen:
        return {'skipped_without_row': seen['skip']}
    pool = obj.get('pool') or seen['pool']
    mode = seen['mode']
    signals, conditions = _assess(seen, session=session, now=now)
    values = _holding_values(seen, session)              # None for an entry chain
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
    unit = {'ticker': ticker, 'signal_date': signal_date, 'pool': pool}
    spent: dict[str, tuple[str, int, int]] = {}      # model runs made so far: run id -> (mode, in, out)

    def run_row(run_id_: str, run_mode: str, status: str, attempt: int, **kwargs) -> dict:
        return service._agent_run_row(unit, settings=settings, attempt=attempt, now=now, status=status,
                                      run_id=run_id_, mode=run_mode, **kwargs)

    def commit(build: Callable[[Any, int], dict]) -> dict:
        try:
            out = _commit(seen, ticker=ticker, signal_date=signal_date, session=session, now=now, trigger=trigger,
                          build=build)
        except service.Unavailable:
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
    evidence = timeline.prompt_evidence(
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
    ctx = agent_tools.ToolContext(ticker=ticker, session=session, cutoff_at=now, read=service._read_only)
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
            elif reason in service.CONNECTION_LOST:
                quota['lost'] = reason                # §8.8: the rest of the node is skipped for the same reason

            def failed(conn, attempt: int) -> dict:
                repository.insert_agent_run(conn, run_row(review_id, 'review', 'error', attempt, reason=reason,
                                                          detail=error_detail), [])
                return {**base, 'outcome': 'error', 'decided_by': 'agent', 'reason': reason,
                        'review_agent_run_id': review_id, 'attempt': attempt}
            return commit(failed)

        review_tokens = (reviewed['input_tokens'] or 0, reviewed['output_tokens'] or 0)

        def review_run(conn, attempt: int) -> None:
            repository.insert_agent_run(conn, run_row(review_id, 'review', reviewed['status'], attempt,
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
                         for cited in growth_check.knowledge_citations(output=said, counter=None, mode='review',
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
    if bars:
        revised, failure = _invoke(
            lambda model, checkpointer: agent_graph.run_unit_v2(
                unit=graph_unit, ctx=ctx, model=model, checkpointer=checkpointer, run_id=revise_id,
                last_close=Decimal(str(bars[-1]['close'])), as_of_session=session, now=lambda: now,
                previous=output, evidence=evidence, user_refs=user_refs, review_reason=said_reason,
                version=timeline.AGENT_V3, mode=mode, via='retrigger' if retriggered else None),
            deps=deps, settings=settings)
    else:
        revised, failure = None, ('bars_missing', 'no bars through the review session')
    if failure is not None and failure[0] == 'quota_exhausted':
        quota['exhausted'] = True
    elif failure is not None and failure[0] in service.CONNECTION_LOST:
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
    if clock() - started >= service.AGENT_DEADLINE_S:
        return 'stage_budget'
    if unavailable is not None:
        return unavailable
    if quota.get('exhausted'):
        return 'quota_exhausted'
    if quota.get('lost'):
        return quota['lost']
    if settings.daily_tokens is not None:
        with service._tx() as conn:
            used = repository.agent_tokens_on(conn, now.astimezone(service.NY).date())
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
        repository.insert_agent_run(conn, run_row(revise_id, 'revise', 'error', attempt, reason=reason,
                                                  detail=error_detail), [])
        return {**row, 'detail': {**row['detail'], 'revision_status': f'error: {reason}'}}
    tokens = {'input_tokens': revised['input_tokens'] or 0, 'output_tokens': revised['output_tokens'] or 0}
    row['input_tokens'] += tokens['input_tokens']
    row['output_tokens'] += tokens['output_tokens']
    payload = {'output': revised['output'], 'counter': revised['counter'], **tokens}
    if revised['status'] == 'pass':
        repository.insert_agent_run(conn, run_row(revise_id, 'revise', 'pass', attempt, **payload),
                                    revised['calls'])
        summary = (revised['output'] or {}).get('summary')
        return {**row, 'outcome': 'invalidate', 'reason': _reason(summary or row['reason'], settings),
                'detail': {**row['detail'], 'via': 'revision_pass', **(exit_detail or {})}}
    if revised['status'] != 'ok':
        repository.insert_agent_run(conn, run_row(revise_id, 'revise', revised['status'], attempt,
                                                  reason=revised['reason'], **payload), revised['calls'])
        return {**row, 'detail': {**row['detail'],
                                  'revision_status': f"{revised['status']}: {revised['reason']}"}}

    ticker, signal_date, mode = seen['version']['ticker'], seen['version']['signal_date'], seen['mode']
    head_plan, head_version = seen['plan'], seen['version']
    equity_source = service.sizing_equity(conn, session, now=now)
    plan = timeline.build_version_plan(
        output=revised['output'], agent_run_id=revise_id, model=settings.model, ticker=ticker,
        signal_date=signal_date, as_of_session=session, version_no=head_version['version_no'] + 1,
        supersedes_plan_id=str(head_plan['id']), mode=mode, bars=seen['bars'],
        earnings_row=repository.latest_earnings(conn, ticker, cutoff_at=now),
        snapshot=equity_source,
        exposure=service.exposure(conn, session, cutoff_at=now, ticker=ticker),
        params=load_config().workbench, context=service.plan_context(conn, ticker, session, now=now),
        version=timeline.AGENT_V3, entry_cap=seen['entry_cap'] if mode == 'entry' else None)
    service.mark_sizing_source(plan, equity_source)
    # D7: the host diff = the model's changed fields plus the host fields (valid_until, entry_cap) that moved
    before = {**((seen['run'] or {}).get('output') or {}), 'valid_until': head_plan['valid_until'],
              'entry_cap': (head_plan.get('inputs') or {}).get('chain_entry_cap')}
    after = {**revised['output'], 'valid_until': plan['valid_until'], 'entry_cap': plan['inputs']['chain_entry_cap']}
    host_diff = timeline.host_diff(before, after)
    if retriggered and not host_diff:               # a renewal that renews nothing is no version
        repository.insert_agent_run(conn, run_row(revise_id, 'revise', 'rejected', attempt, reason='no_change',
                                                  **payload), revised['calls'])
        return {**row, 'detail': {**row['detail'], 'revision_status': 'rejected: no_change'}}
    if mode == 'entry' and plan['status'] == 'invalid':      # D23: an entry revision must be adoptable
        reasons = ', '.join(plan['invalid_reasons'])
        repository.insert_agent_run(conn, run_row(revise_id, 'revise', 'rejected', attempt, reason='plan_invalid',
                                                  detail=reasons, **payload), revised['calls'])
        return {**row, 'detail': {**row['detail'], 'revision_status': f'plan_invalid: {reasons}'}}
    plan_id = repository.store_plan(conn, plan)['record']['id']
    repository.insert_agent_run(conn, run_row(revise_id, 'revise', 'ok', attempt, plan_id=plan_id, **payload),
                                revised['calls'])
    repository.insert_plan_version(conn, {
        'plan_id': plan_id, 'ticker': ticker, 'signal_date': signal_date,
        'version_no': head_version['version_no'] + 1, 'supersedes_plan_id': head_plan['id'],
        'origin': 'recheck' if trigger == 'recheck' else 'review', 'agent_run_id': revise_id, 'cutoff_at': now,
        'as_of_session': session, 'diff': host_diff, 'schema_version': timeline.AGENT_V3})
    # v2.7 D18: the knowledge the new version cited, with the version in the same transaction
    repository.insert_knowledge_citations(conn, [
        {**cited, 'agent_run_id': revise_id, 'plan_id': plan_id}
        for cited in growth_check.knowledge_citations(output=revised['output'], counter=revised['counter'],
                                                      mode='revise', calls=revised['calls'])])
    return {**row, 'new_plan_id': plan_id}


def _record_lost_runs(spent: dict, *, reason: str, run_row: Callable, attempt: int) -> None:
    """The chain moved while the model ran (a decision, or a review written without the run lock), so the
    review writes no row; its model runs are still recorded — ``skipped`` with the reason and their tokens —
    so the daily token cap counts them."""
    with service._tx() as conn:
        for run_id, (mode, tokens_in, tokens_out) in spent.items():
            repository.insert_agent_run(conn, run_row(run_id, mode, 'skipped', attempt, reason=reason,
                                                      input_tokens=tokens_in, output_tokens=tokens_out), [])


def _write_failed(conn, attempt: int, *, exc: BaseException, base: dict, spent: dict, run_row: Callable,
                  settings) -> dict:
    """The review row after a failed write: every model run made so far as an ``error`` run with its
    tokens, outcome ``error`` with the classified reason and ``detail.write_error``."""
    reason, error_detail = agent_model.classify_error(exc, settings.api_key)
    ids = {'review': None, 'revise': None}
    for run_id, (mode, tokens_in, tokens_out) in spent.items():
        repository.insert_agent_run(conn, run_row(run_id, mode, 'error', attempt, reason=reason, detail=error_detail,
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
    with service._tx() as conn:
        if not repository.try_review_lock(conn, ticker, signal_date):
            return {'skipped_without_row': 'review_in_progress'}
        status = service.chain_status(conn, ticker, signal_date, session=session, now=now)
        if (status is None or status['state']['state'] not in REVIEWABLE_STATES
                or status['state']['mode'] != seen['mode']
                or str(status['head']['plan']['id']) != str(seen['plan']['id'])):
            return {'skipped_without_row': 'review_in_progress'}       # someone else moved the chain meanwhile
        if trigger == 'batch':
            attempts = repository.batch_attempts(conn, ticker, signal_date, session)
            if any(r['outcome'] in repository.EFFECTIVE_REVIEW_OUTCOMES for r in attempts):
                return {'skipped_without_row': 'already_reviewed'}
        else:
            attempts = [r for r in status['reviews'] if r['session'] == session and r['trigger'] == trigger]
        last = repository.last_effective_review(conn, seen['plan']['id'])
        if (last or {}).get('id') != (seen['last_review'] or {}).get('id'):
            return {'skipped_without_row': 'review_in_progress'}       # the head was reviewed meanwhile
        row = build(conn, len(attempts) + 1)
        cited = row.pop('knowledge_citations', None) or []
        record = repository.insert_plan_review(conn, row)
        repository.insert_knowledge_citations(conn, [{**c, 'review_id': record['id']} for c in cited])
        return record


# ---------------------------------------------------------------- holding status (spec §7.3, D19)

_WHY_TEXT = {'fresh_version': '当天刚生成', 'object_cap': '超出复查对象上限', 'max_attempts': '已达重试上限',
             'review_in_progress': '正在复查', 'already_reviewed': '已复查', 'chain_not_active': '链不在复查状态',
             'error': '复查出错'}
_UNAVAILABLE_REASONS = ('disabled', 'not_configured', 'not_connected', 'model_unavailable')


def _why_text(row: dict | None, skipped_reason: str | None, unavailable: str | None) -> str:
    """Why today's review did not conclude, for 「今日复查未完成（<原因>）」 (§7.4, §13)."""
    if row is not None:
        reason = row.get('reason') or ''
        text = AGENT_REASON_TEXT.get(reason, reason)
        return f'Agent 不可用：{text}' if reason in _UNAVAILABLE_REASONS else text or '复查未完成'
    if skipped_reason:
        return _WHY_TEXT.get(skipped_reason, AGENT_REASON_TEXT.get(skipped_reason, skipped_reason))
    if unavailable:
        return f'Agent 不可用：{AGENT_REASON_TEXT.get(unavailable, unavailable)}'
    return '复查未执行'


def _held_tickers(conn, now: datetime) -> list[str]:
    """Tickers with an open position as the cutoff knew it (R11). A ticker whose fill history is broken is
    still listed (its row will fail visibly instead of silently missing)."""
    out = []
    for ticker in sorted({t['ticker'] for t in repository.list_trades(conn, cutoff=now)}):
        try:
            if positions.build(repository.list_trades(conn, ticker, cutoff=now),
                               splits=repository.split_events(conn, ticker))['position'] is not None:
                out.append(ticker)
        except positions.PositionError:
            out.append(ticker)
    return out


def _exiting_signals(conn, *, ticker: str, plan: dict, bars: list[dict], retriggers: list[dict], previous: dict | None,
                     exit_session: date | None, session: date, now: datetime) -> list[dict]:
    """The signals an exiting chain records instead of a review (§4.3, D5): the retrigger and the stop / T1
    touches — the fired ones only, in ``{kind, detail}`` form. No model, no extra source read."""
    since_session = previous['session'] if previous else (exit_session or session)
    since_at = previous['created_at'] if previous else now
    found = timeline.signals(head_plan=plan, mode='holding', session=session, since_session=since_session,
                             since_at=since_at, now=now, bars=bars, filings=None, abnormal=[], regime_label=None,
                             new_inputs=[], retriggers=retriggers)
    return [{'kind': s['kind'], 'detail': s['detail']} for s in found
            if s.get('fired') and s['kind'] in ('retrigger', 'stop_touched', 't1_touched')]


def _holding_row(conn, ticker: str, session: date, run_id: str | None, *, now: datetime,
                 skipped: dict[tuple[str, str], str], unavailable: str | None,
                 point_in_time: bool = False, account_id=REAL_ACCOUNT_ID) -> dict | None:
    """One position's ``holding_status`` values at S: the chain it belongs to (its latest version sets the stop and
    T1, D20), today's review (whatever became of it), the exit instruction if one stands, or — without a chain —
    the plan its fills follow. None when the cutoff holds no position."""
    ctx = ticker_context(conn, ticker, now=now, point_in_time=point_in_time, account_id=account_id)
    book = ctx['book']
    position = book['position']
    if position is None:
        return None
    items = [] if str(account_id) != str(REAL_ACCOUNT_ID) else [i for i in ticker_chain_items(conn, ticker, session=session, now=now, context=ctx,
                                           point_in_time=point_in_time)
             if i['state']['state'] in ('holding', 'exiting')]
    item = max(items, key=lambda i: i['chain']['signal_date']) if items else None
    bars = repository.read_bars(conn, ticker, session, cutoff_at=now)
    earlier = [r for r in repository.holding_status_for(conn, ticker=ticker, account_id=account_id) if r['session'] < session]
    first_buy = position['opened_at'].astimezone(service.NY).date()
    previous = earlier[-1] if earlier and earlier[-1]['session'] >= first_buy else None
    facts: dict[str, Any] = {
        'ticker': ticker, 'quantity': position['quantity'], 'avg_cost': position['avg_cost'],
        'first_buy_session': first_buy, 'bars': bars,
        'splits': repository.split_events(conn, ticker, **({'cutoff_at': now} if str(account_id) != str(REAL_ACCOUNT_ID) else {})),
        'previous': previous,
        'sold_after_t1': any(t['side'] == 'sell' and t.get('voided_at') is None
                             and t['executed_at'].astimezone(service.NY).date() >= session for t in ctx['trades'])}
    if item is not None:
        chain = item['chain']
        key = (ticker, chain['signal_date'].isoformat())
        today = [r for r in chain['reviews'] if r['session'] == session]
        effective = [r for r in today if r['outcome'] in repository.EFFECTIVE_REVIEW_OUTCOMES]
        row = (effective or today or [None])[-1]
        facts.update(plan=chain['head_plan'], plan_id=chain['head_plan']['id'],
                     initial_plan=_plan_of_buy(conn, item['buys'][0]) if item['buys'] else None,
                     chain_signal_date=chain['signal_date'], chain_state=item['state']['state'],
                     version_no=chain['versions'][-1]['version_no'], review_id=row['id'] if row else None,
                     today_outcome=row['outcome'] if row else None)
        if row is not None and row['outcome'] == 'invalidate':
            facts['exit_reason'] = (row['detail'] or {}).get('exit_reason')
        elif row is not None and row['decided_by'] == 'agent' and row['outcome'] in ('maintain', 'revise'):
            facts['reason'] = row['reason']
        elif row is not None and row['outcome'] in ('maintain', 'revise'):
            facts['review_conditions'] = [condition_registry.describe(c) for c in row['conditions'] or []
                                          if c.get('list') == 'review_when']
        else:
            facts['review_note'] = _why_text(row, skipped.get(key), unavailable)
        if item['state']['state'] == 'exiting':
            exits_ = [r for r in chain['reviews'] if r['outcome'] == 'invalidate' and r['mode'] == 'holding'
                      and r['timeline_version'] == timeline.TIMELINE_V2]
            first = exits_[0] if exits_ else None
            facts.update(exit_since=first['session'] if first else None,
                         exit_reason=(first['detail'] or {}).get('exit_reason') if first else None,
                         signals=_exiting_signals(conn, ticker=ticker, plan=chain['head_plan'], bars=bars,
                                                  retriggers=_retrigger_rows(conn, ticker, chain['signal_date'],
                                                                             now=now),
                                                  previous=previous, exit_session=first['session'] if first else None,
                                                  session=session, now=now))
    else:
        plans_by_trade = {lot['buy_trade_id']: _plan_of_buy(conn, next(t for t in ctx['trades']
                                                                       if t['id'] == lot['buy_trade_id']))
                          for lot in book['open']}
        plans_by_trade = {k: v for k, v in plans_by_trade.items() if v is not None}
        plan = exits.position_plan(book['open'], ctx['trades'], plans_by_trade)
        first_plan = plans_by_trade.get(book['open'][0]['buy_trade_id'])
        label = {'rule': '规则基线', 'own': '自定计划', 'agent': 'Agent 计划'}.get((plan or {}).get('kind'), '规则基线')
        facts.update(plan=plan, plan_id=plan['id'] if plan else None, initial_plan=first_plan, chain_signal_date=None,
                     chain_state=None, plan_label=label, review_id=None, today_outcome=None)
        if plan is not None:       # no chain, no review: the host steps alone (a standing exit stays until the lots go)
            values = timeline.holding_facts(facts, session=session)
            if values['exit_trigger']:
                facts.update(today_outcome='invalidate', exit_reason=values['exit_trigger'], exit_since=session)
            elif previous is not None and previous['instruction'] == 'exit':
                facts.update(today_outcome='invalidate', exit_reason=previous['exit_reason'],
                             exit_since=(previous.get('detail') or {}).get('exit_since') or previous['session'])
    status = timeline.holding_status(facts, session=session)
    if status['instruction'] == 'exit':
        status['detail'] = {**status['detail'], 'exit_since': (facts.get('exit_since') or session).isoformat()}
    if str(account_id) != str(REAL_ACCOUNT_ID):
        equity = repository.latest_equity_day(conn, account_id, on_or_before=session)
        saved = ((equity or {}).get('detail') or {}).get('paper_states', {}).get(ticker)
        if saved:
            from investment_assistant.workbench import paper_fill
            state = paper_fill._state(saved['state'])
            status.update(stop=state.stop, t1_reached=state.t1_done, reached_1r=state.reached_1r,
                          held_sessions=state.held_sessions,
                          stop_mode='trailing' if state.t1_done else 'initial')
            # Paper states are AFTER the host settled this session. A stop touch
            # has already sold the position; a surviving T1 half is already sold.
            # Do not re-derive either instruction from the real ledger's fee-
            # inclusive average cost or count missing bars as observed sessions.
            due = state.time_stop_due
            status.update(instruction='exit' if due else 'hold', exit_reason='time_stop' if due else None,
                          remaining_sessions=max(status['max_hold_sessions'] - state.held_sessions, 0)
                          if status['max_hold_sessions'] is not None and not state.t1_done and not state.reached_1r else None)
            status['detail'].pop('exit_since', None)
            if due:
                since = ((previous or {}).get('detail') or {}).get('exit_since') or session.isoformat()
                status['detail']['exit_since'] = since
                status['reason'] = timeline.maintain_reason('exiting', since=since,
                    why=timeline.EXIT_REASON_TEXT['time_stop'], quantity=status['quantity'])
            else:
                status['reason'] = '按冻结入场计划管理' + ('；T1 已卖出一半，余仓按移动止损' if state.t1_done else '')
            if status['detail'].get('bars_missing'):
                status['reason'] = timeline.maintain_reason('agent', reason=status['reason'],
                    note=f'缺 {session.isoformat()} 的日线')
            close = status['last_close']
            status['to_stop_pct'] = timeline._pct(close - state.stop, close) if close else None
            if state.t1_done:
                status['to_t1_pct'] = None
            status['detail'].update(paper_state=saved['state'], paper_state_split_factor=saved['split_factor'])
        status['account_id'] = account_id
    return {**status, 'run_id': run_id or 'review'}


def write_holding_status(conn, session: date, run_id: str | None, *, now: datetime,
                         skipped: dict[tuple[str, str], str] | None = None,
                         unavailable: str | None = None) -> dict[str, Any]:
    """Append one ``holding_status`` row for every ticker with an open position at the cutoff (D19): whatever
    became of its review — concluded, skipped, errored, the agent unavailable, an exiting chain, a position with no
    chain at all. Run it after all reviews, in the caller's transaction; each ticker under its own savepoint so one
    failure leaves the others. A second call for the same session writes nothing (the first row wins).

    ``skipped`` maps ``(ticker, chain signal date ISO)`` to the stage's row-less skip reason (``object_cap`` ...),
    ``unavailable`` is the agent's unavailable reason. Returns ``{'rows': [stored rows], 'failures':
    [(ticker, reason)]}``."""
    rows: list[dict[str, Any]] = []
    failures: list[tuple[str, str]] = []
    for ticker in _held_tickers(conn, now):
        try:
            with conn.transaction():
                values = _holding_row(conn, ticker, session, run_id, now=now, skipped=skipped or {},
                                      unavailable=unavailable)
                if values is not None:
                    rows.append(repository.insert_holding_status(conn, values))
        except service.Unavailable:
            raise
        except Exception as exc:        # §13: one ticker's failure only costs that ticker its row (check reports it)
            failures.append((ticker, families.sanitize_reason(exc)))
    return {'rows': rows, 'failures': failures}


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
        with service._tx() as conn:
            if not repository.executable_ready(conn):
                written = {'rows': [], 'failures': [('*', 'migration_029_missing')]}
            else:
                written = write_holding_status(conn, session, run_id, now=now, skipped=skipped,
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
    with service._tx() as conn:
        ready = repository.timeline_ready(conn) if timeline_ready is None else timeline_ready
        objects, excluded = review_objects(conn, session, now=now) if ready else ([], None)
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
            out = review_chain({**obj, 'filings_known': sec_known}, session=session, now=now, trigger='batch',
                               run_id=run_id, deps=deps, settings=settings, started=started, clock=clock,
                               quota=quota)
        except service.Unavailable:
            raise
        except Exception as exc:    # §13: one chain's surprise never stops the stage
            summary['errors'].append(f"{obj['ticker']} review: {families.sanitize_reason(exc)}")
            detail['excluded'].append(_excluded(obj, 'error'))      # no row: say why in the holding status
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
        service._check_derivations(acc, acc.outputs['plan_ids'], summary)
        acc.duration_ms += (clock() - t_stage) * 1000
    return None


def _record(obj: dict, out: dict, *, review: dict, detail: dict, acc: stages_module.StageAcc | None) -> None:
    """Fold one chain's result into summary['review'] and the stage accumulator."""
    ticker = obj['ticker']
    if 'skipped_without_row' in out:
        detail['excluded'].append(_excluded(obj, out['skipped_without_row']))
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
    if outcome in repository.EFFECTIVE_REVIEW_OUTCOMES:
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
    if not repository.timeline_ready(conn):
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
    with service.read_only_tx() as conn:
        _require_ready(conn)
        status = service.chain_status(conn, ticker, signal_date, session=session, now=now)
        if status is None:
            raise LookupError(f'该单元没有 agent 计划链：{ticker} {signal_date}')
        if status['state']['state'] == 'exiting':
            raise RecheckConflict('chain_exiting')
        if status['state']['state'] not in REVIEWABLE_STATES:
            raise RecheckConflict('chain_not_active')
        unavailable = agent_model.unavailable_reason(settings)
        if unavailable is not None:
            raise RecheckConflict(f'agent_unavailable: {unavailable}')
    if run_lock:
        with _run_lock(ticker, signal_date) as got:
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
    with _run_lock(ticker, signal_date) as got:
        if not got:
            raise RecheckConflict('review_in_progress')
        _precheck(ticker, signal_date, session=session, now=now, settings=settings, run_lock=False)
        clock = deps.clock or time.monotonic
        out = _review_chain({'ticker': ticker, 'signal_date': signal_date}, session=session, now=now,
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
    for row in repository.chain_retriggers_for(conn, ticker, chain_signal_date=signal_date, cutoff=cutoff):
        found = handled.get(row['unit_signal_date'].isoformat())
        out.append({**row, 'version_no': numbers.get(str(found['new_plan_id'])) if found else None})
    return out


def _version_execution(plan: dict | None, status: dict, session: date) -> dict | None:
    """What the fills made of one version (R13, `execution-1`): ``{status}`` and, for an adopted one, the day (MM-DD)
    and size of the first fill linked to it. None for a version without a plan row."""
    if plan is None:
        return None
    state = status['state']['state']
    code = execution.status(plan, status['buys'], session=session, chain_ended=state not in ACTIVE_STATES,
                            chain_in_entry=state == 'entry')
    out: dict[str, Any] = {'status': code}
    if code == 'adopted':
        mine = [b for b in status['buys'] if b.get('voided_at') is None and str(b.get('plan_id')) == str(plan['id'])
                and plan['valid_from'] <= b['executed_at'].astimezone(service.NY).date() <= plan['valid_until']]
        first = min(mine, key=lambda b: (b['executed_at'], str(b['id'])))
        out.update(date=first['executed_at'].astimezone(service.NY).strftime('%m-%d'),
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
    with service.read_only_tx() as conn:
        _require_ready(conn)
        status = service.chain_status(conn, ticker, signal_date, session=session, now=now)
        if status is None:
            raise LookupError(f'该单元没有 agent 计划链：{ticker} {signal_date}')
        head_id = str(status['head']['plan']['id'])
        ticker_bars = repository.read_bars(conn, ticker, session, cutoff_at=now, limit=400)
        spy = repository.read_bars(conn, service.BENCHMARK, session, cutoff_at=now, limit=400)
        splits = repository.split_events(conn, ticker)
        numbers = {str(v['plan_id']): v['version_no'] for v in status['versions']}
        versions = []
        for version in status['versions']:
            plan = repository.plan_by_id(conn, version['plan_id'])
            run = repository.agent_run_by_id(conn, version['agent_run_id'])
            output = (run or {}).get('output') or {}
            found = (service.shadow_outcomes(plan, ticker_bars, spy, splits=splits) if plan is not None
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
        refs = my_knowledge.refs_for_plans(conn, [v['plan_id'] for v in versions])      # v2.7 §6.2
        for version in versions:
            version['knowledge_refs'] = refs.get(version['plan_id'], [])
        retriggers, holding, notes = [], [], []
        if repository.executable_ready(conn):
            retriggers = retrigger_rows(conn, ticker, signal_date, status['reviews'], numbers)
            holding = repository.holding_status_for(conn, ticker=ticker, chain_signal_date=signal_date)
            notes = repository.plan_notes_for(conn, ticker, chain_signal_date=signal_date)
    return {'ticker': ticker, 'signal_date': signal_date, 'session': session,
            'state': status['state'], 'head_plan_id': head_id,
            'version_no': status['head']['version']['version_no'],
            'latest_review': service._review_view(status['latest_review']),
            'shadow_version': plan_shadow_module.SHADOW_VERSION,
            'versions': versions, 'reviews': reviews, 'inputs': inputs,
            'retriggers': retriggers, 'holding_status': holding, 'notes': notes}


def reviews_view(session: date) -> dict:
    """Every review row of review session S (batch and recheck, oldest first) with the version numbers
    it reviewed and produced. Read-only; Unavailable without migration 026."""
    with service.read_only_tx() as conn:
        _require_ready(conn)
        rows = repository.reviews_on(conn, session)
        numbers: dict[str, int] = {}
        for row in rows:
            for plan_id in (row['head_plan_id'], row['new_plan_id']):
                if plan_id is not None and str(plan_id) not in numbers:
                    version = repository.version_by_plan(conn, plan_id)
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
    with service.read_only_tx() as conn:
        _require_ready(conn)
        context = ticker_context(conn, ticker, now=now)
        for chain in repository.all_chains(conn):
            if chain['ticker'].upper() != ticker:
                continue
            status = service.chain_status(conn, ticker, chain['signal_date'], session=session, now=now,
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
    if not repository.stage_runs_ready(conn):
        return None, {}
    start: datetime | None = None
    out: dict[tuple[str, str], list[str]] = {}
    for row in repository.stage_runs_for_session(conn, session):
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
    versions = repository.chain_versions(conn, ticker, signal_date)
    reviews = repository.chain_reviews(conn, ticker, signal_date)
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
        contexts[ticker] = ticker_context(conn, ticker, now=now)
    item = read_chain_facts(conn, chain, session=session, now=now, context=contexts[ticker])
    chain_input = {**item['chain'], 'versions': known, 'head_plan': repository.plan_by_id(conn, head['plan_id']),
                   'reviews': prior}
    state = timeline.chain_state_v2(chain_input, session=session, now=now, facts=item['facts'])['state']
    if state not in REVIEWABLE_STATES:
        out(f'  {tag}：{state}，不需复查')
        return None
    reasons = excluded.get((ticker.upper(), signal_date.isoformat()), [])
    if 'object_cap' in reasons:
        out(f'  {tag} {state}：超出复查上限（object_cap）')
        return None
    effective = [r for r in batch if r['outcome'] in repository.EFFECTIVE_REVIEW_OUTCOMES]
    if len(effective) == 1:
        out(f"  {tag} {state}：有效批处理复查 1 条（{effective[0]['outcome']}）")
        return 'complete'
    why = [f"{'' if r['trigger'] == 'batch' else '重新复查 '}{r['outcome']}（{r['reason']}）"
           for r in reviews if r['session'] == session and r['outcome'] not in repository.EFFECTIVE_REVIEW_OUTCOMES]
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
    for ticker in sorted({c['ticker'] for c in repository.all_chains(conn)}):
        if ticker not in contexts:
            contexts[ticker] = ticker_context(conn, ticker, now=now)
        first = {c['signal_date']: c['first_schema_version'] for c in contexts[ticker]['chains']}
        active = [i for i in ticker_chain_items(conn, ticker, session=session, now=now, context=contexts[ticker])
                  if i['state']['state'] in ACTIVE_STATES]
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
    fired = {(r['ticker'], r['signal_date']) for r in repository.reviews_on(conn, session)
             if any(s.get('kind') == 'retrigger' and s.get('fired') for s in r['signals'] or [])}
    problems = []
    for row in repository.retriggers_on(conn, session):
        key = (row['ticker'], row['chain_signal_date'])
        if key in fired or excluded.get((row['ticker'].upper(), row['chain_signal_date'].isoformat())):
            continue
        ticker = row['ticker']
        if ticker not in contexts:
            contexts[ticker] = ticker_context(conn, ticker, now=now)
        item = read_chain_facts(conn, {'ticker': ticker, 'signal_date': row['chain_signal_date']}, session=session,
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
    found = [r['cutoff_at'] for r in repository.reviews_on(conn, session) if r['trigger'] == 'batch']
    found += [r['created_at'] for r in repository.retriggers_on(conn, session)]
    return max(found) if found else None


def _check_holding_status(conn, session: date, *, cutoff: datetime, out: Callable[[str], None]) -> list[str]:
    """Check ③ (spec §9.2): every ticker with open lots on S has exactly one ``holding_status`` row for S (the
    unique key allows at most one; a missing one is the failure D19 exists to prevent)."""
    have = {r['ticker'] for r in repository.holding_status_for(conn, session=session)}
    held = _held_tickers(conn, cutoff)
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
    for chain in repository.all_chains(conn):
        ticker, signal_date = chain['ticker'], chain['signal_date']
        versions = repository.chain_versions(conn, ticker, signal_date)
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
            run = repository.agent_run_by_id(conn, version['agent_run_id'])
            if run is None or run['cutoff_at'] != version['cutoff_at']:
                problems.append(f'{tag}：cutoff_at 与生成运行不一致')
            if run is None:
                continue
            calls = {c['seq'] for c in repository.agent_tool_calls(conn, run['id'])}
            for ref in _refs_in(run['output']) + _refs_in(run['counter']):
                if problem := _ref_problem(ref, calls=calls, inputs=inputs_of(ticker), cutoff=version['cutoff_at']):
                    problems.append(f'{tag}：{problem}')
    for row in repository.reviews_on(conn, session):
        tag = f"{row['ticker']} {row['signal_date']} 复查 {row['id']}"
        calls = ({c['seq'] for c in repository.agent_tool_calls(conn, row['review_agent_run_id'])}
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
    with service.read_only_tx() as conn:
        _require_ready(conn)
        if not repository.executable_ready(conn):
            raise service.Unavailable('迁移 029 未执行')

        def inputs_of(ticker: str) -> dict[int, dict]:
            if ticker not in cache:
                cache[ticker] = {row['id']: row for row in user_inputs.ticker_rows(conn, ticker)}
            return cache[ticker]

        stage_start, excluded = _stage_evidence(conn, session)
        out(f'复查完整性 {session}')
        verdicts = [_check_chain(conn, chain, session, now=now, contexts=contexts, stage_start=stage_start,
                                 excluded=excluded, out=out)
                    for chain in repository.all_chains(conn)]
        new_versions, problems = _check_versions(conn, session, inputs_of)
        if repository.accounts_ready(conn):
            from investment_assistant.workbench import paper_accounts
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
    from investment_assistant.workbench.review_digest import SH      # the workbench's Beijing-time constant

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
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.workbench.plan_review',
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
