"""The daily screen stage inside the batch, offline end to end.

Universe: U00-U29 fabricated range tickers plus SYNTH on the manual list.
U00-U02 trigger trend_reclaim on S1, U03-U24 form the setup band with
descending relative strength, U25-U29 fail the setup band; QQQ is flat.
"""
import socket
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from investment_assistant.core import tx as core_tx

from investment_assistant.data.calendar import add_trading_days, trading_days_between
from investment_assistant.workbench import repository, service
from investment_assistant.workbench import universe as universe_module

NY = ZoneInfo('America/New_York')
S1 = add_trading_days(datetime(2026, 9, 1).date(), 0)          # Tuesday signal session
HISTORY = 299
FORWARD = 90
DAYS_BACK = [add_trading_days(S1, -(HISTORY - i)) for i in range(HISTORY + 1)]
ALL_DAYS = DAYS_BACK + [add_trading_days(S1, k) for k in range(1, FORWARD + 1)]
UNIVERSE_TICKERS = (*(f'U{i:02d}' for i in range(30)), 'SYNTH')


def evening(day):
    return datetime.combine(day, time(18, 30), tzinfo=NY).astimezone(UTC)


def _trigger_path(start):
    """Uptrend, 3-day dip, reclaim on S1 (closes; pad 0 like the SYNTH scenario)."""
    up = [start + i * 0.25 for i in range(HISTORY - 3)]
    closes = up + [up[-1] - 6] * 3 + [up[-1] + 1]   # reclaim above the pre-dip close on S1
    return dict(zip(DAYS_BACK, closes))


def _series(ticker):
    """{date: (close, pad)} over the whole horizon."""
    if ticker in ('SPY', 'QQQ', 'SMH', 'XLK', 'SYNB'):
        return {d: (500.0 if ticker != 'SYNB' else 100.0, 0.0) for d in ALL_DAYS}
    if ticker == 'SYNTH':
        path = _trigger_path(50.0)
        tail = path[S1]
        for k in range(1, FORWARD + 1):
            tail += 0.5
            path[add_trading_days(S1, k)] = tail
        return {d: (c, 0.0) for d, c in path.items()}
    if ticker in ('U00', 'U01', 'U02'):
        path = _trigger_path({'U00': 50.0, 'U01': 60.0, 'U02': 70.0}[ticker])
        tail = path[S1]
        for k in range(1, FORWARD + 1):
            if ticker == 'U00':      # drops out of every screen condition after S1
                path[add_trading_days(S1, k)] = path[S1] * 0.85
            else:
                tail += 0.5
                path[add_trading_days(S1, k)] = tail
        return {d: (c, 0.0) for d, c in path.items()}
    i = int(ticker[1:])
    if 3 <= i <= 24:                 # setup band: uptrend, pad 2% of close -> ATR% ~4%, RS descending
        step = 0.30 - 0.01 * (i - 3)
        closes = [100.0 + step * k for k in range(len(ALL_DAYS))]
        series = {d: (c, c * 0.02) for d, c in zip(ALL_DAYS, closes)}
        if ticker == 'U05':          # one big day: setup kept, abnormal_move watch candidate on S1
            series[S1] = (series[S1][0] * 1.05, series[S1][1])
        return series
    closes = [100.0 + 0.1 * k for k in range(len(ALL_DAYS))]   # U25-U29: ATR% far below the band
    return {d: (c, c * 0.001) for d, c in zip(ALL_DAYS, closes)}


