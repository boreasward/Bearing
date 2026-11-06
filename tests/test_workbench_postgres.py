"""Real constraint behavior for the US workbench schema; disposable schema only."""
import investment_assistant.core.dbutil as core_dbutil
import investment_assistant.core.schema as core_schema
import investment_assistant.data.repo as data_repo
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest


NOW = datetime(2026, 10, 22, 22, 30, tzinfo=UTC)


def _bar(conn, revision=1, **overrides):
    values = {'ticker': 'NVDA', 'price_date': date(2026, 10, 21), 'open': 100, 'high': 105, 'low': 99,
              'close': 104, 'volume': 1000, 'adj_close': 104, 'revision': revision, 'fetched_at': NOW}
    values.update(overrides)
    cols = ', '.join(values)
    marks = ', '.join(['%s'] * len(values))
    conn.execute(f'INSERT INTO price_bars ({cols}) VALUES ({marks})', list(values.values()))


def test_price_bar_revision_is_unique(workbench_db):
    import psycopg
    _bar(workbench_db)
    _bar(workbench_db, revision=2, close=103)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _bar(workbench_db, revision=2)


def test_split_factor_must_be_positive(workbench_db):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        _bar(workbench_db, split_factor=0)


def test_candidate_action_and_uniqueness(workbench_db):
    import psycopg
    sql = ('INSERT INTO signal_candidates (ticker, signal_date, action, rule_id, rule_version, inputs) '
           "VALUES ('NVDA', '2026-10-21', %s, 'trend_reclaim', '1', '{}')")
    workbench_db.execute(sql, ['buy'])
    with pytest.raises(psycopg.errors.UniqueViolation):
        workbench_db.execute(sql, ['buy'])


def test_candidate_rejects_unknown_action(workbench_db):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        workbench_db.execute("INSERT INTO signal_candidates (ticker, signal_date, action, rule_id, rule_version, inputs) "
                             "VALUES ('NVDA', '2026-10-21', 'short', 'x', '1', '{}')")


def _decision(conn, reason='trend intact', plan_id=None):
    return conn.execute(
        'INSERT INTO signal_decisions (request_id, request_hash, ticker, signal_date, candidate_ids, '
        "proposed_action, final_action, decision, reason, plan_id) VALUES (%s, 'h', 'NVDA', '2026-10-21', "
        "'{}', 'buy', 'buy', 'accept', %s, %s) RETURNING id", [uuid.uuid4(), reason, plan_id]).fetchone()[0]


def test_decision_requires_reason(workbench_db):
    import psycopg
    assert _decision(workbench_db)
    with pytest.raises(psycopg.errors.CheckViolation):
        _decision(workbench_db, reason='   ')


def test_trade_constraints(workbench_db):
    import psycopg
    decision_id = _decision(workbench_db)
    sql = ('INSERT INTO trades (request_id, request_hash, ticker, side, quantity, price, executed_at, decision_id) '
           "VALUES (%s, 'h', 'NVDA', 'buy', %s, 101.5, %s, %s)")
    workbench_db.execute(sql, [uuid.uuid4(), 10, NOW, decision_id])
    with pytest.raises(psycopg.errors.CheckViolation):
        workbench_db.execute(sql, [uuid.uuid4(), 0, NOW, decision_id])


def test_trade_void_needs_reason(workbench_db):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        workbench_db.execute(
            'INSERT INTO trades (request_id, request_hash, ticker, side, quantity, price, executed_at, voided_at) '
            "VALUES (%s, 'h', 'NVDA', 'sell', 1, 1, %s, %s)", [uuid.uuid4(), NOW, NOW])


def test_weekly_review_starts_on_monday(workbench_db):
    import psycopg
    sql = "INSERT INTO weekly_reviews (week_start, calc_version, stats) VALUES (%s, 'review-1', '{}')"
    workbench_db.execute(sql, [date(2026, 10, 19)])
    with pytest.raises(psycopg.errors.CheckViolation):
        workbench_db.execute(sql, [date(2026, 10, 20)])


