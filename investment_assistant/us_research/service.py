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
from . import repository as repo
from .domain import stock_id
from .metrics import financial_series, performance_view, valuation_view
from .sources import USSources
from .templates import research_template

DATASETS = ('financials', 'market', 'filings')
RAW_ROOT = Path(__file__).resolve().parents[2] / '.local/us-research/raw'
_ACCESSION = re.compile(r'\d{10}-\d{2}-\d{6}')


def _connection():
    """core.tx's connection with this subsystem's legacy error contract: an unconfigured
    database stays a ``ValueError`` (HTTP 400), not the workbench's ``Unavailable``.
    Tests patch the ``connection`` alias below; Task 1 dissolves this module."""
    try:
        return core_tx.connection()
    except core_tx.Unavailable:
        raise ValueError('未配置数据库，尚未采集美股研究数据') from None


connection = _connection   # guidance imports this name; sync/view call _connection() through core.tx


def validate_request(symbols=None, datasets=None, retry_failed=False):
    if symbols is not None:
        if not isinstance(symbols, list) or not symbols:
            raise ValueError('symbols须为非空美股列表')
        symbols = list(dict.fromkeys(stock_id(s).rsplit(':', 1)[-1] for s in symbols))
    if datasets is not None and (not isinstance(datasets, list) or not datasets
            or any(not isinstance(d, str) or d not in DATASETS for d in datasets)):
        raise ValueError('未知或空的数据集')
    if not isinstance(retry_failed, bool):
        raise ValueError('retry_failed须为布尔值')
    return symbols, list(dict.fromkeys(datasets)) if datasets is not None else list(DATASETS)


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


def _financial_observations(records):
    observations = []
    for record in records:
        if record.get('conflicts'):
            for candidate in record['conflicts']:
                observations.append({**candidate, 'id': record['id'], 'security_id': record['security_id'],
                    'first_seen_at': record['first_seen_at'], 'disclosed_at': record['disclosed_at']})
        else:
            observations.append(record)
    return observations


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
    from investment_assistant.filings.analysis import analyze_files
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


def sync(symbols=None, *, datasets=None, retry_failed=False, source=None, config=None):
    symbols, selected = validate_request(symbols, datasets, retry_failed)
    results = []
    with _connection() as conn:
        if symbols is None:
            symbols = []
            for row in list_watchlist_items(conn):
                if row['status'] != 'active':
                    continue
                try:
                    symbols.append(stock_id(row['ticker']).rsplit(':', 1)[-1])
                except ValueError:
                    continue
        # Session lock is held before source I/O, across independent commits.
        # An older slow response can never arrive after a newer collection.
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext('us-research-sync'))")
            acquired = cur.fetchone()[0]
        conn.commit()
        if not acquired:
            raise ValueError('美股研究更新正在运行，请查看任务记录')
        try:
            for ticker in dict.fromkeys(symbols):
                sid = stock_id(ticker)
                for dataset in selected:
                    state = repo.states(conn, sid).get(dataset)
                    if retry_failed and state and state['status'] not in {'failed', 'partial'}:
                        results.append({'security_id': sid, 'dataset': dataset, 'status': 'skipped'})
                        continue
                    try:
                        if dataset == 'filings':
                            rows, details = _local_filing_rows(conn, ticker, config=config)
                        else:
                            adapter = source if source is not None else USSources()
                            payload = getattr(adapter, 'companyfacts' if dataset == 'financials' else 'market')(ticker)
                            _identity(payload, ticker)
                            rows, details = _financial_rows(payload) if dataset == 'financials' else _market_rows(payload)
                        records = repo.store_rows(conn, sid, dataset, rows)
                        collected_ids = {r['id'] for r in records}
                        details['retained_old_values'] = any(r['id'] not in collected_ids
                            for r in repo.read_rows(conn, sid, dataset))
                        status = 'ready' if records else 'empty'
                        if details.get('document_errors') or details.get('conflicting_fact_keys') or (dataset == 'market' and details.get('limitations')):
                            status = 'partial'
                        repo.collection_state(conn, sid, dataset, status=status, count=len(records), details=details)
                        conn.commit()
                        results.append({'security_id': sid, 'dataset': dataset, 'status': status, 'rows': len(records)})
                    except Exception:
                        conn.rollback()
                        # Never trust exception text, including ValueError/SourceError.
                        error = '来源采集或本地处理失败；旧数据已保留'
                        repo.collection_state(conn, sid, dataset, status='failed', error=error)
                        conn.commit()
                        results.append({'security_id': sid, 'dataset': dataset, 'status': 'failed', 'error': error})
        finally:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext('us-research-sync'))")
            conn.commit()
    return {'results': results, 'errors': [r for r in results if r['status'] in {'failed', 'partial'}], 'notifications': 'disabled'}


