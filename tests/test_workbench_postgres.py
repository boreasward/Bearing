"""Real constraint behavior for the US workbench schema; disposable schema only."""
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
    from investment_assistant.workbench import repository
    row_a = {'status': 'known', 'earnings_date': date(2026, 11, 19)}
    out1 = repository.store_earnings(workbench_db, 'NVDA', row_a, now=NOW)
    out2 = repository.store_earnings(workbench_db, 'NVDA', row_a, now=NOW + timedelta(hours=1))
    assert out1['status'] == 'inserted'
    assert out2['status'] == 'unchanged'
    assert out2['id'] == out1['id']
    assert workbench_db.execute('SELECT count(*) FROM earnings_dates').fetchone()[0] == 1
    out3 = repository.store_earnings(workbench_db, 'NVDA',
                                     {'status': 'known', 'earnings_date': date(2027, 2, 18)},
                                     now=NOW + timedelta(hours=2))
    assert out3['status'] == 'inserted'
    assert workbench_db.execute('SELECT count(*) FROM earnings_dates').fetchone()[0] == 2


def test_latest_earnings_respects_cutoff(workbench_db):
    from investment_assistant.workbench import repository
    repository.store_earnings(workbench_db, 'NVDA', {'status': 'known', 'earnings_date': date(2026, 11, 19)},
                              now=NOW)
    repository.store_earnings(workbench_db, 'NVDA', {'status': 'known', 'earnings_date': date(2027, 2, 18)},
                              now=NOW + timedelta(hours=1))
    row = repository.latest_earnings(workbench_db, 'NVDA', cutoff_at=NOW + timedelta(minutes=30))
    assert row['earnings_date'] == date(2026, 11, 19)
    assert repository.latest_earnings(workbench_db, 'NVDA', cutoff_at=NOW + timedelta(hours=2))[
        'earnings_date'] == date(2027, 2, 18)
    assert repository.latest_earnings(workbench_db, 'MU', cutoff_at=NOW) is None


def _plan_row(**overrides):
    values = {'ticker': 'NVDA', 'signal_date': date(2026, 10, 21), 'kind': 'rule', 'based_on_id': None,
              'plan_version': 'plan-1', 'ref_price': 100, 'atr': 2, 'entry_low': 99, 'entry_high': 101,
              'stop': 96, 'target1': 108, 'trail_atr_multiple': 3, 'valid_from': date(2026, 10, 22),
              'valid_until': date(2026, 10, 28), 'sizing': {'status': 'no_equity', 'per_share_risk': '4'},
              'status': 'ok', 'invalid_reasons': [], 'warnings': [], 'inputs': {'bar_ids': [1, 2]},
              'request_id': None, 'request_hash': None}
    values.update(overrides)
    return values


def test_store_plan_rule_dedup_conflict_and_reads(workbench_db):
    from investment_assistant.workbench import repository
    plan = _plan_row()
    out1 = repository.store_plan(workbench_db, plan)
    assert out1['status'] == 'inserted'
    assert repository.store_plan(workbench_db, _plan_row())['status'] == 'existing'
    out3 = repository.store_plan(workbench_db, _plan_row(entry_low=98))
    assert out3['status'] == 'conflict' and out3['record']['id'] == out1['record']['id']
    assert workbench_db.execute('SELECT count(*) FROM trade_plans').fetchone()[0] == 1
    head = repository.rule_plan(workbench_db, 'NVDA', date(2026, 10, 21))
    assert head['id'] == out1['record']['id'] and head['stop'] == 96
    assert repository.rule_plan(workbench_db, 'NVDA', date(2026, 10, 21), plan_version='plan-2') is None
    assert repository.plan_by_id(workbench_db, head['id'])['target1'] == 108
    assert len(repository.plans_since(workbench_db, date(2026, 10, 1))) == 1
    assert repository.plans_since(workbench_db, date(2026, 11, 1)) == []


def test_old_weekly_snapshot_unchanged(workbench_db, monkeypatch):
    from contextlib import contextmanager

    from investment_assistant.workbench import repository, review, service

    @contextmanager
    def connection():
        yield workbench_db

    monkeypatch.setattr(service, 'connection', connection)
    old = repository.insert_weekly_review(workbench_db, date(2026, 10, 19), 'review-1', {'units': 3}, '',
                                          None)
    assert old['calc_version'] == 'review-1'
    assert repository.weekly_head(workbench_db, date(2026, 10, 19))['stats'] == {'units': 3}


def test_monthly_frozen_and_supersede(workbench_db, monkeypatch):
    import uuid as _uuid
    from contextlib import contextmanager
    from datetime import UTC, datetime

    from investment_assistant.workbench import ledger, service

    @contextmanager
    def connection():
        yield workbench_db

    monkeypatch.setattr(service, 'connection', connection)
    now = datetime(2026, 11, 1, 22, 30, tzinfo=UTC)
    first = service.generate_monthly(date(2026, 10, 1), notes='初版', now=now)
    assert first['stats']['calc_version'] == 'monthly-5'
    assert first['stats']['return'] == {
        'status': 'missing_snapshot', 'source': 'snapshot', 'start': None, 'end': None,
        'net_flow': None, 'return': None}
    with pytest.raises(ledger.Conflict):
        service.generate_monthly(date(2026, 10, 1), notes='重复生成', now=now)
    fix = service.generate_monthly(date(2026, 10, 1), notes='更正', supersedes_id=first['id'], now=now)
    assert fix['supersedes_id'] == first['id'] and fix['notes'] == '更正'
    listed = service.list_monthly()
    assert [r['id'] for r in listed] == [fix['id']]
    assert workbench_db.execute('SELECT count(*) FROM monthly_reviews').fetchone()[0] == 2


def test_monthly_ignores_stop_events_outside_the_month(workbench_db, monkeypatch):
    from contextlib import contextmanager
    from datetime import UTC, date, datetime
    from decimal import Decimal as D

    from investment_assistant.workbench import repository, rules, service

    @contextmanager
    def connection():
        yield workbench_db

    monkeypatch.setattr(service, 'connection', connection)
    unit_day = date(2026, 10, 21)
    plan = repository.insert_plan(workbench_db, {
        'ticker': 'NVDA', 'signal_date': unit_day, 'kind': 'rule', 'based_on_id': None, 'plan_version': 'plan-1',
        'ref_price': D('100'), 'atr': D('2'), 'entry_low': D('99'), 'entry_high': D('101'), 'stop': D('94'),
        'target1': D('108'), 'trail_atr_multiple': D('3'), 'valid_from': date(2026, 10, 22),
        'valid_until': date(2026, 10, 27), 'sizing': {}, 'status': 'ok', 'invalid_reasons': [], 'warnings': [],
        'inputs': {}, 'request_id': None, 'request_hash': None})
    decision = repository._insert_decision_for_tests(workbench_db, {
        'request_id': uuid.uuid4(), 'ticker': 'NVDA', 'signal_date': unit_day,
        'candidate_ids': [], 'brief_id': None, 'proposed_action': 'buy', 'final_action': 'buy',
        'decision': 'accept', 'reason': '合成', 'supersedes_id': None, 'plan_id': plan['id']},
        'h', decided_at=datetime(2026, 10, 22, tzinfo=UTC))
    repository.insert_trade(workbench_db, {
        'request_id': uuid.uuid4(), 'ticker': 'NVDA', 'side': 'buy', 'quantity': D('10'), 'price': D('100'),
        'fee': D('0'), 'executed_at': datetime(2026, 10, 22, 15, tzinfo=UTC), 'decision_id': decision['id'],
        'note': None}, 'h', recorded_at=datetime(2026, 10, 22, 16, tzinfo=UTC))
    repository.store_candidate(workbench_db, 'NVDA', date(2026, 11, 10),
                               rules.Candidate('plan_stop', 'sell', {'signal_date': '2026-11-10'}), run_id='r')

    review = service.generate_monthly(date(2026, 10, 1), now=datetime(2026, 11, 20, 22, 30, tzinfo=UTC))
    assert review['stats']['discipline']['counts']['held_loser'] == 0  # 11 月的止损事件不算 10 月


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
    from investment_assistant.workbench import repository
    assert repository.missing_tables(workbench_db) == []
    workbench_db.execute('DROP TABLE market_regimes')
    assert 'market_regimes' in repository.missing_tables(workbench_db)


