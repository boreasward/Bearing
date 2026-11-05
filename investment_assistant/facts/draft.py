"""v3 draft rendering: the rule shortlist as Markdown before any model runs (spec §5.3).

The draft is deterministic — it exists the moment the facts task finishes, model failures
cannot keep it away — and every number shown comes from the pure functions in this package.
"""
from __future__ import annotations

from datetime import date, datetime

from investment_assistant.core.texts import REGIME_LABEL_TEXT
from investment_assistant.facts.coverage import coverage_lines
from investment_assistant.facts.ranking import TIER_TEXT

STATE_TEXT = {'strong': '强', 'weak': '弱', 'mixed': '混合'}


def _share(breadth: dict, pct_key: str, above_key: str, eligible_key: str) -> str:
    pct = breadth.get(pct_key)
    if pct is None:
        return '样本不足'
    return f"{pct:.0%}（{breadth.get(above_key, 0)}/{breadth.get(eligible_key, 0)}）"


def _etf_table_rows(table: list[dict]) -> list[str]:
    if not table:
        return []
    shown = table if len(table) <= 6 else table[:3] + table[-3:]
    rows = ['| ETF | 20 日 | RS20 | RS60 | 状态 |', '| --- | ---: | ---: | ---: | --- |']
    for entry in shown:
        ret20 = f"{entry['ret20']:+.1%}" if entry.get('ret20') is not None else '—'
        rs20 = f"{entry['rs20']:+.2f}" if entry.get('rs20') is not None else '—'
        rs60 = f"{entry['rs60']:+.2f}" if entry.get('rs60') is not None else '—'
        rows.append(f"| {entry['etf']} | {ret20} | {rs20} | {rs60} |"
                    f" {STATE_TEXT.get(entry.get('state') or '', '—')} |")
    return rows


def _market_lines(market: dict) -> list[str]:
    label = market.get('label') or '未知'
    tier = market.get('regime_tier') or 'insufficient'
    lines = [f"- 区制：{REGIME_LABEL_TEXT.get(label, label)}（{TIER_TEXT.get(tier, tier)}）"]
    breadth = market.get('breadth') or {}
    lines.append(f"- 广度：站上 50 日线 {_share(breadth, 'pct_50', 'above_50', 'eligible_50')}；"
                 f"站上 200 日线 {_share(breadth, 'pct_200', 'above_200', 'eligible_200')}")
    lines.append(f"- 52 周新高 {breadth.get('new_highs', 0)} 只、新低 {breadth.get('new_lows', 0)} 只")
    spy_ret20 = market.get('spy_ret20')
    lines.append(f'- SPY 20 日 {spy_ret20:+.1%}' if spy_ret20 is not None else '- SPY 20 日样本不足')
    lines.extend(_etf_table_rows(market.get('etf_table') or []))
    return lines


def _candidate_lines(candidates: list[dict], top_n: int) -> list[str]:
    if not candidates:
        return ['今天没有入选的股票。']
    lines = []
    for row in candidates:
        line = f"{row['rank']}. {row['ticker']} · {row['reason']}"
        if row['rank'] <= top_n:
            line += ' → 进入二次确认'
        lines.append(line)
    return lines


def _holding_lines(holdings: list[dict]) -> list[str]:
    if not holdings:
        return ['没有持仓记录。']
    lines = []
    for row in holdings:
        if row.get('error'):
            lines.append(f"- {row['ticker']}：持仓记录无法核算（{row['error']}）")
            continue
        parts = [f"- {row['ticker']}：{row['quantity']} 股，均价 {row['avg_cost']}"]
        if row['last_close'] is None:
            parts.append('最新收盘缺失，盈亏待算')
        elif row['pnl_pct'] is not None:
            parts.append(f"最新收盘 {row['last_close']}（盈亏 {row['pnl_pct']:+.1%}）")
        else:
            parts.append(f"最新收盘 {row['last_close']}")
        if row['stop'] is None:
            parts.append('未设止损')
        elif row['to_stop_pct'] is not None:
            parts.append(f"距止损 {row['to_stop_pct']:.1%}")
        lines.append('，'.join(parts) + '。')
    return lines


def render_draft(*, session: date, as_of: datetime, market: dict, candidates: list[dict],
                 holdings: list[dict], coverage: dict, top_n: int, error: str | None = None) -> str:
    """The draft of the session: title, market, rule shortlist, holdings, open questions,
    footer. An `error` draft carries only the title, the reason and the footer."""
    title = f'# 草稿 · {session:%Y-%m-%d}'
    footer = (f'数据截至 {session:%Y-%m-%d} 收盘（采集于北京时间 {as_of:%Y-%m-%d %H:%M}）。'
              '规则未经验证，不构成投资建议。')
    if error is not None:
        return f'{title}\n\n今天没有草稿：{error}\n\n{footer}\n'
    cov_lines = coverage_lines(coverage) or ['本次没有采集，用的是库里已有的数据。']
    sections = [
        [title],
        ['## 市场环境', *_market_lines(market)],
        ['## 适合交易什么（规则名单）', *_candidate_lines(candidates, top_n)],
        ['## 持仓', *_holding_lines(holdings)],
        ['## 今天下不了结论的', *cov_lines],
        [footer],
    ]
    return '\n\n'.join('\n'.join(section) for section in sections) + '\n'
