"""Every external call site of spec §6.2 runs under fetch-1 (offline: fake servers on 127.0.0.1, injected fetchers).

Sleeps are injected or recorded, so retries cost nothing; `attempts` and `FetchError.kind` are asserted per row."""
import investment_assistant.data.repo as data_repo
import contextlib
import json
import time
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from investment_assistant.core import tx as core_tx
import requests

from investment_assistant.data import fetch_policy as fp
from investment_assistant.data import http
from investment_assistant.data.sources import edgar as monitor_source
from investment_assistant.data.adapters import sec_text as sec_downloader
from investment_assistant.data.sources.edgar import EdgarSource
from investment_assistant.notify.discord import DiscordChannel, DiscordClient
from investment_assistant.notify.discord_bot import DeliveryError, DiscordBotClient
import investment_assistant.data.collect as collect
import investment_assistant.core.tx as service
from investment_assistant.data.adapters import bars, earnings, fred as macro, news
from _adapter_calls import adapter_run, item
from test_fetch_policy import FAST, FAST_POST, FakeServer, closed_port, reply
from investment_assistant.workflow.nodes.collect import _Family, sec_listing_summary

SESSION = date(2026, 10, 28)
NOW = datetime(2026, 10, 28, 22, 30, tzinfo=UTC)


@pytest.fixture
def server():
    srv = FakeServer()
    yield srv
    srv.close()


@pytest.fixture
def sleeps(monkeypatch):
    """collect / earnings wait through these hooks; the list shows the backoff the policy asked for."""
    recorded = []
    monkeypatch.setattr(collect, 'FETCH_SLEEP', recorded.append)
    monkeypatch.setattr(earnings, 'FETCH_SLEEP', recorded.append)
    return recorded


@pytest.fixture
def fake_db(monkeypatch):
    """No database: `_tx` yields a placeholder, the store functions record what they were given."""
    stored = SimpleNamespace(bars=[], news=[], macro=[], acceptance=[])

    @contextlib.contextmanager
    def tx():
        yield object()

    monkeypatch.setattr(core_tx, '_tx', tx)
    monkeypatch.setattr(data_repo, 'store_bars', lambda conn, ticker, rows, *, now, source='yfinance': (
        stored.bars.append((ticker, rows)) or {'inserted': len(rows), 'unchanged': 0}))
    monkeypatch.setattr(data_repo, 'store_news', lambda conn, entries, *, now: (
        stored.news.append(entries) or {'inserted': len(entries), 'existing': 0}))
    monkeypatch.setattr(data_repo, 'store_macro', lambda conn, series_id, rows, *, now: (
        stored.macro.append((series_id, rows)) or {'inserted': len(rows), 'unchanged': 0}))
    monkeypatch.setattr(data_repo, 'fill_filing_acceptance', lambda conn, metas: (
        stored.acceptance.append(metas) or len(metas)))
    return stored


def frame(days=('2026-10-27', '2026-10-28')):
    index = pd.DatetimeIndex(days)
    return pd.DataFrame({'Open': 10.0, 'High': 11.0, 'Low': 9.0, 'Close': 10.5, 'Adj Close': 10.5,
                         'Volume': 1000, 'Stock Splits': 0.0}, index=index)


def deps(**kwargs):
    base = dict(bar_fetcher=None, news_fetcher=None, filing_source=None, earnings_fetcher=None, macro_fetcher=None)
    return SimpleNamespace(**{**base, **kwargs})


# ---------------------------------------------------------------- row 1: bars

def test_row1_bars_timeout_is_retried_twice(sleeps):
    calls = []

    def fetcher(ticker, start, end):
        calls.append(ticker)
        raise requests.ReadTimeout('slow')

    booked = item(adapter_run('bars', ['NVDA'], session=SESSION, now=NOW, bar_fetcher=fetcher), 'NVDA')
    assert booked['ok'] is False and booked['error_kind'] == 'timeout' and booked['attempts'] == 3
    assert calls == ['NVDA'] * 3 and sleeps == [2, 8]


def test_row1_empty_frame_is_not_retried(sleeps):
    booked = item(adapter_run('bars', ['NVDA'], session=SESSION, now=NOW, bar_fetcher=lambda *a: pd.DataFrame()),
                  'NVDA')
    assert booked['error_kind'] == 'empty' and booked['attempts'] == 1 and sleeps == []
    assert 'no bars returned for NVDA' in booked['reason']


