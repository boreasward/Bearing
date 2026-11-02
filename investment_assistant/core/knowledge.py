"""Versioned concept cards and the principles page (v2.6 spec §4–§5.2, §10–§11).

Cards live in docs/knowledge/concepts/<id>.md (YAML frontmatter + Markdown), the principles page in
docs/knowledge/principles.md. `load` reads and validates the whole directory, collecting every problem
before it raises; rendering happens here too (markdown-it-py, raw HTML off, links rewritten at token level),
so the API, the Agent tool and the CLI share one implementation. Pure file reads: no database.

This module must not import `workbench.agent` or `service` at module level — agent/tools.py imports it.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from markdown_it import MarkdownIt

from investment_assistant.core.stages import STAGE_IDS

KNOWLEDGE_VERSION = 'knowledge-1'

# Group ids in display order, with the page titles (spec §4.1).
GROUPS: tuple[tuple[str, str], ...] = (
    ('family', '数据族'),
    ('indicator', '指标'),
    ('rule', '规则 v1'),
    ('plan', '规则计划 plan-1'),
    ('agent', 'Agent 计划'),
    ('account', '账户与模拟交易'),
    ('review', '复盘'),
    ('system', '系统运作'),
)

# Code version strings a card may declare in `applies_to`: current ones plus the history still present in old
# rows. A literal on purpose (see the module docstring); tests/test_knowledge.py pins it to the code constants.
KNOWN_VERSIONS: frozenset[str] = frozenset({
    'plan-1', 'agent-1', 'agent-2', 'agent-review-1', 'agent-brief-1', 'condition-1', 'timeline-1',
    'context-2', 'screen-1', 'regime-2', 'derivation-1', 'plan-shadow-1', 'tracks-1', 'review-6',
    'monthly-3', 'stages-2', 'backcheck-2', 'rules-1', 'knowledge-1',
    'stages-1', 'review-5', 'monthly-2', 'regime-1', 'context-1', 'backcheck-1',
    'stages-3', 'retro-1', 'agent-retro-1', 'agent-discuss-1', 'my-knowledge-1', 'knowledge-perf-1', 'review-7',
    'stages-4', 'daily-1', 'fetch-1', 'siwc-1',
    'timeline-2', 'condition-2', 'plan-2', 'plan-shadow-2', 'agent-3', 'agent-review-2', 'agent-retro-2',
    'entry-1', 'execution-1', 'discipline-1', 'discipline-2', 'tracks-2', 'retro-2', 'review-8', 'monthly-4',
    'account-1', 'paper-fill-1', 'follow-plans-1', 'agent-portfolio-1', 'stages-5', 'daily-2', 'monthly-5',
    'agent-4', 'agent-portfolio-2', 'agent-pm-1', 'agent-optimize-1', 'agent-impl-brief-1', 'stages-6', 'daily-3',
    'portfolio-check-1', 'indicator-1',
})

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO_ROOT / 'docs' / 'knowledge'

ID_BODY = r'(?:family|indicator|rule|plan|agent|account|review|system)\.[a-z0-9_]+'
ID_RE = re.compile(rf'^{ID_BODY}$')
REF_RE = re.compile(rf'^(?:concept:)?({ID_BODY})(?:@([1-9][0-9]*))?$')
CARD_LINK_RE = re.compile(rf'^(?:(concepts/))?({ID_BODY})\.md$')
STAGE_LINK_RE = re.compile(r'^#stage-([a-z0-9_]+)$')
# For the Agent text: only the inline form `[文字](<id>.md)` (no title, no reference style, no brackets inside the
# text) is accepted in cards, so this rewrite is exact; code fences and code spans are left alone.
CODE_OR_CARD_LINK_RE = re.compile(
    rf'(?P<fence>^(?P<ticks>`{{3,}}|~{{3,}})[^\n]*\n.*?(?:^(?P=ticks)[`~]*[ \t]*$|\Z))'
    rf'|(?P<code>(?P<run>`+)(?:(?!(?P=run)).)+?(?P=run))'
    rf'|(?P<link>\[(?P<text>[^\[\]]*)\]\((?P<id>{ID_BODY})\.md\))',
    re.DOTALL | re.MULTILINE)
FRONTMATTER_RE = re.compile(r'\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)(.*)\Z', re.DOTALL)

CARD_REQUIRED = ('id', 'title', 'group', 'version', 'applies_to', 'summary', 'related')
CARD_OPTIONAL = ('sources',)
PRINCIPLES_REQUIRED = ('title', 'version')
TITLE_MAX, SUMMARY_MAX, BODY_MAX = 30, 120, 6000

STALE_NOTE = '该引用指向第 {requested} 版，当前为第 {current} 版；概念卡不保存旧版正文'
AGENT_NOTE = '概念卡随代码版本走，不按截止时间回读'

_md = MarkdownIt('commonmark', {'html': False}).enable('table')


class KnowledgeError(Exception):
    """The card directory is invalid; `problems` lists every problem found, `str(exc)` is the first."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__(self.problems[0] if self.problems else '概念卡加载失败')


