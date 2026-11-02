"""Offline CSV contract: exact prices, inclusive window and fail-closed validation."""
import importlib
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
import requests

from investment_assistant.data.fetch_policy import FetchError


HEADER = 'Date,Open,High,Low,Close,Volume\n'
CSV = HEADER + '2026-10-29,10.123456,12,9,11.234567,123\n2026-10-30,11,13,10,12,0\n'
START, END = date(2026, 10, 29), date(2026, 10, 30)


class Transport:
    def __init__(self, text=CSV, *, status=200, error=None):
        self.text, self.status, self.error = text, status, error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text, status_code=self.status, headers={'Retry-After': '2'})


def test_parses_csv_to_rows():
    stooq = importlib.import_module('investment_assistant.data.sources.stooq')
    transport = Transport()
    rows = stooq.fetch_bars('NVDA', start=START, end=END, session=transport)
    assert rows == [
        {'price_date': START, 'open': Decimal('10.123456'), 'high': Decimal('12'),
         'low': Decimal('9'), 'close': Decimal('11.234567'), 'adj_close': Decimal('11.234567'),
         'volume': 123, 'split_factor': Decimal('1'), 'source': 'stooq'},
        {'price_date': END, 'open': Decimal('11'), 'high': Decimal('13'),
         'low': Decimal('10'), 'close': Decimal('12'), 'adj_close': Decimal('12'),
         'volume': 0, 'split_factor': Decimal('1'), 'source': 'stooq'},
    ]
    assert all(isinstance(row[key], Decimal) for row in rows
               for key in ('open', 'high', 'low', 'close', 'adj_close', 'split_factor'))
    assert len(transport.calls) == 1
    url, kwargs = transport.calls[0]
    assert url.lower() == 'https://stooq.com/q/d/l/?s=nvda.us&i=d'
    assert kwargs['timeout'][0] > 0 and kwargs['timeout'][1] > 0
    assert kwargs['allow_redirects'] is False


def test_filters_start_inclusively_and_sorts():
    stooq = importlib.import_module('investment_assistant.data.sources.stooq')
    csv = HEADER + ('2026-10-30,11,13,10,12,2\n2026-10-28,10,12,9,11,1\n'
                    '2026-10-29,10,12,9,11,1\n')
    assert [r['price_date'] for r in stooq.fetch_bars('AAPL', start=START, end=END, session=Transport(csv))] == [
        START, END,
    ]


@pytest.mark.parametrize('bad', [
    '2026-11-02,10,12,9,11,1',    # future of the caller's latest completed session
    '2026-10-30,13,12,9,11,1',    # open above high
    '2026-10-30,10,12,9,8,1',     # close below low
    '2026-10-30,10,8,12,11,1',    # inverted range
    '2026-10-30,0,12,0,11,1',
    '2026-10-30,-1,12,-2,11,1',
    '2026-10-30,NaN,12,9,11,1',
    '2026-10-30,10,Infinity,9,11,1',
    '2026-10-30,10,12,9,no-price,1',
    '2026-02-30,10,12,9,11,1',
    '20261030,10,12,9,11,1',
    '2026-10-30T00:00:00,10,12,9,11,1',
    '2026-10-30,10,12,9,11,-1',
    '2026-10-30,10,12,9,11,1.5',
    '2026-10-30,10,12,9,11,NaN',
    '2026-10-30,10,12,9,11',
    '2026-10-30,10,12,9,11,1,extra',
])
def test_rejects_future_date_and_bad_ohlc(bad):
    stooq = importlib.import_module('investment_assistant.data.sources.stooq')
    with pytest.raises(FetchError) as caught:
        stooq.fetch_bars('NVDA', start=START, end=END, session=Transport(CSV + bad + '\n'))
    assert caught.value.kind == 'parse'  # no partial success even with good preceding rows