def test_filings_columns_and_scheduled_job(workbench_db):
    workbench_db.execute(
        "INSERT INTO filings (ticker, form, filing_date, accession_number, primary_document, file_path, "
        "accepted_at, items) VALUES ('NVDA', '8-K', '2026-08-27', '0001045810-26-000001', 'a.htm', 'x', %s, %s)",
        [NOW, ['2.02', '9.01']])
    row = workbench_db.execute("SELECT time_local, weekday_mask, timezone FROM scheduled_jobs "
                               "WHERE name = 'daily'").fetchone()           # 028 replaces the ``workbench`` row
    assert row == ('11:30', '2-6', 'Asia/Shanghai')


def test_upsert_filing_keeps_first_known_acceptance(workbench_db):
    from investment_assistant import db
    args = dict(ticker='NVDA', form='8-K', filing_date=date(2026, 8, 27),
                accession_number='0001045810-26-000010', primary_document='k.htm', file_path='/x')
    db.upsert_filing(workbench_db, **args)
    db.upsert_filing(workbench_db, **args, accepted_at='2026-08-27T16:20:31-04:00', items=['2.02'])
    db.upsert_filing(workbench_db, **args, accepted_at='2026-08-28T09:00:00-04:00', items=['8.01'])
    accepted, items = workbench_db.execute('SELECT accepted_at, items FROM filings').fetchone()
    assert accepted == datetime(2026, 8, 27, 20, 20, 31, tzinfo=UTC)
    assert items == ['2.02']


def _insert(conn, table, values):
    import json
    cols, marks, params = [], [], []
    for key, value in values.items():
        cols.append(key)
        if key in ('sizing', 'inputs'):
            marks.append('%s::jsonb')
            params.append(json.dumps(value))
        else:
            marks.append('%s')
            params.append(value)
    return conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(marks)}) RETURNING id",
                        params).fetchone()[0]


def _plan(conn, kind='rule', **overrides):
    values = {'ticker': 'NVDA', 'signal_date': date(2026, 10, 21), 'kind': kind, 'plan_version': 'plan-1',
              'ref_price': 100, 'atr': 2, 'entry_low': 99, 'entry_high': 101, 'stop': 96, 'target1': 108,
              'valid_from': date(2026, 10, 22), 'valid_until': date(2026, 10, 28),
              'sizing': {}, 'status': 'ok', 'invalid_reasons': [], 'warnings': [], 'inputs': {}}
    if kind == 'own':
        values['request_id'] = overrides.pop('request_id', uuid.uuid4())
        values['request_hash'] = overrides.pop('request_hash', 'h')
    values.update(overrides)
    return _insert(conn, 'trade_plans', values)


def _flow(conn, **overrides):
    values = {'request_id': uuid.uuid4(), 'request_hash': 'h',
              'flow_date': date(2026, 10, 21), 'amount': 1000, 'note': None, 'recorded_at': NOW}
    values.update(overrides)
    return _insert(conn, 'cash_flows', values)


def _snapshot(conn, **overrides):
    values = {'request_id': uuid.uuid4(), 'request_hash': 'h', 'as_of': date(2026, 10, 21),
              'equity': 100000, 'note': None, 'recorded_at': NOW}
    values.update(overrides)
    return _insert(conn, 'account_snapshots', values)


def test_rule_plan_unique_per_version(workbench_db):
    import psycopg
    _plan(workbench_db)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _plan(workbench_db)
    workbench_db.rollback()
    _plan(workbench_db, kind='own')  # own 计划不受 (ticker, signal_date, plan_version) 唯一约束


def test_plan_levels_ordered(workbench_db):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        _plan(workbench_db, stop=99)  # stop >= entry_low


def test_cash_flow_nonzero_and_void_pair(workbench_db):
    import psycopg
    _flow(workbench_db)
    with pytest.raises(psycopg.errors.CheckViolation):
        _flow(workbench_db, amount=0)
    workbench_db.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        _flow(workbench_db, voided_at=NOW)  # voided_at 必须与 void_reason 成对


def test_snapshot_equity_positive(workbench_db):
    import psycopg
    _snapshot(workbench_db)
    with pytest.raises(psycopg.errors.CheckViolation):
        _snapshot(workbench_db, equity=0)


