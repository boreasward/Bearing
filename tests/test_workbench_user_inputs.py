"""User inputs (v2.5 spec §6.3, §8.1) and the manual recheck (§8.2, D14) against a disposable schema.

Chains come from real batches (fake fetchers, routed scripted model — the helpers of
test_workbench_plan_review); every price is synthetic test data. ``user_inputs.created_at`` is the
database clock, so tests that need an input before a 2026-09 cutoff move its ``created_at`` back
with an UPDATE (test data only; the code never updates these rows).
"""
import investment_assistant.analysis.agent.runner as analysis_agent_runner
import investment_assistant.core.schema as core_schema
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import os
import re
import uuid

import legacy_decisions
from contextlib import contextmanager, nullcontext
from datetime import timedelta

import psycopg
import pytest

from investment_assistant.core import tx as core_tx
from langgraph.checkpoint.memory import InMemorySaver

from test_workbench_service import db   # noqa: F401  (fixture: service wired to the disposable schema)
from test_workbench_plan_review import (QUIET, S1, Market, counter, day, evening, make_model, proposal,
                                        reviews, run, settings, signal_day, skips, tool, verdict)
import investment_assistant.workflow.nodes.review as plan_review
import investment_assistant.core.tx as service
from investment_assistant.execution import ledger
from investment_assistant.execution import user_inputs

REAL_LOCK_CONNECTION = analysis_agent_runner._lock_connection      # before the db fixture points it at the test database


def payload(**overrides):
    out = {'request_id': str(uuid.uuid4()), 'ticker': 'SYNTH', 'kind': 'research', 'body': '研报：毛利率上行'}
    out.update(overrides)
    return out


def add(db, now=None, **overrides):
    return user_inputs.add(payload(**overrides), now=now or evening(day(1)))['record']


def backdate(db, input_id, when):
    db.execute('UPDATE user_inputs SET created_at = %s WHERE id = %s', [when, input_id])
    db.commit()


def connection_is(monkeypatch, status='connected'):
    """The agent connection as ``settings_from_env`` reads it (v2.8: the ChatGPT connection, not an API key)."""
    from investment_assistant.core.llm import connection as agent_connection

    info = {'status': status, 'scopes_ok': status == 'connected', 'model': 'gpt-test' if status == 'connected' else None,
            'secret': 'test-access-token' if status == 'connected' else None}
    monkeypatch.setattr(agent_connection, 'read_for_settings', lambda: info)


@pytest.fixture
def agent_env(monkeypatch):
    monkeypatch.setenv('WORKBENCH_AGENT_PROVIDER', 'chatgpt')
    connection_is(monkeypatch)
    monkeypatch.delenv('WORKBENCH_AGENT_DAILY_TOKENS', raising=False)


def deps_for(model, agent=None):
    return service.Deps(agent_model=model, agent_checkpointer=lambda: nullcontext(InMemorySaver()),
                        agent_settings=agent or settings())


def user_block(prompt: str) -> set[str]:
    return set(re.findall(r'user:\d+', prompt.split('## 用户输入')[1]))


# ---------------------------------------------------------------- user inputs

def test_add_validates(db):
    bad = [dict(body=''), dict(body='   '), dict(body='x' * 4001), dict(kind='rumor'),
           dict(source_url='ftp://x'), dict(source_url='https://' + 'a' * 500), dict(ticker='mu!'),
           dict(request_id='nope'), dict(signal_date='2026-13-01'), dict(kind='retract'),
           dict(supersedes_id='abc'), dict(body=12)]
    for overrides in bad:
        with pytest.raises(ValueError):
            user_inputs.add(payload(**overrides), now=evening(day(1)))
    with pytest.raises(LookupError):                                   # 单元没有候选
        user_inputs.add(payload(signal_date=S1.isoformat()), now=evening(day(1)))
    other = add(db, ticker='SYNA')
    with pytest.raises(ValueError):                                    # 更正的是别的标的
        user_inputs.add(payload(supersedes_id=other['id']), now=evening(day(1)))
    with pytest.raises(ValueError):                                    # 不存在的行
        user_inputs.add(payload(supersedes_id=other['id'] + 100), now=evening(day(1)))
    assert db.execute('SELECT count(*) FROM user_inputs').fetchone()[0] == 1
    ok = add(db, body='  我的判断：回踩不破  ', kind='judgment', source_url='https://example.test/a',
             ticker='synth')
    assert (ok['ticker'], ok['body'], ok['kind'], ok['signal_date']) == ('SYNTH', '我的判断：回踩不破', 'judgment', None)
    assert user_inputs.validate(payload(body='x' * 4000))['body'] == 'x' * 4000


