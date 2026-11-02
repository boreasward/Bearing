"""Offline tests for collect.py's tally and fetch-run writing (v2.3 Task 5, design §6.2)."""
import investment_assistant.data.repo as data_repo
import contextlib
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from investment_assistant.core import tx as core_tx

from investment_assistant.data import fetch_policy as fp
import investment_assistant.data.collect as collect
import investment_assistant.core.tx as service

SESSION = date(2026, 10, 22)
NOW = datetime(2026, 10, 22, 22, 30, tzinfo=UTC)


def _row(tally, **overrides):
    kwargs = dict(trigger='scheduled', run_id=None, session=SESSION, started_at=NOW, finished_at=NOW)
    kwargs.update(overrides)
    return tally.row(**kwargs)


def test_tally_last_outcome_wins_and_row_shape():
    tally = collect.Tally('bars', 'workbench', ('A', 'B', 'C'))
    tally.fail('B', 'x')
    tally.ok('B')
    tally.fail('C', 'y')
    tally.ok('A')
    row = _row(tally)
    assert row['status'] == 'partial'
    assert row['target_count'] == 3 and row['ok_count'] == 2
    assert row['failures'] == [{'target': 'C', 'reason': 'y'}]
    assert row['family'] == 'bars' and row['job'] == 'workbench'
    assert row['scope'] == 'family' and row['ticker'] is None
    assert 'failed_targets' not in row['detail']


def test_tally_skip():
    tally = collect.Tally('fred', 'workbench')
    tally.skip('FRED_API_KEY 未配置')
    row = _row(tally)
    assert row['status'] == 'skipped'
    assert row['detail']['reason'] == 'FRED_API_KEY 未配置'


def test_tally_wildcard_is_not_a_target():
    """'*' is the whole-call failure sentinel: the denominator stays the real
    target set frozen beforehand, no N+1th target appears (I04)."""
    tally = collect.Tally('sec_filings', 'workbench', ('MU', 'NVDA'))
    tally.ok('MU')
    tally.fail('*', 'edgar down')
    row = _row(tally)
    assert row['status'] == 'partial'                       # NVDA never completed collection
    assert row['target_count'] == 2 and row['ok_count'] == 1
    assert row['failures'] == [{'target': '*', 'reason': 'edgar down'}]
    assert row['detail']['failed_targets'] == ['MU', 'NVDA']   # I05: the call's own scope


def test_tally_whole_call_failure_all_targets_failed():
    tally = collect.Tally('sec_filings', 'workbench', ('MU', 'NVDA'))
    tally.fail('*', 'edgar down')
    row = _row(tally)
    assert row['status'] == 'failed'
    assert row['target_count'] == 2 and row['ok_count'] == 0
    assert row['failures'] == [{'target': '*', 'reason': 'edgar down'}]


def test_tally_whole_call_failure_without_targets_is_failed_not_success():
    """Empty real targets plus a call failure must not be a 0==0 success (I04)."""
    tally = collect.Tally('sec_filings', 'workbench')
    tally.fail('*', 'benchmark missing')
    row = _row(tally)
    assert row['status'] == 'failed'
    assert row['target_count'] == 0 and row['ok_count'] == 0
    assert row['failures'] == [{'target': '*', 'reason': 'benchmark missing'}]


def test_write_runs_never_raises(monkeypatch):
    def connection():
        raise RuntimeError('boom')

    monkeypatch.setattr(core_tx, 'connection', connection)
    out = collect.write_runs([_row(collect.Tally('bars', 'workbench', ('A',)))])
    assert out == {'written': 0, 'error': 'boom'}


def test_wall_clock_is_real_utc_now():
    from datetime import UTC

    before = datetime.now(UTC)
    got = collect.wall_clock()
    after = datetime.now(UTC)
    assert before <= got <= after


# ---------------------------------------------------------------- v2.8: Tally.record and detail.targets (fetch-1 §6.4)
def _attempts(*elapsed, kind='timeout', last_ok=False):
    items = [{'n': i + 1, 'kind': kind, 'status': None, 'elapsed_ms': ms} for i, ms in enumerate(elapsed)]
    if last_ok:
        items[-1].update(kind=None, status=200)
    return items


