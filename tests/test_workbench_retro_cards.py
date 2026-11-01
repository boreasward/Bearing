"""Review cards (v2.7 spec §4, §9, §12) against a disposable schema: detection, frozen facts, the retro stage's
accounting and gates, drafting, the verdict, the views and the command line.

The ledger rows are written straight through the repository (a plan, a decision, trades) and the daily bars are
fabricated test prices; a scripted model answers the drafts. Helpers here are shared with the other growth tests."""
import json
import uuid
from contextlib import nullcontext
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from investment_assistant.core import tx as core_tx
from langchain_core.messages import AIMessage

from test_workbench_service import (NOW, SESSION, Sources, _agent_settings, agent_deps, db, deps,  # noqa: F401
                                    seed_watchlist)
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.workbench import discipline as discipline_module
from investment_assistant.workbench import ledger, repository, retro, retro_cards, service
from investment_assistant.workbench import stages as stages_module

NY = ZoneInfo('America/New_York')
FETCHED = NOW - timedelta(days=2)


def D(k: int) -> date:
    """Session k days from the batch session (negative = earlier): D(0) = 2026-10-22."""
    return add_trading_days(SESSION, k)


def at(day: date, hour: int = 15) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=NY).astimezone(UTC)


def evening(day: date) -> datetime:
    return at(day, 18)


def put_bars(db, ticker: str, path: dict[date, tuple], *, fetched=FETCHED) -> None:
    rows = [{'price_date': d, 'open': o, 'high': h, 'low': l, 'close': c, 'adj_close': c, 'volume': 1000,
             'split_factor': 1} for d, (o, h, l, c) in sorted(path.items())]
    repository.store_bars(db, ticker, rows, now=fetched)


def flat(first: date, last: date, price=100.0, high=101.0, low=99.0) -> dict[date, tuple]:
    out, day = {}, first
    while day <= last:
        out[day] = (price, high, low, price)
        day = add_trading_days(day, 1)
    return out


def make_plan(db, ticker: str, signal_date: date, *, status='ok', reasons=(), valid_from=None, valid_until=None):
    return repository.insert_plan(db, {
        'ticker': ticker, 'signal_date': signal_date, 'kind': 'rule', 'based_on_id': None, 'plan_version': 'plan-1',
        'ref_price': 100, 'atr': 2, 'entry_low': 98, 'entry_high': 102, 'stop': 94, 'target1': 112,
        'trail_atr_multiple': 3, 'valid_from': valid_from or add_trading_days(signal_date, 1),
        'valid_until': valid_until or add_trading_days(signal_date, 3), 'sizing': {'status': 'no_equity'},
        'status': status, 'invalid_reasons': list(reasons), 'warnings': [], 'inputs': {'bar_ids': [1]},
        'request_id': None, 'request_hash': None})


def make_decision(db, ticker, signal_date, plan_id, *, decision='accept', decided_at=None, action='buy',
                  supersedes_id=None):
    final = {'accept': action, 'reject': 'ignore', 'downgrade': 'watch'}[decision]
    return repository._insert_decision_for_tests(db, {
        'request_id': uuid.uuid4(), 'ticker': ticker, 'signal_date': signal_date, 'candidate_ids': [uuid.uuid4()],
        'brief_id': None, 'proposed_action': action, 'final_action': final, 'decision': decision,
        'reason': '合成样例决定', 'supersedes_id': supersedes_id, 'plan_id': plan_id},
        'h', decided_at=decided_at or evening(signal_date))


def make_trade(db, ticker, side, price, when, decision_id=None, quantity=10, plan_id=None):
    return repository.insert_trade(db, {
        'request_id': uuid.uuid4(), 'ticker': ticker, 'side': side, 'quantity': quantity, 'price': price, 'fee': 0,
        'executed_at': when, 'decision_id': decision_id, 'plan_id': plan_id, 'note': None}, 'h', recorded_at=NOW)


def make_note(db, plan_id, ticker, body='先观察，不急着做'):
    """A note of yours on a rule baseline (v2.9 D29): the only thing that makes an unfilled unit a card subject."""
    out = ledger.record_note(db, {'request_id': str(uuid.uuid4()), 'ticker': ticker, 'plan_id': str(plan_id),
                                  'body': body}, now=NOW)
    db.execute('UPDATE plan_notes SET created_at = %s WHERE id = %s', (NOW - timedelta(days=1), out['record']['id']))
    return out['record']


def scenario(db, ticker: str, kind: str = 'closed', *, shift: int = 0) -> dict:
    """One unit (signal D(-3+shift)); ``kind`` picks how it ends at (or before) the batch session. The unfilled
    kinds carry a note of yours on the baseline, the retro-2 condition for a card (D29).

    closed:    accepted, bought 103 (above the entry zone: chase) on D(-2), sold 106 on D(0)
    stop:      rejected, never bought; the shadow fills on D(-2) and stops out on D(-1)
    expired:   rejected, never bought; the price stays above the entry zone through the plan's last day
    invalid:   rejected on an invalid plan (no_room), decided D(-2)
    unplanned: a buy without a decision on D(-2), sold 104 on D(0)
    """
    signal = D(-3 + shift)
    start = D(-8 + shift)
    last = D(0 + shift)
    path = flat(start, last)
    out = {'ticker': ticker, 'signal_date': signal, 'kind': kind, 'shift': shift}
    if kind == 'unplanned':
        out['signal_date'] = None
        buy = make_trade(db, ticker, 'buy', 100, at(D(-2 + shift)))
        sell = make_trade(db, ticker, 'sell', 104, at(D(0 + shift)))
        path[D(0 + shift)] = (100.0, 105.0, 99.0, 104.0)
        out.update(buy=buy, sell=sell)
        put_bars(db, ticker, path)
        return out
    plan = make_plan(db, ticker, signal, status='invalid' if kind == 'invalid' else 'ok',
                     reasons=['no_room'] if kind == 'invalid' else [])
    out['plan'] = plan
    if kind == 'closed':
        decision = make_decision(db, ticker, signal, plan['id'])
        path[D(-2 + shift)] = (101.0, 104.0, 99.0, 103.0)
        path[D(0 + shift)] = (103.0, 107.0, 102.0, 106.0)
        out['buy'] = make_trade(db, ticker, 'buy', 103, at(D(-2 + shift)), decision['id'])
        out['sell'] = make_trade(db, ticker, 'sell', 106, at(D(0 + shift)), decision['id'])
    elif kind == 'stop':
        decision = make_decision(db, ticker, signal, plan['id'], decision='reject')
        path[D(-2 + shift)] = (100.0, 101.0, 99.0, 100.0)
        path[D(-1 + shift)] = (99.0, 100.0, 92.0, 93.0)
    elif kind == 'expired':
        decision = make_decision(db, ticker, signal, plan['id'], decision='reject')
        path.update({d: (120.0, 121.0, 119.0, 120.0) for d in path})
    elif kind == 'invalid':
        decision = make_decision(db, ticker, signal, plan['id'], decision='reject', decided_at=evening(D(-2 + shift)))
    else:
        raise ValueError(kind)
    out['decision'] = decision
    if kind in ('stop', 'expired', 'invalid'):
        out['note'] = make_note(db, plan['id'], ticker)
    put_bars(db, ticker, path)
    return out


def draft_reply(*, lessons: int = 1, refs=('fact:plans', 'fact:bars_after'), review_ref='fact:execution') -> AIMessage:
    lesson = {'title': '追高的代价', 'body': '买价高于入场区上沿时，止损距离被放大，同样的仓位承担了更大的风险。',
              'scope': '趋势回踩类入场，买价高于计划入场区上沿时', 'counter_examples': '放量突破当天的追入不适用',
              'concepts': ['review.discipline_chase'], 'tickers': [], 'refs': ['fact:discipline']}
    return AIMessage(content=json.dumps({
        'headline': '一句话：这笔平仓合计盈利，但买得偏高',
        'expected_vs_actual': [{'text': '计划价位与之后的日线对照：价格没有触及止损。', 'refs': list(refs)}],
        'decision_review': {'text': '你采纳了规则计划。', 'refs': [review_ref]},
        'lessons': [{**lesson, 'title': f"{lesson['title']}{n}"} for n in range(lessons)]}, ensure_ascii=False))


def connection_is(monkeypatch, status='connected'):
    """The agent connection as ``settings_from_env`` reads it (v2.8: the ChatGPT connection, not an API key)."""
    from investment_assistant.workbench import agent_connection

    info = {'status': status, 'scopes_ok': status == 'connected', 'model': 'gpt-test' if status == 'connected' else None,
            'secret': 'test-access-token' if status == 'connected' else None}
    monkeypatch.setattr(agent_connection, 'read_for_settings', lambda: info)


def stage_deps(make, replies, **settings):
    return agent_deps(make, replies, settings=_agent_settings(**settings))


def run_stage(db, d, *, session=SESSION, now=NOW, summary=None, started=0.0, clock=lambda: 0.0, acc=None,
              run_id='run-test'):
    summary = summary if summary is not None else {'errors': [], 'agent': {'quota_exhausted': False}}
    acc = acc if acc is not None else stages_module.StageAcc(clock=lambda: 0.0)
    out = retro_cards.run_stage(session, now=now, deps=d, summary=summary, started=started, clock=clock,
                                run_id=run_id, acc=acc)
    db.commit()
    return out, summary, acc


