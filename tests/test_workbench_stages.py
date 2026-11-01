"""Stage registry and recorder: order vs migration check, classify, accumulator, row shape."""
import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from investment_assistant.workbench import stages

SESSION = date(2026, 10, 27)
WALL = datetime(2026, 10, 27, 12, 30, tzinfo=UTC)


def _recorder(rows, *, run_id='run-1', clock=None):
    return stages.StageRecorder(run_id, 'manual_batch', SESSION, writer=rows.append,
                                wall_now=lambda: WALL, clock=clock or (lambda: 0.0))


def _check_ids(migration: str) -> tuple[str, ...]:
    sql = Path('migrations', migration).read_text(encoding='utf-8')
    match = re.search(r"CHECK \(stage IN \(([^)]+)\)\)", sql)
    return tuple(re.findall(r"'([^']+)'", match.group(1)))


def test_stage_ids_match_migration_check():
    assert stages.STAGE_IDS_V1 == _check_ids('025_stage_runs.sql')       # stages-1：025 的七个阶段
    assert stages.STAGE_IDS_V2 == _check_ids('026_plan_timeline.sql')    # stages-2：026 的八个阶段
    assert stages.STAGE_IDS_V3 == _check_ids('027_growth_loop.sql')      # stages-3：027 的九个阶段
    assert stages.STAGE_IDS_V4 == _check_ids('028_daily_workflow.sql')      # stages-4：028 的十个阶段
    assert stages.STAGE_IDS == _check_ids('030_accounts.sql')           # stages-5：030 的十一个阶段
    assert stages.STAGE_IDS_BY_VERSION == {'stages-1': stages.STAGE_IDS_V1, 'stages-2': stages.STAGE_IDS_V2,
                                           'stages-3': stages.STAGE_IDS_V3, 'stages-4': stages.STAGE_IDS_V4,
                                           'stages-5': stages.STAGE_IDS}


