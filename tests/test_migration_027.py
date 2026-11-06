"""Migration 027 (v2.7 spec §11): eleven append-only tables and three relaxed constraints. Throwaway schema only."""
import investment_assistant.core.dbutil as core_dbutil
import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

# Repository consumers use their owning layers below.

MIGRATION = Path('migrations/027_growth_loop.sql')
NEW_TABLES = ('review_cards', 'review_card_drafts', 'discussions', 'knowledge_items', 'discussion_turns',
              'knowledge_drafts', 'knowledge_versions', 'knowledge_status', 'knowledge_draft_verdicts',
              'review_card_verdicts', 'knowledge_citations')

NOW = datetime(2026, 10, 28, 22, 30, tzinfo=UTC)
DAY = date(2026, 10, 21)
_JSONB = {'sizing', 'inputs', 'output', 'counter', 'facts', 'refs', 'detail', 'args', 'result', 'attachment',
          'skips', 'failures', 'degradations', 'outputs'}


@pytest.fixture
def migrated_db():
    """Migrations < 027 in a throwaway schema, dropped afterwards; explicit test URL only."""
    url = os.environ.get('INVESTMENT_ASSISTANT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('explicit test database URL required; disposable schema only')
    import psycopg
    schema = f'm027_test_{uuid.uuid4().hex[:12]}'
    with psycopg.connect(url) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
        conn.execute(f'SET search_path TO {schema}')
        for path in sorted(Path('migrations').glob('0*.sql')):
            if path.name < '027':
                conn.execute(path.read_text(encoding='utf-8'))
        conn.commit()
        try:
            yield conn
        finally:
            conn.rollback()
            conn.execute(f'DROP SCHEMA {schema} CASCADE')
            conn.commit()


def _apply(conn):
    conn.execute(MIGRATION.read_text(encoding='utf-8'))
    conn.commit()


def _insert(conn, table, values, returning='id'):
    """One row inside a savepoint, so a rejected insert leaves the outer transaction usable."""
    cols, marks, params = [], [], []
    for key, value in values.items():
        cols.append(key)
        if key in _JSONB:
            marks.append('%s::jsonb')
            params.append(json.dumps(value) if value is not None else None)
        else:
            marks.append('%s')
            params.append(value)
    with conn.transaction():
        return conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(marks)}) "
                            f'RETURNING {returning}', params).fetchone()[0]


def _run(conn, **overrides):
    values = {'id': uuid.uuid4(), 'ticker': 'NVDA', 'signal_date': DAY, 'pool': 'main', 'attempt': 1,
              'mode': 'retro', 'agent_version': 'agent-retro-1', 'provider': 'legacy', 'model': 'gpt-test',
              'cutoff_at': NOW, 'status': 'rejected', 'reason': 'malformed', 'plan_id': None,
              'started_at': NOW, 'finished_at': NOW}
    values.update(overrides)
    return _insert(conn, 'agent_runs', values)


def _trade(conn, **overrides):
    values = {'request_id': uuid.uuid4(), 'request_hash': 'h', 'ticker': 'NVDA', 'side': 'buy', 'quantity': 10,
              'price': 100, 'fee': 0, 'executed_at': NOW}
    values.update(overrides)
    return _insert(conn, 'trades', values)


def _card(conn, **overrides):
    values = {'event_key': f'unit:NVDA:{DAY}:ended:{uuid.uuid4().hex[:6]}', 'subject': 'unit', 'ticker': 'NVDA',
              'signal_date': DAY, 'buy_trade_id': None, 'end_kind': 'stop', 'end_session': DAY + timedelta(days=3),
              'retro_version': 'retro-1', 'facts': {}}
    values.update(overrides)
    return _insert(conn, 'review_cards', values)


def _draft_row(conn, card_id, attempt=1, status='ok', **overrides):
    values = {'card_id': card_id, 'attempt': attempt, 'trigger': 'batch',
              'agent_run_id': _run(conn, mode='retro', status='ok' if status == 'ok' else 'rejected',
                                   reason=None if status == 'ok' else 'retro_malformed'),
              'status': status, 'reason': None if status == 'ok' else 'retro_malformed',
              'output': {'headline': 'h'} if status == 'ok' else None}
    values.update(overrides)
    return _insert(conn, 'review_card_drafts', values)