def cards(db):
    return repository.review_cards(db)


def only_card(db, ticker=None):
    found = [c for c in cards(db) if ticker is None or c['ticker'] == ticker]
    assert len(found) == 1
    return found[0]


# ---------------------------------------------------------------- detection and frozen facts

def test_close_creates_card_and_drafts(db, scripted_model):
    s = scenario(db, 'ZZZA')
    d = stage_deps(scripted_model, [draft_reply(lessons=2)])
    out, summary, acc = run_stage(db, d)
    assert out is None
    card = only_card(db)
    assert card['event_key'] == f"unit:ZZZA:{s['signal_date']}:closed:{s['sell']['id']}"
    assert (card['subject'], card['end_kind'], card['end_session']) == ('unit', 'position_closed', D(0))
    assert card['retro_version'] == 'retro-2' and card['detected_run_id'] == 'run-test'
    facts = card['facts']
    assert tuple(facts) != () and set(facts) == set(retro.FACT_KEYS_V2)
    assert facts['subject']['end_text'] == '平仓（自行卖出）' and facts['subject']['exit_basis'] == 'other'
    assert facts['execution']['text'] == '采纳（照规则基线）' and facts['execution']['status'] == 'adopted'
    assert facts['execution']['legacy_decision']['decision'] == 'accept'      # the old decision is kept as it was
    assert facts['execution']['legacy_decision']['text'] == '采纳（照规则计划）'    # I2: with the retro-1 wording
    assert [p['roles'] for p in facts['plans']] == [['reference', 'executed']]
    assert facts['trades']['actual_r'] is not None and len(facts['trades']['segments']) == 1
    assert facts['discipline']['totals'] == {'chase': 1, 'early_take': 0, 'held_loser': 0, 'unplanned': 0,
                                             'pending': 1}
    assert facts['discipline']['trades'][0]['immature'] == ['early_take']
    drafts = repository.card_drafts(db, card['id'])
    assert [(x['status'], x['trigger'], x['attempt']) for x in drafts] == [('ok', 'batch', 1)]
    lessons = repository.knowledge_drafts(db, open_only=True)
    assert [(x['origin'], x['lesson_index']) for x in lessons] == [('review_card', 0), ('review_card', 1)]
    run = repository.agent_run_by_id(db, drafts[0]['agent_run_id'])
    assert (run['mode'], run['agent_version'], run['status'], run['plan_id']) == ('retro', 'agent-retro-2', 'ok', None)
    assert run['signal_date'] == s['signal_date'] and run['pool'] == 'main'
    assert summary['retro'] == {'new_cards': 1, 'drafts': 1, 'to_review': 1}


def test_retro_counts_match_rows(db, scripted_model):
    scenario(db, 'ZZZA', 'closed')
    scenario(db, 'ZZZB', 'stop')
    scenario(db, 'ZZZC', 'expired')
    scenario(db, 'ZZZD', 'invalid')
    d = stage_deps(scripted_model, [draft_reply(), draft_reply(), draft_reply(), draft_reply(lessons=0)])
    out, summary, acc = run_stage(db, d)
    rows = cards(db)
    assert len(rows) == 4
    by_kind = {c['ticker']: c['end_kind'] for c in rows}
    assert by_kind == {'ZZZA': 'position_closed', 'ZZZB': 'stop', 'ZZZC': 'expired', 'ZZZD': 'invalidated'}
    drafts = [x for c in rows for x in repository.card_drafts(db, c['id'])]
    assert acc.input_count == 4 and acc.output_count == len(drafts) == 4
    assert all(x['status'] == 'ok' for x in drafts)
    assert sorted(acc.outputs['card_ids']) == sorted(c['id'] for c in rows)
    assert sorted(acc.outputs['draft_ids']) == sorted(x['id'] for x in drafts)
    assert sorted(acc.outputs['agent_run_ids']) == sorted(str(x['agent_run_id']) for x in drafts)
    assert acc.detail == {
        'events': 4, 'new_cards': 4, 'by_end_kind': {'position_closed': 1, 'stop': 1, 'target': 0, 'expired': 1,
                                                       'invalidated': 1},
        'no_plan': 0, 'drafts': {'ok': 4, 'rejected': 0, 'error': 0, 'skipped': 0}, 'to_review': 4,
        'tokens': {'input': 40, 'output': 20}}
    assert summary['retro'] == {'new_cards': 4, 'drafts': 4, 'to_review': 4}
    assert (acc.failures, acc.skips, acc.degradations) == ([], [], [])


def test_retro_stage_idempotent_same_session(db, scripted_model):
    scenario(db, 'ZZZA')
    scenario(db, 'ZZZB', 'stop')
    first_deps = stage_deps(scripted_model, [draft_reply(), draft_reply()])
    run_stage(db, first_deps)
    before = (len(cards(db)), db.execute('SELECT count(*) FROM review_card_drafts').fetchone()[0],
              db.execute('SELECT count(*) FROM knowledge_drafts').fetchone()[0],
              db.execute('SELECT count(*) FROM agent_runs').fetchone()[0])
    again = stage_deps(scripted_model, [])                              # an empty queue: any model call would raise
    out, summary, acc = run_stage(db, again, run_id='run-again')
    after = (len(cards(db)), db.execute('SELECT count(*) FROM review_card_drafts').fetchone()[0],
             db.execute('SELECT count(*) FROM knowledge_drafts').fetchone()[0],
             db.execute('SELECT count(*) FROM agent_runs').fetchone()[0])
    assert before == after == (2, 2, 2, 2)
    assert acc.detail['events'] == 0 and acc.detail['new_cards'] == 0 and acc.input_count == 0
    assert acc.detail['to_review'] == 2 and summary['retro'] == {'new_cards': 0, 'drafts': 0, 'to_review': 2}


def test_card_is_not_rebuilt_after_the_event_leaves_the_window(db, scripted_model):
    scenario(db, 'ZZZA')
    run_stage(db, stage_deps(scripted_model, [draft_reply()]))
    later = D(6)
    out, summary, acc = run_stage(db, stage_deps(scripted_model, []), session=later, now=at(later, 18))
    assert len(cards(db)) == 1 and acc.detail['events'] == 0


def test_unplanned_trade_card(db, scripted_model):
    s = scenario(db, 'ZZZU', 'unplanned')
    run_stage(db, stage_deps(scripted_model, [draft_reply()]))
    card = only_card(db)
    assert card['subject'] == 'trade' and card['signal_date'] is None and card['buy_trade_id'] == s['buy']['id']
    assert card['event_key'] == f"trade:{s['buy']['id']}"
    facts = card['facts']
    assert facts['execution']['text'] == '计划外成交' and facts['execution']['status'] == 'unplanned'
    assert facts['plans'] == [] and facts['holding'] is None
    assert facts['tracks'] == {'actual': {'status': 'no_plan', 'status_text': '无计划成交', 'r': None,
                                          'plan_id': None, 'plan_kind': None}}
    assert facts['discipline']['totals']['unplanned'] == 1 and facts['subject']['buy_trade_id'] == str(s['buy']['id'])
    run = repository.agent_run_by_id(db, repository.card_drafts(db, card['id'])[0]['agent_run_id'])
    assert run['signal_date'] == D(-2)                                  # the buy's New York day


def test_held_unit_whose_latest_decision_is_a_reject_keeps_the_accepted_plan(db, scripted_model):
    """Accept + buy, then a reject (no plan_id) on the same unit, then a full close: the card exists and its reference
    is the plan the buy's decision executed (spec D3)."""
    signal = D(-3)
    plan = make_plan(db, 'ZZZR', signal)
    accept = make_decision(db, 'ZZZR', signal, plan['id'])
    make_decision(db, 'ZZZR', signal, None, decision='reject', decided_at=evening(D(-1)), supersedes_id=accept['id'])
    make_trade(db, 'ZZZR', 'buy', 103, at(D(-2)), accept['id'])
    make_trade(db, 'ZZZR', 'sell', 106, at(D(0)), accept['id'])
    path = flat(D(-8), D(0))
    path[D(-2)], path[D(0)] = (101.0, 104.0, 99.0, 103.0), (103.0, 107.0, 102.0, 106.0)
    put_bars(db, 'ZZZR', path)
    out, summary, acc = run_stage(db, stage_deps(scripted_model, [draft_reply()]))
    card = only_card(db)
    assert (card['end_kind'], card['end_session']) == ('position_closed', D(0))
    facts = card['facts']
    assert [(p['key'], p['roles']) for p in facts['plans']] == [(f"plan:{plan['id']}", ['reference', 'executed'])]
    assert facts['execution']['text'] == '采纳（照规则基线）'          # the buy's own decision carried the plan
    assert facts['execution']['legacy_decision']['decision'] == 'reject'      # the head decision, kept as it was
    assert facts['execution']['legacy_decision']['text'] == '否决'
    assert acc.detail['events'] == 1 and acc.detail['no_plan'] == 0


def _held_without_any_plan(db, ticker='ZZZN'):
    signal = D(-3)
    make_plan(db, ticker, signal)
    decision = make_decision(db, ticker, signal, None)                  # accepted without a plan, never one on the chain
    make_trade(db, ticker, 'buy', 100, at(D(-2)), decision['id'])
    make_trade(db, ticker, 'sell', 101, at(D(0)), decision['id'])
    put_bars(db, ticker, flat(D(-8), D(0)))


