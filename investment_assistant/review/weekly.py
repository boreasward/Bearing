"""Discipline, track and weekly/monthly snapshot use cases."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

from investment_assistant.core import dbutil as core_dbutil, schema as core_schema, tx as core_tx
from investment_assistant.core.constants import BENCHMARK, NY
from investment_assistant.data import repo as data_repo
from investment_assistant.data.adapters import bars
from investment_assistant.analysis import repo as analysis_repo, synthesis, pool as pool_module
from investment_assistant.execution import repo as execution_repo, repo_legacy as execution_repo_legacy
from investment_assistant.execution import chains as execution_chains, plan_context as execution_plan_context
from investment_assistant.execution import ledger, timeline

from investment_assistant.execution.accounts import REAL_ACCOUNT_ID
from investment_assistant.execution.paper import accounts as execution_paper_accounts
from investment_assistant.execution import adoption as execution_module, simulation as _plan_shadow_module
from investment_assistant.analysis.agent import graph as agent_graph
from investment_assistant.review import repo as review_repo, outcomes as review, monthly
from investment_assistant.review import discipline as _discipline_module, tracks as _tracks_module
from investment_assistant.review.shadow import _outcomes, _plan_chains, shadow_outcomes

def _discipline_v2(conn, *, trades: list[dict[str, Any]], segments: list[dict[str, Any]], tickers: list[str],
                   session: date, now: datetime, since: date, until: date | None = None, account_id=REAL_ACCOUNT_ID) -> dict[str, Any]:
    """`discipline-2` over ``trades`` (v2.9 D28): every buy is judged against the plan its fill is linked to
    (``trades.plan_id``; an old fill through its decision), the chain's newest version at the sale for the early take
    and the daily holding status for the held loser. ``segments`` are the closed lots, ``tickers`` the ones whose
    bars, stop events and holding rows are read (since ``since``; stop events up to ``until``)."""


    contexts: dict[str, dict[str, Any]] = {}
    plans_by_trade: dict[Any, dict[str, Any]] = {}
    chain_head_by_trade: dict[Any, list[dict[str, Any]]] = {}
    legacy_decisions: dict[Any, dict[str, Any]] = {}
    plans_by_id: dict[Any, dict[str, Any]] = {}
    for t in trades:
        if t['side'] != 'buy' or t['voided_at'] is not None:
            continue
        if t.get('plan_id') is not None:
            plan = execution_repo.plan_by_id(conn, t['plan_id'])
            if plan is None:
                continue
            plans_by_trade[t['id']] = plan
            context = contexts.get(t['ticker']) or contexts.setdefault(
                t['ticker'], execution_chains.ticker_context(conn, t['ticker'], now=now, account_id=account_id))
            key = context['plan_chain'].get(t['plan_id'])
            if key is not None and str(account_id) == str(REAL_ACCOUNT_ID):
                chain_head_by_trade[t['id']] = [execution_repo.plan_by_id(conn, v['plan_id'])
                                                for v in execution_repo.chain_versions(conn, key[0], key[1])]
        elif t.get('decision_id') and t['decision_id'] not in legacy_decisions:
            decision = execution_repo_legacy.decision_by_id(conn, t['decision_id'])
            if decision is not None:
                legacy_decisions[t['decision_id']] = decision
                if decision['plan_id'] and decision['plan_id'] not in plans_by_id:
                    plan = execution_repo.plan_by_id(conn, decision['plan_id'])
                    if plan is not None:
                        plans_by_id[decision['plan_id']] = plan
    stop_events, holding_rows = [], []
    for tk in tickers:
        stop_events += [c for c in data_repo.read_candidates(conn, tk, since=since)
                        if str(account_id) == str(REAL_ACCOUNT_ID) and c['rule_id'] == 'plan_stop' and (until is None or c['signal_date'] <= until)]
        if core_schema.executable_ready(conn):
            holding_rows += [r for r in execution_repo.holding_status_for(conn, ticker=tk, account_id=account_id) if r['session'] >= since]
    out = _discipline_module.evaluate_v2(
        trades=trades, plans_by_trade=plans_by_trade, chain_head_by_trade=chain_head_by_trade,
        holding_rows=holding_rows, legacy_decisions=legacy_decisions,
        bars_by_ticker={tk: data_repo.read_bars(conn, tk, session, cutoff_at=now, limit=400) for tk in tickers},
        segments=segments, session=session, plans_by_id=plans_by_id, stop_events=stop_events)
    return {**out, 'calc_version': _discipline_module.DISCIPLINE_VERSION}


def discipline(*, now: datetime, since: date | None = None, account_id=REAL_ACCOUNT_ID) -> dict[str, Any]:
    """Execution discipline of recorded buys against the plans their fills follow (`discipline-2`)."""
    session = bars.latest_closed_session(now)
    since = since or session - timedelta(days=90)
    with core_tx.read_only_tx() as conn:
        # Buys and stop events share one window, or old buys see no stops and flag false early_takes.
        trades = [t for t in execution_repo.list_trades(conn, cutoff=now, account_id=ledger._uuid(account_id, 'account_id'))
                  if t['voided_at'] is None and t['executed_at'].astimezone(NY).date() >= since]
        tickers = sorted({t['ticker'] for t in trades})
        segments = []
        for tk in tickers:
            segments += execution_plan_context._book(conn, tk, account_id=account_id)['closed']
        out = _discipline_v2(conn, trades=trades, segments=segments, tickers=tickers, session=session, now=now,
                             since=since, account_id=account_id)
    return {'session': session, 'since': since, **out}


def trade_review(*, now: datetime) -> dict[str, Any]:
    session = bars.latest_closed_session(now)
    with core_tx._tx() as conn:
        spy = data_repo.read_bars(conn, BENCHMARK, session, cutoff_at=now, limit=400)
        closed, open_ = [], []
        for ticker in sorted({t['ticker'] for t in execution_repo.list_trades(conn)}):
            book = execution_plan_context._book(conn, ticker)
            ticker_bars = data_repo.read_bars(conn, ticker, session, cutoff_at=now, limit=400)
            closed += [{'ticker': ticker, **review.trade_segment_review(s, ticker_bars, spy)} for s in book['closed']]
            if book['position'] and ticker_bars:
                open_.append(review.open_position_review(ticker, book['position'], ticker_bars))
    return {'session': session, 'calc_version': review.CALC_VERSION, 'closed': closed, 'open': open_}


def _week_discipline(conn, session: date, now: datetime) -> dict[str, Any]:
    """Discipline over the same 90-day window the stop events use, in an existing transaction."""
    since = session - timedelta(days=90)
    trades = [t for t in execution_repo.list_trades(conn)
              if t['voided_at'] is None and t['executed_at'].astimezone(NY).date() >= since]
    tickers = sorted({t['ticker'] for t in trades})
    segments = []
    for tk in tickers:
        segments += execution_plan_context._book(conn, tk)['closed']
    return _discipline_v2(conn, trades=trades, segments=segments, tickers=tickers, session=session, now=now,
                          since=since)


def _track_agent_pick(conn, ticker: str, signal_date: date, decision: dict[str, Any] | None, *,
                      ready: bool) -> dict[str, Any]:
    """v2.5 §9.2: which Agent version the agent track takes — ``{run, plan, version_no, basis, latest}``.

    1. a decision with ``agent_plan_id`` → that version (``'decision'``);
    2. a decision without it (before v2.5) → the unit's agent-1 plan, else version 0 (``'first'``);
    3. undecided → version 0 (``'first'``), so a later revision never polishes the result.
    ``run`` is the run that produced the plan (agent-1: the latest propose run, as before); ``latest`` is the
    unit's newest agent plan (the chain head, or the agent-1 plan) for the unit's validity and pool. Units
    without an agent-2 chain read exactly as before v2.5; ``basis`` is None when no agent plan is taken."""
    versions = execution_repo.chain_versions(conn, ticker, signal_date) if ready else []
    chosen_id = decision.get('agent_plan_id') if decision is not None else None
    if not versions:
        latest = execution_repo.agent_plan(conn, ticker, signal_date)
        plan = execution_repo.plan_by_id(conn, chosen_id) if chosen_id is not None else latest
        return {'run': analysis_repo.latest_agent_run(conn, ticker, signal_date), 'plan': plan, 'version_no': None,
                'basis': None if plan is None else ('decision' if chosen_id is not None else 'first'),
                'latest': latest}
    latest = execution_repo.plan_by_id(conn, versions[-1]['plan_id'])
    by_plan = {v['plan_id']: v for v in versions}
    if chosen_id is not None and chosen_id not in by_plan:        # an agent-1 plan id beside a chain
        return {'run': analysis_repo.latest_agent_run(conn, ticker, signal_date),
                'plan': execution_repo.plan_by_id(conn, chosen_id), 'version_no': None, 'basis': 'decision',
                'latest': latest}
    if chosen_id is None and decision is not None:
        legacy = next((p for p in execution_repo.plans_for_unit(conn, ticker, signal_date)
                       if p['kind'] == 'agent' and p['plan_version'] == agent_graph.AGENT_VERSION), None)
        if legacy is not None:
            return {'run': analysis_repo.latest_agent_run(conn, ticker, signal_date), 'plan': legacy,
                    'version_no': None, 'basis': 'first', 'latest': latest}
    version = by_plan[chosen_id] if chosen_id is not None else versions[0]
    return {'run': data_repo.agent_run_by_id(conn, version['agent_run_id']),
            'plan': execution_repo.plan_by_id(conn, version['plan_id']), 'version_no': version['version_no'],
            'basis': 'decision' if chosen_id is not None else 'first', 'latest': latest}


def _track_rows(conn, since: date, until: date, *, session: date, now: datetime,
                version: str = _tracks_module.TRACKS_V2, account_id=REAL_ACCOUNT_ID) -> list[dict[str, Any]]:
    """§4.4: one row per unit in the window — rule, agent and actual side by side, every id traceable. Bars and books
    are cached per ticker within the transaction; ids and dates are stringified so the rows freeze into JSONB
    snapshots as-is. ``version`` picks `tracks-2` (default, v2.9 D27) or the old `tracks-1` reading."""
    spy = data_repo.read_bars(conn, BENCHMARK, session, cutoff_at=now, limit=400)
    bars_by_ticker: dict[str, list[dict[str, Any]]] = {}
    ready = core_schema.timeline_ready(conn)
    contexts: dict[str, dict[str, Any]] = {}
    builder = _track_row if version == _tracks_module.TRACKS_V2 else _track_row_v1
    return [builder(conn, unit['ticker'], unit['signal_date'], spy=spy, bars_by_ticker=bars_by_ticker, ready=ready,
                    session=session, now=now, **({'contexts': contexts, 'account_id': account_id} if builder is _track_row else {}))
            for unit in analysis_repo.track_units(conn, since, until)]


def unit_track_row(conn, ticker: str, signal_date: date, *, session: date, now: datetime,
                   version: str = _tracks_module.TRACKS_V2) -> dict[str, Any]:
    """The track row of one unit, exactly the row ``_track_rows`` builds for it (the review card's frozen
    ``facts.tracks`` takes its three tracks from here, v2.7 D7). ``version='tracks-1'`` reads the old way (the check
    of a retro-1 card compares against it)."""
    spy = data_repo.read_bars(conn, BENCHMARK, session, cutoff_at=now, limit=400)
    ready = core_schema.timeline_ready(conn)
    if version == _tracks_module.TRACKS_V1:
        return _track_row_v1(conn, ticker, signal_date, spy=spy, bars_by_ticker={}, ready=ready, session=session,
                             now=now)
    return _track_row(conn, ticker, signal_date, spy=spy, bars_by_ticker={}, ready=ready, session=session, now=now,
                      contexts={})


def unit_fills(conn, ticker: str, signal_date: date, context: dict[str, Any]) -> list[dict[str, Any]]:
    """The live buys that belong to one unit, oldest first (D22, D27): the fills linked to any plan row of the unit
    (rule baseline, own plan, Agent version), the fills of the unit's own chain (``execution.chain_buys``: also those
    linked to a unit merged into it) and, for old data, the fills of the unit's decisions. ``context`` is
    ``execution_chains.ticker_context`` of the ticker."""
    unit_plans = {p['id'] for p in execution_repo.plans_for_unit(conn, ticker, signal_date)}
    legacy = execution_repo_legacy.unit_decision_ids(conn, ticker, signal_date)
    chain = ({'ticker': ticker, 'signal_date': signal_date}
             if any(c['signal_date'] == signal_date for c in context['chains']) else None)
    owned = {str(b['id']): b for b in execution_module.chain_buys(
        (ticker, signal_date), context['trades'], plan_chain=context['plan_chain'],
        legacy_decision_ids=legacy)} if chain is not None else {}
    found = dict(owned)
    for t in context['trades']:
        if t['side'] != 'buy' or t.get('voided_at') is not None:
            continue
        if (t.get('plan_id') is not None and t['plan_id'] in unit_plans) or \
                (t.get('decision_id') is not None and t['decision_id'] in legacy):
            found[str(t['id'])] = t
    return sorted(found.values(), key=lambda t: (t['executed_at'], str(t['id'])))


def _track_row(conn, ticker: str, signal_date: date, *, spy: list[dict[str, Any]],
               bars_by_ticker: dict[str, list[dict[str, Any]]], ready: bool, session: date,
               now: datetime, contexts: dict[str, dict[str, Any]], account_id=REAL_ACCOUNT_ID) -> dict[str, Any]:
    """The `tracks-2` row of one unit (v2.9 D27): the Agent track takes the version the first fill was linked to
    (`fill`), an old decision's version (`decision`) or the first version; a unit merged into another chain has
    `merged` (no R); the actual track takes its plan from the fills' own plan link."""


    if ticker not in bars_by_ticker:
        bars_by_ticker[ticker] = data_repo.read_bars(conn, ticker, session, cutoff_at=now, limit=400)
    ticker_bars = bars_by_ticker[ticker]
    splits = data_repo.split_events(conn, ticker)
    context = contexts.get(ticker) or contexts.setdefault(ticker, execution_chains.ticker_context(conn, ticker, now=now, account_id=account_id))
    rule = execution_repo.rule_plan(conn, ticker, signal_date)
    legacy = execution_repo_legacy.decision_head(conn, ticker, signal_date, timeline=ready) if str(account_id) == str(REAL_ACCOUNT_ID) else None
    versions = execution_repo.chain_versions(conn, ticker, signal_date) if ready else []
    fills = unit_fills(conn, ticker, signal_date, context)
    merged = (not versions and core_schema.executable_ready(conn)
              and execution_repo.retrigger_for_unit(conn, ticker, signal_date) is not None)

    def outcome_of(plan: dict[str, Any] | None) -> dict[str, Any] | None:
        if plan is None or plan['status'] != 'ok':
            return None
        return shadow_outcomes(plan, ticker_bars, spy, splits=splits)['outcome']

    if versions:
        pick = _tracks_module.agent_pick({'ticker': ticker, 'signal_date': signal_date}, legacy_decision=legacy,
                                         chain_buys=fills, versions=versions, merged=False)
        plan = execution_repo.plan_by_id(conn, pick['plan_id']) if pick['plan_id'] is not None else None
        run = (data_repo.agent_run_by_id(conn, pick['plan']['agent_run_id']) if pick['plan'] is not None
               else analysis_repo.latest_agent_run(conn, ticker, signal_date))
        agent_plan = execution_repo.plan_by_id(conn, versions[-1]['plan_id'])
        agent_track = _tracks_module.agent_track(run, plan, outcome_of(plan), version_no=pick['version_no'],
                                                 version_basis=pick['version_basis'])
    elif merged:
        agent_plan = None
        agent_track = _tracks_module.merged_agent_track()
    else:
        old = _track_agent_pick(conn, ticker, signal_date, legacy, ready=ready)
        agent_plan = old['latest']
        agent_track = _tracks_module.agent_track(old['run'], old['plan'], outcome_of(old['plan']),
                                                 version_no=old['version_no'], version_basis=old['basis'])

    rule_track = _tracks_module.plan_track(rule, outcome_of(rule))
    plans_by_trade = {}
    for fill in fills:
        linked = execution_chains._plan_of_buy(conn, fill)
        if linked is not None:
            plans_by_trade[fill['id']] = linked
    book = context['book']
    buy_ids = {t['id'] for t in fills}
    actual = _tracks_module.actual_track_v2(
        legacy_decision=legacy, plans_by_trade=plans_by_trade, buys=fills,
        segments=[sg for sg in book['closed'] if sg['buy_trade_id'] in buy_ids],
        open_lots=[lot for lot in book['open'] if lot['buy_trade_id'] in buy_ids],
        last_close=Decimal(str(ticker_bars[-1]['close'])) if ticker_bars else None,
        splits=splits, session=session,
        unit_valid_until=max((p['valid_until'] for p in (rule, agent_plan, *plans_by_trade.values())
                              if p is not None), default=None))

    def context_of(plan):
        return (plan.get('inputs') or {}).get('context') if plan else None
    pool = review.pool_of(context_of(rule) or context_of(agent_plan),
                          data_repo.pool_entry_at(conn, ticker, signal_date, cutoff_at=now))
    return {
        'ticker': ticker, 'signal_date': signal_date.isoformat(), 'pool': pool,
        'rule': {**rule_track, 'plan_id': str(rule_track['plan_id']) if rule_track['plan_id'] else None},
        'agent': {**agent_track,
                  'run_id': str(agent_track['run_id']) if agent_track['run_id'] else None,
                  'plan_id': str(agent_track['plan_id']) if agent_track['plan_id'] else None},
        'actual': {**actual,
                   'decision_id': str(actual['decision_id']) if actual['decision_id'] else None,
                   'plan_id': str(actual['plan_id']) if actual['plan_id'] else None,
                   'buy_trade_ids': [str(tid) for tid in actual['buy_trade_ids']]},
    }


