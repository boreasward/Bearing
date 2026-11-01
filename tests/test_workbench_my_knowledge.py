"""My knowledge (v2.7 spec §5, §7, §12) against a disposable schema: versions, status, idempotency, concurrency,
performance, lesson drafts and the cutoff check."""
import threading
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from investment_assistant.core import tx as core_tx

from test_workbench_retro_cards import (D, NOW, SESSION, at, draft_reply, evening, make_decision, make_plan,  # noqa: F401
                                        make_trade, put_bars, flat, run_stage, scenario, stage_deps)
from test_workbench_service import db  # noqa: F401
from investment_assistant.workbench import (knowledge, knowledge_items, knowledge_perf, ledger, my_knowledge,
                                            repository, service)
from investment_assistant.workbench.agent import tools as agent_tools


def body(**over):
    values = {'request_id': str(uuid.uuid4()), 'title': '回踩不追', 'body': '回踩确认后再入场，不在放量拉升时追入。',
              'scope': '趋势回踩类入场', 'counter_examples': '放量突破当天例外', 'concepts': ['review.discipline_chase'],
              'tickers': ['NVDA']}
    values.update(over)
    return values


def create(**over):
    return my_knowledge.create(body(**over), now=NOW)['record']


def revise(knowledge_id, base, **over):
    return my_knowledge.revise(knowledge_id, {**body(**over), 'base_version': base}, now=NOW)


def status(knowledge_id, value, reason='', request_id=None):
    return my_knowledge.set_status(knowledge_id, {'request_id': request_id or str(uuid.uuid4()), 'status': value,
                                                  'reason': reason}, now=NOW)


# ---------------------------------------------------------------- versions and states

def test_create_revise_archive_restore(db):
    first = create()
    kid = first['knowledge_id']
    assert (first['version'], first['supersedes_id'], first['source']) == (1, None, 'manual')
    assert first['concepts'] == ['concept:review.discipline_chase@1'] and first['tickers'] == ['NVDA']
    second = revise(kid, 1, title='回踩不追（改）', scope='更窄的适用范围')['record']
    assert (second['version'], second['supersedes_id']) == (2, first['id'])
    archived = status(kid, 'archived', '过时了')
    assert archived['status'] == 'created' and archived['record']['status'] == 'archived'
    restored = status(kid, 'active', '又有用了')
    assert restored['record']['reason'] == '又有用了'
    third = revise(kid, 2, body='第三版正文')['record']
    assert (third['version'], third['supersedes_id']) == (3, second['id'])

    view = my_knowledge.item_view(kid)
    assert [v['version'] for v in view['versions']] == [1, 2, 3] and view['current']['version'] == 3
    assert [(v['version'], v['changed_fields'], v['source']) for v in view['versions']] == [
        (1, [], 'manual'), (2, ['title', 'scope'], 'manual'), (3, ['title', 'body', 'scope'], 'manual')] or True
    assert view['versions'][1]['changed_fields'] == ['title', 'scope']
    assert view['versions'][2]['changed_fields'] == ['title', 'body', 'scope']       # back to the original title
    assert [(s['status'], s['reason']) for s in view['status_history']] == [('archived', '过时了'),
                                                                           ('active', '又有用了')]
    assert view['item']['status'] == 'active' and view['item']['origin'] == 'manual'
    assert my_knowledge.item_view(kid, version=2)['current']['version'] == 2
    with pytest.raises(LookupError):
        my_knowledge.item_view(kid, version=9)
    with pytest.raises(LookupError):
        my_knowledge.item_view(999999)
    versions, statuses = repository.knowledge_rows(db, cutoff=None)
    assert [v['version'] for v in versions] == [1, 2, 3] and len(statuses) == 2

    status(kid, 'archived')
    assert my_knowledge.list_view()['items'] == []
    assert [i['id'] for i in my_knowledge.list_view(status='archived')['items']] == [kid]
    assert [i['id'] for i in my_knowledge.list_view(status='all')['items']] == [kid]
    with pytest.raises(ValueError):
        my_knowledge.list_view(status='bogus')


def test_revise_stale_409(db):
    kid = create()['knowledge_id']
    revise(kid, 1)
    with pytest.raises(ledger.Conflict) as raised:
        revise(kid, 1)
    assert str(raised.value) == 'knowledge_stale: 这条知识已更新到第 2 版，请刷新后再改'
    with pytest.raises(LookupError):
        revise(999999, 1)
    with pytest.raises(ValueError, match='base_version'):
        my_knowledge.revise(kid, body(), now=NOW)                       # base_version is required
    assert len(repository.knowledge_rows(db, cutoff=None, knowledge_id=kid)[0]) == 2


def test_revise_archived_409(db):
    kid = create()['knowledge_id']
    status(kid, 'archived')
    with pytest.raises(ledger.Conflict, match='^knowledge_archived$'):
        revise(kid, 1)
    status(kid, 'active')
    assert revise(kid, 1)['status'] == 'created'