def test_decision_plan_fk(workbench_db):
    import psycopg
    plan_id = _plan(workbench_db)
    assert _decision(workbench_db, plan_id=plan_id)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _decision(workbench_db, plan_id=uuid.uuid4())


def test_store_earnings_dedupes(workbench_db):
    # Repository consumers use their owning layers below.
    row_a = {'status': 'known', 'earnings_date': date(2026, 11, 19)}
    out1 = data_repo.store_earnings(workbench_db, 'NVDA', row_a, now=NOW)
    out2 = data_repo.store_earnings(workbench_db, 'NVDA', row_a, now=NOW + timedelta(hours=1))
    assert out1['status'] == 'inserted'
    assert out2['status'] == 'unchanged'
    assert out2['id'] == out1['id']
    assert workbench_db.execute('SELECT count(*) FROM earnings_dates').fetchone()[0] == 1
    out3 = data_repo.store_earnings(workbench_db, 'NVDA',
                                     {'status': 'known', 'earnings_date': date(2027, 2, 18)},
                                     now=NOW + timedelta(hours=2))
    assert out3['status'] == 'inserted'
    assert workbench_db.execute('SELECT count(*) FROM earnings_dates').fetchone()[0] == 2


def test_latest_earnings_respects_cutoff(workbench_db):
    # Repository consumers use their owning layers below.
    data_repo.store_earnings(workbench_db, 'NVDA', {'status': 'known', 'earnings_date': date(2026, 11, 19)},
                              now=NOW)
    data_repo.store_earnings(workbench_db, 'NVDA', {'status': 'known', 'earnings_date': date(2027, 2, 18)},
                              now=NOW + timedelta(hours=1))
    row = data_repo.latest_earnings(workbench_db, 'NVDA', cutoff_at=NOW + timedelta(minutes=30))
    assert row['earnings_date'] == date(2026, 11, 19)
    assert data_repo.latest_earnings(workbench_db, 'NVDA', cutoff_at=NOW + timedelta(hours=2))[
        'earnings_date'] == date(2027, 2, 18)
    assert data_repo.latest_earnings(workbench_db, 'MU', cutoff_at=NOW) is None


def test_pool_tier_check(workbench_db):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        workbench_db.execute("INSERT INTO pool_tiers (ticker, tier, effective_session, recorded_at) "
                             "VALUES ('NVDA', 'x', '2026-10-21', %s)", [NOW])


def test_regime_unique_per_session_version(workbench_db):
    import psycopg
    sql = ("INSERT INTO market_regimes (session, calc_version, label, breadth, sectors, macro, inputs) "
           "VALUES ('2026-10-21', 'regime-1', 'neutral', '{}', '{}', '{}', '{}')")
    workbench_db.execute(sql)
    with pytest.raises(psycopg.errors.UniqueViolation):
        workbench_db.execute(sql)


def test_regime_label_check(workbench_db):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        workbench_db.execute("INSERT INTO market_regimes (session, calc_version, label, breadth, sectors, macro, inputs) "
                             "VALUES ('2026-10-21', 'regime-1', 'risky', '{}', '{}', '{}', '{}')")


def test_macro_value_required(workbench_db):
    import psycopg
    with pytest.raises(psycopg.errors.NotNullViolation):
        workbench_db.execute("INSERT INTO macro_observations (series_id, obs_date, value, fetched_at) "
                             "VALUES ('BAMLH0A0HYM2', '2026-10-21', NULL, %s)", [NOW])


def test_missing_tables_lists_018(workbench_db):
    # Repository consumers use their owning layers below.
    assert core_dbutil.missing_tables(workbench_db) == []
    workbench_db.execute('DROP TABLE market_regimes')
    assert 'market_regimes' in core_dbutil.missing_tables(workbench_db)