@pytest.mark.parametrize('text, kind', [
    ('', 'empty'), ('No data', 'empty'), (HEADER, 'empty'),
    ('<html>not CSV</html>', 'parse'), ('Date,Close\n2026-10-30,11\n', 'parse'),
    (HEADER + '2026-10-28,10,12,9,11,1\n', 'empty'),
])
def test_empty_and_malformed_payloads(text, kind):
    stooq = importlib.import_module('investment_assistant.data.sources.stooq')
    with pytest.raises(FetchError) as caught:
        stooq.fetch_bars('NVDA', start=START, end=END, session=Transport(text))
    assert caught.value.kind == kind


def test_validate_rows_is_atomic_and_does_not_mutate_inputs():
    stooq = importlib.import_module('investment_assistant.data.sources.stooq')
    rows = stooq.fetch_bars('NVDA', start=START, end=END, session=Transport())
    before = [dict(row) for row in rows]
    assert stooq.validate_rows(rows, latest_session=END) == rows
    for change in ({'price_date': '2026-10-30'}, {'price_date': date(2026, 11, 2)},
                   {'high': Decimal('NaN')}, {'open': Decimal('14')}, {'low': None}):
        with pytest.raises(FetchError) as caught:
            stooq.validate_rows([rows[0], {**rows[1], **change}], latest_session=END)
        assert caught.value.kind == 'parse'
    assert rows == before


@pytest.mark.parametrize('status, kind', [(404, 'http_4xx'), (429, 'http_429'), (503, 'http_5xx')])
def test_http_errors_are_classified_without_retry(status, kind):
    stooq = importlib.import_module('investment_assistant.data.sources.stooq')
    transport = Transport(status=status)
    with pytest.raises(FetchError) as caught:
        stooq.fetch_bars('NVDA', start=START, end=END, session=transport)
    assert (caught.value.kind, caught.value.status) == (kind, status)
    assert len(transport.calls) == 1


@pytest.mark.parametrize('error, kind', [
    (requests.Timeout('private transport detail'), 'timeout'),
    (requests.ConnectionError('private transport detail'), 'network'),
])
def test_transport_failures_are_safe_and_single_attempt(error, kind):
    stooq = importlib.import_module('investment_assistant.data.sources.stooq')
    transport = Transport(error=error)
    with pytest.raises(FetchError) as caught:
        stooq.fetch_bars('NVDA', start=START, end=END, session=transport)
    assert caught.value.kind == kind
    assert 'private transport detail' not in str(caught.value)
    assert len(transport.calls) == 1
def test_fill_from_backup_only_missing_sessions_and_ratio(monkeypatch):
    from datetime import date
    from decimal import Decimal
    from investment_assistant.data import repo
    from investment_assistant.data.adapters import bars
    seen = []
    missing = [date(2026, 7, 2), date(2026, 7, 6)]
    def sessions(conn, ticker, days):
        assert days == [date(2026, 6, 22), date(2026, 6, 23), date(2026, 6, 24), date(2026, 6, 25),
                        date(2026, 6, 26), date(2026, 6, 29), date(2026, 6, 30), date(2026, 7, 1),
                        date(2026, 7, 2), date(2026, 7, 6)]
        return missing
    monkeypatch.setattr(repo, 'missing_bar_sessions', sessions)
    monkeypatch.setattr(repo, 'ratio_from_primary', lambda conn, ticker, day: Decimal('.98'))
    def store(conn, rows, **kwargs):
        seen.extend(rows)
        return {'inserted': len(rows), 'existing': 0, 'latest': max(r['price_date'] for r in rows)}
    rows = [dict(price_date=day, open=Decimal(100), high=Decimal(105), low=Decimal(90),
                 close=Decimal(100), adj_close=Decimal(100), volume=2, source='stooq')
            for day in [date(2026, 6, 1), date(2026, 7, 1), *missing]]
    out = bars.fill_from_backup(None, 'NVDA', session=date(2026, 7, 6), rows=rows, store=store)
    assert [r['price_date'] for r in seen] == missing
    assert all(r['adj_close'] == Decimal(98) and r['source'] == 'stooq' for r in seen)
    assert out == {'inserted': 2, 'existing': 0, 'latest': date(2026, 7, 6)}
    assert all(r['adj_close'] == 100 for r in rows)
