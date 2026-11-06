from pathlib import Path

import pytest
import requests

from investment_assistant.config import AssistantConfig, FilingsConfig, SecMonitorConfig
from investment_assistant.data.sources.edgar import EdgarSource, read_watchlist


@pytest.fixture
def cfg(tmp_path):
    watchlist = tmp_path / "watchlist.md"
    watchlist.write_text("| Ticker | 采集 |\n|---|---|\n| NVDA | 是 |\n| PRIVATE | 否 |\n")
    legacy = tmp_path / "legacy.jsonl"
    legacy.write_text("")
    return AssistantConfig(
        filings=FilingsConfig(output_dir=tmp_path / "filings"),
        sec_monitor=SecMonitorConfig(watchlist_path=str(watchlist),
            legacy_log_path=str(legacy), state_dir=str(tmp_path / "state")),
    )


def test_watchlist_is_read_each_time_and_empty_table_fails(cfg):
    from pathlib import Path
    path = Path(cfg.sec_monitor.watchlist_path)
    assert read_watchlist(path) == ["NVDA"]
    path.write_text("| Ticker | 采集 |\n| MU | 是 |\n")
    assert read_watchlist(path) == ["MU"]
    path.write_text("| Ticker | 采集 |\n| MU | 否 |\n")
    with pytest.raises(ValueError):
        read_watchlist(path)


def test_http_errors_follow_the_retry_policy_and_missing_agent_is_an_error(tmp_path, monkeypatch):
    """fetch-1: 429 / 5xx retry twice (Retry-After capped by the 2 s / 8 s backoff), 4xx fails at once."""
    class Session:
        calls = 0
        def __init__(self, status, headers):
            self.status, self.headers = status, headers
        def get(self, *args, **kwargs):
            self.calls += 1
            return type("Response", (), {"status_code": self.status, "headers": self.headers})()
    waits = []
    monkeypatch.setenv("SEC_USER_AGENT", "Research test@example.com")
    monkeypatch.setattr("investment_assistant.data.sources.edgar.time.sleep", lambda _s: None)   # the throttle
    session = Session(429, {"Retry-After": "60"})
    source = EdgarSource(tmp_path, session=session, sleep=waits.append)
    with pytest.raises(RuntimeError, match="HTTP 429"):
        source.get("https://data.sec.gov/test")
    assert session.calls == 3 and waits == [2, 8]
    session = Session(403, {})
    source = EdgarSource(tmp_path, session=session, sleep=waits.append)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        source.get("https://data.sec.gov/test")
    assert session.calls == 1
    monkeypatch.delenv("SEC_USER_AGENT")
    with pytest.raises(ValueError, match="SEC_USER_AGENT"):
        source.get("https://data.sec.gov/test")


