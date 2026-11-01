"""Knowledge performance look-back (v2.7 spec §7, ``knowledge-perf-1``). Pure.

Descriptive only. The unit of sampling is the chain (ticker, signal date): of all the plan versions that
cited a knowledge item, the earliest one stands for its unit, so a chain revised three times that cites the
same item counts once and a later revision cannot polish the result. The summary is
``review.shadow_block`` over the plan-shadow outcomes, with the same minimum sample as the plan-shadow view.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from investment_assistant.review import outcomes as review
from investment_assistant.review.tracks import TRACK_STATUS_TEXT

KNOWLEDGE_PERF_VERSION = 'knowledge-perf-1'
NOTE_ENOUGH = '描述性统计，样本小且重叠，不代表这条经验有效'


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, (date, datetime)) else str(value)[:10]


def _outcome_date(plan: dict[str, Any], outcome: dict[str, Any] | None) -> str | None:
    """The last leg's exit date of a closed plan, ``valid_until`` of an expired one, else None."""
    if outcome is None:
        return None
    if outcome.get('status') == 'closed' and outcome.get('legs'):
        return _iso(outcome['legs'][-1]['exit_date'])
    if outcome.get('status') == 'expired':
        return _iso(plan.get('valid_until'))
    return None


def _sort_key(plan: dict[str, Any], plan_id: str) -> tuple:
    return plan['created_at'], plan_id


def summarize(*, citations: list[dict[str, Any]], plans: dict[str, dict[str, Any]],
              outcomes: dict[str, dict[str, Any]], reviews_cited: int) -> dict[str, Any]:
    """``citations``: ``knowledge_citations`` rows of one item (``plan_id`` None = cited in a review only);
    ``plans``: plan id -> ``trade_plans`` row (``ticker``, ``signal_date``, ``kind``, ``created_at``,
    ``valid_until``, optional ``version_no``); ``outcomes``: plan id -> ``plan_shadow.simulate`` result;
    ``reviews_cited``: how many review records cited the item, reported beside the sample, never in it."""
    by_plan: dict[str, list[dict[str, Any]]] = {}
    for row in citations:
        if row.get('plan_id') is None or str(row['plan_id']) not in plans:
            continue
        by_plan.setdefault(str(row['plan_id']), []).append(row)

    by_unit: dict[tuple, list[str]] = {}
    for plan_id in by_plan:
        plan = plans[plan_id]
        by_unit.setdefault((plan['ticker'], plan['signal_date']), []).append(plan_id)

    rows: list[dict[str, Any]] = []
    sampled: list[dict[str, Any]] = []
    for (ticker, signal_date), plan_ids in sorted(by_unit.items(), key=lambda item: (item[0][1], item[0][0])):
        plan_id = min(plan_ids, key=lambda pid: _sort_key(plans[pid], pid))
        plan = plans[plan_id]
        cited = by_plan[plan_id]
        cited_version = min(c['version'] for c in cited)
        outcome = outcomes.get(plan_id)
        status = outcome['status'] if outcome else 'missing'
        sampled.append(outcome or {'status': 'missing', 'r': None})
        rows.append({
            'unit': {'ticker': ticker, 'signal_date': _iso(signal_date)},
            'plan_id': plan_id, 'kind': plan.get('kind'), 'version_no': plan.get('version_no'),
            'cited_version': cited_version,
            'fields': sorted({c['field'] for c in cited if c['version'] == cited_version}),
            'status': status, 'status_text': TRACK_STATUS_TEXT.get(status, status),
            'r': outcome.get('r') if outcome else None,
            'fill_date': _iso(outcome.get('fill_date')) if outcome else None,
            'outcome_date': _outcome_date(plan, outcome)})

    block = review.shadow_block(sampled)
    note = (NOTE_ENOUGH if block['determined'] >= review.SHADOW_MIN_SAMPLE else
            f"样本 {block['count']} 个（有结果 {block['determined']} 个），少于 {review.SHADOW_MIN_SAMPLE} 个不给均值")

    versions: dict[int, dict[str, set]] = {}
    for plan_id, cited in by_plan.items():
        for row in cited:
            entry = versions.setdefault(row['version'], {'plans': set(), 'units': set()})
            entry['plans'].add(plan_id)
            entry['units'].add((plans[plan_id]['ticker'], plans[plan_id]['signal_date']))
    return {'calc_version': KNOWLEDGE_PERF_VERSION, 'sample': block, 'min_sample': review.SHADOW_MIN_SAMPLE,
            'note': note, 'rows': rows,
            'by_version': [{'version': version, 'plans': len(entry['plans']), 'units': len(entry['units'])}
                           for version, entry in sorted(versions.items())],
            'reviews_cited': reviews_cited}
