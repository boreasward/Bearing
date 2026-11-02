import json
from datetime import date
from pathlib import Path

import pytest
import requests

from investment_assistant.config import AssistantConfig, FilingsConfig, NotifyConfig, SecMonitorConfig, load_config
from investment_assistant.tasks.filings import scan_and_notify
from investment_assistant.data.sources.edgar import EdgarSource, read_watchlist
from investment_assistant.notify.discord_bot import DeliveryError, DiscordBotClient


DAY = date(2026, 10, 15)
ACC = "0001045810-26-000001"


@pytest.fixture
def cfg(tmp_path):
    watchlist = tmp_path / "watchlist.md"
    watchlist.write_text("| Ticker | 采集 |\n|---|---|\n| NVDA | 是 |\n| PRIVATE | 否 |\n")
    legacy = tmp_path / "legacy.jsonl"
    legacy.write_text("")
    return AssistantConfig(
        filings=FilingsConfig(output_dir=tmp_path / "filings"),
        sec_monitor=SecMonitorConfig(enabled=True, start_date="2026-10-01", watchlist_path=str(watchlist),
            legacy_log_path=str(legacy), state_dir=str(tmp_path / "state"), discord_channel_id="123"),
    )


class Source:
    evidence = [{"url": "https://data.sec.gov/submissions/CIK0001045810.json", "sha256": "hash"}]

    def __init__(self, *, fail=False, selected=True):
        self.fail, self.selected = fail, selected
        self.downloads = 0

    def discover(self, tickers, start, end):
        assert tickers == ["NVDA"]
        if self.fail:
            raise RuntimeError("SEC HTTP 403")
        return [{"ticker": "NVDA", "form": "8-K", "filing_date": "2026-10-15", "accession_number": ACC,
                 "primary_document": "main.htm", "edgar_url": "https://www.sec.gov/main.htm"}]

    def documents(self, meta):
        self.downloads += 1
        return self.selected, [("main.htm", b"<html>Item 2.02</html>"), ("ex99.htm", b"release")]


class Client:
    def __init__(self, failure=None):
        self.sent = []
        self.failure = failure

    def send(self, content, nonce):
        self.sent.append(content)
        if self.failure:
            raise self.failure
        return {"message_id": str(len(self.sent)), "channel_id": "123"}


def scan(cfg, source, client, **kwargs):
    return scan_and_notify(cfg, DAY, DAY, source=source, client=client, notify_cfg=NotifyConfig(),
                           persist=kwargs.pop("persist", lambda rows: None),
                           reports=lambda rows: [], **kwargs)


def test_source_failure_is_not_zero_and_duplicate_alert_is_suppressed(cfg):
    client = Client()
    first = scan(cfg, Source(fail=True), client)
    second = scan(cfg, Source(fail=True), client)
    assert first["coverage"] == "unavailable" and first["errors"]["collection"] == "SEC HTTP 403"
    assert first["delivery"]["receipts"][0]["message_id"] == "1"
    assert len(client.sent) == 1
    assert "无新符合规则" not in client.sent[0]
    assert "last_complete_scan" not in json.loads((Path(cfg.sec_monitor.state_dir) / 'state.json').read_text())


def test_archive_and_receipts_resume_without_redownload_or_refiling(cfg):
    source, client = Source(), Client()
    first = scan(cfg, source, client)
    second = scan(cfg, source, client)
    assert first["coverage"] == "complete_for_declared_universe"
    assert source.downloads == 1
    assert len(list(cfg.filings.output_dir.rglob("*.htm"))) == 2
    assert sum("】NVDA 8-K" in text for text in client.sent) == 1
    assert len(client.sent) == 1
    assert second["delivery"]["receipts"][0]["status"] == "sent"