def test_store_macro_revision_appends(workbench_db):
    from decimal import Decimal
    # Repository consumers use their owning layers below.
    rows = [{'obs_date': date(2026, 10, 20), 'value': Decimal('3.1')},
            {'obs_date': date(2026, 10, 21), 'value': Decimal('3.2')}]
    assert data_repo.store_macro(workbench_db, 'BAMLH0A0HYM2', rows, now=NOW) == {'inserted': 2, 'unchanged': 0}
    assert data_repo.store_macro(workbench_db, 'BAMLH0A0HYM2', rows, now=NOW) == {'inserted': 0, 'unchanged': 2}
    revised = [{'obs_date': date(2026, 10, 20), 'value': Decimal('3.15')}]
    assert data_repo.store_macro(workbench_db, 'BAMLH0A0HYM2', revised, now=NOW + timedelta(hours=1)) == \
        {'inserted': 1, 'unchanged': 0}
    read = data_repo.read_macro(workbench_db, 'BAMLH0A0HYM2', end_date=date(2026, 10, 23), cutoff_at=NOW + timedelta(hours=2))
    assert [(r['obs_date'], r['value']) for r in read] == \
        [(date(2026, 10, 20), Decimal('3.15')), (date(2026, 10, 21), Decimal('3.2'))]


def test_read_macro_as_of_cutoff(workbench_db):
    from decimal import Decimal
    # Repository consumers use their owning layers below.
    data_repo.store_macro(workbench_db, 'T10Y2Y', [{'obs_date': date(2026, 10, 20), 'value': Decimal('0.1')}],
                           now=NOW)
    data_repo.store_macro(workbench_db, 'T10Y2Y', [{'obs_date': date(2026, 10, 20), 'value': Decimal('0.2')}],
                           now=NOW + timedelta(days=1))
    as_of_now = data_repo.read_macro(workbench_db, 'T10Y2Y', end_date=date(2026, 10, 23), cutoff_at=NOW + timedelta(hours=1))
    assert as_of_now[0]['value'] == Decimal('0.1')
    as_of_next_day = data_repo.read_macro(workbench_db, 'T10Y2Y', end_date=date(2026, 10, 23),
                                           cutoff_at=NOW + timedelta(days=2))
    assert as_of_next_day[0]['value'] == Decimal('0.2')
    assert data_repo.read_macro(workbench_db, 'T10Y2Y', end_date=date(2026, 10, 19), cutoff_at=NOW + timedelta(days=2)) == []


def _agent_run(conn, plan_id=None, **overrides):
    import json
    values = {'id': uuid.uuid4(), 'ticker': 'NVDA', 'signal_date': date(2026, 10, 21), 'pool': 'main',
              'attempt': 1, 'agent_version': 'agent-1', 'provider': 'chatgpt', 'model': 'gpt-test',
              'cutoff_at': NOW, 'status': 'rejected', 'reason': 'malformed', 'output': None, 'counter': None,
              'plan_id': plan_id, 'input_tokens': 0, 'output_tokens': 0,
              'started_at': NOW, 'finished_at': NOW}
    values.update(overrides)
    cols, marks, params = [], [], []
    for key, value in values.items():
        cols.append(key)
        if key in ('output', 'counter'):
            marks.append('%s::jsonb')
            params.append(json.dumps(value) if value is not None else None)
        else:
            marks.append('%s')
            params.append(value)
    return conn.execute(f"INSERT INTO agent_runs ({', '.join(cols)}) VALUES ({', '.join(marks)}) RETURNING id",
                        params).fetchone()[0]


def _tool_call(conn, run_id, seq=1, **overrides):
    import json
    values = {'run_id': run_id, 'seq': seq, 'phase': 'propose', 'tool': 'get_price_bars',
              'args': {}, 'result': {'ref': 'call:1', 'data': {}}, 'result_sha256': 'a' * 64, 'called_at': NOW}
    values.update(overrides)
    cols, marks, params = [], [], []
    for key, value in values.items():
        cols.append(key)
        if key in ('args', 'result'):
            marks.append('%s::jsonb')
            params.append(json.dumps(value))
        else:
            marks.append('%s')
            params.append(value)
    conn.execute(f"INSERT INTO agent_tool_calls ({', '.join(cols)}) VALUES ({', '.join(marks)})", params)


def test_agent_plan_kind_allowed(workbench_db):
    assert _plan(workbench_db, kind='agent', plan_version='agent-1')


