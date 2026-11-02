"""run_batch 的阶段记账（v2.4 §4；v2.5 §7.1 起 stages-2 八个阶段；v2.7 §9 起 stages-3 九个阶段）：计数与四族 fetch 行、
筛选与区制的产出引用对得上；非交易日推送之外的阶段整段跳过；信号、计划、Agent、推送的计数与产出引用；每次运行恰好 9 行
（未执行 027 时 8 行，未执行 026 时 7 行）。
复用 characterization 的假 fetcher，recorder 用默认写入，行落在一次性库的 stage_runs 里再读回。"""
import investment_assistant.workflow.repo as workflow_repo
from datetime import UTC, date, datetime, timedelta

import pytest

from test_workbench_batch_characterization import UNIVERSE, batch_deps
from test_workbench_service import (NOW, SESSION, Sources, _agent_settings, _ok_responses, _task_run,
                                    agent_deps, db, deps, seed_watchlist)
from investment_assistant.analysis import regime as regime_module
import investment_assistant.workflow.batch as service
import investment_assistant.core.stages as stages_module
from investment_assistant.workflow.recorder import StageRecorder
from investment_assistant.data.adapters import universe as universe_module

SATURDAY = datetime(2026, 10, 24, 22, 30, tzinfo=UTC)   # 纽约周六，非交易日


def _recorder(run_id: str, *, trigger: str = 'scheduled') -> StageRecorder:
    return StageRecorder(run_id, trigger, SESSION, wall_now=lambda: NOW,
                                       version=stages_module.STAGES_VERSION_V3)


def _stage(db, run_id: str, stage_id: str) -> dict:
    rows = [row for row in workflow_repo.stage_runs_for_run(db, run_id) if row['stage'] == stage_id]
    assert len(rows) == 1
    return rows[0]


def test_collect_counts_match_fetch_runs(db):
    """collect 的计数等于本次前四族 data_fetch_runs 之和；失败与降级挨着采集记。v2.12：另有 earnings 日期行与
    fundamentals / sec_text / sector 三个注册表族各一行，写进 outputs 但不进计数（同 universe）。"""
    summary = service.run_batch(tickers=['NVDA', 'MU'], now=NOW,
                                deps=batch_deps(fail={'MU'}, fail_news=()),
                                record_trigger='scheduled', run_id='run-collect',
                                stages=_recorder('run-collect'))
    fam = dict(db.execute("SELECT family, id FROM data_fetch_runs "
                          "WHERE run_id = 'run-collect'").fetchall())
    assert set(fam) == {'bars', 'news', 'sec_filings', 'fred', 'earnings', 'fundamentals', 'sec_text', 'sector'}
    totals = db.execute("SELECT sum(target_count), sum(ok_count) FROM data_fetch_runs "
                        "WHERE run_id = 'run-collect' AND family IN ('bars', 'news', 'sec_filings', 'fred')").fetchone()
    row = _stage(db, 'run-collect', 'collect')
    assert row['run_id'] == 'run-collect' and row['session'] == SESSION
    assert row['input_count'] == totals[0] and row['output_count'] == totals[1]
    assert row['outputs']['data_fetch_runs'] == [fam[f] for f in
                                                 ('bars', 'news', 'sec_filings', 'fred', 'earnings', 'fundamentals',
                                                  'sec_text', 'sector')]
    assert row['status'] == 'partial'
    assert {'target': 'bars:MU', 'reason': 'provider down'} in row['failures']
    assert {'target': 'sec_filings:MU', 'reason': 'edgar down'} in row['failures']      # v2.8 D21: one SEC request per ticker
    assert any(d['kind'] == 'fred_not_configured' for d in row['degradations'])
    earnings = row['detail']['earnings']                  # v2.8: plus the per-target items
    assert {k: earnings[k] for k in ('targets', 'ok', 'failed')} == {'targets': 2, 'ok': 2, 'failed': 0}
    assert len(earnings['items']) == 2


