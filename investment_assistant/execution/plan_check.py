"""agent-2 / agent-3 plan, counter and review checks (v2.5 spec §4.1, §4.3; v2.9 spec §5.5). Pure functions.

check_plan runs citations.check_proposal unchanged (refs = this run's calls plus
the user inputs given to it), then the agent-2 gates in order: thesis count,
invalidation conditions, review conditions, and `changes` against the host
diff. The first failing gate names the reason; nothing is salvaged.

Two deliberate readings of the spec:
- Thesis count runs before check_proposal, because check_proposal folds an
  empty thesis list into 'malformed' and §4.4 names 'thesis_count' for it.
- A 'pass' is not an executable plan: only its thesis (count, refs) is checked;
  invalidation / review_when / changes are ignored and come back empty with
  diff None. In a revision the caller turns a pass into an invalidation (D11).

agent-3 (`schema='agent-3'`, v2.9) adds, after check_proposal: `entry_type` (one of three, else
`invalid_entry_type`) and `max_hold_sessions` (an integer 5–20, else `invalid_max_hold`), both required of a buy
(absent → malformed); `held_sessions_reached` may only be a review condition (the host injects the failure
one); `CHANGE_FIELDS` grows by the two new fields; a revision found by a retrigger may keep every model field
(only the host fields `valid_until` / `entry_cap` move, so `changes = []` is allowed, D7). `mode='holding'`
(R3) checks a holding-phase revision: the entry zone is copied from the previous version, the stop may only
rise (`stop_lowered`) and must stay under the review-day close (`stop_above_close`). The default
`schema='agent-2'` keeps every old caller's behaviour exactly.

diff is the one host diff (the timeline module reuses it). It accepts both the
check_plan output (Decimal / date) and the same dict read back from JSON
(strings), and its output is JSON-serializable: prices as '95.00', entry as
'94.00–96.00', thesis as the text sequence, conditions as [{kind, params}] with
every param a canonical string. Condition text and refs are never compared.
"""
from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from investment_assistant.execution import entry_rules
from investment_assistant.execution import conditions
from investment_assistant.execution.citations import Rejected, _loads, check_proposal

SCHEMA_V2 = 'agent-2'
SCHEMA_V3 = 'agent-3'
THESIS_RANGE = (1, 3)
INVALIDATION_RANGE = (1, 5)
REVIEW_WHEN_MAX = 3
AGENT_MAX_HOLD_RANGE = (5, 20)
MAX_HOLD_DEFAULT = 10                      # what an agent-2 version without the field is read as
CHANGE_FIELDS_AGENT2 = ('action', 'entry', 'stop', 'target1', 'thesis', 'invalidation', 'review_when')
CHANGE_FIELDS = ('action', 'entry', 'entry_type', 'stop', 'target1', 'max_hold_sessions', 'thesis',
                 'invalidation', 'review_when')
HOST_FIELDS = ('valid_until', 'entry_cap')   # host-computed; only in timeline.host_diff, never in `changes`
REVIEW_OUTCOMES = ('maintain', 'revise', 'invalidate')
_CENT = Decimal('0.01')
_THESIS_REF = re.compile(r'^T?([1-9])$')


def _payload(text: str) -> Any:
    try:
        return _loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _cited(item_refs: Any, allowed: set[str]) -> bool:
    """A non-empty list whose every entry is a known ref."""
    return (isinstance(item_refs, list) and bool(item_refs)
            and all(isinstance(ref, str) and ref in allowed for ref in item_refs))


# ---------------------------------------------------------------- plan

def _invalidation(raw: Any, *, allowed: set[str], last_close: Decimal, as_of_session: date,
                  schema: str = SCHEMA_V2) -> list[dict]:
    if not isinstance(raw, list) or not INVALIDATION_RANGE[0] <= len(raw) <= INVALIDATION_RANGE[1]:
        raise Rejected('invalid_condition')
    out = []
    for item in raw:
        # held_sessions_reached is the host's own failure condition (D16): the model never writes it there, and
        # agent-2 knows no such kind at all
        if isinstance(item, dict) and item.get('kind') == 'held_sessions_reached':
            raise Rejected('invalid_condition')
        cond = conditions.validate(item, use='invalidation', last_close=last_close, as_of_session=as_of_session)
        if not _cited(cond.get('refs'), allowed):
            raise Rejected('invalid_condition')
        out.append(cond)
    return out