def test_tally_record_targets_shape():
    """The two samples of spec §6.4, field by field, sorted by target."""
    tally = collect.Tally('bars', 'daily', ['NVDA', 'AAPL'])
    failure = fp.FetchError('timeout after 30s (3 attempts)', kind='timeout', status=None,
                            attempts=_attempts(30000, 30206, 40206), target='NVDA')
    tally.record('NVDA', failure)
    tally.record('AAPL', fp.Fetched(None, _attempts(812, last_ok=True), 812), rows=2, latest=date(2026, 10, 29))
    row = _row(tally, trigger='scheduled', run_id='r1')
    assert row['detail']['policy'] == 'fetch-1'
    assert row['detail']['targets'] == [
        {'target': 'AAPL', 'ok': True, 'attempts': 1, 'error_kind': None, 'http_status': 200,
         'reason': None, 'rows': 2, 'latest': '2026-10-29', 'elapsed_ms': 812,
         'source_used': None, 'fallback': {'tried': False, 'reason': None}},
        {'target': 'NVDA', 'ok': False, 'attempts': 3, 'error_kind': 'timeout', 'http_status': None,
         'reason': 'timeout after 30s (3 attempts)', 'rows': None, 'latest': None, 'elapsed_ms': 100412,
         'source_used': None, 'fallback': {'tried': False, 'reason': None}}]
    assert row['status'] == 'partial' and row['target_count'] == 2 and row['ok_count'] == 1
    assert row['failures'] == [{'target': 'NVDA', 'reason': 'timeout after 30s (3 attempts)'}]
    assert 'targets_truncated' not in row['detail']
    import json
    json.dumps(row['detail'])                                    # plain JSON values only


def test_tally_record_last_outcome_wins_and_sanitises(monkeypatch):
    monkeypatch.setenv('FRED_API_KEY', 'SECRET-KEY-VALUE')
    tally = collect.Tally('fred', 'daily')
    tally.record('T10Y2Y', fp.FetchError('boom SECRET-KEY-VALUE ' + 'x' * 400, kind='other'))
    tally.record('T10Y2Y', fp.Fetched(None, _attempts(5, last_ok=True), 5), rows=1)
    tally.record('BAML', ValueError('plain failure'))             # a bare exception: classified, no attempt data
    row = _row(tally)
    by_target = {t['target']: t for t in row['detail']['targets']}
    assert by_target['T10Y2Y']['ok'] is True and by_target['BAML']['error_kind'] == 'other'
    assert by_target['BAML']['attempts'] is None and by_target['BAML']['reason'] == 'plain failure'
    tally.record('T10Y2Y', fp.FetchError('boom SECRET-KEY-VALUE ' + 'x' * 400, kind='other'))
    reason = _row(tally)['failures'][-1]['reason']
    assert 'SECRET-KEY-VALUE' not in reason and len(reason) <= 300


def test_tally_targets_truncated_at_600():
    tally = collect.Tally('bars', 'daily')
    for i in range(650):
        tally.record(f'T{i:04d}', fp.Fetched(None, _attempts(1, last_ok=True), 1))
    row = _row(tally)
    assert len(row['detail']['targets']) == 600 == collect.TARGETS_MAX
    assert row['detail']['targets_truncated'] == 50
    assert row['detail']['targets'][0]['target'] == 'T0000' and row['detail']['targets'][-1]['target'] == 'T0599'
    assert row['target_count'] == 650 and row['ok_count'] == 650          # the status still counts every target


def test_tally_source_counts_use_final_successes_before_target_clipping(monkeypatch):
    monkeypatch.setenv('FRED_API_KEY', 'SECRET-KEY-VALUE')
    tally = collect.Tally('bars', 'daily')
    for i in range(collect.TARGETS_MAX):
        tally.record(f'A{i:04d}', _ok(), source_used='yfinance')
    tally.record('Z-backup', _ok(), source_used='stooq',
                 fallback={'tried': True, 'reason': 'timeout SECRET-KEY-VALUE'})
    tally.record('Z-retried', _ok(), source_used='stooq')
    tally.record('Z-retried', _ok(), source_used='yfinance')
    tally.record('Z-failed', _ok(), source_used='stooq')
    tally.record('Z-failed', _bad(), source_used='stooq', fallback={'tried': True, 'reason': 'failed'})
    row = _row(tally)
    assert row['target_count'] == collect.TARGETS_MAX + 3
    assert row['ok_count'] == collect.TARGETS_MAX + 2 and row['status'] == 'partial'
    assert len(row['detail']['targets']) == collect.TARGETS_MAX
    assert row['detail']['targets_truncated'] == 3
    assert row['detail']['source_counts'] == {'yfinance': collect.TARGETS_MAX + 1, 'stooq': 1}
    assert row['detail']['source_counts_complete'] is True
    # Reading freezes fresh counts; replaying row() does not increment successful retries.
    assert _row(tally)['detail']['source_counts'] == row['detail']['source_counts']
    assert 'SECRET-KEY-VALUE' not in tally._records['Z-backup']['fallback']['reason']
    assert tally._records['Z-backup']['fallback']['tried'] is True


