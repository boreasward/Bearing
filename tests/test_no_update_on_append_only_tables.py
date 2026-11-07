"""Static guard: the append-only tables get rows added and never changed, no `UPDATE` / `DELETE` against them.

`feature_flag_changes`, `sector_classifications` and the append-only `v3_` tables of migration 032 (`v3_runs`,
`v3_candidates`, `v3_trades`, `v3_equity`, `v3_tool_calls`) plus migration 034's record tables (`v3_plan_events`,
`v3_cash_flows`, `v3_voids`, `v3_chat_messages` — voiding a trade or cash flow appends a `v3_voids` row, the
original row is never touched). `v3_verdicts`, `v3_plans`, `v3_holding_reviews`, `v3_briefs`, `v3_chats` and
`v3_settings` are upserted by design (one row per key, rewritten in place) and are not listed. Scans every SQL
string literal of the production code (an `ON CONFLICT … DO UPDATE` upsert counts). Offline: parses the source
files, no database."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ('investment_assistant', 'scripts')
TABLES = ('feature_flag_changes', 'sector_classifications',
          'v3_runs', 'v3_candidates', 'v3_trades', 'v3_equity', 'v3_tool_calls',
          'v3_plan_events', 'v3_cash_flows', 'v3_voids', 'v3_chat_messages')
NAMES = '|'.join(TABLES)
UPDATE = re.compile(rf'\bUPDATE\s+(?:ONLY\s+)?({NAMES})\b(.*)', re.I | re.S)
DELETE = re.compile(rf'\b(?:DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+(?:ONLY\s+)?({NAMES})\b', re.I)
INSERT = re.compile(rf'\bINSERT\s+INTO\s+({NAMES})\b', re.I)
UPSERT = re.compile(r'\bON\s+CONFLICT\b.*\bDO\s+UPDATE\b', re.I | re.S)


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


def test_no_update_or_delete_on_append_only_tables():
    offences = []
    for where, text in _sql_strings():
        if DELETE.search(text):
            offences.append(f'{where}: DELETE/TRUNCATE')
        for match in UPDATE.finditer(text):
            offences.append(f'{where}: UPDATE {match.group(1).lower()}')
        if UPSERT.search(text) and INSERT.search(text):
            offences.append(f'{where}: INSERT ... DO UPDATE')
    assert not offences, f'append-only tables must not be changed: {offences}'


def test_scan_sees_the_real_inserts():
    inserted = {match.group(1).lower() for _, text in _sql_strings() for match in INSERT.finditer(text)}
    assert inserted >= {'sector_classifications', 'v3_runs', 'v3_candidates', 'v3_trades', 'v3_equity',
                        'v3_tool_calls', 'v3_plan_events', 'v3_cash_flows', 'v3_voids', 'v3_chat_messages'}


def test_scanner_covers_concatenated_sql_and_upserts():
    source = "conn.execute('UPDATE v3_trades SET ' + ', '.join(fields) + ' WHERE id = %s')"
    assert any(UPDATE.search(text) for _, text in _source_strings(ast.parse(source)))
    upsert = "conn.execute('INSERT INTO v3_runs (run_id) VALUES (%s) ON CONFLICT (run_id) DO UPDATE SET run_id = 1')"
    assert any(INSERT.search(text) and UPSERT.search(text) for _, text in _source_strings(ast.parse(upsert)))