def _review_when(raw: Any, *, last_close: Decimal, as_of_session: date, schema: str = SCHEMA_V2) -> list[dict]:
    if raw is None or raw == []:
        raw = [{'kind': 'next_session', 'params': {}}]
    if not isinstance(raw, list) or len(raw) > REVIEW_WHEN_MAX:
        raise Rejected('invalid_condition')
    out = []
    for item in raw:
        if schema == SCHEMA_V2 and isinstance(item, dict) and item.get('kind') == 'held_sessions_reached':
            raise Rejected('invalid_condition')                 # condition-1 callers: not a registered kind
        cond = conditions.validate(item, use='review', last_close=last_close, as_of_session=as_of_session)
        cond.pop('refs', None)          # review conditions carry no citations; unchecked refs are not kept
        out.append(cond)
    return out


def _changes(raw: Any, *, allowed: set[str]) -> list[dict]:
    """Shape and citations of a revision's `changes`; the field-set match is the caller's."""
    if raw is None or raw == []:
        raise Rejected('changes_mismatch')
    if not isinstance(raw, list):
        raise Rejected('malformed')
    out = []
    for item in raw:
        if (not isinstance(item, dict) or not isinstance(item.get('field'), str)
                or not isinstance(item.get('text'), str)
                or not isinstance(item.get('refs'), list) or not item['refs']):
            raise Rejected('malformed')
        out.append({'field': item['field'], 'text': item['text'], 'refs': item['refs']})
    if any(not _cited(item['refs'], allowed) for item in out):
        raise Rejected('invalid_citation')
    return out


def _exec_fields(payload: dict) -> tuple[str, int]:
    """agent-3's two execution fields of a buy: absent → malformed, a bad value → its own reason."""
    if 'entry_type' not in payload or 'max_hold_sessions' not in payload:
        raise Rejected('malformed')
    entry_type, max_hold = payload['entry_type'], payload['max_hold_sessions']
    if not isinstance(entry_type, str) or entry_type not in entry_rules.ENTRY_TYPES:
        raise Rejected('invalid_entry_type')
    low, high = AGENT_MAX_HOLD_RANGE
    if isinstance(max_hold, bool) or not isinstance(max_hold, int) or not low <= max_hold <= high:
        raise Rejected('invalid_max_hold')
    return entry_type, max_hold


def _holding_levels(out: dict, previous: dict | None, last_close: Decimal) -> None:
    """R3 on a holding-phase buy: the entry zone is the previous version's (the model's entry fields are
    ignored), T1 stays above it, the stop only rises and stays under the review-day close."""
    if previous is None or not isinstance(previous.get('levels'), dict):
        raise ValueError('a holding-phase revision needs the previous version')
    levels, before = out['levels'], previous['levels']
    for key in ('entry_low', 'entry_high'):
        carried = _money(before[key]['value'])
        assert carried is not None                     # a checked predecessor level is always a money string
        levels[key] = {'value': Decimal(carried), 'refs': levels[key]['refs']}
    if not levels['entry_high']['value'] < levels['target1']['value']:
        raise Rejected('level_check')
    carried_stop = _money(before['stop']['value'])
    assert carried_stop is not None
    stop, previous_stop = levels['stop']['value'], Decimal(carried_stop)
    if stop < previous_stop:
        raise Rejected('stop_lowered')
    if stop >= last_close:
        raise Rejected('stop_above_close')