def test_row1_success_is_one_attempt_and_return_shape_is_unchanged(sleeps, fake_db):
    run = adapter_run('bars', ['NVDA'], session=SESSION, now=NOW, bar_fetcher=lambda *a: frame())
    source, rows, result = run.stored['NVDA']
    assert source == 'yfinance' and result['inserted'] == 2 and result['existing'] == 0
    assert list(rows.facts) == ['skipped'] and rows.facts['skipped'] == []
    booked = item(run, 'NVDA')
    assert booked['ok'] and booked['attempts'] == 1 and booked['rows'] == 2
    assert sleeps == [] and fake_db.bars[0][0] == 'NVDA'


class _FakeYf:
    """Records how the default yfinance fetchers are wired (session, timeout, thread backstop)."""

    def __init__(self, monkeypatch):
        import yfinance
        self.sessions, self.timeouts, self.deadlines = [], [], []
        owner = self

        class Ticker:
            def __init__(self, symbol, session=None):
                owner.sessions.append(session)
                self.calendar = {'Earnings Date': [date(2026, 11, 20)]}

            def history(self, **kwargs):
                owner.timeouts.append(kwargs.get('timeout'))
                return frame()

        class Search:
            def __init__(self, query, *, session=None, timeout=None, **kwargs):
                owner.sessions.append(session)
                owner.timeouts.append(timeout)
                self.news = [{'title': 't', 'link': 'https://x/1', 'relatedTickers': [query]}]

        monkeypatch.setattr(yfinance, 'Ticker', Ticker)
        monkeypatch.setattr(yfinance, 'Search', Search)
        real = fp.hard_deadline
        monkeypatch.setattr(fp, 'hard_deadline', lambda fn, *, seconds: (
            owner.deadlines.append(seconds) or real(fn, seconds=seconds)))


def _is_curl_session(session):
    from curl_cffi import requests as cffi_requests
    return isinstance(session, cffi_requests.Session)


def test_rows1_to_3_yfinance_fetchers_use_own_session_and_35s_deadline(monkeypatch):
    fake = _FakeYf(monkeypatch)
    assert not bars._yfinance_fetcher('NVDA', date(2026, 10, 1), date(2026, 10, 29)).empty
    assert news._yfinance_fetcher('NVDA')[0]['title'] == 't'
    assert earnings._yfinance_fetcher('NVDA') == {'Earnings Date': [date(2026, 11, 20)]}
    assert len(fake.sessions) == 3 and all(_is_curl_session(s) for s in fake.sessions)
    # one process-wide session, so yfinance's cookie / crumb stay with it (a new session per call orphans them)
    assert all(s is fp.shared_yf_session() for s in fake.sessions)
    assert fake.timeouts == [30, 30] and fake.deadlines == [35, 35, 35]
    bars._yfinance_fetcher('MU', date(2026, 10, 1), date(2026, 10, 29))
    assert fake.sessions[-1] is fake.sessions[0]


# ---------------------------------------------------------------- row 2: news

def test_row2_news_5xx_is_retried(sleeps):
    def fetcher(ticker):
        raise fp.http_error(503, 'Yahoo 503')

    booked = item(adapter_run('news', ['NVDA'], session=SESSION, now=NOW, news_fetcher=fetcher), 'NVDA')
    assert booked['error_kind'] == 'http_5xx' and booked['attempts'] == 3 and sleeps == [2, 8]


def test_row2_news_success_shape(sleeps, fake_db):
    raw = [{'title': 'a', 'link': 'https://x/1', 'relatedTickers': ['NVDA'],
            'providerPublishTime': 1792000000}]
    run = adapter_run('news', ['NVDA'], session=SESSION, now=NOW, news_fetcher=lambda t: raw)
    _, rows, result = run.stored['NVDA']
    assert (result['inserted'], result['existing']) == (1, 0)
    assert rows.facts == {'fetched': 1, 'skipped': 0}


# ---------------------------------------------------------------- row 3: earnings

def test_row3_earnings_connection_error_is_retried(sleeps):
    def fetcher(ticker):
        raise ConnectionError('reset')

    with pytest.raises(fp.FetchError) as caught:
        earnings.fetch_next('NVDA', fetcher=fetcher, today=SESSION)
    assert caught.value.kind == 'network' and caught.value.target == 'earnings:NVDA'
    assert len(caught.value.attempts) == 3 and sleeps == [2, 8]


