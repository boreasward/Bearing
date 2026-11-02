"""Batch + read model against a disposable schema; all market data below is synthetic."""
from investment_assistant.data import families as data_families
import investment_assistant.review.weekly as review_weekly
import investment_assistant.review.shadow as review_shadow
import investment_assistant.analysis.agent.runner as analysis_agent_runner
import investment_assistant.analysis.units as analysis_units
from investment_assistant.execution import plan_context as execution_plan_context
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.core.schema as core_schema
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import investment_assistant.workflow.repo as workflow_repo
import json
import uuid

import legacy_decisions
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from investment_assistant.core import tx as core_tx

from investment_assistant.data.calendar import add_trading_days
from decimal import Decimal

import investment_assistant.workflow.batch as service
from investment_assistant.views.today import unit_cards, _agent_view
from investment_assistant.views.agent_runs import agent_runs, agent_run_detail
import investment_assistant.workflow.nodes.push as push_node
from investment_assistant.core.constants import AGENT_DEADLINE_S
from investment_assistant.tasks import ports
from investment_assistant.execution import account_records as account
from investment_assistant.execution import plan_levels as plans

SESSION = date(2026, 10, 22)                         # Thursday
NOW = datetime(2026, 10, 22, 22, 30, tzinfo=UTC)     # 18:30 New York


def test_breadth_tickers_union_and_fallback():
    from investment_assistant.analysis import pool as pool_module
    from investment_assistant.data.adapters import universe as universe_module
    manual = pool_module.Pool(core=('A', 'B'), extended=(), sectors={})
    combined = universe_module.Universe('test', SESSION, 'test', ('B', 'C'))
    assert analysis_units._breadth_tickers(manual, combined) == (['A', 'B', 'C'], [])
    assert analysis_units._breadth_tickers(manual, None) == (['A', 'B'], ['regime_universe_missing'])


def closes_for(ticker):
    if ticker == 'SPY':
        return [500.0] * 300
    base = [50 + i * 0.25 for i in range(296)]
    return base + [base[-1] - 6] * 3 + [base[-1] + 1]   # trend_reclaim on SESSION


def frame(ticker, end):
    closes = closes_for(ticker)
    days = [add_trading_days(SESSION, -(len(closes) - 1 - i)) for i in range(len(closes))]
    rows = [(d, c) for d, c in zip(days, closes) if d <= end]
    index = pd.DatetimeIndex([pd.Timestamp(d, tz='America/New_York') for d, _ in rows])
    return pd.DataFrame({'Open': [c for _, c in rows], 'High': [c * 1.02 for _, c in rows],
                         'Low': [c * 0.99 for _, c in rows], 'Close': [c for _, c in rows],
                         'Adj Close': [c for _, c in rows], 'Volume': [1000] * len(rows),
                         'Stock Splits': [0.0] * len(rows)}, index=index)


class Sources:
    def __init__(self, fail=(), fail_news=(), fail_earnings=()):
        self.fail = set(fail)
        self.fail_news = set(fail_news)
        self.fail_earnings = set(fail_earnings)
        self.calls = []

    def bars(self, ticker, start, end_exclusive):
        self.calls.append(('bars', ticker))
        if ticker in self.fail:
            raise RuntimeError('provider down')
        return frame(ticker, end_exclusive - timedelta(days=1))

    def news(self, ticker):
        if ticker in self.fail_news:
            raise RuntimeError('news down')
        return [{'content': {'title': f'{ticker} headline', 'pubDate': '2026-10-22T12:00:00Z',
                             'canonicalUrl': {'url': f'https://news.example.com/{ticker}'}}}]

    def earnings(self, ticker, **_):
        if ticker in self.fail_earnings:
            raise RuntimeError('earnings down')
        return {'Earnings Date': [date(2027, 2, 18)]}

    def discover(self, tickers, start, end):
        return [{'ticker': 'NVDA', 'form': '8-K', 'filing_date': '2026-10-22', 'items': ['8.01'],
                 'accepted_at': '2026-10-22T08:00:00-04:00', 'edgar_url': 'https://www.sec.gov/Archives/x.htm'}]


@pytest.fixture
def db(workbench_db, monkeypatch):
    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(core_tx, 'connection', connection)
    # The plan review's run lock opens a dedicated non-pooled connection of its own, as in production;
    # here it points at the explicit test database (advisory locks only, no table access).
    import os

    import psycopg

    import investment_assistant.workflow.nodes.review as plan_review
    url = os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']
    monkeypatch.setattr(analysis_agent_runner, '_lock_connection', lambda: psycopg.connect(url, autocommit=True))
    return workbench_db


def deps(sources, earnings=None):
    return service.Deps(bar_fetcher=sources.bars, news_fetcher=sources.news, filing_source=sources,
                        earnings_fetcher=earnings or sources.earnings)


def test_batch_collects_evaluates_and_briefs(db):
    summary = service.run_batch(tickers=['NVDA', 'MU'], now=NOW, deps=deps(Sources()))
    assert summary['session'] == SESSION.isoformat()
    nvda = summary['tickers']['NVDA']
    assert nvda['bars']['inserted'] > 0 and nvda['error'] is None
    assert set(nvda['candidates']) >= {'trend_reclaim', 'new_filing'}
    assert summary['briefs'] == {'NVDA': 'fallback', 'MU': 'fallback'}
    assert summary['benchmark']['error'] is None
    stored = db.execute('SELECT count(*) FROM news_items WHERE source = %s', ['sec_8k']).fetchone()
    assert stored == (1,)


def test_batch_rerun_is_idempotent(db):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()))
    again = service.run_batch(tickers=['NVDA'], now=NOW + timedelta(minutes=5), deps=deps(Sources()))
    assert again['tickers']['NVDA']['bars']['inserted'] == 0
    assert again['conflicts'] == []
    assert db.execute('SELECT count(*) FROM signal_candidates').fetchone()[0] == len(again['tickers']['NVDA']['candidates'])


def test_one_ticker_failure_does_not_block_others(db):
    summary = service.run_batch(tickers=['NVDA', 'MU'], now=NOW, deps=deps(Sources(fail={'MU'})))
    assert 'provider down' in summary['tickers']['MU']['error']
    assert summary['tickers']['NVDA']['error'] is None
    assert any('MU' in e for e in summary['errors'])


def test_too_many_tickers_is_an_error_not_a_truncation(db):
    with pytest.raises(ValueError):
        service.run_batch(tickers=[f'T{i}' for i in range(101)], now=NOW, deps=deps(Sources()))


def test_scheduled_run_skips_when_new_york_day_was_not_a_session(db):
    saturday = datetime(2026, 10, 24, 22, 30, tzinfo=UTC)
    sources = Sources()
    out = service.run_batch(tickers=['NVDA'], now=saturday, deps=deps(sources), skip_non_trading=True)
    assert out == {'status': 'skipped', 'reason': 'not a trading day in New York', 'ny_date': '2026-10-24'}
    assert sources.calls == []


def test_today_lists_open_units_with_brief_and_context(db):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()))
    view = unit_cards(now=NOW)
    unit = view['units'][0]
    assert unit['ticker'] == 'NVDA' and unit['signal_date'] == SESSION
    assert unit['proposed_action'] == 'buy' and unit['entry_passed'] is False
    assert unit['brief']['status'] == 'fallback'
    assert {c['rule_id'] for c in unit['candidates']} >= {'trend_reclaim', 'new_filing'}
    assert unit['news'][0]['source'] == 'sec_8k' or unit['news'][0]['source'] == 'yfinance'


def test_decide_then_unit_leaves_today(db):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()))
    unit = unit_cards(now=NOW)['units'][0]
    out = legacy_decisions.decide({'request_id': '6f1d1c1e-1111-4444-8888-000000000001', 'ticker': 'NVDA',
                          'signal_date': SESSION.isoformat(), 'candidate_ids': [str(c['id']) for c in unit['candidates']],
                          'brief_id': str(unit['brief']['id']), 'proposed_action': 'buy', 'final_action': 'buy',
                          'decision': 'accept', 'reason': '合成样例：趋势修复',
                          'plan_id': str(unit['plan']['id'])}, now=NOW)
    assert out['status'] == 'created'
    assert unit_cards(now=NOW)['units'] == []


def test_unavailable_database_is_reported(monkeypatch):
    monkeypatch.delenv('INVESTMENT_ASSISTANT_DATABASE_URL', raising=False)
    with pytest.raises(service.Unavailable):
        unit_cards(now=NOW)


def test_missing_schema_is_unavailable_not_empty(workbench_db, monkeypatch):
    workbench_db.execute('DROP TABLE signal_candidates CASCADE')

    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(core_tx, 'connection', connection)
    with pytest.raises(service.Unavailable):
        unit_cards(now=NOW)


def test_sec_listing_fills_unknown_filing_acceptance_without_overwriting(db):
    insert = ("INSERT INTO filings (ticker, form, filing_date, accession_number, primary_document, file_path, "
              "accepted_at, items) VALUES ('NVDA', '8-K', '2026-10-21', %s, 'k.htm', '/x', %s, %s)")
    db.execute(insert, ['0001045810-26-000001', None, None])
    known = datetime(2026, 10, 20, 12, tzinfo=UTC)
    db.execute(insert, ['0001045810-26-000002', known, ['8.01']])

    class Listing(Sources):
        def discover(self, tickers, start, end):
            return [{'ticker': 'NVDA', 'form': '8-K', 'filing_date': '2026-10-21', 'accession_number': acc,
                     'items': ['2.02', '9.01'], 'accepted_at': '2026-10-22T00:41:42+00:00',
                     'edgar_url': f'https://www.sec.gov/Archives/{acc}.htm'}
                    for acc in ('0001045810-26-000001', '0001045810-26-000002')]
    summary = service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Listing()))
    assert summary['filings']['acceptance_filled'] == 1
    rows = {acc: (accepted, items) for acc, accepted, items in
            db.execute('SELECT accession_number, accepted_at, items FROM filings').fetchall()}
    assert rows['0001045810-26-000001'] == (datetime(2026, 10, 22, 0, 41, 42, tzinfo=UTC), ['2.02', '9.01'])
    assert rows['0001045810-26-000002'] == (known, ['8.01'])


def breakdown_closes():
    base = [50 + i * 0.25 for i in range(299)]
    return base + [110.0]   # 10+ days above MA50, then a break below it: trend_break


class BreakdownSources(Sources):
    def bars(self, ticker, start, end_exclusive):
        if ticker != 'AMD' and ticker != 'MU':
            return super().bars(ticker, start, end_exclusive)
        closes = breakdown_closes() if ticker == 'AMD' else closes_for(ticker)
        days = [add_trading_days(SESSION, -(len(closes) - 1 - i)) for i in range(len(closes))]
        rows = [(d, c) for d, c in zip(days, closes) if d <= end_exclusive - timedelta(days=1)]
        index = pd.DatetimeIndex([pd.Timestamp(d, tz='America/New_York') for d, _ in rows])
        return pd.DataFrame({'Open': [c for _, c in rows], 'High': [c * 1.02 for _, c in rows],
                             'Low': [c * 0.99 for _, c in rows], 'Close': [c for _, c in rows],
                             'Adj Close': [c for _, c in rows], 'Volume': [1000] * len(rows),
                             'Stock Splits': [0.0] * len(rows)}, index=index)


def test_batch_writes_rule_plan_for_buy_unit(db):
    summary = service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()))
    assert summary['plans']['NVDA'] == 'inserted'
    unit = unit_cards(now=NOW)['units'][0]
    plan = unit['plan']
    assert plan['kind'] == 'rule' and plan['plan_version'] == 'plan-2' and plan['status'] == 'ok'
    assert plan['sizing']['status'] == 'no_equity'
    assert plan['sizing']['equity_source'] == 'snapshot'
    bars = data_repo.read_bars(db, 'NVDA', SESSION, cutoff_at=NOW)
    levels = plans.rule_levels(Decimal(str(bars[-1]['close'])), plans.atr14(bars))
    for key, value in levels.items():
        assert plan[key] == value
    assert plan['inputs']['bar_ids'] == [b['id'] for b in bars[-15:]]
    assert plan['inputs']['earnings_date_id'] is not None


def test_no_plan_for_conflict_or_sell_units(db):
    # 冲突单元：趋势修复（buy）+ 持仓回撤（sell）
    legacy_decisions.add_trade({'request_id': '6f1d1c1e-2222-4444-8888-000000000001', 'ticker': 'NVDA', 'side': 'buy',
                       'quantity': '10', 'price': '137.00', 'executed_at': '2026-10-10T15:00:00+00:00'}, now=NOW)
    conflict = service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()))
    assert 'plans' not in conflict or 'NVDA' not in conflict['plans']
    # 卖出单元：趋势跌破且无持仓
    sell = service.run_batch(tickers=['AMD'], now=NOW, deps=deps(BreakdownSources()))
    assert 'AMD' not in sell['plans']
    assert db.execute('SELECT count(*) FROM trade_plans').fetchone()[0] == 0


def test_rerun_returns_existing_plan(db):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()))
    again = service.run_batch(tickers=['NVDA'], now=NOW + timedelta(minutes=5), deps=deps(Sources()))
    assert again['plans']['NVDA'] == 'existing'
    assert db.execute('SELECT count(*) FROM trade_plans').fetchone()[0] == 1


def test_changed_inputs_record_conflict_without_overwrite(db):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()))
    original = unit_cards(now=NOW)['units'][0]['plan']

    class Shifted(Sources):
        def bars(self, ticker, start, end_exclusive):
            f = super().bars(ticker, start, end_exclusive)
            f.iloc[-1, f.columns.get_loc('Close')] += 2
            f.iloc[-1, f.columns.get_loc('Adj Close')] += 2
            return f

    again = service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Shifted()))
    assert again['plans']['NVDA'] == 'conflict'
    assert any(c.get('rule_id') == 'rule_plan' for c in again['conflicts'])
    assert db.execute('SELECT count(*) FROM trade_plans').fetchone()[0] == 1
    assert unit_cards(now=NOW)['units'][0]['plan']['ref_price'] == original['ref_price']


def test_backdated_snapshot_recorded_after_cutoff_ignored(db):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()))
    plan = unit_cards(now=NOW)['units'][0]['plan']
    assert plan['sizing']['status'] == 'no_equity'
    # 补录一笔 as_of 在信号日之前、recorded_at 晚于本次运行截止时间的快照
    account.record_snapshot(db, {'request_id': '6f1d1c1e-3333-4444-8888-000000000001',
                                 'as_of': (SESSION - timedelta(days=2)).isoformat(),
                                 'equity': '100000'}, now=NOW + timedelta(hours=1))
    again = service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()))
    assert again['plans']['NVDA'] == 'existing'  # 补录不可见，重算结果与原计划一致


