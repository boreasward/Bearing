"""SEC monitoring as the first node of the ``daily`` workflow (v2.8 §4.6) and the manual-only ``sec_backfill`` task.

``workflow.nodes.sec_monitor.run`` is the node body; ``backfill`` runs the same
body over a date range, outside the graph, under the ``daily:run`` lock."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from investment_assistant.config import AssistantConfig, load_config
from investment_assistant.core import locks, tx as core_tx
from investment_assistant.data.adapters import bars
from investment_assistant.data.sec.storage import download_configured_filings
from investment_assistant.data.sec import monitor
from investment_assistant.data.sources.edgar import EdgarSource
from investment_assistant.notify.discord_bot import DeliveryError, DiscordBotClient
from investment_assistant.workflow.nodes import sec_monitor

NY = ZoneInfo("America/New_York")
FILINGS_LISTED_MAX = 50        # filings kept in the node's outputs
MAX_RANGE_DAYS = 92


def _deliver(state: dict, save, event_ids: list[str], client) -> list[dict]:
    receipts = []
    for event_id in dict.fromkeys(event_ids):
        event = state["events"][event_id]
        status = event["status"]
        if status == "sending":
            # A crash between the HTTP send and local commit is ambiguous.
            event["status"] = status = "unknown"
            save()
        if status not in {"sent", "unknown"}:
            event["status"] = "sending"
            save()
            try:
                event["receipt"] = client.send(event["content"], event_id)
                event["status"] = "sent"
                event.pop("error", None)
            except DeliveryError as exc:
                event["status"] = "unknown" if exc.uncertain else "failed"
                event["error"] = str(exc)
            except Exception:
                event["status"] = "unknown"
                event["error"] = "Discord 未预期错误；投递待核验"
            save()
        receipts.append({"event_id": event_id, "status": event["status"],
                         **event.get("receipt", {}), **({"error": event["error"]} if event.get("error") else {})})
    return receipts


def scan_and_notify(config, start: date, end: date, *, source=None, client=None, notify_cfg=None,
                    persist=None, reports=None) -> dict:
    cfg = config.sec_monitor
    if not cfg.state_dir:
        raise ValueError("SEC monitor state_dir 未配置")
    directory = Path(cfg.state_dir)
    source = source or EdgarSource(directory / "evidence")
    client = client or DiscordBotClient(cfg.discord_channel_id)
    if notify_cfg is None:
        from investment_assistant.services.settings import effective_notify_config
        notify_cfg = effective_notify_config(config.notify)
    with monitor.locked_state(directory) as (state, save):
        try:
            result = monitor.collect(config, start, end, state, save, source, persist=persist, reports=reports)
        except Exception as exc:
            result = {"start_date": start.isoformat(), "end_date": end.isoformat(), "coverage": "unavailable",
                      "errors": {"collection": str(exc)}, "filings": [], "downloaded_count": 0, "event_ids": []}
        result["evidence"] = source.evidence
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        scan_key = f"{today}:{start}:{end}"
        if result["errors"]:
            if "collection" in result["errors"]:
                detail = (f"{str(result['errors']['collection'])[:800]}\n未能完成查源/归档，不能判断为零命中。"
                          "成功查找记录未前移。")
            else:
                failed = "; ".join(f"{ticker}: {error}" for ticker, error in sorted(result["errors"].items()))
                detail = (f"部分股票查源失败：{failed[:700]}\n其余股票已正常处理（归档与通知照常）；覆盖不完整，"
                          "不能判断为零命中，成功查找记录未前移。")
            title = "SEC 采集失败" if "collection" in result["errors"] else "SEC 采集部分失败"
            content = (f"【{title}】本地投研工作台 · {today}\n范围 {start} 至 {end}（美东申报日期）\n"
                       f"{detail}运行记录：http://127.0.0.1:8787/")
            result["event_ids"].append(monitor._event(state, "failure:" + today + ":" + content, content))
        elif (not state.get("notified_scans", {}).get(scan_key)
              and not any(state["events"][i]["status"] != "sent" for i in result["event_ids"])):
            # Only a complete scan can assert no new qualifying filings.
            content = (f"【SEC】{today} 观察名单无新符合规则的 10-Q/10-K/8-K。\n"
                       f"已完整检查 {len(result['tickers'])} 只，范围 {start} 至 {end}（美东申报日期），"
                       f"已知申报不重复推送。历史待核验 {result['legacy_pending_verification']} 条。\n"
                       "来源：https://www.sec.gov/edgar/search/")
            result["event_ids"].append(monitor._event(state, f"zero:{today}:{start}:{end}", content))
        save()
        if notify_cfg.channel != "discord" or not notify_cfg.discord_enabled or notify_cfg.task_enabled.get("filings") is False:
            result["delivery"] = {"status": "disabled", "receipts": []}
        else:
            receipts = _deliver(state, save, result["event_ids"], client)
            failed = any(r["status"] != "sent" for r in receipts)
            result["delivery"] = {"status": "needs_attention" if failed else "sent", "receipts": receipts}
            if failed:
                result["errors"]["delivery"] = "Discord 投递失败或待核验；详见 delivery.receipts"
            elif not result["errors"]:
                state.setdefault("notified_scans", {})[scan_key] = True
                save()
        return result


def run_monitor(config, *, start_date=None, end_date=None, trigger_source="scheduled") -> dict:
    from investment_assistant.tasks._harness import run_task

    start = start_date or date.fromisoformat(config.sec_monitor.start_date)
    end = end_date or (start_date if start_date else datetime.now(ZoneInfo("America/New_York")).date())
    if start > end:
        raise ValueError("SEC 起始日期晚于结束日期")
    return run_task("filings", lambda: scan_and_notify(config, start, end), config=config,
                    target_start_date=start, target_end_date=end, trigger_source=trigger_source, notify=False)


def _core(
    config: AssistantConfig,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    result = download_configured_filings(
        config.watchlist, config.filings, start_date=start_date, end_date=end_date
    )
    return {
        "downloaded_count": result.get("downloaded_count", 0),
        "filings": [
            {"ticker": m.get("ticker"), "form": m.get("form"),
             "filing_date": m.get("filing_date"), "path": m.get("path")}
            for m in result.get("filings", [])
        ],
        "errors": result.get("errors", {}),
    }






def _monitor_range(config: AssistantConfig, ny_date: date) -> tuple[date, date]:
    """The monitor's scan range as the scheduled ``filings`` task had it: the configured start date to New York today."""
    start = date.fromisoformat(config.sec_monitor.start_date)
    if start > ny_date:
        raise ValueError("SEC 起始日期晚于结束日期")
    return start, ny_date


