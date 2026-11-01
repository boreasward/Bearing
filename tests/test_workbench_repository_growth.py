"""Repository helpers for the v2.7 growth-loop tables (SQL; throwaway schema via the workbench_db fixture)."""
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.core.dbutil as core_dbutil
import investment_assistant.core.locks as core_locks
import investment_assistant.core.schema as core_schema
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import investment_assistant.execution.repo_legacy as execution_repo_legacy
import investment_assistant.review.repo as review_repo
import os
import uuid
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest

from investment_assistant.review.knowledge import items as knowledge_items
from investment_assistant.core import knowledge

NOW = datetime(2026, 10, 28, 22, 30, tzinfo=UTC)
DAY = date(2026, 10, 20)


def decision(conn, ticker='NVDA', signal_date=DAY, *, decision='accept', action='buy', supersedes_id=None,
             plan_id=None, decided_at=NOW):
    final = {'accept': action, 'reject': 'ignore', 'downgrade': 'watch'}[decision]
    return execution_repo_legacy._insert_decision_for_tests(conn, {
        'request_id': uuid.uuid4(), 'ticker': ticker, 'signal_date': signal_date, 'candidate_ids': [uuid.uuid4()],
        'brief_id': None, 'proposed_action': action, 'final_action': final, 'decision': decision,
        'reason': 'because', 'supersedes_id': supersedes_id, 'plan_id': plan_id}, 'h', decided_at=decided_at)


def trade(conn, ticker='NVDA', side='buy', decision_id=None, executed_at=NOW, quantity=10):
    return execution_repo.insert_trade(conn, {
        'request_id': uuid.uuid4(), 'ticker': ticker, 'side': side, 'quantity': quantity, 'price': 100, 'fee': 0,
        'executed_at': executed_at, 'decision_id': decision_id, 'note': None}, 'h', recorded_at=NOW)


def card(conn, key='unit:NVDA:2026-10-20:ended', **overrides):
    row = {'event_key': key, 'subject': 'unit', 'ticker': 'NVDA', 'signal_date': DAY, 'buy_trade_id': None,
           'end_kind': 'stop', 'end_session': date(2026, 10, 23), 'retro_version': 'retro-1',
           'facts': {'subject': {'kind': 'unit'}, 'plans': []}, 'detected_run_id': 'run-1'}
    row.update(overrides)
    return review_repo.insert_review_card(conn, row)


def item_with_versions(conn, specs, *, request_prefix=''):
    """specs: [(created_at, tickers, concepts), ...] - successive versions of one new item."""
    item_id = review_repo.insert_knowledge_item(conn, {'origin': 'manual'})
    previous = None
    for number, (created_at, tickers, concepts) in enumerate(specs, start=1):
        previous = review_repo.insert_knowledge_version(conn, {
            'knowledge_id': item_id, 'version': number, 'supersedes_id': previous, 'request_id': uuid.uuid4(),
            'request_hash': 'h', 'draft_id': None, 'title': f't{number}', 'body': 'b', 'scope': 's',
            'counter_examples': '', 'concepts': concepts, 'tickers': tickers, 'schema_version': 'my-knowledge-1'})
        conn.execute('UPDATE knowledge_versions SET created_at = %s WHERE id = %s', (created_at, previous))
    return item_id


def test_growth_ready_false_without_tables(workbench_db):
    conn = workbench_db
    assert core_schema.growth_ready(conn) is True
    assert len(core_dbutil.GROWTH_TABLES) == 11
    conn.execute('DROP TABLE knowledge_citations')
    assert core_schema.growth_ready(conn) is False
    conn.execute('SELECT 1')                                            # the transaction stays usable


