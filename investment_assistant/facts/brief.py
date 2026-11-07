"""v3.1 brief rendering (spec §11): the ten-section Markdown for draft and final, the
error brief and the Discord summary lines. String assembly only — no database, no config
reads; everything comes in through the `view` dict that facts.view.brief_view produces.

view keys consumed here:
- session, as_of (北京时间), top_n, first_run
- today_actions: [{'ticker', 'text'}] in §7.4 order; empty -> section 0 absent
- summary: str | None — the agent's one-liner (final only)
- changes: {'new_plans': [str], 'ended_plans': [str], 'holding_changes': [str],
            'new_count': int, 'dropped_count': int} — lines are pre-assembled sentences
- market: the market-view dict the old draft rendered
- holdings: holdings_view rows, plus optional 'earnings_note' and
  'review': {'verdict': 'holds'|'broken'|'unknown', 'reasons': str} (final only)
- pending_plans: [{'ticker', 'day', 'entry_low', 'entry_high', 'stop', 'target1', 'shares',
                   'valid_until', 'missing_bar'}] — one line each (当天没变的不展开)；missing_bar
  时行尾加「缺日线，未复查」（spec §6.3 末段）
- candidates: [{'ticker', 'rank', 'reason', 'streak', 'new', 'verdict', 'skip_reason', 'held'}]
- dropped: [{'ticker', 'reasons': [code|str]}]
- account: {'cash', 'equity', 'unrealized_pnl', 'realized_pnl'} | None
- coverage: the facts.coverage dict; unresolved: {'missing_bars': [ticker], 'agenda': [str]}
- footer: {'runtime', 'provider', 'model', 'usage': {'input','output'} | None,
           'elapsed_seconds', 'note'} — model empty -> 「未选定模型，二次确认未做」
"""

from __future__ import annotations

from datetime import date, datetime

from investment_assistant.core.texts import REGIME_LABEL_TEXT
from investment_assistant.facts.coverage import coverage_lines
from investment_assistant.facts.ranking import TIER_TEXT

STATE_TEXT = {"strong": "强", "weak": "弱", "mixed": "混合"}
REVIEW_VERDICT_TEXT = {"holds": "成立", "broken": "不成立", "unknown": "无法判断"}
CANDIDATE_VERDICT_TEXT = {"confirm": " → 确认", "veto": " → 否决", "unknown": " → 无法判断"}
DROPPED_REASON_TEXT = {
    "rank_cut": "名次被截断",
    "illiquid": "流动性不足",
    "no_setup": "形态不满足",
    "earnings_soon": "财报临近",
}


def _share(breadth: dict, pct_key: str, above_key: str, eligible_key: str) -> str:
    pct = breadth.get(pct_key)
    if pct is None:
        return "样本不足"
    return f"{pct:.0%}（{breadth.get(above_key, 0)}/{breadth.get(eligible_key, 0)}）"


def _etf_table_rows(table: list[dict]) -> list[str]:
    if not table:
        return []
    shown = table if len(table) <= 6 else table[:3] + table[-3:]
    rows = ["| ETF | 20 日 | RS20 | RS60 | 状态 |", "| --- | ---: | ---: | ---: | --- |"]
    for entry in shown:
        ret20 = f"{entry['ret20']:+.1%}" if entry.get("ret20") is not None else "—"
        rs20 = f"{entry['rs20']:+.2f}" if entry.get("rs20") is not None else "—"
        rs60 = f"{entry['rs60']:+.2f}" if entry.get("rs60") is not None else "—"
        rows.append(f"| {entry['etf']} | {ret20} | {rs20} | {rs60} | {STATE_TEXT.get(entry.get('state') or '', '—')} |")
    return rows


def _market_lines(market: dict) -> list[str]:
    label = market.get("label") or "未知"
    tier = market.get("regime_tier") or "insufficient"
    lines = [f"- 区制：{REGIME_LABEL_TEXT.get(label, label)}（{TIER_TEXT.get(tier, tier)}）"]
    breadth = market.get("breadth") or {}
    lines.append(
        f"- 广度：站上 50 日线 {_share(breadth, 'pct_50', 'above_50', 'eligible_50')}；"
        f"站上 200 日线 {_share(breadth, 'pct_200', 'above_200', 'eligible_200')}"
    )
    lines.append(f"- 52 周新高 {breadth.get('new_highs', 0)} 只、新低 {breadth.get('new_lows', 0)} 只")
    spy_ret20 = market.get("spy_ret20")
    lines.append(f"- SPY 20 日 {spy_ret20:+.1%}" if spy_ret20 is not None else "- SPY 20 日样本不足")
    lines.extend(_etf_table_rows(market.get("etf_table") or []))
    return lines


