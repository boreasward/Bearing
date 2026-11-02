"""Offline analyst/earnings-date fixtures following Task 0's actual announcement shape."""
import importlib
from datetime import date
from types import SimpleNamespace

import pytest
import requests

from investment_assistant.data import fetch_policy
from investment_assistant.data.fetch_policy import FetchError


TODAY = date(2026, 11, 1)


def payload(announcement):
    return {'data': {'announcement': announcement, 'eQRResposeModel': None,
                     'heading': 'Earnings Date', 'reportText': ''},
            'message': None, 'status': {'rCode': 200}}


class Transport:
    def __init__(self, value, *, status=200, error=None, json_error=False):
        self.value, self.status, self.error, self.json_error = value, status, error, json_error
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error

        def json():
            if self.json_error:
                raise ValueError('private invalid response')
            return self.value

        return SimpleNamespace(status_code=self.status, headers={'Retry-After': '2'}, json=json)


def install(monkeypatch, value, **kwargs):
    transport = Transport(value, **kwargs)
    monkeypatch.setattr(fetch_policy, 'policy_session', lambda *args, **kw: transport)
    return transport


@pytest.mark.parametrize('value, expected', [
    (payload('Earnings announcement* for NVDA: Nov 17, 2026'), date(2026, 11, 17)),
    (payload('Nov 1, 2026'), TODAY),
    (payload('Earnings announcement* for AAPL: November 17, 2026'), date(2026, 11, 17)),
    (payload('Oct 30, 2026'), None),
    (payload('Not available'), None), (payload('N/A'), None), (payload(''), None), (payload(None), None),
    ({'data': None, 'message': None, 'status': {'rCode': 200}}, None),
    ({'data': {}}, None), ({}, None),
])
def test_parses_known_and_unknown(monkeypatch, value, expected):
    nasdaq = importlib.import_module('investment_assistant.data.sources.nasdaq')
    transport = install(monkeypatch, value)
    assert nasdaq.fetch_earnings_date('nvda', today=TODAY) == {
        'status': 'known' if expected else 'unknown', 'earnings_date': expected,
    }
    assert len(transport.calls) == 1
    url, kwargs = transport.calls[0]
    assert url.upper().endswith('/API/ANALYST/NVDA/EARNINGS-DATE')
    assert 'Mozilla/' in kwargs['headers']['User-Agent']
    assert 'application/json' in kwargs['headers']['Accept']
    assert kwargs['timeout'][0] > 0 and kwargs['timeout'][1] > 0
    assert kwargs['allow_redirects'] is False


@pytest.mark.parametrize('value', [[], {'data': []}, payload(123), payload('Feb 30, 2027')])
def test_rejects_malformed_json_shape_or_date(monkeypatch, value):
    nasdaq = importlib.import_module('investment_assistant.data.sources.nasdaq')
    install(monkeypatch, value)
    with pytest.raises(FetchError) as caught:
        nasdaq.fetch_earnings_date('NVDA', today=TODAY)
    assert caught.value.kind == 'parse'


def test_invalid_json_is_parse(monkeypatch):
    nasdaq = importlib.import_module('investment_assistant.data.sources.nasdaq')
    install(monkeypatch, None, json_error=True)
    with pytest.raises(FetchError) as caught:
        nasdaq.fetch_earnings_date('NVDA', today=TODAY)
    assert caught.value.kind == 'parse'
    assert 'private invalid response' not in str(caught.value)


@pytest.mark.parametrize('status, kind', [(403, 'http_4xx'), (429, 'http_429'), (503, 'http_5xx')])
def test_http_failure_is_not_unknown_or_retried(monkeypatch, status, kind):
    nasdaq = importlib.import_module('investment_assistant.data.sources.nasdaq')
    transport = install(monkeypatch, None, status=status)
    with pytest.raises(FetchError) as caught:
        nasdaq.fetch_earnings_date('NVDA', today=TODAY)
    assert (caught.value.kind, caught.value.status) == (kind, status)
    assert len(transport.calls) == 1


def test_api_error_envelope_is_not_unknown(monkeypatch):
    nasdaq = importlib.import_module('investment_assistant.data.sources.nasdaq')
    install(monkeypatch, {'data': None, 'message': 'error', 'status': {'rCode': 500}})
    with pytest.raises(FetchError) as caught:
        nasdaq.fetch_earnings_date('NVDA', today=TODAY)
    assert (caught.value.kind, caught.value.status) == ('http_5xx', 500)


@pytest.mark.parametrize('error, kind', [
    (requests.Timeout('private transport detail'), 'timeout'),
    (requests.ConnectionError('private transport detail'), 'network'),
])
def test_transport_failure_is_not_unknown_or_retried(monkeypatch, error, kind):
    nasdaq = importlib.import_module('investment_assistant.data.sources.nasdaq')
    transport = install(monkeypatch, None, error=error)
    with pytest.raises(FetchError) as caught:
        nasdaq.fetch_earnings_date('NVDA', today=TODAY)
    assert caught.value.kind == kind
    assert 'private transport detail' not in str(caught.value)
    assert len(transport.calls) == 1
