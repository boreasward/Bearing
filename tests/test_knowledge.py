"""Concept-card knowledge layer (v2.6 spec §4): load, validate, render, refs, CLI. Offline, tmp_path only."""
import ast
import hashlib
import re
from pathlib import Path

import pytest
import yaml

from investment_assistant.core import knowledge
from investment_assistant.core.knowledge import KnowledgeError, load

GOOD_BODY = '## 是什么\n\n止损是一个价位。\n'


def write_card(root: Path, card_id: str = 'plan.stop', *, filename: str | None = None, body: str = GOOD_BODY,
               drop: tuple[str, ...] = (), **overrides) -> Path:
    meta = {'id': card_id, 'title': '止损', 'group': card_id.split('.')[0], 'version': 1,
            'applies_to': ['plan-1'], 'summary': '参考价减 2 倍 ATR14。', 'related': []}
    meta.update(overrides)
    for key in drop:
        meta.pop(key, None)
    folder = root / 'concepts'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (filename or f'{card_id}.md')
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=None)
    path.write_text(f'---\n{front}---\n\n{body}', encoding='utf-8')
    return path


def write_principles(root: Path, body: str, *, title: str = '系统每天怎么跑', version: int = 1) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'principles.md'
    path.write_text(f'---\ntitle: {title}\nversion: {version}\n---\n\n{body}', encoding='utf-8')
    return path


@pytest.fixture(autouse=True)
def fresh_cache():
    knowledge.reset_cache()
    yield
    knowledge.reset_cache()


def problems_of(root: Path) -> list[str]:
    with pytest.raises(KnowledgeError) as info:
        load(root)
    return info.value.problems


def test_load_valid_card(tmp_path):
    write_card(tmp_path, 'plan.stop', related=['indicator.atr14'], body='## 是什么\n\n止损。\n')
    write_card(tmp_path, 'indicator.atr14', title='ATR14', summary='14 日平均真实波幅。')
    cat = load(tmp_path)
    c = cat.concepts['plan.stop']
    assert (c.id, c.title, c.group, c.version) == ('plan.stop', '止损', 'plan', 1)
    assert c.applies_to == ('plan-1',) and c.related == ('indicator.atr14',) and c.sources == ()
    assert c.summary == '参考价减 2 倍 ATR14。'
    assert c.body_md == '## 是什么\n\n止损。'
    assert '<h2>是什么</h2>' in c.html
    assert c.ref == 'concept:plan.stop@1'
    assert re.fullmatch(r'[0-9a-f]{64}', c.content_sha256)
    assert c.content_sha256 == hashlib.sha256(c.body_md.encode('utf-8')).hexdigest()
    assert cat.principles is None and cat.loaded_at is not None


VALIDATION_CASES = [
    ('filename-id', dict(filename='plan.other.md'), 'plan.other.md', '与文件名不一致'),
    ('unknown-group', dict(group='foo'), 'plan.stop.md', '未知分组'),
    ('group-prefix', dict(group='rule'), 'plan.stop.md', '前缀不符'),
    ('extra-field', dict(colour='red'), 'plan.stop.md', '多余字段'),
    ('missing-summary', dict(drop=('summary',)), 'plan.stop.md', '缺少字段 summary'),
    ('version-zero', dict(version=0), 'plan.stop.md', 'version'),
    ('applies-unknown', dict(applies_to=['plan-9']), 'plan.stop.md', 'applies_to'),
    ('related-missing', dict(related=['indicator.nope']), 'plan.stop.md', 'related'),
    ('related-self', dict(related=['plan.stop']), 'plan.stop.md', 'related'),
    ('summary-long', dict(summary='字' * 121), 'plan.stop.md', 'summary'),
    ('summary-newline', dict(summary='第一行\n第二行'), 'plan.stop.md', 'summary'),
    ('title-long', dict(title='字' * 31), 'plan.stop.md', 'title'),
    ('body-empty', dict(body='\n  \n'), 'plan.stop.md', '正文'),
    ('body-long', dict(body='字' * 6001), 'plan.stop.md', '正文'),
    ('sources-missing', dict(sources=['investment_assistant/workbench/nope.py']), 'plan.stop.md', 'sources'),
]


@pytest.mark.parametrize('overrides,filename,keyword', [c[1:] for c in VALIDATION_CASES],
                         ids=[c[0] for c in VALIDATION_CASES])
def test_validation_rules(tmp_path, overrides, filename, keyword):
    write_card(tmp_path, 'plan.stop', **overrides)
    problems = problems_of(tmp_path)
    assert len(problems) == 1, problems
    assert filename in problems[0] and keyword in problems[0]


