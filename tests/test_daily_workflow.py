"""The ``daily`` graph (v2.8): eleven nodes, rows, node retry, holidays, budgets, locks, the fault drill.

SQL tests on the disposable database (``daily_db``: the workbench schema plus migration 028). Fetchers, the model,
the checkpointer, ``sleep``, the clocks, the notifier and ``job_reports`` are fakes; nothing leaves the process."""
import json
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import _v27_task
from _daily_db import (FakeClock, NotifyCfg, Notices, clock, daily_config, daily_db, job_reports, notices,  # noqa: F401
                       plain_db, shared_saver)
from test_workbench_service import (NOW, SESSION, Sources, _agent_settings, _ok_responses, _universe_deps, agent_deps,
                                    deps, seed_watchlist)

from investment_assistant.tasks import filings
from investment_assistant.workbench import repository, service, stages, workflow

HOLIDAY = datetime(2026, 10, 24, 22, 30, tzinfo=UTC)          # a Saturday in New York
RUN_ID = 'daily-20261022223000-0a1b2c3d'


@pytest.fixture(autouse=True)
def fake_sec(monkeypatch):
    """No SEC pass touches the network: ``_scan`` returns a canned monitor result (tests override ``fake_sec.result``)."""
    class FakeSec:
        calls = 0
        result = {'tickers': ['NVDA'], 'filings': [], 'errors': {}, 'downloaded_count': 0, 'event_ids': []}

    def scan(*args, **kwargs):
        FakeSec.calls += 1
        return dict(FakeSec.result), 'monitor'

    monkeypatch.setattr(filings, '_scan', scan)
    return FakeSec


def world(*, sources=None, agent=False, clk=None, scripted=None, universe=('MU',)):
    """Deps over the synthetic sources; the Agent is off unless a scripted model is given."""
    d = _universe_deps(sources or Sources(), list(universe))
    d.agent_settings = _agent_settings(provider='chatgpt' if agent else 'off')
    if scripted is not None:
        d.agent_model = scripted
        d.agent_checkpointer = lambda: nullcontext(InMemorySaver())
    if clk is not None:
        d.clock = clk.mono
    return d


def go(daily_db, clock, *, trigger='manual_batch', now=NOW, run_id=RUN_ID, d=None, factory=None, explicit_pool=None,
       **kwargs):
    seed_watchlist(daily_db, [('NVDA', ['core'])]) if not explicit_pool and not kwargs.pop('no_seed', False) else None
    return workflow.run(run_id=run_id, trigger=trigger, now=now, deps=d or world(clk=clock),
                        explicit_pool=explicit_pool, checkpointer_factory=factory or shared_saver()[1],
                        sleep=clock.sleep, notify_cfg=NotifyCfg(), config=daily_config(), **kwargs)


def rows(conn, run_id=RUN_ID):
    return repository.stage_rows_for_run(conn, run_id)


def effective(conn, run_id=RUN_ID):
    return workflow.effective_rows(rows(conn, run_id))


def statuses(conn, run_id=RUN_ID):
    eff = effective(conn, run_id)
    return {stage: (eff[stage]['status'], eff[stage]['reason']) for stage in workflow.NODES}


# ------------------------------------------------------------------------------------------------ rows per path

@pytest.mark.parametrize('path', ['scheduled', 'manual', 'holiday', 'explicit_pool', 'failed_midway'])
def test_daily_eleven_rows_each_path(daily_db, clock, notices, path, monkeypatch):
    kwargs: dict = {}
    if path == 'scheduled':
        kwargs.update(trigger='scheduled')
    elif path == 'holiday':
        kwargs.update(trigger='scheduled', now=HOLIDAY, no_seed=True)
    elif path == 'explicit_pool':
        kwargs.update(explicit_pool=['NVDA', 'MU'])
    elif path == 'failed_midway':
        monkeypatch.setattr(service, 'stage_regime', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('regime boom')))
        monkeypatch.setitem(workflow.BODIES, 'regime', service.stage_regime)
    result = go(daily_db, clock, **kwargs)
    eff = effective(daily_db)
    assert list(eff) == list(workflow.NODES) and len(eff) == 11          # exactly eleven effective rows, in stages-5 order
    assert [r['stage'] for r in rows(daily_db)[:11]] == list(stages.STAGE_IDS)
    assert {r['stages_version'] for r in rows(daily_db)} == {'stages-5'} and {r['job'] for r in rows(daily_db)} == {'daily'}
    if path == 'failed_midway':
        assert result['status'] == 'failed' and result['failed_node'] == 'regime'
        assert [eff[s]['status'] for s in workflow.NODES[4:]] == ['skipped'] * 7
        assert {eff[s]['reason'] for s in workflow.NODES[4:]} == {'upstream_failed'}
        assert len(rows(daily_db)) == 11 and eff['regime']['status'] == 'failed'
        assert eff['regime']['detail']['retry']['waited_s'] == 60 and clock.sleeps == [60]
    if path == 'holiday':
        assert [eff[s]['reason'] for s in workflow.NODES[:9]] == ['not_trading_day'] * 9


