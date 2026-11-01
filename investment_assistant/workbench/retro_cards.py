"""Review cards: detection, freezing, drafting, review, views and CLI (v2.7 spec §4, §9, §12).

The pure rules live in ``retro`` (end events, frozen facts, card status); this module reads the ledger, calls
them, writes the cards (``review_cards``, ``review_card_drafts``, ``review_card_verdicts`` and the lesson drafts /
knowledge a verdict creates) and serves the pages. Detection reads in one read-only transaction, inserts the new
cards in one write transaction and drafts each card in a write transaction of its own: a surprise on one subject or
card only costs that subject or card (spec §9).

Layering: ``api/routes`` -> this module (transactions here) -> ``retro`` / ``knowledge_items`` (pure) -> ``repository``.
A few small SELECTs the repository has no function for (the draft verdict rows, the card of a draft) are made here;
this module never UPDATEs or DELETEs.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from contextlib import ExitStack
from datetime import UTC, date, datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import psycopg

from investment_assistant.workbench import (bars as bars_module, families,
                                            knowledge_items, ledger, my_knowledge, plan_shadow, repository, retro, service)
from investment_assistant.workbench import tracks as tracks_module
from investment_assistant.workbench import stages as stages_module
from investment_assistant.workbench.agent import graph as agent_graph
from investment_assistant.workbench.agent import model as agent_model
from investment_assistant.workbench.agent import tools as agent_tools
from investment_assistant.workbench.agent.growth_check import FIELDS as CITED_FIELDS
from investment_assistant.core import tx as core_tx

NY = ZoneInfo('America/New_York')
MIGRATION_MISSING = '迁移 027 未执行'
NOTE_MAX = 2000
CARD_LOCK = 'review-card-verdict'      # advisory lock key prefix: verdict, draft writes and skip rows of one card
LEGACY_DECISION_KEYS = ('decision', 'final_action', 'reason', 'decided_at', 'plan_id', 'agent_plan_id')   # + `text`
LESSON_KEYS = ('title', 'body', 'scope', 'counter_examples', 'concepts', 'tickers')
_EPOCH = date(2000, 1, 1)


# ---------------------------------------------------------------- small helpers

def _read_ro(fn: Callable[[Any], Any]) -> Any:
    """The tools' ``ctx.read``: a real read-only transaction (``service._read_only`` is a pass-through)."""
    with core_tx.read_only_tx() as conn:
        return fn(conn)


def require_ready(conn) -> None:
    if not repository.growth_ready(conn):
        raise service.Unavailable(MIGRATION_MISSING)
    if not repository.executable_ready(conn):           # retro-2 reads trades.plan_id, plan_notes, chain_retriggers
        raise service.Unavailable('migration_029_missing')


def _ny_day(moment: datetime) -> date:
    return moment.astimezone(NY).date()


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _concept_ids() -> set[str]:
    return set(my_knowledge.concept_versions() or ())


# ---------------------------------------------------------------- detection (read side)

class _Cache:
    """Per-ticker reads shared by the subjects of one scan: bars, splits, book, trades, plan signal days."""

    def __init__(self, conn, session: date, now: datetime):
        self.conn, self.session, self.now = conn, session, now
        self.spy = repository.read_bars(conn, service.BENCHMARK, session, cutoff_at=now, limit=400)
        self._by_ticker: dict[str, dict[str, Any]] = {}
        self._contexts: dict[str, dict[str, Any]] = {}

    def context(self, ticker: str) -> dict[str, Any]:
        """``plan_review.ticker_context`` of the ticker at the scan's cutoff (the fills as it knew them)."""
        found = self._contexts.get(ticker)
        if found is None:
            from investment_assistant.workbench import plan_review     # imports service

            found = self._contexts[ticker] = plan_review.ticker_context(self.conn, ticker, now=self.now)
        return found

    def ticker(self, ticker: str) -> dict[str, Any]:
        found = self._by_ticker.get(ticker)
        if found is None:
            conn = self.conn
            trades = repository.list_trades(conn, ticker)
            splits = repository.split_events(conn, ticker)
            candidates = repository.read_candidates(conn, ticker, since=_EPOCH)
            found = {
                'bars': repository.read_bars(conn, ticker, self.session, cutoff_at=self.now, limit=400),
                'splits': splits, 'trades': trades, 'candidates': candidates,
                'book': service._book(conn, ticker),
                'stop_days': [c['signal_date'] for c in candidates if c['rule_id'] == 'plan_stop'],
                'target_days': [c['signal_date'] for c in candidates if c['rule_id'] == 'plan_target'],
            }
            self._by_ticker[ticker] = found
        return found