def _track_row_v1(conn, ticker: str, signal_date: date, *, spy: list[dict[str, Any]],
               bars_by_ticker: dict[str, list[dict[str, Any]]], ready: bool, session: date,
               now: datetime) -> dict[str, Any]:
    if ticker not in bars_by_ticker:
        bars_by_ticker[ticker] = data_repo.read_bars(conn, ticker, session, cutoff_at=now, limit=400)
    ticker_bars = bars_by_ticker[ticker]
    splits = data_repo.split_events(conn, ticker)
    rule = execution_repo.rule_plan(conn, ticker, signal_date)
    decision = execution_repo_legacy.decision_head(conn, ticker, signal_date, timeline=ready)
    pick = _track_agent_pick(conn, ticker, signal_date, decision, ready=ready)
    agent_plan = pick['latest']
    rule_outcome = (_plan_shadow_module.simulate(rule, ticker_bars, spy, splits=splits)
                    if rule is not None and rule['status'] == 'ok' else None)
    agent_outcome = (_plan_shadow_module.simulate(pick['plan'], ticker_bars, spy, splits=splits)
                     if pick['plan'] is not None and pick['plan']['status'] == 'ok' else None)
    rule_track = _tracks_module.plan_track(rule, rule_outcome)
    agent_track = _tracks_module.agent_track(pick['run'], pick['plan'], agent_outcome,
                                             version_no=pick['version_no'], version_basis=pick['basis'])
    chosen = (execution_repo.plan_by_id(conn, decision['plan_id'])
              if decision is not None and decision.get('plan_id') else None)
    book = execution_plan_context._book(conn, ticker)
    # A buy may link to any decision in the unit's chain: correcting a decision
    # after the fill must not drop the fill from the actual track.
    chain_ids = execution_repo_legacy.unit_decision_ids(conn, ticker, signal_date) if decision is not None else set()
    buy_ids = ({t['id'] for t in execution_repo.list_trades(conn, ticker)
                if t.get('voided_at') is None and t['side'] == 'buy'
                and t.get('decision_id') in chain_ids})
    actual = _tracks_module.actual_track(
        decision=decision, plan=chosen, buys=[{'id': tid} for tid in sorted(buy_ids, key=str)],
        segments=[s for s in book['closed'] if s['buy_trade_id'] in buy_ids],
        open_lots=[l for l in book['open'] if l['buy_trade_id'] in buy_ids],
        last_close=Decimal(str(ticker_bars[-1]['close'])) if ticker_bars else None,
        splits=splits, session=session,
        unit_valid_until=max((p['valid_until'] for p in (rule, agent_plan, chosen)
                              if p is not None), default=None))
    def context_of(plan):
        return (plan.get('inputs') or {}).get('context') if plan else None
    pool = review.pool_of(context_of(rule) or context_of(agent_plan),
                          data_repo.pool_entry_at(conn, ticker, signal_date, cutoff_at=now))
    return {
        'ticker': ticker, 'signal_date': signal_date.isoformat(), 'pool': pool,
        'rule': {**rule_track, 'plan_id': str(rule_track['plan_id']) if rule_track['plan_id'] else None},
        'agent': {**agent_track,
                  'run_id': str(agent_track['run_id']) if agent_track['run_id'] else None,
                  'plan_id': str(agent_track['plan_id']) if agent_track['plan_id'] else None},
        'actual': {**actual,
                   'decision_id': str(actual['decision_id']) if actual['decision_id'] else None,
                   'plan_id': str(actual['plan_id']) if actual['plan_id'] else None,
                   'buy_trade_ids': [str(tid) for tid in actual['buy_trade_ids']]},
    }