def _screen_run(session, created_at, **overrides):
    base = {'session': session, 'calc_version': 'screen-1', 'universe_name': 'nasdaq100',
            'universe_as_of': date(2026, 10, 1), 'cutoff_at': created_at, 'status': 'success',
            'summary': {'counts': {}}, 'run_id': 'r1', 'created_at': created_at}
    base.update(overrides)
    return base


def _screen_row(ticker, **overrides):
    base = {'ticker': ticker, 'group': 'setup', 'trigger_rules': [], 'filters': {'data': 'ok'},
            'rs': 0.05, 'atr_pct': 0.04, 'dollar_volume_20': 6e8, 'rank': 1, 'selected': True}
    base.update(overrides)
    return base


def test_screen_run_roundtrip_and_latest_per_session(workbench_db):
    from investment_assistant.workbench import repository
    s1 = date(2026, 10, 21)
    t1, t2 = NOW, NOW + timedelta(hours=1)
    stored1 = repository.insert_screen_run(workbench_db, _screen_run(s1, t1),
                                           [_screen_row('A', group='trigger', trigger_rules=['trend_reclaim'], rank=1),
                                            _screen_row('B', rank=2)])
    assert stored1['session'] == s1 and stored1['status'] == 'success'
    rows = repository.screen_results(workbench_db, stored1['id'])
    assert [r['ticker'] for r in rows] == ['A', 'B']
    assert rows[0]['group'] == 'trigger' and rows[0]['trigger_rules'] == ['trend_reclaim']
    assert rows[0]['selected'] is True and rows[1]['selected'] is True
    stored2 = repository.insert_screen_run(workbench_db, _screen_run(s1, t2, run_id='r2'), [])
    assert repository.latest_screen_run(workbench_db, session=s1,
                                        cutoff_at=t2 + timedelta(hours=1))['id'] == stored2['id']
    assert repository.latest_screen_run(workbench_db, session=s1,
                                        cutoff_at=t1 + timedelta(minutes=1))['id'] == stored1['id']
    assert repository.latest_screen_run(workbench_db, session=None,
                                        cutoff_at=t2 + timedelta(hours=1))['id'] == stored2['id']
    assert repository.latest_screen_run(workbench_db, session=date(2026, 10, 20),
                                        cutoff_at=t2 + timedelta(hours=1)) is None


def test_screen_selected_since_uses_latest_run_per_session(workbench_db):
    from investment_assistant.workbench import repository
    s1, s2 = date(2026, 10, 21), date(2026, 10, 22)
    t1, t2 = NOW, NOW + timedelta(hours=1)
    repository.insert_screen_run(workbench_db, _screen_run(s1, t1),
                                 [_screen_row('A', rank=1), _screen_row('B', rank=2)])
    # Rerun of S1: only B is selected this time; the earlier A pick no longer counts.
    repository.insert_screen_run(workbench_db, _screen_run(s1, t2), [_screen_row('B', rank=1)])
    repository.insert_screen_run(workbench_db, _screen_run(s2, t2 + timedelta(hours=1)),
                                 [_screen_row('C', rank=1)])
    got = repository.screen_selected_since(workbench_db, s1, s1, cutoff_at=t2 + timedelta(hours=2))
    assert got == {'B': {'session': s1, 'rank': 1}}
    got = repository.screen_selected_since(workbench_db, s1, s2, cutoff_at=t2 + timedelta(hours=2))
    assert got == {'B': {'session': s1, 'rank': 1}, 'C': {'session': s2, 'rank': 1}}


def _lock_plan(ticker, valid_until, *, status='ok', invalid_reasons=None):
    from decimal import Decimal
    return {'ticker': ticker, 'signal_date': date(2026, 10, 21), 'kind': 'rule', 'ref_price': Decimal('100'),
            'atr': Decimal('2'), 'entry_low': Decimal('98'), 'entry_high': Decimal('101'), 'stop': Decimal('95'),
            'target1': Decimal('110'), 'trail_atr_multiple': 3,
            'valid_from': date(2026, 10, 21), 'valid_until': valid_until,
            'sizing': {'shares': 10}, 'status': status,
            'invalid_reasons': invalid_reasons or [], 'warnings': [], 'inputs': {}}


def _trade(conn, ticker, side, *, executed_at=NOW):
    conn.execute('INSERT INTO trades (request_id, request_hash, ticker, side, quantity, price, executed_at) '
                 "VALUES (%s, 'h', %s, %s, 10, 100, %s)", [uuid.uuid4(), ticker, side, executed_at])


def test_locked_screen_tickers(workbench_db):
    from investment_assistant.workbench import repository
    session = date(2026, 10, 21)
    cutoff = datetime.now(UTC) + timedelta(hours=1)
    repository.insert_plan(workbench_db, _lock_plan('OKP', session + timedelta(days=5)))
    repository.insert_plan(workbench_db, _lock_plan('INVP', session + timedelta(days=5), status='invalid',
                                               invalid_reasons=['earnings']))
    repository.insert_plan(workbench_db, _lock_plan('EXPP', session - timedelta(days=1)))
    repository.insert_plan(workbench_db, _lock_plan('FUTP', session + timedelta(days=5)))
    workbench_db.execute("UPDATE trade_plans SET created_at = now() + interval '2 hours' WHERE ticker = 'FUTP'")
    _trade(workbench_db, 'HELD', 'buy')
    _trade(workbench_db, 'GONE', 'buy', executed_at=NOW - timedelta(days=1))
    _trade(workbench_db, 'GONE', 'sell')
    assert repository.locked_screen_tickers(workbench_db, session, cutoff_at=cutoff) == {'OKP', 'HELD'}


def test_screen_lockable_tickers(workbench_db):
    from investment_assistant.workbench import repository
    day1, day2 = date(2026, 10, 21), date(2026, 10, 22)
    repository.insert_pool_tiers(workbench_db, [
        {'ticker': 'SCR', 'tier': 'extended', 'sector_etf': None, 'source': 'screen'},
        {'ticker': 'FLIP', 'tier': 'extended', 'sector_etf': None, 'source': 'screen'},
        {'ticker': 'OLD', 'tier': 'extended', 'sector_etf': None, 'source': 'screen'},
        {'ticker': 'MAN', 'tier': 'core', 'sector_etf': None}], session=day1, now=NOW)
    repository.insert_pool_tiers(workbench_db, [
        {'ticker': 'FLIP', 'tier': 'extended', 'sector_etf': None, 'source': 'watchlist'},
        {'ticker': 'OLD', 'tier': 'removed', 'sector_etf': None, 'source': 'screen'}],
        session=day2, now=NOW + timedelta(days=1))
    # Screen members now, and picks flipped to the watchlist; never the manual-only or removed.
    assert repository.screen_lockable_tickers(workbench_db) == {'SCR', 'FLIP'}


