"""Static guard (v2.9 Review Focus 3): the evidence tables are append-only, no `UPDATE` / `DELETE` against them.

`trade_plans`, `agent_plan_versions`, `plan_reviews`, `signal_decisions`, `chain_retriggers`, `holding_status`,
`plan_notes` and `trades` get rows added and never changed; `trades` has one status write (`voided_at` /
`void_reason`, via `repository.mark_trade_voided`). Scans every SQL string literal of the production code (an
`ON CONFLICT … DO UPDATE` upsert counts). Offline: parses the source files, no database."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ('investment_assistant', 'scripts')
TABLES = ('trade_plans', 'agent_plan_versions', 'plan_reviews', 'signal_decisions', 'chain_retriggers',
          'holding_status', 'plan_notes', 'trades', 'feature_flag_changes', 'sector_classifications',
          'research_versions', 'roadmap_goals', 'roadmap_versions', 'roadmap_decisions', 'roadmap_evidence',
          'roadmap_briefs')
# D51 explicitly excludes research_requests from the once-updatable category.
ONCE_UPDATABLE = {'data_requests', 'roadmap_proposals', 'portfolio_checks'}
STATE_UPDATABLE = {'research_requests'}
MUTABLE_NAMES = '|'.join(sorted(ONCE_UPDATABLE | STATE_UPDATABLE))
GUARDED_UPDATE = re.compile(rf'\bUPDATE\s+(?:ONLY\s+)?({MUTABLE_NAMES})\b(.*)', re.I | re.S)
MUTABLE_DELETE = re.compile(rf'\b(?:DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+(?:ONLY\s+)?({MUTABLE_NAMES})\b', re.I)
MUTABLE_INSERT = re.compile(rf'\bINSERT\s+INTO\s+({MUTABLE_NAMES})\b', re.I)
NAMES = '|'.join(TABLES)
UPDATE = re.compile(rf'\bUPDATE\s+(?:ONLY\s+)?({NAMES})\b(.*)', re.I | re.S)
DELETE = re.compile(rf'\b(?:DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+(?:ONLY\s+)?({NAMES})\b', re.I)
INSERT = re.compile(rf'\bINSERT\s+INTO\s+({NAMES})\b', re.I)
UPSERT = re.compile(r'\bON\s+CONFLICT\b.*\bDO\s+UPDATE\b', re.I | re.S)
# `trades` may change `voided_at` / `void_reason` and nothing else.
VOID_ONLY = re.compile(r'\bSET\s+voided_at\s*=\s*%s\s*,\s*void_reason\s*=\s*%s\b(?!\s*,)', re.I)


def _string_expr(node):
    """Join SQL string fragments across +/f-strings, keeping unknown expressions as markers."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return ''.join(_string_expr(value) for value in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _string_expr(node.left) + _string_expr(node.right)
    return '{dynamic}'


def _source_strings(tree):
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            parent = parents.get(node)
            if isinstance(parent, ast.JoinedStr) or (isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Add)):
                continue  # Scan the full statement rather than fragments lacking the WHERE clause.
            text = _string_expr(node)
            if text != '{dynamic}':
                yield node.lineno, text


def _sql_strings():
    for directory in SOURCE_DIRS:
        for path in sorted((ROOT / directory).rglob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for line, text in _source_strings(tree):
                yield f'{path.relative_to(ROOT)}:{line}', text


def _guarded(table, rest):
    where = re.search(r'\bWHERE\b(.*)', rest, re.I | re.S)
    if not where or re.search(r'\bOR\b', where.group(1), re.I):
        return False
    clause = where.group(1)
    if table == 'portfolio_checks':
        return bool(re.search(r'\bcommentary\s+IS\s+NULL\b', clause, re.I))
    if table == 'research_requests':
        return bool(re.search(r'\bstatus\s*=\s*%s\b', clause, re.I))
    if table == 'roadmap_proposals':
        return bool(re.search(r"\bstatus\s*=\s*'proposed'", clause, re.I))
    if re.search(r"\bstatus\s*=\s*'queued'", clause, re.I):
        return True
    # D40: a later rerun pointer is not a second settlement. No other column may change.
    return bool(re.fullmatch(r'\s*SET\s+rerun_run_id\s*=\s*%s\s*', rest[:where.start()], re.I)
                and re.search(r"\bstatus\s*=\s*'fetched'", clause, re.I)
                and re.search(r'\brerun_run_id\s+IS\s+NULL\b', clause, re.I)
                and re.search(r'\brows\s*>\s*0\b', clause, re.I))


def test_mutable_tables_require_expected_prior_state():
    offences = []
    for where, text in _sql_strings():
        if MUTABLE_DELETE.search(text) or (MUTABLE_INSERT.search(text) and UPSERT.search(text)):
            offences.append(f'{where}: DELETE/TRUNCATE/upsert')
        for match in GUARDED_UPDATE.finditer(text):
            if not _guarded(match.group(1).lower(), match.group(2)):
                offences.append(f'{where}: unguarded {match.group(1)}')
    assert not offences, f'state transitions require compare-and-set: {offences}'


def test_state_scanner_covers_concatenated_sql_and_rejects_unguarded_pointer_writes():
    source = '''conn.execute('UPDATE research_requests SET ' + ', '.join(fields) +
                            ' WHERE status = %s AND id = %s')'''
    statements = list(_source_strings(ast.parse(source)))
    match = GUARDED_UPDATE.search(next(text for _, text in statements if 'UPDATE' in text))
    assert match and _guarded('research_requests', match.group(2))
    assert not _guarded('research_requests', ' SET status = %s WHERE id = %s')
    assert not _guarded('research_requests', " SET status = %s WHERE status = 'closed'")
    assert not _guarded('data_requests', " SET rows = %s WHERE status = 'fetched'")
    pointer = " SET rerun_run_id = %s WHERE status = 'fetched' AND rows > 0 AND rerun_run_id IS NULL"
    assert _guarded('data_requests', pointer)
    assert not _guarded('data_requests', pointer.replace('AND rerun_run_id IS NULL', ''))
    assert not _guarded('data_requests', pointer.replace('SET rerun_run_id', 'SET rows = 0, rerun_run_id'))
    assert not _guarded('roadmap_proposals', " SET status = %s WHERE status = 'proposed' OR id = 1")
    assert _guarded('portfolio_checks', ' SET commentary = %s WHERE commentary IS NULL AND id = %s')


def test_no_update_or_delete_on_append_only_tables():
    offences = []
    for where, text in _sql_strings():
        if DELETE.search(text):
            offences.append(f'{where}: DELETE/TRUNCATE')
        for match in UPDATE.finditer(text):
            table, rest = match.group(1).lower(), match.group(2)
            if table == 'trades' and VOID_ONLY.search(rest[:200]):
                continue                              # the one status write: mark_trade_voided
            offences.append(f'{where}: UPDATE {table}')
        if UPSERT.search(text) and INSERT.search(text):
            offences.append(f'{where}: INSERT ... DO UPDATE')
    assert not offences, f'append-only tables must not be changed: {offences}'
