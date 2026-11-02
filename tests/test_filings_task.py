from datetime import UTC, date, datetime

import pytest

from investment_assistant.config import AssistantConfig, FilingsConfig, SecMonitorConfig
from investment_assistant.tasks import filings
from investment_assistant.workflow.nodes import sec_monitor


def test_core_summarizes_downloads(monkeypatch, tmp_path):
    def fake_download(tickers, cfg, *, downloader=None, start_date=None, end_date=None):
        return {"downloaded_count": 1, "filings": [{"ticker": "NVDA", "form": "10-Q", "filing_date": "2026-06-28", "path": "/x/NVDA/10-Q/a.htm", "downloaded": True}], "errors": {}}

    monkeypatch.setattr(filings, "download_configured_filings", fake_download)
    cfg = AssistantConfig(filings=FilingsConfig(output_dir=tmp_path))
    summary = filings._core(cfg)
    assert summary["downloaded_count"] == 1
    assert summary["filings"][0]["ticker"] == "NVDA"
    assert summary["filings"][0]["filing_date"] == "2026-06-28"


def test_core_passes_date_range(monkeypatch, tmp_path):
    seen = {}

    def fake_download(tickers, cfg, *, downloader=None, start_date=None, end_date=None):
        seen.update({"start_date": start_date, "end_date": end_date})
        return {"downloaded_count": 0, "filings": [], "errors": {}}

    monkeypatch.setattr(filings, "download_configured_filings", fake_download)
    cfg = AssistantConfig(filings=FilingsConfig(output_dir=tmp_path))
    filings._core(cfg, start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))
    assert seen == {"start_date": date(2026, 6, 1), "end_date": date(2026, 6, 30)}


def _collecting_write_runs(monkeypatch, rows):
    import investment_assistant.data.collect as collect

    def fake_write_runs(new_rows):
        rows.extend(new_rows)
        return {"written": len(new_rows), "error": None}, list(range(41, 41 + len(new_rows)))

    monkeypatch.setattr(collect, "write_runs_with_ids", fake_write_runs)


def _monitor_watchlist_file(tmp_path, tickers):
    """A real monitor watchlist table: only 采集=是 rows are in scope."""
    path = tmp_path / "watchlist.md"
    lines = ["| Ticker | 采集 |", "| --- | --- |"]
    for ticker, collect_it in tickers:
        lines.append(f"| {ticker} | {collect_it} |")
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _record(monkeypatch, summary, cfg, *, trigger_source="scheduled", run_id="daily-r1", **kwargs):
    rows = []
    _collecting_write_runs(monkeypatch, rows)
    status = sec_monitor._record_fetch_run({"run_id": run_id, "summary": summary}, config=cfg,
                                       trigger_source=trigger_source, started_at=datetime(2026, 10, 22, tzinfo=UTC),
                                       **kwargs)
    return rows, status


def test_downloader_records_partial_by_ticker(monkeypatch):
    rows, status = _record(monkeypatch, {"downloaded_count": 0, "filings": [], "errors": {"MU": "timeout"}},
                           AssistantConfig(watchlist=["MU", "NVDA"]), run_id="daily-r9")
    assert len(rows) == 1
    # D47 (v2.12): the monitor's row is the sec_text family's, scope sec_monitor
    assert rows[0]["family"] == "sec_text" and rows[0]["job"] == "daily"          # v2.8: the node's job
    assert rows[0]["status"] == "partial"
    assert rows[0]["failures"] == [{"target": "MU", "reason": "timeout"}]
    assert rows[0]["trigger"] == "scheduled" and rows[0]["run_id"] == "daily-r9" and rows[0]["session"] is None
    assert rows[0]["target_count"] == 2 and rows[0]["ok_count"] == 1
    detail = rows[0]["detail"]      # v2.8: the legacy Tally.ok/fail also write detail.policy / detail.targets
    assert {k: detail[k] for k in ("targets_source", "mode")} == {"targets_source": "config.watchlist", "mode": "downloader"}
    assert detail["policy"] == "fetch-1" and [t["target"] for t in detail["targets"]] == ["MU", "NVDA"]
    assert status == {"written": 1, "error": None, "ids": [41]}