def test_boundary_values_accepted(tmp_path):
    write_card(tmp_path, 'plan.stop', title='字' * 30, summary='字' * 120, body='字' * 6000,
               sources=['investment_assistant/execution/plan_levels.py'])
    assert load(tmp_path).concepts['plan.stop'].sources == ('investment_assistant/execution/plan_levels.py',)


def test_all_problems_reported_at_once(tmp_path):
    write_card(tmp_path, 'plan.stop', version=0)
    write_card(tmp_path, 'indicator.atr14', summary='x' * 200)
    problems = problems_of(tmp_path)
    assert len(problems) == 2
    assert any('plan.stop.md' in p for p in problems) and any('indicator.atr14.md' in p for p in problems)
    with pytest.raises(KnowledgeError) as info:
        load(tmp_path)
    assert str(info.value) == info.value.problems[0]


@pytest.mark.parametrize('link', ['[a](https://x.com)', '![i](x.png)', '[a](../features/x.md)', '[a](#top)',
                                  '[a](#stage-collect)', '<https://x.com>'])
def test_forbidden_links_rejected(tmp_path, link):
    write_card(tmp_path, 'plan.stop', body=f'## 是什么\n\n见 {link}。\n')
    problems = problems_of(tmp_path)
    assert len(problems) == 1 and 'plan.stop.md' in problems[0]


def test_link_to_missing_card_rejected(tmp_path):
    write_card(tmp_path, 'plan.stop', body='见 [x](indicator.nope.md)。')
    problems = problems_of(tmp_path)
    assert len(problems) == 1 and 'indicator.nope' in problems[0]


def test_missing_field_does_not_stop_other_checks(tmp_path):
    write_card(tmp_path, 'plan.stop', drop=('summary',), body='见 [x](https://x.com)。', version=0)
    problems = problems_of(tmp_path)
    assert len(problems) == 3, problems
    assert any('缺少字段 summary' in p for p in problems) and any('version' in p for p in problems)
    assert any('https://x.com' in p for p in problems)


def test_missing_summary_plus_external_link_is_two_problems(tmp_path):
    write_card(tmp_path, 'plan.stop', drop=('summary',), body='见 [x](https://x.com)。')
    assert len(problems_of(tmp_path)) == 2


def test_reference_style_and_titled_card_links_rejected(tmp_path):
    write_card(tmp_path, 'plan.b', title='B')
    write_card(tmp_path, 'plan.a', body='见 [B][r]。\n\n[r]: plan.b.md\n')
    write_card(tmp_path, 'plan.c', body='见 [B](plan.b.md "标题")。')
    write_card(tmp_path, 'plan.d', body="见 [B](plan.b.md 'x')。")
    write_card(tmp_path, 'plan.e', body='见 [[B]](plan.b.md)。')
    problems = problems_of(tmp_path)
    assert len(problems) == 4
    for name in ('plan.a.md', 'plan.c.md', 'plan.d.md', 'plan.e.md'):
        assert sum(name in p for p in problems) == 1


def test_inline_code_lookalike_is_not_a_link(tmp_path):
    write_card(tmp_path, 'plan.b', title='B')
    write_card(tmp_path, 'plan.a', body='写法是 `[B](plan.b.md)`，真链接 [B](plan.b.md)。\n\n```\n[B](plan.b.md)\n```\n')
    write_card(tmp_path, 'plan.c', body='只有 `[B](plan.b.md)` 这种写法。')
    cat = load(tmp_path)
    assert cat.backlinks['plan.b'] == ('plan.a',)           # plan.c only has a code lookalike
    text = cat.concepts['plan.a'].body_text
    assert text.count('（concept:plan.b）') == 1
    assert text.count('`[B](plan.b.md)`') == 1 and '```\n[B](plan.b.md)\n```' in text
    assert cat.concepts['plan.c'].body_text == cat.concepts['plan.c'].body_md


def test_principles_heading_links_and_plain_text(tmp_path):
    write_card(tmp_path, 'plan.stop')
    write_principles(tmp_path, '## 用 `ATR` 的 **粗** 标题\n\n正文\n')
    assert load(tmp_path).principles.sections[0].heading == '用 ATR 的 粗 标题'
    write_principles(tmp_path, '## 见 [止损](concepts/plan.stop.md)\n\n## 外 [x](https://x.com)\n')
    problems = problems_of(tmp_path)
    assert len(problems) == 2 and all('标题' in p for p in problems)


