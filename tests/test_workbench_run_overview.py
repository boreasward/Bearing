"""Run overview (v2.6 spec §7): per-stage sentences from stage_runs rows, reason texts, run selection.

The offline part feeds `build` / `describe` with rows written by the real StageRecorder (an injected writer
collects them), so the counts are compared with what the recorder actually produced. The SQL part reads a
disposable schema and pins the read-only behaviour and the CLI."""
import investment_assistant.workflow.repo as workflow_repo
import inspect
import re
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest

import investment_assistant.core.texts as labels

from investment_assistant.analysis import rules

import investment_assistant.views.runs.overview as run_overview

import investment_assistant.core.tx as service
import investment_assistant.core.stages as stages_module
from investment_assistant.workflow.recorder import StageRecorder
from investment_assistant.analysis.agent import REASON_TEXT as AGENT_REASON_TEXT

SESSION = date(2026, 10, 27)
T0 = datetime(2026, 10, 27, 22, 30, tzinfo=UTC)          # 06:30 Beijing on the 28th
STAGE_TITLES = {s.id: s for s in stages_module.STAGES}
V3 = stages_module.STAGE_IDS_V3                 # the nine stages of the v2.7 runs these older tests feed in
V4 = stages_module.STAGE_IDS_V4                 # the literal ten-stage registry, independent of current STAGE_IDS


def row(stage, *, run_id='run-1', status='success', reason=None, i=0, o=0, skips=(), failures=(),
        degradations=(), outputs=None, detail=None, trigger='scheduled', version='stages-3',
        started=T0, seconds=2, duration_ms=1234):
    return {'run_id': run_id, 'job': 'workbench', 'trigger': trigger, 'session': SESSION, 'stage': stage,
            'stages_version': version, 'status': status, 'reason': reason, 'input_count': i, 'output_count': o,
            'skips': list(skips), 'failures': list(failures), 'degradations': list(degradations),
            'outputs': outputs or {}, 'detail': detail or {}, 'started_at': started,
            'finished_at': started + timedelta(seconds=seconds), 'duration_ms': duration_ms}


def describe(r):
    stage = STAGE_TITLES[r['stage']]
    return run_overview.describe(r, title=stage.title, purpose=stage.purpose)


# ---------------------------------------------------------------- describe: templates

TEMPLATE_CASES = [
    ('collect', dict(i=174, o=141, detail={'earnings': {'targets': 120, 'ok': 118, 'failed': 2}}),
     '采集 141/174 个目标（日线、资讯、SEC、FRED 按族计）；财报日 118/120'),
    ('screen', dict(i=100, o=12), '纳指 100 范围 100 只，今日入选 12 只进扩展池'),
    ('regime', dict(i=140, o=1), '用 140 只股票的广度生成区制快照 1 份'),
    ('signals', dict(i=30, o=5, detail={'units': {'buy': 2, 'watch': 1}}),
     '评估 30 只，产生 5 条候选；决策单元：买入 2 · 观察 1'),
    ('plan', dict(i=2, o=2, detail={'status': {'ok': 1, 'invalid': 1}, 'derivation': {'checked': 2, 'consistent': 2}}),
     '2 个买入单元，生成规则计划 2 份（有效 1 · 作废 1）；推导核对一致 2/2'),
    ('agent', dict(i=3, o=2, outputs={'plan_ids': ['a', 'b']}, detail={'tokens': {'input': 900, 'output': 120}}),
     'Agent 处理 3 个单元，2 个给出结果，新 Agent 计划 2 份；token 输入 900 · 输出 120'),
    ('accounts', dict(i=3, o=2), '处理 3 个账户，完成 2 个'),
    ('review', dict(i=4, o=4, outputs={'plan_ids': ['n1']},
                    detail={'outcomes': {'maintain': 2, 'revise': 1, 'invalidate': 1},
                            'decided_by': {'host': 1, 'agent': 3},
                            'ended': {'expired': 1, 'closed_by_decision': 0, 'position_closed': 2},
                            'holdings_without_chain': ['AAPL', 'MSFT']}),
     '复查 4 份 Agent 计划：维持 2 · 修订 1（出新版 1）· 作废 1；系统规则判定 1 · Agent 判定 3；'
     '已结束的链：过期 1 · 否决 0 · 平仓 2；无 Agent 计划的持仓：AAPL, MSFT'),
    ('retro', dict(i=3, o=2, detail={'events': 3, 'new_cards': 3, 'to_review': 2,
                                    'drafts': {'ok': 2, 'rejected': 1, 'error': 0, 'skipped': 0},
                                    'by_end_kind': {'position_closed': 1, 'stop': 1, 'target': 0, 'expired': 1,
                                                    'invalidated': 0}}),
     '新结束 3 笔（平仓 1 · 止损 1 · 过期 1），新建复盘卡 3 张；起草 2/3 张；待审定 2 张'),
    ('retro', dict(i=0, o=0, detail={'events': 0, 'new_cards': 0, 'to_review': 0, 'by_end_kind': {}}),
     '没有新结束的计划或平仓；待审定 0 张'),
    ('push', dict(i=3, o=3, outputs={'push': {'sent': True, 'messages': 3, 'reason': None}}, ),
     '发送 3 条消息'),
]


@pytest.mark.parametrize('stage,kwargs,sentence', TEMPLATE_CASES, ids=[f'{c[0]}-{n}' for n, c in enumerate(TEMPLATE_CASES)])
def test_describe_templates(stage, kwargs, sentence):
    out = describe(row(stage, **kwargs))
    assert out['sentence'] == sentence
    assert out['status'] == 'success' and out['status_text'] == '成功'
    assert out['id'] == stage and out['title'] == STAGE_TITLES[stage].title
    assert out['purpose'] == STAGE_TITLES[stage].purpose