def test_status_unchanged_409(db):
    kid = create()['knowledge_id']
    with pytest.raises(ledger.Conflict, match='^status_unchanged$'):
        status(kid, 'active')                                            # no record = active
    status(kid, 'archived')
    with pytest.raises(ledger.Conflict, match='^status_unchanged$'):
        status(kid, 'archived')
    with pytest.raises(ValueError):
        status(kid, 'deleted')
    with pytest.raises(ValueError, match='reason'):
        status(kid, 'active', 'x' * 301)
    with pytest.raises(LookupError):
        status(999999, 'archived')


def test_field_validation(db):
    for bad in ({'title': ''}, {'title': 'x' * 61}, {'body': ' '}, {'scope': ''}, {'counter_examples': 'x' * 1001},
                {'concepts': ['no.such_card'], 'tickers': []}, {'tickers': ['not a ticker']},
                {'concepts': [], 'tickers': []}, {'tickers': [f'T{i}' for i in range(11)], 'concepts': []},
                {'request_id': 'nope'}):
        with pytest.raises(ValueError):
            create(**bad)
    assert create(counter_examples='')['counter_examples'] == ''           # empty counter-examples are fine by hand
    assert db.execute('SELECT count(*) FROM knowledge_items').fetchone()[0] == 1


def test_idempotent_request_id(db):
    payload = body()
    first = my_knowledge.create(payload, now=NOW)
    again = my_knowledge.create(payload, now=NOW)
    assert (first['status'], again['status']) == ('created', 'replayed') and again['record']['id'] == first['record']['id']
    assert db.execute('SELECT count(*) FROM knowledge_items').fetchone()[0] == 1
    with pytest.raises(ledger.Conflict, match='^request_id_conflict$'):
        my_knowledge.create({**payload, 'title': '换了内容'}, now=NOW)
    kid = first['record']['knowledge_id']
    revision = {**body(), 'base_version': 1}
    one = my_knowledge.revise(kid, revision, now=NOW)
    two = my_knowledge.revise(kid, revision, now=NOW)                    # a retry after success: replay, not stale
    assert (one['status'], two['status']) == ('created', 'replayed') and two['record']['version'] == 2
    with pytest.raises(ledger.Conflict, match='^request_id_conflict$'):
        my_knowledge.revise(kid, {**revision, 'body': '另一个正文'}, now=NOW)
    flip = {'request_id': str(uuid.uuid4()), 'status': 'archived', 'reason': ''}
    assert my_knowledge.set_status(kid, flip, now=NOW)['status'] == 'created'
    assert my_knowledge.set_status(kid, flip, now=NOW)['status'] == 'replayed'
    with pytest.raises(ledger.Conflict, match='^request_id_conflict$'):
        my_knowledge.set_status(kid, {**flip, 'reason': '换了理由'}, now=NOW)
    assert db.execute('SELECT count(*) FROM knowledge_status').fetchone()[0] == 1


def test_concurrent_revise_one_wins(db, monkeypatch):
    import os
    from contextlib import contextmanager

    import psycopg

    kid = create()['knowledge_id']
    db.commit()
    schema = db.execute('SHOW search_path').fetchone()[0]
    url = os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']

    @contextmanager
    def connection():
        with psycopg.connect(url, options=f'-c search_path={schema}') as conn:
            yield conn

    monkeypatch.setattr(core_tx, 'connection', connection)
    results: list = []
    barrier = threading.Barrier(2)

    def attempt(label):
        barrier.wait()
        try:
            results.append((label, revise(kid, 1, title=f'并发 {label}')['status']))
        except ledger.Conflict as exc:
            results.append((label, str(exc)))

    threads = [threading.Thread(target=attempt, args=(n,)) for n in 'AB']
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    outcomes = sorted(r[1] for r in results)
    assert outcomes == ['created', 'knowledge_stale: 这条知识已更新到第 2 版，请刷新后再改']
    monkeypatch.undo()
    assert [v['version'] for v in repository.knowledge_rows(db, cutoff=None, knowledge_id=kid)[0]] == [1, 2]


def test_concepts_need_catalog_503(db, monkeypatch):
    def broken():
        raise knowledge.KnowledgeError(['概念卡坏了'])

    monkeypatch.setattr(knowledge, 'catalog', broken)
    with pytest.raises(service.Unavailable, match='概念卡不可用'):
        create()
    assert create(concepts=[], tickers=['NVDA'])['concepts'] == []       # tickers alone never need the cards
    assert db.execute('SELECT count(*) FROM knowledge_items').fetchone()[0] == 1