def test_broken_yaml_does_not_cascade_related_errors(tmp_path):
    (tmp_path / 'concepts').mkdir()
    (tmp_path / 'concepts' / 'indicator.atr14.md').write_text('---\nid: [unclosed\n---\n\n正文', encoding='utf-8')
    write_card(tmp_path, 'plan.stop', related=['indicator.atr14'])
    problems = problems_of(tmp_path)
    assert len(problems) == 1 and 'indicator.atr14.md' in problems[0]


def test_sources_must_stay_inside_repo(tmp_path):
    for bad in ('../outside.py', '/etc/hosts', 'investment_assistant/../../x'):
        problems = None
        write_card(tmp_path, 'plan.stop', sources=[bad])
        problems = problems_of(tmp_path)
        assert len(problems) == 1 and '仓库内' in problems[0], (bad, problems)


def test_whitespace_only_title_and_summary_rejected(tmp_path):
    write_card(tmp_path, 'plan.stop', title='   ', summary=' \t ')
    problems = problems_of(tmp_path)
    assert len(problems) == 2
    assert any('title' in p for p in problems) and any('summary' in p for p in problems)


def test_concepts_prefix_link_only_allowed_in_principles(tmp_path):
    write_card(tmp_path, 'indicator.atr14', title='ATR14')
    write_card(tmp_path, 'plan.stop', body='见 [x](concepts/indicator.atr14.md)。')
    assert len(problems_of(tmp_path)) == 1


def test_raw_html_escaped(tmp_path):
    write_card(tmp_path, 'plan.stop', body='## 是什么\n\n<script>alert(1)</script>\n\n<b>粗</b> 与 <img src=x onerror=1>')
    html = load(tmp_path).concepts['plan.stop'].html
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert '<b>' not in html and '<img' not in html


def test_card_link_in_indented_code_block_is_rejected_with_a_hint(tmp_path):
    write_card(tmp_path, 'indicator.atr14', title='ATR14')
    write_card(tmp_path, 'plan.stop', related=['indicator.atr14'],
               body='用 [ATR](indicator.atr14.md) 计算。\n\n    [示例](indicator.atr14.md)\n')
    [problem] = problems_of(tmp_path)
    assert 'plan.stop.md' in problem and '概念卡链接必须是行内的' in problem and '或写在缩进代码块里' in problem


def test_javascript_href_never_becomes_a_link(tmp_path):
    write_card(tmp_path, 'plan.stop', body='[x](javascript:alert(1))')
    html = load(tmp_path).concepts['plan.stop'].html      # markdown-it refuses the scheme: plain text, no <a>
    assert '<a' not in html and 'href' not in html


def test_links_rewritten(tmp_path):
    write_card(tmp_path, 'indicator.atr14', title='ATR14')
    write_card(tmp_path, 'plan.stop', related=['indicator.atr14'],
               body='## 怎么算\n\n用 [ATR](indicator.atr14.md) 计算。\n\n| 列 | 值 |\n| --- | --- |\n| a | 1 |\n')
    c = load(tmp_path).concepts['plan.stop']
    assert '<a href="#knowledge/indicator.atr14" data-concept="indicator.atr14">ATR</a>' in c.html
    assert '<table>' in c.html
    assert 'ATR（concept:indicator.atr14）' in c.body_text
    assert '](indicator.atr14.md)' not in c.body_text
    assert '| a | 1 |' in c.body_text            # everything else verbatim
    assert c.body_md.count('](indicator.atr14.md)') == 1   # source untouched


PRINCIPLES = """导言一句。

## 系统做什么

出研究候选，你决定。

## ① 数据采集

读数据。[当天这一步](#stage-collect)，见 [止损](concepts/plan.stop.md)。

## ② 其它

没有链接。
"""


def test_principles_sections(tmp_path):
    write_card(tmp_path, 'plan.stop')
    write_principles(tmp_path, PRINCIPLES)
    p = load(tmp_path).principles
    assert (p.title, p.version) == ('系统每天怎么跑', 1)
    assert [s.heading for s in p.sections] == ['', '系统做什么', '① 数据采集', '② 其它']
    intro, _, collect, other = p.sections
    assert '导言一句' in intro.html and intro.stage is None
    assert collect.stage == 'collect' and collect.concepts == ('plan.stop',)
    assert 'href="#today"' in collect.html and 'data-stage="collect"' in collect.html
    assert '<a href="#knowledge/plan.stop" data-concept="plan.stop">止损</a>' in collect.html
    assert '<h2>' not in collect.html
    assert other.stage is None and other.concepts == ()


