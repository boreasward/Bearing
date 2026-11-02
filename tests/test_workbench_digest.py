"""Digest content and Discord templates for the daily workbench push (design §7.2)."""
import json
from datetime import date
from decimal import Decimal as D

from investment_assistant.data.calendar import add_trading_days
from investment_assistant.notify import templates
from investment_assistant.review import push_digest as digest

SESSION = date(2026, 10, 22)
URL = 'http://localhost:8787'


def plan_row(signal_date=SESSION, ticker='NVDA', **over):
    row = {'ticker': ticker, 'signal_date': signal_date, 'kind': 'rule', 'plan_version': 'plan-1', 'status': 'ok',
           'entry_low': D('98.5'), 'entry_high': D('101.5'), 'stop': D('94'), 'target1': D('112'),
           'valid_from': date(2026, 10, 23), 'valid_until': date(2026, 10, 27),
           'sizing': {'status': 'sized', 'shares': 166, 'risk_amount': D('1000'),
                      'snapshot_as_of': date(2026, 10, 20)},
           'invalid_reasons': [], 'warnings': []}
    row.update(over)
    return row


def unit(ticker='NVDA', plan=None, brief_summary='解读：量价配合。', signal_date=SESSION, agent=None,
         source=None):
    return {'ticker': ticker, 'signal_date': signal_date, 'proposed_action': 'buy', 'rule_ids': ['trend_reclaim'],
            'labels': [], 'source': source,
            'plan': plan,
            'agent': agent,
            'brief': ({'status': 'fallback', 'output': {'summary': brief_summary}} if brief_summary else None)}


def holding(ticker='NVDA', **over):
    row = {'ticker': ticker, 'quantity': 10, 'avg_cost': 100, 'plan_id': 'p1', 'stop': 94, 'mode': 'initial',
           't1_reached': False, 'target1': 112, 'last_close': 100.0, 'to_stop_pct': -0.06,
           'to_target_pct': 0.12, 'next_earnings': None}
    row.update(over)
    return row


def build(units=(), holdings=(), summary=None, last_push_failed=False, extended_plans=0, market=None,
          agent_summary=None, screen=None, plan_updates=None, retro=None, fetch_status=None, holding_status=None,
          merged=None, session_units=None, portfolio=None):
    return digest.build(session=SESSION, units=list(units), holdings=list(holdings),
                        summary=summary or {'errors': [], 'earnings': {}},
                        last_push_failed=last_push_failed, public_url=URL,
                        extended_plans=extended_plans, market=market, agent_summary=agent_summary,
                        screen=screen, plan_updates=plan_updates, retro=retro, fetch_status=fetch_status,
                        holding_status=holding_status, merged=merged, session_units=session_units,
                        portfolio=portfolio)


def agent_ok(ticker='NVDA', summary='回踩修复放量', first_risk='财报临近'):
    plan = plan_row(ticker=ticker, kind='agent', plan_version='agent-1')
    return {'id': 'r1', 'status': 'ok', 'model': 'gpt-test', 'agent_version': 'agent-1',
            'output': {'action': 'buy', 'summary': summary,
                       'levels': {k: {'value': str(plan[k]), 'refs': ['call:1']}
                                  for k in ('entry_low', 'entry_high', 'stop', 'target1')},
                       'thesis': [{'text': 't', 'refs': ['call:1']}]},
            'counter': ({'status': 'ok', 'risks': [{'text': first_risk, 'refs': ['call:1']}], 'dropped': 0}
                        if first_risk else None),
            'plan': plan, 'input_tokens': 10, 'output_tokens': 5, 'created_at': '2026-10-22T23:00:00+00:00'}


def all_text(messages):
    return '\n'.join(f.value for m in messages for f in m.fields)


def test_digest_plan_carries_action_rules_funding_and_contra():
    u = unit(plan=plan_row())
    u['brief']['output']['contra'] = [{'text': '反证：量能不足'}]
    dg = build(units=[u])
    entry = dg['plans'][0]
    assert entry['action'] == 'buy' and entry['rule_ids'] == ['trend_reclaim']
    assert entry['plan_version'] == 'plan-1' and entry['snapshot_as_of'] == '2026-10-20'
    assert '反证：量能不足' in entry['contra']
    text = all_text(templates.workbench_digest(dg))
    for needle in ('买入', 'trend_reclaim · plan-1', '资金日 2026-10-20', '反证：量能不足'):
        assert needle in text, needle


def test_holding_alerts_today():
    summary = {'errors': [], 'earnings': {},
               'tickers': {'NVDA': {'candidates': ['plan_stop']}}}
    dg = build(holdings=[holding()], summary=summary)
    assert dg['holdings'][0]['plan_alerts'] == ['plan_stop']
    text = all_text(templates.workbench_digest(dg))
    assert 'plan_stop' in text and '核对券商止损单' in text
    target = build(holdings=[holding()],
                   summary={'errors': [], 'earnings': {}, 'tickers': {'NVDA': {'candidates': ['plan_target']}}})
    assert target['holdings'][0]['plan_alerts'] == ['plan_target']
    assert '卖出一半' in all_text(templates.workbench_digest(target))


def test_digest_sections():
    dg = build(units=[unit(plan=plan_row())], holdings=[holding()])
    assert {'session', 'overview', 'plans', 'holdings', 'pending', 'errors'} <= set(dg)
    assert dg['overview']['new_plans'] == 1 and dg['overview']['holdings'] == 1
    assert dg['plans'][0]['ticker'] == 'NVDA' and dg['plans'][0]['shares'] == 166
    assert dg['holdings'][0]['ticker'] == 'NVDA'


def test_digest_no_plans_still_sends():
    dg = build()
    assert dg['plans'] == []
    assert '今日无新计划' in all_text(templates.workbench_digest(dg))


def test_digest_marks_last_push_failed():
    dg = build(last_push_failed=True)
    assert dg['overview']['last_push_failed'] is True
    assert '上次推送失败' in all_text(templates.workbench_digest(dg))


def test_plan_field_text():
    dg = build(units=[unit(plan=plan_row())])
    text = all_text(templates.workbench_digest(dg))
    for needle in ('区间', '止损', 'T1', '证据：未验证', URL):
        assert needle in text, needle


def test_field_truncated_to_1024_and_message_under_6000():
    units = [unit(ticker=f'T{i:03d}', plan=plan_row(ticker=f'T{i:03d}'), brief_summary='长' * 5000)
             for i in range(30)]
    for message in templates.workbench_digest(build(units=units)):
        for field in message.fields:
            assert len(field.value) <= 1024
        total = len(message.title) + sum(len(f.name) + len(f.value) for f in message.fields) + len(message.footer)
        assert total <= 6000


