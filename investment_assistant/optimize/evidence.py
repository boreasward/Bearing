"""Pure aggregation of caller-windowed evidence (spec §8.3).

Input iterables contain SQL aggregates, not database connections:
* fetch_failures: family, error_kind, failed_targets/backup_used/partial/failed,
  backup_incomplete_runs (missing counts are zero); group by family × error_kind.
  Incomplete runs make backup_used a known lower bound, never an exact zero.
* agent_reasons: mode, reason, count (default 1). Deferred data requests use
  mode=data_request, reason=deferred in this same stream.
* research_blocked: why, count (default 1).
* counter_adoption: adopted, total; legacy names for runs with counter opinions
  followed by a successful revision successor / all runs with counter opinions.
  Summed counts yield a run-level follow-up revision ratio, not item-level or
  causal adoption. Each row carries its measurement definition and limitation
  through fact_blocks (integration-contracts, Task2仓储与证据 / O2).
* lesson_repeats: scope, count (default 1); retain grouped counts >=2.
* stale_versions: version_id, status, days_pending; retain pending >30 days.
  This route stream may also carry kind=process_review, metric, months,
  triggered: the caller computes the three-month signal from host rules.
* open_proposals: full rows selected by the caller; IDs are retained as strings.

The caller owns the 7/30-day SQL windows and reference date. Group identities,
not mutable counts or input order, determine aggregate row IDs. Empty sections
mean no supplied observations, not proof of zero failures.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from hashlib import sha256


EVIDENCE_KEYS = ('data_failures', 'agent_quality', 'retro_patterns', 'route', 'open_proposals')


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)


def _id(section: str, identity) -> str:
    return sha256(_json([section, identity]).encode('utf-8')).hexdigest()[:24]


def _count(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('evidence_count_invalid')
    return value


def _counts(rows, *, keys: tuple[str, ...], counts: tuple[str, ...], default: int = 0) -> list[dict]:
    grouped = {}
    for row in rows:
        identity = tuple(row[key] for key in keys)
        target = grouped.setdefault(identity, {**dict(zip(keys, identity)), **dict.fromkeys(counts, 0)})
        for key in counts:
            target[key] += _count(row.get(key, default))
    return [grouped[key] for key in sorted(grouped, key=_json)]


def summarize(*, fetch_failures, agent_reasons, research_blocked, counter_adoption,
              lesson_repeats, stale_versions, open_proposals) -> dict[str, list[dict]]:
    """Return detached, deterministically ordered rows in all five sections."""
    pack = {key: [] for key in EVIDENCE_KEYS}

    def add(section, row, identity):
        copied = deepcopy(row)
        if 'id' in copied:
            copied['source_id'] = copied.pop('id')
        pack[section].append({**copied, 'id': _id(section, identity)})

    for row in _counts(fetch_failures, keys=('family', 'error_kind'),
                       counts=('failed_targets', 'backup_used', 'partial', 'failed', 'backup_incomplete_runs')):
        if row['backup_incomplete_runs']:
            row['note'] = '备源使用数为已知下界；历史目标来源记录不完整，不能据此断言未使用备源'
        add('data_failures', row, [row['family'], row['error_kind']])
    for row in _counts(agent_reasons, keys=('mode', 'reason'), counts=('count',), default=1):
        add('agent_quality', {'kind': 'agent_reason', **row}, ['agent_reason', row['mode'], row['reason']])
    for row in _counts(research_blocked, keys=('why',), counts=('count',), default=1):
        add('agent_quality', {'kind': 'research_blocked', 'reason': row['why'], 'count': row['count']},
            ['research_blocked', row['why']])
    adoption = list(counter_adoption)
    if adoption:
        adopted, total = 0, 0
        for row in adoption:
            accepted, available = _count(row['adopted']), _count(row['total'])
            if accepted > available:
                raise ValueError('evidence_count_invalid')
            adopted += accepted
            total += available
        add('agent_quality', {'kind': 'counter_adoption', 'adopted': adopted, 'total': total,
                              'ratio': adopted / total if total else None,
                              'measurement': 'counter_followed_by_successful_revision',
                              'note': '运行级后续修订比例，不证明逐条反方意见被采纳或因果关系'}, ['counter_adoption'])
    for row in _counts(lesson_repeats, keys=('scope',), counts=('count',), default=1):
        if row['count'] >= 2:
            add('retro_patterns', row, [row['scope']])
    for row in stale_versions:
        if row.get('kind') == 'process_review':
            add('route', row, ['process_review', row['metric']])
        elif row['status'] == 'user_acceptance_pending' and row['days_pending'] > 30:
            add('route', row, ['stale_version', row['version_id']])
    for row in open_proposals:
        pack['open_proposals'].append({**deepcopy(row), 'id': str(row['id'])})
    for rows in pack.values():
        if len({row['id'] for row in rows}) != len(rows):
            raise ValueError('evidence_duplicate_id')
    for section in ('agent_quality', 'route', 'open_proposals'):
        pack[section].sort(key=lambda row: row['id'])
    return pack


def fact_blocks(pack) -> str:
    """Five named JSON blocks with explicit empty states and referenceable IDs."""
    return '\n\n'.join(f'[fact:{key}]\n' + _json({'status': 'ok' if pack.get(key) else 'empty',
                                                'rows': pack.get(key, [])}) for key in EVIDENCE_KEYS)


def is_proposal_day(session: date) -> bool:
    """The caller supplies a New York exchange-session *date*, not Beijing now.

    Friday's completed session is processed on Beijing Saturday, in both DST
    regimes. Holiday/session validity belongs to the workflow's calendar gate.
    """
    return session.weekday() == 4
