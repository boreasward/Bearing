"""Read-only use cases behind the Data menu (v2.3 Task 8, design §8): overview,
family detail and raw browse. All market data below is synthetic. The reads never
collect: a never-collected ticker is an empty 200, not an error and not a fetch
(test_browse_unknown_ticker_is_empty_not_error), and every GET leaves the business
tables untouched (test_reads_are_read_only)."""
import investment_assistant.core.dbutil as core_dbutil
import investment_assistant.data.repo as data_repo
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

from investment_assistant.core import tx as core_tx

from investment_assistant.data.calendar import add_trading_days
from investment_assistant.data.adapters import bars as bars_module
from investment_assistant.derivation.indicators import price
import investment_assistant.data.collect as collect
import investment_assistant.views.data as data_view
import investment_assistant.workflow.targets as families
import investment_assistant.core.tx as service
from investment_assistant.data import families as data_families
from investment_assistant.execution import plan_levels as plans
from investment_assistant.data.adapters import fred as macro_module
from investment_assistant.data.adapters import news as news_module
from investment_assistant.data.adapters import universe as universe_module

SESSION = date(2026, 10, 22)                         # Thursday
NOW = datetime(2026, 10, 22, 22, 30, tzinfo=UTC)     # 18:30 New York

CARD_KEYS = {'id', 'title', 'purpose', 'kind', 'fetch_via', 'per_ticker', 'job', 'coverage',
             'latest_at', 'coverage_note', 'last_run', 'last_ticker_run', 'last_success_at',
             'evidence', 'notes', 'current_error', 'fetch_runs_ready'}
BUSINESS_TABLES = core_dbutil.WORKBENCH_TABLES + ('data_fetch_runs',)


@pytest.fixture
def db(workbench_db, monkeypatch):
    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(core_tx, 'connection', connection)
    return workbench_db


def _bar(conn, **overrides):
    values = {'ticker': 'NVDA', 'price_date': SESSION, 'open': 100, 'high': 105, 'low': 99,
              'close': 104, 'volume': 1000, 'adj_close': 104, 'revision': 1, 'fetched_at': NOW}
    values.update(overrides)
    cols = ', '.join(values)
    conn.execute(f"INSERT INTO price_bars ({cols}) VALUES ({', '.join(['%s'] * len(values))})",
                 list(values.values()))


def _run(db, **overrides):
    row = {'family': 'bars', 'job': 'workbench', 'trigger': 'manual_family', 'run_id': 'r1',
           'scope': 'family', 'ticker': None, 'session': SESSION, 'status': 'success',
           'target_count': 2, 'ok_count': 2, 'failures': [], 'detail': {},
           'started_at': NOW, 'finished_at': NOW}
    row.update(overrides)
    data_repo.insert_fetch_run(db, row)


def test_overview_has_six_cards_and_evidence(db):
    _bar(db)
    _run(db, run_id='r-ov', finished_at=NOW + timedelta(hours=1))
    # v2.8: the workbench / filings rows are replaced by one ``daily`` row (028); every family card shows it

    out = data_view.overview(now=NOW)
    cards = {card['id']: card for card in out['families']}
    assert [card['id'] for card in out['families']] == \
        ['bars', 'sec_filings', 'news', 'atr', 'fred', 'universe']
    assert out['evidence'] == data_families.EVIDENCE
    assert out['session'] == SESSION and out['fetch_runs_ready'] is True

    bars_card = cards['bars']
    assert set(bars_card) == CARD_KEYS
    assert bars_card['coverage']['targets'] == len(families.targets(db, 'bars', session=NOW.date(), now=NOW)[0])
    assert bars_card['coverage']['covered'] == 1 and 'NVDA' not in bars_card['coverage']['lagging']
    assert set(bars_card['job']) == {'name', 'enabled', 'next_run_at'}
    assert bars_card['job']['name'] == 'daily' and bars_card['job']['enabled'] is True
    assert bars_card['job']['next_run_at'] is not None
    assert bars_card['last_run'] == {'status': 'success', 'trigger': 'manual_family', 'scope': 'family',
                                     'ticker': None, 'job': 'workbench', 'reason': None,
                                     'finished_at': NOW + timedelta(hours=1),
                                     'ok_count': 2, 'target_count': 2, 'failures': []}
    assert bars_card['last_success_at'] == NOW + timedelta(hours=1)
    assert bars_card['fetch_runs_ready'] is True and bars_card['current_error'] is None
    assert bars_card['coverage_note'] == data_families.COVERAGE_NOTES['bars']

    assert {card['job']['name'] for card in cards.values()} == {'daily'}
    assert 'atr' not in {family.id for family in data_families.FAMILIES}
    assert 'no_filing_is_not_missing' in cards['sec_filings']['notes']
    assert 'pool_empty' in cards['news']['notes']

    assert cards['atr']['evidence'] == data_families.EVIDENCE + ' `plan-1` 的计算输入，规则 `gate_hold`'
    assert cards['atr']['last_run'] == bars_card['last_run']      # ATR 取 bars 的拉取记录
    assert cards['atr']['last_success_at'] == bars_card['last_success_at']
    assert cards['news']['last_run'] is None
    assert bars_card['last_ticker_run'] is None

    loaded = universe_module.load()
    assert cards['universe']['coverage'] == {'targets': len(loaded.tickers),
                                             'covered': len(loaded.tickers), 'lagging': []}
    assert cards['universe']['latest_at'] == loaded.as_of
    assert 'universe_stale' not in cards['universe']['notes']


