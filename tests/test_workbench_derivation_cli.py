"""只读的推导命令行（v2.4 §8 第 3 点）：check 逐份列出计划的一致性，stages 核对阶段证据。

计划与阶段行来自真实批处理（假 fetcher、一次性库）；篡改直接 UPDATE 已存计划。
两个子命令都在只读事务里执行，前后各表行数不变。"""
import investment_assistant.workflow.repo as workflow_repo
import subprocess
import sys
from pathlib import Path
from datetime import UTC, date, datetime, timedelta

import pytest

from investment_assistant.core import tx as core_tx

from test_workbench_service import NOW, SESSION, _ok_responses, _task_run, agent_deps, db, seed_watchlist   # noqa: F401
from test_workbench_stage_runs import _recorder, _sent, wired   # noqa: F401  (fixtures)
import investment_assistant.workflow.check as derivation
import investment_assistant.core.tx as service
import investment_assistant.core.stages as stages_module
import investment_assistant.workflow.batch as d_workflow_batch

TABLES = ('trade_plans', 'signal_candidates', 'stage_runs', 'job_reports', 'agent_runs', 'price_bars')


def _counts(db):
    return {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in TABLES}


def _insert_report(db, run_id, started_at, trigger='scheduled', task='workbench'):
    db.execute("INSERT INTO job_reports (task, run_id, status, started_at, trigger_source) "
               "VALUES (%s, %s, 'ok', %s, %s)", [task, run_id, started_at, trigger])
    db.commit()


def _scheduled(wired, monkeypatch, tickers=(('NVDA', ['core']), ('MU', ['core']))):
    seed_watchlist(wired, list(tickers))
    _sent(monkeypatch)
    result = _task_run('scheduled')
    wired.commit()
    return result


def _run(capsys, *argv):
    code = derivation.main(list(argv))
    out = capsys.readouterr().out
    return code, out


def _line(out: str, needle: str) -> str:
    [line] = [x for x in out.splitlines() if needle in x]
    return line


# ---------------------------------------------------------------- offline

def test_import_is_database_free():
    code = ("import sys; import investment_assistant.derivation.chain as d; "
            "bad = [m for m in ('psycopg', 'investment_assistant.core.tx', "
            "'investment_assistant.derivation.views') if m in sys.modules]; "
            "assert not bad, bad; assert callable(d.derive_plan)")
    done = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                          cwd=Path(__file__).resolve().parent.parent)
    assert done.returncode == 0, done.stderr


def test_session_window_runs_from_ny_close_to_next_trading_day_close():
    start, end = derivation.session_window(date(2026, 10, 22))          # 周四 → 周五，夏令时 UTC-4
    assert (start, end) == (datetime(2026, 10, 22, 20, 0, tzinfo=UTC), datetime(2026, 10, 23, 20, 0, tzinfo=UTC))
    start, end = derivation.session_window(date(2026, 10, 23))          # 周五 → 下周一
    assert end == datetime(2026, 10, 26, 20, 0, tzinfo=UTC)
    start, end = derivation.session_window(date(2026, 10, 30))          # 周五 → 周一，期间夏令时结束（UTC-5）
    assert (start, end) == (datetime(2026, 10, 30, 20, 0, tzinfo=UTC), datetime(2026, 11, 2, 21, 0, tzinfo=UTC))


def test_bad_arguments_exit_two(capsys):
    for argv in (['check'], ['check', '--since', 'yesterday'], ['stages'], ['check', '--since', '2026-10-22',
                                                                           '--kind', 'x']):
        with pytest.raises(SystemExit) as raised:
            derivation.main(argv)
        assert raised.value.code == 2
    capsys.readouterr()


# ---------------------------------------------------------------- check