def _scan(config: AssistantConfig, *, start_date: date | None, end_date: date | None, ny_date: date,
          drop_after: datetime | None, notify_cfg: Any) -> tuple[dict[str, Any], str]:
    """One SEC pass: the monitor (scan, archive, extract, direct delivery) when ``sec_monitor`` is enabled, else the
    downloader for the natural T-1. Returns ``(result, mode)``; ``drop_after`` (a resumed run's cutoff) makes the monitor
    ignore filings accepted after it."""
    if config.sec_monitor.enabled:
        start, end = (start_date, end_date) if start_date else _monitor_range(config, ny_date)
        end = end or start
        if start > end:
            raise ValueError("SEC 起始日期晚于结束日期")
        source = (EdgarSource(Path(config.sec_monitor.state_dir) / "evidence", drop_after=drop_after)
                  if drop_after is not None and config.sec_monitor.state_dir else None)
        result = scan_and_notify(config, start, end, source=source, notify_cfg=notify_cfg)
        if source is not None:
            result["after_cutoff_dropped"] = source.after_cutoff_dropped
        return result, "monitor"
    target = start_date or (ny_date - timedelta(days=1))          # natural target day T-1
    return _core(config, start_date=target, end_date=end_date or target), "downloader"






def backfill(config: AssistantConfig, *, start_date: date, end_date: date | None = None,
             trigger_source: str = "manual", deps: core_tx.Deps | None = None) -> dict[str, Any]:
    """The manual-only ``sec_backfill`` task: the same pass over a date range (at most 92 days), outside the graph.

    Holds the ``daily:run`` lock (``DailyBusy`` when a run is in progress), writes one ``data_fetch_runs`` row
    (family ``sec_text``, ``detail.scope = 'sec_monitor'``, ``job = 'sec_backfill'``, trigger ``manual_family``) and one
    ``job_reports`` row (``task = 'sec_backfill'``). ``deps`` injects the sources (tests)."""
    from investment_assistant.tasks._harness import run_task

    end_date = end_date or start_date
    if end_date < start_date:
        raise ValueError("to must be greater than or equal to from")
    if (end_date - start_date).days > MAX_RANGE_DAYS:
        raise ValueError(f"date range is limited to {MAX_RANGE_DAYS} days")
    ny_today = datetime.now(NY).date()

    def body(run_id: str) -> dict[str, Any]:
        from investment_assistant.services.settings import effective_notify_config

        started = datetime.now(UTC)
        result, mode = _scan(config, start_date=start_date, end_date=end_date, ny_date=ny_today, drop_after=None,
                             notify_cfg=effective_notify_config(config.notify))
        now = datetime.now(UTC)
        session = bars.latest_closed_session(now)
        found: dict[str, Any] = {}
        # D47: the pass's downloads and its research refresh are the sec_text adapter's work (scope sec_monitor)
        collect_text = sec_monitor._remember(found, sec_monitor.text_collector(
            deps=deps or core_tx.Deps(), config=config, trigger="manual_family", run_id=run_id, session=session, now=now,
            features=None))
        data_status = sec_monitor._record_fetch_run({"run_id": run_id, "summary": result}, config=config,
                                        trigger_source=trigger_source, started_at=started, job="sec_backfill",
                                        trigger="manual_family", session=session, collect_text=collect_text)
        out = {**result, "mode": mode, "data_status": data_status}
        research = (sec_monitor.research_summary(found["tally"], found["handled"])
                    if found.get("tally") is not None else None)
        if research is not None and research["results"]:
            out["research"] = research
        return out

    with locks.exclusive():
        return run_task("sec_backfill", body, config=config, target_start_date=start_date,
                        target_end_date=end_date, trigger_source=trigger_source, notify=False, pass_run_id=True)




def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser(description="SEC 回补（按日期，只手动运行）")
    parser.add_argument("--config", default=None)
    parser.add_argument("--date", default=None, help="回补指定单日（YYYY-MM-DD）")
    parser.add_argument("--from", dest="from_date", default=None, help="范围起始日（YYYY-MM-DD）")
    parser.add_argument("--to", dest="to_date", default=None, help="范围结束日（YYYY-MM-DD）")
    args = parser.parse_args()
    if args.date:
        start = end = date.fromisoformat(args.date)
    elif args.from_date and args.to_date:
        start, end = date.fromisoformat(args.from_date), date.fromisoformat(args.to_date)
    else:
        parser.error("必须指定 --date 或 --from 与 --to")
    print(json.dumps(backfill(load_config(args.config), start_date=start, end_date=end, trigger_source="manual"),
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