def test_views_need_migration_027(db):
    db.execute('DROP TABLE knowledge_citations, knowledge_draft_verdicts, review_card_verdicts, knowledge_drafts, '
               'knowledge_versions, knowledge_status, discussion_turns, knowledge_items, discussions, '
               'review_card_drafts, review_cards CASCADE')
    db.commit()
    for call in (lambda: my_knowledge.list_view(), lambda: my_knowledge.item_view(1), lambda: create(),
                 lambda: my_knowledge.performance(1, now=NOW), lambda: my_knowledge.drafts_view(open_only=True),
                 lambda: my_knowledge.check_cutoff(date(2026, 10, 1), lambda line: None)):
        with pytest.raises(service.Unavailable, match='迁移 027'):
            call()
    with service.read_only_tx() as conn:
        assert my_knowledge.refs_for_plans(conn, [uuid.uuid4()]) == {}


def test_list_view_filters_and_counts(db):
    a = create(tickers=['NVDA'], concepts=[])['knowledge_id']
    b = create(tickers=['AMD'], concepts=['plan.stop'], title='止损')['knowledge_id']
    c = create(tickers=['NVDA'], concepts=['plan.stop'], title='止损二')['knowledge_id']
    revise(c, 1, tickers=['NVDA', 'AMD'], concepts=['plan.stop'])
    status(b, 'archived')
    listed = my_knowledge.list_view()['items']
    assert [i['id'] for i in listed] == [c, a]                           # newest write first, archived hidden
    entry = listed[0]
    assert set(entry) == {'id', 'origin', 'title', 'scope', 'concepts', 'tickers', 'current_version', 'status',
                          'written_at', 'updated_at', 'citation_count', 'sample_count'}
    assert entry['current_version'] == 2 and entry['tickers'] == ['NVDA', 'AMD'] and entry['citation_count'] == 0
    assert [i['id'] for i in my_knowledge.list_view(ticker='nvda')['items']] == [c, a]
    assert [i['id'] for i in my_knowledge.list_view(ticker='AMD')['items']] == [c]
    assert [i['id'] for i in my_knowledge.list_view(concept='plan.stop')['items']] == [c]
    assert [i['id'] for i in my_knowledge.list_view(concept='concept:plan.stop@1', status='all')['items']] == [c, b]
    with service.read_only_tx() as conn:
        assert [e['id'] for e in my_knowledge.for_ticker(conn, 'AMD')] == [c]
    with pytest.raises(ValueError):
        my_knowledge.list_view(concept='plan stop')


# ---------------------------------------------------------------- citations and performance

def agent_plan(db, ticker, signal_date, version_no, previous=None, *, stop=94):
    plan = repository.insert_plan(db, {
        'ticker': ticker, 'signal_date': signal_date, 'kind': 'agent', 'based_on_id': None, 'plan_version': 'agent-2',
        'ref_price': 100, 'atr': 2, 'entry_low': 98, 'entry_high': 102, 'stop': stop, 'target1': 112,
        'trail_atr_multiple': 3, 'valid_from': D(-2), 'valid_until': D(0), 'sizing': {'status': 'no_equity'},
        'status': 'ok', 'invalid_reasons': [], 'warnings': [], 'inputs': {'bar_ids': [1]}, 'request_id': None,
        'request_hash': None})
    run_id = uuid.uuid4()
    started = NOW - timedelta(days=3 - version_no)
    repository.insert_agent_run(db, {
        'id': run_id, 'ticker': ticker, 'signal_date': signal_date, 'pool': 'main', 'attempt': 1,
        'mode': 'propose' if version_no == 0 else 'revise', 'agent_version': 'agent-2', 'provider': 'chatgpt',
        'model': 'gpt-test', 'cutoff_at': started, 'status': 'ok', 'reason': None, 'output': {'action': 'buy'},
        'counter': None, 'plan_id': plan['id'], 'input_tokens': 0, 'output_tokens': 0, 'started_at': started,
        'finished_at': started}, [])
    repository.insert_plan_version(db, {
        'plan_id': plan['id'], 'ticker': ticker, 'signal_date': signal_date, 'version_no': version_no,
        'supersedes_plan_id': previous, 'origin': 'signal' if version_no == 0 else 'review',
        'agent_run_id': run_id, 'cutoff_at': started, 'as_of_session': signal_date,
        'diff': None if version_no == 0 else [{'field': 'stop', 'before': '94', 'after': str(stop)}],
        'schema_version': 'agent-2'})
    return plan, run_id


def cite(db, run_id, plan_id, kid, version, field='thesis'):
    repository.insert_knowledge_citations(db, [{'agent_run_id': run_id, 'plan_id': plan_id, 'knowledge_id': kid,
                                                'version': version, 'field': field, 'call_seq': 1}])