def test_pool_entry_at_respects_effective_session_and_cutoff(workbench_db):
    from datetime import timedelta
    from investment_assistant.workbench import repository
    monday, wednesday, friday = date(2026, 10, 19), date(2026, 10, 21), date(2026, 10, 23)
    assert repository.pool_latest(workbench_db) == {}
    assert repository.insert_pool_tiers(
        workbench_db, [{'ticker': 'NVDA', 'tier': 'extended', 'sector_etf': None}], session=monday, now=NOW) == 1
    assert repository.insert_pool_tiers(
        workbench_db, [{'ticker': 'NVDA', 'tier': 'core', 'sector_etf': 'SMH'}], session=wednesday,
        now=NOW + timedelta(days=4)) == 1
    assert repository.pool_latest(workbench_db) == \
        {'NVDA': {'tier': 'core', 'sector_etf': 'SMH', 'source': 'watchlist'}}
    assert repository.pool_entry_at(workbench_db, 'NVDA', monday, cutoff_at=NOW + timedelta(days=1)) == \
        {'tier': 'extended', 'sector_etf': None, 'source': 'watchlist'}
    assert repository.pool_entry_at(workbench_db, 'NVDA', date(2026, 10, 20), cutoff_at=NOW + timedelta(days=1)) == \
        {'tier': 'extended', 'sector_etf': None, 'source': 'watchlist'}
    assert repository.pool_entry_at(workbench_db, 'NVDA', wednesday, cutoff_at=NOW + timedelta(days=5)) == \
        {'tier': 'core', 'sector_etf': 'SMH', 'source': 'watchlist'}
    # The Wednesday row was recorded Friday; a Thursday cutoff cannot see it yet.
    assert repository.pool_entry_at(workbench_db, 'NVDA', wednesday, cutoff_at=NOW + timedelta(days=2)) == \
        {'tier': 'extended', 'sector_etf': None, 'source': 'watchlist'}
    assert repository.pool_entry_at(workbench_db, 'MU', wednesday, cutoff_at=NOW + timedelta(days=5)) is None


def test_store_macro_revision_appends(workbench_db):
    from decimal import Decimal
    from investment_assistant.workbench import repository
    rows = [{'obs_date': date(2026, 10, 20), 'value': Decimal('3.1')},
            {'obs_date': date(2026, 10, 21), 'value': Decimal('3.2')}]
    assert repository.store_macro(workbench_db, 'BAMLH0A0HYM2', rows, now=NOW) == {'inserted': 2, 'unchanged': 0}
    assert repository.store_macro(workbench_db, 'BAMLH0A0HYM2', rows, now=NOW) == {'inserted': 0, 'unchanged': 2}
    revised = [{'obs_date': date(2026, 10, 20), 'value': Decimal('3.15')}]
    assert repository.store_macro(workbench_db, 'BAMLH0A0HYM2', revised, now=NOW + timedelta(hours=1)) == \
        {'inserted': 1, 'unchanged': 0}
    read = repository.read_macro(workbench_db, 'BAMLH0A0HYM2', end_date=date(2026, 10, 23), cutoff_at=NOW + timedelta(hours=2))
    assert [(r['obs_date'], r['value']) for r in read] == \
        [(date(2026, 10, 20), Decimal('3.15')), (date(2026, 10, 21), Decimal('3.2'))]


def test_read_macro_as_of_cutoff(workbench_db):
    from decimal import Decimal
    from investment_assistant.workbench import repository
    repository.store_macro(workbench_db, 'T10Y2Y', [{'obs_date': date(2026, 10, 20), 'value': Decimal('0.1')}],
                           now=NOW)
    repository.store_macro(workbench_db, 'T10Y2Y', [{'obs_date': date(2026, 10, 20), 'value': Decimal('0.2')}],
                           now=NOW + timedelta(days=1))
    as_of_now = repository.read_macro(workbench_db, 'T10Y2Y', end_date=date(2026, 10, 23), cutoff_at=NOW + timedelta(hours=1))
    assert as_of_now[0]['value'] == Decimal('0.1')
    as_of_next_day = repository.read_macro(workbench_db, 'T10Y2Y', end_date=date(2026, 10, 23),
                                           cutoff_at=NOW + timedelta(days=2))
    assert as_of_next_day[0]['value'] == Decimal('0.2')
    assert repository.read_macro(workbench_db, 'T10Y2Y', end_date=date(2026, 10, 19), cutoff_at=NOW + timedelta(days=2)) == []


def test_store_regime_existing_then_conflict(workbench_db):
    from investment_assistant.workbench import regime, repository
    snap = {'session': date(2026, 10, 21), 'calc_version': regime.REGIME_VERSION, 'label': 'neutral',
            'breadth': {'tickers': 2}, 'sectors': {}, 'macro': {},
            'inputs': {'tickers': ['A', 'B'], 'etfs': [], 'series': []}}
    first = repository.store_regime(workbench_db, snap)
    assert first['status'] == 'inserted'
    assert first['record']['label'] == 'neutral'
    again = repository.store_regime(workbench_db, snap)
    assert again['status'] == 'existing'
    assert again['record']['id'] == first['record']['id']
    changed = {**snap, 'label': 'risk_on', 'breadth': {'tickers': 3}}
    conflict = repository.store_regime(workbench_db, changed)
    assert conflict['status'] == 'conflict'
    assert conflict['record']['id'] == first['record']['id']
    assert conflict['record']['label'] == 'neutral'  # never overwritten
    far = NOW + timedelta(days=365)  # created_at uses the server clock, later than the fake NOW
    assert repository.regime_for(workbench_db, date(2026, 10, 21), cutoff_at=far)['id'] == first['record']['id']
    assert repository.regime_for(workbench_db, date(2026, 10, 21), cutoff_at=NOW) is None
    assert repository.regime_for(workbench_db, date(2026, 10, 22), cutoff_at=far) is None
    assert repository.latest_regime(workbench_db, on_or_before=date(2026, 10, 22), cutoff_at=far)['id'] == \
        first['record']['id']
    assert repository.latest_regime(workbench_db, on_or_before=date(2026, 10, 20), cutoff_at=far) is None


def _pool_week_seed(db, *, tier_rows, candidate_days, plan_days):
    """Bars + pool tiers + buy candidates + rule plans for the by-pool weekly tests."""
    from investment_assistant.config import WorkbenchConfig
    from investment_assistant.workbench import plans, repository
    from investment_assistant.workbench.rules import Candidate
    monday = date(2026, 10, 19)
    repository.insert_pool_tiers(db, tier_rows, session=monday, now=NOW)
    for i in range(50):  # 40 calendar days of warmup before the week, then through the Friday
        _bar(db, price_date=monday - timedelta(days=40) + timedelta(days=i), close=100 + i, high=102 + i,
             low=99 + i, open=100 + i, adj_close=100 + i)
    bars = repository.read_bars(db, 'NVDA', monday + timedelta(days=9), cutoff_at=NOW)
    for day, tier in plan_days:
        upto = [b for b in bars if b['price_date'] <= day]
        plan = plans.build_rule_plan(ticker='NVDA', signal_date=day, bars=upto, earnings_row=None, snapshot=None,
                                     exposure={'ticker_value': 0, 'total_value': 0}, params=WorkbenchConfig(),
                                     context={'pool_tier': tier, 'regime': None, 'sector': None, 'next_earnings': None})
        repository.insert_plan(db, plan)
    for day in candidate_days:
        repository.store_candidate(db, 'NVDA', day, Candidate('trend_reclaim', 'buy', {'signal_date': day.isoformat()}),
                                   run_id='r')


