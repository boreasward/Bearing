"""Normalization of the stored market snapshots behind the US research views; never sends notifications.

Refreshing local filings is the ``sec_text`` adapter's work (spec D47, R28), run by the ``facts`` collection and the
manual fetch."""
from __future__ import annotations

from investment_assistant.data import research_rows
from investment_assistant.data.fundamentals import stock_id

DATASETS = ('financials', 'market', 'filings')


def _identity(payload, ticker):
    identity = payload.get('identity') or {}
    if identity.get('security_id') != stock_id(ticker) or identity.get('ticker') != ticker:
        raise ValueError('来源证券身份不匹配')


def _market_rows(payload):
    snapshots = [research_rows.archive_snapshot(s) for s in payload['raw_snapshots']]
    snapshot_paths = {s['source_hash']: s['snapshot_path'] for s in snapshots}
    hashes = set(snapshot_paths)
    rows = []
    for group, bars in [('stock', payload['bars']), *payload.get('benchmarks', {}).items()]:
        for bar in bars:
            if bar.get('source_hash') not in hashes:
                raise ValueError('价格与原始来源摘要不匹配')
            rows.append({'natural_key': f'bar:{group}:{bar["date"]}', 'payload': {**bar, 'kind': 'bar', 'group': group, 'snapshot_path': snapshot_paths[bar['source_hash']]},
                'disclosed_at': bar['date'], 'source_url': bar.get('source_url'), 'source_hash': bar['source_hash']})
    quote = payload.get('quote') or {}
    if quote:
        if quote.get('source_hash') not in hashes:
            raise ValueError('报价与原始来源摘要不匹配')
        rows.append({'natural_key': 'quote:' + str(quote.get('date')), 'payload': {**quote, 'kind': 'quote', 'snapshot_path': snapshot_paths[quote['source_hash']]},
            'disclosed_at': quote.get('date'), 'source_url': quote.get('source_url'), 'source_hash': quote.get('source_hash')})
    return rows, {'acquisitions': snapshots, 'limitations': payload.get('limitations', [])}