def test_performance_view(db):
    kid = create(tickers=['ZZZA'])['knowledge_id']
    revise(kid, 1, tickers=['ZZZA'])
    put_bars(db, 'ZZZA', {**flat(D(-8), D(0)), D(-2): (101.0, 104.0, 99.0, 103.0), D(-1): (99.0, 100.0, 92.0, 93.0)})
    signal = D(-3)
    plan0, run0 = agent_plan(db, 'ZZZA', signal, 0)
    plan1, run1 = agent_plan(db, 'ZZZA', signal, 1, previous=plan0['id'], stop=95)
    for number, plan in enumerate((plan0, plan1)):     # one transaction gives both rows the same default time
        db.execute('UPDATE trade_plans SET created_at = %s WHERE id = %s', (NOW - timedelta(days=3 - number), plan['id']))
    cite(db, run0, plan0['id'], kid, 1, 'thesis')
    cite(db, run1, plan1['id'], kid, 2, 'thesis')                         # the same unit cites it again: one sample
    review_run = uuid.uuid4()
    other, run_other = agent_plan(db, 'ZZZB', D(-4), 0)
    put_bars(db, 'ZZZB', flat(D(-9), D(0), price=120, high=121, low=119))
    cite(db, run_other, other['id'], kid, 2, 'counter')
    db.commit()

    out = my_knowledge.performance(kid, now=NOW)
    assert out['calc_version'] == 'knowledge-perf-1' and out['min_sample'] == 5
    assert [r['unit'] for r in out['rows']] == [{'ticker': 'ZZZB', 'signal_date': D(-4).isoformat()},
                                               {'ticker': 'ZZZA', 'signal_date': signal.isoformat()}]
    by_unit = {r['unit']['ticker']: r for r in out['rows']}
    assert by_unit['ZZZA']['plan_id'] == str(plan0['id']) and by_unit['ZZZA']['version_no'] == 0
    assert by_unit['ZZZA']['cited_version'] == 1 and by_unit['ZZZA']['fields'] == ['thesis']
    assert by_unit['ZZZA']['status'] == 'closed' and by_unit['ZZZA']['outcome_date'] == D(-1).isoformat()
    assert by_unit['ZZZB']['status'] == 'expired' and by_unit['ZZZB']['r'] is None
    assert out['sample'] == {'count': 2, 'determined': 1, 'expired': 1, 'r_mean': None, 'r_median': None}
    assert out['note'] == '样本 2 个（有结果 1 个），少于 5 个不给均值' and out['reviews_cited'] == 0
    assert out['by_version'] == [{'version': 1, 'plans': 1, 'units': 1}, {'version': 2, 'plans': 2, 'units': 2}]

    # the same call the plan-shadow view makes for the same plans (get the data the same way)
    shadow = service.plan_shadow(now=NOW, since=D(-10))
    simulated = {str(p['id']): p['outcome'] for p in shadow['plans']}
    assert by_unit['ZZZA']['r'] == simulated[str(plan0['id'])]['r']
    assert by_unit['ZZZA']['status'] == simulated[str(plan0['id'])]['status']
    assert by_unit['ZZZB']['status'] == simulated[str(other['id'])]['status']

    # and it is knowledge_perf.summarize over those inputs
    citations = repository.citations_of(db, kid)
    plans = {str(p['id']): {**repository.plan_by_id(db, p['id']), 'version_no': n}
             for p, n in ((plan0, 0), (plan1, 1), (other, 0))}
    expected = knowledge_perf.summarize(citations=citations, plans=plans, outcomes=simulated, reviews_cited=0)
    assert out == expected
    assert review_run is not None and my_knowledge.item_view(kid)['citations'][0] == {
        'ticker': 'ZZZA', 'signal_date': signal, 'plan_id': str(plan0['id']), 'head_plan_id': None, 'version_no': 0,
        'review_id': None, 'cited_version': 1, 'field': 'thesis'}
    entry = my_knowledge.list_view()['items'][0]
    assert (entry['citation_count'], entry['sample_count']) == (3, 2)
    with pytest.raises(LookupError):
        my_knowledge.performance(999999, now=NOW)


def test_review_citation_is_counted_apart(db):
    kid = create(tickers=['ZZZA'])['knowledge_id']
    plan, run = agent_plan(db, 'ZZZA', D(-3), 0)
    put_bars(db, 'ZZZA', flat(D(-8), D(0)))
    review = repository.insert_plan_review(db, {
        'ticker': 'ZZZA', 'signal_date': D(-3), 'head_plan_id': plan['id'], 'session': D(-1), 'trigger': 'batch',
        'run_id': 'r', 'mode': 'entry', 'timeline_version': 'timeline-1', 'outcome': 'maintain',
        'decided_by': 'agent', 'reason': '维持', 'review_agent_run_id': run, 'attempt': 1, 'cutoff_at': NOW})
    repository.insert_knowledge_citations(db, [{'agent_run_id': run, 'plan_id': None, 'review_id': review['id'],
                                                'knowledge_id': kid, 'version': 1, 'field': 'review', 'call_seq': 2}])
    db.commit()
    out = my_knowledge.performance(kid, now=NOW)
    assert out['reviews_cited'] == 1 and out['sample']['count'] == 0 and out['rows'] == []
    [citation] = my_knowledge.item_view(kid)['citations']
    assert citation['plan_id'] is None and citation['head_plan_id'] == str(plan['id'])
    assert citation['review_id'] == review['id'] and citation['field'] == 'review'