@pytest.mark.parametrize('source', [None, '', '   '])
def test_tally_missing_success_source_makes_accounting_incomplete(source):
    tally = collect.Tally('bars', 'daily')
    tally.record('known', _ok(), source_used='stooq')
    tally.record('unknown', _ok(), source_used=source)
    assert _row(tally)['detail']['source_counts'] == {'stooq': 1}
    assert _row(tally)['detail']['source_counts_complete'] is False
    tally.record('unknown', _bad())
    assert _row(tally)['detail']['source_counts_complete'] is True


def test_tally_source_counts_empty_when_no_successful_targets():
    tally = collect.Tally('bars', 'daily', ['A'])
    assert _row(tally)['detail']['source_counts'] == {}
    assert _row(tally)['detail']['source_counts_complete'] is True
    tally.record('*', _bad(), source_used='stooq')
    tally.record('A', _bad(), source_used='stooq', fallback={'tried': True, 'reason': 'failed'})
    row = _row(tally)
    assert row['status'] == 'failed' and row['ok_count'] == 0
    assert row['detail']['source_counts'] == {} and row['detail']['source_counts_complete'] is True


def test_tally_legacy_ok_fail_delegate_to_record():
    tally = collect.Tally('bars', 'workbench', ('A', 'B'))
    tally.ok('A')
    tally.fail('B', 'provider down')
    row = _row(tally)
    assert row['detail']['targets'] == [
        {'target': 'A', 'ok': True, 'attempts': None, 'error_kind': None, 'http_status': None, 'reason': None,
         'rows': None, 'latest': None, 'elapsed_ms': None,
         'source_used': None, 'fallback': {'tried': False, 'reason': None}},
        {'target': 'B', 'ok': False, 'attempts': None, 'error_kind': 'other', 'http_status': None,
         'reason': 'provider down', 'rows': None, 'latest': None, 'elapsed_ms': None,
         'source_used': None, 'fallback': {'tried': False, 'reason': None}}]
    assert row['detail']['source_counts'] == {} and row['detail']['source_counts_complete'] is False
    assert row['failures'] == [{'target': 'B', 'reason': 'provider down'}]
    # a FetchError handed to the old fail() keeps its category and attempts
    tally.fail('B', fp.FetchError('x', kind='http_5xx', status=503, attempts=_attempts(3, 4)))
    item = _row(tally)['detail']['targets'][1]
    assert (item['error_kind'], item['http_status'], item['attempts'], item['elapsed_ms']) == ('http_5xx', 503, 2, 7)


def test_tally_without_records_adds_no_policy_note():
    row = _row(collect.Tally('universe', 'daily', ('nasdaq100',)))
    assert 'targets' not in row['detail'] and 'policy' not in row['detail']


def _ok(n=1):
    return fp.Fetched(None, _attempts(1, last_ok=True), 1)


def _bad(kind='timeout'):
    return fp.FetchError('failed', kind=kind, attempts=_attempts(1))


def _build(kind, legacy=False):
    tally = collect.Tally('bars', 'daily', ['A', 'B'])
    if kind == 'skipped':
        tally.skip('FRED_API_KEY 未配置')
    elif kind == 'success':
        for t in 'AB':
            tally.ok(t) if legacy else tally.record(t, _ok())
    elif kind == 'partial':
        tally.ok('A') if legacy else tally.record('A', _ok())
        tally.fail('B', 'x') if legacy else tally.record('B', _bad())
    elif kind == 'failed':
        for t in 'AB':
            tally.fail(t, 'x') if legacy else tally.record(t, _bad())
    elif kind == 'call_failed':
        tally.fail('*', 'edgar down') if legacy else tally.record('*', _bad())
    return _row(tally)['status']


@pytest.mark.parametrize('kind,expected', [('success', 'success'), ('partial', 'partial'), ('failed', 'failed'),
                                           ('skipped', 'skipped'), ('call_failed', 'failed')])
def test_family_status_unchanged(kind, expected):
    assert _build(kind) == _build(kind, legacy=True) == expected