def test_check_all_consistent(wired, monkeypatch, capsys):
    _scheduled(wired, monkeypatch)
    ids = [str(r[0]) for r in wired.execute('SELECT id FROM trade_plans').fetchall()]
    assert len(ids) == 2
    before = _counts(wired)
    code, out = _run(capsys, 'check', '--since', SESSION.isoformat())
    assert code == 0
    for plan_id in ids:
        assert 'consistent' in _line(out, plan_id)
    assert 'NVDA' in out and 'MU' in out and 'rule' in out
    last = out.strip().splitlines()[-1]
    assert last.startswith('汇总') and '2 份' in last and 'mismatch 0' in last and 'incomplete 0' in last
    assert _counts(wired) == before


def test_check_flags_tampered_stop(wired, monkeypatch, capsys):
    _scheduled(wired, monkeypatch)
    wired.execute("UPDATE trade_plans SET stop = stop + 1 WHERE ticker = 'NVDA'")
    wired.commit()
    nvda, mu = [str(wired.execute("SELECT id FROM trade_plans WHERE ticker = %s", [t]).fetchone()[0])
                for t in ('NVDA', 'MU')]
    code, out = _run(capsys, 'check', '--since', SESSION.isoformat())
    assert code == 1
    assert 'mismatch' in _line(out, nvda) and 'stop' in out
    assert 'consistent' in _line(out, mu) and 'mismatch' not in _line(out, mu)
    assert '1 份 mismatch' in out.strip().splitlines()[-1] or 'mismatch 1' in out.strip().splitlines()[-1]


def test_check_ignores_inconsistency_of_non_ok_plans(wired, monkeypatch, capsys):
    """退出码只看 status = ok 的计划（spec §8）：作废计划不一致仍逐份列出，但不失败。"""
    _scheduled(wired, monkeypatch, [('NVDA', ['core'])])
    wired.execute("UPDATE trade_plans SET stop = stop + 1, status = 'invalid', "
                  "invalid_reasons = ARRAY['earnings_in_validity']")
    wired.commit()
    code, out = _run(capsys, 'check', '--since', SESSION.isoformat())
    assert 'mismatch' in out and code == 0


def test_check_filters_kind_and_dates(db, scripted_model, capsys):
    d = agent_deps(scripted_model, _ok_responses('AAA'))
    d_workflow_batch.run_batch(tickers=['AAA'], now=NOW, deps=d, run_id='run-cli', stages=_recorder('run-cli'))
    db.commit()
    rule_id = str(db.execute("SELECT id FROM trade_plans WHERE kind = 'rule'").fetchone()[0])
    agent_id = str(db.execute("SELECT id FROM trade_plans WHERE kind = 'agent'").fetchone()[0])

    code, out = _run(capsys, 'check', '--since', SESSION.isoformat(), '--kind', 'agent')
    assert code == 0 and agent_id in out and rule_id not in out and '1 份' in out.splitlines()[-1]
    code, out = _run(capsys, 'check', '--since', SESSION.isoformat(), '--kind', 'rule')
    assert code == 0 and rule_id in out and agent_id not in out
    code, out = _run(capsys, 'check', '--since', SESSION.isoformat())
    assert rule_id in out and agent_id in out and '2 份' in out.splitlines()[-1]

    code, out = _run(capsys, 'check', '--since', '2026-10-23')            # 信号日在窗口之前
    assert code == 0 and '0 份' in out.splitlines()[-1]
    code, out = _run(capsys, 'check', '--since', '2026-10-01', '--until', '2026-10-21')
    assert code == 0 and '0 份' in out.splitlines()[-1]
    code, out = _run(capsys, 'check', '--since', '2026-10-01', '--until', SESSION.isoformat())
    assert code == 0 and '2 份' in out.splitlines()[-1]


# ---------------------------------------------------------------- stages

def test_stages_complete_run_passes(wired, monkeypatch, capsys):
    result = _scheduled(wired, monkeypatch)
    before = _counts(wired)
    code, out = _run(capsys, 'stages', '--session', SESSION.isoformat())
    assert code == 0, out
    assert result['run_id'] in out
    for stage in stages_module.STAGE_IDS_V3:
        assert any(line.lstrip().startswith(stage) for line in out.splitlines()), stage
    assert out.strip().splitlines()[-1].startswith('汇总')
    assert _counts(wired) == before