def test_overflow_says_rest_in_system():
    units = [unit(ticker=f'T{i:03d}', plan=plan_row(ticker=f'T{i:03d}'),
                  brief_summary='很长的解读摘要' * 20) for i in range(80)]
    text = all_text(templates.workbench_digest(build(units=units)))
    assert '其余' in text and '见系统' in text


def test_holding_earnings_eve_reminder():
    eve = add_trading_days(SESSION, 1).isoformat()
    dg = build(holdings=[holding(next_earnings=eve)])
    assert dg['holdings'][0]['earnings_eve'] is True
    assert '财报' in all_text(templates.workbench_digest(dg))


def test_expiring_plan_listed_in_pending():
    expiring = plan_row(valid_until=add_trading_days(SESSION, 1))
    dg = build(units=[unit(plan=expiring)])
    assert dg['pending']['expiring'][0]['ticker'] == 'NVDA'


def test_digest_collects_errors():
    dg = build(summary={'errors': ['NVDA bars: provider down'], 'earnings': {'MU': 'error: boom'}})
    assert any('provider down' in e for e in dg['errors'])
    assert any('MU' in e for e in dg['errors'])


def test_closed_day_message():
    messages = templates.workbench_closed(date(2026, 10, 24))
    assert len(messages) == 1
    assert '纽约休市，未运行' in messages[0].title


# --- P2: market context, extended count, errors by source ---

def market_row(**over):
    row = {'id': 'r1', 'session': SESSION, 'calc_version': 'regime-1', 'label': 'risk_off',
           'breadth': {'tickers': 40, 'eligible_50': 40, 'above_50': 20, 'pct_50': 0.5,
                       'eligible_200': 40, 'above_200': 12, 'pct_200': 0.3,
                       'eligible_52w': 38, 'new_highs': 3, 'new_lows': 1},
           'sectors': {'SMH': {'rs20': -0.12, 'rs60': -0.2, 'state': 'weak'},
                       'XLK': {'rs20': 0.03, 'rs60': -0.01, 'state': 'mixed'},
                       'QQQ': None},
           'macro': {'BAMLH0A0HYM2': {'value': D('3.2'), 'obs_date': SESSION, 'change': D('0.1'),
                                     'change_from': SESSION},
                     'T10Y2Y': {'value': D('-0.1'), 'obs_date': SESSION, 'change': D('-0.05'),
                                'change_from': SESSION}},
           'inputs': {}, 'created_at': None}
    row.update(over)
    return row


def ctx(**over):
    context = {'context_version': 'context-2', 'pool_tier': 'core',
               'regime': {'id': 'r1', 'label': 'risk_off'},
               'sector': {'etf': 'SMH', 'rs20': -0.12, 'rs60': -0.2, 'state': 'weak'},
               'next_earnings': date(2026, 11, 20)}
    context.update(over)
    return context


def test_digest_market_field_text():
    dg = build(market=market_row())
    assert dg['overview']['market']['label'] == 'risk_off'
    text = all_text(templates.workbench_digest(dg))
    for needle in ('区制 防守', '站上 MA50 50.0%', 'MA200 30.0%', '新高 3 / 新低 1',
                   'SMH -12.0% 弱', 'XLK +3.0% 分化', 'QQQ 数据不足',
                   'HY OAS 3.2（20 期 +0.1）', '2s10s -0.1（20 期 -0.05）'):
        assert needle in text, needle


def test_digest_fred_not_configured_text():
    market = market_row(macro={'BAMLH0A0HYM2': None, 'T10Y2Y': None},
                        inputs={'macro_status': {'BAMLH0A0HYM2': 'not_configured', 'T10Y2Y': 'not_configured'}})
    assert 'FRED 未配置' in all_text(templates.workbench_digest(build(market=market)))
    assert '市场环境未生成' in all_text(templates.workbench_digest(build(market=None)))


def test_digest_fred_failed_is_not_called_unconfigured():
    market = market_row(macro={'BAMLH0A0HYM2': None, 'T10Y2Y': None},
                        inputs={'macro_status': {'BAMLH0A0HYM2': 'error', 'T10Y2Y': 'error'}})
    text = all_text(templates.workbench_digest(build(market=market)))
    assert 'FRED 采集失败' in text and 'FRED 未配置' not in text
    # A snapshot without recorded status (or with no stored rows) says only that there is no data.
    for inputs in ({}, {'macro_status': {'BAMLH0A0HYM2': 'ok', 'T10Y2Y': 'ok'}}):
        text = all_text(templates.workbench_digest(build(market=market_row(
            macro={'BAMLH0A0HYM2': None, 'T10Y2Y': None}, inputs=inputs))))
        assert 'FRED 无数据' in text and 'FRED 未配置' not in text


def test_plan_line_has_regime_earnings_sector():
    plan = plan_row()
    plan['inputs'] = {'context': ctx()}
    dg = build(units=[unit(plan=plan)])
    assert dg['plans'][0]['regime_label'] == 'risk_off'
    assert dg['plans'][0]['next_earnings'] == '2026-11-20'
    text = all_text(templates.workbench_digest(dg))
    assert '区制 防守' in text and '下次财报 2026-11-20' in text and '行业 SMH 弱' in text

    bare = build(units=[unit(plan=plan_row())])
    bare_text = all_text(templates.workbench_digest(bare))
    assert '下次财报 未知' in bare_text and '行业 未映射' in bare_text


def test_extended_count_in_overview():
    dg = build(extended_plans=3)
    assert dg['overview']['extended_plans'] == 3
    assert '扩展池新计划 3 份（不推送）' in all_text(templates.workbench_digest(dg))
    silent = build()
    assert '扩展池新计划' not in all_text(templates.workbench_digest(silent))


def test_core_empty_overview_text():
    dg = build(summary={'errors': ['core_empty'], 'earnings': {}})
    assert dg['overview']['core_empty'] is True
    assert '核心池为空，只推送持仓' in all_text(templates.workbench_digest(dg))


def test_errors_grouped_by_source():
    summary = {'errors': ['MU bars: down'], 'earnings': {},
               'sources': {'bars': ['MU'], 'news': [], 'earnings': [], 'sec': [], 'etf_bars': [], 'fred': [],
                           'regime': []}}
    dg = build(summary=summary)
    assert dg['errors_by_source']['bars'] == ['MU']
    assert 'budget' not in dg['errors_by_source']                 # v2.8 deleted the batch budget
    messages = templates.workbench_digest(dg)
    names = {f.name for m in messages for f in m.fields}
    assert '日线' in names and '耗时' not in names and '资讯' not in names
    assert 'MU' in all_text(messages)


