"""My-knowledge fields, `know:` refs and cutoff visibility (v2.7 spec §5). Pure, offline."""
from datetime import UTC, datetime, timedelta

import pytest

from investment_assistant.core import knowledge

T0 = datetime(2026, 10, 20, 12, tzinfo=UTC)
CUTOFF = T0 + timedelta(days=2)


def test_parse_ref():
    assert knowledge.parse_knowledge_ref('know:3@2') == (3, 2)
    assert knowledge.parse_knowledge_ref('know:3') == (3, None)
    assert knowledge.parse_knowledge_ref('know:120@15') == (120, 15)
    for bad in ('know:0@1', 'know:3@0', 'concept:plan.stop', 'know:', 'know:3@', 'know:03', 'know:3@2\n',
                ' know:3@2', 'know:-1@1', 'KNOW:3@1', '', None, 3):
        with pytest.raises(ValueError):
            knowledge.parse_knowledge_ref(bad)


def test_ref_roundtrip():
    assert knowledge.ref(3, 2) == 'know:3@2'
    assert knowledge.parse_knowledge_ref(knowledge.ref(7, 1)) == (7, 1)
    assert knowledge.KNOW_REF_RE.pattern == r'^know:([1-9][0-9]*)(?:@([1-9][0-9]*))?$'


def _version(knowledge_id, version, created, **extra):
    return {'id': knowledge_id * 100 + version, 'knowledge_id': knowledge_id, 'version': version,
            'created_at': created, 'title': f'k{knowledge_id}v{version}', **extra}


def _status(knowledge_id, status, created, id_=None):
    return {'id': id_ or int(created.timestamp()), 'knowledge_id': knowledge_id, 'status': status,
            'created_at': created}


def test_visible_four_cases():
    # 1. a version written after the cutoff: the older one is seen
    v1, v2 = _version(1, 1, T0), _version(1, 2, CUTOFF + timedelta(seconds=1))
    assert knowledge.visible([v1, v2], [], cutoff=CUTOFF) == [v1]
    assert knowledge.visible([v1, v2], [], cutoff=None) == [v2]                       # None means "now"
    # 2. archived after the cutoff: still visible then
    archived_later = _status(2, 'archived', CUTOFF + timedelta(hours=1))
    k2 = _version(2, 1, T0)
    assert knowledge.visible([k2], [archived_later], cutoff=CUTOFF) == [k2]
    assert knowledge.visible([k2], [archived_later], cutoff=None) == []
    # 3. archived before the cutoff and restored after it: not visible at the cutoff
    k3 = _version(3, 1, T0)
    statuses = [_status(3, 'archived', T0 + timedelta(hours=1), 1), _status(3, 'active', CUTOFF + timedelta(hours=1), 2)]
    assert knowledge.visible([k3], statuses, cutoff=CUTOFF) == []
    assert knowledge.visible([k3], statuses, cutoff=None) == [k3]
    # 4. nothing written yet at the cutoff: not returned
    k4 = _version(4, 1, CUTOFF + timedelta(seconds=1))
    assert knowledge.visible([k4], [], cutoff=CUTOFF) == []
    # grouped by item, ordered by id, highest visible version each
    k1v3 = _version(1, 3, T0 + timedelta(hours=5))
    out = knowledge.visible([k4, v2, k1v3, k3, k2, v1], [archived_later], cutoff=CUTOFF)
    assert [(row['knowledge_id'], row['version']) for row in out] == [(1, 3), (2, 1), (3, 1)]
    # archived and restored before the cutoff: visible
    restored = [_status(3, 'archived', T0 + timedelta(hours=1), 1), _status(3, 'active', T0 + timedelta(hours=2), 2)]
    assert knowledge.visible([k3], restored, cutoff=CUTOFF) == [k3]


def test_status_at_default_active():
    assert knowledge.status_at([], CUTOFF) == 'active'
    assert knowledge.status_at([], None) == 'active'
    rows = [_status(1, 'archived', T0, 1), _status(1, 'active', T0 + timedelta(hours=1), 2),
            _status(1, 'archived', T0 + timedelta(hours=2), 3)]
    assert knowledge.status_at(rows, None) == 'archived'
    assert knowledge.status_at(rows, T0 + timedelta(minutes=30)) == 'archived'
    assert knowledge.status_at(rows, T0 + timedelta(minutes=90)) == 'active'
    assert knowledge.status_at(rows, T0 - timedelta(minutes=1)) == 'active'          # before any record
    assert knowledge.status_at(list(reversed(rows)), None) == 'archived'             # input order does not matter