@pytest.fixture(autouse=True)
def no_fetch_backoff(monkeypatch):
    """The fetch-1 backoff (2 s, 8 s) never sleeps for real."""
    from investment_assistant.workbench import collect, earnings

    monkeypatch.setattr(collect, 'FETCH_SLEEP', lambda seconds: None)
    monkeypatch.setattr(earnings, 'FETCH_SLEEP', lambda seconds: None)


def flaky(stage_id, monkeypatch, *, fail_times=1, before=None):
    """Make a node's body raise ``fail_times`` times, then behave; ``before`` runs at the start of every attempt."""
    original = workflow.BODIES[stage_id]
    calls = {'n': 0}

    def body(state, ctx, acc):
        calls['n'] += 1
        if before is not None:
            before()
        if calls['n'] <= fail_times:
            raise RuntimeError(f'flaky {stage_id}')
        return original(state, ctx, acc)

    monkeypatch.setitem(workflow.BODIES, stage_id, body)
    return calls


# ------------------------------------------------------------------------------------------------ node retry

def test_node_retries_once_after_60s(daily_db, clock, notices, monkeypatch):
    calls = flaky('regime', monkeypatch)
    result = go(daily_db, clock)
    assert result['status'] in ('success', 'degraded') and calls['n'] == 2
    assert clock.sleeps == [60]                                    # one wait, injected, no real sleeping
    regime = [r for r in rows(daily_db) if r['stage'] == 'regime']
    assert len(regime) == 1                                        # one row however often the node ran
    assert regime[0]['detail']['retry'] == {'first_error': 'flaky regime', 'waited_s': 60}
    assert regime[0]['status'] in ('success', 'degraded') and regime[0]['duration_ms'] >= 60_000      # degraded: FRED is off


def test_node_failed_twice_stops_chain(daily_db, clock, notices, monkeypatch):
    calls = flaky('regime', monkeypatch, fail_times=2)
    result = go(daily_db, clock)
    assert calls['n'] == 2 and result['status'] == 'failed' and result['failed_node'] == 'regime'
    eff = effective(daily_db)
    assert eff['regime']['status'] == 'failed' and eff['regime']['reason'] == 'aborted: flaky regime'
    assert eff['regime']['detail']['retry']['waited_s'] == 60
    assert [eff[s]['reason'] for s in workflow.NODES[4:]] == ['upstream_failed'] * 7
    assert result['summary']['run_status'] == 'failed' and result['summary']['failed_node'] == 'regime'
    assert result['summary']['error'] == 'regime: aborted: flaky regime'
    assert daily_db.execute('SELECT count(*) FROM signal_candidates').fetchone()[0] == 0   # nothing after the stop


def test_a_state_read_failing_after_a_node_failure_keeps_that_failure(daily_db, clock, notices, monkeypatch):
    """The checkpoint read after ``NodeFailed`` raises: the failed node stays the failure and the rest are
    ``upstream_failed`` — no second ``failed`` row on the first node after it."""
    from langgraph.graph.state import CompiledStateGraph

    flaky('regime', monkeypatch, fail_times=2)

    def broken(self, *args, **kwargs):
        raise RuntimeError('state read failed')

    monkeypatch.setattr(CompiledStateGraph, 'get_state', broken)
    result = go(daily_db, clock)
    assert result['status'] == 'failed' and result['failed_node'] == 'regime'
    eff = effective(daily_db)
    assert [eff[s]['status'] for s in workflow.NODES if eff[s]['status'] == 'failed'] == ['failed']
    assert eff['regime']['reason'] == 'aborted: flaky regime'
    assert [eff[s]['reason'] for s in workflow.NODES[4:]] == ['upstream_failed'] * 7