def test_digest_limits_with_100_ticker_summary():
    failed = [f'T{i:03d}' for i in range(100)]
    summary = {'errors': [f'{t} bars: down' for t in failed], 'earnings': {},
               'sources': {'bars': failed, 'news': failed, 'earnings': failed, 'sec': ['all'],
                           'etf_bars': ['XLK'], 'fred': ['T10Y2Y'], 'regime': []}}
    dg = build(summary=summary)
    for message in templates.workbench_digest(dg):
        for field in message.fields:
            assert len(field.value) <= 1024
        total = len(message.title) + sum(len(f.name) + len(f.value) for f in message.fields) + len(message.footer)
        assert total <= 6000


# --- agent lines in the daily push (P3 §10.2) ---

def test_agent_line_ok():
    u = unit(plan=plan_row(), agent=agent_ok())
    text = all_text(templates.workbench_digest(build(units=[u])))
    assert 'Agent（gpt-test）买入 · 区间 98.5–101.5 · 止损 94 · T1 112 · 166 股；回踩修复放量' in text
    assert '反方：财报临近' in text


def test_agent_line_pass():
    agent = {'id': 'r2', 'status': 'pass', 'model': 'gpt-test', 'agent_version': 'agent-1',
             'output': {'action': 'pass', 'summary': '趋势不明'}, 'counter': None, 'plan': None,
             'input_tokens': 5, 'output_tokens': 5, 'created_at': '2026-10-22T23:00:00+00:00'}
    text = all_text(templates.workbench_digest(build(units=[unit(plan=plan_row(), agent=agent)])))
    assert 'Agent（gpt-test）不建议买入；趋势不明' in text


def test_agent_line_missing_reason():
    agent = {'id': 'r3', 'status': 'skipped', 'reason': 'quota_exhausted',
             'created_at': '2026-10-22T23:00:00+00:00'}
    text = all_text(templates.workbench_digest(build(units=[unit(plan=plan_row(), agent=agent)])))
    assert 'Agent 未给出计划（额度用尽）' in text      # v2.5 §10.3


def test_agent_only_unit_listed():
    u = unit(plan=None, agent=agent_ok())
    dg = build(units=[u])
    assert len(dg['plans']) == 1
    item = dg['plans'][0]
    assert item['kind'] == 'agent' and item['entry_low'] is None and item['shares'] is None
    text = all_text(templates.workbench_digest(dg))
    assert 'Agent（gpt-test）买入 · 区间 98.5–101.5' in text
    assert text.count('区间') == 1          # 规则区间行不写，只有 Agent 行里的区间


def test_overview_agent_counts_and_quota_warning():
    units = [unit(plan=plan_row(), agent=agent_ok()),
             unit(ticker='MU', plan=plan_row(ticker='MU'),
                  agent={'id': 'r', 'status': 'pass', 'model': 'gpt-test', 'output': {'summary': 's'},
                         'counter': None, 'plan': None, 'input_tokens': 0, 'output_tokens': 0,
                         'created_at': 'c'}),
             unit(ticker='AMD', plan=plan_row(ticker='AMD'), agent=None)]
    dg = build(units=units, agent_summary={'quota_exhausted': True})
    assert dg['overview']['agent'] == {'ok': 1, 'pass': 1, 'missing': 1}
    assert dg['overview']['agent_quota_exhausted'] is True
    text = all_text(templates.workbench_digest(dg))
    assert 'Agent 建议 1 · 不建议 1 · 未给出 1' in text
    assert '⚠️ Agent 额度用尽，其余单元未运行' in text


def test_overview_agent_counts_only_session_buy_units():
    """Sell/watch, conflict and earlier still-open units never enter the agent stage: not 'missing'."""
    units = [unit(plan=plan_row(), agent=agent_ok()),
             {**unit(ticker='AAPL'), 'proposed_action': 'sell'},
             {**unit(ticker='MSFT'), 'proposed_action': 'watch'},
             {**unit(ticker='MU'), 'labels': ['conflict']},
             unit(ticker='AMD', signal_date=date(2026, 10, 21), plan=plan_row(ticker='AMD')),
             unit(ticker='TSM', plan=plan_row(ticker='TSM'), agent=None)]
    assert build(units=units)['overview']['agent'] == {'ok': 1, 'pass': 0, 'missing': 1}


def test_digest_limits_with_agent_lines():
    long_text = '风' * 900
    units = [unit(ticker=f'T{i:02d}', plan=plan_row(ticker=f'T{i:02d}'),
                  agent=agent_ok(ticker=f'T{i:02d}', summary=long_text, first_risk=long_text))
             for i in range(15)]
    for message in templates.workbench_digest(build(units=units)):
        for field in message.fields:
            assert len(field.value) <= 1024
        total = len(message.title) + sum(len(f.name) + len(f.value) for f in message.fields) + len(message.footer)
        assert total <= 6000


def test_agent_line_without_snapshot_omits_shares():
    no_equity = plan_row(kind='agent', plan_version='agent-1',
                         sizing={'status': 'no_equity', 'shares': None, 'risk_amount': None,
                                 'snapshot_as_of': None})
    agent = {**agent_ok(), 'plan': no_equity}
    text = all_text(templates.workbench_digest(build(units=[unit(plan=plan_row(), agent=agent)])))
    assert 'None' not in text
    assert '缺资金快照，无法计算' in text


# --- agent-1 briefs are not repeated in plan lines (P4 §7.5) ---

def test_agent_brief_not_repeated_in_plan_line():
    u = unit(plan=plan_row())
    u['brief'] = {'status': 'ok', 'prompt_version': 'agent-1',
                  'output': {'summary': '回踩修复放量', 'contra': [{'text': '财报临近', 'refs': ['call:1']}]}}
    out = build(units=[u])
    assert out['plans'][0]['brief_summary'] is None
    assert out['plans'][0]['contra'] is None


def test_legacy_brief_still_attached():
    u = unit(plan=plan_row())
    u['brief'] = {'status': 'ok', 'prompt_version': 'workbench-brief-1',
                  'output': {'summary': '解读：量价配合。', 'contra': []}}
    out = build(units=[u])
    assert out['plans'][0]['brief_summary'] == '解读：量价配合。'
    assert out['plans'][0]['contra'] == ''