def test_insert_card_on_conflict_returns_none(workbench_db):
    conn = workbench_db
    first = card(conn)
    assert isinstance(first, int)
    assert card(conn) is None                                           # same event key: DO NOTHING
    assert card(conn, 'unit:NVDA:2026-10-20:closed:s1', end_kind='position_closed') is not None
    stored = review_repo.review_card(conn, first)
    assert stored['event_key'] == 'unit:NVDA:2026-10-20:ended' and stored['facts']['subject'] == {'kind': 'unit'}
    assert stored['signal_date'] == DAY and stored['end_session'] == date(2026, 10, 23)
    assert review_repo.review_card_by_key(conn, 'unit:NVDA:2026-10-20:ended')['id'] == first
    assert review_repo.review_card_by_key(conn, 'nope') is None
    assert review_repo.review_card(conn, 999999) is None
    # a trade card
    buy = trade(conn)
    trade_card = card(conn, f"trade:{buy['id']}", subject='trade', signal_date=None, buy_trade_id=buy['id'],
                      end_kind='position_closed')
    assert trade_card is not None


def test_review_cards_listing(workbench_db):
    conn = workbench_db
    a = card(conn, 'k1', end_session=date(2026, 10, 22))
    b = card(conn, 'k2', ticker='AMD', end_session=date(2026, 10, 26))
    c = card(conn, 'k3', end_session=date(2026, 10, 27))
    assert [r['id'] for r in review_repo.review_cards(conn)] == [c, b, a]    # newest end first
    assert [r['id'] for r in review_repo.review_cards(conn, ticker='nvda')] == [c, a]
    assert [r['id'] for r in review_repo.review_cards(conn, since=date(2026, 10, 26))] == [c, b]


def test_card_drafts_and_verdict(workbench_db):
    conn = workbench_db
    cid = card(conn)
    run = uuid.uuid4()
    conn.execute(
        "INSERT INTO agent_runs (id, ticker, signal_date, pool, attempt, mode, agent_version, provider, model, "
        "cutoff_at, status, reason, started_at, finished_at) VALUES (%s, 'NVDA', %s, 'main', 1, 'retro', "
        "'agent-retro-1', 'chatgpt', 'gpt-test', %s, 'ok', NULL, %s, %s)", (run, DAY, NOW, NOW, NOW))
    d1 = review_repo.insert_card_draft(conn, {'card_id': cid, 'attempt': 1, 'trigger': 'batch', 'agent_run_id': None,
                                              'status': 'skipped', 'reason': 'stage_budget', 'output': None})
    d2 = review_repo.insert_card_draft(conn, {'card_id': cid, 'attempt': 2, 'trigger': 'manual', 'agent_run_id': run,
                                              'status': 'ok', 'reason': None, 'output': {'headline': '一句话'}})
    drafts = review_repo.card_drafts(conn, cid)
    assert [d['id'] for d in drafts] == [d1, d2] and drafts[1]['output'] == {'headline': '一句话'}
    assert review_repo.card_drafts(conn, 999999) == []
    assert review_repo.card_verdict(conn, cid) is None
    vid = review_repo.insert_card_verdict(conn, {'card_id': cid, 'request_id': uuid.uuid4(), 'request_hash': 'h',
                                                'draft_id': d2, 'note': '我的复盘'})
    verdict = review_repo.card_verdict(conn, cid)
    assert verdict['id'] == vid and verdict['note'] == '我的复盘' and verdict['draft_id'] == d2
    with pytest.raises(psycopg.errors.UniqueViolation):
        with conn.transaction():
            review_repo.insert_card_verdict(conn, {'card_id': cid, 'request_id': uuid.uuid4(), 'request_hash': 'h',
                                                  'draft_id': None, 'note': ''})