def test_screen_stage(db):
    seed_watchlist(db, [('AAA', ['core']), ('BBB', []), ('CCC', [])])
    summary = service.run_batch(now=NOW, deps=batch_deps(), stages=_recorder('run-screen'))
    row = _stage(db, 'run-screen', 'screen')
    assert row['status'] == 'success'
    assert row['input_count'] == len(UNIVERSE)                     # 范围成员数
    assert row['output_count'] == len(summary['screen']['selected'])
    assert row['outputs']['screen_run_id'] == db.execute(
        'SELECT id FROM screen_runs WHERE session = %s', [SESSION]).fetchone()[0]

    service.run_batch(tickers=['NVDA'], now=NOW, deps=batch_deps(), stages=_recorder('run-explicit'))
    row = _stage(db, 'run-explicit', 'screen')
    assert row['status'] == 'skipped' and row['reason'] == 'explicit_pool'

    broken = batch_deps()
    broken.universe = None

    def boom():
        raise universe_module.UniverseError('文件不可读')

    broken.universe_loader = boom
    service.run_batch(now=NOW, deps=broken, stages=_recorder('run-nouniverse'))
    row = _stage(db, 'run-nouniverse', 'screen')
    assert row['status'] == 'degraded'
    assert any(d['kind'] == 'universe_load_failed' for d in row['degradations'])


def test_regime_stage(db):
    seed_watchlist(db, [('AAA', ['core']), ('BBB', []), ('CCC', [])])
    summary = service.run_batch(now=NOW, deps=batch_deps(), stages=_recorder('run-regime'))
    row = _stage(db, 'run-regime', 'regime')
    assert row['input_count'] == len({'AAA', 'BBB', 'CCC'} | set(UNIVERSE))   # 广度样本
    assert row['output_count'] == 1
    assert row['outputs']['market_regime_id'] == str(db.execute(
        'SELECT id FROM market_regimes WHERE session = %s', [SESSION]).fetchone()[0])

    service.run_batch(tickers=['NVDA'], now=NOW, deps=batch_deps(), stages=_recorder('run-thin'))
    row = _stage(db, 'run-thin', 'regime')
    assert row['status'] == 'degraded'                            # 广度样本不足
    assert any(d['kind'] == 'label_insufficient' for d in row['degradations'])


def test_non_trading_day_skips_six():
    """周六早退：push 之前的阶段（含 review）整段 skipped，push 留给任务层，不写行。"""
    rows = []
    recorder = StageRecorder('run-closed', 'scheduled', date(2026, 10, 24),
                                            writer=rows.append, wall_now=lambda: SATURDAY,
                                            version=stages_module.STAGES_VERSION_V3)
    summary = service.run_batch(now=SATURDAY, skip_non_trading=True, deps=batch_deps(),
                                stages=recorder)
    assert summary['status'] == 'skipped'
    assert [row['stage'] for row in rows] == list(stages_module.STAGE_IDS_V3[:-1])
    assert all(row['status'] == 'skipped' and row['reason'] == 'not_trading_day'
               for row in rows)


# ---------------------------------------------------------------- signals / plan / agent / push

class ShortHistorySources(Sources):
    """NEW has only 30 sessions of history: every rule skips it as insufficient_history."""

    def bars(self, ticker, start, end_exclusive):
        out = super().bars(ticker, start, end_exclusive)
        return out.tail(30) if ticker == 'NEW' else out


@pytest.fixture
def wired(db, monkeypatch):
    """Task runs write job_reports into the disposable schema, so run_ids can be compared."""
    from contextlib import contextmanager

    @contextmanager
    def connect(url):
        yield db

    monkeypatch.setattr('investment_assistant.db.connect', connect)
    monkeypatch.setenv('INVESTMENT_ASSISTANT_DATABASE_URL', 'postgresql://wired-to-test-schema')
    monkeypatch.setattr('investment_assistant.services.settings.effective_notify_config',
                        lambda notify: notify)          # 测试库没有 notify_settings 表
    return db


def _sent(monkeypatch, out=None):
    """Replace the notifier, so the real push() / push_closed() build their payloads but send nothing."""
    from investment_assistant.notify import notifier

    calls = []

    def fake(task, notifications, notify_cfg, *, client=None):
        calls.append(len(notifications))
        return out if out is not None else {'sent': True, 'messages': len(notifications), 'channel': 'discord'}

    monkeypatch.setattr(notifier, 'send_notifications', fake)
    return calls


