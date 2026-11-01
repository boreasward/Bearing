"""Review push payloads and their Discord templates (P4 design §6): pure functions, no network."""
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from investment_assistant.notify import templates
from investment_assistant.review import period_digest as review_digest

SH = ZoneInfo('Asia/Shanghai')
NY = ZoneInfo('America/New_York')
URL = 'http://localhost:8787'
EVIDENCE = '描述性统计，样本小且重叠，不代表规则或 Agent 有效。证据：未验证'


def _track_stats(units=2, determined=2, mean=1.0, median=1.0, total=2.0, pending=0, by_status=None,
                 by_plan_kind=None):
    return {'units': units, 'determined': determined, 'pending': pending, 'missing': 0,
            'mean': mean, 'median': median, 'sum': total, 'wins': 1,
            'by_status': by_status or {'closed': 1, 'open': 1},
            'by_plan_kind': by_plan_kind or {'rule': 1, 'agent': 1}}


WEEKLY_REVIEW = {
    'id': 7, 'week_start': date(2026, 10, 19), 'calc_version': 'review-4',
    'stats': {
        'units': {'decided': 2, 'undecided': 1},
        'unsignaled_trades': ['t9'],
        'closed_trades': [{'id': 1}, {'id': 2}],
        'tracks': {'units': [], 'summary': {'all': {
            'rule': _track_stats(mean=0.5, median=0.5, total=1.0),
            'agent': _track_stats(mean=0.75, median=0.75, total=1.5),
            'actual': _track_stats(by_status={'not_executed': 1, 'open': 1},
                                   by_plan_kind={'rule': 1, 'agent': 1})}}},
        'by_pool': {'core': {'trend_reclaim': {'triggers': 3, 'plans': 2, 'expired': 0, 'filled': 1,
                                               'r_mean': 1.5, 'r_median': 1.5}}},
        'discipline': {'counts': {'chase': 0, 'early_take': 1, 'held_loser': 0, 'unplanned': 0},
                       'compliance_rate': 0.9},
        'note': '描述性统计，样本小且重叠，不代表规则有效。',
    }}


def _weekly_payload(review=WEEKLY_REVIEW):
    return review_digest.weekly(review, public_url=URL)


# --- due weeks and months ---

def test_due_week_after_friday_close():
    saturday = datetime(2026, 10, 24, 8, 0, tzinfo=SH)      # 北京周六 08:00 = 纽约周五晚
    assert review_digest.due_week(saturday) == date(2026, 10, 19)


def test_due_week_midweek_is_previous_week():
    wednesday = datetime(2026, 10, 21, 8, 0, tzinfo=SH)
    assert review_digest.due_week(wednesday) == date(2026, 10, 12)


def test_due_week_good_friday():
    after_good_friday = datetime(2026, 4, 4, 8, 0, tzinfo=SH)   # 2026-04-03 耶稣受难日休市
    assert review_digest.due_week(after_good_friday) == date(2026, 3, 30)


def test_due_month_after_last_session():
    december = datetime(2026, 12, 5, 8, 0, tzinfo=SH)           # 11-30 周一收盘后
    assert review_digest.due_month(december) == date(2026, 11, 1)


def test_due_month_month_ends_on_weekend():
    november_first = datetime(2026, 11, 1, 8, 0, tzinfo=SH)     # 10-31 是周六
    assert review_digest.due_month(november_first) == date(2026, 10, 1)


def test_last_session_picks_final_trading_day():
    assert review_digest.last_session(date(2026, 10, 19), date(2026, 10, 25)) == date(2026, 10, 23)


def test_reminder_due_first_saturday():
    month = date(2026, 10, 1)
    assert review_digest.reminder_due(month, datetime(2026, 11, 6, 8, 0, tzinfo=SH)) is False
    assert review_digest.reminder_due(month, datetime(2026, 11, 7, 8, 0, tzinfo=SH)) is True
    assert review_digest.reminder_due(month, datetime(2026, 11, 20, 8, 0, tzinfo=SH)) is True


# --- weekly payload and template ---

