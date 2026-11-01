"""Explicit collection and local-only US research views; never sends notifications."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from investment_assistant.config import load_config
from investment_assistant.core import tx as core_tx
from investment_assistant.db import list_filings, list_watchlist_items
from investment_assistant.data import repo
from investment_assistant.data.fundamentals import stock_id

DATASETS = ('financials', 'market', 'filings')
RAW_ROOT = Path(__file__).resolve().parents[2] / '.local/us-research/raw'
_ACCESSION = re.compile(r'\d{10}-\d{2}-\d{6}')


def archive_snapshot(snapshot, *, root=None):
    """Archive original bytes, never a reconstructed JSON representation."""
    raw = snapshot.get('raw_snapshot')
    if not isinstance(raw, bytes):
        raise ValueError('来源原始字节缺失')
    digest = hashlib.sha256(raw).hexdigest()
    if snapshot.get('source_hash') != digest:
        raise ValueError('来源字节摘要不匹配')
    directory = Path(root if root is not None else RAW_ROOT)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (digest + '.raw')
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('已存来源快照摘要不匹配')
    else:
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
            name = output.name
            try:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
                os.replace(name, path)
            finally:
                Path(name).unlink(missing_ok=True)
    return {**{k: v for k, v in snapshot.items() if k != 'raw_snapshot'}, 'snapshot_path': str(path)}


def _identity(payload, ticker):
    identity = payload.get('identity') or {}
    if identity.get('security_id') != stock_id(ticker) or identity.get('ticker') != ticker:
        raise ValueError('来源证券身份不匹配')


def _financial_rows(payload):
    snapshot = archive_snapshot({k: payload[k] for k in ('source_url', 'source_hash', 'raw_snapshot', 'collected_at', 'request_params')})
    groups = {}
    economic_fields = ('metric', 'value', 'unit', 'basis', 'start', 'end', 'period_kind', 'form', 'accession', 'filed', 'concept')
    for raw_fact in payload['observations']:
        if raw_fact.get('source_hash') != snapshot['source_hash']:
            raise ValueError('财务事实与原始来源摘要不匹配')
        fact = {**raw_fact, 'snapshot_path': snapshot['snapshot_path']}
        fact.pop('id', None)
        natural_key = json.dumps([fact.get(k) for k in ('metric', 'unit', 'basis', 'start', 'end', 'accession', 'concept')], separators=(',', ':'))
        fingerprint = json.dumps([fact.get(k) for k in economic_fields], allow_nan=False)
        # Identical candidates do not create ambiguity. Candidate order must not
        # make a new economic version when a whole response is merely reordered.
        groups.setdefault(natural_key, {}).setdefault(fingerprint, fact)
    rows, conflict_keys = [], []
    for key, unique in groups.items():
        candidates = [unique[k] for k in sorted(unique)]
        fact = candidates[0]
        body = fact
        if len(candidates) > 1:
            # This one fact identity is ambiguous, not a temporal last-write-win.
            # Its persisted parent ID locates all complete source candidates.
            body = {**fact, 'value': None, 'source_locator': None, 'conflicts': candidates}
            conflict_keys.append(key)
        rows.append({'natural_key': key, 'payload': body,
            'disclosed_at': max(f['filed'] for f in candidates), 'source_url': fact.get('source_url'), 'source_hash': fact.get('source_hash')})
    return rows, {'identity': dict(payload['identity']), 'acquisition': snapshot, 'coverage': payload.get('coverage'),
                  'limitations': payload.get('limitations', []), 'conflicting_fact_keys': conflict_keys}


def _market_rows(payload):
    snapshots = [archive_snapshot(s) for s in payload['raw_snapshots']]
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
                snapshot = archive_snapshot({'source_url': doc.get('url') or doc['path'], 'source_hash': doc['sha256'],
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