def test_stages_missing_row_fails(wired, monkeypatch, capsys):
    result = _scheduled(wired, monkeypatch)
    wired.execute("DELETE FROM stage_runs WHERE run_id = %s AND stage = 'agent'", [result['run_id']])
    wired.commit()
    code, out = _run(capsys, 'stages', '--session', SESSION.isoformat())
    assert code == 1
    assert '7 行' in out and 'agent' in _line(out, '缺少阶段')


def test_run_stage_count_by_version():
    assert derivation.RUN_STAGE_COUNT == {'stages-1': 7, 'stages-2': 8, 'stages-3': 9, 'stages-4': 10,
                                          'stages-5': 11, 'stages-6': 13}
    assert {v: len(ids) for v, ids in stages_module.STAGE_IDS_BY_VERSION.items()} == derivation.RUN_STAGE_COUNT


GROWTH_DROP = ('knowledge_citations, knowledge_draft_verdicts, review_card_verdicts, knowledge_drafts, '
               'knowledge_versions, knowledge_status, discussion_turns, knowledge_items, discussions, '
               'review_card_drafts, review_cards')


def test_stages_cli_counts_by_version(wired, monkeypatch, capsys):
    """v2.5/v2.7 §9: stages-1 runs pass with 7 rows, stages-2 with 8, stages-3 with 9; a short run fails."""
    nine = _scheduled(wired, monkeypatch)
    wired.execute(f'DROP TABLE {GROWTH_DROP} CASCADE')                   # 027 未执行 → stages-2
    wired.commit()
    eight = _task_run('manual')
    wired.execute('DROP TABLE plan_reviews, user_inputs, agent_plan_versions CASCADE')   # 026 未执行 → stages-1
    wired.commit()
    seven = _task_run('manual')
    wired.commit()

    def rows(run_id):
        return wired.execute('SELECT stages_version, count(*) FROM stage_runs WHERE run_id = %s '
                             'GROUP BY stages_version', [run_id]).fetchall()

    assert rows(nine['run_id']) == [('stages-3', 9)] and rows(eight['run_id']) == [('stages-2', 8)]
    assert rows(seven['run_id']) == [('stages-1', 7)]
    code, out = _run(capsys, 'stages', '--session', SESSION.isoformat())
    assert code == 0, out
    assert 'stages-3' in _line(out, nine['run_id']) and '9 行' in _line(out, nine['run_id'])
    assert 'stages-2' in _line(out, eight['run_id']) and '8 行' in _line(out, eight['run_id'])
    assert 'stages-1' in _line(out, seven['run_id']) and '7 行' in _line(out, seven['run_id'])
    assert '3 个齐全' in out.splitlines()[-1]

    wired.execute("DELETE FROM stage_runs WHERE run_id = %s AND stage = 'retro'", [nine['run_id']])
    wired.commit()
    code, out = _run(capsys, 'stages', '--session', SESSION.isoformat())
    assert code == 1
    assert 'retro' in _line(out, '缺少阶段') and '2 个齐全' in out.splitlines()[-1]


def test_stages_run_missing_from_job_reports_fails(wired, monkeypatch, capsys):
    result = _scheduled(wired, monkeypatch)
    wired.execute('DELETE FROM job_reports WHERE run_id = %s', [result['run_id']])
    wired.commit()
    code, out = _run(capsys, 'stages', '--session', SESSION.isoformat())
    assert code == 1 and 'job_reports' in out


def test_stages_report_without_rows_is_missing_evidence(wired, monkeypatch, capsys):
    _scheduled(wired, monkeypatch)
    _insert_report(wired, 'run-no-evidence', datetime(2026, 10, 22, 22, 50, tzinfo=UTC), trigger='manual_batch')
    code, out = _run(capsys, 'stages', '--session', SESSION.isoformat())
    assert code == 1
    line = _line(out, 'run-no-evidence')
    assert '缺证据' in line and '2026-10-23 06:50' in line            # 北京时间