def test_stages_registry_eleven():
    """v2.11: stages-5 inserts accounts after review; ids equal 030's stage_runs CHECK; the older
    versions are literal tuples, not derived from the registry."""
    assert stages.STAGES_VERSION == 'stages-5'
    assert stages.STAGE_IDS == ('sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review',
                                'accounts', 'push', 'retro')
    assert stages.STAGE_IDS_V3 == ('collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'retro', 'push')
    assert stages.STAGE_IDS_V2 == ('collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'push')
    assert stages.STAGE_IDS_V1 == ('collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'push')
    assert [s.title for s in stages.STAGES] == [
        'SEC 监控', '数据采集', '筛选 `screen-1`', '市场环境 `regime-2`', '信号与候选', '规则计划 `plan-1`',
        'Agent 建议', '每日复查', '账户', '复核与推送', '复盘']
    by_id = {s.id: s for s in stages.STAGES}
    assert by_id['review'].purpose == '对每份有效 Agent 计划做一次复查：维持、修订或作废；系统信号与你的输入触发认真复查。'
    assert by_id['sec_monitor'] == stages.StageDef(
        'sec_monitor', 'SEC 监控', '扫描观察名单的 SEC 申报，归档原文、提取正文事实并经 Discord Bot 直发通知；已通知过的不重发。')
    assert by_id['retro'] == stages.StageDef(
        'retro', '复盘', '交易日检测已结束的计划与平仓并起草复盘卡；周 / 月复盘到期时生成并推送。')
    assert stages.LEGACY_STAGES['retro'].title == '复盘卡'                  # how stages-1 .. stages-3 runs show it
    assert all(s.purpose for s in stages.STAGES)
    rows = []
    rec = _recorder(rows)
    with rec.stage('collect'):
        pass
    assert rec.close(None) == {'written': 11, 'error': None}
    assert {row['stage'] for row in rows} == set(stages.STAGE_IDS)
    assert {row['stages_version'] for row in rows} == {'stages-5'}


def test_stages_3_recorder_writes_nine_in_the_v27_order():
    rows = []
    rec = stages.StageRecorder('run-v3', 'scheduled', SESSION, writer=rows.append, wall_now=lambda: WALL,
                               clock=lambda: 0.0, version='stages-3')
    rec.skip('sec_monitor', 'x')                       # not a stages-3 stage: ignored
    assert rec.close(None) == {'written': 9, 'error': None}
    assert [row['stage'] for row in rows] == list(stages.STAGE_IDS_V3)
    assert {row['stages_version'] for row in rows} == {'stages-3'}


def test_restart_keeps_started_at_and_has_row():
    rows = []
    walls = iter([WALL, WALL.replace(minute=45), WALL.replace(minute=50)])
    rec = stages.StageRecorder('run-r', 'scheduled', SESSION, writer=rows.append, wall_now=lambda: next(walls),
                               clock=lambda: 0.0)
    first = rec.open('collect')
    first.input(9)
    assert not rec.has_row('collect')
    second = rec.restart('collect')                    # the node's retry: a fresh accumulator, the same start
    assert second is not first and second.input_count == 0
    rec.finish('collect')
    assert rec.has_row('collect') and rec.error is None
    assert rows[0]['started_at'] == WALL and rows[0]['input_count'] == 0


def test_acc_freeze_and_restore_roundtrip():
    acc = stages.StageAcc()
    acc.input(3)
    acc.output(2)
    acc.skip('why', 'T1')
    acc.fail('T2', 'boom')
    acc.degrade('conflict', 'x')
    acc.ref('inserted', 'p1')
    acc.detail['status'] = {'ok': 1}
    acc.duration_ms = 12.0
    other = stages.StageAcc()
    other.restore(acc.freeze())
    assert other.freeze() == acc.freeze()
    other.skip('why', 'T3')                            # the skip groups are live again
    assert other.skips == [{'reason': 'why', 'count': 2, 'samples': ['T1', 'T3']}]


def test_stages_2_recorder_writes_eight_and_ignores_retro():
    """D29: without migration 027 the run is booked as stages-2 — eight rows, retro never written."""
    rows = []
    rec = stages.StageRecorder('run-v2', 'scheduled', SESSION, writer=rows.append, wall_now=lambda: WALL,
                               clock=lambda: 0.0, version='stages-2')
    acc = rec.open('retro')
    acc.input(3)
    rec.finish('retro')
    rec.skip('retro', 'migration_027_missing')
    assert rows == []
    assert rec.close(None) == {'written': 8, 'error': None}
    assert [row['stage'] for row in rows] == list(stages.STAGE_IDS_V2)
    assert {row['stages_version'] for row in rows} == {'stages-2'}


def test_stages_1_recorder_writes_seven_and_ignores_review():
    """D22: without migration 026 the run is booked as stages-1 — seven rows, review and retro never written."""
    rows = []
    rec = stages.StageRecorder('run-v1', 'scheduled', SESSION, writer=rows.append, wall_now=lambda: WALL,
                               clock=lambda: 0.0, version='stages-1')
    acc = rec.open('review')                 # 照常返回累加器，但不登记
    acc.input(3)
    rec.finish('review')
    rec.skip('review', 'migration_026_missing')
    with rec.stage('review'):
        pass
    rec.skip('retro', 'migration_027_missing')
    assert rows == []
    assert rec.close(None) == {'written': 7, 'error': None}
    assert [row['stage'] for row in rows] == list(stages.STAGE_IDS_V1)
    assert {row['stages_version'] for row in rows} == {'stages-1'}
    with pytest.raises(ValueError):
        stages.StageRecorder('run-x', 'scheduled', SESSION, writer=rows.append, version='stages-9')


def test_classify_precedence():
    assert stages.classify(executed=False, failed_reason=None, failures=[], degradations=[]) == 'skipped'
    assert stages.classify(executed=True, failed_reason='整体报错', failures=[], degradations=[]) == 'failed'
    assert stages.classify(executed=True, failed_reason=None,
                           failures=[{'target': 'bars:MU', 'reason': 'x'}],
                           degradations=[{'kind': 'conflict', 'detail': ''}]) == 'partial'
    assert stages.classify(executed=True, failed_reason=None, failures=[],
                           degradations=[{'kind': 'conflict', 'detail': ''}]) == 'degraded'
    assert stages.classify(executed=True, failed_reason=None, failures=[], degradations=[]) == 'success'


def test_skip_groups_and_caps_samples():
    acc = stages.StageAcc()
    for i in range(12):
        acc.skip('trend_reclaim:insufficient_history', f'T{i}')
    assert acc.skips[0]['count'] == 12
    assert len(acc.skips[0]['samples']) == 10
    acc.skip('trend_reclaim:insufficient_history', 'T0')   # 重复样本去重，计数照加
    acc.skip('atr_unavailable')                            # 无样本同样计数
    assert acc.skips[0]['count'] == 13
    assert acc.skips[0]['samples'] == [f'T{i}' for i in range(10)]
    assert acc.skips[1] == {'reason': 'atr_unavailable', 'count': 1, 'samples': []}


def test_row_shape():
    rows = []
    ticks = iter((100.0, 100.35))          # timing() 读两次单调时钟：350 ms
    rec = _recorder(rows, clock=lambda: next(ticks))
    with rec.stage('collect') as acc:
        acc.input(20)
        acc.output(18)
        acc.skip('trend_reclaim:insufficient_history', 'MU')
        acc.fail('bars:AAPL', 'timeout')
        acc.degrade('fred_not_configured', 'FRED_API_KEY 未配置')
        acc.ref('data_fetch_runs', 7)
        acc.set_ref('marker', 'm')
        acc.detail['earnings'] = 3
    row = rows[0]
    assert set(row) == {'run_id', 'job', 'trigger', 'session', 'stage', 'stages_version', 'attempt', 'status',
                        'reason', 'input_count', 'output_count', 'skips', 'failures', 'degradations',
                        'outputs', 'detail', 'started_at', 'finished_at', 'duration_ms'}
    assert row['run_id'] == 'run-1' and row['job'] == 'workbench'
    assert row['trigger'] == 'manual_batch' and row['session'] == SESSION
    assert row['stage'] == 'collect' and row['stages_version'] == 'stages-5'
    assert row['status'] == 'partial' and row['reason'] is None
    assert row['input_count'] == 20 and row['output_count'] == 18
    assert row['skips'] == [{'reason': 'trend_reclaim:insufficient_history', 'count': 1, 'samples': ['MU']}]
    assert row['failures'] == [{'target': 'bars:AAPL', 'reason': 'timeout'}]
    assert row['degradations'] == [{'kind': 'fred_not_configured', 'detail': 'FRED_API_KEY 未配置'}]
    assert row['outputs'] == {'data_fetch_runs': [7], 'marker': 'm'}
    assert row['detail'] == {'earnings': 3, 'timings': {'measured_ms': 350}}    # sub-timings, not the duration
    assert row['started_at'] == WALL and row['finished_at'] == WALL
    assert row['attempt'] == 1
    assert row['duration_ms'] == 0                                              # D25: the wall span, here zero


def test_recorder_writes_attempt_and_job():
    rows = []
    default = _recorder(rows)
    default.skip('collect', 'x')
    assert (rows[0]['attempt'], rows[0]['job']) == (1, 'workbench')
    resumed = stages.StageRecorder('run-2', 'scheduled', SESSION, writer=rows.append, wall_now=lambda: WALL,
                                   clock=lambda: 0.0, attempt=2, job='daily')
    with resumed.stage('collect'):
        pass
    resumed.skip('screen', 'upstream_failed')
    assert [(r['stage'], r['attempt'], r['job']) for r in rows[1:]] == [('collect', 2, 'daily'), ('screen', 2, 'daily')]
    assert resumed.close(None)['written'] == 11              # still one row per stage of the (stages-5) set


def test_duration_ms_is_wall_span():
    """D25: duration_ms = finished_at - started_at on the wall clock, whatever the sub-timer measured."""
    rows = []
    walls = iter([WALL, WALL.replace(minute=32, second=30)])        # open -> finish: 2 min 30 s of wall time
    ticks = iter((10.0, 10.35))                                     # the monotonic sub-timer saw only 350 ms
    rec = stages.StageRecorder('run-w', 'scheduled', SESSION, writer=rows.append, wall_now=lambda: next(walls),
                               clock=lambda: next(ticks))
    with rec.stage('collect') as acc:
        acc.detail['timings'] = {'in_signals_ms': 12}
    row = rows[0]
    assert row['duration_ms'] == 150_000
    assert row['started_at'] == WALL and row['finished_at'] == WALL.replace(minute=32, second=30)
    assert row['detail']['timings'] == {'in_signals_ms': 12, 'measured_ms': 350}
    # a skipped stage has no span; a clock that steps back never yields a negative duration
    assert stages.StageRecorder('r', 'scheduled', SESSION, writer=rows.append, wall_now=lambda: WALL,
                                clock=lambda: 0.0)._row('plan', status='skipped', reason='x', acc=None,
                                                        started_at=WALL, finished_at=WALL)['duration_ms'] == 0
    backwards = stages.StageRecorder('r', 'scheduled', SESSION, writer=rows.append, wall_now=lambda: WALL,
                                     clock=lambda: 0.0)._row('plan', status='success', reason=None, acc=None,
                                                             started_at=WALL, finished_at=WALL.replace(second=0, minute=29))
    assert backwards['duration_ms'] == 0


def test_stage_context_writes_aborted_and_reraises():
    rows = []
    rec = _recorder(rows)
    with pytest.raises(RuntimeError, match='boom'):
        with rec.stage('collect') as acc:
            acc.input(2)
            raise RuntimeError('boom')
    assert rows[0]['status'] == 'failed'
    assert rows[0]['reason'] == 'aborted: boom'
    assert rows[0]['input_count'] == 2


def test_close_fills_remaining_stages():
    rows = []
    rec = _recorder(rows, run_id='r-skip')
    with rec.stage('collect') as acc:
        acc.input(3)
    assert rec.close(None) == {'written': 11, 'error': None}
    assert len(rows) == 11
    assert rows[0]['stage'] == 'collect' and rows[0]['status'] == 'success'
    rest = [row for row in rows if row['stage'] != 'collect']
    assert {row['stage'] for row in rest} == set(stages.STAGE_IDS) - {'collect'}
    assert all(row['status'] == 'skipped' and row['reason'] == 'not_reached' for row in rest)

    rows2 = []                             # 有异常：其余阶段记 failed / aborted
    rec2 = _recorder(rows2, run_id='r-fail')
    with rec2.stage('collect'):
        pass
    rec2.close(RuntimeError('x'))
    assert all(row['status'] == 'failed' and row['reason'] == 'aborted: x'
               for row in rows2 if row['stage'] != 'collect')


def test_finish_twice_writes_once():
    rows = []
    rec = _recorder(rows)
    acc = rec.open('signals')
    acc.input(5)
    rec.finish('signals')
    acc.output(9)
    rec.finish('signals')
    rec.skip('signals', 'late')            # 已写过：忽略
    assert len(rows) == 1
    assert rows[0]['status'] == 'success'
    assert rows[0]['input_count'] == 5 and rows[0]['output_count'] == 0


def test_skip_and_skip_many_write_skipped_rows():
    rows = []
    rec = _recorder(rows)
    rec.skip('push', 'manual_run')
    rec.skip_many(['signals', 'plan'], 'not_trading_day')
    assert [row['stage'] for row in rows] == ['push', 'signals', 'plan']
    assert all(row['status'] == 'skipped' for row in rows)
    assert rows[0]['reason'] == 'manual_run' and rows[1]['reason'] == 'not_trading_day'
    assert all(row['input_count'] == 0 and row['duration_ms'] == 0 for row in rows)


def test_writer_failure_is_swallowed():
    def boom(row):
        raise RuntimeError('db down')

    rec = stages.StageRecorder('run-1', 'scheduled', SESSION, writer=boom,
                               wall_now=lambda: WALL, clock=lambda: 0.0)
    with rec.stage('collect'):
        pass
    assert rec.close() == {'written': 0, 'error': 'db down'}


def test_stages_v4_remains_literal_ten_and_accounts_purpose():
    assert stages.STAGES_VERSION_V4 == 'stages-4'
    assert stages.STAGE_IDS_V4 == ('sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan', 'agent',
                                   'review', 'push', 'retro')
    stage = next(s for s in stages.STAGES if s.id == 'accounts')
    assert stage.title == '账户'
    assert stage.purpose == '结算模拟账户昨天的挂单与退出，给每个账户记一行当日权益，再由照计划执行账户机械挂单、Agent 自管账户决定明天买卖什么。'
    rows = []
    rec = stages.StageRecorder('v4', 'scheduled', SESSION, writer=rows.append, version='stages-4')
    rec.skip('accounts', 'not_in_v4')
    assert rec.close(None) == {'written': 10, 'error': None}
    assert [r['stage'] for r in rows] == list(stages.STAGE_IDS_V4)