def test_describe_review_all_zero_ended_and_no_holdings_omitted():
    out = describe(row('review', i=1, o=1, detail={
        'outcomes': {'maintain': 1, 'revise': 0, 'invalidate': 0}, 'decided_by': {'host': 1, 'agent': 0},
        'ended': {'expired': 0, 'closed_by_decision': 0, 'position_closed': 0}, 'holdings_without_chain': []}))
    assert '已结束的链' not in out['sentence'] and '无 Agent 计划的持仓' not in out['sentence']
    assert out['sentence'] == '复查 1 份 Agent 计划：维持 1 · 修订 0（出新版 0）· 作废 0；系统规则判定 1 · Agent 判定 0'


def test_describe_retro():
    """v2.7 §9 的两句模板；分项只列非零项，无事件但有待起草的旧卡也走第一句，缺键按 0。"""
    out = describe(row('retro', i=3, o=2, detail={'events': 3, 'new_cards': 3, 'to_review': 2, 'by_end_kind': {
        'position_closed': 1, 'stop': 1, 'target': 0, 'expired': 1, 'invalidated': 0}}))
    assert out['sentence'] == '新结束 3 笔（平仓 1 · 止损 1 · 过期 1），新建复盘卡 3 张；起草 2/3 张；待审定 2 张'
    assert out['concepts'] == ['review.retro_card', 'review.retro_end']
    quiet = describe(row('retro', i=0, o=0, detail={'events': 0, 'new_cards': 0, 'to_review': 0, 'by_end_kind': {}}))
    assert quiet['sentence'] == '没有新结束的计划或平仓；待审定 0 张'
    older = describe(row('retro', i=2, o=1, detail={'events': 0, 'new_cards': 0, 'to_review': 4}))
    assert older['sentence'] == '新结束 0 笔，新建复盘卡 0 张；起草 1/2 张；待审定 4 张'
    assert describe(row('retro'))['sentence'] == '没有新结束的计划或平仓；待审定 0 张'
    assert describe(row('retro', status='skipped', reason='not_trading_day'))['sentence'] == '跳过：纽约休市'
    capped = describe(row('retro', status='degraded', i=7, o=4, detail={
        'events': 1, 'new_cards': 1, 'to_review': 4, 'drafts': {'ok': 4, 'rejected': 1, 'error': 0, 'skipped': 2}},
                          skips=[{'reason': 'draft_cap', 'count': 2, 'samples': ['AAPL']}],
                          degradations=[{'kind': 'draft_rejected', 'detail': 'AMD'}]))
    assert '起草 4/5 张' in capped['sentence']                      # {ok}/{attempted}: skipped cards were not attempted
    assert capped['sentence'].endswith('；降级：草稿未通过校验；跳过 2（超出每次起草上限 5 张 × 2）')


def test_describe_push_variants():
    assert describe(row('push', detail={'closed': True}, i=1, o=1,
                        outputs={'push': {'sent': True, 'messages': 1, 'reason': None}}))['sentence'] \
        == '纽约休市，发送休市说明 1 条'
    assert describe(row('push'))['sentence'] == '没有可发的消息'
    assert describe(row('push', status='skipped', reason='manual_run'))['sentence'] == '跳过：手动运行不推送'


def test_describe_skipped_and_failed():
    skipped = describe(row('screen', status='skipped', reason='explicit_pool'))
    assert skipped['sentence'] == '跳过：指定股票运行，不筛选'
    assert skipped['reason'] == 'explicit_pool' and skipped['reason_text'] == '指定股票运行，不筛选'
    failed = describe(row('agent', status='failed', reason='aborted: boom', i=2, o=0))
    assert failed['sentence'] == '失败：运行中断：boom' and failed['status_text'] == '失败'
    assert (failed['input_count'], failed['output_count']) == (2, 0)


def test_suffix_order():
    out = describe(row(
        'agent', status='partial', i=5, o=3, outputs={'plan_ids': ['a']},
        failures=[{'target': 'AAA', 'reason': 'x'}],
        degradations=[{'kind': 'fred_not_configured', 'detail': ''}],
        skips=[{'reason': 'token_budget', 'count': 2, 'samples': ['A', 'B']},
               {'reason': 'max_attempts', 'count': 1, 'samples': ['C']}]))
    assert out['sentence'] == ('Agent 处理 5 个单元，3 个给出结果，新 Agent 计划 1 份'
                               '；1 处失败；降级：FRED 未配置'
                               '；跳过 3（超出 token 上限 × 2、已达重试上限 × 1）')
    assert out['skips'][0] == {'reason': 'token_budget', 'text': '超出 token 上限', 'count': 2, 'samples': ['A', 'B']}
    assert out['degradations'] == [{'kind': 'fred_not_configured', 'text': 'FRED 未配置', 'detail': ''}]
    assert out['failures'] == [{'target': 'AAA', 'reason': 'x'}]


def test_suffix_tolerates_empty_kind_and_reason():
    out = describe(row('signals', i=1, o=1, degradations=[{'kind': None, 'detail': ''}, {'kind': '', 'detail': ''}],
                       skips=[{'reason': None, 'count': 1, 'samples': []}]))
    assert 'None' not in out['sentence']
    assert out['sentence'].endswith('；降级：未知、未知；跳过 1（未知 × 1）')
    assert out['degradations'][0]['text'] is None


def test_suffix_only_first_three_skip_reasons():
    skips = [{'reason': f'r{n}', 'count': n, 'samples': []} for n in range(1, 6)]
    out = describe(row('signals', skips=skips, i=1, o=1))
    assert out['sentence'].endswith('；跳过 15（r1 × 1、r2 × 2、r3 × 3）')


# ---------------------------------------------------------------- reason texts

