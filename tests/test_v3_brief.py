"""v3.1 brief rendering (spec §11): ten sections, draft vs final, error brief, Discord lines."""

from datetime import date, datetime
from decimal import Decimal as D

from investment_assistant.facts.brief import discord_lines, render_brief, render_error_brief, render_weekly

SESSION = date(2026, 11, 4)
AS_OF = datetime(2026, 11, 5, 8, 30)
FOOTER_BASE = "数据截至 2026-11-04 收盘（采集于北京时间 2026-11-05 08:30）。规则未经验证，不构成投资建议。"


def view(**overrides):
    row = {
        "session": SESSION,
        "as_of": AS_OF,
        "top_n": 5,
        "first_run": False,
        "today_actions": [{"ticker": "AAPL", "text": "止损已触及，未见卖出记录"}],
        "summary": "一句话总结。",
        "changes": {
            "new_plans": ["新计划 MSFT：放量突破"],
            "ended_plans": ["结束 TSLA：5 个交易日未买入"],
            "holding_changes": ["AAPL 止损上调至 130"],
            "new_count": 2,
            "dropped_count": 1,
        },
        "market": {"label": "neutral", "regime_tier": "neutral", "breadth": {}, "spy_ret20": -0.013, "etf_table": []},
        "holdings": [
            {
                "ticker": "AAPL",
                "quantity": D(10),
                "avg_cost": D(105),
                "last_close": 120.0,
                "pnl_pct": 0.14,
                "stop": D(130),
                "to_stop_pct": 0.08,
                "plan_id": 8,
                "kind": "planned",
                "trailing": True,
                "unrealized_pnl": 150.0,
                "held_sessions": 3,
                "next_action": {"kind": "hold", "text": "继续持有", "stop": D(130), "reason": None},
                "stop_order_status": "confirmed",
                "review": {"verdict": "holds", "reasons": "论点仍成立"},
                "earnings_note": "财报日 12-02",
            }
        ],
        "pending_plans": [
            {
                "ticker": "MSFT",
                "day": 2,
                "entry_low": D(300),
                "entry_high": D(310),
                "stop": D(290),
                "target1": D(340),
                "shares": 20,
                "valid_until": date(2026, 11, 10),
            }
        ],
        "candidates": [
            {
                "ticker": "MSFT",
                "rank": 1,
                "reason": "理由 1",
                "streak": 2,
                "new": False,
                "verdict": "confirm",
                "skip_reason": None,
                "held": False,
            },
            {
                "ticker": "NVDA",
                "rank": 2,
                "reason": "理由 2",
                "streak": 1,
                "new": True,
                "verdict": None,
                "skip_reason": "名额已满",
                "held": False,
            },
            {
                "ticker": "AAPL",
                "rank": 3,
                "reason": "理由 3",
                "streak": 5,
                "new": False,
                "verdict": None,
                "skip_reason": None,
                "held": True,
            },
            {
                "ticker": "ORCL",
                "rank": 7,
                "reason": "理由 7",
                "streak": 1,
                "new": True,
                "verdict": None,
                "skip_reason": None,
                "held": False,
            },
        ],
        "dropped": [{"ticker": "TSLA", "reasons": ["rank_cut"]}],
        "account": {"cash": D(9398), "equity": D(12698), "unrealized_pnl": D("299.4"), "realized_pnl": D("398.6")},
        "coverage": {
            "families": {
                "bars": {"total": 138, "ok": 130, "missing": [{"target": "IBM", "reason": "x"}], "skipped": None}
            },
            "missing_bars": ["IBM"],
        },
        "unresolved": {"missing_bars": ["IBM"], "agenda": ["AAPL 持仓复查"]},
        "footer": {
            "runtime": "rt-1",
            "provider": "prov",
            "model": "m-1",
            "usage": {"input": 1200, "output": 340},
            "elapsed_seconds": 95,
            "note": None,
        },
    }
    row.update(overrides)
    return row


def test_today_actions_section_first_or_absent():
    text = render_brief(view(), final=True)
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings[0] == "## 今日操作"
    assert "- AAPL：止损已触及，未见卖出记录" in text
    # 没有内容时整段不出现（spec §11 第 0 段）
    empty = render_brief(view(today_actions=[]), final=True)
    assert "今日操作" not in empty


def test_no_changes_says_so():
    text = render_brief(view(changes=None), final=True)
    assert "没有变化" in text
    first_run = render_brief(view(first_run=True, changes=None), final=True)
    assert "首次运行" in first_run and "没有变化" not in first_run
    changed = render_brief(view(), final=True)
    assert "新计划 MSFT：放量突破" in changed and "名单新上榜 2 只、掉出 1 只" in changed