def screen_report(*, setups=12, **over):
    setups_rows = [{'ticker': f'S{i:02d}', 'rs': 0.05 - i * 0.001, 'atr_pct': 0.04, 'rank': 3 + i}
                   for i in range(setups)]
    row = {'session': SESSION.isoformat(), 'calc_version': 'screen-1', 'status': 'success', 'error': None,
           'universe': {'name': 'nasdaq100', 'as_of': '2026-10-22', 'stale': False},
           'counts': {'universe': 100, 'data_skipped': 0, 'illiquid': 0, 'earnings_soon': 0,
                      'no_setup': 50, 'trigger': 2, 'setup': 12, 'selected': 14},
           'members': 20, 'cap': 40, 'evicted': [], 'removed': ['GONE'], 'flags': [],
           'triggers': [
               {'ticker': 'U00', 'rule_ids': ['trend_reclaim'], 'rs': 0.12, 'review': 'plan',
                'plan': {'entry_low': '98.5', 'entry_high': '101.5', 'stop': '94', 'target1': '112',
                         'shares': 166, 'sizing_status': 'sized'}, 'invalid_reasons': []},
               {'ticker': 'U01', 'rule_ids': ['volume_breakout'], 'rs': 0.10, 'review': 'invalid',
                'plan': None, 'invalid_reasons': ['earnings']}],
           'setups': setups_rows[:10], 'setups_more': max(0, setups - 10)}
    row.update(over)
    return row


def test_screen_message_shapes():
    msgs = templates.workbench_digest(build(screen=screen_report()))
    assert len(msgs) == 5
    screen_msg = msgs[4]
    assert screen_msg.title.startswith('🔎 筛选报告')
    fields = {f.name: f.value for f in screen_msg.fields}
    assert 'U00' in fields['今日触发 → 复核']
    assert '入场 98.5–101.5 · 止损 94 · T1 112 · 建议 166 股' in fields['今日触发 → 复核']
    assert '作废 earnings' in fields['今日触发 → 复核']
    assert 'S00' in fields['形态候选（等待触发）'] and 'S09' in fields['形态候选（等待触发）']
    assert 'S10' not in fields['形态候选（等待触发）']
    assert '另 2 只见页面' in fields['形态候选（等待触发）']
    assert '移出 1' in fields['概览'] and '成员 20/40' in fields['概览']


def test_screen_message_without_equity_snapshot():
    # The real database had no equity snapshot: shares is None and must not print as "None".
    report = screen_report()
    report['triggers'][0]['plan'].update(shares=None, sizing_status='no_equity')
    fields = {f.name: f.value for f in templates.workbench_digest(build(screen=report))[4].fields}
    line = fields['今日触发 → 复核']
    assert 'T1 112 · 缺资金快照，无法计算股数' in line
    assert 'None' not in line and '建议' not in line


def test_screen_message_none_and_error():
    msgs = templates.workbench_digest(build())
    assert len(msgs) == 5
    fields = {f.name: f.value for f in msgs[4].fields}
    assert '今日无筛选记录' in fields['概览']
    failed = screen_report(status='error', error='benchmark_missing')
    failed['triggers'] = []
    failed['setups'] = []
    failed['setups_more'] = 0
    msgs = templates.workbench_digest(build(screen=failed))
    fields = {f.name: f.value for f in msgs[4].fields}
    assert '筛选未运行：benchmark_missing' in fields['概览']
    assert '沿用成员 20 只' in fields['概览']


def test_screen_plan_marked_in_new_plans():
    msgs = templates.workbench_digest(build(units=[unit(plan=plan_row(), source='screen',
                                                        ticker='U00')]))
    plans_msg = msgs[1]
    names = [f.name for f in plans_msg.fields]
    assert '【筛选】U00' in names


def test_screen_message_respects_limits():
    huge = screen_report(setups=40)
    huge['setups'] = huge['setups'][:10]
    huge['triggers'] = [
        {'ticker': f'TRIGLONGNAME{i}', 'rule_ids': ['trend_reclaim', 'volume_breakout'], 'rs': 0.1,
         'review': 'plan', 'plan': None, 'invalid_reasons': ['earnings', 'no_room']}
        for i in range(20)]
    msgs = templates.workbench_digest(build(screen=huge))
    screen_msg = msgs[4]
    for f in screen_msg.fields:
        assert len(f.value) <= 1024, f.name
    total = len(screen_msg.title) + sum(len(f.name) + len(f.value) + 8 for f in screen_msg.fields)
    assert total <= 6000


def test_numeric_padding_is_trimmed_in_every_message():
    # NUMERIC(…, 6) columns come back as D('250.770000'); the push must read 250.77, as the page does.
    padded = {'entry_low': D('250.770000'), 'entry_high': D('264.370000'), 'stop': D('230.380000'),
              'target1': D('311.940000')}
    plan = plan_row(**padded, sizing={'status': 'sized', 'shares': 10, 'risk_amount': D('1000.000000'),
                                      'snapshot_as_of': date(2026, 10, 20)})
    agent = agent_ok()
    agent['plan'].update(padded)
    report = screen_report()
    report['triggers'][0]['plan'].update(padded)
    report['triggers'][0]['rs'] = D('0.217239')
    report['setups'][0].update(rs=D('0.334444'), atr_pct=D('0.045600'))
    market = market_row(macro={
        'BAMLH0A0HYM2': {'value': D('3.400000'), 'obs_date': SESSION, 'change': D('0.6'), 'change_from': SESSION},
        'T10Y2Y': {'value': D('0.310000'), 'obs_date': SESSION, 'change': D('0'), 'change_from': SESSION}})
    msgs = templates.workbench_digest(build(units=[unit(plan=plan, agent=agent)], market=market, screen=report))
    text = all_text(msgs)
    assert '区间 250.77–264.37 · 止损 230.38 · T1 311.94' in text                  # rule plan line
    assert '规则基线：区间 250.77–264.37 · 止损 230.38 · T1 311.94 · 10 股；' in text   # §10.3: no risk amount
    assert 'Agent（gpt-test）买入 · 区间 250.77–264.37 · 止损 230.38 · T1 311.94' in text
    assert '入场 250.77–264.37 · 止损 230.38 · T1 311.94 · 建议 166 股' in text      # screen trigger
    assert 'U00 · trend_reclaim · RS 0.217 ·' in text
    assert 'S00 · RS 0.334 · ATR 4.56%' in text
    assert 'HY OAS 3.4（20 期 +0.6）' in text and '2s10s 0.31（' in text
    assert '0000' not in text


# --- v2.5 §10.3: agent line first, rule baseline after; plan updates (D25) ---