@dataclass(frozen=True)
class Concept:
    id: str
    title: str
    group: str
    version: int
    applies_to: tuple[str, ...]
    summary: str
    related: tuple[str, ...]
    sources: tuple[str, ...]
    body_md: str
    body_text: str
    html: str
    content_sha256: str

    @property
    def ref(self) -> str:
        return f'concept:{self.id}@{self.version}'


@dataclass(frozen=True)
class Section:
    heading: str
    html: str
    stage: str | None
    concepts: tuple[str, ...]


@dataclass(frozen=True)
class Principles:
    title: str
    version: int
    sections: tuple[Section, ...]


@dataclass(frozen=True)
class Catalog:
    concepts: dict[str, Concept]
    backlinks: dict[str, tuple[str, ...]]
    principles: Principles | None
    loaded_at: datetime


# --------------------------------------------------------------------------------------------- parsing


@dataclass
class _Parsed:
    label: str                    # 'concepts/plan.stop.md' / 'principles.md'
    stem: str
    meta: dict[str, Any]
    body: str
    tokens: list


def _split(text: str, label: str, problems: list[str]) -> tuple[dict[str, Any], str] | None:
    match = FRONTMATTER_RE.match(text)
    if not match:
        problems.append(f'{label}: 缺少 frontmatter（文件须以 --- 包起来的 YAML 开头）')
        return None
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        problems.append(f'{label}: frontmatter 不是合法 YAML（{str(exc).splitlines()[0]}）')
        return None
    if not isinstance(meta, dict):
        problems.append(f'{label}: frontmatter 必须是键值对')
        return None
    return meta, match.group(2).strip()


def _field_set(meta: dict[str, Any], required: tuple[str, ...], optional: tuple[str, ...], label: str,
               problems: list[str]) -> set[str]:
    """Report missing and extra fields (one problem each); return the missing ones. Checking continues anyway."""
    missing = {key for key in required if key not in meta}
    for key in required:
        if key in missing:
            problems.append(f'{label}: 缺少字段 {key}')
    for key in meta:
        if key not in required and key not in optional:
            problems.append(f'{label}: 多余字段 {key}')
    return missing


def _is_pos_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _read(path: Path, label: str, problems: list[str]) -> str | None:
    try:
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        problems.append(f'{label}: 无法读取（{exc.__class__.__name__}）')
        return None


def _inline_children(tokens: list):
    for token in tokens:
        if token.type == 'inline' and token.children:
            yield from token.children


def _check_links(tokens: list, label: str, *, principles: bool, concept_ids: set[str], stage_ids: tuple[str, ...],
                 problems: list[str]) -> list[tuple[str, str, Any]]:
    """Validate every link/image in `tokens`; return [(kind, target, link_open token)] for the valid ones."""
    found: list[tuple[str, str, Any]] = []
    for token in _inline_children(tokens):
        if token.type == 'image':
            problems.append(f'{label}: 不允许图片（{token.attrGet("src")}）')
        elif token.type == 'link_open':
            href = token.attrGet('href') or ''
            card = CARD_LINK_RE.match(href)
            stage = STAGE_LINK_RE.match(href)
            if card and (principles or not card.group(1)):
                if card.group(2) not in concept_ids:
                    problems.append(f'{label}: 链接指向不存在的概念卡 {card.group(2)}')
                else:
                    found.append(('concept', card.group(2), token))
            elif stage and principles:
                if stage.group(1) not in stage_ids:
                    problems.append(f'{label}: 未知阶段 #stage-{stage.group(1)}')
                else:
                    found.append(('stage', stage.group(1), token))
            elif stage:
                problems.append(f'{label}: 卡里不允许阶段链接 {href}（只许原理页）')
            else:
                problems.append(f'{label}: 不允许的链接 {href}（只许 <id>.md 形式的概念卡链接）')
    return found