def test_row3_earnings_result_and_outcome(sleeps):
    row, fetched = earnings.fetch_next_with_outcome(
        'nvda', fetcher=lambda t: {'Earnings Date': [date(2026, 11, 20)]}, today=SESSION)
    assert row == {'status': 'known', 'earnings_date': date(2026, 11, 20)}
    assert len(fetched.attempts) == 1
    assert earnings.fetch_next('NVDA', fetcher=lambda t: {}, today=SESSION) == {
        'status': 'unknown', 'earnings_date': None}


# ---------------------------------------------------------------- row 4: EdgarSource.get

class ScriptedSession:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    post = get


def resp(status, body=b'{}', headers=None):
    return SimpleNamespace(status_code=status, content=body, headers=headers or {})


@pytest.fixture
def edgar(tmp_path, monkeypatch):
    monkeypatch.setenv('SEC_USER_AGENT', 'Research test@example.com')
    throttled = []
    monkeypatch.setattr(monitor_source, 'time', SimpleNamespace(
        sleep=throttled.append, monotonic=time.monotonic))

    def make(session, **kwargs):
        waits = []
        source = EdgarSource(tmp_path, session=session, sleep=waits.append, **kwargs)
        return source, waits, throttled

    return make


def test_row4_edgar_get_retries_5xx_and_names_the_target(edgar):
    session = ScriptedSession(resp(503))
    source, waits, _ = edgar(session)
    with pytest.raises(fp.FetchError) as caught:
        source.get('https://data.sec.gov/submissions/CIK0001045810.json')
    err = caught.value
    assert err.kind == 'http_5xx' and err.status == 503 and len(err.attempts) == 3
    assert err.target == 'sec:submissions:0001045810' and waits == [2, 8] and len(session.calls) == 3
    assert 'SEC HTTP 503' in str(err)


def test_row4_edgar_get_429_waits_min_retry_after_and_backoff(edgar):
    session = ScriptedSession(resp(429, headers={'Retry-After': '1'}), resp(200, b'{"ok": 1}'))
    source, waits, _ = edgar(session)
    assert source.get('https://www.sec.gov/files/company_tickers.json') == b'{"ok": 1}'
    assert waits == [1] and len(session.calls) == 2


def test_row4_edgar_get_4xx_is_not_retried(edgar):
    session = ScriptedSession(resp(404))
    source, waits, _ = edgar(session)
    with pytest.raises(fp.FetchError) as caught:
        source.get('https://www.sec.gov/Archives/edgar/data/1/000104581026000010/x.htm')
    assert caught.value.kind == 'http_4xx' and caught.value.target == 'sec:archive:0001045810-26-000010'
    assert len(session.calls) == 1 and waits == []


def test_row4_edgar_throttle_runs_before_every_attempt(edgar):
    session = ScriptedSession(requests.ReadTimeout('secret-detail'))
    source, waits, throttled = edgar(session)
    with pytest.raises(fp.FetchError) as caught:
        source.get('https://data.sec.gov/test')
    assert len(session.calls) == 3 and len(throttled) == 3          # one throttle wait before each attempt
    assert caught.value.kind == 'timeout' and 'secret-detail' not in str(caught.value)
    assert all(call[1]['timeout'] == (10, 30) for call in session.calls)


def test_row4_edgar_default_session_follows_the_policy(tmp_path):
    source = EdgarSource(tmp_path)
    assert source.session.policy == fp.FetchPolicy()


# ---------------------------------------------------------------- row 4a: per-ticker listing

TICKERS = {'0': {'ticker': 'NVDA', 'cik_str': 1045810}, '1': {'ticker': 'MU', 'cik_str': 723125}}


def filing_source(tmp_path, *, accepted=('2026-10-28T21:00:00.000Z', '2026-10-29T03:00:00.000Z'), fail=None):
    source = EdgarSource(tmp_path, session=object(), sleep=lambda s: None)
    fetched = []

    def get_json(url):
        fetched.append(url)
        if 'company_tickers' in url:
            if fail == 'company_tickers':
                raise fp.FetchError('SEC HTTP 503', kind='http_5xx', status=503, target='sec:company_tickers',
                                    attempts=[{'n': i, 'kind': 'http_5xx', 'status': 503, 'elapsed_ms': 1}
                                              for i in (1, 2, 3)])
            return TICKERS
        if fail and fail in url:
            raise fp.FetchError('SEC 请求失败', kind='network', target='sec:submissions',
                                attempts=[{'n': 1, 'kind': 'network', 'status': None, 'elapsed_ms': 1}])
        return {'filings': {'recent': {
            'form': ['8-K', '8-K'], 'filingDate': ['2026-10-28', '2026-10-28'],
            'accessionNumber': ['0001045810-26-000010', '0001045810-26-000011'],
            'primaryDocument': ['a.htm', 'b.htm'], 'acceptanceDateTime': list(accepted),
            'items': ['2.02', '8.01']}, 'files': []}}

    source.json = get_json
    source.fetched = fetched
    return source