def test_draft_marks_unfinished_and_hides_agent_parts():
    draft = render_brief(view(), final=False)
    assert draft.splitlines()[1] == "二次确认未完成"
    assert "## 一句话" not in draft and "一句话总结。" not in draft
    assert "复查：" not in draft  # 第 4 段的复查结论不出现
    assert " → 确认" not in draft  # 第 6 段的结论不出现
    assert "名额已满" in draft  # 名单里没有结论的写明原因
    final = render_brief(view(), final=True)
    assert "二次确认未完成" not in final
    assert "## 一句话" in final and "一句话总结。" in final
    assert "复查：成立——论点仍成立" in final
    assert " → 确认" in final


def test_footer_has_disclaimer_and_model():
    final = render_brief(view(), final=True)
    assert FOOTER_BASE in final
    assert "模型 m-1" in final and "规则未经验证，不构成投资建议" in final
    assert "耗时 95 秒" in final and "token" in final
    no_model = render_brief(view(footer={"runtime": None, "provider": None, "model": None}), final=True)
    assert FOOTER_BASE in no_model and "未选定模型，二次确认未做" in no_model
    draft = render_brief(view(), final=False)
    assert FOOTER_BASE in draft and "m-1" not in draft.split(FOOTER_BASE)[-1]


def test_error_brief_leads_with_stop_warning_when_holding():
    text = render_error_brief(session=SESSION, as_of=AS_OF, error="数据库不可达", has_holdings=True)
    lines = text.splitlines()
    assert lines[0] == "# 简报 · 2026-11-04"
    assert lines[2] == "今天无法检查止损：数据库不可达"  # 有持仓时第一行
    assert FOOTER_BASE in text
    no_holdings = render_error_brief(session=SESSION, as_of=AS_OF, error="数据库不可达", has_holdings=False)
    assert "今天无法检查止损" not in no_holdings
    assert "今天没有简报：数据库不可达" in no_holdings


def test_no_downgrade_word():
    for kwargs, final in (
        ({}, True),
        ({}, False),
        (
            {
                "today_actions": [],
                "changes": None,
                "pending_plans": [],
                "dropped": [],
                "unresolved": {"missing_bars": [], "agenda": []},
            },
            True,
        ),
    ):
        assert "降级" not in render_brief(view(**kwargs), final=final)
    assert "降级" not in render_error_brief(session=SESSION, as_of=AS_OF, error="基准日线缺失：SPY", has_holdings=True)


def test_discord_lines_order():
    lines = discord_lines(view(), brief_path="/tmp/briefs/2026-11-04.md")
    # 顺序（spec §11）：今日操作 → 一句话 → 持仓操作各一行 → 新计划 / 结束的计划 → 文件位置
    assert lines[0] == "AAPL：止损已触及，未见卖出记录"
    assert lines[1] == "一句话总结。"
    assert lines[2] == "AAPL：继续持有"
    assert lines[3] == "新计划：新计划 MSFT：放量突破"
    assert lines[4] == "结束的计划：结束 TSLA：5 个交易日未买入"
    assert lines[-1] == "简报文件：/tmp/briefs/2026-11-04.md"
    assert len(lines) == 6


def test_draft_sends_unconcluded_top_n_into_confirmation():
    """草稿里没有结论、没有原因、未持有的前 N 只标「进入二次确认」（§11 第 6 段）。"""
    draft = render_brief(
        view(
            candidates=[
                {
                    "ticker": "AMD",
                    "rank": 2,
                    "reason": "理由 2",
                    "streak": 1,
                    "new": True,
                    "verdict": None,
                    "skip_reason": None,
                    "held": False,
                }
            ]
        ),
        final=False,
    )
    line = next(l for l in draft.splitlines() if l.startswith("2. AMD"))
    assert line.endswith("→ 进入二次确认")
    final = render_brief(
        view(
            candidates=[
                {
                    "ticker": "AMD",
                    "rank": 2,
                    "reason": "理由 2",
                    "streak": 1,
                    "new": True,
                    "verdict": None,
                    "skip_reason": None,
                    "held": False,
                }
            ]
        ),
        final=True,
    )
    assert "进入二次确认" not in final  # 最终简报不写这句


def test_price_text_trims_storage_zeros_without_rounding():
    from investment_assistant.core.texts import price_text

    assert price_text(D("164.130000")) == "164.13"
    assert price_text(D("40.500000")) == "40.50"
    assert price_text(D("34.457")) == "34.46"  # 取到分、四舍五入（spec §7 / B14）
    assert price_text(D("100.000000")) == "100.00"
    assert price_text(None) == "—"


