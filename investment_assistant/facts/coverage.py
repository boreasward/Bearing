"""v3 coverage: what each fetch family got, what is missing and why (pure).

Input rows are `data.tally.Tally.row()` dicts (`family, status, target_count, ok_count,
failures, detail`); same-family rows (the daily pass plus any backfills) add up, and missing
targets dedupe by target. Whole-call failures are the `'*'` sentinel, never a target.
"""
from __future__ import annotations

FAMILY_TEXT = {'bars': '日线', 'news': '资讯', 'sec_filings': 'SEC 申报', 'earnings': '财报日',
               'fred': '宏观', 'universe': '筛选范围', 'fundamentals': '基本面', 'sector': '行业归类'}
MISSING_SHOWN = 12       # missing targets listed per family line; the excess becomes 等 N 个


def coverage(rows: list[dict]) -> dict:
    """Merge the run's fetch rows into one per-family record plus the missing-bars tickers."""
    families: dict[str, dict] = {}
    for row in rows:
        family = families.setdefault(row['family'], {'total': 0, 'ok': 0, 'missing': [], 'skipped': None})
        family['total'] += row.get('target_count') or 0
        family['ok'] += row.get('ok_count') or 0
        if family['skipped'] is None and row.get('status') == 'skipped':
            family['skipped'] = (row.get('detail') or {}).get('reason')
        seen = {missing['target'] for missing in family['missing']}
        for failure in row.get('failures') or []:
            target = failure.get('target')
            if target in seen:
                continue
            family['missing'].append({'target': target, 'reason': failure.get('reason')})
            seen.add(target)
    for family in families.values():
        family['missing'].sort(key=lambda missing: missing['target'])
    return {'families': families,
            'missing_bars': [missing['target'] for missing in families.get('bars', {}).get('missing', [])
                             if missing['target'] != '*']}


def coverage_lines(cov: dict) -> list[str]:
    """One line per family, in FAMILY_TEXT order then family name; the empty coverage of a
    no-collect run is rendered by the draft, not here."""
    lines: list[str] = []
    ordered = [family for family in FAMILY_TEXT if family in cov['families']]
    ordered += sorted(family for family in cov['families'] if family not in FAMILY_TEXT)
    for family in ordered:
        info = cov['families'][family]
        name = FAMILY_TEXT.get(family, family)
        whole = next((missing for missing in info['missing'] if missing['target'] == '*'), None)
        if whole is not None:
            lines.append(f"{name}：整族未取到：{whole['reason']}")
            continue
        if info['skipped'] is not None:
            lines.append(f"{name}：未拉取：{info['skipped']}" if info['skipped']
                         else f'{name}：未拉取')
            continue
        line = f"{name}：{info['ok']}/{info['total']}"
        targets = [missing['target'] for missing in info['missing']]
        if targets:
            line += f"，缺 {'、'.join(targets[:MISSING_SHOWN])}"
            if len(targets) > MISSING_SHOWN:
                line += f' 等 {len(targets) - MISSING_SHOWN} 个'
        lines.append(line)
    return lines
