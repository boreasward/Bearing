"""Pure portfolio-manager commentary gate (spec §7.2); never an order builder."""
from __future__ import annotations

from investment_assistant.core.constants import PLAN_LEVEL_KEYS
from investment_assistant.analysis.agent.research_check import CALL_REF, Rejected, _object, _text


# Only the numeric/level subset: PLAN_LEVEL_KEYS also contains the allowed plan_id.
_LEVEL_KEYS = frozenset(PLAN_LEVEL_KEYS).intersection({
    'entry_low', 'entry_high', 'stop', 'target1', 'ref_price', 'atr', 'sizing', 'shares',
}) | frozenset({'entry', 'levels', 'price', 'quantity', 'max_shares', 'entry_cap',
               'limit_price', 'stop_price', 'target_price', 'portion'})


def _has_levels(value) -> bool:
    if isinstance(value, dict):
        return bool(_LEVEL_KEYS.intersection(value)) or any(_has_levels(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_levels(item) for item in value)
    return False


def check_pm(output, *, ok_plan_ids: frozenset, refs: frozenset) -> dict:
    """Check decoded/fenced JSON and return only the four commentary fields.

    Rejections use Rejected from research_check: pm_malformed,
    pm_summary_length, pm_levels_forbidden, pm_ranking_invalid, pm_refs_invalid.
    Ranking is a subset (it need not rank every eligible plan). Positive ranks
    and plan IDs must be unique. Unknown fields are rejected, not propagated.
    """
    raw = _object(output, reason='pm_malformed')
    if _has_levels(raw):
        raise Rejected('pm_levels_forbidden')
    if set(raw) != {'summary', 'ranking', 'tradeoffs', 'hold_actions'}:
        raise Rejected('pm_malformed')
    if not _text(raw['summary'], 300):
        raise Rejected('pm_summary_length')
    if any(not isinstance(raw[key], list) for key in ('ranking', 'tradeoffs', 'hold_actions')):
        raise Rejected('pm_malformed')

    ranking, seen_plans, seen_ranks = [], set(), set()
    for item in raw['ranking']:
        if not isinstance(item, dict) or set(item) != {'plan_id', 'rank', 'reason'} or not _text(item['reason']):
            raise Rejected('pm_malformed')
        plan_id, rank = item['plan_id'], item['rank']
        if (not isinstance(plan_id, str) or plan_id not in ok_plan_ids or plan_id in seen_plans
                or isinstance(rank, bool) or not isinstance(rank, int) or rank < 1 or rank in seen_ranks):
            raise Rejected('pm_ranking_invalid')
        seen_plans.add(plan_id)
        seen_ranks.add(rank)
        ranking.append(dict(item))

    tradeoffs = []
    for item in raw['tradeoffs']:
        if not isinstance(item, dict) or set(item) != {'text', 'refs'} or not _text(item['text']):
            raise Rejected('pm_malformed')
        citations = item['refs']
        if (not isinstance(citations, list) or not citations
                or any(not isinstance(ref, str) or not (ref == 'fact:portfolio_check'
                       or (CALL_REF.fullmatch(ref) and ref in refs)) for ref in citations)):
            raise Rejected('pm_refs_invalid')
        tradeoffs.append({'text': item['text'], 'refs': list(citations)})

    holds = []
    for item in raw['hold_actions']:
        if (not isinstance(item, dict) or set(item) != {'ticker', 'suggestion', 'reason'}
                or not _text(item['ticker']) or not _text(item['reason'])
                or item['suggestion'] not in ('keep', 'trim', 'exit')):
            raise Rejected('pm_malformed')
        holds.append(dict(item))
    return {'summary': raw['summary'], 'ranking': ranking, 'tradeoffs': tradeoffs, 'hold_actions': holds}
