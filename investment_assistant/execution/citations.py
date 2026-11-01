"""Proposal and counter validation (P3 design §6.2–6.3). Pure functions.

check_proposal runs the six gates in order — fence/JSON shape, action and
thesis structure, buy levels, citation existence, price-tool backing, level
ordering and band — so the first failing gate names the reason. filter_counter
keeps only risks whose refs exist in either phase; any prices or actions in a
counter output are ignored at the code level.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

LEVEL_BAND = Decimal('0.5')
_LEVEL_KEYS = ('entry_low', 'entry_high', 'stop', 'target1')
_CENT = Decimal('0.01')
_FENCE = re.compile(r'^```(?:json)?\s*(.*?)\s*```$', re.DOTALL)


class Rejected(Exception):
    """The proposal is void; reason is one of the rejected reasons (§6.4)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _loads(text: str) -> Any:
    stripped = text.strip()
    fence = _FENCE.match(stripped)   # exactly one fence layer, never nested
    if fence:
        stripped = fence.group(1)
    return json.loads(stripped)


def _positive_number(value: Any) -> Decimal | None:
    """Numbers and numeric strings count; bools, NaN and non-positives do not."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() and number > 0 else None


def _cent(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding='ROUND_HALF_UP')


def check_proposal(text: str, *, refs: set[str], price_refs: set[str], last_close: Decimal,
                   mode: str = 'entry') -> dict:
    """§6.2's six steps; a Rejected carries the step's reason, nothing is salvaged.

    mode='holding' (v2.9 spec §21 R3): a holding-phase revision may move the stop up through the entry price, so
    the ordering step asks `entry_low <= entry_high < target1` and `stop < target1` instead of
    `stop < entry_low <= entry_high < target1`; the ±50 % band is unchanged."""
    if mode not in ('entry', 'holding'):
        raise ValueError(f'unknown chain mode: {mode!r}')
    try:
        payload = _loads(text)
    except (json.JSONDecodeError, ValueError):
        raise Rejected('malformed') from None
    if not isinstance(payload, dict):
        raise Rejected('malformed')

    action = payload.get('action')
    if action not in ('buy', 'pass'):
        raise Rejected('malformed')
    if not isinstance(payload.get('summary'), str):
        raise Rejected('malformed')
    thesis_raw = payload.get('thesis')
    if not isinstance(thesis_raw, list) or not thesis_raw:
        raise Rejected('malformed')
    thesis = []
    for item in thesis_raw:
        if not isinstance(item, dict) or not isinstance(item.get('text'), str):
            raise Rejected('malformed')
        item_refs = item.get('refs')
        if not isinstance(item_refs, list) or not item_refs:
            raise Rejected('malformed')
        thesis.append({'text': item['text'], 'refs': item_refs})

    levels: dict[str, dict] | None = None
    if action == 'buy':
        levels_raw = payload.get('levels')
        if not isinstance(levels_raw, dict):
            raise Rejected('malformed')
        levels = {}
        for key in _LEVEL_KEYS:
            item = levels_raw.get(key)
            if not isinstance(item, dict):
                raise Rejected('malformed')
            value = _positive_number(item.get('value'))
            item_refs = item.get('refs')
            if value is None or not isinstance(item_refs, list) or not item_refs:
                raise Rejected('malformed')
            levels[key] = {'value': value, 'refs': item_refs}

    cited = [r for item in thesis for r in item['refs']]
    if levels is not None:
        cited += [r for item in levels.values() for r in item['refs']]
    if any(not isinstance(ref, str) or ref not in refs for ref in cited):
        raise Rejected('invalid_citation')

    if levels is not None and any(
            not any(isinstance(ref, str) and ref in price_refs for ref in item['refs'])
            for item in levels.values()):
        raise Rejected('invalid_citation')

    if levels is not None:
        values = {key: _cent(item['value']) for key, item in levels.items()}
        if mode == 'holding':
            ordered = values['entry_low'] <= values['entry_high'] < values['target1'] and values['stop'] < values['target1']
        else:
            ordered = values['stop'] < values['entry_low'] <= values['entry_high'] < values['target1']
        if not ordered:
            raise Rejected('level_check')
        low_band, high_band = last_close * (1 - LEVEL_BAND), last_close * (1 + LEVEL_BAND)
        if any(value < low_band or value > high_band for value in values.values()):
            raise Rejected('level_check')
        levels = {key: {'value': values[key], 'refs': item['refs']} for key, item in levels.items()}

    return {'action': action, 'summary': payload['summary'], 'thesis': thesis, 'levels': levels}


def filter_counter(text: str, *, refs: set[str]) -> dict:
    """Risks only; entries citing unknown refs drop out and count, unparseable output is malformed."""
    try:
        payload = _loads(text)
    except (json.JSONDecodeError, ValueError):
        return {'status': 'malformed', 'risks': [], 'dropped': 0}
    if not isinstance(payload, dict) or not isinstance(payload.get('risks'), list):
        return {'status': 'malformed', 'risks': [], 'dropped': 0}
    risks: list[dict] = []
    dropped = 0
    for item in payload['risks']:
        valid = (isinstance(item, dict) and isinstance(item.get('text'), str)
                 and isinstance(item.get('refs'), list) and item['refs']
                 and all(isinstance(ref, str) and ref in refs for ref in item['refs']))
        if valid:
            risks.append({'text': item['text'], 'refs': item['refs']})
        else:
            dropped += 1
    return {'status': 'ok', 'risks': risks, 'dropped': dropped}


def check_interpretation(text: str, *, refs: set[str]) -> dict:
    """§7.2's three steps for an interpret run's output: JSON shape, item
    structure, citation existence. Extra keys (action, levels) are dropped —
    an interpretation never carries a trade instruction."""
    try:
        payload = _loads(text)
    except (json.JSONDecodeError, ValueError):
        raise Rejected('malformed') from None
    if not isinstance(payload, dict):
        raise Rejected('malformed')

    def well_formed(items: Any) -> bool:
        return isinstance(items, list) and all(
            isinstance(item, dict) and isinstance(item.get('text'), str)
            and isinstance(item.get('refs'), list) and item['refs']
            for item in items)

    if not isinstance(payload.get('summary'), str)             or not well_formed(payload.get('support'))             or not well_formed(payload.get('contra'))             or not well_formed(payload.get('conflicts')):
        raise Rejected('malformed')
    for key in ('support', 'contra', 'conflicts'):
        for item in payload[key]:
            if any(ref not in refs for ref in item['refs']):
                raise Rejected('invalid_citation')
    return {'summary': payload['summary'],
            **{key: [{'text': item['text'], 'refs': item['refs']} for item in payload[key]]
               for key in ('support', 'contra', 'conflicts')}}
