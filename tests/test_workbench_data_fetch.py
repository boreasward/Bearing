"""Single-family manual fetch use cases against a disposable schema (v2.3 Task 7, design §7).

All market data below is synthetic. The use case layer only writes data tables
and one data_fetch_runs row; test_fetch_writes_nothing_else pins that nothing
else (rules, plans, candidates, pool, briefs, job reports) is touched.
"""
import os
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import psycopg
import pytest

from investment_assistant.core import tx as core_tx

from investment_assistant.data.calendar import add_trading_days
from investment_assistant.workbench import collect, data_view, families, service
from investment_assistant.workbench.universe import Universe, UniverseError

SESSION = date(2026, 10, 22)                         # Thursday
NOW = datetime(2026, 10, 22, 22, 30, tzinfo=UTC)     # 18:30 New York


def frame(ticker, end):
    closes = [500.0] * 300 if ticker == 'SPY' else \
        [50 + i * 0.25 for i in range(296)] + [50 + 296 * 0.25 - 6] * 3 + [50 + 296 * 0.25 + 1]
    days = [add_trading_days(SESSION, -(len(closes) - 1 - i)) for i in range(len(closes))]
    rows = [(d, c) for d, c in zip(days, closes) if d <= end]
    index = pd.DatetimeIndex([pd.Timestamp(d, tz='America/New_York') for d, _ in rows])
    return pd.DataFrame({'Open': [c for _, c in rows], 'High': [c * 1.02 for _, c in rows],
                         'Low': [c * 0.99 for _, c in rows], 'Close': [c for _, c in rows],
                         'Adj Close': [c for _, c in rows], 'Volume': [1000] * len(rows),
                         'Stock Splits': [0.0] * len(rows)}, index=index)


class Sources:
    def __init__(self):
        self.calls = []

    def bars(self, ticker, start, end_exclusive):
        self.calls.append(('bars', ticker))
        return frame(ticker, end_exclusive - timedelta(days=1))

    def news(self, ticker):
        self.calls.append(('news', ticker))
        return [{'content': {'title': f'{ticker} headline', 'pubDate': '2026-10-22T12:00:00Z',
                             'canonicalUrl': {'url': f'https://news.example.com/{ticker}'}}}]

    def discover(self, tickers, start, end):
        self.calls.append(('sec', tuple(sorted(tickers))))
        return []


@pytest.fixture
def db(workbench_db, monkeypatch):
    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(core_tx, 'connection', connection)
    return workbench_db


def deps(sources, **overrides):
    return service.Deps(bar_fetcher=sources.bars, news_fetcher=sources.news,
                        filing_source=sources, **overrides)


def seed_watchlist(db, rows):
    for ticker, tags in rows:
        db.execute("INSERT INTO watchlist_items (ticker, status, tags) VALUES (%s, 'active', %s)",
                   [ticker, tags])


def fetch_row(db, run_id):
    return db.execute('SELECT family, job, trigger, run_id, scope, ticker, session, status, '
                      'target_count, ok_count FROM data_fetch_runs WHERE run_id = %s',
                      [run_id]).fetchone()


def test_fetch_single_ticker(db):
    out = data_view.run_fetch('bars', 'MU', run_id='run-1', now=NOW, deps=deps(Sources()))
    assert db.execute("SELECT count(*) FROM price_bars WHERE ticker = 'MU'").fetchone()[0] > 0
    assert out['status'] == 'success'
    assert out['data_status'] == {'written': 1, 'error': None}
    assert fetch_row(db, 'run-1') == ('bars', 'workbench', 'manual_family', 'run-1', 'ticker', 'MU',
                                      SESSION, 'success', 1, 1)
    assert out['row']['ticker'] == 'MU' and out['row']['scope'] == 'ticker'


def test_fetch_row_times_use_real_wall_clock(db):
    """拉取行 started_at/finished_at 用真实墙钟（可注入），不再等于调用前的 now（I07）。"""
    tick = iter([datetime(2026, 10, 22, 20, 0, tzinfo=UTC),
                 datetime(2026, 10, 22, 20, 5, tzinfo=UTC)])   # 采集耗时 5 分钟

    out = data_view.run_fetch('bars', 'MU', run_id='run-w', now=NOW, deps=deps(Sources()),
                              wall_now=lambda: next(tick))
    assert out['row']['started_at'] == datetime(2026, 10, 22, 20, 0, tzinfo=UTC)
    assert out['row']['finished_at'] == datetime(2026, 10, 22, 20, 5, tzinfo=UTC)


def test_validate_fetch_rejects_non_string_ticker():
    """非字符串 ticker 是参数类型错误（路由 400），不是 .strip() 的 AttributeError（I06）。"""
    with pytest.raises(ValueError, match='ticker 必须是字符串'):
        data_view.validate_fetch('bars', 123)
    with pytest.raises(ValueError, match='ticker 必须是字符串'):
        data_view.validate_fetch('news', ['MU'])


def test_fetch_normalizes_ticker():
    assert data_view.validate_fetch('bars', ' mu ') == ('bars', 'MU')


def test_fetch_family_uses_targets(db):
    seed_watchlist(db, [('NVDA', ['core'])])
    sources = Sources()
    data_view.run_fetch('news', None, run_id='run-2', now=NOW, deps=deps(sources))
    called = {ticker for kind, ticker in sources.calls if kind == 'news'}
    assert called == set(families.targets(db, 'news')[0])