def test_card_uses_latest_run(db):
    success_at, failed_at = NOW + timedelta(hours=1), NOW + timedelta(hours=2)
    _run(db, run_id='ok', finished_at=success_at)
    _run(db, run_id='bad', status='failed', ok_count=0, finished_at=failed_at,
         failures=[{'target': f'T{i}', 'reason': f'boom {i}'} for i in range(8)])
    db.commit()

    card = data_view.overview(now=NOW)['families'][0]
    assert card['last_run']['status'] == 'failed' and card['last_run']['finished_at'] == failed_at
    assert [f['target'] for f in card['last_run']['failures']] == ['T0', 'T1', 'T2', 'T3', 'T4']
    assert card['last_success_at'] == success_at


def test_card_keeps_family_run_and_shows_newer_ticker_run_apart(db):
    """2026-10-27 (after acceptance): a single-ticker probe must not stand in for the
    family. last_run is the newest family-scope row; a newer ticker row shows on its
    own line; last_success_at counts family rows only."""
    family_at, ok_ticker_at, bad_ticker_at = (NOW + timedelta(hours=h) for h in (1, 2, 3))
    _run(db, run_id='fam', finished_at=family_at)
    _run(db, run_id='mu', scope='ticker', ticker='MU', target_count=1, ok_count=1,
         finished_at=ok_ticker_at)
    _run(db, run_id='bad', scope='ticker', ticker='ZZZZ', status='failed', target_count=1,
         ok_count=0, finished_at=bad_ticker_at,
         failures=[{'target': 'ZZZZ', 'reason': f'boom {i}'} for i in range(7)])
    db.commit()

    cards = {card['id']: card for card in data_view.overview(now=NOW)['families']}
    bars_card = cards['bars']
    assert bars_card['last_run']['scope'] == 'family' and bars_card['last_run']['finished_at'] == family_at
    ticker_run = bars_card['last_ticker_run']
    assert ticker_run['ticker'] == 'ZZZZ' and ticker_run['status'] == 'failed'
    assert ticker_run['finished_at'] == bad_ticker_at and len(ticker_run['failures']) == 5
    assert bars_card['last_success_at'] == family_at          # MU 单只成功不算整族成功
    assert cards['atr']['last_ticker_run'] == ticker_run      # ATR 跟随日线

    detail = data_view.family_detail('bars', now=NOW)
    assert detail['card']['last_ticker_run'] == ticker_run
    assert [run['run_id'] for run in detail['runs']] == ['bad', 'mu', 'fam']   # 记录表不变