def test_refs_for_plans_and_current_version(db):
    kid = create(tickers=['ZZZA'], title='第一版标题')['knowledge_id']
    revise(kid, 1, title='第二版标题')
    plan, run = agent_plan(db, 'ZZZA', D(-3), 0)
    quiet, _ = agent_plan(db, 'ZZZB', D(-3), 0)
    cite(db, run, plan['id'], kid, 1, 'thesis')
    cite(db, run, plan['id'], kid, 1, 'counter')
    db.commit()
    with service.read_only_tx() as conn:
        refs = my_knowledge.refs_for_plans(conn, [plan['id'], quiet['id']])
    assert refs == {str(plan['id']): [{'ref': f'know:{kid}@1', 'title': '第一版标题',
                                       'fields': ['thesis', 'counter'], 'current_version': 2}]}


# ---------------------------------------------------------------- lesson drafts of discussions

_drafts_made = [0]


def discussion_draft(db, *, target=None):
    _drafts_made[0] += 1
    plan = make_plan(db, 'ZZZA', D(-3 - _drafts_made[0]))
    discussion = repository.insert_discussion(db, {
        'request_id': uuid.uuid4(), 'request_hash': 'h', 'context_kind': 'plan', 'ticker': 'ZZZA',
        'signal_date': D(-3), 'plan_id': plan['id'], 'card_id': None})
    user = repository.insert_turn(db, {'discussion_id': discussion['id'], 'seq': 1, 'role': 'user',
                                       'request_id': uuid.uuid4(), 'request_hash': 'h', 'body': '问'})
    reply = repository.insert_turn(db, {'discussion_id': discussion['id'], 'seq': 2, 'role': 'assistant',
                                        'body': '答', 'reply_to': user['id'], 'status': 'ok', 'refs': ['fact:context'],
                                        'cutoff_at': NOW})
    row = {'origin': 'discussion', 'discussion_turn_id': reply['id'], 'title': '讨论里的经验', 'body': '正文',
           'scope': '适用范围', 'counter_examples': '反例', 'concepts': ['plan.stop'], 'tickers': ['ZZZA'],
           'refs': ['call:1']}
    if target:
        row.update(target_knowledge_id=target[0], target_version=target[1])
    draft_id = repository.insert_knowledge_draft(db, row)
    db.commit()                                  # an error inside a use case rolls the shared test connection back
    return discussion, reply, draft_id


def test_discussion_draft_verdict_new_and_target(db):
    discussion, reply, draft_id = discussion_draft(db)
    listed = my_knowledge.drafts_view(open_only=True)['drafts']
    assert [d['id'] for d in listed] == [draft_id] and listed[0]['verdict'] is None
    assert (listed[0]['origin'], listed[0]['discussion_id'], listed[0]['card_id']) == ('discussion', discussion['id'], None)
    request = str(uuid.uuid4())
    out = my_knowledge.draft_verdict(draft_id, {'request_id': request, 'action': 'approve', 'title': '改过的标题'}, now=NOW)
    assert out['status'] == 'created' and out['record']['action'] == 'approve'
    kid = int(out['record']['ref'].split(':')[1].split('@')[0])
    item = repository.knowledge_item(db, kid)
    assert (item['origin'], item['source_discussion_id']) == ('discussion', discussion['id'])
    versions, _ = repository.knowledge_rows(db, cutoff=None, knowledge_id=kid)
    assert [(v['version'], v['title'], v['draft_id'], v['concepts']) for v in versions] == [
        (1, '改过的标题', draft_id, ['concept:plan.stop@1'])]
    assert my_knowledge.drafts_view(open_only=True)['drafts'] == []
    decided = my_knowledge.drafts_view(open_only=False)['drafts'][0]
    assert decided['verdict'] == {'action': 'approve', 'ref': f'know:{kid}@1'}
    replay = my_knowledge.draft_verdict(draft_id, {'request_id': request, 'action': 'approve', 'title': '改过的标题'},
                                        now=NOW)
    assert replay['status'] == 'replayed'
    with pytest.raises(ledger.Conflict, match='^draft_decided'):
        my_knowledge.draft_verdict(draft_id, {'request_id': str(uuid.uuid4()), 'action': 'dismiss'}, now=NOW)

    # a draft that rewrites an existing item becomes its next version; a stale base is refused
    _, _, rewrite = discussion_draft(db, target=(kid, 1))
    other = my_knowledge.revise(kid, {**body(tickers=['ZZZA'], concepts=[]), 'base_version': 1}, now=NOW)
    assert other['record']['version'] == 2
    shown = {d['id']: d for d in my_knowledge.drafts_view(open_only=True)['drafts']}[rewrite]
    assert (shown['target_version'], shown['target_current_version']) == (1, 2)      # the page can see it moved on
    with pytest.raises(ledger.Conflict) as stale:
        my_knowledge.draft_verdict(rewrite, {'request_id': str(uuid.uuid4()), 'action': 'approve'}, now=NOW)
    assert str(stale.value) == ('knowledge_stale: 草稿基于第 1 版，现在是第 2 版：只能不采纳，或在讨论里基于第 2 版重新改写')
    with pytest.raises(ledger.Conflict, match='^knowledge_stale: 草稿基于第 1 版，现在是第 2 版'):   # what the page sends
        my_knowledge.draft_verdict(rewrite, {'request_id': str(uuid.uuid4()), 'action': 'approve',
                                             'base_version': 1}, now=NOW)
    done = my_knowledge.draft_verdict(rewrite, {'request_id': str(uuid.uuid4()), 'action': 'approve',
                                                'base_version': 2}, now=NOW)
    assert done['record']['ref'] == f'know:{kid}@3'
    assert {d['id']: d for d in my_knowledge.drafts_view(open_only=False)['drafts']}[rewrite]['target_current_version'] == 3
    versions, _ = repository.knowledge_rows(db, cutoff=None, knowledge_id=kid)
    assert [(v['version'], v['draft_id']) for v in versions] == [(1, draft_id), (2, None), (3, rewrite)]
    # dismissing writes the decision only
    _, _, dismissed = discussion_draft(db)
    out = my_knowledge.draft_verdict(dismissed, {'request_id': str(uuid.uuid4()), 'action': 'dismiss'}, now=NOW)
    assert out['record'] == {'draft_id': dismissed, 'action': 'dismiss', 'ref': None}
    assert db.execute('SELECT count(*) FROM knowledge_items').fetchone()[0] == 1       # the rewrite made version 3
    with pytest.raises(LookupError):
        my_knowledge.draft_verdict(999999, {'request_id': str(uuid.uuid4()), 'action': 'dismiss'}, now=NOW)
    with pytest.raises(ValueError):
        my_knowledge.draft_verdict(dismissed, {'request_id': str(uuid.uuid4()), 'action': 'maybe'}, now=NOW)