def _change_lines(view: dict) -> list[str]:
    if view.get("first_run"):
        return ["首次运行"]
    changes = view.get("changes") or {}
    lines = [
        *(changes.get("new_plans") or []),
        *(changes.get("ended_plans") or []),
        *(changes.get("holding_changes") or []),
    ]
    new_count = changes.get("new_count") or 0
    dropped_count = changes.get("dropped_count") or 0
    if not lines and not new_count and not dropped_count:
        return ["没有变化"]
    if new_count or dropped_count:
        lines.append(f"名单新上榜 {new_count} 只、掉出 {dropped_count} 只")
    return lines


def _holding_lines(holdings: list[dict], *, final: bool) -> list[str]:
    if not holdings:
        return ["没有持仓记录。"]
    lines: list[str] = []
    for row in holdings:
        if row.get("error"):
            lines.append(f"- {row['ticker']}：持仓记录无法核算（{row['error']}）")
            continue
        parts: list[str] = []
        action = row.get("next_action") or {}
        if action.get("text"):
            parts.append(action["text"])
        if row.get("stop") is not None:
            parts.append(f"止损 {row['stop']}")
        pnl, pct = row.get("unrealized_pnl"), row.get("pnl_pct")
        if pnl is not None:
            parts.append(f"浮动盈亏 {pnl}（{pct:+.1%}）" if pct is not None else f"浮动盈亏 {pnl}")
        elif pct is not None:
            parts.append(f"盈亏 {pct:+.1%}")
        if row.get("held_sessions") is not None:
            parts.append(f"已持有 {row['held_sessions']} 个交易日")
        if row.get("earnings_note"):
            parts.append(row["earnings_note"])
        review = row.get("review")
        if final and review:
            verdict = REVIEW_VERDICT_TEXT.get(review.get("verdict"), review.get("verdict"))
            parts.append(f"复查：{verdict}——{review['reasons']}" if review.get("reasons") else f"复查：{verdict}")
        if not parts and row.get("quantity") is not None:
            parts.append(f"{row['quantity']} 股，均价 {row['avg_cost']}")
        lines.append(f"- {row['ticker']}：" + "，".join(parts) + "。")
    return lines


def _pending_plan_lines(plans: list[dict]) -> list[str]:
    if not plans:
        return ["今天没有待入场计划。"]
    return [
        f"- {row['ticker']}（第 {row.get('day', 1)} 天）："
        f"入场 {row['entry_low']}–{row['entry_high']}，止损 {row['stop']}，"
        f"目标 {row['target1']}，{row['shares']} 股，有效至 {row['valid_until']:%Y-%m-%d}"
        + ("，缺日线，未复查" if row.get("missing_bar") else "")
        for row in plans
    ]


def _candidate_lines(candidates: list[dict], *, top_n: int, final: bool) -> list[str]:
    if not candidates:
        return ["今天没有入选的股票。"]
    lines: list[str] = []
    for row in candidates:
        line = f"{row['rank']}. {row['ticker']} · {row['reason']}"
        if row.get("streak") is not None:
            line += " · " + ("新上榜" if row.get("new") else f"连续第 {row['streak']} 天")
        if row["rank"] <= top_n:
            if row.get("held"):
                line += " · 已持有"
            elif final and row.get("verdict") in CANDIDATE_VERDICT_TEXT:
                line += CANDIDATE_VERDICT_TEXT[row["verdict"]]
            elif row.get("skip_reason"):
                line += f" · {row['skip_reason']}"
            elif not final:
                line += " → 进入二次确认"
        lines.append(line)
    return lines


def _dropped_lines(dropped: list[dict]) -> list[str]:
    lines: list[str] = []
    for row in dropped:
        reasons = [str(reason) for reason in row.get("reasons") or []]
        text = "、".join(DROPPED_REASON_TEXT.get(reason, reason) for reason in reasons)
        lines.append(f"- 掉出：{row['ticker']}" + (f"（{text}）" if text else ""))
    return lines


def _account_lines(account: dict | None) -> list[str]:
    if not account:
        return ["还没有账户记录。"]

    def show(value) -> str:
        return "—" if value is None else f"{value}"

    return [
        (
            f"- 现金 {show(account.get('cash'))}，总资产 {show(account.get('equity'))}，"
            f"浮动盈亏 {show(account.get('unrealized_pnl'))}，已实现盈亏 {show(account.get('realized_pnl'))}"
        )
    ]