def test_unknown_delivery_is_not_automatically_retried(cfg):
    client = Client(DeliveryError("timeout", uncertain=True))
    first = scan(cfg, Source(), client)
    second = scan(cfg, Source(), client)
    assert len(client.sent) == 1
    assert first["delivery"]["status"] == second["delivery"]["status"] == "needs_attention"
    assert "delivery" in second["errors"]


def test_definite_rejection_is_retried_without_reloading_source_documents(cfg):
    source = Source()
    client = Client(DeliveryError("HTTP 429", uncertain=False))
    first = scan(cfg, source, client)
    client.failure = None
    second = scan(cfg, source, client)
    assert first["delivery"]["status"] == "needs_attention"
    assert second["delivery"]["status"] == "sent"
    assert source.downloads == 1 and len(client.sent) == 2


@pytest.mark.parametrize("sent", [True, False])
def test_legacy_accession_is_not_redownloaded_or_pushed_even_if_unconfirmed(cfg, sent):
    from pathlib import Path
    Path(cfg.sec_monitor.legacy_log_path).write_text(json.dumps({"ticker": "NVDA", "accession": ACC, "discord_sent": sent}))
    source, client = Source(), Client()
    out = scan(cfg, source, client)
    assert source.downloads == 0 and out["filings"] == []
    assert out["legacy_pending_verification"] == int(not sent)
    assert "无新符合规则" in client.sent[0]


def test_corrupt_legacy_log_stops_scan_and_does_not_emit_zero(cfg):
    from pathlib import Path
    Path(cfg.sec_monitor.legacy_log_path).write_text("bad json")
    client = Client()
    out = scan(cfg, Source(), client)
    assert out["coverage"] == "unavailable"
    assert "无新符合规则" not in client.sent[0]


class PartialSource(Source):
    """discover() as EdgarSource returns it: (metas, {ticker: error}); MU's listing failed."""

    def discover(self, tickers, start, end):
        assert tickers == ["NVDA", "MU"]
        return Source.discover(self, ["NVDA"], start, end), {"MU": "SEC HTTP 503 (3 attempts)"}


def test_monitor_collect_continues_after_one_ticker_fails(cfg):
    Path(cfg.sec_monitor.watchlist_path).write_text("| Ticker | 采集 |\n|---|---|\n| NVDA | 是 |\n| MU | 是 |\n")
    source, client = PartialSource(), Client()
    out = scan(cfg, source, client)
    # NVDA is still archived and notified; MU's failure is reported per ticker, not as a whole-scan failure
    assert source.downloads == 1 and [m["ticker"] for m in out["filings"]] == ["NVDA"]
    assert out["errors"] == {"MU": "SEC HTTP 503 (3 attempts)"}
    assert out["coverage"] == "partial_for_declared_universe"
    assert any("】NVDA 8-K" in text for text in client.sent)
    partial = [text for text in client.sent if "部分股票查源失败" in text]
    assert partial and "MU" in partial[0] and "其余股票已正常处理" in partial[0] and "采集失败" not in partial[0]
    assert not any("无新符合规则" in text for text in client.sent)
    assert "last_complete_scan" not in json.loads((Path(cfg.sec_monitor.state_dir) / "state.json").read_text())


def test_non_target_8k_has_link_but_no_formal_archive(cfg):
    out = scan(cfg, Source(selected=False), Client())
    assert len(out["other_8k"]) == 1
    assert not cfg.filings.output_dir.exists()


def test_index_failure_resumes_owned_files_without_becoming_legacy(cfg):
    source, client = Source(), Client()
    def broken(rows):
        raise RuntimeError("index unavailable")
    first = scan(cfg, source, client, persist=broken)
    second = scan(cfg, source, client)
    assert first["errors"] and not second["errors"]
    assert source.downloads == 1
    assert sum("】NVDA 8-K" in text for text in client.sent) == 1


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