def _rewrite(found: list[tuple[str, str, Any]]) -> None:
    """Rewrite validated links on the tokens themselves (never on the rendered HTML)."""
    for kind, target, token in found:
        if kind == 'concept':
            token.attrSet('href', f'#knowledge/{target}')
            token.attrSet('data-concept', target)
        else:
            token.attrSet('href', '#today')
            token.attrSet('data-stage', target)


def _agent_body(body: str) -> tuple[str, int]:
    """Agent text of a card body: inline card links become `文字（concept:<id>）`; returns (text, links rewritten)."""
    count = 0

    def repl(match: re.Match) -> str:
        nonlocal count
        if match.group('link') is None:
            return match.group(0)
        count += 1
        return f'{match.group("text")}（concept:{match.group("id")}）'

    return CODE_OR_CARD_LINK_RE.sub(repl, body), count


def _render(tokens: list) -> str:
    return _md.renderer.render(tokens, _md.options, {})


def _validate_card(parsed: _Parsed, concept_ids: set[str], repo_root: Path,
                   problems: list[str]) -> tuple[Concept, set[str]] | None:
    """Validate one card; returns (concept, ids it links to in the body) or None when it has problems."""
    label, meta = parsed.label, parsed.meta
    start = len(problems)
    missing = _field_set(meta, CARD_REQUIRED, CARD_OPTIONAL, label, problems)
    card_id, title, group, version = meta.get('id'), meta.get('title'), meta.get('group'), meta.get('version')
    summary, applies_to, related = meta.get('summary'), meta.get('applies_to'), meta.get('related')
    sources = meta.get('sources', [])
    id_ok = isinstance(card_id, str) and bool(ID_RE.match(card_id))

    if 'id' not in missing:
        if not id_ok:
            problems.append(f'{label}: id {card_id!r} 格式不对（分组.名字，小写字母数字下划线）')
        elif card_id != parsed.stem:
            problems.append(f'{label}: id {card_id} 与文件名不一致')
    if 'title' not in missing:
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= TITLE_MAX:
            problems.append(f'{label}: title 须是 1–{TITLE_MAX} 字的文字' +
                            (f'（现 {len(title.strip())} 字）' if isinstance(title, str) else ''))
        else:
            title = title.strip()
    if 'group' not in missing:
        if group not in [g for g, _ in GROUPS]:
            problems.append(f'{label}: 未知分组 {group!r}')
        elif id_ok and card_id.split('.')[0] != group:
            problems.append(f'{label}: group {group} 与 id 前缀不符')
    if 'version' not in missing and not _is_pos_int(version):
        problems.append(f'{label}: version 必须是正整数，现为 {version!r}')
    if 'applies_to' not in missing:
        if not _str_list(applies_to):
            problems.append(f'{label}: applies_to 必须是版本号列表')
        else:
            for item in applies_to:
                if item not in KNOWN_VERSIONS:
                    problems.append(f'{label}: applies_to 含未知版本 {item}')
    if 'summary' not in missing:
        if not isinstance(summary, str) or not 1 <= len(summary.strip()) <= SUMMARY_MAX:
            problems.append(f'{label}: summary 须是 1–{SUMMARY_MAX} 字' +
                            (f'（现 {len(summary.strip())} 字）' if isinstance(summary, str) else ''))
        elif '\n' in summary.strip() or '\r' in summary.strip():
            problems.append(f'{label}: summary 不能含换行')
        else:
            summary = summary.strip()
    if 'related' not in missing:
        if not _str_list(related):
            problems.append(f'{label}: related 必须是概念 id 列表')
        else:
            for item in related:
                if item == card_id:
                    problems.append(f'{label}: related 不能包含自己')
                elif item not in concept_ids:
                    problems.append(f'{label}: related 里的 {item} 不存在')
    if not _str_list(sources):
        problems.append(f'{label}: sources 必须是仓库内路径列表')
    else:
        base = repo_root.resolve()
        for item in sources:
            if Path(item).is_absolute() or not (base / item).resolve().is_relative_to(base):
                problems.append(f'{label}: sources 里的 {item} 必须是仓库内的相对路径')
            elif not (base / item).exists():
                problems.append(f'{label}: sources 里的 {item} 不存在')
    body = parsed.body
    if not body:
        problems.append(f'{label}: 正文为空')
    elif len(body) > BODY_MAX:
        problems.append(f'{label}: 正文超过 {BODY_MAX} 字（现 {len(body)} 字）')

    found = _check_links(parsed.tokens, label, principles=False, concept_ids=concept_ids, stage_ids=(),
                         problems=problems)
    body_text, rewritten = _agent_body(body)
    card_links = [target for kind, target, _ in found if kind == 'concept']
    card_tokens = sum(1 for t in _inline_children(parsed.tokens)           # card-shaped links (valid or not)
                      if t.type == 'link_open' and (m := CARD_LINK_RE.match(t.attrGet('href') or ''))
                      and not m.group(1))
    if rewritten != card_tokens:
        problems.append(f'{label}: 概念卡链接必须是行内的 [文字](<id>.md)，不能用引用式、带标题、文字里含方括号，或写在缩进代码块里')
    if len(problems) > start:
        return None
    _rewrite(found)
    concept = Concept(
        id=card_id, title=title, group=group, version=version, applies_to=tuple(applies_to), summary=summary,
        related=tuple(related), sources=tuple(sources), body_md=body, body_text=body_text,
        html=_render(parsed.tokens), content_sha256=hashlib.sha256(body.encode('utf-8')).hexdigest())
    return concept, set(card_links)