def test_principles_empty_intro_dropped(tmp_path):
    write_principles(tmp_path, '## 只有一节\n\n正文。\n')
    assert [s.heading for s in load(tmp_path).principles.sections] == ['只有一节']


def test_principles_unknown_and_duplicate_stage(tmp_path):
    write_principles(tmp_path, '## 一\n[x](#stage-foo)\n\n## 二\n[x](#stage-collect)\n\n## 三\n[x](#stage-collect)\n')
    problems = problems_of(tmp_path)
    assert len(problems) == 2
    assert any('foo' in p for p in problems) and any('collect' in p for p in problems)
    assert all('principles.md' in p for p in problems)


def test_principles_frontmatter_validated(tmp_path):
    (tmp_path).mkdir(exist_ok=True)
    (tmp_path / 'principles.md').write_text('---\ntitle: x\nversion: 0\nextra: 1\n---\n\n## 一\n', encoding='utf-8')
    problems = problems_of(tmp_path)
    assert len(problems) == 2 and all('principles.md' in p for p in problems)


def test_backlinks(tmp_path):
    write_card(tmp_path, 'plan.a', title='A', related=['plan.b', 'plan.c'])
    write_card(tmp_path, 'plan.b', title='B')
    write_card(tmp_path, 'plan.c', title='C', body='见 [B](plan.b.md)。')
    cat = load(tmp_path)
    assert cat.backlinks['plan.b'] == ('plan.a', 'plan.c')
    assert cat.backlinks['plan.c'] == ('plan.a',)
    assert 'plan.a' not in cat.backlinks


def test_parse_ref_and_stale(tmp_path):
    assert knowledge.parse_ref('concept:plan.stop@2') == ('plan.stop', 2)
    assert knowledge.parse_ref('plan.stop') == ('plan.stop', None)
    assert knowledge.parse_ref('concept:plan.stop') == ('plan.stop', None)
    for bad in ('concept:Plan.Stop', 'plan', 'plan.stop@0', 'plan.stop@x', 'foo.bar', '', 'plan.stop@'):
        with pytest.raises(ValueError):
            knowledge.parse_ref(bad)
    write_card(tmp_path, 'plan.stop')
    cat = load(tmp_path)
    concept, stale = knowledge.lookup(cat, 'concept:plan.stop@2')
    assert concept.id == 'plan.stop'
    assert stale == {'requested': 2, 'current': 1, 'note': '该引用指向第 2 版，当前为第 1 版；概念卡不保存旧版正文'}
    assert knowledge.lookup(cat, 'plan.stop@1')[1] is None
    assert knowledge.lookup(cat, 'plan.stop')[1] is None
    with pytest.raises(LookupError):
        knowledge.lookup(cat, 'plan.nope')
    with pytest.raises(ValueError):
        knowledge.lookup(cat, 'Plan.Stop')


def test_empty_or_missing_root(tmp_path):
    for root in (tmp_path / 'nope', tmp_path):
        cat = load(root)
        assert cat.concepts == {} and cat.backlinks == {} and cat.principles is None


def test_catalog_failure_not_cached(tmp_path, monkeypatch):
    write_card(tmp_path, 'plan.stop', version=0)
    monkeypatch.setattr(knowledge, 'DEFAULT_ROOT', tmp_path)
    with pytest.raises(KnowledgeError):
        knowledge.catalog()
    write_card(tmp_path, 'plan.stop')
    first = knowledge.catalog()
    assert 'plan.stop' in first.concepts
    assert knowledge.catalog() is first                      # success is cached
    write_card(tmp_path, 'plan.stop', version=0)
    assert knowledge.catalog() is first                      # until restart / reset
    knowledge.catalog.cache_clear()
    with pytest.raises(KnowledgeError):
        knowledge.catalog()


def test_knowledge_module_does_not_import_agent_or_service_at_top_level():
    source = Path(knowledge.__file__).read_text(encoding='utf-8')
    imports = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module or '')
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    assert not [name for name in imports if name.startswith('investment_assistant.')
                and not (name == 'investment_assistant.core' or name.startswith('investment_assistant.core.')
                         or name == 'investment_assistant.data.calendar')], imports


def test_groups_order():
    assert [g for g, _ in knowledge.GROUPS] == ['family', 'indicator', 'rule', 'plan', 'agent', 'account', 'review', 'system']
    assert dict(knowledge.GROUPS)['family'] == '数据族' and dict(knowledge.GROUPS)['system'] == '系统运作'
    assert dict(knowledge.GROUPS)['account'] == '账户与模拟交易'