def test_stages_window_is_ny_close_to_next_close(wired, monkeypatch, capsys):
    _scheduled(wired, monkeypatch)
    _insert_report(wired, 'run-before', datetime(2026, 10, 22, 19, 59, tzinfo=UTC))        # 收盘前一分钟
    _insert_report(wired, 'run-after', datetime(2026, 10, 23, 20, 0, tzinfo=UTC))          # 下一收盘时刻，不含
    code, out = _run(capsys, 'stages', '--session', SESSION.isoformat())
    assert code == 0 and 'run-before' not in out and 'run-after' not in out
    _insert_report(wired, 'run-first-minute', datetime(2026, 10, 22, 20, 0, tzinfo=UTC))
    code, out = _run(capsys, 'stages', '--session', SESSION.isoformat())
    assert code == 1 and '缺证据' in _line(out, 'run-first-minute')


def test_stages_without_table_or_runs(wired, monkeypatch, capsys):
    code, out = _run(capsys, 'stages', '--session', SESSION.isoformat())          # 没有任何运行
    assert code == 0 and '0 个运行' in out.splitlines()[-1]
    _scheduled(wired, monkeypatch)
    wired.execute('DROP TABLE stage_runs')
    wired.commit()
    code, out = _run(capsys, 'stages', '--session', SESSION.isoformat())
    assert code == 1 and '迁移 025 未执行' in out


def test_commands_run_in_read_only_transactions(wired, monkeypatch, capsys):
    _scheduled(wired, monkeypatch)
    opened = []
    real = service.read_only_tx

    def counting():
        opened.append(1)
        return real()

    monkeypatch.setattr(core_tx, 'read_only_tx', counting)
    _run(capsys, 'check', '--since', SESSION.isoformat())
    _run(capsys, 'stages', '--session', SESSION.isoformat())
    assert len(opened) == 2                                        # 每个子命令一个只读事务


# ---------------------------------------------------------------- v2.8: stages-4 and attempt

def _row(run_id, stage, attempt, status, reason=None, *, minute=0):
    started = datetime(2026, 10, 23, 3, 30, tzinfo=UTC) + timedelta(minutes=minute)
    return {'run_id': run_id, 'job': 'daily', 'trigger': 'scheduled', 'session': SESSION, 'stage': stage,
            'stages_version': 'stages-4', 'attempt': attempt, 'status': status, 'reason': reason, 'input_count': 0,
            'output_count': 0, 'skips': [], 'failures': [], 'degradations': [], 'outputs': {}, 'detail': {},
            'started_at': started, 'finished_at': started, 'duration_ms': 0}


def _insert_run(conn, run_id, *, resumed=False):
    """Ten rows; ``resumed`` = collect failed in the first execution and the second one re-ran it and what followed."""
    # Repository consumers use their owning layers below.

    nodes = stages_module.STAGE_IDS_V4
    if not resumed:
        for stage in nodes:
            workflow_repo.insert_stage_run(conn, _row(run_id, stage, 1, 'success'))
    else:
        for n, stage in enumerate(nodes):
            first = 'success' if n < 2 else ('failed' if stage == 'collect' else 'skipped')
            workflow_repo.insert_stage_run(conn, _row(run_id, stage, 1, 'failed' if stage == 'collect' else first,
                                                   'aborted: x' if stage == 'collect' else (
                                                       'upstream_failed' if first == 'skipped' else None)))
        for stage in nodes[1:]:
            workflow_repo.insert_stage_run(conn, _row(run_id, stage, 2, 'success', minute=30))
    conn.commit()


@pytest.fixture
def db28(db):          # ``conftest.WORKBENCH_MIGRATIONS`` ends with 029 since v2.9
    return db