def _unresolved_lines(view: dict) -> list[str]:
    lines = coverage_lines(view.get("coverage") or {"families": {}, "missing_bars": []}) or [
        "本次没有采集，用的是库里已有的数据。"
    ]
    unresolved = view.get("unresolved") or {}
    # §8 有意保持通用数据质量清单的「缺日线」：待入场计划的逐字提示「缺日线，未复查」
    # 在第 5 段（终审 F1 裁定），两处各司其职。
    for ticker in unresolved.get("missing_bars") or []:
        lines.append(f"- {ticker}：缺日线")
    for item in unresolved.get("agenda") or []:
        lines.append(f"- 未完成：{item}")
    return lines


def _footer_text(view: dict, *, final: bool) -> str:
    session, as_of = view["session"], view["as_of"]
    text = f"数据截至 {session:%Y-%m-%d} 收盘（采集于北京时间 {as_of:%Y-%m-%d %H:%M}）。规则未经验证，不构成投资建议。"
    if not final:
        return text
    footer = view.get("footer") or {}
    if not footer.get("model"):
        return text + " 未选定模型，二次确认未做。"
    info = [
        f"运行时 {footer.get('runtime') or '—'}",
        f"提供方 {footer.get('provider') or '—'}",
        f"模型 {footer['model']}",
    ]
    usage = footer.get("usage") or {}
    if usage:
        info.append(f"token 输入 {usage.get('input', 0)}、输出 {usage.get('output', 0)}")
    if footer.get("elapsed_seconds") is not None:
        info.append(f"耗时 {footer['elapsed_seconds']} 秒")
    text += " " + "；".join(info) + "。"
    if footer.get("note"):
        text += footer["note"]
    return text


def render_brief(view: dict, *, final: bool) -> str:
    """The ten-section brief of spec §11. `final=False` is the draft: the agent parts are
    gone (section 1, the §4 review conclusions, the §6 verdicts) and the title is followed
    by 「二次确认未完成」."""
    head = [f"# 简报 · {view['session']:%Y-%m-%d}"]
    if not final:
        head.append("二次确认未完成")
    sections: list[list[str]] = [head]
    actions = view.get("today_actions") or []
    if actions:
        sections.append(["## 今日操作", *[f"- {row['ticker']}：{row['text']}" for row in actions]])
    if final and view.get("summary"):
        sections.append(["## 一句话", view["summary"]])
    sections.append(["## 和昨天比变了什么", *_change_lines(view)])
    sections.append(["## 市场环境", *_market_lines(view.get("market") or {})])
    sections.append(["## 持仓", *_holding_lines(view.get("holdings") or [], final=final)])
    sections.append(["## 待入场计划", *_pending_plan_lines(view.get("pending_plans") or [])])
    sections.append(
        [
            "## 适合交易什么（规则名单）",
            *_candidate_lines(view.get("candidates") or [], top_n=view.get("top_n", 5), final=final),
            *_dropped_lines(view.get("dropped") or []),
        ]
    )
    sections.append(["## 账户", *_account_lines(view.get("account"))])
    sections.append(["## 今天下不了结论的", *_unresolved_lines(view)])
    sections.append([_footer_text(view, final=final)])
    return "\n\n".join("\n".join(section) for section in sections) + "\n"


def render_error_brief(*, session: date, as_of: datetime, error: str, has_holdings: bool) -> str:
    """The reason-only brief of spec §5: with holdings the first line warns that stops
    could not be checked today."""
    lead = f"今天无法检查止损：{error}" if has_holdings else f"今天没有简报：{error}"
    footer = (
        f"数据截至 {session:%Y-%m-%d} 收盘（采集于北京时间 {as_of:%Y-%m-%d %H:%M}）。规则未经验证，不构成投资建议。"
    )
    return f"# 简报 · {session:%Y-%m-%d}\n\n{lead}\n\n{footer}\n"


def discord_lines(view: dict, *, brief_path: str) -> list[str]:
    """The Discord summary (spec §11): today's actions first, the one-liner, one line per
    holding's next action, one line each for new and ended plans, then the brief file."""
    lines: list[str] = []
    for row in view.get("today_actions") or []:
        lines.append(f"{row['ticker']}：{row['text']}")
    if view.get("summary"):
        lines.append(view["summary"])
    for row in view.get("holdings") or []:
        action = row.get("next_action")
        if action and action.get("text"):
            lines.append(f"{row['ticker']}：{action['text']}")
    changes = view.get("changes") or {}
    if changes.get("new_plans"):
        lines.append("新计划：" + "；".join(changes["new_plans"]))
    if changes.get("ended_plans"):
        lines.append("结束的计划：" + "；".join(changes["ended_plans"]))
    lines.append(f"简报文件：{brief_path}")
    return lines