def test_earnings_fetch_failure_not_stored(db):
    def boom(_t):
        raise RuntimeError('calendar down')

    summary = service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources(), earnings=boom))
    assert summary['earnings']['NVDA'].startswith('error:')
    assert db.execute('SELECT count(*) FROM earnings_dates').fetchone()[0] == 0
    plan = unit_cards(now=NOW)['units'][0]['plan']
    assert 'earnings_unknown' in plan['warnings']


def test_holding_outside_watchlist_is_collected(db):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()))
    legacy_decisions.add_trade({'request_id': '6f1d1c1e-4444-4444-8888-000000000001', 'ticker': 'AMD', 'side': 'buy',
                       'quantity': '10', 'price': '100.00', 'executed_at': '2026-10-20T15:00:00+00:00'}, now=NOW)
    summary = service.run_batch(tickers=['NVDA'], now=NOW + timedelta(minutes=10), deps=deps(Sources()))
    assert 'AMD' in summary['tickers']
    assert 'AMD' in summary['earnings']


def test_today_includes_holdings(db):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()))
    legacy_decisions.add_trade({'request_id': '6f1d1c1e-5555-4444-8888-000000000001', 'ticker': 'NVDA', 'side': 'buy',
                       'quantity': '10', 'price': '110.00', 'executed_at': '2026-10-22T20:00:00+00:00'}, now=NOW)
    view = unit_cards(now=NOW)
    holding = view['holdings'][0]
    assert holding['ticker'] == 'NVDA'
    assert holding['plan_id'] is None and holding['stop'] is None and holding['t1_reached'] is False
    assert holding['last_close'] > 0
    assert holding['next_earnings'] == '2027-02-18'


def test_plan_shadow_view(db):
    from investment_assistant.execution import simulation as shadow_module
    service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()))
    view = review_shadow.plan_shadow(now=NOW)
    assert view['calc_version'] == shadow_module.SHADOW_VERSION
    assert len(view['plans']) == 1
    plan = view['plans'][0]
    assert plan['ticker'] == 'NVDA' and plan['outcome']['status'] in ('pending', 'open', 'expired')


def test_discipline_since_filters_trades(db):
    legacy_decisions.add_trade({'request_id': '6f1d1c1e-8888-4444-8888-000000000001', 'ticker': 'NVDA', 'side': 'buy',
                       'quantity': '10', 'price': '100.00',
                       'executed_at': '2026-06-15T15:00:00+00:00'}, now=NOW)
    legacy_decisions.add_trade({'request_id': '6f1d1c1e-9999-4444-8888-000000000002', 'ticker': 'AMD', 'side': 'buy',
                       'quantity': '10', 'price': '100.00',
                       'executed_at': '2026-10-20T15:00:00+00:00'}, now=NOW)
    view = review_weekly.discipline(now=NOW, since=date(2026, 10, 1))
    assert [t['ticker'] for t in view['trades']] == ['AMD']


class PushClient:
    def __init__(self):
        self.sent = []

    def send(self, channel, payload):
        self.sent.append(payload)


def test_push_once_per_session_and_force(db):
    from investment_assistant.config import NotifyConfig

    cfg = NotifyConfig()
    out = push_node.push(now=NOW, force=False, notify_cfg=cfg, send=ports.send, client=PushClient())
    assert out['sent'] is True and out['messages'] >= 1 and out['session'] == SESSION.isoformat()
    db.execute("INSERT INTO job_reports (task, run_id, status, started_at, finished_at, summary) "
               "VALUES ('workbench', 'r1', 'success', %s, %s, %s::jsonb)",
               [NOW, NOW, json.dumps({'push': out})])
    again = push_node.push(now=NOW, force=False, notify_cfg=cfg, send=ports.send, client=PushClient())
    assert again['sent'] is False and again['reason'] == 'already_pushed'
    forced = push_node.push(now=NOW, force=True, notify_cfg=cfg, send=ports.send, client=PushClient())
    assert forced['sent'] is True


def test_push_failure_recorded_not_raised(db):
    from investment_assistant.config import NotifyConfig

    class Boom:
        def send(self, channel, payload):
            raise RuntimeError('discord down')

    out = push_node.push(now=NOW, force=True, notify_cfg=NotifyConfig(), send=ports.send, client=Boom())
    assert out['sent'] is False and out['reason'].startswith('send_failed')


def seed_watchlist(db, rows):
    for ticker, tags in rows:
        db.execute("INSERT INTO watchlist_items (ticker, status, tags) VALUES (%s, 'active', %s)", [ticker, tags])


def _task_run(trigger, moment=NOW, task_deps=None):
    from investment_assistant.config import AssistantConfig
    import _v27_task as workbench_task

    return workbench_task.run(config=AssistantConfig(), trigger_source=trigger, now=moment,
                              deps=task_deps or deps(Sources()))


def test_scheduled_run_pushes_once_per_session(db, monkeypatch):
    seed_watchlist(db, [('NVDA', ['core'])])
    calls = []

    def fake_push(*, now, force, summary=None, notify_cfg=None, client=None, send):
        calls.append(force)
        return {'session': SESSION.isoformat(), 'sent': True, 'messages': 4}

    monkeypatch.setattr(push_node, 'push', fake_push)
    result = _task_run('scheduled')
    assert calls == [False]
    assert result['summary']['push'] == {'session': SESSION.isoformat(), 'sent': True, 'messages': 4}


def test_manual_run_does_not_push(db, monkeypatch):
    seed_watchlist(db, [('NVDA', ['core'])])

    def boom(**_):
        raise AssertionError('manual run must not push')

    monkeypatch.setattr(push_node, 'push', boom)
    result = _task_run('manual')
    assert 'push' not in result['summary']


def test_closed_day_pushes_closed_message(db, monkeypatch):
    closed = []

    def fake_closed(*, now, notify_cfg=None, client=None, send):
        closed.append(now)
        return {'session': '2026-10-24', 'sent': True, 'messages': 1}

    monkeypatch.setattr(push_node, 'push_closed', fake_closed)
    result = _task_run('scheduled', moment=datetime(2026, 10, 24, 22, 30, tzinfo=UTC))
    assert closed and result['summary']['status'] == 'skipped'
    assert result['summary']['push']['sent'] is True


def test_task_run_ids_reach_four_tables(db, monkeypatch):
    """v2.4 §6.1: 任务外壳的 run_id 传到批处理，四张表的新行同源（job_reports 为准）。"""
    from contextlib import contextmanager

    @contextmanager
    def wired(url):
        yield db

    monkeypatch.setattr('investment_assistant.db.connect', wired)
    monkeypatch.setenv('INVESTMENT_ASSISTANT_DATABASE_URL', 'postgresql://wired-to-test-schema')
    seed_watchlist(db, [('NVDA', ['core'])])

    _task_run('manual')

    run_id = db.execute("SELECT run_id FROM job_reports WHERE task = 'workbench' "
                        'ORDER BY id DESC LIMIT 1').fetchone()[0]
    assert db.execute('SELECT DISTINCT run_id FROM data_fetch_runs').fetchall() == [(run_id,)]
    assert db.execute('SELECT DISTINCT run_id FROM screen_runs').fetchall() == [(run_id,)]
    assert db.execute('SELECT DISTINCT run_id FROM signal_candidates '
                      'WHERE signal_date = %s', [SESSION]).fetchall() == [(run_id,)]


# --- P2: pool, ETF/FRED collection, regime snapshot, extended units ---

def recording_deps(sources, monkeypatch):
    """Deps plus a recorder of every brief write (which tickers get a brief)."""
    calls = []
    real = analysis_repo.store_brief

    def recording(conn, brief):
        calls.append(brief['ticker'])
        return real(conn, brief)
    monkeypatch.setattr(analysis_repo, 'store_brief', recording)
    return deps(sources), calls


def test_batch_reads_pool_from_watchlist(db):
    seed_watchlist(db, [('AAA', ['core']), ('BBB', ['core', 'sector:SMH']), ('CCC', []), ('DDD', []), ('EEE', [])])
    summary = service.run_batch(now=NOW, deps=deps(Sources()))
    assert summary['pool']['core'] == 2 and summary['pool']['extended'] == 3 and summary['pool']['holdings'] == 0
    assert len(summary['pool']['changes']) == 5
    assert db.execute('SELECT count(*) FROM pool_tiers').fetchone()[0] == 5


def test_rerun_appends_no_tier_changes(db):
    seed_watchlist(db, [('AAA', ['core']), ('CCC', [])])
    service.run_batch(now=NOW, deps=deps(Sources()))
    again = service.run_batch(now=NOW + timedelta(minutes=5), deps=deps(Sources()))
    assert again['pool']['changes'] == []
    assert db.execute('SELECT count(*) FROM pool_tiers').fetchone()[0] == 2


def test_pool_over_limit_fails_batch(db):
    seed_watchlist(db, [(f'T{i:03d}', []) for i in range(101)])
    sources = Sources()
    with pytest.raises(ValueError, match='100'):
        service.run_batch(now=NOW, deps=deps(sources))
    assert sources.calls == []


def test_core_empty_runs_and_reports(db):
    seed_watchlist(db, [('CCC', []), ('DDD', [])])
    summary = service.run_batch(now=NOW, deps=deps(Sources()))
    assert summary['pool']['core'] == 0
    assert 'core_empty' in summary['errors']
    assert set(summary['tickers']) == {'CCC', 'DDD'}


def test_etf_bars_collected_and_isolated(db):
    class FailingXLK(Sources):
        def bars(self, ticker, start, end_exclusive):
            if ticker == 'XLK':
                raise RuntimeError('etf down')
            return super().bars(ticker, start, end_exclusive)
    summary = service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(FailingXLK()))
    assert summary['etfs']['XLK'].startswith('error:')
    assert summary['etfs']['SMH'].startswith('inserted ')
    assert summary['sources']['etf_bars'] == ['XLK']
    assert summary['tickers']['NVDA']['error'] is None


def test_fred_not_configured(db, monkeypatch):
    monkeypatch.delenv('FRED_API_KEY', raising=False)
    called = []

    def macro_fetcher(url, *, params):
        called.append(params)
        return ({'observations': []}, {'ok': True})
    summary = service.run_batch(tickers=['NVDA'], now=NOW,
                                deps=service.Deps(bar_fetcher=Sources().bars, news_fetcher=Sources().news,
                                                  filing_source=Sources(),
                                                  earnings_fetcher=Sources().earnings, macro_fetcher=macro_fetcher))
    assert summary['macro'] == {'BAMLH0A0HYM2': 'not_configured', 'T10Y2Y': 'not_configured'}
    assert summary['sources']['fred'] == []
    assert called == []
    inputs = db.execute('SELECT inputs FROM market_regimes').fetchone()[0]
    assert inputs['macro_status'] == {'BAMLH0A0HYM2': 'not_configured', 'T10Y2Y': 'not_configured'}


def test_fred_error_in_sources(db, monkeypatch):
    monkeypatch.setenv('FRED_API_KEY', 'SECRET123')

    def macro_fetcher(url, *, params):
        raise RuntimeError(f'FRED down api_key=SECRET123 url={url}')
    summary = service.run_batch(tickers=['NVDA'], now=NOW,
                                deps=service.Deps(bar_fetcher=Sources().bars, news_fetcher=Sources().news,
                                                  filing_source=Sources(),
                                                  earnings_fetcher=Sources().earnings, macro_fetcher=macro_fetcher))
    assert sorted(summary['sources']['fred']) == ['BAMLH0A0HYM2', 'T10Y2Y']
    assert 'SECRET123' not in json.dumps(summary)
    assert all(v.startswith('error:') for v in summary['macro'].values())
    inputs = db.execute('SELECT inputs FROM market_regimes').fetchone()[0]
    assert inputs['macro_status'] == {'BAMLH0A0HYM2': 'error', 'T10Y2Y': 'error'}


def test_regime_snapshot_written(db):
    seed_watchlist(db, [(f'T{i:02d}', []) for i in range(25)])
    summary = service.run_batch(now=NOW, deps=deps(Sources()))
    assert db.execute('SELECT count(*) FROM market_regimes').fetchone()[0] == 1
    assert summary['regime']['label'] != 'insufficient'
    assert summary['regime']['status'] == 'inserted'
    assert summary['sources']['regime'] == []


def test_extended_unit_has_plan_but_no_brief(db, monkeypatch):
    seed_watchlist(db, [('AAA', ['core']), ('NVDA', [])])   # NVDA extended, triggers trend_reclaim
    d, calls = recording_deps(Sources(), monkeypatch)
    summary = service.run_batch(now=NOW, deps=d)
    assert summary['plans']['NVDA'] == 'inserted'
    assert 'NVDA' not in summary['briefs']
    assert 'NVDA' not in calls
    assert 'AAA' in calls  # core ticker still gets its model brief


def test_held_extended_ticker_gets_brief(db, monkeypatch):
    from investment_assistant.execution import ledger
    seed_watchlist(db, [('AAA', ['core']), ('NVDA', [])])
    ledger.record_trade(db, {'request_id': str(uuid.uuid4()), 'ticker': 'NVDA', 'side': 'buy', 'quantity': '10',
                             'price': '101.5', 'fee': '1', 'executed_at': '2026-10-10T14:00:00+00:00'},
                        now=datetime(2026, 10, 10, 15, tzinfo=UTC))
    d, calls = recording_deps(Sources(), monkeypatch)
    summary = service.run_batch(now=NOW, deps=d)
    assert summary['plans']['NVDA'] == 'inserted'
    assert summary['briefs']['NVDA'] == 'fallback'
    assert 'NVDA' in calls


def test_sources_distinguish_failures(db):
    seed_watchlist(db, [('AAA', []), ('BBB', []), ('CCC', [])])
    summary = service.run_batch(now=NOW, deps=deps(Sources(fail={'AAA'}, fail_news={'BBB'}, fail_earnings={'CCC'})))
    assert summary['sources']['bars'] == ['AAA']
    assert summary['sources']['news'] == ['BBB']
    assert summary['sources']['earnings'] == ['CCC']
    assert summary['sources']['sec'] == []
    assert summary['sources']['etf_bars'] == []
    assert summary['sources']['fred'] == []


def test_100_tickers_complete_with_timings(db):
    seed_watchlist(db, [(f'T{i:03d}', []) for i in range(100)])
    summary = service.run_batch(now=NOW, deps=deps(Sources()))
    assert len(summary['tickers']) == 100
    assert all(isinstance(v, (int, float)) for v in summary['timings'].values())
    assert set(summary['timings']) == {'collect', 'regime', 'rules', 'agent', 'total'}


