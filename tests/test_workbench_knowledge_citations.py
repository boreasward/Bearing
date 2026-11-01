"""Knowledge citations are written with the plan, the revision or the review row, in the same transaction
(v2.7 spec §6.2, D18, D27). SQL: explicit test database, the plan-review harness with a scripted model."""
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.execution.repo as execution_repo
import investment_assistant.review.repo as review_repo
import json
from datetime import UTC, datetime

import pytest
from langchain_core.messages import AIMessage

from investment_assistant.analysis import briefs

import investment_assistant.core.tx as service
from test_workbench_plan_review import (STOP_UP, Market, _setup_stop, counter, day, evening, head, make_model,  # noqa: F401
                                        plan_json, reviews, run, settings, signal_day, tool, xmin)
from test_workbench_repository_growth import item_with_versions
from test_workbench_service import db   # noqa: F401  (fixture: service wired to the disposable schema)

WRITTEN = datetime(2026, 8, 1, 12, tzinfo=UTC)               # well before the first session of the harness
TICKERS = ['SYNTH', 'SYNTA', 'SYNTB']


@pytest.fixture
def item(db):
    item_id = item_with_versions(db, [(WRITTEN, TICKERS, [])])
    db.commit()
    return item_id


def ref_of(item_id):
    return f'know:{item_id}@1'


def know_proposal(ticker, ref, *, cite_ref=None, **kwargs):
    """get_indicators (call:1), get_knowledge (call:2), then a plan whose first thesis also cites the lesson."""
    payload = json.loads(plan_json(**kwargs))
    payload['thesis'][0]['refs'] = ['call:1', cite_ref or ref]
    return [tool('get_indicators', {'ticker': ticker}), tool('get_knowledge', {'ticker': ticker}, 'k1'),
            AIMessage(content=json.dumps(payload, ensure_ascii=False))]


def know_counter(ref):
    return [tool('get_news', {'days': 7}, 't2'),
            AIMessage(content=json.dumps({'counter': [{'thesis': 1, 'text': '经验在跳空时失效', 'refs': ['call:1', ref]}],
                                          'risks': [{'text': '风险', 'refs': [ref]}]}, ensure_ascii=False))]


def know_verdict(outcome, ref, reason='依经验复查'):
    return [tool('get_knowledge', {'ticker': 'SYNTH'}, 'rk'),
            AIMessage(content=json.dumps({'outcome': outcome, 'reason': reason, 'refs': [ref]}, ensure_ascii=False))]


def citations(db, item_id):
    return review_repo.citations_of(db, item_id)


def test_first_version_writes_citations(db, item):
    ref = ref_of(item)
    run(db, Market(), 0, tickers=['SYNTH'], model=make_model(propose=know_proposal('SYNTH', ref),
                                                              counter=know_counter(ref)))
    chain = head(db)
    plan_id, run_id = chain['plan']['id'], chain['version']['agent_run_id']
    rows = citations(db, item)
    assert sorted((r['field'], r['call_seq']) for r in rows) == [('counter', 2), ('risks', 2), ('thesis', 2)]
    for row in rows:
        assert (row['version'], str(row['agent_run_id']), str(row['plan_id']), row['review_id']) == (
            1, str(run_id), str(plan_id), None)
    # plan, run and citations: one transaction
    assert {xmin(db, 'trade_plans', 'id', plan_id), xmin(db, 'agent_runs', 'id', run_id),
            *{xmin(db, 'knowledge_citations', 'id', r['id']) for r in rows}} == {xmin(db, 'trade_plans', 'id', plan_id)}
    assert [c['tool'] for c in analysis_repo.agent_tool_calls(db, run_id)][:2] == ['get_indicators', 'get_knowledge']


def test_runs_without_knowledge_write_no_citation_rows(db, item):
    run(db, Market(), 0, tickers=['SYNTH'], model=make_model(
        propose=[tool('get_indicators', {'ticker': 'SYNTH'}), tool('get_news', {'days': 7}, 'n1'),
                 AIMessage(content=plan_json())], counter=counter()))
    assert head(db) is not None and db.execute('SELECT count(*) FROM knowledge_citations').fetchone()[0] == 0