class ScreenSources:
    """Serves only what was 'published' by the requested end date."""

    def __init__(self, *, fail=(), qqq_last=None):
        self.fail = set(fail)
        self.qqq_last = qqq_last

    def bars(self, ticker, start, end_exclusive):
        end = end_exclusive - timedelta(days=1)
        if ticker in self.fail:
            raise RuntimeError(f'no bars returned for {ticker} {start}..{end}')
        series = _series(ticker)
        if ticker == 'QQQ' and self.qqq_last is not None:
            series = {d: v for d, v in series.items() if d <= self.qqq_last}
        rows = [(d, c, p) for d, (c, p) in series.items() if start <= d <= end]
        if not rows:
            raise RuntimeError(f'no bars returned for {ticker} {start}..{end}')
        index = pd.DatetimeIndex([pd.Timestamp(d, tz='America/New_York') for d, _, _ in rows])
        closes = [c for _, c, _ in rows]
        pads = [p for _, _, p in rows]
        return pd.DataFrame({'Open': closes, 'High': [c + p for c, p in zip(closes, pads)],
                             'Low': [c - p for c, p in zip(closes, pads)], 'Close': closes,
                             'Adj Close': closes, 'Volume': [1_000_000] * len(rows),
                             'Stock Splits': [0.0] * len(rows)}, index=index)

    def news(self, ticker):
        return []

    def discover(self, tickers, start, end):
        return []


def make_deps(sources=None, *, universe=None, earnings=None):
    s = sources or ScreenSources()
    return service.Deps(bar_fetcher=s.bars, news_fetcher=s.news, filing_source=s,
                        earnings_fetcher=earnings or (lambda t: {'Earnings Date': []}),
                        universe=universe if universe is not None else
                        universe_module.Universe('test', S1, 'test', UNIVERSE_TICKERS))


