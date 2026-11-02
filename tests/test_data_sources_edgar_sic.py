"""SIC parsing uses the real EdgarSource, with only HTTP replaced by local fixtures."""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from investment_assistant.core.constants import SEC_EVIDENCE_DIR
from investment_assistant.data import fetch_policy
from investment_assistant.data.fetch_policy import FetchError, FetchPolicy
from investment_assistant.data.sources import edgar


SUBMISSIONS = 'https://data.sec.gov/submissions/CIK0001045810.json'


class Transport:
    def __init__(self, payload, *, failures=()):
        self.payload, self.failures = payload, list(failures)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url == edgar.COMPANY_TICKERS_URL:
            value = {'0': {'ticker': 'NVDA', 'cik_str': 1045810, 'title': 'NVIDIA CORP'}}
        else:
            assert url == SUBMISSIONS
            if self.failures:
                failure = self.failures.pop(0)
                if isinstance(failure, Exception):
                    raise failure
                return SimpleNamespace(status_code=failure, headers={}, content=b'')
            value = self.payload
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        return SimpleNamespace(status_code=200, headers={}, content=body)


@pytest.fixture
def source(monkeypatch, tmp_path):
    monkeypatch.setenv('SEC_USER_AGENT', 'offline-fixture sic@example.invalid')
    monkeypatch.setattr(edgar.time, 'sleep', lambda _: None)

    def make(payload, *, failures=(), backoff=()):
        transport = Transport(payload, failures=failures)
        instance = edgar.EdgarSource(tmp_path / 'evidence', session=transport,
                                     policy=FetchPolicy(backoff_s=backoff), sleep=lambda _: None)
        return instance, transport

    return make


def test_fetch_sic_reuses_source_cik_cache_and_evidence(source):
    instance, transport = source({'sic': '3674', 'sicDescription': 'SEMICONDUCTORS & RELATED DEVICES'})
    deps = SimpleNamespace(filing_source=instance)
    expected = {'sic': '3674', 'sic_description': 'SEMICONDUCTORS & RELATED DEVICES'}
    assert edgar.fetch_sic('nvda', deps=deps) == expected
    assert edgar.fetch_sic('NVDA', deps=deps) == expected
    assert [url for url, _ in transport.calls] == [edgar.COMPANY_TICKERS_URL, SUBMISSIONS, SUBMISSIONS]
    assert len(instance.evidence) == 3
    assert all(Path(item['path']).is_file() for item in instance.evidence)


@pytest.mark.parametrize('payload, expected', [
    ({'sic': 100, 'sicDescription': ' AGRICULTURAL PRODUCTION '},
     {'sic': '0100', 'sic_description': 'AGRICULTURAL PRODUCTION'}),
    ({'sic': ' 7372 ', 'sicDescription': 'PREPACKAGED SOFTWARE'},
     {'sic': '7372', 'sic_description': 'PREPACKAGED SOFTWARE'}),
    ({'sic': '9999'}, {'sic': '9999', 'sic_description': None}),
    ({}, {'sic': None, 'sic_description': None}),
    ({'sic': None, 'sicDescription': None}, {'sic': None, 'sic_description': None}),
    ({'sic': '0000', 'sicDescription': ''}, {'sic': None, 'sic_description': None}),
])
def test_sic_normalization_and_missing_metadata(source, payload, expected):
    instance, _ = source(payload)
    assert edgar.fetch_sic('NVDA', deps=SimpleNamespace(filing_source=instance)) == expected


@pytest.mark.parametrize('payload', [
    {'sic': 'tech'}, {'sic': '12345'}, {'sic': -1}, {'sic': True}, {'sic': []},
    {'sic': '3674', 'sicDescription': []}, [], b'not json',
])
def test_malformed_sic_is_parse_without_payload_or_user_agent(source, payload):
    instance, transport = source(payload)
    with pytest.raises(FetchError) as caught:
        edgar.fetch_sic('NVDA', deps=SimpleNamespace(filing_source=instance))
    assert caught.value.kind == 'parse'
    assert caught.value.target == 'sec:submissions:0001045810'
    assert 'sic@example.invalid' not in str(caught.value)
    assert len(transport.calls) == 2