def agent2_ok(ticker='NVDA', version_no=0, summary='回踩修复放量', counter_text='量能未放大'):
    plan = plan_row(ticker=ticker, kind='agent', plan_version='agent-2', entry_low=D('99'), entry_high=D('102'),
                    stop=D('95'), target1=D('115'))
    return {'id': 'r9', 'status': 'ok', 'model': 'gpt-test', 'agent_version': 'agent-2',
            'output': {'action': 'buy', 'summary': summary,
                       'thesis': [{'text': '放量突破', 'refs': ['call:1']}]},
            'counter': {'status': 'ok', 'counter': [{'thesis': 1, 'text': counter_text, 'refs': ['call:1']}],
                        'risks': [{'text': '大盘走弱', 'refs': ['call:1']}], 'dropped': 0, 'uncountered': []},
            'plan': plan, 'input_tokens': 10, 'output_tokens': 5, 'created_at': '2026-10-22T23:00:00+00:00',
            'chain': {'version_no': version_no, 'head_plan_id': 'p9', 'state': 'entry', 'mode': 'entry',
                      'basis': None, 'latest_review': None}}


def plan_field(dg, ticker='NVDA'):
    msgs = templates.workbench_digest(dg)
    return next(f.value for f in msgs[1].fields if f.name == ticker)


def test_agent_line_before_rule_baseline():
    dg = build(units=[unit(plan=plan_row(), agent=agent2_ok())])
    agent = dg['plans'][0]['agent']
    assert (agent['version_no'], agent['first_counter'], agent['legacy']) == (0, '量能未放大', False)
    value = plan_field(dg)
    agent_line = 'Agent 第 0 版：区间 99–102 · 止损 95 · T1 115 · 166 股 · 回踩修复放量 · 反证：量能未放大'
    baseline = '规则基线：区间 98.5–101.5 · 止损 94 · T1 112 · 166 股'
    assert agent_line in value and baseline in value
    assert '风险' not in value                                     # 规则基线只到股数
    assert value.index('Agent 第 0 版') < value.index('规则基线')
    assert value.startswith('Agent 第 0 版')
    assert '证据：未验证' in value


def test_agent_missing_writes_reason_then_baseline():
    agent = {'id': 'r3', 'status': 'error', 'reason': 'model_error', 'created_at': '2026-10-22T23:00:00+00:00'}
    value = plan_field(build(units=[unit(plan=plan_row(), agent=agent)]))
    assert value.startswith('Agent 未给出计划（')
    assert value.index('Agent 未给出计划') < value.index('规则基线：区间 98.5–101.5')


def test_legacy_agent1_line_marked_and_first():
    value = plan_field(build(units=[unit(plan=plan_row(), agent={**agent_ok(), 'legacy': True})]))
    assert value.startswith('Agent（gpt-test）买入 · 区间 98.5–101.5')
    assert 'agent-1（旧版，不参与复查）' in value
    assert value.index('agent-1（旧版') < value.index('规则基线')


def test_unit_without_agent_keeps_plain_rule_line():
    value = plan_field(build(units=[unit(plan=plan_row(), agent=None)]))
    assert '规则基线' not in value and 'Agent' not in value
    assert '区间 98.5–101.5 · 止损 94 · T1 112' in value and '仓位 166 股 · 风险 1000' in value


def test_agent2_brief_not_repeated_in_plan_line():
    """Task 6 review: an agent-2 brief (summary + first counter) already sits in the agent line."""
    u = unit(plan=plan_row(), agent=agent2_ok())
    u['brief'] = {'status': 'ok', 'prompt_version': 'agent-2',
                  'output': {'summary': '回踩修复放量', 'contra': [{'text': '量能未放大', 'refs': ['call:1']}]}}
    dg = build(units=[u])
    assert dg['plans'][0]['brief_summary'] is None and dg['plans'][0]['contra'] is None
    value = plan_field(dg)
    assert value.count('回踩修复放量') == 1 and value.count('量能未放大') == 1


def review(outcome, *, ticker='NVDA', decided_by='agent', reason='理由', mode='entry', pool='main',
           new_plan_id=None, from_version=0, to_version=None, changes=(), levels=None, valid_until=None):
    return {'ticker': ticker, 'signal_date': SESSION, 'session': SESSION, 'trigger': 'batch', 'mode': mode,
            'outcome': outcome, 'decided_by': decided_by, 'reason': reason, 'new_plan_id': new_plan_id,
            'pool': pool, 'from_version': from_version, 'to_version': to_version, 'changes': list(changes),
            'levels': levels, 'valid_until': valid_until}


LEVELS = {'entry_low': D('99.000000'), 'entry_high': D('102'), 'stop': D('97'), 'target1': D('115')}


def update_rows():
    return [
        review('maintain', ticker='AAA', decided_by='host'),
        review('revise', ticker='NVDA', new_plan_id='p1', to_version=1, levels=LEVELS,
               valid_until=date(2026, 10, 28), changes=['止损上移到 97', '论点补充 8-K 订单']),
        review('revise', ticker='MU', reason='changes_mismatch'),                     # 修订失败：无新版
        review('skipped', ticker='AMD', reason='stage_budget'),
        review('error', ticker='AMD', reason='model_error'),
        review('invalidate', ticker='TSM', decided_by='host', reason='失效条件成立：收盘跌破 112'),
        review('revise', ticker='EXT', pool='extended', new_plan_id='p2', to_version=1, levels=LEVELS,
               valid_until=date(2026, 10, 28), changes=['x']),                     # 扩展池入场期：只影子跟踪
        review('invalidate', ticker='EXH', pool='extended', mode='holding', reason='论点被证伪'),  # 持仓链总推
    ]


def test_plan_updates_only_revise_with_version_and_invalidate():
    dg = build(plan_updates=update_rows())
    assert [(u['ticker'], u['kind']) for u in dg['plan_updates']] == \
        [('NVDA', 'revise'), ('TSM', 'invalidate'), ('EXH', 'invalidate')]
    nvda = dg['plan_updates'][0]
    assert nvda == {'ticker': 'NVDA', 'kind': 'revise', 'from_version': 0, 'to_version': 1,
                    'changes': ['止损上移到 97', '论点补充 8-K 订单'], 'levels': LEVELS,
                    'valid_until': date(2026, 10, 28), 'decided_by': 'agent', 'reason': '理由',
                    'mode': 'entry', 'via': None, 'exit_reason': None, 'retrigger': None}
    assert build()['plan_updates'] == []