def test_add_without_migration_026_is_unavailable(db, monkeypatch):
    monkeypatch.setattr(core_schema, 'timeline_ready', lambda conn: False)
    with pytest.raises(service.Unavailable, match='迁移 026 未执行'):
        user_inputs.add(payload(), now=evening(day(1)))
    with pytest.raises(service.Unavailable, match='迁移 026 未执行'):
        user_inputs.list_for('SYNTH', None)


def test_request_id_reused_concurrently_is_conflict(db, monkeypatch):
    """Two requests with one request_id under different tickers each pass their own ticker's lock; the
    unique index decides, and the loser is a 409, not a 500."""
    request_id = str(uuid.uuid4())
    user_inputs.add(payload(request_id=request_id), now=evening(day(1)))
    monkeypatch.setattr(execution_repo, 'user_input_by_request', lambda conn, rid: None)   # 另一请求还没提交时的视角
    with pytest.raises(ledger.Conflict):
        user_inputs.add(payload(request_id=request_id, ticker='SYNA'), now=evening(day(1)))
    assert db.execute('SELECT count(*) FROM user_inputs').fetchone()[0] == 1


def test_idempotent_and_conflict(db):
    body = payload(source_url='https://example.test/r')
    first = user_inputs.add(body, now=evening(day(1)))
    again = user_inputs.add(dict(body), now=evening(day(2)))
    assert first['status'] == 'created' and again['status'] == 'replayed'
    assert again['record']['id'] == first['record']['id']
    assert first['record']['request_hash'] == ledger.request_hash(user_inputs.validate(body))
    with pytest.raises(ledger.Conflict):
        user_inputs.add({**body, 'body': '换了内容'}, now=evening(day(2)))
    assert db.execute('SELECT count(*) FROM user_inputs').fetchone()[0] == 1


def test_correction_and_retract_visible_to_new_reviews_only(db):
    """D13: corrections and retracts are new rows; the row an earlier version cited stays readable, the
    next review's evidence block only carries what is effective at its cutoff."""
    market = Market()
    signal_day(db, market, ['SYNTH'])
    a = add(db, body='研报：毛利率上行')
    d = add(db, body='传闻：大客户砍单', kind='news', signal_date=S1.isoformat())
    for row in (a, d):
        backdate(db, row['id'], evening(day(1)) - timedelta(hours=2))
    cite_a = [{'field': 'stop', 'text': '研报支持上移止损', 'refs': [f"user:{a['id']}"]}]
    model = make_model(review=verdict('SYNTH', 'revise', '研报支持上移止损'),
                       revise=proposal('SYNTH', stop=117.0, changes=cite_a), counter=counter())
    run(db, market, 1, tickers=['SYNTH'], model=model)
    [first] = reviews(db)
    assert first['user_input_ids'] == [a['id'], d['id']] and first['new_plan_id'] is not None
    first_prompt = next(m for route, m in model.seen if route == 'review')[1].content
    assert user_block(first_prompt) == {f"user:{a['id']}", f"user:{d['id']}"}

    b = add(db, body='更正：毛利率持平', supersedes_id=a['id'])
    r = add(db, body='撤回', kind='retract', supersedes_id=d['id'], signal_date=S1.isoformat())
    for row in (b, r):
        backdate(db, row['id'], evening(day(2)) - timedelta(hours=2))
    model = make_model(review=verdict('SYNTH', 'maintain', '更正后照旧', cite=False))
    run(db, market, 2, tickers=['SYNTH'], model=model)
    second = reviews(db)[-1]
    assert second['session'] == day(2) and second['user_input_ids'] == [b['id']]
    assert next(s for s in second['signals'] if s['kind'] == 'user_input')['fired'] is True
    prompt = next(m for route, m in model.seen if route == 'review')[1].content
    assert user_block(prompt) == {f"user:{b['id']}"}                   # 旧行、撤回的行、撤回行本身都不在

    v1 = execution_repo.chain_head(db, 'SYNTH', S1)
    v1_run = data_repo.agent_run_by_id(db, v1['version']['agent_run_id'])
    assert v1_run['output']['changes'][0]['refs'] == [f"user:{a['id']}"]
    listed = {row['id']: row for row in user_inputs.list_for('SYNTH', S1)}
    assert listed[a['id']]['body'] == '研报：毛利率上行'                 # 第 1 版引用的旧行仍可读
    assert (listed[a['id']]['status'], listed[a['id']]['superseded_by']) == ('corrected', b['id'])
    assert (listed[d['id']]['status'], listed[d['id']]['superseded_by']) == ('retracted', r['id'])
    assert listed[b['id']]['status'] == 'active' and listed[r['id']]['ref'] == f"user:{r['id']}"
    assert set(listed) == {a['id'], b['id'], d['id'], r['id']}
    ticker_only = {row['id'] for row in user_inputs.list_for('SYNTH', None)}
    assert ticker_only == {a['id'], b['id'], d['id'], r['id']}       # 标的页：标的级与各链的输入都列出
    assert user_inputs.list_for('SYNTH', S1 + timedelta(days=1)) == [
        row for row in user_inputs.list_for('SYNTH', S1) if row['signal_date'] is None]