@pytest.mark.parametrize('with_snapshot', [False, True])
def test_rerun_with_context_returns_existing_p1_plan(db, with_snapshot):
    from investment_assistant.config import load_config
    seed_watchlist(db, [('NVDA', ['core'])])
    if with_snapshot:
        account.record_snapshot(db, {'request_id': str(uuid.uuid4()), 'as_of': SESSION.isoformat(),
            'equity': '100000'}, now=NOW)
    service.run_batch(now=NOW, deps=deps(Sources()))  # stores bars and candidates
    plan_id = db.execute("SELECT id FROM trade_plans WHERE kind = 'rule' AND ticker = 'NVDA'").fetchone()[0]
    db.execute('DELETE FROM trade_plans WHERE id = %s', [plan_id])
    with core_tx._tx() as conn:
        p1 = plans.build_rule_plan(
            ticker='NVDA', signal_date=SESSION, bars=data_repo.read_bars(conn, 'NVDA', SESSION, cutoff_at=NOW),
            earnings_row=data_repo.latest_earnings(conn, 'NVDA', cutoff_at=NOW),
            snapshot=execution_repo.snapshot_at(conn, SESSION, cutoff_at=NOW),
            exposure=execution_plan_context.exposure(conn, SESSION, cutoff_at=NOW, ticker='NVDA'),
            params=load_config().workbench, version=plans.PLAN_V2)       # v2.9: the baseline is plan-2
        saved = execution_repo.store_plan(conn, p1)
        assert saved['status'] == 'inserted'
        frozen = saved['record']
        assert 'context' not in p1['inputs']
        assert 'equity_source' not in p1['sizing']
    again = service.run_batch(now=NOW + timedelta(minutes=5), deps=deps(Sources()))
    assert again['plans']['NVDA'] == 'existing'
    assert again['conflicts'] == []
    assert execution_repo.plan_by_id(db, frozen['id']) == frozen


@pytest.mark.parametrize('change, expected', [
    ({'equity_source': 'snapshot'}, 'existing'),
    ({'equity_source': 'ledger'}, 'conflict'),
    ({'equity_source': None}, 'conflict'),
    ({'equity_source': 'snapshot', 'snapshot_id': 'other-snapshot'}, 'conflict'),
    ({'equity_source': 'snapshot', 'equity': Decimal(200000)}, 'conflict'),
    ({'equity_source': 'snapshot', 'shares': 99}, 'conflict'),
])
def test_legacy_plan_source_default_preserves_real_sizing_conflicts(monkeypatch, change, expected):
    from copy import deepcopy
    stored = {'id': uuid.uuid4(), 'ticker': 'NVDA', 'signal_date': SESSION, 'kind': 'rule',
        'plan_version': plans.PLAN_V2, 'status': 'ok', 'ref_price': Decimal(100), 'atr': Decimal(2),
        'entry_low': Decimal(98), 'entry_high': Decimal(102), 'stop': Decimal(94), 'target1': Decimal(112),
        'entry_type': 'zone_limit', 'max_hold_sessions': 10,
        'sizing': {'snapshot_id': 'frozen-snapshot', 'equity': Decimal(100000), 'shares': 10}}
    frozen = deepcopy(stored)
    rebuilt = {**deepcopy(stored), 'sizing': {**stored['sizing'], **change}}
    intended = deepcopy(rebuilt)
    monkeypatch.setattr(execution_repo, 'rule_plan', lambda *args, **kwargs: stored)
    monkeypatch.setattr(execution_repo, 'insert_plan', lambda *args, **kwargs: pytest.fail('existing plan rewritten'))
    result = execution_repo.store_plan(None, rebuilt)
    assert result == {'status': expected, 'record': frozen}
    assert stored == frozen and rebuilt == intended


# --- P2: today split, market block ---

def test_today_splits_extended_units(db):
    seed_watchlist(db, [('AAA', ['core']), ('NVDA', [])])
    service.run_batch(now=NOW, deps=deps(Sources()))
    view = unit_cards(now=NOW)
    assert [u['ticker'] for u in view['units']] == ['AAA']
    assert [u['ticker'] for u in view['extended_units']] == ['NVDA']
    ext = view['extended_units'][0]
    assert set(ext) >= {'ticker', 'signal_date', 'proposed_action', 'rule_ids', 'plan'}
    assert ext['proposed_action'] == 'buy' and ext['plan'] is not None


def test_held_extended_stays_in_main_list(db):
    from investment_assistant.execution import ledger
    seed_watchlist(db, [('AAA', ['core']), ('NVDA', [])])
    ledger.record_trade(db, {'request_id': str(uuid.uuid4()), 'ticker': 'NVDA', 'side': 'buy', 'quantity': '10',
                             'price': '101.5', 'fee': '1', 'executed_at': '2026-10-10T14:00:00+00:00'},
                        now=datetime(2026, 10, 10, 15, tzinfo=UTC))
    service.run_batch(now=NOW, deps=deps(Sources()))
    view = unit_cards(now=NOW)
    assert 'NVDA' in [u['ticker'] for u in view['units']]
    assert 'NVDA' not in [u['ticker'] for u in view['extended_units']]


def test_today_market_block(db):
    seed_watchlist(db, [(f'T{i:02d}', []) for i in range(25)])
    service.run_batch(now=NOW, deps=deps(Sources()))
    view = unit_cards(now=NOW)
    assert view['market']['session'] == SESSION
    assert view['market']['calc_version'] == 'regime-2'
    assert view['market']['label'] in ('risk_on', 'neutral', 'risk_off', 'insufficient')


def test_manual_tickers_override_does_not_rewrite_tiers(db):
    # Repository consumers use their owning layers below.
    seed_watchlist(db, [('AAA', ['core']), ('BBB', [])])
    service.run_batch(now=NOW, deps=deps(Sources()))
    service.run_batch(tickers=['NVDA'], now=NOW + timedelta(minutes=5), deps=deps(Sources()))
    assert db.execute("SELECT count(*) FROM pool_tiers WHERE tier = 'removed'").fetchone()[0] == 0
    assert analysis_repo.pool_latest(db) == {'AAA': {'tier': 'core', 'sector_etf': None,
                                                    'source': 'watchlist'},
                                          'BBB': {'tier': 'extended', 'sector_etf': None,
                                                  'source': 'watchlist'}}


# ---------------------------------------------------------------- agent stage (P3 §8)

from contextlib import nullcontext                                              # noqa: E402
from langchain_core.language_models import BaseChatModel                        # noqa: E402
from langchain_core.messages import AIMessage                                   # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver                           # noqa: E402


def _agent_settings(**overrides):
    from investment_assistant.core.llm import model as agent_model_module
    values = {'provider': 'chatgpt', 'model': 'gpt-test',
              'api_key': 'sk-test', 'daily_tokens': None, 'config_error': None}
    values.update(overrides)
    return agent_model_module.AgentSettings(**values)


def agent_deps(make, responses, *, settings=None, **overrides):
    d = deps(Sources())
    d.agent_model = make(responses)
    d.agent_checkpointer = lambda: nullcontext(InMemorySaver())
    d.agent_settings = settings or _agent_settings()
    for key, value in overrides.items():
        setattr(d, key, value)
    return d


def _tool_msg(name, args):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': f'{name}-1'}])


def _agent_buy(ref='call:1'):
    return json.dumps({'action': 'buy', 'summary': '回踩修复放量',
                       'entry_type': 'zone_limit', 'max_hold_sessions': 10,
                       'levels': {'entry_low': {'value': 98.5, 'refs': [ref]},
                                  'entry_high': {'value': 101.5, 'refs': [ref]},
                                  'stop': {'value': 96.0, 'refs': [ref]},
                                  'target1': {'value': 108.0, 'refs': [ref]}},
                       'thesis': [{'text': '突破 20 日线', 'refs': [ref]}],
                       'invalidation': [{'kind': 'close_below', 'params': {'price': 94}, 'text': '收盘跌破 94',
                                         'refs': [ref]}]}, ensure_ascii=False)


def _research_ok(ref='call:1'):
    """A checked researcher conclusion (§6.1) citing the researcher's own call."""
    return AIMessage(content=json.dumps({'facts': [{'text': '财报披露营收增长', 'kind': 'disclosure', 'refs': [ref]}],
                                         'risks': [], 'blocked': [], 'summary': '基本面已核验'}, ensure_ascii=False))


def _agent4_buy(research_ref='call:1', price_ref='call:2'):
    """``_agent_buy`` for the agent-4 flow: levels cite the plan's own price tool, the thesis also cites
    the researcher's call (§6.1's first-version citation rule)."""
    plan = json.loads(_agent_buy(price_ref))
    plan['thesis'][0]['refs'] = [research_ref, price_ref]
    return json.dumps(plan, ensure_ascii=False)


def _ok_responses(ticker='NVDA', ref='call:1'):
    """An agent-4 first version (v2.13 §6.1): researcher tool + conclusion, propose tool + plan,
    counter tool + reply. The researcher's call is ``call:1``, the plan's own tool ``call:2``
    and the counter's ``call:3``; ``ref`` is kept for the callers that read it back."""
    return [_tool_msg('get_news', {'days': 30}), _research_ok(),
            _tool_msg('get_indicators', {'ticker': ticker}), AIMessage(content=_agent4_buy()),
            _tool_msg('get_news', {'days': 7}), AIMessage(content=json.dumps(
                {'counter': [{'thesis': 1, 'text': '财报临近', 'refs': [ref]}], 'risks': []},
                ensure_ascii=False))]


def test_agent_stage_after_rule_plans(db, scripted_model):
    summary = service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, []))
    assert summary['plans']['NVDA'] == 'inserted'          # 规则计划先入库，不受 Agent 影响
    runs = analysis_repo.agent_runs_for(db, 'NVDA', SESSION)
    assert [(r['status'], r['reason']) for r in runs] == [('error', 'model_error')]
    assert summary['agent']['runs'] == {'NVDA': 'error: model_error'}


def test_agent_order_main_before_extended(db, scripted_model):
    from investment_assistant.analysis import pool as pool_module
    for ticker in ('CCC', 'DDD'):   # 分层记录：扩展池；DDD 另有持仓 → 主列表
        db.execute("INSERT INTO pool_tiers (ticker, tier, effective_session, recorded_at) "
                   "VALUES (%s, 'extended', %s, %s)", [ticker, SESSION - timedelta(days=1), NOW])
    legacy_decisions.add_trade({'request_id': str(uuid.uuid4()), 'ticker': 'DDD', 'side': 'buy', 'quantity': '10',
                       'price': '100.00', 'executed_at': '2026-09-20T15:00:00+00:00'}, now=NOW)
    bad = json.dumps({'action': 'buy', 'summary': 'x',
                      'levels': {k: {'value': v, 'refs': ['call:99']} for k, v in
                                 (('entry_low', 98.5), ('entry_high', 101.5), ('stop', 96.0), ('target1', 108.0))},
                      'thesis': [{'text': 't', 'refs': ['call:99']}]})
    unit_replies = [_tool_msg('get_indicators', {'ticker': 'X'}), AIMessage(content=bad)]
    summary = service.run_batch(pool=pool_module.Pool(core=('AAA', 'BBB'), extended=('CCC', 'DDD')),
                                now=NOW, deps=agent_deps(scripted_model, unit_replies * 4))
    assert list(summary['agent']['runs']) == ['AAA', 'BBB', 'DDD', 'CCC']   # 核心与持仓字母序，再扩展


def test_agent_ok_writes_plan_run_calls(db, scripted_model):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses()))
    plan = execution_repo.agent_plan(db, 'NVDA', SESSION)
    assert plan['kind'] == 'agent' and plan['plan_version'] == 'agent-3'
    run = analysis_repo.latest_agent_run(db, 'NVDA', SESSION)
    assert run['status'] == 'ok' and str(run['plan_id']) == str(plan['id'])
    assert run['agent_version'] == 'agent-4'
    [version] = execution_repo.chain_versions(db, 'NVDA', SESSION)
    assert version['version_no'] == 0 and str(version['agent_run_id']) == str(run['id'])
    calls = analysis_repo.agent_tool_calls(db, run['id'])
    assert [(c['seq'], c['phase'], c['tool']) for c in calls] == \
        [(1, 'research', 'get_news'), (2, 'propose', 'get_indicators'), (3, 'counter', 'get_news')]


def test_existing_ok_or_pass_not_rerun(db, scripted_model):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses()))
    again = service.run_batch(tickers=['NVDA'], now=NOW + timedelta(minutes=5),
                              deps=agent_deps(scripted_model, []))     # 无回复可用：重跑即抛错
    assert len(analysis_repo.agent_runs_for(db, 'NVDA', SESSION)) == 1
    assert again['agent']['runs'] == {}
    assert again['plans']['NVDA'] == 'existing'


def test_rerun_same_session_no_duplicate_plans(db, scripted_model):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses()))
    service.run_batch(tickers=['NVDA'], now=NOW + timedelta(minutes=5),
                      deps=agent_deps(scripted_model, []))
    assert db.execute("SELECT count(*) FROM trade_plans WHERE kind = 'agent'").fetchone()[0] == 1


def test_failed_unit_retried_until_max_attempts(db, scripted_model):
    for i in range(3):   # 三次批处理均失败
        service.run_batch(tickers=['NVDA'], now=NOW + timedelta(minutes=10 * i),
                          deps=agent_deps(scripted_model, []))
    assert [r['status'] for r in analysis_repo.agent_runs_for(db, 'NVDA', SESSION)] == ['error'] * 3
    service.run_batch(tickers=['NVDA'], now=NOW + timedelta(minutes=40),
                      deps=agent_deps(scripted_model, []))
    runs = analysis_repo.agent_runs_for(db, 'NVDA', SESSION)
    assert (runs[3]['status'], runs[3]['reason']) == ('skipped', 'max_attempts')   # 第四次只记一次
    service.run_batch(tickers=['NVDA'], now=NOW + timedelta(minutes=50),
                      deps=agent_deps(scripted_model, []))
    assert len(analysis_repo.agent_runs_for(db, 'NVDA', SESSION)) == 4               # 第五次不再写


class LateClock:
    """First call is the batch start; everything after is past the agent deadline."""

    def __init__(self):
        self.first = True

    def __call__(self):
        if self.first:
            self.first = False
            return 0.0
        return float(AGENT_DEADLINE_S)


