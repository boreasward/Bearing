from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from investment_assistant.config import AssistantConfig, load_config
from investment_assistant.core import redact as families, tx as core_tx
from investment_assistant.data import collect, registry as data_registry
from investment_assistant.data.adapter_support import adapter_deps_from_service
from investment_assistant.data.fetch_policy import Fetched
from investment_assistant.data.sources.edgar import read_watchlist
from investment_assistant.workflow.state import runtime_flags

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

SCOPE = "sec_monitor"          # spec D47: the scope of the adapter run behind the monitor's row


def _record_fetch_run(result: dict[str, Any], *, config: AssistantConfig,
                      trigger_source: str, started_at: datetime, job: str = "daily",
                      trigger: str | None = None, wall_now=None, session: date | None = None,
                      collect_text=None) -> dict[str, Any]:
    """Append this run's one ``sec_text`` row (``detail.scope = 'sec_monitor'``) to data_fetch_runs (v2.3 §6.3,
    v2.12 D47: the monitor's downloads and its local-research refresh are the ``sec_text`` adapter's work now).

    ``collect_text(tally, tickers)`` runs the adapter over the tickers whose scan did not fail AND found new filings
    (R27; the other clean tickers are booked ok by the scan itself) and books each target's own outcome (source,
    attempts, rows, downloaded originals) into the tally; without it (a unit-level call) a clean scan result books
    every clean target ok. A ticker or the whole scan that failed is booked as before, from the scan.

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

    tally = collect.Tally("sec_text", job, tickers)
    tally.detail = {"targets_source": targets_source, "mode": mode, "scope": SCOPE}
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
        clean = [] if call_failed else [t for t in tickers if t not in tally.outcomes]
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
        clean = [] if call_failed else [t for t in tickers if t not in tally.outcomes]
    elif summary.get("error"):
        tally.fail("*", summary["error"])   # 整次失败：没有任何目标成功
        clean = []
    else:
        clean = list(tickers)
    if non_target_errors:
        tally.detail["non_target_errors"] = non_target_errors
    if collect_text is not None:
        # R27: the adapter pass covers only the tickers this scan found new filings for (the scope of BASE's
        # local-research refresh); the daily collect node's sec_text row covers the rest.
        filed = {m.get("ticker") for m in summary.get("filings") or [] if isinstance(m, dict)}
        collect_text(tally, [ticker for ticker in clean if ticker in filed])
        for ticker in clean:
            if ticker not in filed:
                # the scan itself (EDGAR, source 'sec') succeeded and found nothing new to download
                tally.record(ticker, Fetched(None), rows=0, source_used="sec")
    else:
        for ticker in clean:
            tally.ok(ticker)
    data_status, ids = collect.write_runs_with_ids([tally.row(
        trigger=trigger,
        run_id=result.get("run_id"), session=session,
        started_at=started_at, finished_at=(wall_now or (lambda: datetime.now(UTC)))())])
    if data_status.get("error"):
        print(f"[data_fetch_runs] write failed family=sec_text job={job} trigger={trigger} "
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

def text_collector(*, deps, config: AssistantConfig, trigger: str, run_id: str | None, session: date,
                   now: datetime, features: dict[str, bool] | None):
    """The ``collect_text(tally, tickers)`` hook of ``_record_fetch_run``: the ``sec_text`` adapter over the tickers the
    scan left standing, ``scope = 'sec_monitor'`` (spec D47). Its downloads share the family's persistent per-ticker /
    per-session three-document budget with the daily ``collect`` node, reuse the originals the monitor archived
    (verified metadata, exhibits) and store the ``filings`` rows with their analysis observations — what the old
    local-research refresh did after the scan. ``deps`` is the host's ``core.tx.Deps``."""
    adapter = data_registry.get("sec_text")
    projected = adapter_deps_from_service(deps, transaction=core_tx._tx, config=config, sleep=collect.FETCH_SLEEP)

    def collect_text(tally, tickers: list[str]) -> None:
        if not tickers:
            return
        with core_tx._tx() as conn:
            targets = adapter.targets(conn, SCOPE, explicit=tickers)
        data_registry.run_adapter(adapter, targets, scope=SCOPE, trigger=trigger, run_id=run_id, session=session,
                                  now=now, deps=projected, features=runtime_flags(features), tally=tally)
    return collect_text


