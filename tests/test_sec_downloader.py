from datetime import date

from investment_assistant.data.adapters import sec_text as sec_downloader
from investment_assistant.data.adapters.sec_text import SecEdgarDownloader


def make_getter(tickers_json, submissions_json):
    def _getter(url, **kw):
        if "company_tickers.json" in url:
            return tickers_json, {"ok": True, "error": None, "status_code": 200}
        if "submissions" in url:
            return submissions_json, {"ok": True, "error": None, "status_code": 200}
        return None, {"ok": False, "error": "404", "status_code": 404}
    return _getter


def test_download_filters_by_form_and_yesterday(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "test test@example.com")
    tickers_json = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"}}
    submissions_json = {"filings": {"recent": {
        "form": ["10-Q", "8-K", "10-K"],
        "filingDate": ["2026-06-28", "2026-06-28", "2026-06-01"],
        "accessionNumber": ["acc-q", "acc-8k", "acc-k"],
        "primaryDocument": ["q.htm", "8k.htm", "k.htm"],
    }}}
    getter = make_getter(tickers_json, submissions_json)
    written = []
    monkeypatch.setattr(sec_downloader, "_download_document",
                        lambda url, dest, *, headers: (written.append((url, dest)) or dest))

    dl = SecEdgarDownloader(getter=getter, cache_dir=tmp_path)
    out = dl.download_filings_batch(
        "AAPL", ["10-Q", "10-K"], date(2026, 6, 28), date(2026, 6, 28), tmp_path / "filings"
    )
    # 仅 2026-06-28 的 10-Q 命中（10-K 日期不符、8-K 表单不符）
    assert len(out) == 1
    assert out[0]["form"] == "10-Q"
    assert out[0]["ticker"] == "AAPL"
    assert out[0]["filing_date"] == "2026-06-28"
    assert out[0]["accession_number"] == "acc-q"
    assert out[0]["primary_document"] == "q.htm"
    assert out[0]["downloaded"] is True


def test_download_date_range_hits_multiple_days(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "test test@example.com")
    tickers_json = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"}}
    submissions_json = {"filings": {"recent": {
        "form": ["10-Q", "8-K", "10-K"],
        "filingDate": ["2026-06-28", "2026-06-28", "2026-06-01"],
        "accessionNumber": ["acc-q", "acc-8k", "acc-k"],
        "primaryDocument": ["q.htm", "8k.htm", "k.htm"],
    }}}
    getter = make_getter(tickers_json, submissions_json)
    monkeypatch.setattr(sec_downloader, "_download_document",
                        lambda url, dest, *, headers: dest)

    dl = SecEdgarDownloader(getter=getter, cache_dir=tmp_path)
    out = dl.download_filings_batch(
        "AAPL", ["10-Q", "10-K"], date(2026, 6, 1), date(2026, 6, 28), tmp_path / "filings"
    )
    # 范围覆盖 06-01 与 06-28：10-K 与 10-Q 均命中
    assert len(out) == 2


def test_download_degrades_without_user_agent(tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    dl = SecEdgarDownloader(getter=lambda *a, **k: (None, {"ok": False}), cache_dir=tmp_path)
    out = dl.download_filings_batch(
        "AAPL", ["10-Q"], date(2026, 6, 28), date(2026, 6, 28), tmp_path / "filings"
    )
    assert out == []


def test_download_skips_existing_file_but_returns_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "test test@example.com")
    tickers_json = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"}}
    submissions_json = {"filings": {"recent": {
        "form": ["10-Q"],
        "filingDate": ["2026-06-28"],
        "accessionNumber": ["acc-q"],
        "primaryDocument": ["q.htm"],
    }}}
    dest = tmp_path / "filings" / "AAPL" / "10-Q" / "acc-q-q.htm"
    dest.parent.mkdir(parents=True)
    dest.write_text("<html>cached</html>")
    called = []
    monkeypatch.setattr(sec_downloader, "_download_document",
                        lambda url, d, *, headers: called.append(url))

    dl = SecEdgarDownloader(getter=make_getter(tickers_json, submissions_json), cache_dir=tmp_path)
    out = dl.download_filings_batch(
        "AAPL", ["10-Q"], date(2026, 6, 28), date(2026, 6, 28), tmp_path / "filings")
    assert called == []  # 未重复下载
    assert out[0]["downloaded"] is False
    assert out[0]["path"] == str(dest)


def test_download_records_acceptance_time_and_items(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "test test@example.com")
    tickers_json = {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA"}}
    submissions_json = {"filings": {"recent": {
        "form": ["8-K", "10-Q"], "filingDate": ["2026-08-27", "2026-08-27"],
        "accessionNumber": ["acc-8k", "acc-q"], "primaryDocument": ["k.htm", "q.htm"],
        "acceptanceDateTime": ["2026-08-27T16:20:31.000Z", "bad"], "items": ["2.02,9.01", ""],
    }}}
    monkeypatch.setattr(sec_downloader, "_download_document", lambda url, dest, *, headers: dest)
    out = SecEdgarDownloader(getter=make_getter(tickers_json, submissions_json), cache_dir=tmp_path).download_filings_batch(
        "NVDA", ["8-K", "10-Q"], date(2026, 8, 27), date(2026, 8, 27), tmp_path / "filings")
    assert [(o["accepted_at"], o["items"]) for o in out] == [("2026-08-27T16:20:31+00:00", ["2.02", "9.01"]), (None, [])]