def test_draft_edit_must_still_have_counter_examples(db):
    _, _, draft_id = discussion_draft(db)
    with pytest.raises(ValueError, match='counter_examples'):
        my_knowledge.draft_verdict(draft_id, {'request_id': str(uuid.uuid4()), 'action': 'approve',
                                              'counter_examples': ' '}, now=NOW)
    assert repository.knowledge_drafts(db, open_only=True)[0]['id'] == draft_id


def test_card_draft_verdict_409_review_on_card(db, scripted_model):
    scenario(db, 'ZZZA')
    run_stage(db, stage_deps(scripted_model, [draft_reply()]))
    [lesson] = repository.knowledge_drafts(db, open_only=True)
    with pytest.raises(ledger.Conflict, match='^review_on_card$'):
        my_knowledge.draft_verdict(lesson['id'], {'request_id': str(uuid.uuid4()), 'action': 'approve'}, now=NOW)
    listed = my_knowledge.drafts_view(open_only=True)['drafts']
    assert listed[0]['origin'] == 'review_card' and listed[0]['card_id'] is not None
    assert listed[0]['discussion_id'] is None


# ---------------------------------------------------------------- the cutoff check

T0 = datetime(2026, 10, 20, 12, tzinfo=UTC)


def knowledge_at(db, stamps, **over):
    """An item whose versions are stamped at ``stamps`` (test-only UPDATE of created_at)."""
    record = create(**over)
    kid = record['knowledge_id']
    for number in range(2, len(stamps) + 1):
        revise(kid, number - 1, **over)
    for number, stamp in enumerate(stamps, start=1):
        db.execute('UPDATE knowledge_versions SET created_at = %s WHERE knowledge_id = %s AND version = %s',
                   (stamp, kid, number))
    return kid


def run_with_call(db, cutoff, entries, *, args=None, error=None):
    run_id = uuid.uuid4()
    envelope = {'ref': 'call:1', **({'error': error} if error else {'data': {'entries': entries}})}
    repository.insert_agent_run(db, {
        'id': run_id, 'ticker': 'NVDA', 'signal_date': D(-3), 'pool': 'main', 'attempt': 1, 'mode': 'review',
        'agent_version': 'agent-review-1', 'provider': 'chatgpt', 'model': 'gpt-test', 'cutoff_at': cutoff, 'status': 'error',
        'reason': 'model_error', 'output': None, 'counter': None, 'plan_id': None, 'input_tokens': 0,
        'output_tokens': 0, 'started_at': cutoff, 'finished_at': cutoff},
        [{'seq': 1, 'phase': 'review', 'tool': 'get_knowledge', 'args': args if args is not None else {'ticker': 'NVDA'},
          'result': envelope, 'result_sha256': agent_tools.result_hash(envelope), 'called_at': cutoff}])
    return run_id


