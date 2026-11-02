"""Explicit collection and local-only US research views; never sends notifications."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from investment_assistant.config import load_config
from investment_assistant.core import tx as core_tx
from investment_assistant.db import list_filings, list_watchlist_items
from investment_assistant.data import repo, research_rows
from investment_assistant.data.fundamentals import stock_id

DATASETS = ('financials', 'market', 'filings')
_ACCESSION = re.compile(r'\d{10}-\d{2}-\d{6}')


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


def _owned_path(raw, root, ticker, accession):
    path = Path(raw).resolve()
    if not path.is_relative_to(root / ticker) or accession not in path.name or not path.is_file():
        raise ValueError('本地原文缺失或证券归属无效')
    return str(path)


def _verified_sec_url(raw, accession):
    url = urlsplit(raw or '')
    if (url.scheme == 'https' and url.hostname in {'www.sec.gov', 'sec.gov'} and not url.username
            and not url.password and not url.query and not url.fragment
            and re.fullmatch(r'/Archives/edgar/data/\d+/' + accession.replace('-', '') + r'/[^/]+', url.path)):
        return raw
    return ''


def _local_filing_rows(conn, ticker, *, config=None):
    """Read indexed original documents and verified monitor metadata only."""
    from investment_assistant.data.sec.extraction import analyze_files
    config = config if config is not None else load_config()
    root = config.filings.output_dir.resolve()
    metadata, metadata_error = {}, None
    if config.sec_monitor.state_dir:
        state_path = Path(config.sec_monitor.state_dir) / 'state.json'
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
                if state.get('version') != 1 or not isinstance(state.get('files'), dict):
                    raise ValueError('invalid monitor metadata')
                metadata = state['files']
            except Exception:
                metadata_error = 'SEC本地来源元数据不可用；未采用未验证链接或附件'
    rows, errors = [], []
    # No arbitrary page limit: parse the complete already-indexed security library.
    with conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM filings WHERE ticker=%s', (ticker,))
        count = cur.fetchone()[0]
    indexed = list_filings(conn, ticker=ticker, limit=max(count, 1))
    for filing in indexed:
        accession = filing['accession_number']
        try:
            if filing['ticker'] != ticker or not _ACCESSION.fullmatch(accession or ''):
                raise ValueError('invalid filing identity')
            if filing['form'] not in ({'10-Q', '8-K'} if ticker == 'TSLA' else {'10-Q', '10-K', '8-K'}):
                continue  # Preserve the existing source scope (notably TSLA 10-K).
            path = _owned_path(filing['file_path'], root, ticker, accession)
            meta = {'ticker': ticker, 'form': filing['form'], 'filing_date': str(filing['filing_date']),
                    'accession_number': accession, 'primary_document': filing['primary_document'], 'path': path,
                    'attachments': [], 'document_urls': {}}
            entry = metadata.get(ticker + ':' + accession, {}).get('meta', {})
            limitations = []
            if entry:
                if (entry.get('ticker') != ticker or entry.get('accession_number') != accession
                        or entry.get('form') != filing['form'] or Path(entry.get('path', '')).resolve() != Path(path)):
                    raise ValueError('monitor metadata identity mismatch')
                meta['attachments'] = [_owned_path(p, root, ticker, accession) for p in entry.get('attachments', [])]
                for p in [path, *meta['attachments']]:
                    candidate = entry.get('document_urls', {}).get(p, '')
                    if url := _verified_sec_url(candidate, accession):
                        meta['document_urls'][p] = url
                meta['edgar_url'] = meta['document_urls'].get(path) or _verified_sec_url(entry.get('edgar_url'), accession)
            if not meta.get('edgar_url'):
                meta['edgar_url'] = f'/api/filings/records/{filing["id"]}/download'
                limitations.append('未核验原始SEC文档URL；保留已索引的本地原文链接')
            if metadata_error:
                limitations.append(metadata_error)
            analysis = analyze_files(meta)
            snapshots = []
            for doc in analysis['sources']:
                snapshot = research_rows.archive_snapshot({'source_url': doc.get('url') or doc['path'], 'source_hash': doc['sha256'],
                    'raw_snapshot': Path(doc['path']).read_bytes()})
                snapshots.append(snapshot)
            payload = {'filing_id': filing['id'], 'ticker': ticker, 'form': filing['form'], 'accession_number': accession,
                'filing_date': str(filing['filing_date']), 'source_url': meta['edgar_url'], 'path': path,
                'analysis': analysis, 'snapshots': snapshots, 'limitations': limitations}
            rows.append({'natural_key': accession, 'payload': payload, 'disclosed_at': filing['filing_date'],
                         'source_url': meta['edgar_url'], 'source_hash': analysis['sources'][0]['sha256']})
        except Exception:
            errors.append({'filing_id': filing['id'], 'accession_number': accession, 'error': '本地原文解析失败或来源归属未通过校验'})
    return rows, {'indexed_documents': len(indexed), 'document_errors': errors,
                  'limitations': [metadata_error] if metadata_error else []}


def refresh_local_filings(tickers, *, config=None):
    """Refresh only indexed local filings after SEC collection; no online sync endpoint."""
    results = []
    with core_tx.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext('us-research-sync'))")
            acquired = cur.fetchone()[0]
        conn.commit()
        if not acquired:
            raise ValueError('美股研究更新正在运行，请查看任务记录')
        try:
            for ticker in dict.fromkeys(tickers):
                sid = stock_id(ticker)
                try:
                    rows, details = _local_filing_rows(conn, ticker, config=config)
                    records = repo.store_rows(conn, sid, 'filings', rows)
                    collected_ids = {r['id'] for r in records}
                    details['retained_old_values'] = any(r['id'] not in collected_ids
                        for r in repo.read_rows(conn, sid, 'filings'))
                    status = 'ready' if records else 'empty'
                    if details.get('document_errors'):
                        status = 'partial'
                    repo.collection_state(conn, sid, 'filings', status=status, count=len(records), details=details)
                    conn.commit()
                    results.append({'security_id': sid, 'dataset': 'filings', 'status': status, 'rows': len(records)})
                except Exception:
                    conn.rollback()
                    error = '来源采集或本地处理失败；旧数据已保留'
                    repo.collection_state(conn, sid, 'filings', status='failed', error=error)
                    conn.commit()
                    results.append({'security_id': sid, 'dataset': 'filings', 'status': 'failed', 'error': error})
        finally:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext('us-research-sync'))")
            conn.commit()
    return {'results': results, 'errors': [r for r in results if r['status'] in {'failed', 'partial'}], 'notifications': 'disabled'}
