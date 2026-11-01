"""推导与阶段证据的只读读取用例（v2.4 §8–§9）：计划经真实批处理生成，再按 id 回读推导。

夹具沿用 test_workbench_stage_runs（假 fetcher、recorder、一次性库）；
推导读到的是计划生成时的那一行，日线新修订、更正快照都不改变结论；读取全程只读。"""
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import investment_assistant.workflow.repo as workflow_repo
import uuid
from datetime import date, timedelta
from decimal import Decimal

import psycopg
import pytest

from investment_assistant.core import tx as core_tx

from test_workbench_service import (NOW, SESSION, Sources, _ok_responses, _task_run, agent_deps, db, deps,
                                    seed_watchlist)
from test_workbench_stage_runs import _recorder, _rows, _sent, wired   # noqa: F401  (fixtures)
from investment_assistant.config import WorkbenchConfig
import investment_assistant.core.tx as service
import investment_assistant.workflow.batch as d_workflow_batch
from investment_assistant.execution import account_records as account
from investment_assistant.execution import plan_levels as plans
from investment_assistant.derivation import chain as derivation
import investment_assistant.views.derivation as view
import investment_assistant.core.stages as stages_module

PRE_V24 = 'v2.4 之前生成，无阶段证据'
NO_TABLE = '迁移 025 未执行，暂无阶段证据'


def _batch(db, run_id, *, tickers=('NVDA',), sources=None, now=NOW, **kwargs):
    kwargs.setdefault('deps', deps(sources or Sources()))
    summary = d_workflow_batch.run_batch(tickers=list(tickers), now=now, run_id=run_id,
                                stages=_recorder(run_id), **kwargs)
    db.commit()
    return summary


def _plan_ids(db, kind='rule'):
    return [str(r[0]) for r in db.execute(
        'SELECT id FROM trade_plans WHERE kind = %s ORDER BY ticker, created_at', [kind]).fetchall()]


def _counts(db):
    tables = ('trade_plans', 'signal_candidates', 'stage_runs', 'data_fetch_runs', 'job_reports', 'price_bars')
    return {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in tables}


# ---------------------------------------------------------------- plan derivation

def test_plan_derivation_consistent_from_batch(wired, monkeypatch):
    """批处理生成的每份计划回读后都 consistent；run.run_id 就是写入它的那次 job_reports.run_id。"""
    seed_watchlist(wired, [('NVDA', ['core']), ('MU', ['core'])])
    _sent(monkeypatch)
    result = _task_run('scheduled')
    wired.commit()
    ids = _plan_ids(wired)
    assert len(ids) == 2
    for plan_id in ids:
        out = view.plan_derivation(plan_id)
        assert out['consistency'] == {'status': 'consistent', 'mismatches': [], 'missing': []}
        assert out['derivation_version'] == derivation.DERIVATION_VERSION
        assert out['evidence'] == derivation.EVIDENCE_TEXT
        assert out['trigger_note'] == derivation.TRIGGER_NOTE
        assert out['plan']['id'] == plan_id
        run = out['run']
        assert run['ready'] is True and run['run_id'] == result['run_id']
        assert run['stage'] == 'plan' and run['status'] in ('success', 'partial', 'degraded')
        assert 'derivation' in run
        assert out['trigger'] and all(c['clauses'] for c in out['trigger'])
    assert result['run_id'] == wired.execute(
        "SELECT run_id FROM job_reports WHERE task = 'workbench'").fetchone()[0]