def test_stage_budget_skips_remaining(db, scripted_model):
    service.run_batch(tickers=['NVDA'], now=NOW,
                      deps=agent_deps(scripted_model, [], clock=LateClock()))
    runs = analysis_repo.agent_runs_for(db, 'NVDA', SESSION)
    assert [(r['status'], r['reason']) for r in runs] == [('skipped', 'stage_budget')]


class _QuotaError(Exception):
    status_code = 429

    def __init__(self):
        super().__init__('Error code: 429 - quota')
        self.body = {'error': {'code': 'subscription_sharing_usage_limit_exceeded', 'message': '已达到 5 小时的使用上限'}}


class QuotaModel(BaseChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise _QuotaError()

    @property
    def _llm_type(self):
        return 'quota-test'


def test_quota_exhausted_skips_rest(db, scripted_model):
    d = agent_deps(scripted_model, [])
    d.agent_model = QuotaModel()
    summary = service.run_batch(tickers=['MU', 'NVDA'], now=NOW, deps=d)
    assert summary['agent']['quota_exhausted'] is True
    assert summary['agent']['runs'] == {'MU': 'error: quota_exhausted', 'NVDA': 'skipped: quota_exhausted'}
    assert db.execute("SELECT count(*) FROM trade_plans WHERE kind = 'rule'").fetchone()[0] == 2
    assert summary['plans'] == {'MU': 'inserted', 'NVDA': 'inserted'}   # 规则计划照常


class _ErrorModel(BaseChatModel):
    """A chat model whose every call fails like the ChatGPT endpoint: ``status`` and error ``code`` of the response."""
    status: int = 401
    code: str = 'invalid_token'

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        error = Exception(f'Error code: {self.status}')
        error.status_code = self.status
        error.body = {'error': {'code': self.code, 'message': 'x'}}
        raise error

    @property
    def _llm_type(self):
        return 'error-test'


@pytest.mark.parametrize('status, code, reason', [(401, 'invalid_token', 'not_connected'),
                                                  (404, 'model_not_found', 'model_unavailable')])
def test_connection_lost_mid_node_skips_the_rest(db, scripted_model, status, code, reason):
    """Spec §8.8: the first unit that finds the connection / model unusable ends the node's model calls; the remaining
    units are skipped with the same reason (like ``quota_exhausted``), the rule plans are untouched."""
    d = agent_deps(scripted_model, [])
    d.agent_model = _ErrorModel(status=status, code=code)
    summary = service.run_batch(tickers=['MU', 'NVDA'], now=NOW, deps=d)
    assert summary['agent']['runs'] == {'MU': f'error: {reason}', 'NVDA': f'skipped: {reason}'}
    assert summary['agent']['connection_lost'] == reason and summary['agent']['quota_exhausted'] is False
    assert [(r['status'], r['reason']) for r in analysis_repo.agent_runs_for(db, 'NVDA', SESSION)] == [('skipped', reason)]
    assert summary['plans'] == {'MU': 'inserted', 'NVDA': 'inserted'}


def test_a_healthy_run_has_no_connection_lost_key(db, scripted_model):
    summary = service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses('NVDA')))
    assert 'connection_lost' not in summary['agent']


def test_not_configured_makes_no_model_call(db, scripted_model):
    d = agent_deps(scripted_model, [])                     # 若被调用即抛错
    d.agent_settings = _agent_settings(connection='no_sharing')     # signed in, but the account shares no plan usage
    service.run_batch(tickers=['NVDA'], now=NOW, deps=d)
    run = analysis_repo.latest_agent_run(db, 'NVDA', SESSION)
    assert (run['status'], run['reason']) == ('skipped', 'not_configured')


def test_disabled_provider(db, scripted_model):
    d = agent_deps(scripted_model, [])
    d.agent_settings = _agent_settings(provider='off')
    service.run_batch(tickers=['NVDA'], now=NOW, deps=d)
    run = analysis_repo.latest_agent_run(db, 'NVDA', SESSION)
    assert (run['status'], run['reason']) == ('skipped', 'disabled')


def test_config_error_reported(db, scripted_model):
    d = agent_deps(scripted_model, [])
    d.agent_settings = _agent_settings(config_error='WORKBENCH_AGENT_DAILY_TOKENS 必须是正整数')
    summary = service.run_batch(tickers=['NVDA'], now=NOW, deps=d)
    run = analysis_repo.latest_agent_run(db, 'NVDA', SESSION)
    assert (run['status'], run['reason']) == ('skipped', 'not_configured')
    assert any(str(e).startswith('agent_config:') for e in summary['errors'])


def test_token_budget_skips(db, scripted_model):
    d = agent_deps(scripted_model, _ok_responses('MU'))
    d.agent_settings = _agent_settings(daily_tokens=10)    # 首个运行耗 60 token
    summary = service.run_batch(tickers=['MU', 'NVDA'], now=NOW, deps=d)
    assert analysis_repo.latest_agent_run(db, 'MU', SESSION)['status'] == 'ok'
    nvda = analysis_repo.latest_agent_run(db, 'NVDA', SESSION)
    assert (nvda['status'], nvda['reason']) == ('skipped', 'token_budget')


class FlatSources(Sources):
    """Constant prices: trend_reclaim never fires, ATR14 is 0 (rule plan skips)."""

    def bars(self, ticker, start, end_exclusive):
        if ticker == 'SPY':
            return super().bars(ticker, start, end_exclusive)
        days = [add_trading_days(SESSION, -(59 - i)) for i in range(60)]
        index = pd.DatetimeIndex([pd.Timestamp(d, tz='America/New_York') for d in days])
        return pd.DataFrame({'Open': [100.0] * 60, 'High': [100.0] * 60, 'Low': [100.0] * 60,
                             'Close': [100.0] * 60, 'Adj Close': [100.0] * 60, 'Volume': [1000] * 60,
                             'Stock Splits': [0.0] * 60}, index=index)


def test_atr_skipped_unit_still_gets_agent(db, scripted_model):
    db.execute("INSERT INTO signal_candidates (ticker, signal_date, action, rule_id, rule_version, inputs) "
               "VALUES ('NVDA', %s, 'buy', 'trend_reclaim', '1', '{}')", [SESSION])
    d = agent_deps(scripted_model, _ok_responses())
    d.bar_fetcher, d.news_fetcher, d.filing_source, d.earnings_fetcher = \
        (lambda *a, **k: FlatSources().bars(*a, **k), FlatSources().news, FlatSources(),
         FlatSources().earnings)
    summary = service.run_batch(tickers=['NVDA'], now=NOW, deps=d)
    assert summary['plans']['NVDA'].startswith('skip: atr')   # 规则计划因 ATR 跳过
    run = analysis_repo.latest_agent_run(db, 'NVDA', SESSION)
    assert run['status'] == 'ok'                              # Agent 照跑


def test_checkpoint_unavailable(db, scripted_model):
    import psycopg

    def boom():
        raise psycopg.OperationalError('checkpoint db down')

    d = agent_deps(scripted_model, [])
    d.agent_checkpointer = boom
    service.run_batch(tickers=['NVDA'], now=NOW, deps=d)
    run = analysis_repo.latest_agent_run(db, 'NVDA', SESSION)
    assert (run['status'], run['reason']) == ('error', 'checkpoint_unavailable')


class LeakyErrorModel(BaseChatModel):
    """Fails with the API key in the message, like an upstream error echoing the request."""
    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError('upstream 502 for key sk-test')

    @property
    def _llm_type(self):
        return 'leaky-test'


def test_error_detail_stored_redacted(db, scripted_model):
    d = agent_deps(scripted_model, [])
    d.agent_model = LeakyErrorModel()
    service.run_batch(tickers=['NVDA'], now=NOW, deps=d)
    run = analysis_repo.latest_agent_run(db, 'NVDA', SESSION)
    assert (run['status'], run['reason']) == ('error', 'model_error')
    assert run['detail'] == 'RuntimeError: upstream 502 for key ***'
    assert agent_run_detail(str(run['id']))['detail'] == run['detail']
    assert agent_runs(now=NOW)['runs'][0]['detail'] == run['detail']


def test_checkpoint_unavailable_detail_stored(db, scripted_model):
    import psycopg

    def boom():
        raise psycopg.OperationalError('checkpoint db down')

    d = agent_deps(scripted_model, [])
    d.agent_checkpointer = boom
    service.run_batch(tickers=['NVDA'], now=NOW, deps=d)
    run = analysis_repo.latest_agent_run(db, 'NVDA', SESSION)
    assert run['detail'] == 'OperationalError: checkpoint db down'


def test_ok_and_skipped_runs_have_no_detail(db, scripted_model):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses()))
    service.run_batch(tickers=['MU'], now=NOW,
                      deps=agent_deps(scripted_model, [], settings=_agent_settings(provider='off')))
    assert analysis_repo.latest_agent_run(db, 'NVDA', SESSION)['detail'] is None
    assert analysis_repo.latest_agent_run(db, 'MU', SESSION)['detail'] is None


def test_summary_agent_keys(db, scripted_model):
    summary = service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses()))
    assert summary['agent'] == {'runs': {'NVDA': 'ok'}, 'briefs': {'NVDA': 'ok'},
                                'tokens': {'input': 60, 'output': 30}, 'quota_exhausted': False, 'merged': {},
                                'research_requests': 0,      # v2.13: the node's tails (empty queues here)
                                'data_requests': {'queued': 0, 'fetched': 0, 'failed': 0, 'deferred': 0,
                                                  'reruns': 0}}
    assert 'agent' in summary['sources'] and 'agent' in summary['timings']


def test_manual_run_conflict_and_not_buy(db, scripted_model):
    service.run_batch(tickers=['NVDA'], now=NOW,
                      deps=agent_deps(scripted_model, [], settings=_agent_settings(provider='off')))
    breakdown = BreakdownSources()
    service.run_batch(tickers=['AMD'], now=NOW, deps=service.Deps(
        bar_fetcher=breakdown.bars, news_fetcher=breakdown.news, filing_source=breakdown,
        earnings_fetcher=breakdown.earnings,
        agent_settings=_agent_settings(provider='off')))
    with pytest.raises(ValueError):
        analysis_agent_runner.run_agent('AMD', SESSION, now=NOW)              # sell 单元
    with pytest.raises(ValueError):
        analysis_agent_runner.run_agent('MU', SESSION, now=NOW)               # 不存在
    run = analysis_agent_runner.run_agent('NVDA', SESSION, now=NOW, deps=agent_deps(scripted_model, _ok_responses()))
    assert run['status'] == 'ok' and run['plan_id'] is not None
    assert run['agent_version'] == 'agent-4'
    [version] = execution_repo.chain_versions(db, 'NVDA', SESSION)       # 手动重跑也只生成首版
    assert (version['version_no'], version['origin']) == (0, 'signal')
    assert str(version['plan_id']) == str(run['plan_id'])
    with pytest.raises(analysis_agent_runner.AgentConflict):                         # 已有首版：不再生成
        analysis_agent_runner.run_agent('NVDA', SESSION, now=NOW, deps=agent_deps(scripted_model, []))
    assert len(execution_repo.chain_versions(db, 'NVDA', SESSION)) == 1


# ---------------------------------------------------------------- agent views (P3 §10.1/10.3)

def test_today_unit_has_agent_ok_view(db, scripted_model):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses()))
    unit = unit_cards(now=NOW)['units'][0]
    assert set(unit['agent']) == {'id', 'status', 'model', 'agent_version', 'output', 'counter', 'plan',
                                  'input_tokens', 'output_tokens', 'created_at', 'chain', 'knowledge_refs'}
    assert unit['agent']['knowledge_refs'] == []                       # v2.7 §6.2: nothing cited by this plan
    assert unit['agent']['status'] == 'ok' and unit['agent']['model'] == 'gpt-test'
    assert unit['agent']['plan']['kind'] == 'agent' and unit['agent']['agent_version'] == 'agent-4'
    assert unit['agent']['output']['levels']['entry_low']['value'] == '98.50'
    chain = unit['agent']['chain']
    assert set(chain) == {'version_no', 'head_plan_id', 'cutoff_at', 'diff', 'state', 'mode', 'basis',
                          'latest_review'}
    assert (chain['version_no'], chain['state'], chain['mode'], chain['latest_review']) == (0, 'entry', 'entry', None)
    assert chain['diff'] is None and chain['cutoff_at'] == execution_repo.chain_head(db, 'NVDA', SESSION)['version']['cutoff_at']
    assert chain['head_plan_id'] == str(unit['agent']['plan']['id'])


def test_today_hides_rejected_output(db, scripted_model):
    bad = json.dumps({'action': 'buy', 'summary': 'x',
                      'levels': {k: {'value': v, 'refs': ['call:99']} for k, v in
                                 (('entry_low', 98.5), ('entry_high', 101.5), ('stop', 96.0), ('target1', 108.0))},
                      'thesis': [{'text': 't', 'refs': ['call:99']}]})
    service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, [
        _tool_msg('get_news', {'days': 7}), AIMessage(content='非JSON研究结论'),   # the researcher fails softly (D23)
        _tool_msg('get_indicators', {'ticker': 'NVDA'}), AIMessage(content=bad)]))
    unit = unit_cards(now=NOW)['units'][0]
    assert set(unit['agent']) == {'id', 'status', 'reason', 'created_at'}
    assert unit['agent']['reason'] == 'invalid_citation'


def test_extended_unit_agent_status(db):
    from investment_assistant.analysis import pool as pool_module
    db.execute("INSERT INTO pool_tiers (ticker, tier, effective_session, recorded_at) "
               "VALUES ('CCC', 'extended', %s, %s)", [SESSION - timedelta(days=1), NOW])
    off = deps(Sources())
    off.agent_settings = _agent_settings(provider='off')
    service.run_batch(pool=pool_module.Pool(core=('AAA',), extended=('CCC',)), now=NOW, deps=off)
    db.execute("INSERT INTO pool_tiers (ticker, tier, effective_session, recorded_at) "
               "VALUES ('XNO', 'extended', %s, %s)", [SESSION - timedelta(days=1), NOW])
    db.execute("INSERT INTO signal_candidates (ticker, signal_date, action, rule_id, rule_version, inputs) "
               "VALUES ('XNO', %s, 'buy', 'trend_reclaim', '1', '{}')", [SESSION])
    ext = {e['ticker']: e for e in unit_cards(now=NOW)['extended_units']}
    assert ext['CCC']['agent_status'] == 'skipped: disabled'
    assert ext['XNO']['agent_status'] is None      # 从未运行


