"""v3.1 brief rendering (spec §11): the ten-section Markdown for draft and final, the
error brief and the Discord summary lines. String assembly only — no database, no config
reads; everything comes in through the `view` dict that facts.view.brief_view produces.

view keys consumed here:
- session, as_of (北京时间), top_n, first_run
- today_actions: [{'ticker', 'text'}] in §7.4 order; empty -> section 0 absent
- summary: str | None — the agent's one-liner (final only)
- changes: {'new_plans': [str], 'ended_plans': [str], 'holding_changes': [str],
            'research': [str], 'new_count': int, 'dropped_count': int} — lines are pre-assembled sentences
- market: the market-view dict the old draft rendered
- holdings: holdings_view rows, plus optional 'earnings_note' and
  'review': {'verdict': 'holds'|'broken'|'unknown', 'reasons': str} (final only)
- pending_plans: [{'ticker', 'day', 'entry_low', 'entry_high', 'stop', 'target1', 'shares',
                   'valid_until', 'missing_bar'}] — one line each (当天没变的不展开)；missing_bar
  时行尾加「缺日线，未复查」（spec §6.3 末段）
- research: [{'ticker', 'day', 'thesis', 'condition_text', 'last_close', 'state', 'conclusion'}] —
  one line each in section 5a (spec §8)；state ∈ triggered/watching/missing_bar/no_condition/insufficient_history，
  conclusion 只进最终稿；空列表 -> 整段不出现
- candidates: [{'ticker', 'rank', 'reason', 'streak', 'new', 'verdict', 'skip_reason', 'held'}]
- dropped: [{'ticker', 'reasons': [code|str]}]
- account: {'cash', 'equity', 'unrealized_pnl', 'realized_pnl'} | None
- coverage: the facts.coverage dict; unresolved: {'missing_bars': [ticker], 'agenda': [str]}
- footer: {'runtime', 'provider', 'model', 'usage': {'input','output'} | None,
           'elapsed_seconds', 'note'} — model empty -> 「未选定模型，二次确认未做」
"""

from __future__ import annotations

import re
from datetime import date, datetime