def test_no_plan_is_counted_only_where_the_close_falls_in_the_window(db, scripted_model):
    """A held unit whose chain never had a plan_id gets no card; the stage counts it once the close is inside the window,
    not while the position is open or before the sale, and not after the window moved on."""
    _held_without_any_plan(db)
    open_unit = make_decision(db, 'ZZZO', D(-3), None)
    make_trade(db, 'ZZZO', 'buy', 100, at(D(-2)), open_unit['id'])      # still held: no end, so nothing to count
    put_bars(db, 'ZZZO', flat(D(-8), D(0)))
    before = D(-1)                                                      # the sale (D(0)) has not happened yet
    out, summary, acc = run_stage(db, stage_deps(scripted_model, []), session=before, now=evening(before))
    assert cards(db) == [] and acc.detail['no_plan'] == 0
    out, summary, acc = run_stage(db, stage_deps(scripted_model, []))
    assert cards(db) == [] and acc.detail['no_plan'] == 1 and acc.detail['events'] == 0     # ZZZN, not ZZZO
    later = D(6)                                                        # the close left the 5-session window
    out, summary, acc = run_stage(db, stage_deps(scripted_model, []), session=later, now=evening(later))
    assert cards(db) == [] and acc.detail['no_plan'] == 0


def test_detect_since_backfills(db, scripted_model):
    scenario(db, 'ZZZO', 'closed', shift=-9)                            # ended nine sessions ago: outside the window
    assert retro_cards.detect(SESSION, now=NOW) == []
    old = retro.window_start(SESSION)
    assert old == D(-4)
    found = retro_cards.detect(SESSION, now=NOW, window_start=D(-12))
    db.commit()
    assert len(found) == 1 and only_card(db)['end_session'] == D(-9)
    assert retro_cards.detect(SESSION, now=NOW, window_start=D(-12)) == []      # idempotent


# ---------------------------------------------------------------- characterisation of the frozen numbers

def test_facts_tracks_equal_track_rows(db, scripted_model):
    s = scenario(db, 'ZZZA')
    run_stage(db, stage_deps(scripted_model, [draft_reply()]))
    card = only_card(db)
    [row] = [r for r in service._track_rows(db, s['signal_date'], s['signal_date'], session=SESSION, now=NOW)
             if r['ticker'] == 'ZZZA']
    tracks = card['facts']['tracks']
    assert set(tracks) == {'rule', 'agent', 'actual'}
    for name in ('rule', 'agent', 'actual'):
        assert tracks[name]['status'] == row[name]['status'] and tracks[name]['r'] == row[name]['r']
        assert tracks[name]['plan_id'] == row[name]['plan_id']
    assert tracks['actual']['status'] == 'closed' and tracks['actual']['plan_kind'] == 'rule'
    assert tracks['rule']['status'] == 'open' and tracks['agent']['status'] == 'missing'    # the shadow never sold
    assert retro_cards._fresh_tracks(row, modern=True) == tracks         # the check command's comparison


def test_facts_discipline_equals_evaluate(db, scripted_model):
    s = scenario(db, 'ZZZA')
    run_stage(db, stage_deps(scripted_model, [draft_reply()]))
    frozen = only_card(db)['facts']['discipline']
    live = service.discipline(now=NOW)
    [mine] = [t for t in live['trades'] if t['trade_id'] == str(s['buy']['id'])]
    assert frozen['trades'] == [{'trade_id': mine['trade_id'], 'flags': mine['flags'], 'immature': mine['immature']}]
    assert mine['flags'] == ['chase'] and mine['immature'] == ['early_take']
    assert frozen['totals'] == {'chase': 1, 'early_take': 0, 'held_loser': 0, 'unplanned': 0, 'pending': 1}
    assert frozen['as_of'] == SESSION.isoformat()
    # the very same call the retro module makes
    cache = retro_cards._Cache(db, SESSION, NOW)
    again = retro_cards.discipline_of(db, cache, 'ZZZA', {s['buy']['id']}, session=SESSION)
    assert again == frozen
    assert discipline_module.EARLY_TAKE_SESSIONS == 20


def test_card_view_discipline_now_differs(db, scripted_model):
    scenario(db, 'ZZZA')
    run_stage(db, stage_deps(scripted_model, [draft_reply()]))
    card = only_card(db)
    later = D(22)
    # the price reaches T1 (112) five sessions after the sale; every later bar is stored before `later`
    path = flat(D(1), later)
    path[D(5)] = (110.0, 113.0, 109.0, 111.0)
    put_bars(db, 'ZZZA', path, fetched=at(D(1)))
    db.commit()
    view = retro_cards.card_view(card['id'], now=at(later, 18))
    frozen, now_ = view['facts']['discipline'], view['discipline_now']
    assert frozen['trades'][0]['immature'] == ['early_take'] and frozen['totals']['early_take'] == 0
    assert now_['trades'][0]['flags'] == ['chase', 'early_take'] and now_['trades'][0]['immature'] == []
    assert now_['totals'] == {'chase': 1, 'early_take': 1, 'held_loser': 0, 'unplanned': 0, 'pending': 0}
    assert view['facts']['discipline']['as_of'] == SESSION.isoformat() and now_['as_of'] == later.isoformat()
    assert list(view['facts']) == list(retro.FACT_KEYS_V2)               # JSONB order restored
    assert set(view) == {'card', 'facts', 'status', 'drafts', 'lesson_drafts', 'verdict', 'discipline_now',
                         'discussions'}
    assert view['status'] == 'to_review' and view['verdict'] is None and view['discussions'] == []


# ---------------------------------------------------------------- gates and degradation

def _copies(db, card, n):
    for i in range(n):
        repository.insert_review_card(db, {
            'event_key': f"copy:{card['id']}:{i}", 'subject': 'unit', 'ticker': card['ticker'],
            'signal_date': card['signal_date'], 'buy_trade_id': None, 'end_kind': card['end_kind'],
            'end_session': card['end_session'] - timedelta(days=0), 'retro_version': card['retro_version'],
            'facts': card['facts'], 'detected_run_id': 'copy'})
    db.commit()


def _seed_cards(db, scripted_model, n=3):
    """One real card detected with an unavailable agent, plus copies of it: no drafts yet, ``n`` cards in all."""
    scenario(db, 'ZZZA')
    off = stage_deps(scripted_model, [], connection='disconnected')
    run_stage(db, off)
    _copies(db, only_card(db), n - 1)
    db.execute('DELETE FROM review_card_drafts')             # forget the skip row, so each gate shows by itself
    db.commit()
    assert len(cards(db)) == n


def test_draft_cap_and_gates(db, scripted_model):
    _seed_cards(db, scripted_model, 7)
    # draft_cap: five drafts, the other two skipped, no row written for the cap
    d = stage_deps(scripted_model, [draft_reply() for _ in range(5)])
    out, summary, acc = run_stage(db, d)
    assert acc.detail['drafts'] == {'ok': 5, 'rejected': 0, 'error': 0, 'skipped': 2}
    assert [(s['reason'], s['count']) for s in acc.skips] == [('draft_cap', 2)]
    assert acc.input_count == 7 and acc.output_count == 5 and summary['retro']['drafts'] == 5
    assert db.execute('SELECT count(*) FROM review_card_drafts').fetchone()[0] == 5

    # stage_budget: nothing started once the agent deadline has passed
    db.execute('DELETE FROM knowledge_drafts')
    db.execute('DELETE FROM review_card_drafts')
    db.commit()
    out, summary, acc = run_stage(db, stage_deps(scripted_model, []), clock=lambda: service.AGENT_DEADLINE_S + 1)
    assert [(s['reason'], s['count']) for s in acc.skips] == [('stage_budget', 7)]
    assert acc.detail['drafts']['skipped'] == 7
    rows = db.execute("SELECT status, reason FROM review_card_drafts").fetchall()
    assert sorted(rows) == [('skipped', 'stage_budget')] * 7                 # one skip row per card, with the reason
    out, summary, acc = run_stage(db, stage_deps(scripted_model, []), clock=lambda: service.AGENT_DEADLINE_S + 1)
    assert db.execute('SELECT count(*) FROM review_card_drafts').fetchone()[0] == 7      # same reason: no new rows

    # quota_exhausted: the agent stage already ran into the quota
    summary = {'errors': [], 'agent': {'quota_exhausted': True}}
    out, summary, acc = run_stage(db, stage_deps(scripted_model, []), summary=summary)
    assert [(s['reason'], s['count']) for s in acc.skips] == [('quota_exhausted', 7)]

    # token_budget: today's tokens are already at the limit
    repository.insert_agent_run(db, {
        'id': uuid.uuid4(), 'ticker': 'AAA', 'signal_date': SESSION, 'pool': 'main', 'attempt': 1, 'mode': 'propose',
        'agent_version': 'agent-2', 'provider': 'chatgpt', 'model': 'gpt-test', 'cutoff_at': NOW, 'status': 'error',
        'reason': 'model_error', 'output': None, 'counter': None, 'plan_id': None, 'input_tokens': 900,
        'output_tokens': 200, 'started_at': NOW, 'finished_at': NOW}, [])
    out, summary, acc = run_stage(db, stage_deps(scripted_model, [], daily_tokens=1000))
    assert [(s['reason'], s['count']) for s in acc.skips] == [('token_budget', 7)]

    # a card that already failed three times is no longer "needing a draft" (spec §4.4): not input, not skipped
    first = min(cards(db), key=lambda c: c['id'])           # the oldest end is drafted first
    db.execute('DELETE FROM review_card_drafts')
    for attempt in (1, 2, 3):
        repository.insert_card_draft(db, {'card_id': first['id'], 'attempt': attempt, 'trigger': 'batch',
                                          'agent_run_id': None, 'status': 'skipped', 'reason': 'old', 'output': None})
    db.execute("UPDATE review_card_drafts SET status = 'rejected', reason = 'invalid_citation', "
               "agent_run_id = (SELECT id FROM agent_runs LIMIT 1) WHERE card_id = %s", (first['id'],))
    db.commit()
    assert retro.card_status(drafts=repository.card_drafts(db, first['id']), verdict=None) == 'draft_failed'
    out, summary, acc = run_stage(db, stage_deps(scripted_model, [draft_reply() for _ in range(6)]))
    assert acc.input_count == 6 and [(s['reason'], s['count']) for s in acc.skips] == [('draft_cap', 1)]
    assert acc.detail['drafts'] == {'ok': 5, 'rejected': 0, 'error': 0, 'skipped': 1}
    assert summary['retro']['to_review'] == 5


