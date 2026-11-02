"""R23 (spec D15): the screen node's new-member backfill is the same ``registry.run_adapter`` call the collect node and
the Data menu use — scope ``screen_new_members``, the run's frozen switches, one tally per family — and returns the
shape the node has always read (bars / news / earnings per ticker, problems, the SEC listing)."""
from contextlib import contextmanager
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

import investment_assistant.core.tx as core_tx
import investment_assistant.data.collect as collect
from investment_assistant.data import registry
from investment_assistant.workflow import screen_collection

SESSION = date(2026, 10, 28)
NOW = datetime(2026, 10, 28, 22, 30, tzinfo=UTC)


@pytest.fixture
def db(workbench_db, monkeypatch):
    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(core_tx, 'connection', connection)
    return workbench_db


def frame():
    return pd.DataFrame({'Open': 10.0, 'High': 11.0, 'Low': 9.0, 'Close': 10.5, 'Adj Close': 10.5, 'Volume': 1000,
                         'Stock Splits': 0.0}, index=pd.DatetimeIndex(['2026-10-27', '2026-10-28']))


class Filings:
    def discover_one(self, ticker, start, end, **kwargs):
        if ticker == 'BAD':
            raise RuntimeError('SEC down for BAD')
        return [{'ticker': ticker, 'form': '8-K', 'filing_date': '2026-10-27', 'accession_number': f'000{ticker}-26-1',
                 'edgar_url': 'https://www.sec.gov/x', 'items': None, 'accepted_at': None}]


def deps_for(**overrides):
    base = dict(bar_fetcher=lambda *a: frame(),
                news_fetcher=lambda t: [{'title': f'{t} n', 'link': f'https://x/{t}', 'relatedTickers': [t]}],
                earnings_fetcher=lambda t: {'Earnings Date': [date(2026, 11, 20)]}, filing_source=Filings())
    base.update(overrides)
    return core_tx.Deps(**base)


def test_backfill_runs_every_family_through_run_adapter_with_the_screen_scope(db, monkeypatch):
    seen = []
    real = registry.run_adapter

    def spy(adapter, targets, **kwargs):
        seen.append((adapter.id, list(targets), kwargs['scope'], kwargs['features']))
        return real(adapter, targets, **kwargs)
    monkeypatch.setattr(registry, 'run_adapter', spy)
    tallies = {f: collect.Tally(f, 'daily') for f in ('news', 'sec_filings')}

    out = screen_collection.backfill_new_members(
        ['MU', 'NVDA'], session=SESSION, now=NOW, deps=deps_for(), fetched={'NVDA'}, features={'backup_sources': True},
        trigger='scheduled', run_id='daily-r1', news_tally=tallies['news'], sec_tally=tallies['sec_filings'])

    assert [(a, t) for a, t, _, _ in seen] == [('bars', ['MU']), ('news', ['MU', 'NVDA']),
                                               ('earnings', ['MU', 'NVDA']), ('sec_filings', ['MU', 'NVDA'])]
    assert {scope for _, _, scope, _ in seen} == {'screen_new_members'}
    assert all(flags == {'backup_sources': True} for _, _, _, flags in seen)        # the run's frozen switches
    assert out['bars']['NVDA'] == 'collected_in_screen'                              # the range step already took it
    assert out['bars']['MU'] == {'counts': {'inserted': 2, 'unchanged': 0}, 'skipped': []}
    assert out['news']['MU']['inserted'] == 1 and set(out['news']['MU']) >= {'inserted', 'existing', 'fetched', 'skipped'}
    assert out['earnings'] == {'MU': 'inserted', 'NVDA': 'inserted'}
    assert out['problems'] == {'MU': [], 'NVDA': []} and out['filings_error'] is None
    assert out['filings']['by_ticker'] == {'MU': {'rows': 1, 'latest': '2026-10-27'},
                                           'NVDA': {'rows': 1, 'latest': '2026-10-27'}}
    row = tallies['news'].row(trigger='scheduled', run_id='daily-r1', session=SESSION, started_at=NOW, finished_at=NOW)
    assert row['detail']['scope'] == 'screen_new_members' and row['ok_count'] == 2
    assert row['detail']['source_counts'] == {'yfinance': 2}


def test_backfill_failures_are_problems_not_exceptions_and_unavailable_propagates(db, monkeypatch):
    def bars_boom(ticker, start, end):
        raise RuntimeError('rate limited')
    out = screen_collection.backfill_new_members(
        ['MU', 'BAD'], session=SESSION, now=NOW, deps=deps_for(bar_fetcher=bars_boom,
                                                               news_fetcher=lambda t: (_ for _ in ()).throw(RuntimeError('news down')),
                                                               earnings_fetcher=lambda t: (_ for _ in ()).throw(RuntimeError('no calendar'))),
        features={})
    assert out['problems']['MU'] == ['bars: rate limited', 'news: news down']
    assert out['earnings'] == {'MU': 'error: no calendar', 'BAD': 'error: no calendar'}
    assert out['filings']['errors'] == {'BAD': 'SEC down for BAD'} and 'MU' in out['filings']['by_ticker']

    class Down:
        def discover_one(self, *a, **k):
            raise RuntimeError('boom')
    whole = screen_collection.backfill_new_members(['MU'], session=SESSION, now=NOW,
                                                   deps=deps_for(filing_source=Down()), features={})
    assert whole['filings']['errors'] == {'MU': 'boom'}

    @contextmanager
    def no_database():
        raise core_tx.Unavailable('数据库不可达')
        yield
    monkeypatch.setattr(core_tx, '_tx', no_database)
    with pytest.raises(core_tx.Unavailable):
        screen_collection.backfill_new_members(['MU'], session=SESSION, now=NOW, deps=deps_for(), features={})


def test_without_frozen_switches_the_backfill_reads_the_runtime_ones(db, monkeypatch):
    """R20: a backfill with no frozen run state (``features=None``) reads the current switches — a flag with no
    change row is on — instead of ``{}``, which the adapters read as ``backup_sources`` off."""
    import uuid

    from investment_assistant.core import features as core_features

    seen = []
    real = registry.run_adapter

    def spy(adapter, targets, **kwargs):
        seen.append(dict(kwargs['features']))
        return real(adapter, targets, **kwargs)
    monkeypatch.setattr(registry, 'run_adapter', spy)
    screen_collection.backfill_new_members(['MU'], session=SESSION, now=NOW, deps=deps_for())
    assert seen and all(flags == dict.fromkeys(core_features.FLAGS, True) for flags in seen)
    core_features.record_change(db, flag='backup_sources', enabled=False, reason='停备源', request_id=uuid.uuid4())
    db.commit()
    seen.clear()
    screen_collection.backfill_new_members(['MU'], session=SESSION, now=NOW, deps=deps_for())
    assert seen and all(flags['backup_sources'] is False and flags['company_researcher'] is True for flags in seen)
