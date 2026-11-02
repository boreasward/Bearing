"""Nasdaq's undocumented analyst endpoint, using Task 0's announcement text shape."""
from __future__ import annotations

import re
from datetime import date

import requests

from investment_assistant.data import fetch_policy
from investment_assistant.data.fetch_policy import FetchError

URL = 'https://api.nasdaq.com/api/analyst/{ticker}/earnings-date'
HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'),
    'Accept': 'application/json, text/plain, */*',
}
_DATE = re.compile(
    r'\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|'
    r'Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+([0-9]{1,2}),\s+([0-9]{4})\b',
    re.IGNORECASE,
)
_MONTHS = {name: month for month, name in enumerate(
    ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}


def fetch_earnings_date(ticker, *, today) -> dict:
    """Return the existing earnings ``known/unknown`` shape; never retry inside this source."""
    target = f'earnings:{ticker.upper()}'
    try:
        with fetch_policy.policy_session() as client:
            response = client.get(URL.format(ticker=ticker.upper()), headers=HEADERS,
                                  timeout=(fetch_policy.CONNECT_TIMEOUT_S, fetch_policy.TOTAL_TIMEOUT_S),
                                  allow_redirects=False)
    except requests.RequestException as exc:
        kind, status = fetch_policy.classify_exception(exc)
        raise FetchError('Nasdaq 请求失败', kind=kind, status=status, target=target) from None
    if response.status_code != 200:
        raise fetch_policy.http_error(response.status_code, f'Nasdaq HTTP {response.status_code}',
                                      retry_after=response.headers.get('Retry-After'))
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError('payload')
        status = payload.get('status')
        code = status.get('rCode') if isinstance(status, dict) else None
        if isinstance(code, int) and code >= 400:
            raise fetch_policy.http_error(code, f'Nasdaq API {code}')
        data = payload.get('data')
        if data is not None and not isinstance(data, dict):
            raise ValueError('data')
        announcement = (data or {}).get('announcement')
        if announcement is not None and not isinstance(announcement, str):
            raise ValueError('announcement')
        upcoming = []
        for match in _DATE.finditer(announcement or ''):
            month, day, year = match.groups()
            value = date(int(year), _MONTHS[month[:3].lower()], int(day))
            if value >= today:
                upcoming.append(value)
    except (ValueError, TypeError):
        raise FetchError('Nasdaq 财报日 JSON 或日期无效', kind='parse', target=target) from None
    if not upcoming:
        return {'status': 'unknown', 'earnings_date': None}
    return {'status': 'known', 'earnings_date': min(upcoming)}