def test_review_stage_quota_stops_the_retro_drafts_without_spending_an_attempt(db, scripted_model):
    _seed_cards(db, scripted_model, 2)
    db.execute('DELETE FROM knowledge_drafts')
    db.execute('DELETE FROM review_card_drafts')
    db.commit()
    summary = {'errors': [], 'agent': {'quota_exhausted': False}, 'review': {'quota_exhausted': True}}
    out, summary, acc = run_stage(db, stage_deps(scripted_model, []), summary=summary)     # an empty queue: no model call
    assert [(s['reason'], s['count']) for s in acc.skips] == [('quota_exhausted', 2)]
    assert acc.detail['drafts'] == {'ok': 0, 'rejected': 0, 'error': 0, 'skipped': 2}
    assert acc.failures == [] and db.execute('SELECT count(*) FROM agent_runs').fetchone()[0] == 0
    assert sorted(db.execute('SELECT status, reason FROM review_card_drafts').fetchall()) \
        == [('skipped', 'quota_exhausted')] * 2
    assert [card_state['failed'] for card_state in
            (retro_cards.card_state(db, c['id']) for c in cards(db))] == [0, 0]       # nothing counts towards draft_failed
    out, summary, acc = run_stage(db, stage_deps(scripted_model, [draft_reply(), draft_reply()]))
    assert acc.detail['drafts']['ok'] == 2                                  # the next day's run drafts them normally


def test_max_attempts_is_reported_once_and_draft_failed_is_not_needing(db, scripted_model):
    """Spec §4.4 / §9: the run whose attempt is the third failure reports `max_attempts`; afterwards the card is not
    input, not a degradation when the agent is down, and a stage with nothing else to draft is a plain success."""
    scenario(db, 'ZZZA')
    bad = AIMessage(content='不是 JSON')
    for attempt in (1, 2):
        out, summary, acc = run_stage(db, stage_deps(scripted_model, [bad]), run_id=f'run-{attempt}')
        assert acc.detail['drafts']['rejected'] == 1 and acc.skips == [] and acc.input_count == 1
    out, summary, acc = run_stage(db, stage_deps(scripted_model, [bad]), run_id='run-3')
    assert acc.input_count == 1 and [(s['reason'], s['count'], s['samples']) for s in acc.skips] == [
        ('max_attempts', 1, ['ZZZA'])]
    card = only_card(db)
    assert retro_cards.card_state(db, card['id'])['status'] == 'draft_failed'
    rows = []
    recorder = stages_module.StageRecorder('run-4', 'manual_batch', SESSION, writer=rows.append, wall_now=lambda: NOW)
    with recorder.stage('retro') as acc:
        retro_cards.run_stage(SESSION, now=NOW, deps=stage_deps(scripted_model, [], connection='disconnected'),
                              summary={'errors': []}, started=0.0, clock=lambda: 0.0, run_id='run-4', acc=acc)
    [row] = [r for r in rows if r['stage'] == 'retro']
    assert (row['status'], row['input_count'], row['skips'], row['degradations']) == ('success', 0, [], [])
    assert len(repository.card_drafts(db, card['id'])) == 3                # nothing written after the third failure


def test_verdict_landing_while_the_model_runs_leaves_no_orphan_lessons(db, scripted_model):
    """The verdict may land between the model call and the write: the run is kept, the draft row is a rejected
    `card_reviewed`, and no lesson drafts are written (they could never be decided)."""
    scenario(db, 'ZZZA')
    run_stage(db, stage_deps(scripted_model, [], connection='disconnected'))
    card = only_card(db)
    d = stage_deps(scripted_model, [draft_reply(lessons=2)])
    real = d.agent_model.invoke

    def verdict_then_answer(*args, **kwargs):
        retro_cards.verdict(card['id'], {'request_id': str(uuid.uuid4()), 'note': '先审了', 'lessons': []}, now=NOW)
        return real(*args, **kwargs)

    d.agent_model = _Wrapped(d.agent_model, verdict_then_answer)
    out = retro_cards.draft_card(card['id'], now=NOW, deps=d, trigger='manual')
    db.commit()
    assert (out['status'], out['reason']) == ('rejected', 'card_reviewed')
    assert db.execute('SELECT count(*) FROM knowledge_drafts').fetchone()[0] == 0
    last = repository.card_drafts(db, card['id'])[-1]
    assert (last['status'], last['reason'], last['output']) == ('rejected', 'card_reviewed', None)
    assert repository.agent_run_by_id(db, last['agent_run_id'])['mode'] == 'retro'      # the run (and its tokens) is kept
    assert retro_cards.card_state(db, card['id'])['status'] == 'reviewed'
    assert retro_cards.card_view(card['id'], now=NOW)['lesson_drafts'] == []


class _Wrapped:
    """A model whose invoke runs a hook first (another request landing while the model thinks)."""

    def __init__(self, model, invoke):
        self._model, self._invoke = model, invoke

    def bind_tools(self, tools, **kwargs):
        self._model.bind_tools(tools, **kwargs)
        return self

    def invoke(self, messages, *args, **kwargs):
        return self._invoke(messages, *args, **kwargs)


def test_two_drafts_in_a_row_get_distinct_attempts_and_the_second_is_draft_exists(db, scripted_model):
    scenario(db, 'ZZZA')
    run_stage(db, stage_deps(scripted_model, [], connection='disconnected'))
    card = only_card(db)
    first_deps = stage_deps(scripted_model, [draft_reply()])
    second_deps = stage_deps(scripted_model, [draft_reply()])
    real = first_deps.agent_model.invoke

    def other_draft_first(*args, **kwargs):             # a second draft finishes while the first model call runs
        inner = retro_cards.draft_card(card['id'], now=NOW, deps=second_deps, trigger='manual')
        assert inner['status'] == 'ok'
        return real(*args, **kwargs)

    first_deps.agent_model = _Wrapped(first_deps.agent_model, other_draft_first)
    out = retro_cards.draft_card(card['id'], now=NOW, deps=first_deps, trigger='manual')
    db.commit()
    assert (out['status'], out['reason']) == ('rejected', 'draft_exists')            # no exception, the run is kept
    drafts = repository.card_drafts(db, card['id'])
    assert [(d['attempt'], d['status'], d['reason']) for d in drafts][-2:] == [(2, 'ok', None),
                                                                               (3, 'rejected', 'draft_exists')]
    assert db.execute('SELECT count(*) FROM knowledge_drafts').fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM agent_runs WHERE mode = 'retro'").fetchone()[0] == 2


def test_skip_rows_take_the_card_lock(db, scripted_model):
    scenario(db, 'ZZZA')
    run_stage(db, stage_deps(scripted_model, [], connection='disconnected'))
    card = only_card(db)
    retro_cards._record_skip(card['id'], 'stage_budget', 'batch')
    retro_cards._record_skip(card['id'], 'stage_budget', 'batch')
    assert [(d['attempt'], d['reason']) for d in repository.card_drafts(db, card['id'])] == [
        (1, 'agent_unavailable: not_connected'), (2, 'stage_budget')]


def test_list_rows_and_card_view_say_whether_the_subject_was_filled(db, scripted_model):
    scenario(db, 'ZZZU', 'unplanned')
    scenario(db, 'ZZZB', 'stop')
    scenario(db, 'ZZZA')
    run_stage(db, stage_deps(scripted_model, [], connection='disconnected'))
    rows = {r['ticker']: r for r in retro_cards.list_view(status=None, ticker=None, since=None)['cards']}
    assert (rows['ZZZU']['filled'], rows['ZZZU']['actual_r']) == (True, None)          # bought and sold, no R
    assert (rows['ZZZB']['filled'], rows['ZZZB']['actual_r']) == (False, None)         # never bought: 未成交
    assert rows['ZZZA']['filled'] is True and rows['ZZZA']['actual_r'] is not None
    ids = {c['ticker']: c['id'] for c in cards(db)}
    assert retro_cards.card_view(ids['ZZZU'], now=NOW)['card']['filled'] is True
    assert retro_cards.card_view(ids['ZZZB'], now=NOW)['card']['filled'] is False
    learning = retro_cards.learning_cards(db, SESSION - timedelta(days=3), SESSION)
    assert {c['ticker']: c['filled'] for c in learning} == {'ZZZU': True, 'ZZZB': False, 'ZZZA': True}


