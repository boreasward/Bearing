"""Strict SEC source used by the migrated monitor (no silent empty-on-error)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from lxml import html

from investment_assistant.data import fetch_policy
from investment_assistant.data.fetch_policy import FetchError, FetchPolicy

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_TICKERS_TARGET = "sec:company_tickers"
ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")
# acceptanceDateTime is UTC ("Z"). Verified against EDGAR: an after-hours Item 2.02 8-K
# with filingDate 2026-10-21 is stamped 2026-10-22T00:41:42Z, i.e. 20:41 New York time.
_ACCEPTANCE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?Z")


def edgar_acceptance(value) -> str | None:
    """ISO timestamp in UTC, or None when absent or malformed."""
    match = _ACCEPTANCE.fullmatch(value) if isinstance(value, str) else None
    if not match:
        return None
    return datetime.fromisoformat(match.group(1)).replace(tzinfo=timezone.utc).isoformat()


def edgar_items(value) -> list[str] | None:
    if not isinstance(value, str):
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def target_label(url: str) -> str:
    """The fetch target for an SEC URL (spec §6.2 row 4): ``sec:company_tickers``, ``sec:submissions:<CIK>``,
    ``sec:archive:<accession>``."""
    if "company_tickers" in url:
        return COMPANY_TICKERS_TARGET
    match = re.search(r"/submissions/(CIK\d+)", url)
    if match:
        return "sec:submissions:" + match.group(1)[3:]
    match = re.search(r"/Archives/edgar/data/\d+/(\d{18})/", url)
    if match:
        raw = match.group(1)
        return f"sec:archive:{raw[:10]}-{raw[10:12]}-{raw[12:]}"
    return "sec:other"


def drop_after_cutoff(metas: list[dict], cutoff: datetime | None) -> tuple[list[dict], int]:
    """Drop filings accepted after ``cutoff`` (spec §5.5, a resumed run must not see later filings).

    A filing without an acceptance time cannot be judged and is kept. Returns ``(kept, dropped)``."""
    if cutoff is None:
        return list(metas), 0
    kept = [m for m in metas if not (m.get("accepted_at") and datetime.fromisoformat(m["accepted_at"]) > cutoff)]
    return kept, len(metas) - len(kept)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("wb") as stream:
        os.chmod(temp, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def read_watchlist(path: Path) -> list[str]:
    """Only the current authoritative table's 采集=是 rows are in scope."""
    tickers: list[str] = []
    columns = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if "Ticker" in cells and "采集" in cells:
            columns = (cells.index("Ticker"), cells.index("采集"))
            continue
        if columns and len(cells) > max(columns) and cells[columns[1]] == "是":
            ticker = cells[columns[0]].upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", ticker):
                raise ValueError("SEC观察名单包含无效 ticker")
            if ticker not in tickers:
                tickers.append(ticker)
    if not tickers:
        raise ValueError("SEC观察名单没有有效的采集行；未使用缓存名单")
    return tickers