def _effective_invalidation(reviews: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The earliest effective review that invalidated the chain: ``{id, session, reason}`` or None."""
    found = [r for r in reviews if r['outcome'] == 'invalidate']
    if not found:
        return None
    first = min(found, key=lambda r: (r['session'], r['created_at'], r['id']))
    return {'id': first['id'], 'session': first['session'], 'reason': first['reason']}


def _evaluate(subject: dict[str, Any], cache: _Cache, *, window_start: date, known: Callable[[str], bool],
              full: bool = False) -> dict[str, Any]:
    """§4.1 for one subject on rows already read. ``{'state': 'none'}``, ``{'state': 'no_plan', 'ticker',
    'signal_date', 'end_session'}`` (a held unit that closed inside the window with no ``plan_id`` anywhere on its
    chain: the end is known, the reference plan is not) or ``{'state': 'event', 'event', 'ref_plan', 'sub',
    'known'}`` (``known``: a card with this key exists)."""
    ticker = subject['ticker']
    buys = subject['buys']
    if subject['kind'] == 'trade' and not full and known(f"trade:{subject['buy_trade_id']}"):
        return {'state': 'event', 'known': True, 'event': {'event_key': f"trade:{subject['buy_trade_id']}"},
                'ref_plan': None, 'sub': subject}
    data = cache.ticker(ticker)
    buy_ids = {b['id'] for b in buys}
    segments = [s for s in data['book']['closed'] if s['buy_trade_id'] in buy_ids]
    open_lots = [lot for lot in data['book']['open'] if lot['buy_trade_id'] in buy_ids]
    decision = subject['decision']
    if subject['kind'] == 'unit':
        if subject.get('v2'):
            ref_plan = retro.reference_plan_v2(decision=decision, plans=subject['plans'], has_buys=bool(buys),
                                               buys=buys, chain=subject['decisions'])
            if ref_plan is None:        # a chain opened without a rule baseline: its first Agent version
                ref_plan = next((p for p in subject['plans'] if p['kind'] == 'agent'), None)
        else:
            ref_plan = retro.reference_plan(decision=decision, plans=subject['plans'], has_buys=bool(buys),
                                            buys=buys, chain=subject['decisions'])
        if ref_plan is None:
            # decide the end first: a held unit counts as "no plan" only when its close falls inside the window
            closed = retro.close_session(segments=segments, open_lots=open_lots) if buys else None
            if closed is not None and window_start <= closed <= cache.session:
                return {'state': 'no_plan', 'ticker': ticker, 'signal_date': subject['signal_date'],
                        'end_session': closed}
            return {'state': 'none'}
        sub = {'kind': 'unit', 'ticker': ticker, 'signal_date': subject['signal_date']}
    else:
        ref_plan = None
        sub = {'kind': 'trade', 'ticker': ticker, 'signal_date': None, 'buy_trade_id': subject['buy_trade_id']}
    shadow = None
    if not buys and ref_plan is not None and ref_plan['status'] == 'ok':
        shadow = plan_shadow.simulate(ref_plan, data['bars'], cache.spy, splits=data['splits'])
    event = retro.end_event(
        subject=sub, ref_plan=ref_plan, buys=buys, segments=segments, open_lots=open_lots, shadow=shadow,
        invalidating_review=_effective_invalidation(subject['reviews']),
        decided_session=_ny_day(decision['decided_at']) if decision else None,
        stop_days=data['stop_days'], target_days=data['target_days'], bars=data['bars'], session=cache.session,
        window_start=window_start)
    if event is None:
        return {'state': 'none'}
    return {'state': 'event', 'event': event, 'ref_plan': ref_plan, 'sub': sub, 'shadow': shadow,
            'segments': segments, 'known': known(event['event_key'])}


def _plans_with_versions(conn, plans: list[dict[str, Any]], ready: bool) -> list[dict[str, Any]]:
    out = []
    for plan in plans:
        version = (repository.version_by_plan(conn, plan['id'])
                   if ready and plan['kind'] == 'agent' and plan['plan_version'] in (agent_graph.AGENT_V2, agent_graph.AGENT_V3)
                   else None)
        out.append({**plan, 'version_no': version['version_no'] if version else None,
                    'origin': version['origin'] if version else None,
                    'cutoff_at': version['cutoff_at'] if version else None})
    return out


def _knowledge_fact(conn, plan_ids: list[Any]) -> list[dict[str, Any]]:
    """The knowledge versions this unit's plans cited: ``[{ref, title_at_version, fields}]``."""
    cited: dict[tuple[int, int], list[str]] = {}
    for row in repository.citations_for_plans(conn, plan_ids):
        fields = cited.setdefault((row['knowledge_id'], row['version']), [])
        if row['field'] not in fields:
            fields.append(row['field'])
    out = []
    for (knowledge_id, version), fields in sorted(cited.items()):
        versions, _ = repository.knowledge_rows(conn, cutoff=None, knowledge_id=knowledge_id)
        title = next((v['title'] for v in versions if v['version'] == version), '')
        out.append({'ref': knowledge_items.ref(knowledge_id, version), 'title_at_version': title,
                    'fields': [f for f in CITED_FIELDS if f in fields]})
    return out


def _reviews_fact(reviews: list[dict[str, Any]], has_chain: bool) -> dict[str, Any] | None:
    if not has_chain:
        return None
    outcomes = {name: sum(1 for r in reviews if r['outcome'] == name)
                for name in ('maintain', 'revise', 'invalidate', 'skipped', 'error')}
    invalidating = [r for r in reviews if r['outcome'] == 'invalidate']
    first = min(invalidating, key=lambda r: (r['session'], r['created_at'], r['id'])) if invalidating else None
    return {'count': len(reviews), 'outcomes': outcomes,
            'invalidated': None if first is None else {'session': first['session'], 'reason': first['reason'],
                                                      'decided_by': first['decided_by']}}


def discipline_of(conn, cache: _Cache, ticker: str, buy_ids: set, *, session: date) -> dict[str, Any]:
    """The card's buys judged by `discipline-2` as of ``session`` — the same call ``service.discipline`` makes,
    narrowed to this ticker; ``retro.discipline_summary`` keeps only the card's buys (§4.3)."""
    data = cache.ticker(ticker)
    live = [t for t in data['trades'] if t['voided_at'] is None]
    evaluated = service._discipline_v2(conn, trades=live, segments=data['book']['closed'], tickers=[ticker],
                                       session=session, now=cache.now, since=_EPOCH)
    return retro.discipline_summary(evaluated, buy_ids, as_of=session)


def _freeze(conn, item: dict[str, Any], cache: _Cache, *, session: date, now: datetime) -> dict[str, Any]:
    """The frozen facts of one event (§4.2), from rows read in this transaction: the ten `retro-1` facts, or — for the
    subjects `_subjects_v2` builds — the eleven `retro-2` ones."""
    if item['subject'].get('v2'):
        return _freeze_v2(conn, item, cache, session=session, now=now)
    sub, event, subject = item['sub'], item['event'], item['subject']
    ticker, buys = sub['ticker'], subject['buys']
    data = cache.ticker(ticker)
    ready = repository.timeline_ready(conn)
    plans = _plans_with_versions(conn, subject['plans'], ready)
    plan_by_id = {str(p['id']): p for p in plans}
    decision = subject['decision']
    versions = repository.chain_versions(conn, ticker, sub['signal_date']) if (ready and sub['kind'] == 'unit') else []

    roles: dict[str, list[str]] = {}

    def role(plan_id: Any, name: str) -> None:
        if plan_id is not None and str(plan_id) in plan_by_id:
            roles.setdefault(str(plan_id), []).append(name)

    role(item['ref_plan']['id'] if item['ref_plan'] else None, 'reference')
    role(decision.get('plan_id') if decision else None, 'executed')
    if buys and item['ref_plan'] is not None and 'executed' not in roles.get(str(item['ref_plan']['id']), []):
        role(item['ref_plan']['id'], 'executed')              # a held unit's reference is the plan its buys executed
    role(decision.get('agent_plan_id') if decision else None, 'seen_at_decision')
    if versions:
        role(versions[0]['plan_id'], 'first')
        role(versions[-1]['plan_id'], 'head')

    shadow_by_plan: dict[str, Any] = {}
    for plan in plans:
        known_shadow = item.get('shadow') if item['ref_plan'] and plan['id'] == item['ref_plan']['id'] else None
        if known_shadow is not None:
            shadow_by_plan[str(plan['id'])] = known_shadow
        elif plan['status'] == 'ok':
            shadow_by_plan[str(plan['id'])] = plan_shadow.simulate(plan, data['bars'], cache.spy,
                                                                   splits=data['splits'])
        else:
            shadow_by_plan[str(plan['id'])] = None

    track_row = (service.unit_track_row(conn, ticker, sub['signal_date'], session=session, now=now)
                 if sub['kind'] == 'unit' else None)
    executed = plan_by_id.get(str(decision['plan_id'])) if decision and decision.get('plan_id') else None
    text = retro.decision_text(decision, executed,
                               agent_version_no=executed.get('version_no') if executed else None)
    segments = item.get('segments') or []
    sell_ids = {s['sell_trade_id'] for s in segments}
    trades = [*buys, *[t for t in data['trades'] if t['id'] in sell_ids and t.get('voided_at') is None]]
    actual = (track_row or {}).get('actual') or {}
    actual_r = actual.get('r') if actual.get('status') in ('closed', 'open') else None
    pool = (track_row or {}).get('pool')
    subject_fact = {**sub, 'pool': pool}
    return retro.build_facts(
        subject=subject_fact, end=event, plans=plans, roles=roles, bars=data['bars'], splits=data['splits'],
        shadow_by_plan=shadow_by_plan, track_row=track_row, decision=decision, decision_text=text, trades=trades,
        segments=segments, actual_r=actual_r,
        discipline=discipline_of(conn, cache, ticker, {b['id'] for b in buys}, session=session),
        reviews=_reviews_fact(subject['reviews'], bool(versions)),
        knowledge=_knowledge_fact(conn, [p['id'] for p in plans]) if sub['kind'] == 'unit' else [])


def _subjects_v2(conn, cache: _Cache, *, since: date, until: date) -> list[dict[str, Any]]:
    """The `retro-2` subjects (v2.9 D29): a unit or chain that has fills (through ``plan_id``, a merged unit's baseline
    included, or an old decision) or carries a live note of yours, and the plan-less buys. A chain or unit with no
    fill and no note earns no card. Pruned like `retro-1`: a unit whose fills are all before ``since`` and whose
    ticker was flat before it ended long ago."""
    keys = repository.retro_subject_keys(conn, until=until)
    flat: dict[str, bool] = {}

    def closed_before(ticker: str) -> bool:
        if ticker not in flat:
            flat[ticker] = not repository._open_before(conn, ticker, since)
        return flat[ticker]

    out: list[dict[str, Any]] = []
    for ticker, signal_date in keys['units']:
        context = cache.context(ticker)
        buys = service.unit_fills(conn, ticker, signal_date, context)
        versions = repository.chain_versions(conn, ticker, signal_date)
        plans = repository.plans_for_unit(conn, ticker, signal_date)
        known = {p['id'] for p in plans}
        for buy in buys:
            if buy.get('plan_id') is not None and buy['plan_id'] not in known:
                linked = repository.plan_by_id(conn, buy['plan_id'])      # a merged unit's baseline, an own plan
                if linked is not None:
                    plans.append(linked)
                    known.add(linked['id'])
        notes = (repository.plan_notes_for(conn, ticker, chain_signal_date=signal_date, cutoff=cache.now) if versions
                 else [n for p in plans if p['kind'] in ('rule', 'own')
                       for n in repository.plan_notes_for(conn, ticker, plan_id=p['id'], cutoff=cache.now)])
        notes = [n for n in notes if n['status'] == 'active']
        if not buys and not notes:
            continue
        if buys and all(_ny_day(b['executed_at']) < since for b in buys) and closed_before(ticker):
            continue
        decisions = repository.unit_decision_chain(conn, ticker, signal_date)
        out.append({'kind': 'unit', 'v2': True, 'ticker': ticker, 'signal_date': signal_date, 'buy_trade_id': None,
                    'decision': repository.decision_head(conn, ticker, signal_date, timeline=True),
                    'decision_ids': [d['id'] for d in decisions], 'decisions': decisions, 'buys': buys, 'plans': plans,
                    'reviews': repository.chain_reviews(conn, ticker, signal_date), 'notes': notes,
                    'has_chain': bool(versions)})
    for buy in keys['trades']:
        if _ny_day(buy['executed_at']) < since and closed_before(buy['ticker']):
            continue
        out.append({'kind': 'trade', 'v2': True, 'ticker': buy['ticker'], 'signal_date': None,
                    'buy_trade_id': buy['id'], 'decision': None, 'decision_ids': [], 'decisions': [], 'buys': [buy],
                    'plans': [], 'reviews': [], 'notes': [], 'has_chain': False})
    return out


def _freeze_v2(conn, item: dict[str, Any], cache: _Cache, *, session: date, now: datetime) -> dict[str, Any]:
    """The eleven frozen `retro-2` facts of one event (v2.9 D29): `retro-1`'s ten with the decision replaced by what
    you executed (`execution`, with the notes of the time and an old decision under `legacy_decision`) and the daily
    holding status of the position (`holding`)."""
    sub, event, subject = item['sub'], item['event'], item['subject']
    ticker, buys = sub['ticker'], subject['buys']
    data = cache.ticker(ticker)
    ready = repository.timeline_ready(conn)
    plans = _plans_with_versions(conn, subject['plans'], ready)
    plan_by_id = {str(p['id']): p for p in plans}
    decision = subject['decision']
    versions = repository.chain_versions(conn, ticker, sub['signal_date']) if (ready and sub['kind'] == 'unit') else []

    from investment_assistant.workbench import plan_review     # imports service

    plans_by_trade: dict[Any, dict[str, Any]] = {}
    for buy in buys:
        linked = plan_review._plan_of_buy(conn, buy)          # its own plan_id, else the plan of its old decision
        if linked is not None:
            plans_by_trade[buy['id']] = plan_by_id.get(str(linked['id']), linked)

    roles: dict[str, list[str]] = {}

    def role(plan_id: Any, name: str) -> None:
        if plan_id is not None and str(plan_id) in plan_by_id:
            roles.setdefault(str(plan_id), []).append(name)

    role(item['ref_plan']['id'] if item['ref_plan'] else None, 'reference')
    for plan in plans_by_trade.values():
        if 'executed' not in roles.get(str(plan['id']), []):
            role(plan['id'], 'executed')
    role(decision.get('agent_plan_id') if decision else None, 'seen_at_decision')
    if versions:
        role(versions[0]['plan_id'], 'first')
        role(versions[-1]['plan_id'], 'head')

    shadow_by_plan: dict[str, Any] = {}
    for plan in plans:
        known_shadow = item.get('shadow') if item['ref_plan'] and plan['id'] == item['ref_plan']['id'] else None
        if plan['status'] == 'ok':
            shadow_by_plan[str(plan['id'])] = service.shadow_outcomes(plan, data['bars'], cache.spy,
                                                                      splits=data['splits'])['outcome']
        else:
            shadow_by_plan[str(plan['id'])] = known_shadow

    track_row = (service.unit_track_row(conn, ticker, sub['signal_date'], session=session, now=now)
                 if sub['kind'] == 'unit' else None)
    legacy = None
    if decision is not None:
        legacy_plan = plan_by_id.get(str(decision['plan_id'])) if decision.get('plan_id') else None
        legacy = {k: decision.get(k) for k in LEGACY_DECISION_KEYS}
        legacy['text'] = retro.decision_text(decision, legacy_plan,        # frozen words, as retro-1's decision.text
                                             agent_version_no=legacy_plan.get('version_no') if legacy_plan else None)
    version_no_by_plan = {v['plan_id']: v['version_no'] for v in versions}
    for plan in plans:
        if plan.get('version_no') is not None:
            version_no_by_plan.setdefault(plan['id'], plan['version_no'])
    execution_fact = retro.execution_fact(buys=buys, plans_by_trade=plans_by_trade,
                                          version_no_by_plan=version_no_by_plan, notes=subject['notes'],
                                          legacy_decision=legacy)
    segments = item.get('segments') or []
    sell_ids = {s['sell_trade_id'] for s in segments}
    trades = [*buys, *[t for t in data['trades'] if t['id'] in sell_ids and t.get('voided_at') is None]]
    actual = (track_row or {}).get('actual') or {}
    actual_r = actual.get('r') if actual.get('status') in ('closed', 'open') else None
    pool = (track_row or {}).get('pool')
    first_buy = min((_ny_day(b['executed_at']) for b in buys), default=None)
    holding_rows = ([r for r in repository.holding_status_for(conn, ticker=ticker)
                     if first_buy <= r['session'] <= event['end_session']]
                    if first_buy is not None and repository.executable_ready(conn) else [])
    holding = retro.holding_fact(holding_rows, sell_date=event['end_session'] if event['end_kind'] == 'position_closed'
                                 else None)
    return retro.build_facts_v2(
        execution=execution_fact, holding=holding, subject={**sub, 'pool': pool}, end=event, plans=plans,
        roles=roles, bars=data['bars'], splits=data['splits'], shadow_by_plan=shadow_by_plan, track_row=track_row,
        trades=trades, segments=segments, actual_r=actual_r,
        discipline=discipline_of(conn, cache, ticker, {b['id'] for b in buys}, session=session),
        reviews=_reviews_fact(subject['reviews'], bool(versions)),
        knowledge=_knowledge_fact(conn, [p['id'] for p in plans]) if sub['kind'] == 'unit' else [])


def _scan(conn, *, session: date, now: datetime, window_start: date, with_facts: bool,
          include_known: bool = False) -> dict[str, Any]:
    """Every subject that could end in ``[window_start, session]``, judged by ``retro.end_event``.

    ``items``: events without a card yet (and, with ``include_known``, those that have one), each carrying its
    frozen ``facts`` when ``with_facts``; ``stats``: counts and per-subject failures. One bad subject only lands
    in ``failures`` (§9)."""
    stats: dict[str, Any] = {'events': 0, 'by_end_kind': {kind: 0 for kind in retro.END_KINDS}, 'no_plan': 0,
                             'no_plan_units': [], 'failures': [], 'existing': 0}
    items: list[dict[str, Any]] = []
    cache = _Cache(conn, session, now)

    def known(key: str) -> bool:
        return repository.review_card_by_key(conn, key) is not None

    for subject in _subjects_v2(conn, cache, since=window_start, until=session):
        target = subject['ticker']
        try:
            with conn.transaction():                   # a savepoint: a failed statement must not poison the scan
                result = _evaluate(subject, cache, window_start=window_start, known=known, full=include_known)
                if result['state'] == 'no_plan':
                    stats['no_plan'] += 1
                    stats['no_plan_units'].append(result)
                    continue
                if result['state'] != 'event':
                    continue
                if result['known']:
                    stats['existing'] += 1
                    if not include_known:
                        continue
                else:
                    stats['events'] += 1
                    stats['by_end_kind'][result['event']['end_kind']] += 1
                item = {**result, 'subject': subject}
                if with_facts and not result['known']:
                    item['facts'] = _freeze(conn, item, cache, session=session, now=now)
                items.append(item)
        except service.Unavailable:
            raise
        except Exception as exc:                        # noqa: BLE001 - one subject never stops the scan (§9)
            stats['failures'].append((target, families.sanitize_reason(exc) or type(exc).__name__))
    return {'items': items, 'stats': stats}


def _fresh_tracks(row: dict[str, Any], *, modern: bool = False) -> dict[str, Any]:
    """The ``facts.tracks`` shape of a track row, mapped by the same ``retro`` helper the freeze uses; ``modern`` (a
    retro-2 card, `tracks-2` rows) words the statuses from the tracks-2 table."""
    parts = {'rule': retro._track(row['rule']), 'agent': retro._track(row['agent'], 'version_no', 'version_basis'),
             'actual': retro._track(row['actual'], 'plan_kind')}
    if modern:
        for part in parts.values():
            part['status_text'] = retro.TRACK_STATUS_TEXT_V2.get(part['status'], part['status'])
    return retro._jsonable(parts)


def _card_row(item: dict[str, Any], *, run_id: str | None) -> dict[str, Any]:
    event, sub = item['event'], item['sub']
    return {'event_key': event['event_key'], 'subject': sub['kind'], 'ticker': sub['ticker'],
            'signal_date': sub['signal_date'], 'buy_trade_id': sub.get('buy_trade_id'),
            'end_kind': event['end_kind'], 'end_session': event['end_session'],
            'retro_version': retro.RETRO_VERSION, 'facts': item['facts'], 'detected_run_id': run_id}


def detect_all(session: date, *, now: datetime, window_start: date | None = None,
               run_id: str | None = None) -> dict[str, Any]:
    """Scan (read-only transaction), then insert the new cards in one write transaction. Returns the scan
    ``stats`` plus ``new_ids`` (cards inserted by this call) and ``ticker_of`` (card id -> ticker)."""
    start = window_start if window_start is not None else retro.window_start(session)
    with core_tx.read_only_tx() as conn:
        require_ready(conn)
        scanned = _scan(conn, session=session, now=now, window_start=start, with_facts=True)
    stats, new_ids, ticker_of = scanned['stats'], [], {}
    if scanned['items']:
        with core_tx._tx() as conn:
            for item in scanned['items']:
                card_id = repository.insert_review_card(conn, _card_row(item, run_id=run_id))
                if card_id is not None:
                    new_ids.append(card_id)
                    ticker_of[card_id] = item['sub']['ticker']
    return {**stats, 'new_ids': new_ids, 'ticker_of': ticker_of}


def detect(session: date, *, now: datetime, window_start: date | None = None, run_id: str | None = None) -> list[int]:
    """Detect the end events of ``[window_start, session]`` (default: the last 5 sessions counting ``session``)
    and create the missing cards; returns the ids of the cards created by this call."""
    return detect_all(session, now=now, window_start=window_start, run_id=run_id)['new_ids']


# ---------------------------------------------------------------- statuses and drafting

def card_state(conn, card_id: int) -> dict[str, Any]:
    drafts = repository.card_drafts(conn, card_id)
    verdict = repository.card_verdict(conn, card_id)
    return {'drafts': drafts, 'verdict': verdict, 'status': retro.card_status(drafts=drafts, verdict=verdict),
            'ok': next((d for d in drafts if d['status'] == 'ok'), None),
            'failed': sum(d['status'] in ('rejected', 'error') for d in drafts)}


def _needing_draft(conn) -> list[dict[str, Any]]:
    """Spec §4.4: cards with no ok draft, no verdict and fewer than ``RETRO_MAX_ATTEMPTS`` failed attempts (a card at
    the cap is ``draft_failed`` and waits for a manual redraft or a verdict), oldest end first:
    ``{card, drafts, failed}``."""
    out = []
    for card in sorted(repository.review_cards(conn), key=lambda c: (c['end_session'], c['id'])):
        state = card_state(conn, card['id'])
        if state['verdict'] is None and state['ok'] is None and state['failed'] < retro.RETRO_MAX_ATTEMPTS:
            out.append({'card': card, **state})
    return out


def _unit_of(conn, card: dict[str, Any]) -> dict[str, Any]:
    """The agent_runs identity of a card's unit; a card without a signal date borrows the buy's New York date."""
    signal_date = card['signal_date']
    if signal_date is None:
        buy = next((t for t in repository.list_trades(conn, card['ticker']) if t['id'] == card['buy_trade_id']), None)
        signal_date = _ny_day(buy['executed_at']) if buy else card['end_session']
    first = None
    if card['subject'] == 'unit':
        runs = repository.agent_runs_for(conn, card['ticker'], signal_date)
        first = runs[0] if runs else None
    return {'ticker': card['ticker'], 'signal_date': signal_date, 'pool': first['pool'] if first else 'main'}


def _record_skip(card_id: int, reason: str, trigger: str) -> None:
    """Remember why a card was not drafted: one ``skipped`` draft row, but only when the card's latest row does not
    already say the same (the batch runs daily; the log records changes, not days)."""
    with core_tx._tx() as conn:
        repository.lock(conn, f'{CARD_LOCK}:{card_id}')            # the lock a verdict and every draft write take
        drafts = repository.card_drafts(conn, card_id)
        if drafts and drafts[-1]['status'] == 'skipped' and drafts[-1]['reason'] == reason:
            return
        repository.insert_card_draft(conn, {'card_id': card_id, 'attempt': drafts[-1]['attempt'] + 1 if drafts else 1,
                                            'trigger': trigger, 'agent_run_id': None, 'status': 'skipped',
                                            'reason': reason, 'output': None})


def draft_card(card_id: int, *, now: datetime, deps: service.Deps | None = None, trigger: str = 'manual',
               attempt_cap: bool = False, session: date | None = None) -> dict[str, Any]:
    """Draft one card once (spec §4.4): read the card, run ``agent-retro-2`` (``agent-retro-1`` for a retro-1 card) on its frozen facts, and write the run,
    its tool calls, the ``review_card_drafts`` row and (on ``ok``) one ``knowledge_drafts`` row per lesson in one
    transaction. ``attempt_cap`` (the batch) refuses a card that already failed ``RETRO_MAX_ATTEMPTS`` times;
    a manual redraft is not capped. Returns ``{status, reason, draft_id, agent_run_id, input_tokens,
    output_tokens}``; ``status`` is ``ok`` / ``rejected`` / ``error`` / ``skipped`` / ``conflict``
    (``draft_exists`` / ``card_reviewed``: nothing written). Model exceptions are classified into an ``error`` draft;
    a failure while writing propagates to the caller."""
    deps = deps or service.Deps()
    settings = deps.agent_settings if deps.agent_settings is not None else agent_model.settings_from_env()
    with core_tx.read_only_tx() as conn:
        require_ready(conn)
        card = repository.review_card(conn, card_id)
        if card is None:
            raise LookupError(f'复盘卡 {card_id} 不存在')
        state = card_state(conn, card_id)
        unit = _unit_of(conn, card)
    if state['verdict'] is not None:
        return {'status': 'conflict', 'reason': 'card_reviewed', 'draft_id': None, 'agent_run_id': None}
    if state['ok'] is not None:
        return {'status': 'conflict', 'reason': 'draft_exists', 'draft_id': None, 'agent_run_id': None}
    if attempt_cap and state['failed'] >= retro.RETRO_MAX_ATTEMPTS:
        _record_skip(card_id, 'max_attempts', trigger)
        return {'status': 'skipped', 'reason': 'max_attempts', 'draft_id': None, 'agent_run_id': None}
    unavailable = agent_model.unavailable_reason(settings)
    if unavailable is not None:
        reason = f'agent_unavailable: {unavailable}'
        _record_skip(card_id, reason, trigger)
        return {'status': 'skipped', 'reason': reason, 'draft_id': None, 'agent_run_id': None}

    attempt = (state['drafts'][-1]['attempt'] + 1) if state['drafts'] else 1
    run_id = str(uuid.uuid4())
    day = session if session is not None else bars_module.latest_closed_session(now)
    result: dict[str, Any] = {'status': 'error', 'reason': 'model_error', 'detail': None, 'output': None,
                              'calls': [], 'input_tokens': 0, 'output_tokens': 0}
    try:
        ctx = agent_tools.ToolContext(ticker=card['ticker'], session=day, cutoff_at=now, read=_read_ro)
        with ExitStack() as stack:
            try:
                factory = deps.agent_checkpointer or (
                    lambda: agent_model.checkpointer(os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL', '')))
                checkpointer = stack.enter_context(factory())
            except Exception as exc:               # noqa: BLE001
                _, detail = agent_model.classify_error(exc, settings.api_key)
                result.update(reason='checkpoint_unavailable', detail=detail)
            else:
                model_obj = deps.agent_model if deps.agent_model is not None else agent_model.chat_model(settings)
                try:
                    modern = card['retro_version'] == retro.RETRO_V2
                    out = agent_graph.run_retro(card={**card, 'end_session': card['end_session']}, ctx=ctx,
                                                model=model_obj, checkpointer=checkpointer, run_id=run_id,
                                                now=lambda: now, concept_ids=_concept_ids(),
                                                facts_text=(retro.facts_text_v2 if modern else retro.facts_text)(card['facts']),
                                                agent_version=agent_graph.RETRO_AGENT_V2 if modern
                                                else agent_graph.RETRO_AGENT_V1)
                    result.update(out, detail=None)
                except service.Unavailable:
                    raise
                except Exception as exc:           # noqa: BLE001 - classified like every agent run
                    reason, detail = agent_model.classify_error(exc, settings.api_key)
                    if service._checkpoint_missing(exc):
                        reason = 'checkpoint_unavailable'
                    result.update(reason=reason, detail=detail)
    except service.Unavailable:
        raise
    except Exception as exc:                       # noqa: BLE001 - the unexpected: this card only
        reason, detail = agent_model.classify_error(exc, settings.api_key)
        result.update(status='error', reason=reason, detail=detail)

    status = result['status']
    run_row = service._agent_run_row(
        unit, settings=settings, attempt=attempt, now=now, status=status, run_id=run_id,
        reason=None if status == 'ok' else result['reason'], detail=result.get('detail'),
        output=result['output'] if status == 'ok' else None, input_tokens=result['input_tokens'],
        output_tokens=result['output_tokens'], mode='retro',
        agent_version=agent_graph.RETRO_AGENT_V2 if card['retro_version'] == retro.RETRO_V2
        else agent_graph.RETRO_AGENT_V1)
    try:
        with core_tx._tx() as conn:
            repository.lock(conn, f'{CARD_LOCK}:{card_id}')
            # the card may have moved while the model ran: recompute the attempt and look again (a verdict, or
            # another draft that got there first, must not leave lesson drafts nobody can decide)
            fresh = card_state(conn, card_id)
            attempt = fresh['drafts'][-1]['attempt'] + 1 if fresh['drafts'] else 1
            run_row['attempt'] = attempt
            reason = None if status == 'ok' else result['reason']
            stored = status
            if fresh['verdict'] is not None:
                stored, reason = 'rejected', 'card_reviewed'
            elif status == 'ok' and fresh['ok'] is not None:
                stored, reason = 'rejected', 'draft_exists'
            repository.insert_agent_run(conn, run_row, result['calls'])
            draft_id = repository.insert_card_draft(conn, {
                'card_id': card_id, 'attempt': attempt, 'trigger': trigger, 'agent_run_id': run_id, 'status': stored,
                'reason': reason, 'output': result['output'] if stored == 'ok' else None})
            if stored == 'ok':
                for index, lesson in enumerate(result['output']['lessons']):
                    repository.insert_knowledge_draft(conn, {
                        'origin': 'review_card', 'card_draft_id': draft_id, 'lesson_index': index,
                        **{key: lesson[key] for key in LESSON_KEYS}, 'refs': lesson.get('refs') or []})
    except psycopg.errors.UniqueViolation:
        return {'status': 'conflict', 'reason': 'draft_exists', 'draft_id': None, 'agent_run_id': None}
    return {'status': stored, 'reason': reason, 'draft_id': draft_id, 'agent_run_id': run_id,
            'input_tokens': result['input_tokens'], 'output_tokens': result['output_tokens']}


def redraft_check(card_id: int, *, now: datetime | None = None) -> None:
    """The synchronous part of ``POST …/redraft`` (spec §12): 503 without migration 027, 404 for an unknown card,
    409 ``card_reviewed`` / ``draft_exists`` / ``agent_unavailable: <reason>`` / ``token_budget``. Nothing is
    written. A manual redraft is not capped at 3 attempts, but it respects the token limit."""
    settings = agent_model.settings_from_env()
    with core_tx.read_only_tx() as conn:
        require_ready(conn)
        if repository.review_card(conn, card_id) is None:
            raise LookupError(f'复盘卡 {card_id} 不存在')
        state = card_state(conn, card_id)
        if state['verdict'] is not None:
            raise ledger.Conflict('card_reviewed')
        if state['ok'] is not None:
            raise ledger.Conflict('draft_exists')
        unavailable = agent_model.unavailable_reason(settings)
        if unavailable is not None:
            raise ledger.Conflict(f'agent_unavailable: {unavailable}')
        if settings.daily_tokens is not None and repository.agent_tokens_on(
                conn, _ny_day(now or datetime.now(UTC))) >= settings.daily_tokens:
            raise ledger.Conflict('token_budget')


def redraft(card_id: int, *, now: datetime, deps: service.Deps | None = None, run_id: str | None = None) -> dict:
    """The background body of ``POST …/redraft``: one manual draft, no attempt cap. Returns the draft result
    (the runner keeps it for ``GET /api/runs/<id>``)."""
    return draft_card(card_id, now=now, deps=deps, trigger='manual', attempt_cap=False)


# ---------------------------------------------------------------- the batch stage

def run_stage(session: date, *, now: datetime, deps: service.Deps, summary: dict[str, Any], started: float,
              clock: Callable[[], float], run_id: str | None, acc: stages_module.StageAcc | None) -> str | None:
    """The batch's ``retro`` stage (spec §9): detect the end events of the last 5 sessions, create the missing
    cards, then draft the cards that have no ok draft, oldest end first, at most ``RETRO_MAX_DRAFTS`` per run and
    behind the same gates as the agent stage. ``summary['retro'] = {new_cards, drafts, to_review}``; with ``acc``
    the stage's counts, skips, failures, degradations, outputs and ``detail``. One card's surprise only fails
    that card. Returns None (the caller finishes the stage)."""
    t_stage = clock()
    settings = deps.agent_settings if deps.agent_settings is not None else agent_model.settings_from_env()
    retro_summary = summary['retro'] = {'new_cards': 0, 'drafts': 0, 'to_review': 0}
    detected = detect_all(session, now=now, run_id=run_id)
    for target, reason in detected['failures']:
        summary['errors'].append(f'{target} retro: {families.sanitize_reason(reason)}')
        if acc is not None:
            acc.fail(target, reason)
    new_ids = detected['new_ids']
    retro_summary['new_cards'] = len(new_ids)
    ny_date = now.astimezone(NY).date()
    drafts = {'ok': 0, 'rejected': 0, 'error': 0, 'skipped': 0}
    tokens = {'input': 0, 'output': 0}
    unavailable = agent_model.unavailable_reason(settings)
    # the quota may have run out in the agent stage or in the review stage that ran just before this one
    quota_exhausted = any(bool((summary.get(stage) or {}).get('quota_exhausted')) for stage in ('agent', 'review'))
    started_drafts = 0

    with core_tx.read_only_tx() as conn:
        needing = _needing_draft(conn)
    if acc is not None:
        acc.input(len(needing))
        acc.outputs.update({'card_ids': list(new_ids), 'draft_ids': [], 'agent_run_ids': []})
        if unavailable is not None and needing:
            acc.degrade('agent_unavailable', unavailable)

    def skip(entry: dict[str, Any], reason: str, *, record: bool = True) -> None:
        drafts['skipped'] += 1
        if acc is not None:
            acc.skip(reason, entry['card']['ticker'])
        if record:
            _record_skip(entry['card']['id'], reason, 'batch')

    for entry in needing:
        card = entry['card']
        try:
            if started_drafts >= retro.RETRO_MAX_DRAFTS:
                skip(entry, 'draft_cap', record=False)
            elif clock() - started >= service.AGENT_DEADLINE_S:
                skip(entry, 'stage_budget')
            elif unavailable is not None:
                skip(entry, f'agent_unavailable: {unavailable}')
            elif quota_exhausted:
                skip(entry, 'quota_exhausted')
            elif settings.daily_tokens is not None and _tokens_used(ny_date) >= settings.daily_tokens:
                skip(entry, 'token_budget')
            else:
                started_drafts += 1
                out = draft_card(card['id'], now=now, deps=deps, trigger='batch', attempt_cap=True, session=session)
                status = out['status']
                if status == 'conflict':
                    continue
                drafts[status] += 1
                tokens['input'] += out.get('input_tokens') or 0
                tokens['output'] += out.get('output_tokens') or 0
                if out.get('agent_run_id') and acc is not None:
                    acc.ref('agent_run_ids', out['agent_run_id'])
                if status == 'ok':
                    retro_summary['drafts'] += 1
                    if acc is not None:
                        acc.output()
                        acc.ref('draft_ids', out['draft_id'])
                elif status == 'rejected':
                    if acc is not None:
                        acc.degrade('draft_rejected', f"{card['ticker']}: {out['reason']}")
                elif status == 'error':
                    if out['reason'] == 'quota_exhausted':
                        quota_exhausted = True
                    if acc is not None:
                        acc.fail(card['ticker'], out['reason'])
                if status in ('rejected', 'error') and entry['failed'] + 1 >= retro.RETRO_MAX_ATTEMPTS \
                        and acc is not None:
                    acc.skip('max_attempts', card['ticker'])      # reported once: the run whose attempt reached the cap
        except service.Unavailable:
            raise
        except Exception as exc:                   # noqa: BLE001 - one card's surprise only fails that card
            reason = families.sanitize_reason(exc) or type(exc).__name__
            summary['errors'].append(f"{card['ticker']} retro: {reason}")
            if acc is not None:
                acc.fail(card['ticker'], reason)

    with core_tx.read_only_tx() as conn:
        to_review = sum(1 for card in repository.review_cards(conn) if card_state(conn, card['id'])['status'] == 'to_review')
    retro_summary['to_review'] = to_review
    if acc is not None:
        acc.detail.update({'events': detected['events'], 'new_cards': len(new_ids),
                           'by_end_kind': dict(detected['by_end_kind']), 'no_plan': detected['no_plan'],
                           'drafts': drafts, 'to_review': to_review, 'tokens': tokens})
        acc.duration_ms += (clock() - t_stage) * 1000
    return None


def _tokens_used(ny_date: date) -> int:
    with core_tx.read_only_tx() as conn:
        return repository.agent_tokens_on(conn, ny_date)


# ---------------------------------------------------------------- review (verdict)

def _validate_verdict(payload: Any) -> dict[str, Any]:
    """The normalised request or ValueError (400). Lesson fields are validated in ``verdict`` where the card's
    drafts are known (a drafted lesson needs counter-examples, a hand-written one may leave them empty)."""
    if not isinstance(payload, dict):
        raise ValueError('body must be a JSON object')
    try:
        request_id = uuid.UUID(str(payload.get('request_id')))
    except ValueError:
        raise ValueError('request_id must be a UUID') from None
    note = payload.get('note') or ''
    if not isinstance(note, str) or len(note.strip()) > NOTE_MAX:
        raise ValueError(f'note 最多 {NOTE_MAX} 字')
    lessons = payload.get('lessons')
    if lessons is None:
        lessons = []
    if not isinstance(lessons, list):
        raise ValueError('lessons 必须是列表')
    out = []
    for item in lessons:
        if not isinstance(item, dict):
            raise ValueError('每条教训必须是对象')
        action = item.get('action')
        if action not in ('approve', 'dismiss'):
            raise ValueError("action 必须是 approve 或 dismiss")
        draft_id = item.get('draft_id')
        if draft_id is not None:
            if isinstance(draft_id, bool) or not isinstance(draft_id, int) or draft_id < 1:
                raise ValueError('draft_id 必须是草稿 id')
        elif action == 'dismiss':
            raise ValueError('另写的教训不能不采纳')
        out.append({**{key: item.get(key) for key in LESSON_KEYS}, 'action': action, 'draft_id': draft_id})
    return {'request_id': request_id, 'note': note.strip(), 'lessons': out}


def _lesson_rows(conn, ok_draft: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    if ok_draft is None:
        return {}
    return {d['id']: d for d in repository.knowledge_drafts(conn, open_only=False, card_draft_id=ok_draft['id'])}


def verdict(card_id: int, payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """Review a card once (spec §4.5, D12). One transaction: the ``review_card_verdicts`` row, a
    ``knowledge_draft_verdicts`` row for every lesson draft of the card's ok draft (each must appear exactly once,
    else ValueError / 400), and for every approved lesson (a draft, possibly edited, or one written here) a new
    ``knowledge_items`` row (origin ``review_card``) with version 1. A card can be reviewed once: a second
    request with the same ``request_id`` and content replays the first (``status: replayed``), anything else is
    ``Conflict('card_reviewed')`` (409); a reused ``request_id`` with other content is ``request_id_conflict``.
    A card without an ok draft may be reviewed with a note and hand-written lessons only. 503 without migration
    027 or, when a lesson attaches a concept, without the concept cards."""
    rec = _validate_verdict(payload)
    concept_versions = my_knowledge.concept_versions()
    request_id = rec['request_id']
    with core_tx._tx() as conn:
        require_ready(conn)
        repository.lock(conn, f'{CARD_LOCK}:{card_id}')
        card = repository.review_card(conn, card_id)
        if card is None:
            raise LookupError(f'复盘卡 {card_id} 不存在')
        state = card_state(conn, card_id)
        existing = state['verdict']
        if existing is not None and existing['request_id'] != request_id:
            raise ledger.Conflict('card_reviewed')
        drafted = _lesson_rows(conn, state['ok'])
        seen = [item['draft_id'] for item in rec['lessons'] if item['draft_id'] is not None]
        if sorted(seen) != sorted(drafted) or len(set(seen)) != len(seen):
            raise ValueError('教训草稿必须全部出现且只出现一次')
        lessons = []
        for item in rec['lessons']:
            draft = drafted.get(item['draft_id'])
            if item['action'] == 'dismiss':
                lessons.append({'draft': draft, 'action': 'dismiss', 'fields': None})
                continue
            try:
                fields = knowledge_items.validate({key: item.get(key) for key in LESSON_KEYS},
                                                  concept_versions=concept_versions,
                                                  require_counter=draft is not None)
            except LookupError:
                raise service.Unavailable('概念卡不可用：请检查 docs/knowledge 目录') from None
            lessons.append({'draft': draft, 'action': 'approve', 'fields': fields})
        digest = ledger.request_hash({'card_id': card_id, 'note': rec['note'], 'lessons': [
            {'draft_id': l['draft']['id'] if l['draft'] else None, 'action': l['action'], 'fields': l['fields']}
            for l in lessons]})
        if existing is not None:
            if existing['request_hash'] == digest:
                return _verdict_result(conn, card_id, 'replayed')
            raise ledger.Conflict('request_id_conflict')
        try:
            with conn.transaction():
                repository.insert_card_verdict(conn, {
                    'card_id': card_id, 'request_id': request_id, 'request_hash': digest,
                    'draft_id': state['ok']['id'] if state['ok'] else None, 'note': rec['note']})
                for index, lesson in enumerate(lessons):
                    draft = lesson['draft']
                    if lesson['action'] == 'dismiss':
                        repository.insert_draft_verdict(conn, {'draft_id': draft['id'], 'action': 'dismiss',
                                                               'request_id': request_id})
                        continue
                    knowledge_id = repository.insert_knowledge_item(conn, {'origin': 'review_card',
                                                                           'source_card_id': card_id})
                    version_request = uuid.uuid5(request_id, f'lesson:{index}')
                    version_id = repository.insert_knowledge_version(conn, {
                        'knowledge_id': knowledge_id, 'version': 1, 'supersedes_id': None,
                        'request_id': version_request, 'request_hash': digest,
                        'draft_id': draft['id'] if draft else None, **lesson['fields'],
                        'schema_version': knowledge_items.MY_KNOWLEDGE_VERSION})
                    if draft is not None:
                        repository.insert_draft_verdict(conn, {'draft_id': draft['id'], 'action': 'approve',
                                                               'knowledge_version_id': version_id,
                                                               'request_id': request_id})
        except psycopg.errors.UniqueViolation:
            raise ledger.Conflict('request_id_conflict') from None
        return _verdict_result(conn, card_id, 'created')


def _verdict_result(conn, card_id: int, status: str) -> dict[str, Any]:
    view = _verdict_view(conn, card_id)
    record = repository.card_verdict(conn, card_id)
    return {'status': status,
            'record': {'id': record['id'], 'card_id': card_id, 'note': record['note'], 'draft_id': record['draft_id'],
                       'created_at': record['created_at']},
            'lessons': view['lessons']}


def _adopted(conn, card_id: int) -> list[dict[str, Any]]:
    """Knowledge written from this card's review: ``[{knowledge_id, version row (v1), draft_id}]``."""
    versions, _ = repository.knowledge_rows(conn, cutoff=None)
    first = {v['knowledge_id']: v for v in versions if v['version'] == 1}
    out = []
    for knowledge_id in sorted(first):
        item = repository.knowledge_item(conn, knowledge_id)
        if item is not None and item['source_card_id'] == card_id:
            out.append({'knowledge_id': knowledge_id, 'version': first[knowledge_id], 'draft_id': first[knowledge_id]['draft_id']})
    return out


def _verdict_view(conn, card_id: int) -> dict[str, Any] | None:
    record = repository.card_verdict(conn, card_id)
    if record is None:
        return None
    state = card_state(conn, card_id)
    drafted = _lesson_rows(conn, state['ok'])
    adopted = {a['draft_id']: a for a in _adopted(conn, card_id) if a['draft_id'] is not None}
    lessons = []
    for draft_id, draft in sorted(drafted.items(), key=lambda kv: kv[1]['lesson_index']):
        mine = adopted.get(draft_id)
        lessons.append({'draft_id': draft_id, 'action': 'approve' if mine else 'dismiss',
                        'title': mine['version']['title'] if mine else draft['title'],
                        'ref': knowledge_items.ref(mine['knowledge_id'], 1) if mine else None})
    for extra in _adopted(conn, card_id):
        if extra['draft_id'] is None:
            lessons.append({'draft_id': None, 'action': 'approve', 'title': extra['version']['title'],
                            'ref': knowledge_items.ref(extra['knowledge_id'], 1)})
    return {'note': record['note'], 'created_at': record['created_at'], 'lessons': lessons}


# ---------------------------------------------------------------- views

def _facts_flags(facts: dict[str, Any]) -> tuple[list[str], list[str]]:
    trades = (facts.get('discipline') or {}).get('trades') or []
    flags = [f for f in retro.FLAG_ORDER if any(f in t['flags'] for t in trades)]
    immature = list(dict.fromkeys(f for t in trades for f in t['immature']))
    return flags, immature


def _filled(facts: dict[str, Any]) -> bool:
    """Whether the subject was bought at all (a live buy in the frozen trades): tells 「未成交」 from a filled trade
    that has no R (an unplanned trade has no stop to price the risk)."""
    return any(t.get('side') == 'buy' for t in (facts.get('trades') or {}).get('trades') or [])


def facts_keys(card: dict[str, Any]) -> tuple[str, ...]:
    """The fact keys of the card's own version, in order (JSONB does not keep key order)."""
    return retro.FACT_KEYS_V2 if card['retro_version'] == retro.RETRO_V2 else retro.FACT_KEYS


def _row(card: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    facts = card['facts'] or {}
    tracks = facts.get('tracks') or {}
    flags, immature = _facts_flags(facts)
    ok = state['ok']
    return {'id': card['id'], 'ticker': card['ticker'], 'subject': card['subject'],
            'signal_date': card['signal_date'], 'end_kind': card['end_kind'],
            'exit_basis': (facts.get('subject') or {}).get('exit_basis'), 'end_session': card['end_session'],
            'end_text': (facts.get('subject') or {}).get('end_text') or retro.end_text(card['end_kind'], None),
            'decision_text': (facts.get('decision') or {}).get('text'),
            'execution_text': (facts.get('execution') or {}).get('text') or (facts.get('decision') or {}).get('text'),
            'actual_r': (facts.get('trades') or {}).get('actual_r'), 'filled': _filled(facts),
            'rule_r': (tracks.get('rule') or {}).get('r'), 'agent_r': (tracks.get('agent') or {}).get('r'),
            'flags': flags, 'immature': immature, 'status': state['status'],
            'headline': (ok['output'] or {}).get('headline') if ok else None, 'discipline': (facts.get('discipline') or {}).get('totals')}


def cards_with_state(conn, *, ticker: str | None = None, since: date | None = None) -> list[tuple[dict, dict]]:
    return [(card, card_state(conn, card['id'])) for card in repository.review_cards(conn, ticker=ticker, since=since)]


def learning_cards(conn, week_start: date, week_end: date) -> list[dict[str, Any]]:
    """The cards that ended in the week, as ``review.learning_stats`` reads them (the list row plus ``card_id``
    and the frozen discipline totals)."""
    return [{**_row(card, state), 'card_id': card['id']}
            for card, state in cards_with_state(conn, since=week_start) if card['end_session'] <= week_end]


def list_view(*, status: str | None, ticker: str | None, since: date | None) -> dict[str, Any]:
    """``GET /review-cards`` (spec §4.7): rows newest end first, ``counts`` by status over the ticker / date
    filter (the status filter only narrows ``cards``). ValueError for an unknown status."""
    if status not in (None, '', *retro.CARD_STATUSES):
        raise ValueError(f"status 必须是 {', '.join(retro.CARD_STATUSES)} 之一")
    with core_tx.read_only_tx() as conn:
        require_ready(conn)
        pairs = cards_with_state(conn, ticker=ticker, since=since)
    counts = {name: sum(1 for _, state in pairs if state['status'] == name) for name in retro.CARD_STATUSES}
    rows = [_row(card, state) for card, state in pairs if not status or state['status'] == status]
    return {'cards': rows, 'counts': counts}


def _draft_view(draft: dict[str, Any]) -> dict[str, Any]:
    return {'id': draft['id'], 'attempt': draft['attempt'], 'trigger': draft['trigger'],
            'agent_run_id': str(draft['agent_run_id']) if draft['agent_run_id'] else None,
            'status': draft['status'], 'reason': draft['reason'], 'output': draft['output'],
            'created_at': draft['created_at']}


def _lesson_view(draft: dict[str, Any]) -> dict[str, Any]:
    return {'id': draft['id'], 'lesson_index': draft['lesson_index'], 'title': draft['title'], 'body': draft['body'],
            'scope': draft['scope'], 'counter_examples': draft['counter_examples'],
            'concepts': list(draft['concepts']), 'tickers': list(draft['tickers']), 'refs': draft['refs']}


def discussions_of(conn, **context) -> list[dict[str, Any]]:
    from investment_assistant.workbench import discussion

    return discussion.list_in(conn, **context)


def card_view(card_id: int, *, now: datetime) -> dict[str, Any]:
    """``GET /review-cards/{id}`` (spec §12): the card, its frozen facts, status, every draft attempt, the lesson
    drafts of the ok draft, the verdict (with the knowledge it created), ``discipline_now`` (the same buys judged
    as of today, never written) and the card's discussions. LookupError for an unknown card."""
    session = bars_module.latest_closed_session(now)
    with core_tx.read_only_tx() as conn:
        require_ready(conn)
        card = repository.review_card(conn, card_id)
        if card is None:
            raise LookupError(f'复盘卡 {card_id} 不存在')
        state = card_state(conn, card_id)
        facts = {name: card['facts'][name] for name in facts_keys(card) if name in (card['facts'] or {})}
        frozen_buys = {x['id'] for x in (facts.get('trades') or {}).get('trades', []) if x['side'] == 'buy'}
        buy_ids = {t['id'] for t in repository.list_trades(conn, card['ticker']) if str(t['id']) in frozen_buys}
        now_discipline = discipline_of(conn, _Cache(conn, session, now), card['ticker'], buy_ids, session=session)
        return {
            'card': {'id': card['id'], 'event_key': card['event_key'], 'subject': card['subject'],
                     'ticker': card['ticker'], 'signal_date': card['signal_date'], 'end_kind': card['end_kind'],
                     'end_session': card['end_session'], 'created_at': card['created_at'],
                     'filled': _filled(facts)},
            'facts': facts, 'status': state['status'],
            'drafts': [_draft_view(d) for d in state['drafts']],
            'lesson_drafts': [_lesson_view(d) for d in sorted(_lesson_rows(conn, state['ok']).values(),
                                                              key=lambda d: d['lesson_index'])],
            'verdict': _verdict_view(conn, card_id), 'discipline_now': now_discipline,
            'discussions': discussions_of(conn, card_id=card_id)}


# ---------------------------------------------------------------- check and command line

def check(since: date, until: date | None = None, out: Callable[[str], None] = print) -> int:
    """``check --since D [--until D]`` (spec §12): every end event in the window, judged again by §4.1, must have
    exactly one card (the same ``event_key``; an event without one is 「缺卡」), and every card that ended in the
    window must carry all its fact keys (ten for a retro-1 card, eleven for a retro-2 one). A closed unit whose chain never carried a ``plan_id`` has no reference plan,
    so no card can exist: it is listed on its own 「无参考计划」 line and fails the check. The card's frozen
    ``facts.tracks`` is compared with the ``tracks-1`` row recomputed as of the freeze (the card's session, bars
    known when it was created), so a shadow that kept moving is not a change; a difference is listed as 「事实已变化」
    without failing (a trade voided after the freeze, spec §15).
    Returns 0 when nothing is missing, 1 otherwise. Read-only; Unavailable (exit 2 in ``main``) without a
    database or migration 027; ValueError when ``since`` is after ``until``."""
    now = datetime.now(UTC)
    end = until if until is not None else bars_module.latest_closed_session(now)
    if since > end:
        raise ValueError('since 不能晚于 until')
    with core_tx.read_only_tx() as conn:
        require_ready(conn)
        scanned = _scan(conn, session=end, now=now, window_start=since, with_facts=False)
        present = [c for c in repository.review_cards(conn, since=since) if c['end_session'] <= end]
        out(f'复盘卡完整性 {since} ~ {end}')
        missing = changed = incomplete = 0
        for item in scanned['items']:
            missing += 1
            event = item['event']
            out(f"缺卡  {event['event_key']}  {item['sub']['ticker']}  {event['end_kind']}  {event['end_session']}")
        no_plan_units = scanned['stats']['no_plan_units']
        for unit in no_plan_units:
            out(f"无参考计划  unit:{unit['ticker']}:{unit['signal_date']}  {unit['ticker']}  信号日 {unit['signal_date']}  "
                f"平仓 {unit['end_session']}  决定链上没有任何计划")
        for card in sorted(present, key=lambda c: (c['end_session'], c['id'])):
            facts = card['facts'] or {}
            modern = card['retro_version'] == retro.RETRO_V2
            absent = [name for name in facts_keys(card) if name not in facts]
            if absent:
                incomplete += 1
                out(f"事实不全  {card['event_key']}  缺 {', '.join(absent)}")
                continue
            note = ''
            if card['subject'] == 'unit':
                # as of the freeze, not today: a shadow keeps moving after the close, a changed ledger does not
                as_of = (facts.get('discipline') or {}).get('as_of')
                frozen_session = date.fromisoformat(as_of) if as_of else card['end_session']
                row = service.unit_track_row(conn, card['ticker'], card['signal_date'], session=frozen_session,
                                             now=card['created_at'],
                                             version=tracks_module.TRACKS_V2 if modern else tracks_module.TRACKS_V1)
                if _fresh_tracks(row, modern=modern) != (facts.get('tracks') or {}):
                    changed += 1
                    note = f"  事实已变化：tracks 与现在的 {'tracks-2' if modern else 'tracks-1'} 行不同"
            out(f"齐全  {card['event_key']}  #{card['id']}{note}")
    for target, reason in scanned['stats']['failures']:
        out(f'读取失败  {target}  {reason}')
    passed = not missing and not incomplete and not no_plan_units and not scanned['stats']['failures']
    out(f"汇总：窗口 {since} ~ {end} 应有 {len(present) + missing} 张，缺卡 {missing}，事实不全 {incomplete}，"
        f"事实已变化 {changed}，无参考计划 {len(no_plan_units)} → {'通过' if passed else '不通过'}")
    return 0 if passed else 1


def _cli_now() -> datetime:
    return datetime.now(UTC)


def _date_arg(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'日期格式应为 YYYY-MM-DD：{text}') from None


def _beijing(moment: datetime | None) -> str:
    from investment_assistant.workbench.review_digest import SH

    return moment.astimezone(SH).strftime('%Y-%m-%d %H:%M') if moment is not None else '-'


def _show(card_id: int, out: Callable[[str], None]) -> int:
    now = _cli_now()
    try:
        view = card_view(card_id, now=now)
    except LookupError as exc:
        out(str(exc))
        return 1
    card, facts = view['card'], view['facts']
    subject = facts.get('subject') or {}
    out(f"复盘卡 #{card['id']}  {card['ticker']}  {subject.get('end_text')}  结束 {card['end_session']}  状态 {view['status']}")
    out(f"事件 {card['event_key']}  建于 {_beijing(card['created_at'])}")
    if 'execution' in facts:
        out(f"执行 {facts['execution'].get('text')}")
        holding = facts.get('holding')
        if holding:
            out(f"持仓 状态 {holding.get('rows')} 天，最多持有 {holding.get('held_sessions')} 个交易日")
    else:
        out(f"决定 {(facts.get('decision') or {}).get('text')}")
    tracks = facts.get('tracks') or {}
    for name in ('rule', 'agent', 'actual'):
        if name in tracks:
            track = tracks[name]
            out(f"轨道 {name}  {track['status_text']}  R {track['r']}")
    totals = (facts.get('discipline') or {}).get('totals')
    if totals:
        out('纪律 ' + '  '.join(f'{k} {v}' for k, v in totals.items()))
    for draft in view['drafts']:
        out(f"草稿 #{draft['id']} 第 {draft['attempt']} 次  {draft['trigger']}  {draft['status']}"
            f"{'  ' + draft['reason'] if draft['reason'] else ''}")
        if draft['output']:
            out(f"  {draft['output']['headline']}")
    for lesson in view['lesson_drafts']:
        out(f"教训 #{lesson['id']}  {lesson['title']}")
    verdict_ = view['verdict']
    if verdict_:
        out(f"审定 {_beijing(verdict_['created_at'])}  {verdict_['note']}")
        for lesson in verdict_['lessons']:
            out(f"  {lesson['action']}  {lesson['title']}  {lesson['ref'] or ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """``detect [--since D]`` creates the missing cards of the window (writes, no drafting); ``draft --card ID``
    drafts one card now (writes); ``show --card ID`` prints a card (read-only); ``check --since D [--until D]``
    verifies the window (read-only; exit 1 when a card is missing). Exit 2 for bad arguments or an unavailable
    database / migration 027."""
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.workbench.retro_cards',
                                     description='复盘卡：检测、起草、查看与完整性核对。')
    commands = parser.add_subparsers(dest='command', required=True)
    detector = commands.add_parser('detect', help='检测并建卡（写，不起草）')
    detector.add_argument('--since', type=_date_arg, help='窗口起点（默认最近 5 个交易日），YYYY-MM-DD')
    drafter = commands.add_parser('draft', help='同步起草一张卡（写）')
    drafter.add_argument('--card', type=int, required=True, help='卡 id')
    shower = commands.add_parser('show', help='打印一张卡（只读）')
    shower.add_argument('--card', type=int, required=True, help='卡 id')
    checker = commands.add_parser('check', help='核对窗口内每个结束事件恰有一张卡（只读）')
    checker.add_argument('--since', type=_date_arg, required=True, help='起，YYYY-MM-DD')
    checker.add_argument('--until', type=_date_arg, help='止，YYYY-MM-DD')
    args = parser.parse_args(argv)
    try:
        if args.command == 'check':
            if args.until is not None and args.since > args.until:
                print('since 不能晚于 until', file=sys.stderr)
                return 2
            return check(args.since, args.until, print)
        if args.command == 'show':
            return _show(args.card, print)
        now = _cli_now()
        if args.command == 'detect':
            session = bars_module.latest_closed_session(now)
            found = detect_all(session, now=now, window_start=args.since)
            for card_id in found['new_ids']:
                print(f"新卡 #{card_id}  {found['ticker_of'][card_id]}")
            for target, reason in found['failures']:
                print(f'读取失败  {target}  {reason}')
            print(f"汇总：新结束 {found['events']} 笔，新建 {len(found['new_ids'])} 张，没有计划 {found['no_plan']}")
            return 0
        result = draft_card(args.card, now=now, deps=service.Deps(), trigger='manual', attempt_cap=False)
        print(f"起草结果：{result['status']}{'  ' + result['reason'] if result.get('reason') else ''}")
        return 0 if result['status'] == 'ok' else 1
    except service.Unavailable as exc:
        print(f'数据库不可用：{exc}', file=sys.stderr)
        return 2
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