def test_weekly_review3_has_by_pool(workbench_db, monkeypatch):
    from contextlib import contextmanager
    from investment_assistant.workbench import repository, service

    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(service, 'connection', connection)
    _pool_week_seed(workbench_db,
                    tier_rows=[{'ticker': 'NVDA', 'tier': 'extended', 'sector_etf': None}],
                    candidate_days=[date(2026, 10, 20)], plan_days=[(date(2026, 10, 20), 'extended')])
    out = service.generate_weekly(date(2026, 10, 19), now=NOW + timedelta(days=2))
    assert out['calc_version'] == 'review-8'
    row = out['stats']['by_pool']['extended']['trend_reclaim']
    assert (row['triggers'], row['plans']) == (1, 0)                # a rule-only plan is counted under `by_pool_v1`
    older = out['stats']['by_pool_v1']['extended']['trend_reclaim']
    assert (older['triggers'], older['plans']) == (0, 1)        # triggers are counted once, under `by_pool`


def test_tier_change_midweek_uses_signal_date_tier(workbench_db, monkeypatch):
    from contextlib import contextmanager
    from investment_assistant.workbench import repository, service

    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(service, 'connection', connection)
    monday = date(2026, 10, 19)
    repository.insert_pool_tiers(workbench_db, [{'ticker': 'NVDA', 'tier': 'extended', 'sector_etf': None}],
                                 session=monday, now=NOW)
    repository.insert_pool_tiers(workbench_db, [{'ticker': 'NVDA', 'tier': 'core', 'sector_etf': None}],
                                 session=date(2026, 10, 21), now=NOW + timedelta(days=1))
    _pool_week_seed(workbench_db, tier_rows=[], candidate_days=[date(2026, 10, 20), date(2026, 10, 22)],
                    plan_days=[(date(2026, 10, 20), 'extended'), (date(2026, 10, 22), 'core')])
    out = service.generate_weekly(monday, now=NOW + timedelta(days=2))
    by_pool = out['stats']['by_pool']
    assert by_pool['extended']['trend_reclaim']['triggers'] == 1
    assert by_pool['core']['trend_reclaim']['triggers'] == 1


def test_old_review2_snapshot_unchanged(workbench_db, monkeypatch):
    from contextlib import contextmanager
    from investment_assistant.workbench import repository, service

    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(service, 'connection', connection)
    old = repository.insert_weekly_review(workbench_db, date(2026, 10, 12), 'review-2', {'units': 7}, '', None)
    _pool_week_seed(workbench_db,
                    tier_rows=[{'ticker': 'NVDA', 'tier': 'core', 'sector_etf': None}],
                    candidate_days=[date(2026, 10, 20)], plan_days=[(date(2026, 10, 20), 'core')])
    out = service.generate_weekly(date(2026, 10, 19), now=NOW + timedelta(days=2))
    assert out['calc_version'] == 'review-8'
    again = repository.weekly_head(workbench_db, date(2026, 10, 12))
    assert again['id'] == old['id'] and again['calc_version'] == 'review-2' and again['stats'] == {'units': 7}


def test_backcheck_makes_no_network_calls(workbench_db, monkeypatch):
    from contextlib import contextmanager
    from investment_assistant.workbench import bars, earnings, macro, news, repository, service

    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(service, 'connection', connection)

    def boom(*a, **kw):
        raise AssertionError('network call during backcheck')
    monkeypatch.setattr(bars, 'fetch_history', boom)
    monkeypatch.setattr(news, 'fetch_yfinance', boom)
    monkeypatch.setattr(earnings, 'fetch_next', boom)
    monkeypatch.setattr(macro, 'fetch_series', boom)
    workbench_db.execute("INSERT INTO watchlist_items (ticker, status, tags) VALUES ('NVDA', 'active', '{}')")
    for i in range(260):
        _bar(workbench_db, price_date=date(2026, 1, 5) + timedelta(days=i), close=100 + i, high=102 + i,
             low=99 + i, open=100 + i, adj_close=100 + i)
    record = service.run_backcheck(now=NOW)
    assert record['calc_version'] == 'backcheck-2'
    assert record['params']['tickers'] == ['NVDA']
    assert record['stats']['all']['ticker_sessions'] == 260 - 200
    assert repository.list_backchecks(workbench_db, 10)[0]['id'] == record['id']


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
    from investment_assistant.workbench import repository
    assert repository.missing_tables(workbench_db) == []
    workbench_db.execute('DROP TABLE agent_tool_calls')
    assert 'agent_tool_calls' in repository.missing_tables(workbench_db)


def test_postgres_saver_setup_in_disposable_schema(workbench_db):
    import os
    from investment_assistant.workbench.agent import model
    base = os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']
    schema = f'cp_{uuid.uuid4().hex[:8]}'
    workbench_db.execute(f'CREATE SCHEMA {schema}')
    workbench_db.commit()   # setup_checkpoints uses its own connection: the schema must be visible
    try:
        model.setup_checkpoints(f'{base}?options=-csearch_path%3D{schema}')
        workbench_db.execute(f'SET search_path TO {schema}')
        assert workbench_db.execute("SELECT to_regclass('checkpoints')").fetchone()[0] is not None
    finally:
        workbench_db.rollback()
        workbench_db.execute(f'DROP SCHEMA {schema} CASCADE')
        workbench_db.commit()


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
    from investment_assistant.workbench import repository
    workbench_db.execute('DROP TABLE review_pushes')
    assert 'review_pushes' in repository.missing_tables(workbench_db)


# --- mode-filtered run queries and brief run links (P4 §3.1–3.2, §7) ---

def test_latest_agent_run_filters_mode(workbench_db):
    from investment_assistant.workbench import repository
    propose_id = _agent_run(workbench_db, status='skipped', reason='disabled')
    interpret_id = _agent_run(workbench_db, mode='interpret', status='ok', reason=None,
                              output={'summary': 's'})
    default = repository.latest_agent_run(workbench_db, 'NVDA', date(2026, 10, 21))
    assert default['id'] == propose_id       # 后写的 interpret 运行不改变 propose 视图
    interpret = repository.latest_agent_run(workbench_db, 'NVDA', date(2026, 10, 21), mode='interpret')
    assert interpret['id'] == interpret_id


def test_agent_runs_for_counts_per_mode(workbench_db):
    from investment_assistant.workbench import repository
    _agent_run(workbench_db)
    _agent_run(workbench_db, status='skipped', reason='disabled')
    _agent_run(workbench_db, mode='interpret', status='ok', reason=None, output={'summary': 'a'})
    _agent_run(workbench_db, mode='interpret', status='ok', reason=None, output={'summary': 'b'})
    propose = repository.agent_runs_for(workbench_db, 'NVDA', date(2026, 10, 21))
    assert len(propose) == 2 and all(r['mode'] == 'propose' for r in propose)
    interpret = repository.agent_runs_for(workbench_db, 'NVDA', date(2026, 10, 21), mode='interpret')
    assert len(interpret) == 2 and all(r['mode'] == 'interpret' for r in interpret)


