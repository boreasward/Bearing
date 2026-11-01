"""Cross-language guard for the v2.8 daily workflow (spec §5.4, §7.1, §8.8, §12).

The front end keeps its own copies of the Agent reason texts (`web/src/lib/workbench.ts`), the resume refusal texts
and the run-id pattern (`web/src/lib/runs.ts`). Offline: reads source files, no database, no network."""
import re
from pathlib import Path

from investment_assistant.workbench import agent, derivation, run_detail, stages

ROOT = Path(__file__).resolve().parents[1]
WORKBENCH_TS = ROOT / 'web' / 'src' / 'lib' / 'workbench.ts'
RUNS_TS = ROOT / 'web' / 'src' / 'lib' / 'runs.ts'
RUN_DETAIL_TEST_TS = ROOT / 'web' / 'src' / 'lib' / 'components' / 'RunDetail.test.ts'
WORKFLOW_PY = ROOT / 'investment_assistant' / 'workbench' / 'workflow.py'
RUN_DETAIL_PY = ROOT / 'investment_assistant' / 'workbench' / 'run_detail.py'

# The two reasons v2.8 adds (spec §8.8, verbatim).
NEW_REASONS = {'not_connected': 'ChatGPT 未连接', 'model_unavailable': '所选模型不可用'}


def _const_body(text: str, name: str) -> str:
    match = re.search(rf'export const {name}\b[^=]*=\s*\{{(.*?)\n?\}}(?: as const)?\n', text, re.S)
    assert match, f'{name} not found'
    return match.group(1)


def _ts_table(text: str, name: str) -> dict[str, str]:
    body = re.sub(r'//[^\n]*', '', _const_body(text, name))
    return dict(re.findall(r"([A-Za-z_0-9]+):\s*'([^']*)'", body))


def test_agent_reason_text_matches():
    frontend = _ts_table(WORKBENCH_TS.read_text(encoding='utf-8'), 'AGENT_REASON_TEXT')
    # The front end may know more reasons than the back end, never fewer, and shared keys read the same.
    assert set(agent.REASON_TEXT) <= set(frontend)
    assert {k: frontend[k] for k in agent.REASON_TEXT} == agent.REASON_TEXT
    for reason, text in NEW_REASONS.items():
        assert agent.REASON_TEXT[reason] == text
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


def test_node_ids_match():
    """The detail page takes its nodes (id, title, order) from the API; the front end has no node list of its own
    in `runs.ts`. Its current fixture follows stages-5; historical stages-4 keeps its ten nodes."""
    assert stages.STAGE_IDS == ('sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review',
                                'accounts', 'push', 'retro')
    assert derivation.RUN_STAGE_COUNT[stages.STAGES_VERSION] == len(stages.STAGE_IDS) == 11
    assert stages.STAGE_IDS_BY_VERSION['stages-4'] == ('sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan',
                                                      'agent', 'review', 'push', 'retro')
    assert derivation.RUN_STAGE_COUNT['stages-4'] == 10
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