def test_unavailable_not_retried(daily_db, clock, notices, monkeypatch):
    def down(state, ctx, acc):
        raise service.Unavailable('数据库不可达')

    monkeypatch.setitem(workflow.BODIES, 'screen', down)
    result = go(daily_db, clock)
    assert clock.sleeps == []                                      # no 60-second wait for an unreachable database
    eff = effective(daily_db)
    assert eff['screen']['status'] == 'failed' and 'detail' in eff['screen']
    assert 'retry' not in eff['screen']['detail'] and result['failed_node'] == 'screen'


def test_regime_failure_stops_chain(daily_db, clock, notices, monkeypatch):
    """Spec §5.2 / Q5: the regime computation failing is no longer 'book it and carry on'."""
    def boom(**kwargs):
        raise RuntimeError('regime exploded')

    monkeypatch.setattr(service.regime_module, 'snapshot', boom)
    result = go(daily_db, clock)
    assert result['failed_node'] == 'regime' and clock.sleeps == [60]
    assert effective(daily_db)['signals']['reason'] == 'upstream_failed'
    assert daily_db.execute('SELECT count(*) FROM market_regimes').fetchone()[0] == 0


def test_target_failure_never_fails_node(daily_db, clock, notices):
    result = go(daily_db, clock, d=world(sources=Sources(fail={'NVDA'}), clk=clock))
    eff = effective(daily_db)
    assert eff['collect']['status'] == 'partial'
    assert {'bars:NVDA'} <= {f['target'] for f in eff['collect']['failures']}
    assert not any(eff[s]['reason'] == 'upstream_failed' for s in workflow.NODES)        # the chain went on
    assert result['status'] == 'degraded' and result['failed_node'] is None


# ------------------------------------------------------------------------------------------------ holidays and retro

def test_holiday_skips_before_push_and_retro_judges_due(daily_db, clock, notices):
    result = go(daily_db, clock, trigger='scheduled', now=HOLIDAY, no_seed=True)
    eff = effective(daily_db)
    assert [eff[s]['status'] for s in workflow.NODES[:9]] == ['skipped'] * 9
    assert {eff[s]['reason'] for s in workflow.NODES[:9]} == {'not_trading_day'}
    assert eff['push']['status'] == 'success' and eff['push']['detail']['closed'] is True
    assert any('休市' in title for title in notices.titles('workbench'))
    # the retro node judged the weekly / monthly review on a day without trading: the due week was generated
    retro = eff['retro']
    assert retro['status'] in ('success', 'partial') and retro['detail']['cards'] is False
    assert retro['detail']['weekly']['action'] in ('generated', 'reused')
    assert result['status'] in ('success', 'degraded')


def test_every_execution_summary_carries_cutoff_and_session(daily_db, clock, notices):
    """The detail page reads ``cutoff_at`` from the summary; a holiday run (``collect`` is skipped) has it too."""
    result = go(daily_db, clock, trigger='scheduled', now=HOLIDAY, no_seed=True)
    assert result['summary']['cutoff_at'] == HOLIDAY.isoformat()
    assert result['summary']['session'] == result['session'] == '2026-10-23'      # the Friday before the Saturday


def test_retro_not_due_on_non_trading_day(daily_db, clock, notices):
    due_week = date(2026, 10, 19)                                   # the week due on HOLIDAY
    repository.insert_review_push(daily_db, {'kind': 'weekly', 'period': due_week, 'review_id': None,
                                             'trigger_source': 'scheduled', 'sent': True, 'messages': 1})
    go(daily_db, clock, trigger='scheduled', now=HOLIDAY, no_seed=True)
    retro = effective(daily_db)['retro']
    assert (retro['status'], retro['reason']) == ('skipped', 'not_due')


