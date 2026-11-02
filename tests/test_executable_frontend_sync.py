"""Cross-language guard for v2.9 executable plans (spec §5.2, §5.5, §6.2, §6.4, §7.3, §10, §21.1 R3/R6/R10, §21.4).

The front end keeps its own copies of the backend wordings in `web/src/lib/workbench.ts`: the opening-case sentences,
the instruction and execution texts, the five new void reasons, the 409 / 410 / 400 sentences and the labels.
Offline: reads source files and imports the pure modules, no database."""
from investment_assistant.execution import trades as execution_trades
import inspect
import re

from pathlib import Path

from investment_assistant.api.routes import workbench as workbench_routes
from investment_assistant.notify import templates
from investment_assistant.analysis import agent
import investment_assistant.workflow.nodes.review as plan_review
from investment_assistant.review import retro
from investment_assistant.review import retro_cards
import investment_assistant.core.tx as service
from investment_assistant.execution import timeline
from investment_assistant.execution import entry_rules
from investment_assistant.execution import adoption as execution
from investment_assistant.execution import ledger

ROOT = Path(__file__).resolve().parents[1]
WORKBENCH_TS = ROOT / 'web' / 'src' / 'lib' / 'workbench.ts'
GROWTH_TS = ROOT / 'web' / 'src' / 'lib' / 'growth.ts'

# The five void reasons v2.9 adds (spec §5.5 + §21.4).
NEW_REASONS = ('invalid_entry_type', 'invalid_max_hold', 'stop_lowered', 'stop_above_close', 'active_chain')
# The two new 409 codes of §21.4, plus the R6 stale one.
NEW_CONFLICTS = ('chain_exiting', 'plan_after_fill')


def _ts() -> str:
    return WORKBENCH_TS.read_text(encoding='utf-8')


def _const_body(text: str, name: str) -> str:
    match = re.search(rf'export const {name}\b[^=]*=\s*\{{(.*?)\n?\}}(?: as const)?\n', text, re.S)
    assert match, f'{name} not found'
    return match.group(1)


def _ts_table(text: str, name: str) -> dict[str, str]:
    body = re.sub(r'//[^\n]*', '', _const_body(text, name))
    return dict(re.findall(r"([A-Za-z_0-9]+):\s*'([^']*)'", body))


def _ts_string(text: str, name: str) -> str:
    match = re.search(rf"export const {name}\b[^=]*=\s*'([^']*)'", text)
    assert match, f'{name} not found'
    return match.group(1)


def test_new_void_reasons_match():
    frontend = _ts_table(_ts(), 'AGENT_REASON_TEXT')
    for reason in NEW_REASONS:
        assert reason in agent.REASON_TEXT, reason
        assert frontend[reason] == agent.REASON_TEXT[reason], reason
    # shared keys read the same all over; the front end may know more reasons than the back end, never fewer
    assert {k: frontend[k] for k in agent.REASON_TEXT} == agent.REASON_TEXT
    assert agent.REASON_TEXT['stop_above_close'] == '持仓期止损不能高于最新收盘'      # R3, verbatim
    assert agent.REASON_TEXT['active_chain'] == '已有进行中的计划链，这次触发已并入'    # §5.5, verbatim


def test_opening_case_texts_match():
    text = _ts()
    assert _ts_table(text, 'ENTRY_TYPE_TEXT') == entry_rules.ENTRY_TYPE_TEXT
    assert _ts_table(text, 'OPEN_CASE_TEXT') == entry_rules.OPEN_CASE_TEXT
    assert set(entry_rules.ENTRY_TYPE_TEXT) == set(entry_rules.ENTRY_TYPES)
    assert set(entry_rules.OPEN_CASE_TEXT) == set(entry_rules.OPEN_CASES)
    # nested table: compare the ordered (key, sentence) tokens of both sides
    body = re.sub(r'//[^\n]*', '', _const_body(text, 'OPEN_SCENARIO_TEXT'))
    tokens = re.findall(r"([A-Za-z_]+):\s*(?:'([^']*)'|\{)", body)
    expected: list[tuple[str, str]] = []
    for case, value in entry_rules.TEXT.items():
        if isinstance(value, dict):
            expected.append((case, ''))
            expected.extend((entry_type, sentence) for entry_type, sentence in value.items())
        else:
            expected.append((case, value))
    assert tokens == expected


def test_instruction_and_exit_reason_texts_match():
    text = _ts()
    assert _ts_table(text, 'INSTRUCTION_TEXT') == timeline.INSTRUCTION_TEXT
    assert _ts_table(text, 'EXIT_REASON_TEXT') == timeline.EXIT_REASON_TEXT
    assert set(timeline.INSTRUCTION_TEXT) == {'hold', 'sell_half', 'exit', 'no_plan'}
    assert set(timeline.EXIT_REASON_TEXT) == set(timeline.EXIT_REASONS)


def test_execution_texts_match():
    assert _ts_table(_ts(), 'EXECUTION_TEXT') == execution.EXECUTION_TEXT


