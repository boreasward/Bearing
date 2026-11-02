"""Structural gate for implementation briefs; no model or persistence calls."""
from __future__ import annotations

import re


_ATX = re.compile(r'^ {0,3}(#{1,6})\s+(.+?)\s*$')
_FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')
_ACCEPTANCE = re.compile(r'^(?:\d+[.、)]\s*)?验收(?:$|[\s：:（(]|标准|条件|步骤|清单|方法)')


def _has_acceptance(text: str) -> bool:
    # Hidden comments cannot satisfy a visible acceptance section. Keep line
    # boundaries so removing a multiline comment cannot join heading fragments.
    text = re.sub(r'<!--.*?(?:-->|\Z)', lambda match: '\n' * match[0].count('\n'), text, flags=re.S)
    lines = text.splitlines()
    visible = []
    fence = None
    for line in lines:
        match = _FENCE.match(line)
        if fence is not None:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = None
            visible.append('')
        elif match:
            fence = match[1]
            visible.append('')
        else:
            visible.append(line if not line.startswith(('    ', '\t')) else '')

    headings, markers = [], set()
    for i, line in enumerate(visible):
        match = _ATX.match(line)
        if match:
            title = re.sub(r'\s+#+\s*$', '', match[2])
            headings.append((i, len(match[1]), title))
            markers.add(i)
        elif i and visible[i - 1].strip() and re.fullmatch(r' {0,3}(?:=+|-+)\s*', line):
            headings.append((i - 1, 1 if line.strip()[0] == '=' else 2, visible[i - 1].strip()))
            markers.update((i - 1, i))
    for index, (start, level, title) in enumerate(headings):
        if not _ACCEPTANCE.match(title):
            continue
        end = next((pos for pos, depth, _ in headings[index + 1:] if depth <= level), len(visible))
        if any(line.strip() for i, line in enumerate(visible[start + 1:end], start + 1) if i not in markers):
            return True
    return False


def check_brief(text) -> dict:
    """Return {body: original Markdown}; invalid output raises ValueError(code).

    Codes: brief_empty, brief_length, brief_acceptance_missing. The 20,000
    character limit measures the original string. A real nonempty ATX/Setext
    acceptance section is required; a mention or a fenced example is not one.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError('brief_empty')
    if len(text) > 20000:
        raise ValueError('brief_length')
    if not _has_acceptance(text):
        raise ValueError('brief_acceptance_missing')
    return {'body': text}