def test_retro_on_a_trading_day_is_never_not_due(daily_db, clock, notices):
    repository.insert_review_push(daily_db, {'kind': 'weekly', 'period': date(2026, 10, 19), 'review_id': None,
                                             'trigger_source': 'scheduled', 'sent': True, 'messages': 1})
    go(daily_db, clock, trigger='scheduled')
    retro = effective(daily_db)['retro']
    assert retro['status'] != 'skipped' and retro['detail']['cards'] is True      # the card pass always ran


def test_retro_after_push_failure_does_not_block_push(daily_db, clock, notices, monkeypatch):
    from investment_assistant.workbench import retro_cards

    def boom(*args, **kwargs):
        raise RuntimeError('retro exploded')

    monkeypatch.setattr(retro_cards, 'run_stage', boom)
    result = go(daily_db, clock, trigger='scheduled')
    eff = effective(daily_db)
    assert eff['push']['status'] in ('success', 'partial', 'degraded')              # the plan was pushed first
    assert any('工作台' in title for title in notices.titles('workbench'))
    assert eff['retro']['status'] == 'failed' and result['status'] == 'failed'
    assert eff['retro']['detail']['weekly']['action'] in ('generated', 'reused', 'already_sent')   # the review part is on the row


def test_manual_run_no_push(daily_db, clock, notices):
    go(daily_db, clock, trigger='manual_batch')
    eff = effective(daily_db)
    assert (eff['push']['status'], eff['push']['reason']) == ('skipped', 'manual_run')
    assert notices.calls == [] or all(task == 'workbench_review' for task, _ in notices.calls)
    weekly = eff['retro']['detail']['weekly']
    assert weekly['action'] in ('generated', 'reused') and weekly['push'] == 'manual_run'     # generated, not pushed
    assert daily_db.execute('SELECT count(*) FROM review_pushes').fetchone()[0] == 0


# ------------------------------------------------------------------------------------------------ collect and screen

def _member_sources():
    class Members(Sources):
        def bars(self, ticker, start, end_exclusive):
            frame = super().bars(ticker, start, end_exclusive)
            if ticker == 'MU':                          # enough volume for screen-1 to keep it
                frame = frame.copy()
                frame['Volume'] = 1_000_000
            return frame

    return Members()


def test_collect_includes_universe_bars(daily_db, clock, notices):
    sources = _member_sources()
    go(daily_db, clock, d=world(sources=sources, clk=clock))
    bars_row = daily_db.execute("SELECT detail, target_count FROM data_fetch_runs WHERE family = 'bars' "
                                "AND job = 'daily'").fetchone()
    assert 'MU' in {t['target'] for t in bars_row[0]['targets']}            # the range's bars are the collect node's
    assert ('bars', 'MU') in sources.calls
    families = {r[0] for r in daily_db.execute("SELECT family FROM data_fetch_runs WHERE job = 'daily'")}
    assert families == {'bars', 'news', 'sec_filings', 'fred', 'universe'} | {'sec_filings'}
    collect = effective(daily_db)['collect']
    assert len(collect['outputs']['data_fetch_runs']) == 5


def test_screen_backfills_new_members(daily_db, clock, notices):
    go(daily_db, clock, d=world(sources=_member_sources(), clk=clock))
    screen = effective(daily_db)['screen']
    assert screen['detail']['new_members'] == ['MU']
    assert screen['detail']['backfill'] == {'targets': 1, 'ok': 1, 'failed': 0}
    scopes = daily_db.execute("SELECT family, detail->>'scope', target_count FROM data_fetch_runs "
                              "WHERE detail->>'scope' = 'screen_new_members' ORDER BY family").fetchall()
    assert scopes == [('news', 'screen_new_members', 1), ('sec_filings', 'screen_new_members', 1)]
    assert len(screen['outputs']['data_fetch_runs']) == 2
    assert daily_db.execute("SELECT count(*) FROM news_items n JOIN news_item_tickers t ON t.news_item_id = n.id "
                            "WHERE t.ticker = 'MU'").fetchone()[0] >= 1


def test_screen_backfill_failure_makes_screen_partial(daily_db, clock, notices):
    sources = _member_sources()
    sources.fail_news = {'MU'}
    go(daily_db, clock, d=world(sources=sources, clk=clock))
    screen = effective(daily_db)['screen']
    assert screen['status'] == 'partial'
    assert {'news:MU'} <= {f['target'] for f in screen['failures']}
    assert effective(daily_db)['regime']['status'] != 'failed'