def test_ticker_run_older_than_family_run_is_hidden(db):
    _run(db, run_id='bad', scope='ticker', ticker='ZZZZ', status='failed', target_count=1,
         ok_count=0, finished_at=NOW + timedelta(hours=1),
         failures=[{'target': 'ZZZZ', 'reason': 'boom'}])
    _run(db, run_id='fam', finished_at=NOW + timedelta(hours=2))   # 之后的批处理收起旧试探
    db.commit()

    bars_card = data_view.overview(now=NOW)['families'][0]
    assert bars_card['last_run']['finished_at'] == NOW + timedelta(hours=2)
    assert bars_card['last_ticker_run'] is None


def test_only_ticker_runs_leave_family_run_empty(db):
    _run(db, run_id='mu', scope='ticker', ticker='MU', target_count=1, ok_count=1)
    db.commit()

    bars_card = data_view.overview(now=NOW)['families'][0]
    assert bars_card['last_run'] is None and bars_card['last_success_at'] is None
    assert bars_card['last_ticker_run']['ticker'] == 'MU'


def test_overview_without_fetch_runs_table(db):
    _bar(db)
    db.execute('DROP TABLE data_fetch_runs')
    db.commit()

    out = data_view.overview(now=NOW)
    assert out['fetch_runs_ready'] is False
    for card in out['families']:
        assert card['last_run'] is None and card['last_success_at'] is None
        assert card['last_ticker_run'] is None
    bars_card = next(card for card in out['families'] if card['id'] == 'bars')
    assert bars_card['coverage']['targets'] == len(families.targets(db, 'bars', session=NOW.date(), now=NOW)[0])
    assert bars_card['coverage']['covered'] == 1


def test_detail_rows_and_last_failure(db):
    _bar(db)
    _run(db, status='failed', ok_count=0, failures=[{'target': '*', 'reason': 'whole family down'}])
    db.commit()

    detail = data_view.family_detail('bars', now=NOW)
    assert detail['card']['id'] == 'bars' and detail['card']['coverage']['targets'] > 0
    assert [run['status'] for run in detail['runs']] == ['failed']
    assert detail['rows']
    for row in detail['rows']:
        assert set(row) == {'target', 'latest_at', 'lagging', 'last_failure'}
        assert row['last_failure'] == 'whole family down'   # workbench 全族 '*' 覆盖本次目标
    nvda = next(row for row in detail['rows'] if row['target'] == 'NVDA')
    assert nvda['latest_at'] == SESSION and nvda['lagging'] is False

    atr = data_view.family_detail('atr', now=NOW)          # ATR 详情取 bars 的记录
    assert atr['card']['id'] == 'atr' and [run['family'] for run in atr['runs']] == ['bars']
    assert data_view.family_detail('universe', now=NOW)['rows'] == []


@pytest.mark.parametrize('job, spreads', [('workbench', True), ('daily', True), ('sec_backfill', False), ('filings', False)])
def test_family_wildcard_failure_spreads_only_for_the_batch_jobs(db, job, spreads):
    """A family-scoped '*' without ``failed_targets`` covers the current targets for the batch jobs (the v2.7
    ``workbench`` and the v2.8 ``daily``), never another job's list (the SEC backfill, the old filings task)."""
    _bar(db)
    _run(db, job=job, status='failed', ok_count=0, failures=[{'target': '*', 'reason': 'whole family down'}])
    db.commit()
    detail = data_view.family_detail('bars', now=NOW)
    assert any(row['last_failure'] == 'whole family down' for row in detail['rows']) is spreads


def test_ticker_scoped_wildcard_failure_stays_on_its_ticker(db):
    """MU 单只 SEC 失败的 '*' 哨兵只作用于 MU，不污染 NVDA（I05）。"""
    for ticker in ('MU', 'NVDA'):
        db.execute("INSERT INTO watchlist_items (ticker, status, tags) VALUES (%s, 'active', '{}')",
                   [ticker])
    _run(db, family='sec_filings', scope='ticker', ticker='MU', status='failed', ok_count=0,
         target_count=1, failures=[{'target': '*', 'reason': 'edgar down'}])
    db.commit()

    rows = {row['target']: row for row in data_view.family_detail('sec_filings', now=NOW)['rows']}
    assert rows['MU']['last_failure'] == 'edgar down'
    assert rows['NVDA']['last_failure'] is None   # 未采过 ≠ 失败