def _item(conn, origin='manual', **overrides):
    values = {'origin': origin}
    values.update(overrides)
    return _insert(conn, 'knowledge_items', values)


def _version(conn, item_id, version=1, supersedes=None, **overrides):
    values = {'knowledge_id': item_id, 'version': version, 'supersedes_id': supersedes,
              'request_id': uuid.uuid4(), 'request_hash': 'h', 'title': 't', 'body': 'b', 'scope': 's',
              'counter_examples': '', 'concepts': [], 'tickers': ['NVDA'], 'schema_version': 'my-knowledge-1'}
    values.update(overrides)
    return _insert(conn, 'knowledge_versions', values)


def _discussion(conn, **overrides):
    values = {'request_id': uuid.uuid4(), 'request_hash': 'h', 'context_kind': 'card', 'ticker': 'NVDA',
              'signal_date': DAY, 'plan_id': None, 'card_id': None}
    values.update(overrides)
    return _insert(conn, 'discussions', values)


def _kdraft(conn, card_draft_id, lesson_index=0, **overrides):
    values = {'origin': 'review_card', 'card_draft_id': card_draft_id, 'lesson_index': lesson_index,
              'title': 't', 'body': 'b', 'scope': 's', 'counter_examples': 'c', 'concepts': [],
              'tickers': ['NVDA'], 'refs': []}
    values.update(overrides)
    return _insert(conn, 'knowledge_drafts', values)


def test_migrate_001_to_027_and_rerun(migrated_db):
    conn = migrated_db
    _apply(conn)
    _apply(conn)                                   # IF NOT EXISTS / DROP IF EXISTS: safe to run twice
    for table in NEW_TABLES:
        assert conn.execute('SELECT to_regclass(%s) IS NOT NULL', [table]).fetchone() == (True,), table
    assert len(NEW_TABLES) == 11


def test_relaxed_constraints_accept_new_and_old(migrated_db):
    import psycopg
    conn = migrated_db
    _apply(conn)
    assert _run(conn, mode='retro', status='ok', reason=None)
    assert _run(conn, mode='discuss', status='ok', reason=None)
    assert _run(conn, mode='discuss', status='rejected', reason='answer_malformed')
    plan_id = _insert(conn, 'trade_plans', {
        'ticker': 'NVDA', 'signal_date': DAY, 'kind': 'agent', 'plan_version': 'agent-2', 'ref_price': 100,
        'atr': 2, 'entry_low': 99, 'entry_high': 101, 'stop': 96, 'target1': 108, 'valid_from': date(2026, 10, 22),
        'valid_until': date(2026, 10, 28), 'sizing': {}, 'status': 'ok', 'invalid_reasons': [], 'warnings': [],
        'inputs': {}})
    with pytest.raises(psycopg.errors.CheckViolation):
        _run(conn, mode='retro', status='ok', reason=None, plan_id=plan_id)   # a retro run never has a plan
    with pytest.raises(psycopg.errors.CheckViolation):
        _run(conn, mode='retro', status='pass', reason=None)                   # and can never pass
    with pytest.raises(psycopg.errors.CheckViolation):
        _run(conn, mode='rewrite')
    # old values and old rules hold
    assert _run(conn, mode='interpret', status='ok', reason=None)
    assert _run(conn, mode='revise', status='ok', reason=None, plan_id=plan_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        _run(conn, mode='propose', status='ok', reason=None)

    run_id = _run(conn, mode='retro', status='ok', reason=None)
    call = {'run_id': run_id, 'seq': 1, 'phase': 'retro', 'tool': 'get_knowledge', 'args': {},
            'result': {'ref': 'call:1'}, 'result_sha256': 'a' * 64, 'called_at': NOW}
    assert _insert(conn, 'agent_tool_calls', call)
    assert _insert(conn, 'agent_tool_calls', {**call, 'seq': 2, 'phase': 'discuss'})
    assert _insert(conn, 'agent_tool_calls', {**call, 'seq': 3, 'phase': 'review'})
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, 'agent_tool_calls', {**call, 'seq': 4, 'phase': 'revise'})

    stage = {'run_id': 'r1', 'job': 'workbench', 'trigger': 'scheduled', 'session': DAY, 'stage': 'retro',
             'stages_version': 'stages-3', 'status': 'success', 'input_count': 0, 'output_count': 0,
             'started_at': NOW, 'finished_at': NOW + timedelta(seconds=1), 'duration_ms': 1000}
    assert _insert(conn, 'stage_runs', stage)
    assert _insert(conn, 'stage_runs', {**stage, 'stage': 'review'})
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, 'stage_runs', {**stage, 'stage': 'recheck'})

    # the CHECKs were replaced by named ones, not stacked
    for table, column, name in (('agent_runs', 'mode', 'agent_runs_mode_check'),
                                ('agent_tool_calls', 'phase', 'agent_tool_calls_phase_check'),
                                ('stage_runs', 'stage', 'stage_runs_stage_check')):
        names = [r[0] for r in conn.execute(
            'SELECT c.conname FROM pg_constraint c '
            'JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) '
            "WHERE c.conrelid = %s::regclass AND c.contype = 'c' AND a.attname = %s "
            'AND cardinality(c.conkey) = 1', [table, column]).fetchall()]
        assert names == [name]