def test_agent_runs_coverage_lists_missing(db, scripted_model):
    service.run_batch(tickers=['NVDA'], now=NOW,
                      deps=agent_deps(scripted_model, [], settings=_agent_settings(provider='off')))
    db.execute("INSERT INTO signal_candidates (ticker, signal_date, action, rule_id, rule_version, inputs) "
               "VALUES ('XNO', %s, 'buy', 'trend_reclaim', '1', '{}')", [SESSION])
    out = agent_runs(now=NOW)
    assert out['coverage'] == {'units': 2, 'covered': 1, 'merged': 0, 'merged_units': [],
                               'missing': [{'ticker': 'XNO', 'signal_date': SESSION}]}
    assert [r['ticker'] for r in out['runs']] == ['NVDA']
    assert all('output' not in r and r['tool_calls'] >= 0 for r in out['runs'])


def test_agent_run_detail_has_calls_and_hashes(db, scripted_model):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses()))
    run = analysis_repo.latest_agent_run(db, 'NVDA', SESSION)
    detail = agent_run_detail(str(run['id']))
    assert [c['seq'] for c in detail['tool_calls']] == [1, 2, 3]   # researcher, propose, counter
    assert all(len(c['result_sha256']) == 64 for c in detail['tool_calls'])
    with pytest.raises(LookupError):
        agent_run_detail(str(uuid.uuid4()))


import psycopg                                                                    # noqa: E402


class MissingCheckpointModel(BaseChatModel):
    """First model call raises the error a PostgresSaver raises when the tables are missing."""

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise psycopg.errors.UndefinedTable('relation "checkpoints" does not exist')

    @property
    def _llm_type(self):
        return 'missing-checkpoint'


def test_checkpoint_table_missing_is_checkpoint_unavailable(db, scripted_model):
    import psycopg
    d = agent_deps(scripted_model, [])
    d.agent_model = MissingCheckpointModel()
    service.run_batch(tickers=['NVDA'], now=NOW, deps=d)
    run = analysis_repo.latest_agent_run(db, 'NVDA', SESSION)
    assert (run['status'], run['reason']) == ('error', 'checkpoint_unavailable')


# --- agent-produced briefs and the interpret group (P4 §7) ---

def _interpret_reply(ref='call:1'):
    return [_tool_msg('get_trigger', {}), AIMessage(content=json.dumps(
        {'summary': '持仓回撤，趋势未坏', 'support': [{'text': '量能萎缩', 'refs': [ref]}],
         'contra': [], 'conflicts': []}, ensure_ascii=False))]


def _watch_candidate(db, ticker='WTCH'):
    db.execute('INSERT INTO signal_candidates (ticker, signal_date, action, rule_id, rule_version, inputs) '
               "VALUES (%s, %s, 'watch', 'watch_only', '1', '{}')", [ticker, SESSION])


def _buy_candidate(db, ticker='WTCH', rule_id='manual_buy'):
    db.execute('INSERT INTO signal_candidates (ticker, signal_date, action, rule_id, rule_version, inputs) '
               "VALUES (%s, %s, 'buy', %s, '1', '{}')", [ticker, SESSION, rule_id])


def _mixed_deps(make, responses, *, flat=(), **overrides):
    """Deps whose bars are flat for the given tickers (no rule fires) and default otherwise."""
    d = agent_deps(make, responses, **overrides)
    sources = Sources()

    def bars(ticker, start, end_exclusive):
        if ticker in flat:
            return FlatSources().bars(ticker, start, end_exclusive)
        return sources.bars(ticker, start, end_exclusive)
    d.bar_fetcher = bars
    return d


def test_rules_loop_writes_no_brief(db, monkeypatch):
    monkeypatch.setattr(analysis_agent_runner, 'agent_stage', lambda *a, **k: None)
    summary = service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()))
    assert db.execute('SELECT count(*) FROM signal_briefs').fetchone()[0] == 0
    assert summary['briefs'] == {}


def test_buy_unit_brief_derived_from_proposal(db, scripted_model):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses()))
    run = analysis_repo.latest_agent_run(db, 'NVDA', SESSION)
    brief = analysis_repo.latest_brief(db, 'NVDA', SESSION)
    assert brief['prompt_version'] == 'agent-4' and brief['status'] == 'ok'
    assert brief['agent_run_id'] == run['id']
    assert brief['output']['summary'] == '回踩修复放量'
    # agent-2 (v2.5 Task 6): the brief carries the summary and the first per-thesis counter
    assert brief['output']['contra'] == [{'thesis': 1, 'text': '财报临近', 'refs': ['call:1']}]
    assert [r['mode'] for r in analysis_repo.agent_runs_for(db, 'NVDA', SESSION)] == ['propose']


def test_sell_and_watch_units_get_interpretations(db, scripted_model):
    _watch_candidate(db)
    d = _mixed_deps(scripted_model, _interpret_reply() * 2, flat=('WTCH',))
    breakdown = BreakdownSources()
    original = d.bar_fetcher
    d.bar_fetcher = lambda ticker, start, end: (breakdown.bars(ticker, start, end)
                                                if ticker == 'AMD' else original(ticker, start, end))
    summary = service.run_batch(tickers=['AMD', 'WTCH'], now=NOW, deps=d)
    for ticker in ('AMD', 'WTCH'):
        runs = analysis_repo.agent_runs_for(db, ticker, SESSION, mode='interpret')
        assert [r['status'] for r in runs] == ['ok']
        brief = analysis_repo.latest_brief(db, ticker, SESSION)
        assert brief['status'] == 'ok' and brief['prompt_version'] == 'agent-brief-1'
    assert db.execute("SELECT count(*) FROM agent_runs WHERE mode = 'propose'").fetchone()[0] == 0


def test_conflict_buy_unit_is_interpreted_not_proposed(db, scripted_model):
    legacy_decisions.add_trade({'request_id': '6f1d1c1e-2222-4444-8888-000000000042', 'ticker': 'NVDA', 'side': 'buy',
                       'quantity': '10', 'price': '137.00', 'executed_at': '2026-10-10T15:00:00+00:00'}, now=NOW)
    summary = service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, _interpret_reply()))
    runs = analysis_repo.agent_runs_for(db, 'NVDA', SESSION, mode='interpret')
    assert [r['status'] for r in runs] == ['ok']
    assert db.execute("SELECT count(*) FROM agent_runs WHERE mode = 'propose'").fetchone()[0] == 0
    assert analysis_repo.latest_brief(db, 'NVDA', SESSION)['prompt_version'] == 'agent-brief-1'


def test_agent_off_writes_rule_only_briefs(db, scripted_model):
    seed_watchlist(db, [('AAA', ['core']), ('NVDA', [])])
    d = agent_deps(scripted_model, [])
    d.agent_settings = _agent_settings(provider='off')
    summary = service.run_batch(now=NOW, deps=d)
    rows = dict(db.execute('SELECT ticker, status FROM signal_briefs').fetchall())
    errors = dict(db.execute('SELECT ticker, error FROM signal_briefs').fetchall())
    assert rows == {'AAA': 'fallback'}              # 扩展池 NVDA 不写解读
    assert errors == {'AAA': '已关闭'}
    assert summary['briefs'] == {'AAA': 'fallback'}
    assert db.execute("SELECT count(*) FROM agent_runs WHERE status = 'skipped'").fetchone()[0] == 2


def test_stage_order_main_propose_interpret_extended(db, scripted_model):
    from investment_assistant.analysis import pool as pool_module
    _watch_candidate(db)
    db.execute('INSERT INTO pool_tiers (ticker, tier, effective_session, recorded_at) '
               "VALUES ('CCC', 'extended', %s, %s)", [SESSION - timedelta(days=1), NOW])
    def unit_replies(ticker):
        return [_tool_msg('get_news', {'days': 7}), _research_ok(),          # the researcher phase (§6.1)
                _tool_msg('get_indicators', {'ticker': ticker}), AIMessage(content=_agent4_buy()),
                AIMessage(content=json.dumps({'counter': [], 'risks': []}))]
    d = _mixed_deps(scripted_model, unit_replies('AAA') + _interpret_reply() + unit_replies('CCC'),
                    flat=('WTCH',))
    model = d.agent_model
    service.run_batch(pool=pool_module.Pool(core=('AAA', 'WTCH'), extended=('CCC',)), now=NOW, deps=d)
    tickers_seen = [m.content.split(' ')[1] for messages in model.seen for m in messages
                    if m.type == 'human' and str(m.content).startswith('标的 ')]
    # counter replies carry no unit human message; agent-4 units show research + propose humans
    assert tickers_seen == ['AAA'] * 4 + ['WTCH'] * 2 + ['CCC'] * 4


def test_quota_exhausted_skips_interpret_and_extended(db, scripted_model):
    from investment_assistant.analysis import pool as pool_module
    _watch_candidate(db)
    db.execute('INSERT INTO pool_tiers (ticker, tier, effective_session, recorded_at) '
               "VALUES ('CCC', 'extended', %s, %s)", [SESSION - timedelta(days=1), NOW])
    d = _mixed_deps(scripted_model, [], flat=('WTCH',))
    d.agent_model = QuotaModel()
    summary = service.run_batch(pool=pool_module.Pool(core=('AAA', 'WTCH'), extended=('CCC',)), now=NOW, deps=d)
    rows = db.execute('SELECT ticker, mode, status, reason FROM agent_runs ORDER BY created_at, id').fetchall()
    assert rows == [('AAA', 'propose', 'error', 'quota_exhausted'),
                    ('WTCH', 'interpret', 'skipped', 'quota_exhausted'),
                    ('CCC', 'propose', 'skipped', 'quota_exhausted')]
    assert summary['agent']['runs'] == {'AAA': 'error: quota_exhausted', 'CCC': 'skipped: quota_exhausted'}
    assert summary['agent']['briefs'] == {'AAA': 'error: quota_exhausted', 'WTCH': 'skipped: quota_exhausted'}
    assert summary['briefs'] == {'AAA': 'fallback', 'WTCH': 'fallback'}


def test_interpret_ok_does_not_skip_propose(db, scripted_model):
    _watch_candidate(db)
    d = _mixed_deps(scripted_model, _interpret_reply(), flat=('WTCH',))
    service.run_batch(tickers=['WTCH'], now=NOW, deps=d)
    assert analysis_repo.latest_agent_run(db, 'WTCH', SESSION, mode='interpret')['mode'] == 'interpret'
    _buy_candidate(db)
    later = NOW + timedelta(minutes=10)
    again = _mixed_deps(scripted_model, _ok_responses('WTCH'), flat=('WTCH',))
    service.run_batch(tickers=['WTCH'], now=later, deps=again)
    propose = analysis_repo.agent_runs_for(db, 'WTCH', SESSION)          # 默认只看 propose
    assert [r['status'] for r in propose] == ['ok']
    unit = next(u for u in unit_cards(now=later)['units'] if u['ticker'] == 'WTCH')
    assert unit['agent']['status'] == 'ok'


def test_attempts_counted_per_mode(db, scripted_model):
    for i in range(3):   # propose 三次失败
        service.run_batch(tickers=['NVDA'], now=NOW + timedelta(minutes=10 * i),
                          deps=agent_deps(scripted_model, []))
    assert [r['status'] for r in analysis_repo.agent_runs_for(db, 'NVDA', SESSION)] == ['error'] * 3
    _watch_candidate(db)
    d = _mixed_deps(scripted_model, _interpret_reply(), flat=('WTCH',))
    summary = service.run_batch(tickers=['NVDA', 'WTCH'], now=NOW + timedelta(minutes=40), deps=d)
    nvda = analysis_repo.agent_runs_for(db, 'NVDA', SESSION)
    assert (nvda[3]['status'], nvda[3]['reason']) == ('skipped', 'max_attempts')   # propose 达上限
    assert [r['status'] for r in analysis_repo.agent_runs_for(db, 'WTCH', SESSION, mode='interpret')] == ['ok']


def test_agent_runs_coverage_ignores_interpret(db, scripted_model):
    _watch_candidate(db)
    d = _mixed_deps(scripted_model, _interpret_reply(), flat=('WTCH',))
    service.run_batch(tickers=['WTCH'], now=NOW, deps=d)
    _buy_candidate(db)
    view = agent_runs(now=NOW)
    assert view['coverage']['missing'] == [{'ticker': 'WTCH', 'signal_date': SESSION}]
    assert any(r['mode'] == 'interpret' for r in view['runs'])


# --- three-track rows and the live view (P4 §4) ---

def _decide(unit, final, *, plan_id=None, now=NOW):
    return legacy_decisions.decide({
        'request_id': str(uuid.uuid4()), 'ticker': unit['ticker'],
        'signal_date': unit['signal_date'].isoformat(),
        'candidate_ids': [str(c) for c in unit['candidate_ids']],
        'proposed_action': unit['proposed_action'], 'final_action': final,
        'decision': 'accept' if final != 'ignore' else 'reject', 'reason': '轨道对比用例',
        'plan_id': str(plan_id) if plan_id else None}, now=now)['record']


def test_track_rows_trace_ids(db, scripted_model):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses()))
    off = agent_deps(scripted_model, [], settings=_agent_settings(provider='off'))
    service.run_batch(tickers=['MU'], now=NOW, deps=off)
    view = unit_cards(now=NOW)
    nvda_unit = next(u for u in view['units'] if u['ticker'] == 'NVDA')
    mu_unit = next(u for u in view['units'] if u['ticker'] == 'MU')
    agent_plan = execution_repo.agent_plan(db, 'NVDA', SESSION)
    nvda_decision = _decide(nvda_unit, 'buy', plan_id=agent_plan['id'])
    _decide(mu_unit, 'ignore')
    trade = legacy_decisions.add_trade({'request_id': str(uuid.uuid4()), 'ticker': 'NVDA', 'side': 'buy',
                               'quantity': '10', 'price': '100.00', 'fee': '1',
                               'executed_at': '2026-10-22T15:00:00+00:00',
                               'decision_id': str(nvda_decision['id'])}, now=NOW)['record']
    rows = {row['ticker']: row for row in review_weekly.tracks(now=NOW)['units']}
    nvda = rows['NVDA']
    assert nvda['rule']['plan_id'] == str(execution_repo.rule_plan(db, 'NVDA', SESSION)['id'])
    assert nvda['agent']['run_id'] == str(analysis_repo.latest_agent_run(db, 'NVDA', SESSION)['id'])
    assert nvda['agent']['plan_id'] == str(agent_plan['id'])
    assert nvda['actual']['decision_id'] == str(nvda_decision['id'])
    assert nvda['actual']['buy_trade_ids'] == [str(trade['id'])]
    assert nvda['actual']['plan_kind'] == 'agent'
    mu = rows['MU']
    assert mu['actual']['status'] == 'not_executed' and mu['actual']['r'] == 0.0


