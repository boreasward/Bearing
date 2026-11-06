"""Repository functions of v2.9 (migration 029): executable_ready, chain_retriggers, holding_status, plan_notes,
trades.plan_id, cutoff-aware trade reads, chain summaries, the default of rule_plan and the private decision insert.

SQL tests run in a throwaway schema of the disposable test database (the `workbench_db` fixture, migrations through
029); `executable_ready` is also checked against databases missing one of the pieces."""
import investment_assistant.core.schema as core_schema
import ast
import os
import uuid
from pathlib import Path

import pytest


# --- executable_ready ---

def test_executable_ready_true_after_029(workbench_db):
    assert core_schema.executable_ready(workbench_db) is True


@pytest.mark.parametrize('drop', [
    'DROP TABLE chain_retriggers', 'DROP TABLE holding_status', 'DROP TABLE plan_notes',
    'ALTER TABLE trades DROP COLUMN plan_id CASCADE', 'ALTER TABLE trade_plans DROP COLUMN entry_type CASCADE',
    'ALTER TABLE trade_plans DROP COLUMN max_hold_sessions CASCADE'])
def test_executable_ready_false_when_a_piece_is_missing(workbench_db, drop):
    workbench_db.execute(drop)
    assert core_schema.executable_ready(workbench_db) is False


def test_executable_ready_false_on_database_without_029():
    url = os.environ.get('INVESTMENT_ASSISTANT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('explicit test database URL required; disposable schema only')
    import psycopg
    schema = f'er_test_{uuid.uuid4().hex[:12]}'
    with psycopg.connect(url) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
        conn.execute(f'SET search_path TO {schema}')
        try:
            for path in sorted(Path('migrations').glob('0*.sql')):
                if path.name < '029':
                    conn.execute(path.read_text(encoding='utf-8'))
            conn.commit()
            assert core_schema.executable_ready(conn) is False
            conn.execute(Path('migrations/029_executable_plans.sql').read_text(encoding='utf-8'))
            conn.commit()
            assert core_schema.executable_ready(conn) is True
        finally:
            conn.rollback()
            conn.execute(f'DROP SCHEMA {schema} CASCADE')
            conn.commit()


# --- chain_retriggers ---


# --- holding_status ---


# --- plan_notes ---


# --- trades: plan_id, cutoff ---


# --- chains_for_ticker ---


# --- plans: execution fields, rule_plan default (R7) ---


# --- insert_decision: the paper_decisions writer (signal_decisions stays read-only) ---


def test_insert_decision_is_defined_only_in_the_repository():
    """`signal_decisions` is read-only in production code: the one `insert_decision` definition is the
    repository's paper_decisions writer, and no `record_decision` bridge exists anywhere."""
    root = Path('investment_assistant')
    for path in root.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.FunctionDef) and node.name == 'insert_decision':
                assert path == root / 'execution' / 'repo.py', path
            name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None
            if name == 'record_decision':
                raise AssertionError(f'{path}:{node.lineno}')