def test_agent_text(tmp_path):
    write_card(tmp_path, 'plan.stop', related=['indicator.atr14'], body='用 [ATR](indicator.atr14.md)。')
    write_card(tmp_path, 'indicator.atr14', title='ATR14')
    cat = load(tmp_path)
    one = knowledge.agent_text(cat, 'plan.stop')
    assert set(one) == {'concept_ref', 'id', 'title', 'group', 'version', 'applies_to', 'summary', 'body',
                        'content_sha256', 'knowledge_version', 'point_in_time', 'note'}
    assert one['point_in_time'] is False and one['concept_ref'] == 'concept:plan.stop@1'
    assert one['knowledge_version'] == 'knowledge-1' and one['note'] == '概念卡随代码版本走，不按截止时间回读'
    assert one['body'] == 'ATR（concept:indicator.atr14）'.join(['用 ', '。'])
    assert one['content_sha256'] == cat.concepts['plan.stop'].content_sha256
    assert one['applies_to'] == ['plan-1']
    index = knowledge.agent_text(cat, None)
    assert set(index) == {'knowledge_version', 'point_in_time', 'concepts'} and index['point_in_time'] is False
    assert index['concepts'] == [{'id': 'indicator.atr14', 'title': 'ATR14', 'group': 'indicator'},
                                 {'id': 'plan.stop', 'title': '止损', 'group': 'plan'}]
    for bad in ('plan.nope', 'Not An Id'):
        with pytest.raises(LookupError) as info:
            knowledge.agent_text(cat, bad)
        assert str(info.value) == f'未知概念 {bad}，不给 id 可取全部列表'
    with pytest.raises(LookupError) as info:                 # card_view / lookup keep the 404 wording
        knowledge.lookup(cat, 'plan.nope')
    assert str(info.value) == '没有这张概念卡：plan.nope'


def test_views(tmp_path):
    write_card(tmp_path, 'plan.stop', related=['indicator.atr14'])
    write_card(tmp_path, 'indicator.atr14', title='ATR14')
    write_principles(tmp_path, '## 一\n[x](#stage-collect)\n')
    cat = load(tmp_path)
    index = knowledge.index_view(cat)
    assert index['knowledge_version'] == 'knowledge-1'
    assert [(g['id'], g['count']) for g in index['groups']] == [
        ('family', 0), ('indicator', 1), ('rule', 0), ('plan', 1), ('agent', 0), ('account', 0), ('review', 0), ('system', 0)]
    assert [c['id'] for c in index['concepts']] == ['indicator.atr14', 'plan.stop']
    assert set(index['concepts'][1]) == {'id', 'ref', 'title', 'group', 'version', 'applies_to', 'summary', 'related'}
    card = knowledge.card_view(cat, 'indicator.atr14')
    assert card['backlinks'] == [{'id': 'plan.stop', 'title': '止损'}] and card['stale'] is None
    assert knowledge.card_view(cat, 'plan.stop')['related_cards'] == [{'id': 'indicator.atr14', 'title': 'ATR14'}]
    assert 'html' in card
    assert knowledge.card_view(cat, 'plan.stop@3')['stale']['requested'] == 3
    view = knowledge.principles_view(cat)
    assert view['title'] == '系统每天怎么跑' and view['sections'][0]['stage'] == 'collect'
    assert set(view['sections'][0]) == {'heading', 'html', 'stage', 'concepts'}
    with pytest.raises(LookupError):
        knowledge.principles_view(load(tmp_path / 'empty'))


def test_cli_check_and_show(tmp_path, capsys):
    good = tmp_path / 'good'
    write_card(good, 'plan.stop')
    write_principles(good, '## 一\n\n## 二\n\n## 三\n')
    assert knowledge.main(['check', '--root', str(good)]) == 0
    assert '概念卡 1 张、原理页 3 节，通过' in capsys.readouterr().out
    bad = tmp_path / 'bad'
    write_card(bad, 'plan.stop', version=0)
    write_card(bad, 'plan.other', summary='')
    assert knowledge.main(['check', '--root', str(bad)]) == 1
    out = capsys.readouterr().out
    assert 'plan.stop.md' in out and 'plan.other.md' in out
    assert knowledge.main(['show', 'concept:plan.stop', '--root', str(good)]) == 0
    shown = capsys.readouterr().out
    assert 'plan.stop' in shown and '止损' in shown and '止损是一个价位' in shown
    assert knowledge.main(['show', 'plan.nope', '--root', str(good)]) == 1
    assert knowledge.main(['show', 'plan.stop', '--root', str(bad)]) == 1
