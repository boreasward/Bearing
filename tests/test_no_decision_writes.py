"""Static guard (v2.9 D24, spec §6.4, Review Focus 6): production code never writes `signal_decisions`.

Since v2.9 adoption comes from the fills; the table keeps its old rows and is only read. The one INSERT left is
`repository._insert_decision_for_tests` (a fixture helper for tests that need an old-style decision), and nothing in
the package calls it. Offline: parses the source files, no database."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ('investment_assistant', 'scripts')
WRITE = re.compile(r'\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+(?:ONLY\s+)?signal_decisions\b', re.I)
FIXTURE_HELPER = '_insert_decision_for_tests'
HELPER_FILE = ROOT / 'investment_assistant' / 'workbench' / 'repository.py'


def _python_files():
    for directory in SOURCE_DIRS:
        yield from sorted((ROOT / directory).rglob('*.py'))


def _strings(tree: ast.AST):
    """Every string literal of a module, f-string pieces included, with the line it starts on."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def _enclosing_function(tree: ast.AST, line: int) -> str | None:
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno <= line <= (node.end_lineno or line):
            if best is None or node.lineno >= best.lineno:
                best = node
    return best.name if best else None


def test_signal_decisions_has_only_selects_in_production_code():
    offences = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for line, text in _strings(tree):
            if not WRITE.search(text):
                continue
            allowed = path == HELPER_FILE and _enclosing_function(tree, line) == FIXTURE_HELPER
            if not allowed:
                offences.append(f'{path.relative_to(ROOT)}:{line}')
    assert not offences, f'signal_decisions is read-only in production code: {offences}'


def test_decision_insert_helper_is_private_and_unused_by_production_code():
    """The fixture helper stays private and uncalled, and no `record_decision` bridge exists; the v2.11
    `insert_decision` (the `paper_decisions` writer of migration 030) lives in the repository and may be
    called by the service layer — writes to `signal_decisions` are the first test's business."""
    offences = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None
            if name == 'record_decision':
                offences.append(f'{path.relative_to(ROOT)}:{node.lineno}')
            if name == FIXTURE_HELPER and path != HELPER_FILE:
                offences.append(f'{path.relative_to(ROOT)}:{node.lineno}')
            if isinstance(node, ast.FunctionDef) and node.name == 'record_decision':
                offences.append(f'{path.relative_to(ROOT)}:{node.lineno}')
            if isinstance(node, ast.FunctionDef) and node.name == FIXTURE_HELPER and path != HELPER_FILE:
                offences.append(f'{path.relative_to(ROOT)}:{node.lineno}')
    assert not offences, f'decision writers must not exist outside the repository: {offences}'
