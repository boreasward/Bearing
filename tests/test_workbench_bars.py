from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from investment_assistant.workbench import bars, repository

T0 = datetime(2026, 10, 22, 22, 30, tzinfo=UTC)


def frame(rows):
    index = pd.DatetimeIndex([pd.Timestamp(d, tz='America/New_York') for d, *_ in rows])
    data = [r[1:] for r in rows]
    return pd.DataFrame(data, index=index,
                        columns=['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume', 'Stock Splits'])


def test_normalize_keeps_split_adjusted_ohlc_and_adj_close():
    rows, skipped = bars.normalize_history(frame([
        ('2026-10-20', 100.0, 102.0, 99.0, 101.0, 100.5, 1000, 0.0),
        ('2026-10-21', 101.0, 103.0, 100.0, 102.1234567, 101.6, 1200, 4.0),
    ]))
    assert skipped == []
    assert rows[0] == {'price_date': date(2026, 10, 20), 'open': Decimal('100.000000'),
                       'high': Decimal('102.000000'), 'low': Decimal('99.000000'),
                       'close': Decimal('101.000000'), 'adj_close': Decimal('100.500000'),
                       'volume': 1000, 'split_factor': Decimal('1.000000')}
    assert rows[1]['close'] == Decimal('102.123457')
    assert rows[1]['split_factor'] == Decimal('4.000000')


def test_normalize_skips_incomplete_rows_instead_of_filling():
    rows, skipped = bars.normalize_history(frame([
        ('2026-10-20', 100.0, 102.0, 99.0, float('nan'), 100.5, 1000, 0.0),
        ('2026-10-21', 101.0, 103.0, 100.0, 102.0, 101.6, 1200, float('nan')),
    ]))
    assert skipped == [date(2026, 10, 20)]
    assert [r['price_date'] for r in rows] == [date(2026, 10, 21)]
    assert rows[0]['split_factor'] == Decimal('1.000000')


def test_fetch_history_requests_unadjusted_prices_with_actions():
    calls = []

    def fetcher(ticker, start, end):
        calls.append((ticker, start, end))
        return frame([('2026-10-21', 1, 1, 1, 1, 1, 1, 0)])

    out = bars.fetch_history('nvda', date(2026, 10, 1), date(2026, 10, 21), fetcher=fetcher)
    assert calls == [('NVDA', date(2026, 10, 1), date(2026, 10, 22))]
    assert len(out) == 1


def test_fetch_history_raises_on_empty():
    with pytest.raises(bars.BarsUnavailable):
        bars.fetch_history('NVDA', date(2026, 10, 1), date(2026, 10, 21), fetcher=lambda *a: pd.DataFrame())



def test_yfinance_transport_failure_surfaces_as_a_network_error(monkeypatch):
    """A proxy / connection failure must reach fetch-1 as ``network`` (retried), not come back as an empty frame
    that bars.py turns into a final ``empty`` (the 2026-10-31 QQQ ``benchmark_missing``)."""
    import socket

    from investment_assistant.data import fetch_policy as fp

    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        dead_proxy = f'http://127.0.0.1:{sock.getsockname()[1]}'
    for name in ('HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy'):
        monkeypatch.setenv(name, dead_proxy)
    monkeypatch.setattr(fp, '_SHARED_YF', [])        # a session built under this environment
    with pytest.raises(Exception) as caught:
        bars._yfinance_fetcher('QQQ', date(2026, 10, 20), date(2026, 10, 31))
    assert not isinstance(caught.value, bars.BarsUnavailable)
    assert fp.classify_exception(caught.value)[0] == 'network'


def test_yfinance_no_data_answer_is_still_an_empty_result(monkeypatch):
    """Yahoo answering "no price data" (or no timezone) for the range stays the final ``empty`` with the usual text."""
    import yfinance as yf
    from yfinance.exceptions import YFPricesMissingError, YFTzMissingError

    for exc in (YFPricesMissingError('QQQ', ' (1d 2026-10-20 -> 2026-10-31)'), YFTzMissingError('QQQ')):
        class FakeTicker:
            def __init__(self, *args, **kwargs):
                pass

            def history(self, **kwargs):
                raise exc

        monkeypatch.setattr(yf, 'Ticker', FakeTicker)
        with pytest.raises(bars.BarsUnavailable, match='no bars returned for QQQ 2026-10-20..2026-10-31'):
            bars.fetch_history('QQQ', date(2026, 10, 20), date(2026, 10, 31))