def test_reason_texts():
    assert run_overview.reason_text('agent_unavailable: 未配置') == 'Agent 不可用：未配置'
    assert run_overview.reason_text('agent_unavailable: not_configured') == 'Agent 不可用：未配置'
    assert run_overview.reason_text('aborted: boom') == '运行中断：boom'
    assert run_overview.reason_text('not_trading_day') == '纽约休市'
    assert run_overview.reason_text('never-heard-of-it') == 'never-heard-of-it'
    assert run_overview.reason_text('x: y') == 'x: y'
    assert run_overview.reason_text(None) is None
    for code, text in {'explicit_pool': '指定股票运行，不筛选', 'manual_run': '手动运行不推送',
                       'already_pushed': '今天已推送过', 'task_disabled': '推送任务已关闭',
                       'not_reached': '运行提前结束，未执行到', 'already_completed': '已有结果',
                       'already_reviewed': '今天已复查', 'fresh_version': '当天刚生成，不复查',
                       'object_cap': '超出复查对象上限 20', 'atr_unavailable': 'ATR 无法计算'}.items():
        assert run_overview.reason_text(code) == text


def test_degradation_kind_texts():
    expected = {'fred_not_configured': 'FRED 未配置', 'label_insufficient': '广度样本不足',
                'macro_not_ok': '宏观序列不完整', 'conflict': '与已存记录不一致（未覆盖）',
                'universe_load_failed': '范围文件加载失败', 'benchmark_missing': 'QQQ 缺当日数据',
                'derivation_mismatch': '推导核对不一致', 'derivation_check_error': '推导核对出错',
                'revision_failed': '修订未通过校验', 'agent_unavailable': 'Agent 不可用'}
    for kind, text in expected.items():
        assert run_overview.reason_text(kind) == text


def _precondition_returns() -> list[str]:
    return re.findall(r"return '([a-z_]+)'", inspect.getsource(rules.precondition))


def test_rule_skip_texts_cover_precondition():
    returns = _precondition_returns()
    assert returns, 'precondition source should have string returns'
    for rule_id in ('trend_reclaim', 'volume_breakout'):
        for reason in returns + ['earnings_time_unknown']:
            text = run_overview.reason_text(f'{rule_id}:{reason}')
            assert text.startswith(f'{labels.RULE_TEXT[rule_id]}：')
            assert text != f'{labels.RULE_TEXT[rule_id]}：{reason}'      # translated, not echoed


def test_reason_tables_disjoint():
    assert set(run_overview.REASON_TEXT) & set(AGENT_REASON_TEXT) == set()
    assert run_overview.reason_text('token_budget') == AGENT_REASON_TEXT['token_budget']
    assert set(run_overview.STATUS_TEXT) == {'success', 'partial', 'degraded', 'failed', 'skipped', 'missing',
                                             'not_recorded'}
    assert run_overview.STATUS_TEXT['partial'] == '部分失败'


def test_stage_concepts_cover_registry():
    assert set(run_overview.STAGE_CONCEPTS) == set(stages_module.STAGE_IDS)
    assert run_overview.STAGE_CONCEPTS['collect'] == ('family.bars', 'family.news', 'family.sec_filings',
                                                      'family.fred')
    assert run_overview.STAGE_CONCEPTS['push'] == ('system.push',)
    assert run_overview.STAGE_CONCEPTS['accounts'] == (
        'account.ledger', 'account.paper', 'account.fill_rules', 'account.follow_plans', 'account.agent_portfolio')


@pytest.mark.parametrize('stage', stages_module.STAGE_IDS)
def test_describe_tolerates_missing_detail_keys(stage):
    out = describe(row(stage, detail={}, outputs={}))
    assert out['sentence'] and '；' not in out['sentence'] or stage in ('push', 'retro')
    assert out['sentence'] != run_overview.UNKNOWN_STAGE_NOTE
    assert out['concepts'] == list(run_overview.STAGE_CONCEPTS[stage])
    # an old row whose detail holds unrelated keys is fine too
    assert describe(row(stage, detail={'weird': 1}, outputs={'x': 1}))['sentence']


# ---------------------------------------------------------------- build: real recorder rows

def _recorded_rows(version='stages-3', run_id='run-rec', trigger='scheduled'):
    rows: list[dict] = []
    tick = iter(range(1000))
    wall = lambda: T0 + timedelta(seconds=next(tick))          # noqa: E731
    rec = StageRecorder(run_id, trigger, SESSION, writer=rows.append, wall_now=wall,
                                      version=version)
    acc = rec.open('collect')
    acc.input(10)
    acc.output(8)
    acc.skip('FRED_API_KEY 未配置', 'FRED')
    acc.fail('bars:MU', 'provider down')
    acc.degrade('fred_not_configured', 'no key')
    acc.detail['earnings'] = {'targets': 4, 'ok': 4, 'failed': 0}
    rec.finish('collect')
    with rec.stage('screen') as screen:
        screen.input(100)
        screen.output(7)
    rec.skip('regime', 'explicit_pool')
    signals = rec.open('signals')
    signals.input(30)
    signals.output(4)
    signals.skip('trend_reclaim:insufficient_history', 'ZZZ')
    signals.skip('trend_reclaim:insufficient_history', 'YYY')
    signals.detail['units'] = {'buy': 1}
    signals.ref('inserted', 'c1')
    rec.finish('signals')
    rec.close()
    return rows