from investment_assistant.core.texts import NO_EQUITY_TEXT, REGIME_LABEL_TEXT, money_value, price_text, quantity_value
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
RESEARCH_STATE_TEXT = {
    "triggered": "条件已到，待复看",
    "watching": "观察中",
    "missing_bar": "缺日线，未复查",
    "no_condition": "尚无条件",
    "insufficient_history": "日线不足，未检查",
}
WEEKLY_EXIT_TEXT = {
    "initial_stop": "初始止损",
    "trailing_stop": "移动止损",
    "max_hold": "持有到期",
    "thesis_broken": "论点失效",
    "manual": "用户自行卖出",
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
    label_text, tier_text = REGIME_LABEL_TEXT.get(label, label), TIER_TEXT.get(tier, tier)
    # 中性区制的档位也是「中性」：同一个词不写两遍
    lines = [f"- 区制：{label_text}" if label_text == tier_text else f"- 区制：{label_text}（{tier_text}）"]
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
        *(changes.get("research") or []),
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
            parts.append(f"止损 {price_text(row['stop'])}")
        pnl, pct = money_value(row.get("unrealized_pnl")), row.get("pnl_pct")
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
            parts.append(f"{quantity_value(row['quantity'])} 股，均价 {price_text(row['avg_cost'])}")
        lines.append(f"- {row['ticker']}：" + "，".join(parts) + "。")
    return lines


def _shares_text(shares) -> str:
    return NO_EQUITY_TEXT if shares is None else f"{shares} 股"


def _pending_plan_lines(plans: list[dict]) -> list[str]:
    if not plans:
        return ["今天没有待入场计划。"]
    return [
        f"- {row['ticker']}（第 {row.get('day', 1)} 天）："
        f"入场 {price_text(row['entry_low'])}–{price_text(row['entry_high'])}，止损 {price_text(row['stop'])}，"
        f"目标 {price_text(row['target1'])}，{_shares_text(row['shares'])}，有效至 {row['valid_until']:%Y-%m-%d}"
        + ("，缺日线，未复查" if row.get("missing_bar") else "")
        for row in plans
    ]


def _research_lines(research: list[dict], *, final: bool) -> list[str]:
    """第 5a 段「研究」每行（spec §8）：论点截到 40 字，条件缺省「尚无条件」；
    结论一句只进最终稿。"""
    lines = []
    for row in research:
        line = (
            f"- {row['ticker']}（第 {row.get('day', 1)} 天）：{(row.get('thesis') or '')[:40]}；"
            f"条件：{row.get('condition_text') or '尚无条件'}；最新收盘 {price_text(row.get('last_close'))}；"
            f"{RESEARCH_STATE_TEXT.get(row.get('state') or '', row.get('state') or '—')}"
        )
        if final and row.get("conclusion"):
            line += f"；结论：{row['conclusion']}"
        lines.append(line)
    return lines


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
        return text + "金额单位美元。"
    footer = view.get("footer") or {}
    if not footer.get("model"):
        return text + " 未选定模型，二次确认未做。金额单位美元。"
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
    return text + "金额单位美元。"


SECTION_TITLES: tuple[tuple[str, str], ...] = (
    ("actions", "今日操作"),
    ("summary", "一句话"),
    ("changes", "和昨天比变了什么"),
    ("market", "市场环境"),
    ("holdings", "持仓"),
    ("pending", "待入场计划"),
    ("research", "研究"),
    ("candidates", "适合交易什么（规则名单）"),
    ("account", "账户"),
    ("unresolved", "今天下不了结论的"),
)
_SECTION_KEYS_BY_TITLE = {title: key for key, title in SECTION_TITLES}


def _heading(key: str) -> str:
    title = dict(SECTION_TITLES)[key]
    return f"## {title}"


def split_brief(markdown: str) -> dict:
    """Split a rendered brief into its known sections without discarding unknown text."""
    text = markdown.replace("\r\n", "\n")
    if text.startswith("# "):
        _, _, text = text.partition("\n")

    text = text.rstrip()
    paragraph_parts = re.split(r"(\n[ \t]*\n)", text) if text else []
    footer = None
    if paragraph_parts and paragraph_parts[-1].startswith("数据截至"):
        footer = paragraph_parts.pop()
        if paragraph_parts:
            paragraph_parts.pop()
        text = "".join(paragraph_parts)

    lead_lines: list[str] = []
    sections: list[dict] = []
    seen: set[str] = set()
    current_key: str | None = None
    current_lines: list[str] = []

    def save_current() -> None:
        if current_key is not None:
            sections.append(
                {"key": current_key, "title": dict(SECTION_TITLES)[current_key], "markdown": "\n".join(current_lines).strip()}
            )

    for line in text.split("\n"):
        title = line.rstrip()
        key = _SECTION_KEYS_BY_TITLE.get(title[3:]) if title.startswith("## ") else None
        if key is not None and key not in seen:
            save_current()
            current_key = key
            current_lines = []
            seen.add(key)
        elif current_key is None:
            lead_lines.append(line)
        else:
            current_lines.append(line)
    save_current()

    lead = "\n".join(line for line in lead_lines if line.strip() != "二次确认未完成").strip() or None
    return {"lead": lead, "sections": sections, "footer": footer}


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
        sections.append([_heading("actions"), *[f"- {row['ticker']}：{row['text']}" for row in actions]])
    if final and view.get("summary"):
        sections.append([_heading("summary"), view["summary"]])
    sections.append([_heading("changes"), *_change_lines(view)])
    sections.append([_heading("market"), *_market_lines(view.get("market") or {})])
    sections.append([_heading("holdings"), *_holding_lines(view.get("holdings") or [], final=final)])
    sections.append([_heading("pending"), *_pending_plan_lines(view.get("pending_plans") or [])])
    research = view.get("research") or []
    if research:
        sections.append([_heading("research"), *_research_lines(research, final=final)])
    sections.append(
        [
            _heading("candidates"),
            *_candidate_lines(view.get("candidates") or [], top_n=view.get("top_n", 5), final=final),
            *_dropped_lines(view.get("dropped") or []),
        ]
    )
    sections.append([_heading("account"), *_account_lines(view.get("account"))])
    sections.append([_heading("unresolved"), *_unresolved_lines(view)])
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
    """The Discord summary (spec §11)：今日操作、一句话、持仓操作、新计划、条件已到的研究各一行、
    结束的计划，最后是简报文件。"""
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
    for row in view.get("research") or []:
        if row.get("state") == "triggered":
            lines.append(f"研究 {row['ticker']}：条件已到（{row['condition_text']}）")
    if changes.get("ended_plans"):
        lines.append("结束的计划：" + "；".join(changes["ended_plans"]))
    lines.append(f"简报文件：{brief_path}")
    return lines


def _hypothetical_sentence(simulation: dict | None) -> str:
    """没买计划的假设结果一句（spec §5.3 / §5.4）：未成交写原因，已离场写日期价格原因与 R，
    进行中按最后一根收盘写浮动 R。"""
    if not simulation:
        return "如果照做：—"
    if simulation.get("status") == "not_filled":
        reason = "收盘跌破止损" if simulation.get("reason") == "close_below_stop" else "价格未进入场区间"
        return f"如果照做：未成交，{reason}"
    if simulation.get("status") == "exited":
        why = WEEKLY_EXIT_TEXT.get(simulation.get("exit_reason") or "", simulation.get("exit_reason") or "—")
        return (
            f"如果照做：{simulation['exit_session']:%Y-%m-%d} 以 {price_text(simulation['exit_price'])} 离场"
            f"（{why}），R {simulation['r_multiple']}"
        )
    return (
        f"如果照做：进行中（{simulation['exit_session']:%Y-%m-%d} 收盘 {price_text(simulation['exit_price'])}，"
        f"R {simulation['r_multiple']}）"
    )


def render_weekly(weekly: dict, *, as_of: datetime) -> str:
    """周报（spec §5.4）：结束的计划、已平仓、没买的、账户、名单，页脚与简报同句加「金额单位美元」；
    没有记录的周写「本周没有结束的计划」。"""
    sections = [[f"# 周报 · {weekly['week_start']:%Y-%m-%d}–{weekly['week_end']:%Y-%m-%d}"]]
    ended = ["## 结束的计划"]
    if not weekly.get("ended"):
        ended.append("本周没有结束的计划。")
    else:
        ended.append(f"本周结束 {weekly['ended']} 份：照做 {weekly['followed']} 份、有偏差 {weekly['deviated']} 份。")
        counts = weekly.get("tag_counts") or {}
        if counts:
            ended.append("偏差标签：" + "、".join(f"{text} ×{count}" for text, count in counts.items()) + "。")
    sections.append(ended)
    closed = weekly.get("closed") or []
    if closed:
        rows = ["## 已平仓"]
        for row in closed:
            r = row.get("r_multiple")
            why = WEEKLY_EXIT_TEXT.get(row.get("exit_reason") or "", row.get("exit_reason") or "—")
            tags = "、".join(row.get("tags") or []) or "无"
            rows.append(f"- {row['ticker']}：R {r if r is not None else '—'}，{why}，标签：{tags}")
        rows.append(f"R 合计 {weekly.get('total_r', 0)}。")  # §5.4：只含已平仓，不含假设结果
        sections.append(rows)
    not_bought = weekly.get("not_bought") or []
    if not_bought:
        rows = ["## 没买的计划"]
        for row in not_bought:
            rows.append(
                f"- {row['ticker']}：{row.get('end_reason') or '—'}，{_hypothetical_sentence(row.get('hypothetical'))}。"
            )
        sections.append(rows)

    def show(value) -> str:
        return "—" if value is None else f"{money_value(value)}"

    account_line = (
        f"- 周初总资产 {show(weekly.get('equity_start'))}，周末总资产 {show(weekly.get('equity_end'))}，"
        f"本周已实现盈亏 {show(weekly.get('realized_pnl'))}。"
    )
    sections.append(["## 账户", account_line])
    listing = weekly.get("listing") or {}
    parts = []
    if listing.get("new"):
        parts.append("新上榜：" + "、".join(listing["new"]))
    if listing.get("dropped"):
        parts.append("掉出：" + "、".join(listing["dropped"]))
    if listing.get("most_relisted"):
        parts.append(f"再次上榜最多：{listing['most_relisted']}")
    sections.append(["## 名单", ("- " + "；".join(parts) + "。") if parts else "本周名单没有变化。"])
    footer = (
        f"数据截至 {weekly['week_end']:%Y-%m-%d} 收盘（采集于北京时间 {as_of:%Y-%m-%d %H:%M}）。"
        "规则未经验证，不构成投资建议。金额单位美元。"
    )
    sections.append([footer])
    return "\n\n".join("\n".join(section) for section in sections) + "\n"