# both filings of the fake source were accepted by then (21:00Z and 03:00Z of the next day)
AFTER_BOTH = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def sec_run(source, targets, *, now=NOW):
    """The SEC listing as the daily node runs it (one adapter call per target; the ticker directory is the whole-call
    target) and the summary it builds from what was stored."""
    run = adapter_run('sec_filings', targets, session=SESSION, now=now, filing_source=source)
    family = _Family('sec_filings', 'daily', targets)
    family.tally, family.stored = run.tally, run.stored
    return run, sec_listing_summary(family, drop_after=now)


def test_sec_listing_per_ticker(tmp_path, fake_db):
    source = filing_source(tmp_path)
    run, (out, whole_call) = sec_run(source, ['NVDA', 'ZZZZ', 'MU'], now=AFTER_BOTH)
    assert whole_call is None and out['errors'] == {'ZZZZ': 'SEC 未找到 ZZZZ 的 CIK'}
    assert set(out['by_ticker']) == {'NVDA', 'MU'}
    assert out['by_ticker']['NVDA'] == {'rows': 2, 'latest': '2026-10-28'}
    assert out['inserted'] == 2 + 2 and out['acceptance_filled'] == 4 and out['existing'] == 0   # filled rows are apart
    row = run.tally.row(trigger='scheduled', run_id='r', session=SESSION, started_at=NOW, finished_at=NOW)
    assert row['status'] == 'partial' and row['target_count'] == 3 and row['ok_count'] == 2
    assert row['failures'] == [{'target': 'ZZZZ', 'reason': 'SEC 未找到 ZZZZ 的 CIK'}]
    by_target = {t['target']: t for t in row['detail']['targets']}
    assert by_target['NVDA']['ok'] and by_target['NVDA']['rows'] == 4 and by_target['MU']['ok']
    # (the adapter path classes a missing CIK — a ValueError out of the SEC source — as parse, the legacy path as other)
    assert by_target['ZZZZ']['ok'] is False and by_target['ZZZZ']['error_kind'] == 'parse'


def test_company_tickers_fetched_once(tmp_path, fake_db):
    source = filing_source(tmp_path)
    sec_run(source, ['NVDA', 'MU', 'NVDA'])
    assert sum('company_tickers' in url for url in source.fetched) == 1
    assert sum('submissions' in url for url in source.fetched) == 2          # a repeated target is listed once


def test_company_tickers_failure_is_the_whole_call_and_costs_one_fetch(tmp_path, fake_db):
    source = filing_source(tmp_path, fail='company_tickers')
    run, (out, whole_call) = sec_run(source, ['NVDA', 'MU'])
    assert out is None and whole_call and run.tally.call_failure == whole_call
    assert sum('company_tickers' in url for url in source.fetched) == 1       # no retry per ticker
    row = run.tally.row(trigger='scheduled', run_id='r', session=SESSION, started_at=NOW, finished_at=NOW)
    assert row['status'] == 'failed' and row['failures'][0]['target'] == '*'
    # EdgarSource.discover re-raises the whole-call failure instead of listing it per ticker
    with pytest.raises(fp.FetchError):
        filing_source(tmp_path, fail='company_tickers').discover(['NVDA'], '2026-10-01', '2026-10-28')


def test_sec_listing_a_failed_ticker_stores_nothing_and_does_not_stop_the_others(tmp_path, fake_db):
    source = filing_source(tmp_path, fail='CIK0001045810')
    run, (out, whole_call) = sec_run(source, ['NVDA', 'MU'])
    assert whole_call is None and list(out['errors']) == ['NVDA'] and set(out['by_ticker']) == {'MU'}
    assert len(fake_db.news) == 1                                     # only MU's listing reached the store