def _rows(db, run_id: str) -> dict[str, dict]:
    return {row['stage']: row for row in workflow_repo.stage_runs_for_run(db, run_id)}


def _job_run_id(db) -> str:
    return db.execute("SELECT run_id FROM job_reports WHERE task = 'workbench' "
                      'ORDER BY id DESC LIMIT 1').fetchone()[0]


def test_stage_runs_nine_rows(wired, monkeypatch):
    """定时、手动、非交易日、显式池、中途异常：每个 run_id 恰好 9 行（stages-3）。"""
    seed_watchlist(wired, [('NVDA', ['core'])])
    _sent(monkeypatch)
    saturday = datetime(2026, 10, 24, 22, 30, tzinfo=UTC)
    seen = []
    for trigger, moment in (('scheduled', NOW), ('manual', NOW + timedelta(minutes=5)),
                            ('scheduled', saturday)):
        result = _task_run(trigger, moment=moment)
        run_id = _job_run_id(wired)
        assert run_id == result['run_id']
        rows = workflow_repo.stage_runs_for_run(wired, run_id)
        assert [r['stage'] for r in rows].count('push') == 1
        assert {r['stage'] for r in rows} == set(stages_module.STAGE_IDS_V3) and len(rows) == 9
        assert {r['run_id'] for r in rows} == {run_id}
        assert {r['stages_version'] for r in rows} == {'stages-3'}
        assert result['summary']['stage_status'] == {'written': 9, 'error': None}
        seen.append(rows)
    assert {r['trigger'] for r in seen[0]} == {'scheduled'}
    assert {r['trigger'] for r in seen[1]} == {'manual_batch'}
    assert {r['status'] for r in seen[2] if r['stage'] != 'push'} == {'skipped'}
    assert [r['status'] for r in seen[2] if r['stage'] in ('review', 'retro')] == ['skipped', 'skipped']
    retro_rows = [next(r for r in rows if r['stage'] == 'retro') for rows in seen[:2]]
    assert [(r['status'], r['input_count'], r['output_count']) for r in retro_rows] == [('success', 0, 0)] * 2
    assert retro_rows[0]['detail']['events'] == 0 and retro_rows[0]['detail']['by_end_kind'] == {
        'position_closed': 0, 'stop': 0, 'target': 0, 'expired': 0, 'invalidated': 0}

    # 显式池：直接调 run_batch，再 close
    recorder = _recorder('run-explicit-pool')
    service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()), stages=recorder)
    assert recorder.close() == {'written': 9, 'error': None}
    rows = _rows(wired, 'run-explicit-pool')
    assert set(rows) == set(stages_module.STAGE_IDS_V3)
    assert rows['screen']['reason'] == 'explicit_pool'
    assert (rows['push']['status'], rows['push']['reason']) == ('skipped', 'not_reached')

    # 中途异常：已写的阶段保留，其余（含 review、retro）记 failed / aborted，仍是 9 行
    def boom(**_):
        raise service.Unavailable('boom')

    monkeypatch.setattr(regime_module, 'snapshot', boom)
    result = _task_run('scheduled', moment=NOW + timedelta(minutes=10))
    rows = workflow_repo.stage_runs_for_run(wired, result['run_id'])
    assert len(rows) == 9 and {r['stage'] for r in rows} == set(stages_module.STAGE_IDS_V3)
    assert {r['status'] for r in rows if r['stage'] in ('review', 'retro', 'push')} == {'failed'}


GROWTH_DROP = ('knowledge_citations, knowledge_draft_verdicts, review_card_verdicts, knowledge_drafts, '
               'knowledge_versions, knowledge_status, discussion_turns, knowledge_items, discussions, '
               'review_card_drafts, review_cards')