def test_agent_plan_unique_per_unit(workbench_db):
    import psycopg
    _plan(workbench_db, kind='agent', plan_version='agent-1')
    with pytest.raises(psycopg.errors.UniqueViolation):
        _plan(workbench_db, kind='agent', plan_version='agent-1')
    workbench_db.rollback()
    assert _plan(workbench_db)  # rule 计划不受 agent 部分唯一索引影响


def test_agent_run_ok_requires_plan(workbench_db):
    import psycopg
    plan_id = _plan(workbench_db, kind='agent', plan_version='agent-1')
    assert _agent_run(workbench_db, status='ok', reason=None, plan_id=plan_id, output={'action': 'buy'})
    with pytest.raises(psycopg.errors.CheckViolation):
        _agent_run(workbench_db, status='ok', reason=None)  # ok 无 plan_id


def test_agent_run_reason_required(workbench_db):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        _agent_run(workbench_db, status='rejected', reason=None)


def test_tool_call_seq_unique(workbench_db):
    import psycopg
    run_id = _agent_run(workbench_db)
    _tool_call(workbench_db, run_id, seq=1)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _tool_call(workbench_db, run_id, seq=1)


def test_missing_tables_lists_019(workbench_db):
    # Repository consumers use their owning layers below.
    assert core_dbutil.missing_tables(workbench_db) == []
    workbench_db.execute('DROP TABLE agent_tool_calls')
    assert 'agent_tool_calls' in core_dbutil.missing_tables(workbench_db)


def test_agent_run_mode_defaults_propose(workbench_db):
    run_id = _agent_run(workbench_db)
    mode = workbench_db.execute('SELECT mode FROM agent_runs WHERE id = %s', [run_id]).fetchone()[0]
    assert mode == 'propose'


def test_interpret_ok_without_plan_allowed(workbench_db):
    assert _agent_run(workbench_db, mode='interpret', status='ok', reason=None, output={'summary': 's'})


def test_propose_ok_without_plan_rejected(workbench_db):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        _agent_run(workbench_db, status='ok', reason=None)


def test_interpret_pass_rejected(workbench_db):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        _agent_run(workbench_db, mode='interpret', status='pass', reason=None)


def test_old_ok_plan_constraint_dropped(workbench_db):
    old_def = "(status = 'ok'::text) = (plan_id IS NOT NULL)"
    count = workbench_db.execute(
        "SELECT count(*) FROM pg_constraint WHERE conrelid = 'agent_runs'::regclass AND contype = 'c' "
        'AND pg_get_constraintdef(oid) LIKE %s AND pg_get_constraintdef(oid) NOT LIKE %s',
        [f'%{old_def}%', '%mode%']).fetchone()[0]
    assert count == 0


def test_brief_agent_run_fk(workbench_db):
    import psycopg
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        workbench_db.execute(
            'INSERT INTO signal_briefs (ticker, signal_date, model, prompt_version, cutoff_at, '
            'input_refs, output, status, agent_run_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
            ['NVDA', date(2026, 10, 21), 'gpt-test', 'agent-brief-1', NOW, '[]', '{}', 'ok', uuid.uuid4()])
    workbench_db.rollback()


def test_review_push_reason_required(workbench_db):
    import psycopg
    workbench_db.execute(
        'INSERT INTO review_pushes (kind, period, trigger_source, sent, messages, reason) '
        "VALUES ('weekly', %s, 'manual', false, 0, 'send_failed: boom')", [date(2026, 10, 19)])
    with pytest.raises(psycopg.errors.CheckViolation):
        workbench_db.execute(
            'INSERT INTO review_pushes (kind, period, trigger_source, sent, messages) '
            "VALUES ('weekly', %s, 'manual', false, 0)", [date(2026, 10, 26)])
    workbench_db.rollback()


def test_review_job_merged_into_daily(workbench_db):
    # 020 inserted ``workbench_review`` (08:00, disabled); 028 merges the three tasks into ``daily``
    names = {r[0] for r in workbench_db.execute('SELECT name FROM scheduled_jobs')}
    assert 'workbench_review' not in names and 'workbench' not in names and 'filings' not in names
    row = workbench_db.execute('SELECT time_local, weekday_mask, timezone FROM scheduled_jobs '
                               "WHERE name = 'daily'").fetchone()
    assert row == ('11:30', '2-6', 'Asia/Shanghai')