def test_conflict_texts_match():
    text = _ts()
    frontend = _ts_table(text, 'CONFLICT_TEXT')
    assert set(frontend) == {'plan_stale', 'plan_after_fill', 'chain_exiting', 'active_chain'}
    for code in NEW_CONFLICTS:
        assert code in frontend
    # R10 chain_exiting and the §4.7 active_chain sentence live in the routes module
    assert frontend['chain_exiting'] == workbench_routes.CHAIN_EXITING_TEXT == '这条链已发出退出指令，不再复查'
    assert frontend['active_chain'] == workbench_routes.ACTIVE_CHAIN_TEXT
    # R6: plan_after_fill is a constant of the ledger; plan_stale is an f-string there with the head's version number
    assert frontend['plan_after_fill'] == ledger.PLAN_AFTER_FILL_TEXT
    ledger_source = inspect.getsource(ledger)
    stale = frontend['plan_stale'].replace('{n}', "{head['version_no']}")
    assert f'f"plan_stale: {stale}"' in ledger_source
    # the backend 409 body is `<code>: <sentence>`; the page shows the sentence
    assert "f'plan_after_fill: {PLAN_AFTER_FILL_TEXT}'" in ledger_source
    assert 'chain_exiting' in inspect.getsource(plan_review)


def test_retired_endpoint_and_link_sentences_match():
    text = _ts()
    sentence = _ts_string(text, 'DECISIONS_RETIRED_SENTENCE')
    assert execution_trades.DECISIONS_RETIRED == f'decisions_retired: {sentence}'
    assert re.search(r'DECISIONS_RETIRED_TEXT\s*=\s*`decisions_retired: \$\{DECISIONS_RETIRED_SENTENCE\}`', text)
    assert _ts_string(text, 'DECISION_LINK_RETIRED_TEXT') == ledger.DECISION_LINK_RETIRED.split(': ', 1)[1]
    assert ledger.DECISION_LINK_RETIRED.startswith('decision_link_retired: ')
    assert _ts_string(text, 'MIGRATION_029_TEXT') in inspect.getsource(plan_review)


def test_small_texts_and_limits_match():
    text = _ts()
    assert _ts_string(text, 'STALE_HOLDING_TEXT') in inspect.getsource(templates)      # R18
    assert int(re.search(r'export const NOTE_MAX_CHARS = (\d+)', text).group(1)) == ledger.NOTE_MAX_CHARS == execution.NOTE_MAX_CHARS
    assert _ts_table(text, 'STOP_MODE_TEXT') == {'initial': '初始', 'trailing': '移动'}      # R15: initial / trailing only


def test_first_version_calcs_cover_review_8_and_monthly_4():
    # Task 4 review M12: the plan-kind label of the new snapshots counts each chain's first version
    match = re.search(r'const FIRST_VERSION_CALCS = new Set\(\[([^\]]*)\]\)', _ts())
    assert match
    calcs = set(re.findall(r"'([^']+)'", match.group(1)))
    assert {'review-8', 'monthly-4'} <= calcs


def _interface_keys(text: str, name: str) -> dict[str, set[str]]:
    """The keys of `export interface <name> {...}`: `{key: keys of its inline object type}` (empty for a scalar)."""
    start = text.index(f'export interface {name} {{') + len(f'export interface {name} ')
    depth, end = 0, start
    for i in range(start, len(text)):
        depth += {'{': 1, '}': -1}.get(text[i], 0)
        if depth == 0:
            end = i
            break
    body = re.sub(r'//[^\n]*|/\*.*?\*/', '', text[start + 1:end], flags=re.S)
    nested: dict[str, set[str]] = {}

    def keep(match: re.Match) -> str:                   # `key?: { a: 1; b?: 2 }` -> `key?: OBJ`, remembering a and b
        nested[match.group(1)] = set(re.findall(r'(\w+)\??:', match.group(3)))
        return f'{match.group(1)}{match.group(2)}: OBJ'

    flat = re.sub(r'(\w+)(\??):\s*\{([^{}]*)\}', keep, body)
    return {key: nested.get(key, set()) for key in re.findall(r'(\w+)\??:', flat)}


def test_retro2_fact_keys_the_front_end_reads_exist_in_the_backend():
    """Task-6 review I2: `FactHolding` / `FactExecution` once read keys the backend never wrote (`exit_instruction_date`,
    `sold_date`, `legacy_decision.text`). Every key the front end types read must be one the frozen fact carries."""
    from datetime import date

    text = GROWTH_TS.read_text(encoding='utf-8')
    held = retro.holding_fact([{'session': date(2026, 10, 20), 'held_sessions': 2, 'instruction': 'exit',
                                'exit_reason': 'time_stop', 'max_hold_sessions': 10, 'r_now': 0.5}],
                              sell_date=date(2026, 10, 21))
    holding = _interface_keys(text, 'FactHolding')
    assert set(holding) <= set(held)
    assert holding['exit'] <= set(held['exit'])
    execution_out = retro.execution_fact(buys=[], plans_by_trade={})
    execution_keys = _interface_keys(text, 'FactExecution')
    assert set(execution_keys) <= set(execution_out)
    legacy_keys = set(retro_cards.LEGACY_DECISION_KEYS) | {'text'}
    assert execution_keys['legacy_decision'] <= legacy_keys
    assert execution_keys['first_fill'] <= {'executed_at', 'quantity', 'price'}
    assert execution_keys['notes'] <= {'id', 'body', 'created_at'}