def _plain(inline) -> str:
    """Plain text of an inline token (code spans without backticks, soft breaks as spaces)."""
    parts = []
    for child in inline.children or []:
        if child.type in ('text', 'code_inline'):
            parts.append(child.content)
        elif child.type in ('softbreak', 'hardbreak'):
            parts.append(' ')
    return ''.join(parts).strip()


def _split_sections(tokens: list) -> list[tuple[str, Any, list]]:
    """Split block tokens at top-level `## ` headings; text before the first one gets heading ''.

    Returns [(plain heading, the heading's inline token or None, section body tokens)]."""
    sections: list[tuple[str, Any, list]] = [('', None, [])]
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.type == 'heading_open' and token.tag == 'h2' and token.level == 0 and i + 2 < len(tokens):
            inline = tokens[i + 1]
            sections.append((_plain(inline), inline, []))
            i += 3                                   # heading_open, inline, heading_close
            continue
        sections[-1][2].append(token)
        i += 1
    return sections


def _validate_principles(parsed: _Parsed, concept_ids: set[str], stage_ids: tuple[str, ...],
                         problems: list[str]) -> Principles | None:
    label, meta = parsed.label, parsed.meta
    start = len(problems)
    missing = _field_set(meta, PRINCIPLES_REQUIRED, (), label, problems)
    if missing:
        return None
    if not isinstance(meta['title'], str) or not meta['title'].strip():
        problems.append(f'{label}: title 不能为空')
    if not _is_pos_int(meta['version']):
        problems.append(f'{label}: version 必须是正整数，现为 {meta["version"]!r}')
    sections: list[Section] = []
    seen_stages: dict[str, str] = {}
    for heading, heading_inline, tokens in _split_sections(parsed.tokens):
        where = f'{label}（{heading}）' if heading else label
        if heading_inline is not None and any(c.type in ('link_open', 'image') for c in heading_inline.children or []):
            problems.append(f'{where}: 标题里不能有链接或图片')
        found = _check_links(tokens, where, principles=True, concept_ids=concept_ids, stage_ids=stage_ids,
                             problems=problems)
        stages_here: list[str] = []
        for kind, target, _ in found:
            if kind != 'stage':
                continue
            if target in seen_stages:
                problems.append(f'{where}: #stage-{target} 出现了两次（已在“{seen_stages[target]}”）')
            else:
                seen_stages[target] = heading
                stages_here.append(target)
        if len(stages_here) > 1:
            problems.append(f'{where}: 一节里只许链一个阶段，现有 {", ".join(stages_here)}')
        concepts: list[str] = []
        for kind, target, _ in found:
            if kind == 'concept' and target not in concepts:
                concepts.append(target)
        _rewrite(found)
        html = _render(tokens)
        if not heading and not html.strip():
            continue                                  # empty intro
        sections.append(Section(heading=heading, html=html, stage=stages_here[0] if stages_here else None,
                                concepts=tuple(concepts)))
    if len(problems) > start:
        return None
    return Principles(title=meta['title'], version=meta['version'], sections=tuple(sections))


# --------------------------------------------------------------------------------------------- loading