def test_weekly_payload_shape():
    payload = _weekly_payload()
    assert payload['kind'] == 'weekly' and payload['period'] == date(2026, 10, 19)
    assert payload['start'] == '2026-10-19' and payload['end'] == '2026-10-25'
    assert payload['overview'] == {'decided': 2, 'undecided': 1, 'unsignaled': 1, 'closed': 2}
    assert payload['tracks']['actual']['units'] == 2
    assert payload['rules'] == [{'pool': 'core', 'rule_id': 'trend_reclaim', 'triggers': 3, 'plans': 2,
                                 'filled': 1, 'r_mean': 1.5}]
    assert payload['discipline'] == {'chase': 0, 'early_take': 1, 'held_loser': 0, 'unplanned': 0,
                                     'compliance_rate': 0.9}
    assert payload['evidence'] == '证据：未验证'
    assert payload['note'] == '描述性统计，样本小且重叠，不代表规则或 Agent 有效。'
    assert payload['url'] == 'http://localhost:8787/#review/weekly'


def test_weekly_lines():
    messages = templates.workbench_weekly(_weekly_payload())
    assert len(messages) == 1
    text = chr(10).join(f.value for f in messages[0].fields)
    assert messages[0].title == '📅 周度复盘 · 2026-10-19 – 2026-10-25'
    assert '已决定 2 · 未处理 1 · 计划外交易 1 · 已平仓 2' in text
    assert '实际 2 单元 · 已定 2 · 均值 +1.00R · 中位 +1.00R · 合计 +2.00R' in text
    assert '实际：未执行 1（记 0R）· 未平仓 1 · 照规则 1 · 照 Agent 1 · 照自定 0' in text
    assert '核心 trend_reclaim 触发 3 · 计划 2 · 成交 1 · 均值 +1.50R' in text
    assert '追高 0 · 过早止盈 1 · 死扛亏损 0 · 计划外 0 · 合规率 90%' in text
    assert '描述性统计' in text and '#review/weekly' in text


def test_weekly_without_tracks_says_so():
    review = {**WEEKLY_REVIEW, 'stats': {**WEEKLY_REVIEW['stats'], 'tracks': None}}
    payload = review_digest.weekly(review, public_url=URL)
    assert payload['tracks'] is None
    text = chr(10).join(f.value for f in templates.workbench_weekly(payload)[0].fields)
    assert '该快照无轨道对比' in text


def test_weekly_mean_none_renders_dash():
    review = WEEKLY_REVIEW
    stats = review['stats']
    tracks = {**stats['tracks'], 'summary': {'all': {k: {**v, 'mean': None, 'median': None, 'sum': None}
                                                     for k, v in stats['tracks']['summary']['all'].items()}}}
    payload = review_digest.weekly({**review, 'stats': {**stats, 'tracks': tracks}}, public_url=URL)
    text = chr(10).join(f.value for f in templates.workbench_weekly(payload)[0].fields)
    assert '均值 —' in text


# --- v2.7 §10: 「你学到了什么」 ---

def _learning(*, cards=2, knowledge=1, extra_cards=0):
    rows = [{'card_id': 1, 'ticker': 'NVDA', 'end_kind': 'position_closed', 'end_text': '平仓（按止损）',
             'decision_text': '采纳（照规则计划）', 'actual_r': -1.0, 'rule_r': -1.0, 'agent_r': None,
             'flags': ['chase'], 'status': 'to_review', 'headline': 'h'},
            {'card_id': 2, 'ticker': 'AMD', 'end_kind': 'expired', 'end_text': '过期未成交',
             'decision_text': '降为观察', 'actual_r': None, 'rule_r': None, 'agent_r': None, 'flags': [],
             'status': 'reviewed', 'headline': None}][:cards]
    rows += [{**rows[0], 'card_id': 10 + n, 'ticker': f'T{n}'} for n in range(extra_cards)]
    return {'discipline': {'cards': len(rows), 'chase': 1, 'early_take': 0, 'held_loser': 0, 'unplanned': 1,
                           'pending': 2},
            'cards': rows,
            'counts': {'total': len(rows), 'reviewed': 1, 'to_review': len(rows) - 1, 'drafting': 0,
                       'draft_failed': 0},
            'knowledge': [{'ref': 'know:7@2', 'title': '回踩不追', 'new_outcomes': 1,
                           'sample': {'count': 3, 'determined': 3, 'expired': 0, 'r_mean': None,
                                      'r_median': None}}][:knowledge]}