def test_brief_round_trips_agent_run_id(workbench_db):
    from investment_assistant.workbench import repository
    run_id = _agent_run(workbench_db, mode='interpret', status='ok', reason=None, output={'summary': 's'})
    brief = {'ticker': 'NVDA', 'signal_date': date(2026, 10, 21), 'model': 'gpt-test',
             'prompt_version': 'agent-brief-1', 'cutoff_at': NOW, 'input_refs': ['call:1'],
             'output': {'summary': 's'}, 'status': 'ok', 'error': None, 'agent_run_id': run_id}
    repository.store_brief(workbench_db, brief)
    stored = repository.latest_brief(workbench_db, 'NVDA', date(2026, 10, 21))
    assert stored['agent_run_id'] == run_id and stored['status'] == 'ok'


# --- ordered sensitivity, tracks and detail in monthly-2 (P4 §5) ---

def _mt_plan(conn, ticker, day, stop, entry=None):
    from decimal import Decimal as D
    from investment_assistant.workbench import repository
    entry = entry if entry is not None else D('100')
    return repository.insert_plan(conn, {
        'ticker': ticker, 'signal_date': day, 'kind': 'rule', 'based_on_id': None,
        'plan_version': 'plan-1', 'ref_price': entry, 'atr': D('2'), 'entry_low': entry - D('1'),
        'entry_high': entry + D('1'), 'stop': stop, 'target1': entry + D('8'),
        'trail_atr_multiple': D('3'), 'valid_from': day, 'valid_until': day + timedelta(days=5),
        'sizing': {}, 'status': 'ok', 'invalid_reasons': [], 'warnings': [], 'inputs': {},
        'request_id': None, 'request_hash': None})


def _mt_round_trip(conn, ticker, day, plan_id, *, sell_day, sell_price):
    from decimal import Decimal as D
    from investment_assistant.workbench import repository
    decision = repository._insert_decision_for_tests(conn, {
        'request_id': uuid.uuid4(), 'ticker': ticker, 'signal_date': day, 'candidate_ids': [],
        'brief_id': None, 'proposed_action': 'buy', 'final_action': 'buy', 'decision': 'accept',
        'reason': '合成', 'supersedes_id': None, 'plan_id': plan_id}, 'h',
        decided_at=datetime(2026, 10, 1, tzinfo=UTC))
    repository.insert_trade(conn, {
        'request_id': uuid.uuid4(), 'ticker': ticker, 'side': 'buy', 'quantity': D('10'),
        'price': D('100'), 'fee': D('0'), 'executed_at': datetime(day.year, day.month, day.day, 15, tzinfo=UTC),
        'decision_id': decision['id'], 'note': None}, 'h', recorded_at=datetime(day.year, day.month, day.day, 16, tzinfo=UTC))
    repository.insert_trade(conn, {
        'request_id': uuid.uuid4(), 'ticker': ticker, 'side': 'sell', 'quantity': D('10'),
        'price': sell_price, 'fee': D('0'),
        'executed_at': datetime(sell_day.year, sell_day.month, sell_day.day, 15, tzinfo=UTC),
        'decision_id': None, 'note': None}, 'h',
        recorded_at=datetime(sell_day.year, sell_day.month, sell_day.day, 16, tzinfo=UTC))


def test_monthly_sensitivity_ordered_by_close_time(workbench_db, monkeypatch):
    import pytest as _pytest
    from contextlib import contextmanager
    from investment_assistant.workbench import service

    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(service, 'connection', connection)
    from decimal import Decimal as D
    # 平仓时间序：A(+2R) 10-05、B(−1R) 10-06、A(+2R) 10-07、B(−3R) 10-08。
    # 按时间 [2, −1, 2, −3] 累计回撤 3R；monthly-1 的拼接序 [2, 2, −1, −3] 会得到 4R（低估被消除）。
    plan_a = _mt_plan(workbench_db, 'A', date(2026, 10, 1), D('95'))
    plan_b = _mt_plan(workbench_db, 'B', date(2026, 10, 1), D('90'))
    _mt_round_trip(workbench_db, 'A', date(2026, 10, 1), plan_a['id'], sell_day=date(2026, 10, 5), sell_price=D('110'))
    _mt_round_trip(workbench_db, 'B', date(2026, 10, 1), plan_b['id'], sell_day=date(2026, 10, 6), sell_price=D('90'))
    _mt_round_trip(workbench_db, 'A', date(2026, 10, 2), plan_a['id'], sell_day=date(2026, 10, 7), sell_price=D('110'))
    _mt_round_trip(workbench_db, 'B', date(2026, 10, 2), plan_b['id'], sell_day=date(2026, 10, 8), sell_price=D('70'))
    stats = service.generate_monthly(date(2026, 10, 1), now=datetime(2026, 11, 20, 22, 30, tzinfo=UTC))['stats']
    row = next(r for r in stats['sensitivity']['rows'] if r['risk_pct'] == 0.02)
    assert row['max_drawdown'] == _pytest.approx(3 * 0.02)
    assert stats['sensitivity']['count'] == 4


def test_monthly_snapshot_has_tracks_and_detail(workbench_db, monkeypatch):
    from contextlib import contextmanager
    from investment_assistant.workbench import service

    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(service, 'connection', connection)
    from decimal import Decimal as D
    plan = _mt_plan(workbench_db, 'NVDA', date(2026, 10, 1), D('95'))
    _mt_round_trip(workbench_db, 'NVDA', date(2026, 10, 1), plan['id'],
                   sell_day=date(2026, 10, 5), sell_price=D('110'))
    stats = service.generate_monthly(date(2026, 10, 1), now=datetime(2026, 11, 20, 22, 30, tzinfo=UTC))['stats']
    assert stats['calc_version'] == 'monthly-5'
    assert stats['return']['source'] == 'snapshot'
    assert 'units' in stats['tracks'] and 'summary' in stats['tracks']
    assert set(stats['review']) >= {'flag', 'months', 'compound', 'spy_compound'}


def test_monthly_missing_start_snapshot_stays_missing(workbench_db, monkeypatch):
    from contextlib import contextmanager
    from investment_assistant.workbench import repository, service

    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(service, 'connection', connection)
    repository.insert_snapshot(workbench_db, {
        'request_id': uuid.uuid4(), 'as_of': date(2026, 10, 31).isoformat(), 'equity': '100000',
        'note': None, 'supersedes_id': None}, 'h', recorded_at=datetime(2026, 11, 1, tzinfo=UTC))
    stats = service.generate_monthly(date(2026, 10, 1), now=datetime(2026, 11, 20, 22, 30, tzinfo=UTC))['stats']
    assert stats['return']['status'] == 'missing_snapshot'
    assert stats['tier'] is None                      # 缺快照不写 0%


def _fetch_run(**overrides):
    base = {'family': 'bars', 'job': 'workbench', 'trigger': 'scheduled', 'run_id': 'r1',
            'scope': 'family', 'ticker': None, 'session': date(2026, 10, 23), 'status': 'success',
            'target_count': 2, 'ok_count': 2, 'failures': [], 'detail': {},
            'started_at': NOW, 'finished_at': NOW + timedelta(minutes=5)}
    base.update(overrides)
    return base