def test_without_migration_027_stages_2(wired, monkeypatch):
    """D29: 删掉 027 的十一张表 → 任务层按 stages-2 记账，每个 run_id 8 行，没有 retro 行；
    summary['errors'] 恰有一条 growth: migration_027_missing，其余阶段照常。"""
    seed_watchlist(wired, [('NVDA', ['core'])])
    _sent(monkeypatch)
    clean = _task_run('scheduled', moment=NOW - timedelta(days=1))
    wired.execute(f'DROP TABLE {GROWTH_DROP} CASCADE')
    wired.commit()
    for trigger, moment in (('scheduled', NOW), ('manual', NOW + timedelta(minutes=5)),
                            ('scheduled', datetime(2026, 10, 24, 22, 30, tzinfo=UTC))):
        result = _task_run(trigger, moment=moment)
        rows = workflow_repo.stage_runs_for_run(wired, result['run_id'])
        assert len(rows) == 8 and [r['stage'] for r in rows].count('retro') == 0
        assert {r['stage'] for r in rows} == set(stages_module.STAGE_IDS_V2)
        assert {r['stages_version'] for r in rows} == {'stages-2'}
        assert result['summary']['stage_status'] == {'written': 8, 'error': None}
        if trigger == 'scheduled' and moment == NOW:
            assert result['summary']['errors'].count('growth: migration_027_missing') == 1
            assert 'retro' not in result['summary']
            by_stage = {r['stage']: r for r in rows}
            assert {by_stage[name]['status'] for name in ('collect', 'screen', 'regime', 'signals', 'plan', 'agent',
                                                          'review')} <= {'success', 'partial', 'degraded', 'skipped'}
    assert 'growth: migration_027_missing' not in clean['summary']['errors']


def test_without_migration_026_runs_record_stages_1(wired, monkeypatch):
    """D22: 删掉 026 的三张表 → 任务层按 stages-1 记账，每个 run_id 7 行，没有 review 行。"""
    seed_watchlist(wired, [('NVDA', ['core'])])
    _sent(monkeypatch)
    wired.execute('DROP TABLE plan_reviews, user_inputs, agent_plan_versions CASCADE')
    wired.commit()
    for trigger, moment in (('scheduled', NOW), ('manual', NOW + timedelta(minutes=5)),
                            ('scheduled', datetime(2026, 10, 24, 22, 30, tzinfo=UTC))):
        result = _task_run(trigger, moment=moment)
        rows = workflow_repo.stage_runs_for_run(wired, result['run_id'])
        assert len(rows) == 7 and [r['stage'] for r in rows].count('review') == 0
        assert {r['stage'] for r in rows} == set(stages_module.STAGE_IDS_V1)
        assert {r['stages_version'] for r in rows} == {'stages-1'}
        assert result['summary']['stage_status'] == {'written': 7, 'error': None}
        if trigger == 'scheduled' and moment == NOW:
            assert result['summary']['errors'].count('timeline: migration_026_missing') == 1
            assert result['summary']['review'] == {'objects': 0, 'tokens': {'input': 0, 'output': 0},
                                                   'outcomes': {'maintain': 0, 'revise': 0, 'invalidate': 0,
                                                                'skipped': 0, 'error': 0}}


def test_signals_and_plan_counts(db, monkeypatch):
    from investment_assistant.execution import plan_levels as plans

    real = plans.build_rule_plan

    def build(**kwargs):
        return {'skip': 'atr_unavailable'} if kwargs['ticker'] == 'MU' else real(**kwargs)

    monkeypatch.setattr(plans, 'build_rule_plan', build)
    summary = service.run_batch(tickers=['NEW', 'MU', 'NVDA'], now=NOW, deps=deps(ShortHistorySources()),
                                run_id='run-counts', stages=_recorder('run-counts'))
    rows = _rows(db, 'run-counts')
    signals, plan = rows['signals'], rows['plan']

    assert signals['status'] == 'success'
    assert signals['input_count'] == 3
    candidates = sum(len(t['candidates']) for t in summary['tickers'].values())
    assert signals['output_count'] == candidates > 0
    stored = db.execute("SELECT id FROM signal_candidates WHERE run_id = 'run-counts'").fetchall()
    assert sorted(signals['outputs']['inserted']) == sorted(str(r[0]) for r in stored)
    assert signals['outputs']['existing'] == []
    skip = {group['reason']: group for group in signals['skips']}
    assert skip['trend_reclaim:insufficient_history']['samples'] == ['NEW']
    assert skip['trend_reclaim:insufficient_history']['count'] == 1

    assert plan['input_count'] == 2                                  # MU 与 NVDA 是无冲突的买入单元
    assert summary['plans'] == {'MU': 'skip: atr_unavailable', 'NVDA': 'inserted'}
    assert plan['output_count'] == 1 == sum(not v.startswith('skip:') for v in summary['plans'].values())
    assert [(g['reason'], g['count'], g['samples']) for g in plan['skips']] == \
        [('atr_unavailable', 1, ['MU'])]
    ids = db.execute("SELECT id FROM trade_plans WHERE kind = 'rule'").fetchall()
    assert plan['outputs'] == {'inserted': [str(r[0]) for r in ids], 'existing': []}
    assert plan['status'] == 'success'
    assert plan['detail']['status'] == {'ok': 1, 'invalid': 0}