def test_downloader_whole_failure_is_call_sentinel(monkeypatch):
    """A whole-run downloader failure keeps config.watchlist as the denominator;
    '*' is the sentinel, never an N+1th target (I02/I04)."""
    rows, _ = _record(monkeypatch, {"error": "downloader down"},
                      AssistantConfig(watchlist=["MU", "NVDA"]))
    assert rows[0]["status"] == "failed"
    assert rows[0]["target_count"] == 2 and rows[0]["ok_count"] == 0
    assert rows[0]["failures"] == [{"target": "*", "reason": "downloader down"}]
    assert rows[0]["detail"]["failed_targets"] == ["MU", "NVDA"]


def _monitor_cfg(tmp_path, tickers):
    return AssistantConfig(
        watchlist=["MU", "NVDA"],
        sec_monitor=SecMonitorConfig(enabled=True, start_date="2026-10-01",
                                      watchlist_path=_monitor_watchlist_file(tmp_path, tickers)))


def test_monitor_failure_uses_real_watchlist_targets(monkeypatch, tmp_path):
    """The monitor's real failure shape: coverage unavailable, errors.collection.
    Targets are the monitor's own watchlist file, never config.watchlist (I02/I03)."""
    rows, status = _record(monkeypatch, {"start_date": "2026-10-01", "end_date": "2026-10-22",
                                         "coverage": "unavailable", "errors": {"collection": "edgar down"},
                                         "filings": [], "downloaded_count": 0},
                           _monitor_cfg(tmp_path, [("AAPL", "是"), ("TSLA", "否")]), run_id="r1")
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["target_count"] == 1 and rows[0]["ok_count"] == 0   # 只有 AAPL；MU/NVDA 没被采集
    assert rows[0]["failures"] == [{"target": "*", "reason": "edgar down"}]
    # D47: the row says which scope wrote it; the tally always carries its final-source totals (R7), here none
    assert rows[0]["detail"] == {"targets_source": "sec_monitor.watchlist_path", "mode": "monitor",
                                 "scope": "sec_monitor", "source_counts": {}, "source_counts_complete": True,
                                 "failed_targets": ["AAPL"]}
    assert rows[0]["trigger"] == "scheduled" and rows[0]["run_id"] == "r1"
    assert status["written"] == 1


def test_monitor_success_uses_summary_tickers(monkeypatch, tmp_path):
    rows, _ = _record(monkeypatch, {"start_date": "2026-10-01", "end_date": "2026-10-22",
                                    "coverage": "complete_for_declared_universe", "tickers": ["AAPL"],
                                    "errors": {}, "filings": [], "downloaded_count": 0},
                      _monitor_cfg(tmp_path, [("AAPL", "是")]))
    assert rows[0]["status"] == "success"
    assert rows[0]["target_count"] == 1 and rows[0]["ok_count"] == 1
    assert rows[0]["failures"] == []
    # a scan-only booking (no adapter run) names no source: the totals say so (incomplete), never a made-up one
    assert {k: v for k, v in rows[0]["detail"].items() if k not in ("policy", "targets")} == {
        "targets_source": "sec_monitor.watchlist_path", "mode": "monitor", "scope": "sec_monitor",
        "source_counts": {}, "source_counts_complete": False}
    assert [t["target"] for t in rows[0]["detail"]["targets"]] == ["AAPL"]


def test_monitor_delivery_failure_is_not_a_target(monkeypatch, tmp_path):
    """Discord delivery failed after a complete collection: not a ticker, not ok'd
    away — reported separately in detail.non_target_errors (I02)."""
    rows, _ = _record(monkeypatch, {"start_date": "2026-10-01", "end_date": "2026-10-22",
                                    "coverage": "complete_for_declared_universe", "tickers": ["AAPL"],
                                    "errors": {"delivery": "Discord 投递失败或待核验；详见 delivery.receipts"},
                                    "filings": [], "downloaded_count": 0},
                      _monitor_cfg(tmp_path, [("AAPL", "是")]))
    assert rows[0]["status"] == "success"
    assert rows[0]["failures"] == [] and rows[0]["target_count"] == 1 and rows[0]["ok_count"] == 1
    assert rows[0]["detail"]["non_target_errors"] == {
        "delivery": "Discord 投递失败或待核验；详见 delivery.receipts"}


