"""Cross-language guard for the v2.8 daily workflow (spec §5.4, §7.1, §8.8, §12).

The front end keeps its own copies of the Agent reason texts (`web/src/lib/workbench.ts`), the resume refusal texts
and the run-id pattern (`web/src/lib/runs.ts`). Offline: reads source files, no database, no network."""
import re
from pathlib import Path

import pytest

from investment_assistant.analysis import agent

import investment_assistant.workflow.check as derivation

import investment_assistant.views.runs.detail as run_detail

import investment_assistant.core.stages as stages
from investment_assistant.core import features, texts

ROOT = Path(__file__).resolve().parents[1]
WORKBENCH_TS = ROOT / 'web' / 'src' / 'lib' / 'workbench.ts'
RUNS_TS = ROOT / 'web' / 'src' / 'lib' / 'runs.ts'
STATUS_TS = ROOT / 'web' / 'src' / 'lib' / 'ui' / 'domain' / 'status.ts'
MIGRATION_031 = ROOT / 'migrations' / '031_five_layers.sql'
RUN_DETAIL_TEST_TS = ROOT / 'web' / 'src' / 'lib' / 'components' / 'RunDetail.test.ts'
WORKFLOW_PY = ROOT / 'investment_assistant' / 'workflow' / 'graph.py'
RUN_DETAIL_PY = ROOT / 'investment_assistant' / 'views' / 'runs' / 'detail.py'

# Cross-end assertions that read the Task 5 front end (v2.12-v2.15 vocabulary). They are expected to fail until Task 5
# is merged; Task 7 removes this marker (the removal list is in the Task 4 report, Segment F).
TASK5 = pytest.mark.xfail(strict=False, reason='Task 5 front end (v2.12-v2.15 vocabulary) not merged yet; Task 7 removes')

# The two reasons v2.8 adds (spec §8.8, verbatim).
NEW_REASONS = {'not_connected': 'ChatGPT 未连接', 'model_unavailable': '所选模型不可用'}


def _const_body(text: str, name: str) -> str:
    match = re.search(rf'export const {name}\b[^=]*=\s*\{{(.*?)\n?\}}(?: as const)?\n', text, re.S)
    assert match, f'{name} not found'
    return match.group(1)


def _ts_table(text: str, name: str) -> dict[str, str]:
    body = re.sub(r'//[^\n]*', '', _const_body(text, name))
    return dict(re.findall(r"([A-Za-z_0-9]+):\s*'([^']*)'", body))


def test_agent_reason_text_backend_vocabulary():
    for reason, text in NEW_REASONS.items():
        assert agent.REASON_TEXT[reason] == text
    # v2.12-v2.15: the researcher, the PM, the optimizer and the brief writer add their rejection codes, and the new
    # nodes add their skip reasons to the run overview's table
    assert {'research_malformed', 'research_refs_invalid', 'pm_malformed', 'proposal_refs_invalid',
            'brief_acceptance_missing'} <= set(agent.REASON_TEXT)
    assert {'feature_disabled', 'not_proposal_day', 'no_evidence'} <= set(texts.RUN_REASON_TEXT)


@TASK5
def test_agent_reason_text_matches():
    frontend = _ts_table(WORKBENCH_TS.read_text(encoding='utf-8'), 'AGENT_REASON_TEXT')
    # The front end may know more reasons than the back end, never fewer, and shared keys read the same.
    assert set(agent.REASON_TEXT) <= set(frontend)
    assert {k: frontend[k] for k in agent.REASON_TEXT} == agent.REASON_TEXT
    for reason, text in NEW_REASONS.items():
        assert frontend[reason] == text


def test_resume_error_codes_match():
    """`RESUME_ERROR_TEXT` has a text for every code the resume refusal can carry: the codes of
    `workflow.ResumeRejected` (not_failed, node_mismatch, resume_window_closed), `daily_running` (the lock is held)
    and the extra `resume.reason` values the detail page can show (`interrupted`, `stage_failed`, `checkpoint_unavailable`)."""
    frontend = _ts_table(RUNS_TS.read_text(encoding='utf-8'), 'RESUME_ERROR_TEXT')
    rejected = set(re.findall(r"ResumeRejected\(\s*'(\w+)'", WORKFLOW_PY.read_text(encoding='utf-8')))
    assert rejected == {'not_failed', 'node_mismatch', 'resume_window_closed'}
    reasons = set(re.findall(r"block\['reason'\] = '(\w+)'", RUN_DETAIL_PY.read_text(encoding='utf-8')))
    reasons |= {'interrupted'}              # assigned through `nothing_to_resume`
    assert reasons == {'daily_running', 'not_failed', 'checkpoint_unavailable', 'resume_window_closed', 'interrupted',
                       'stage_failed'}
    assert set(frontend) == rejected | reasons | {'daily_running'}
    # The texts of the codes the back end words itself read the same in the front end.
    assert frontend['not_failed'] == '这次运行没有失败的节点'
    assert frontend['node_mismatch'].startswith('只能从失败的节点')
    assert 'resume_window_closed' in frontend and '纽约 17:00' in frontend['resume_window_closed']


STAGES_6 = ('sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'portfolio',
            'accounts', 'push', 'retro', 'propose')