@pytest.mark.parametrize('now, expected', [
    (datetime(2026, 10, 22, 22, 30, tzinfo=UTC), date(2026, 10, 22)),  # Thu 18:30 NY, session closed
    (datetime(2026, 10, 22, 19, 0, tzinfo=UTC), date(2026, 10, 21)),   # Thu 15:00 NY, still open
    (datetime(2026, 10, 24, 22, 30, tzinfo=UTC), date(2026, 10, 23)),  # Saturday
    (datetime(2026, 11, 26, 23, 0, tzinfo=UTC), date(2026, 11, 25)),   # Thanksgiving
])
def test_latest_closed_session(now, expected):
    assert bars.latest_closed_session(now) == expected


def test_latest_closed_session_requires_aware_time():
    with pytest.raises(ValueError):
        bars.latest_closed_session(datetime(2026, 10, 22, 18, 0))


# --- storage (disposable schema) ---

def bar(day, close='101', adj='100.5'):
    return {'price_date': day, 'open': Decimal('100'), 'high': Decimal('102'), 'low': Decimal('99'),
            'close': Decimal(close), 'adj_close': Decimal(adj), 'volume': 1000, 'split_factor': Decimal('1')}


def test_store_appends_revision_only_when_values_change(workbench_db):
    d = date(2026, 10, 21)
    assert repository.store_bars(workbench_db, 'NVDA', [bar(d)], now=T0) == {'inserted': 1, 'unchanged': 0}
    assert repository.store_bars(workbench_db, 'NVDA', [bar(d)], now=T0 + timedelta(days=1)) == {'inserted': 0, 'unchanged': 1}
    assert repository.store_bars(workbench_db, 'NVDA', [bar(d, adj='99.9')], now=T0 + timedelta(days=2)) == {'inserted': 1, 'unchanged': 0}
    revisions = workbench_db.execute('SELECT revision, adj_close FROM price_bars ORDER BY revision').fetchall()
    assert revisions == [(1, Decimal('100.500000')), (2, Decimal('99.900000'))]


def test_read_bars_respects_cutoff_and_end_date(workbench_db):
    d1, d2, d3 = date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)
    repository.store_bars(workbench_db, 'NVDA', [bar(d1), bar(d2)], now=T0)
    repository.store_bars(workbench_db, 'NVDA', [bar(d2, adj='99')], now=T0 + timedelta(hours=5))
    repository.store_bars(workbench_db, 'NVDA', [bar(d3)], now=T0 + timedelta(hours=5))

    early = repository.read_bars(workbench_db, 'NVDA', d3, cutoff_at=T0 + timedelta(hours=1))
    assert [b['price_date'] for b in early] == [d1, d2]
    assert early[-1]['adj_close'] == 100.5 and early[-1]['revision'] == 1

    late = repository.read_bars(workbench_db, 'NVDA', d2, cutoff_at=T0 + timedelta(hours=6))
    assert [b['price_date'] for b in late] == [d1, d2]
    assert late[-1]['adj_close'] == 99.0 and late[-1]['revision'] == 2
    assert isinstance(late[-1]['id'], int)


def test_read_bars_limit_keeps_most_recent(workbench_db):
    days = [date(2026, 10, 19) + timedelta(days=i) for i in range(4)]
    repository.store_bars(workbench_db, 'NVDA', [bar(d) for d in days], now=T0)
    out = repository.read_bars(workbench_db, 'NVDA', days[-1], cutoff_at=T0, limit=2)
    assert [b['price_date'] for b in out] == days[-2:]


def test_float_noise_in_adjusted_close_is_not_a_revision(workbench_db):
    # Observed live: yfinance re-derives adj_close with ~1e-7 relative noise between fetches.
    d = date(2026, 3, 4)
    repository.store_bars(workbench_db, 'GOOG', [bar(d, close='303.450012', adj='302.857391')], now=T0)
    noisy = repository.store_bars(workbench_db, 'GOOG', [bar(d, close='303.450012', adj='302.857361')],
                                  now=T0 + timedelta(minutes=10))
    dividend = repository.store_bars(workbench_db, 'GOOG', [bar(d, close='303.450012', adj='302.55')],
                                     now=T0 + timedelta(days=90))
    assert noisy == {'inserted': 0, 'unchanged': 1}
    assert dividend == {'inserted': 1, 'unchanged': 0}


def test_any_change_in_raw_prices_is_still_a_revision(workbench_db):
    d = date(2026, 3, 4)
    repository.store_bars(workbench_db, 'GOOG', [bar(d, close='303.450012')], now=T0)
    assert repository.store_bars(workbench_db, 'GOOG', [bar(d, close='303.450013')],
                                 now=T0 + timedelta(minutes=1)) == {'inserted': 1, 'unchanged': 0}