def test_sec_listing_accepts_sources_that_only_have_discover(fake_db):
    class Legacy:
        def discover(self, tickers, start, end):
            assert len(tickers) == 1
            return [{'ticker': tickers[0], 'form': '8-K', 'filing_date': '2026-10-27', 'accession_number': 'a',
                     'edgar_url': 'https://www.sec.gov/x', 'items': None, 'accepted_at': None}]

    run, (out, whole_call) = sec_run(Legacy(), ['NVDA', 'MU'])
    assert whole_call is None and out['errors'] == {} and out['inserted'] + out['acceptance_filled'] >= 2
    assert set(out['by_ticker']) == {'NVDA', 'MU'}


def test_edgar_discover_returns_metas_and_errors(tmp_path):
    source = filing_source(tmp_path)
    metas, errors = source.discover(['NVDA', 'ZZZZ', 'MU'], '2026-10-01', '2026-10-28')
    assert {m['ticker'] for m in metas} == {'NVDA', 'MU'} and list(errors) == ['ZZZZ']
    assert len(source.discover_one('MU', '2026-10-01', '2026-10-28')) == 2


# ---------------------------------------------------------------- spec §5.5: drop_after

def test_news_drop_after_cutoff(fake_db):
    """Every store cuts at the run's immutable cutoff ``now`` (the adapters have no second ``drop_after``)."""
    cutoff = datetime(2026, 10, 28, 22, 0, tzinfo=UTC)
    before, after = int(cutoff.timestamp()) - 3600, int(cutoff.timestamp()) + 3600
    raw = [{'title': 'before', 'link': 'https://x/1', 'relatedTickers': ['NVDA'], 'providerPublishTime': before},
           {'title': 'after', 'link': 'https://x/2', 'relatedTickers': ['NVDA'], 'providerPublishTime': after},
           {'title': 'undated', 'link': 'https://x/3', 'relatedTickers': ['NVDA']}]
    run = adapter_run('news', ['NVDA'], session=SESSION, now=cutoff, news_fetcher=lambda t: raw)
    result = run.stored['NVDA'][2]
    assert result['after_cutoff_dropped'] == 1 and result['inserted'] == 2
    assert [entry[0]['title'] for entry in fake_db.news[0]] == ['before', 'undated']
    assert item(run, 'NVDA')['latest'] == datetime.fromtimestamp(before, UTC).isoformat()
    plain = adapter_run('news', ['NVDA'], session=SESSION, now=cutoff + timedelta(hours=2),
                        news_fetcher=lambda t: raw).stored['NVDA'][2]
    assert plain['after_cutoff_dropped'] == 0 and plain['inserted'] == 3


def test_sec_drop_after_cutoff(tmp_path, fake_db):
    source = filing_source(tmp_path)                       # accepted 2026-10-28 21:00Z and 2026-10-29 03:00Z
    cutoff = datetime(2026, 10, 28, 22, 0, tzinfo=UTC)
    run, (out, _) = sec_run(source, ['NVDA', 'MU'], now=cutoff)
    assert out['after_cutoff_dropped'] == 2 and out['acceptance_filled'] == 2
    assert out['by_ticker']['NVDA']['rows'] == 1
    assert {m['accepted_at'] for m in fake_db.acceptance[0]} == {'2026-10-28T21:00:00+00:00'}
    # the monitor path: the source itself drops, counts and keeps filings without an acceptance time
    metas = filing_source(tmp_path, accepted=('2026-10-28T21:00:00.000Z', 'bad')).discover_one(
        'NVDA', '2026-10-01', '2026-10-28', drop_after=cutoff)
    assert len(metas) == 2
    watching = EdgarSource(tmp_path, session=object(), drop_after=cutoff)
    watching.json = filing_source(tmp_path).json
    assert len(watching.discover_one('NVDA', '2026-10-01', '2026-10-28')) == 1 and watching.after_cutoff_dropped == 1


# ---------------------------------------------------------------- row 5: get_json

def test_get_json_shape_unchanged(server):
    server.routes['/a'] = lambda h: reply(h, 200, b'{"observations": []}')
    data, meta = http.get_json(server.url('/a'), params={'x': '1'}, headers={'X-Test': 'y'}, policy=FAST)
    assert data == {'observations': []}
    assert {k: meta[k] for k in ('ok', 'error', 'status_code')} == {'ok': True, 'error': None, 'status_code': 200}
    assert meta['error_kind'] is None and len(meta['attempts']) == 1


# ---------------------------------------------------------------- row 6: FRED

