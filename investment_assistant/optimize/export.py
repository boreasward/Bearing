"""Deterministic roadmap Markdown from supplied records (spec §8.2).

No query, filesystem access, current time, deduplication or status filtering:
the caller selects record versions and open proposals. Every supplied row and
field is retained, including metadata and verbatim multiline user quotations.
Only overview-table cells are escaped; detail strings are not rewritten.
"""
from __future__ import annotations

import json


_LABELS = {
    'goal_id': '目标 ID', 'version': '记录版本', 'body': '内容', 'version_id': '版本 ID', 'title': '标题',
    'theme': '主题', 'scope': '范围', 'depends_on': '依赖', 'status': '状态', 'exit_conditions': '退出条件',
    'spec_path': '设计文档', 'plan_path': '实施计划', 'tag': '标签', 'proposal_id': '关联提案',
    'decision_no': '决策编号', 'decided_on': '决策日期', 'question': '问题', 'decision': '决定',
    'quote': '用户原话', 'version_ids': '关联版本', 'evidence_id': '证据 ID', 'kind': '种类',
    'observed_on': '证据日期', 'source_path': '来源路径', 'source': '来源', 'change': '改动',
    'rationale': '理由', 'evidence_refs': '证据引用', 'expected': '预期', 'acceptance': '验收',
    'decided_at': '处理时间', 'decision_reason': '处理理由', 'created_at': '创建时间',
}
_FIELD_ORDER = tuple(_LABELS)


def _value(value) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return '—'
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)
    return str(value)


def _cell(value) -> str:
    return _value(value).replace('\\', '\\\\').replace('|', '\\|').replace('\r\n', '\n').replace('\n', '<br>')


def _records(rows, *, title, id_key) -> str:
    chunks = []
    for row in rows:
        revision = f" · 记录版本 {row['version']}" if 'version' in row else ''
        chunks.append(f"### {title}{row[id_key]}{revision}")
        keys = [key for key in _FIELD_ORDER if key in row] + sorted(set(row) - set(_FIELD_ORDER))
        for key in keys:
            chunks.append(f"**{_LABELS.get(key, key)}**\n\n{_value(row[key])}")
    return '\n\n'.join(chunks) if chunks else '暂无记录。'


def render_markdown(*, goals, versions, decisions, evidence, proposals) -> str:
    """Fixed six-section template; accepts iterable mappings, returns Markdown."""
    versions = list(versions)  # Overview and definitions each consume the same complete sequence.
    if versions:
        overview = ['| 版本 | 标题 | 主题 | 状态 | 依赖 |', '| --- | --- | --- | --- | --- |']
        for row in versions:
            overview.append('| ' + ' | '.join(_cell(row.get(key)) for key in
                                             ('version_id', 'title', 'theme', 'status', 'depends_on')) + ' |')
        table = '\n'.join(overview)
    else:
        table = '暂无记录。'
    sections = [
        ('目标', _records(goals, title='目标 ', id_key='goal_id')),
        ('版本总览表', table),
        ('各版定义', _records(versions, title='版本 ', id_key='version_id')),
        ('决策', _records(decisions, title='决策 #', id_key='decision_no')),
        ('证据索引', _records(evidence, title='证据 ', id_key='evidence_id')),
        ('未决提案', _records(proposals, title='提案 #', id_key='id')),
    ]
    return '# Roadmap\n\n' + '\n\n'.join(f'## {title}\n\n{body}' for title, body in sections) + '\n'
