"""Pure research-output boundary (spec §6.1).

``refs`` is the Recorder's research-phase allowlist. An inference must cite a
call also cited by a non-inference fact in this output; a risk, another
inference, or merely an existing call is not factual support. Fact order does
not affect that check. There is no separate, invented fact-reference syntax.

The six specified rejection reasons are preserved verbatim. Other structural
errors (including text >200, >6 risks and invalid JSON) use research_malformed.
This module owns Rejected for these new research/PM checks; callers must catch
this class rather than the legacy citations exception.
"""
from __future__ import annotations

import json
import re

KINDS = frozenset({'disclosure', 'guidance', 'price_fact', 'user', 'inference'})
CALL_REF = re.compile(r'call:[1-9][0-9]*\Z')
_FENCE = re.compile(r'\A```(?:json)?\s*\n(.*?)\n```\Z', re.DOTALL | re.IGNORECASE)


class Rejected(ValueError):
    """A model-output rejection, with a stable machine-readable ``reason``."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _object(output, *, reason: str) -> dict:
    if isinstance(output, str):
        text = output.strip()
        fence = _FENCE.fullmatch(text)
        try:
            output = json.loads(fence.group(1) if fence else text)
        except (ValueError, TypeError):
            raise Rejected(reason) from None
    if not isinstance(output, dict):
        raise Rejected(reason)
    return output


def _text(value, limit: int | None = None) -> bool:
    return isinstance(value, str) and bool(value.strip()) and (limit is None or len(value) <= limit)


def _refs(value, allowed: frozenset, *, inference: bool = False) -> list[str]:
    if inference and value == []:
        raise Rejected('research_inference_unsupported')
    if (not isinstance(value, list) or not value
            or any(not isinstance(ref, str) or not CALL_REF.fullmatch(ref) or ref not in allowed for ref in value)):
        raise Rejected('research_refs_invalid')
    return list(value)


def check_research(output, *, refs: frozenset) -> dict:
    """Accept a decoded object or JSON (optionally fenced); return fresh data.

    Failures reject the whole research segment. No truncation or removal of
    unsupported statements is performed. Text is preserved, not silently cut.
    """
    raw = _object(output, reason='research_malformed')
    facts = raw.get('facts')
    if not isinstance(facts, list) or not 1 <= len(facts) <= 12:
        raise Rejected('research_facts_count')
    if set(raw) != {'facts', 'risks', 'blocked', 'summary'}:
        raise Rejected('research_malformed')
    checked_facts = []
    for fact in facts:
        if not isinstance(fact, dict):
            raise Rejected('research_malformed')
        kind = fact.get('kind')
        if not isinstance(kind, str) or kind not in KINDS:
            raise Rejected('research_kind')
        if set(fact) != {'text', 'kind', 'refs'} or not _text(fact['text'], 200):
            raise Rejected('research_malformed')
        checked_facts.append({'text': fact['text'], 'kind': kind,
                              'refs': _refs(fact['refs'], refs, inference=kind == 'inference')})
    inferences = [fact for fact in checked_facts if fact['kind'] == 'inference']
    factual_refs = {ref for fact in checked_facts if fact['kind'] != 'inference' for ref in fact['refs']}
    if len(inferences) > 3 or any(not factual_refs.intersection(fact['refs']) for fact in inferences):
        raise Rejected('research_inference_unsupported')

    risks = raw['risks']
    if not isinstance(risks, list) or len(risks) > 6:
        raise Rejected('research_malformed')
    checked_risks = []
    for risk in risks:
        if not isinstance(risk, dict) or set(risk) != {'text', 'refs'} or not _text(risk['text'], 200):
            raise Rejected('research_malformed')
        checked_risks.append({'text': risk['text'], 'refs': _refs(risk['refs'], refs)})
    if not _text(raw['summary'], 300):
        raise Rejected('research_summary_length')
    blocked = raw['blocked']
    if (not isinstance(blocked, list) or len(blocked) > 6
            or any(not isinstance(item, dict) or set(item) != {'what', 'why'}
                   or not _text(item['what']) or not _text(item['why']) for item in blocked)):
        raise Rejected('research_blocked_shape')
    return {'facts': checked_facts, 'risks': checked_risks,
            'blocked': [dict(item) for item in blocked], 'summary': raw['summary']}


def research_fact_block(output) -> str:
    """Render a *checked* output, retaining original call refs for later phases."""
    facts = [f"{i}. [{fact['kind']}] {fact['text']} ({', '.join(fact['refs'])})"
             for i, fact in enumerate(output['facts'], 1)]
    risks = [f"{i}. {risk['text']} ({', '.join(risk['refs'])})" for i, risk in enumerate(output['risks'], 1)]
    blocked = [f"{i}. {item['what']}：{item['why']}" for i, item in enumerate(output['blocked'], 1)]
    parts = ['[fact:research]', output['summary']]
    for title, rows in (('事实', facts), ('风险', risks), ('缺失数据', blocked)):
        parts.extend([f'## {title}', '\n'.join(rows) if rows else '暂无条目'])
    return '\n'.join(parts)


CONCLUSION_ACTIONS = frozenset({'buy', 'pass', 'wait'})


def check_conclusion(output, *, refs: frozenset) -> dict:
    """The dossier conclusion of the research second entrance (§6.3/§6.4): one JSON object the
    conclusion phase emits after the researcher. ``action`` is a *dossier* verdict (D39 B: it never
    opens a chain or trades); ``wait`` says the data the researcher found missing decides the question.
    Sections are the dossier's reading structure (催化与时间 / 反证与替代解释 / 价格与情景 / 下一步);
    a buy conclusion must name at least one catalyst — what would prove it."""
    raw = _object(output, reason='conclusion_malformed')
    if set(raw) != {'action', 'summary', 'catalysts', 'counter', 'price_scenarios', 'next_steps'}:
        raise Rejected('conclusion_malformed')
    if raw['action'] not in CONCLUSION_ACTIONS:
        raise Rejected('conclusion_action')
    if not _text(raw['summary'], 300):
        raise Rejected('conclusion_summary')
    out: dict = {'action': raw['action'], 'summary': raw['summary'], 'catalysts': [], 'counter': [],
                 'price_scenarios': [], 'next_steps': []}
    for key in ('catalysts', 'counter', 'price_scenarios', 'next_steps'):
        items = raw[key]
        if not isinstance(items, list) or len(items) > 6:
            raise Rejected('conclusion_malformed')
        for item in items:
            if (not isinstance(item, dict) or set(item) != {'text', 'refs'}
                    or not _text(item['text'], 200)):
                raise Rejected('conclusion_malformed')
            out[key].append({'text': item['text'], 'refs': _refs(item['refs'], refs)})
    if out['action'] == 'buy' and not out['catalysts']:
        raise Rejected('conclusion_catalysts_required')
    return out