def test_track_rows_keep_buys_after_decision_correction(db, scripted_model):
    """A buy linked to an earlier decision in the chain still belongs to the unit."""
    service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses()))
    nvda_unit = next(u for u in unit_cards(now=NOW)['units'] if u['ticker'] == 'NVDA')
    agent_plan = execution_repo.agent_plan(db, 'NVDA', SESSION)
    first = _decide(nvda_unit, 'buy', plan_id=agent_plan['id'])
    trade = legacy_decisions.add_trade({'request_id': str(uuid.uuid4()), 'ticker': 'NVDA', 'side': 'buy',
                               'quantity': '10', 'price': '100.00', 'fee': '1',
                               'executed_at': '2026-10-22T15:00:00+00:00',
                               'decision_id': str(first['id'])}, now=NOW)['record']
    corrected = legacy_decisions.decide({
        'request_id': str(uuid.uuid4()), 'ticker': 'NVDA', 'signal_date': SESSION.isoformat(),
        'candidate_ids': [str(c) for c in nvda_unit['candidate_ids']],
        'proposed_action': nvda_unit['proposed_action'], 'final_action': 'buy', 'decision': 'accept',
        'reason': '更正理由', 'plan_id': str(agent_plan['id']), 'supersedes_id': str(first['id'])},
        now=NOW + timedelta(minutes=5))['record']
    actual = next(r for r in review_weekly.tracks(now=NOW)['units'] if r['ticker'] == 'NVDA')['actual']
    assert actual['decision_id'] == str(corrected['id'])
    assert actual['buy_trade_ids'] == [str(trade['id'])]
    assert actual['status'] == 'open' and actual['r'] is not None


def test_track_units_include_agent_only_unit(db, scripted_model):
    db.execute('INSERT INTO signal_candidates (ticker, signal_date, action, rule_id, rule_version, inputs) '
               "VALUES ('NVDA', %s, 'buy', 'trend_reclaim', '1', '{}')", [SESSION])
    d = agent_deps(scripted_model, _ok_responses())
    flat = FlatSources()
    d.bar_fetcher, d.news_fetcher, d.filing_source, d.earnings_fetcher =         flat.bars, flat.news, flat, flat.earnings
    service.run_batch(tickers=['NVDA'], now=NOW, deps=d)      # ATR 跳过规则计划，Agent 照跑
    units = analysis_repo.track_units(db, SESSION - timedelta(days=7), SESSION)
    assert units == [{'ticker': 'NVDA', 'signal_date': SESSION}]


def test_track_units_ignore_interpret_runs(db, scripted_model):
    _watch_candidate(db)
    d = _mixed_deps(scripted_model, _interpret_reply(), flat=('WTCH',))
    service.run_batch(tickers=['WTCH'], now=NOW, deps=d)
    assert analysis_repo.track_units(db, SESSION - timedelta(days=7), SESSION) == []


def test_weekly_snapshot_has_tracks(db, scripted_model):
    off = agent_deps(scripted_model, [], settings=_agent_settings(provider='off'))
    service.run_batch(tickers=['NVDA', 'MU'], now=NOW, deps=off)
    record = review_weekly.generate_weekly(SESSION - timedelta(days=SESSION.weekday()), now=NOW)
    assert record['calc_version'] == 'review-8'
    stats = record['stats']
    assert stats['tracks']['summary']['all']['actual']['units'] == 2
    assert all('ticker' in row and row['rule'] and row['agent'] and row['actual']
               for row in stats['tracks']['units'])


# ------------------------------------------------- fetch-run recording (v2.3 Task 5)

def _universe_deps(sources, members):
    """Deps over Sources plus a synthetic range, so the screen stage fetches the members."""
    from investment_assistant.data.adapters import universe as universe_module
    d = deps(sources)
    d.universe = universe_module.Universe('test', SESSION, 'test', tuple(members))
    return d


def test_batch_records_nine_families(db):
    """v2.12 (spec §10): one row per registered family — the five of the v2.3 collection, the earnings date row and
    the registry families fundamentals / sec_text / sector."""
    import investment_assistant.workflow.targets as families
    seed_watchlist(db, [('NVDA', ['core'])])
    summary = service.run_batch(now=NOW, deps=_universe_deps(Sources(fail={'MU'}), ['MU']),
                                record_trigger='scheduled')
    rows = db.execute('SELECT family, job, trigger, run_id, scope, session, status, failures '
                      'FROM data_fetch_runs ORDER BY family').fetchall()
    assert len(rows) == 9
    by_family = {row[0]: row for row in rows}
    assert set(by_family) == set(data_families.RECORDED)
    for row in rows:
        assert row[1:6] == ('workbench', 'scheduled', None, 'family', SESSION)
    assert by_family['bars'][6] == 'partial'
    assert {'target': 'MU', 'reason': 'provider down'} in by_family['bars'][7]
    assert by_family['fred'][6] == 'skipped'
    assert by_family['universe'][6] == 'success'
    assert summary['data_status'] == {'written': 9, 'error': None}


def test_rerun_appends_new_status_rows(db):
    seed_watchlist(db, [('NVDA', ['core'])])
    d = _universe_deps(Sources(), ['MU'])
    service.run_batch(now=NOW, deps=d, record_trigger='scheduled')
    service.run_batch(now=NOW, deps=d, record_trigger='scheduled')
    assert db.execute('SELECT count(*) FROM data_fetch_runs').fetchone()[0] == 18


def test_fred_failure_reason_is_redacted(db, monkeypatch):
    seed_watchlist(db, [('NVDA', ['core'])])
    monkeypatch.setenv('FRED_API_KEY', 'k3y')

    def macro_fetcher(url, *, params):
        raise RuntimeError('bad k3y')

    d = _universe_deps(Sources(), ['MU'])
    d.macro_fetcher = macro_fetcher
    service.run_batch(now=NOW, deps=d, record_trigger='scheduled')
    status, failures = db.execute("SELECT status, failures FROM data_fetch_runs "
                                  "WHERE family = 'fred'").fetchone()
    assert status == 'failed'                                  # 两条序列逐只失败
    assert failures[0]['reason'] == 'bad ***'


def test_batch_survives_missing_fetch_runs_table(db):
    seed_watchlist(db, [('NVDA', ['core'])])
    d = _universe_deps(Sources(fail={'MU'}), ['MU'])
    baseline = service.run_batch(now=NOW, deps=d, record_trigger='scheduled')
    db.execute('DROP TABLE data_fetch_runs CASCADE')   # 031: data_requests points at it; the simulation drops both
    again = service.run_batch(now=NOW, deps=d, record_trigger='scheduled')
    assert again['errors'] == baseline['errors']               # 记账失败不进 summary['errors']
    assert again['data_status']['written'] == 0 and again['data_status']['error']


def test_no_record_trigger_writes_nothing(db):
    seed_watchlist(db, [('NVDA', ['core'])])
    summary = service.run_batch(now=NOW, deps=_universe_deps(Sources(), ['MU']))
    assert 'data_status' not in summary
    assert db.execute('SELECT count(*) FROM data_fetch_runs').fetchone()[0] == 0


def test_explicit_tickers_write_eight_families_without_universe(db):
    summary = service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()),
                                record_trigger='scheduled')
    recorded = {row[0] for row in db.execute('SELECT family FROM data_fetch_runs').fetchall()}
    assert recorded == {'bars', 'news', 'sec_filings', 'fred', 'earnings', 'fundamentals', 'sec_text', 'sector'}
    assert summary['data_status'] == {'written': 8, 'error': None}      # 没有筛选就没有 universe 行


def test_task_run_passes_trigger(db):
    import investment_assistant.workflow.targets as families
    seed_watchlist(db, [('NVDA', ['core'])])
    _task_run('manual')
    rows = db.execute('SELECT family, trigger FROM data_fetch_runs').fetchall()
    assert rows and {row[1] for row in rows} == {'manual_batch'}
    assert {row[0] for row in rows} == set(data_families.RECORDED)


def test_batch_waits_for_family_lock(db):
    import os
    import threading

    seed_watchlist(db, [('NVDA', ['core'])])
    url = os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']
    schema = db.execute('SHOW search_path').fetchone()[0]      # 与 workbench_db 同一 schema
    out = {}

    def run():
        out['summary'] = service.run_batch(now=NOW, deps=_universe_deps(Sources(), ['MU']),
                                           record_trigger='scheduled')

    with psycopg.connect(url, autocommit=True) as other:
        other.execute(f'SET search_path TO {schema}')
        other.execute("SELECT pg_advisory_lock(hashtext('data_family:news'))")
        try:
            thread = threading.Thread(target=run)
            thread.start()
            thread.join(0.5)
            assert thread.is_alive()                          # 阻塞等 news 锁，未开始采集
        finally:
            other.execute("SELECT pg_advisory_unlock(hashtext('data_family:news'))")
        thread.join(10)
        assert not thread.is_alive()
    assert out['summary']['data_status'] == {'written': 9, 'error': None}


def test_batch_sec_listing_is_per_ticker(db):
    """v2.8 D21: the SEC listing is one request per ticker, so a source that is down fails each ticker of the collect
    list on its own (no ``'*'``); MU, a screen member, is back-filled by the screen node, not part of this family row."""
    seed_watchlist(db, [('NVDA', ['core'])])

    class SecDown(Sources):
        def bars(self, ticker, start, end_exclusive):
            frame = super().bars(ticker, start, end_exclusive)
            if ticker == 'MU':   # 默认序列成交量过低会被 screen-1 判 illiquid，先保证入池
                frame = frame.copy()
                frame['Volume'] = 1_000_000
            return frame

        def discover(self, tickers, start, end):
            raise RuntimeError('edgar down')

    summary = service.run_batch(now=NOW, deps=_universe_deps(SecDown(), ['MU']), record_trigger='scheduled')
    row = db.execute("SELECT status, target_count, ok_count, failures, detail "
                     "FROM data_fetch_runs WHERE family = 'sec_filings'").fetchone()
    assert row[0] == 'failed'
    assert row[1] == 1 and row[2] == 0                          # NVDA only: the denominator is the real target set
    assert row[3] == [{'target': 'NVDA', 'reason': 'edgar down'}]
    assert 'failed_targets' not in row[4]
    assert 'edgar down' in ' '.join(summary['errors'])


def test_batch_sec_company_tickers_failure_is_the_call_sentinel(db):
    """Only when company_tickers.json is unreachable nothing can be resolved: ``'*'`` with the real denominator (I04)."""
    from investment_assistant.data.fetch_policy import FetchError
    from investment_assistant.data.sources import edgar as monitor_source

    seed_watchlist(db, [('NVDA', ['core'])])

    class NoDirectory(Sources):
        def discover(self, tickers, start, end):
            raise FetchError('dns down', kind='network', attempts=[{'n': 1, 'kind': 'network', 'status': None,
                                                                    'elapsed_ms': 5}],
                             target=monitor_source.COMPANY_TICKERS_TARGET)

    service.run_batch(tickers=['NVDA', 'MU'], now=NOW, deps=service.Deps(
        bar_fetcher=NoDirectory().bars, news_fetcher=NoDirectory().news, filing_source=NoDirectory(),
        earnings_fetcher=NoDirectory().earnings), record_trigger='scheduled')
    row = db.execute("SELECT status, target_count, ok_count, failures, detail "
                     "FROM data_fetch_runs WHERE family = 'sec_filings'").fetchone()
    assert row[0] == 'failed' and row[1] == 2 and row[2] == 0
    assert [f['target'] for f in row[3]] == ['*']
    assert row[4]['failed_targets'] == ['MU', 'NVDA']


def test_batch_rows_use_real_wall_clock(db):
    """拉取行时间戳用注入的真实墙钟，不再等于批处理 cutoff now（I07）。"""
    seed_watchlist(db, [('NVDA', ['core'])])
    tick = iter([datetime(2026, 10, 22, 21, 0, tzinfo=UTC),
                 datetime(2026, 10, 22, 21, 9, tzinfo=UTC)])   # 采集阶段耗时 9 分钟
    service.run_batch(now=NOW, deps=_universe_deps(Sources(), ['MU']), record_trigger='scheduled',
                      wall_now=lambda: next(tick))
    started, finished = db.execute(
        'SELECT DISTINCT started_at, finished_at FROM data_fetch_runs').fetchone()
    assert started == datetime(2026, 10, 22, 21, 0, tzinfo=UTC)
    assert finished == datetime(2026, 10, 22, 21, 9, tzinfo=UTC)


def test_screen_degradation_keeps_fetch_reasons_and_denominator(db):
    """QQQ 基准缺失导致筛选降级时，范围标的 MU 的采集失败仍进 bars 记账（I09）：
    非池成员不会再被逐股重试，丢在这里就永远丢了。"""
    seed_watchlist(db, [('NVDA', ['core'])])                    # NVDA 在池里；MU 只在范围
    service.run_batch(now=NOW, deps=_universe_deps(Sources(fail={'QQQ', 'MU'}), ['MU']),
                      record_trigger='scheduled')
    row = db.execute("SELECT r.status, r.failures, r.target_count, r.ok_count, "
                     "s.summary->>'error' FROM data_fetch_runs r, screen_runs s "
                     "WHERE r.family = 'bars' ORDER BY r.id DESC LIMIT 1").fetchone()
    assert row[4] == 'benchmark_missing'
    by_target = {f['target']: f['reason'] for f in row[1]}
    assert by_target.get('MU') == 'provider down'               # 降级不丢失败原因
    assert by_target.get('QQQ') == 'provider down'
    assert row[0] == 'partial'
    assert row[3] == row[2] - 2                                 # 分母含 MU，只有 QQQ+MU 失败