def test_derivation_stages_accepts_resumed_runs_cli(db28, capsys):
    """The CLI version of the resume proof (spec §5.6): clean 10-row runs and resumed ones pass, a forged
    "completed node ran again" row fails."""
    _insert_run(db28, 'daily-20261023033000-aaaaaaaa')
    _insert_run(db28, 'daily-20261023043000-bbbbbbbb', resumed=True)
    for run_id in ('daily-20261023033000-aaaaaaaa', 'daily-20261023043000-bbbbbbbb'):
        _insert_report(db28, run_id, datetime(2026, 10, 23, 3, 30, tzinfo=UTC), task='daily')
    code, out = _run(capsys, 'stages', '--session', SESSION.isoformat())
    assert code == 0, out
    assert 'stages-4' in _line(out, 'daily-20261023033000-aaaaaaaa') and '10 行' in _line(out, 'daily-20261023033000-aaaaaaaa')
    assert '含重试' in _line(out, 'daily-20261023043000-bbbbbbbb')
    assert '2 个齐全' in out.splitlines()[-1] and out.splitlines()[-1].endswith('通过')
    # forge: sec_monitor already succeeded in attempt 1, yet an attempt 2 appears
    # Repository consumers use their owning layers below.
    workflow_repo.insert_stage_run(db28, _row('daily-20261023033000-aaaaaaaa', 'sec_monitor', 2, 'success', minute=40))
    db28.commit()
    code, out = _run(capsys, 'stages', '--session', SESSION.isoformat())
    assert code == 1
    assert 'sec_monitor 的 attempt 2 不该存在' in out and '1 个齐全' in out.splitlines()[-1]
    # a gap in the attempts is reported too
    workflow_repo.insert_stage_run(db28, _row('daily-20261023043000-bbbbbbbb', 'collect', 4, 'success', minute=50))
    db28.commit()
    code, out = _run(capsys, 'stages', '--session', SESSION.isoformat())
    assert code == 1 and 'collect 的 attempt 不连续' in out


def test_derivation_stages_short_stages_4_run_fails(db28, capsys):
    # Repository consumers use their owning layers below.

    for stage in stages_module.STAGE_IDS_V4[:-1]:
        workflow_repo.insert_stage_run(db28, _row('daily-20261023033000-cccccccc', stage, 1, 'success'))
    _insert_report(db28, 'daily-20261023033000-cccccccc', datetime(2026, 10, 23, 3, 30, tzinfo=UTC), task='daily')
    db28.commit()
    code, out = _run(capsys, 'stages', '--session', SESSION.isoformat())
    assert code == 1 and 'retro' in _line(out, '缺少阶段')


def test_derivation_stages_5_counts_account_node(db28, capsys):
    # Repository consumers use their owning layers below.

    run_id = 'daily-20261023033000-dddddddd'
    for stage in stages_module.STAGE_IDS_V5:
        workflow_repo.insert_stage_run(db28, {**_row(run_id, stage, 1, 'success'), 'stages_version': 'stages-5'})
    _insert_report(db28, run_id, datetime(2026, 10, 23, 3, 30, tzinfo=UTC), task='daily')
    db28.commit()
    code, out = _run(capsys, 'stages', '--session', SESSION.isoformat())
    assert code == 0 and '11 行' in _line(out, run_id) and 'stages-5' in _line(out, run_id)


def test_derivation_stages_6_counts_portfolio_and_propose(db28, capsys):
    run_id = 'daily-20261023033000-eeeeeeee'
    for stage in stages_module.STAGE_IDS:
        workflow_repo.insert_stage_run(db28, {**_row(run_id, stage, 1, 'success'), 'stages_version': 'stages-6'})
    _insert_report(db28, run_id, datetime(2026, 10, 23, 3, 30, tzinfo=UTC), task='daily')
    db28.commit()
    code, out = _run(capsys, 'stages', '--session', SESSION.isoformat())
    assert code == 0 and '13 行' in _line(out, run_id) and 'stages-6' in _line(out, run_id)
    db28.execute("DELETE FROM stage_runs WHERE run_id = %s AND stage = 'portfolio'", [run_id])
    db28.commit()
    code, out = _run(capsys, 'stages', '--session', SESSION.isoformat())
    assert code == 1 and 'portfolio' in _line(out, '缺少阶段')
