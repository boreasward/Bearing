"""Candidate units, pools and active-chain merging."""
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

RETRIGGER_STATES = ('entry', 'holding', 'exiting')


def _empty_retriggers() -> dict[str, Any]:
    return {'count': 0, 'by_state': {state: 0 for state in RETRIGGER_STATES}, 'tickers': []}


def _note_retrigger(acc: Any, ticker: str, row: dict[str, Any]) -> None:
    """Fold one committed merge (a stored ``chain_retriggers`` row, never a locally computed value) into the plan
    stage's ``detail.retriggers = {count, by_state, tickers}`` (v2.9 spec §4.2)."""
    retriggers = acc.detail.setdefault('retriggers', _empty_retriggers())
    retriggers['count'] += 1
    retriggers['by_state'][row['chain_state']] += 1
    if ticker not in retriggers['tickers']:
        retriggers['tickers'].append(ticker)


def _open_or_merge(conn, ticker: str, session: date, *, now: datetime) -> dict[str, Any] | None:
    """The `plan` node's chain decision for one buy unit (v2.9 spec §4.2, D3), under the transaction-level lock
    ``chain-open:<ticker>``: the ticker's active chain on S (R5: an entry chain on its last valid day renews), or
    None when the unit opens a chain of its own. A unit already merged on an earlier run of the session stays
    merged (its stored row decides), and a unit never merges into its own or a later chain."""


    core_dbutil.lock(conn, f'chain-open:{ticker}')
    stored = execution_repo.retrigger_for_unit(conn, ticker, session)
    if stored is not None:
        return {'row': stored}
    found = execution_chains.active_chain_of(conn, ticker, session=session, now=now, before=session)
    return {'found': found} if found is not None else None


def _sync_pool(conn, pool_: pool_module.Pool, session: date, now: datetime) -> list[dict[str, Any]]:
    """Append tier changes against the newest recorded state, effective this session."""
    changes = pool_module.tier_changes(pool_, analysis_repo.pool_latest(conn))
    analysis_repo.insert_pool_tiers(conn, changes, session=session, now=now)
    return changes


def _unit_is_extended(conn, ticker: str, signal_date: date, *, held: bool, now: datetime) -> bool:
    """Extended-pool unit: the signal-date tier is extended, the ticker is not held, and a screen
    pick with a valid rule plan counts as a main-list unit (spec §7.2)."""
    if held:
        return False
    entry = data_repo.pool_entry_at(conn, ticker, signal_date, cutoff_at=now)
    if entry is None or entry['tier'] != 'extended':
        return False
    if entry.get('source') == 'screen':
        plan = execution_repo.rule_plan(conn, ticker, signal_date)
        if plan is not None and plan['status'] == 'ok':
            return False
    return True


def _breadth_tickers(manual: pool_module.Pool, universe_: Any) -> tuple[list[str], list[str]]:
    """Breadth sample and notes: the screening universe ∪ the manual pool, deduped.

    A missing universe falls back to the manual pool alone and flags it. """
    if universe_ is not None:
        merged = sorted({*manual.tickers, *universe_.tickers})
        return merged, []
    return sorted(manual.tickers), ['regime_universe_missing']


def _session_units(conn, session: date, *, now: datetime) -> dict[str, list[dict[str, Any]]]:
    """This session's undecided units in three groups (P4 §7.3): main-list
    conflict-free buys (propose), other main-list units (interpret), extended
    conflict-free buys (propose); each alphabetical, every item carrying its
    candidates for the fallback brief."""
    main_buy: list[dict[str, Any]] = []
    main_other: list[dict[str, Any]] = []
    extended: list[dict[str, Any]] = []
    for group in analysis_repo.candidate_groups_since(conn, session):
        if group['signal_date'] != session:
            continue
        ticker = group['ticker']
        if execution_repo_legacy.decision_head(conn, ticker, group['signal_date']) is not None:
            continue
        book = execution_plan_context._book(conn, ticker)
        unit = synthesis.synthesize(ticker, group['signal_date'], group['candidates'],
                                    holding=book['position'] is not None,
                                    now=now)
        if unit is None:
            continue
        conflict_free_buy = unit['proposed_action'] == 'buy' and 'conflict' not in unit['labels']
        is_extended = _unit_is_extended(conn, ticker, group['signal_date'],
                                        held=book['position'] is not None, now=now)
        entry = data_repo.pool_entry_at(conn, ticker, group['signal_date'], cutoff_at=now)
        item = {'ticker': ticker, 'signal_date': group['signal_date'],
                'pool': 'extended' if is_extended else 'main',
                'pool_tier': entry['tier'] if entry else 'core',
                'proposed_action': unit['proposed_action'], 'labels': unit['labels'],
                'candidates': group['candidates']}
        if is_extended:
            if conflict_free_buy:
                extended.append(item)
        elif conflict_free_buy:
            main_buy.append(item)
        else:
            main_other.append(item)
    for bucket in (main_buy, main_other, extended):
        bucket.sort(key=lambda u: u['ticker'])
    return {'main_buy': main_buy, 'main_other': main_other, 'extended': extended}


def _agent_units(conn, session: date, *, now: datetime) -> list[dict[str, Any]]:
    """§8.1 1–2: this session's undecided, conflict-free buy units (ATR-skipped rule
    plans included); main-list units first, then extended, each alphabetical."""
    groups = _session_units(conn, session, now=now)
    return groups['main_buy'] + groups['extended']


def _interpret_units(conn, session: date, *, now: datetime) -> list[dict[str, Any]]:
    """§7.1: this session's undecided main-list units that are not conflict-free
    buys (sell, watch, conflicted); alphabetical. Extended units never interpret."""
    return _session_units(conn, session, now=now)['main_other']