@pytest.fixture
def stored(monkeypatch):
    """No database: `_tx` yields a placeholder and the store functions report what they were handed."""
    @contextlib.contextmanager
    def tx():
        yield object()

    monkeypatch.setattr(core_tx, '_tx', tx)
    monkeypatch.setattr(data_repo, 'store_bars', lambda conn, t, rows, *, now: {'inserted': len(rows) - 1,
                                                                                   'unchanged': 1})
    monkeypatch.setattr(data_repo, 'store_news', lambda conn, entries, *, now: {'inserted': len(entries),
                                                                                   'existing': 0})
    monkeypatch.setattr(data_repo, 'store_macro', lambda conn, s, rows, *, now: {'inserted': 3, 'unchanged': 0})
    monkeypatch.setattr(data_repo, 'fill_filing_acceptance', lambda conn, metas: len(metas))


def _targets(tally):
    return {t['target']: t for t in _row(tally)['detail']['targets']}


def test_rows_and_latest_per_family(stored):
    days = pd.DatetimeIndex(['2026-10-26', '2026-10-27', '2026-10-28'])
    frame = pd.DataFrame({'Open': 10.0, 'High': 11.0, 'Low': 9.0, 'Close': 10.5, 'Adj Close': 10.5,
                          'Volume': 1000, 'Stock Splits': 0.0}, index=days)
    published = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)
    raw = [{'title': 'a', 'link': 'https://x/1', 'relatedTickers': ['NVDA'],
            'providerPublishTime': int(published.timestamp())},
           {'title': 'b', 'link': 'https://x/2', 'relatedTickers': ['NVDA'],
            'providerPublishTime': int(published.timestamp()) - 7200}]
    source = SimpleNamespace(discover=lambda tickers, start, end: [
        {'ticker': tickers[0], 'form': '8-K', 'filing_date': '2026-10-27', 'accession_number': 'a', 'items': None,
         'edgar_url': 'https://www.sec.gov/a', 'accepted_at': None},
        {'ticker': tickers[0], 'form': '8-K', 'filing_date': '2026-10-25', 'accession_number': 'b', 'items': None,
         'edgar_url': 'https://www.sec.gov/b', 'accepted_at': None}])
    deps = SimpleNamespace(
        bar_fetcher=lambda *a: frame, news_fetcher=lambda t: raw, filing_source=source,
        macro_fetcher=lambda url, *, params: ({'observations': [{'date': '2026-10-26', 'value': '1'},
                                                                 {'date': '2026-10-27', 'value': '2'}]}, {'ok': True}))
    bars, news, sec, fred = (collect.Tally(f, 'daily') for f in ('bars', 'news', 'sec_filings', 'fred'))
    collect.bars_one('NVDA', session=SESSION, now=NOW, deps=deps, lookback_days=10, tally=bars)
    collect.news_one('NVDA', now=NOW, deps=deps, tally=news)
    collect.sec_listing(['NVDA'], session=SESSION, now=NOW, deps=deps, tally=sec)
    collect.fred_series('T10Y2Y', session=SESSION, now=NOW, deps=deps, api_key='K', tally=fred)
    assert (_targets(bars)['NVDA']['rows'], _targets(bars)['NVDA']['latest']) == (2, '2026-10-28')   # newly written
    assert (_targets(news)['NVDA']['rows'], _targets(news)['NVDA']['latest']) == (2, published.isoformat())
    assert (_targets(sec)['NVDA']['rows'], _targets(sec)['NVDA']['latest']) == (2, '2026-10-27')     # filings found
    assert (_targets(fred)['T10Y2Y']['rows'], _targets(fred)['T10Y2Y']['latest']) == (3, '2026-10-27')
    for tally in (bars, news, sec, fred):
        item = next(iter(_targets(tally).values()))
        assert item['ok'] and item['attempts'] == 1 and item['http_status'] == 200 and item['elapsed_ms'] >= 0


def test_failures_after_a_good_fetch_keep_the_attempts(stored, monkeypatch):
    def broken_store(conn, t, rows, *, now):
        raise RuntimeError('disk full')

    monkeypatch.setattr(data_repo, 'store_bars', broken_store)
    frame = pd.DataFrame({'Open': 10.0, 'High': 11.0, 'Low': 9.0, 'Close': 10.5, 'Adj Close': 10.5,
                          'Volume': 1000, 'Stock Splits': 0.0}, index=pd.DatetimeIndex(['2026-10-28']))
    tally = collect.Tally('bars', 'daily', ['NVDA'])
    with pytest.raises(RuntimeError, match='disk full'):
        collect.bars_one('NVDA', session=SESSION, now=NOW, deps=SimpleNamespace(bar_fetcher=lambda *a: frame),
                         lookback_days=10, tally=tally)
    item = _targets(tally)['NVDA']
    assert item['ok'] is False and item['reason'] == 'disk full' and item['attempts'] == 1