def _learning_field(learning):
    review = {**WEEKLY_REVIEW, 'stats': {**WEEKLY_REVIEW['stats'], 'learning': learning}}
    messages = templates.workbench_weekly(review_digest.weekly(review, public_url=URL))
    names = [f.name for f in messages[0].fields]
    return messages[0], names, next((f for f in messages[0].fields if f.name == '你学到了什么'), None)


def test_weekly_payload_carries_learning():
    learning = _learning()
    assert review_digest.weekly({**WEEKLY_REVIEW, 'stats': {**WEEKLY_REVIEW['stats'], 'learning': learning}},
                                public_url=URL)['learning'] == learning


def test_weekly_learning_field_lines():
    message, names, field = _learning_field(_learning())
    assert names.index('纪律') + 1 == names.index('你学到了什么') == names.index('说明') - 1
    assert field.value.split(chr(10)) == [
        '纪律（本周结束的 2 笔）：追高 1 · 过早止盈 0 · 死扛亏损 0 · 计划外 1 · 待定 2',
        '复盘卡 2 张（已审定 1 · 待审定 1）',
        'NVDA 平仓（按止损） · 实际 -1.00R · 采纳（照规则计划）',
        'AMD 过期未成交 · 实际 未成交 · 降为观察',
        '被检验的知识 1 条',
        'know:7@2 回踩不追 · 样本 3（少于 5 个不给均值）']
    with_mean = _learning()
    with_mean['knowledge'][0]['sample'] = {'count': 6, 'determined': 5, 'expired': 1, 'r_mean': 0.4, 'r_median': 0.3}
    assert _learning_field(with_mean)[2].value.split(chr(10))[-1] == 'know:7@2 回踩不追 · 样本 6（均值 +0.40R）'


def test_weekly_learning_caps_cards_and_knowledge_lines():
    learning = _learning(extra_cards=5)                       # 7 cards: five lines + 「…另有 2 张」
    more = {**learning['knowledge'][0]}
    learning['knowledge'] = [{**more, 'ref': f'know:{n}@1', 'title': f'k{n}'} for n in range(1, 6)]
    lines = _learning_field(learning)[2].value.split(chr(10))
    assert len([line for line in lines if ' · 实际 ' in line]) == 5
    assert '…另有 2 张' in lines
    assert '被检验的知识 5 条' in lines
    assert [line for line in lines if line.startswith('know:')] == [f'know:{n}@1 k{n} · 样本 3（少于 5 个不给均值）'
                                                          for n in (1, 2, 3)]


def test_weekly_learning_truncated():
    learning = _learning(extra_cards=0)
    learning['cards'] = [{**learning['cards'][0], 'card_id': n, 'ticker': 'T' * 10,
                          'decision_text': '很长的决定文案' * 40} for n in range(5)]
    learning['counts'] = {'total': 5, 'reviewed': 0, 'to_review': 5, 'drafting': 0, 'draft_failed': 0}
    learning['discipline']['cards'] = 5
    learning['knowledge'] = [{'ref': f'know:{n}@1', 'title': '题' * 40, 'new_outcomes': 1,
                              'sample': {'count': 9, 'determined': 9, 'expired': 0, 'r_mean': 0.1, 'r_median': 0.1}}
                             for n in range(3)]
    message, _, field = _learning_field(learning)
    assert len(field.value) == 1024 and field.value.endswith('…')
    assert field.value.startswith('纪律（本周结束的 5 笔）')                 # 第 1、2 项完整，从第 3、4 项的末尾截断
    assert '复盘卡 5 张（已审定 0 · 待审定 5）' in field.value
    assert len(message.title) + sum(len(f.name) + len(f.value) for f in message.fields) + len(message.footer) < 6000


def test_weekly_learning_all_empty_one_line():
    empty = {'discipline': {'cards': 0, 'chase': 0, 'early_take': 0, 'held_loser': 0, 'unplanned': 0, 'pending': 0},
             'cards': [], 'counts': {'total': 0, 'reviewed': 0, 'to_review': 0, 'drafting': 0, 'draft_failed': 0},
             'knowledge': []}
    assert _learning_field(empty)[2].value == '本周没有结束的交易，也没有被检验的知识'


def test_old_snapshot_has_no_learning_field():
    payload = _weekly_payload()                                # review-4 快照：stats 里没有 learning
    assert payload['learning'] is None
    names = [f.name for f in templates.workbench_weekly(payload)[0].fields]
    assert '你学到了什么' not in names and names[-1] == '说明'