# ---------------------------------------------------------------- recheck

def test_recheck_cutoff_is_request_time(db):
    """The cutoff is the click time T0 handed in, not when the background job runs (wall clock is later)."""
    market = Market()
    v0 = signal_day(db, market, ['SYNTH'], review_when={'SYNTH': QUIET})[0]['SYNTH']
    run(db, market, 1, tickers=['SYNTH'])                              # 宿主维持，K 线采到 day(1)
    t0 = evening(day(1)) + timedelta(hours=1)
    model = make_model(review=verdict('SYNTH', 'revise', '止损应上移'),
                       revise=proposal('SYNTH', stop=117.0, review_when=QUIET,
                                       changes=[{'field': 'stop', 'text': '止损上移到 117', 'refs': ['call:1']}]),
                       counter=counter())
    row = plan_review.recheck('synth', S1, now=t0, deps=deps_for(model), run_id='plan_recheck-t0')
    assert (row['trigger'], row['session'], row['cutoff_at'], row['run_id']) == ('recheck', day(1), t0,
                                                                                 'plan_recheck-t0')
    assert (row['outcome'], row['decided_by'], row['attempt']) == ('revise', 'agent', 1)
    new = execution_repo.chain_head(db, 'SYNTH', S1)
    assert str(new['plan']['id']) == str(row['new_plan_id'])
    assert (new['version']['origin'], new['version']['cutoff_at'], new['version']['as_of_session']) == \
        ('recheck', t0, day(1))
    assert str(new['version']['supersedes_plan_id']) == str(v0['plan']['id'])
    for run_id in (row['review_agent_run_id'], row['revision_agent_run_id']):
        assert data_repo.agent_run_by_id(db, run_id)['cutoff_at'] == t0


def test_recheck_skips_quiet_day_shortcut(db):
    market = Market()
    signal_day(db, market, ['SYNTH'], review_when={'SYNTH': QUIET})
    run(db, market, 1, tickers=['SYNTH'])
    [batch] = reviews(db)
    assert (batch['outcome'], batch['decided_by']) == ('maintain', 'host')     # 批处理：无事可看
    model = make_model(review=verdict('SYNTH', 'maintain', '你要求看，我看了：照旧', cite=False))
    row = plan_review.recheck('SYNTH', S1, now=evening(day(1)) + timedelta(hours=1), deps=deps_for(model))
    assert model.calls == {'review': 1}
    assert (row['trigger'], row['outcome'], row['decided_by'], row['reason']) == \
        ('recheck', 'maintain', 'agent', '你要求看，我看了：照旧')
    assert not any(s['fired'] for s in row['signals'])
    again = plan_review.recheck('SYNTH', S1, now=evening(day(1)) + timedelta(hours=2),
                                deps=deps_for(make_model(review=verdict('SYNTH', 'maintain', '再看一次', cite=False))))
    assert again['attempt'] == 2 and len(reviews(db)) == 3


