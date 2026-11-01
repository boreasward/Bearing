"""Three-track comparison per decision unit (P4 design §4): rule plan, agent
plan and the actually executed result, all in R of each track's own stop.
Pure functions; the row assembly and SQL live in repository/service.

「没算出来」（pending、missing）与「0R」（没交易）分开：均值与中位数只
用 r 不为空的结果，not_executable / expired / not_executed 记 0。

`tracks-2` (v2.9 spec D27) lives beside `tracks-1`: `agent_pick` chooses the Agent version (old decision →
`decision`, the first fill's linked version → `fill`, else `first`; a unit merged into another chain →
`merged`, outside the mean and counted apart), `actual_track_v2` takes the plan from the fills' own plan link,
`summarize_v2` counts the merged units. The `tracks-1` functions are unchanged (a voided shadow outcome scores
0R like an expired one; no tracks-1 outcome is ever voided)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from statistics import median
from typing import Any

from investment_assistant.review.discipline import _at_basis, _current_basis

TRACKS_V1 = 'tracks-1'
TRACKS_V2 = 'tracks-2'
# review.weekly builds `tracks-2` rows and weekly/monthly snapshots (spec §2); `tracks-1` rows stay readable
# in the old snapshots and old review cards.
TRACKS_VERSION = TRACKS_V2

TRACK_STATUS_TEXT = {
    'missing': '缺失',
    'not_executable': '计划作废',
    'pending': '待定',
    'expired': '到期未成交',
    'open': '未平仓',
    'closed': '已平仓',
    'pass': '不建议',
    'not_executed': '未执行',
    'no_plan': '无计划成交',
}
# tracks-2 adds two statuses; the tracks-1 table above stays as the catalog and frontend sync know it.
TRACK_STATUS_TEXT_V2 = {**TRACK_STATUS_TEXT, 'voided': '开盘作废', 'merged': '并入其他链'}
VERSION_BASES = ('decision', 'fill', 'first', 'merged')


def plan_track(plan: dict[str, Any] | None, outcome: dict[str, Any] | None) -> dict[str, Any]:
    """§4.2's rule table applied to one plan and its simulated outcome."""
    if plan is None:
        return {'plan_id': None, 'status': 'missing', 'r': None}
    if plan.get('status') == 'invalid':
        return {'plan_id': plan['id'], 'status': 'not_executable', 'r': 0.0}
    if outcome is None:
        return {'plan_id': plan['id'], 'status': 'missing', 'r': None}
    status = outcome['status']
    r = 0.0 if status in ('expired', 'voided') else outcome.get('r')
    return {'plan_id': plan['id'], 'status': status, 'r': r}


def agent_track(run: dict[str, Any] | None, plan: dict[str, Any] | None,
                outcome: dict[str, Any] | None, *, version_no: int | None = None,
                version_basis: str | None = None) -> dict[str, Any]:
    """The unit's chosen agent run: pass scores 0, ok follows its agent plan,
    everything else is missing with the run status and reason kept.

    v2.5 §9.2: the row also says which version was taken (``version_no``, None for agent-1)
    and why (``version_basis``: 'decision' / 'first', None when no agent plan was taken)."""
    return {**_agent_track(run, plan, outcome), 'version_no': version_no, 'version_basis': version_basis}


def _agent_track(run: dict[str, Any] | None, plan: dict[str, Any] | None,
                 outcome: dict[str, Any] | None) -> dict[str, Any]:
    if run is None:
        return {'run_id': None, 'run_status': None, 'run_reason': None,
                'plan_id': None, 'status': 'missing', 'r': None}
    base = {'run_id': run['id'], 'run_status': run['status'], 'run_reason': run.get('reason')}
    if run['status'] == 'pass':
        return {**base, 'plan_id': None, 'status': 'pass', 'r': 0.0}
    if run['status'] == 'ok':
        track = plan_track(plan, outcome)
        return {**base, 'plan_id': track['plan_id'], 'status': track['status'], 'r': track['r']}
    return {**base, 'plan_id': None, 'status': 'missing', 'r': None}


def _day(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value)[:10])


