"""The comparison page's seams (D4, spec §12.1): ``markers`` on ``/api/accounts/compare`` and ``/{id}/equity`` — one
case per source kind (feature switch change, first session of an ``agent_version``, parameter change) — and the
``agent_version`` of every decision."""
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import investment_assistant.execution.repo as execution_repo
import investment_assistant.views.account as account_views
from investment_assistant.core import tx as core_tx
from test_workbench_paper_accounts import conn, paper  # noqa: F401


def wall():
    return datetime.now(UTC) + timedelta(hours=1)       # rows carry their real insertion time; look at them afterwards


def flag_change(conn, flag, enabled, created_at):
    conn.execute('INSERT INTO feature_flag_changes (flag, enabled_before, enabled_after, reason, request_id, '
                 'created_at) VALUES (%s, %s, %s, %s, %s, %s)', [flag, not enabled, enabled, 'test', uuid.uuid4(),
                                                                created_at])


def decision(conn, account, day, *, agent_version=None, strategy_version=None):
    execution_repo.insert_decision(conn, {'account_id': account['id'], 'session': day, 'status': 'ok',
                                          'agent_version': agent_version, 'strategy_version': strategy_version,
                                          'evidence': {}, 'output': {}, 'summary': 's'})


def test_feature_changes_are_markers_on_the_trading_day_they_took_effect(conn):
    flag_change(conn, 'portfolio_manager', False, datetime(2026, 10, 21, 22, 0, tzinfo=UTC))     # Wed 18:00 New York
    flag_change(conn, 'portfolio_manager', True, datetime(2026, 10, 24, 15, 0, tzinfo=UTC))      # a Saturday
    flag_change(conn, 'backup_sources', False, datetime(2099, 1, 1, tzinfo=UTC))                 # not known yet
    found = account_views.markers(conn, execution_repo.list_accounts(conn), now=wall())
    assert found == [
        {'session': date(2026, 10, 21), 'kind': 'feature', 'label': '功能开关 portfolio_manager 关闭'},
        {'session': date(2026, 10, 26), 'kind': 'feature', 'label': '功能开关 portfolio_manager 开启'}]   # Sat → Mon


def test_agent_version_first_sessions_are_markers_per_account(conn):
    agent = paper(conn, '自管', 'agent_managed')
    follow = paper(conn, '照计划', 'follow_plans')
    for day, version in ((date(2026, 10, 20), 'agent-portfolio-1'), (date(2026, 10, 21), 'agent-portfolio-1'),
                         (date(2026, 10, 22), 'agent-portfolio-2'), (date(2026, 10, 23), 'agent-portfolio-2')):
        decision(conn, agent, day, agent_version=version, strategy_version='agent-managed')
    decision(conn, follow, date(2026, 10, 20), strategy_version='follow-plans-1')
    decision(conn, follow, date(2026, 10, 21), strategy_version='follow-plans-1')
    found = account_views.markers(conn, execution_repo.list_accounts(conn), now=wall())
    assert [(m['session'], m['kind'], m['label']) for m in found] == [
        (date(2026, 10, 20), 'version', '照计划 · follow-plans-1'),      # follow-plans-1 is a boundary too
        (date(2026, 10, 20), 'version', '自管 · agent-portfolio-1'),
        (date(2026, 10, 22), 'version', '自管 · agent-portfolio-2')]     # only a value never seen before counts


def test_parameter_changes_are_markers_but_the_opening_is_not(conn):
    account = paper(conn, '自管', 'agent_managed')
    before = {'sector_cap': '0.35', 'max_per_sector': 3, 'max_positions': 5}
    execution_repo.insert_param_change(conn, {'account_id': account['id'], 'kind': 'opening',
                                              'effective_from': date(2026, 10, 21), 'params_after': before})
    execution_repo.insert_param_change(conn, {'account_id': account['id'], 'kind': 'params', 'reason': 'r',
        'effective_from': date(2026, 10, 24),        # a Saturday: effective from Monday's chart point
        'params_before': before, 'params_after': {**before, 'sector_cap': '0.4', 'max_per_sector': 2}})
    assert account_views.markers(conn, execution_repo.list_accounts(conn), now=wall()) == [
        {'session': date(2026, 10, 26), 'kind': 'params', 'label': '自管 参数调整：max_per_sector、sector_cap'}]