def test_stage_errors_are_sanitized(db, scripted_model, monkeypatch):
    scenario(db, 'ZZZA')
    monkeypatch.setenv('FRED_API_KEY', 'sekret-key-123')

    def boom(*a, **k):
        raise RuntimeError('provider said sekret-key-123 is bad')

    monkeypatch.setattr(retro_cards, 'draft_card', boom)
    out, summary, acc = run_stage(db, stage_deps(scripted_model, []))
    assert summary['errors'] and all('sekret-key-123' not in e for e in summary['errors'])
    assert all('sekret-key-123' not in f['reason'] for f in acc.failures)


def test_agent_unavailable_still_creates_cards(db, scripted_model):
    scenario(db, 'ZZZA')
    scenario(db, 'ZZZB', 'stop')
    rows = []
    recorder = stages_module.StageRecorder('run-off', 'manual_batch', SESSION, writer=rows.append,
                                           wall_now=lambda: NOW)
    summary = {'errors': [], 'agent': None}
    with recorder.stage('retro') as acc:
        retro_cards.run_stage(SESSION, now=NOW, deps=stage_deps(scripted_model, [], connection='disconnected'), summary=summary,
                              started=0.0, clock=lambda: 0.0, run_id='run-off', acc=acc)
    db.commit()
    assert len(cards(db)) == 2
    [row] = [r for r in rows if r['stage'] == 'retro']
    assert row['status'] == 'degraded' and row['input_count'] == 2 and row['output_count'] == 0
    assert [d['kind'] for d in row['degradations']] == ['agent_unavailable']
    assert [(s['reason'], s['count']) for s in row['skips']] == [('agent_unavailable: not_connected', 2)]
    assert row['detail']['drafts'] == {'ok': 0, 'rejected': 0, 'error': 0, 'skipped': 2}
    assert row['detail']['new_cards'] == 2 and row['detail']['to_review'] == 0


def test_nothing_to_draft_is_a_plain_success_even_without_an_agent(db, scripted_model):
    scenario(db, 'ZZZQ', 'stop', shift=-9)                      # ended long before the window: no event, no card
    rows = []
    recorder = stages_module.StageRecorder('run-quiet', 'manual_batch', SESSION, writer=rows.append,
                                           wall_now=lambda: NOW)
    with recorder.stage('retro') as acc:
        retro_cards.run_stage(SESSION, now=NOW, deps=stage_deps(scripted_model, [], connection='disconnected'),
                              summary={'errors': []}, started=0.0, clock=lambda: 0.0, run_id='run-q', acc=acc)
    [row] = [r for r in rows if r['stage'] == 'retro']
    assert (row['status'], row['input_count'], row['output_count'], row['degradations']) == ('success', 0, 0, [])
    assert row['detail']['events'] == 0 and row['detail']['to_review'] == 0


def test_rejected_and_error_drafts_are_accounted(db, scripted_model):
    scenario(db, 'ZZZA')
    scenario(db, 'ZZZB', 'stop')
    bad = AIMessage(content='不是 JSON')
    d = stage_deps(scripted_model, [bad, draft_reply()])
    out, summary, acc = run_stage(db, d)
    statuses = sorted((c['ticker'], repository.card_drafts(db, c['id'])[0]['status'],
                       repository.card_drafts(db, c['id'])[0]['reason']) for c in cards(db))
    assert statuses == [('ZZZA', 'ok', None), ('ZZZB', 'rejected', 'retro_malformed')]       # the older end is drafted first
    assert acc.detail['drafts'] == {'ok': 1, 'rejected': 1, 'error': 0, 'skipped': 0}
    assert [x['kind'] for x in acc.degradations] == ['draft_rejected']
    assert db.execute("SELECT count(*) FROM knowledge_drafts").fetchone()[0] == 1
    rejected = db.execute("SELECT status, reason, output, plan_id FROM agent_runs WHERE mode = 'retro' "
                          "ORDER BY created_at").fetchall()
    assert [(r[0], r[1], r[2], r[3]) for r in rejected] == [('rejected', 'retro_malformed', None, None),
                                                            ('ok', None, rejected[1][2], None)]
    assert summary['retro'] == {'new_cards': 2, 'drafts': 1, 'to_review': 1}
    # a model exception becomes an error draft and does not stop the next card
    scripted = scripted_model([draft_reply()])
    boom = stage_deps(scripted_model, [])
    boom.agent_model = None

    class Exploding:
        def bind_tools(self, tools, **kwargs):
            return self

        def invoke(self, messages, *args, **kwargs):
            raise RuntimeError('provider down')

    boom.agent_model = Exploding()
    third = scenario(db, 'ZZZC', 'expired')
    out, summary, acc = run_stage(db, boom)
    assert acc.detail['drafts']['error'] == 2 and [f['reason'] for f in acc.failures] == ['model_error'] * 2
    errors = db.execute("SELECT status, reason FROM review_card_drafts WHERE status = 'error'").fetchall()
    assert errors == [('error', 'model_error')] * 2
    assert third['plan'] is not None and scripted is not None


# ---------------------------------------------------------------- verdict

def _approve(draft, **over):
    return {'draft_id': draft['id'], 'action': 'approve', 'title': draft['title'], 'body': draft['body'],
            'scope': draft['scope'], 'counter_examples': draft['counter_examples'],
            'concepts': list(draft['concepts']), 'tickers': list(draft['tickers']), **over}


def _dismiss(draft):
    return {'draft_id': draft['id'], 'action': 'dismiss', 'title': draft['title'], 'body': draft['body'],
            'scope': draft['scope'], 'counter_examples': draft['counter_examples'],
            'concepts': list(draft['concepts']), 'tickers': list(draft['tickers'])}


def _drafted_card(db, scripted_model, lessons=2):
    scenario(db, 'ZZZA')
    run_stage(db, stage_deps(scripted_model, [draft_reply(lessons=lessons)]))
    card = only_card(db)
    return card, repository.knowledge_drafts(db, open_only=True)


def test_verdict_requires_all_drafts(db, scripted_model):
    card, lessons = _drafted_card(db, scripted_model)
    body = {'request_id': str(uuid.uuid4()), 'note': '我的复盘', 'lessons': [_approve(lessons[0])]}
    with pytest.raises(ValueError, match='全部出现'):
        retro_cards.verdict(card['id'], body, now=NOW)
    twice = {**body, 'lessons': [_approve(lessons[0]), _approve(lessons[0])]}
    with pytest.raises(ValueError):
        retro_cards.verdict(card['id'], twice, now=NOW)
    foreign = {**body, 'lessons': [*body['lessons'], {**_approve(lessons[1]), 'draft_id': 999999}]}
    with pytest.raises(ValueError):
        retro_cards.verdict(card['id'], foreign, now=NOW)
    for field in ('scope', 'counter_examples'):                           # a drafted lesson needs both
        broken = {**body, 'lessons': [_approve(lessons[0], **{field: ' '}), _approve(lessons[1])]}
        with pytest.raises(ValueError):
            retro_cards.verdict(card['id'], broken, now=NOW)
    db.rollback()
    assert repository.card_verdict(db, card['id']) is None
    assert db.execute('SELECT count(*) FROM knowledge_items').fetchone()[0] == 0


def test_verdict_creates_items_versions_and_draft_verdicts_atomically(db, scripted_model):
    card, lessons = _drafted_card(db, scripted_model)
    request_id = str(uuid.uuid4())
    body = {'request_id': request_id, 'note': '  我的复盘：下次不追  ', 'lessons': [
        _approve(lessons[0], title='改过的标题', tickers=['ZZZA']), _dismiss(lessons[1]),
        {'action': 'approve', 'title': '另写的', 'body': '一条自己写的经验', 'scope': '回踩后的入场',
         'counter_examples': '', 'concepts': [], 'tickers': ['ZZZA']}]}
    result = retro_cards.verdict(card['id'], body, now=NOW)
    assert result['status'] == 'created'
    assert [(x['draft_id'], x['action'], x['ref'] is not None) for x in result['lessons']] == [
        (lessons[0]['id'], 'approve', True), (lessons[1]['id'], 'dismiss', False), (None, 'approve', True)]
    verdict = repository.card_verdict(db, card['id'])
    assert verdict['note'] == '我的复盘：下次不追' and verdict['draft_id'] is not None
    items = db.execute('SELECT id, origin, source_card_id, source_discussion_id FROM knowledge_items ORDER BY id').fetchall()
    assert [(o, c, d) for _, o, c, d in items] == [('review_card', card['id'], None)] * 2
    versions, _ = repository.knowledge_rows(db, cutoff=None)
    assert [(v['version'], v['title'], v['supersedes_id'], v['draft_id']) for v in versions] == [
        (1, '改过的标题', None, lessons[0]['id']), (1, '另写的', None, None)]
    assert versions[0]['concepts'] == ['concept:review.discipline_chase@1'] and versions[0]['tickers'] == ['ZZZA']
    assert versions[0]['schema_version'] == 'my-knowledge-1'
    dv = db.execute('SELECT draft_id, action, knowledge_version_id FROM knowledge_draft_verdicts '
                    'ORDER BY draft_id').fetchall()
    assert dv == [(lessons[0]['id'], 'approve', versions[0]['id']), (lessons[1]['id'], 'dismiss', None)]
    assert repository.knowledge_drafts(db, open_only=True) == []
    view = retro_cards.card_view(card['id'], now=NOW)
    assert view['status'] == 'reviewed'
    assert view['verdict']['note'] == '我的复盘：下次不追'
    assert [(x['draft_id'], x['action'], x['title']) for x in view['verdict']['lessons']] == [
        (lessons[0]['id'], 'approve', '改过的标题'), (lessons[1]['id'], 'dismiss', lessons[1]['title']),
        (None, 'approve', '另写的')]
    assert view['verdict']['lessons'][0]['ref'] == f"know:{items[0][0]}@1" and view['verdict']['lessons'][1]['ref'] is None
    # the same request replays; nothing is written twice
    again = retro_cards.verdict(card['id'], body, now=NOW)
    assert again['status'] == 'replayed' and again['record']['id'] == result['record']['id']
    assert db.execute('SELECT count(*) FROM knowledge_items').fetchone()[0] == 2