@pytest.mark.parametrize("missing,expected", [
    ("FILINGS_REPORT_DIR", "正文报告"), ("INVESTMENT_ASSISTANT_DATABASE_URL", "SEC 索引"),
])
def test_missing_report_or_database_config_cannot_claim_complete(cfg, monkeypatch, missing, expected):
    monkeypatch.setenv("FILINGS_REPORT_DIR", str(cfg.filings.output_dir / 'reports'))
    monkeypatch.setenv("INVESTMENT_ASSISTANT_DATABASE_URL", "test-not-connected")
    monkeypatch.delenv(missing)
    source, client = Source(), Client()
    result = scan_and_notify(cfg, DAY, DAY, source=source, client=client, notify_cfg=NotifyConfig())
    assert result['coverage'] == 'unavailable' and expected in result['errors']['collection']
    assert source.downloads == 0 and '采集失败' in client.sent[0]


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


def test_bot_returns_receipt_and_does_not_leak_token_on_timeout(monkeypatch):
    monkeypatch.setenv("SEC_DISCORD_BOT_TOKEN", "secret-test-token")
    class Session:
        def post(self, url, **kwargs):
            assert kwargs['json']['allowed_mentions'] == {'parse': []}
            assert kwargs['json']['enforce_nonce'] is True
            raise requests.Timeout("secret-test-token")
    with pytest.raises(DeliveryError) as caught:
        DiscordBotClient("123", session=Session()).send("message", "nonce")
    assert caught.value.uncertain
    assert "secret-test-token" not in str(caught.value)


def test_monitor_config_loads_and_routes_filings_task(tmp_path, monkeypatch):
    """v2.8: the sec_monitor node's pass takes the monitor path when the config enables it (and the scan range is the
    configured start date to New York today)."""
    from datetime import date

    from investment_assistant.tasks import filings as monitor
    from investment_assistant.tasks import filings
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"sec_monitor": {"enabled": True, "start_date": "2026-10-01"}}))
    cfg = load_config(path)
    seen = {}
    monkeypatch.setattr(monitor, "scan_and_notify",
                        lambda config, start, end, **kw: seen.update(start=start, end=end) or {"migrated": True})
    out, mode = filings._scan(cfg, start_date=None, end_date=None, ny_date=date(2026, 10, 22), drop_after=None,
                              notify_cfg=object())
    assert (out, mode) == ({"migrated": True}, "monitor")
    assert seen == {"start": date(2026, 10, 1), "end": date(2026, 10, 22)}


def test_harness_reports_collector_error_without_duplicate_notification(monkeypatch):
    from investment_assistant.tasks import _harness
    recorded = []
    monkeypatch.setattr(_harness, "_record", lambda **kw: recorded.append(kw))
    monkeypatch.setattr(_harness, "dispatch", lambda *a, **kw: pytest.fail("must not dispatch twice"))
    out = _harness.run_task("filings", lambda: {"errors": {"source": "403"}}, config=AssistantConfig(), notify=False)
    assert out["status"] == recorded[0]["status"] == "error"


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