def test_recheck_preconditions(db, monkeypatch):
    market = Market()
    heads = signal_day(db, market, ['SYNA', 'SYNTH'])[0]
    now = evening(day(1))
    monkeypatch.setenv('WORKBENCH_AGENT_PROVIDER', 'chatgpt')
    connection_is(monkeypatch)
    monkeypatch.delenv('WORKBENCH_AGENT_DAILY_TOKENS', raising=False)
    plan_review.recheck_check('SYNTH', S1, now=now)                     # 通过：不抛
    with pytest.raises(LookupError):
        plan_review.recheck_check('SYNB', S1, now=now)                  # 没有链
    with pytest.raises(ValueError):
        plan_review.recheck_check('mu!', S1, now=now)

    execution_repo.insert_plan_review(db, {
        'ticker': 'SYNA', 'signal_date': S1, 'head_plan_id': heads['SYNA']['plan']['id'], 'session': day(1),
        'trigger': 'batch', 'run_id': 'run-x', 'mode': 'entry', 'timeline_version': 'timeline-1',
        'outcome': 'invalidate', 'decided_by': 'host', 'reason': '失效条件成立：收盘跌破 112.00', 'attempt': 1,
        'cutoff_at': now})
    db.commit()
    with pytest.raises(plan_review.RecheckConflict) as raised:
        plan_review.recheck_check('SYNA', S1, now=now)
    assert raised.value.reason == 'chain_not_active' and str(raised.value) == 'chain_not_active'
    with pytest.raises(plan_review.RecheckConflict, match='chain_not_active'):
        plan_review.recheck('SYNA', S1, now=now, deps=deps_for(make_model()))

    connection_is(monkeypatch, 'disconnected')
    with pytest.raises(plan_review.RecheckConflict) as raised:
        plan_review.recheck_check('SYNTH', S1, now=now)
    assert raised.value.reason == 'agent_unavailable: not_connected'
    monkeypatch.setenv('WORKBENCH_AGENT_PROVIDER', 'off')
    with pytest.raises(plan_review.RecheckConflict, match='agent_unavailable: disabled'):
        plan_review.recheck_check('SYNTH', S1, now=now)
    with pytest.raises(plan_review.RecheckConflict, match='agent_unavailable: not_connected'):
        plan_review.recheck('SYNTH', S1, now=now, deps=deps_for(make_model(), agent=settings(connection='disconnected')))

    monkeypatch.setattr(core_schema, 'timeline_ready', lambda conn: False)
    with pytest.raises(service.Unavailable, match='迁移 026 未执行'):
        plan_review.recheck_check('SYNTH', S1, now=now)
    assert [r['outcome'] for r in reviews(db, 'SYNTH')] == []          # 前置检查不写行


