"""v2.8 removed the Zhipu model layer (spec §9): no tracked text file may still name it, except the allow-list below.

Offline: reads the files ``git ls-files`` lists, no database, no network."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r'zhipu|bigmodel|ZHIPU_API_KEY|glm-5', re.IGNORECASE)
THIS_FILE = 'tests/test_no_zhipu_residue.py'
SELF_NAME = 'test_no_zhipu_residue'     # frozen docs may name this guard; the name alone is not residue
# Frozen design documents of this release (dated 2026-10-29, but written about the removal itself).
RELEASE_DOCS = ('docs/specs/2026-10-29-v2.8-daily-workflow-design.md', 'docs/plans/2026-10-29-v2.8-daily-workflow.md')
# The v3 designs name the model providers configured in the user's Pi, this one among them: Pi replaces
# the model layer, and which provider it talks to is a user setting (v3 spec §7 「模型设置」), not code in
# this repository. The v3.1 design does the same (its implementation-method line names the local model).
V3_DOCS = ('docs/specs/2026-11-05-agent-first-rebuild-design.md',
           'docs/specs/2026-11-06-v3.1-plan-line-account-and-web-design.md')
DATED = re.compile(r'^docs/(?:specs|plans)/(\d{4}-\d{2}-\d{2})-')


def _allowed(path: str) -> bool:
    """Spec §9 「允许残留」, verbatim."""
    if path in ('CHANGELOG.md', 'docs/roadmap/roadmap.md', 'docs/roadmap/v2.8-runtime-feasibility.md', THIS_FILE):
        return True
    if path in RELEASE_DOCS or path in V3_DOCS:
        return True
    if path.startswith('docs/archive/'):
        return True
    if re.fullmatch(r'docs/roadmap/v2\.[^/]*-acceptance\.md', path):
        return True
    match = DATED.match(path)
    return bool(match and match.group(1) < '2026-10-29')        # frozen specs / plans before this release


def _tracked() -> list[str]:
    out = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, check=True, capture_output=True).stdout
    return [name for name in out.decode('utf-8').split('\0') if name]


def _text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None                         # listed but deleted in the working tree
    if b'\0' in data:
        return None                         # binary
    return data.decode('utf-8', errors='replace')


def _residue(line: str) -> bool:
    return bool(PATTERN.search(line.replace(SELF_NAME, '')))


def test_no_zhipu_residue():
    hits: list[str] = []
    for name in _tracked():
        if _allowed(name):
            continue
        text = _text(ROOT / name)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if _residue(line):
                hits.append(f'{name}:{number}: {line.strip()[:120]}')
    assert not hits, 'Zhipu residue outside the spec §9 allow-list:\n' + '\n'.join(hits)


def test_allow_list_is_exactly_the_spec_list():
    for name in ('CHANGELOG.md', 'docs/roadmap/roadmap.md', 'docs/roadmap/v2.7-acceptance.md',
                 'docs/roadmap/v2.8-runtime-feasibility.md', 'docs/archive/anything.md',
                 'docs/specs/2026-10-24-p3-agent-track-design.md', 'docs/plans/2026-10-28-v2.7-growth-loop.md',
                 'docs/specs/2026-10-29-v2.8-daily-workflow-design.md', 'docs/plans/2026-10-29-v2.8-daily-workflow.md',
                 'docs/specs/2026-11-05-agent-first-rebuild-design.md',
                 'docs/specs/2026-11-06-v3.1-plan-line-account-and-web-design.md'):
        assert _allowed(name), name
    for name in ('README.md', 'docs/features/agent-track.md', 'docs/architecture.md', 'docs/knowledge/principles.md',
                 'docs/operations/local-service.md', 'investment_assistant/core/llm/model.py',
                 'tests/test_workbench_service.py', 'web/src/routes/Today.test.ts',
                 'docs/specs/2026-10-29-another-topic-design.md', 'docs/roadmap/v2.8-acceptance-notes.md'):
        assert not _allowed(name), name


def test_pattern_is_case_insensitive():
    for sample in ('Zhipu', 'ZHIPU_API_KEY', 'open.BigModel.cn', 'GLM-5.3'):
        assert PATTERN.search(sample), sample


def test_guard_file_name_is_not_residue():
    assert not _residue('`tests/test_no_zhipu_residue.py` guards the removal')
    assert _residue('tests/test_no_zhipu_residue.py still calls the Zhipu API')