def test_overview_counts_plan_updates():
    dg = build(plan_updates=update_rows())
    assert dg['overview']['plan_updates'] == {'revise': 1, 'invalidate': 2}
    assert '计划更新 修订 1 · 作废 2' in all_text(templates.workbench_digest(dg))
    assert build(plan_updates=[])['overview']['plan_updates'] == {'revise': 0, 'invalidate': 0}
    assert '计划更新 修订 0 · 作废 0' in all_text(templates.workbench_digest(build(plan_updates=[])))


def test_overview_omits_plan_updates_without_timeline():
    dg = build()                                                   # 迁移 026 未执行：plan_updates=None
    assert 'plan_updates' not in dg['overview'] and dg['plan_updates'] == []
    assert '计划更新' not in all_text(templates.workbench_digest(dg))


def test_plan_update_field_texts():
    msgs = templates.workbench_digest(build(plan_updates=update_rows()))
    values = [f.value for f in msgs[2].fields]
    assert values[0] == ('NVDA 第 0 → 1 版：止损上移到 97；论点补充 8-K 订单 · '
                         '新价位 区间 99–102 · 止损 97 · T1 115 · 有效期至 2026-10-28')
    assert values[1] == 'TSM 作废（系统规则）：失效条件成立：收盘跌破 112'
    assert values[2] == 'EXH 退出指令（论点被证伪）'              # v2.9 §11: a holding-phase invalidation is an exit


def test_invalidated_chain_says_so_before_baseline():
    agent = agent2_ok(version_no=1)
    agent['chain'].update(state='invalidated', latest_review={'outcome': 'invalidate', 'reason': '失效条件成立：收盘跌破 95'})
    value = plan_field(build(units=[unit(plan=plan_row(), agent=agent)]))
    assert value.startswith('Agent 已作废此计划（第 1 版）：失效条件成立：收盘跌破 95；规则基线：')


# --- v2.8 D3 / §13.1: 「复盘卡 上次推送后新增 a · 待审定 b」 and the data line ---

def test_retro_line_since_last_push():
    shown = build(retro={'new_cards': 2, 'to_review': 3})
    assert shown['overview']['retro'] == {'new_cards': 2, 'to_review': 3}
    lines = templates.workbench_digest(shown)[0].fields[0].value.split('\n')
    assert '复盘卡 上次推送后新增 2 · 待审定 3' in lines
    only_pending = build(retro={'new_cards': 0, 'to_review': 4})
    assert '复盘卡 上次推送后新增 0 · 待审定 4' in all_text(templates.workbench_digest(only_pending))
    for quiet in (build(retro={'new_cards': 0, 'to_review': 0}), build(retro=None), build()):      # a and b both 0
        assert '复盘卡' not in all_text(templates.workbench_digest(quiet))
    assert 'retro' not in build()['overview']


def _family(name, status, ok, total):
    return {'family': name, 'status': status, 'ok_count': ok, 'target_count': total}


def test_fetch_degraded_line():
    status = [_family('bars', 'partial', 118, 131), _family('news', 'success', 100, 100),
              _family('sec_filings', 'failed', 0, 40), _family('fred', 'skipped', 0, 2),
              _family('universe', 'partial', 0, 1)]
    dg = build(fetch_status=status)
    assert [f['family'] for f in dg['overview']['fetch_degraded']] == ['bars', 'sec_filings', 'universe']
    overview = templates.workbench_digest(dg)[0].fields[0].value
    assert ('数据：日线 118/131（13 个目标没拿到，见运行详情）；SEC 申报 0/40（40 个目标没拿到，见运行详情）；'
            '纳指 100 范围 0/1（1 个目标没拿到，见运行详情）') in overview
    many = build(fetch_status=[_family(f, 'partial', 1, 2) for f in ('bars', 'news', 'sec_filings', 'fred')])
    line = next(l for l in templates.workbench_digest(many)[0].fields[0].value.split('\n') if l.startswith('数据：'))
    assert line.count('；') == 2                                  # at most three families
    for quiet in (build(fetch_status=[_family('bars', 'success', 3, 3), _family('fred', 'skipped', 0, 2)]),
                  build(fetch_status=[]), build(fetch_status=None), build()):
        assert '数据：' not in all_text(templates.workbench_digest(quiet))


# --- v2.9 §11: executable new plans, holding status lines, exit marker, no undecided units ---

def agent3_ok(ticker='MU', version_no=2, entry_type='zone_limit', retrigger=None, max_hold=10):
    plan = plan_row(ticker=ticker, kind='agent', plan_version='agent-3', entry_low=D('99'), entry_high=D('102'),
                    stop=D('95'), target1=D('115'), entry_type=entry_type, max_hold_sessions=max_hold,
                    valid_from=date(2026, 10, 23), valid_until=date(2026, 10, 27))
    return {'id': 'r10', 'status': 'ok', 'model': 'gpt-test', 'agent_version': 'agent-3',
            'output': {'action': 'buy', 'summary': '回踩修复放量', 'thesis': [{'text': '放量突破', 'refs': ['call:1']}]},
            'counter': {'status': 'ok', 'counter': [{'thesis': 1, 'text': '量能未放大', 'refs': ['call:1']}],
                        'risks': [], 'dropped': 0, 'uncountered': []},
            'plan': plan, 'input_tokens': 10, 'output_tokens': 5, 'created_at': '2026-10-22T23:00:00+00:00',
            'chain': {'version_no': version_no, 'head_plan_id': 'p9', 'state': 'entry', 'mode': 'entry',
                      'basis': None, 'latest_review': None, 'signal_date': date(2026, 10, 14),
                      'retrigger': retrigger}}


def test_new_plan_three_lines():
    dg = build(units=[unit(ticker='MU', plan=plan_row(ticker='MU'), agent=agent3_ok(retrigger={
        'from_version': 1, 'to_version': 2}))])
    lines = plan_field(dg, 'MU').split('\n')
    assert lines[0] == ('MU 10-14 第 2 版（再次触发，第 1 → 2 版）：区间限价 99–102 · 止损 95 · T1 115 · 166 股 · '
                        '有效至 2026-10-27')
    assert lines[1] == '开盘：区间内买 / 高开不追挂 102 / 低开 ≤95 作废、否则回到 99 买'
    assert lines[2].startswith('最长持有 10 天未到 1R 即退出 · 回踩修复放量 · 反证：量能未放大')
    assert '规则基线：区间 98.5–101.5 · 止损 94 · T1 112 · 166 股' in plan_field(dg, 'MU')     # after the Agent lines
    # next_open: the open case collapses to the first valid day
    nxt = plan_field(build(units=[unit(ticker='MU', plan=plan_row(ticker='MU'),
                                       agent=agent3_ok(entry_type='next_open', max_hold=7))]), 'MU').split('\n')
    assert nxt[0].startswith('MU 10-14 第 2 版：次日开盘 99–102') and '（再次触发' not in nxt[0]
    assert nxt[1] == '开盘：仅 10-23 开盘在 99–102 买，否则作废'
    assert nxt[2].startswith('最长持有 7 天未到 1R 即退出')