def test_review_cards_checks(migrated_db):
    import psycopg
    conn = migrated_db
    _apply(conn)
    buy = _trade(conn)
    assert _card(conn, event_key='unit:NVDA:2026-10-21:ended')
    assert _card(conn, event_key=f'trade:{buy}', subject='trade', signal_date=None, buy_trade_id=buy,
                 end_kind='position_closed')
    with pytest.raises(psycopg.errors.UniqueViolation):
        _card(conn, event_key='unit:NVDA:2026-10-21:ended')               # one card per end event
    with pytest.raises(psycopg.errors.CheckViolation):
        _card(conn, subject='trade', signal_date=None, end_kind='position_closed')   # trade needs its buy
    with pytest.raises(psycopg.errors.CheckViolation):
        _card(conn, event_key='trade:x', subject='trade', signal_date=None, buy_trade_id=buy,
              end_kind='stop')                                           # a trade card only ends by closing
    with pytest.raises(psycopg.errors.CheckViolation):
        _card(conn, signal_date=None)                                    # a unit card has its signal date
    with pytest.raises(psycopg.errors.CheckViolation):
        _card(conn, end_kind='cancelled')
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _card(conn, subject='trade', signal_date=None, buy_trade_id=uuid.uuid4(), end_kind='position_closed')


def test_one_ok_draft_per_card(migrated_db):
    import psycopg
    conn = migrated_db
    _apply(conn)
    card = _card(conn)
    assert _draft_row(conn, card, 1, 'rejected')
    assert _draft_row(conn, card, 2, 'ok')
    assert _draft_row(conn, card, 3, 'rejected')
    with pytest.raises(psycopg.errors.UniqueViolation):
        _draft_row(conn, card, 4, 'ok')                                  # at most one ok draft
    with pytest.raises(psycopg.errors.UniqueViolation):
        _draft_row(conn, card, 3, 'rejected')                            # UNIQUE (card_id, attempt)
    with pytest.raises(psycopg.errors.CheckViolation):
        _draft_row(conn, card, 5, 'rejected', reason=None)               # a failed draft says why
    with pytest.raises(psycopg.errors.CheckViolation):
        _draft_row(conn, card, 5, 'ok', output=None)                     # ok has an output
    with pytest.raises(psycopg.errors.CheckViolation):
        _draft_row(conn, card, 5, 'rejected', output={'x': 1})           # only ok has one
    with pytest.raises(psycopg.errors.CheckViolation):
        _draft_row(conn, card, 5, 'rejected', agent_run_id=None)         # non-skipped drafts name their run
    assert _draft_row(conn, card, 5, 'skipped', agent_run_id=None, reason='stage_budget', output=None)
    with pytest.raises(psycopg.errors.CheckViolation):
        _draft_row(conn, card, 6, 'ok', trigger='cron')


