"""``workflow.check.run_checks`` (spec D45, plan Step 16): the seven items of one session, each with an ``ok`` and a
``mismatch`` case, and the ``check --session`` CLI over them. Disposable schema only; nothing is run, no model."""
import uuid
from datetime import timedelta
from decimal import Decimal as D

import pytest

import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import investment_assistant.workflow.check as workflow_check
import investment_assistant.workflow.repo as workflow_repo
from investment_assistant.execution import ledger
from investment_assistant.optimize import repo as optimize_repo
from test_workbench_ledger import FILL, plan_row, trade_payload
from test_workbench_paper_accounts import END, NOW, conn, paper  # noqa: F401  (conn fixture)

ITEMS = list(workflow_check.CHECK_ITEMS)


def stage(conn, stage_id, *, status='success', reason=None, session=END, run_id='daily-check', job='daily',
          started=NOW, finished=NOW, detail=None):
    workflow_repo.insert_stage_run(conn, {
        'run_id': run_id, 'job': job, 'trigger': 'manual_batch', 'session': session, 'stage': stage_id,
        'stages_version': 'stages-6', 'status': status, 'reason': reason, 'input_count': 0, 'output_count': 0,
        'skips': [], 'failures': [], 'degradations': [], 'outputs': {}, 'detail': detail or {},
        'started_at': started, 'finished_at': finished, 'duration_ms': 0})


def agent_run(conn, *, output, version='agent-4', mode='propose', cutoff=NOW, ticker='NVDA'):
    run_id = uuid.uuid4()
    analysis_repo.insert_agent_run(conn, {
        'id': run_id, 'ticker': ticker, 'signal_date': END, 'pool': 'main', 'attempt': 1, 'mode': mode,
        'agent_version': version, 'provider': 'chatgpt', 'model': 'gpt-test', 'cutoff_at': cutoff, 'status': 'pass',
        'reason': None, 'output': output, 'counter': None, 'plan_id': None, 'input_tokens': 0, 'output_tokens': 0,
        'started_at': cutoff, 'finished_at': cutoff}, [])
    return run_id


def checks(conn, *, session=END, now=NOW):
    conn.commit()
    return workflow_check.run_checks(conn, session=session, now=now)


def statuses(found):
    return {name: item['status'] for name, item in found['items'].items()}


def test_empty_schema_nothing_is_a_mismatch(conn):
    found = checks(conn)
    assert list(found['items']) == ITEMS and found['ok'] is True and found['session'] == END
    assert statuses(found) == {
        'derivation': 'ok', 'holding_state': 'skipped', 'accounts': 'ok', 'portfolio_checks': 'skipped',
        'proposals_without_changes': 'ok', 'data_requests': 'ok', 'research': 'ok'}
    assert found['items']['holding_state']['reason'] == 'no_batch'
    assert found['items']['portfolio_checks']['reason'] == 'no_portfolio_run'


def test_pre_031_items_are_skipped_not_failed(workbench_db_pre031):
    found = workflow_check.run_checks(workbench_db_pre031, session=END, now=NOW)
    for name in ('portfolio_checks', 'proposals_without_changes', 'data_requests'):
        assert found['items'][name] == {'status': 'skipped', 'problems': [], 'reason': 'migration_031_missing'}
    assert found['ok'] is True


# ---------------------------------------------------------------- derivation

def test_derivation_mismatch(conn, monkeypatch):
    plan = plan_row(conn, signal_date=END, status='ok')
    plan_row(conn, ticker='MU', signal_date=END, status='invalid', invalid_reasons=['earnings_in_validity'])
    plan_row(conn, ticker='AMD', signal_date=END - timedelta(days=7), status='ok')      # another session's unit
    seen = []

    def derive(stored, rows):
        seen.append(stored['ticker'])
        return {'consistency': {'status': 'mismatch' if stored['ticker'] == 'NVDA' else 'consistent',
                                'mismatches': [], 'missing': []}}

    monkeypatch.setattr(workflow_check, 'derive_plan', derive)
    monkeypatch.setattr(workflow_check.derivation_view, 'load_rows', lambda conn_, stored: None)
    found = checks(conn)
    assert seen == ['NVDA']                                  # only the session's ok plans are re-derived
    item = found['items']['derivation']
    assert item['status'] == 'mismatch' and found['ok'] is False
    assert item['problems'] == [f"NVDA {str(plan['id'])[:8]}：mismatch"]