def test_manual_targets_exclude_closed_positions_but_batch_keeps_them(db):
    """已知兼容偏差（M01）：手动目标=观察池∪未平仓持仓；旧批处理循环仍采集已清仓
    历史持仓。此测试钉住该偏差，防止被误称为与批处理完全一致。"""
    import investment_assistant.workflow.targets as families
    seed_watchlist(db, [('AMD', ['core'])])
    for offset, side in ((10, 'buy'), (9, 'sell')):
        db.execute('INSERT INTO trades (request_id, request_hash, ticker, side, quantity, price, '
                   'executed_at) VALUES (%s, %s, %s, %s, 10, 100, %s)',
                   [uuid.uuid4(), f'flat-{side}', 'FLAT', side, NOW - timedelta(days=offset)])
    db.commit()

    manual, _ = families.targets(db, 'news', session=NOW.date(), now=NOW)
    assert manual == ['AMD']                                   # FLAT 已清仓：手动目标不含
    service.run_batch(now=NOW, deps=_universe_deps(Sources(), []), record_trigger='scheduled')
    target_count = db.execute("SELECT target_count FROM data_fetch_runs "
                              "WHERE family = 'news' ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert target_count == 2                                   # 批处理仍采集 AMD + FLAT（旧行为）


# ---------------------------------------------------------------- v2.5 Task 6: agent-2 first versions and chain views

def _drop_timeline_tables(db):
    """Simulate a database where migration 026 never ran (its three tables are missing)."""
    db.execute('DROP TABLE plan_reviews, user_inputs, agent_plan_versions CASCADE')
    db.commit()


def test_agent_stage_without_migration_026(db, scripted_model):
    from investment_assistant.analysis import pool as pool_module
    import investment_assistant.workflow.recorder as stages_module
    _drop_timeline_tables(db)
    assert core_schema.timeline_ready(db) is False
    _watch_candidate(db)
    db.execute('INSERT INTO pool_tiers (ticker, tier, effective_session, recorded_at) '
               "VALUES ('CCC', 'extended', %s, %s)", [SESSION - timedelta(days=1), NOW])
    d = _mixed_deps(scripted_model, _interpret_reply(), flat=('WTCH',))     # 只有解读会调用模型
    recorder = stages_module.StageRecorder('run-no026', 'scheduled', SESSION, wall_now=lambda: NOW)
    summary = service.run_batch(pool=pool_module.Pool(core=('AAA', 'WTCH'), extended=('CCC',)), now=NOW,
                                deps=d, stages=recorder)
    rows = db.execute('SELECT ticker, mode, status, reason FROM agent_runs ORDER BY created_at, id').fetchall()
    assert rows == [('AAA', 'propose', 'skipped', 'migration_026_missing'),
                    ('WTCH', 'interpret', 'ok', None),
                    ('CCC', 'propose', 'skipped', 'migration_026_missing')]
    aaa_brief = analysis_repo.latest_brief(db, 'AAA', SESSION)                   # 主列表照旧得到规则版简报
    assert (aaa_brief['status'], aaa_brief['error']) == ('fallback', '迁移 026 未执行')
    assert analysis_repo.latest_brief(db, 'CCC', SESSION) is None                # 扩展池不写简报
    assert summary['plans']['AAA'] == 'inserted' and summary['plans']['CCC'] == 'inserted'   # 规则计划照常
    assert db.execute("SELECT count(*) FROM trade_plans WHERE kind = 'agent'").fetchone()[0] == 0
    assert summary['errors'].count('timeline: migration_026_missing') == 1
    [agent_row] = [r for r in workflow_repo.stage_runs_for_run(db, 'run-no026') if r['stage'] == 'agent']
    assert agent_row['status'] == 'degraded'
    assert [g['kind'] for g in agent_row['degradations']] == ['migration_026_missing']
    assert [(g['reason'], g['samples']) for g in agent_row['skips']] == \
        [('migration_026_missing', ['AAA', 'CCC'])]

    # 「今日」不崩：按 agent-1 的方式读，没有链
    view = unit_cards(now=NOW)
    assert view['timeline_ready'] is False and view['chains'] == []
    aaa = next(u for u in view['units'] if u['ticker'] == 'AAA')
    assert aaa['agent']['status'] == 'skipped' and aaa['agent']['reason'] == 'migration_026_missing'
    assert 'chain' not in aaa['agent'] and 'legacy' not in aaa['agent']


def _insert_run(db, *, ticker='NVDA', mode, status, at, agent_version='agent-2', plan_id=None, reason=None,
                output=None, counter=None):
    return analysis_repo.insert_agent_run(db, {
        'id': str(uuid.uuid4()), 'ticker': ticker, 'signal_date': SESSION, 'pool': 'main', 'attempt': 1,
        'mode': mode, 'agent_version': agent_version, 'provider': 'chatgpt', 'model': 'gpt-test', 'cutoff_at': at,
        'status': status, 'reason': reason, 'detail': None, 'output': output, 'counter': counter,
        'plan_id': plan_id, 'input_tokens': 1, 'output_tokens': 1, 'started_at': at, 'finished_at': at}, [])


def test_today_agent_view_uses_head_not_latest_run(db, scripted_model):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses()))
    v0 = analysis_repo.latest_agent_run(db, 'NVDA', SESSION)
    head = execution_repo.chain_head(db, 'NVDA', SESSION)
    later = NOW + timedelta(minutes=30)
    _insert_run(db, mode='revise', status='rejected', reason='changes_mismatch', at=later)
    _insert_run(db, mode='propose', status='error', reason='model_error', at=later)   # 比首版更晚的运行
    db.commit()
    unit = unit_cards(now=later)['units'][0]
    agent = unit['agent']
    assert agent['id'] == str(v0['id']) and agent['status'] == 'ok'            # 内容取最新版的运行
    assert agent['output'] == v0['output'] and agent['counter'] == v0['counter']
    assert str(agent['plan']['id']) == str(head['plan']['id'])
    assert agent['chain']['version_no'] == 0 and agent['chain']['head_plan_id'] == str(head['plan']['id'])
    assert 'legacy' not in agent


def test_today_marks_agent1_legacy(db, scripted_model):
    from investment_assistant.config import WorkbenchConfig
    off = agent_deps(scripted_model, [], settings=_agent_settings(provider='off'))
    service.run_batch(tickers=['NVDA'], now=NOW, deps=off)
    later = NOW + timedelta(minutes=10)
    run_id = str(uuid.uuid4())
    levels = {'entry_low': Decimal('98.50'), 'entry_high': Decimal('101.50'), 'stop': Decimal('96.00'),
              'target1': Decimal('108.00')}
    old = plans.build_agent_plan(levels=levels, agent_run_id=run_id, model='gpt-test', ticker='NVDA',
                                 signal_date=SESSION, bars=data_repo.read_bars(db, 'NVDA', SESSION, cutoff_at=NOW),
                                 earnings_row=None, snapshot=None,
                                 exposure={'ticker_value': Decimal(0), 'total_value': Decimal(0)},
                                 params=WorkbenchConfig())
    plan = execution_repo.insert_plan(db, old)
    output = {'action': 'buy', 'summary': '旧版建议',
              'levels': {k: {'value': str(v), 'refs': ['call:1']} for k, v in levels.items()},
              'thesis': [{'text': '旧论点', 'refs': ['call:1']}]}
    analysis_repo.insert_agent_run(db, {
        'id': run_id, 'ticker': 'NVDA', 'signal_date': SESSION, 'pool': 'main', 'attempt': 2, 'mode': 'propose',
        'agent_version': 'agent-1', 'provider': 'chatgpt', 'model': 'gpt-test', 'cutoff_at': later, 'status': 'ok',
        'reason': None, 'detail': None, 'output': output, 'counter': {'status': 'ok', 'risks': [], 'dropped': 0},
        'plan_id': plan['id'], 'input_tokens': 1, 'output_tokens': 1, 'started_at': later, 'finished_at': later}, [])
    db.commit()
    view = unit_cards(now=later)
    agent = view['units'][0]['agent']
    assert agent['legacy'] is True and agent['chain'] is None
    assert agent['agent_version'] == 'agent-1' and agent['plan']['id'] == plan['id']
    assert view['timeline_ready'] is True and view['chains'] == []


def test_today_chains_and_has_chain(db, scripted_model):
    service.run_batch(tickers=['MU', 'NVDA'], now=NOW,
                      deps=agent_deps(scripted_model, _ok_responses('MU') + _ok_responses('NVDA')))
    units = {u['ticker']: u for u in unit_cards(now=NOW)['units']}
    heads = {t: execution_repo.chain_head(db, t, SESSION) for t in ('MU', 'NVDA')}
    _decide(units['MU'], 'buy', plan_id=heads['MU']['plan']['id'])            # 决定买入、未成交 → 入场期
    nvda_decision = _decide(units['NVDA'], 'buy', plan_id=heads['NVDA']['plan']['id'])
    legacy_decisions.add_trade({'request_id': str(uuid.uuid4()), 'ticker': 'NVDA', 'side': 'buy', 'quantity': '10',
                       'price': '100.00', 'executed_at': '2026-10-22T15:00:00+00:00',
                       'decision_id': str(nvda_decision['id'])}, now=NOW)       # 成交 → 持仓期
    legacy_decisions.add_trade({'request_id': str(uuid.uuid4()), 'ticker': 'AMD', 'side': 'buy', 'quantity': '5',
                       'price': '90.00', 'executed_at': '2026-10-20T15:00:00+00:00'}, now=NOW)   # 无链的持仓
    reason = '无新信号，复查条件未满足（下一交易日）；' + '补充说明' * 10
    execution_repo.insert_plan_review(db, {
        'ticker': 'MU', 'signal_date': SESSION, 'head_plan_id': heads['MU']['plan']['id'], 'session': SESSION,
        'trigger': 'batch', 'mode': 'entry', 'timeline_version': 'timeline-1',
        'signals': [{'kind': 't1_touched', 'fired': True, 'detail': 'x', 'evidence': []},
                    {'kind': 'new_8k', 'fired': False, 'detail': 'y', 'evidence': []}],
        'outcome': 'maintain', 'decided_by': 'host', 'reason': reason, 'attempt': 1, 'cutoff_at': NOW})
    db.commit()

    view = unit_cards(now=NOW)
    assert view['units'] == []                                     # 两个单元都已决定
    chains = {c['ticker']: c for c in view['chains']}
    assert set(chains) == {'MU', 'NVDA'}
    mu, nvda = chains['MU'], chains['NVDA']
    assert set(mu) == {'ticker', 'signal_date', 'state', 'mode', 'basis', 'version_no', 'head_plan_id',
                       'valid_until', 'pool', 'latest_review', 'signals_today'}
    assert (mu['state'], mu['mode'], mu['version_no']) == ('entry', 'entry', 0)
    assert mu['head_plan_id'] == str(heads['MU']['plan']['id']) and mu['pool'] == 'main'
    assert mu['latest_review']['outcome'] == 'maintain' and mu['latest_review']['decided_by'] == 'host'
    assert mu['latest_review']['reason'] == reason[:40]
    assert mu['signals_today'] == ['t1_touched']
    assert (nvda['state'], nvda['mode'], nvda['latest_review'], nvda['signals_today']) == \
        ('holding', 'holding', None, [])
    holdings = {h['ticker']: h for h in view['holdings']}
    assert holdings['NVDA']['has_chain'] is True and holdings['AMD']['has_chain'] is False
    assert holdings['NVDA']['chain_state'] == 'holding' and holdings['AMD']['chain_state'] is None

    execution_repo.insert_plan_review(db, {                            # 持仓期的链被复查作废
        'ticker': 'NVDA', 'signal_date': SESSION, 'head_plan_id': heads['NVDA']['plan']['id'], 'session': SESSION,
        'trigger': 'batch', 'mode': 'holding', 'timeline_version': 'timeline-1', 'outcome': 'invalidate',
        'decided_by': 'host', 'reason': '失效条件成立', 'attempt': 1, 'cutoff_at': NOW})
    db.commit()
    view = unit_cards(now=NOW)
    assert {c['ticker'] for c in view['chains']} == {'MU'}
    holdings = {h['ticker']: h for h in view['holdings']}
    assert (holdings['NVDA']['has_chain'], holdings['NVDA']['chain_state']) == (True, 'invalidated')
    assert (holdings['AMD']['has_chain'], holdings['AMD']['chain_state']) == (False, None)


def test_agent_view_legacy_only_for_agent1_plans():
    """v2.5 D2 / §10.1: 「agent-1（旧版）」只标在有 agent-1 计划的单元（ok / pass 运行）；之前跳过或出错的
    agent-1 运行没有计划，走「Agent 未给出计划」。"""
    def run(status, version='agent-1'):
        return {'id': uuid.uuid4(), 'status': status, 'reason': None if status in ('ok', 'pass') else 'model_error',
                'model': 'gpt-test', 'agent_version': version, 'output': {}, 'counter': None, 'input_tokens': 0,
                'output_tokens': 0, 'created_at': NOW}
    assert _agent_view(run('ok'), None)['legacy'] is True
    assert _agent_view(run('pass'), None)['legacy'] is True
    for status in ('skipped', 'error', 'rejected'):
        assert 'legacy' not in _agent_view(run(status), None), status
    assert 'legacy' not in _agent_view(run('ok', 'agent-2'), None)


# ---------------------------------------------------------------- v2.5 Task 10: plan updates in the push (D25)

def _revision(db, prev, version_no, *, stop, session, text, trigger='recheck'):
    """Version n of the NVDA chain the way a review writes it: plan, revise run, version row, review row."""
    plan = execution_repo.insert_plan(db, {**prev, 'stop': Decimal(stop)})
    at = datetime.now(UTC)
    review_run = _insert_run(db, mode='review', status='ok', at=at, agent_version='agent-review-1')
    run = _insert_run(db, mode='revise', status='ok', at=at, plan_id=plan['id'],
                      output={'action': 'buy', 'summary': '修订',
                              'changes': [{'field': 'stop', 'text': text, 'refs': ['call:1']}]})
    execution_repo.insert_plan_version(db, {
        'plan_id': plan['id'], 'ticker': 'NVDA', 'signal_date': SESSION, 'version_no': version_no,
        'supersedes_plan_id': prev['id'], 'origin': 'recheck' if trigger == 'recheck' else 'review',
        'agent_run_id': run['id'], 'cutoff_at': at, 'as_of_session': session,
        'diff': [{'field': 'stop', 'before': str(prev['stop']), 'after': stop}], 'schema_version': 'agent-2'})
    execution_repo.insert_plan_review(db, {
        'ticker': 'NVDA', 'signal_date': SESSION, 'head_plan_id': prev['id'], 'session': session,
        'trigger': trigger, 'mode': 'entry', 'timeline_version': 'timeline-1', 'outcome': 'revise',
        'decided_by': 'agent', 'reason': '新 8-K', 'review_agent_run_id': review_run['id'],
        'revision_agent_run_id': run['id'], 'new_plan_id': plan['id'],
        'attempt': 1, 'cutoff_at': at})
    db.commit()
    return plan


def _report_push(db, push, *, created_at='clock_timestamp()', status=None):
    summary = {'push': push, **({'status': status} if status else {})}
    db.execute("INSERT INTO job_reports (task, run_id, status, started_at, finished_at, summary, created_at) "
               f"VALUES ('workbench', %s, 'success', %s, %s, %s::jsonb, {created_at})",
               [str(uuid.uuid4()), NOW, NOW, json.dumps(summary)])
    db.commit()