def test_plan_detail_counts_ok_and_invalid(db):
    """detail.status 与 trade_plans.status 对账：MU 的财报落在计划有效期内 → invalid。"""
    sources = Sources()
    sources_earnings = sources.earnings

    def earnings(ticker, **kwargs):
        if ticker == 'MU':
            return {'Earnings Date': [date(2026, 10, 26)]}        # 信号日后的第一个交易日
        return sources_earnings(ticker, **kwargs)

    for run_id in ('run-invalid-1', 'run-invalid-2'):             # 第二次重跑：全部是 existing
        service.run_batch(tickers=['MU', 'NVDA'], now=NOW, deps=deps(sources, earnings=earnings),
                          stages=_recorder(run_id))
    status = dict(db.execute('SELECT id::text, status FROM trade_plans').fetchall())
    assert sorted(status.values()) == ['invalid', 'ok']           # 不是空断言：两种都有
    for run_id in ('run-invalid-1', 'run-invalid-2'):
        row = _rows(db, run_id)['plan']
        ids = row['outputs']['inserted'] + row['outputs']['existing']
        assert row['output_count'] == len(ids) == 2
        assert row['detail']['status'] == {'ok': sum(status[i] == 'ok' for i in ids),
                                           'invalid': sum(status[i] == 'invalid' for i in ids)} \
            == {'ok': 1, 'invalid': 1}


def test_rerun_marks_existing_plans(db):
    service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()), stages=_recorder('run-first'))
    service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()), stages=_recorder('run-second'))
    first, second = _rows(db, 'run-first'), _rows(db, 'run-second')
    assert len(first['plan']['outputs']['inserted']) == 1 and first['plan']['outputs']['existing'] == []
    assert second['plan']['outputs']['inserted'] == []
    assert second['plan']['outputs']['existing'] == first['plan']['outputs']['inserted']
    assert second['plan']['output_count'] == 1
    assert second['signals']['outputs']['inserted'] == []
    assert sorted(second['signals']['outputs']['existing']) == sorted(first['signals']['outputs']['inserted'])


def test_agent_unavailable_beats_empty(db, scripted_model):
    d = agent_deps(scripted_model, [], settings=_agent_settings(connection='disconnected'))
    d.bar_fetcher = ShortHistorySources().bars                         # 没有买入单元
    service.run_batch(tickers=['NEW'], now=NOW, deps=d, stages=_recorder('run-noagent'))
    row = _rows(db, 'run-noagent')['agent']
    assert row['status'] == 'skipped' and row['reason'] == 'agent_unavailable: not_connected'