def test_new_plan_merged_baseline_follows():
    u = unit(ticker='MU', plan=plan_row(ticker='MU'), agent=agent3_ok())
    u['merged'] = [{'signal_date': date(2026, 10, 22), 'plan': plan_row(ticker='MU', entry_low=D('100'))}]
    value = plan_field(build(units=[u], merged={('MU', SESSION)}), 'MU')
    assert value.index('规则基线：') < value.index('今日再次触发的规则基线（2026-10-22）：区间 100–101.5')


def status_row(ticker='MU', instruction='hold', reason='趋势未变，继续持有', **over):
    row = {'ticker': ticker, 'session': SESSION, 'quantity': D('10'), 'avg_cost': D('100'), 'stop': D('94.8'),
           'target1': D('108'), 'held_sessions': 4, 'max_hold_sessions': 10, 'to_stop_pct': D('-5.20'),
           'to_t1_pct': D('8.10'), 'r_now': D('0.62'), 't1_reached': False, 'instruction': instruction,
           'reason': reason, 'exit_reason': 'stop_touched' if instruction == 'exit' else None}
    row.update(over)
    return row


def test_holding_section_pushes_maintain():
    dg = build(holdings=[holding('MU')], holding_status=[status_row()])
    assert dg['overview']['holdings'] == 1 and dg['holdings'][0]['instruction'] == 'hold'
    msgs = templates.workbench_digest(dg)
    holdings_msg = next(m for m in msgs if m.title.startswith('💼'))
    [field] = holdings_msg.fields
    assert field.value == 'MU 持有 4/10 天 · 距止损 −5.20% · 距 T1 +8.10% · 现 +0.6R · 继续持有：趋势未变，继续持有'
    # a position with no row (the review stage failed) falls back to the old fields and says so
    fallback = build(holdings=[holding('MU'), holding('AMD')], holding_status=[status_row()])
    text = all_text(templates.workbench_digest(fallback))
    assert fallback['overview']['holdings'] == 2 and '今日持仓状态未写入（每日复查失败），以下为现算' in text


def test_exit_marker():
    dg = build(holding_status=[status_row('MU', 'exit', '退出指令自 2026-10-22 起（止损触及），仍持有 10 股'),
                               status_row('AMD', 'hold'), status_row('TSM', 'no_plan', '无计划持仓（回撤规则看护）',
                                                                       to_stop_pct=None, to_t1_pct=None,
                                                                       r_now=None, max_hold_sessions=None)])
    values = {f.name: f.value for m in templates.workbench_digest(dg) if m.title.startswith('💼') for f in m.fields}
    assert values['MU'].startswith('⚠ MU 持有 4/10 天') and '次一交易日开盘卖出：退出指令自 2026-10-22 起' in values['MU']
    assert not values['AMD'].startswith('⚠')
    assert values['TSM'] == 'TSM 持有 4 天 · 无计划持仓（回撤规则看护）：无计划持仓（回撤规则看护）'


def test_plan_updates_exit_and_retrigger_text():
    rows = [
        review('invalidate', ticker='MU', decided_by='host', reason='止损触及（95.00）：应已按止损卖出；仍持有则次一交易日开盘卖出',
               mode='holding'),
        {**review('revise', ticker='NVDA', new_plan_id='p1', from_version=1, to_version=2, levels=LEVELS,
                  valid_until=date(2026, 10, 28), changes=['止损上移到 97']), 'via': 'retrigger',
         'retrigger': {'unit_signal_dates': ['2026-10-22'], 'rule_ids': ['volume_breakout']}},
        {**review('revise', ticker='TSM', new_plan_id='p2', from_version=0, to_version=1, levels=LEVELS,
                  valid_until=date(2026, 10, 28), changes=[]), 'via': 'retrigger',
         'retrigger': {'unit_signal_dates': ['2026-10-22'], 'rule_ids': ['volume_breakout']}},
    ]
    values = [f.value for f in templates.workbench_digest(build(plan_updates=rows))[2].fields]
    assert values[0] == 'MU 退出指令（止损触及（95.00）：应已按止损卖出；仍持有则次一交易日开盘卖出）'
    assert values[1].startswith('NVDA 第 1 → 2 版（再次触发）：止损上移到 97 · 新价位 区间 99–102')
    assert values[2].startswith('TSM 第 0 → 1 版（再次触发）：再次触发（放量突破，2026-10-22），沿用价位，有效期延至 2026-10-28')


def test_digest_has_no_undecided_units():
    dg = build(units=[unit(plan=plan_row())])
    assert 'undecided' not in dg['pending']
    text = all_text(templates.workbench_digest(dg))
    assert '未决定' not in text
    assert 'undecided' not in str(dg)


def test_pending_section_counts():
    dg = build(units=[unit('MU', plan=plan_row(ticker='MU'), agent=agent3_ok()),
                      unit('NVDA', plan=plan_row())],
               holding_status=[status_row('AMD'), status_row('TSM', 'exit', '退出指令自 10-21 起')])
    assert dg['pending']['entry'] == ['MU', 'NVDA'] and dg['pending']['exit'] == ['TSM']
    assert dg['pending']['holding'] == ['AMD', 'TSM'] and dg['overview']['pending'] == 3
    msgs = templates.workbench_digest(dg)
    pending = next(m for m in msgs if m.title.startswith('⏳'))
    first = pending.fields[0]
    assert first.name == '入场期可执行 2 · 持仓 2（退出指令 1）'
    assert first.value == '入场期：MU、NVDA\n持仓：AMD、TSM\n退出指令：TSM'
    assert '待处理 3 · 异常 0' in all_text(msgs)


def test_overview_counts_merged_units():
    merged_unit = unit('MU', plan=plan_row(ticker='MU'), agent=None)
    dg = build(units=[unit('NVDA', plan=plan_row(), agent=agent2_ok())], session_units=[
        merged_unit, unit('NVDA', plan=plan_row(), agent=agent2_ok())], merged={('MU', SESSION)})
    assert dg['overview']['agent'] == {'ok': 1, 'pass': 0, 'missing': 0, 'merged': 1}


