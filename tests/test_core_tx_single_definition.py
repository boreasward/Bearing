"""The three shared transaction APIs each have one definition, in core/tx.py."""
import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / 'investment_assistant'
SHARED_NAMES = ('connection', '_tx', 'read_only_tx')


def _definitions(tree: ast.AST) -> list[tuple[str, str]]:
    kinds = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in SHARED_NAMES:
            kinds.append((node.name, 'def'))
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Lambda):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in SHARED_NAMES:
                    kinds.append((target.id, 'lambda'))
    return kinds


def test_shared_transaction_apis_defined_once():
    definitions = {name: [] for name in SHARED_NAMES}
    for path in sorted(PACKAGE.rglob('*.py')):
        for name, kind in _definitions(ast.parse(path.read_text(encoding='utf-8'))):
            definitions[name].append((str(path.relative_to(PACKAGE)), kind))
    assert definitions == {name: [('core/tx.py', 'def')] for name in SHARED_NAMES}
