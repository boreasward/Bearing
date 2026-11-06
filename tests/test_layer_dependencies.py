"""Architectural boundaries include imports hidden inside functions."""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / 'investment_assistant'
LAYERS = ['core', 'data', 'derivation', 'execution', 'analysis']
TOP = ['facts']
EDGE = ['api', 'notify', 'toolsurface']
RANK = {**{name: index for index, name in enumerate(LAYERS)},
        **dict.fromkeys(TOP, 7), **dict.fromkeys(EDGE, 8),
        **dict.fromkeys(['db', 'config', 'runtime_paths', 'migrate'], -1)}


def _modules():
    modules = {}
    for path in ROOT.rglob('*.py'):
        parts = path.relative_to(ROOT.parent).with_suffix('').parts
        name = '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)
        modules[name] = (path, ast.parse(path.read_text(encoding='utf-8')))
    return modules


def _imports(name, path, nodes):
    package = name if path.name == '__init__.py' else name.rpartition('.')[0]
    for node in nodes:
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = '.'.join(package.split('.')[:len(package.split('.')) - node.level + 1])
                module = '.'.join(filter(None, [base, node.module]))
            else:
                module = node.module or ''
            yield module
            yield from (f'{module}.{alias.name}' for alias in node.names if alias.name != '*')


def test_no_upward_imports():
    violations = []
    for name, (path, tree) in _modules().items():
        if name == 'investment_assistant':
            continue
        source = name.split('.')[1]
        if source not in RANK:
            violations.append(f'{name}: unmigrated package {source}')
            continue
        for target in _imports(name, path, ast.walk(tree)):
            if not target.startswith('investment_assistant.'):
                continue
            package = target.split('.')[1]
            if package not in RANK or RANK[package] > RANK[source]:
                violations.append(f'{name} -> {target}')
    assert not violations, '\n'.join(sorted(set(violations)))


REMOVED = ('workbench', 'filings', 'us_research',
           'workflow', 'views', 'services', 'tasks', 'review', 'optimize',
           'analysis/agent', 'core/llm', 'execution/paper', 'api/routes',
           'hermes', 'market', 'research', 'strategies', 'tickers')


def test_removed_v2_packages_stay_gone():
    for package in REMOVED:
        assert not (ROOT / package).exists(), package


def test_no_module_cycles():
    modules = _modules()
    graph = {}
    for name, (path, tree) in modules.items():
        # All imports executed at module load, including conditional imports;
        # function-local imports are checked by the rank guard above.
        def top_level(node):
            yield node
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                for child in ast.iter_child_nodes(node):
                    yield from top_level(child)
        graph[name] = {target for target in _imports(name, path, top_level(tree))
                       if target in modules and target != name}
    # Tarjan: no optional test dependency and no recursion through function bodies.
    indices, low, stack, active, cycles = {}, {}, [], set(), []

    def visit(node):
        indices[node] = low[node] = len(indices)
        stack.append(node)
        active.add(node)
        for target in sorted(graph[node]):
            if target not in indices:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in active:
                low[node] = min(low[node], indices[target])
        if low[node] == indices[node]:
            component = []
            while True:
                member = stack.pop()
                active.remove(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                cycles.append(sorted(component))

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    assert not cycles, '\n'.join(' -> '.join(component) for component in cycles)