def test_missing_tables_lists_review_pushes(workbench_db):
    # Repository consumers use their owning layers below.
    workbench_db.execute('DROP TABLE review_pushes')
    assert 'review_pushes' in core_dbutil.missing_tables(workbench_db)


# --- mode-filtered run queries and brief run links (P4 §3.1–3.2, §7) ---


# --- ordered sensitivity, tracks and detail in monthly-2 (P4 §5) ---


def _fetch_run(**overrides):
    base = {'family': 'bars', 'job': 'workbench', 'trigger': 'scheduled', 'run_id': 'r1',
            'scope': 'family', 'ticker': None, 'session': date(2026, 10, 23), 'status': 'success',
            'target_count': 2, 'ok_count': 2, 'failures': [], 'detail': {},
            'started_at': NOW, 'finished_at': NOW + timedelta(minutes=5)}
    base.update(overrides)
    return base


def test_fetch_run_round_trip(workbench_db):
    # Repository consumers use their owning layers below.
    conn = workbench_db
    data_repo.insert_fetch_run(conn, _fetch_run(status='failed', ok_count=0,
                                                 failures=[{'target': '*', 'reason': 'yahoo down'}]))
    latest_bars = data_repo.insert_fetch_run(conn, _fetch_run(run_id='r2'))
    data_repo.insert_fetch_run(conn, _fetch_run(family='fred', status='skipped', target_count=0, ok_count=0,
                                                 detail={'reason': 'no FRED_API_KEY'}))
    assert latest_bars['family'] == 'bars' and latest_bars['status'] == 'success'
    assert latest_bars['failures'] == [] and latest_bars['created_at'] is not None
    assert data_repo.latest_fetch_runs(conn)['bars']['status'] == 'success'
    runs = data_repo.fetch_runs(conn, 'bars')
    assert [r['status'] for r in runs] == ['success', 'failed']  # newest first
    assert list(data_repo.last_success_at(conn)) == ['bars']


def test_fetch_runs_table_not_required_by_workbench(workbench_db):
    # Repository consumers use their owning layers below.
    assert 'data_fetch_runs' not in core_dbutil.WORKBENCH_TABLES
    workbench_db.execute('DROP TABLE data_fetch_runs CASCADE')    # 031's data_requests references it
    assert core_dbutil.missing_tables(workbench_db) == []
    assert core_schema.fetch_runs_ready(workbench_db) is False


# --- stage runs (v2.4 §5) ---


def test_stage_runs_ready_false_without_table(workbench_db):
    # Repository consumers use their owning layers below.
    assert core_schema.stage_runs_ready(workbench_db) is True
    workbench_db.execute('DROP TABLE stage_runs')
    assert core_dbutil.missing_tables(workbench_db) == []
    assert core_schema.stage_runs_ready(workbench_db) is False


# --- family targets and coverage queries (v2.3 §4/§8) ---


