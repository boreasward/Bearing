from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from investment_assistant.config import AssistantConfig, load_config
from investment_assistant.core import redact as families
from investment_assistant.data import collect
from investment_assistant.data.acquisition import refresh_local_filings
from investment_assistant.data.sources.edgar import read_watchlist

FILINGS_LISTED_MAX = 50

def _monitor_targets(config: AssistantConfig) -> tuple[list[str], str | None]:
    """The monitor's actual collection list: the watchlist file's 采集=是 rows.

    Returns (targets, error): the error is kept in detail when even the list
    cannot be read, so a failure's denominator is honestly unknown, never the
    config.watchlist (I03)."""
    from investment_assistant.data.sources.edgar import read_watchlist

    try:
        return read_watchlist(Path(config.sec_monitor.watchlist_path)), None
    except Exception as exc:
        return [], str(exc)

def _record_fetch_run(result: dict[str, Any], *, config: AssistantConfig,
                      trigger_source: str, started_at: datetime, job: str = "daily",
                      trigger: str | None = None, wall_now=None) -> dict[str, Any]:
    """Append this run's one sec_filings row to data_fetch_runs (v2.3 §6.3).

    Targets are what this run actually collected (I03): the monitor reads its own
    watchlist file (summary.tickers on success, the file itself on failure); the
    downloader keeps config.watchlist. In the result protocol (I02): a per-ticker
    error fails that ticker; 'collection' is the whole-call failure sentinel '*'
    (no target is counted ok, '*' is not a target); non-ticker keys such as
    'delivery' are not fabricated targets and land in detail.non_target_errors.
    A write failure never changes the task result, and is printed to stderr as the
    searchable trail for the scheduled path, where the returned data_status is
    dropped (I08)."""
    summary = result.get("summary") or {}
    trigger = trigger or ("scheduled" if trigger_source == "scheduled" else "manual_batch")
    if config.sec_monitor.enabled:
        targets_source = "sec_monitor.watchlist_path"
        tickers = summary.get("tickers")
        if not isinstance(tickers, list) or not tickers:
            tickers, targets_error = _monitor_targets(config)
        else:
            targets_error = None
        mode = "monitor"
    else:
        targets_source = "config.watchlist"
        tickers, targets_error, mode = list(config.watchlist), None, "downloader"

    tally = collect.Tally("sec_filings", job, tickers)
    tally.detail = {"targets_source": targets_source, "mode": mode}
    if targets_error is not None:
        tally.detail["targets_error"] = families.sanitize_reason(targets_error)
    errors = summary.get("errors")
    call_failed = False
    non_target_errors: dict[str, str] = {}
    if isinstance(errors, dict):
        target_set = {t for t in tickers if isinstance(t, str)}
        for key, reason in errors.items():
            if key in target_set:
                tally.fail(key, reason)
            elif key == "collection":
                tally.fail("*", reason)   # whole call down: no target completed collection
                call_failed = True
            else:   # e.g. 'delivery': a real problem, but not a collection target
                non_target_errors[key] = families.sanitize_reason(reason)
        if not call_failed:
            for ticker in tickers:
                if ticker not in tally.outcomes:
                    tally.ok(ticker)
    elif isinstance(errors, list):
        for item in errors:
            if isinstance(item, dict):
                ticker = item.get("ticker")
                if isinstance(ticker, str) and ticker in set(tickers):
                    tally.fail(ticker, item.get("error", item))
                else:
                    tally.fail("*", item.get("error", item))
                    call_failed = True
            else:
                tally.fail("*", item)
                call_failed = True
        if not call_failed:
            for ticker in tickers:
                if ticker not in tally.outcomes:
                    tally.ok(ticker)
    elif summary.get("error"):
        tally.fail("*", summary["error"])   # 整次失败：没有任何目标成功
    else:
        for ticker in tickers:
            tally.ok(ticker)
    if non_target_errors:
        tally.detail["non_target_errors"] = non_target_errors
    data_status, ids = collect.write_runs_with_ids([tally.row(
        trigger=trigger,
        run_id=result.get("run_id"), session=None,
        started_at=started_at, finished_at=(wall_now or (lambda: datetime.now(UTC)))())])
    if data_status.get("error"):
        print(f"[data_fetch_runs] write failed family=sec_filings job={job} trigger={trigger} "
              f"error={data_status['error']}", file=sys.stderr)
    return {**data_status, "ids": ids}