def test_markers_are_ordered_and_from_session_cuts_them(conn):
    account = paper(conn, '自管', 'agent_managed')
    flag_change(conn, 'company_researcher', False, datetime(2026, 10, 21, 22, 0, tzinfo=UTC))
    decision(conn, account, date(2026, 10, 21), agent_version='agent-portfolio-2')
    execution_repo.insert_param_change(conn, {'account_id': account['id'], 'kind': 'params',
        'effective_from': date(2026, 10, 21), 'params_before': {'a': 1}, 'params_after': {'a': 2}})
    accounts_ = execution_repo.list_accounts(conn)
    assert [m['kind'] for m in account_views.markers(conn, accounts_, now=wall())] == ['feature', 'version', 'params']
    assert account_views.markers(conn, accounts_, now=wall(), from_session=date(2026, 10, 22)) == []


def test_compare_and_equity_carry_markers_and_decisions_the_agent_version(conn, monkeypatch):
    agent = paper(conn, '自管', 'agent_managed')
    other = paper(conn, '照计划', 'follow_plans')
    flag_change(conn, 'portfolio_manager', False, datetime(2026, 10, 21, 22, 0, tzinfo=UTC))
    decision(conn, agent, date(2026, 10, 22), agent_version='agent-portfolio-2')
    decision(conn, other, date(2026, 10, 22), strategy_version='follow-plans-1')
    execution_repo.insert_param_change(conn, {'account_id': other['id'], 'kind': 'params',
        'effective_from': date(2026, 10, 23), 'params_before': {'max_positions': 5}, 'params_after': {'max_positions': 3}})
    conn.commit()
    now = wall()

    compared = account_views.compare(now=now)
    assert [(m['kind'], m['session']) for m in compared['markers']] == [
        ('feature', date(2026, 10, 21).isoformat()), ('version', '2026-10-22'), ('version', '2026-10-22'),
        ('params', '2026-10-23')]
    assert all(set(m) == {'session', 'kind', 'label'} for m in compared['markers'])

    own = account_views.equity(str(agent['id']), now=now)
    assert {'rows', 'spy', 'markers'} <= set(own)
    assert [(m['kind'], m['label']) for m in own['markers']] == [
        ('feature', '功能开关 portfolio_manager 关闭'), ('version', '自管 · agent-portfolio-2')]   # the other account's seams stay out

    listed = account_views.decisions(str(agent['id']), now=now)['decisions']
    assert [d['agent_version'] for d in listed] == ['agent-portfolio-2']
    full = account_views.decisions(str(agent['id']), now=now, session=date(2026, 10, 22))['decision']
    assert full['agent_version'] == 'agent-portfolio-2'
    follow_listed = account_views.decisions(str(other['id']), now=now)['decisions']
    assert [d['agent_version'] for d in follow_listed] == [None]       # a follow-plans decision has no Agent version


def test_portfolio_check_of_one_account_and_session(conn):
    """`GET /api/accounts/{id}/portfolio-check?session=`: 「今日」's shape for any account; a missing row is
    not_computed, a hard result alone is degraded."""
    account = paper(conn, '自管', 'agent_managed')
    conn.commit()
    now, session = wall(), date(2026, 10, 22)
    out = account_views.portfolio_check(str(account['id']), now=now, session=session)
    assert (out['status'], out['reason'], out['result']) == ('not_computed', '组合检查节点尚未运行', None)
    assert out['session'] == session.isoformat() and out['account']['name'] == '自管'

    result = {'exposure_by_sector': {}, 'verdicts': [], 'conflicts': [], 'order': [], 'params_used': {},
              'version': 'portfolio-check-1'}
    execution_repo.insert_portfolio_check(conn, {'account_id': account['id'], 'session': session, 'run_id': 'r',
        'cutoff_at': datetime(2026, 10, 22, 22, 30, tzinfo=UTC), 'version': 'portfolio-check-1', 'result': result})
    conn.commit()
    out = account_views.portfolio_check(str(account['id']), now=now, session=session)
    assert (out['status'], out['result'], out['commentary']) == ('degraded', result, None)
    assert account_views.portfolio_check(str(account['id']), now=now, session=date(2026, 10, 21))['status'] == 'not_computed'