def test_agent_stage_counts(db, scripted_model):
    d = agent_deps(scripted_model, _ok_responses('AAA'))               # AAA 有回复；BBB 回复用完 → error
    service.run_batch(tickers=['AAA', 'BBB'], now=NOW, deps=d, stages=_recorder('run-agent'))
    row = _rows(db, 'run-agent')['agent']
    runs = db.execute('SELECT id, ticker, status, plan_id FROM agent_runs ORDER BY ticker').fetchall()
    assert [(r[1], r[2]) for r in runs] == [('AAA', 'ok'), ('BBB', 'error')]
    assert row['status'] == 'partial'
    assert row['input_count'] == 2 and row['output_count'] == 1
    assert [f['target'] for f in row['failures']] == ['BBB']
    assert row['outputs']['agent_run_ids'] == [str(runs[0][0]), str(runs[1][0])]
    assert row['outputs']['plan_ids'] == [str(runs[0][3])]
    assert set(row['detail']['tokens']) == {'input', 'output'}

    # 重跑：已完成的单元记进 skips，不再产出新的运行
    service.run_batch(tickers=['AAA', 'BBB'], now=NOW, deps=agent_deps(scripted_model, []),
                      stages=_recorder('run-agent-again'))
    again = _rows(db, 'run-agent-again')['agent']
    assert again['output_count'] == 0 and again['input_count'] == 2
    assert [(g['reason'], g['samples']) for g in again['skips']] == [('already_completed', ['AAA'])]
    assert [f['target'] for f in again['failures']] == ['BBB']


def test_push_stage_outcomes(wired, monkeypatch):
    seed_watchlist(wired, [('NVDA', ['core'])])

    def run(outcome, trigger='scheduled', moment=NOW):
        _sent(monkeypatch, outcome)
        result = _task_run(trigger, moment=moment)
        return _rows(wired, result['run_id'])['push']

    manual = run(None, 'manual')
    assert (manual['status'], manual['reason']) == ('skipped', 'manual_run')

    disabled = run({'sent': False, 'messages': 0, 'reason': 'task_disabled'})
    assert (disabled['status'], disabled['reason']) == ('skipped', 'task_disabled')

    failed = run({'sent': False, 'messages': 1, 'reason': 'send_failed: discord down'})
    assert failed['status'] == 'failed' and failed['reason'] == 'send_failed: discord down'
    assert failed['outputs']['push']['messages'] == 1

    empty = run({'sent': False, 'messages': 0, 'reason': 'no_notifications'})
    assert (empty['status'], empty['input_count'], empty['output_count']) == ('success', 0, 0)

    first = run(None)
    assert first['status'] == 'success' and first['output_count'] > 0
    assert first['outputs']['push']['sent'] is True
    assert first['outputs']['push']['messages'] == first['output_count']

    again = run(None)                                  # 同日已推送成功：真实 push() 判定，不再发送
    assert (again['status'], again['reason']) == ('skipped', 'already_pushed')

    closed = run({'sent': True, 'messages': 1, 'channel': 'discord'},
                 moment=datetime(2026, 10, 24, 22, 30, tzinfo=UTC))
    assert closed['status'] == 'success' and closed['detail']['closed'] is True
    assert closed['output_count'] == 1
    closed_failed = run({'sent': False, 'messages': 0, 'reason': 'send_failed: down'},
                        moment=datetime(2026, 10, 24, 22, 30, tzinfo=UTC))
    assert closed_failed['status'] == 'failed' and closed_failed['detail']['closed'] is True


def test_abort_marks_remaining_failed(wired, monkeypatch):
    seed_watchlist(wired, [('AAA', ['core']), ('BBB', [])])

    def boom(**_):
        raise service.Unavailable('boom')

    monkeypatch.setattr(regime_module, 'snapshot', boom)
    result = _task_run('scheduled', task_deps=batch_deps())
    assert result['status'] == 'error' and result['summary'] == {'error': 'boom'}
    rows = _rows(wired, result['run_id'])
    assert set(rows) == set(stages_module.STAGE_IDS_V3)
    assert rows['collect']['status'] in ('success', 'partial')
    assert rows['screen']['status'] == 'success'
    assert (rows['regime']['status'], rows['regime']['reason']) == ('failed', 'aborted: boom')
    for stage in ('signals', 'plan', 'agent', 'review', 'retro', 'push'):
        assert (rows[stage]['status'], rows[stage]['reason']) == ('failed', 'aborted: boom')


def test_missing_stage_runs_table_does_not_fail_batch(wired):
    seed_watchlist(wired, [('NVDA', ['core'])])
    with_table = _task_run('manual')
    assert with_table['summary']['stage_status'] == {'written': 9, 'error': None}

    wired.execute('DROP TABLE stage_runs')
    wired.commit()
    without = _task_run('manual')
    status = without['summary']['stage_status']
    assert status['written'] == 0 and status['error']
    assert without['summary']['errors'] == with_table['summary']['errors']
    assert without['status'] == with_table['status']