def test_overview_counts_equal_recorder_rows():
    rows = _recorded_rows()
    assert len(rows) == 9
    view = run_overview.build(rows, session=SESSION, run_id=None, ready=True)
    assert view['ready'] is True and view['note'] is None and view['session'] == '2026-10-27'
    assert [s['id'] for s in view['stages']] == list(V3)
    by_stage = {r['stage']: r for r in rows}
    for stage in view['stages']:
        src = by_stage[stage['id']]
        assert stage['input_count'] == src['input_count']
        assert stage['output_count'] == src['output_count']
        assert stage['reason'] == src['reason']
        assert stage['duration_ms'] == src['duration_ms']
        assert stage['status'] == src['status']
        assert [{k: s[k] for k in ('reason', 'count', 'samples')} for s in stage['skips']] == src['skips']
        assert stage['failures'] == src['failures']
        assert [{k: d[k] for k in ('kind', 'detail')} for d in stage['degradations']] == src['degradations']
    collect = next(s for s in view['stages'] if s['id'] == 'collect')
    assert collect['status'] == 'partial' and collect['status_text'] == '部分失败'
    assert collect['sentence'] == ('采集 8/10 个目标（日线、资讯、SEC、FRED 按族计）；财报日 4/4；1 处失败；降级：FRED 未配置；'
                                   '跳过 1（FRED_API_KEY 未配置 × 1）')
    signals = next(s for s in view['stages'] if s['id'] == 'signals')
    assert signals['skips'][0]['text'] == '趋势回踩修复：日线不足 200 根'
    assert signals['skips'][0]['count'] == 2 and signals['skips'][0]['samples'] == ['ZZZ', 'YYY']
    # run bookkeeping
    [run] = view['runs']
    assert run['run_id'] == 'run-rec' and run['trigger'] == 'scheduled' and run['trigger_text'] == '定时'
    assert run['stages_version'] == 'stages-3' and run['rows'] == 9 and run['closed'] is False
    assert view['selected_run_id'] == 'run-rec'


def test_overview_stages_1_placeholder():
    rows = _recorded_rows(version='stages-1')
    assert len(rows) == 7
    view = run_overview.build(rows, session=SESSION, run_id=None, ready=True)
    assert [s['id'] for s in view['stages']] == list(V3)
    assert len(view['stages']) == 9
    placeholder = view['stages'][6]
    assert placeholder['id'] == 'review' and placeholder['status'] == 'not_recorded'
    assert placeholder['status_text'] == '未记录'
    assert placeholder['sentence'] == '这次运行按 stages-1 记账（早于 v2.5 或迁移 026 未执行），没有每日复查阶段'
    assert placeholder['input_count'] is None and placeholder['output_count'] is None
    assert placeholder['duration_ms'] is None and placeholder['skips'] == []
    retro = view['stages'][7]
    assert retro['id'] == 'retro' and retro['status'] == 'not_recorded'
    assert retro['sentence'] == '这次运行按 stages-1 记账（早于 v2.5 或迁移 026 未执行），没有每日复查和复盘卡阶段'
    assert view['stages'][8]['id'] == 'push'
    assert view['runs'][0]['stages_version'] == 'stages-1' and view['runs'][0]['rows'] == 7


def test_overview_stages_2_placeholder():
    """A stages-2 run (before v2.7, or migration 027 missing) has eight rows; retro is "not recorded", not "missing"."""
    rows = _recorded_rows(version='stages-2')
    assert len(rows) == 8
    view = run_overview.build(rows, session=SESSION, run_id=None, ready=True)
    assert [s['id'] for s in view['stages']] == list(V3) and len(view['stages']) == 9
    review, retro = view['stages'][6], view['stages'][7]
    assert review['status'] == 'skipped' and retro['status'] == 'not_recorded' and retro['status_text'] == '未记录'
    assert retro['sentence'] == '这次运行按 stages-2 记账（早于 v2.7 或迁移 027 未执行），没有复盘卡阶段'
    assert retro['concepts'] == ['review.retro_card', 'review.retro_end']
    assert '可能仍在运行' not in view['headline'] and view['runs'][0]['rows'] == 8


def test_missing_and_unknown_stage():
    rows = [row(s) for s in V3 if s != 'regime']
    rows.append(row('mystery', status='success', i=1, o=1))
    view = run_overview.build(rows, session=SESSION, run_id=None, ready=True)
    ids = [s['id'] for s in view['stages']]
    assert ids[:9] == list(V3) and ids[-1] == 'mystery' and len(ids) == 10
    missing = view['stages'][2]
    assert missing['id'] == 'regime' and missing['status'] == 'missing' and missing['status_text'] == '缺证据'
    assert missing['sentence'] == '这一阶段没有证据行（记账失败，见运行报告）'
    assert missing['input_count'] is None and missing['output_count'] is None
    unknown = view['stages'][-1]
    assert unknown['sentence'] == '未知阶段' and unknown['title'] == 'mystery'
    assert unknown['status'] == 'success'


def test_unfinished_run_is_not_an_accounting_failure():
    rows = [row('collect', i=10, o=10), row('screen', i=100, o=7)]
    view = run_overview.build(rows, session=SESSION, run_id=None, ready=True)
    assert [s['status'] for s in view['stages']] == ['success', 'success'] + ['missing'] * 7
    for stage in view['stages'][2:]:
        assert stage['sentence'] == '还没有这一阶段的记录：运行可能仍在进行，或中途被终止'
        assert stage['status_text'] == '缺证据'
    assert view['headline'].endswith('成功 2 · 缺证据 7（可能仍在运行）')


def test_gap_before_a_recorded_stage_stays_an_accounting_failure():
    rows = [row(s) for s in V3 if s != 'regime']
    view = run_overview.build(rows, session=SESSION, run_id=None, ready=True)
    assert view['stages'][2]['sentence'] == '这一阶段没有证据行（记账失败，见运行报告）'
    assert '可能仍在运行' not in view['headline']
    # a gap in the middle plus an unrecorded tail: the middle one is still a bookkeeping failure
    rows = [row(s) for s in ('collect', 'signals')]
    view = run_overview.build(rows, session=SESSION, run_id=None, ready=True)
    assert view['stages'][1]['sentence'] == '这一阶段没有证据行（记账失败，见运行报告）'
    assert '可能仍在运行' not in view['headline']