def test_knowledge_version_chain_checks(migrated_db):
    import psycopg
    conn = migrated_db
    _apply(conn)
    item = _item(conn)
    v1 = _version(conn, item, 1)
    v2 = _version(conn, item, 2, supersedes=v1)
    assert conn.execute('SELECT created_at IS NOT NULL FROM knowledge_versions WHERE id = %s', [v2]).fetchone() \
        == (True,)
    with pytest.raises(psycopg.errors.CheckViolation):
        _version(conn, _item(conn), 1, supersedes=v1)       # version 1 has no predecessor
    other = _item(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        _version(conn, other, 2, supersedes=None)                         # version 2 must supersede
    with pytest.raises(psycopg.errors.UniqueViolation):
        _version(conn, item, 3, supersedes=v1)                            # no fork: UNIQUE (supersedes_id)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _version(conn, item, 2, supersedes=v2)                            # UNIQUE (knowledge_id, version)
    with pytest.raises(psycopg.errors.CheckViolation):
        _version(conn, other, 1, concepts=[], tickers=[])                 # at least one link
    with pytest.raises(psycopg.errors.CheckViolation):
        _version(conn, other, 1, tickers=[f'T{i}' for i in range(11)])    # at most ten
    assert _version(conn, other, 1, concepts=['concept:plan.stop@1'] * 4, tickers=['A'] * 6)   # exactly ten
    with pytest.raises(psycopg.errors.CheckViolation):
        _version(conn, _item(conn), 1, title=' ')
    with pytest.raises(psycopg.errors.CheckViolation):
        _version(conn, _item(conn), 1, title='字' * 61)
    with pytest.raises(psycopg.errors.CheckViolation):
        _version(conn, _item(conn), 1, body='x' * 4001)
    with pytest.raises(psycopg.errors.CheckViolation):
        _version(conn, _item(conn), 1, scope='')
    with pytest.raises(psycopg.errors.CheckViolation):
        _version(conn, _item(conn), 1, counter_examples='x' * 1001)
    assert _version(conn, _item(conn), 1, counter_examples='')            # hand-written lessons may skip it
    # items: origin and its source go together
    with pytest.raises(psycopg.errors.CheckViolation):
        _item(conn, 'review_card')
    with pytest.raises(psycopg.errors.CheckViolation):
        _item(conn, 'manual', source_card_id=_card(conn))
    with pytest.raises(psycopg.errors.CheckViolation):
        _item(conn, 'discussion')
    assert _item(conn, 'review_card', source_card_id=_card(conn))
    # status rows
    status = {'knowledge_id': item, 'status': 'archived', 'reason': 'old', 'request_id': uuid.uuid4(),
              'request_hash': 'h'}
    assert _insert(conn, 'knowledge_status', status)
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, 'knowledge_status', {**status, 'request_id': uuid.uuid4(), 'status': 'deleted'})
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, 'knowledge_status', {**status, 'request_id': uuid.uuid4(), 'reason': 'x' * 301})
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert(conn, 'knowledge_status', status)                         # request_id is idempotent


