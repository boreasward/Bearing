"""News metadata from yfinance and SEC 8-K listings. Links are stored, bodies never fetched."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

from investment_assistant.data import fetch_policy

NewsFetcher = Callable[[str], list]
NEWS_COUNT = 20


def _yfinance_fetcher(ticker: str) -> list:
    # Ticker.get_news() hits an endpoint Yahoo now answers with 404, and yfinance turns that
    # into an empty list. The search endpoint still returns news, tagged with relatedTickers.
    import yfinance as yf

    # Shared session + timeout + thread backstop (fetch-1 spec §6.2 row 2).
    def call() -> list:
        return yf.Search(ticker, news_count=NEWS_COUNT, max_results=0, enable_fuzzy_query=False,
                         session=fetch_policy.shared_yf_session(), timeout=fetch_policy.TOTAL_TIMEOUT_S).news

    return fetch_policy.hard_deadline(call, seconds=fetch_policy.YF_HARD_DEADLINE_S)


def fetch_yfinance(ticker: str, *, fetcher: NewsFetcher | None = None) -> list:
    return (fetcher or _yfinance_fetcher)(ticker.upper())


def _url(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get('url')
    return value if isinstance(value, str) and value.startswith(('http://', 'https://')) else None


def _published(content: dict) -> datetime | None:
    raw = content.get('pubDate')
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else None
    epoch = content.get('providerPublishTime')
    if isinstance(epoch, (int, float)) and epoch > 0:
        return datetime.fromtimestamp(epoch, UTC)
    return None


def normalize_yfinance(raw: list, *, ticker: str | None = None) -> tuple[list[dict[str, Any]], int]:
    """Accept both the ``{'content': {...}}`` shape and the flat search shape.

    With ``ticker``, items whose relatedTickers omit it are skipped (search matches loosely).
    """
    items: list[dict[str, Any]] = []
    skipped = 0
    for entry in raw:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        related = entry.get('relatedTickers')
        if ticker and isinstance(related, list) and ticker.upper() not in related:
            skipped += 1
            continue
        content = entry.get('content') if isinstance(entry.get('content'), dict) else entry
        title = content.get('title')
        url = _url(content.get('canonicalUrl')) or _url(content.get('clickThroughUrl')) or _url(content.get('link'))
        if not isinstance(title, str) or not title.strip() or not url:
            skipped += 1
            continue
        provider = content.get('provider')
        publisher = provider.get('displayName') if isinstance(provider, dict) else content.get('publisher')
        items.append({'source': 'yfinance', 'title': title.strip(), 'url': url,
                      'publisher': publisher if isinstance(publisher, str) else None,
                      'published_at': _published(content)})
    return items, skipped


def filing_news(metas: list[dict[str, Any]]) -> list[tuple[dict[str, Any], list[str]]]:
    """8-K listings from the SEC monitor's discover() as (news item, tickers)."""
    out = []
    for meta in metas:
        if meta.get('form') != '8-K' or not _url(meta.get('edgar_url')):
            continue
        items = meta.get('items')
        title = f"{meta['ticker']} 8-K" + (f" (Items {', '.join(items)})" if items else '')
        accepted = meta.get('accepted_at')
        out.append(({'source': 'sec_8k', 'title': title, 'url': meta['edgar_url'], 'publisher': 'SEC EDGAR',
                     'published_at': datetime.fromisoformat(accepted) if accepted else None}, [meta['ticker']]))
    return out