def test_citations_rolled_back_with_plan(db, item, monkeypatch):
    ref = ref_of(item)
    real = briefs.for_run
    hits: list[str] = []

    def exploding(run_row, *args, **kwargs):
        if run_row.get('status') == 'ok':                     # the ok run's brief is written after the citations
            hits.append('boom')
            raise RuntimeError('brief exploded')
        return real(run_row, *args, **kwargs)

    monkeypatch.setattr(briefs, 'for_run', exploding)
    summary, _, _ = run(db, Market(), 0, tickers=['SYNTH'], model=make_model(propose=know_proposal('SYNTH', ref),
                                                                              counter=know_counter(ref)))
    assert hits                                               # the failure came after the citations were inserted
    assert db.execute('SELECT count(*) FROM knowledge_citations').fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM trade_plans WHERE kind = 'agent'").fetchone()[0] == 0
    assert head(db) is None
    runs = analysis_repo.agent_runs_for(db, 'SYNTH', day(0))
    assert runs and runs[-1]['status'] == 'error' and runs[-1]['plan_id'] is None     # the unit's error row stands alone


def test_citations_not_written_when_the_plan_row_fails(db, item, monkeypatch):
    ref = ref_of(item)
    real_store = execution_repo.store_plan

    def failing(conn, plan):
        if plan.get('kind') == 'agent':
            raise RuntimeError('plan insert failed')
        return real_store(conn, plan)

    monkeypatch.setattr(execution_repo, 'store_plan', failing)
    run(db, Market(), 0, tickers=['SYNTH'], model=make_model(propose=know_proposal('SYNTH', ref),
                                                              counter=know_counter(ref)))
    assert db.execute('SELECT count(*) FROM knowledge_citations').fetchone()[0] == 0
    assert head(db) is None


def test_revision_writes_new_plan_citations(db, item):
    ref = ref_of(item)
    market = Market()
    _setup_stop(market, None)
    signal_day(db, market, ['SYNTH'])
    v0_plan = head(db)['plan']['id']
    revise = know_proposal('SYNTH', ref, stop=117.0, changes=STOP_UP)
    model = make_model(review=know_verdict('revise', ref, '止损应上移'), revise=revise, counter=know_counter(ref))
    run(db, market, 1, tickers=['SYNTH'], model=model)
    [row] = reviews(db)
    new = head(db)
    assert new['version']['version_no'] == 1 and str(row['new_plan_id']) == str(new['plan']['id'])
    rows = citations(db, item)
    by_run = {}
    for r in rows:
        by_run.setdefault(str(r['agent_run_id']), []).append(r)
    assert set(by_run) == {str(row['review_agent_run_id']), str(row['revision_agent_run_id'])}
    plan_rows = by_run[str(row['revision_agent_run_id'])]
    assert sorted(r['field'] for r in plan_rows) == ['counter', 'risks', 'thesis']
    assert {(str(r['plan_id']), r['review_id']) for r in plan_rows} == {(str(new['plan']['id']), None)}
    assert str(new['plan']['id']) != str(v0_plan)
    [verdict_row] = by_run[str(row['review_agent_run_id'])]
    assert (verdict_row['field'], verdict_row['plan_id'], verdict_row['review_id']) == ('review', None, row['id'])
    # the version, its citations and the review row share one transaction
    assert {xmin(db, 'trade_plans', 'id', new['plan']['id']), xmin(db, 'plan_reviews', 'id', row['id']),
            *{xmin(db, 'knowledge_citations', 'id', r['id']) for r in rows}} == {xmin(db, 'plan_reviews', 'id', row['id'])}