def test_agent_and_own_plans_derive_consistently(db, scripted_model):
    """Agent 计划按 agent_run_id 回读运行；自定计划按 based_on_id 回读规则计划。"""
    d = agent_deps(scripted_model, _ok_responses('AAA'))
    d_workflow_batch.run_batch(tickers=['AAA'], now=NOW, deps=d, run_id='run-agent', stages=_recorder('run-agent'))
    db.commit()
    [agent_id] = _plan_ids(db, 'agent')
    out = view.plan_derivation(agent_id)
    assert out['consistency']['status'] == 'consistent', out['consistency']
    assert out['plan']['plan_version'] == 'agent-3'                       # v2.9：首版是 agent-3
    assert view.load_rows(db, execution_repo.plan_by_id(db, agent_id)).version['version_no'] == 0
    assert out['inputs']['agent_run']['id'] is not None
    assert out['run']['stage'] == 'agent' and out['run']['run_id'] == 'run-agent'

    [rule_id] = _plan_ids(db, 'rule')
    rule = execution_repo.plan_by_id(db, rule_id)
    bars = data_repo.read_bars(db, 'AAA', SESSION, cutoff_at=NOW)
    levels = {k: rule[k] + Decimal('0.01') for k in ('entry_low', 'entry_high', 'stop', 'target1')}
    own = plans.build_own_plan(rule_plan=rule, levels=levels, ticker='AAA', signal_date=SESSION, bars=bars,
                               earnings_row=data_repo.latest_earnings(db, 'AAA', cutoff_at=NOW), snapshot=None,
                               exposure={'ticker_value': Decimal(0), 'total_value': Decimal(0)},
                               params=WorkbenchConfig())
    stored = execution_repo.insert_plan(db, {**own, 'request_id': str(uuid.uuid4()), 'request_hash': 'h' * 64})
    db.commit()
    out = view.plan_derivation(str(stored['id']))
    assert out['consistency']['status'] == 'consistent', out['consistency']
    assert out['run'] == {'ready': True, 'run_id': None, 'note': PRE_V24}   # 自定计划不是批处理产出


def test_agent2_plan_without_version_row_is_incomplete(db, scripted_model):
    """agent-2 计划回读版本行；读不到（没有这一版、或迁移 026 未执行）→ incomplete，不抛异常。"""
    d_workflow_batch.run_batch(tickers=['AAA'], now=NOW, deps=agent_deps(scripted_model, _ok_responses('AAA')))
    db.commit()
    [agent_id] = _plan_ids(db, 'agent')
    plan = execution_repo.plan_by_id(db, agent_id)
    rows = view.load_rows(db, plan)
    assert rows.missing == [] and str(rows.version['plan_id']) == agent_id

    orphan = execution_repo.insert_plan(db, {k: v for k, v in plan.items() if k not in ('id', 'created_at')})
    db.commit()                                                               # 同样的计划行，但没有版本行
    out = view.plan_derivation(str(orphan['id']))
    assert out['consistency']['status'] == 'incomplete'
    assert out['consistency']['missing'] == [f"agent_plan_versions:{orphan['id']}"]

    db.execute('DROP TABLE plan_reviews, user_inputs, agent_plan_versions CASCADE')
    db.commit()
    out = view.plan_derivation(agent_id)
    assert out['consistency']['missing'] == [f'agent_plan_versions:{agent_id}']


def test_load_rows_shapes_match_what_derivation_needs(db):
    """JSONB / NUMERIC 读回的类型：日线数值是 Decimal，财报日是 date，equity 是 Decimal。"""
    account.record_snapshot(db, {'request_id': str(uuid.uuid4()), 'as_of': (SESSION - timedelta(days=2)).isoformat(),
                                 'equity': '100000'}, now=NOW - timedelta(hours=1))
    _batch(db, 'run-shapes')
    [plan_id] = _plan_ids(db)
    plan = execution_repo.plan_by_id(db, plan_id)
    rows = view.load_rows(db, plan)
    assert rows.missing == []
    assert [b['id'] for b in rows.bars] == plan['inputs']['bar_ids']
    assert len(rows.bars) == 15
    for bar in rows.bars:
        assert all(isinstance(bar[k], Decimal) for k in ('open', 'high', 'low', 'close', 'adj_close'))
        assert isinstance(bar['price_date'], date) and bar['revision'] == 1
    assert isinstance(rows.earnings['earnings_date'], date)
    assert isinstance(rows.snapshot['equity'], Decimal) and rows.snapshot['equity'] == Decimal('100000')
    assert rows.rule_plan is None and rows.agent_run is None
    assert rows.regime is None or rows.regime['label']
    assert rows.candidates and all(isinstance(c['inputs'], dict) for c in rows.candidates)
    assert isinstance(plan['inputs']['exposure'], dict)
    assert derivation.derive_plan(plan, rows)['consistency']['status'] == 'consistent'


def test_load_rows_reports_unreadable_ids(db):
    _batch(db, 'run-gone')
    plan = execution_repo.plan_by_id(db, _plan_ids(db)[0])
    ids = plan['inputs']['bar_ids']
    broken = {**plan, 'inputs': {**plan['inputs'], 'bar_ids': ids[:-1] + [999999999],
                                 'snapshot_id': str(uuid.uuid4()), 'earnings_date_id': str(uuid.uuid4())}}
    rows = view.load_rows(db, broken)
    assert 'price_bars:999999999' in rows.missing
    assert [b['id'] for b in rows.bars] == ids[:-1]
    out = derivation.derive_plan(broken, rows)
    assert out['consistency']['status'] == 'incomplete'
    assert 'bar_ids' in out['consistency']['missing']
    assert any(m.startswith('account_snapshots:') for m in out['consistency']['missing'])