# ------------------------------------------------------------------------------------------------ status and locks

@pytest.mark.parametrize('statuses, expected', [
    (['success', 'skipped', 'success'], 'success'),
    (['success', 'partial', 'success'], 'degraded'),
    (['success', 'degraded', 'skipped'], 'degraded'),
    (['success', 'failed', 'skipped'], 'failed'),
    (['partial', 'failed'], 'failed'),
])
def test_run_status_from_rows(statuses, expected):
    rows_ = [{'stage': f's{i}', 'status': s, 'attempt': 1} for i, s in enumerate(statuses)]
    assert workflow.run_status(rows_) == expected


def test_run_status_reads_effective_rows():
    rows_ = [{'stage': 'collect', 'status': 'failed', 'attempt': 1}, {'stage': 'screen', 'status': 'skipped', 'attempt': 1},
             {'stage': 'collect', 'status': 'success', 'attempt': 2}, {'stage': 'screen', 'status': 'success', 'attempt': 2}]
    assert workflow.run_status(rows_) == 'success'


def test_daily_lock_rejects_overlap(daily_db, clock, notices):
    from investment_assistant.tasks import daily

    with daily.exclusive():
        assert daily.is_running() is True
        with pytest.raises(daily.DailyBusy):
            with daily.exclusive():
                pass
        with pytest.raises(daily.DailyBusy):
            daily.run(daily_config(), trigger_source='manual', now=NOW, deps=world(clk=clock),
                      checkpointer_factory=shared_saver()[1], sleep=clock.sleep, notify_cfg=NotifyCfg())
    assert daily.is_running() is False                                # released in finally


def test_failure_notification_once(daily_db, clock, notices, job_reports, monkeypatch):
    from investment_assistant.tasks import daily

    flaky('regime', monkeypatch, fail_times=2)
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    out = daily.run(daily_config(), trigger_source='manual', now=NOW, deps=world(clk=clock), run_id=RUN_ID,
                    checkpointer_factory=shared_saver()[1], sleep=clock.sleep, notify_cfg=NotifyCfg())
    assert out['status'] == 'error'
    failures = [t for t in notices.titles('workbench') if '每日工作流失败' in t]
    assert failures == [f'⚠️ 每日工作流失败 · {SESSION.isoformat()}']
    assert len(job_reports.rows()) == 1


def test_migration_028_missing_refuses(plain_db, clock, notices, job_reports):
    from investment_assistant.tasks import daily

    out = daily.run(daily_config(), trigger_source='manual', now=NOW, deps=world(clk=clock), run_id=RUN_ID,
                    checkpointer_factory=shared_saver()[1], sleep=clock.sleep, notify_cfg=NotifyCfg())
    assert out['status'] == 'error' and out['summary']['error'] == 'migration_028_missing'
    assert [r['status'] for r in job_reports.rows()] == ['error']
    assert plain_db.execute('SELECT count(*) FROM stage_runs').fetchone()[0] == 0           # no graph ran
    assert len([t for t in notices.titles('workbench') if '每日工作流失败' in t]) == 1


def test_sec_monitor_node_records_fetch_row_and_lock(daily_db, clock, notices, fake_sec, monkeypatch):
    from investment_assistant.workbench import collect

    fake_sec.result = {'tickers': ['NVDA', 'MU'], 'errors': {'MU': 'sec timeout'}, 'downloaded_count': 1,
                       'event_ids': ['a'], 'delivery': {'status': 'sent', 'receipts': [{'status': 'sent'}]},
                       'filings': [{'ticker': 'NVDA', 'form': '8-K', 'filing_date': '2026-10-22',
                                    'accession_number': '0001-26-000001'}]}
    held: list = []
    real = collect.family_locks

    from contextlib import contextmanager

    @contextmanager
    def recording(family_ids):
        held.append(list(family_ids))
        with real(family_ids):
            yield

    monkeypatch.setattr(collect, 'family_locks', recording)
    go(daily_db, clock)
    assert held[0] == ['sec_filings']                                  # the node holds the family lock first
    row = effective(daily_db)['sec_monitor']
    assert row['status'] == 'partial' and (row['input_count'], row['output_count']) == (2, 1)
    assert row['outputs']['filings'] == [{'ticker': 'NVDA', 'form': '8-K', 'filing_date': '2026-10-22',
                                          'accession': '0001-26-000001', 'notified': True}]
    fetch = daily_db.execute("SELECT id, job, run_id, session, trigger, status FROM data_fetch_runs "
                             "WHERE family = 'sec_filings' AND session IS NULL").fetchone()
    assert fetch[1:] == ('daily', RUN_ID, None, 'manual_batch', 'partial')
    assert row['outputs']['data_fetch_runs'] == [fetch[0]]
    assert row['detail']['mode'] == 'monitor' and row['detail']['notifications'] == {'delivery': 'sent', 'sent': 1}