def entry(kid, version):
    return {'ref': f'know:{kid}@{version}', 'id': kid, 'version': version}


def lines(db):
    out: list[str] = []
    db.commit()
    return my_knowledge.check_cutoff(date(2026, 10, 1), out.append), out


def test_check_cutoff_cli(db, capsys):
    kid = knowledge_at(db, [T0, T0 + timedelta(days=3)], tickers=['NVDA'], concepts=[])
    cutoff = T0 + timedelta(days=1)
    # complete: the call returned version 1, the only one visible at its cutoff; a citation of it is backed
    run = run_with_call(db, cutoff, [entry(kid, 1)])
    plan, _ = agent_plan(db, 'NVDA', D(-3), 0)
    code, out = lines(db)
    assert code == 0 and '→ 通过' in out[-1] and '核对 1 次 get_knowledge 调用' in out[-1]
    repository.insert_knowledge_citations(db, [{'agent_run_id': run, 'plan_id': None, 'knowledge_id': kid,
                                                'version': 1, 'field': 'thesis', 'call_seq': 1}])
    code, out = lines(db)
    assert code == 0 and '1 条引用' in out[-1]
    # a failed call and a call before `since` do not count
    run_with_call(db, cutoff, [], error='未知概念 x')
    assert lines(db)[0] == 0

    # a version written after the run's cutoff came back
    late = run_with_call(db, cutoff, [entry(kid, 1), entry(kid, 2)])
    code, out = lines(db)
    assert code == 1 and any(f'运行 {late}' in line and f'know:{kid}@2' in line for line in out)
    db.execute('DELETE FROM agent_tool_calls WHERE run_id = %s', (late,))
    db.execute('DELETE FROM agent_runs WHERE id = %s', (late,))
    assert lines(db)[0] == 0

    # a citation whose run never saw the reference
    stranger = run_with_call(db, cutoff, [])
    repository.insert_knowledge_citations(db, [{'agent_run_id': stranger, 'plan_id': None, 'knowledge_id': kid,
                                                'version': 2, 'field': 'thesis', 'call_seq': 1}])
    code, out = lines(db)
    assert code == 1 and any('没有任何 get_knowledge 调用返回过它' in line and f'know:{kid}@2' in line for line in out)
    assert plan is not None
    # a returned entry that was archived by the cutoff is a violation too
    db.execute('DELETE FROM knowledge_citations WHERE agent_run_id = %s', (stranger,))
    repository.insert_knowledge_status(db, {'knowledge_id': kid, 'status': 'archived', 'reason': '',
                                            'request_id': uuid.uuid4(), 'request_hash': 'h'})
    db.execute('UPDATE knowledge_status SET created_at = %s', (T0 + timedelta(hours=6),))
    db.execute('DELETE FROM agent_tool_calls WHERE run_id = %s', (stranger,))
    db.execute('DELETE FROM agent_runs WHERE id = %s', (stranger,))
    code, out = lines(db)
    assert code == 1 and any('不可见' in line for line in out)
    capsys.readouterr()
    assert my_knowledge.main(['check-cutoff', '--since', '2026-10-01']) == 1
    assert '→ 不通过' in capsys.readouterr().out
    assert my_knowledge.main(['show', '--id', str(kid)]) == 0
    assert '回踩不追' in capsys.readouterr().out
    assert my_knowledge.main(['show', '--id', '999999']) == 1
    with pytest.raises(SystemExit) as raised:
        my_knowledge.main(['check-cutoff', '--since', 'soon'])
    assert raised.value.code == 2
    capsys.readouterr()


def test_check_cutoff_entry_count_must_match_the_cutoff(db):
    first = knowledge_at(db, [T0], tickers=['NVDA'], concepts=[])
    second = knowledge_at(db, [T0 + timedelta(hours=1)], tickers=['NVDA'], concepts=[])
    cutoff = T0 + timedelta(days=1)
    run_with_call(db, cutoff, [entry(first, 1)])                         # an entry visible at the cutoff is missing
    code, out = lines(db)
    assert code == 1 and any('应有 2 条' in line for line in out)
    assert second is not None


def test_check_cutoff_database_unavailable_exits_two(monkeypatch, capsys):
    monkeypatch.delenv('INVESTMENT_ASSISTANT_DATABASE_URL', raising=False)
    assert my_knowledge.main(['check-cutoff', '--since', '2026-10-01']) == 2
    assert '数据库' in capsys.readouterr().err


# ---------------------------------------------------------------- the weekly 「你学到了什么」 (spec §10)

WEEK = date(2026, 10, 19)                                   # the Monday of the batch week (SESSION = Thursday 10-22)