def test_row6_fred_failure_carries_error_kind_and_attempts_without_the_key(sleeps):
    secret = 'FRED-SECRET-KEY'
    attempts = [{'n': i, 'kind': 'http_5xx', 'status': 503, 'elapsed_ms': 5} for i in (1, 2, 3)]

    def fetcher(url, *, params):
        return None, {'ok': False, 'error': f'http 503: {params["api_key"]}', 'status_code': 503,
                      'attempts': attempts, 'error_kind': 'http_5xx'}

    run = adapter_run('fred', ['T10Y2Y'], session=SESSION, now=NOW, fred_api_key=secret, macro_fetcher=fetcher)
    booked = item(run, 'T10Y2Y')
    assert booked['error_kind'] == 'http_5xx' and booked['http_status'] == 503 and booked['attempts'] == 3
    assert secret not in booked['reason'] and sleeps == []                   # the inner get_json already retried


def test_row6_fred_success_with_tally_books_rows_and_latest(sleeps, fake_db):
    def fetcher(url, *, params):
        return {'observations': [{'date': '2026-10-26', 'value': '1.5'}, {'date': '2026-10-27', 'value': '1.6'}]}, \
            {'ok': True}

    run = adapter_run('fred', ['T10Y2Y'], session=SESSION, now=NOW, fred_api_key='K', macro_fetcher=fetcher)
    assert run.stored['T10Y2Y'][2]['inserted'] == 2 and run.stored['T10Y2Y'][2]['existing'] == 0
    booked = item(run, 'T10Y2Y')
    assert booked['rows'] == 2 and booked['latest'] == '2026-10-27'
    # no key: a skipped row, no request at all
    keyless = adapter_run('fred', ['T10Y2Y'], session=SESSION, now=NOW, fred_api_key=None,
                          macro_fetcher=lambda *a, **k: pytest.fail('no key, no request'))
    assert keyless.tally.row(trigger='scheduled', run_id=None, session=SESSION, started_at=NOW,
                             finished_at=NOW)['status'] == 'skipped'


def test_fred_default_fetcher_goes_through_get_json_with_series_target(monkeypatch):
    seen = {}

    def fake_get_json(url, **kwargs):
        seen.update(kwargs)
        return {'observations': []}, {'ok': True}

    monkeypatch.setattr(macro, 'get_json', fake_get_json)
    macro.fetch_series('T10Y2Y', start=date(2026, 6, 1), end=SESSION, api_key='K')
    assert seen['target'] == 'T10Y2Y' and seen['redact']('K') == '***'


# ---------------------------------------------------------------- rows 7, 8: downloader

def failing_getter(url, **kwargs):
    return None, {'ok': False, 'error': 'http 503: down', 'status_code': 503, 'error_kind': 'http_5xx',
                  'attempts': [{'n': i, 'kind': 'http_5xx', 'status': 503, 'elapsed_ms': 1} for i in (1, 2, 3)]}


def test_downloader_failure_raises_not_empty(tmp_path, monkeypatch):
    monkeypatch.setenv('SEC_USER_AGENT', 'test test@example.com')
    downloader = sec_downloader.SecEdgarDownloader(getter=failing_getter)
    with pytest.raises(fp.FetchError) as caught:
        downloader.download_filings_batch('AAPL', ['10-Q'], SESSION, SESSION, tmp_path)
    assert caught.value.kind == 'http_5xx' and len(caught.value.attempts) == 3
    # submissions failing (company_tickers fine) is a failure too, not zero hits
    def getter(url, **kwargs):
        if 'company_tickers' in url:
            return {'0': {'cik_str': 320193, 'ticker': 'AAPL'}}, {'ok': True, 'error': None, 'status_code': 200}
        return failing_getter(url)

    with pytest.raises(fp.FetchError) as caught:
        sec_downloader.SecEdgarDownloader(getter=getter).download_filings_batch(
            'AAPL', ['10-Q'], SESSION, SESSION, tmp_path)
    assert caught.value.target == 'AAPL'


def test_downloader_company_tickers_fetched_once_even_when_it_failed(tmp_path, monkeypatch):
    monkeypatch.setenv('SEC_USER_AGENT', 'test test@example.com')
    calls = []

    def getter(url, **kwargs):
        calls.append(url)
        return failing_getter(url)

    downloader = sec_downloader.SecEdgarDownloader(getter=getter)
    for ticker in ('AAPL', 'MU', 'NVDA'):
        with pytest.raises(fp.FetchError):
            downloader.download_filings_batch(ticker, ['10-Q'], SESSION, SESSION, tmp_path)
    assert len(calls) == 1