def check_plan(text: str, *, refs: set[str], price_refs: set[str], user_refs: set[str],
               last_close: Decimal, as_of_session: date, previous: dict | None,
               schema: str = SCHEMA_V2, mode: str = 'entry', via: str | None = None,
               research_refs: set[str] | frozenset[str] | None = None) -> dict:
    """§4.1 in full; previous is the prior version's check_plan output (None = first version).

    schema 'agent-3' adds the execution fields and the new gates (module docstring); mode 'holding' applies
    the holding-phase level rules (agent-3 only); via 'retrigger' lets an entry-phase revision renew without
    touching any model field. ``research_refs`` (D38/D42) is the researcher-phase call refs of an agent-4
    FIRST version whose research segment ended ok: at least one thesis must then cite one of them (None —
    the default and every revision — keeps the rule off; a failed/disabled researcher exempts the run)."""
    if schema not in (SCHEMA_V2, SCHEMA_V3):
        raise ValueError(f'unknown plan schema: {schema!r}')
    if mode not in ('entry', 'holding'):
        raise ValueError(f'unknown chain mode: {mode!r}')
    v3 = schema == SCHEMA_V3
    allowed = set(refs) | set(user_refs)
    payload = _payload(text)
    if isinstance(payload, dict) and isinstance(payload.get('thesis'), list) \
            and not THESIS_RANGE[0] <= len(payload['thesis']) <= THESIS_RANGE[1]:
        raise Rejected('thesis_count')
    base = check_proposal(text, refs=allowed, price_refs=price_refs, last_close=last_close,
                          mode='holding' if v3 and mode == 'holding' else 'entry')
    if not isinstance(payload, dict):   # unreachable: check_proposal accepted it, so it parsed to an object
        raise Rejected('malformed')

    out: dict[str, Any] = {
        'action': base['action'],
        'summary': base['summary'],
        'levels': base['levels'],
        'thesis': [{'id': f'T{i}', 'text': item['text'], 'refs': item['refs']}
                   for i, item in enumerate(base['thesis'], start=1)],
        'invalidation': [],
        'review_when': [],
        'changes': [],
        'diff': None,
    }
    if v3:
        out.update(entry_type=None, max_hold_sessions=None)
    if research_refs is not None and not any(set(item['refs']) & research_refs for item in out['thesis']):
        raise Rejected('research_not_cited')
    if out['action'] == 'pass':
        return out

    if v3:
        out['entry_type'], out['max_hold_sessions'] = _exec_fields(payload)
        if mode == 'holding':
            _holding_levels(out, previous, last_close)
    out['invalidation'] = _invalidation(payload.get('invalidation'), allowed=allowed,
                                        last_close=last_close, as_of_session=as_of_session, schema=schema)
    out['review_when'] = _review_when(payload.get('review_when'), last_close=last_close,
                                      as_of_session=as_of_session, schema=schema)

    raw_changes = payload.get('changes')
    if previous is None:
        if raw_changes is not None and raw_changes != []:
            raise Rejected('malformed')
        return out

    host_diff = diff(previous, out, schema=schema)
    if not host_diff:
        if v3 and mode == 'entry' and via == 'retrigger':
            if raw_changes is not None and raw_changes != []:
                raise Rejected('changes_mismatch')       # nothing changed, so there is nothing to explain
            out['diff'] = []                              # renewal: only the host fields move (D7)
            return out
        raise Rejected('no_change')
    changes = _changes(raw_changes, allowed=allowed)
    if {item['field'] for item in changes} != {item['field'] for item in host_diff}:
        raise Rejected('changes_mismatch')
    out['changes'] = changes
    out['diff'] = host_diff
    return out


# ---------------------------------------------------------------- diff

