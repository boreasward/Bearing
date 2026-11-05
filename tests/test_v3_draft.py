"""v3 draft rendering: sections, top-N markers, the empty-list fallbacks and the fixed footer."""
from datetime import date, datetime

from investment_assistant.facts.draft import render_draft

AS_OF = datetime(2026, 11, 5, 8, 30)
MARKET = {'label': 'neutral', 'regime_tier': 'neutral',
          'breadth': {'tickers': 100, 'eligible_50': 100, 'above_50': 62,
                      'eligible_200': 100, 'above_200': 55, 'eligible_52w': 100,
                      'new_highs': 12, 'new_lows': 3, 'pct_50': 0.62, 'pct_200': 0.55},
          'spy_ret20': -0.013,
          'etf_table': [{'etf': 'XLV', 'ret20': 0.021, 'rs20': 0.034, 'rs60': 0.05, 'state': 'strong'},
                        {'etf': 'XLP', 'ret20': 0.011, 'rs20': 0.024, 'rs60': 0.03, 'state': 'strong'},
                        {'etf': 'XLU', 'ret20': 0.005, 'rs20': 0.018, 'rs60': 0.01, 'state': 'mixed'},
                        {'etf': 'SMH', 'ret20': -0.193, 'rs20': -0.18, 'rs60': -0.12, 'state': 'weak'}]}
CANDS = [{'ticker': f'T{i}', 'rank': i, 'reason': f'理由 {i}'} for i in range(1, 8)]
COV = {'families': {'bars': {'total': 138, 'ok': 130, 'missing': [], 'skipped': None}},
       'missing_bars': []}
FOOTER = '数据截至 2026-11-04 收盘（采集于北京时间 2026-11-05 08:30）。规则未经验证，不构成投资建议。'


def render(**overrides):
    kwargs = {'session': date(2026, 11, 4), 'as_of': AS_OF, 'market': MARKET, 'candidates': CANDS,
              'holdings': [], 'coverage': COV, 'top_n': 5}
    kwargs.update(overrides)
    return render_draft(**kwargs)


def test_sections_and_footer():
    text = render()
    for heading in ('# 草稿 · 2026-11-04', '## 市场环境', '## 适合交易什么（规则名单）', '## 持仓',
                    '## 今天下不了结论的'):
        assert heading in text
    assert FOOTER in text
    assert '没有持仓记录。' in text and '降级' not in text
    lines = text.splitlines()
    for i, cand in enumerate(CANDS, start=1):
        line = next(l for l in lines if l.startswith(f'{i}. {cand["ticker"]} · '))
        assert line.startswith(f"{i}. {cand['ticker']} · {cand['reason']}")
        assert line.endswith('→ 进入二次确认') == (i <= 5)


def test_no_candidates():
    text = render(candidates=[])
    assert '今天没有入选的股票。' in text
    assert '→ 进入二次确认' not in text


def test_error_draft():
    text = render(error='基准日线缺失：SPY')
    assert '今天没有草稿：基准日线缺失：SPY' in text and FOOTER in text
    for heading in ('## 市场环境', '## 适合交易什么（规则名单）', '## 持仓', '## 今天下不了结论的'):
        assert heading not in text