def tracks(*, now: datetime, since: date | None = None, until: date | None = None, account_id=REAL_ACCOUNT_ID) -> dict[str, Any]:
    """§4.5: the live three-track view, defaulting to the last 90 days of signal dates;
    not frozen — weekly and monthly snapshots carry their own copy."""
    session = bars.latest_closed_session(now)
    since = since if since is not None else session - timedelta(days=90)
    until = until if until is not None else session
    with core_tx.read_only_tx() as conn:
        rows = _track_rows(conn, since, until, session=session, now=now, account_id=ledger._uuid(account_id, 'account_id'))
    return {'session': session.isoformat(), 'since': since.isoformat(), 'until': until.isoformat(),
            'calc_version': _tracks_module.TRACKS_VERSION, 'units': rows,
            'summary': _tracks_module.summarize_v2(rows)}


def _weekly_learning(conn, week_start: date, week_end: date, *, session: date, now: datetime) -> dict[str, Any]:
    """The weekly 「你学到了什么」 (v2.7 §10, ``review-7``) from the same transaction as the rest of the snapshot:
    the week's review cards and the knowledge items whose cited plans reached an outcome that week. Without
    migration 027 it is the empty learning (all zeros), not a missing key."""
    from investment_assistant.review.knowledge import my_knowledge
    from investment_assistant.review import retro_cards

    cards: list[dict[str, Any]] = []
    perf: dict[int, dict[str, Any]] = {}
    if core_schema.growth_ready(conn):
        cards = retro_cards.learning_cards(conn, week_start, week_end)
        perf = my_knowledge.learning_perf(conn, session=session, now=now)
    return review.learning_stats(week_start=week_start, week_end=week_end, cards=cards, perf_by_item=perf)