def research_summary(tally, handled: list[str]) -> dict[str, Any]:
    """What the node's ``detail.research`` says about the adapter run: one result per ticker it handled (a ticker whose
    scan failed is the scan's failure, reported there), the failed ones again as ``errors`` (the node then degrades,
    the scan's own result stands)."""
    results = []
    for ticker in handled:
        reason = tally.outcomes.get(ticker)
        record = tally.record_for(ticker)
        results.append({"ticker": ticker, "dataset": "filings", "status": "ready" if reason is None else "failed",
                        "rows": record.get("rows"), "source_used": record.get("source_used"),
                        **({"error": reason} if reason is not None else {})})
    return {"results": results, "errors": [r for r in results if r["status"] == "failed"],
            "notifications": "disabled"}


def run(state: dict[str, Any], ctx, acc) -> dict[str, Any]:
    """The ``sec_monitor`` node body (spec §4.6, D47): the monitor or the downloader scans and notifies, then the
    ``sec_text`` adapter (``scope = 'sec_monitor'``) downloads and indexes the originals of the tickers the scan left
    standing, and the fetch-run row — family ``sec_text``, ``detail.scope = 'sec_monitor'`` — records both.

    Holds the ``sec_filings`` (the scan writes the filing index) and ``sec_text`` family locks for the node, so it
    never runs alongside a manual fetch of the Data menu, and in monitor mode the monitor's own state-file lock.
    Per-ticker errors make the node ``partial``; the adapter's failures only degrade it."""
    config = ctx.config if isinstance(getattr(ctx, "config", None), AssistantConfig) else load_config()
    now = datetime.fromisoformat(state["cutoff_at"])
    ny_date = date.fromisoformat(state["ny_date"])
    session = date.fromisoformat(state["session"]) if state.get("session") else ny_date
    wall = getattr(ctx, "wall_now", None) or (lambda: datetime.now(UTC))
    started = wall()
    trigger = state.get("trigger") or "scheduled"
    research: dict[str, Any] = {}
    with collect.family_locks(["sec_filings", "sec_text"]):
        result, mode = ctx.sec_scan(config, start_date=None, end_date=None, ny_date=ny_date,
                             drop_after=now if ctx.resumed else None, notify_cfg=ctx.notify_cfg)
        data_status = _record_fetch_run(
            {"run_id": state.get("run_id"), "summary": result}, config=config,
            trigger_source="scheduled" if trigger == "scheduled" else "manual", started_at=started, wall_now=wall,
            session=session, collect_text=_remember(research, text_collector(
                deps=ctx.deps, config=config, trigger="scheduled" if trigger == "scheduled" else "manual_batch",
                run_id=state.get("run_id"), session=session, now=now, features=state.get("features"))))
    _account(result, mode, acc, config=config, ids=data_status.get("ids") or [])
    if research.get("tally") is not None:
        found = research_summary(research["tally"], research["handled"])
        if found["results"]:
            acc.detail["research"] = found
            if found["errors"]:
                acc.degrade("local_research_failed", "")
    summary = dict(state.get("summary") or {})
    summary["sec_monitor"] = {"mode": mode, "filings": len(result.get("filings") or []),
                              "errors": result.get("errors") or {}}
    out: dict[str, Any] = {"summary": summary}
    tally = research.get("tally")
    done = [t for t in research.get("handled") or [] if tally.outcomes.get(t, "?") is None] if tally else []
    if done:     # D47: the daily collect node's sec_text row covers the remaining targets and skips these
        out["refs"] = {**(state.get("refs") or {}), "sec_monitor": {"sec_text": done}}
    return out


def _remember(box: dict[str, Any], collect_text):
    """Wrap ``collect_text`` so the caller can read the tally it filled."""
    def wrapped(tally, tickers: list[str]) -> None:
        box["tally"], box["handled"] = tally, list(tickers)
        collect_text(tally, tickers)
    return wrapped