def test_node_ids_match_backend_versions():
    """stages-6 (daily-3) has thirteen nodes; every older version keeps its own list and count."""
    assert stages.STAGES_VERSION == 'stages-6'
    assert stages.STAGE_IDS == STAGES_6
    assert derivation.RUN_STAGE_COUNT[stages.STAGES_VERSION] == len(stages.STAGE_IDS) == 13
    assert stages.STAGE_IDS_BY_VERSION['stages-6'] == STAGES_6
    assert stages.STAGE_IDS_BY_VERSION['stages-5'] == ('sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan',
                                                      'agent', 'review', 'accounts', 'push', 'retro')
    assert derivation.RUN_STAGE_COUNT['stages-5'] == 11
    assert stages.STAGE_IDS_BY_VERSION['stages-4'] == ('sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan',
                                                      'agent', 'review', 'push', 'retro')
    assert derivation.RUN_STAGE_COUNT['stages-4'] == 10


@TASK5
def test_node_ids_match():
    """The detail page takes its nodes (id, title, order) from the API; the front end has no node list of its own
    in `runs.ts`. Its fixture (RunDetail.test.ts) follows the current stages version."""
    runs_ts = RUNS_TS.read_text(encoding='utf-8')
    listed = re.search(r'export const NODE_IDS\b[^=]*=\s*\[(.*?)\]', runs_ts, re.S)
    if listed:                                               # if the front end ever grows a list, it must match
        assert tuple(re.findall(r"'(\w+)'", listed.group(1))) == stages.STAGE_IDS
    test_titles = _ts_table(RUN_DETAIL_TEST_TS.read_text(encoding='utf-8').replace('const TITLES', 'export const TITLES'),
                            'TITLES')
    assert tuple(test_titles) == stages.STAGE_IDS


def test_run_id_regex_matches():
    ts = re.search(r'export const RUN_ID_RE\s*=\s*/(.*)/\s*\n', RUNS_TS.read_text(encoding='utf-8'))
    assert ts, 'RUN_ID_RE not found'
    frontend = re.compile(ts.group(1))
    backend = run_detail.RUN_ID_RE
    assert frontend.pattern == backend.pattern
    samples = ['daily-20261030033000-1a2b3c4d', 'daily_resume-20261030050102-9f8e7d6c', 'workbench-20261027063000-00ff00ff',
               'us-research-20261030033000-1a2b3c4d',       # spec §7.1 lists these ids, the pattern excludes them (known)
               '', 'daily', 'daily-2026-1a2b3c4d', 'Daily-20261030033000-1a2b3c4d', 'daily-20261030033000-1A2B3C4D',
               'daily-20261030033000-1a2b3c4', 'daily-20261030033000-1a2b3c4d/x', 'daily-20261030033000-1a2b3c4de',
               '../daily-20261030033000-1a2b3c4d', 'daily-20261030033000-1a2b3c4d?x=1']
    for sample in samples:
        assert bool(frontend.match(sample)) == bool(backend.match(sample)), sample
    assert backend.match('daily-20261030033000-1a2b3c4d') and not backend.match('us-research-20261030033000-1a2b3c4d')


# ---------------------------------------------------------------- v2.12-v2.15 vocabulary (spec §12.1, §13)

def _sql_checks(table: str, column: str = 'status') -> set[str]:
    block = re.search(rf'CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);', MIGRATION_031.read_text(encoding='utf-8'), re.S)
    assert block, table
    check = re.search(rf"\b{column}\s+TEXT[^\n]*?CHECK\s*\({column} IN \(([^)]*)\)\)", block.group(1), re.S)
    assert check, f'{table}.{column} CHECK missing'
    return set(re.findall(r"'([^']+)'", check.group(1)))


STATUS_TABLES = ('RESEARCH_STATUS_TEXT', 'PROPOSAL_STATUS_TEXT', 'PORTFOLIO_VERDICT_TEXT',
                 'DATA_REQUEST_STATUS_TEXT', 'FEATURE_FLAG_TEXT')


def test_status_words_cover_the_migration_domains():
    """The back end owns the words (`core.texts`); they cover exactly the states migration 031 allows."""
    assert set(texts.RESEARCH_STATUS_TEXT) == _sql_checks('research_requests') | _sql_checks('research_versions')
    assert set(texts.PROPOSAL_STATUS_TEXT) == _sql_checks('roadmap_proposals')
    assert set(texts.DATA_REQUEST_STATUS_TEXT) == _sql_checks('data_requests')
    assert set(texts.PORTFOLIO_VERDICT_TEXT) == {'ok', 'blocked', 'deferred'}
    assert set(texts.FEATURE_FLAG_TEXT) == {'enabled', 'disabled'}
    assert features.FLAGS == ('backup_sources', 'company_researcher', 'portfolio_manager', 'optimize_proposals')


@TASK5
def test_status_words_match_frontend():
    text = STATUS_TS.read_text(encoding='utf-8')
    for name in STATUS_TABLES:
        assert _ts_table(text, name) == getattr(texts, name), name


@TASK5
def test_feature_flags_routes_and_research_words_are_named_in_the_frontend():
    """Switch names, the new route prefixes and the researcher's status words appear verbatim in the web sources."""
    sources = '\n'.join(path.read_text(encoding='utf-8') for path in (ROOT / 'web' / 'src').rglob('*')
                        if path.suffix in ('.ts', '.svelte') and '.test.' not in path.name)
    for flag in features.FLAGS:
        assert f"'{flag}'" in sources or f'"{flag}"' in sources, flag
    for route in ('/api/research', '/api/roadmap', '/api/settings/features', '/portfolio-check', '/api/data/indicators'):
        assert route in sources, route
    for status in ('ok', 'rejected', 'error', 'disabled', 'carried'):      # output.research.status (spec §6.1)
        assert f"'{status}'" in sources, status
