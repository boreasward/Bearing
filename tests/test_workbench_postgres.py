"""Real constraint behavior for the US workbench schema; disposable schema only."""
import investment_assistant.data.repo as data_repo
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


def test_macro_value_required(workbench_db):
    import psycopg
    with pytest.raises(psycopg.errors.NotNullViolation):
        workbench_db.execute("INSERT INTO macro_observations (series_id, obs_date, value, fetched_at) "
                             "VALUES ('BAMLH0A0HYM2', '2026-10-21', NULL, %s)", [NOW])


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


# --- stage runs (v2.4 §5) ---


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