def test_family_wildcard_failure_scopes_to_its_own_targets(db):
    """filings monitor 的全族 '*' 失败只作用于该次调用的目标清单，不推广到当前池（I05/I03）。"""
    for ticker in ('MU', 'NVDA'):
        db.execute("INSERT INTO watchlist_items (ticker, status, tags) VALUES (%s, 'active', '{}')",
                   [ticker])
    _run(db, family='sec_filings', job='filings', trigger='scheduled', status='failed', ok_count=0,
         target_count=1, failures=[{'target': '*', 'reason': 'edgar down'}],
         detail={'targets_source': 'sec_monitor.watchlist_path', 'failed_targets': ['AAPL']})
    db.commit()

    rows = {row['target']: row for row in data_view.family_detail('sec_filings', now=NOW)['rows']}
    assert set(rows) == {'MU', 'NVDA'}
    assert all(row['last_failure'] is None for row in rows.values())   # AAPL 不在池里，不外溢


def test_last_run_carries_job_and_skipped_reason(db):
    _run(db, family='fred', status='skipped', target_count=2, ok_count=0,
         detail={'reason': 'FRED_API_KEY 未配置'})
    db.commit()

    card = next(c for c in data_view.overview(now=NOW)['families'] if c['id'] == 'fred')
    assert card['last_run']['status'] == 'skipped'
    assert card['last_run']['reason'] == 'FRED_API_KEY 未配置'   # I11: 原因进卡片载荷
    assert card['last_run']['job'] == 'workbench'                # I13: 实际任务路径
    assert card['last_success_at'] is None                       # skipped 不前移最近成功


def test_runs_newest_first_and_same_session_manual_plus_scheduled_append(db):
    """同 session 的定时+手动两行都保留，最新在前（created_at 相同时按 id）；last_run 取
    最新、last_success 取最新成功（M05）。"""
    _bar(db)
    base = NOW - timedelta(hours=3)
    _run(db, run_id='sched', trigger='scheduled', finished_at=base, started_at=base)
    _run(db, run_id='manual', trigger='manual_family', status='partial', ok_count=1,
         finished_at=NOW - timedelta(hours=1))
    db.commit()

    detail = data_view.family_detail('bars', now=NOW)
    assert [run['run_id'] for run in detail['runs']] == ['manual', 'sched']
    card = detail['card']
    assert card['last_run']['trigger'] == 'manual_family' and card['last_run']['status'] == 'partial'
    assert card['last_success_at'] == NOW - timedelta(hours=1)   # partial 也算最近成功（spec §8）
    assert data_view.overview(now=NOW)['families'][0]['last_run']['trigger'] == 'manual_family'


def test_universe_card_exposes_current_error_not_a_fake_run(db, monkeypatch):
    """当前范围文件坏了：历史成功拉取保留，文件错误独立暴露（M03）。"""
    _run(db, family='universe', run_id='u-ok', finished_at=NOW - timedelta(hours=2))
    db.commit()

    def broken():
        raise universe_module.UniverseError('bad member count: 2')

    monkeypatch.setattr(universe_module, 'load', broken)
    card = next(c for c in data_view.overview(now=NOW)['families'] if c['id'] == 'universe')
    assert 'universe_unavailable' in card['notes']
    assert 'bad member count: 2' in card['current_error']       # 原文诊断，不笼统称不可读
    assert card['coverage'] == {'targets': 0, 'covered': 0, 'lagging': []}
    assert card['last_run'] is not None and card['last_run']['status'] == 'success'  # 历史保留
    assert card['last_success_at'] == NOW - timedelta(hours=2)

    browse = data_view.browse('universe', now=NOW)
    assert browse['has_data'] is False and 'bad member count: 2' in browse['current_error']


def test_browse_bars_latest_revision_and_counts(db):
    _bar(db, price_date=add_trading_days(SESSION, -1), close=100)
    _bar(db, close=100, fetched_at=NOW - timedelta(hours=2))
    _bar(db, close=101, revision=2, fetched_at=NOW - timedelta(hours=1))
    db.commit()

    out = data_view.browse('bars', now=NOW, ticker='NVDA')
    assert out['family'] == 'bars' and out['key'] == 'NVDA' and out['has_data'] is True
    rows = out['rows']
    assert rows == sorted(rows, key=lambda row: row['price_date'], reverse=True)   # 新到旧
    day = next(row for row in rows if row['price_date'] == SESSION)                # 同日两修订 → 一行
    assert day['close'] == 101.0 and day['revisions'] == 2
    assert day['fetched_at'] == NOW - timedelta(hours=1) and day['note']
    assert rows[1]['revisions'] == 1


