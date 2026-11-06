"""v3 candidate ranking: the three factor tiers and RS decide the order, never filter (spec §5.4).

Rows come from screen-1 results; `setup` from factors.setup_followthrough and `industry` from
factors.industry_strength. The reason line is verbatim Chinese so the draft, the stored
candidate rows and the tool surface all show one spelling.
"""
from __future__ import annotations

from typing import Any

from investment_assistant.core.texts import RULE_TEXT

TIER_ORDER = {'favorable': 0, 'neutral': 1, 'insufficient': 2, 'unfavorable': 3}
TIER_TEXT = {'favorable': '有利', 'neutral': '中性', 'unfavorable': '不利', 'insufficient': '样本不足'}


def _best_setup(trigger_rules: list[str], setup: dict[str, dict]) -> dict | None:
    """The triggered rule's statistics with the better tier; same tier -> the larger sample."""
    stats = [setup[rule] for rule in trigger_rules if rule in setup]
    if not stats:
        return None
    return max(stats, key=lambda stat: (-TIER_ORDER[stat['tier']], stat['n']))


def _setup_text(stat: dict | None) -> str:
    if stat is None or stat['n'] == 0:
        return '形态近期样本不足（0 次）'
    return (f"形态近期{TIER_TEXT[stat['tier']]}（{stat['n']} 次 {stat['wins']} 涨，"
            f"{stat['mean_return']:+.1%}）")


def rank_candidates(results: list[dict], *, setup: dict[str, dict], industry: dict[str, dict],
                    groups: dict[str, str | None]) -> list[dict]:
    """Selected screen rows in draft order: trigger group first, then by setup tier, industry
    tier and RS; ties fall through to the ticker. `detail` keeps the raw stats behind the row."""
    rows: list[dict[str, Any]] = []
    for result in results:
        if not result.get('selected'):
            continue
        group = result['group']
        trigger_rules = list(result.get('trigger_rules') or [])
        stat = _best_setup(trigger_rules, setup) if group == 'trigger' else None
        setup_tier = stat['tier'] if stat is not None else 'insufficient'
        name = groups.get(result['ticker'])
        ind_stat = industry.get(name) if name is not None else None
        industry_tier = ind_stat['tier'] if ind_stat is not None else 'insufficient'
        rs = result.get('rs')
        industry_text = f'{name} {TIER_TEXT[industry_tier]}' if name is not None else '未归类 样本不足'
        rs_text = f'RS {rs:.2f}' if rs is not None else 'RS —'
        if group == 'trigger':
            # 全角括号后不加空格（spec §5.4 例行逐字：…（19 次 13 涨，+2.7%）· 医药化工 有利 · RS 0.35）
            reason = f"{'、'.join(RULE_TEXT[rule] for rule in trigger_rules)} · " \
                f'{_setup_text(stat)}· {industry_text} · {rs_text}'
        else:
            reason = f'形态待触发 · {industry_text} · {rs_text}'
        rows.append({'ticker': result['ticker'], 'rank': 0, 'screen_group': group,
                     'trigger_rules': trigger_rules, 'rs': rs, 'atr_pct': result.get('atr_pct'),
                     'setup_tier': setup_tier, 'industry': name, 'industry_tier': industry_tier,
                     'reason': reason,
                     'detail': {'setup': stat, 'industry': ind_stat, 'screen_rank': result.get('rank')}})
    rows.sort(key=lambda row: (0 if row['screen_group'] == 'trigger' else 1,
                               TIER_ORDER[row['setup_tier']], TIER_ORDER[row['industry_tier']],
                               -(row['rs'] or 0.0), row['ticker']))
    for rank, row in enumerate(rows, start=1):
        row['rank'] = rank
    return rows