# ------------------------------------------------------------------------------------------------ budgets and timing

def _agent_world(scripted_model, clk, *, units=True):
    d = agent_deps(scripted_model, _ok_responses('NVDA'))
    d.agent_settings = _agent_settings()
    d.clock = clk.mono
    return d


def _agent_run_statuses(conn):
    return [(r[0], r[1]) for r in conn.execute('SELECT status, reason FROM agent_runs ORDER BY created_at')]


def test_agent_budget_starts_at_agent_node(daily_db, clock, notices, scripted_model, monkeypatch):
    """The collect node used 1400 s of the clock; the agent still gets its whole 1500 s (v2.8 D24)."""
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    flaky('collect', monkeypatch, fail_times=0, before=lambda: clock.advance(1400))
    go(daily_db, clock, d=_agent_world(scripted_model, clock), no_seed=True)
    assert _agent_run_statuses(daily_db)[0][0] in ('ok', 'pass')
    assert not any(reason == 'stage_budget' for _, reason in _agent_run_statuses(daily_db))


def test_collect_retries_do_not_shrink_agent_budget(daily_db, clock, notices, scripted_model, monkeypatch):
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    flaky('collect', monkeypatch, fail_times=1, before=lambda: clock.advance(800))      # 800 + 60 wait + 800
    go(daily_db, clock, d=_agent_world(scripted_model, clock), no_seed=True)
    assert clock.sleeps == [60] and clock.t >= 1660
    assert not any(reason == 'stage_budget' for _, reason in _agent_run_statuses(daily_db))
    assert _agent_run_statuses(daily_db)[0][0] in ('ok', 'pass')


def test_review_shares_agent_window(daily_db, clock, notices, scripted_model, monkeypatch):
    from investment_assistant.workbench import plan_review

    seen = {}

    def spy(session, **kwargs):
        seen['elapsed'] = kwargs['clock']() - kwargs['started']
        return None

    monkeypatch.setattr(plan_review, 'run_stage', spy)
    original = workflow.BODIES['agent']

    def agent_then_wait(state, ctx, acc):
        out = original(state, ctx, acc)
        clock.advance(1400)                                             # the agent node used 1400 s
        return out

    monkeypatch.setitem(workflow.BODIES, 'agent', agent_then_wait)
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    go(daily_db, clock, d=_agent_world(scripted_model, clock), no_seed=True)
    assert 1399 <= seen['elapsed'] <= 1401                              # review counts from the agent node's start


def test_retro_has_own_window(daily_db, clock, notices, scripted_model, monkeypatch):
    from investment_assistant.workbench import retro_cards

    seen = {}

    def spy(session, **kwargs):
        seen['elapsed'] = kwargs['clock']() - kwargs['started']

    monkeypatch.setattr(retro_cards, 'run_stage', spy)
    original = workflow.BODIES['agent']

    def agent_then_wait(state, ctx, acc):
        out = original(state, ctx, acc)
        clock.advance(1600)                                             # more than the whole agent window
        return out

    monkeypatch.setitem(workflow.BODIES, 'agent', agent_then_wait)
    seed_watchlist(daily_db, [('NVDA', ['core'])])
    go(daily_db, clock, d=_agent_world(scripted_model, clock), no_seed=True)
    assert seen['elapsed'] < 5                                          # the retro window opened at the retro node