def test_downloader_company_tickers_cached_in_memory(tmp_path, monkeypatch):
    monkeypatch.setenv('SEC_USER_AGENT', 'test test@example.com')
    calls = []

    def getter(url, **kwargs):
        calls.append(url)
        if 'company_tickers' in url:
            return {'0': {'cik_str': 320193, 'ticker': 'AAPL'}}, {'ok': True, 'error': None, 'status_code': 200}
        return {'filings': {'recent': {}}}, {'ok': True, 'error': None, 'status_code': 200}

    downloader = sec_downloader.SecEdgarDownloader(getter=getter)       # no cache_dir: memory only
    downloader.download_filings_batch('AAPL', ['10-Q'], SESSION, SESSION, tmp_path)
    downloader.download_filings_batch('AAPL', ['10-Q'], SESSION, SESSION, tmp_path)
    assert sum('company_tickers' in url for url in calls) == 1


def test_row8_download_document_retries_and_writes(server, tmp_path):
    path = '/Archives/edgar/data/1045810/000104581026000010/x.htm'
    seen = []

    def flaky(handler):
        seen.append(1)
        reply(handler, 503 if len(seen) == 1 else 200, b'<html>ok</html>')

    server.routes[path] = flaky
    waits = []
    dest = tmp_path / 'a' / 'x.htm'
    sec_downloader._download_document(server.url(path), dest, headers={'User-Agent': 'T t@e.com'}, policy=FAST,
                                      sleep=waits.append)
    assert dest.read_bytes() == b'<html>ok</html>' and len(seen) == 2 and waits == [0.01]
    server.routes[path] = lambda h: reply(h, 403)
    with pytest.raises(fp.FetchError) as caught:
        sec_downloader._download_document(server.url(path), tmp_path / 'b.htm', headers={}, policy=FAST,
                                          sleep=waits.append)
    assert caught.value.kind == 'http_4xx' and caught.value.target == 'archive:0001045810-26-000010'
    assert not (tmp_path / 'b.htm').exists()


# ---------------------------------------------------------------- rows 9, 10: pushes

def webhook(server, url_path, policy=FAST_POST):
    waits = []
    client = DiscordClient(server.url(url_path), '', policy=policy, sleep=waits.append)
    return client, waits


def test_row9_discord_webhook_does_not_resend_after_5xx_or_read_timeout(server):
    server.routes['/hook'] = lambda h: reply(h, 502, b'bad gateway')
    client, waits = webhook(server, '/hook')
    with pytest.raises(RuntimeError, match='Discord send failed: 502'):
        client.send(DiscordChannel.EARNINGS, {'content': 'x'})
    assert server.hits['/hook'] == 1 and waits == []
    from test_fetch_policy import slow
    server.routes['/slow'] = slow
    client, waits = webhook(server, '/slow', fp.FetchPolicy(connect_s=0.5, total_s=0.4, backoff_s=(0.01, 0.02),
                                                             retry_post=False))
    with pytest.raises(fp.FetchError) as caught:
        client.send(DiscordChannel.EARNINGS, {'content': 'x'})
    assert caught.value.kind == 'timeout' and server.hits['/slow'] == 1 and waits == []


def test_row9_discord_webhook_retries_429_and_connection_failure(server):
    server.routes['/limited'] = lambda h: reply(h, 429, headers={'Retry-After': '0'})
    client, waits = webhook(server, '/limited')
    with pytest.raises(fp.FetchError) as caught:
        client.send(DiscordChannel.EARNINGS, {'content': 'x'})
    assert caught.value.kind == 'http_429' and server.hits['/limited'] == 3 and waits == [0.0, 0.0]
    client = DiscordClient(f'http://127.0.0.1:{closed_port()}/hook', '', policy=FAST_POST, sleep=waits.append)
    with pytest.raises(fp.FetchError) as caught:
        client.send(DiscordChannel.EARNINGS, {'content': 'x'})
    assert caught.value.kind == 'network' and len(caught.value.attempts) == 3