@pytest.fixture
def offline(workbench_db, monkeypatch):
    """Database via the disposable schema; any other network connection fails the test."""
    real_connect = socket.socket.connect

    def guarded(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ('127.0.0.1', '::1', 'localhost'):
            raise AssertionError(f'network access attempted: {address}')
        return real_connect(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', guarded)

    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(core_tx, 'connection', connection)
    return workbench_db


def seed_watchlist(conn, rows=(('SYNTH', ['core']),)):
    for ticker, tags in rows:
        conn.execute("INSERT INTO watchlist_items (ticker, status, tags) VALUES (%s, 'active', %s)",
                     [ticker, tags])
    conn.commit()


def stored_results(conn, session, *, now):
    run = repository.latest_screen_run(conn, session=session)
    assert run is not None
    return repository.screen_results(conn, run['id'])


def test_screen_selects_and_extends_pool(offline):
    seed_watchlist(offline)
    batch = service.run_batch(now=evening(S1), deps=make_deps())
    assert batch['errors'] == []
    screen = batch['screen']
    assert screen['status'] == 'success'
    assert screen['selected'][:3] == ['U00', 'U01', 'U02']
    assert len(screen['selected']) == 20
    results = stored_results(offline, S1, now=evening(S1))
    assert len(results) == 30                                   # every range ticker evaluated
    assert all(r['ticker'] != 'SYNTH' for r in results)         # manual list is never screened
    rows = offline.execute("SELECT ticker, tier, source FROM pool_tiers WHERE source = 'screen'").fetchall()
    assert len(rows) == 20 and all(r[1] == 'extended' for r in rows)
    assert batch['pool']['screen'] == 20


def test_valid_screen_plan_is_decidable(offline):
    seed_watchlist(offline)
    service.run_batch(now=evening(S1), deps=make_deps())
    view = service.unit_cards(now=evening(S1))
    u00 = next(u for u in view['units'] if u['ticker'] == 'U00')
    assert u00['source'] == 'screen' and u00['plan'] is not None and u00['plan']['status'] == 'ok'
    ctx = u00['plan']['inputs']['context']
    assert ctx['source'] == 'screen' and ctx['screen']['rank'] == 1 and ctx['screen']['group'] == 'trigger'
    u05 = next(u for u in view['extended_units'] if u['ticker'] == 'U05')
    assert u05['plan'] is None and u05['proposed_action'] == 'watch'
    with core_tx.connection() as conn:
        groups = service._session_units(conn, S1, now=evening(S1))
    assert any(u['ticker'] == 'U00' for u in groups['main_buy'])
    assert not any(u['ticker'] == 'U05' for u in groups['main_buy'])


def test_invalid_screen_plan_stays_read_only(offline):
    class FlippingEarnings:
        """First fetch says the date is far away, later fetches move it inside plan validity."""
        def __init__(self, first, then):
            self.first, self.then, self.calls = first, then, {}

        def __call__(self, ticker):
            n = self.calls.get(ticker, 0)
            self.calls[ticker] = n + 1
            day = self.first if n == 0 else self.then
            return {'Earnings Date': [day.isoformat()]}

    seed_watchlist(offline)
    deps = make_deps(earnings=FlippingEarnings(add_trading_days(S1, 20), add_trading_days(S1, 2)))
    service.run_batch(now=evening(S1), deps=deps)
    view = service.unit_cards(now=evening(S1))
    assert not any(u['ticker'] == 'U01' for u in view['units'])
    u01 = next(u for u in view['extended_units'] if u['ticker'] == 'U01')
    assert u01['plan'] is not None and u01['plan']['status'] == 'invalid'
    assert 'earnings_in_validity' in u01['plan']['invalid_reasons']


def test_retention_and_eviction(offline):
    seed_watchlist(offline)
    service.run_batch(now=evening(S1), deps=make_deps())
    for k in range(1, 10):                                     # S1+1 .. S1+9: U00 stays a member
        batch = service.run_batch(now=evening(add_trading_days(S1, k)), deps=make_deps())
        assert 'U00' in batch['screen']['members']
        assert 'U00' not in batch['screen']['removed']
    batch = service.run_batch(now=evening(add_trading_days(S1, 10)), deps=make_deps())
    assert 'U00' not in batch['screen']['members']
    assert 'U00' in batch['screen']['removed']
    assert 'U00' not in batch['screen']['evicted']              # expired, not crowded out
    entry = repository.pool_entry_at(offline, 'U00', add_trading_days(S1, 10),
                                     cutoff_at=evening(add_trading_days(S1, 11)))
    assert entry['tier'] == 'removed' and entry['source'] == 'screen'


def test_rerun_same_session_is_stable(offline):
    seed_watchlist(offline)
    deps = make_deps()
    first = service.run_batch(now=evening(S1), deps=deps)
    tiers = offline.execute('SELECT count(*) FROM pool_tiers').fetchone()[0]
    second = service.run_batch(now=evening(S1), deps=deps)
    runs = offline.execute('SELECT count(*) FROM screen_runs').fetchone()[0]
    assert runs == 2
    assert offline.execute('SELECT count(*) FROM pool_tiers').fetchone()[0] == tiers
    assert second['screen']['selected'] == first['screen']['selected']


def test_universe_fetch_failures_are_skips_not_filters(offline):
    seed_watchlist(offline)
    failed = {f'U{i:02d}' for i in range(25, 30)}
    deps = make_deps(ScreenSources(fail=failed))
    batch = service.run_batch(now=evening(S1), deps=deps)
    assert batch['screen']['status'] == 'success'
    for t in failed:
        assert t in batch['sources']['bars']
        assert t in batch['screen']['skips']
    assert not (failed & set(batch['screen']['selected']))
    assert batch['screen']['counts']['no_setup'] == 0          # they were skipped, not filtered


def test_collect_returns_failure_reasons(offline):
    from investment_assistant.workbench import pool as pool_module
    from investment_assistant.workbench import screening

    class RateLimitedSources(ScreenSources):
        def bars(self, ticker, start, end_exclusive):
            if ticker == 'XXX':
                raise RuntimeError('rate limited')
            return super().bars(ticker, start, end_exclusive)

    seed_watchlist(offline)
    deps = make_deps(RateLimitedSources(),
                     universe=universe_module.Universe('test', S1, 'test', ('XXX', 'U00')))
    # run_stage reads the benchmark from the store; outside run_batch nothing collected QQQ yet.
    from investment_assistant.workbench import collect
    collect.bars_one('QQQ', session=S1, now=evening(S1), deps=deps,
                     lookback_days=screening.COLLECT_LOOKBACK_DAYS)
    # v2.8 D4: the range is collected by the collect node (``collect_range``); run_stage only reads and carries the outcomes
    fetched, failures, reasons = screening.collect_range(core_tx.connection, session=S1, now=evening(S1),
                                                          targets=['XXX', 'U00'], deps=deps)
    assert (fetched, failures, reasons) == ({'U00'}, ['XXX'], {'XXX': 'rate limited'})
    stage = screening.run_stage(session=S1, now=evening(S1),
                                manual=pool_module.Pool(core=('SYNTH',), extended=(), sectors={}),
                                held=[], deps=deps, run_id=None, fetched=fetched, fetch_reasons=reasons)
    assert stage['status'] == 'success'
    assert stage['fetch_reasons'] == {'XXX': 'rate limited'}
    assert stage['fetch_failures'] == ['XXX']


def test_universe_missing_carries_members(offline):
    seed_watchlist(offline)
    deps = make_deps()
    service.run_batch(now=evening(S1), deps=deps)
    baseline = service.run_batch(now=evening(add_trading_days(S1, 1)), deps=deps)

    def broken_loader():
        raise universe_module.UniverseError('config/universes/missing.json: cannot read')

    day2 = add_trading_days(S1, 2)
    batch = service.run_batch(now=evening(day2),
                              deps=service.Deps(bar_fetcher=deps.bar_fetcher, news_fetcher=deps.news_fetcher,
                                                filing_source=deps.filing_source,
                                                earnings_fetcher=deps.earnings_fetcher,
                                                universe_loader=broken_loader))
    screen = batch['screen']
    assert screen['status'] == 'error' and 'universe' in screen['error']
    assert sorted(screen['members']) == sorted(baseline['screen']['members'])   # retention only, no new picks
    assert any(str(e).startswith('screen:') for e in batch['errors'])
    assert 'regime_universe_missing' in batch['errors']
    error_runs = offline.execute("SELECT count(*) FROM screen_runs WHERE status = 'error'").fetchone()[0]
    assert error_runs == 1

    # QQQ losing its bar fails the whole run the same way: benchmark_missing, members carried.
    day3 = add_trading_days(S1, 3)
    batch = service.run_batch(now=evening(day3), deps=make_deps(ScreenSources(qqq_last=add_trading_days(S1, 1))))
    assert batch['screen']['error'] == 'benchmark_missing'
    assert sorted(batch['screen']['members']) == sorted(baseline['screen']['members'])
    assert 'regime_universe_missing' not in batch['errors']    # the universe itself loaded fine


def _regime_sample(monkeypatch):
    from investment_assistant.workbench import regime
    seen = []
    real = regime.snapshot

    def spy(**kwargs):
        seen.append(sorted(kwargs['pool_bars']))
        return real(**kwargs)
    monkeypatch.setattr(regime, 'snapshot', spy)
    return seen


def test_regime_breadth_sample_on_screen_errors(offline, monkeypatch):
    seed_watchlist(offline)
    deps = make_deps()
    service.run_batch(now=evening(S1), deps=deps)
    seen = _regime_sample(monkeypatch)

    def broken_loader():
        raise universe_module.UniverseError('config/universes/missing.json: cannot read')

    # Unreadable universe: the manual pool alone, not the carried screen members.
    day1 = add_trading_days(S1, 1)
    batch = service.run_batch(now=evening(day1),
                              deps=service.Deps(bar_fetcher=deps.bar_fetcher, news_fetcher=deps.news_fetcher,
                                                filing_source=deps.filing_source,
                                                earnings_fetcher=deps.earnings_fetcher,
                                                universe_loader=broken_loader))
    assert batch['screen']['members'] and seen[-1] == ['SYNTH']
    # Missing QQQ: the universe loaded, so breadth keeps the universe sample.
    day2 = add_trading_days(S1, 2)
    batch = service.run_batch(now=evening(day2), deps=make_deps(ScreenSources(qqq_last=S1)))
    assert batch['screen']['error'] == 'benchmark_missing'
    assert set(seen[-1]) == set(UNIVERSE_TICKERS)


class CountingSources(ScreenSources):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.news_calls = []

    def news(self, ticker):
        self.news_calls.append(ticker)
        return []


def _buy(conn, ticker, day):
    import uuid
    from decimal import Decimal
    repository.insert_trade(conn, {
        'request_id': uuid.uuid4(), 'ticker': ticker, 'side': 'buy', 'quantity': Decimal('10'),
        'price': Decimal('100'), 'fee': Decimal('0'),
        'executed_at': datetime.combine(day, time(15, 0), tzinfo=NY), 'decision_id': None, 'note': None},
        'h', recorded_at=datetime.combine(day, time(16, 0), tzinfo=NY))
    conn.commit()


def test_off_pool_holding_stays_out_of_screen(offline):
    # SYNB is held but never screened: it joins the batch as a holding, not as a screen member.
    seed_watchlist(offline)
    _buy(offline, 'SYNB', add_trading_days(S1, -5))
    sources = CountingSources()
    batch = service.run_batch(now=evening(S1), deps=make_deps(sources))
    assert 'SYNB' not in batch['screen']['members']
    assert batch['pool']['holdings'] == 1 and 'SYNB' in batch['tickers']
    assert offline.execute("SELECT count(*) FROM pool_tiers WHERE ticker = 'SYNB'").fetchone()[0] == 0
    assert sources.news_calls.count('SYNB') == 1


def test_held_screen_member_runs_once(offline):
    # U00 is picked on S1, bought, and drops out of every condition: the position keeps it
    # a member past the retention window, and the batch runs it once, not again as a holding.
    seed_watchlist(offline)
    service.run_batch(now=evening(S1), deps=make_deps())
    _buy(offline, 'U00', add_trading_days(S1, 1))
    sources = CountingSources()
    batch = service.run_batch(now=evening(add_trading_days(S1, 10)), deps=make_deps(sources))
    assert 'U00' in batch['screen']['members'] and 'U00' not in batch['screen']['removed']
    assert batch['pool']['holdings'] == 0
    assert sources.news_calls.count('U00') == 1


def test_screen_view_defaults_to_newest_run(offline):
    # Before the next batch runs, the latest closed session has no run yet: show the last one.
    seed_watchlist(offline)
    service.run_batch(now=evening(S1), deps=make_deps())
    report = service.screen_view(now=evening(add_trading_days(S1, 1)))['report']
    assert report is not None and report['session'] == S1.isoformat()
    assert service.screen_view(now=evening(S1), session=add_trading_days(S1, 1))['report'] is None


def test_universe_stale_flag(offline):
    seed_watchlist(offline)
    stale = universe_module.Universe('test', S1 - timedelta(days=121), 'test', UNIVERSE_TICKERS)
    batch = service.run_batch(now=evening(S1), deps=make_deps(universe=stale))
    assert 'universe_stale' in batch['screen']['flags']
    assert batch['screen']['status'] == 'success' and len(batch['screen']['selected']) == 20


def test_manual_source_flip(offline):
    seed_watchlist(offline)
    deps = make_deps()
    service.run_batch(now=evening(S1), deps=deps)
    day1 = add_trading_days(S1, 1)
    offline.execute("INSERT INTO watchlist_items (ticker, status, tags) VALUES ('U03', 'active', '{}')")
    offline.commit()
    service.run_batch(now=evening(day1), deps=deps)
    entry = repository.pool_entry_at(offline, 'U03', day1, cutoff_at=evening(day1) + timedelta(hours=1))
    assert entry == {'tier': 'extended', 'sector_etf': None, 'source': 'watchlist'}
    day2 = add_trading_days(S1, 2)
    offline.execute("DELETE FROM watchlist_items WHERE ticker = 'U03'")
    offline.commit()
    service.run_batch(now=evening(day2), deps=deps)
    entry = repository.pool_entry_at(offline, 'U03', day2, cutoff_at=evening(day2) + timedelta(hours=1))
    assert entry == {'tier': 'extended', 'sector_etf': None, 'source': 'screen'}


def test_explicit_tickers_skip_screen(offline):
    seed_watchlist(offline)
    batch = service.run_batch(now=evening(S1), tickers=['SYNTH'], deps=make_deps())
    assert batch['screen'] == {'status': 'skipped', 'reason': 'explicit_pool'}
    assert offline.execute('SELECT count(*) FROM screen_runs').fetchone()[0] == 0


class PushSpy:
    def __init__(self):
        self.payloads = []

    def send(self, channel, payload):
        self.payloads.append(payload)
        return 'ok'


def test_force_push_reads_screen_from_db(offline, monkeypatch):
    from investment_assistant.config import NotifyConfig
    seed_watchlist(offline)
    service.run_batch(now=evening(S1), deps=make_deps())
    spy = PushSpy()
    out = service.push(now=evening(S1) + timedelta(hours=1), force=True, summary=None,
                       notify_cfg=NotifyConfig(), client=spy)
    assert out['sent'] is True
    pushed = '\n'.join(str(p) for p in spy.payloads)
    assert 'U00' in pushed
    assert len(spy.payloads) == 5


def test_replay_is_read_only(offline, monkeypatch):
    seed_watchlist(offline)
    deps = make_deps()
    service.run_batch(now=evening(S1), deps=deps)
    service.run_batch(now=evening(add_trading_days(S1, 1)), deps=deps)
    tables = ('screen_runs', 'screen_results', 'pool_tiers', 'trade_plans', 'signal_candidates')
    before = {t: offline.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in tables}
    monkeypatch.setattr(universe_module, 'load',
                        lambda: universe_module.Universe('test', S1, 'test', UNIVERSE_TICKERS))
    reports = service.replay_screen(now=evening(add_trading_days(S1, 1)), sessions=2)['reports']
    assert [r['session'] for r in reports] == [add_trading_days(S1, 1).isoformat(), S1.isoformat()]
    s1_run = repository.latest_screen_run(offline, session=S1)
    stored_selected = [r['ticker'] for r in repository.screen_results(offline, s1_run['id']) if r['selected']]
    assert reports[1]['selected'] == stored_selected
    assert all(r.get('note') == '财报日用当前已知值，非当时时点值' for r in reports)
    after = {t: offline.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in tables}
    assert after == before


def test_replay_bounds(offline):
    for n in (0, 21):
        with pytest.raises(ValueError):
            service.replay_screen(now=evening(S1), sessions=n)


def test_report_full_keeps_all_setups(offline):
    from investment_assistant.workbench import screening
    seed_watchlist(offline)
    service.run_batch(now=evening(S1), deps=make_deps())
    with core_tx.connection() as conn:
        brief = screening.report(conn, S1, now=evening(S1))
        full = screening.report(conn, S1, now=evening(S1), full=True)
    assert len(brief['setups']) == 10 and brief['setups_more'] == len(full['setups']) - 10
    assert len(full['setups']) > 10 and full['setups_more'] == 0
    assert 'results' in full and 'results' not in brief


def _small_universe():
    """SYNTH plus six trigger stocks, all of which get valid plans (locked members)."""
    return universe_module.Universe('test', S1, 'test', ('U00', 'U01', 'U02', 'U03', 'U04', 'U05', 'SYNTH'))


def _all_six_trigger(monkeypatch):
    """The default series makes U03-U05 setup stocks; this test needs all six to trigger
    so each gets a valid plan and counts as a locked screen member the next day."""
    real_series = _series

    def trigger_series(ticker):
        if ticker in ('U03', 'U04', 'U05'):
            path = _trigger_path({'U03': 80.0, 'U04': 90.0, 'U05': 95.0}[ticker])
            tail = path[S1]
            for k in range(1, FORWARD + 1):
                tail += 0.5
                path[add_trading_days(S1, k)] = tail
            return {d: (c, 0.0) for d, c in path.items()}
        return real_series(ticker)

    monkeypatch.setattr(sys.modules[__name__], '_series', trigger_series)


def test_locked_over_pool_cap_keeps_batch_alive(offline, monkeypatch):
    _all_six_trigger(monkeypatch)
    # Day one: 94 manual + 6 trigger picks (cap 6) -> six locked picks with valid plans.
    # Day two: one more manual row (95 total, hard cap 5) -> six locked picks no longer
    # fit; the pool cap wins over "locked never crowded out" and the batch survives (spec §9.3).
    seed_watchlist(offline, rows=[('SYNTH', ['core'])] + [(f'X{i:02d}', []) for i in range(93)])
    deps = make_deps(universe=_small_universe())
    first = service.run_batch(now=evening(S1), deps=deps)
    assert first['screen']['status'] == 'success' and first['pool']['screen'] == 6
    # trade_plans.created_at uses the database clock; pin it to the test timeline
    # so day two's locked-member lookup can see day one's plans.
    offline.execute("UPDATE trade_plans SET created_at = %s", [evening(S1)])
    offline.commit()

    offline.execute("INSERT INTO watchlist_items (ticker, status, tags) VALUES ('X93', 'active', '{}')")
    offline.commit()
    batch = service.run_batch(now=evening(add_trading_days(S1, 1)), deps=deps)
    assert batch['screen']['status'] == 'success'
    assert 'screen_locked_over_cap' in batch['screen']['flags']
    assert 'screen_locked_over_pool_cap' in batch['screen']['flags']
    assert batch['pool']['screen'] == 5                       # 100 - 95 manual
    assert len(batch['screen']['members']) == 5
    assert len(batch['screen']['evicted']) == 1               # the sixth locked pick
    active = {t for t, row in repository.pool_latest(offline).items() if row['tier'] != 'removed'}
    assert len(active) == 100
    assert batch['errors'] == []


def test_error_path_carryover_respects_pool_cap(offline, monkeypatch):
    _all_six_trigger(monkeypatch)
    # A failed screen after a full manual pool: carryover members must be capped
    # against MAX_POOL too, not just MAX_MEMBERS.
    seed_watchlist(offline, rows=[('SYNTH', ['core'])] + [(f'X{i:02d}', []) for i in range(93)])
    deps = make_deps(universe=_small_universe())
    service.run_batch(now=evening(S1), deps=deps)
    offline.execute("UPDATE trade_plans SET created_at = %s", [evening(S1)])
    offline.commit()

    offline.execute("INSERT INTO watchlist_items (ticker, status, tags) VALUES ('X93', 'active', '{}')")
    offline.commit()

    def broken_loader():
        raise universe_module.UniverseError('missing file')

    day1 = add_trading_days(S1, 1)
    batch = service.run_batch(now=evening(day1),
                              deps=service.Deps(bar_fetcher=deps.bar_fetcher, news_fetcher=deps.news_fetcher,
                                                filing_source=deps.filing_source,
                                                earnings_fetcher=deps.earnings_fetcher,
                                                universe_loader=broken_loader))
    assert batch['screen']['status'] == 'error'
    assert len(batch['screen']['members']) <= 5               # 100 - 95 manual
    assert any(str(e).startswith('screen:') for e in batch['errors'])
    active = {t for t, row in repository.pool_latest(offline).items() if row['tier'] != 'removed'}
    assert len(active) <= 100