# ---------------------------------------------------------------- derivation check after the batch (v2.4 §8)

def _plan_snapshot(db) -> list[tuple]:
    return db.execute('SELECT id, ticker, stop, target1, entry_low, entry_high, sizing::text, status '
                      'FROM trade_plans ORDER BY id').fetchall()


def test_batch_derivation_counts_clean(db):
    summary = service.run_batch(tickers=['NVDA', 'MU'], now=NOW, deps=deps(Sources()), run_id='run-clean',
                                stages=_recorder('run-clean'))
    plan = _rows(db, 'run-clean')['plan']
    count = db.execute('SELECT count(*) FROM trade_plans').fetchone()[0]
    assert count == 2 and plan['status'] == 'success'
    derived = plan['detail']['derivation']
    assert derived['checked'] == count and derived['consistent'] == count
    assert derived['mismatch'] == 0 and derived['incomplete'] == 0
    assert sorted(i['plan_id'] for i in derived['items']) == sorted(plan['outputs']['inserted'])
    assert not [e for e in summary['errors'] if e.startswith('derivation_mismatch')]

    # 重跑：已存计划（existing）同样被校验
    again = service.run_batch(tickers=['NVDA', 'MU'], now=NOW, deps=deps(Sources()), run_id='run-clean-2',
                              stages=_recorder('run-clean-2'))
    plan2 = _rows(db, 'run-clean-2')['plan']
    assert plan2['outputs']['inserted'] == [] and len(plan2['outputs']['existing']) == 2
    assert plan2['detail']['derivation']['checked'] == 2 and plan2['detail']['derivation']['mismatch'] == 0
    assert not [e for e in again['errors'] if e.startswith('derivation_mismatch')]


def test_batch_without_recorder_does_not_check(db, monkeypatch):
    import investment_assistant.views.derivation as derivation_view

    def boom(_):
        raise AssertionError('stages=None 时不得校验')

    monkeypatch.setattr(derivation_view, 'check_plans', boom)
    summary = service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()))
    assert summary['errors'] == [] and summary['plans'] == {'NVDA': 'inserted'}


def test_batch_derivation_detects_tampered_plan(db):
    """真实篡改：改掉已存的 stop，重跑时 existing 计划被推导判为 mismatch。没有校验这条测试会失败。"""
    service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()), run_id='run-orig',
                      stages=_recorder('run-orig'))
    db.execute('UPDATE trade_plans SET stop = stop + 1')
    db.commit()
    before = _plan_snapshot(db)
    summary = service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()), run_id='run-tampered',
                                stages=_recorder('run-tampered'))
    plan_id = str(before[0][0])
    row = _rows(db, 'run-tampered')['plan']
    derived = row['detail']['derivation']
    assert derived['mismatch'] == 1 and derived['consistent'] == 0
    assert [(i['plan_id'], i['status']) for i in derived['items']] == [(plan_id, 'mismatch')]
    assert row['status'] == 'degraded'
    assert f'derivation_mismatch: NVDA {plan_id}' in summary['errors']
    assert _plan_snapshot(db) == before


