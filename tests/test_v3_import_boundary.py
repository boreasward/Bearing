"""v3 packages import only the kept lower layers (plan Global Constraints); facts never imports toolsurface."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'investment_assistant'
V3_PACKAGES = ('facts', 'toolsurface')
# plan「新代码的 import 边界」：core 六个保留模块、data/derivation 整包、四个 analysis 模块、
# 三个 execution 模块、db、config，以及彼此（toolsurface 可 import facts）。
KEPT = ('investment_assistant.core.tx', 'investment_assistant.core.dbutil',
        'investment_assistant.core.constants', 'investment_assistant.core.redact',
        'investment_assistant.core.texts', 'investment_assistant.core.knowledge',
        'investment_assistant.data', 'investment_assistant.derivation',
        'investment_assistant.analysis.rules', 'investment_assistant.analysis.screen_rules',
        'investment_assistant.analysis.regime', 'investment_assistant.analysis.pool',
        'investment_assistant.execution.plan_levels', 'investment_assistant.execution.entry_rules',
        'investment_assistant.execution.positions',
        'investment_assistant.db', 'investment_assistant.config', 'investment_assistant.facts')


def _imports(tree, package):
    """Absolute import targets, resolving relative imports, function bodies included (ast.walk)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = '.'.join(package.split('.')[:len(package.split('.')) - node.level + 1])
                yield '.'.join(filter(None, [base, node.module]))
            else:
                yield node.module or ''
            yield from (f'{node.module}.{alias.name}' for alias in node.names if alias.name != '*')


def test_v3_packages_import_only_kept_modules():
    violations = []
    for name in V3_PACKAGES:
        assert (PACKAGE / name).is_dir(), f'v3 package missing: {name}'
        for path in sorted((PACKAGE / name).rglob('*.py')):
            parts = path.relative_to(ROOT).with_suffix('').parts
            module = '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)
            package = module if path.name == '__init__.py' else module.rpartition('.')[0]
            for target in _imports(ast.parse(path.read_text(encoding='utf-8')), package):
                if not target.startswith('investment_assistant.'):
                    continue
                kept = any(target == prefix or target.startswith(prefix + '.') for prefix in KEPT)
                if name == 'facts' and target.startswith('investment_assistant.toolsurface'):
                    kept = False
                if not kept:
                    violations.append(f'{path.relative_to(ROOT)}: {target}')
    assert not violations, '\n'.join(violations)