def test_browse_atr_matches_plans_atr14(db):
    for i in range(60 + price.MIN_BARS):
        day = add_trading_days(SESSION, -(74 - i))
        close = 100 + (i % 9)
        _bar(db, price_date=day, open=close - 1, high=close + 2, low=close - 2,
             close=close, adj_close=close)
    db.commit()

    out = data_view.browse('atr', now=NOW, ticker='NVDA')
    assert len(out['rows']) == 60
    window = data_repo.read_bars(db, 'NVDA', SESSION, cutoff_at=NOW, limit=60 + price.MIN_BARS)
    last = out['rows'][-1]
    assert set(last) == {'price_date', 'close', 'atr', 'atr_pct'}
    assert last['price_date'] == SESSION
    assert last['atr'] == float(price.atr14(window))
    assert last['atr_pct'] == last['atr'] / last['close']


def test_browse_unknown_ticker_is_empty_not_error(db, monkeypatch):
    calls = []

    def recorder(name):
        def fn(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f'browse 不应调用外部数据源：{name}')
        return fn

    monkeypatch.setattr(bars_module, 'fetch_history', recorder('bars'))
    monkeypatch.setattr(news_module, 'fetch_yfinance', recorder('news'))
    monkeypatch.setattr(macro_module, 'fetch_series', recorder('fred'))
    monkeypatch.setattr(collect, 'sec_listing', recorder('sec'))
    db.commit()

    out = data_view.browse('bars', now=NOW, ticker='ZZZZ')
    assert out == {'family': 'bars', 'key': 'ZZZZ', 'has_data': False, 'rows': []}
    assert calls == []


def test_browse_validation():
    with pytest.raises(ValueError, match='ticker'):
        data_view.browse('bars', now=NOW)
    with pytest.raises(ValueError):
        data_view.browse('sec_filings', now=NOW)
    with pytest.raises(ValueError):
        data_view.browse('fred', now=NOW)
    with pytest.raises(ValueError, match='未知序列'):
        data_view.browse('fred', now=NOW, series='DGS10')
    with pytest.raises(LookupError):
        data_view.browse('nope', now=NOW)
    with pytest.raises(LookupError):
        data_view.family_detail('nope', now=NOW)


def test_reads_are_read_only(db):
    _bar(db, close=104)
    _bar(db, close=105, revision=2, fetched_at=NOW - timedelta(hours=1))
    data_repo.store_news(db, [({'source': 'sec_8k', 'title': 'NVDA 8-K',
                                 'url': 'https://sec.example/x', 'publisher': 'SEC EDGAR',
                                 'published_at': datetime(2026, 10, 20, 14, tzinfo=UTC)}, ['NVDA'])],
                          now=NOW)
    db.execute('INSERT INTO filings (ticker, form, filing_date, accession_number, primary_document, '
               'file_path, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)',
               ['NVDA', '10-Q', SESSION, '0001045810-26-000001', 'd.htm', '/x', NOW])
    data_repo.store_macro(db, 'BAMLH0A0HYM2',
                           [{'obs_date': date(2026, 10, 21), 'value': Decimal('3.2')}], now=NOW)
    _run(db)
    db.commit()

    def counts():
        out = {table: db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
               for table in BUSINESS_TABLES}
        db.commit()
        return out

    before = counts()
    data_view.overview(now=NOW)
    data_view.family_detail('bars', now=NOW)
    data_view.family_detail('universe', now=NOW)
    data_view.browse('bars', now=NOW, ticker='NVDA')
    data_view.browse('atr', now=NOW, ticker='NVDA')
    data_view.browse('news', now=NOW, ticker='NVDA')
    data_view.browse('sec_filings', now=NOW, ticker='NVDA')
    data_view.browse('fred', now=NOW, series='BAMLH0A0HYM2')
    data_view.browse('universe', now=NOW)
    assert counts() == before

    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with data_view._read_tx() as conn:
            conn.execute('INSERT INTO data_fetch_runs (family, job, trigger, scope, status, '
                         'target_count, ok_count, started_at, finished_at) '
                         "VALUES ('bars', 'workbench', 'manual_family', 'family', 'failed', 1, 0, %s, %s)",
                         [NOW, NOW])