def test_unfinished_run_is_not_an_accounting_failure():
    rows = [row('collect', i=10, o=10), row('screen', i=100, o=7)]
    view = run_overview.build(rows, session=SESSION, run_id=None, ready=True)
    assert [s['status'] for s in view['stages']] == ['success', 'success'] + ['missing'] * 7
    for stage in view['stages'][2:]:
        assert stage['sentence'] == '还没有这一阶段的记录：运行可能仍在进行，或中途被终止'
        assert stage['status_text'] == '缺证据'
    assert view['headline'].endswith('成功 2 · 缺证据 7（可能仍在运行）')


def test_gap_before_a_recorded_stage_stays_an_accounting_failure():
    rows = [row(s) for s in V3 if s != 'regime']
    view = run_overview.build(rows, session=SESSION, run_id=None, ready=True)
    assert view['stages'][2]['sentence'] == '这一阶段没有证据行（记账失败，见运行报告）'
    assert '可能仍在运行' not in view['headline']
    # a gap in the middle plus an unrecorded tail: the middle ones are still bookkeeping failures
    view = run_overview.build([row('collect'), row('signals')], session=SESSION, run_id=None, ready=True)
    assert view['stages'][1]['sentence'] == '这一阶段没有证据行（记账失败，见运行报告）'
    assert '可能仍在运行' not in view['headline']


def test_cli_exit_code_for_an_unfinished_run(monkeypatch, capsys):
    view = run_overview.build([row('collect'), row('screen')], session=SESSION, run_id=None, ready=True)
    monkeypatch.setattr(run_overview, 'overview', lambda **kwargs: view)
    assert run_overview.main(['--session', '2026-10-27']) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[0].endswith('（可能仍在运行）') and len(out) == 10
    assert '运行可能仍在进行' in out[3]


def test_not_ready_and_no_rows_notes():
    view = run_overview.build([], session=SESSION, run_id=None, ready=False)
    assert view == {'session': '2026-10-27', 'ready': False, 'note': '迁移 025 未执行，暂无阶段证据', 'runs': [],
                    'selected_run_id': None, 'headline': None, 'stages': []}
    view = run_overview.build([], session=SESSION, run_id=None, ready=True)
    assert view['ready'] is True and view['stages'] == [] and view['runs'] == [] and view['headline'] is None
    assert view['note'] == '这一天没有阶段证据：当天没有运行，或运行早于 v2.4（2026-10-27 起才记录）'


def closed_run(run_id='run-closed', started=T0 + timedelta(hours=1)):
    rows = [row(s, run_id=run_id, status='skipped', reason='not_trading_day', started=started)
            for s in V3 if s != 'push']
    rows.append(row('push', run_id=run_id, i=1, o=1, started=started, detail={'closed': True},
                    outputs={'push': {'sent': True, 'messages': 1, 'reason': None}}))
    return rows


def real_run(run_id='run-real', started=T0, statuses=None):
    statuses = statuses or {}
    return [row(s, run_id=run_id, started=started, status=statuses.get(s, 'success'))
            for s in V3]


def test_non_trading_day_headline():
    view = run_overview.build(closed_run(), session=SESSION, run_id=None, ready=True)
    assert view['headline'] == '纽约休市，未运行（只推送一行休市说明）'
    assert view['runs'][0]['closed'] is True and view['runs'][0]['trigger_text'] == '休市'
    assert len(view['stages']) == 9
    assert view['stages'][0]['sentence'] == '跳过：纽约休市'
    assert view['stages'][-1]['sentence'] == '纽约休市，发送休市说明 1 条'


def test_headline_counts():
    rows = real_run(statuses={'regime': 'degraded', 'screen': 'skipped'})
    rows[0] = row('collect', run_id='run-real', started=T0, seconds=192)             # run spans 3 min 12 s via the collect row
    view = run_overview.build(rows, session=SESSION, run_id=None, ready=True)
    assert view['headline'] == '定时运行 · 06:30 开始 · 用时 3 分 12 秒 · 9 个阶段：成功 7 · 降级 1 · 跳过 1'
    short = run_overview.build(real_run(), session=SESSION, run_id=None, ready=True)['headline']
    assert short == '定时运行 · 06:30 开始 · 用时 2 秒 · 9 个阶段：成功 9'
    manual = run_overview.build([r | {'trigger': 'manual_batch'} for r in real_run()], session=SESSION,
                                run_id=None, ready=True)
    assert manual['headline'].startswith('手动运行 · 06:30 开始')


def test_selects_latest_started_run():
    first = real_run('run-a', started=T0)
    second = real_run('run-b', started=T0 + timedelta(hours=2))
    view = run_overview.build(first + second, session=SESSION, run_id=None, ready=True)
    assert view['selected_run_id'] == 'run-b'
    assert [r['run_id'] for r in view['runs']] == ['run-a', 'run-b']
    chosen = run_overview.build(first + second, session=SESSION, run_id='run-a', ready=True)
    assert chosen['selected_run_id'] == 'run-a'


def test_skips_closed_run_by_default():
    real = real_run('run-real', started=T0)
    closed = closed_run('run-closed', started=T0 + timedelta(hours=3))
    view = run_overview.build(real + closed, session=SESSION, run_id=None, ready=True)
    assert view['selected_run_id'] == 'run-real'
    runs = {r['run_id']: r for r in view['runs']}
    assert runs['run-closed']['closed'] is True and runs['run-closed']['trigger_text'] == '休市'
    assert runs['run-real']['closed'] is False
    assert run_overview.build(real + closed, session=SESSION, run_id='run-closed',
                              ready=True)['selected_run_id'] == 'run-closed'