def test_uses_sec_text_adapter_with_scope(workbench_db, tmp_path, monkeypatch):
    """Spec D47: after the scan the node's downloads and its research refresh are the ``sec_text`` adapter's work with
    ``scope = 'sec_monitor'``; the fetch-run row's family is ``sec_text`` with ``detail.scope = 'sec_monitor'``. A ticker
    whose scan failed is booked from the scan and not fetched again, and (R27) so is one whose scan found no new filing:
    the adapter pass covers only the tickers with new filings in this scan, the collect node's row covers the rest."""
    from contextlib import contextmanager
    from datetime import UTC, datetime
    from types import SimpleNamespace

    import investment_assistant.core.stages as stages
    import investment_assistant.core.tx as core_tx
    from investment_assistant.workflow.nodes import sec_monitor

    workbench_db.execute(Path("migrations/014_us_research.sql").read_text())
    workbench_db.commit()

    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(core_tx, "connection", connection)

    class TextSource:
        def __init__(self):
            self.discovered, self.downloads = [], []

        def discover_one(self, ticker, start, end, **kwargs):
            self.discovered.append(ticker)
            return [{"ticker": ticker, "form": "10-Q", "filing_date": "2026-10-01", "accession_number": ACC,
                     "primary_document": "q.htm", "items": None,
                     "edgar_url": "https://www.sec.gov/Archives/edgar/data/1/000104581026000001/q.htm"}]

        def get(self, url):
            self.downloads.append(url)
            return b"<html><body><p>Revenue was strong in the quarter and cash flow improved.</p></body></html>"

    cfg = AssistantConfig(
        filings=FilingsConfig(output_dir=tmp_path / "filings"),
        sec_monitor=SecMonitorConfig(enabled=True, start_date="2026-10-01", state_dir=str(tmp_path / "state"),
                                     watchlist_path=str(tmp_path / "watchlist.md")))
    source = TextSource()
    ctx = SimpleNamespace(
        config=cfg, deps=core_tx.Deps(filing_source=source), resumed=False, notify_cfg=object(),
        wall_now=lambda: datetime(2026, 10, 22, 23, tzinfo=UTC),
        sec_scan=lambda config, **kwargs: ({"tickers": ["NVDA", "MU", "AMD"], "errors": {"MU": "sec timeout"},
                                            "filings": [{"ticker": "NVDA", "form": "10-Q", "filing_date": "2026-10-01",
                                                         "accession_number": ACC}],
                                            "downloaded_count": 0, "event_ids": []}, "monitor"))
    state = {"run_id": "daily-r1", "trigger": "scheduled", "ny_date": "2026-10-22", "session": "2026-10-22",
             "cutoff_at": "2026-10-22T22:30:00+00:00", "summary": {}, "features": {"backup_sources": True}}
    acc = stages.StageAcc()

    out = sec_monitor.run(state, ctx, acc)

    # MU's scan failed (booked from the scan) and AMD's found nothing new (R27): neither is fetched again
    assert source.discovered == ["NVDA"]
    assert out["refs"]["sec_monitor"] == {"sec_text": ["NVDA"]}     # D47: the collect node's row skips only this
    assert len(source.downloads) == 1
    rows = workbench_db.execute("SELECT family, job, trigger, run_id, scope, session, status, target_count, ok_count, "
                                "failures, detail FROM data_fetch_runs").fetchall()
    assert len(rows) == 1                                   # one row, and no sec_filings row any more
    family, job, trigger, run_id, scope, session, status, targets, ok, failures, detail = rows[0]
    assert (family, job, trigger, run_id, scope, status) == ("sec_text", "daily", "scheduled", "daily-r1", "family", "partial")
    assert (targets, ok, session) == (3, 2, date(2026, 10, 22))        # NVDA fetched, AMD ok from the scan, MU failed
    assert failures == [{"target": "MU", "reason": "sec timeout"}]
    assert detail["scope"] == "sec_monitor" and detail["mode"] == "monitor"
    assert detail["source_counts"] == {"sec": 2} and detail["source_counts_complete"] is True   # NVDA fetched + AMD scanned
    nvda = next(t for t in detail["targets"] if t["target"] == "NVDA")
    assert nvda["ok"] is True and nvda["source_used"] == "sec" and nvda["rows"] == 1
    # the originals are indexed (filings row + analysis observation): what the old local-research refresh produced
    assert workbench_db.execute("SELECT ticker, form FROM filings").fetchall() == [("NVDA", "10-Q")]
    assert len(list((tmp_path / "filings" / "NVDA").rglob("*q.htm"))) == 1
    assert workbench_db.execute("SELECT security_id, dataset, natural_key FROM us_research_observations").fetchall() \
        == [("US:stock:NVDA", "filings", ACC)]
    assert acc.detail["research"]["results"][0]["ticker"] == "NVDA"
    assert acc.detail["research"]["results"][0]["status"] == "ready" and acc.detail["research"]["errors"] == []
