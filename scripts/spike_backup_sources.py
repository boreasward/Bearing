"""One-time backup-source spike (v2.12 Task 0, Steps 6–7). Not part of the package.

Probes the two candidate backup sources with live read-only GET requests and prints the
facts that decide whether Task 3's ``data/sources/`` keeps them:

* Stooq daily CSV (``https://stooq.com/q/d/l/?s=<t>.us&i=d``) for AAPL / MSFT / NVDA /
  SPY / QQQ — column names, the last three rows, whether the latest date equals
  ``last_completed_trading_day()``, and whether the series is split-adjusted (NVDA's
  10:1 split of 2024-06-10: a 2024-06-07 close around 1208 is raw, around 121 adjusted).
* Nasdaq's JSON API (``/api/analyst/<t>/earnings-date`` and
  ``/api/calendar/earnings?date=<today>``) with a browser User-Agent and
  ``Accept: application/json`` — status codes and the response's field names.

Run from the repository root:  .venv/bin/python -I scripts/spike_backup_sources.py
Each source is requested once per endpoint (no retries by design); findings are recorded
by hand in docs/roadmap/v2.12-v2.15-acceptance.md §0.
"""
from __future__ import annotations

import csv
import io
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from investment_assistant.data.calendar import last_completed_trading_day  # noqa: E402

TICKERS = ('AAPL', 'MSFT', 'NVDA', 'SPY', 'QQQ')
STOOQ_URL = 'https://stooq.com/q/d/l/?s={ticker}.us&i=d'
NASDAQ_ANALYST_URL = 'https://api.nasdaq.com/api/analyst/{ticker}/earnings-date'
NASDAQ_CALENDAR_URL = 'https://api.nasdaq.com/api/calendar/earnings?date={today}'
HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'),
    'Accept': 'application/json, text/plain, */*',
}
TIMEOUT_S = 25
NVDA_SPLIT_DAY = date(2024, 6, 10)          # 10:1 split, first adjusted session
NVDA_LAST_RAW_DAY = date(2024, 6, 7)        # last session before the split


def spike_stooq() -> None:
    expected = last_completed_trading_day(datetime.now(UTC))
    print(f'== Stooq daily CSV (latest completed trading day: {expected}) ==')
    nvda_split_rows: dict[date, str] = {}
    for ticker in TICKERS:
        try:
            resp = requests.get(STOOQ_URL.format(ticker=ticker), timeout=TIMEOUT_S)
        except requests.RequestException as exc:
            print(f'{ticker}: REQUEST FAILED: {type(exc).__name__}: {exc}')
            continue
        print(f'{ticker}: HTTP {resp.status_code}, {len(resp.content)} bytes')
        if resp.status_code != 200:
            continue
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        if not rows:
            print(f'{ticker}: EMPTY CSV (columns: {resp.text.splitlines()[:1]})')
            continue
        columns = list(rows[0])
        latest = rows[-1]['Date']
        print(f'{ticker}: columns={columns}')
        for row in rows[-3:]:
            print(f'  {row}')
        print(f'{ticker}: latest date {latest} == last_completed_trading_day() {expected}: '
              f'{latest == expected.isoformat()}')
        if ticker == 'NVDA':
            by_date = {row['Date']: row['Close'] for row in rows}
            for day in (NVDA_LAST_RAW_DAY, NVDA_SPLIT_DAY):
                if day.isoformat() in by_date:
                    nvda_split_rows[day] = by_date[day.isoformat()]
    if nvda_split_rows:
        raw = nvda_split_rows.get(NVDA_LAST_RAW_DAY)
        first = nvda_split_rows.get(NVDA_SPLIT_DAY)
        print(f'NVDA split check: close {NVDA_LAST_RAW_DAY}={raw}, close {NVDA_SPLIT_DAY}={first}')
        if raw is not None:
            try:
                raw_close = float(raw)
            except ValueError:
                print('NVDA split check: close is not a number')
            else:
                verdict = 'split-adjusted (前一日收盘已除以 10)' if raw_close < 200 else \
                          'NOT split-adjusted (原始价)'
                print(f'NVDA split verdict: {verdict} ({NVDA_LAST_RAW_DAY} close = {raw})')
    else:
        print('NVDA split check: 2024-06-07/06-10 rows not returned')


def _nasdaq_fields(payload) -> str:
    if isinstance(payload, dict):
        return f'top-level keys: {sorted(payload)}; r/data keys: ' \
               f'{[sorted(v) if isinstance(v, dict) else type(v).__name__ for v in payload.values()]}'
    return f'body type: {type(payload).__name__}'


def spike_nasdaq() -> None:
    today = datetime.now(UTC).date()
    print(f'== Nasdaq JSON API (today: {today}) ==')
    for ticker in TICKERS:
        url = NASDAQ_ANALYST_URL.format(ticker=ticker)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT_S)
        except requests.RequestException as exc:
            print(f'analyst/{ticker}/earnings-date: REQUEST FAILED: {type(exc).__name__}: {exc}')
            continue
        print(f'analyst/{ticker}/earnings-date: HTTP {resp.status_code}, content-type '
              f'{resp.headers.get("content-type")}')
        if resp.status_code == 200:
            try:
                print(f'analyst/{ticker}/earnings-date: {_nasdaq_fields(resp.json())}')
            except json.JSONDecodeError:
                print(f'analyst/{ticker}/earnings-date: NOT JSON: {resp.text[:200]!r}')
    url = NASDAQ_CALENDAR_URL.format(today=today.isoformat())
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        print(f'calendar/earnings?date={today}: REQUEST FAILED: {type(exc).__name__}: {exc}')
        return
    print(f'calendar/earnings?date={today}: HTTP {resp.status_code}, content-type '
          f'{resp.headers.get("content-type")}')
    if resp.status_code == 200:
        try:
            print(f'calendar/earnings?date={today}: {_nasdaq_fields(resp.json())}')
        except json.JSONDecodeError:
            print(f'calendar/earnings?date={today}: NOT JSON: {resp.text[:200]!r}')


def main() -> None:
    print(f'spike run at {datetime.now(UTC).isoformat()}')
    spike_stooq()
    print()
    spike_nasdaq()


if __name__ == '__main__':
    main()