@pytest.mark.parametrize("exception,reason", [
    (requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED secret"), "certificate_validation_failed"),
    (requests.exceptions.SSLError("UNEXPECTED_EOF secret"), "tls_peer_closed"),
    (requests.exceptions.ProxyError("secret"), "proxy_unavailable"),
    (requests.Timeout("secret"), "request_timeout"),
])
def test_network_failures_are_classified_without_leaking_details(tmp_path, monkeypatch, exception, reason):
    monkeypatch.setenv("SEC_USER_AGENT", "Research test@example.com")
    class Session:
        def get(self, *args, **kwargs):
            raise exception
    monkeypatch.setattr("investment_assistant.data.sources.edgar.time.sleep", lambda _s: None)   # the throttle
    with pytest.raises(RuntimeError) as caught:
        EdgarSource(tmp_path, session=Session(), sleep=lambda _s: None).get("https://data.sec.gov/test")
    assert reason in str(caught.value) and "secret" not in str(caught.value)


def test_historical_pages_are_scanned_and_tsla_10k_is_excluded(tmp_path):
    source = EdgarSource(tmp_path)
    def rows(forms):
        return {"form": forms, "filingDate": ["2026-10-15"] * len(forms),
                "accessionNumber": [f"0001045810-26-{i:06d}" for i in range(len(forms))],
                "primaryDocument": [f"main{i}.htm" for i in range(len(forms))]}
    urls = []
    def get(url):
        urls.append(url)
        if "company_tickers" in url:
            return {"0": {"ticker": "TSLA", "cik_str": 1318605}}
        if "-submissions-" in url:
            return rows(["10-K", "10-Q", "8-K"])
        return {"filings": {"recent": rows([]), "files": [{"name": "CIK0001318605-submissions-001.json",
                    "filingFrom": "2026-10-01", "filingTo": "2026-10-15"}]}}
    source.json = get
    out, errors = source.discover(["TSLA"], "2026-10-01", "2026-10-15")
    assert errors == {} and [x["form"] for x in out] == ["10-Q", "8-K"]
    assert any("-submissions-001" in url for url in urls)
    with pytest.raises(ValueError):
        source.rows({"form": ["10-Q"]})


@pytest.mark.parametrize("primary,exhibit,selected", [(b"<p>Item 2.02 Results</p>", False, True),
    (b"<p>Other update</p>", True, True), (b"<p>Item 5.02</p>", False, False)])
def test_8k_filter_and_exhibit_download(tmp_path, primary, exhibit, selected):
    source = EdgarSource(tmp_path)
    base = "https://www.sec.gov/Archives/edgar/data/1/123/"
    table = '<table summary="Document Format Files"><tr><th>Seq</th></tr>'
    if exhibit:
        table += '<tr><td>2</td><td>release</td><td><a href="ex99.htm">ex99</a></td><td>EX-99.1</td></tr>'
    table += '</table>'
    data = {base + 'main.htm': primary, base + 'index.html': table.encode(), base + 'ex99.htm': b'release'}
    source.get = data.__getitem__
    keep, docs = source.documents({"form": "8-K", "primary_document": "main.htm",
                                  "edgar_url": base + 'main.htm', "index_url": base + 'index.html'})
    assert keep == selected
    assert len(docs) == 1 + int(exhibit)


def _submission_source(tmp_path, **extra):
    source = EdgarSource(tmp_path)
    recent = {"form": ["8-K", "10-Q"], "filingDate": ["2026-08-27", "2026-08-27"],
              "accessionNumber": ["0001045810-26-000010", "0001045810-26-000011"],
              "primaryDocument": ["k.htm", "q.htm"], **extra}

    def get(url):
        if "company_tickers" in url:
            return {"0": {"ticker": "NVDA", "cik_str": 1045810}}
        return {"filings": {"recent": recent, "files": []}}
    source.json = get
    return source


def test_acceptance_time_z_suffix_is_real_utc(tmp_path):
    # Observed on EDGAR (TSLA, 2026-10): an Item 2.02 8-K with filingDate 2026-10-21 carries
    # acceptanceDateTime 2026-10-22T00:41:42Z. Only the UTC reading (20:41 ET on 10-21) fits the
    # filing date and EDGAR's hours; reading it as Eastern would put it after midnight on 10-22.
    source = _submission_source(tmp_path, acceptanceDateTime=["2026-10-22T00:41:42.000Z", "2026-08-27T11:05:00.000Z"],
                                items=["2.02,9.01", ""])
    out = {x["form"]: x for x in source.discover(["NVDA"], "2026-08-01", "2026-08-31")[0]}
    assert out["8-K"]["accepted_at"] == "2026-10-22T00:41:42+00:00"
    assert out["8-K"]["items"] == ["2.02", "9.01"]
    assert out["10-Q"]["accepted_at"] == "2026-08-27T11:05:00+00:00"
    assert out["10-Q"]["items"] == []


def test_missing_acceptance_fields_stay_unknown(tmp_path):
    out, _ = _submission_source(tmp_path).discover(["NVDA"], "2026-08-01", "2026-08-31")
    assert all(x["accepted_at"] is None and x["items"] is None for x in out)


def test_malformed_acceptance_time_is_unknown_not_guessed(tmp_path):
    source = _submission_source(tmp_path, acceptanceDateTime=["not-a-time", None], items=[None, 5])
    out, _ = source.discover(["NVDA"], "2026-08-01", "2026-08-31")
    assert all(x["accepted_at"] is None and x["items"] is None for x in out)


# ------------------------------------------------------------------------------------------------ D47: the sec_text adapter