def test_webhook_token_not_in_error(monkeypatch):
    """A refused connection's exception text repeats the URL; the webhook token must not reach the error, the
    attempt records or the notifier's summary."""
    from investment_assistant.config import NotifyConfig
    from investment_assistant.notify import notifier

    token = 'test-webhook-token-abcdefghijklmnopqrstuvwxyz0123456789'
    url = f'http://127.0.0.1:{closed_port()}/api/webhooks/123456789012345678/{token}'
    waits = []
    client = DiscordClient(url, url, policy=FAST_POST, sleep=waits.append)
    with pytest.raises(fp.FetchError) as caught:
        client.send(DiscordChannel.EARNINGS, {'content': 'x'})
    assert caught.value.kind == 'network' and len(caught.value.attempts) == 3
    assert token not in str(caught.value) and token not in repr(caught.value.attempts)
    assert '123456789012345678' not in str(caught.value)
    out = notifier.dispatch('filings', 'success', {'downloaded_count': 0, 'filings': []},
                            NotifyConfig(webhooks={'earnings': url}), client=client)
    assert out['sent'] is False and out['reason'].startswith('send_failed: network error')
    assert token not in repr(out)
    # an HTTP error message built by a caller that did include the URL is scrubbed too
    with pytest.raises(fp.FetchError) as caught:
        fp.fetch('t', lambda: (_ for _ in ()).throw(RuntimeError(f'POST {url} failed')), sleep=lambda s: None)
    assert token not in str(caught.value)


def test_row9_discord_webhook_success(server):
    server.routes['/ok'] = lambda h: reply(h, 204, b'')
    client, waits = webhook(server, '/ok')
    client.send(DiscordChannel.EARNINGS, {'content': 'x'})
    assert server.hits['/ok'] == 1


def bot_receipt(channel='123'):
    return resp(200, json.dumps({'id': '9', 'channel_id': channel}).encode())


class JsonResp(SimpleNamespace):
    def json(self):
        return json.loads(self.content)


def bot_resp(status, body=b'{}', headers=None):
    return JsonResp(status_code=status, content=body, headers=headers or {})


def bot(session, monkeypatch, **kwargs):
    monkeypatch.setenv('SEC_DISCORD_BOT_TOKEN', 'test-bot-token')
    waits = []
    return DiscordBotClient('123', session=session, sleep=waits.append, **kwargs), waits


def test_row10_bot_retries_429_with_the_same_nonce(monkeypatch):
    session = ScriptedSession(bot_resp(429, headers={'Retry-After': '1'}),
                              bot_resp(200, json.dumps({'id': '9', 'channel_id': '123'}).encode()))
    client, waits = bot(session, monkeypatch)
    assert client.send('hello', 'nonce-1') == {'message_id': '9', 'channel_id': '123'}
    assert waits == [1] and len(session.calls) == 2


def test_row10_bot_never_resends_after_a_read_timeout_or_5xx(monkeypatch):
    session = ScriptedSession(requests.ReadTimeout('test-bot-token'))
    client, waits = bot(session, monkeypatch)
    with pytest.raises(DeliveryError) as caught:
        client.send('hello', 'nonce-1')
    assert caught.value.uncertain and 'test-bot-token' not in str(caught.value)
    assert len(session.calls) == 1 and waits == []
    session = ScriptedSession(bot_resp(503))
    client, waits = bot(session, monkeypatch)
    with pytest.raises(DeliveryError) as caught:
        client.send('hello', 'nonce-1')
    assert caught.value.uncertain and str(caught.value) == 'Discord HTTP 503' and len(session.calls) == 1
    session = ScriptedSession(bot_resp(403))
    client, _ = bot(session, monkeypatch)
    with pytest.raises(DeliveryError) as caught:
        client.send('hello', 'nonce-1')
    assert not caught.value.uncertain and str(caught.value) == 'Discord HTTP 403'


def test_row10_bot_retries_when_the_request_never_left(monkeypatch):
    session = ScriptedSession(requests.ConnectTimeout('x'), bot_resp(200, json.dumps(
        {'id': '9', 'channel_id': '123'}).encode()))
    client, waits = bot(session, monkeypatch)
    assert client.send('hello', 'n')['message_id'] == '9' and len(session.calls) == 2
    assert all(call[1]['timeout'] == (10, 30) for call in session.calls)


def test_row10_bot_429_exhausted_is_a_definite_rejection(monkeypatch):
    session = ScriptedSession(bot_resp(429, headers={'Retry-After': '60'}))
    client, waits = bot(session, monkeypatch)
    with pytest.raises(DeliveryError) as caught:
        client.send('hello', 'n')
    assert not caught.value.uncertain and waits == [2, 8] and len(session.calls) == 3