def test_monitor_failure_with_unreadable_watchlist_is_unknown_targets(monkeypatch, tmp_path):
    cfg = AssistantConfig(watchlist=["MU", "NVDA"],
                          sec_monitor=SecMonitorConfig(enabled=True, start_date="2026-10-01",
                                                        watchlist_path=str(tmp_path / "missing.md")))
    rows, _ = _record(monkeypatch, {"coverage": "unavailable", "errors": {"collection": "edgar down"},
                                    "filings": [], "downloaded_count": 0}, cfg)
    assert rows[0]["status"] == "failed"
    assert rows[0]["target_count"] == 0 and rows[0]["ok_count"] == 0   # 目标未知，不伪造分母
    assert rows[0]["failures"] == [{"target": "*", "reason": "edgar down"}]
    assert rows[0]["detail"]["targets_error"]


def test_trigger_maps_to_the_fetch_row(monkeypatch):
    cfg = AssistantConfig(watchlist=["NVDA"])
    rows, _ = _record(monkeypatch, {"downloaded_count": 0, "filings": [], "errors": {}}, cfg, trigger_source="manual")
    assert rows[0]["trigger"] == "manual_batch"
    rows, _ = _record(monkeypatch, {"downloaded_count": 0, "filings": [], "errors": {}}, cfg, trigger_source="manual",
                      job="sec_backfill", trigger="manual_family")
    assert (rows[0]["job"], rows[0]["trigger"]) == ("sec_backfill", "manual_family")


def test_record_failure_does_not_change_result(monkeypatch):
    import investment_assistant.data.collect as collect

    monkeypatch.setattr(collect, "write_runs_with_ids",
                        lambda new_rows: ({"written": 0, "error": "migration 024 未执行"}, []))
    status = sec_monitor._record_fetch_run({"summary": {"downloaded_count": 1, "filings": [], "errors": {}}},
                                       config=AssistantConfig(watchlist=["NVDA"]), trigger_source="scheduled",
                                       started_at=datetime(2026, 10, 22, tzinfo=UTC))
    assert status == {"written": 0, "error": "migration 024 未执行", "ids": []}


def test_record_failure_prints_searchable_stderr_line(monkeypatch, capsys):
    """The write failure needs a searchable trail on stderr (I08); the node's result is unchanged."""
    import investment_assistant.data.collect as collect

    monkeypatch.setattr(collect, "write_runs_with_ids",
                        lambda new_rows: ({"written": 0, "error": "permission denied"}, []))
    sec_monitor._record_fetch_run({"summary": {"downloaded_count": 0, "filings": [], "errors": {}}},
                              config=AssistantConfig(watchlist=["NVDA"]), trigger_source="scheduled",
                              started_at=datetime(2026, 10, 22, tzinfo=UTC))
    assert "family=sec_text job=daily trigger=scheduled error=permission denied" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------ the node body

class _Ctx:
    def __init__(self, resumed=False, config=None):
        self.resumed, self.config, self.notify_cfg = resumed, config, object()


STATE = {"run_id": "daily-20261022223000-0a1b2c3d", "trigger": "scheduled", "ny_date": "2026-10-22",
         "cutoff_at": "2026-10-22T22:30:00+00:00", "summary": {}}


def _node(monkeypatch, scan_result, mode, cfg, *, ctx=None, locks=None):
    from contextlib import contextmanager

    import investment_assistant.data.collect as collect
    import investment_assistant.core.stages as stages

    seen = {}
    rows = []
    _collecting_write_runs(monkeypatch, rows)

    def scan(config, **kwargs):
        seen["scan"] = kwargs
        seen["locked"] = bool(locks and locks[-1] == "open")
        return dict(scan_result), mode

    @contextmanager
    def family_locks(ids):
        (locks if locks is not None else []).append(("open", list(ids)))
        yield
        (locks if locks is not None else []).append(("closed", list(ids)))

    monkeypatch.setattr(collect, "family_locks", family_locks)
    acc = stages.StageAcc()
    ctx = ctx or _Ctx(config=cfg)
    ctx.sec_scan = scan
    ctx.deps = object()
    # The adapter run itself (sources, store, budgets) is covered against a real schema in test_sec_monitor; here the
    # node's own wiring is under test, so the hook just books the targets the scan left standing.
    def fake_collector(**kwargs):
        def collect_text(tally, tickers):
            seen["text"] = {"tickers": list(tickers), **kwargs}
            for ticker in tickers:
                tally.ok(ticker)
        return collect_text
    monkeypatch.setattr(sec_monitor, "text_collector", fake_collector)
    out = sec_monitor.run(dict(STATE), ctx, acc)
    return out, acc, seen, rows


