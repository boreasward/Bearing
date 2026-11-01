from __future__ import annotations

import logging
import os
from pathlib import Path
from datetime import date, timedelta
from typing import Any, Protocol

from investment_assistant.config import FilingsConfig

logger = logging.getLogger(__name__)


class FilingDownloader(Protocol):
    def download_filings_batch(
        self, ticker: str, form_types: list[str], start_date: date, end_date: date, output_base
    ) -> list[dict[str, Any]]:
        ...


def _default_downloader() -> FilingDownloader:
    from investment_assistant.data.adapters.sec_text import SecEdgarDownloader

    return SecEdgarDownloader()


def download_configured_filings(
    tickers: list[str],
    cfg: FilingsConfig,
    *,
    downloader: FilingDownloader | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    dl = downloader or _default_downloader()
    first = start_date or (date.today() - timedelta(days=1))  # 默认昨日 T-1
    last = end_date or first
    filings_meta: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for raw in tickers:
        ticker = str(raw or "").strip().upper()
        if not ticker:
            continue
        try:
            filings_meta.extend(dl.download_filings_batch(ticker, list(cfg.forms), first, last, cfg.output_dir))
        except Exception as exc:  # 单标的失败不影响其余
            errors[ticker] = str(exc)
    index_error: str | None = None
    try:
        _persist_filing_index(filings_meta)
    except Exception as exc:  # 索引写入失败不拖垮下载结果
        logger.warning("filings index persist failed: %s", exc)
        index_error = str(exc)
    result = {
        "downloaded_count": sum(1 for m in filings_meta if m.get("downloaded")),
        "filings": filings_meta,
        "errors": errors,
    }
    if index_error is not None:
        result["index_error"] = index_error
    result["reports"] = _write_reports(filings_meta)
    return result


def _write_reports(filings_meta: list[dict[str, Any]]) -> list[str]:
    report_dir = os.environ.get("FILINGS_REPORT_DIR")
    if not report_dir or not filings_meta:
        return []
    from investment_assistant.data.sec.report import report_path, write_filing_report
    from investment_assistant.data.sec.extraction import analyze_files

    written: list[str] = []
    for meta in filings_meta:
        raw = Path(meta["path"]).read_text(encoding="utf-8", errors="replace")
        meta["analysis"] = analyze_files(meta, raw)
        dest = write_filing_report(Path(report_dir), meta, raw)
        # An interrupted run may already have published its report. Keep its link.
        written.append(str(dest or report_path(Path(report_dir), meta)))
    return written


def _persist_filing_index(filings_meta: list[dict[str, Any]]) -> None:
    if not filings_meta:
        return
    database_url = os.environ.get("INVESTMENT_ASSISTANT_DATABASE_URL")
    if not database_url:
        return
    from investment_assistant.db import connect, upsert_filing

    with connect(database_url) as conn:
        for meta in filings_meta:
            upsert_filing(
                conn,
                ticker=meta["ticker"],
                form=meta["form"],
                filing_date=date.fromisoformat(meta["filing_date"]),
                accession_number=meta["accession_number"],
                primary_document=meta["primary_document"],
                file_path=meta["path"],
                accepted_at=meta.get("accepted_at"),
                items=meta.get("items"),
            )
