"""My-knowledge fields, `know:` refs and cutoff visibility (v2.7 spec §5). Pure, offline."""
from datetime import UTC, datetime, timedelta

import pytest

from investment_assistant.review.knowledge import items as ki
from investment_assistant.core import knowledge

T0 = datetime(2026, 10, 20, 12, tzinfo=UTC)
CUTOFF = T0 + timedelta(days=2)
CATALOG = {'plan.stop': 3, 'review.discipline_early_take': 1, 'agent.thesis': 2}


def payload(**overrides):
    values = {'title': '财报前不追', 'body': '财报前三天不追高。', 'scope': '财报前 3 个交易日内的买入',
              'counter_examples': '财报已确认利好时可以例外', 'concepts': ['plan.stop'], 'tickers': ['nvda']}
    values.update(overrides)
    return values


def validate(**overrides):
    require = overrides.pop('require_counter', False)
    catalog = overrides.pop('concept_versions', CATALOG)
    return ki.validate(payload(**overrides), concept_versions=catalog, require_counter=require)


def test_constants():
    assert ki.MY_KNOWLEDGE_VERSION == 'my-knowledge-1'
    assert (knowledge.KNOWLEDGE_RESULT_MAX, knowledge.INDEX_MAX, ki.LINKS_MAX) == (10, 50, 10)
    assert ki.LIMITS == {'title': 60, 'body': 4000, 'scope': 500, 'counter_examples': 1000}


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


LIMIT_CASES = [
    ('title-61', dict(title='字' * 61)),
    ('title-empty', dict(title='  ')),
    ('body-4001', dict(body='字' * 4001)),
    ('body-empty', dict(body='')),
    ('scope-empty', dict(scope='  ')),
    ('scope-501', dict(scope='字' * 501)),
    ('counter-1001', dict(counter_examples='字' * 1001)),
    ('counter-empty-required', dict(counter_examples='', require_counter=True)),
    ('counter-blank-required', dict(counter_examples='  \n', require_counter=True)),
    ('links-0', dict(concepts=[], tickers=[])),
    ('links-11', dict(concepts=[], tickers=[f'T{i}' for i in range(11)])),
    ('links-11-mixed', dict(concepts=['plan.stop', 'agent.thesis'], tickers=[f'T{i}' for i in range(9)])),
    ('unknown-concept', dict(concepts=['plan.nope'])),
    ('bad-concept-id', dict(concepts=['not an id'])),
    ('bad-ticker', dict(tickers=['600519.SH'])),
    ('bad-ticker-text', dict(tickers=['no way'])),
    ('title-not-text', dict(title=5)),
    ('concepts-not-list', dict(concepts='plan.stop')),
]


@pytest.mark.parametrize('name,overrides', LIMIT_CASES, ids=[c[0] for c in LIMIT_CASES])
def test_validate_limits(name, overrides):
    with pytest.raises(ValueError):
        validate(**overrides)


def test_validate_edges_accepted():
    ok = validate(title='字' * 60, body='字' * 4000, scope='字' * 500, counter_examples='字' * 1000)
    assert len(ok['title']) == 60 and len(ok['body']) == 4000
    assert validate(counter_examples='')['counter_examples'] == ''            # hand-written lessons may skip it
    assert validate(concepts=['plan.stop', 'agent.thesis'], tickers=[f'T{i}' for i in range(8)])    # exactly 10
    assert validate(concepts=[], tickers=['NVDA'])['concepts'] == []
    assert validate(concepts=['plan.stop'], tickers=[])['tickers'] == []


def test_validate_normalises():
    out = validate(title='  财报前不追  ', tickers=['nvda', ' NVDA ', 'amd'], concepts=['plan.stop', 'plan.stop'])
    assert out['title'] == '财报前不追'
    assert out['tickers'] == ['NVDA', 'AMD']                                  # upper case, de-duplicated, in order
    assert out['concepts'] == ['concept:plan.stop@3']
    assert set(out) == {'title', 'body', 'scope', 'counter_examples', 'concepts', 'tickers'}


def test_validate_concepts_get_current_version():
    out = validate(concepts=['plan.stop', 'concept:agent.thesis', 'concept:review.discipline_early_take@7'])
    assert out['concepts'] == ['concept:plan.stop@3', 'concept:agent.thesis@2',
                               'concept:review.discipline_early_take@1']       # a pinned old version is replaced


def test_validate_concepts_without_catalog_is_lookup_error():
    with pytest.raises(LookupError, match='概念卡不可用'):
        validate(concept_versions=None)
    # tickers only: no catalog needed
    assert validate(concepts=[], tickers=['NVDA'], concept_versions=None)['tickers'] == ['NVDA']
    with pytest.raises(ValueError):                                          # other problems stay ValueError
        validate(concept_versions=None, title='')


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


def test_changed_fields():
    prev = {'title': 'a', 'body': 'b', 'scope': 's', 'counter_examples': 'c', 'concepts': ['concept:plan.stop@1'],
            'tickers': ['NVDA']}
    assert ki.changed_fields(prev, dict(prev)) == []
    assert ki.changed_fields(prev, {**prev, 'body': 'b2', 'tickers': ['NVDA', 'AMD']}) == ['body', 'tickers']
    assert ki.changed_fields(prev, {**prev, 'concepts': ['concept:plan.stop@2']}) == ['concepts']
    assert ki.changed_fields(prev, {**prev, 'title': 'x', 'scope': 'y', 'counter_examples': ''}) \
        == ['title', 'scope', 'counter_examples']