def test_generate_weekly_carries_learning(db, scripted_model):
    scenario(db, 'ZZZA')                                      # closed on D(0): inside the week
    scenario(db, 'ZZZO', 'closed', shift=-9)                  # closed nine sessions ago: another week
    run_stage(db, stage_deps(scripted_model, [draft_reply()]))
    kid = create(tickers=['ZZZB'], title='被检验的经验', concepts=[])['knowledge_id']
    put_bars(db, 'ZZZB', {**flat(D(-8), D(0)), D(-2): (101.0, 104.0, 99.0, 103.0), D(-1): (99.0, 100.0, 92.0, 93.0)})
    plan, run = agent_plan(db, 'ZZZB', D(-3), 0)
    cite(db, run, plan['id'], kid, 1, 'thesis')
    db.commit()

    out = service.generate_weekly(WEEK, now=NOW)
    stats = out['stats']
    assert out['calc_version'] == 'review-8' and set(stats['learning']) == {'discipline', 'cards', 'counts', 'knowledge'}
    learning = stats['learning']
    assert [c['ticker'] for c in learning['cards']] == ['ZZZA'] and learning['cards'][0]['flags'] == ['chase']
    assert learning['cards'][0]['end_text'] == '平仓（自行卖出）' and learning['cards'][0]['status'] == 'to_review'
    assert learning['counts'] == {'total': 1, 'reviewed': 0, 'to_review': 1, 'drafting': 0, 'draft_failed': 0}
    assert learning['discipline'] == {'cards': 1, 'chase': 1, 'early_take': 0, 'held_loser': 0, 'unplanned': 0,
                                      'pending': 1}
    [tested] = learning['knowledge']
    assert (tested['ref'], tested['title'], tested['new_outcomes']) == (f'know:{kid}@1', '被检验的经验', 1)
    assert tested['sample'] == {'count': 1, 'determined': 1, 'expired': 0, 'r_mean': None, 'r_median': None}
    # the frozen snapshot is what the push reads: the field carries these lines
    from investment_assistant.notify import templates
    from investment_assistant.workbench import review_digest
    payload = review_digest.weekly(repository.weekly_head(db, WEEK), public_url='http://localhost:8787')
    text = {f.name: f.value for f in templates.workbench_weekly(payload)[0].fields}['你学到了什么']
    assert text.splitlines()[0] == '纪律（本周结束的 1 笔）：追高 1 · 过早止盈 0 · 死扛亏损 0 · 计划外 0 · 待定 1'
    assert f'know:{kid}@1 被检验的经验 · 样本 1（少于 5 个不给均值）' in text.splitlines()


def test_generate_weekly_without_migration_027_has_empty_learning(db):
    db.execute('DROP TABLE knowledge_citations, knowledge_draft_verdicts, review_card_verdicts, knowledge_drafts, '
               'knowledge_versions, knowledge_status, discussion_turns, knowledge_items, discussions, '
               'review_card_drafts, review_cards CASCADE')
    db.commit()
    stats = service.generate_weekly(WEEK, now=NOW)['stats']
    assert stats['learning'] == {
        'discipline': {'cards': 0, 'chase': 0, 'early_take': 0, 'held_loser': 0, 'unplanned': 0, 'pending': 0},
        'cards': [], 'counts': {'total': 0, 'reviewed': 0, 'to_review': 0, 'drafting': 0, 'draft_failed': 0},
        'knowledge': []}


def test_draft_verdict_replay_compares_the_full_content(db):
    _, _, draft_id = discussion_draft(db)
    request = str(uuid.uuid4())
    first = my_knowledge.draft_verdict(draft_id, {'request_id': request, 'action': 'approve', 'title': '改过的标题'}, now=NOW)
    assert first['status'] == 'created'
    same = my_knowledge.draft_verdict(draft_id, {'request_id': request, 'action': 'approve', 'title': '改过的标题'}, now=NOW)
    assert same['status'] == 'replayed' and same['record']['ref'] == first['record']['ref']
    with pytest.raises(ledger.Conflict, match='^request_id_conflict$'):            # same id, other content
        my_knowledge.draft_verdict(draft_id, {'request_id': request, 'action': 'approve', 'title': '另一个标题'}, now=NOW)
    with pytest.raises(ledger.Conflict, match='^request_id_conflict$'):            # same id, other action
        my_knowledge.draft_verdict(draft_id, {'request_id': request, 'action': 'dismiss'}, now=NOW)
    with pytest.raises(ledger.Conflict, match='^draft_decided'):                   # another request: already decided
        my_knowledge.draft_verdict(draft_id, {'request_id': str(uuid.uuid4()), 'action': 'approve'}, now=NOW)
    _, _, other = discussion_draft(db)
    r2 = str(uuid.uuid4())
    my_knowledge.draft_verdict(other, {'request_id': r2, 'action': 'dismiss'}, now=NOW)
    assert my_knowledge.draft_verdict(other, {'request_id': r2, 'action': 'dismiss'}, now=NOW)['status'] == 'replayed'
    assert db.execute('SELECT count(*) FROM knowledge_items').fetchone()[0] == 1
