"""v3.1 brief rendering (spec §11): ten sections, draft vs final, error brief, Discord lines."""
from datetime import date, datetime
from decimal import Decimal as D

from investment_assistant.facts.brief import discord_lines, render_brief, render_error_brief

SESSION = date(2026, 11, 4)
AS_OF = datetime(2026, 11, 5, 8, 30)
FOOTER_BASE = '数据截至 2026-11-04 收盘（采集于北京时间 2026-11-05 08:30）。规则未经验证，不构成投资建议。'


def view(**overrides):
    row = {
        'session': SESSION, 'as_of': AS_OF, 'top_n': 5, 'first_run': False,
        'today_actions': [{'ticker': 'AAPL', 'text': '止损已触及，未见卖出记录'}],
        'summary': '一句话总结。',
        'changes': {'new_plans': ['新计划 MSFT：放量突破'], 'ended_plans': ['结束 TSLA：5 个交易日未买入'],
                    'holding_changes': ['AAPL 止损上调至 130'], 'new_count': 2, 'dropped_count': 1},
        'market': {'label': 'neutral', 'regime_tier': 'neutral', 'breadth': {}, 'spy_ret20': -0.013,
                   'etf_table': []},
        'holdings': [{'ticker': 'AAPL', 'quantity': D(10), 'avg_cost': D(105), 'last_close': 120.0,
                      'pnl_pct': 0.14, 'stop': D(130), 'to_stop_pct': 0.08, 'plan_id': 8, 'kind': 'planned',
                      'trailing': True, 'unrealized_pnl': 150.0, 'held_sessions': 3,
                      'next_action': {'kind': 'hold', 'text': '继续持有', 'stop': D(130), 'reason': None},
                      'stop_order_status': 'confirmed', 'review': {'verdict': 'holds', 'reasons': '论点仍成立'},
                      'earnings_note': '财报日 12-02'}],
        'pending_plans': [{'ticker': 'MSFT', 'day': 2, 'entry_low': D(300), 'entry_high': D(310),
                           'stop': D(290), 'target1': D(340), 'shares': 20,
                           'valid_until': date(2026, 11, 10)}],
        'candidates': [{'ticker': 'MSFT', 'rank': 1, 'reason': '理由 1', 'streak': 2, 'new': False,
                        'verdict': 'confirm', 'skip_reason': None, 'held': False},
                       {'ticker': 'NVDA', 'rank': 2, 'reason': '理由 2', 'streak': 1, 'new': True,
                        'verdict': None, 'skip_reason': '名额已满', 'held': False},
                       {'ticker': 'AAPL', 'rank': 3, 'reason': '理由 3', 'streak': 5, 'new': False,
                        'verdict': None, 'skip_reason': None, 'held': True},
                       {'ticker': 'ORCL', 'rank': 7, 'reason': '理由 7', 'streak': 1, 'new': True,
                        'verdict': None, 'skip_reason': None, 'held': False}],
        'dropped': [{'ticker': 'TSLA', 'reasons': ['rank_cut']}],
        'account': {'cash': D(9398), 'equity': D(12698), 'unrealized_pnl': D('299.4'),
                    'realized_pnl': D('398.6')},
        'coverage': {'families': {'bars': {'total': 138, 'ok': 130,
                                           'missing': [{'target': 'IBM', 'reason': 'x'}], 'skipped': None}},
                     'missing_bars': ['IBM']},
        'unresolved': {'missing_bars': ['IBM'], 'agenda': ['AAPL 持仓复查']},
        'footer': {'runtime': 'rt-1', 'provider': 'prov', 'model': 'm-1',
                   'usage': {'input': 1200, 'output': 340}, 'elapsed_seconds': 95, 'note': None},
    }
    row.update(overrides)
    return row


