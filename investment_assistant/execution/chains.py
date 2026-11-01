"""execution chains use cases; model calls belong to analysis."""
from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable
from investment_assistant.core import schema as core_schema
from investment_assistant.core.constants import NY, BENCHMARK, REAL_ACCOUNT_ID, PLAN_LEVEL_KEYS
from investment_assistant.core.tx import Unavailable
from investment_assistant.core.redact import sanitize_reason
from investment_assistant.data import repo as data_repo
from investment_assistant.execution import repo as execution_repo, repo_legacy as execution_repo_legacy

from investment_assistant.core.texts import AGENT_REASON_TEXT
from investment_assistant.derivation.indicators import price
from investment_assistant.execution import adoption as execution, exits, positions, timeline
from investment_assistant.execution import conditions as condition_registry, plan_levels as plans_module
from investment_assistant.execution import plan_context as execution_context

_UNAVAILABLE_REASONS = ('disabled', 'not_configured', 'not_connected', 'model_unavailable')


_WHY_TEXT = {'fresh_version': '当天刚生成', 'object_cap': '超出复查对象上限', 'max_attempts': '已达重试上限',
             'review_in_progress': '正在复查', 'already_reviewed': '已复查', 'chain_not_active': '链不在复查状态',
             'error': '复查出错'}


DEFAULT_REVIEW_WHEN = [{'kind': 'next_session', 'params': {}}]


ENDED_STATES = ('expired', 'invalidated', 'position_closed', 'superseded', 'closed_by_decision')


ACTIVE_STATES = timeline.ACTIVE_STATES              # at most one per ticker; a new trigger merges into it


REVIEWABLE_STATES = timeline.REVIEWABLE_STATES      # reviewed daily and rechecked (R10)


REVIEW_MAX_OBJECTS = 20


CHAIN_REVIEW_PREVIEW = 40      # 「今日」进行中的 Agent 计划：最近复查的理由只显示前 40 字（§10.2）