def load(root: Path, *, stage_ids: tuple[str, ...] = STAGE_IDS, repo_root: Path = REPO_ROOT) -> Catalog:
    """Read and validate `root/concepts/*.md` and `root/principles.md`; raise KnowledgeError with every problem."""
    root = Path(root)
    problems: list[str] = []
    cards: list[_Parsed] = []
    folder = root / 'concepts'
    card_files = sorted(folder.glob('*.md')) if folder.is_dir() else []
    for path in card_files:
        label = f'concepts/{path.name}'
        text = _read(path, label, problems)
        split = _split(text, label, problems) if text is not None else None
        if split is not None:
            meta, body = split
            cards.append(_Parsed(label, path.stem, meta, body, _md.parse(body)))
    principles_parsed = None
    principles_path = root / 'principles.md'
    if principles_path.is_file():
        text = _read(principles_path, 'principles.md', problems)
        split = _split(text, 'principles.md', problems) if text is not None else None
        if split is not None:
            meta, body = split
            principles_parsed = _Parsed('principles.md', 'principles', meta, body, _md.parse(body))

    concept_ids = {path.stem for path in card_files}      # every file counts, even one that failed to parse
    concepts: dict[str, Concept] = {}
    body_links: dict[str, set[str]] = {}
    for parsed in cards:
        result = _validate_card(parsed, concept_ids, repo_root, problems)
        if result is not None:
            concepts[result[0].id] = result[0]
            body_links[result[0].id] = result[1]
    principles = (_validate_principles(principles_parsed, concept_ids, stage_ids, problems)
                  if principles_parsed is not None else None)
    if problems:
        raise KnowledgeError(problems)

    linked_from: dict[str, set[str]] = {}
    for concept in concepts.values():
        targets = set(concept.related) | body_links[concept.id]    # from validated tokens
        for target in targets - {concept.id}:
            linked_from.setdefault(target, set()).add(concept.id)
    backlinks = {target: tuple(sorted(sources)) for target, sources in linked_from.items()}
    return Catalog(concepts=concepts, backlinks=backlinks, principles=principles, loaded_at=datetime.now(UTC))


# Process-wide cache, keyed on the root so a changed DEFAULT_ROOT (tests) reloads. Failures are never cached.
_cache_lock = threading.Lock()
_cached: tuple[Path, Catalog] | None = None


def catalog() -> Catalog:
    global _cached
    root = Path(DEFAULT_ROOT)
    with _cache_lock:
        if _cached is not None and _cached[0] == root:
            return _cached[1]
        cat = load(root)                              # raises -> nothing cached
        _cached = (root, cat)
        return cat


def reset_cache() -> None:
    global _cached
    with _cache_lock:
        _cached = None


catalog.cache_clear = reset_cache                     # type: ignore[attr-defined]


# --------------------------------------------------------------------------------------------- refs

KNOWLEDGE_RESULT_MAX = 10
INDEX_MAX = 50
CITATION_FIELDS = ('thesis', 'invalidation', 'changes', 'counter', 'risks', 'review')
KNOW_REF_RE = re.compile(r'^know:([1-9][0-9]*)(?:@([1-9][0-9]*))?$')


def parse_knowledge_ref(text: str) -> tuple[int, int | None]:
    """``know:<id>[@<version>]`` -> ``(id, version | None)``; anything else raises ``ValueError``.
    The version-less form is accepted in page addresses only; agent citations must carry one."""
    match = KNOW_REF_RE.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise ValueError(f'不是知识引用：{text!r}')
    return int(match.group(1)), int(match.group(2)) if match.group(2) else None


def ref(knowledge_id: int, version: int) -> str:
    return f'know:{knowledge_id}@{version}'


def _order(row: dict[str, Any]) -> tuple:
    return row['created_at'], row.get('id') or 0


def status_at(statuses: list[dict[str, Any]], cutoff: datetime | None) -> str:
    """One item's state at ``cutoff`` (None = now): the last record written at or before it, else ``active``."""
    seen = [row for row in statuses if cutoff is None or row['created_at'] <= cutoff]
    return max(seen, key=_order)['status'] if seen else 'active'


