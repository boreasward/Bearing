"""Salvage up to three individually valid proposals (spec §8.3)."""
from __future__ import annotations

import json
import re
from copy import deepcopy

from investment_assistant.optimize.evidence import EVIDENCE_KEYS


_FIELDS = frozenset({'source', 'title', 'change', 'rationale', 'evidence_refs', 'expected', 'acceptance'})
_LIMITS = {'change': 500, 'rationale': 500, 'expected': 200, 'acceptance': 300}
_FENCE = re.compile(r'\A```(?:json)?\s*\n(.*?)\n```\Z', re.S | re.I)


def _title_key(title: str) -> str:
    return ' '.join(title.split()).casefold()


def _reason(item, *, allowed: set[str], seen_titles: set[str]) -> str | None:
    if not isinstance(item, dict) or set(item) != _FIELDS:
        return 'proposal_malformed'
    if item['source'] not in ('data', 'agent_quality', 'route'):
        return 'proposal_source'
    if not isinstance(item['title'], str) or not item['title'].strip():
        return 'proposal_title'
    for key, limit in _LIMITS.items():
        if not isinstance(item[key], str) or not item[key].strip():
            return 'proposal_malformed'
        if len(item[key]) > limit:
            return 'proposal_length'
    citations = item['evidence_refs']
    if (not isinstance(citations, list) or not citations
            or any(not isinstance(ref, str) or ref not in allowed for ref in citations)):
        return 'proposal_refs_invalid'
    if _title_key(item['title']) in seen_titles:
        return 'proposal_duplicate_title'
    return None


def check_proposals(output, *, pack, open_titles: frozenset) -> dict:
    """Return {proposals, dropped}; no individual error rejects its siblings.

    dropped = [{index: zero-based input index, reason: code}]. Invalid outer
    JSON/shape yields one diagnostic with index=None. The first three *valid*,
    nonduplicate proposals are retained; later entries get proposal_limit.
    Title has no invented upper bound (the spec sets none).
    """
    if isinstance(output, str):
        text = output.strip()
        fence = _FENCE.fullmatch(text)
        try:
            output = json.loads(fence.group(1) if fence else text)
        except (ValueError, TypeError):
            output = None
    if not isinstance(output, dict) or set(output) != {'proposals'} or not isinstance(output['proposals'], list):
        return {'proposals': [], 'dropped': [{'index': None, 'reason': 'proposal_malformed'}]}
    allowed = {f"fact:{key}#{row['id']}" for key in EVIDENCE_KEYS for row in pack.get(key, [])}
    seen_titles = {_title_key(title) for title in open_titles}
    proposals, dropped = [], []
    for index, item in enumerate(output['proposals']):
        reason = _reason(item, allowed=allowed, seen_titles=seen_titles)
        if reason is None and len(proposals) >= 3:
            reason = 'proposal_limit'
        if reason is not None:
            dropped.append({'index': index, 'reason': reason})
        else:
            proposals.append(deepcopy(item))
            seen_titles.add(_title_key(item['title']))
    return {'proposals': proposals, 'dropped': dropped}