def test_only_closed_run_selected():
    closed = closed_run('run-c1', started=T0) + closed_run('run-c2', started=T0 + timedelta(hours=1))
    view = run_overview.build(closed, session=SESSION, run_id=None, ready=True)
    assert view['selected_run_id'] == 'run-c2'


def test_run_id_not_in_session_is_lookup_error_pure():
    with pytest.raises(LookupError):
        run_overview.build(real_run(), session=SESSION, run_id='nope', ready=True)


# ---------------------------------------------------------------- SQL (disposable schema)

from test_workbench_service import NOW, db  # noqa: E402,F401  (fixture)


def _insert(db, rows):
    for r in rows:
        workflow_repo.insert_stage_run(db, r)
    db.commit()


def _stage_count(db):
    return db.execute('SELECT count(*) FROM stage_runs').fetchone()[0]


def test_overview_reads_session_rows_read_only(db):
    _insert(db, real_run('run-a', started=T0) + closed_run('run-z', started=T0 + timedelta(hours=2)))
    before = _stage_count(db)
    view = run_overview.overview(session=SESSION, run_id=None, now=NOW)
    assert view['ready'] is True and view['selected_run_id'] == 'run-a'
    assert [r['run_id'] for r in view['runs']] == ['run-a', 'run-z']
    assert [s['id'] for s in view['stages']] == list(V3)
    assert _stage_count(db) == before
    # without a session, the latest closed session at `now` is used (2026-10-22 here: no rows)
    assert run_overview.overview(session=None, run_id=None, now=NOW)['stages'] == []
    # the use case runs in a read-only transaction: a write through it is refused
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with service.read_only_tx() as conn:
            conn.execute('DELETE FROM stage_runs')
    db.rollback()
    assert _stage_count(db) == before


def test_run_id_not_in_session_is_lookup_error(db):
    _insert(db, real_run('run-a'))
    with pytest.raises(LookupError):
        run_overview.overview(session=SESSION, run_id='other-run', now=NOW)


def test_overview_without_stage_runs_table(db):
    db.execute('DROP TABLE stage_runs CASCADE')
    db.commit()
    view = run_overview.overview(session=SESSION, run_id=None, now=NOW)
    assert view['ready'] is False and view['note'] == '迁移 025 未执行，暂无阶段证据'
    assert view['stages'] == [] and view['runs'] == []


def test_cli_output_and_exit_codes(db, capsys):
    _insert(db, real_run('run-a'))
    assert run_overview.main(['--session', '2026-10-27']) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 9 + 1                                           # headline + 9 stage lines
    assert out[0].startswith('定时运行')
    collect = out[1]
    assert collect.startswith(f'{STAGE_TITLES["collect"].title} · 成功 · ') and collect.endswith(' · 0/0')
    assert run_overview.main(['--session', '2026-10-26']) == 1          # no evidence that day
    assert '这一天没有阶段证据' in capsys.readouterr().out
    assert run_overview.main(['--session', '2026-10-27', '--run-id', 'nope']) == 2
    capsys.readouterr()
    for argv in (['--session', 'yesterday'], []):
        with pytest.raises(SystemExit) as raised:
            run_overview.main(argv)
        assert raised.value.code == 2
    capsys.readouterr()


def test_cli_database_unavailable_exits_two(monkeypatch, capsys):
    monkeypatch.delenv('INVESTMENT_ASSISTANT_DATABASE_URL', raising=False)
    assert run_overview.main(['--session', '2026-10-27']) == 2
    assert '数据库' in capsys.readouterr().err


# ---------------------------------------------------------------- v2.8: stages-4, sec_monitor, retro, headline

def test_describe_sec_monitor():
    monitor = describe(row('sec_monitor', i=40, o=3, version='stages-4',
                           detail={'mode': 'monitor', 'notifications': {'delivery': 'sent', 'sent': 4}}))
    assert monitor['sentence'] == '扫描 40 只，新归档 3 份申报，通知 4 条'
    failing = describe(row('sec_monitor', status='partial', i=40, o=3, failures=[{'target': 'sec:MU', 'reason': 'x'}],
                           detail={'mode': 'monitor', 'notifications': {'delivery': 'sent', 'sent': 1}}))
    assert failing['sentence'] == '扫描 40 只，新归档 3 份申报，通知 1 条；1 只失败'
    downloader = describe(row('sec_monitor', i=40, o=2, detail={'mode': 'downloader'}))
    assert downloader['sentence'] == '下载器模式：新下载 2 份申报'
    both = describe(row('sec_monitor', status='partial', i=3, o=0, failures=[{'target': 'sec:A', 'reason': 'x'}],
                        detail={'mode': 'downloader'}))
    assert both['sentence'] == '下载器模式：新下载 0 份申报；1 只失败'
    assert describe(row('sec_monitor', status='skipped', reason='not_trading_day'))['sentence'] == '跳过：纽约休市'


CARDS = {'events': 3, 'new_cards': 3, 'to_review': 2, 'drafts': {'ok': 2, 'rejected': 1, 'error': 0, 'skipped': 0},
         'by_end_kind': {'position_closed': 1, 'stop': 1, 'target': 0, 'expired': 1, 'invalidated': 0}}
CARDS_TEXT = '新结束 3 笔（平仓 1 · 止损 1 · 过期 1），新建复盘卡 3 张；起草 2/3 张；待审定 2 张'


