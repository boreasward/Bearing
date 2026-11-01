"""Watchlist boundaries: no implicit defaults, identity guessing, or ingestion on listing."""
import investment_assistant.data.repo as data_repo
from contextlib import nullcontext
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from investment_assistant.services import watchlist as wl


def test_paused_us_symbols_do_not_return_from_config(monkeypatch):
    monkeypatch.setattr(wl, 'watchlist_rows', lambda: [{'ticker': 'MU', 'status': 'paused'}])
    assert wl.current_watchlist() == []


def test_configured_empty_list_stays_empty(monkeypatch):
    monkeypatch.setenv('INVESTMENT_ASSISTANT_DATABASE_URL', 'test-only')
    monkeypatch.setattr(wl, 'connect', lambda _: nullcontext(object()))
    monkeypatch.setattr(wl, 'list_watchlist_items', lambda _: [])
    assert wl.watchlist_rows() == []
    assert wl.current_watchlist() == []


def test_listing_flags_non_us_rows_and_keeps_them_out_of_jobs(monkeypatch):
    rows = [{'ticker': 'MU', 'status': 'active'}, {'ticker': '600519.SH', 'status': 'active'}]  # invalid US code
    monkeypatch.setenv('INVESTMENT_ASSISTANT_DATABASE_URL', 'test-only')
    monkeypatch.setattr(wl, 'connect', lambda _: nullcontext(object()))
    monkeypatch.setattr(wl, 'list_watchlist_items', lambda _: rows)
    listed = wl.watchlist_rows()
    assert listed[0] == rows[0]
    assert listed[1]['market'] is None and listed[1]['identity_error']
    assert wl.current_watchlist() == ['MU']


def test_watchlist_rows_has_no_market_filter():
    import inspect
    assert 'market' not in inspect.signature(wl.watchlist_rows).parameters


@pytest.mark.parametrize('ticker', ['000300.SH', '600519.SZ', '123456', 'CN:index:000300.SH', 'garbage value', None, True])
def test_invalid_or_ambiguous_identity_cannot_be_saved(monkeypatch, ticker):
    monkeypatch.setenv('INVESTMENT_ASSISTANT_DATABASE_URL', 'test-only')
    monkeypatch.setattr(wl, 'connect', lambda _: nullcontext(object()))
    with pytest.raises(ValueError):
        wl.add_watchlist_item({'ticker': ticker})


def _enrich_env(monkeypatch, *, read_rows, states, latest_earnings):
    from investment_assistant.data import repo as us_repo
    # Repository consumers use their owning layers below.
    monkeypatch.setenv('INVESTMENT_ASSISTANT_DATABASE_URL', 'test-only')
    monkeypatch.setattr(wl, 'connect', lambda _: nullcontext(object()))
    monkeypatch.setattr(wl, 'list_watchlist_items', lambda _: [{'ticker': 'MU', 'status': 'active'}])
    monkeypatch.setattr(us_repo, 'read_rows', lambda *a, **kw: read_rows)
    monkeypatch.setattr(us_repo, 'states', lambda *a: states)
    monkeypatch.setattr(data_repo, 'latest_earnings', lambda conn, ticker, *, cutoff_at: latest_earnings)


def test_us_enrichment_retains_success_identity_and_uses_duration_end_not_later_instant(monkeypatch):
    _enrich_env(monkeypatch,
                read_rows=[
                    {'period_kind': 'duration', 'start': '2026-05-30', 'end': '2026-09-03', 'metric': 'revenue'},
                    {'period_kind': 'instant', 'end': '2026-10-01', 'metric': 'shares_outstanding'}],
                states={'financials': {'status': 'failed', 'last_success_details': {'identity': {'name': 'Micron Technology'}}}},
                latest_earnings=None)
    result = wl.watchlist_rows(enriched=True)[0]
    assert result['market'] == 'US' and 'board' not in result
    assert result['name'] == 'Micron Technology'
    assert result['latest_period'] == '2026-09-03'
    assert result['datasets']['financials']['status'] == 'failed'
    assert result['next_event'] is None


def test_next_event_is_upcoming_earnings(monkeypatch):
    from datetime import date, timedelta
    day = date.today() + timedelta(days=10)
    _enrich_env(monkeypatch, read_rows=[], states={},
                latest_earnings={'earnings_date': day, 'status': 'confirmed'})
    result = wl.watchlist_rows(enriched=True)[0]
    assert result['next_event'] == {'date': day.isoformat(), 'kind': 'earnings', 'title': None}


@pytest.mark.parametrize('earnings', [{'earnings_date': date.today() - timedelta(days=1), 'status': 'confirmed'}, None])
def test_past_or_missing_earnings_gives_no_event(monkeypatch, earnings):
    _enrich_env(monkeypatch, read_rows=[], states={}, latest_earnings=earnings)
    result = wl.watchlist_rows(enriched=True)[0]
    assert result['next_event'] is None


@pytest.mark.parametrize('ticker', ['AAPL.US', 'TSLA.SH', 'ABC.DEF'])
def test_unsupported_us_identity_rejected_before_write_and_resolve(monkeypatch, ticker):
    monkeypatch.setenv('INVESTMENT_ASSISTANT_DATABASE_URL', 'test-only')
    monkeypatch.setattr(wl, 'connect', lambda _: pytest.fail('invalid identity opened write connection'))
    with pytest.raises(ValueError): wl.add_watchlist_item({'ticker': ticker})
    with pytest.raises(ValueError): wl.resolve_candidates(ticker)


def test_valid_share_class_supported_and_non_us_codes_rejected():
    assert wl.validated_symbol('brk.b') == 'BRK.B'
    for ticker in ('600519.SH', '688008', '000001.SZ'):  # invalid US codes
        with pytest.raises(ValueError):
            wl.validated_symbol(ticker)


def test_resolve_returns_a_single_us_candidate():
    assert wl.resolve_candidates(' mu ') == [{'symbol': 'MU', 'market': 'US', 'asset_type': 'stock'}]


def test_legacy_invalid_rows_remain_listed_and_deletable_but_not_active_jobs(monkeypatch):
    rows = [{'ticker': ticker, 'status': 'active'} for ticker in ['MU', 'AAPL.US', 'TSLA.SH', 'ABC.DEF', '600519.SH']]
    monkeypatch.setenv('INVESTMENT_ASSISTANT_DATABASE_URL', 'test-only')
    monkeypatch.setattr(wl, 'connect', lambda _: nullcontext(None))
    monkeypatch.setattr(wl, 'list_watchlist_items', lambda _: rows)
    listed = wl.watchlist_rows(enriched=True)
    assert len(listed) == 5
    assert [r['ticker'] for r in listed if r.get('identity_error')] == ['AAPL.US', 'TSLA.SH', 'ABC.DEF', '600519.SH']
    assert wl.current_watchlist() == ['MU']
    deleted = []
    monkeypatch.setattr(wl, 'db_delete_watchlist_item', lambda conn, ticker: deleted.append(ticker) or {'ticker': ticker})
    assert wl.delete_watchlist_item('AAPL.US') == {'ticker': 'AAPL.US'}
    assert deleted == ['AAPL.US']
