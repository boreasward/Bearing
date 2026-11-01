"""My knowledge (v2.7 spec §5, ``my-knowledge-1``): pure field validation and change detection.

Reference parsing and cutoff visibility live in ``core.knowledge``.
"""
from __future__ import annotations

import re
from typing import Any

from investment_assistant.data.identity import stock_id
from investment_assistant.core.knowledge import ID_BODY

MY_KNOWLEDGE_VERSION = 'my-knowledge-1'
LINKS_MAX = 10                     # concepts + tickers attached to one version
LIMITS = {'title': 60, 'body': 4000, 'scope': 500, 'counter_examples': 1000}
FIELDS = ('title', 'body', 'scope', 'counter_examples', 'concepts', 'tickers')

_CONCEPT_RE = re.compile(rf'^(?:concept:)?({ID_BODY})(?:@[1-9][0-9]*)?$')


def _text(payload: dict[str, Any], field: str, *, required: bool) -> str:
    value = payload.get(field)
    if value is None and not required:
        value = ''
    if not isinstance(value, str):
        raise ValueError(f'{field} 必须是文字')
    value = value.strip()
    if required and not value:
        raise ValueError(f'{field} 不能为空')
    if len(value) > LIMITS[field]:
        raise ValueError(f'{field} 最多 {LIMITS[field]} 字')
    return value


def _list(payload: dict[str, Any], field: str) -> list[Any]:
    value = payload.get(field)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f'{field} 必须是列表')
    return value


def validate(payload: dict[str, Any], *, concept_versions: dict[str, int] | None,
             require_counter: bool) -> dict[str, Any]:
    """Normalised fields of one knowledge version.

    ``concept_versions`` maps concept id -> current card version; each ``concepts`` entry (``plan.stop``,
    ``concept:plan.stop`` or a pinned ``concept:plan.stop@2``) comes back as ``concept:<id>@<current>``.
    ``None`` means the card catalog could not be loaded: asking to attach a concept then raises
    ``LookupError``; tickers alone never need it. ``require_counter`` is set for agent-drafted lessons."""
    out: dict[str, Any] = {
        'title': _text(payload, 'title', required=True),
        'body': _text(payload, 'body', required=True),
        'scope': _text(payload, 'scope', required=True),
        'counter_examples': _text(payload, 'counter_examples', required=require_counter),
    }
    concept_ids: list[str] = []
    for item in _list(payload, 'concepts'):
        match = _CONCEPT_RE.fullmatch(item.strip()) if isinstance(item, str) else None
        if match is None:
            raise ValueError(f'不是概念卡 id：{item!r}')
        if match.group(1) not in concept_ids:
            concept_ids.append(match.group(1))
    tickers: list[str] = []
    for item in _list(payload, 'tickers'):
        try:
            stock_id(item)
        except (ValueError, AttributeError):
            raise ValueError(f'不是有效的美股代码：{item!r}') from None
        ticker = item.strip().upper()
        if ticker not in tickers:
            tickers.append(ticker)
    if len(concept_ids) + len(tickers) > LINKS_MAX:
        raise ValueError(f'挂靠最多 {LINKS_MAX} 处')
    if not concept_ids and not tickers:
        raise ValueError('至少挂靠一张概念卡或一个标的')
    if concept_ids and concept_versions is None:
        raise LookupError('概念卡不可用')
    for concept_id in concept_ids:
        if concept_id not in (concept_versions or {}):
            raise ValueError(f'概念卡不存在：{concept_id}')
    out['concepts'] = [f'concept:{concept_id}@{concept_versions[concept_id]}' for concept_id in concept_ids]
    out['tickers'] = tickers
    return out


def changed_fields(prev: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Which of the version fields differ between two versions, in field order."""
    return [field for field in FIELDS if prev.get(field) != new.get(field)]
