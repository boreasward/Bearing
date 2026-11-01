"""Stored-data shadow summaries, backed by the execution simulator."""
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

from investment_assistant.analysis.agent import graph as agent_graph
from investment_assistant.execution import simulation as _plan_shadow_module
from investment_assistant.review import outcomes as review

def _decided_units(conn, heads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rule_ids = {c['id']: c['rule_id'] for c in data_repo.candidates_by_ids(
        conn, [cid for h in heads for cid in h['candidate_ids']])}
    return [{**h, 'rule_ids': [rule_ids[c] for c in h['candidate_ids'] if c in rule_ids]} for h in heads]


def _outcomes(conn, units, session: date, now: datetime) -> dict[Any, dict[str, Any]]:
    spy = data_repo.read_bars(conn, BENCHMARK, session, cutoff_at=now, limit=400)
    cache: dict[str, list] = {}
    out = {}
    for u in units:
        if u['ticker'] not in cache:
            cache[u['ticker']] = data_repo.read_bars(conn, u['ticker'], session, cutoff_at=now, limit=400)
        out[u['id']] = review.shadow_outcome(u, cache[u['ticker']], spy)
    return out


def shadow(*, now: datetime, since: date | None = None) -> dict[str, Any]:
    """The control group (`review-8`, v2.9 D26): every unit since ``since`` — a ticker-day with candidates, whether or
    not anybody decided on it or traded it — assumed entered at the next open, with its 5 / 10 / 20 session excess
    return over SPY. Old decided-only snapshots keep their own `review-7` shape in the weekly review."""
    session = bars.latest_closed_session(now)
    since = since or session - timedelta(days=90)
    with core_tx._tx() as conn:
        units = []
        for group in sorted(analysis_repo.candidate_groups_since(conn, since), key=lambda g: (g['signal_date'], g['ticker'])):
            if group['signal_date'] > session:
                continue
            shape = synthesis.synthesize(group['ticker'], group['signal_date'], group['candidates'], holding=False,
                                         now=now)
            units.append({'id': f"{group['ticker']}:{group['signal_date'].isoformat()}", 'ticker': group['ticker'],
                          'signal_date': group['signal_date'], 'proposed_action': shape['proposed_action'],
                          'rule_ids': shape['rule_ids']})
        outcomes = _outcomes(conn, units, session, now)
    return {'session': session, 'since': since, 'calc_version': review.CALC_VERSION,
            'units': [{**u, 'outcome': outcomes[u['id']]} for u in units]}


def _plan_chains(conn, plans: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
    """v2.5 §9.1: ``plan_id → {version_no, origin, is_head, supersedes_plan_id}`` for the agent-2 versions
    among ``plans``; agent-1, rule and own plans (and everything without migration 026) are absent."""
    wanted = [p for p in plans if p['kind'] == 'agent' and p['plan_version'] in (agent_graph.AGENT_V2, agent_graph.AGENT_V3)]
    if not wanted or not core_schema.timeline_ready(conn):
        return {}
    versions_by_unit: dict[tuple, list[dict[str, Any]]] = {}
    out = {}
    for plan in wanted:
        key = (plan['ticker'], plan['signal_date'])
        if key not in versions_by_unit:
            versions_by_unit[key] = execution_repo.chain_versions(conn, *key)
        versions = versions_by_unit[key]
        head_no = max(v['version_no'] for v in versions) if versions else None
        version = next((v for v in versions if v['plan_id'] == plan['id']), None)
        if version is not None:
            out[plan['id']] = {'version_no': version['version_no'], 'origin': version['origin'],
                               'is_head': version['version_no'] == head_no,
                               'supersedes_plan_id': (str(version['supersedes_plan_id'])
                                                      if version['supersedes_plan_id'] else None)}
    return out


def shadow_outcomes(plan: dict[str, Any], bars_: list[dict[str, Any]], spy: list[dict[str, Any]], *,
                    splits: list[tuple[date, Decimal]]) -> dict[str, Any]:
    """One plan's shadow outcomes (v2.9 D14): ``{'outcome', 'shadow_version', 'shadow_v1'}``. A plan with execution
    fields (``plan-2``, ``agent-3``, own plans since v2.9) is replayed by `plan-shadow-2` and, as the counterfactual
    「无时间止损、旧入场规则」, by `plan-shadow-1` (``shadow_v1``); an older plan only by `plan-shadow-1`
    (``shadow_v1`` None, ``outcome`` is that result)."""
    both = _plan_shadow_module.simulate_both(plan, bars_, spy, splits=splits)
    if 'v2' in both:
        return {'outcome': both['v2'], 'shadow_version': _plan_shadow_module.SHADOW_V2, 'shadow_v1': both['v1']}
    return {'outcome': both['v1'], 'shadow_version': _plan_shadow_module.SHADOW_V1, 'shadow_v1': None}


def plan_shadow(*, now: datetime, since: date | None = None) -> dict[str, Any]:
    """Simulated outcomes for rule, agent and own plans of the last 90 days; nothing frozen until a review.

    v2.5 §9.1: every agent version is its own row with ``chain`` (None for other plans), and ``agent_summary`` sums
    each chain's first version and its current head. v2.9 §5.6: every row carries its ``shadow_version``; a plan
    with execution fields is replayed by `plan-shadow-2` and its `plan-shadow-1` result rides along as ``shadow_v1``;
    ``agent_summary.counterfactual`` sums the same agent rows under both."""
    session = bars.latest_closed_session(now)
    since = since or session - timedelta(days=90)
    with core_tx._tx() as conn:
        spy = data_repo.read_bars(conn, BENCHMARK, session, cutoff_at=now, limit=400)
        plans = execution_repo.plans_since(conn, since)
        chains = _plan_chains(conn, plans)
        out = []
        for plan in plans:
            ticker_bars = data_repo.read_bars(conn, plan['ticker'], session, cutoff_at=now, limit=400)
            found = shadow_outcomes(plan, ticker_bars, spy, splits=data_repo.split_events(conn, plan['ticker']))
            out.append({**plan, 'chain': chains.get(plan['id']), **found})
    summary = review.shadow_agent_summary(out)
    paired = [row for row in out if row.get('kind') == 'agent' and row['shadow_v1'] is not None]
    v2_outcomes = [row['outcome'] for row in paired]
    summary['counterfactual'] = {
        'v1': review.shadow_block([row['shadow_v1'] for row in paired]),
        'v2': {**review.shadow_block(v2_outcomes), 'voided': sum(1 for o in v2_outcomes if o.get('status') == 'voided')}}
    return {'session': session, 'since': since, 'calc_version': _plan_shadow_module.SHADOW_VERSION,
            'shadow_version': _plan_shadow_module.SHADOW_VERSION, 'plans': out, 'agent_summary': summary}