def _news(conn, ticker, source, url, published_at, fetched_at=NOW):
    data_repo.store_news(conn, [({'source': source, 'title': f'{ticker} {url}', 'url': url,
                                   'publisher': 'p', 'published_at': published_at}, [ticker])],
                          now=fetched_at)


def test_browse_news_reads_whole_window_and_sec_survives_busy_news(db):
    """窗口内完整读取：201 条 yfinance 不再静默截到 200，也不会把窗口内的 sec_8k
    挤出 SEC 浏览（I10）。"""
    recent = datetime(2026, 10, 20, 12, tzinfo=UTC)          # 窗口内（60 天）
    for i in range(201):
        _news(db, 'MU', 'yfinance', f'https://n.example/{i}', recent)
    _news(db, 'MU', 'sec_8k', 'https://sec.example/old-8k',
          datetime(2026, 10, 12, 14, tzinfo=UTC))            # 10 天前的 8-K，无 filings 行
    db.commit()

    news = data_view.browse('news', now=NOW, ticker='MU')
    assert len(news['rows']) == 202                          # 全窗口，无第二个静默上限
    sec = data_view.browse('sec_filings', now=NOW, ticker='MU')
    assert sec['has_data'] is True and len(sec['sec_8k']) == 1
    assert sec['sec_8k'][0]['url'] == 'https://sec.example/old-8k'


def test_browse_marks_sec_8k_without_acceptance_as_unknown_filing_date(db):
    """无 accepted_at 的 sec_8k：发布时间为空，标注「申报日未知」，不用采集日冒充（I14）。"""
    _news(db, 'MU', 'sec_8k', 'https://sec.example/no-date', None,
          fetched_at=datetime(2026, 10, 22, 14, tzinfo=UTC))
    db.commit()

    sec = data_view.browse('sec_filings', now=NOW, ticker='MU')
    assert len(sec['sec_8k']) == 1
    assert sec['sec_8k'][0]['published_at'] is None
    assert sec['sec_8k'][0]['filing_date_note'] == '申报日未知'


@pytest.mark.parametrize('family', ['earnings', 'fundamentals', 'sec_text', 'sector'])
def test_registered_without_raw_browser_is_explicitly_rejected(family, monkeypatch):
    def unrelated(*args, **kwargs):
        pytest.fail('unsupported family must not read universe or open a transaction')
    monkeypatch.setattr(data_view, '_browse_universe', unrelated)
    monkeypatch.setattr(core_tx, 'read_only_tx', unrelated)
    with pytest.raises(ValueError, match=f'数据族暂不支持原始浏览：{family}'):
        data_view.browse(family, now=NOW, ticker='NVDA')


def test_ready_overview_uses_nine_real_adapters_and_new_latest_facts(db):
    from pathlib import Path
    from investment_assistant.data import registry
    db.execute(Path('migrations/014_us_research.sql').read_text())
    db.execute(Path('migrations/031_five_layers.sql').read_text())
    db.execute("INSERT INTO watchlist_items (ticker,status,tags) VALUES ('MU','active','{}')")
    data_repo.insert_sector_classification(db, dict(ticker='MU', sic='3571', sic_description='hardware',
        group='科技硬件', source='sec', fetched_at=NOW))
    db.commit()
    cards = data_view.overview(now=NOW)['families']
    assert [c['id'] for c in cards] == list(registry.ids())
    assert 'atr' not in {c['id'] for c in cards}
    sector = next(c for c in cards if c['id'] == 'sector')
    assert sector['latest_at'] == NOW and sector['coverage']['covered'] == 1
    assert sector['job'] is None  # No Task4 daily activation yet.
    assert data_view.family_detail('sector', now=NOW)['rows']