def test_rule_baseline_alone_is_three_lines():
    """Review I2 (spec §11, D13): with no executable Agent plan the rule baseline is the plan, in the same format."""
    base = plan_row(ticker='MU', plan_version='plan-2', entry_type='zone_limit', max_hold_sessions=10)
    expected = ['MU 规则基线：区间限价 98.5–101.5 · 止损 94 · T1 112 · 166 股 · 有效至 2026-10-27',
                '开盘：区间内买 / 高开不追挂 101.5 / 低开 ≤94 作废、否则回到 98.5 买',
                '最长持有 10 天未到 1R 即退出 · 趋势回踩修复']
    plain = plan_field(build(units=[unit(ticker='MU', plan=base, brief_summary=None)]), 'MU')
    assert plain.split('；')[0].split('\n') == expected
    assert '规则基线：区间' not in plain.replace('MU 规则基线：区间限价', '')          # not repeated as a second line
    # an Agent pass / error / skip explanation follows the third line, on its own line
    err = {'id': 'r3', 'status': 'error', 'reason': 'model_error', 'created_at': '2026-10-22T23:00:00+00:00'}
    lines = plan_field(build(units=[unit(ticker='MU', plan=base, agent=err)]), 'MU').split('\n')
    assert lines[:3] == expected and lines[3].startswith('Agent 未给出计划（')
    # next_open keeps its one-line open case
    nxt = plan_row(ticker='MU', plan_version='plan-2', entry_type='next_open', max_hold_sessions=7)
    second = plan_field(build(units=[unit(ticker='MU', plan=nxt)]), 'MU').split('\n')[1]
    assert second == '开盘：仅 10-23 开盘在 98.5–101.5 买，否则作废'
    # plan-1 baselines (no execution fields) and an executable Agent plan keep the old shapes
    old = plan_field(build(units=[unit(ticker='MU', plan=plan_row(ticker='MU'))]), 'MU')
    assert '规则基线' not in old and old.startswith('动作 买入')
    with_agent = plan_field(build(units=[unit(ticker='MU', plan=base, agent=agent3_ok())]), 'MU')
    assert with_agent.split('\n')[0].startswith('MU 10-14 第 2 版：区间限价 99–102') and \
        '；规则基线：区间 98.5–101.5 · 止损 94 · T1 112 · 166 股' in with_agent


# ---------------------------------------------------------------- 🧩 组合检查 (v2.14 spec §7.4)

def portfolio_check(status='ready', *, commentary=True, **over):
    verdicts = [{'plan_id': 'p-nvda', 'ticker': 'NVDA', 'group': '半导体', 'verdict': 'ok', 'reasons': [], 'order': 1},
                {'plan_id': 'p-amd', 'ticker': 'AMD', 'group': '半导体', 'verdict': 'blocked',
                 'reasons': ['sector_cap'], 'order': None},
                {'plan_id': 'p-mu', 'ticker': 'MU', 'group': '半导体', 'verdict': 'blocked',
                 'reasons': ['max_per_sector', 'sector_unknown'], 'order': None},
                {'plan_id': 'p-ko', 'ticker': 'KO', 'group': '消费', 'verdict': 'deferred',
                 'reasons': ['deferred_by_cash'], 'order': 2},
                {'plan_id': 'p-pg', 'ticker': 'PG', 'group': '消费', 'verdict': 'ok', 'reasons': [], 'order': 3}]
    view = {'status': status, 'reason': None if status == 'ready' else 'Agent 不可用', 'session': SESSION,
            'cutoff_at': None, 'result': {'verdicts': verdicts, 'order': ['p-nvda', 'p-ko', 'p-pg'], 'conflicts': [],
                                          'exposure_by_sector': {}, 'params_used': {}, 'version': 'portfolio-check-1'},
            'commentary': ({'summary': '先做 NVDA，行业集中度留意半导体',
                            'ranking': [{'plan_id': 'p-nvda', 'rank': 1, 'reason': '半导体敞口还有空间'}],
                            'tradeoffs': [], 'hold_actions': []} if commentary else None)}
    view.update(over)
    return view


def test_digest_portfolio_field_counts_blocked_with_reasons_and_order():
    field = build(portfolio=portfolio_check())['portfolio']
    assert (field['status'], field['ok'], field['blocked'], field['deferred']) == ('ready', 2, 2, 1)
    assert field['blocked_items'] == [{'ticker': 'AMD', 'reasons': ['sector_cap']},
                                      {'ticker': 'MU', 'reasons': ['max_per_sector', 'sector_unknown']}]
    assert field['order'] == ['NVDA', 'KO', 'PG']                      # the host's fill order, by ticker
    assert field['summary'] == '先做 NVDA，行业集中度留意半导体' and field['first_reason'] == '半导体敞口还有空间'


def test_workbench_digest_sends_the_portfolio_message_right_after_the_new_plans():
    dg = build(units=[unit(plan=plan_row())], portfolio=portfolio_check())
    messages = templates.workbench_digest(dg)
    assert [m.title.split(' · ')[0] for m in messages][:3] == ['📊 工作台', '🆕 新计划', '🧩 组合检查']
    fields = {f.name: f.value for f in messages[2].fields}
    assert fields['结果'] == '可一起做 2 条 · 被拦 2 条 · 因现金暂缓 1 条'
    assert fields['被拦 (2)'] == 'AMD：超过行业集中度上限\nMU：超过同行业标的数量上限、行业未归类'
    assert fields['建议先后'] == 'NVDA → KO → PG'
    assert fields['一句理由'] == '先做 NVDA，行业集中度留意半导体；半导体敞口还有空间'


def test_portfolio_message_without_commentary_says_only_the_hard_result():
    dg = build(portfolio=portfolio_check('degraded', commentary=False))
    message = templates.workbench_digest(dg)[2]
    assert message.title.startswith('🧩 组合检查')
    assert {f.name: f.value for f in message.fields}['一句理由'] == 'Agent 不可用，只有硬结果'


def test_no_hard_result_sends_no_portfolio_message():
    """Switch off / node not run / migration missing: not an all-clear, so no message (K2)."""
    for view in (None, portfolio_check('not_computed', result=None, commentary=None),
                 portfolio_check('not_computed', reason='功能开关已关闭')):
        dg = build(portfolio=view if view is None or view.get('result') is None else {**view, 'status': 'not_computed'})
        assert dg['portfolio'] is None
        assert not any(m.title.startswith('🧩') for m in templates.workbench_digest(dg))
    assert len(templates.workbench_digest(build())) == 5               # the message set is unchanged without one