def test_second_verdict_409(db, scripted_model):
    card, lessons = _drafted_card(db, scripted_model, lessons=1)
    first = {'request_id': str(uuid.uuid4()), 'note': '', 'lessons': [_dismiss(lessons[0])]}
    retro_cards.verdict(card['id'], first, now=NOW)
    other = {'request_id': str(uuid.uuid4()), 'note': 'x', 'lessons': [_approve(lessons[0])]}
    with pytest.raises(ledger.Conflict, match='^card_reviewed$'):
        retro_cards.verdict(card['id'], other, now=NOW)
    changed = {**first, 'note': '换了内容'}
    with pytest.raises(ledger.Conflict, match='^request_id_conflict$'):
        retro_cards.verdict(card['id'], changed, now=NOW)
    with pytest.raises(LookupError):
        retro_cards.verdict(999999, first, now=NOW)
    with pytest.raises(ValueError):
        retro_cards.verdict(card['id'], {**first, 'request_id': 'nope'}, now=NOW)


def test_verdict_without_draft_note_only(db, scripted_model):
    scenario(db, 'ZZZA')
    run_stage(db, stage_deps(scripted_model, [], connection='disconnected'))          # drafts skipped: the card has no ok draft
    card = only_card(db)
    result = retro_cards.verdict(card['id'], {'request_id': str(uuid.uuid4()), 'note': '只写复盘',
                                             'lessons': []}, now=NOW)
    assert result['status'] == 'created' and result['lessons'] == []
    verdict = repository.card_verdict(db, card['id'])
    assert verdict['note'] == '只写复盘' and verdict['draft_id'] is None
    assert db.execute('SELECT count(*) FROM knowledge_items').fetchone()[0] == 0
    assert retro_cards.card_view(card['id'], now=NOW)['status'] == 'reviewed'


def test_redraft_conflicts(db, scripted_model, monkeypatch):
    scenario(db, 'ZZZA')
    run_stage(db, stage_deps(scripted_model, [draft_reply()]))
    card = only_card(db)
    connection_is(monkeypatch)
    with pytest.raises(ledger.Conflict, match='^draft_exists$'):
        retro_cards.redraft_check(card['id'])
    with pytest.raises(LookupError):
        retro_cards.redraft_check(999999)
    db.execute('DELETE FROM knowledge_drafts')
    db.execute('DELETE FROM review_card_drafts')
    db.commit()
    retro_cards.redraft_check(card['id'])                                 # no ok draft, agent configured: allowed
    connection_is(monkeypatch, 'disconnected')
    with pytest.raises(ledger.Conflict, match='^agent_unavailable: not_connected$'):
        retro_cards.redraft_check(card['id'])
    connection_is(monkeypatch)
    monkeypatch.setenv('WORKBENCH_AGENT_DAILY_TOKENS', '100')
    repository.insert_agent_run(db, {
        'id': uuid.uuid4(), 'ticker': 'AAA', 'signal_date': SESSION, 'pool': 'main', 'attempt': 1, 'mode': 'propose',
        'agent_version': 'agent-2', 'provider': 'chatgpt', 'model': 'gpt-test', 'cutoff_at': NOW, 'status': 'error',
        'reason': 'model_error', 'output': None, 'counter': None, 'plan_id': None, 'input_tokens': 150,
        'output_tokens': 0, 'started_at': datetime.now(UTC), 'finished_at': datetime.now(UTC)}, [])
    with pytest.raises(ledger.Conflict, match='^token_budget$'):
        retro_cards.redraft_check(card['id'])
    monkeypatch.delenv('WORKBENCH_AGENT_DAILY_TOKENS')
    retro_cards.verdict(card['id'], {'request_id': str(uuid.uuid4()), 'note': 'x', 'lessons': []}, now=NOW)
    with pytest.raises(ledger.Conflict, match='^card_reviewed$'):
        retro_cards.redraft_check(card['id'])


def test_manual_redraft_is_not_capped_and_adds_an_attempt(db, scripted_model):
    scenario(db, 'ZZZA')
    bad = AIMessage(content='不是 JSON')
    run_stage(db, stage_deps(scripted_model, [bad]))
    card = only_card(db)
    assert retro_cards.card_state(db, card['id'])['status'] == 'drafting'
    for _ in range(2):
        out = retro_cards.redraft(card['id'], now=NOW, deps=stage_deps(scripted_model, [bad]))
        assert out['status'] == 'rejected'
    assert retro_cards.card_state(db, card['id'])['status'] == 'draft_failed'      # three failures
    out = retro_cards.redraft(card['id'], now=NOW, deps=stage_deps(scripted_model, [draft_reply()]))
    assert out['status'] == 'ok' and out['draft_id'] is not None
    drafts = repository.card_drafts(db, card['id'])
    assert [(d['attempt'], d['trigger'], d['status']) for d in drafts] == [
        (1, 'batch', 'rejected'), (2, 'manual', 'rejected'), (3, 'manual', 'rejected'), (4, 'manual', 'ok')]
    again = retro_cards.draft_card(card['id'], now=NOW, deps=stage_deps(scripted_model, []))
    assert again['status'] == 'conflict' and again['reason'] == 'draft_exists'
    assert retro_cards.card_state(db, card['id'])['status'] == 'to_review'


# ---------------------------------------------------------------- views and command line

def test_list_view_rows_and_counts(db, scripted_model):
    scenario(db, 'ZZZA')
    scenario(db, 'ZZZB', 'stop')
    run_stage(db, stage_deps(scripted_model, [draft_reply(), draft_reply(lessons=0)]))
    view = retro_cards.list_view(status=None, ticker=None, since=None)
    assert [r['ticker'] for r in view['cards']] == ['ZZZB', 'ZZZA'] or [r['end_session'] for r in view['cards']] == \
        sorted([r['end_session'] for r in view['cards']], reverse=True)
    assert view['counts'] == {'reviewed': 0, 'to_review': 2, 'draft_failed': 0, 'drafting': 0}
    row = next(r for r in view['cards'] if r['ticker'] == 'ZZZA')
    assert set(row) >= {'id', 'ticker', 'subject', 'signal_date', 'end_kind', 'exit_basis', 'end_session',
                        'decision_text', 'execution_text', 'actual_r', 'rule_r', 'agent_r', 'flags', 'immature', 'status',
                        'headline'}
    assert row['flags'] == ['chase'] and row['immature'] == ['early_take'] and row['status'] == 'to_review'
    assert row['execution_text'] == '采纳（照规则基线）' and row['decision_text'] is None
    assert row['headline'].startswith('一句话')
    assert row['actual_r'] is not None and row['agent_r'] is None
    stop = next(r for r in view['cards'] if r['ticker'] == 'ZZZB')
    assert stop['actual_r'] is None and stop['execution_text'] == '未执行' and stop['flags'] == []
    assert [r['ticker'] for r in retro_cards.list_view(status='to_review', ticker='ZZZB', since=None)['cards']] == ['ZZZB']
    assert retro_cards.list_view(status='reviewed', ticker=None, since=None)['cards'] == []
    assert retro_cards.list_view(status='reviewed', ticker=None, since=None)['counts']['to_review'] == 2
    assert retro_cards.list_view(status=None, ticker=None, since=D(0))['cards'] != []
    with pytest.raises(ValueError):
        retro_cards.list_view(status='bogus', ticker=None, since=None)


def test_card_views_need_migration_027(db):
    db.execute('DROP TABLE knowledge_citations, knowledge_draft_verdicts, review_card_verdicts, knowledge_drafts, '
               'knowledge_versions, knowledge_status, discussion_turns, knowledge_items, discussions, '
               'review_card_drafts, review_cards CASCADE')
    db.commit()
    with pytest.raises(service.Unavailable, match='迁移 027'):
        retro_cards.list_view(status=None, ticker=None, since=None)
    with pytest.raises(service.Unavailable, match='迁移 027'):
        retro_cards.card_view(1, now=NOW)
    with pytest.raises(service.Unavailable, match='迁移 027'):
        retro_cards.redraft_check(1)