def test_describe_retro_with_weekly():
    sent = {'action': 'generated', 'push': {'sent': True, 'messages': 1}}
    held = {'action': 'generated', 'push': 'manual_run'}
    due = describe(row('retro', i=3, o=2, detail={**CARDS, 'cards': True, 'weekly': sent, 'monthly': None}))
    assert due['sentence'] == CARDS_TEXT + '；周度复盘：已推送；月度：未到期'
    manual = describe(row('retro', i=3, o=2, detail={**CARDS, 'cards': True, 'weekly': held,
                                                     'monthly': {'action': 'already_sent'}}))
    assert manual['sentence'] == CARDS_TEXT + '；周度复盘：已生成未推送；月度：未到期'
    holiday = describe(row('retro', detail={'cards': False, 'weekly': sent, 'monthly': {'action': 'reused',
                                                                                         'push': {'sent': False}}}))
    assert holiday['sentence'] == '周度复盘：已推送；月度：已生成未推送'          # no card pass on a day without trading
    assert describe(row('retro', i=3, o=2, detail=CARDS))['sentence'] == CARDS_TEXT       # a v2.7 row reads as before


def test_describe_retro_not_due():
    out = describe(row('retro', status='skipped', reason='not_due', version='stages-4'))
    assert out['sentence'] == '今天没有到期的复盘' and out['reason_text'] == '今天没有到期的复盘'


def test_reason_text_new_codes():
    assert run_overview.reason_text('upstream_failed') == '上游节点失败，未执行'
    assert run_overview.reason_text('not_due') == '今天没有到期的复盘'
    assert run_overview.reason_text('daily_running') == '另一次运行正在进行'
    assert run_overview.reason_text('aborted: boom') == '运行中断：boom'
    assert describe(row('screen', status='skipped', reason='upstream_failed'))['sentence'] == '跳过：上游节点失败，未执行'


def _v4_rows(*, failed_at=None, retried=True, attempts=1):
    """A stages-4 run; ``failed_at`` fails that node, the later ones are skipped as upstream_failed."""
    out, broken = [], False
    for n, stage in enumerate(V4):
        kwargs = dict(version='stages-4', started=T0 + timedelta(seconds=10 * n), run_id='daily-x')
        if broken:
            out.append(row(stage, status='skipped', reason='upstream_failed', **kwargs))
        elif stage == failed_at:
            broken = True
            out.append(row(stage, status='failed', reason='aborted: provider down',
                           detail={'retry': {'first_error': 'provider down', 'waited_s': 60}} if retried else {},
                           **kwargs))
        else:
            out.append(row(stage, i=1, o=1, **kwargs))
    return out


def test_overview_of_a_stages_4_run_has_ten_stages():
    view = run_overview.build(_v4_rows(), session=SESSION, run_id=None, ready=True)
    assert [s['id'] for s in view['stages']] == list(V4) and len(view['stages']) == 10
    assert 'accounts' not in [s['id'] for s in view['stages']]
    assert view['stages'][0]['id'] == 'sec_monitor' and view['stages'][-1]['id'] == 'retro'
    assert view['stages'][-1]['title'] == '复盘' and view['runs'][0]['rows'] == 10
    legacy = run_overview.build(_recorded_rows(), session=SESSION, run_id=None, ready=True)
    assert [s['title'] for s in legacy['stages']][7] == '复盘卡'                   # the old name for the old runs


def test_headline_failed_and_resumed():
    failed = run_overview.build(_v4_rows(failed_at='collect'), session=SESSION, run_id=None, ready=True)
    assert failed['headline'] == '10 月 27 日的运行在「数据采集」失败（已自动重试 1 次）：运行中断：provider down；其后 8 个节点未执行'
    no_retry = run_overview.build(_v4_rows(failed_at='regime', retried=False), session=SESSION, run_id=None, ready=True)
    assert no_retry['headline'] == '10 月 27 日的运行在「市场环境 `regime-2`」失败：运行中断：provider down；其后 6 个节点未执行'
    # resumed from "collect": collect and everything after it carry a second row
    rows = _v4_rows(failed_at='collect')
    later = T0 + timedelta(hours=1)
    rows += [row(stage, i=1, o=1, version='stages-4', run_id='daily-x', started=later + timedelta(seconds=n))
             for n, stage in enumerate(V4[1:])]
    resumed = run_overview.build(rows, session=SESSION, run_id=None, ready=True)
    assert resumed['headline'].endswith('（第 2 次执行，从「数据采集」续跑）')
    assert resumed['headline'].startswith('定时运行 · ') and '失败' not in resumed['headline'].split('·')[0]
    assert [s['status'] for s in resumed['stages']].count('success') == 10
    assert resumed['runs'][0]['rows'] == 19


def test_a_closed_stages_4_run_is_recognised():
    rows = [row(stage, status='skipped', reason='not_trading_day', version='stages-4', run_id='daily-h')
            for stage in V4[:8]]
    rows += [row('push', version='stages-4', run_id='daily-h', i=1, o=1, detail={'closed': True}),
             row('retro', status='skipped', reason='not_due', version='stages-4', run_id='daily-h')]
    view = run_overview.build(rows, session=SESSION, run_id=None, ready=True)
    assert view['runs'][0]['closed'] is True and view['headline'] == run_overview.CLOSED_HEADLINE


def test_overview_of_a_stages_5_run_has_accounts_between_review_and_push():
    rows = [row(stage, version='stages-5', i=1, o=1, started=T0 + timedelta(seconds=n))
            for n, stage in enumerate(stages_module.STAGE_IDS_V5)]
    view = run_overview.build(rows, session=SESSION, run_id=None, ready=True)
    ids = [s['id'] for s in view['stages']]
    assert ids == list(stages_module.STAGE_IDS_V5) and len(ids) == 11
    assert ids[7:10] == ['review', 'accounts', 'push']
    assert view['stages'][8]['sentence'] == '处理 1 个账户，完成 1 个'
    assert view['runs'][0]['rows'] == 11


