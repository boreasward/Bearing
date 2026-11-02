"""Source-only financial observation packaging extracted from us_research.service."""
import hashlib
import json
import os
import tempfile
from pathlib import Path

RAW_ROOT = Path(__file__).resolve().parents[2] / '.local/us-research/raw'


def archive_snapshot(snapshot, *, root=None):
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


def financial_rows(payload, *, root=None):
    snapshot = archive_snapshot({k: payload[k] for k in (
        'source_url', 'source_hash', 'raw_snapshot', 'collected_at', 'request_params')}, root=root)
    groups = {}
    economic_fields = ('metric', 'value', 'unit', 'basis', 'start', 'end', 'period_kind', 'form', 'accession', 'filed', 'concept')
    for raw_fact in payload['observations']:
        if raw_fact.get('source_hash') != snapshot['source_hash']:
            raise ValueError('财务事实与原始来源摘要不匹配')
        fact = {**raw_fact, 'snapshot_path': snapshot['snapshot_path']}
        fact.pop('id', None)
        natural_key = json.dumps([fact.get(k) for k in (
            'metric', 'unit', 'basis', 'start', 'end', 'accession', 'concept')], separators=(',', ':'))
        fingerprint = json.dumps([fact.get(k) for k in economic_fields], allow_nan=False)
        groups.setdefault(natural_key, {}).setdefault(fingerprint, fact)
    rows, conflict_keys = [], []
    for key, unique in groups.items():
        candidates = [unique[k] for k in sorted(unique)]
        fact = candidates[0]
        body = fact
        if len(candidates) > 1:
            body = {**fact, 'value': None, 'source_locator': None, 'conflicts': candidates}
            conflict_keys.append(key)
        rows.append({'natural_key': key, 'payload': body, 'disclosed_at': max(f['filed'] for f in candidates),
                     'source_url': fact.get('source_url'), 'source_hash': fact.get('source_hash')})
    return rows, {'identity': dict(payload['identity']), 'acquisition': snapshot, 'coverage': payload.get('coverage'),
                  'limitations': payload.get('limitations', []), 'conflicting_fact_keys': conflict_keys}