def test_pending_line_formats_prices_and_unsized_shares():
    """2026-11-07 上线冒烟：待入场行出现「164.130000」与「None 股」；股数没算时用固定文案。"""
    plan = {
        "ticker": "GILD",
        "day": 1,
        "entry_low": D("164.130000"),
        "entry_high": D("169.650000"),
        "stop": D("155.850000"),
        "target1": D("188.980000"),
        "shares": None,
        "valid_until": date(2026, 11, 13),
    }
    text = render_brief(view(pending_plans=[plan]), final=True)
    line = next(row for row in text.splitlines() if row.startswith("- GILD"))
    assert line == (
        "- GILD（第 1 天）：入场 164.13–169.65，止损 155.85，目标 188.98，未提供账户资金，未算股数，有效至 2026-11-13"
    )
    assert "None" not in text


def test_regime_line_does_not_repeat_the_same_word():
    market = {"breadth": {}, "spy_ret20": None, "etf_table": []}
    neutral = render_brief(view(market={**market, "label": "neutral", "regime_tier": "neutral"}), final=True)
    assert "- 区制：中性\n" in neutral and "中性（中性）" not in neutral
    risk_on = render_brief(view(market={**market, "label": "risk_on", "regime_tier": "favorable"}), final=True)
    assert "- 区制：进攻（有利）" in risk_on


def test_display_values_for_money_quantity_and_price():
    from investment_assistant.core.texts import money_value, price_value, quantity_value

    assert str(money_value(D("-2736.000000000000"))) == "-2736.00"
    assert str(money_value(D("12.345"))) == "12.35"
    assert money_value(None) is None
    assert str(quantity_value(D("120.000000"))) == "120"
    assert str(quantity_value(D("0.500000"))) == "0.5"
    assert str(price_value(D("75.870000"))) == "75.87"
    assert str(price_value(D("100.000000"))) == "100.00"


def test_holding_line_shows_money_to_the_cent():
    holding = {
        "ticker": "RKLB",
        "next_action": {"kind": "hold", "text": "继续持有"},
        "stop": D("44.280000"),
        "unrealized_pnl": D("-2736.000000000000"),
        "pnl_pct": -0.3005,
    }
    text = render_brief(view(holdings=[holding]), final=True)
    line = next(row for row in text.splitlines() if row.startswith("- RKLB"))
    assert "止损 44.28" in line and "浮动盈亏 -2736.00（-30.0%）" in line


def _research_rows():
    return [
        {
            "ticker": "SNDK",
            "day": 3,
            "thesis": "近期大幅下跌，我认为是入场的时间",
            "condition_text": "收盘站上 38.00",
            "last_close": D("38.50"),
            "state": "triggered",
            "conclusion": "可以出计划",
        },
        {
            "ticker": "MU",
            "day": 2,
            "thesis": "内存涨价周期",
            "condition_text": "最低价触及 35.00 或以下",
            "last_close": D("36.10"),
            "state": "watching",
            "conclusion": None,
        },
        {
            "ticker": "WDC",
            "day": 5,
            "thesis": "同板块联动",
            "condition_text": "收盘跌破 30.00",
            "last_close": None,
            "state": "missing_bar",
            "conclusion": None,
        },
        {
            "ticker": "STX",
            "day": 1,
            "thesis": "论点未定",
            "condition_text": None,
            "last_close": D("30.00"),
            "state": "no_condition",
            "conclusion": None,
        },
    ]


def test_research_section_states_and_absent_when_empty():
    """第 5a 段「研究」：排在待入场计划之后；四种状态文案；最终稿多一句结论；没有研究时整段不出现。"""
    final = render_brief(view(research=_research_rows()), final=True)
    lines = final.splitlines()
    assert "## 研究" in lines
    assert lines.index("## 研究") > lines.index("## 待入场计划")
    assert lines.index("## 研究") < lines.index("## 适合交易什么（规则名单）")
    assert (
        "- SNDK（第 3 天）：近期大幅下跌，我认为是入场的时间；条件：收盘站上 38.00；"
        "最新收盘 38.50；条件已到，待复看；结论：可以出计划" in lines
    )
    assert "- MU（第 2 天）：内存涨价周期；条件：最低价触及 35.00 或以下；最新收盘 36.10；观察中" in lines
    assert "- WDC（第 5 天）：同板块联动；条件：收盘跌破 30.00；最新收盘 —；缺日线，未复查" in lines
    assert "- STX（第 1 天）：论点未定；条件：尚无条件；最新收盘 30.00；尚无条件" in lines
    # 草稿没有结论句，研究段照常
    draft = render_brief(view(research=_research_rows()), final=False)
    assert "## 研究" in draft.splitlines() and "结论：" not in draft
    # 论点截到 40 字
    long_thesis = "长" * 60
    rows = [{**_research_rows()[1], "thesis": long_thesis}]
    text = render_brief(view(research=rows), final=True)
    assert f"：{'长' * 40}；条件：" in text and f"{'长' * 41}" not in text
    # research 为空 → 整段不出现
    assert "## 研究" not in render_brief(view(research=[]), final=True).splitlines()


