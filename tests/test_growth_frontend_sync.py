"""Cross-language guard for the v2.7 growth loop (spec §4.1, §4.3, §4.6, §6.4, §11 补充说明).

The front end keeps its own copies of the retro texts (`web/src/lib/growth.ts`), the Agent reason texts
(`web/src/lib/workbench.ts`) and the uuid5 derivation of 「存为补充信息」 request ids (`growth.ts` + the vector in
`growth-fixtures.ts`). Offline: reads source files, no database."""
import re

import pytest
from pathlib import Path

from investment_assistant.analysis import agent

from investment_assistant.review import discussion

from investment_assistant.review import retro

ROOT = Path(__file__).resolve().parents[1]
GROWTH_TS = ROOT / 'web' / 'src' / 'lib' / 'growth.ts'
WORKBENCH_TS = ROOT / 'web' / 'src' / 'lib' / 'workbench.ts'
FIXTURES_TS = ROOT / 'web' / 'src' / 'lib' / 'growth-fixtures.ts'

# The six reasons v2.7 adds (spec §6.4).
NEW_REASONS = ('retro_malformed', 'lesson_invalid', 'answer_malformed', 'migration_027_missing', 'discussion_busy',
               'draft_exists')


def _const_body(text: str, name: str) -> str:
    match = re.search(rf'export const {name}\b[^=]*=\s*\{{(.*?)\n?\}}(?: as const)?\n', text, re.S)
    assert match, f'{name} not found'
    return match.group(1)


def _ts_table(text: str, name: str) -> dict[str, str]:
    body = re.sub(r'//[^\n]*', '', _const_body(text, name))
    return dict(re.findall(r"([A-Za-z_0-9]+):\s*'([^']*)'", body))


def test_end_and_status_texts_match():
    text = GROWTH_TS.read_text(encoding='utf-8')
    assert _ts_table(text, 'END_TEXT') == retro.END_TEXT
    assert _ts_table(text, 'EXIT_BASIS_TEXT') == retro.EXIT_BASIS_TEXT
    assert _ts_table(text, 'CARD_STATUS_TEXT') == retro.CARD_STATUS_TEXT
    assert _ts_table(text, 'DECISION_TEXT') == retro.DECISION_TEXT
    assert _ts_table(text, 'VERSION_BASIS_TEXT') == retro.BASIS_TEXT
    assert set(retro.END_TEXT) == set(retro.END_KINDS)
    assert set(retro.CARD_STATUS_TEXT) == set(retro.CARD_STATUSES)


@pytest.mark.xfail(strict=False, reason='Task 5 front end (v2.12-v2.15 vocabulary) not merged yet; Task 7 removes')
def test_agent_reason_text_matches():
    text = WORKBENCH_TS.read_text(encoding='utf-8')
    frontend = _ts_table(text, 'AGENT_REASON_TEXT')
    # The front end may know more reasons than the back end, never fewer, and shared keys read the same.
    assert set(agent.REASON_TEXT) <= set(frontend)
    assert {k: frontend[k] for k in agent.REASON_TEXT} == agent.REASON_TEXT
    for reason in NEW_REASONS:
        assert reason in agent.REASON_TEXT
        assert frontend[reason] == agent.REASON_TEXT[reason]
    assert frontend['draft_exists'] == '这张卡已有成功的草稿'


def test_user_input_request_id_vector():
    text = FIXTURES_TS.read_text(encoding='utf-8')
    body = _const_body(text, 'USER_INPUT_REQUEST_ID_VECTOR')
    vector = dict(re.findall(r"(\w+):\s*'([0-9a-f-]+)'", body))
    assert set(vector) == {'turn_request_id', 'user_input_request_id'}
    assert discussion.user_input_request_id(vector['turn_request_id']) == vector['user_input_request_id']