def test_knowledge_rows_cutoff_and_filters(workbench_db):
    conn = workbench_db
    t0 = datetime(2026, 10, 20, 12, tzinfo=UTC)
    cutoff = t0 + timedelta(days=2)
    # item A: v1 (NVDA, plan.stop) before the cutoff; v2 moves to AMD after it
    a = item_with_versions(conn, [(t0, ['NVDA'], ['concept:plan.stop@1']),
                                  (cutoff + timedelta(hours=1), ['AMD'], ['concept:plan.stop@1'])])
    # item B: v1 (AMD) and v2 (NVDA) both before the cutoff
    b = item_with_versions(conn, [(t0, ['AMD'], []), (t0 + timedelta(hours=1), ['NVDA'], ['concept:plan_stop@1'])])
    # item C: written only after the cutoff
    c = item_with_versions(conn, [(cutoff + timedelta(hours=2), ['NVDA'], [])])
    review_repo.insert_knowledge_status(conn, {'knowledge_id': b, 'status': 'archived', 'reason': 'old',
                                              'request_id': uuid.uuid4(), 'request_hash': 'h'})
    conn.execute("UPDATE knowledge_status SET created_at = %s WHERE knowledge_id = %s", (t0 + timedelta(hours=3), b))
    review_repo.insert_knowledge_status(conn, {'knowledge_id': b, 'status': 'active', 'reason': '',
                                              'request_id': uuid.uuid4(), 'request_hash': 'h'})
    conn.execute("UPDATE knowledge_status SET created_at = %s WHERE knowledge_id = %s AND status = 'active'",
                 (cutoff + timedelta(hours=5), b))

    def ids(rows):
        return sorted({(r['knowledge_id'], r['version']) for r in rows})

    versions, statuses = data_repo.knowledge_rows(conn, cutoff=cutoff)
    assert ids(versions) == [(a, 1), (b, 1), (b, 2)]                    # C and A.v2 were written after the cutoff
    assert [(s['knowledge_id'], s['status']) for s in statuses] == [(b, 'archived')]   # the restore is later too
    visible = knowledge.visible(versions, statuses, cutoff=cutoff)
    assert [(r['knowledge_id'], r['version']) for r in visible] == [(a, 1)]            # B is archived at the cutoff

    now_versions, now_statuses = data_repo.knowledge_rows(conn, cutoff=None)
    assert ids(now_versions) == [(a, 1), (a, 2), (b, 1), (b, 2), (c, 1)]
    assert [(r['knowledge_id'], r['version']) for r in
            knowledge.visible(now_versions, now_statuses, cutoff=None)] == [(a, 2), (b, 2), (c, 1)]

    # filters look at the item's highest version visible at the cutoff, and still return its whole history
    by_nvda, _ = data_repo.knowledge_rows(conn, cutoff=cutoff, ticker='NVDA')
    assert ids(by_nvda) == [(a, 1), (b, 1), (b, 2)]                     # A.v1 (NVDA) and B.v2 (NVDA)
    by_amd, _ = data_repo.knowledge_rows(conn, cutoff=cutoff, ticker='AMD')
    assert ids(by_amd) == []                                            # AMD only in A.v2 (later) and B.v1 (superseded)
    by_amd_now, _ = data_repo.knowledge_rows(conn, cutoff=None, ticker='AMD')
    assert ids(by_amd_now) == [(a, 1), (a, 2)]
    by_concept, _ = data_repo.knowledge_rows(conn, cutoff=cutoff, concept='plan.stop')
    assert ids(by_concept) == [(a, 1)]                                  # matched by id, not by LIKE wildcard (plan_stop)
    by_prefix, _ = data_repo.knowledge_rows(conn, cutoff=cutoff, concept='plan')
    assert ids(by_prefix) == []                                         # the id must match whole
    by_id, by_id_statuses = data_repo.knowledge_rows(conn, cutoff=cutoff, knowledge_id=b)
    assert ids(by_id) == [(b, 1), (b, 2)] and [s['status'] for s in by_id_statuses] == ['archived']
    both, _ = data_repo.knowledge_rows(conn, cutoff=cutoff, ticker='NVDA', concept='plan.stop')
    assert ids(both) == [(a, 1)]
    assert review_repo.knowledge_item(conn, a)['origin'] == 'manual' and review_repo.knowledge_item(conn, 99999) is None


def test_try_advisory_lock_mutual_exclusion(workbench_db):
    key = f'retro-test:{uuid.uuid4()}'
    url = os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']                 # the fixture skipped without it
    with psycopg.connect(url) as first, psycopg.connect(url) as second:
        assert core_locks.try_advisory_lock(first, key) is True
        assert core_locks.try_advisory_lock(second, key) is False              # held by the open transaction
        assert core_locks.try_advisory_lock(second, key + ':other') is True    # another key is free
        second.rollback()
        first.commit()                                                         # transaction scope: released here
        assert core_locks.try_advisory_lock(second, key) is True
        second.rollback()