class SixDecimalSources(Sources):
    """Prices with 6 decimals and true ranges of about 0.12: the float and Decimal paths must agree."""

    def bars(self, ticker, start, end_exclusive):
        df = super().bars(ticker, start, end_exclusive)
        close = (df['Close'] * 0.7 + 0.123457).round(6)
        out = df.copy()
        out['Close'] = close
        out['Open'] = close
        out['High'] = (close + 0.123457).round(6)
        out['Low'] = (close - 0.098765).round(6)
        return out


def test_derivation_float_and_decimal_paths_agree(db):
    _batch(db, 'run-six', sources=SixDecimalSources())
    [plan_id] = _plan_ids(db)
    plan = execution_repo.plan_by_id(db, plan_id)
    assert plan['ref_price'] != plan['ref_price'].quantize(Decimal('0.01'))   # 真有 6 位小数
    assert plan['atr'] < 1 and plan['atr'] != plan['atr'].quantize(Decimal('0.01'))
    out = view.plan_derivation(plan_id)
    assert out['consistency'] == {'status': 'consistent', 'mismatches': [], 'missing': []}
    tr1 = next(s for s in out['steps'] if s['key'] == 'tr_1')
    assert tr1['result'] is not None


def test_derivation_reads_exact_bar_revision(db):
    """日线后来有了新修订：推导仍按计划记录的原 id 读取，结论不变。"""
    _batch(db, 'run-rev')
    [plan_id] = _plan_ids(db)
    plan = execution_repo.plan_by_id(db, plan_id)
    last_id = plan['inputs']['bar_ids'][-1]
    before = db.execute('SELECT ticker, price_date, open, high, low, close, adj_close, volume, split_factor, revision '
                        'FROM price_bars WHERE id = %s', [last_id]).fetchone()
    corrected = {'price_date': before[1], 'open': float(before[2]), 'high': float(before[3]),
                 'low': float(before[4]), 'close': float(before[5]) + 1.0, 'adj_close': float(before[6]) + 1.0,
                 'volume': before[7], 'split_factor': float(before[8])}
    assert data_repo.store_bars(db, before[0], [corrected], now=NOW + timedelta(hours=1))['inserted'] == 1
    db.commit()
    assert db.execute('SELECT max(revision) FROM price_bars WHERE ticker = %s AND price_date = %s',
                      [before[0], before[1]]).fetchone()[0] == before[9] + 1
    out = view.plan_derivation(plan_id)
    assert out['consistency'] == {'status': 'consistent', 'mismatches': [], 'missing': []}
    last = out['inputs']['bars'][-1]
    assert last['id'] == str(last_id) and int(last['revision']) == before[9]
    assert Decimal(last['close']) == before[5]


def test_snapshot_superseded_still_consistent(db):
    first = account.record_snapshot(db, {'request_id': str(uuid.uuid4()),
                                         'as_of': (SESSION - timedelta(days=2)).isoformat(), 'equity': '100000'},
                                    now=NOW - timedelta(hours=1))['record']
    db.commit()
    _batch(db, 'run-snap')
    [plan_id] = _plan_ids(db)
    plan = execution_repo.plan_by_id(db, plan_id)
    assert str(plan['inputs']['snapshot_id']) == str(first['id'])
    assert plan['sizing']['status'] != 'no_equity'
    account.record_snapshot(db, {'request_id': str(uuid.uuid4()),
                                 'as_of': (SESSION - timedelta(days=2)).isoformat(), 'equity': '150000',
                                 'supersedes_id': str(first['id'])}, now=NOW + timedelta(hours=1))
    db.commit()
    assert str(execution_repo.snapshot_head(db)['id']) != str(first['id'])
    out = view.plan_derivation(plan_id)
    assert out['consistency'] == {'status': 'consistent', 'mismatches': [], 'missing': []}
    assert out['inputs']['snapshot']['id'] == str(first['id'])
    assert Decimal(out['inputs']['snapshot']['equity']) == Decimal('100000')