def visible(versions: list[dict[str, Any]], statuses: list[dict[str, Any]], *,
            cutoff: datetime | None) -> list[dict[str, Any]]:
    """The highest version visible at cutoff, excluding items archived then; ordered by item id."""
    top: dict[Any, dict[str, Any]] = {}
    for row in versions:
        if cutoff is not None and row['created_at'] > cutoff:
            continue
        current = top.get(row['knowledge_id'])
        if current is None or row['version'] > current['version']:
            top[row['knowledge_id']] = row
    by_item: dict[Any, list[dict[str, Any]]] = {}
    for row in statuses:
        by_item.setdefault(row['knowledge_id'], []).append(row)
    return [top[key] for key in sorted(top) if status_at(by_item.get(key, []), cutoff) != 'archived']


def ref_entries(rows: list[dict[str, Any]], versions: list[dict[str, Any]]) -> dict[Any, list[dict[str, Any]]]:
    """Group citation facts with their cited title and latest version; callers supply the stored facts."""
    top: dict[int, int] = {}
    title: dict[tuple[int, int], str] = {}
    for v in versions:
        top[v['knowledge_id']] = max(top.get(v['knowledge_id'], 0), v['version'])
        title[(v['knowledge_id'], v['version'])] = v['title']
    grouped: dict[Any, dict[tuple[int, int], list[str]]] = {}
    for row in rows:
        fields = grouped.setdefault(row['group'], {}).setdefault((row['knowledge_id'], row['version']), [])
        if row['field'] not in fields:
            fields.append(row['field'])
    return {group: [{'ref': ref(kid, version), 'title': title.get((kid, version), ''),
                     'fields': [f for f in CITATION_FIELDS if f in fields], 'current_version': top.get(kid, version)}
                    for (kid, version), fields in sorted(cited.items())]
            for group, cited in grouped.items()}


