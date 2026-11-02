import copy
from datetime import date
from uuid import UUID

from investment_assistant.optimize.export import render_markdown


def records():
    return {'goals': [{'goal_id': 'process', 'version': 1, 'body': '最终目标：稳定执行。\n主指标：流程偏差。'}],
            'versions': [
                {'version_id': 'v2.12', 'version': 1, 'title': '注册表 | 数据', 'theme': '数据可信',
                 'scope': '覆盖 bars。\n保留原口径。', 'depends_on': ['v2.11'], 'status': 'done',
                 'exit_conditions': '主备源可复现。', 'spec_path': 'docs/specs/a.md', 'plan_path': 'docs/plans/a.md',
                 'tag': 'v2.12.0', 'proposal_id': None},
                {'version_id': 'v2.15', 'version': 2, 'title': 'roadmap', 'theme': '系统内规划',
                 'scope': '保留决策原话。', 'depends_on': ['v2.12'], 'status': 'planned',
                 'exit_conditions': '完整导出。', 'spec_path': 'docs/specs/b.md', 'plan_path': 'docs/plans/b.md',
                 'tag': None, 'proposal_id': 7}],
            'decisions': [
                {'decision_no': 112, 'version': 1, 'decided_on': date(2026, 11, 1), 'question': '是否合并？',
                 'decision': '一次发布。', 'quote': '  原话有空格，不能删。  ', 'version_ids': ['v2.12', 'v2.15']},
                {'decision_no': 113, 'version': 1, 'decided_on': date(2026, 11, 1), 'question': '研究开链？',
                 'decision': '只建档案。', 'quote': '选 B。\n不自动开链。', 'version_ids': ['v2.13']},
                {'decision_no': 114, 'version': 1, 'decided_on': date(2026, 11, 1), 'question': '界面要求？',
                 'decision': '保留原文。', 'quote': '中文 | `代码` **原话**\n> 引用', 'version_ids': ['v2.15']}],
            'evidence': [{'evidence_id': 'manual-20261101', 'version': 1, 'version_id': 'v2.12',
                          'kind': 'manual', 'body': '备源测试通过。', 'observed_on': date(2026, 11, 1),
                          'source_path': 'docs/roadmap/acceptance.md'}],
            'proposals': [{'id': 7, 'title': '补齐失败证据', 'source': 'data', 'change': '新增记录。',
                           'rationale': '失败目前不可见。', 'evidence_refs': ['fact:data_failures#abc'],
                           'expected': '定位缺口。', 'acceptance': '每次失败有原因。', 'status': 'proposed',
                           'request_id': UUID('12345678-1234-1234-1234-123456789012')}]}


def test_renders_all_ids_and_quotes_verbatim():
    source = records()
    original = copy.deepcopy(source)
    text = render_markdown(**source)
    for version in source['versions']:
        assert version['version_id'] in text
        for key in ('scope', 'theme', 'exit_conditions', 'spec_path', 'plan_path'):
            assert version[key] in text
    for decision in source['decisions']:
        assert f"### 决策 #{decision['decision_no']}" in text
        assert decision['quote'] in text
        assert decision['question'] in text
        assert decision['decision'] in text
    assert text.count('### 决策 #') == 3
    assert text.count('### 版本 ') == 2
    assert 'manual-20261101' in text and '备源测试通过。' in text
    assert '最终目标：稳定执行。\n主指标：流程偏差。' in text
    assert '12345678-1234-1234-1234-123456789012' in text
    assert 'fact:data_failures#abc' in text
    assert source == original


def test_template_order_empty_states_and_determinism():
    text = render_markdown(**records())
    headings = ['## 目标', '## 版本总览表', '## 各版定义', '## 决策', '## 证据索引', '## 未决提案']
    offsets = [text.index(h) for h in headings]
    assert offsets == sorted(offsets)
    assert render_markdown(**records()) == text
    empty = render_markdown(goals=[], versions=[], decisions=[], evidence=[], proposals=[])
    assert all(h in empty for h in headings)
    assert empty.count('暂无记录。') == 6


def test_version_table_escapes_pipes_but_detail_keeps_original():
    text = render_markdown(**records())
    table = text.split('## 版本总览表', 1)[1].split('## 各版定义', 1)[0]
    assert '注册表 \\| 数据' in table
    assert '注册表 | 数据' in text.split('## 各版定义', 1)[1]


def test_export_keeps_every_record_revision_and_unknown_metadata():
    source = records()
    old = copy.deepcopy(source['decisions'][0])
    old.update(version=2, quote='修订后的原话。', context='补充上下文不可丢。')
    source['decisions'].append(old)
    text = render_markdown(**source)
    assert text.count('### 决策 #112') == 2
    assert '  原话有空格，不能删。  ' in text
    assert '修订后的原话。' in text
    assert '补充上下文不可丢。' in text