def _money(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return f'{number.quantize(_CENT, rounding="ROUND_HALF_UP"):.2f}'


def _level(plan: dict, key: str) -> str | None:
    levels = plan.get('levels')
    if not isinstance(levels, dict) or not isinstance(levels.get(key), dict):
        return None
    return _money(levels[key].get('value'))


def _entry(plan: dict) -> str | None:
    low, high = _level(plan, 'entry_low'), _level(plan, 'entry_high')
    return None if low is None and high is None else f'{low}–{high}'


def _canon_param(name: str, value: Any) -> str:
    if name == 'price':
        return _money(value) or str(value)
    if name == 'date':
        if isinstance(value, date):
            return value.isoformat()
        return str(value)[:10] if isinstance(value, str) else str(value)
    if name == 'value' and not isinstance(value, bool) and isinstance(value, (int, float, Decimal, str)):
        try:
            return repr(float(value))
        except ValueError:
            return str(value)
    return str(value)


def _conditions(plan: dict, key: str) -> list[dict]:
    items = plan.get(key) or []
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        params = item.get('params') or {}
        out.append({'kind': item.get('kind'),
                    'params': {name: _canon_param(name, params[name]) for name in sorted(params)}})
    return out


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _thesis_texts(plan: dict) -> list[str]:
    return [item['text'] for item in plan.get('thesis') or [] if isinstance(item, dict) and 'text' in item]


def diff(previous: dict, new: dict, *, schema: str = SCHEMA_V2) -> list[dict]:
    """[{field, before, after}] for each changed field, in CHANGE_FIELDS order (§4.1).

    schema 'agent-3' compares the two execution fields as well. A version without them (agent-2) reads as
    `zone_limit` and 10 sessions, so an agent-2 predecessor never produces a spurious difference. The host
    fields (HOST_FIELDS) are not compared here; `timeline.host_diff` adds them."""
    extract = {
        'action': lambda plan: plan.get('action'),
        'entry': _entry,
        'entry_type': lambda plan: plan.get('entry_type') or entry_rules.DEFAULT_ENTRY_TYPE,
        'stop': lambda plan: _level(plan, 'stop'),
        'target1': lambda plan: _level(plan, 'target1'),
        'max_hold_sessions': lambda plan: plan.get('max_hold_sessions') or MAX_HOLD_DEFAULT,
        'thesis': _thesis_texts,
        'invalidation': lambda plan: _conditions(plan, 'invalidation'),
        'review_when': lambda plan: _conditions(plan, 'review_when'),
    }
    out = []
    for field in (CHANGE_FIELDS if schema == SCHEMA_V3 else CHANGE_FIELDS_AGENT2):
        before, after = extract[field](previous), extract[field](new)
        if _canon(before) != _canon(after):
            out.append({'field': field, 'before': before, 'after': after})
    return out


# ---------------------------------------------------------------- counter

def _thesis_no(value: Any, thesis_count: int) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and (match := _THESIS_REF.match(value.strip())):
        value = int(match.group(1))
    if isinstance(value, int) and 1 <= value <= thesis_count:
        return value
    return None


def filter_counter_v2(text: str, *, refs: set[str], thesis_count: int) -> dict:
    """Per-thesis counters and risks; bad entries drop out and count, prices and actions are ignored."""
    every = list(range(1, thesis_count + 1))
    payload = _payload(text)
    if (not isinstance(payload, dict) or not isinstance(payload.get('counter'), list)
            or not isinstance(payload.get('risks', []), list)):
        return {'status': 'malformed', 'counter': [], 'risks': [], 'dropped': 0, 'uncountered': every}
    counter: list[dict] = []
    risks: list[dict] = []
    dropped = 0
    for item in payload['counter']:
        number = _thesis_no(item.get('thesis'), thesis_count) if isinstance(item, dict) else None
        if number is not None and isinstance(item.get('text'), str) and _cited(item.get('refs'), refs):
            counter.append({'thesis': number, 'text': item['text'], 'refs': item['refs']})
        else:
            dropped += 1
    for item in payload.get('risks', []):
        if isinstance(item, dict) and isinstance(item.get('text'), str) and _cited(item.get('refs'), refs):
            risks.append({'text': item['text'], 'refs': item['refs']})
        else:
            dropped += 1
    countered = {item['thesis'] for item in counter}
    return {'status': 'ok', 'counter': counter, 'risks': risks, 'dropped': dropped,
            'uncountered': [n for n in every if n not in countered]}


# ---------------------------------------------------------------- review verdict

def check_review(text: str, *, refs: set[str]) -> dict:
    """agent-review-1's single verdict; revise / invalidate must cite, maintain may not."""
    payload = _payload(text)
    if not isinstance(payload, dict) or payload.get('outcome') not in REVIEW_OUTCOMES:
        raise Rejected('review_malformed')
    reason = payload.get('reason')
    if not isinstance(reason, str) or not reason.strip():
        raise Rejected('review_malformed')
    cited = payload.get('refs', [])
    if not isinstance(cited, list):
        raise Rejected('review_malformed')
    if cited and not _cited(cited, refs):
        raise Rejected('invalid_citation')
    if payload['outcome'] != 'maintain' and not cited:
        raise Rejected('invalid_citation')
    return {'outcome': payload['outcome'], 'reason': reason, 'refs': cited}