def test_fetch_run_round_trip(workbench_db):
    from investment_assistant.workbench import repository
    conn = workbench_db
    repository.insert_fetch_run(conn, _fetch_run(status='failed', ok_count=0,
                                                 failures=[{'target': '*', 'reason': 'yahoo down'}]))
    latest_bars = repository.insert_fetch_run(conn, _fetch_run(run_id='r2'))
    repository.insert_fetch_run(conn, _fetch_run(family='fred', status='skipped', target_count=0, ok_count=0,
                                                 detail={'reason': 'no FRED_API_KEY'}))
    assert latest_bars['family'] == 'bars' and latest_bars['status'] == 'success'
    assert latest_bars['failures'] == [] and latest_bars['created_at'] is not None
    assert repository.latest_fetch_runs(conn)['bars']['status'] == 'success'
    runs = repository.fetch_runs(conn, 'bars')
    assert [r['status'] for r in runs] == ['success', 'failed']  # newest first
    assert list(repository.last_success_at(conn)) == ['bars']


def test_fetch_runs_table_not_required_by_workbench(workbench_db):
    from investment_assistant.workbench import repository
    assert 'data_fetch_runs' not in repository.WORKBENCH_TABLES
    workbench_db.execute('DROP TABLE data_fetch_runs')
    assert repository.missing_tables(workbench_db) == []
    assert repository.fetch_runs_ready(workbench_db) is False


# --- stage runs (v2.4 §5) ---

def _stage_run(**overrides):
    base = {'run_id': 'r1', 'job': 'workbench', 'trigger': 'scheduled',
            'session': date(2026, 10, 23), 'stage': 'collect', 'stages_version': 'stages-1',
            'status': 'success', 'reason': None, 'input_count': 2, 'output_count': 2,
            'skips': [], 'failures': [], 'degradations': [], 'outputs': {}, 'detail': {},
            'started_at': NOW, 'finished_at': NOW + timedelta(minutes=5), 'duration_ms': 300000}
    base.update(overrides)
    return base


def test_stage_run_for_plan_prefers_inserting_row(workbench_db):
    from investment_assistant.workbench import repository
    conn = workbench_db
    plan = str(uuid.uuid4())
    repository.insert_stage_run(conn, _stage_run(run_id='r1', stage='plan',
                                                 outputs={'inserted': [plan], 'existing': []}))
    repository.insert_stage_run(conn, _stage_run(run_id='r2', stage='plan',
                                                 outputs={'inserted': [], 'existing': [plan]}))
    assert repository.stage_run_for_plan(conn, plan)['run_id'] == 'r1'
    agent_plan = str(uuid.uuid4())
    repository.insert_stage_run(conn, _stage_run(run_id='r3', stage='agent',
                                                 outputs={'agent_run_ids': [7], 'plan_ids': [agent_plan]}))
    assert repository.stage_run_for_plan(conn, agent_plan)['run_id'] == 'r3'
    assert repository.stage_run_for_plan(conn, str(uuid.uuid4())) is None


def test_stage_runs_round_trip_queries(workbench_db):
    from investment_assistant.workbench import repository
    conn = workbench_db
    repository.insert_stage_run(conn, _stage_run(run_id='r1', stage='collect'))
    repository.insert_stage_run(conn, _stage_run(run_id='r1', stage='push', status='skipped',
                                                 reason='manual_run', input_count=0, output_count=0,
                                                 duration_ms=0))
    repository.insert_stage_run(conn, _stage_run(run_id='r2', stage='collect', session=date(2026, 10, 24)))
    assert [row['stage'] for row in repository.stage_runs_for_run(conn, 'r1')] == ['collect', 'push']
    assert {row['run_id'] for row in repository.stage_runs_for_session(conn, date(2026, 10, 23))} == {'r1'}
    assert repository.stage_runs_for_session(conn, date(2026, 10, 24))[0]['run_id'] == 'r2'


def test_workbench_reports_between(workbench_db):
    from investment_assistant.workbench import repository
    conn = workbench_db
    conn.execute("INSERT INTO job_reports (task, run_id, status, started_at, trigger_source) VALUES "
                 "('workbench', 'r1', 'success', %s, 'scheduled'), "
                 "('workbench', 'r2', 'success', %s, 'manual'), "
                 "('filings', 'r3', 'success', %s, 'scheduled')",
                 [NOW, NOW + timedelta(hours=2), NOW + timedelta(hours=1)])
    reports = repository.workbench_reports_between(conn, NOW, NOW + timedelta(hours=5))
    assert [(row['run_id'], row['trigger_source']) for row in reports] == [('r1', 'scheduled'), ('r2', 'manual')]
    assert reports[0]['started_at'] == NOW
    assert repository.workbench_reports_between(conn, NOW + timedelta(hours=5),
                                                NOW + timedelta(hours=10)) == []


def test_stage_runs_ready_false_without_table(workbench_db):
    from investment_assistant.workbench import repository
    assert repository.stage_runs_ready(workbench_db) is True
    workbench_db.execute('DROP TABLE stage_runs')
    assert repository.missing_tables(workbench_db) == []
    assert repository.stage_runs_ready(workbench_db) is False


# --- family targets and coverage queries (v2.3 §4/§8) ---

def _test_universe(*tickers):
    from investment_assistant.workbench.universe import Universe
    return Universe(name='nasdaq100', as_of=date(2026, 10, 1), source='hand', tickers=tickers)


def test_targets_bars_union_and_notes(workbench_db):
    from investment_assistant.workbench import families, repository
    from investment_assistant.workbench.universe import UniverseError
    conn = workbench_db
    conn.execute("INSERT INTO watchlist_items (ticker, status, tags) "
                 "VALUES ('AAA', 'active', %s), ('BBB', 'active', '{}')", [['core']])
    repository.insert_pool_tiers(conn, [
        {'ticker': 'CCC', 'tier': 'extended', 'sector_etf': None, 'source': 'screen'},
        {'ticker': 'DDD', 'tier': 'removed', 'sector_etf': None, 'source': 'screen'}],
        session=date(2026, 10, 19), now=NOW)
    _trade(conn, 'EEE', 'buy')
    _trade(conn, 'GGG', 'buy', executed_at=NOW - timedelta(days=1))   # 已清仓：不进目标
    _trade(conn, 'GGG', 'sell')
    loader = lambda: _test_universe('ZZZ', 'AAA')
    bars, notes = families.targets(conn, 'bars', universe_loader=loader)
    assert bars[:4] == ['SPY', 'SMH', 'XLK', 'QQQ']
    assert set(bars) == {'SPY', 'SMH', 'XLK', 'QQQ', 'AAA', 'BBB', 'CCC', 'EEE', 'ZZZ'}
    assert bars[4:] == sorted(bars[4:]) and notes == []
    assert 'GGG' not in bars and 'EEE' in bars   # 持仓 = 未平仓标的，已清仓的 GGG 不算
    assert families.targets(conn, 'atr', universe_loader=loader)[0] == bars
    news, news_notes = families.targets(conn, 'news', universe_loader=loader)
    assert set(news) == {'AAA', 'BBB', 'CCC', 'EEE'} and news_notes == []
    assert 'GGG' not in news
    assert families.targets(conn, 'sec_filings', universe_loader=loader)[0] == news
    assert families.targets(conn, 'fred') == (['BAMLH0A0HYM2', 'T10Y2Y'], [])
    assert families.targets(conn, 'universe') == ([], [])
    with pytest.raises(LookupError):
        families.targets(conn, 'nope')

    def broken_loader():
        raise UniverseError('cannot read universe file')
    again, notes2 = families.targets(conn, 'bars', universe_loader=broken_loader)
    assert 'ZZZ' not in again and {'AAA', 'BBB', 'CCC', 'EEE'} <= set(again)
    assert 'universe_unavailable' in notes2