def _run(capsys, *argv):
    code = retro_cards.main(list(argv))
    return code, capsys.readouterr().out


def test_retro_check_cli(db, scripted_model, capsys):
    scenario(db, 'ZZZA')
    scenario(db, 'ZZZB', 'stop')
    since = D(-4).isoformat()
    code, out = _run(capsys, 'check', '--since', since, '--until', SESSION.isoformat())
    assert code == 1 and out.count('缺卡') == 3 and '→ 不通过' in out.splitlines()[-1]       # 2 events + the summary
    assert retro_cards.detect(SESSION, now=NOW)
    db.commit()
    code, out = _run(capsys, 'check', '--since', since, '--until', SESSION.isoformat())
    assert code == 0 and out.count('齐全') == 2 and '缺卡 0' in out and '→ 通过' in out.splitlines()[-1]
    assert '事实已变化' not in out.replace('事实已变化 0', '')
    # a trade voided after the card was frozen: the facts changed, the check still passes and says so
    sell = db.execute("SELECT id FROM trades WHERE ticker = 'ZZZA' AND side = 'sell'").fetchone()[0]
    repository.mark_trade_voided(db, sell, 'typo', voided_at=NOW)
    db.commit()
    code, out = _run(capsys, 'check', '--since', since, '--until', SESSION.isoformat())
    assert code == 0 and '事实已变化' in out
    assert any(line.startswith('齐全') and '事实已变化：tracks' in line for line in out.splitlines())
    # an incomplete card is a failure
    only = [c for c in cards(db) if c['ticker'] == 'ZZZB'][0]
    db.execute("UPDATE review_cards SET facts = facts - 'knowledge' WHERE id = %s", (only['id'],))
    db.commit()
    code, out = _run(capsys, 'check', '--since', since, '--until', SESSION.isoformat())
    assert code == 1 and '事实不全' in out and 'knowledge' in out
    # bad arguments
    assert _run(capsys, 'check', '--since', SESSION.isoformat(), '--until', since)[0] == 2
    with pytest.raises(SystemExit) as raised:
        retro_cards.main(['check', '--since', 'yesterday'])
    assert raised.value.code == 2
    capsys.readouterr()


def test_retro_check_compares_facts_as_of_the_freeze(db, scripted_model, capsys):
    """A shadow that moves on after the close is not a changed fact; a trade voided after the freeze is."""
    scenario(db, 'ZZZA')
    assert retro_cards.detect(SESSION, now=NOW)
    db.commit()
    since, until = D(-4).isoformat(), D(3).isoformat()
    later = flat(D(1), D(3))
    later[D(2)] = (110.0, 113.0, 109.0, 112.0)                          # the rule plan's shadow reaches T1 (112)
    put_bars(db, 'ZZZA', later, fetched=at(D(1)))
    db.commit()
    assert retro_cards._fresh_tracks(service.unit_track_row(
        db, 'ZZZA', only_card(db)['signal_date'], session=D(3), now=NOW + timedelta(days=30)))['rule'] \
        != only_card(db)['facts']['tracks']['rule']                     # the premise: today's row really drifted
    code, out = _run(capsys, 'check', '--since', since, '--until', until)
    assert code == 0 and '事实已变化 0' in out and '事实已变化：' not in out
    sell = db.execute("SELECT id FROM trades WHERE ticker = 'ZZZA' AND side = 'sell'").fetchone()[0]
    repository.mark_trade_voided(db, sell, 'typo', voided_at=NOW)
    db.commit()
    code, out = _run(capsys, 'check', '--since', since, '--until', until)
    assert code == 0 and '事实已变化 1' in out and '事实已变化：tracks' in out


def test_retro_check_fails_for_a_closed_unit_without_a_reference_plan(db, scripted_model, capsys):
    scenario(db, 'ZZZA')
    _held_without_any_plan(db)
    since = D(-4).isoformat()
    assert retro_cards.detect(SESSION, now=NOW)
    db.commit()
    code, out = _run(capsys, 'check', '--since', since, '--until', SESSION.isoformat())
    lines = out.splitlines()
    assert code == 1 and sum('无参考计划' in line for line in lines) == 2         # its own line + the summary count
    own = next(line for line in lines if line.startswith('无参考计划'))
    assert 'ZZZN' in own and str(D(-3)) in own and str(D(0)) in own
    assert '缺卡 0' in lines[-1] and '无参考计划 1' in lines[-1] and '→ 不通过' in lines[-1]
    assert out.count('齐全') == 1                                       # ZZZA's card is still fine
    assert _run(capsys, 'check', '--since', D(1).isoformat(), '--until', SESSION.isoformat())[0] == 2   # since > until


def test_retro_check_database_unavailable_exits_two(monkeypatch, capsys):
    monkeypatch.delenv('INVESTMENT_ASSISTANT_DATABASE_URL', raising=False)
    assert retro_cards.main(['check', '--since', '2026-10-01']) == 2
    assert '数据库' in capsys.readouterr().err


def test_detect_show_and_draft_commands(db, scripted_model, capsys, monkeypatch):
    scenario(db, 'ZZZA')
    monkeypatch.setattr(retro_cards, '_cli_now', lambda: NOW)
    code, out = _run(capsys, 'detect')
    assert code == 0 and '新卡 #' in out and '新建 1 张' in out
    db.commit()
    code, out = _run(capsys, 'detect')
    assert code == 0 and '新建 0 张' in out
    card = only_card(db)
    code, out = _run(capsys, 'show', '--card', str(card['id']))
    assert code == 0 and 'ZZZA' in out and '平仓（自行卖出）' in out and '执行 采纳（照规则基线）' in out
    assert _run(capsys, 'show', '--card', '999999')[0] == 1
    built = stage_deps(scripted_model, [draft_reply()])
    monkeypatch.setattr(retro_cards.service, 'Deps', lambda: built)
    code, out = _run(capsys, 'draft', '--card', str(card['id']))
    assert code == 0 and '起草结果：ok' in out
    assert repository.card_drafts(db, card['id'])[0]['trigger'] == 'manual'


def test_retro_read_paths_are_read_only(db, scripted_model, monkeypatch):
    """list_view / card_view / check open read-only transactions: a write attempted through them is refused."""
    scenario(db, 'ZZZA')
    run_stage(db, stage_deps(scripted_model, [draft_reply()]))
    opened = []
    real = service.read_only_tx

    def counting():
        opened.append(1)
        return real()

    monkeypatch.setattr(core_tx, 'read_only_tx', counting)
    retro_cards.list_view(status=None, ticker=None, since=None)
    retro_cards.card_view(only_card(db)['id'], now=NOW)
    assert len(opened) == 2


# ---------------------------------------------------------------- retro-2 (v2.9 spec D29)

def plan_linked_unit(db, ticker, *, sell=True, notes=()):
    """A rule baseline bought and (optionally) sold through ``trades.plan_id`` only: no decision anywhere."""
    signal = D(-3)
    plan = make_plan(db, ticker, signal)
    buy = make_trade(db, ticker, 'buy', 103, at(D(-2)), plan_id=plan['id'])
    out = {'plan': plan, 'buy': buy, 'signal_date': signal}
    path = flat(D(-8), D(0))
    path[D(-2)] = (101.0, 104.0, 99.0, 103.0)
    if sell:
        out['sell'] = make_trade(db, ticker, 'sell', 106, at(D(0)))
        path[D(0)] = (103.0, 107.0, 102.0, 106.0)
    put_bars(db, ticker, path)
    for body in notes:
        out.setdefault('notes', []).append(make_note(db, plan['id'], ticker, body))
    return out


def test_retro2_subjects(db, scripted_model):
    """A unit with fills linked to a plan, a plan-less buy, and an unfilled unit you wrote a note on earn a card; an
    unfilled unit without a note does not."""
    linked = plan_linked_unit(db, 'ZZZL')
    plain = scenario(db, 'ZZZU', 'unplanned')
    noted = scenario(db, 'ZZZN', 'expired')                         # the helper wrote a note on its baseline
    quiet = scenario(db, 'ZZZQ', 'expired')
    db.execute('DELETE FROM plan_notes WHERE ticker = %s', ['ZZZQ'])              # ... except this one: no note, no card
    out, summary, acc = run_stage(db, stage_deps(scripted_model, [draft_reply() for _ in range(3)]))
    found = {c['ticker']: c for c in cards(db)}
    assert set(found) == {'ZZZL', 'ZZZU', 'ZZZN'}
    assert (found['ZZZL']['subject'], found['ZZZL']['end_kind']) == ('unit', 'position_closed')
    assert found['ZZZL']['facts']['execution']['text'] == '采纳（照规则基线）'
    assert found['ZZZL']['facts']['execution']['legacy_decision'] is None
    assert (found['ZZZU']['subject'], found['ZZZU']['buy_trade_id']) == ('trade', plain['buy']['id'])
    assert (found['ZZZN']['subject'], found['ZZZN']['end_kind']) == ('unit', 'expired')
    assert found['ZZZN']['facts']['execution']['status'] == 'not_executed'
    assert [n['body'] for n in found['ZZZN']['facts']['execution']['notes']] == ['先观察，不急着做']
    assert all(c['retro_version'] == 'retro-2' for c in found.values())
    assert linked['plan'] is not None and noted['note'] is not None and quiet['plan'] is not None