def test_retro_subjects_scope(workbench_db):
    conn = workbench_db
    t = datetime(2026, 10, 21, 15, tzinfo=UTC)
    # A: decided and bought; B: decided (reject), no trade; C: a sell unit; D: a decision outside the window
    a_dec = decision(conn, 'NVDA', DAY, decided_at=t)
    a_buy = trade(conn, 'NVDA', decision_id=a_dec['id'], executed_at=t)
    a_void = trade(conn, 'NVDA', decision_id=a_dec['id'], executed_at=t + timedelta(hours=1))
    execution_repo.mark_trade_voided(conn, a_void['id'], 'typo', voided_at=NOW)
    a_sell = trade(conn, 'NVDA', side='sell', decision_id=a_dec['id'], executed_at=t + timedelta(days=2))
    b_dec = decision(conn, 'AMD', DAY, decision='reject', decided_at=t)
    decision(conn, 'MSFT', DAY, action='sell', decided_at=t)
    # an old unit that never filled and whose end was already carded: nothing new can end it
    old_day = date(2026, 8, 3)
    old_t = datetime(2026, 8, 4, 15, tzinfo=UTC)
    decision(conn, 'AAPL', old_day, decided_at=old_t)
    card(conn, key='unit:AAPL:2026-08-03:ended', ticker='AAPL', signal_date=old_day, end_session=date(2026, 8, 10))
    # an old decided-but-unfilled unit with no card yet could still end inside the window: kept ("when in doubt")
    decision(conn, 'AMZN', old_day, decided_at=old_t)
    # signal long before `since`, the position closes inside the window: returned
    f_dec = decision(conn, 'F', old_day, decided_at=old_t)
    f_buy = trade(conn, 'F', decision_id=f_dec['id'], executed_at=old_t)
    trade(conn, 'F', side='sell', decision_id=f_dec['id'], executed_at=datetime(2026, 10, 27, 15, tzinfo=UTC))
    # signal long before `since`, the position closed before it: not returned
    g_dec = decision(conn, 'GE', old_day, decided_at=old_t)
    trade(conn, 'GE', decision_id=g_dec['id'], executed_at=old_t)
    trade(conn, 'GE', side='sell', decision_id=g_dec['id'], executed_at=datetime(2026, 9, 15, 15, tzinfo=UTC))
    # signal long before `since`, the position is still open: returned
    h_dec = decision(conn, 'HD', old_day, decided_at=old_t)
    h_buy = trade(conn, 'HD', decision_id=h_dec['id'], executed_at=old_t)
    # a corrected decision on the same unit: only the head is reported, with the whole chain's buys
    e_first = decision(conn, 'TSLA', DAY, decided_at=t)
    e_head = decision(conn, 'TSLA', DAY, supersedes_id=e_first['id'], decided_at=t + timedelta(hours=1))
    e_buy = trade(conn, 'TSLA', decision_id=e_first['id'], executed_at=t)
    # unplanned buys: one live, one voided, one sell, one outside the window
    free_buy = trade(conn, 'IBM', executed_at=t)
    voided_free = trade(conn, 'KO', executed_at=t)
    execution_repo.mark_trade_voided(conn, voided_free['id'], 'typo', voided_at=NOW)
    trade(conn, 'PEP', side='sell', executed_at=t)
    # unplanned buys bought long before `since`: closed inside the window -> returned; closed before it -> out
    low_buy = trade(conn, 'LOW', executed_at=old_t)
    trade(conn, 'LOW', side='sell', executed_at=datetime(2026, 10, 28, 15, tzinfo=UTC))
    trade(conn, 'XOM', executed_at=old_t)
    trade(conn, 'XOM', side='sell', executed_at=datetime(2026, 9, 10, 15, tzinfo=UTC))
    trade(conn, 'PG', executed_at=datetime(2026, 10, 29, 15, tzinfo=UTC))      # bought after `until`: not yet
    # a candidate-only unit (never decided, no trade) is simply not there
    conn.execute("INSERT INTO signal_candidates (ticker, signal_date, action, rule_id, rule_version, inputs) "
                 "VALUES ('ORCL', %s, 'buy', 'trend_reclaim', 'rules-1', '{}'::jsonb)", (DAY,))

    subjects = review_repo.retro_subjects(conn, since=date(2026, 10, 1), until=date(2026, 10, 28))
    units = {(s['ticker'], s['signal_date']): s for s in subjects if s['kind'] == 'unit'}
    trades = {s['buy_trade_id']: s for s in subjects if s['kind'] == 'trade'}
    # no sell unit, no ORCL, no carded old unit (AAPL), no unit closed before `since` (GE)
    assert set(units) == {('NVDA', DAY), ('AMD', DAY), ('TSLA', DAY), ('F', old_day), ('HD', old_day),
                          ('AMZN', old_day)}
    assert set(trades) == {free_buy['id'], low_buy['id']}                   # voided / sell / closed-before / future out
    assert [b['id'] for b in units[('F', old_day)]['buys']] == [f_buy['id']]       # started before, ended inside
    assert [b['id'] for b in units[('HD', old_day)]['buys']] == [h_buy['id']]
    assert units[('AMZN', old_day)]['buys'] == []
    nvda = units[('NVDA', DAY)]
    assert nvda['decision']['id'] == a_dec['id']
    assert [b['id'] for b in nvda['buys']] == [a_buy['id']]                 # the voided buy does not count
    assert nvda['decision_ids'] == [a_dec['id']]
    assert nvda['plans'] == [] and nvda['reviews'] == []
    amd = units[('AMD', DAY)]
    assert amd['decision']['id'] == b_dec['id'] and amd['buys'] == []       # decided, never bought: still a subject
    tsla = units[('TSLA', DAY)]
    assert tsla['decision']['id'] == e_head['id']
    assert [b['id'] for b in tsla['buys']] == [e_buy['id']]                 # a buy on a superseded decision stays in the unit
    assert set(tsla['decision_ids']) == {e_first['id'], e_head['id']}
    assert [(d['id'], d['plan_id']) for d in tsla['decisions']] == [(e_first['id'], None), (e_head['id'], None)]   # oldest first
    free = trades[free_buy['id']]
    assert (free['ticker'], free['signal_date'], free['decision'], free['plans']) == ('IBM', None, None, [])
    assert [b['id'] for b in free['buys']] == [free_buy['id']] and free['decisions'] == []
    assert a_sell['id'] not in {b['id'] for s in subjects for b in s['buys']}
    # ordering: units by (signal_date, ticker), then trades by execution time
    assert [s['kind'] for s in subjects] == ['unit'] * 6 + ['trade'] * 2
    assert [(s['ticker'], s['signal_date']) for s in subjects if s['kind'] == 'unit'] == [
        ('AMZN', old_day), ('F', old_day), ('HD', old_day), ('AMD', DAY), ('NVDA', DAY), ('TSLA', DAY)]
    assert [s['ticker'] for s in subjects if s['kind'] == 'trade'] == ['LOW', 'IBM']     # by execution time
    # since/until are the END-session window: NVDA sold on 10-23, so it is out once `since` is after that
    later = review_repo.retro_subjects(conn, since=date(2026, 10, 24), until=date(2026, 10, 28))
    assert {s['ticker'] for s in later if s['kind'] == 'unit'} == {'AMD', 'TSLA', 'F', 'HD', 'AMZN'}
    assert {s['ticker'] for s in later if s['kind'] == 'trade'} == {'IBM', 'LOW'}        # IBM still open, LOW closes 10-28
    # a window before everything closed: `until` caps the signal date and the buy date
    early = review_repo.retro_subjects(conn, since=date(2026, 10, 1), until=date(2026, 10, 19))
    assert {s['ticker'] for s in early if s['kind'] == 'unit'} == {'F', 'HD', 'AMZN'}    # signals of 10-20 are later
    assert {s['ticker'] for s in early if s['kind'] == 'trade'} == {'LOW'}               # IBM was bought on 10-21