def test_stage_sec_monitor_monitor_and_downloader(monkeypatch, tmp_path):
    monitor = {"tickers": ["AAPL", "MU"], "errors": {"MU": "sec timeout"}, "downloaded_count": 1, "event_ids": ["a"],
               "delivery": {"status": "sent", "receipts": [{"status": "sent"}, {"status": "failed"}]},
               "filings": [{"ticker": "AAPL", "form": "10-Q", "filing_date": "2026-10-21",
                            "accession_number": "0000320193-26-000010"}]}
    out, acc, seen, rows = _node(monkeypatch, monitor, "monitor", _monitor_cfg(tmp_path, [("AAPL", "是"), ("MU", "是")]))
    assert (acc.input_count, acc.output_count) == (2, 1)
    assert acc.failures == [{"target": "sec:MU", "reason": "sec timeout"}]
    assert acc.outputs["filings"] == [{"ticker": "AAPL", "form": "10-Q", "filing_date": "2026-10-21",
                                       "accession": "0000320193-26-000010", "notified": True}]
    assert acc.outputs["data_fetch_runs"] == [41]
    assert acc.detail["mode"] == "monitor" and acc.detail["notifications"] == {"delivery": "sent", "sent": 1}
    assert out["summary"]["sec_monitor"] == {"mode": "monitor", "filings": 1, "errors": {"MU": "sec timeout"}}
    assert rows[0]["job"] == "daily" and rows[0]["run_id"] == STATE["run_id"] and rows[0]["family"] == "sec_text"
    assert rows[0]["session"] == date(2026, 10, 22)
    assert seen["text"]["tickers"] == ["AAPL"] and seen["text"]["session"] == date(2026, 10, 22)   # MU's scan failed
    # the downloader branch: no delivery, the new downloads are the output
    downloaded = {"downloaded_count": 2, "errors": {}, "filings": [{"ticker": "MU", "form": "10-K", "filing_date": "2026-10-21",
                                                                  "path": "/x/MU.htm"}]}
    out, acc, _, rows = _node(monkeypatch, downloaded, "downloader", AssistantConfig(watchlist=["MU"]))
    assert (acc.input_count, acc.output_count) == (1, 1) and acc.detail["mode"] == "downloader"
    assert acc.outputs["filings"][0]["notified"] is False and acc.failures == []
    assert out["summary"]["sec_monitor"]["mode"] == "downloader"


def test_stage_sec_monitor_passes_the_resume_cutoff(monkeypatch, tmp_path):
    cfg = _monitor_cfg(tmp_path, [("AAPL", "是")])
    result = {"tickers": ["AAPL"], "errors": {}, "filings": [], "after_cutoff_dropped": 2}
    _, acc, seen, _ = _node(monkeypatch, result, "monitor", cfg)
    assert seen["scan"]["drop_after"] is None
    _, acc, seen, _ = _node(monkeypatch, result, "monitor", cfg, ctx=_Ctx(resumed=True, config=cfg))
    assert seen["scan"]["drop_after"] == datetime(2026, 10, 22, 22, 30, tzinfo=UTC)
    assert acc.detail["after_cutoff_dropped"] == 2


def test_sec_monitor_holds_family_lock(monkeypatch, tmp_path):
    locks: list = []
    cfg = _monitor_cfg(tmp_path, [("AAPL", "是")])
    _node(monkeypatch, {"tickers": ["AAPL"], "errors": {}, "filings": []}, "monitor", cfg, locks=locks)
    assert locks == [("open", ["sec_filings", "sec_text"]), ("closed", ["sec_filings", "sec_text"])]


def test_backfill_range_limit(monkeypatch):
    cfg = AssistantConfig()
    with pytest.raises(ValueError, match="92"):
        filings.backfill(cfg, start_date=date(2026, 1, 1), end_date=date(2026, 6, 1))
    with pytest.raises(ValueError, match="greater"):
        filings.backfill(cfg, start_date=date(2026, 10, 5), end_date=date(2026, 10, 1))
    assert filings.MAX_RANGE_DAYS == 92
