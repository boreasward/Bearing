"""v3 draft rendering, migrated to facts.brief.render_brief(view, final=False) (spec §11).

The old draft sections survive as the agent-free parts of the brief; error drafts go through
render_error_brief. render_draft itself stays as a thin delegate for job.py until Task 3 rewires it.
"""

from datetime import date, datetime

from investment_assistant.facts.brief import render_brief, render_error_brief

from investment_assistant.facts.draft import render_draft

AS_OF = datetime(2026, 11, 5, 8, 30)
MARKET = {
    "label": "neutral",
    "regime_tier": "neutral",
    "breadth": {
        "tickers": 100,
        "eligible_50": 100,
        "above_50": 62,
        "eligible_200": 100,
        "above_200": 55,
        "eligible_52w": 100,
        "new_highs": 12,
        "new_lows": 3,
        "pct_50": 0.62,
        "pct_200": 0.55,
    },
    "spy_ret20": -0.013,
    "etf_table": [
        {"etf": "XLV", "ret20": 0.021, "rs20": 0.034, "rs60": 0.05, "state": "strong"},
        {"etf": "XLP", "ret20": 0.011, "rs20": 0.024, "rs60": 0.03, "state": "strong"},
        {"etf": "XLU", "ret20": 0.005, "rs20": 0.018, "rs60": 0.01, "state": "mixed"},
        {"etf": "SMH", "ret20": -0.193, "rs20": -0.18, "rs60": -0.12, "state": "weak"},
    ],
}
CANDS = [{"ticker": f"T{i}", "rank": i, "reason": f"理由 {i}"} for i in range(1, 8)]
COV = {"families": {"bars": {"total": 138, "ok": 130, "missing": [], "skipped": None}}, "missing_bars": []}
FOOTER = "数据截至 2026-11-04 收盘（采集于北京时间 2026-11-05 08:30）。规则未经验证，不构成投资建议。"


def view(**overrides):
    row = {
        "session": date(2026, 11, 4),
        "as_of": AS_OF,
        "top_n": 5,
        "first_run": False,
        "today_actions": [],
        "summary": None,
        "changes": None,
        "market": MARKET,
        "holdings": [],
        "pending_plans": [],
        "candidates": CANDS,
        "dropped": [],
        "account": None,
        "coverage": COV,
        "unresolved": {"missing_bars": [], "agenda": []},
        "footer": {},
    }
    row.update(overrides)
    return row


def render(**overrides):
    return render_brief(view(**overrides), final=False)


def test_sections_and_footer():
    text = render()
    assert text.splitlines()[1] == "二次确认未完成"
    for heading in ("## 市场环境", "## 适合交易什么（规则名单）", "## 持仓", "## 今天下不了结论的"):
        assert heading in text
    assert FOOTER in text
    assert "没有持仓记录。" in text and "降级" not in text
    lines = text.splitlines()
    for i, cand in enumerate(CANDS, start=1):
        line = next(l for l in lines if l.startswith(f"{i}. {cand['ticker']} · "))
        assert line.startswith(f"{i}. {cand['ticker']} · {cand['reason']}")
        assert line.endswith("→ 进入二次确认") == (i <= 5)


def test_no_candidates():
    text = render(candidates=[])
    assert "今天没有入选的股票。" in text
    assert "→ 进入二次确认" not in text


def test_error_draft():
    text = render_error_brief(session=date(2026, 11, 4), as_of=AS_OF, error="基准日线缺失：SPY", has_holdings=False)
    assert "今天没有简报：基准日线缺失：SPY" in text and FOOTER in text
    for heading in ("## 市场环境", "## 适合交易什么（规则名单）", "## 持仓", "## 今天下不了结论的"):
        assert heading not in text


def test_render_draft_delegates_to_brief():
    """render_draft 保留原名：内部构造最小 view 调 render_brief(final=False)。"""
    text = render_draft(
        session=date(2026, 11, 4), as_of=AS_OF, market=MARKET, candidates=CANDS, holdings=[], coverage=COV, top_n=5
    )
    assert text == render()
    error = render_draft(
        session=date(2026, 11, 4),
        as_of=AS_OF,
        market=MARKET,
        candidates=[],
        holdings=[],
        coverage=COV,
        top_n=5,
        error="基准日线缺失：SPY",
    )
    assert error == render_error_brief(
        session=date(2026, 11, 4), as_of=AS_OF, error="基准日线缺失：SPY", has_holdings=False
    )