def test_stages_5_headline_resumes_from_accounts():
    ids = stages_module.STAGE_IDS_V5
    rows = [row(stage, version='stages-5', i=1, o=1, run_id='daily-account',
                started=T0 + timedelta(seconds=n)) for n, stage in enumerate(ids)]
    later = T0 + timedelta(hours=1)
    rows += [row(stage, version='stages-5', i=1, o=1, run_id='daily-account',
                 started=later + timedelta(seconds=n)) | {'attempt': 2} for n, stage in enumerate(ids[8:])]
    view = run_overview.build(rows, session=SESSION, run_id=None, ready=True)
    assert view['headline'].endswith('（第 2 次执行，从「账户」续跑）')
    assert [s['status'] for s in view['stages']].count('success') == 11
    assert view['runs'][0]['rows'] == 14


def test_overview_of_a_stages_6_run_has_portfolio_before_accounts_and_propose_last():
    rows = [row(stage, version='stages-6', i=1, o=1, started=T0 + timedelta(seconds=n))
            for n, stage in enumerate(stages_module.STAGE_IDS)]
    view = run_overview.build(rows, session=SESSION, run_id=None, ready=True)
    ids = [s['id'] for s in view['stages']]
    assert ids == list(stages_module.STAGE_IDS) and len(ids) == 13
    assert ids[7:10] == ['review', 'portfolio', 'accounts'] and ids[-1] == 'propose'
    assert view['stages'][8]['sentence'] == '检查 1 个账户，写入 1 份组合检查'
    assert view['stages'][12]['sentence'] == '写入 1 条优化提案'
    assert view['runs'][0]['rows'] == 13


def test_portfolio_sentence_counts_blocked_deferred_and_commentary():
    out = describe(row('portfolio', i=2, o=2, detail={'accounts': [
        {'blocked': 1, 'deferred': 0, 'commentary_status': 'ok'}, {'blocked': 0, 'deferred': 2, 'commentary_status': None}]}))
    assert out['sentence'] == '检查 2 个账户，写入 2 份组合检查；被拦 1 条、暂缓 2 条；真实账户组合评论：已写入'
    failed = describe(row('portfolio', i=1, o=1, detail={'accounts': [{'commentary_status': 'rejected'}]}))
    assert failed['sentence'].endswith('真实账户组合评论：只有硬结果')
    assert describe(row('propose', i=0, o=0, detail={'reason': 'no_evidence'}))['sentence'] == '没有可用的证据，未起草提案'


# --- v2.9 (spec §6.3, §4.2, §4.3): the new ended breakdown and the new counts of plan / agent / review ---

def test_ended_sentence_breakdown():
    detail = {'outcomes': {'maintain': 1, 'revise': 0, 'invalidate': 0}, 'decided_by': {'host': 1, 'agent': 0},
              'ended': {'expired': 3, 'invalidated': 2, 'position_closed': 1, 'superseded': 1, 'closed_by_decision': 0}}
    out = describe(row('review', i=1, o=1, detail=detail))
    assert '；已结束 7（过期 3 · 作废 2 · 平仓 1 · 被取代 1）' in out['sentence']
    assert '否决' not in out['sentence']
    with_legacy = describe(row('review', i=1, o=1, detail={
        **detail, 'ended': {**detail['ended'], 'closed_by_decision': 2}}))
    assert '已结束 9（过期 3 · 作废 2 · 平仓 1 · 被取代 1 · 否决 2）' in with_legacy['sentence']
    empty = describe(row('review', i=1, o=1, detail={
        **detail, 'ended': {'expired': 0, 'invalidated': 0, 'position_closed': 0, 'superseded': 0,
                            'closed_by_decision': 0}}))
    assert '已结束' not in empty['sentence']
    # rows written before v2.9 keep their sentence
    old = describe(row('review', i=1, o=1, detail={
        **detail, 'ended': {'expired': 1, 'closed_by_decision': 0, 'position_closed': 2}}))
    assert '已结束的链：过期 1 · 否决 0 · 平仓 2' in old['sentence']


def test_plan_agent_review_new_counts():
    plan = describe(row('plan', i=3, o=3, detail={
        'status': {'ok': 3, 'invalid': 0}, 'retriggers': {'count': 2, 'by_state': {'entry': 1, 'holding': 1, 'exiting': 0},
                                                           'tickers': ['MU', 'AMD']}}))
    assert plan['sentence'] == '3 个买入单元，生成规则计划 3 份（有效 3 · 作废 0）；2 个单元并入已有活跃链（入场期 1 · 持仓 1 · 退出期 0）'
    quiet = describe(row('plan', i=1, o=1, detail={'retriggers': {'count': 0, 'by_state': {}, 'tickers': []}}))
    assert '并入' not in quiet['sentence']
    agent = describe(row('agent', i=3, o=2, outputs={'plan_ids': ['a']},
                         detail={'tokens': {'input': 900, 'output': 120}, 'merged': 1}, skips=[
                             {'reason': 'active_chain', 'count': 1, 'samples': ['MU·2026-10-21']}]))
    assert agent['sentence'].startswith('Agent 处理 3 个单元，2 个给出结果，新 Agent 计划 1 份；token 输入 900 · 输出 120；'
                                        '1 个单元并入已有链，不重复提议')
    assert '跳过 1（已有进行中的计划链，这次触发已并入 × 1）' in agent['sentence']
    review = describe(row('review', i=2, o=2, detail={
        'outcomes': {'maintain': 1, 'revise': 1, 'invalidate': 0}, 'decided_by': {'host': 0, 'agent': 2},
        'signals': {'retrigger': 1, 'new_8k': 0}, 'exiting': [{'ticker': 'AMD', 'signal_date': '2026-10-14'}],
        'holding_status': {'rows': 3, 'by_instruction': {'hold': 2, 'exit': 1, 'sell_half': 0, 'no_plan': 0}}}))
    assert review['sentence'].endswith('；再次触发 1 条；退出期不复查 1 条；持仓状态 3 行（退出指令 1）')
