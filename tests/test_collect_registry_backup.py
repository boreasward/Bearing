"""The daily ``collect`` node runs every fetched family through ``registry.run_adapter`` (R21): the primary source,
the backup when the run's frozen ``backup_sources`` flag is on, the degradation otherwise — and the target detail
says which source answered. Offline fakes; SQL on the disposable schema through migration 031."""
import dataclasses
import uuid
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from test_workbench_service import NOW, SESSION, Sources, _universe_deps, db, seed_watchlist   # noqa: F401

import investment_assistant.data.collect as data_collect
import investment_assistant.data.registry as data_registry
import investment_assistant.workflow.batch as service
from investment_assistant.core import features as core_features
from investment_assistant.data import fetch_policy
from investment_assistant.data.adapters import earnings as earnings_module
from investment_assistant.data.sources import nasdaq


class EmptyBars(Sources):
    """MU's bars come back empty (a fallback kind: the answer is missing, not 'no such ticker')."""

    def bars(self, ticker, start, end_exclusive):
        if ticker == 'MU':
            self.calls.append(('bars', ticker))
            return pd.DataFrame()
        return super().bars(ticker, start, end_exclusive)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(data_collect, 'FETCH_SLEEP', lambda seconds: None)
    monkeypatch.setattr(earnings_module, 'FETCH_SLEEP', lambda seconds: None)


@pytest.fixture
def backup_bars(monkeypatch):
    """Stooq stays disabled (spike), so the bars adapter gets a second source for the mechanism: a fake in the 'stooq'
    slot (the table's source CHECK), so the real D46 fill-only-missing-sessions path runs on its rows."""
    calls = []

    def fetch(target, *, session, start, end, deps, now):
        calls.append(target)
        close = Decimal('42.000000')
        return [{'price_date': session, 'open': close, 'high': close, 'low': close, 'close': close,
                 'adj_close': close, 'volume': 7, 'split_factor': Decimal('1.000000')}]

    original = data_registry.get('bars')
    assert len(original.sources) == 1 and original.sources[0].id == 'yfinance'
    second = dataclasses.replace(original.sources[0], id='stooq', fetch=fetch,
                                 policy=fetch_policy.FetchPolicy(backoff_s=()), note='test backup')
    monkeypatch.setitem(data_registry._BY_ID, 'bars', dataclasses.replace(original, sources=(*original.sources, second)))
    return calls


def fetch_row(conn, family):
    return conn.execute("SELECT status, ok_count, target_count, detail FROM data_fetch_runs WHERE family = %s",
                        (family,)).fetchone()


def items(detail):
    return {item['target']: item for item in detail['targets']}


def test_bars_primary_failure_with_backup_flag_on_tries_the_backup(db, backup_bars):
    seed_watchlist(db, [('NVDA', ['core'])])
    summary = service.run_batch(now=NOW, deps=_universe_deps(EmptyBars(), ['MU']), record_trigger='scheduled')
    assert backup_bars == ['MU']                                       # only the failed target, once
    status, ok, targets, detail = fetch_row(db, 'bars')
    assert status == 'success' and ok == targets
    mu, nvda = items(detail)['MU'], items(detail)['NVDA']
    assert (mu['ok'], mu['source_used']) == (True, 'stooq')
    assert mu['fallback']['tried'] is True and 'MU' in mu['fallback']['reason']
    assert (nvda['source_used'], nvda['fallback']) == ('yfinance', {'tried': False, 'reason': None})
    assert detail['source_counts'] == {'yfinance': targets - 1, 'stooq': 1} and detail['source_counts_complete']
    assert db.execute("SELECT source, volume FROM price_bars WHERE ticker = 'MU'").fetchall() == [('stooq', 7)]
    assert summary['sources']['bars'] == [] and not any('MU' in line for line in summary['errors'])
    assert 'scope' not in detail and detail['trigger'] == 'scheduled'     # the run's own rows carry no scope label


def test_bars_primary_failure_with_backup_flag_off_stays_a_visible_failure(db, backup_bars):
    seed_watchlist(db, [('NVDA', ['core'])])
    core_features.record_change(db, flag='backup_sources', enabled=False, reason='暂停备源', request_id=uuid.uuid4())
    summary = service.run_batch(now=NOW, deps=_universe_deps(EmptyBars(), ['MU']), record_trigger='scheduled')
    assert backup_bars == []                                           # the backup was never asked
    status, ok, targets, detail = fetch_row(db, 'bars')
    assert status == 'partial' and ok == targets - 1
    mu = items(detail)['MU']
    assert (mu['ok'], mu['source_used'], mu['fallback']) == (False, None, {'tried': False, 'reason': None})
    assert detail['source_counts'] == {'yfinance': targets - 1}
    assert db.execute("SELECT count(*) FROM price_bars WHERE ticker = 'MU'").fetchone()[0] == 0
    assert 'MU' in summary['sources']['bars']                          # the node's own summary keeps saying so


def test_a_frozen_flag_state_beats_the_current_flag(db, backup_bars, monkeypatch):
    """The workflow hands the node the run's frozen switches; a flip after that is not read."""
    import investment_assistant.workflow.batch as batch

    seed_watchlist(db, [('NVDA', ['core'])])
    core_features.record_change(db, flag='backup_sources', enabled=False, reason='暂停备源', request_id=uuid.uuid4())
    original = batch.stage_collect
    monkeypatch.setattr(batch, 'stage_collect', lambda state, ctx, acc: original(
        {**state, 'features': dict.fromkeys(core_features.FLAGS, True)}, ctx, acc))
    service.run_batch(now=NOW, deps=_universe_deps(EmptyBars(), ['MU']), record_trigger='scheduled')
    assert backup_bars == ['MU']
    assert items(fetch_row(db, 'bars')[3])['MU']['source_used'] == 'stooq'


def test_earnings_nasdaq_backup_is_active_and_follows_the_flag(db, monkeypatch):
    asked = []

    def nasdaq_date(ticker, *, today):
        asked.append(ticker)
        return {'status': 'known', 'earnings_date': date(2027, 3, 1)}

    monkeypatch.setattr(nasdaq, 'fetch_earnings_date', nasdaq_date)

    class Down(Sources):
        def earnings(self, ticker, **_):
            if ticker == 'MU':
                raise ConnectionError('offline')
            return super().earnings(ticker)

    seed_watchlist(db, [('NVDA', ['core'])])
    service.run_batch(now=NOW, deps=_universe_deps(Down(), ['MU']), record_trigger='scheduled')
    assert asked == ['MU']
    status, ok, targets, detail = fetch_row(db, 'earnings')
    assert status == 'success'
    assert (items(detail)['MU']['source_used'], items(detail)['MU']['fallback']['tried']) == ('nasdaq', True)
    assert items(detail)['NVDA']['source_used'] == 'yfinance'
    assert db.execute("SELECT source FROM earnings_dates WHERE ticker = 'MU'").fetchone() == ('nasdaq',)