def _position_plan_state(conn, ticker: str, book: dict[str, Any], ticker_bars: list[dict[str, Any]],
                         session: date, *, now: datetime) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(plan, active_stop state) for a held ticker; both None when the position has no plan.

    v2.9 D20 / D22 (migration 029 and the timeline tables present): a position that belongs to a holding / exiting
    chain follows the chain's latest version (a stop lifted by a holding-phase revision takes effect); otherwise the
    plan its fills follow — ``trades.plan_id`` first, then the old decision route (``_plan_of_buy``). Fills
    are read as the cutoff knew them (R11). Before 029 only the decision route exists, as before."""
    position = book['position']
    if position is None:
        return None, None
    if core_schema.timeline_ready(conn) and core_schema.executable_ready(conn):

        ctx = ticker_context(conn, ticker, now=now)
        position = ctx['book']['position']
        if position is None:
            return None, None
        held = [i for i in ticker_chain_items(conn, ticker, session=session, now=now, context=ctx)
                if i['state']['state'] in ('holding', 'exiting')]
        if held:
            plan = max(held, key=lambda i: i['chain']['signal_date'])['chain']['head_plan']
        else:
            by_id = {t['id']: t for t in ctx['trades']}
            plans_by_trade = {lot['buy_trade_id']: found for lot in ctx['book']['open']
                              if (found := _plan_of_buy(conn, by_id[lot['buy_trade_id']])) is not None}
            plan = exits.position_plan(ctx['book']['open'], ctx['trades'], plans_by_trade)
        avg_cost = position['avg_cost']
    else:
        trades = execution_repo.list_trades(conn, ticker)
        plans_by_trade = execution_repo.plan_for_buy_trades(conn, [lot['buy_trade_id'] for lot in book['open']])
        plan = exits.position_plan(book['open'], trades, plans_by_trade)
        avg_cost = position['avg_cost']
    if plan is None:
        return None, None
    opened = position['opened_at'].astimezone(NY).date()
    bars_since = [b for b in ticker_bars if b['price_date'] >= opened]
    state = exits.active_stop(plan, bars_since, avg_cost=avg_cost,
                              splits=data_repo.split_events(conn, ticker)) if bars_since else {
        'stop': plan['stop'], 't1_reached': False, 't1_reached_on': None, 'mode': 'initial', 'target1': plan['target1']}
    return plan, state


def unit_buys(conn, ticker: str, signal_date: date) -> list[dict[str, Any]]:
    """Buys linked to any decision in the unit's decision chain (§5.3: the three-way comparison's reading,
    ``unit_decision_ids`` + ``trades.decision_id``). Voided rows are left in; ``chain_state`` drops them."""
    ids = execution_repo_legacy.unit_decision_ids(conn, ticker, signal_date)
    if not ids:
        return []
    return [t for t in execution_repo.list_trades(conn, ticker) if t['side'] == 'buy' and t.get('decision_id') in ids]


def chain_status(conn, ticker: str, signal_date: date, *, session: date,
                 book: dict[str, Any] | None = None, now: datetime | None = None,
                 context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """An agent chain read at session S: ``{'head', 'versions', 'reviews', 'state', 'latest_review'}``, or None
    when the unit has no agent chain. ``latest_review`` is the chain's newest effective review row (maintain /
    revise / invalidate) or None. The caller has checked ``core_schema.timeline_ready``.

    With ``now`` (the cutoff) the chain is judged by `timeline-2` (v2.9 spec §4.1): ``state`` is
    ``chain_state_v2``'s ``{state, mode, active, basis, basis_ref, timeline_version, renewable}`` and the result
    also carries ``chain`` / ``facts`` (its inputs, for the `plan` node and the review) and ``buys`` (the
    chain's live fills, read with ``execution_trades.list_trades(cutoff=now)``); ``context`` (``ticker_context``) saves
    the ticker-wide reads when several chains of one ticker are read. Without ``now`` the `timeline-1` judge runs as
    before (the read views not yet moved to timeline-2; ``book`` may be passed in to save the trade read)."""
    head = execution_repo.chain_head(conn, ticker, signal_date)
    if head is None:
        return None
    versions = execution_repo.chain_versions(conn, ticker, signal_date)
    reviews = execution_repo.chain_reviews(conn, ticker, signal_date)
    effective = [r for r in reviews if r['outcome'] in execution_repo.EFFECTIVE_REVIEW_OUTCOMES]
    latest = effective[-1] if effective else None
    if now is not None:

        item = read_chain_facts(conn, {'ticker': ticker, 'signal_date': signal_date}, session=session,
                                            now=now, context=context)
        return {'head': head, 'versions': versions, 'reviews': reviews, 'state': item['state'],
                'latest_review': latest, 'chain': item['chain'], 'facts': item['facts'], 'buys': item['buys']}
    book = book if book is not None else execution_context._book(conn, ticker)
    state = timeline.chain_state(versions=versions, head_plan=head['plan'], reviews=reviews,
                                 decision=execution_repo_legacy.decision_head(conn, ticker, signal_date),
                                 unit_buys=unit_buys(conn, ticker, signal_date), open_lots=book['open'],
                                 session=session, legacy=False)
    return {'head': head, 'versions': versions, 'reviews': reviews, 'state': state, 'latest_review': latest}


def _review_view(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {'id': row['id'], 'session': row['session'], 'trigger': row['trigger'], 'outcome': row['outcome'],
            'decided_by': row['decided_by'], 'reason': row['reason'],
            'new_plan_id': str(row['new_plan_id']) if row['new_plan_id'] else None,
            'revision_status': (row.get('detail') or {}).get('revision_status'), 'created_at': row['created_at'],
            'conditions': row['conditions']}


def _chain_view(status: dict[str, Any]) -> dict[str, Any]:
    """The ``chain`` dict of a unit's agent block (§12 GET today); §10.1 also shows the head's generation
    time (``cutoff_at``) and its host diff against the previous version (``diff``, None for version 0)."""
    version = status['head']['version']
    return {'version_no': version['version_no'],
            'head_plan_id': str(status['head']['plan']['id']),
            'cutoff_at': version['cutoff_at'], 'diff': version['diff'],
            'state': status['state']['state'], 'mode': status['state']['mode'], 'basis': status['state']['basis'],
            'latest_review': _review_view(status['latest_review'])}


def _active_chains(conn, session: date, *, now: datetime) -> list[dict[str, Any]]:
    """§10.2 进行中的 Agent 计划: the chains in the entry, holding or exiting phase at S (`timeline-2`, no decision
    needed any more)."""
    rows: list[dict[str, Any]] = []
    for chain in execution_repo.all_chains(conn):
        ticker, signal_date = chain['ticker'], chain['signal_date']
        status = chain_status(conn, ticker, signal_date, session=session, now=now)
        if status is None:
            continue
        state = status['state']['state']
        if state not in timeline.ACTIVE_STATES:
            continue
        latest = status['latest_review']
        today_rows = [r for r in status['reviews'] if r['session'] == session]
        fired = [s['kind'] for s in (today_rows[-1]['signals'] or []) if s.get('fired')] if today_rows else []
        head = status['head']
        rows.append({
            'ticker': ticker, 'signal_date': signal_date, 'state': state, 'mode': status['state']['mode'],
            'basis': status['state']['basis'], 'version_no': head['version']['version_no'],
            'head_plan_id': str(head['plan']['id']), 'valid_until': head['plan']['valid_until'],
            'pool': chain['pool'],
            'latest_review': ({'outcome': latest['outcome'], 'decided_by': latest['decided_by'],
                               'created_at': latest['created_at'],
                               'reason': latest['reason'][:CHAIN_REVIEW_PREVIEW]} if latest else None),
            'signals_today': fired,
        })
    return rows


def held_chain_states(conn, session: date, *, tickers: set[str] | None = None,
                      now: datetime | None = None) -> dict[str, str]:
    """D7: held ticker -> state at S of the agent chain that holds the position: a chain in state holding or
    exiting, or one that ended while its buys still have open lots (invalidated by a review, closed by an old
    decision) — such a holding has a chain, it just is no longer reviewed. ``holding`` / ``exiting`` win over an ended
    state when a ticker has several. A held ticker absent from the result has no agent chain at all. ``tickers`` limits
    the read to those tickers. With ``now`` the chains are judged by `timeline-2` and their buys are the fills linked
    through ``plan_id`` or an old decision (``execution.chain_buys``); without it the `timeline-1` reading stays."""
    out: dict[str, str] = {}
    books: dict[str, dict[str, Any]] = {}
    contexts: dict[str, dict[str, Any]] = {}
    held_now: set[str] | None = None
    for chain in execution_repo.all_chains(conn):
        ticker, signal_date = chain['ticker'], chain['signal_date']
        if tickers is not None and ticker not in tickers:
            continue
        if now is not None:

            if held_now is None:
                held_now = execution_repo.open_position_tickers(conn)
            if ticker not in held_now:
                continue
            context = contexts.get(ticker) or contexts.setdefault(ticker, ticker_context(conn, ticker, now=now))
            book = context['book']
            if book['position'] is None:
                continue
            status = chain_status(conn, ticker, signal_date, session=session, now=now, context=context)
            if status is None:
                continue
            buy_ids = {str(b['id']) for b in status['buys']}
        else:
            book = books.get(ticker) or books.setdefault(ticker, execution_context._book(conn, ticker))
            if book['position'] is None:
                continue
            status = chain_status(conn, ticker, signal_date, session=session, book=book)
            if status is None:
                continue
            buy_ids = {str(b['id']) for b in unit_buys(conn, ticker, signal_date) if b.get('voided_at') is None}
        state = status['state']['state']
        owns_lots = any(str(lot.get('buy_trade_id')) in buy_ids and Decimal(str(lot.get('quantity') or 0)) > 0
                        for lot in book['open'])
        if state not in ('holding', 'exiting') and not owns_lots:
            continue
        if out.get(ticker) not in ('holding', 'exiting'):
            out[ticker] = state
    return out


def ticker_context(conn, ticker: str, *, now: datetime, point_in_time: bool = False, account_id=REAL_ACCOUNT_ID) -> dict[str, Any]:
    """What every chain of one ticker is judged from, read once at cutoff ``now``: the fills as the cutoff knew
    them (``execution_trades.list_trades(cutoff=now)``, R11), the position they build, the ticker's chains and retrigger rows, and
    the plan -> chain map of the fills' linked plans (D22). Raises ``positions.PositionError`` on a broken fill
    history; the callers read it under a savepoint.

    ``point_in_time=True`` also cuts the chains: only those whose first version existed by ``now`` (a chain opened
    later is not there yet). Readers of a past day (the plan table of an older session, the plans a back-filled fill
    may follow) read this way together with ``read_chain_facts(point_in_time=True)``."""
    ticker = ticker.upper()
    trades = execution_repo.list_trades(conn, ticker, cutoff=now, account_id=account_id)
    book = positions.build(trades, splits=data_repo.split_events(conn, ticker, **({'cutoff_at': now} if str(account_id) != str(REAL_ACCOUNT_ID) else {})))
    chains = execution_repo.chains_for_ticker(conn, ticker, cutoff=now if point_in_time else None)
    retriggers = execution_repo.chain_retriggers_for(conn, ticker, cutoff=now)
    versions_by_plan = {v['plan_id']: v for c in chains for v in execution_repo.chain_versions(conn, ticker, c['signal_date'])}
    plan_chain: dict[Any, Any] = {}
    for plan_id in {t['plan_id'] for t in trades if t.get('plan_id') is not None}:
        plan = execution_repo.plan_by_id(conn, plan_id)
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
    when the unit has no agent chain. Fills come from ``execution_trades.list_trades(cutoff=now)`` (R11), retriggers are cut at
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
    versions = execution_repo.chain_versions(conn, ticker, signal_date)
    if point_in_time:
        versions = [v for v in versions if v['created_at'] <= now]
    if not versions:
        return None
    ctx = context or ticker_context(conn, ticker, now=now, point_in_time=point_in_time)
    head_plan = execution_repo.plan_by_id(conn, versions[-1]['plan_id'])
    buys = execution.chain_buys((ticker, signal_date), ctx['trades'], plan_chain=ctx['plan_chain'],
                                legacy_decision_ids=execution_repo_legacy.unit_decision_ids(conn, ticker, signal_date))
    decision = execution_repo_legacy.decision_head(conn, ticker, signal_date)
    if point_in_time and decision is not None and decision['decided_at'] > now:
        decision = None
    facts = {'decision': decision, 'buys': buys,
             'open_lots': ctx['book']['open'],
             'retriggers': [r for r in ctx['retriggers'] if r['chain_signal_date'] == signal_date],
             'later_chains': [{'signal_date': c['signal_date']} for c in ctx['chains']
                              if signal_date < c['signal_date'] <= session]}
    reviews = execution_repo.chain_reviews(conn, ticker, signal_date)
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


def _chain_pool(conn, status: dict[str, Any]) -> str:
    """`pool` of the run that produced version 0 (main / extended)."""
    first = status['versions'][0] if status['versions'] else None
    run = data_repo.agent_run_by_id(conn, first['agent_run_id']) if first is not None else None
    return (run or {}).get('pool') or 'main'


def review_object(conn, ticker: str, signal_date: date, *, session: date, pool: str | None = None,
                  book: dict[str, Any] | None = None, now: datetime | None = None,
                  context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """One agent chain read at S as a review object, or None without a chain. Keys: ticker,
    signal_date, state, mode, pool (version 0's), head ({'plan', 'version'}), version_no, as_of_session,
    valid_until. The caller decides whether the state is reviewable (``REVIEWABLE_STATES``). With ``now`` the
    chain is judged by `timeline-2` (the review stage always passes it)."""
    status = chain_status(conn, ticker, signal_date, session=session, book=book, now=now, context=context)
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
    for chain in execution_repo.all_chains(conn):
        ticker = chain['ticker']
        try:
            with conn.transaction():        # savepoint: a broken chain must not abort the others' reads
                if ticker not in contexts:
                    contexts[ticker] = ticker_context(conn, ticker, now=now)
                ctx = contexts[ticker]
                obj = review_object(conn, ticker, chain['signal_date'], session=session, pool=chain['pool'],
                                    now=now, context=ctx)
                reviewed = obj is not None and any(
                    r['outcome'] in execution_repo.EFFECTIVE_REVIEW_OUTCOMES
                    for r in execution_repo.batch_attempts(conn, obj['ticker'], obj['signal_date'], session))
                if obj is not None and _owns_open_lots(conn, ctx, chain):
                    chained.add(ticker)
        except Unavailable:
            raise
        except Exception as exc:            # §7.4 / §13: one chain's surprise only affects that chain
            failures.append((ticker, sanitize_reason(exc)))
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
    without = execution_repo.open_position_tickers(conn) - chained     # D7: a chain that ended under a holding is still its chain
    return objects[:REVIEW_MAX_OBJECTS], {'skips': skips, 'excluded': excluded, 'ended': ended,
                                          'exiting': exiting, 'failures': failures,
                                          'holdings_without_chain': sorted(without)}


def _owns_open_lots(conn, ctx: dict[str, Any], chain: dict[str, Any]) -> bool:
    """Whether some open lot of the ticker was bought on this chain (its linked fills, D22, or the old route)."""
    if ctx['book']['position'] is None:
        return False
    open_ids = {str(lot['buy_trade_id']) for lot in ctx['book']['open']}
    buys = execution.chain_buys((ctx['ticker'], chain['signal_date']), ctx['trades'], plan_chain=ctx['plan_chain'],
                                legacy_decision_ids=execution_repo_legacy.unit_decision_ids(conn, ctx['ticker'],
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
    for chain in execution_repo.all_chains(conn):
        if chain['ticker'].upper() in skip or chain['ticker'] in out:
            continue
        try:
            with conn.transaction():
                status = chain_status(conn, chain['ticker'], chain['signal_date'], session=session, now=now)
        except Unavailable:
            raise
        except Exception as exc:
            if failures is not None:
                failures.append((chain['ticker'], sanitize_reason(exc)))
            continue
        if status is not None and status['state']['state'] in ACTIVE_STATES:
            out.add(chain['ticker'])
    return sorted(out)


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
    for row in execution_repo.chain_retriggers_for(conn, ticker, chain_signal_date=signal_date, cutoff=now):
        baseline: dict[str, Any] = {}
        if row.get('baseline_plan_id') is not None:
            plan = execution_repo.plan_by_id(conn, row['baseline_plan_id'])
            baseline = {key: plan[key] for key in plans_module.LEVEL_KEYS} if plan is not None else {}
        wanted = set(row.get('rule_ids') or [])
        frozen = {c['rule_id']: {k: v for k, v in (c.get('inputs') or {}).items() if k not in PLAN_LEVEL_KEYS}
                  for c in data_repo.unit_candidates(conn, ticker, row['unit_signal_date']) if c['rule_id'] in wanted}
        out.append({**row, 'baseline': baseline, 'frozen_values': frozen})
    return out


def _plan_of_buy(conn, trade: dict) -> dict | None:
    """The plan a fill follows: its ``plan_id``, else (old data) the plan its decision confirmed."""
    if trade.get('plan_id') is not None:
        return execution_repo.plan_by_id(conn, trade['plan_id'])
    if trade.get('decision_id') is not None:
        return execution_repo.plan_for_buy_trades(conn, [trade['id']]).get(trade['id'])
    return None


def _holding_input(conn, ctx: dict, *, ticker: str, buys: list[dict], plan: dict | None, bars: list[dict]) -> dict | None:
    """``timeline.holding_facts``' input for the ticker's position at the cutoff (R11): quantity and cost from the
    open lots, the first open buy's New York day, the bars, the effective ``plan`` and the ``initial_plan`` whose
    stop prices 1R (the plan of the chain's first fill, D20)."""
    position = ctx['book']['position']
    if position is None:
        return None
    return {'ticker': ticker, 'quantity': position['quantity'], 'avg_cost': position['avg_cost'],
            'first_buy_session': position['opened_at'].astimezone(NY).date(), 'bars': bars,
            'splits': data_repo.split_events(conn, ticker), 'plan': plan,
            'initial_plan': _plan_of_buy(conn, buys[0]) if buys else None}


def _read(conn, ticker: str, signal_date: date, *, session: date, now: datetime, trigger: str,
          filings_known: bool) -> dict[str, Any]:
    """Everything the host needs, read at cutoff ``now``; ``{'skip': reason}`` when the chain is not
    reviewable right now (not active, fresh head, already reviewed by the batch on S)."""
    context = ticker_context(conn, ticker, now=now)
    status = chain_status(conn, ticker, signal_date, session=session, now=now, context=context)
    if status is None or status['state']['state'] not in REVIEWABLE_STATES:
        return {'skip': 'chain_not_active'}
    head = status['head']
    version, plan = head['version'], head['plan']
    if trigger == 'batch' and version['as_of_session'] == session:
        return {'skip': 'fresh_version'}
    attempts = (execution_repo.batch_attempts(conn, ticker, signal_date, session) if trigger == 'batch'
                else [r for r in status['reviews'] if r['session'] == session and r['trigger'] == trigger])
    if trigger == 'batch' and any(r['outcome'] in execution_repo.EFFECTIVE_REVIEW_OUTCOMES for r in attempts):
        return {'skip': 'already_reviewed'}
    last = execution_repo.last_effective_review(conn, plan['id'])
    since_session, since_at = timeline.since_window(version, last)
    regime_row = data_repo.regime_for(conn, session, cutoff_at=now)
    inner = (plan.get('inputs') or {}).get('context') or {}
    entry = data_repo.pool_entry_at(conn, ticker, session, cutoff_at=now)
    etf = (entry or {}).get('sector_etf') or (inner.get('sector') or {}).get('etf')
    sector = ((regime_row or {}).get('sectors') or {}).get(etf) if etf else None
    earnings = data_repo.latest_earnings(conn, ticker, cutoff_at=now)
    events, _ = execution_context._filing_events(conn, ticker, session, now, since=version['as_of_session'])
    inputs = timeline.effective_inputs(execution_repo.user_inputs_for(conn, ticker, signal_date), now)
    bars = data_repo.read_bars(conn, ticker, session, cutoff_at=now)
    mode = status['state']['mode']
    seen = {
        'status': status, 'head': head, 'version': version, 'plan': plan, 'mode': mode,
        'pool': _chain_pool(conn, status),
        'run': data_repo.agent_run_by_id(conn, version['agent_run_id']), 'last_review': last,
        'attempts': attempts, 'since_session': since_session, 'since_at': since_at,
        'bars': bars,
        'spy': data_repo.read_bars(conn, BENCHMARK, session, cutoff_at=now),
        'filings': sorted(events, key=lambda f: (f['filing_date'], str(f['id']))) if filings_known else None,
        'abnormal': [c for c in data_repo.read_candidates(conn, ticker, since=since_session)
                     if c['rule_id'] == 'abnormal_move'],
        'regime_label': regime_row['label'] if regime_row is not None else None,
        'sector_state': (sector or {}).get('state'),
        'earnings_date': (earnings['earnings_date'] if earnings and earnings['status'] == 'known' else None),
        'inputs_new': [r for r in inputs if r['created_at'] > since_at],
        'inputs_old': [r for r in inputs if r['created_at'] <= since_at],
        'trigger_rows': data_repo.unit_candidates(conn, ticker, signal_date),
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
    indicators = price.indicators(seen['bars'], seen['spy'])
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
    for ticker in sorted({t['ticker'] for t in execution_repo.list_trades(conn, cutoff=now)}):
        try:
            if positions.build(execution_repo.list_trades(conn, ticker, cutoff=now),
                               splits=data_repo.split_events(conn, ticker))['position'] is not None:
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
    bars = data_repo.read_bars(conn, ticker, session, cutoff_at=now)
    earlier = [r for r in execution_repo.holding_status_for(conn, ticker=ticker, account_id=account_id) if r['session'] < session]
    first_buy = position['opened_at'].astimezone(NY).date()
    previous = earlier[-1] if earlier and earlier[-1]['session'] >= first_buy else None
    facts: dict[str, Any] = {
        'ticker': ticker, 'quantity': position['quantity'], 'avg_cost': position['avg_cost'],
        'first_buy_session': first_buy, 'bars': bars,
        'splits': data_repo.split_events(conn, ticker, **({'cutoff_at': now} if str(account_id) != str(REAL_ACCOUNT_ID) else {})),
        'previous': previous,
        'sold_after_t1': any(t['side'] == 'sell' and t.get('voided_at') is None
                             and t['executed_at'].astimezone(NY).date() >= session for t in ctx['trades'])}
    if item is not None:
        chain = item['chain']
        key = (ticker, chain['signal_date'].isoformat())
        today = [r for r in chain['reviews'] if r['session'] == session]
        effective = [r for r in today if r['outcome'] in execution_repo.EFFECTIVE_REVIEW_OUTCOMES]
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
        equity = execution_repo.latest_equity_day(conn, account_id, on_or_before=session)
        saved = ((equity or {}).get('detail') or {}).get('paper_states', {}).get(ticker)
        if saved:
            from investment_assistant.execution.paper import fill as paper_fill
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
                    rows.append(execution_repo.insert_holding_status(conn, values))
        except Unavailable:
            raise
        except Exception as exc:        # §13: one ticker's failure only costs that ticker its row (check reports it)
            failures.append((ticker, sanitize_reason(exc)))
    return {'rows': rows, 'failures': failures}