def test_today_actions_section_first_or_absent():
    text = render_brief(view(), final=True)
    headings = [line for line in text.splitlines() if line.startswith('## ')]
    assert headings[0] == '## 今日操作'
    assert '- AAPL：止损已触及，未见卖出记录' in text
    # 没有内容时整段不出现（spec §11 第 0 段）
    empty = render_brief(view(today_actions=[]), final=True)
    assert '今日操作' not in empty


def test_no_changes_says_so():
    text = render_brief(view(changes=None), final=True)
    assert '没有变化' in text
    first_run = render_brief(view(first_run=True, changes=None), final=True)
    assert '首次运行' in first_run and '没有变化' not in first_run
    changed = render_brief(view(), final=True)
    assert '新计划 MSFT：放量突破' in changed and '名单新上榜 2 只、掉出 1 只' in changed


def test_draft_marks_unfinished_and_hides_agent_parts():
    draft = render_brief(view(), final=False)
    assert draft.splitlines()[1] == '二次确认未完成'
    assert '## 一句话' not in draft and '一句话总结。' not in draft
    assert '复查：' not in draft                    # 第 4 段的复查结论不出现
    assert ' → 确认' not in draft                   # 第 6 段的结论不出现
    assert '名额已满' in draft                      # 名单里没有结论的写明原因
    final = render_brief(view(), final=True)
    assert '二次确认未完成' not in final
    assert '## 一句话' in final and '一句话总结。' in final
    assert '复查：成立——论点仍成立' in final
    assert ' → 确认' in final


def test_footer_has_disclaimer_and_model():
    final = render_brief(view(), final=True)
    assert FOOTER_BASE in final
    assert '模型 m-1' in final and '规则未经验证，不构成投资建议' in final
    assert '耗时 95 秒' in final and 'token' in final
    no_model = render_brief(view(footer={'runtime': None, 'provider': None, 'model': None}), final=True)
    assert FOOTER_BASE in no_model and '未选定模型，二次确认未做' in no_model
    draft = render_brief(view(), final=False)
    assert FOOTER_BASE in draft and 'm-1' not in draft.split(FOOTER_BASE)[-1]


def test_error_brief_leads_with_stop_warning_when_holding():
    text = render_error_brief(session=SESSION, as_of=AS_OF, error='数据库不可达', has_holdings=True)
    lines = text.splitlines()
    assert lines[0] == '# 简报 · 2026-11-04'
    assert lines[2] == '今天无法检查止损：数据库不可达'      # 有持仓时第一行
    assert FOOTER_BASE in text
    no_holdings = render_error_brief(session=SESSION, as_of=AS_OF, error='数据库不可达', has_holdings=False)
    assert '今天无法检查止损' not in no_holdings
    assert '今天没有简报：数据库不可达' in no_holdings


def test_no_downgrade_word():
    for kwargs, final in (({}, True), ({}, False),
                          ({'today_actions': [], 'changes': None, 'pending_plans': [], 'dropped': [],
                            'unresolved': {'missing_bars': [], 'agenda': []}}, True)):
        assert '降级' not in render_brief(view(**kwargs), final=final)
    assert '降级' not in render_error_brief(session=SESSION, as_of=AS_OF, error='基准日线缺失：SPY',
                                            has_holdings=True)


def test_discord_lines_order():
    lines = discord_lines(view(), brief_path='/tmp/briefs/2026-11-04.md')
    # 顺序（spec §11）：今日操作 → 一句话 → 持仓操作各一行 → 新计划 / 结束的计划 → 文件位置
    assert lines[0] == 'AAPL：止损已触及，未见卖出记录'
    assert lines[1] == '一句话总结。'
    assert lines[2] == 'AAPL：继续持有'
    assert lines[3] == '新计划：新计划 MSFT：放量突破'
    assert lines[4] == '结束的计划：结束 TSLA：5 个交易日未买入'
    assert lines[-1] == '简报文件：/tmp/briefs/2026-11-04.md'
    assert len(lines) == 6