def test_duration_ms_equals_wall_span(daily_db, clock, notices, monkeypatch):
    flaky('regime', monkeypatch, before=lambda: clock.advance(7))
    go(daily_db, clock)
    for row in rows(daily_db):
        span = (row['finished_at'] - row['started_at']).total_seconds() * 1000
        assert abs(row['duration_ms'] - span) <= 1000
    regime = effective(daily_db)['regime']
    assert regime['duration_ms'] >= 60_000 + 7_000                      # the retry wait and both attempts are in it


# ------------------------------------------------------------------------------------------------ fault drill

def test_fault_once_env_injects_single_failure(daily_db, clock, notices, monkeypatch):
    monkeypatch.setenv(workflow.FAULT_ENV, 'screen')
    saver, factory = shared_saver()
    first = go(daily_db, clock, factory=factory)
    assert first['failed_node'] == 'screen' and clock.sleeps == [60]
    failed = effective(daily_db)['screen']
    assert failed['reason'] == 'aborted: fault injection'
    # a resume does not inject again: the node already has a row
    again = workflow.resume(RUN_ID, 'screen', now=NOW + timedelta(minutes=30), deps=world(clk=clock),
                            checkpointer_factory=factory, sleep=clock.sleep, notify_cfg=NotifyCfg(),
                            config=daily_config())
    assert again['status'] in ('success', 'degraded') and again['failed_node'] is None
    assert clock.sleeps == [60]
    assert [r['attempt'] for r in rows(daily_db) if r['stage'] == 'screen'] == [1, 2]
    monkeypatch.delenv(workflow.FAULT_ENV)


# ------------------------------------------------------------------------------------------------ the shell

def test_run_batch_shell_unchanged(plain_db):
    """``run_batch`` with no recorder is the v2.7 batch: the same keys, the same values."""
    summary = service.run_batch(tickers=['NVDA'], now=NOW, deps=deps(Sources()))
    assert set(summary) == {'session', 'cutoff_at', 'tickers', 'benchmark', 'filings', 'earnings', 'plans', 'conflicts',
                            'errors', 'briefs', 'screen', 'pool', 'etfs', 'macro', 'regime', 'sources', 'timings',
                            'agent', 'review', 'retro'}
    assert summary['session'] == SESSION.isoformat() and summary['cutoff_at'] == NOW.isoformat()
    assert summary['screen'] == {'status': 'skipped', 'reason': 'explicit_pool'}
    assert summary['pool'] == {'core': 1, 'extended': 0, 'holdings': 0, 'screen': None, 'changes': [], 'chains': 0}
    assert set(summary['timings']) == {'collect', 'regime', 'rules', 'agent', 'total'}
    assert summary['regime']['status'] == 'inserted' and summary['errors'] == []
    assert set(summary['tickers']['NVDA']) == {'bars', 'skipped_bars', 'news', 'candidates', 'skips', 'error'}
    assert summary['sources'] == {'bars': [], 'news': [], 'earnings': [], 'sec': [], 'etf_bars': [], 'fred': [],
                                  'regime': [], 'agent': []}
    assert 'data_status' not in summary and 'push' not in summary


def test_signals_plan_same_transaction(plain_db, monkeypatch):
    """Q4: ``signals`` and ``plan`` still finish a ticker together — a failing plan rolls its candidates back too."""
    real = service._generate_rule_plan

    def plan(conn, ticker, *args, **kwargs):
        if ticker == 'MU':
            raise RuntimeError('plan exploded')
        return real(conn, ticker, *args, **kwargs)

    monkeypatch.setattr(service, '_generate_rule_plan', plan)
    summary = service.run_batch(tickers=['NVDA', 'MU'], now=NOW, deps=deps(Sources()))
    assert 'MU rules: plan exploded' in summary['errors']
    assert plain_db.execute("SELECT count(*) FROM signal_candidates WHERE ticker = 'MU'").fetchone()[0] == 0
    assert plain_db.execute("SELECT count(*) FROM signal_candidates WHERE ticker = 'NVDA'").fetchone()[0] > 0