def test_coverage_queries_take_latest_revision_and_cutoff(workbench_db):
    from decimal import Decimal
    # Repository consumers use their owning layers below.
    conn = workbench_db
    cutoff = NOW + timedelta(minutes=90)
    _bar(conn, price_date=date(2026, 10, 16), close=100, fetched_at=NOW)
    _bar(conn, price_date=date(2026, 10, 19), close=104, fetched_at=NOW)
    _bar(conn, price_date=date(2026, 10, 19), close=103, revision=2, fetched_at=NOW + timedelta(hours=1))
    _bar(conn, price_date=date(2026, 10, 20), close=105, fetched_at=NOW + timedelta(hours=2))
    # 同一日期的两个修订算一根；fetched_at 晚于 cutoff 的行整行不可见；无数据的 MU 不出现在结果里
    assert data_repo.latest_bar_dates(conn, ['NVDA', 'MU'], cutoff_at=cutoff) == {'NVDA': date(2026, 10, 19)}
    assert data_repo.bar_counts(conn, ['NVDA'], end_date=date(2026, 10, 23), cutoff_at=cutoff) == {'NVDA': 2}
    assert data_repo.bar_counts(conn, ['NVDA'], end_date=date(2026, 10, 17), cutoff_at=cutoff) == {'NVDA': 1}

    data_repo.store_news(conn, [
        ({'source': 'yfinance', 'title': 'a', 'url': 'u-a', 'publisher': None,
          'published_at': datetime(2026, 10, 21, 12, tzinfo=UTC)}, ['NVDA']),
        ({'source': 'yfinance', 'title': 'b', 'url': 'u-b', 'publisher': None, 'published_at': None}, ['MU']),
    ], now=NOW)
    data_repo.store_news(conn, [
        ({'source': 'yfinance', 'title': 'late', 'url': 'u-c', 'publisher': None,
          'published_at': datetime(2026, 10, 22, 12, tzinfo=UTC)}, ['MU']),
    ], now=NOW + timedelta(hours=2))
    # 无 published_at 用 fetched_at；晚于 cutoff 抓到的行不算
    assert data_repo.latest_news_at(conn, ['NVDA', 'MU'], cutoff_at=cutoff) == {
        'NVDA': datetime(2026, 10, 21, 12, tzinfo=UTC), 'MU': NOW}

    conn.execute(
        'INSERT INTO filings (ticker, form, filing_date, accession_number, primary_document, file_path, created_at) '
        "VALUES ('F1', '10-Q', %s, 'acc-1', 'd.htm', '/x', %s), ('F3', '8-K', %s, 'acc-2', 'd.htm', '/x', %s)",
        [date(2026, 10, 10), NOW, date(2026, 10, 5), NOW])
    data_repo.store_news(conn, [
        ({'source': 'sec_8k', 'title': 'F2 8-K', 'url': 'u-f2', 'publisher': 'SEC EDGAR',
          'published_at': datetime(2026, 10, 15, 14, tzinfo=UTC)}, ['F2']),
        ({'source': 'sec_8k', 'title': 'F3 8-K', 'url': 'u-f3', 'publisher': 'SEC EDGAR',
          'published_at': datetime(2026, 10, 15, 14, tzinfo=UTC)}, ['F3']),
    ], now=NOW)
    # 只有 sec_8k 时也有值；两路都有时取较大者
    assert data_repo.latest_filing_dates(conn, ['F1', 'F2', 'F3'], cutoff_at=cutoff) == {
        'F1': date(2026, 10, 10), 'F2': date(2026, 10, 15), 'F3': date(2026, 10, 15)}
    # 无 accepted_at（published_at 为空）的 sec_8k 不提供申报日依据：宁可漏计不可
    # 用 fetched_at 冒充（I14）。
    data_repo.store_news(conn, [
        ({'source': 'sec_8k', 'title': 'F4 8-K undated', 'url': 'u-f4', 'publisher': 'SEC EDGAR',
          'published_at': None}, ['F4']),
    ], now=NOW)                                                 # 采集时间在窗口内，也不当申报日用
    assert data_repo.latest_filing_dates(conn, ['F4'], cutoff_at=cutoff) == {}

    data_repo.store_macro(conn, 'BAMLH0A0HYM2', [{'obs_date': date(2026, 10, 20), 'value': Decimal('3.1')}],
                           now=NOW)
    data_repo.store_macro(conn, 'BAMLH0A0HYM2', [{'obs_date': date(2026, 10, 21), 'value': Decimal('3.2')}],
                           now=NOW + timedelta(hours=2))
    data_repo.store_macro(conn, 'T10Y2Y', [{'obs_date': date(2026, 10, 21), 'value': Decimal('0.1')}], now=NOW)
    assert data_repo.latest_macro_dates(conn, ['BAMLH0A0HYM2', 'T10Y2Y'], cutoff_at=cutoff) == {
        'BAMLH0A0HYM2': date(2026, 10, 20), 'T10Y2Y': date(2026, 10, 21)}


# --- plan timeline: versions, reviews, user inputs (v2.5 §6) ---