def _week_units(conn, week_start: date, week_end: date, *, now: datetime) -> dict[str, Any]:
    """v2.9 D26 / D30: every unit of the week (a ticker-day with candidates, whatever became of it) with its
    `execution-1` state, the week's linked fills and the buys that follow no plan.

    ``units``: ``{id, ticker, signal_date, proposed_action, rule_ids, execution}``; ``fills``: ``{trade, unit_id,
    plan_created_at}`` for the live buys of those units; ``unplanned``: the live buys executed in the week with no plan
    link (and no plan through an old decision); ``groups``: the candidate groups the units come from."""


    groups = [g for g in analysis_repo.candidate_groups_since(conn, week_start) if g['signal_date'] <= week_end]
    contexts: dict[str, dict[str, Any]] = {}

    def context_of(ticker: str) -> dict[str, Any]:
        return contexts.get(ticker) or contexts.setdefault(ticker, execution_chains.ticker_context(conn, ticker, now=now))

    units, fills = [], []
    owner: dict[Any, str] = {}          # a fill belongs to one unit: the unit that owns the chain wins over the unit whose
                                        # baseline it was linked to (R17 / D22)
    ordered = sorted(groups, key=lambda g: (g['signal_date'], g['ticker']))
    chain_first = sorted(ordered, key=lambda g: not any(c['signal_date'] == g['signal_date']
                                                         for c in context_of(g['ticker'])['chains']))
    mine_of: dict[tuple, list] = {}
    for group in chain_first:
        key = (group['ticker'], group['signal_date'])
        mine_of[key] = []
        for buy in unit_fills(conn, group['ticker'], group['signal_date'], context_of(group['ticker'])):
            if buy['id'] not in owner:
                owner[buy['id']] = f"{group['ticker']}:{group['signal_date'].isoformat()}"
                mine_of[key].append(buy)
    for group in ordered:
        ticker, signal_date = group['ticker'], group['signal_date']
        shape = synthesis.synthesize(ticker, signal_date, group['candidates'], holding=False, now=now)
        uid = f'{ticker}:{signal_date.isoformat()}'
        mine = mine_of[(ticker, signal_date)]
        # the execution state is a buy unit's; a sell / watch / conflict unit nobody traded has none (M3)
        state = 'not_executed' if shape['proposed_action'] == 'buy' and 'conflict' not in shape['labels'] \
            or mine else 'not_applicable'
        for buy in mine:
            plan = execution_chains._plan_of_buy(conn, buy)
            # an own plan is written with the fill itself, so its age says nothing about how long you waited
            created = plan['created_at'] if plan and plan['kind'] != 'own' else None
            fills.append({'trade': buy, 'unit_id': uid, 'plan_created_at': created})
            day = buy['executed_at'].astimezone(NY).date()
            if plan is None or plan['valid_from'] <= day <= plan['valid_until']:
                state = 'adopted'
            elif state != 'adopted':
                state = 'out_of_validity'
        units.append({'id': uid, 'ticker': ticker, 'signal_date': signal_date,
                      'proposed_action': shape['proposed_action'], 'rule_ids': shape['rule_ids'],
                      'execution': state})
    unplanned = []
    for t in execution_repo.list_trades(conn, cutoff=now):
        if t['side'] != 'buy' or t['voided_at'] is not None or not week_start <= t['executed_at'].astimezone(NY).date() <= week_end:
            continue
        legacy_plan = None
        if t.get('decision_id') is not None:
            decision = execution_repo_legacy.decision_by_id(conn, t['decision_id'])
            legacy_plan = decision['plan_id'] if decision else None
        if execution_module.unplanned(t, legacy_plan_id=legacy_plan if t.get('plan_id') is None else t['plan_id']):
            unplanned.append(t)
    return {'groups': groups, 'units': units, 'fills': fills, 'unplanned': unplanned}