def test_derivation_incomplete_plan_is_a_mismatch(conn):
    plan_row(conn, signal_date=END, status='ok')             # no stored inputs: the derivation cannot be completed
    item = checks(conn)['items']['derivation']
    assert item['status'] == 'mismatch' and 'NVDA' in item['problems'][0] and 'incomplete' in item['problems'][0]


# ---------------------------------------------------------------- holding state

def test_holding_state_mismatch_and_ok(conn):
    ledger.record_trade(conn, trade_payload(), now=FILL + timedelta(hours=1))
    stage(conn, 'review', started=FILL + timedelta(hours=10), finished=FILL + timedelta(hours=11))
    item = checks(conn)['items']['holding_state']
    assert item['status'] == 'mismatch' and item['problems'] == [f'NVDA：{END} 有未平批次但没有持仓状态行']
    execution_repo.insert_holding_status(conn, {
        'ticker': 'NVDA', 'session': END, 'run_id': 'daily-check', 'quantity': D(10), 'avg_cost': D('101.5'),
        'held_sessions': 1, 'instruction': 'hold', 'reason': 'x', 'timeline_version': 'timeline-2'})
    assert checks(conn)['items']['holding_state'] == {'status': 'ok', 'problems': [], 'reason': None}


# ---------------------------------------------------------------- accounts

def test_accounts_mismatch_missing_equity_and_decision(conn):
    account = paper(conn)
    conn.execute('UPDATE accounts SET created_at = %s WHERE id = %s', (NOW - timedelta(days=1), account['id']))
    stage(conn, 'accounts')
    item = checks(conn)['items']['accounts']
    assert item['status'] == 'mismatch'
    assert item['problems'] == [f"模拟：{END} missing equity", f"模拟：{END} missing decision"]


def test_accounts_without_an_accounts_run_owe_nothing(conn):
    paper(conn)
    assert checks(conn)['items']['accounts']['status'] == 'ok'


# ---------------------------------------------------------------- portfolio_checks

def test_portfolio_checks_missing_row_then_ok(conn):
    account = paper(conn)
    conn.execute('UPDATE accounts SET created_at = %s WHERE id = %s', (NOW - timedelta(days=1), account['id']))
    stage(conn, 'portfolio')
    item = checks(conn)['items']['portfolio_checks']
    assert item['status'] == 'mismatch' and item['problems'] == [f'模拟：{END} 没有组合检查行']
    execution_repo.insert_portfolio_check(conn, {
        'account_id': account['id'], 'session': END, 'run_id': 'daily-check', 'cutoff_at': NOW,
        'version': 'portfolio-check-1', 'result': {'verdicts': []}})
    assert checks(conn)['items']['portfolio_checks']['status'] == 'ok'


def test_portfolio_checks_owed_only_when_the_node_ran(conn):
    account = paper(conn)
    conn.execute('UPDATE accounts SET created_at = %s WHERE id = %s', (NOW - timedelta(days=1), account['id']))
    stage(conn, 'portfolio', status='skipped', reason='feature_disabled')           # the run's frozen switch was off
    item = checks(conn)['items']['portfolio_checks']
    assert item['status'] == 'skipped' and item['reason'] == 'no_portfolio_run'
    other = paper(conn, name='晚开')
    conn.execute('UPDATE accounts SET created_at = %s WHERE id = %s', (NOW + timedelta(hours=1), other['id']))
    stage(conn, 'portfolio', run_id='daily-second', status='success')
    execution_repo.insert_portfolio_check(conn, {
        'account_id': account['id'], 'session': END, 'run_id': 'daily-second', 'cutoff_at': NOW,
        'version': 'portfolio-check-1', 'result': {'verdicts': []}})
    assert checks(conn)['items']['portfolio_checks']['status'] == 'ok'        # opened after the run: owes nothing


# ---------------------------------------------------------------- proposals_without_changes