class EdgarSource:
    def __init__(self, evidence_dir: Path, *, session=None, policy: FetchPolicy = FetchPolicy(),
                 sleep=time.sleep, drop_after: datetime | None = None):
        self.evidence_dir = evidence_dir
        self.policy = policy
        self.session = session or fetch_policy.policy_session(policy)
        self.last_request = 0.0
        self.evidence: list[dict] = []
        self._sleep = sleep
        # Resumed runs ignore filings accepted after the original cutoff (spec §5.5); ``after_cutoff_dropped``
        # counts them for the node's detail.
        self.drop_after = drop_after
        self.after_cutoff_dropped = 0
        self._cik_by_ticker: dict[str, int] | None = None
        self._cik_error: FetchError | None = None

    def get(self, url: str) -> bytes:
        ua = os.environ.get("SEC_USER_AGENT", "")
        if "@" not in ua:
            raise ValueError("SEC_USER_AGENT 缺失或没有联系邮箱")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc not in {"www.sec.gov", "data.sec.gov"}:
            raise ValueError("拒绝非 SEC 来源 URL")

        def attempt() -> bytes:
            # SEC asks for at most ~5 requests a second: the throttle sits before every attempt, retries included.
            time.sleep(max(0.0, 0.21 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                resp = self.session.get(url, headers={"User-Agent": ua},
                                        timeout=(self.policy.connect_s, self.policy.total_s), allow_redirects=False)
            except requests.RequestException as exc:
                # Classify without logging proxy credentials or a complete request exception.
                detail = str(exc).lower()
                reason = "network_error"
                if "certificate_verify_failed" in detail:
                    reason = "certificate_validation_failed"
                elif "unexpected_eof" in detail or "eof occurred" in detail:
                    reason = "tls_peer_closed"
                elif "connection reset" in detail:
                    reason = "connection_reset"
                elif isinstance(exc, requests.exceptions.ProxyError):
                    reason = "proxy_unavailable"
                elif isinstance(exc, requests.Timeout):
                    reason = "request_timeout"
                kind = fetch_policy.classify_exception(exc)[0]
                raise FetchError(f"SEC 请求失败 ({type(exc).__name__}; {reason}): {url}",
                                 kind=kind if kind in ("timeout", "network") else "network") from None
            if resp.status_code != 200:
                retry = resp.headers.get("Retry-After", "")
                raise fetch_policy.http_error(
                    resp.status_code, f"SEC HTTP {resp.status_code}; Retry-After={retry or '无'}: {url}",
                    retry_after=retry or None)
            content = resp.content
            if not content:
                raise ValueError(f"SEC 返回空正文: {url}")
            return content

        content = fetch_policy.fetch(target_label(url), attempt, policy=self.policy, sleep=self._sleep).value
        digest = hashlib.sha256(content).hexdigest()
        dest = self.evidence_dir / (digest + ".response")
        if not dest.exists():
            atomic_write(dest, content)
        self.evidence.append({"url": url, "sha256": digest, "path": str(dest)})
        return content

    def json(self, url: str) -> dict:
        try:
            result = json.loads(self.get(url))
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"SEC JSON 无效: {url}") from exc
        if not isinstance(result, dict):
            raise ValueError(f"SEC JSON 结构无效: {url}")
        return result

    @staticmethod
    def rows(payload: dict) -> list[dict]:
        keys = ("form", "filingDate", "accessionNumber", "primaryDocument")
        if any(not isinstance(payload.get(k), list) for k in keys):
            raise ValueError("SEC submissions 缺少必需数组")
        count = len(payload["form"])
        if any(len(payload[k]) != count for k in keys):
            raise ValueError("SEC submissions 数组长度不一致")
        optional = [k for k in ("acceptanceDateTime", "items")
                    if isinstance(payload.get(k), list) and len(payload[k]) == count]
        return [{**{k: payload[k][i] for k in keys}, **{k: payload[k][i] for k in optional}}
                for i in range(count)]

    def _company_tickers(self) -> dict[str, int]:
        """``company_tickers.json`` is fetched once per source; a failure is remembered and re-raised for every
        later ticker (otherwise each of them would burn its own retries on the same dead request)."""
        if self._cik_by_ticker is not None:
            return self._cik_by_ticker
        if self._cik_error is not None:
            raise self._cik_error
        try:
            mapping = self.json(COMPANY_TICKERS_URL)
            self._cik_by_ticker = {str(x["ticker"]).upper(): int(x["cik_str"]) for x in mapping.values()}
        except FetchError as exc:
            exc.target = exc.target or COMPANY_TICKERS_TARGET
            self._cik_error = exc
            raise
        except Exception as exc:
            self._cik_error = FetchError(str(exc) or type(exc).__name__, kind="parse", target=COMPANY_TICKERS_TARGET,
                                         attempts=[{"n": 1, "kind": "parse", "status": 200, "elapsed_ms": 0}])
            raise self._cik_error from None
        return self._cik_by_ticker

    def discover_one(self, ticker: str, start: str, end: str, *, drop_after: datetime | None = None) -> list[dict]:
        """One ticker's 10-Q / 10-K / 8-K filings in ``[start, end]`` (a failure here never touches the others)."""
        cik_by_ticker = self._company_tickers()
        if ticker not in cik_by_ticker:
            raise ValueError(f"SEC 未找到 {ticker} 的 CIK")
        cik = cik_by_ticker[ticker]
        results: list[dict] = []
        data = self.json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
        filings = data.get("filings")
        if not isinstance(filings, dict) or not isinstance(filings.get("files"), list):
            raise ValueError(f"{ticker} submissions 缺少 filings/files")
        rows = self.rows(filings.get("recent", {}))
        for archive in filings["files"]:
            if not all(isinstance(archive.get(k), str) for k in ("filingFrom", "filingTo", "name")):
                raise ValueError(f"{ticker} 历史 submissions 索引无效")
            if archive["filingFrom"] <= end and archive["filingTo"] >= start:
                if not re.fullmatch(r"CIK\d+-submissions-\d+\.json", archive["name"]):
                    raise ValueError("SEC 历史文件名无效")
                rows.extend(self.rows(self.json("https://data.sec.gov/submissions/" + archive["name"])))
        seen: set[str] = set()
        for row in rows:
            form, accession = row["form"], row["accessionNumber"]
            if form not in {"10-Q", "10-K", "8-K"} or not start <= row["filingDate"] <= end:
                continue
            if ticker == "TSLA" and form == "10-K":
                continue
            if accession in seen:
                continue
            seen.add(accession)
            doc = row["primaryDocument"]
            if not ACCESSION.fullmatch(accession) or not re.fullmatch(r"[A-Za-z0-9_.-]+", doc):
                raise ValueError("SEC accession/主文文件名无效")
            base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"
            results.append({
                "ticker": ticker, "form": form, "filing_date": row["filingDate"],
                "accession_number": accession, "primary_document": doc,
                "edgar_url": base + doc, "index_url": base + accession + "-index.html",
                "accepted_at": edgar_acceptance(row.get("acceptanceDateTime")),
                "items": edgar_items(row.get("items")),
            })
        results.sort(key=lambda x: (x["filing_date"], x["ticker"], x["accession_number"]))
        results, dropped = drop_after_cutoff(results, drop_after or self.drop_after)
        self.after_cutoff_dropped += dropped
        return results

    def discover(self, tickers: list[str], start: str, end: str, *,
                 drop_after: datetime | None = None) -> tuple[list[dict], dict[str, str]]:
        """Every ticker's filings, one ticker at a time: ``(metas, {ticker: error})``.

        One ticker failing (CIK missing, a dropped connection) leaves the others alone. Only when
        ``company_tickers.json`` itself cannot be had does the whole call fail (the error is raised)."""
        metas: list[dict] = []
        errors: dict[str, str] = {}
        for ticker in tickers:
            try:
                metas.extend(self.discover_one(ticker, start, end, drop_after=drop_after))
            except FetchError as exc:
                if exc.target == COMPANY_TICKERS_TARGET:
                    raise
                errors[ticker] = str(exc)[:300]
            except Exception as exc:
                errors[ticker] = (str(exc) or type(exc).__name__)[:300]
        return sorted(metas, key=lambda x: (x["filing_date"], x["ticker"], x["accession_number"])), errors

    def documents(self, meta: dict) -> tuple[bool, list[tuple[str, bytes]]]:
        primary = self.get(meta["edgar_url"])
        documents = [(meta["primary_document"], primary)]
        if meta["form"] != "8-K":
            return True, documents
        page = html.fromstring(self.get(meta["index_url"]))
        tables = page.xpath('//table[contains(@summary,"Document Format Files")]')
        if not tables:
            raise ValueError("SEC 8-K 文件索引缺少 Document Format Files 表格")
        exhibits: list[str] = []
        for row in tables[0].xpath('.//tr'):
            cells = row.xpath('./td')
            if len(cells) >= 4 and cells[3].text_content().strip().upper() == "EX-99.1":
                links = cells[2].xpath('.//a/@href')
                if not links:
                    raise ValueError("SEC EX-99.1 缺少链接")
                url = urljoin(meta["index_url"], links[0])
                # Inline-XBRL links wrap the actual Archives URL.
                if "/ix?doc=" in url or "/ixviewer/doc/action?doc=" in url:
                    from urllib.parse import parse_qs
                    url = urljoin("https://www.sec.gov", parse_qs(urlparse(url).query)["doc"][0])
                if not url.startswith(meta["edgar_url"].rsplit("/", 1)[0] + "/"):
                    raise ValueError("SEC EX-99.1 链接不在本申报目录")
                exhibits.append(url)
        text = " ".join(html.fromstring(primary).text_content().split())
        selected = bool(re.search(r"\bItem\s*2\s*\.\s*02\b", text, re.IGNORECASE) or exhibits)
        if selected:
            for url in dict.fromkeys(exhibits):
                documents.append((Path(urlparse(url).path).name, self.get(url)))
        return selected, documents
