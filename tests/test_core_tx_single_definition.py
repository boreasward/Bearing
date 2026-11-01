"""``connection`` is defined exactly once, in ``core/tx.py`` (spec §4.2, D8).

Walks the AST of every module under ``investment_assistant/``: a ``def connection``
(or an assignment of a lambda to it) anywhere else would give some layer a private
database seam that bypasses ``core.tx`` and the re-export patches of the tests."""
import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / 'investment_assistant'
ALLOWED = {PACKAGE / 'core' / 'tx.py'}


def _definitions(tree: ast.AST) -> list[str]:
    kinds = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'connection':
            kinds.append('def')
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == 'connection' \
                        and isinstance(node.value, ast.Lambda):
                    kinds.append('lambda')
    return kinds


def test_connection_defined_once():
    offenders = {}
    for path in sorted(PACKAGE.rglob('*.py')):
        if path in ALLOWED:
            continue
        found = _definitions(ast.parse(path.read_text(encoding='utf-8')))
        if found:
            offenders[str(path.relative_to(PACKAGE.parent))] = found
    assert offenders == {}, f'connection must be defined only in core/tx.py: {offenders}'