def test_weekly_learning_actual_text_for_unfilled_and_filled_without_r():
    """未成交 only when never bought; a filled trade without R (unplanned) reads 无 R（计划外）."""
    learning = _learning(cards=2)
    learning['cards'][0].update(actual_r=None, filled=True, ticker='UNP', end_text='平仓')
    learning['cards'][1].update(actual_r=None, filled=False)
    lines = _learning_field(learning)[2].value.split(chr(10))
    assert 'UNP 平仓 · 实际 无 R（计划外） · 采纳（照规则计划）' in lines
    assert 'AMD 过期未成交 · 实际 未成交 · 降为观察' in lines
    learning['cards'][0].update(actual_r=-1.0)
    assert 'UNP 平仓 · 实际 -1.00R · 采纳（照规则计划）' in _learning_field(learning)[2].value.split(chr(10))


# --- monthly payload and template ---

MONTHLY_REVIEW = {
    'id': 9, 'month': date(2026, 10, 1), 'calc_version': 'monthly-2',
    'stats': {
        'month': '2026-10', 'return': {'status': 'ok', 'return': 0.084, 'net_flow': 0.0},
        'tier': 'excellent', 'spy': {'return': 0.031, 'excess': 0.053},
        'review': {'flag': 'review', 'months': [], 'compound': -0.062, 'spy_compound': 0.021},
        'sensitivity': {'approximate': True, 'count': 4,
                        'rows': [{'risk_pct': 0.005, 'return': 0.01, 'max_drawdown': 0.015},
                                 {'risk_pct': 0.01, 'return': 0.02, 'max_drawdown': 0.03}]},
        'tracks': {'units': [], 'summary': {'all': {
            'rule': _track_stats(), 'agent': _track_stats(),
            'actual': _track_stats(by_status={'closed': 1, 'open': 1})}}},
        'discipline': {'counts': {'chase': 1, 'early_take': 0, 'held_loser': 0, 'unplanned': 0},
                       'compliance_rate': None},
        'note': '描述性统计，样本小且重叠，不代表规则有效。',
    }}


def test_monthly_payload_shape():
    payload = review_digest.monthly(MONTHLY_REVIEW, public_url=URL)
    assert payload['kind'] == 'monthly' and payload['period'] == date(2026, 10, 1)
    assert payload['month'] == '2026-10' and payload['tier'] == 'excellent'
    assert payload['review'] == {'flag': 'review', 'compound': -0.062, 'spy_compound': 0.021}
    assert payload['sensitivity']['count'] == 4
    assert payload['url'].endswith('#review/monthly')


def test_monthly_lines():
    messages = templates.workbench_monthly(review_digest.monthly(MONTHLY_REVIEW, public_url=URL))
    assert messages[0].title == '🗓 月度复盘 · 2026-10'
    text = chr(10).join(f.value for f in messages[0].fields)
    assert '本月 +8.4%（优秀）· SPY +3.1% · 超额 +5.3%' in text
    assert '⚠️ 需复查：3 个月复合 -6.2%，SPY +2.1%' in text
    assert '0.5% 收益 +1.0% · 回撤 1.5%' in text
    assert '共 4 笔' in text
    assert '合规率 —' in text                      # 合规率为空写 —
    assert '#review/monthly' in text


def test_monthly_missing_snapshot_line():
    review = {**MONTHLY_REVIEW,
              'stats': {**MONTHLY_REVIEW['stats'], 'return': {'status': 'missing_snapshot', 'return': None},
                        'tier': None, 'spy': None}}
    text = chr(10).join(f.value for f in
                        templates.workbench_monthly(review_digest.monthly(review, public_url=URL))[0].fields)
    assert '缺资金快照，无法计算' in text
    assert '0.0%' not in text.replace('+0.0%', '')  # 不写 0%


def test_monthly_review_flag_lines():
    def flag_text(flag, compound=None, spy=None):
        stats = {**MONTHLY_REVIEW['stats'],
                 'review': {'flag': flag, 'compound': compound, 'spy_compound': spy}}
        text = chr(10).join(f.value for f in
                            templates.workbench_monthly(review_digest.monthly({**MONTHLY_REVIEW, 'stats': stats},
                                                                             public_url=URL))[0].fields)
        return text

    assert '⚠️ 需复查：3 个月复合 -6.2%，SPY +2.1%' in flag_text('review', -0.062, 0.021)
    assert '正常' in flag_text('ok', 0.05, 0.02)
    assert '样本不足' in flag_text('insufficient')