def test_changes_include_research_lines():
    changes = {
        "new_plans": [],
        "ended_plans": [],
        "holding_changes": [],
        "research": ["新研究 SNDK：论点待补", "研究 SNDK 条件已到：收盘站上 38.00"],
        "new_count": 0,
        "dropped_count": 0,
    }
    text = render_brief(view(changes=changes), final=True)
    assert "新研究 SNDK：论点待补" in text and "研究 SNDK 条件已到：收盘站上 38.00" in text
    assert "没有变化" not in text


def test_footer_says_usd():
    assert "金额单位美元" in render_brief(view(), final=True)
    assert "金额单位美元" in render_brief(view(), final=False)
    assert render_brief(view(), final=True).strip().endswith("金额单位美元。")


def test_discord_research_triggered_line_after_new_plans():
    lines = discord_lines(view(research=_research_rows()), brief_path="/tmp/briefs/2026-11-04.md")
    assert lines[3] == "新计划：新计划 MSFT：放量突破"  # 新计划行之后
    assert lines[4] == "研究 SNDK：条件已到（收盘站上 38.00）"
    assert lines[5] == "结束的计划：结束 TSLA：5 个交易日未买入"
    assert lines[-1] == "简报文件：/tmp/briefs/2026-11-04.md"


def _weekly(**over):
    row = {
        "week_start": date(2026, 11, 2),
        "week_end": date(2026, 11, 6),
        "ended": 0,
        "followed": 0,
        "deviated": 0,
        "tag_counts": {},
        "closed": [],
        "total_r": D(0),
        "not_bought": [],
        "equity_start": None,
        "equity_end": None,
        "realized_pnl": D("0.00"),
        "listing": {"new": [], "dropped": [], "most_relisted": None},
    }
    row.update(over)
    return row


def test_weekly_renders_with_no_records():
    """Review Focus 6：空周的周报照常产出，有标题与页脚。"""
    md = render_weekly(_weekly(), as_of=AS_OF)
    assert md.splitlines()[0] == "# 周报 · 2026-11-02–2026-11-06"
    assert "本周没有结束的计划" in md
    assert (
        "数据截至 2026-11-06 收盘（采集于北京时间 2026-11-05 08:30）。规则未经验证，不构成投资建议。金额单位美元。"
        in md
    )


def test_weekly_lists_closed_and_not_bought():
    weekly = _weekly(
        ended=2,
        followed=1,
        deviated=1,
        tag_counts={"早卖": 1},
        closed=[
            {"ticker": "AAPL", "r_multiple": D("1.25"), "exit_reason": "manual", "tags": ["早卖"]},
            {"ticker": "MSFT", "r_multiple": D("-0.50"), "exit_reason": "initial_stop", "tags": []},
        ],
        total_r=D("0.75"),
        not_bought=[
            {
                "ticker": "TSLA",
                "end_reason": "5 个交易日未买入",
                "hypothetical": {"status": "not_filled", "reason": "never_in_zone"},
            },
            {
                "ticker": "NVDA",
                "end_reason": "被替换",
                "hypothetical": {
                    "status": "exited",
                    "exit_session": date(2026, 11, 5),
                    "exit_price": D("32.45"),
                    "exit_reason": "trailing_stop",
                    "r_multiple": D("0.56"),
                },
            },
        ],
        equity_start=D("12000.00"),
        equity_end=D("12500.50"),
        realized_pnl=D("300.00"),
        listing={"new": ["MSFT"], "dropped": ["NVDA"], "most_relisted": "ORCL"},
    )
    md = render_weekly(weekly, as_of=AS_OF)
    assert "本周结束 2 份：照做 1 份、有偏差 1 份。" in md
    assert "偏差标签：早卖 ×1。" in md
    assert "- AAPL：R 1.25，用户自行卖出，标签：早卖" in md
    assert "- MSFT：R -0.50，初始止损，标签：无" in md
    assert "- TSLA：5 个交易日未买入，如果照做：未成交，价格未进入场区间。" in md
    assert "- NVDA：被替换，如果照做：2026-11-05 以 32.45 离场（移动止损），R 0.56。" in md
    assert "- 周初总资产 12000.00，周末总资产 12500.50，本周已实现盈亏 300.00。" in md
    assert "- 新上榜：MSFT；掉出：NVDA；再次上榜最多：ORCL。" in md
    assert md.strip().endswith("金额单位美元。")