@pytest.mark.parametrize('outcome', ['maintain', 'invalidate'])
def test_review_maintain_writes_review_id(db, item, outcome):
    ref = ref_of(item)
    market = Market()
    signal_day(db, market, ['SYNTH'])
    run(db, market, 1, tickers=['SYNTH'], model=make_model(review=know_verdict(outcome, ref)))
    [row] = reviews(db)
    assert (row['outcome'], row['decided_by']) == (outcome, 'agent')
    [cited] = citations(db, item)
    assert (cited['field'], cited['plan_id'], cited['review_id'], cited['call_seq'], cited['version']) == (
        'review', None, row['id'], 1, 1)
    assert str(cited['agent_run_id']) == str(row['review_agent_run_id'])
    assert xmin(db, 'knowledge_citations', 'id', cited['id']) == xmin(db, 'plan_reviews', 'id', row['id'])


def test_review_without_knowledge_ref_writes_nothing(db, item):
    market = Market()
    signal_day(db, market, ['SYNTH'])
    run(db, market, 1, tickers=['SYNTH'], model=make_model(review=know_verdict('maintain', 'call:1')))
    # the verdict cited only the tool call of the lesson list, not a lesson
    assert reviews(db) and citations(db, item) == []


def test_rejected_and_pass_runs_write_no_citations(db, item):
    ref = ref_of(item)
    pass_plan = json.loads(plan_json(action='pass'))
    pass_plan['thesis'][0]['refs'] = ['call:1', ref]
    pass_msgs = [tool('get_indicators', {'ticker': 'SYNTA'}), tool('get_knowledge', {'ticker': 'SYNTA'}, 'k1'),
                 AIMessage(content=json.dumps(pass_plan, ensure_ascii=False))]
    model = make_model(propose=[*pass_msgs,
                                *know_proposal('SYNTB', ref, cite_ref='know:99@1')],    # SYNTB cites an unreturned lesson
                       counter=counter())
    summary, _, _ = run(db, Market(), 0, tickers=['SYNTA', 'SYNTB'], model=model)
    runs = {t: analysis_repo.agent_runs_for(db, t, day(0))[-1] for t in ('SYNTA', 'SYNTB')}
    assert (runs['SYNTA']['status'], runs['SYNTA']['plan_id']) == ('pass', None)
    assert (runs['SYNTB']['status'], runs['SYNTB']['reason']) == ('rejected', 'invalid_citation')
    assert db.execute('SELECT count(*) FROM knowledge_citations').fetchone()[0] == 0


def test_revision_that_fails_or_passes_writes_no_plan_citations(db, item):
    ref = ref_of(item)
    market = Market()
    signal_day(db, market, ['SYNTH'])
    bad = [{'field': 'entry', 'text': '入场下移', 'refs': ['call:1']}]            # only the stop changed: mismatch
    model = make_model(review=know_verdict('revise', ref), revise=know_proposal('SYNTH', ref, stop=117.0, changes=bad),
                       counter=know_counter(ref))
    run(db, market, 1, tickers=['SYNTH'], model=model)
    [row] = reviews(db)
    assert row['outcome'] == 'revise' and row['new_plan_id'] is None
    rows = citations(db, item)
    assert [(r['field'], str(r['agent_run_id'])) for r in rows] == [('review', str(row['review_agent_run_id']))]
    assert all(r['plan_id'] is None for r in rows)                                # the failed revision cites nothing


def test_agent_run_row_versions_for_new_modes(db):
    unit = {'ticker': 'AAA', 'signal_date': day(0), 'pool': 'main'}
    now = evening(day(0))

    def row(mode):
        from investment_assistant.core.llm.model import _agent_run_row
        return _agent_run_row(unit, settings=settings(), attempt=1, now=now, status='skipped',
                                      reason='stage_budget', mode=mode)

    assert [row(m)['agent_version'] for m in ('retro', 'discuss', 'propose', 'revise', 'review', 'interpret')] == [
        'agent-retro-2', 'agent-discuss-1', 'agent-3', 'agent-3', 'agent-review-2', 'agent-brief-1']
    for mode in ('retro', 'discuss'):                             # migration 027 relaxed the mode constraint
        stored = analysis_repo.insert_agent_run(db, row(mode), [])
        assert (stored['mode'], stored['status']) == (mode, 'skipped')