def test_proposals_with_changes_before_approval(conn):
    def proposal(title):
        row = optimize_repo.insert_proposal(conn, {
            'source': 'data', 'title': title, 'change': 'c', 'rationale': 'r', 'evidence_refs': ['fact:x#1'],
            'expected': 'e', 'acceptance': 'a'})
        return row

    clean, bad = proposal('clean'), proposal('bad')
    assert checks(conn)['items']['proposals_without_changes']['status'] == 'ok'
    optimize_repo.insert_roadmap_record(conn, 'roadmap_versions', {
        'version_id': 'v3', 'version': 1, 'title': 'early', 'status': 'planned', 'proposal_id': bad['id']})
    item = checks(conn)['items']['proposals_without_changes']
    assert item['status'] == 'mismatch' and item['problems'] == [f"提案 {bad['id']} 尚未批准却已有版本或实施简报"]
    assert clean['id'] not in {int(p.split()[1]) for p in item['problems']}


# ---------------------------------------------------------------- data_requests

def test_data_request_queued_for_more_than_a_day(conn):
    old, fresh = (data_repo.insert_data_requests(conn, [{'family': 'bars', 'ticker': 'NVDA', 'requested_at': at}])[0]
                  for at in (NOW - timedelta(days=1, minutes=1), NOW - timedelta(hours=23)))
    item = checks(conn)['items']['data_requests']
    assert item['status'] == 'mismatch' and len(item['problems']) == 1 and f'补查请求 {old} bars NVDA' in item['problems'][0]
    assert fresh is not None
    data_repo.settle_data_request(conn, old, status='failed', fetch_run_id=None, rows=None, reason='x',
                                  rerun_run_id=None)
    assert checks(conn)['items']['data_requests']['status'] == 'ok'          # only ``queued`` counts


# ---------------------------------------------------------------- research

@pytest.mark.parametrize('research', ['ok', 'rejected', 'error', 'disabled', 'carried'])
def test_research_known_statuses_pass(conn, research):
    agent_run(conn, output={'research': {'status': research}})
    assert checks(conn)['items']['research']['status'] == 'ok'


def test_research_status_unknown_or_missing_is_a_mismatch(conn):
    pending = agent_run(conn, output={'research': {'status': 'pending'}})
    absent = agent_run(conn, output={'action': 'pass'}, ticker='MU')
    agent_run(conn, output={'action': 'pass'}, version='agent-3', ticker='AMD')                  # not an agent-4 run
    agent_run(conn, output={'research': {'status': 'weird'}}, cutoff=NOW + timedelta(days=5))     # another session
    item = checks(conn)['items']['research']
    assert item['status'] == 'mismatch'
    assert item['problems'] == [f'NVDA {END} propose {str(pending)[:8]}：研究员状态 pending',
                                f'MU {END} propose {str(absent)[:8]}：研究员状态 缺失']


# ---------------------------------------------------------------- the CLI

def test_check_session_cli_ok(conn, capsys):
    conn.commit()
    assert workflow_check.main(['check', '--session', END.isoformat()]) == 0
    out = capsys.readouterr().out
    for name in ITEMS:
        assert any(line.startswith(('ok ', 'skipped ')) and name in line for line in out.splitlines()), name
    assert out.strip().splitlines()[-1].startswith('汇总：7 项，不一致 0 项') and out.strip().endswith('通过')


def test_check_session_cli_exit_one_on_any_mismatch(conn, capsys, monkeypatch):
    conn.commit()
    real = workflow_check.run_checks

    def broken(conn_, *, session, now):
        found = real(conn_, session=session, now=now)
        found['items']['research'] = {'status': 'mismatch', 'problems': ['NVDA：研究员状态 缺失'], 'reason': None}
        found['ok'] = False
        return found

    monkeypatch.setattr(workflow_check, 'run_checks', broken)
    assert workflow_check.main(['check', '--session', END.isoformat()]) == 1
    out = capsys.readouterr().out
    assert any(line.startswith('mismatch') and 'research' in line for line in out.splitlines())
    assert '  NVDA：研究员状态 缺失' in out and out.strip().endswith('不通过')


def test_check_needs_since_or_session():
    with pytest.raises(SystemExit) as raised:
        workflow_check.main(['check'])
    assert raised.value.code == 2