def test_push_window_includes_recheck_after_last_push(db, scripted_model):
    from investment_assistant.config import NotifyConfig

    service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses()))
    v0 = execution_repo.chain_head(db, 'NVDA', SESSION)['plan']
    v1 = _revision(db, v0, 1, stop='95.5', session=SESSION, text='止损上移到 95.5')     # S 日推送之前
    client = PushClient()
    first = push_node.push(now=NOW, force=False, notify_cfg=NotifyConfig(), send=ports.send, client=client)
    assert first['sent'] is True and first['messages'] == 6                 # 从未推送过 → 只取 S 当日
    text = json.dumps(client.sent, ensure_ascii=False)
    assert '🔁 计划更新 · 2026-10-22' in text and 'NVDA 第 0 → 1 版：止损上移到 95.5' in text
    _report_push(db, first)

    _revision(db, v1, 2, stop='96', session=SESSION, text='止损上移到 96')            # S 日推送之后的重新复查
    client = PushClient()
    nxt = NOW + timedelta(days=1)                                           # 周五 S+1
    second = push_node.push(now=nxt, force=False, notify_cfg=NotifyConfig(), send=ports.send, client=client)
    assert second['sent'] is True and second['messages'] == 6
    text = json.dumps(client.sent, ensure_ascii=False)
    assert '🔁 计划更新 · 2026-10-23' in text
    assert 'NVDA 第 1 → 2 版：止损上移到 96 · 新价位 区间' in text and '止损 96 · T1' in text
    assert '第 0 → 1 版' not in text                                         # S 日推送之前的复查不重复
    assert '计划更新 修订 1 · 作废 0' in text


def test_push_without_updates_sends_five(db):
    from investment_assistant.config import NotifyConfig

    client = PushClient()
    out = push_node.push(now=NOW, force=False, notify_cfg=NotifyConfig(), send=ports.send, client=client)
    assert out['sent'] is True and out['messages'] == 5
    assert '计划更新 修订 0 · 作废 0' in json.dumps(client.sent, ensure_ascii=False)


def test_last_successful_push_at_skips_failed_closed_and_later(db):
    _report_push(db, {'session': '2026-10-21', 'sent': True, 'messages': 5},
                 created_at="'2026-10-21T23:00:00Z'::timestamptz")
    _report_push(db, {'session': '2026-10-22', 'sent': False, 'reason': 'send_failed: x'},
                 created_at="'2026-10-22T23:00:00Z'::timestamptz")
    _report_push(db, {'session': '2026-10-22', 'sent': True, 'messages': 1}, status='skipped',   # 休市消息
                 created_at="'2026-10-22T23:30:00Z'::timestamptz")
    _report_push(db, {'session': '2026-10-23', 'sent': True, 'messages': 5},
                 created_at="'2026-10-23T23:00:00Z'::timestamptz")
    assert workflow_repo.last_successful_push_at(db, before_session=date(2026, 10, 23)) == \
        datetime(2026, 10, 21, 23, 0, tzinfo=UTC)
    assert workflow_repo.last_successful_push_at(db, before_session=date(2026, 10, 26)) == \
        datetime(2026, 10, 23, 23, 0, tzinfo=UTC)
    assert workflow_repo.last_successful_push_at(db, before_session=date(2026, 10, 21)) is None
    _report_push(db, {'session': '2026-10-26', 'sent': True, 'messages': 5,
                      'reviews_until': '2026-10-26T22:59:00+00:00'},
                 created_at="'2026-10-26T23:05:00Z'::timestamptz")
    assert workflow_repo.last_successful_push_at(db, before_session=date(2026, 10, 27)) == \
        datetime(2026, 10, 26, 22, 59, tzinfo=UTC)                       # 读取时刻优先于报告时间


def test_push_window_catches_review_written_while_sending(db, scripted_model, monkeypatch):
    """A recheck written after the push read its reviews but before the run report landed is not lost:
    the window is anchored at the push's read time (summary.push.reviews_until), not job_reports.created_at."""
    from investment_assistant.config import NotifyConfig
    from investment_assistant.notify import notifier

    service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses()))
    v0 = execution_repo.chain_head(db, 'NVDA', SESSION)['plan']
    real_send = notifier.send_notifications

    def send_then_recheck(*args, **kwargs):
        out = real_send(*args, **kwargs)
        _revision(db, v0, 1, stop='95.5', session=SESSION, text='发送期间的重新复查')
        return out

    monkeypatch.setattr(notifier, 'send_notifications', send_then_recheck)
    client = PushClient()
    first = push_node.push(now=NOW, force=False, notify_cfg=NotifyConfig(), send=ports.send, client=client)
    assert first['sent'] is True and first['messages'] == 5 and first['reviews_until']
    assert '发送期间的重新复查' not in json.dumps(client.sent, ensure_ascii=False)
    _report_push(db, first)                                                 # 报告晚于这次重新复查落库

    monkeypatch.setattr(notifier, 'send_notifications', real_send)
    client = PushClient()
    second = push_node.push(now=NOW + timedelta(days=1), force=False, notify_cfg=NotifyConfig(), send=ports.send, client=client)
    text = json.dumps(client.sent, ensure_ascii=False)
    assert second['messages'] == 6 and 'NVDA 第 0 → 1 版：发送期间的重新复查' in text


def test_today_chain_carries_head_cutoff_diff_and_review_conditions(db, scripted_model):
    """§10.1 on Today: the head's generation time and diff, and the latest review's condition verdicts."""
    service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses()))
    v0 = execution_repo.chain_head(db, 'NVDA', SESSION)['plan']
    v1 = _revision(db, v0, 1, stop='95.5', session=SESSION, text='止损上移到 95.5')
    conditions = [{'list': 'invalidation', 'index': 0, 'kind': 'close_below', 'params': {'price': 90},
                   'result': 'not_met', 'detail': '收盘 101 > 90'},
                  {'list': 'review_when', 'index': 0, 'kind': 'next_session', 'params': {},
                   'result': 'met', 'detail': '下一交易日'}]
    review_run = _insert_run(db, mode='review', status='ok', at=datetime.now(UTC), agent_version='agent-review-1')
    execution_repo.insert_plan_review(db, {
        'ticker': 'NVDA', 'signal_date': SESSION, 'head_plan_id': v1['id'], 'session': SESSION,
        'trigger': 'recheck', 'mode': 'entry', 'timeline_version': 'timeline-1', 'conditions': conditions,
        'outcome': 'maintain', 'decided_by': 'agent', 'reason': '论点仍成立', 'review_agent_run_id': review_run['id'],
        'attempt': 1, 'cutoff_at': datetime.now(UTC)})
    db.commit()
    head = execution_repo.chain_head(db, 'NVDA', SESSION)['version']
    chain = unit_cards(now=NOW)['units'][0]['agent']['chain']
    assert chain['version_no'] == 1 and chain['head_plan_id'] == str(v1['id'])
    assert chain['cutoff_at'] == head['cutoff_at']
    assert chain['diff'] == [{'field': 'stop', 'before': str(v0['stop']), 'after': '95.5'}]
    assert chain['latest_review']['outcome'] == 'maintain'
    assert chain['latest_review']['conditions'] == conditions


# ------------------------------------------------- max_attempts reset and rerun (v2.8 D26, migration 028)

@pytest.fixture
def db28(db):          # ``conftest.WORKBENCH_MIGRATIONS`` ends with 029 since v2.9
    return db


def _exhaust_attempts(db, scripted_model):
    for i in range(4):          # three failed batches, the fourth records max_attempts
        service.run_batch(tickers=['NVDA'], now=NOW + timedelta(minutes=10 * i), deps=agent_deps(scripted_model, []))
    runs = analysis_repo.agent_runs_for(db, 'NVDA', SESSION)
    assert [(r['status'], r['reason']) for r in runs][-1] == ('skipped', 'max_attempts') and len(runs) == 4


def _reset(db, request_id=None):
    return analysis_repo.insert_attempt_reset(db, {'ticker': 'NVDA', 'signal_date': SESSION, 'mode': 'propose',
                                                'request_id': request_id or str(uuid.uuid4()), 'origin': 'api'})


def test_reset_clears_attempt_gate(db28, scripted_model):
    _exhaust_attempts(db28, scripted_model)
    _reset(db28)
    summary = service.run_batch(tickers=['NVDA'], now=NOW + timedelta(hours=1),
                                deps=agent_deps(scripted_model, _ok_responses()))
    last = analysis_repo.agent_runs_for(db28, 'NVDA', SESSION)[-1]
    assert last['status'] in ('ok', 'pass') and summary['agent']['runs']['NVDA'] in ('ok', 'pass')   # no longer blocked


def test_attempt_number_keeps_counting(db28, scripted_model):
    _exhaust_attempts(db28, scripted_model)
    _reset(db28)
    for i in range(4):          # after the reset three more failures are allowed, then max_attempts again
        service.run_batch(tickers=['NVDA'], now=NOW + timedelta(hours=1, minutes=i), deps=agent_deps(scripted_model, []))
    runs = analysis_repo.agent_runs_for(db28, 'NVDA', SESSION)
    assert [r['attempt'] for r in runs] == [1, 2, 3, 4, 5, 6, 7, 8]                  # the history is never reset
    assert [(r['status'], r['reason']) for r in runs[4:]][-1] == ('skipped', 'max_attempts')
    assert [r['status'] for r in runs[4:7]] == ['error'] * 3


def test_old_max_attempts_row_does_not_block_after_reset(db28, scripted_model):
    _exhaust_attempts(db28, scripted_model)
    before = len(analysis_repo.agent_runs_for(db28, 'NVDA', SESSION))
    service.run_batch(tickers=['NVDA'], now=NOW + timedelta(minutes=50), deps=agent_deps(scripted_model, []))
    assert len(analysis_repo.agent_runs_for(db28, 'NVDA', SESSION)) == before            # still blocked without a reset
    _reset(db28)
    service.run_batch(tickers=['NVDA'], now=NOW + timedelta(minutes=60), deps=agent_deps(scripted_model, []))
    assert len(analysis_repo.agent_runs_for(db28, 'NVDA', SESSION)) == before + 1


def test_reset_request_id_idempotent(db28, scripted_model):
    _exhaust_attempts(db28, scripted_model)
    request_id = str(uuid.uuid4())
    d = agent_deps(scripted_model, _ok_responses())
    first = analysis_agent_runner.rerun_agent('nvda', SESSION, 'propose', request_id=request_id, origin='run_detail',
                                now=NOW + timedelta(hours=1), deps=d)
    assert first['replayed'] is False and first['run_id'] is not None
    runs = analysis_repo.agent_runs_for(db28, 'NVDA', SESSION)
    assert str(runs[-1]['id']) == first['run_id'] and runs[-1]['status'] in ('ok', 'pass')
    again = analysis_agent_runner.rerun_agent('NVDA', SESSION, 'propose', request_id=request_id, origin='run_detail',
                                now=NOW + timedelta(hours=2), deps=d)
    assert again == {'reset_id': first['reset_id'], 'run_id': None, 'replayed': True}
    assert db28.execute('SELECT count(*) FROM agent_attempt_resets').fetchone()[0] == 1
    assert len(analysis_repo.agent_runs_for(db28, 'NVDA', SESSION)) == len(runs)         # nothing ran twice


def test_a_request_id_already_used_by_another_unit_runs_nothing(db28, scripted_model):
    """``insert_attempt_reset`` answers with the stored row (another unit's) as ``replayed``: no ``run_agent``."""
    _exhaust_attempts(db28, scripted_model)
    request_id = str(uuid.uuid4())
    other = analysis_repo.insert_attempt_reset(db28, {'ticker': 'MU', 'signal_date': SESSION, 'mode': 'propose',
                                                   'request_id': request_id, 'origin': 'api'})
    db28.commit()
    runs = len(analysis_repo.agent_runs_for(db28, 'NVDA', SESSION))
    out = analysis_agent_runner.rerun_agent('NVDA', SESSION, 'propose', request_id=request_id, origin='api',
                              now=NOW + timedelta(hours=1), deps=agent_deps(scripted_model, _ok_responses()))
    assert out == {'reset_id': other['id'], 'run_id': None, 'replayed': True}
    assert len(analysis_repo.agent_runs_for(db28, 'NVDA', SESSION)) == runs


def test_rerun_propose_is_refused_while_a_daily_execution_runs(db28, scripted_model, monkeypatch):
    from investment_assistant.core import locks as daily

    _exhaust_attempts(db28, scripted_model)
    monkeypatch.setattr(daily, 'is_running', lambda: True)
    kwargs = dict(origin='api', now=NOW + timedelta(hours=1), deps=agent_deps(scripted_model, _ok_responses()))
    with pytest.raises(daily.DailyBusy):
        analysis_agent_runner.rerun_agent('NVDA', SESSION, 'propose', request_id=str(uuid.uuid4()), **kwargs)
    assert db28.execute('SELECT count(*) FROM agent_attempt_resets').fetchone()[0] == 0        # nothing was written
    monkeypatch.setattr(daily, 'is_running', lambda: False)
    assert analysis_agent_runner.rerun_agent('NVDA', SESSION, 'propose', request_id=str(uuid.uuid4()), **kwargs)['run_id']


def test_rerun_not_needed_409(db28, scripted_model):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses()))   # an ok run
    kwargs = dict(origin='api', now=NOW + timedelta(hours=1), deps=agent_deps(scripted_model, []))
    with pytest.raises(analysis_agent_runner.AgentConflict, match='not_needed'):
        analysis_agent_runner.rerun_agent('NVDA', SESSION, 'propose', request_id=str(uuid.uuid4()), **kwargs)
    assert db28.execute('SELECT count(*) FROM agent_attempt_resets').fetchone()[0] == 0       # no reset for a unit that did not need it
    with pytest.raises(LookupError):
        analysis_agent_runner.rerun_agent('MU', SESSION, 'propose', request_id=str(uuid.uuid4()), **kwargs)
    with pytest.raises(ValueError):
        analysis_agent_runner.rerun_agent('NVDA', SESSION, 'review', request_id=str(uuid.uuid4()), **kwargs)
    with pytest.raises(ValueError):
        analysis_agent_runner.rerun_agent('NVDA', SESSION, 'propose', request_id='not-a-uuid', **kwargs)
    with pytest.raises(ValueError):
        analysis_agent_runner.rerun_agent('NVDA', SESSION, 'propose', request_id=str(uuid.uuid4()), **{**kwargs, 'origin': 'plan'})
