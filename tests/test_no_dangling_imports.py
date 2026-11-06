"""No code under the package, tests/ or scripts/ refers to an `investment_assistant.*` module or name that is not there.

Everything is checked statically with `ast`; the modules under inspection are never imported."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'investment_assistant'
SCAN_ROOTS = (PACKAGE, ROOT / 'tests', ROOT / 'scripts')
DOTTED = re.compile(r'investment_assistant(?:\.[A-Za-z_][A-Za-z0-9_]*)+')

# (file, dotted string without the leading `investment_assistant.`) pairs that are deliberately not resolvable.
# The prefix is left off so that this file does not itself contain the dangling string.
ABSENT_BY_DESIGN: set[tuple[str, str]] = set()


def _python_files():
    for root in SCAN_ROOTS:
        yield from sorted(path for path in root.rglob('*.py') if '__pycache__' not in path.parts)


def _base(dotted):
    return ROOT.joinpath(*dotted.split('.'))


def _module_exists(dotted):
    return _base(dotted).with_suffix('.py').is_file() or (_base(dotted) / '__init__.py').is_file()


def _is_package(dotted):
    return (_base(dotted) / '__init__.py').is_file()


def _source_of(dotted):
    path = _base(dotted) / '__init__.py' if _is_package(dotted) else _base(dotted).with_suffix('.py')
    return path.read_text(encoding='utf-8')


def _bindings(dotted):
    """(names bound at module level, whether the module is opaque). Opaque = a star import or a module-level
    `__getattr__` makes the set of names unknowable, so name checks against it are skipped."""
    names, opaque = set(), False

    def visit(nodes):
        nonlocal opaque
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                opaque = opaque or node.name == '__getattr__'
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names.update(n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name))
            elif isinstance(node, ast.Import):
                names.update((alias.asname or alias.name).split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == '*':
                        opaque = True
                    else:
                        names.add(alias.asname or alias.name)
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                for field in ('body', 'orelse', 'finalbody'):
                    visit(getattr(node, field, []))
                for handler in getattr(node, 'handlers', []):
                    visit(handler.body)

    visit(ast.parse(_source_of(dotted)).body)
    return names, opaque


def _name_resolves(module, name):
    """`from module import name`: a submodule on disk, or a module-level binding of `module`."""
    if _is_package(module) and _module_exists(f'{module}.{name}'):
        return True
    names, opaque = _bindings(module)
    return opaque or name in names


def _package_of(path):
    module = '.'.join(path.relative_to(ROOT).with_suffix('').parts)
    return module if path.name == '__init__.py' else module.rpartition('.')[0]


def _imports(tree, package):
    """Absolute import targets as (module, name): `name` is None for `import a.b` and for the module part of a
    `from`, and the imported name for `from module import name`. Relative imports are resolved, function bodies
    included (ast.walk)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, None
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = '.'.join(package.split('.')[:len(package.split('.')) - node.level + 1])
                module = '.'.join(filter(None, [base, node.module]))
            else:
                module = node.module or ''
            yield module, None
            for alias in node.names:
                if alias.name != '*':
                    yield module, alias.name


def _is_internal(dotted):
    return dotted == 'investment_assistant' or dotted.startswith('investment_assistant.')


def test_every_internal_import_resolves():
    violations = []
    for path in _python_files():
        for module, name in _imports(ast.parse(path.read_text(encoding='utf-8')), _package_of(path)):
            if not _is_internal(module):
                continue
            if not _module_exists(module):
                violations.append(f'{path.relative_to(ROOT)}: {module}')
            elif name is not None and not _name_resolves(module, name):
                violations.append(f'{path.relative_to(ROOT)}: {module}.{name}')
    assert not violations, '\n'.join(sorted(set(violations)))


def _string_target_resolves(dotted):
    """The longest on-disk module prefix (at least `investment_assistant.<sub>`), then, if anything follows, its first
    attribute must be a name that module binds."""
    parts = dotted.split('.')
    known = [n for n in range(2, len(parts) + 1) if _module_exists('.'.join(parts[:n]))]
    if not known:
        return False
    depth = max(known)
    return depth == len(parts) or _name_resolves('.'.join(parts[:depth]), parts[depth])


def test_every_dotted_module_string_resolves():
    violations = []
    for path in _python_files():
        where = str(path.relative_to(ROOT))
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            for match in DOTTED.finditer(node.value):
                target = match.group(0)
                if (where, target.removeprefix('investment_assistant.')) in ABSENT_BY_DESIGN:
                    continue
                if not _string_target_resolves(target):
                    violations.append(f'{where}: {target}')
    assert not violations, '\n'.join(sorted(set(violations)))


def test_absent_by_design_entries_are_still_needed():
    for where, tail in sorted(ABSENT_BY_DESIGN):
        target = f'investment_assistant.{tail}'
        literals = [n.value for n in ast.walk(ast.parse((ROOT / where).read_text(encoding='utf-8')))
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        assert any(target in text for text in literals), f'{where} no longer mentions {target}: drop the entry'
        assert not _string_target_resolves(target), f'{target} exists now: drop the entry'