def test_batch_derivation_mismatch_reported(wired, monkeypatch):
    """注入一份 mismatch：plan 阶段 degraded，errors 有一条；计划行与推送里的计划行与未注入时相同。"""
    import investment_assistant.views.derivation as derivation_view
    from investment_assistant.review import push_digest as digest

    seed_watchlist(wired, [('NVDA', ['core'])])
    _sent(monkeypatch)
    built = []
    real_build = digest.build

    def build(**kwargs):
        out = real_build(**kwargs)
        built.append(out['plans'])
        return out

    monkeypatch.setattr(digest, 'build', build)
    clean = _task_run('scheduled')
    wired.commit()
    assert clean['summary']['errors'] == [] and len(built) == 1 and built[0]
    plans_before = _plan_snapshot(wired)
    assert len(plans_before) == 1

    wired.execute('DELETE FROM job_reports')            # 让第二次定时运行不被「今日已推送」挡住
    wired.execute('DELETE FROM stage_runs')
    wired.commit()
    real_check = derivation_view.check_plans

    def injected(plan_ids):
        out = real_check(plan_ids)
        out['items'][0]['status'] = 'mismatch'
        out['consistent'] -= 1
        out['mismatch'] += 1
        return out

    monkeypatch.setattr(derivation_view, 'check_plans', injected)
    result = _task_run('scheduled')
    wired.commit()
    plan_id = str(plans_before[0][0])
    assert result['summary']['errors'] == [f'derivation_mismatch: NVDA {plan_id}']
    assert result['status'] == 'error'
    row = _rows(wired, result['run_id'])['plan']
    assert row['status'] == 'degraded'
    assert [d['kind'] for d in row['degradations']] == ['derivation_mismatch']
    assert row['detail']['derivation']['mismatch'] == 1
    assert _plan_snapshot(wired) == plans_before
    assert len(built) == 2 and built[1] == built[0]


def test_batch_derivation_check_error(db, monkeypatch):
    import investment_assistant.views.derivation as derivation_view

    base = service.run_batch(tickers=['NVDA', 'MU'], now=NOW, deps=deps(Sources()), run_id='run-base',
                             stages=_recorder('run-base'))
    plans_before = _plan_snapshot(db)

    def boom(_):
        raise RuntimeError('derivation exploded')

    monkeypatch.setattr(derivation_view, 'check_plans', boom)
    summary = service.run_batch(tickers=['NVDA', 'MU'], now=NOW, deps=deps(Sources()), run_id='run-boom',
                                stages=_recorder('run-boom'))
    assert summary['errors'] == ['derivation_mismatch: * derivation exploded']
    assert summary['plans'] == {'NVDA': 'existing', 'MU': 'existing'} and base['plans'] == {
        'NVDA': 'inserted', 'MU': 'inserted'}
    rows = _rows(db, 'run-boom')
    assert set(rows) == set(stages_module.STAGE_IDS_V3[:-1])            # push 由任务层记
    assert rows['plan']['status'] == 'degraded'
    assert rows['plan']['degradations'] == [{'kind': 'derivation_check_error', 'detail': 'derivation exploded'}]
    assert 'derivation' not in rows['plan']['detail']
    assert _plan_snapshot(db) == plans_before

    def bad_id(_):
        raise ValueError('计划 id 不是合法的 UUID：x')

    monkeypatch.setattr(derivation_view, 'check_plans', bad_id)
    again = service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()), run_id='run-badid',
                              stages=_recorder('run-badid'))
    assert again['errors'] == ['derivation_mismatch: * 计划 id 不是合法的 UUID：x']


def test_agent_stage_checks_only_its_own_plans(db, scripted_model):
    d = agent_deps(scripted_model, _ok_responses('AAA'))
    summary = service.run_batch(tickers=['AAA'], now=NOW, deps=d, run_id='run-agent-check',
                                stages=_recorder('run-agent-check'))
    rows = _rows(db, 'run-agent-check')
    [agent_plan] = rows['agent']['outputs']['plan_ids']
    derived = rows['agent']['detail']['derivation']
    assert derived['checked'] == 1 and derived['consistent'] == 1
    assert [i['plan_id'] for i in derived['items']] == [agent_plan]
    assert rows['plan']['detail']['derivation']['checked'] == 1          # 规则计划只在 plan 阶段
    assert [i['plan_id'] for i in rows['plan']['detail']['derivation']['items']] != [agent_plan]
    assert not [e for e in summary['errors'] if e.startswith('derivation_mismatch')]


def test_skipped_agent_stage_has_no_derivation(db, scripted_model):
    d = agent_deps(scripted_model, [], settings=_agent_settings(connection='disconnected'))
    service.run_batch(tickers=['NVDA'], now=NOW, deps=d, run_id='run-noagent2', stages=_recorder('run-noagent2'))
    row = _rows(db, 'run-noagent2')['agent']
    assert row['status'] == 'skipped' and 'derivation' not in row['detail']