def test_retro2_subject_for_a_fill_linked_to_a_merged_units_baseline(db, scripted_model):
    """D22: a fill linked to the baseline of a unit merged into a chain belongs to the chain's card."""
    signal_chain, signal_merged = D(-4), D(-3)
    chain_plan = make_plan(db, 'ZZZM', signal_chain)
    db.execute("UPDATE trade_plans SET kind = 'agent', plan_version = 'agent-3', entry_type = 'zone_limit', "
               'max_hold_sessions = 10 WHERE id = %s', [chain_plan['id']])
    run_id = uuid.uuid4()
    repository.insert_agent_run(db, {
        'id': run_id, 'ticker': 'ZZZM', 'signal_date': signal_chain, 'pool': 'main', 'attempt': 1, 'mode': 'propose',
        'agent_version': 'agent-3', 'provider': 'chatgpt', 'model': 'gpt-test', 'cutoff_at': at(D(-4)),
        'status': 'ok', 'reason': None, 'output': {'action': 'buy'}, 'counter': None, 'plan_id': chain_plan['id'],
        'input_tokens': 0, 'output_tokens': 0, 'started_at': at(D(-4)), 'finished_at': at(D(-4))}, [])
    repository.insert_plan_version(db, {
        'plan_id': chain_plan['id'], 'ticker': 'ZZZM', 'signal_date': signal_chain, 'version_no': 0,
        'supersedes_plan_id': None, 'origin': 'signal', 'agent_run_id': run_id, 'cutoff_at': at(D(-4)),
        'as_of_session': signal_chain, 'diff': None, 'schema_version': 'agent-3'})
    db.execute('UPDATE agent_plan_versions SET created_at = %s', [at(D(-4))])
    merged_plan = make_plan(db, 'ZZZM', signal_merged)
    repository.insert_chain_retrigger(db, {
        'ticker': 'ZZZM', 'unit_signal_date': signal_merged, 'chain_signal_date': signal_chain, 'chain_state': 'entry',
        'rule_ids': ['volume_breakout'], 'baseline_plan_id': merged_plan['id'], 'entry_cap': D(6), 'run_id': 'r',
        'timeline_version': 'timeline-2', 'created_at': at(D(-3))})
    make_trade(db, 'ZZZM', 'buy', 103, at(D(-2)), plan_id=merged_plan['id'])
    make_trade(db, 'ZZZM', 'sell', 106, at(D(0)))
    put_bars(db, 'ZZZM', flat(D(-8), D(0)))
    run_stage(db, stage_deps(scripted_model, [draft_reply()]))
    card = only_card(db)
    assert (card['ticker'], card['signal_date'], card['end_kind']) == ('ZZZM', signal_chain, 'position_closed')
    assert card['facts']['execution']['status'] == 'adopted' and card['facts']['execution']['plan_kind'] == 'rule'


def test_retro2_facts_execution_holding(db, scripted_model):
    """The eleven facts: `execution` (status, what you followed, the notes of the time, an old decision as is) and
    `holding` (the daily status rows summarised: the exit instruction and how long the sale took)."""
    s = plan_linked_unit(db, 'ZZZH', notes=('这笔想拿到 T1',))
    for index, (offset, instruction, reason) in enumerate(((-1, 'hold', None), (-0, 'exit', 'time_stop'))):
        repository.insert_holding_status(db, {
            'ticker': 'ZZZH', 'session': D(offset), 'run_id': 'r', 'quantity': 10, 'avg_cost': 103, 'held_sessions': index + 2,
            'max_hold_sessions': 10, 'instruction': instruction, 'exit_reason': reason, 'reason': '维持',
            'timeline_version': 'timeline-2'})
    out, summary, acc = run_stage(db, stage_deps(scripted_model, [draft_reply()]))
    facts = only_card(db)['facts']
    assert list(facts) == list(retro.FACT_KEYS_V2) or set(facts) == set(retro.FACT_KEYS_V2)
    execution = facts['execution']
    assert (execution['status'], execution['follow'], execution['text']) == ('adopted', 'rule', '采纳（照规则基线）')
    assert execution['plan_id'] == str(s['plan']['id']) and execution['trade_ids'] == [str(s['buy']['id'])]
    assert [n['body'] for n in execution['notes']] == ['这笔想拿到 T1'] and execution['legacy_decision'] is None
    assert execution['first_fill']['quantity'] == '10'
    holding = facts['holding']
    assert holding['rows'] == 2 and holding['exit'] == {'session': D(0).isoformat(), 'reason': 'time_stop'}
    assert holding['sell_date'] == D(0).isoformat() and holding['sessions_to_sell'] == 0
    assert holding['sold_before_exit'] is False
    assert [p['roles'] for p in facts['plans']] == [['reference', 'executed']]
    assert facts['tracks']['actual']['plan_kind'] == 'rule' and facts['tracks']['agent']['status'] != 'merged'
    # the prompt never carries a note body (D25)
    text = retro.facts_text_v2(facts)
    assert '这笔想拿到 T1' not in text and '[fact:execution]' in text and '[fact:holding]' in text


def test_retro1_cards_render_unchanged(db, scripted_model, capsys):
    """An old card (retro-1, the ten facts with `decision`) is shown, listed, checked and re-drafted exactly as it was:
    the card's own version picks its keys, its facts text and its prompt."""
    s = scenario(db, 'ZZZA')
    cache = retro_cards._Cache(db, SESSION, NOW)
    [subject] = [x for x in repository.retro_subjects(db, since=D(-4), until=SESSION) if x['ticker'] == 'ZZZA']
    item = retro_cards._evaluate(subject, cache, window_start=D(-4), known=lambda key: False)
    assert item['state'] == 'event' and not subject.get('v2')
    facts = retro_cards._freeze(db, {**item, 'subject': subject}, cache, session=SESSION, now=NOW)
    assert set(facts) == set(retro.FACT_KEYS) and facts['decision']['text'] == '采纳（照规则计划）'
    card_id = repository.insert_review_card(db, {
        'event_key': item['event']['event_key'], 'subject': 'unit', 'ticker': 'ZZZA', 'signal_date': s['signal_date'],
        'buy_trade_id': None, 'end_kind': item['event']['end_kind'], 'end_session': item['event']['end_session'],
        'retro_version': 'retro-1', 'facts': facts, 'detected_run_id': 'old'})
    db.commit()
    view = retro_cards.card_view(card_id, now=NOW)
    assert list(view['facts']) == list(retro.FACT_KEYS) and view['facts']['decision']['text'] == '采纳（照规则计划）'
    [row] = retro_cards.list_view(status=None, ticker=None, since=None)['cards']
    assert row['decision_text'] == '采纳（照规则计划）' and row['execution_text'] == '采纳（照规则计划）'
    code = retro_cards.main(['check', '--since', D(-4).isoformat(), '--until', SESSION.isoformat()])
    out = capsys.readouterr().out
    assert code == 0, out                                    # the old card is complete under its own ten keys
    assert f"齐全  {item['event']['event_key']}  #{card_id}" in out
    assert '事实不全 0' in out and '缺卡 0' in out and out.splitlines()[-1].endswith('→ 通过')
    # a re-draft of the old card still speaks the retro-1 language
    built = agent_deps(scripted_model, [draft_reply(review_ref='fact:decision')], settings=_agent_settings())
    result = retro_cards.draft_card(card_id, now=NOW, deps=built, trigger='manual')
    assert result['status'] == 'ok'
    run = repository.agent_run_by_id(db, result['agent_run_id'])
    assert run['agent_version'] == 'agent-retro-1'


def test_retro2_check_cli(db, scripted_model, capsys):
    """`check` knows a retro-2 card's eleven keys: complete cards pass, and one missing `holding` is reported."""
    plan_linked_unit(db, 'ZZZC')
    run_stage(db, stage_deps(scripted_model, [draft_reply()]))
    since = D(-4).isoformat()
    code, out = _run(capsys, 'check', '--since', since)
    assert code == 0 and '齐全' in out and '事实不全 0' in out
    card = only_card(db)
    broken = {k: v for k, v in card['facts'].items() if k != 'holding'}
    repository.insert_review_card(db, {
        'event_key': 'copy:broken', 'subject': 'unit', 'ticker': 'ZZZC', 'signal_date': card['signal_date'],
        'buy_trade_id': None, 'end_kind': 'position_closed', 'end_session': card['end_session'],
        'retro_version': 'retro-2', 'facts': broken, 'detected_run_id': 'x'})
    db.commit()
    code, out = _run(capsys, 'check', '--since', since)
    assert code == 1 and '事实不全  copy:broken  缺 holding' in out


def test_retro_cli_without_029_exits_2(db, capsys, monkeypatch):
    """M10: the review-card scan reads 029 tables; without them the command says migration_029_missing, exit code 2."""
    monkeypatch.setattr(repository, 'executable_ready', lambda conn: False)
    code = retro_cards.main(['check', '--since', D(-4).isoformat()])
    captured = capsys.readouterr()
    assert code == 2 and 'migration_029_missing' in captured.err
    with pytest.raises(service.Unavailable):
        retro_cards.list_view(status=None, ticker=None, since=None)