def test_recheck_and_batch_are_mutually_exclusive(db, agent_env):
    """Review Focus 5: while another connection holds the chain's run lock review-run:<ticker>:<signal_date>,
    a recheck is a 409 (review_in_progress) and the batch skips the chain without a row."""
    market = Market()
    signal_day(db, market, ['SYNTH'])
    with psycopg.connect(os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']) as other:
        other.execute('SELECT pg_advisory_lock(hashtext(%s))', [f'review-run:SYNTH:{S1.isoformat()}'])
        with pytest.raises(plan_review.RecheckConflict, match='review_in_progress'):
            plan_review.recheck_check('SYNTH', S1, now=evening(day(1)))
        model = make_model()
        with pytest.raises(plan_review.RecheckConflict, match='review_in_progress'):
            plan_review.recheck('SYNTH', S1, now=evening(day(1)), deps=deps_for(model))
        summary, rows, batch_model = run(db, market, 1, tickers=['SYNTH'])
        other.rollback()
        other.execute('SELECT pg_advisory_unlock_all()')
    assert model.calls == {} and batch_model.calls.get('review', 0) == 0
    assert reviews(db) == []
    assert skips(rows['review']) == {'review_in_progress': ['SYNTH']}
    plan_review.recheck_check('SYNTH', S1, now=evening(day(1)))         # 锁释放后可以重新复查


def test_recheck_precheck_does_not_probe_the_write_lock(db, agent_env):
    """The run lock alone decides the 409 at the precheck: a held transaction-level write lock
    review:<ticker>:<signal_date> (another review's write transaction) does not, so a probe can never land
    inside the batch's write transaction. A recheck that does run into it still stops before any model call
    (its read takes the write lock), and the batch skips the chain without a row."""
    market = Market()
    signal_day(db, market, ['SYNTH'])
    with psycopg.connect(os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']) as other:
        other.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', [f'review:SYNTH:{S1.isoformat()}'])
        assert plan_review.recheck_check('SYNTH', S1, now=evening(day(1))) is None
        model = make_model()
        with pytest.raises(plan_review.RecheckConflict, match='review_in_progress'):
            plan_review.recheck('SYNTH', S1, now=evening(day(1)), deps=deps_for(model))
        summary, rows, batch_model = run(db, market, 1, tickers=['SYNTH'])
        other.rollback()
    assert model.calls == {} and batch_model.calls.get('review', 0) == 0
    assert reviews(db) == []
    assert skips(rows['review']) == {'review_in_progress': ['SYNTH']}


@pytest.mark.parametrize('unlock_fails', [False, True])
def test_run_lock_connection_is_dedicated_and_closed(db, monkeypatch, unlock_fails):
    """The run lock rides on a connection opened outside the process pool (autocommit, so the pool's few
    connections are never held through model calls) and that connection is always closed on the way
    out — also when the unlock fails, which then releases the lock by closing."""
    url = os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']
    monkeypatch.setenv('INVESTMENT_ASSISTANT_DATABASE_URL', url)
    monkeypatch.setattr(core_tx, 'connection', lambda: pytest.fail('the run lock must not use the pool'))
    opened = []

    class Conn:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, params=()):
            if unlock_fails and 'unlock' in sql:
                raise psycopg.OperationalError('unlock lost')
            return self.conn.execute(sql, params)

        def close(self):
            self.conn.close()

    def spy():
        conn = REAL_LOCK_CONNECTION()
        opened.append(conn)
        return Conn(conn)

    monkeypatch.setattr(analysis_agent_runner, '_lock_connection', spy)
    with analysis_agent_runner._run_lock('SYNTH', S1) as got:
        assert got and opened[0].autocommit
    assert opened[0].closed
    with psycopg.connect(url) as probe:                                 # 锁已释放
        assert probe.execute('SELECT pg_try_advisory_lock(hashtext(%s))',
                             [f'review-run:SYNTH:{S1.isoformat()}']).fetchone()[0]
        probe.execute('SELECT pg_advisory_unlock_all()')


def test_recheck_on_a_held_chain_reviews_in_holding_mode(db):
    """A holding chain is reviewable by recheck too (mode holding); the review tool sees session S."""
    market = Market()
    v0 = signal_day(db, market, ['SYNTH'], review_when={'SYNTH': QUIET})[0]['SYNTH']
    cands = data_repo.unit_candidates(db, 'SYNTH', S1)
    decision = legacy_decisions.decide({'request_id': str(uuid.uuid4()), 'ticker': 'SYNTH', 'signal_date': S1.isoformat(),
                               'candidate_ids': [str(c['id']) for c in cands], 'proposed_action': 'buy',
                               'final_action': 'buy', 'decision': 'accept', 'reason': '合成样例',
                               'plan_id': str(v0['plan']['id'])}, now=evening(S1))['record']
    from test_workbench_plan_review import _trade
    legacy_decisions.add_trade(_trade('SYNTH', 'buy', '10', '125', day(1), decision['id']), now=evening(day(1)))
    run(db, market, 1, tickers=['SYNTH'])
    model = make_model(review=verdict('SYNTH', 'maintain', '持仓照常', first=tool('get_trigger', {}, 'r1')))
    row = plan_review.recheck('SYNTH', S1, now=evening(day(1)) + timedelta(minutes=30), deps=deps_for(model))
    assert (row['mode'], row['outcome'], row['trigger']) == ('holding', 'maintain', 'recheck')


def test_recheck_while_batch_reviews_the_chain_is_409(db, agent_env):
    """Important: the batch holds the chain's run lock through its model call, so a recheck clicked
    meanwhile is a 409 at the precheck and, if it was already queued, stops before any model call."""
    market = Market()
    signal_day(db, market, ['SYNTH'])
    seen = []
    late = make_model()

    def click():
        for attempt in (lambda: plan_review.recheck_check('SYNTH', S1, now=evening(day(1))),
                        lambda: plan_review.recheck('SYNTH', S1, now=evening(day(1)), deps=deps_for(late))):
            with pytest.raises(plan_review.RecheckConflict) as raised:
                attempt()
            seen.append(raised.value.reason)

    model = make_model(review=[click, *verdict('SYNTH', 'maintain', '批处理照旧')])
    run(db, market, 1, tickers=['SYNTH'], model=model)
    assert seen == ['review_in_progress', 'review_in_progress'] and late.calls == {}
    [row] = reviews(db)
    assert (row['trigger'], row['outcome'], row['reason']) == ('batch', 'maintain', '批处理照旧')
    plan_review.recheck_check('SYNTH', S1, now=evening(day(1)))        # 批处理结束、锁已释放


def test_second_recheck_is_409(db, agent_env):
    """A double click (or Today plus the plan page): the second recheck finds the first one's run lock."""
    market = Market()
    signal_day(db, market, ['SYNTH'], review_when={'SYNTH': QUIET})
    run(db, market, 1, tickers=['SYNTH'])
    seen = []
    second = make_model()

    def click_again():
        with pytest.raises(plan_review.RecheckConflict) as raised:
            plan_review.recheck_check('SYNTH', S1, now=evening(day(1)) + timedelta(hours=1))
        seen.append(raised.value.reason)
        with pytest.raises(plan_review.RecheckConflict) as raised:
            plan_review.recheck('SYNTH', S1, now=evening(day(1)) + timedelta(hours=1), deps=deps_for(second))
        seen.append(raised.value.reason)

    first = make_model(review=[click_again, *verdict('SYNTH', 'maintain', '第一次', cite=False)])
    row = plan_review.recheck('SYNTH', S1, now=evening(day(1)) + timedelta(hours=1), deps=deps_for(first))
    assert seen == ['review_in_progress', 'review_in_progress'] and second.calls == {}
    assert row['reason'] == '第一次' and [r['trigger'] for r in reviews(db)] == ['batch', 'recheck']
    assert db.execute("SELECT count(*) FROM agent_runs WHERE mode = 'review'").fetchone()[0] == 1


def test_ticker_chains_lists_every_chain_newest_first(db):
    market = Market()
    heads = signal_day(db, market, ['SYNA', 'SYNTH'])[0]
    execution_repo.insert_plan_review(db, {
        'ticker': 'SYNA', 'signal_date': S1, 'head_plan_id': heads['SYNA']['plan']['id'], 'session': day(1),
        'trigger': 'batch', 'run_id': 'run-x', 'mode': 'entry', 'timeline_version': 'timeline-1',
        'outcome': 'invalidate', 'decided_by': 'host', 'reason': '失效条件成立：收盘跌破 112.00', 'attempt': 1,
        'cutoff_at': evening(day(1))})
    db.commit()
    before = db.execute('SELECT count(*) FROM plan_reviews').fetchone()[0]
    out = plan_review.ticker_chains('syna', now=evening(day(1)))
    assert out == {'ticker': 'SYNA', 'chains': [{
        'signal_date': S1, 'state': 'invalidated', 'mode': None, 'version_no': 0,
        'head_plan_id': str(heads['SYNA']['plan']['id']),
        'latest_review': {'outcome': 'invalidate', 'decided_by': 'host', 'created_at': out['chains'][0]['latest_review']['created_at'],
                          'reason': '失效条件成立：收盘跌破 112.00'}}]}
    [synth] = plan_review.ticker_chains('SYNTH', now=evening(day(1)))['chains']
    assert (synth['state'], synth['latest_review']) == ('entry', None)
    assert plan_review.ticker_chains('SYNB', now=evening(day(1))) == {'ticker': 'SYNB', 'chains': []}
    assert db.execute('SELECT count(*) FROM plan_reviews').fetchone()[0] == before
    with pytest.raises(ValueError):
        plan_review.ticker_chains('mu!', now=evening(day(1)))