def actual_track(*, decision: dict[str, Any] | None, plan: dict[str, Any] | None, buys: list[dict[str, Any]],
                 segments: list[dict[str, Any]], open_lots: list[dict[str, Any]],
                 last_close: Decimal | None, splits: list[tuple[date, Decimal]],
                 session: date, unit_valid_until: date | None) -> dict[str, Any]:
    """§4.2's actual table: what you did with this unit, in R of the chosen
    plan's stop. Closed legs price the risk at the buy-date basis (the same
    formula as the discipline metrics); open lots convert stop and cost to the
    current basis and use the latest close."""
    out = {'decision_id': decision['id'] if decision else None,
           'final_action': decision['final_action'] if decision else None,
           'plan_id': decision['plan_id'] if decision else None,
           'plan_kind': plan['kind'] if plan else None,
           'buy_trade_ids': [b['id'] for b in buys],
           'status': None, 'r': None, 'open': False}
    if decision is not None and decision['final_action'] != 'buy':
        return {**out, 'status': 'not_executed', 'r': 0.0}
    if not buys:
        valid_until = unit_valid_until
        if decision is not None and plan is not None and plan['valid_until'] < session:
            return {**out, 'status': 'not_executed', 'r': 0.0}
        if valid_until is None or valid_until < session:
            return {**out, 'status': 'not_executed', 'r': 0.0}
        return {**out, 'status': 'pending', 'r': None}
    if plan is None or decision is None or decision.get('plan_id') is None:
        return {**out, 'plan_kind': plan['kind'] if plan else None,
                'status': 'no_plan', 'r': None}
    total_qty = Decimal(0)
    weighted = Decimal(0)
    for seg in segments:
        buy_day = _day(seg['opened_at'])
        stop_at_buy = _at_basis(plan['stop'], plan['signal_date'], buy_day, splits)
        entry, exit_ = seg['entry_cost_per_share'], seg['exit_price']
        if entry - stop_at_buy == 0:
            continue
        weighted += seg['quantity'] * (exit_ - entry) / (entry - stop_at_buy)
        total_qty += seg['quantity']
    open_qty = sum((lot['quantity'] for lot in open_lots), Decimal(0))
    if open_qty and last_close is not None:
        stop_now = _current_basis(plan['stop'], plan['signal_date'], splits)
        for lot in open_lots:
            if lot['cost_per_share'] - stop_now == 0:
                continue
            weighted += lot['quantity'] * (last_close - lot['cost_per_share']) / (lot['cost_per_share'] - stop_now)
            total_qty += lot['quantity']
    if total_qty == 0:
        return {**out, 'status': 'pending', 'r': None}
    r = float(weighted / total_qty)
    status = 'open' if open_qty else 'closed'
    return {**out, 'status': status, 'r': r, 'open': status == 'open'}


def _stats(results: list[dict[str, Any]]) -> dict[str, Any]:
    rs = [result['r'] for result in results if result['r'] is not None]
    by_status: dict[str, int] = {}
    for result in results:
        by_status[result['status']] = by_status.get(result['status'], 0) + 1
    return {'units': len(results), 'determined': len(rs),
            'pending': sum(1 for result in results if result['status'] == 'pending'),
            'missing': sum(1 for result in results if result['status'] in ('missing', 'no_plan')),
            'mean': sum(rs) / len(rs) if rs else None,
            'median': median(rs) if rs else None,
            'sum': sum(rs) if rs else None,
            'wins': sum(1 for r in rs if r > 0),
            'by_status': by_status}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """§4.3: counts and descriptive stats per pool and track; no significance tests."""
    out: dict[str, Any] = {}
    for pool in ('all', 'core', 'extended'):
        group = rows if pool == 'all' else [row for row in rows if row['pool'] == pool]
        block = {track: _stats([row[track] for row in group]) for track in ('rule', 'agent', 'actual')}
        kinds: dict[str, int] = {}          # plan_kind as a string key: None (no plan) sorts in JSON
        for row in group:
            kind = row['actual'].get('plan_kind') or 'none'
            kinds[kind] = kinds.get(kind, 0) + 1
        block['actual']['by_plan_kind'] = kinds
        out[pool] = block
    return out


# ---------------------------------------------------------------- tracks-2 (v2.9 spec D27)

def agent_pick(unit: dict[str, Any], *, legacy_decision: dict[str, Any] | None,
               chain_buys: list[dict[str, Any]], versions: list[dict[str, Any]],
               merged: bool) -> dict[str, Any]:
    """Which Agent version the agent track takes: `{plan, plan_id, version_no, version_basis}`.

    unit: `{ticker, signal_date}` (carried for the caller's row, not read); legacy_decision: the unit's head
    decision from before v2.9 (`agent_plan_id`) or None; chain_buys: the unit's chain's live buys (`id`,
    `executed_at`, `plan_id`); versions: the unit's own chain, `agent_plan_versions` rows oldest first (`plan_id`,
    `version_no`; extra keys such as `plan` ride along); merged: the unit joined another chain and opened none.

    1. no chain of its own and merged → `merged` (no plan);
    2. an old decision with `agent_plan_id` → that version, `decision` (an id beside no chain version is
       returned with `plan = None` and `version_no = None` for the caller to resolve, like tracks-1);
    3. the first live buy (by executed_at) linked to one of the chain's versions → that version, `fill`;
    4. else the first version, `first`. No versions and not merged → nothing taken (`version_basis = None`).
    `plan` is the chosen row of `versions`, whatever extra keys it carries.
    """
    if not versions:
        if merged:
            return {'plan': None, 'plan_id': None, 'version_no': None, 'version_basis': 'merged'}
        return {'plan': None, 'plan_id': None, 'version_no': None, 'version_basis': None}
    by_plan = {str(v['plan_id']): v for v in versions}

    def pick(version: dict[str, Any], basis: str) -> dict[str, Any]:
        return {'plan': version, 'plan_id': version['plan_id'], 'version_no': version['version_no'],
                'version_basis': basis}

    chosen = legacy_decision.get('agent_plan_id') if legacy_decision is not None else None
    if chosen is not None:
        if str(chosen) in by_plan:
            return pick(by_plan[str(chosen)], 'decision')
        return {'plan': None, 'plan_id': chosen, 'version_no': None, 'version_basis': 'decision'}
    for trade in sorted((b for b in chain_buys if b.get('voided_at') is None and b.get('plan_id') is not None),
                        key=lambda b: (b['executed_at'], str(b['id']))):
        if str(trade['plan_id']) in by_plan:
            return pick(by_plan[str(trade['plan_id'])], 'fill')
    return pick(min(versions, key=lambda v: v['version_no']), 'first')