def test_run_batch_shell_non_explicit_literals(plain_db):
    """The non-explicit v2.7 batch (range + screen + a new member), key by key against what v2.7 produced: the order of
    the tickers, the pool and source blocks, the SEC listing keys and the earnings statuses."""
    seed_watchlist(plain_db, [('NVDA', ['core'])])
    summary = service.run_batch(now=NOW, deps=_universe_deps(_member_sources(), ['MU']))
    assert list(summary['tickers']) == ['NVDA', 'MU']                  # pool order: core, then the screen member
    assert summary['pool'] == {'core': 1, 'extended': 1, 'holdings': 0, 'screen': 1, 'chains': 0,
                               'changes': summary['pool']['changes']}
    assert [c['ticker'] for c in summary['pool']['changes']] == ['NVDA', 'MU']
    assert summary['screen']['status'] == 'success' and summary['screen']['members'] == ['MU']
    assert summary['tickers']['MU']['bars'] == 'collected_in_screen' and summary['tickers']['NVDA']['bars']['inserted'] > 0
    assert summary['tickers']['MU']['error'] is None and summary['tickers']['NVDA']['error'] is None
    assert summary['tickers']['MU']['news']['inserted'] == 1
    # MU's date was stored by the range step and fetched once more for the member, as v2.7's per-ticker loop did
    assert summary['earnings'] == {'NVDA': 'inserted', 'MU': 'unchanged'}
    assert set(summary['filings']) == {'inserted', 'existing', 'acceptance_filled', 'by_ticker', 'errors'}
    assert summary['sources'] == {'bars': [], 'news': [], 'earnings': [], 'sec': [], 'etf_bars': [], 'fred': [],
                                  'regime': [], 'agent': []}
    assert summary['errors'] == [] and 'data_status' not in summary
    assert set(summary['timings']) == {'collect', 'regime', 'rules', 'agent', 'total'}


def test_migration_030_missing_refuses_before_graph(daily_db, clock, notices, job_reports):
    from investment_assistant.tasks import daily
    daily_db.execute('DROP TABLE paper_chain_done'); daily_db.commit()
    with pytest.raises(service.Unavailable, match='migration_030_missing'):
        daily.ensure_ready()
    result=daily.run(daily_config(),trigger_source='manual',now=NOW,deps=world(clk=clock),run_id=RUN_ID,
        checkpointer_factory=shared_saver()[1],sleep=clock.sleep,notify_cfg=NotifyCfg())
    assert result['summary']['error']=='migration_030_missing'
    assert job_reports.rows()[0]['status']=='error'
    assert daily_db.execute('SELECT count(*) FROM stage_runs').fetchone()[0]==0


@pytest.mark.parametrize('entry', ['function', 'cli'])
def test_resume_migration_030_missing_refuses_before_graph_or_report(daily_db, monkeypatch, notices, job_reports, capsys, entry):
    from investment_assistant.tasks import daily
    import dotenv
    daily_db.execute('DROP TABLE paper_chain_done'); daily_db.commit()
    monkeypatch.setattr(workflow, 'resume', lambda *a, **kw: pytest.fail('resume graph must not be called'))
    monkeypatch.setattr(dotenv, 'load_dotenv', lambda *a, **kw: False)
    if entry == 'function':
        with pytest.raises(service.Unavailable, match='migration_030_missing'):
            daily.resume(RUN_ID, 'accounts', now=NOW, config=daily_config())
    else:
        assert daily.main(['resume', '--run-id', RUN_ID, '--node', 'accounts']) == 2
        assert 'migration_030_missing' in capsys.readouterr().err
    assert job_reports.rows() == []
    assert daily_db.execute('SELECT count(*) FROM stage_runs').fetchone()[0] == 0
    assert notices.calls == []


def test_accounts_node_failure_preserves_push_and_retro(daily_db,clock,notices,monkeypatch):
    from investment_assistant.workbench import paper_accounts
    monkeypatch.setattr(paper_accounts,'run_stage',lambda *a,**k: (_ for _ in ()).throw(ValueError('account stage failure')))
    result=go(daily_db,clock)
    eff=effective(daily_db)
    assert eff['accounts']['status']=='failed'
    assert eff['push']['reason']=='manual_run'
    assert eff['retro']['reason']!='upstream_failed'
    assert result['failed_node']=='accounts' and result['failure']['resumable'] is False