def generate_weekly(week_start: date, *, notes: str = '', supersedes_id=None, now: datetime) -> dict[str, Any]:
    """The `review-8` weekly snapshot (v2.9 D30): executions instead of decisions, the control group is every unit of
    the week, plans split by shadow version, tracks-2 and discipline-2 frozen."""
    if week_start.isoweekday() != 1:
        raise ledger.LedgerError('week_start must be a Monday')
    week_end = week_start + timedelta(days=6)
    session = bars.latest_closed_session(now)
    with core_tx._tx() as conn:
        week = _week_units(conn, week_start, week_end, now=now)
        units = week['units']
        outcomes = _outcomes(conn, units, session, now)
        spy = data_repo.read_bars(conn, BENCHMARK, session, cutoff_at=now, limit=400)
        segments = []
        for ticker in sorted({t['ticker'] for t in execution_repo.list_trades(conn)}):
            ticker_bars = data_repo.read_bars(conn, ticker, session, cutoff_at=now, limit=400)
            segments += [{'ticker': ticker, **review.trade_segment_review(s, ticker_bars, spy)}
                         for s in execution_plan_context._book(conn, ticker)['closed']
                         if week_start <= s['closed_at'].astimezone(NY).date() <= week_end]
        plan_outcomes = []
        week_plans = [p for p in execution_repo.plans_since(conn, week_start) if p['signal_date'] <= week_end]
        chains = _plan_chains(conn, week_plans)        # review-6: first versions vs heads at generation
        for plan in week_plans:
            ticker_bars = data_repo.read_bars(conn, plan['ticker'], session, cutoff_at=now, limit=400)
            # Pool of a plan: the frozen context first, else the tier recorded on its signal date.
            plan_outcomes.append({**plan, 'chain': chains.get(plan['id']), 'pool': review.pool_of(
                (plan.get('inputs') or {}).get('context'),
                data_repo.pool_entry_at(conn, plan['ticker'], plan['signal_date'], cutoff_at=now)),
                'rule_ids': [c['rule_id'] for c in data_repo.unit_candidates(conn, plan['ticker'], plan['signal_date'])
                             if c['action'] == 'buy'],
                **shadow_outcomes(plan, ticker_bars, spy, splits=data_repo.split_events(conn, plan['ticker']))})
        triggers = []
        for group in week['groups']:
            for cand in group['candidates']:
                if cand['action'] != 'buy':
                    continue
                triggers.append({'pool': review.pool_of(
                    None, data_repo.pool_entry_at(conn, group['ticker'], group['signal_date'], cutoff_at=now)),
                    'rule_id': cand['rule_id']})
        track_rows = _track_rows(conn, week_start, week_end, session=session, now=now)
        stats = review.weekly_stats(week_start, units, outcomes, execution_repo.list_trades(conn), segments=segments,
                                    fills=week['fills'], unplanned=week['unplanned'],
                                    entry_opens={k: v.get('entry_price') for k, v in outcomes.items()},
                                    plan_outcomes=plan_outcomes,
                                    # the by-pool plan columns never mix two shadow versions: triggers and the
                                    # plan-shadow-2 plans here, the older plans (no triggers) in by_pool_v1
                                    by_pool=review.pool_summary(triggers, [p for p in plan_outcomes if p['shadow_version'] == _plan_shadow_module.SHADOW_V2]),
                                    by_pool_v1=review.pool_summary([], [p for p in plan_outcomes if p['shadow_version'] != _plan_shadow_module.SHADOW_V2]),
                                    discipline=_week_discipline(conn, session, now),
                                    tracks={'units': track_rows, 'summary': _tracks_module.summarize_v2(track_rows)},
                                    learning=_weekly_learning(conn, week_start, week_end, session=session, now=now))
        stats['generated_at'] = now.isoformat()
        return review.save_weekly(conn, week_start, stats, notes=notes, supersedes_id=supersedes_id)


