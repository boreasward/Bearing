from __future__ import annotations

import json
import os
import re
import time
import fcntl
from copy import copy
from datetime import date
from pathlib import Path
from typing import Any, Callable

from investment_assistant.data import fetch_policy
from investment_assistant.data import http as data_http
from investment_assistant.data.fetch_policy import FetchError, FetchPolicy
from investment_assistant.data.sources.edgar import edgar_acceptance, edgar_items
from investment_assistant.data.sources.edgar import EdgarSource, atomic_write, ACCESSION
from investment_assistant.data import repo
from investment_assistant.data.adapter_types import Source
from investment_assistant.data.adapter_support import ONCE, make_adapter, sec_outcome, sec_source

SEC_TEXT_DAILY_MAX = 3

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
            getter = data_http.get_json
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


def select_filings(rows):
    ordered = sorted(rows, key=lambda r: (str(r['filing_date']), r['accession_number']), reverse=True)
    selected, counts, seen = [], {'10-K': 0, '10-Q': 0}, set()
    for row in ordered:
        if row['accession_number'] in seen:
            continue
        seen.add(row['accession_number'])
        form = row['form']
        if form in counts:
            cap = 1 if form == '10-K' else 2
            if counts[form] >= cap:
                continue
            counts[form] += 1
        elif form != '8-K' or '2.02' not in (row.get('items') or []):
            continue
        selected.append(row)
    return selected


def _fetch(target, *, session, start, end, deps):
    if deps.sec_text_dir is None:
        raise ValueError('sec_text_dir must be the configured persistent filings.output_dir')
    root = Path(deps.sec_text_dir)
    source = sec_source(deps)
    if isinstance(source, EdgarSource):
        # A private policy view: never mutate another family's shared retry policy.
        source = copy(source)
        source.policy = ONCE
        source.fetch_outcomes = []

    def fetch():
        budget_dir = root / '.budgets'
        budget_dir.mkdir(parents=True, exist_ok=True)
        budget_path = budget_dir / f'{target}-{session.isoformat()}.json'
        rows = []
        with budget_path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            budget = json.loads(budget_path.read_text()) if budget_path.exists() else {'attempted': [], 'timeout': False}
            if budget['timeout']:
                raise FetchError('SEC text target timed out this session; retry next session', kind='timeout')
            try:
                if isinstance(source, EdgarSource):
                    found = source.discover_one(target, start.isoformat(), end.isoformat(), include_annual=True)
                else:
                    found = source.discover_one(target, start.isoformat(), end.isoformat())
                selected = select_filings(found)
                with deps.transaction() as conn:
                    indexed = repo.indexed_filing_paths(conn, target)
                for meta in selected:
                    accession, doc = meta['accession_number'], meta['primary_document']
                    if not ACCESSION.fullmatch(accession) or not re.fullmatch(r'[A-Za-z0-9_.-]+', doc):
                        raise FetchError('invalid SEC filing identity', kind='parse')
                    existing = indexed.get(accession)
                    dest = (Path(existing) if existing and Path(existing).is_file()
                            else root / target / meta['form'] / f'{accession}-{doc}')
                    downloaded = False
                    if not dest.is_file():
                        if accession in budget['attempted'] or len(budget['attempted']) >= SEC_TEXT_DAILY_MAX:
                            continue
                        # Reserve before I/O: failures and interrupted processes also consume the cap.
                        budget['attempted'].append(accession)
                        atomic_write(budget_path, json.dumps(budget).encode())
                        content = source.get(meta['edgar_url'])
                        if not content:
                            raise FetchError('SEC text is empty', kind='empty')
                        atomic_write(dest, content)
                        downloaded = True
                    rows.append({**meta, 'path': str(dest), 'downloaded': downloaded})
            except Exception as exc:
                if fetch_policy.classify_exception(exc)[0] == 'timeout':
                    budget['timeout'] = True
                    atomic_write(budget_path, json.dumps(budget).encode())
                raise
        return rows
    return sec_outcome(source, fetch)


def _store(conn, rows, *, target, source_id, now):
    return repo.store_filing_texts(conn, rows, target=target, now=now)


def _latest(conn, targets, *, cutoff_at):
    return repo.latest_text_filing_dates(conn, targets, cutoff_at=cutoff_at)


ADAPTER = make_adapter('sec_text', 'SEC 原文', '最新10-K/10-Q与财报8-K原文',
    'filings + us_research_observations(filings)',
    (Source('sec', _fetch, ONCE, '每只每交易日最多3份，超时次日再试；复用已有正文'),),
    _store, _latest, days=120, coverage_note='覆盖 = 最近已存10-Q/10-K不早于session前120天。')