def _account(result: dict[str, Any], mode: str, acc, *, config: AssistantConfig, ids: list[int]) -> None:
    """Fold a pass into the node's accumulator: counts, per-ticker errors, the (capped) filing list, references."""
    tickers = result.get("tickers")
    if not isinstance(tickers, list):
        tickers = _monitor_targets(config)[0] if mode == "monitor" else list(config.watchlist)
    acc.input(len(tickers))
    filings = [m for m in result.get("filings") or [] if isinstance(m, dict)]
    acc.output(len(filings))
    errors = result.get("errors")
    for key, reason in (errors.items() if isinstance(errors, dict) else []):
        acc.fail(f"sec:{key}", reason)
    delivered = (result.get("delivery") or {}).get("status") == "sent"
    acc.set_ref("filings", [{"ticker": m.get("ticker"), "form": m.get("form"), "filing_date": m.get("filing_date"),
                             "accession": m.get("accession_number") or m.get("accession"), "notified": delivered}
                            for m in filings[:FILINGS_LISTED_MAX]])
    if ids:
        acc.set_ref("data_fetch_runs", ids)
    receipts = (result.get("delivery") or {}).get("receipts") or []
    acc.detail.update(mode=mode, notifications={"delivery": (result.get("delivery") or {}).get("status"),
                                                "sent": sum(1 for r in receipts if r.get("status") == "sent")})
    if "after_cutoff_dropped" in result:
        acc.detail["after_cutoff_dropped"] = result["after_cutoff_dropped"]

def run(state: dict[str, Any], ctx, acc) -> dict[str, Any]:
    """The ``sec_monitor`` node body (spec §4.6): the monitor or the downloader, then the fetch-run row.

    Holds the ``sec_filings`` family lock for the node (so it never runs alongside a manual fetch of the Data menu) and,
    in monitor mode, the monitor's own state-file lock. Per-ticker errors make the node ``partial``; the local
    research refresh after it only degrades the node."""
    config = ctx.config if isinstance(getattr(ctx, "config", None), AssistantConfig) else load_config()
    now = datetime.fromisoformat(state["cutoff_at"])
    ny_date = date.fromisoformat(state["ny_date"])
    wall = getattr(ctx, "wall_now", None) or (lambda: datetime.now(UTC))
    started = wall()
    with collect.family_locks(["sec_filings"]):
        result, mode = ctx.sec_scan(config, start_date=None, end_date=None, ny_date=ny_date,
                             drop_after=now if ctx.resumed else None, notify_cfg=ctx.notify_cfg)
        trigger = state.get("trigger") or "scheduled"
        data_status = _record_fetch_run({"run_id": state.get("run_id"), "summary": result}, config=config,
                                        trigger_source="scheduled" if trigger == "scheduled" else "manual",
                                        started_at=started, wall_now=wall)
    _account(result, mode, acc, config=config, ids=data_status.get("ids") or [])
    research = _local_research_after_success({"summary": result}, config).get("research")
    if research is not None:
        acc.detail["research"] = research
        if research.get("errors"):
            acc.degrade("local_research_failed", "")
    summary = dict(state.get("summary") or {})
    summary["sec_monitor"] = {"mode": mode, "filings": len(result.get("filings") or []),
                              "errors": result.get("errors") or {}}
    return {"summary": summary}

def _local_research_after_success(result, config):
    """After original reporting/notifications, derive only indexed local files."""
    summary = result.get("summary", {})
    tickers = list(dict.fromkeys(m.get("ticker") for m in summary.get("filings", []) if m.get("ticker")))
    if not tickers:
        return result
    try:
        research = refresh_local_filings(tickers, config=config)
    except Exception:
        research = {"errors": [{"error": "美股本地原文研究刷新失败；原SEC结果保留"}], "notifications": "disabled"}
    return {**result, "research": research}
