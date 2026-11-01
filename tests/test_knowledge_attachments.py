"""Cross-language guard for the v2.6 concept attachments (spec §6.2, §14.1).

The front end names concept ids in `web/src/lib/knowledge.ts` (the ATTACH table, `familyConcept`, `ruleConcept`,
`derivationStepConcept`); the back end names them in `reasoning`, `run_overview` and `instrument_view`. All of them
must exist in the real catalog `docs/knowledge`, and the Chinese rule names the two languages carry must agree.
Offline: reads source files and the committed cards, no database."""
from investment_assistant.data import families as data_families
import re
from pathlib import Path

import investment_assistant.workflow.targets as families
import investment_assistant.views.instrument as instrument_view
import investment_assistant.core.texts as labels
import investment_assistant.views.reasoning as reasoning
import investment_assistant.views.runs.overview as run_overview
from investment_assistant.derivation import chain as derivation
from investment_assistant.core.knowledge import DEFAULT_ROOT, ID_BODY, ID_RE, load

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_TS = ROOT / 'web' / 'src' / 'lib' / 'knowledge.ts'
WORKBENCH_TS = ROOT / 'web' / 'src' / 'lib' / 'workbench.ts'
DERIVATION_PY = ROOT / 'investment_assistant' / 'derivation' / 'chain.py'
INSTRUMENT_VIEW_PY = ROOT / 'investment_assistant' / 'views' / 'instrument.py'

QUOTED_ID = re.compile(rf"""['"`]({ID_BODY})['"`]""")


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


def test_frontend_step_table_matches_python_rule():
    text = KNOWLEDGE_TS.read_text(encoding='utf-8')
    body = re.search(r'const STEP_CONCEPT[^=]*=\s*\{(.*?)\n\}', text, re.S).group(1)
    table = {k.strip("'"): v for k, v in re.findall(r"('?[\w.]+'?):\s*'([a-z]+\.[a-z0-9_]+)'", body)}
    assert table == EXACT_STEP_CONCEPT
    fn = re.search(r'export function derivationStepConcept.*?\n\}', text, re.S).group(0)
    prefixes = dict(re.findall(r"key\.startsWith\('([a-z_]+)'\)\) return '([a-z]+\.[a-z0-9_]+)'", fn))
    assert prefixes == {'tr_': 'indicator.atr14', 'earnings': 'plan.earnings', 'cap_': 'plan.sizing'}


# ---- front end attach ids ----

def test_frontend_attach_ids_exist():
    text = KNOWLEDGE_TS.read_text(encoding='utf-8')
    literal_ids = set(QUOTED_ID.findall(text))
    assert len(literal_ids) > 40, 'the ATTACH table should name dozens of concepts'
    # Ids the helpers build at runtime.
    built = {f'family.{family.id}' for family in data_families.FAMILIES}
    built |= {f'rule.{rule_id}' for rule_id in labels.RULE_TEXT}
    built |= {c for c in (step_concept(k) for k in _derivation_step_keys()) if c}
    assert 'familyConcept' in text and 'ruleConcept' in text and 'derivationStepConcept' in text
    concepts = _concepts()
    missing = sorted(i for i in literal_ids | built if i not in concepts)
    assert not missing, f'attached concepts without a card: {missing}'
    assert all(ID_RE.match(i) for i in literal_ids)


def test_frontend_rule_labels_match_backend():
    text = WORKBENCH_TS.read_text(encoding='utf-8')
    body = re.search(r'export const RULE_LABELS[^=]*=\s*\{(.*?)\n\}', text, re.S).group(1)
    body = re.sub(r'//[^\n]*', '', body)
    pairs = dict(re.findall(r"([a-z_0-9]+):\s*'([^']*)'", body))
    assert pairs == labels.RULE_TEXT


# ---- back end concept ids ----

def test_reasoning_and_overview_concepts_exist():
    concepts = _concepts()
    used: set[str] = set()
    for tool, row in reasoning.TOOL_CONCEPTS.items():
        used.add(row['concept'])
        used |= set(row['fields'].values())
    for stage_concepts in run_overview.STAGE_CONCEPTS.values():
        used |= set(stage_concepts)
    used |= {concept for _key, _label, concept in reasoning.LEVEL_CONCEPTS}
    # Every id written out in instrument_view (focus concepts) and the key-data concepts it passes through.
    used |= set(QUOTED_ID.findall(INSTRUMENT_VIEW_PY.read_text(encoding='utf-8')))
    used |= set(instrument_view.reasoning.TOOL_CONCEPTS['get_indicators']['fields'].values())
    assert len(used) > 30
    missing = sorted(i for i in used if i not in concepts)
    assert not missing, f'concepts used by the back end without a card: {missing}'


# ---- v2.7 attach groups (spec §14) ----

V27_GROUPS = ('retroCard', 'myKnowledge', 'discussion', 'weeklyLearning')


def test_v27_attach_groups_exist_in_catalog():
    text = KNOWLEDGE_TS.read_text(encoding='utf-8')
    concepts = _concepts()
    ids = set()
    for group in V27_GROUPS:
        match = re.search(rf'\n  {group}: \{{(.*?)\n  \}},', text, re.S)
        assert match, f'ATTACH group {group} not found'
        group_ids = QUOTED_ID.findall(match.group(1))
        assert group_ids, f'ATTACH group {group} names no concept'
        ids |= set(group_ids)
    # The seven cards v2.7 adds (spec §14) are all attached somewhere.
    assert {'review.retro_card', 'review.retro_end', 'system.my_knowledge', 'review.knowledge_performance',
            'agent.knowledge_citation', 'system.discussion', 'review.weekly_learning'} <= ids
    missing = sorted(i for i in ids if i not in concepts)
    assert not missing, f'v2.7 attached concepts without a card: {missing}'