def test_missing_cik_is_explicit_empty(source):
    instance, transport = source({})
    with pytest.raises(FetchError) as caught:
        edgar.fetch_sic('UNKNOWN', deps=SimpleNamespace(filing_source=instance))
    assert caught.value.kind == 'empty'
    assert len(transport.calls) == 1


def test_sec_retry_policy_is_preserved(source):
    instance, transport = source({'sic': '3674'}, failures=(503,), backoff=(0,))
    assert edgar.fetch_sic('NVDA', deps=SimpleNamespace(filing_source=instance)) == {
        'sic': '3674', 'sic_description': None,
    }
    assert [url for url, _ in transport.calls] == [edgar.COMPANY_TICKERS_URL, SUBMISSIONS, SUBMISSIONS]


def test_sec_transport_error_keeps_attempts_and_is_redacted(source):
    instance, transport = source({}, failures=(requests.ConnectionError('sic@example.invalid'),))
    with pytest.raises(FetchError) as caught:
        edgar.fetch_sic('NVDA', deps=SimpleNamespace(filing_source=instance))
    assert caught.value.kind == 'network'
    assert caught.value.target == 'sec:submissions:0001045810'
    assert [a['kind'] for a in caught.value.attempts] == ['network']
    assert 'sic@example.invalid' not in str(caught.value)
    assert len(transport.calls) == 2


def test_default_source_uses_existing_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv('SEC_USER_AGENT', 'offline-fixture sic@example.invalid')
    monkeypatch.setattr(edgar.time, 'sleep', lambda _: None)
    transport = Transport({'sic': '3674'})
    monkeypatch.setattr(fetch_policy, 'policy_session', lambda policy: transport)
    seen = []
    real_init = edgar.EdgarSource.__init__

    def init(self, evidence_dir, **kwargs):
        seen.append(evidence_dir)
        real_init(self, tmp_path / 'evidence', **kwargs)

    monkeypatch.setattr(edgar.EdgarSource, '__init__', init)
    assert edgar.fetch_sic('NVDA', deps=SimpleNamespace(filing_source=None))['sic'] == '3674'
    assert seen == [SEC_EVIDENCE_DIR]


def test_classify_override_then_group_then_none():
    sector = importlib.import_module('investment_assistant.data.adapters.sector')
    groups, overrides = {'35': '科技硬件', '36': '电子'}, {'NVDA': '半导体', 'NONE': None}
    assert sector.classify('3674', groups=groups, overrides=overrides, ticker='NVDA') == '半导体'
    assert sector.classify(None, groups=groups, overrides=overrides, ticker='NVDA') == '半导体'
    assert sector.classify('3571', groups=groups, overrides=overrides, ticker='AAPL') == '科技硬件'
    assert sector.classify('3674', groups=groups, overrides=overrides, ticker='NONE') is None
    for sic in (None, '', '9', '9999'):
        assert sector.classify(sic, groups=groups, overrides=overrides, ticker='UNKNOWN') is None
    assert groups == {'35': '科技硬件', '36': '电子'}
    assert overrides == {'NVDA': '半导体', 'NONE': None}


def test_config_maps_common_sics_but_leaves_unclassified_unmapped():
    sector = importlib.import_module('investment_assistant.data.adapters.sector')
    config = Path(__file__).resolve().parents[1] / 'config'
    groups = json.loads((config / 'sic_groups.json').read_text())
    overrides = json.loads((config / 'sector_overrides.json').read_text())
    assert overrides == {}
    for sic, expected in [('3571', '科技硬件'), ('3674', '电子'), ('7372', '软件服务'),
                          ('2834', '医药化工'), ('6021', '金融'), ('6798', '金融')]:
        assert sector.classify(sic, groups=groups, overrides=overrides, ticker='X') == expected
    assert sector.classify('9999', groups=groups, overrides=overrides, ticker='X') is None
    assert sector.classify(None, groups=groups, overrides=overrides, ticker='X') is None