def test_monthly_v1_snapshot_renders():
    v1 = {'id': 3, 'month': date(2026, 8, 1), 'calc_version': 'monthly-1',
          'stats': {'month': '2026-08', 'return': {'status': 'ok', 'return': 0.01},
                    'tier': 'viable', 'spy': {'return': 0.02, 'excess': -0.01},
                    'review_flag': 'ok', 'sensitivity': {'rows': [{'risk_pct': 0.01, 'return': 0.0,
                                                                   'max_drawdown': 0.0}]},
                    'tracks': None, 'discipline': None}}
    payload = review_digest.monthly(v1, public_url=URL)
    assert payload['review'] == {'flag': 'ok'}          # 只填 flag
    assert payload['sensitivity']['count'] is None      # monthly-1 无 count
    messages = templates.workbench_monthly(payload)
    assert len(messages) == 1
    text = chr(10).join(f.value for f in messages[0].fields)
    assert '该快照无轨道对比' in text and '2026-08' in messages[0].title


def test_monthly_v1_review_flag_without_compounds():
    """A monthly-1 snapshot carries only the flag: 'review' renders without the compound numbers."""
    v1 = {'id': 4, 'month': date(2026, 8, 1), 'calc_version': 'monthly-1',
          'stats': {'month': '2026-08', 'return': {'status': 'ok', 'return': -0.05},
                    'tier': 'attention', 'spy': {'return': 0.01, 'excess': -0.06},
                    'review_flag': 'review', 'sensitivity': {'rows': []}}}
    text = chr(10).join(f.value for f in
                        templates.workbench_monthly(review_digest.monthly(v1, public_url=URL))[0].fields)
    assert '⚠️ 需复查：连续 3 个月复合亏损且跑输 SPY' in text
    assert 'None' not in text


def test_weekly_rule_mean_is_formatted():
    stats = {**WEEKLY_REVIEW['stats'],
             'by_pool': {'core': {'trend_reclaim': {'triggers': 3, 'plans': 3, 'expired': 0, 'filled': 3,
                                                    'r_mean': 0.5333333333333333, 'r_median': 0.5}}}}
    text = chr(10).join(f.value for f in templates.workbench_weekly(
        review_digest.weekly({**WEEKLY_REVIEW, 'stats': stats}, public_url=URL))[0].fields)
    assert '均值 +0.53R' in text
    assert '0.5333' not in text


def test_reminder_text():
    payload = review_digest.reminder(date(2026, 10, 1), public_url=URL)
    assert payload == {'kind': 'monthly_reminder', 'period': date(2026, 10, 1),
                       'month': '2026-10', 'url': 'http://localhost:8787/#review/account'}
    messages = templates.workbench_review_reminder(payload)
    assert messages[0].title == '⏰ 月度复盘待录入 · 2026-10'
    text = chr(10).join(f.value for f in messages[0].fields)
    assert '缺 2026-10 月末资金快照。录入后在「复盘 → 月度复盘」生成并推送。' in text
    assert '#review/account' in text


def test_review_messages_within_discord_limits():
    rules = [{'pool': 'core', 'rule_id': f'rule_{i:02d}' + '很' * 60, 'triggers': 3, 'plans': 2,
              'filled': 1, 'r_mean': 1.5} for i in range(40)]
    payload = {**_weekly_payload(), 'rules': rules}
    for message in templates.workbench_weekly(payload):
        for field in message.fields:
            assert len(field.value) <= 1024, field.name
        total = len(message.title) + sum(len(f.name) + len(f.value) for f in message.fields)
        assert total <= 6000


def test_weekly_rule_line_without_mean_renders_dash():
    """触发但未成交的规则行 r_mean 为 None：写 —，不写 NoneR。"""
    rules = [{'pool': 'core', 'rule_id': 'trend_reclaim', 'triggers': 3, 'plans': 2,
              'filled': 0, 'r_mean': None}]
    payload = {**_weekly_payload(), 'rules': rules}
    text = chr(10).join(f.value for f in templates.workbench_weekly(payload)[0].fields)
    assert '均值 —' in text
    assert 'None' not in text
