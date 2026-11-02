"""The four switches at run time (spec §9, D3): a run freezes them when it starts, ``job_reports.summary.features``
records them, a flip during the run or before a resume does not reach it. SQL tests on the disposable database."""
import uuid
from datetime import timedelta

import pytest

from _daily_db import (NotifyCfg, clock, daily_config, daily_db, job_reports, notices, shared_saver)   # noqa: F401
from test_daily_resume import failed_run, resume
from test_daily_task import run_daily
from test_daily_workflow import RUN_ID, effective, fake_sec, flaky, go, no_fetch_backoff, world   # noqa: F401
from test_workbench_service import NOW, seed_watchlist

from investment_assistant.core import features as core_features
import investment_assistant.workflow.graph as workflow

ALL_ON = dict.fromkeys(core_features.FLAGS, True)


def flip(conn, flag, enabled, reason='运行中调整'):
    core_features.record_change(conn, flag=flag, enabled=enabled, reason=reason, request_id=uuid.uuid4())
    conn.commit()


def test_initial_state_freezes_flags_and_job_report_records_them(daily_db, clock, notices, job_reports):
    # the pure part: every flag present, junk dropped, the default of a flag without history is on
    state = workflow.initial_state(run_id=RUN_ID, trigger='manual_batch', now=NOW,
                                   features={'backup_sources': False, 'unknown_flag': False})
    assert state['features'] == {**ALL_ON, 'backup_sources': False}
    assert workflow.initial_state(run_id=RUN_ID, trigger='manual_batch', now=NOW)['features'] == ALL_ON

    # the runtime part: recorded changes are what the run starts on, and its job report says so
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    flip(daily_db, 'portfolio_manager', False)
    flip(daily_db, 'optimize_proposals', False)
    out = run_daily(clock)
    frozen = {**ALL_ON, 'portfolio_manager': False, 'optimize_proposals': False}
    assert out['summary']['features'] == frozen
    [report] = job_reports.rows()
    assert report['summary']['features'] == frozen
    eff = effective(daily_db)
    assert (eff['portfolio']['status'], eff['portfolio']['reason']) == ('skipped', 'feature_disabled')
    assert (eff['propose']['status'], eff['propose']['reason']) == ('skipped', 'feature_disabled')


def test_flag_change_during_run_does_not_affect_frozen_state(daily_db, clock, notices, monkeypatch):
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    flip(daily_db, 'optimize_proposals', False)                  # off at the start …
    flaky('portfolio', monkeypatch, fail_times=0, before=lambda: (flip(daily_db, 'portfolio_manager', False),
                                                                  flip(daily_db, 'optimize_proposals', True)))
    out = go(daily_db, clock, no_seed=True)
    # … and the flips made just before the portfolio node (one off, one on) change the database, not the run
    assert core_features.current(daily_db)['portfolio_manager'] is False
    assert core_features.current(daily_db)['optimize_proposals'] is True
    assert out['summary']['features'] == {**ALL_ON, 'optimize_proposals': False}
    eff = effective(daily_db)
    assert eff['portfolio']['reason'] != 'feature_disabled' and eff['portfolio']['status'] != 'skipped'
    assert (eff['propose']['status'], eff['propose']['reason']) == ('skipped', 'feature_disabled')
    # the next run starts on the new values
    out = go(daily_db, clock, run_id='daily-20261022223000-0b1b2c3d', no_seed=True)
    assert out['summary']['features'] == {**ALL_ON, 'portfolio_manager': False}


def test_resume_reuses_frozen_flags(daily_db, clock, notices, monkeypatch):
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    flip(daily_db, 'portfolio_manager', False)
    factory = failed_run(daily_db, clock, monkeypatch, node='regime', no_seed=True)
    frozen = {**ALL_ON, 'portfolio_manager': False}
    # between the failure and the resume the switches are flipped the other way
    flip(daily_db, 'portfolio_manager', True)
    flip(daily_db, 'company_researcher', False)
    out = resume(clock, factory, 'regime', now=NOW + timedelta(hours=1))
    assert out['failed_node'] is None
    assert out['summary']['features'] == frozen
    eff = effective(daily_db)
    assert (eff['portfolio']['status'], eff['portfolio']['reason']) == ('skipped', 'feature_disabled')
    assert core_features.current(daily_db) == {**ALL_ON, 'company_researcher': False}