def refresh_local_filings(tickers, *, config=None):
    return sync(tickers, datasets=['filings'], config=config)


def _cutoff(as_of):
    try:
        cutoff = datetime.fromisoformat(as_of) if isinstance(as_of, str) else as_of
        if cutoff is not None and (not isinstance(cutoff, datetime) or cutoff.tzinfo is None):
            raise ValueError()
        return cutoff
    except (ValueError, TypeError):
        raise ValueError('历史观察截止时间须为含时区的ISO时间') from None


def view(ticker, *, as_of=None):
    sid = stock_id(ticker)
    ticker = sid.rsplit(':', 1)[-1]
    cutoff = _cutoff(as_of)
    with _connection() as conn:
        rows = {ds: repo.read_rows(conn, sid, ds, as_of=cutoff) for ds in DATASETS}
        states = repo.states(conn, sid)
        guidance_records = repo.read_history(conn, sid, 'guidance', as_of=cutoff)
    from .guidance import comparisons
    statuses = []
    for ds in DATASETS:
        state = states.get(ds, {'dataset': ds, 'status': 'never_collected', 'row_count': 0})
        status = 'unavailable_at_cutoff' if cutoff and not rows[ds] else state['status']
        statuses.append({**state, 'scope': 'current_collection_state', 'status': status,
            'stored_rows': len(rows[ds]), 'retained_old_values': bool(rows[ds]) and (state['status'] in {'failed', 'empty'}
                or bool((state.get('details') or {}).get('retained_old_values')))})
    financials = financial_series(_financial_observations(rows['financials']))
    bars = sorted([r for r in rows['market'] if r.get('kind') == 'bar' and r['group'] == 'stock'], key=lambda r: r['date'])
    benchmarks = {name: sorted([r for r in rows['market'] if r.get('group') == name], key=lambda r: r['date']) for name in ('SPY', 'QQQ')}
    quotes = [r for r in rows['market'] if r.get('kind') == 'quote']
    quote = max(quotes, key=lambda r: (r.get('date') or '', r['id']), default={})
    market = {'bars': bars, 'benchmarks': benchmarks, 'quote': quote,
              'performance': performance_view(bars, benchmarks),
              'limitations': states.get('market', {}).get('last_success_details', {}).get('limitations', []),
              'limitations_scope': 'current_collection_state'}
    currencies = {r['unit'] for r in rows['financials'] if re.fullmatch('[A-Z]{3}', r.get('unit', ''))}
    return {'security_id': sid, 'ticker': ticker, 'currency': quote.get('currency') or (next(iter(currencies)) if len(currencies) == 1 else None),
        'financials': financials, 'market': market, 'valuation': valuation_view(financials, market), 'statuses': statuses,
        'template': research_template(ticker),
        'guidance': {'records': guidance_records, 'comparisons': comparisons(guidance_records, financials, as_of=cutoff),
                     'status': 'ready' if guidance_records else 'not_recorded', 'consensus': '未接入一致预期'},
        'filings': sorted(rows['filings'], key=lambda r: (r['filing_date'], r['id']), reverse=True),
        'pit': {'strict': False, 'as_of': cutoff.isoformat() if cutoff else None,
                'scope': 'local_observation_versions', 'limitation': '同时检查首次本地观察和披露时间；来源回补不代表严格历史可用',
                'metadata_scope': '模板与采集状态为当前信息；历史视图隐藏当前观察卡；指引按首次观察与披露时间筛选'}}