def _split_bar(conn, ticker, day, factor):
    data_repo.store_bars(conn, ticker, [{'price_date': day, 'open': 10, 'high': 11, 'low': 9, 'close': 10,
                                          'adj_close': 10, 'volume': 1000, 'split_factor': factor}], now=NOW)


def test_open_before_applies_splits_the_way_positions_do(workbench_db):
    """``_open_before`` runs a share count over the trades before ``since`` with the splits applied: a sale in
    post-split shares must not look like a short, and a reverse split must not leave a ghost position."""
    conn = workbench_db

    def at(day):
        return datetime(2026, 10, day, 15, tzinfo=UTC)

    # forward 2:1 on 10-05, bought 10 before it, sold 15 (post-split shares) after it: 5 shares remain
    trade(conn, 'SPA', executed_at=at(1))
    _split_bar(conn, 'SPA', date(2026, 10, 5), 2)
    trade(conn, 'SPA', side='sell', executed_at=at(6), quantity=15)
    # forward 2:1, everything sold after it (20 post-split shares): flat
    trade(conn, 'SPB', executed_at=at(1))
    _split_bar(conn, 'SPB', date(2026, 10, 5), 2)
    trade(conn, 'SPB', side='sell', executed_at=at(6), quantity=20)
    # reverse 1:10 on 10-05 (factor 0.1): 100 shares become 10, the sale of 10 leaves nothing
    trade(conn, 'SPC', executed_at=at(1), quantity=100)
    _split_bar(conn, 'SPC', date(2026, 10, 5), '0.1')
    trade(conn, 'SPC', side='sell', executed_at=at(6), quantity=10)
    # the split comes after `since`: it does not touch the count before it
    trade(conn, 'SPD', executed_at=at(1))
    _split_bar(conn, 'SPD', date(2026, 10, 12), 2)

    open_before = review_repo._open_before
    assert open_before(conn, 'SPA', date(2026, 10, 8)) is True          # 20 - 15 = 5 left (a naive 10 - 15 would say flat)
    assert open_before(conn, 'SPA', date(2026, 10, 3)) is True          # before the split, only the buy counts
    assert open_before(conn, 'SPB', date(2026, 10, 8)) is False         # 20 - 20 = 0
    assert open_before(conn, 'SPB', date(2026, 10, 6)) is True          # the sale is on `since`, not before it
    assert open_before(conn, 'SPC', date(2026, 10, 8)) is False         # 100 x 0.1 - 10 = 0 (naive: 90 left)
    assert open_before(conn, 'SPD', date(2026, 10, 8)) is True


