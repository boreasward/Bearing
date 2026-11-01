"""Bounded public-source adapters with exact byte snapshots and sanitized transport errors.

Callers own snapshot persistence. No database, notifier, environment loading or global logger changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

from investment_assistant.data.http import _RETRYABLE, _backoff
from investment_assistant.data.adapters.sec_text import SecEdgarDownloader
from investment_assistant.data.fundamentals import default_forms, finite, normalize_companyfacts, observation_id, stock_id


class SourceError(RuntimeError):
    pass


class USSources:
    def __init__(self, *, session=None, cache_dir=None, max_retries=3, backoff_seconds=1.0):
        self.session = session or requests.Session()
        self.max_retries = max(0, min(int(max_retries), 3))
        self.backoff_seconds = backoff_seconds
        self.last_sec_request = 0.0
        self.downloader = SecEdgarDownloader(getter=self._json_getter, cache_dir=cache_dir)

    def _get(self, url, *, headers=None, params=None):
        # Reuse existing retry policy without get_json's full-exception/body logging.
        for attempt in range(self.max_retries + 1):
            if 'sec.gov/' in url:
                time.sleep(max(0, .21 - (time.monotonic() - self.last_sec_request)))
                self.last_sec_request = time.monotonic()
            try:
                response = self.session.get(url, headers=headers or {'User-Agent': 'investment-assistant-local-research/1.0'},
                                            params=params, timeout=30, allow_redirects=False)
            except requests.RequestException:
                if attempt < self.max_retries:
                    time.sleep(_backoff(attempt, self.backoff_seconds))
                    continue
                raise SourceError('Public source network request failed') from None
            if response.status_code != 200:
                if response.status_code in _RETRYABLE and attempt < self.max_retries:
                    time.sleep(_backoff(attempt, self.backoff_seconds))
                    continue
                raise SourceError(f'Public source HTTP {response.status_code}')
            try:
                payload = json.loads(response.content, parse_constant=lambda _: None)
            except (ValueError, UnicodeError):
                raise SourceError('Public source returned invalid JSON') from None
            if not isinstance(payload, dict):
                raise SourceError('Public source returned invalid object')
            return payload, {'source_url': url, 'source_hash': hashlib.sha256(response.content).hexdigest(),
                             'raw_snapshot': response.content, 'request_params': dict(params or {}), 'collected_at': datetime.now(timezone.utc).isoformat()}
        raise SourceError('Public source retries exhausted')

    def _json_getter(self, url, **kwargs):
        payload, _ = self._get(url, **kwargs)
        return payload, {'ok': True, 'error': None, 'status_code': 200}

    def companyfacts(self, ticker: str) -> dict:
        ticker = stock_id(ticker).split(':')[-1]
        ua = os.environ.get('SEC_USER_AGENT', '')
        if '@' not in ua:
            raise SourceError('SEC contact User-Agent is not configured')
        try:
            cik = self.downloader._resolve_cik(ticker, ua)
        except SourceError:
            raise
        except (ValueError, KeyError, TypeError, OSError):
            raise SourceError('SEC ticker identity/cache is invalid') from None
        if not cik:
            raise SourceError('SEC ticker identity not found')
        url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json'
        payload, origin = self._get(url, headers=self.downloader._headers(ua))
        try:
            valid_identity = int(payload.get('cik', -1)) == int(cik) and isinstance(payload.get('entityName'), str) and bool(payload['entityName'].strip())
        except (TypeError, ValueError):
            valid_identity = False
        if not valid_identity:
            raise SourceError('SEC company identity mismatch')
        if not isinstance(payload.get('facts'), dict) or not isinstance(payload['facts'].get('us-gaap'), dict):
            raise SourceError('SEC company facts structure unavailable')
        forms = default_forms(ticker)
        try:
            observations = normalize_companyfacts(payload, ticker=ticker, allowed_forms=forms)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise SourceError('SEC company facts structure invalid') from None
        for row in observations:
            row['source_hash'] = origin['source_hash']
            row.pop('id', None)
            row['id'] = observation_id(row)
        return {'identity': {'ticker': ticker, 'security_id': stock_id(ticker), 'cik': cik, 'name': payload['entityName']},
                'observations': observations, **origin,
                'coverage': {'kind': 'historical_backfill_current_version', 'allowed_forms': forms,
                             'observation_count': len(observations), 'start': min((r['end'] for r in observations), default=None),
                             'end': max((r['end'] for r in observations), default=None), 'complete': None},
                'limitations': ['Standard whole-company taxonomy facts only; segment/product metrics require original disclosures',
                                'Versions observed now are not strict historical point-in-time data'] +
                               (['TSLA 10-K excluded to preserve the existing SEC monitor source rule'] if ticker == 'TSLA' else [])}

    @staticmethod
    def _session_end(meta, tz, collection_day):
        # Optional session metadata is evidence only for its own exchange-local date.
        # No fixed 16:00 assumption: the provider can report an early close.
        periods = meta.get('currentTradingPeriod')
        regular = periods.get('regular') if isinstance(periods, dict) else None
        if not isinstance(regular, dict):
            return None
        start, end = finite(regular.get('start')), finite(regular.get('end'))
        if start is None or end is None or end <= start:
            return None
        try:
            opened, closed = datetime.fromtimestamp(start, tz), datetime.fromtimestamp(end, tz)
        except (ValueError, TypeError, OverflowError, OSError):
            return None
        return closed if opened.date() == closed.date() == collection_day else None

    def _chart(self, ticker):
        yahoo_symbol = ticker.replace('.', '-')
        url = f'https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}'
        payload, origin = self._get(url, params={'range': '2y', 'interval': '1d', 'includeAdjustedClose': 'true', 'events': 'div,splits'})
        chart = payload.get('chart')
        if not isinstance(chart, dict):
            raise SourceError('Yahoo chart structure invalid')
        if chart.get('error') or not isinstance(chart.get('result'), list) or len(chart['result']) != 1:
            raise SourceError('Yahoo chart unavailable')
        result = chart['result'][0]
        if not isinstance(result, dict):
            raise SourceError('Yahoo chart structure invalid')
        meta = result.get('meta')
        if not isinstance(meta, dict):
            raise SourceError('Yahoo chart metadata invalid')
        if str(meta.get('symbol', '')).upper() != yahoo_symbol:
            raise SourceError('Yahoo security identity mismatch')
        indicators = result.get('indicators')
        if not isinstance(indicators, dict):
            raise SourceError('Yahoo chart indicators invalid')
        quote_items = indicators.get('quote')
        if not isinstance(quote_items, list) or len(quote_items) != 1 or not isinstance(quote_items[0], dict):
            raise SourceError('Yahoo chart quote arrays invalid')
        quotes = quote_items[0]
        for field in ('open', 'high', 'low', 'close', 'volume'):
            if quotes.get(field) is not None and not isinstance(quotes[field], list):
                raise SourceError('Yahoo chart quote values invalid')
        adjusted_items = indicators.get('adjclose', [])
        if not isinstance(adjusted_items, list) or len(adjusted_items) > 1 or (
            adjusted_items and not isinstance(adjusted_items[0], dict)
        ):
            raise SourceError('Yahoo chart adjusted-price arrays invalid')
        adjusted = adjusted_items[0].get('adjclose', []) if adjusted_items else []
        if not isinstance(adjusted, list):
            raise SourceError('Yahoo chart adjusted-price values invalid')
        timestamps = result.get('timestamp', [])
        if not isinstance(timestamps, list):
            raise SourceError('Yahoo chart timestamps invalid')
        try:
            tz = ZoneInfo(meta['exchangeTimezoneName'])
            collected = datetime.fromisoformat(origin['collected_at']).astimezone(tz)
            session_end = self._session_end(meta, tz, collected.date())
            excluded_dates = []
            bars = []
            for i, timestamp in enumerate(timestamps):
                bar_day = datetime.fromtimestamp(timestamp, tz).date()
                if bar_day > collected.date():
                    raise SourceError('Yahoo chart contains a future-dated daily bar')
                if bar_day == collected.date() and (session_end is None or collected < session_end):
                    excluded_dates.append(bar_day.isoformat())
                    continue
                row = {'date': bar_day.isoformat(),
                       'currency': meta.get('currency'), 'source_url': url, 'source_hash': origin['source_hash']}
                for output, field in [('open', 'open'), ('high', 'high'), ('low', 'low'), ('close', 'close'), ('volume', 'volume')]:
                    values = quotes.get(field) or []
                    row[output] = finite(values[i]) if i < len(values) else None
                row['adjusted_close'] = finite(adjusted[i]) if i < len(adjusted) else None
                bars.append(row)
            quote_date = datetime.fromtimestamp(meta['regularMarketTime'], tz).date().isoformat() if meta.get('regularMarketTime') else None
        except (KeyError, ValueError, TypeError, IndexError, OverflowError):
            raise SourceError('Yahoo chart structure invalid') from None
        origin['bar_coverage'] = {'completed_daily_bars_only': True, 'exchange_timezone': meta['exchangeTimezoneName'],
                                  'collection_date': collected.date().isoformat(),
                                  'verified_session_end': session_end.isoformat() if session_end else None,
                                  'excluded_dates': sorted(set(excluded_dates))}
        origin['limitations'] = ([f'Excluded incomplete or unverified daily bar: {d}' for d in sorted(set(excluded_dates))])
        quote = {'date': quote_date, 'currency': meta.get('currency'), 'price': finite(meta.get('regularMarketPrice')),
                 'price_basis': 'provider_quote', 'shares': finite(meta.get('sharesOutstanding')),
                 'market_cap': finite(meta.get('marketCap')), 'market_cap_verified': False,
                 'share_class_basis': 'unverified', 'source_url': url, 'source_hash': origin['source_hash']}
        return bars, quote, origin

    def market(self, ticker: str) -> dict:
        ticker = stock_id(ticker).split(':')[-1]
        bars, quote, origin = self._chart(ticker)
        benchmarks, snapshots = {}, [origin]
        limitations = ['Current adjusted-price backfill, not historical adjustment versions',
                       'Provider quote/share-class compatibility unverified; no inferred or historical market capitalization']
        limitations.extend(origin['limitations'])
        for symbol in ['SPY', 'QQQ']:
            try:
                benchmark, _, snapshot = self._chart(symbol)
                benchmarks[symbol] = benchmark
                snapshots.append(snapshot)
                limitations.extend(f'{symbol}: {item}' for item in snapshot['limitations'])
            except SourceError as exc:
                benchmarks[symbol] = []
                limitations.append(f'{symbol} unavailable: {exc}')
        return {'identity': {'ticker': ticker, 'security_id': stock_id(ticker)}, 'bars': bars, 'benchmarks': benchmarks,
                'quote': quote, 'source_url': origin['source_url'], 'collected_at': origin['collected_at'],
                'source_hash': origin['source_hash'], 'raw_snapshots': snapshots, 'limitations': limitations}
