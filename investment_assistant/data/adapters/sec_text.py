from __future__ import annotations

import json
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

from investment_assistant.data import fetch_policy
from investment_assistant.data.fetch_policy import FetchError, FetchPolicy
from investment_assistant.data.sources.edgar import edgar_acceptance, edgar_items

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{doc}"
Getter = Callable[..., tuple[dict[str, Any] | None, dict[str, Any]]]


def _archive_target(url: str) -> str:
    """``archive:<accession>`` for an EDGAR Archives URL (spec §6.2 row 8)."""
    match = re.search(r"/Archives/edgar/data/\d+/(\d{18})/", url)
    if not match:
        return "archive:" + url.rsplit("/", 1)[-1]
    raw = match.group(1)
    return f"archive:{raw[:10]}-{raw[10:12]}-{raw[12:]}"


def _download_document(url: str, dest: Path, *, headers: dict[str, str],
                       policy: FetchPolicy = FetchPolicy(), sleep=time.sleep) -> Path:
    def attempt() -> bytes:
        with fetch_policy.policy_session(policy) as session:
            resp = session.get(url, headers=headers)
        if resp.status_code >= 400:
            raise fetch_policy.http_error(resp.status_code, f"SEC HTTP {resp.status_code}: {url}",
                                          retry_after=resp.headers.get("Retry-After"))
        return resp.content

    content = fetch_policy.fetch(_archive_target(url), attempt, policy=policy, sleep=sleep).value
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return dest


def _failed(status: dict[str, Any], target: str) -> FetchError:
    """The ``get_json`` failure meta as a ``FetchError``: a failed request is not an empty answer."""
    return FetchError(str(status.get("error") or "SEC 请求失败"), kind=status.get("error_kind") or "other",
                      status=status.get("status_code"), attempts=status.get("attempts"), target=target)


class SecEdgarDownloader:
    def __init__(self, *, getter: Getter | None = None, cache_dir: Path | None = None):
        if getter is None:
            from investment_assistant.data import http

            getter = http.get_json
        self._get = getter
        self._cache_dir = Path(cache_dir) if cache_dir else None
        # company_tickers.json is fetched once per downloader (a failure is remembered too), spec §6.2 row 7.
        self._tickers: dict[str, Any] | None = None
        self._tickers_error: FetchError | None = None

    def _headers(self, ua: str) -> dict[str, str]:
        return {"User-Agent": ua}

    def _company_tickers(self, ua: str) -> dict[str, Any]:
        if self._tickers is not None:
            return self._tickers
        if self._tickers_error is not None:
            raise self._tickers_error
        cache = (self._cache_dir / "company_tickers.json") if self._cache_dir else None
        if cache and cache.exists():
            self._tickers = json.loads(cache.read_text(encoding="utf-8"))
            return self._tickers
        data, status = self._get(COMPANY_TICKERS_URL, headers=self._headers(ua))
        if not status.get("ok") or not data:
            self._tickers_error = (_failed(status, "sec:company_tickers") if not status.get("ok") else
                                   FetchError("SEC company_tickers 返回空内容", kind="empty",
                                              target="sec:company_tickers"))
            raise self._tickers_error
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data), encoding="utf-8")
        self._tickers = data
        return data

    def _resolve_cik(self, ticker: str, ua: str) -> str | None:
        data = self._company_tickers(ua)
        target = ticker.strip().upper()
        for entry in data.values():
            if str(entry.get("ticker", "")).upper() == target:
                return f"{int(entry['cik_str']):010d}"
        return None

    def download_filings_batch(
        self, ticker: str, form_types: list[str], start_date: date, end_date: date, output_base: Path
    ) -> list[dict[str, Any]]:
        ua = os.environ.get("SEC_USER_AGENT")
        if not ua:
            return []  # 优雅降级：无 UA 不下载
        cik = self._resolve_cik(ticker, ua)
        if not cik:
            return []
        payload, status = self._get(SUBMISSIONS_URL.format(cik=cik), headers=self._headers(ua))
        if not status.get("ok"):
            raise _failed(status, ticker.upper())     # a failed request must not look like zero hits
        if not payload:
            raise FetchError("SEC submissions 返回空内容", kind="empty", target=ticker.upper())
        recent = payload.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accns = recent.get("accessionNumber", [])
        docs = recent.get("primaryDocument", [])
        accepted = recent.get("acceptanceDateTime", [])
        items = recent.get("items", [])
        first_day = start_date.isoformat()
        last_day = end_date.isoformat()
        out: list[dict[str, Any]] = []
        for i, form in enumerate(forms):
            if form not in form_types or not (first_day <= str(dates[i]) <= last_day):
                continue
            accession = accns[i]
            doc = docs[i] if i < len(docs) and docs[i] else f"{accession}.txt"
            doc_name = Path(doc).name
            dest = Path(output_base) / ticker.upper() / form / f"{accession}-{doc_name}"
            downloaded = False
            if not dest.exists():  # 已存在则跳过下载，但仍返回元数据供索引回补
                url = ARCHIVE_DOC_URL.format(
                    cik_int=int(cik), accession_nodash=accession.replace("-", ""), doc=doc
                )
                _download_document(url, dest, headers=self._headers(ua))
                downloaded = True
            out.append({
                "ticker": ticker.upper(),
                "form": form,
                "filing_date": str(dates[i]),
                "accession_number": accession,
                "primary_document": doc_name,
                "path": str(dest),
                "downloaded": downloaded,
                "accepted_at": edgar_acceptance(accepted[i] if i < len(accepted) else None),
                "items": edgar_items(items[i] if i < len(items) else None),
            })
        return out