def test_draft_and_verdict_uniques(migrated_db):
    import psycopg
    conn = migrated_db
    _apply(conn)
    card = _card(conn)
    card_draft = _draft_row(conn, card, 1, 'ok')
    draft = _kdraft(conn, card_draft, 0)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _kdraft(conn, card_draft, 0)                                      # UNIQUE (card_draft_id, lesson_index)
    with pytest.raises(psycopg.errors.CheckViolation):
        _kdraft(conn, card_draft, 3)                                      # lesson_index 0..2
    with pytest.raises(psycopg.errors.CheckViolation):
        _kdraft(conn, card_draft, 1, counter_examples=' ')                # drafts must carry a counter-example
    with pytest.raises(psycopg.errors.CheckViolation):
        _kdraft(conn, card_draft, 1, concepts=[], tickers=[])
    with pytest.raises(psycopg.errors.CheckViolation):
        _kdraft(conn, None, None)                                         # review_card origin needs its draft
    with pytest.raises(psycopg.errors.CheckViolation):
        _kdraft(conn, card_draft, 1, target_knowledge_id=_item(conn), target_version=1)   # only discussions retarget

    item = _item(conn, 'review_card', source_card_id=card)
    version = _version(conn, item, 1, draft_id=draft)
    verdict = {'draft_id': draft, 'action': 'approve', 'knowledge_version_id': version, 'request_id': uuid.uuid4()}
    assert _insert(conn, 'knowledge_draft_verdicts', verdict)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert(conn, 'knowledge_draft_verdicts', {**verdict, 'action': 'dismiss', 'knowledge_version_id': None})
    other = _kdraft(conn, card_draft, 1)
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, 'knowledge_draft_verdicts', {'draft_id': other, 'action': 'approve', 'request_id': uuid.uuid4()})
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, 'knowledge_draft_verdicts', {'draft_id': other, 'action': 'dismiss',
                                                   'knowledge_version_id': version, 'request_id': uuid.uuid4()})
    assert _insert(conn, 'knowledge_draft_verdicts', {'draft_id': other, 'action': 'dismiss',
                                                      'request_id': uuid.uuid4()})
    with pytest.raises(psycopg.errors.UniqueViolation):
        _version(conn, _item(conn), 1, draft_id=draft)                    # a draft yields one version

    # card verdicts: one per card, idempotent request
    note = {'card_id': card, 'request_id': uuid.uuid4(), 'request_hash': 'h', 'draft_id': card_draft, 'note': 'ok'}
    assert _insert(conn, 'review_card_verdicts', note)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert(conn, 'review_card_verdicts', {**note, 'request_id': uuid.uuid4()})   # one verdict per card
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, 'review_card_verdicts', {**note, 'card_id': _card(conn), 'request_id': uuid.uuid4(),
                                               'note': 'x' * 2001})

    # discussions and turns: one answer per question
    discussion = _discussion(conn, card_id=card)
    with pytest.raises(psycopg.errors.CheckViolation):
        _discussion(conn, context_kind='card', card_id=None)
    with pytest.raises(psycopg.errors.CheckViolation):
        _discussion(conn, context_kind='plan', card_id=card)
    question = {'discussion_id': discussion, 'seq': 1, 'role': 'user', 'request_id': uuid.uuid4(),
                'request_hash': 'h', 'body': 'why this stop?'}
    q1 = _insert(conn, 'discussion_turns', question)
    answer = {'discussion_id': discussion, 'seq': 2, 'role': 'assistant', 'body': 'because', 'reply_to': None,
              'status': 'ok', 'cutoff_at': NOW}
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, 'discussion_turns', answer)                         # an answer replies to a question
    a1 = _insert(conn, 'discussion_turns', {**answer, 'reply_to': q1})
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert(conn, 'discussion_turns', {**answer, 'seq': 3, 'reply_to': q1})   # one answer per question
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert(conn, 'discussion_turns', {**question, 'request_id': uuid.uuid4()})   # UNIQUE (discussion, seq)
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, 'discussion_turns', {**question, 'seq': 3, 'body': ' '})
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, 'discussion_turns', {**answer, 'seq': 3, 'reply_to': q1, 'status': 'rejected'})   # reason
    # a discussion draft hangs off the answer turn
    assert _kdraft(conn, None, None, origin='discussion', discussion_turn_id=a1, target_knowledge_id=item,
                   target_version=1)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _kdraft(conn, None, None, origin='discussion', discussion_turn_id=a1)   # UNIQUE (discussion_turn_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        _kdraft(conn, None, None, origin='discussion', discussion_turn_id=q1, target_knowledge_id=item)


def test_citation_fk_to_version(migrated_db):
    import psycopg
    conn = migrated_db
    _apply(conn)
    item = _item(conn)
    _version(conn, item, 1)
    run = _run(conn, mode='retro', status='ok', reason=None)
    citation = {'agent_run_id': run, 'plan_id': None, 'review_id': None, 'knowledge_id': item, 'version': 1,
                'field': 'thesis', 'call_seq': 1}
    assert _insert(conn, 'knowledge_citations', citation)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _insert(conn, 'knowledge_citations', {**citation, 'version': 2})   # a version that was never written
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _insert(conn, 'knowledge_citations', {**citation, 'knowledge_id': item + 99})
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert(conn, 'knowledge_citations', citation)                     # one row per run x version x field
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, 'knowledge_citations', {**citation, 'field': 'notes'})
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, 'knowledge_citations', {**citation, 'field': 'risks', 'call_seq': 0})


def test_new_tables_not_in_workbench_tables():
    assert not set(NEW_TABLES) & set(core_dbutil.WORKBENCH_TABLES)