def merged_agent_track() -> dict[str, Any]:
    """The agent track of a unit merged into another chain: no run, no plan, no R (excluded from the mean)."""
    return {'run_id': None, 'run_status': None, 'run_reason': None, 'plan_id': None, 'status': 'merged', 'r': None,
            'version_no': None, 'version_basis': 'merged'}


def actual_track_v2(*, legacy_decision: dict[str, Any] | None = None, plans_by_trade: dict[Any, dict[str, Any]],
                    buys: list[dict[str, Any]], segments: list[dict[str, Any]], open_lots: list[dict[str, Any]],
                    last_close: Decimal | None, splits: list[tuple[date, Decimal]], session: date,
                    unit_valid_until: date | None) -> dict[str, Any]:
    """§D27's actual track: what you did with this unit, in R of the plan the first plan-linked fill followed.

    `plans_by_trade` maps a buy's id to the plan row it is linked to (through `plan_id`, or through an old
    decision for fills that predate v2.9; trades without a plan are absent). The plan and `plan_kind` are the
    first buy's (by `executed_at`) that has one. No buys: `not_executed` (0R) once the unit's validity is over
    or an old decision said no, else `pending`; buys but no plan on any of them: `no_plan`. The R arithmetic
    is `actual_track`'s. The output keeps `decision_id` / `final_action` (from `legacy_decision`, else None)
    so rows keep the tracks-1 shape.
    """
    ordered = sorted(buys, key=lambda b: (b.get('executed_at') or date.min, str(b['id'])))
    plan = next((plans_by_trade[b['id']] for b in ordered if plans_by_trade.get(b['id']) is not None), None)
    out = {'decision_id': legacy_decision['id'] if legacy_decision else None,
           'final_action': legacy_decision['final_action'] if legacy_decision else None,
           'plan_id': plan['id'] if plan else None,
           'plan_kind': plan['kind'] if plan else None,
           'buy_trade_ids': [b['id'] for b in buys],
           'status': None, 'r': None, 'open': False}
    if not buys:
        if legacy_decision is not None and legacy_decision['final_action'] != 'buy':
            return {**out, 'status': 'not_executed', 'r': 0.0}
        if unit_valid_until is None or unit_valid_until < session:
            return {**out, 'status': 'not_executed', 'r': 0.0}
        return {**out, 'status': 'pending', 'r': None}
    if plan is None:
        return {**out, 'status': 'no_plan', 'r': None}
    total_qty = Decimal(0)
    weighted = Decimal(0)
    for seg in segments:
        buy_day = _day(seg['opened_at'])
        stop_at_buy = _at_basis(plan['stop'], plan['signal_date'], buy_day, splits)
        entry, exit_ = seg['entry_cost_per_share'], seg['exit_price']
        if entry - stop_at_buy == 0:
            continue
        weighted += seg['quantity'] * (exit_ - entry) / (entry - stop_at_buy)
        total_qty += seg['quantity']
    open_qty = sum((lot['quantity'] for lot in open_lots), Decimal(0))
    if open_qty and last_close is not None:
        stop_now = _current_basis(plan['stop'], plan['signal_date'], splits)
        for lot in open_lots:
            if lot['cost_per_share'] - stop_now == 0:
                continue
            weighted += lot['quantity'] * (last_close - lot['cost_per_share']) / (lot['cost_per_share'] - stop_now)
            total_qty += lot['quantity']
    if total_qty == 0:
        return {**out, 'status': 'pending', 'r': None}
    status = 'open' if open_qty else 'closed'
    return {**out, 'status': status, 'r': float(weighted / total_qty), 'open': status == 'open'}


def _stats_v2(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {**_stats(results), 'merged': sum(1 for result in results if result['status'] == 'merged')}


def summarize_v2(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """`summarize` for tracks-2 rows: the same pools and tracks, plus a `merged` count in every block (merged
    units carry no R, so they stay out of mean / median / sum and are only counted)."""
    out: dict[str, Any] = {}
    for pool in ('all', 'core', 'extended'):
        group = rows if pool == 'all' else [row for row in rows if row['pool'] == pool]
        block = {track: _stats_v2([row[track] for row in group]) for track in ('rule', 'agent', 'actual')}
        kinds: dict[str, int] = {}
        for row in group:
            kind = row['actual'].get('plan_kind') or 'none'
            kinds[kind] = kinds.get(kind, 0) + 1
        block['actual']['by_plan_kind'] = kinds
        out[pool] = block
    return out