def _prev_month(m: date) -> date:
    return (m - timedelta(days=1)).replace(day=1)


def _spy_return_between(conn, start_day: date, end_day: date, *, now: datetime) -> float | None:
    index = {b['price_date']: b['adj_close'] for b in
             data_repo.read_bars(conn, BENCHMARK, end_day, cutoff_at=now, limit=400)}
    if start_day not in index or end_day not in index:
        return None
    return float(index[end_day] / index[start_day] - 1)


def generate_monthly(month: date, *, notes: str = '', supersedes_id=None, now: datetime) -> dict[str, Any]:
    """Freeze one month's review; corrections supersede. Missing snapshots show, never fake zeros."""
    from investment_assistant.config import load_config

    if month.day != 1:
        raise ledger.LedgerError('month must be the first day of the month')
    month_end = month.replace(day=[31, 29 if month.year % 4 == 0 and (month.year % 100 != 0 or month.year % 400 == 0)
                                   else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month.month - 1])
    params = load_config().workbench
    with core_tx._tx() as conn:
        core_dbutil.lock(conn, f'monthly:{month}')
        head = review_repo.monthly_head(conn, month)
        if (head['id'] if head else None) != supersedes_id:
            raise ledger.Conflict('该月已有更新的月度复盘；请更正最新一条')
        snapshots = [s for s in execution_repo.list_snapshots(conn) if s['recorded_at'] <= now]
        flows = execution_paper_accounts.known_flows(conn, REAL_ACCOUNT_ID, now)
        ledger_rows = execution_repo.equity_days(conn, REAL_ACCOUNT_ID, cutoff_at=now) if core_schema.accounts_ready(conn) else []
        real_account = execution_repo.account_by_id(conn, REAL_ACCOUNT_ID) if core_schema.accounts_ready(conn) else None
        opened = bool(real_account and real_account['opened_on'])
        def month_return(m):
            return monthly.month_return_v2(m, equity_rows=ledger_rows, snapshots=snapshots, flows=flows, opened=opened)
        months = [month]
        for _ in range(2):
            months.insert(0, _prev_month(months[0]))
        per_month, spy_ret = [], None
        for m in months:
            ret = month_return(m)
            if ret['status'] != 'ok':
                per_month.append({'month': m.isoformat(), 'return': None, 'spy_return': None})
                continue
            spy_r = _spy_return_between(conn, ret['start'].get('session', ret['start'].get('as_of')), ret['end'].get('session', ret['end'].get('as_of')), now=now)
            per_month.append({'month': m.isoformat(), 'return': ret['return'], 'spy_return': spy_r})
            if m == month:
                spy_ret = spy_r
        ret = month_return(month)


        trades = execution_repo.list_trades(conn)
        trades_by_id = {t['id']: t for t in trades}
        tickers = sorted({t['ticker'] for t in trades})
        rs, segments_month, plan_outcomes = [], [], []   # rs: (closed_at, r), sorted below
        for ticker in tickers:
            book = execution_plan_context._book(conn, ticker)
            splits = data_repo.split_events(conn, ticker)
            for seg in book['closed']:
                if not month <= seg['closed_at'].astimezone(NY).date() <= month_end:
                    continue
                segments_month.append({'ticker': ticker, **seg})
                buy_trade = trades_by_id.get(seg['buy_trade_id'])
                plan = execution_chains._plan_of_buy(conn, buy_trade) if buy_trade is not None else None   # plan_id, else old decision
                if plan is None:
                    continue
                buy_day = buy_trade['executed_at'].astimezone(NY).date()
                stop = _discipline_module._at_basis(plan['stop'], plan['signal_date'], buy_day, splits)
                entry, exit_ = seg['entry_cost_per_share'], seg['exit_price']
                if entry != stop:
                    # P4 §5: sensitivity orders by close time, not ticker order
                    rs.append((seg['closed_at'], float((exit_ - entry) / (entry - stop))))
        spy_bars = data_repo.read_bars(conn, BENCHMARK, bars.latest_closed_session(now), cutoff_at=now, limit=400)
        month_plans = [p for p in execution_repo.plans_since(conn, month) if p['signal_date'] <= month_end]
        chains = _plan_chains(conn, month_plans)       # monthly-3: first versions vs heads at generation
        for plan in month_plans:
            ticker_bars = data_repo.read_bars(conn, plan['ticker'], bars.latest_closed_session(now),
                                               cutoff_at=now, limit=400)
            plan_outcomes.append({**plan, 'chain': chains.get(plan['id']), **shadow_outcomes(
                plan, ticker_bars, spy_bars, splits=data_repo.split_events(conn, plan['ticker']))})
        disc = _discipline_v2(
            conn, trades=[t for t in trades if month <= t['executed_at'].astimezone(NY).date() <= month_end],
            segments=segments_month, tickers=tickers, session=bars.latest_closed_session(now), now=now,
            since=month, until=month_end)

        rs = [r for _, r in sorted(rs, key=lambda item: item[0])]
        track_rows = _track_rows(conn, month, month_end, session=bars.latest_closed_session(now), now=now)
        stats = {
            'calc_version': monthly.MONTHLY_VERSION, 'month': month.isoformat(), 'return': ret,
            'tier': monthly.tier(ret['return'], params.return_tiers) if ret['status'] == 'ok' else None,
            'spy': ({'return': spy_ret, 'excess': ret['return'] - spy_ret}
                    if ret['status'] == 'ok' and spy_ret is not None else None),
            'review_flag': monthly.review_flag(per_month),
            'review': monthly.review_detail(per_month),
            'sensitivity': {'approximate': True, 'count': len(rs), 'rows': monthly.sensitivity(rs)},
            'discipline': disc,
            **review.shadow_plan_summaries(plan_outcomes, month, month_end),
            'tracks': {'units': track_rows, 'summary': _tracks_module.summarize_v2(track_rows)},
            'generated_at': now.isoformat(),
        }
        return review_repo.insert_monthly_review(conn, month, monthly.MONTHLY_VERSION, stats, notes, supersedes_id)


def list_monthly(limit: int = 12) -> list[dict[str, Any]]:
    with core_tx._tx() as conn:
        return review_repo.monthly_heads(conn, limit)


def list_weekly(limit: int = 20) -> list[dict[str, Any]]:
    with core_tx._tx() as conn:
        return review.list_weekly(conn, limit)
