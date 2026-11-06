"""Guard for the concept attachments of the derivation steps (spec §6.2, §14.1).

Every step key the derivation writes must map to a concept that exists in the real catalog `docs/knowledge`, or be on
the spec's explicit 「其余不挂」 list. Offline: reads source files and the committed cards, no database."""
import re
from pathlib import Path

from investment_assistant.derivation import chain as derivation
from investment_assistant.core.knowledge import DEFAULT_ROOT, load

ROOT = Path(__file__).resolve().parents[1]
DERIVATION_PY = ROOT / 'investment_assistant' / 'derivation' / 'chain.py'


def _concepts() -> dict:
    return load(DEFAULT_ROOT).concepts


# ---- derivation step keys (spec §6.2, row 「逐项计算的项目列」) ----

EXACT_STEP_CONCEPT = {
    'atr': 'indicator.atr14', 'ref_price': 'plan.ref_price',
    'entry_low': 'plan.entry_zone', 'entry_high': 'plan.entry_zone',
    'stop': 'plan.stop', 'target1': 'plan.target1', 'trail_atr_multiple': 'plan.trailing_stop',
    'valid_from': 'plan.validity', 'valid_until': 'plan.validity', 'context_flags': 'plan.context_flags',
    'per_share_risk': 'plan.sizing', 'risk_amount': 'plan.sizing', 'equity': 'plan.sizing',
    'ticker_value': 'plan.sizing', 'total_value': 'plan.sizing', 'shares': 'plan.sizing',
    'applied_cap': 'plan.sizing', 'sizing.status': 'plan.sizing',
    'invalid_reasons': 'plan.status', 'warnings': 'plan.status', 'status': 'plan.status',
    'levels_ordered': 'plan.status',
    'entry_type': 'plan.entry_type', 'max_hold_sessions': 'plan.max_hold',          # v2.9 plan-2 / agent-3 derivation
}
# 「其余不挂」: the steps the spec leaves without a concept.
UNATTACHED_STEPS = {'as_of_session', 'chain_entry_cap', 'entry_mid', 'version_no'}


def step_concept(key: str) -> str | None:
    """Python re-implementation of the §6.2 rule (prefix rules first, then the exact table)."""
    if key.startswith('tr_'):
        return 'indicator.atr14'
    if key.startswith('earnings'):
        return 'plan.earnings'
    if key.startswith('cap_'):
        return 'plan.sizing'
    return EXACT_STEP_CONCEPT.get(key)


def _derivation_step_keys() -> set[str]:
    text = DERIVATION_PY.read_text(encoding='utf-8')
    keys = {re.sub(r'\{[^}]*\}', '0', k) for k in re.findall(r"_step\(\s*f?'([^']+)'", text)}
    # Level steps pass `key` as a variable: they are LEVEL_LABELS' keys.
    return keys | set(derivation.LEVEL_LABELS)


def test_derivation_step_keys_covered():
    keys = _derivation_step_keys()
    assert {'atr', 'ref_price', 'tr_0', 'earnings_flag', 'cap_risk', 'shares', 'stop', 'entry_low'} <= keys
    concepts = _concepts()
    unmapped = set()
    for key in keys:
        concept = step_concept(key)
        if concept is None:
            unmapped.add(key)
        else:
            assert concept in concepts, f'step {key!r} maps to unknown concept {concept!r}'
    # A new step key must be a deliberate decision: mapped by the rule or added to the spec's 「其余不挂」 set.
    assert unmapped == UNATTACHED_STEPS