def test_targets_empty_watchlist_is_a_note(workbench_db):
    from investment_assistant.workbench import families
    assert families.targets(workbench_db, 'news') == ([], ['pool_empty'])


def test_targets_invalid_pool_is_its_own_note(workbench_db):
    """非空但非法的池配置（核心超限）不再冒充 pool_empty（M08）。"""
    from investment_assistant.workbench import families
    rows = [(f'C{i:02d}', ['core']) for i in range(16)]
    workbench_db.execute('INSERT INTO watchlist_items (ticker, status, tags) VALUES '
                         + ', '.join(['(%s, \'active\', %s)'] * len(rows)),
                         [v for row in rows for v in row])
    targets, notes = families.targets(workbench_db, 'news')
    assert targets == [] and notes == ['pool_invalid']


def test_coverage_queries_take_latest_revision_and_cutoff(workbench_db):
    from decimal import Decimal
    from investment_assistant.workbench import repository
    conn = workbench_db
    cutoff = NOW + timedelta(minutes=90)
    _bar(conn, price_date=date(2026, 10, 16), close=100, fetched_at=NOW)
    _bar(conn, price_date=date(2026, 10, 19), close=104, fetched_at=NOW)
    _bar(conn, price_date=date(2026, 10, 19), close=103, revision=2, fetched_at=NOW + timedelta(hours=1))
    _bar(conn, price_date=date(2026, 10, 20), close=105, fetched_at=NOW + timedelta(hours=2))
    # 同一日期的两个修订算一根；fetched_at 晚于 cutoff 的行整行不可见；无数据的 MU 不出现在结果里
    assert repository.latest_bar_dates(conn, ['NVDA', 'MU'], cutoff_at=cutoff) == {'NVDA': date(2026, 10, 19)}
    assert repository.bar_counts(conn, ['NVDA'], end_date=date(2026, 10, 23), cutoff_at=cutoff) == {'NVDA': 2}
    assert repository.bar_counts(conn, ['NVDA'], end_date=date(2026, 10, 17), cutoff_at=cutoff) == {'NVDA': 1}

    repository.store_news(conn, [
        ({'source': 'yfinance', 'title': 'a', 'url': 'u-a', 'publisher': None,
          'published_at': datetime(2026, 10, 21, 12, tzinfo=UTC)}, ['NVDA']),
        ({'source': 'yfinance', 'title': 'b', 'url': 'u-b', 'publisher': None, 'published_at': None}, ['MU']),
    ], now=NOW)
    repository.store_news(conn, [
        ({'source': 'yfinance', 'title': 'late', 'url': 'u-c', 'publisher': None,
          'published_at': datetime(2026, 10, 22, 12, tzinfo=UTC)}, ['MU']),
    ], now=NOW + timedelta(hours=2))
    # 无 published_at 用 fetched_at；晚于 cutoff 抓到的行不算
    assert repository.latest_news_at(conn, ['NVDA', 'MU'], cutoff_at=cutoff) == {
        'NVDA': datetime(2026, 10, 21, 12, tzinfo=UTC), 'MU': NOW}

    conn.execute(
        'INSERT INTO filings (ticker, form, filing_date, accession_number, primary_document, file_path, created_at) '
        "VALUES ('F1', '10-Q', %s, 'acc-1', 'd.htm', '/x', %s), ('F3', '8-K', %s, 'acc-2', 'd.htm', '/x', %s)",
        [date(2026, 10, 10), NOW, date(2026, 10, 5), NOW])
    repository.store_news(conn, [
        ({'source': 'sec_8k', 'title': 'F2 8-K', 'url': 'u-f2', 'publisher': 'SEC EDGAR',
          'published_at': datetime(2026, 10, 15, 14, tzinfo=UTC)}, ['F2']),
        ({'source': 'sec_8k', 'title': 'F3 8-K', 'url': 'u-f3', 'publisher': 'SEC EDGAR',
          'published_at': datetime(2026, 10, 15, 14, tzinfo=UTC)}, ['F3']),
    ], now=NOW)
    # 只有 sec_8k 时也有值；两路都有时取较大者
    assert repository.latest_filing_dates(conn, ['F1', 'F2', 'F3'], cutoff_at=cutoff) == {
        'F1': date(2026, 10, 10), 'F2': date(2026, 10, 15), 'F3': date(2026, 10, 15)}
    # 无 accepted_at（published_at 为空）的 sec_8k 不提供申报日依据：宁可漏计不可
    # 用 fetched_at 冒充（I14）。
    repository.store_news(conn, [
        ({'source': 'sec_8k', 'title': 'F4 8-K undated', 'url': 'u-f4', 'publisher': 'SEC EDGAR',
          'published_at': None}, ['F4']),
    ], now=NOW)                                                 # 采集时间在窗口内，也不当申报日用
    assert repository.latest_filing_dates(conn, ['F4'], cutoff_at=cutoff) == {}

    repository.store_macro(conn, 'BAMLH0A0HYM2', [{'obs_date': date(2026, 10, 20), 'value': Decimal('3.1')}],
                           now=NOW)
    repository.store_macro(conn, 'BAMLH0A0HYM2', [{'obs_date': date(2026, 10, 21), 'value': Decimal('3.2')}],
                           now=NOW + timedelta(hours=2))
    repository.store_macro(conn, 'T10Y2Y', [{'obs_date': date(2026, 10, 21), 'value': Decimal('0.1')}], now=NOW)
    assert repository.latest_macro_dates(conn, ['BAMLH0A0HYM2', 'T10Y2Y'], cutoff_at=cutoff) == {
        'BAMLH0A0HYM2': date(2026, 10, 20), 'T10Y2Y': date(2026, 10, 21)}


# --- plan timeline: versions, reviews, user inputs (v2.5 §6) ---

def _tl_version(conn, version_no=0, supersedes=None, *, ticker='NVDA', day=date(2026, 10, 21), pool='main'):
    """One agent-2 version written the way the service will: plan row, run row, version row."""
    from investment_assistant.workbench import repository
    plan = repository.insert_plan(conn, _plan_row(ticker=ticker, signal_date=day, kind='agent',
                                                  plan_version='agent-2'))
    run_id = uuid.uuid4()
    cutoff = NOW + timedelta(days=version_no)
    repository.insert_agent_run(conn, {
        'id': run_id, 'ticker': ticker, 'signal_date': day, 'pool': pool, 'attempt': 1,
        'mode': 'propose' if version_no == 0 else 'revise', 'agent_version': 'agent-2', 'provider': 'chatgpt',
        'model': 'gpt-test', 'cutoff_at': cutoff, 'status': 'ok', 'reason': None, 'output': {'action': 'buy'},
        'counter': None, 'plan_id': plan['id'], 'input_tokens': 0, 'output_tokens': 0,
        'started_at': cutoff, 'finished_at': cutoff}, [])
    return repository.insert_plan_version(conn, {
        'plan_id': plan['id'], 'ticker': ticker, 'signal_date': day, 'version_no': version_no,
        'supersedes_plan_id': supersedes, 'origin': 'signal' if version_no == 0 else 'review',
        'agent_run_id': run_id, 'cutoff_at': cutoff, 'as_of_session': day + timedelta(days=version_no),
        'diff': None if version_no == 0 else [{'field': 'stop', 'before': '96', 'after': '97'}],
        'schema_version': 'agent-2'})