def test_run_evidence_points_to_inserting_run(wired, monkeypatch):
    """同一 NOW 先手动、后定时各跑一次：计划是第一次插入的，第二次只是 existing。"""
    seed_watchlist(wired, [('NVDA', ['core'])])
    _sent(monkeypatch)
    manual = _task_run('manual', moment=NOW)
    wired.commit()
    scheduled = _task_run('scheduled', moment=NOW)
    wired.commit()
    assert manual['run_id'] != scheduled['run_id']
    [plan_id] = _plan_ids(wired)
    assert _rows(wired, manual['run_id'])['plan']['outputs']['inserted'] == [plan_id]
    assert _rows(wired, scheduled['run_id'])['plan']['outputs']['existing'] == [plan_id]
    run = view.plan_derivation(plan_id)['run']
    assert run['run_id'] == manual['run_id'] and run['stage'] == 'plan'


def test_pre_v24_plan_has_note(db):
    d_workflow_batch.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()))   # 没有 recorder：不写阶段行
    db.commit()
    [plan_id] = _plan_ids(db)
    out = view.plan_derivation(plan_id)
    assert out['run'] == {'ready': True, 'run_id': None, 'note': PRE_V24}
    assert out['consistency']['status'] == 'consistent'


def test_missing_stage_runs_table_note(db):
    _batch(db, 'run-drop')
    [plan_id] = _plan_ids(db)
    db.execute('DROP TABLE stage_runs')
    db.commit()
    out = view.plan_derivation(plan_id)
    assert out['run'] == {'ready': False, 'note': NO_TABLE}
    assert out['consistency']['status'] == 'consistent'          # 推导不依赖 stage_runs
    assert view.stage_runs_view(session=SESSION) == {'stages': list(stages_module.STAGES),
                                                     'stage_runs_ready': False, 'runs': []}
    assert view.stage_runs_view(run_id='run-drop')['runs'] == []


def test_plan_derivation_errors(db):
    with pytest.raises(ValueError):
        view.plan_derivation('not-a-uuid')
    with pytest.raises(LookupError):
        view.plan_derivation(str(uuid.uuid4()))


# ---------------------------------------------------------------- unit derivation

def test_unit_derivation_and_errors(db):
    _batch(db, 'run-unit')
    [plan_id] = _plan_ids(db)
    buy = view.unit_derivation('nvda', SESSION)
    assert buy['plan_ids'] == [plan_id]
    assert buy['trigger'] and buy['note'] == derivation.TRIGGER_NOTE
    assert buy['consistency']['status'] == 'consistent' and 'steps' not in buy

    # 没有计划的卖出单元：只有触发段
    analysis_repo.store_candidate(db, 'MU', SESSION, derivation_candidate(), run_id=None)
    db.commit()
    sell = view.unit_derivation('MU', SESSION)
    assert sell['plan_ids'] == []
    assert [c['rule_id'] for c in sell['trigger']] == ['trend_break']
    assert 'steps' not in sell and sell['consistency']['status'] == 'consistent'

    with pytest.raises(LookupError):
        view.unit_derivation('AAPL', SESSION)
    with pytest.raises(LookupError):
        view.unit_derivation('NVDA', SESSION - timedelta(days=30))
    for bad in ('mu!', '', 'TOOLONGTICKER', 'MU.SH', None):
        with pytest.raises(ValueError):
            view.unit_derivation(bad, SESSION)


def derivation_candidate():
    from investment_assistant.analysis import rules
    return rules.Candidate(rule_id='trend_break', rule_version='1', action='sell',
                           inputs={'values': {'close': 90.0, 'ma50': 100.0}, 'thresholds': {}})


# ---------------------------------------------------------------- stage runs

def _stage_row(run_id, stage, session=SESSION, *, offset=0):
    started = NOW + timedelta(seconds=offset)
    return {'run_id': run_id, 'job': 'workbench', 'trigger': 'scheduled', 'session': session, 'stage': stage,
            'stages_version': stages_module.STAGES_VERSION_V3, 'status': 'success', 'reason': None,
            'input_count': 1, 'output_count': 1, 'skips': [], 'failures': [], 'degradations': [],
            'outputs': {}, 'detail': {}, 'started_at': started, 'finished_at': started, 'duration_ms': 0}