def test_validation_errors():
    with pytest.raises(ValueError, match='派生族，请拉取日线'):
        data_view.validate_fetch('atr', None)
    with pytest.raises(ValueError, match='该族不支持单只拉取'):
        data_view.validate_fetch('fred', 'MU')
    with pytest.raises(ValueError):
        data_view.validate_fetch('bars', 'NOT A TICKER')
    with pytest.raises(LookupError):
        data_view.validate_fetch('nope', None)


def test_fetch_fred_without_key_is_skipped(db, monkeypatch):
    monkeypatch.delenv('FRED_API_KEY', raising=False)
    called = []

    def macro_fetcher(url, *, params):
        called.append(url)
        return ({'observations': []}, {'ok': True})

    d = deps(Sources())
    d.macro_fetcher = macro_fetcher
    out = data_view.run_fetch('fred', None, run_id='run-3', now=NOW, deps=d)
    status, detail = db.execute('SELECT status, detail FROM data_fetch_runs WHERE run_id = %s',
                                ['run-3']).fetchone()
    assert status == 'skipped' and detail['reason'] == 'FRED_API_KEY 未配置'
    assert called == [] and out['status'] == 'skipped'


def test_fetch_universe_reload(db):
    def broken():
        raise UniverseError('bad file')

    d = deps(Sources())
    d.universe_loader = broken
    out = data_view.run_fetch('universe', None, run_id='run-4', now=NOW, deps=d)
    status, failures = db.execute('SELECT status, failures FROM data_fetch_runs WHERE run_id = %s',
                                  ['run-4']).fetchone()
    assert status == 'failed' and 'bad file' in failures[0]['reason']

    d2 = deps(Sources())
    d2.universe = Universe('test', SESSION, 'test', ('NVDA',))
    out = data_view.run_fetch('universe', None, run_id='run-5', now=NOW, deps=d2)
    ok_count, detail = db.execute('SELECT ok_count, detail FROM data_fetch_runs WHERE run_id = %s',
                                  ['run-5']).fetchone()
    assert out['status'] == 'success' and ok_count == 1
    assert detail['as_of'] == SESSION.isoformat() and detail['name'] == 'test'


def test_fetch_writes_nothing_else(db):
    seed_watchlist(db, [('NVDA', ['core'])])
    tables = ('trade_plans', 'signal_candidates', 'pool_tiers', 'job_reports', 'signal_briefs')

    def counts():
        return {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in tables}

    before = counts()
    data_view.run_fetch('bars', None, run_id='run-6', now=NOW, deps=deps(Sources()))
    assert counts() == before


def test_busy_lock_returns_busy(db):
    d = deps(Sources())
    with psycopg.connect(os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL'],
                         autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(hashtext('data_family:bars'))")
        with pytest.raises(data_view.FetchBusy, match='该族正在采集'):
            data_view.start_fetch('bars', None, now_fn=lambda: NOW, deps=d)
        out = data_view.run_fetch('bars', None, run_id='run-7', now=NOW, deps=d)
        assert out == {'status': 'busy'}
        assert db.execute('SELECT count(*) FROM data_fetch_runs').fetchone()[0] == 0


def test_lock_released_when_connection_closes(db, monkeypatch):
    from investment_assistant.tasks import runner

    submitted = []

    def record_submit(kind, fn, **kwargs):
        submitted.append((kind, kwargs.get('with_run_id')))
        return 'fake-run'

    monkeypatch.setattr(runner, 'submit', record_submit)
    other = psycopg.connect(os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL'], autocommit=True)
    other.execute("SELECT pg_advisory_lock(hashtext('data_family:bars'))")
    other.close()   # 会话结束：锁随之释放，探测不应再报忙
    out = data_view.start_fetch('bars', None, now_fn=lambda: NOW, deps=deps(Sources()))
    assert out == {'run_id': 'fake-run', 'status': 'pending'}
    assert submitted == [('data:bars', True)]


def test_start_fetch_needs_migration(db):
    db.execute('DROP TABLE data_fetch_runs')
    with pytest.raises(service.Unavailable, match='024'):
        data_view.start_fetch('bars', None, now_fn=lambda: NOW, deps=deps(Sources()))


def test_start_fetch_checks_workbench_tables_before_submit(db, monkeypatch):
    """提交前安全检查工作台表：缺 price_bars 时 503 且不提交后台任务（I06）。"""
    db.execute('DROP TABLE price_bars')
    submitted = []
    monkeypatch.setattr('investment_assistant.tasks.runner.submit',
                        lambda *a, **k: submitted.append(a) or 'r')
    with pytest.raises(service.Unavailable, match='price_bars'):
        data_view.start_fetch('bars', None, now_fn=lambda: NOW, deps=deps(Sources()))
    assert submitted == []


def test_probe_unlock_failure_discards_connection(db, monkeypatch):
    """探测解锁失败：物理丢弃该连接而不是把带锁会话还池，并把错误如实上抛（I01）。"""
    def broken_unlock(conn, family_id):
        raise RuntimeError('unlock boom')

    discarded = []
    monkeypatch.setattr(collect, 'unlock_family', broken_unlock)
    monkeypatch.setattr(collect, 'discard_connection', discarded.append)
    with pytest.raises(RuntimeError, match='unlock boom'):
        data_view.start_fetch('bars', None, now_fn=lambda: NOW, deps=deps(Sources()))
    assert len(discarded) == 1 and discarded[0] is db