def test_knowledge_drafts_citations_discussions(workbench_db):
    conn = workbench_db
    cid = card(conn)
    run = uuid.uuid4()
    conn.execute(
        "INSERT INTO agent_runs (id, ticker, signal_date, pool, attempt, mode, agent_version, provider, model, "
        "cutoff_at, status, reason, started_at, finished_at) VALUES (%s, 'NVDA', %s, 'main', 1, 'retro', "
        "'agent-retro-1', 'chatgpt', 'gpt-test', %s, 'ok', NULL, %s, %s)", (run, DAY, NOW, NOW, NOW))
    draft = review_repo.insert_card_draft(conn, {'card_id': cid, 'attempt': 1, 'trigger': 'batch',
                                                'agent_run_id': run, 'status': 'ok', 'reason': None,
                                                'output': {'headline': 'x'}})
    lesson = {'origin': 'review_card', 'card_draft_id': draft, 'lesson_index': 0, 'discussion_turn_id': None,
              'target_knowledge_id': None, 'target_version': None, 'title': 't', 'body': 'b', 'scope': 's',
              'counter_examples': 'c', 'concepts': [], 'tickers': ['NVDA'], 'refs': ['fact:plans']}
    d0 = review_repo.insert_knowledge_draft(conn, lesson)
    d1 = review_repo.insert_knowledge_draft(conn, {**lesson, 'lesson_index': 1, 'title': 't2'})
    assert [d['id'] for d in review_repo.knowledge_drafts(conn, open_only=True)] == [d0, d1]
    assert [d['id'] for d in review_repo.knowledge_drafts(conn, open_only=True, card_draft_id=draft)] == [d0, d1]
    assert [d['id'] for d in review_repo.knowledge_drafts(conn, open_only=False, ids=[d1])] == [d1]
    assert review_repo.knowledge_drafts(conn, open_only=True, card_draft_id=999999) == []
    first = review_repo.knowledge_drafts(conn, open_only=False, ids=[d0])[0]
    assert first['refs'] == ['fact:plans'] and first['tickers'] == ['NVDA'] and first['counter_examples'] == 'c'

    item = review_repo.insert_knowledge_item(conn, {'origin': 'review_card', 'source_card_id': cid})
    version = review_repo.insert_knowledge_version(conn, {
        'knowledge_id': item, 'version': 1, 'supersedes_id': None, 'request_id': uuid.uuid4(), 'request_hash': 'h',
        'draft_id': d0, 'title': 't', 'body': 'b', 'scope': 's', 'counter_examples': 'c', 'concepts': [],
        'tickers': ['NVDA'], 'schema_version': 'my-knowledge-1'})
    review_repo.insert_draft_verdict(conn, {'draft_id': d0, 'action': 'approve', 'knowledge_version_id': version,
                                           'request_id': uuid.uuid4()})
    review_repo.insert_draft_verdict(conn, {'draft_id': d1, 'action': 'dismiss', 'knowledge_version_id': None,
                                           'request_id': uuid.uuid4()})
    assert review_repo.knowledge_drafts(conn, open_only=True) == []          # both decided
    assert len(review_repo.knowledge_drafts(conn, open_only=False)) == 2

    plan = execution_repo.insert_plan(conn, {
        'ticker': 'NVDA', 'signal_date': DAY, 'kind': 'agent', 'plan_version': 'agent-2', 'ref_price': 100, 'atr': 2,
        'entry_low': 99, 'entry_high': 101, 'stop': 96, 'target1': 108, 'trail_atr_multiple': 2,
        'valid_from': date(2026, 10, 21), 'valid_until': date(2026, 10, 28), 'sizing': {}, 'status': 'ok',
        'invalid_reasons': [], 'warnings': [], 'inputs': {}})
    rows = [{'agent_run_id': run, 'plan_id': plan['id'], 'review_id': None, 'knowledge_id': item, 'version': 1,
             'field': 'thesis', 'call_seq': 1},
            {'agent_run_id': run, 'plan_id': plan['id'], 'review_id': None, 'knowledge_id': item, 'version': 1,
             'field': 'counter', 'call_seq': 1}]
    assert analysis_repo.insert_knowledge_citations(conn, rows) == 2
    assert analysis_repo.insert_knowledge_citations(conn, []) == 0
    assert [c['field'] for c in review_repo.citations_of(conn, item)] == ['thesis', 'counter']
    assert len(review_repo.citations_for_plans(conn, [plan['id']])) == 2
    assert review_repo.citations_for_plans(conn, []) == []
    assert review_repo.citations_of(conn, item + 1) == []

    discussion = review_repo.insert_discussion(conn, {
        'request_id': uuid.uuid4(), 'request_hash': 'h', 'context_kind': 'plan', 'ticker': 'NVDA',
        'signal_date': DAY, 'plan_id': plan['id'], 'card_id': None})
    assert review_repo.discussion(conn, discussion['id'])['plan_id'] == plan['id']
    assert [d['id'] for d in review_repo.discussions_for(conn, plan_id=plan['id'])] == [discussion['id']]
    assert review_repo.discussions_for(conn, card_id=cid) == []
    question = review_repo.insert_turn(conn, {
        'discussion_id': discussion['id'], 'seq': 1, 'role': 'user', 'request_id': uuid.uuid4(), 'request_hash': 'h',
        'body': '为什么是这个止损？', 'attachment': None})
    answer = review_repo.insert_turn(conn, {
        'discussion_id': discussion['id'], 'seq': 2, 'role': 'assistant', 'body': '因为…', 'reply_to': question['id'],
        'status': 'ok', 'reason': None, 'refs': ['call:1'], 'detail': {'draft_dropped': False},
        'agent_run_id': run, 'cutoff_at': NOW, 'run_id': 'r-1'})
    turns = review_repo.turns(conn, discussion['id'])
    assert [t['seq'] for t in turns] == [1, 2] and turns[1]['refs'] == ['call:1'] and turns[1]['id'] == answer['id']
    assert turns[0]['attachment'] is None


def test_try_advisory_lock(workbench_db):
    conn = workbench_db
    assert core_locks.try_advisory_lock(conn, 'retro:2026-10-28') is True
    assert core_locks.try_advisory_lock(conn, 'retro:2026-10-28') is True      # re-entrant within the transaction