def refs_for_plans(rows: list[dict[str, Any]], versions: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """``plan id -> cited knowledge`` from citation and version facts, without database access."""
    return ref_entries([{**row, 'group': str(row['plan_id'])} for row in rows], versions)


def _first_calls(calls: list[dict]) -> dict[str, int]:
    """``know:`` ref -> seq of the first successful ``get_knowledge`` call that returned it."""
    first: dict[str, int] = {}
    for c in calls:
        if c['tool'] != 'get_knowledge' or 'error' in c['result']:
            continue
        data = c['result'].get('data')
        for entry in (data.get('entries') if isinstance(data, dict) else None) or []:
            if isinstance(entry, dict) and isinstance(entry.get('ref'), str):
                first.setdefault(entry['ref'], c['seq'])
    return first


def _field_refs(output: dict | None, counter: dict | None, mode: str) -> list[tuple[str, list]]:
    """(field, refs) pairs of checked output that may carry a ``know:`` ref (spec §6.2)."""
    pairs: list[tuple[str, list]] = []
    if mode == 'review':
        pairs.append(('review', (output or {}).get('refs') or []))
        return pairs
    for field, key in (('thesis', 'thesis'), ('invalidation', 'invalidation'), ('changes', 'changes')):
        for item in (output or {}).get(key) or []:
            if isinstance(item, dict):
                pairs.append((field, item.get('refs') or []))
    for field, key in (('counter', 'counter'), ('risks', 'risks')):
        for item in (counter or {}).get(key) or []:
            if isinstance(item, dict):
                pairs.append((field, item.get('refs') or []))
    return pairs


def knowledge_citations(*, output: dict | None, counter: dict | None, mode: str, calls: list[dict]) -> list[dict]:
    """Citations backed by the first successful tool call; one row per knowledge id, version and field."""
    first = _first_calls(calls)
    rows: list[dict] = []
    seen: set[tuple[int, int, str]] = set()
    for field, item_refs in _field_refs(output, counter, mode):
        for item_ref in item_refs:
            if not isinstance(item_ref, str) or item_ref not in first:
                continue
            try:
                knowledge_id, version = parse_knowledge_ref(item_ref)
            except ValueError:
                continue
            if version is None or (knowledge_id, version, field) in seen:
                continue
            seen.add((knowledge_id, version, field))
            rows.append({'knowledge_id': knowledge_id, 'version': version, 'field': field,
                         'call_seq': first[item_ref]})
    return rows


def parse_ref(text: str) -> tuple[str, int | None]:
    """'concept:plan.stop@2' -> ('plan.stop', 2); 'plan.stop' -> ('plan.stop', None); ValueError otherwise."""
    match = REF_RE.match(text or '')
    if not match:
        raise ValueError(f'概念引用格式不对：{text!r}（应为 concept:<id>[@<version>]）')
    return match.group(1), int(match.group(2)) if match.group(2) else None


def lookup(cat: Catalog, ref: str) -> tuple[Concept, dict | None]:
    concept_id, requested = parse_ref(ref)
    concept = cat.concepts.get(concept_id)
    if concept is None:
        raise LookupError(f'没有这张概念卡：{concept_id}')
    stale = None
    if requested is not None and requested != concept.version:
        stale = {'requested': requested, 'current': concept.version,
                 'note': STALE_NOTE.format(requested=requested, current=concept.version)}
    return concept, stale


# --------------------------------------------------------------------------------------------- views


def _ordered(cat: Catalog) -> list[Concept]:
    order = {group_id: i for i, (group_id, _) in enumerate(GROUPS)}
    return sorted(cat.concepts.values(), key=lambda c: (order[c.group], c.id))


def _brief(cat: Catalog, ids) -> list[dict[str, str]]:
    return [{'id': i, 'title': cat.concepts[i].title} for i in ids if i in cat.concepts]


def _card_fields(concept: Concept) -> dict[str, Any]:
    return {'id': concept.id, 'ref': concept.ref, 'title': concept.title, 'group': concept.group,
            'version': concept.version, 'applies_to': list(concept.applies_to), 'summary': concept.summary,
            'related': list(concept.related)}


def index_view(cat: Catalog) -> dict[str, Any]:
    concepts = _ordered(cat)
    return {
        'knowledge_version': KNOWLEDGE_VERSION,
        'groups': [{'id': group_id, 'title': title, 'count': sum(1 for c in concepts if c.group == group_id)}
                   for group_id, title in GROUPS],
        'concepts': [_card_fields(c) for c in concepts],
    }


def card_view(cat: Catalog, ref: str) -> dict[str, Any]:
    concept, stale = lookup(cat, ref)
    return {**_card_fields(concept), 'html': concept.html,
            'related_cards': _brief(cat, concept.related),
            'backlinks': _brief(cat, cat.backlinks.get(concept.id, ())),
            'stale': stale}


def principles_view(cat: Catalog) -> dict[str, Any]:
    if cat.principles is None:
        raise LookupError('原理页不存在')
    p = cat.principles
    return {'title': p.title, 'version': p.version,
            'sections': [{'heading': s.heading, 'html': s.html, 'stage': s.stage, 'concepts': list(s.concepts)}
                         for s in p.sections]}


def agent_text(cat: Catalog, concept_id: str | None) -> dict[str, Any]:
    """The `get_concept` tool result body (spec §10)."""
    if concept_id is None:
        return {'knowledge_version': KNOWLEDGE_VERSION, 'point_in_time': False,
                'concepts': [{'id': c.id, 'title': c.title, 'group': c.group} for c in _ordered(cat)]}
    try:
        concept, _ = lookup(cat, concept_id)
    except (ValueError, LookupError) as exc:
        raise LookupError(f'未知概念 {concept_id}，不给 id 可取全部列表') from exc
    return {'concept_ref': concept.ref, 'id': concept.id, 'title': concept.title, 'group': concept.group,
            'version': concept.version, 'applies_to': list(concept.applies_to), 'summary': concept.summary,
            'body': concept.body_text, 'content_sha256': concept.content_sha256,
            'knowledge_version': KNOWLEDGE_VERSION, 'point_in_time': False, 'note': AGENT_NOTE}


# --------------------------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.core.knowledge',
                                     description='概念卡与原理页：校验与查看')
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('check', help='校验整个目录')
    check.add_argument('--root', type=Path, default=None)
    show = sub.add_parser('show', help='打印一张卡（frontmatter 与给 Agent 的纯文本）')
    show.add_argument('ref')
    show.add_argument('--root', type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        cat = load(args.root or DEFAULT_ROOT)
    except KnowledgeError as exc:
        for problem in exc.problems:
            print(problem)
        return 1
    if args.command == 'check':
        sections = len(cat.principles.sections) if cat.principles else 0
        print(f'概念卡 {len(cat.concepts)} 张、原理页 {sections} 节，通过')
        return 0
    try:
        concept, stale = lookup(cat, args.ref)
    except (LookupError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(yaml.safe_dump({**_card_fields(concept), 'sources': list(concept.sources),
                          'content_sha256': concept.content_sha256}, allow_unicode=True, sort_keys=False).rstrip())
    if stale:
        print(stale['note'])
    print('---')
    print(concept.body_text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