def test_stage_runs_view_orders_and_validates(db):
    # 阶段行故意倒序写入：视图按 STAGE_IDS 排列；两次运行按先后分组
    for run_id in ('run-a', 'run-b'):
        for i, stage in enumerate(reversed(stages_module.STAGE_IDS_V3)):
            workflow_repo.insert_stage_run(db, _stage_row(run_id, stage, offset=i))
    workflow_repo.insert_stage_run(db, _stage_row('run-other', 'collect', date(2026, 10, 21)))
    db.commit()

    out = view.stage_runs_view(session=SESSION)
    assert out['stage_runs_ready'] is True
    assert out['stages'] == list(stages_module.STAGES)
    assert [r['run_id'] for r in out['runs']] == ['run-a', 'run-b']
    for run in out['runs']:
        assert [row['stage'] for row in run['rows']] == list(stages_module.STAGE_IDS_V3)
    by_run = view.stage_runs_view(run_id='run-other')
    assert [(r['run_id'], [x['stage'] for x in r['rows']]) for r in by_run['runs']] == [('run-other', ['collect'])]
    assert view.stage_runs_view(run_id='nope')['runs'] == []

    with pytest.raises(ValueError):
        view.stage_runs_view()
    with pytest.raises(ValueError):
        view.stage_runs_view(session=SESSION, run_id='run-a')


# ---------------------------------------------------------------- batch check

def test_check_plans_counts_in_one_read_only_transaction(db, monkeypatch):
    _batch(db, 'run-check', tickers=('NVDA', 'MU'))
    ids = _plan_ids(db)
    opened = []
    real = service.read_only_tx

    def counting():
        opened.append(1)
        return real()

    monkeypatch.setattr(core_tx, 'read_only_tx', counting)
    out = view.check_plans(ids + [str(uuid.uuid4())])
    assert len(opened) == 1
    assert out['checked'] == 3 and out['consistent'] == 2 and out['mismatch'] == 0 and out['incomplete'] == 1
    assert [(i['plan_id'], i['ticker'], i['status']) for i in out['items'][:2]] == \
        [(ids[0], 'MU', 'consistent'), (ids[1], 'NVDA', 'consistent')]
    assert out['items'][2]['status'] == 'incomplete'
    assert view.check_plans([]) == {'checked': 0, 'consistent': 0, 'mismatch': 0, 'incomplete': 0, 'items': []}
    with pytest.raises(ValueError):
        view.check_plans(['nope'])


# ---------------------------------------------------------------- read-only guarantees

def test_reads_are_read_only(db):
    _batch(db, 'run-ro')
    [plan_id] = _plan_ids(db)
    before = _counts(db)
    assert before['stage_runs'] > 0 and before['trade_plans'] == 1 and before['price_bars'] > 0

    view.plan_derivation(plan_id)
    view.unit_derivation('NVDA', SESSION)
    view.stage_runs_view(session=SESSION)
    view.stage_runs_view(run_id='run-ro')
    view.check_plans([plan_id])
    db.commit()
    assert _counts(db) == before

    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with service.read_only_tx() as conn:
            conn.execute("INSERT INTO stage_runs (run_id, job, trigger, stage, stages_version, status, reason, "
                         "input_count, output_count, started_at, finished_at, duration_ms) "
                         "VALUES ('x', 'workbench', 'scheduled', 'collect', 'stages-1', 'skipped', 'r', 0, 0, "
                         "now(), now(), 0)")
    db.rollback()
    assert _counts(db) == before


def test_by_id_readers(db):
    _batch(db, 'run-readers')
    plan = execution_repo.plan_by_id(db, _plan_ids(db)[0])
    ids = plan['inputs']['bar_ids']
    rows = data_repo.bars_by_ids(db, list(reversed(ids)) + [999999999])
    assert [r['id'] for r in rows] == list(reversed(ids))              # 输入顺序；读不到的 id 不出现
    assert all(isinstance(r['close'], Decimal) and isinstance(r['volume'], int) for r in rows)
    assert data_repo.bars_by_ids(db, []) == []
    earnings = data_repo.earnings_row_by_id(db, plan['inputs']['earnings_date_id'])
    assert isinstance(earnings['earnings_date'], date) and earnings['status'] == 'known'
    assert data_repo.earnings_row_by_id(db, str(uuid.uuid4())) is None
    assert execution_repo.snapshot_by_id(db, str(uuid.uuid4())) is None
    regime = plan['inputs']['context']['regime']
    if regime:
        assert str(analysis_repo.regime_by_id(db, regime['id'])['id']) == regime['id']
    assert analysis_repo.regime_by_id(db, str(uuid.uuid4())) is None