def test_chain_head_is_highest_version(workbench_db):
    from investment_assistant.workbench import repository
    conn = workbench_db
    day = date(2026, 10, 21)
    assert repository.timeline_ready(conn) is True
    assert repository.chain_head(conn, 'NVDA', day) is None
    v0 = _tl_version(conn)
    assert v0['version_no'] == 0 and v0['diff'] is None and v0['created_at'] is not None
    v1 = _tl_version(conn, 1, v0['plan_id'])
    v2 = _tl_version(conn, 2, v1['plan_id'])
    assert v2['diff'] == [{'field': 'stop', 'before': '96', 'after': '97'}]
    _tl_version(conn, ticker='AMD', pool='extended')                       # another chain

    head = repository.chain_head(conn, 'nvda', day)
    assert head['plan']['id'] == v2['plan_id'] and head['plan']['plan_version'] == 'agent-2'
    assert head['version']['version_no'] == 2 and head['version']['supersedes_plan_id'] == v1['plan_id']
    assert [v['version_no'] for v in repository.chain_versions(conn, 'NVDA', day)] == [0, 1, 2]
    assert repository.version_by_plan(conn, v1['plan_id'])['version_no'] == 1
    assert repository.version_by_plan(conn, uuid.uuid4()) is None
    chains = repository.all_chains(conn)
    assert [(c['ticker'], c['signal_date'], c['plan_id'], c['version_no'], c['pool']) for c in chains] == [
        ('AMD', day, repository.chain_head(conn, 'AMD', day)['plan']['id'], 0, 'extended'),
        ('NVDA', day, v2['plan_id'], 2, 'main')]


def test_plan_review_queries(workbench_db):
    from investment_assistant.workbench import repository
    conn = workbench_db
    day, s1, s2 = date(2026, 10, 21), date(2026, 10, 23), date(2026, 10, 26)
    v0 = _tl_version(conn)

    def review(**overrides):
        row = {'ticker': 'NVDA', 'signal_date': day, 'head_plan_id': v0['plan_id'], 'session': s1,
               'trigger': 'batch', 'run_id': 'workbench-1', 'mode': 'entry', 'timeline_version': 'timeline-1',
               'outcome': 'maintain', 'decided_by': 'host', 'reason': 'no signal', 'attempt': 1,
               'cutoff_at': NOW}
        row.update(overrides)
        return repository.insert_plan_review(conn, row)

    skipped = review(outcome='skipped', reason='review_in_progress')
    assert skipped['signals'] == [] and skipped['user_input_ids'] == [] and skipped['detail'] == {}
    kept = review(attempt=2, signals=[{'kind': 'new_8k', 'fired': False}], user_input_ids=[3],
                  detail={'via': 'host'}, input_tokens=5, output_tokens=7)
    assert kept['signals'] == [{'kind': 'new_8k', 'fired': False}] and kept['user_input_ids'] == [3]
    recheck = review(trigger='recheck', run_id='recheck-1', outcome='skipped', reason='agent_unavailable')
    later = review(session=s2, outcome='error', reason='timeout')

    assert [r['id'] for r in repository.chain_reviews(conn, 'NVDA', day)] == [
        skipped['id'], kept['id'], recheck['id'], later['id']]
    assert [r['id'] for r in repository.reviews_on(conn, s1)] == [skipped['id'], kept['id'], recheck['id']]
    assert [r['id'] for r in repository.batch_attempts(conn, 'NVDA', day, s1)] == [skipped['id'], kept['id']]
    assert repository.batch_attempts(conn, 'NVDA', day, date(2026, 10, 27)) == []
    # skipped / error rows are not effective reviews
    assert repository.last_effective_review(conn, v0['plan_id'])['id'] == kept['id']
    assert repository.last_effective_review(conn, uuid.uuid4()) is None


def test_user_inputs_superseded_by(workbench_db):
    from investment_assistant.workbench import repository
    conn = workbench_db
    day = date(2026, 10, 21)

    def add(**overrides):
        row = {'request_id': uuid.uuid4(), 'request_hash': 'h', 'ticker': 'NVDA', 'signal_date': None,
               'kind': 'research', 'body': 'margin outlook', 'source_url': None, 'supersedes_id': None}
        row.update(overrides)
        return repository.insert_user_input(conn, row)

    ticker_level = add(source_url='https://example.com/a')
    chain = add(signal_date=day, kind='judgment', body='wait for the pullback')
    add(signal_date=date(2026, 10, 14), body='older chain')                    # another chain: excluded
    add(ticker='AMD', body='other ticker')                                      # another ticker: excluded
    correction = add(signal_date=day, kind='judgment', body='pullback done', supersedes_id=chain['id'])
    retract = add(kind='retract', body='withdrawn', supersedes_id=ticker_level['id'])
    assert repository.user_input_by_request(conn, chain['request_id'])['id'] == chain['id']
    assert repository.user_input_by_request(conn, uuid.uuid4()) is None

    rows = repository.user_inputs_for(conn, 'nvda', day)
    assert [(r['id'], r['superseded_by']) for r in rows] == [
        (ticker_level['id'], retract['id']), (chain['id'], correction['id']),
        (correction['id'], None), (retract['id'], None)]
    assert rows[0]['source_url'] == 'https://example.com/a' and rows[0]['created_at'] is not None
    assert [(r['id'], r['superseded_by']) for r in repository.user_inputs_for(conn, 'NVDA', None)] == [
        (ticker_level['id'], retract['id']), (retract['id'], None)]


def test_insert_decision_writes_agent_plan_columns(workbench_db):
    from investment_assistant.workbench import repository
    conn = workbench_db
    head = _tl_version(conn)
    rec = {'request_id': uuid.uuid4(), 'ticker': 'NVDA', 'signal_date': date(2026, 10, 21), 'candidate_ids': [],
           'brief_id': None, 'proposed_action': 'buy', 'final_action': 'buy', 'decision': 'accept',
           'reason': 'trend intact', 'supersedes_id': None, 'plan_id': head['plan_id']}
    plain = repository._insert_decision_for_tests(conn, rec, 'h1', decided_at=NOW)        # old callers: no new keys
    assert 'agent_plan_id' not in plain
    stored = conn.execute('SELECT agent_plan_id, review_view FROM signal_decisions WHERE id = %s',
                          [plain['id']]).fetchone()
    assert stored == (None, None)
    view = {'layout': 'agent_first', 'baseline_expanded': False, 'agent_version_no': 0}
    record = repository._insert_decision_for_tests(conn, {**rec, 'request_id': uuid.uuid4(), 'supersedes_id': plain['id'],
                                               'agent_plan_id': head['plan_id'], 'review_view': view},
                                        'h2', decided_at=NOW + timedelta(minutes=1))
    assert record['agent_plan_id'] == head['plan_id'] and record['review_view'] == view


def test_try_review_lock_is_exclusive(workbench_db):
    import os
    import psycopg
    from investment_assistant.workbench import repository
    day = date(2026, 10, 21)
    assert repository.try_review_lock(workbench_db, 'NVDA', day) is True
    assert repository.try_review_lock(workbench_db, 'NVDA', day) is True      # re-entrant in one session
    with psycopg.connect(os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']) as other:
        assert repository.try_review_lock(other, 'NVDA', day) is False
        assert repository.try_review_lock(other, 'NVDA', date(2026, 10, 22)) is True   # another chain
        other.rollback()
        workbench_db.rollback()                                                # transaction end releases it
        assert repository.try_review_lock(other, 'NVDA', day) is True
        other.rollback()
