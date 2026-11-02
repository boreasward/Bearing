"""「今日」 portfolio_check block (spec §7.4, K2, UI1): ready / not_computed / degraded and the switch semantics.

The block is the real account's frozen hard check of the session plus its PM commentary. With ``portfolio_manager`` off
the whole node was skipped, so a row of an earlier run is NOT this version's check; enabled-without-row is "not run";
a hard result without commentary is degraded."""
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

import investment_assistant.core.features as core_features
import investment_assistant.execution.repo as execution_repo
import investment_assistant.views.today as today_view
from investment_assistant.core import tx as core_tx
from investment_assistant.core.constants import REAL_ACCOUNT_ID
from investment_assistant.core.versions import PORTFOLIO_CHECK_VERSION

SESSION = date(2026, 10, 22)
NOW = datetime(2026, 10, 22, 22, 30, tzinfo=UTC)
RESULT = {'exposure_by_sector': {'半导体': {'held_value': '0', 'pending_value': '0', 'candidate_value': '5000',
                                           'share_of_equity': '0.05'}},
          'verdicts': [{'plan_id': 'p1', 'ticker': 'NVDA', 'group': '半导体', 'verdict': 'ok', 'reasons': [], 'order': 1}],
          'conflicts': [], 'order': ['p1'], 'params_used': {'sector_cap': '0.35'}, 'version': PORTFOLIO_CHECK_VERSION}
COMMENTARY = {'summary': '先做 NVDA', 'ranking': [{'plan_id': 'p1', 'rank': 1, 'reason': '行业未满'}],
              'tradeoffs': [], 'hold_actions': []}


@pytest.fixture
def db(workbench_db):
    return workbench_db


def _check(conn, *, commentary=None):
    execution_repo.insert_portfolio_check(conn, {'account_id': REAL_ACCOUNT_ID, 'session': SESSION, 'run_id': 'r1',
                                                  'cutoff_at': NOW, 'version': PORTFOLIO_CHECK_VERSION, 'result': RESULT})
    if commentary is not None:
        row = execution_repo.portfolio_check_for(conn, REAL_ACCOUNT_ID, SESSION)
        assert execution_repo.set_portfolio_commentary(conn, row['id'], commentary, None)
    conn.commit()


def _switch(conn, enabled):
    import uuid
    core_features.record_change(conn, flag='portfolio_manager', enabled=enabled, reason='test', request_id=uuid.uuid4())
    conn.commit()


def test_missing_row_with_the_switch_on_is_not_run(db):
    view = today_view.portfolio_check_view(db, SESSION)
    assert view == {'status': 'not_computed', 'reason': '组合检查节点尚未运行', 'session': SESSION,
                    'cutoff_at': None, 'result': None, 'commentary': None}


def test_hard_result_without_commentary_is_degraded_and_keeps_the_result(db):
    _check(db)
    view = today_view.portfolio_check_view(db, SESSION)
    assert view['status'] == 'degraded' and view['reason'] == 'Agent 不可用'
    assert view['result'] == RESULT and view['commentary'] is None and view['cutoff_at'] == NOW


def test_hard_result_with_commentary_is_ready(db):
    _check(db, commentary=COMMENTARY)
    view = today_view.portfolio_check_view(db, SESSION)
    assert (view['status'], view['reason']) == ('ready', None)
    assert view['result'] == RESULT and view['commentary'] == COMMENTARY and view['session'] == SESSION


def test_switch_off_is_not_computed_even_with_a_stored_row_of_an_earlier_run(db):
    """K2: the node was skipped for this version; the earlier run's row must not pose as its check."""
    _check(db, commentary=COMMENTARY)
    _switch(db, False)
    view = today_view.portfolio_check_view(db, SESSION)
    assert view['status'] == 'not_computed' and view['reason'] == '功能开关已关闭'
    assert view['result'] is None and view['commentary'] is None and view['cutoff_at'] is None
    # the run's frozen switch decides when the caller passes it (a flip mid-run does not change that run's push)
    assert today_view.portfolio_check_view(db, SESSION, enabled=True)['status'] == 'ready'
    _switch(db, True)
    assert today_view.portfolio_check_view(db, SESSION)['status'] == 'ready'
    assert today_view.portfolio_check_view(db, SESSION, enabled=False)['status'] == 'not_computed'


def test_pre031_reads_no_new_table(workbench_db_pre031):
    view = today_view.portfolio_check_view(workbench_db_pre031, SESSION)
    assert (view['status'], view['reason']) == ('not_computed', '需执行迁移 031')


def test_today_payload_carries_the_block(db, monkeypatch):
    _check(db, commentary=COMMENTARY)

    @contextmanager
    def tx():
        yield db
    monkeypatch.setattr(core_tx, '_tx', tx)
    payload = today_view.today(now=NOW)
    assert payload['portfolio_check']['status'] == 'ready'
    assert payload['portfolio_check']['result'] == RESULT
