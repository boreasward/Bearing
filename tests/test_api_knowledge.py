"""Knowledge API (spec §11): shapes, 400/404/503, and no database needed."""
from pathlib import Path

import pytest
import yaml

from investment_assistant.workbench import knowledge


def write_card(root: Path, card_id: str, *, title: str = '止损', version: int = 1, summary: str = '一句话摘要。',
               related: list[str] | None = None) -> None:
    meta = {'id': card_id, 'title': title, 'group': card_id.split('.')[0], 'version': version,
            'applies_to': ['plan-1'], 'summary': summary, 'related': related or []}
    (root / 'concepts').mkdir(parents=True, exist_ok=True)
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=None)
    (root / 'concepts' / f'{card_id}.md').write_text(f'---\n{front}---\n\n## 是什么\n\n正文。\n', encoding='utf-8')


def write_principles(root: Path, body: str) -> None:
    (root / 'principles.md').write_text(f'---\ntitle: 系统每天怎么跑\nversion: 1\n---\n\n{body}', encoding='utf-8')


@pytest.fixture
def root(tmp_path, monkeypatch):
    knowledge.reset_cache()
    write_card(tmp_path, 'plan.stop', related=['indicator.atr14'])
    write_card(tmp_path, 'indicator.atr14', title='ATR14')
    write_principles(tmp_path, '## 一\n\n[当天这一步](#stage-collect)\n')
    monkeypatch.setattr(knowledge, 'DEFAULT_ROOT', tmp_path)
    yield tmp_path
    knowledge.reset_cache()


def test_index_shape(client, root):
    resp = client.get('/api/knowledge/concepts')
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {'knowledge_version', 'groups', 'concepts'}
    assert body['knowledge_version'] == 'knowledge-1'
    assert [g['id'] for g in body['groups']] == [
        'family', 'indicator', 'rule', 'plan', 'agent', 'account', 'review', 'system']
    assert all(set(g) == {'id', 'title', 'count'} for g in body['groups'])
    assert {g['id']: g['count'] for g in body['groups']}['plan'] == 1
    assert {g['id']: g['count'] for g in body['groups']}['account'] == 0
    assert [c['id'] for c in body['concepts']] == ['indicator.atr14', 'plan.stop']
    assert body['concepts'][1]['ref'] == 'concept:plan.stop@1'


def test_card_and_stale(client, root):
    body = client.get('/api/knowledge/concepts/plan.stop').json()
    assert body['id'] == 'plan.stop' and body['stale'] is None and '<h2>' in body['html']
    assert body['related_cards'] == [{'id': 'indicator.atr14', 'title': 'ATR14'}]
    assert client.get('/api/knowledge/concepts/indicator.atr14').json()['backlinks'] == [
        {'id': 'plan.stop', 'title': '止损'}]
    stale = client.get('/api/knowledge/concepts/concept:plan.stop@2').json()
    assert stale['version'] == 1 and stale['stale']['requested'] == 2 and stale['stale']['current'] == 1
    assert client.get('/api/knowledge/concepts/plan.stop@1').json()['stale'] is None


def test_card_404_and_400(client, root):
    missing = client.get('/api/knowledge/concepts/plan.nope')
    assert missing.status_code == 404 and 'plan.nope' in missing.json()['error']
    bad = client.get('/api/knowledge/concepts/Plan.Stop')
    assert bad.status_code == 400 and bad.json()['error']


def test_principles_shape(client, root):
    body = client.get('/api/knowledge/principles').json()
    assert body['title'] == '系统每天怎么跑' and body['version'] == 1
    assert body['sections'][0]['stage'] == 'collect'


def test_principles_404_when_missing(client, root):
    (root / 'principles.md').unlink()
    knowledge.reset_cache()
    resp = client.get('/api/knowledge/principles')
    assert resp.status_code == 404 and resp.json()['error']
    assert client.get('/api/knowledge/concepts').status_code == 200


@pytest.mark.parametrize('path', ['/api/knowledge/concepts', '/api/knowledge/concepts/plan.stop',
                                  '/api/knowledge/principles'])
def test_api_knowledge_503_lists_problems(client, root, path):
    write_card(root, 'plan.stop', version=0)
    write_card(root, 'indicator.atr14', summary='')
    knowledge.reset_cache()
    resp = client.get(path)
    assert resp.status_code == 503
    body = resp.json()
    assert set(body) == {'error', 'problems'}
    assert len(body['problems']) == 2
    assert body['error'] == f'概念卡加载失败：{body["problems"][0]}'


def test_knowledge_api_works_without_database(client, root, monkeypatch):
    from investment_assistant.workbench import service

    attempts = []

    def no_database():
        attempts.append(1)
        raise service.Unavailable('数据库不可达')

    monkeypatch.setattr(service, 'connection', no_database)
    for path in ('/api/knowledge/concepts', '/api/knowledge/concepts/plan.stop', '/api/knowledge/principles'):
        assert client.get(path).status_code == 200
    assert attempts == []
    # sanity: a database-backed endpoint does hit the broken connection under the same patch
    assert client.get('/api/workbench/run-overview').status_code != 200
    assert attempts == [1]
