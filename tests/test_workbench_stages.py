"""Stage registry and recorder: order vs migration check, classify, accumulator, row shape."""
import re
from pathlib import Path


import investment_assistant.core.stages as stages


def _check_ids(migration: str) -> tuple[str, ...]:
    sql = Path('migrations', migration).read_text(encoding='utf-8')
    match = re.search(r"CHECK \(stage IN \(([^)]+)\)\)", sql)
    return tuple(re.findall(r"'([^']+)'", match.group(1)))


def test_stage_ids_match_migration_check():
    assert stages.STAGE_IDS_V1 == _check_ids('025_stage_runs.sql')       # stages-1：025 的七个阶段
    assert stages.STAGE_IDS_V2 == _check_ids('026_plan_timeline.sql')    # stages-2：026 的八个阶段
    assert stages.STAGE_IDS_V3 == _check_ids('027_growth_loop.sql')      # stages-3：027 的九个阶段
    assert stages.STAGE_IDS_V4 == _check_ids('028_daily_workflow.sql')      # stages-4：028 的十个阶段
    assert stages.STAGE_IDS_V5 == _check_ids('030_accounts.sql')        # stages-5：030 的十一个阶段
    assert stages.STAGE_IDS == _check_ids('031_five_layers.sql')        # stages-6：031 的十三个阶段
    assert stages.STAGE_IDS_BY_VERSION == {'stages-1': stages.STAGE_IDS_V1, 'stages-2': stages.STAGE_IDS_V2,
                                           'stages-3': stages.STAGE_IDS_V3, 'stages-4': stages.STAGE_IDS_V4,
                                           'stages-5': stages.STAGE_IDS_V5, 'stages-6': stages.STAGE_IDS}


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
