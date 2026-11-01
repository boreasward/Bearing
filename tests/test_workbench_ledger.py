"""Trades and plan notes (v2.9 spec §6, R6, R12). There is no decision button any more: a fill follows a plan version,
brings its own levels or follows nothing; ``legacy_decisions`` keeps the old flow for the tests that need old data."""
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.core.dbutil as core_dbutil
import investment_assistant.core.schema as core_schema
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from investment_assistant.execution import ledger
from investment_assistant.analysis.rules import Candidate

T = date(2026, 10, 22)
NOW = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)
FILL = datetime(2026, 10, 23, 14, 0, tzinfo=UTC)         # 10:00 in New York on Friday 10-23


def trade_payload(**overrides):
    payload = {'request_id': str(uuid.uuid4()), 'ticker': 'NVDA', 'side': 'buy', 'quantity': '10',
               'price': '101.5', 'fee': '1', 'executed_at': FILL.isoformat()}
    payload.update(overrides)
    return payload


# --- validation (pure) ---

@pytest.mark.parametrize('overrides', [
    {'quantity': '0'}, {'price': '-1'}, {'fee': '-0.1'}, {'side': 'short'},
    {'executed_at': '2026-10-23T14:00:00'},          # naive time
    {'executed_at': '2026-10-24T14:00:00+00:00'},    # in the future relative to NOW
])
def test_invalid_trades(overrides):
    with pytest.raises(ledger.LedgerError):
        ledger.validate_trade(trade_payload(**overrides), now=NOW + timedelta(hours=14))


def test_request_hash_ignores_request_id_only():
    a = trade_payload()
    assert ledger.request_hash(a) == ledger.request_hash({**a, 'request_id': str(uuid.uuid4())})
    assert ledger.request_hash(a) != ledger.request_hash({**a, 'price': '101.6'})


def test_trade_hash_of_a_plain_fill_matches_the_pre_v29_hash():
    """A request recorded before v2.9 replays to the same hash: ``decision_id: None`` stays in the hashed body and
    ``plan_id`` / ``own_plan`` enter only when given."""
    rec = ledger.validate_trade(trade_payload(), now=NOW + timedelta(days=1))
    old_body = {k: v for k, v in rec.items() if k not in ('plan_id', 'own_plan', 'account_id')}
    old_body['decision_id'] = None
    assert ledger.trade_hash(rec) == ledger.request_hash(old_body)
    with_plan = ledger.validate_trade(trade_payload(plan_id=str(uuid.uuid4())), now=NOW + timedelta(days=1))
    assert ledger.trade_hash(with_plan) != ledger.trade_hash(rec)


def test_trades_reject_decision_id(workbench_db):
    payload = trade_payload(decision_id=str(uuid.uuid4()))
    with pytest.raises(ledger.LedgerError) as caught:
        ledger.record_trade(workbench_db, payload, now=NOW + timedelta(days=1))
    assert str(caught.value) == 'decision_link_retired: v2.9 起成交不再关联决定，请改为关联计划'
    assert workbench_db.execute('SELECT count(*) FROM trades').fetchone() == (0,)


def test_plan_id_and_own_plan_are_exclusive():
    with pytest.raises(ledger.LedgerError):
        ledger.validate_trade(trade_payload(plan_id=str(uuid.uuid4()),
                                            own_plan=own_levels()), now=NOW + timedelta(days=1))
    with pytest.raises(ledger.LedgerError):          # a sale needs no plan of its own
        ledger.validate_trade(trade_payload(side='sell', own_plan=own_levels()), now=NOW + timedelta(days=1))


@pytest.mark.parametrize('hold', [2, 31, True, '10', 7.5])
def test_own_plan_max_hold_range(hold):
    with pytest.raises(ledger.LedgerError, match='最长持有须为 3–30 个交易日'):
        ledger.validate_trade(trade_payload(own_plan=own_levels(max_hold_sessions=hold)), now=NOW + timedelta(days=1))


# --- persistence (disposable schema) ---

def plan_row(conn, **overrides):
    plan = {'ticker': 'NVDA', 'signal_date': T, 'kind': 'rule', 'based_on_id': None, 'plan_version': 'plan-2',
            'ref_price': Decimal('100'), 'atr': Decimal('2'), 'entry_low': Decimal('99'),
            'entry_high': Decimal('101'), 'stop': Decimal('94'), 'target1': Decimal('108'),
            'trail_atr_multiple': Decimal('3'), 'valid_from': date(2026, 10, 23), 'valid_until': date(2026, 10, 27),
            'sizing': {'status': 'no_equity'}, 'status': 'ok', 'invalid_reasons': [], 'warnings': [],
            'inputs': {'bar_ids': [1]}, 'request_id': None, 'request_hash': None,
            'entry_type': 'zone_limit', 'max_hold_sessions': 10}
    plan.update(overrides)
    return execution_repo.insert_plan(conn, plan)


def own_levels(**overrides):
    levels = {'entry_low': '98.5', 'entry_high': '101.5', 'stop': '94', 'target1': '112'}
    levels.update(overrides)
    return levels


def unit_bar(conn, day=T):
    conn.execute("INSERT INTO price_bars (ticker, price_date, open, high, low, close, volume, adj_close, "
                 "revision, fetched_at) VALUES ('NVDA', %s, 100, 102, 99, 100, 1000, 100, 1, %s)", [day, NOW])


def candidate(conn, day=T, ticker='NVDA'):
    analysis_repo.store_candidate(conn, ticker, day, Candidate('trend_reclaim', 'buy', {'signal_date': day.isoformat()}),
                               run_id='r')


def agent3_version(conn, version_no, supersedes, created_at, *, day=T, ticker='NVDA', **levels):
    """One agent-3 version the way the service writes it; ``created_at`` is set by hand (an UPDATE in the disposable
    schema), because R6 compares it with the moment of the fill."""
    plan = plan_row(conn, ticker=ticker, signal_date=day, kind='agent', plan_version='agent-3', **levels)
    run_id = uuid.uuid4()
    analysis_repo.insert_agent_run(conn, {
        'id': run_id, 'ticker': ticker, 'signal_date': day, 'pool': 'main', 'attempt': 1,
        'mode': 'propose' if version_no == 0 else 'revise', 'agent_version': 'agent-3', 'provider': 'chatgpt',
        'model': 'gpt-test', 'cutoff_at': created_at, 'status': 'ok', 'reason': None, 'output': {'action': 'buy'},
        'counter': None, 'plan_id': plan['id'], 'input_tokens': 0, 'output_tokens': 0,
        'started_at': created_at, 'finished_at': created_at}, [])
    execution_repo.insert_plan_version(conn, {
        'plan_id': plan['id'], 'ticker': ticker, 'signal_date': day, 'version_no': version_no,
        'supersedes_plan_id': supersedes, 'origin': 'signal' if version_no == 0 else 'review',
        'agent_run_id': run_id, 'cutoff_at': created_at, 'as_of_session': day,
        'diff': None if version_no == 0 else [{'field': 'stop', 'before': '94', 'after': '95'}],
        'schema_version': 'agent-3'})
    conn.execute('UPDATE agent_plan_versions SET created_at = %s WHERE plan_id = %s', (created_at, plan['id']))
    return plan


def test_trade_is_idempotent_per_request(workbench_db):
    payload = trade_payload()
    first = ledger.record_trade(workbench_db, payload, now=NOW + timedelta(days=1))
    again = ledger.record_trade(workbench_db, payload, now=NOW + timedelta(days=1))
    assert first['status'] == 'created' and again['status'] == 'existing'
    assert first['record']['decision_id'] is None and first['record']['plan_id'] is None
    with pytest.raises(ledger.Conflict):
        ledger.record_trade(workbench_db, {**payload, 'quantity': '11'}, now=NOW + timedelta(days=1))


def test_trades_link_plan(workbench_db):
    rule = plan_row(workbench_db)
    payload = trade_payload(plan_id=str(rule['id']))
    first = ledger.record_trade(workbench_db, payload, now=NOW + timedelta(days=1))
    assert first['status'] == 'created' and first['record']['plan_id'] == rule['id']
    assert first['record']['decision_id'] is None
    assert ledger.record_trade(workbench_db, payload, now=NOW + timedelta(days=1))['status'] == 'existing'
    other = plan_row(workbench_db, signal_date=T - timedelta(days=1))
    with pytest.raises(ledger.Conflict):         # the same request id with another plan is another request
        ledger.record_trade(workbench_db, {**payload, 'plan_id': str(other['id'])}, now=NOW + timedelta(days=1))


def test_trade_plan_must_belong_to_the_ticker(workbench_db):
    mu = plan_row(workbench_db, ticker='MU')
    with pytest.raises(ledger.LedgerError):
        ledger.record_trade(workbench_db, trade_payload(plan_id=str(mu['id'])), now=NOW + timedelta(days=1))
    with pytest.raises(ledger.LedgerError):
        ledger.record_trade(workbench_db, trade_payload(plan_id=str(uuid.uuid4())), now=NOW + timedelta(days=1))


def test_trades_stale_version_409(workbench_db):
    v0 = agent3_version(workbench_db, 0, None, FILL - timedelta(days=2))
    v1 = agent3_version(workbench_db, 1, v0['id'], FILL - timedelta(hours=6))
    with pytest.raises(ledger.Conflict) as caught:
        ledger.record_trade(workbench_db, trade_payload(plan_id=str(v0['id'])), now=NOW + timedelta(days=1))
    assert str(caught.value) == 'plan_stale: 成交时这条链已是第 1 版，请关联第 1 版'
    assert workbench_db.execute('SELECT count(*) FROM trades').fetchone() == (0,)
    ok = ledger.record_trade(workbench_db, trade_payload(plan_id=str(v1['id'])), now=NOW + timedelta(days=1))
    assert ok['record']['plan_id'] == v1['id']


def test_link_head_at_fill_time(workbench_db):
    """R6: back-filling yesterday's fill may link the version that was the head THEN, even when a newer one exists now."""
    v0 = agent3_version(workbench_db, 0, None, FILL - timedelta(days=1))
    v1 = agent3_version(workbench_db, 1, v0['id'], FILL + timedelta(days=1))          # generated after the fill
    now = FILL + timedelta(days=3)
    out = ledger.record_trade(workbench_db, trade_payload(plan_id=str(v0['id'])), now=now)
    assert out['record']['plan_id'] == v0['id']
    later = (FILL + timedelta(days=2)).isoformat()
    with pytest.raises(ledger.Conflict) as caught:               # a fill after v1 existed must follow v1
        ledger.record_trade(workbench_db, trade_payload(plan_id=str(v0['id']), executed_at=later, quantity='1'),
                            now=now)
    assert str(caught.value).startswith('plan_stale: 成交时这条链已是第 1 版')
    assert v1['id'] is not None


def test_plan_after_fill_409(workbench_db):
    v0 = agent3_version(workbench_db, 0, None, FILL + timedelta(hours=3))
    with pytest.raises(ledger.Conflict) as caught:
        ledger.record_trade(workbench_db, trade_payload(plan_id=str(v0['id'])), now=NOW + timedelta(days=1))
    assert str(caught.value) == 'plan_after_fill: 这一版生成于成交之后，不可能是你照着做的那一版'


def test_trades_without_029_refuse_a_plan_link(workbench_db, monkeypatch):
    import investment_assistant.core.tx as service

    monkeypatch.setattr(core_schema, 'executable_ready', lambda conn: False)
    with pytest.raises(service.Unavailable) as caught:
        ledger.record_trade(workbench_db, trade_payload(plan_id=str(uuid.uuid4())), now=NOW + timedelta(days=1))
    assert str(caught.value) == 'migration_029_missing'
    assert ledger.record_trade(workbench_db, trade_payload(), now=NOW + timedelta(days=1))['status'] == 'created'


def test_trades_own_plan_same_tx(workbench_db):
    candidate(workbench_db)
    unit_bar(workbench_db)
    payload = trade_payload(own_plan=own_levels())
    out = ledger.record_trade(workbench_db, payload, now=NOW + timedelta(days=1))
    record = out['record']
    plan = execution_repo.plan_by_id(workbench_db, record['plan_id'])
    assert plan['kind'] == 'own' and plan['plan_version'] == 'plan-2' and plan['signal_date'] == T
    assert (plan['entry_type'], plan['max_hold_sessions']) == ('zone_limit', 10)
    assert plan['valid_from'] == plan['valid_until'] == date(2026, 10, 23)        # the fill day
    assert plan['request_id'] == uuid.uuid5(uuid.UUID(payload['request_id']), 'own_plan')
    replay = ledger.record_trade(workbench_db, payload, now=NOW + timedelta(days=1))
    assert replay['status'] == 'existing' and replay['record']['plan_id'] == plan['id']
    assert workbench_db.execute("SELECT count(*) FROM trade_plans WHERE kind = 'own'").fetchone() == (1,)


def test_trades_own_plan_rolls_back_with_the_fill(workbench_db, monkeypatch):
    candidate(workbench_db)
    unit_bar(workbench_db)

    def broken(*args, **kwargs):
        raise RuntimeError('insert failed')
    monkeypatch.setattr(execution_repo, 'insert_trade', broken)
    with pytest.raises(RuntimeError):
        with workbench_db.transaction():
            ledger.record_trade(workbench_db, trade_payload(own_plan=own_levels()), now=NOW + timedelta(days=1))
    assert workbench_db.execute("SELECT count(*) FROM trade_plans WHERE kind = 'own'").fetchone() == (0,)


def test_own_plan_without_bars_is_400(workbench_db):
    with pytest.raises(ledger.LedgerError):
        ledger.record_trade(workbench_db, trade_payload(own_plan=own_levels()), now=NOW + timedelta(days=1))


def test_own_plan_signal_date_fallbacks(workbench_db):
    """R12: the unit of the active chain at the fill; else the latest unit with candidates in the 20 sessions before;
    else the fill day."""
    fill_day = date(2026, 10, 23)
    # 3) nothing known: the plan belongs to no unit, it is dated by the fill day
    assert ledger.own_plan_signal_date(workbench_db, 'NVDA', FILL) == fill_day
    # 2) candidates: the latest one before the fill day (older than 20 sessions does not count)
    candidate(workbench_db, date(2026, 9, 1))
    assert ledger.own_plan_signal_date(workbench_db, 'NVDA', FILL) == fill_day
    candidate(workbench_db, date(2026, 10, 15))
    candidate(workbench_db, date(2026, 10, 20))
    candidate(workbench_db, fill_day)                 # the fill day itself is not "before"
    assert ledger.own_plan_signal_date(workbench_db, 'NVDA', FILL) == date(2026, 10, 20)
    # 1) an active chain at the moment of the fill wins over the candidates
    agent3_version(workbench_db, 0, None, FILL - timedelta(days=3), day=date(2026, 10, 19),
                  valid_from=date(2026, 10, 20), valid_until=date(2026, 10, 27))
    assert ledger.own_plan_signal_date(workbench_db, 'NVDA', FILL) == date(2026, 10, 19)
    # ... but not before the chain existed
    assert ledger.own_plan_signal_date(workbench_db, 'NVDA', FILL - timedelta(days=5)) != date(2026, 10, 19)


def test_own_plan_signal_date_on_the_last_valid_day(workbench_db):
    """I1: a fill on the chain's ``valid_until`` is judged against the session before it, so the chain is still the
    active one; the day after it is not."""
    candidate(workbench_db, date(2026, 10, 20))
    fill = datetime(2026, 10, 27, 14, 0, tzinfo=UTC)                     # 10:00 New York on valid_until
    agent3_version(workbench_db, 0, None, datetime(2026, 10, 20, 1, 0, tzinfo=UTC), day=date(2026, 10, 19),
                  valid_from=date(2026, 10, 20), valid_until=date(2026, 10, 27))
    assert ledger.own_plan_signal_date(workbench_db, 'NVDA', fill) == date(2026, 10, 19)
    assert ledger.own_plan_signal_date(workbench_db, 'NVDA', fill + timedelta(days=1)) == date(2026, 10, 20)


def test_trade_without_signal_is_allowed(workbench_db):
    out = ledger.record_trade(workbench_db, trade_payload(), now=NOW + timedelta(days=1))
    assert out['record']['decision_id'] is None and out['record']['plan_id'] is None


def test_cannot_sell_more_than_held(workbench_db):
    later = NOW + timedelta(days=1)
    ledger.record_trade(workbench_db, trade_payload(quantity='5'), now=later)
    with pytest.raises(ledger.LedgerError):
        ledger.record_trade(workbench_db, trade_payload(side='sell', quantity='6',
                                                        executed_at='2026-10-23T15:00:00+00:00'), now=later)


def test_sale_may_link_a_plan_without_the_head_check(workbench_db):
    v0 = agent3_version(workbench_db, 0, None, FILL - timedelta(days=2))
    agent3_version(workbench_db, 1, v0['id'], FILL - timedelta(hours=6))
    later = NOW + timedelta(days=1)
    ledger.record_trade(workbench_db, trade_payload(quantity='5'), now=later)
    out = ledger.record_trade(workbench_db, trade_payload(side='sell', quantity='5', plan_id=str(v0['id']),
                                                          executed_at='2026-10-23T15:00:00+00:00'), now=later)
    assert out['record']['plan_id'] == v0['id']


def test_void_requires_reason_once_and_keeps_row(workbench_db):
    later = NOW + timedelta(days=1)
    t = ledger.record_trade(workbench_db, trade_payload(), now=later)['record']
    with pytest.raises(ledger.LedgerError):
        ledger.void_trade(workbench_db, t['id'], ' ', now=later)
    voided = ledger.void_trade(workbench_db, t['id'], '价格录错', now=later)
    assert voided['voided_at'] == later and voided['void_reason'] == '价格录错'
    with pytest.raises(ledger.Conflict):
        ledger.void_trade(workbench_db, t['id'], 'again', now=later)
    with pytest.raises(ledger.NotFound):
        ledger.void_trade(workbench_db, uuid.uuid4(), 'x', now=later)
    assert workbench_db.execute('SELECT count(*) FROM trades').fetchone() == (1,)


def test_void_refused_when_it_would_leave_a_sell_uncovered(workbench_db):
    later = NOW + timedelta(days=1)
    buy = ledger.record_trade(workbench_db, trade_payload(quantity='5'), now=later)['record']
    ledger.record_trade(workbench_db, trade_payload(side='sell', quantity='5',
                                                    executed_at='2026-10-23T15:00:00+00:00'), now=later)
    with pytest.raises(ledger.LedgerError):
        ledger.void_trade(workbench_db, buy['id'], '录错', now=later)


# --- plan notes (spec §6.5) ---

def note_payload(**overrides):
    payload = {'request_id': str(uuid.uuid4()), 'ticker': 'NVDA', 'body': '等回踩再看'}
    payload.update(overrides)
    return payload


def test_note_idempotent_correct_retract(workbench_db):
    v0 = agent3_version(workbench_db, 0, None, FILL - timedelta(days=1))
    base = note_payload(chain_signal_date=T.isoformat())
    first = ledger.record_note(workbench_db, base, now=NOW)
    assert first['status'] == 'created' and first['record']['body'] == '等回踩再看' and first['record']['kind'] == 'note'
    again = ledger.record_note(workbench_db, base, now=NOW)
    assert again['status'] == 'existing' and again['record']['id'] == first['record']['id']
    with pytest.raises(ledger.Conflict):
        ledger.record_note(workbench_db, {**base, 'body': '换了一句'}, now=NOW)
    # a correction is a new note pointing at the old one; a retraction a new row of kind 'retract'
    fix = ledger.record_note(workbench_db, note_payload(chain_signal_date=T.isoformat(), body='改成：突破再买',
                                                        supersedes_id=first['record']['id']), now=NOW)
    shown = execution_repo.plan_notes_for(workbench_db, 'NVDA', chain_signal_date=T)
    assert {n['id']: n['status'] for n in shown} == {first['record']['id']: 'corrected', fix['record']['id']: 'active'}
    gone = ledger.record_note(workbench_db, note_payload(kind='retract', chain_signal_date=T.isoformat(),
                                                         supersedes_id=fix['record']['id'], body=''), now=NOW)
    assert gone['record']['kind'] == 'retract'
    shown = execution_repo.plan_notes_for(workbench_db, 'NVDA', chain_signal_date=T)
    assert {n['status'] for n in shown} == {'corrected', 'retracted'}
    assert workbench_db.execute('SELECT count(*) FROM plan_notes').fetchone() == (3,)
    assert v0['id'] is not None


def test_note_on_a_non_chain_plan_and_scope_checks(workbench_db):
    rule = plan_row(workbench_db)
    out = ledger.record_note(workbench_db, note_payload(plan_id=str(rule['id'])), now=NOW)
    assert out['record']['plan_id'] == rule['id'] and out['record']['chain_signal_date'] is None
    v0 = agent3_version(workbench_db, 0, None, FILL - timedelta(days=1))
    with pytest.raises(ledger.LedgerError):          # a chain version takes its notes on the chain
        ledger.record_note(workbench_db, note_payload(plan_id=str(v0['id'])), now=NOW)
    with pytest.raises(ledger.LedgerError):          # exactly one scope
        ledger.record_note(workbench_db, note_payload(plan_id=str(rule['id']), chain_signal_date=T.isoformat()), now=NOW)
    with pytest.raises(ledger.LedgerError):
        ledger.record_note(workbench_db, note_payload(), now=NOW)
    with pytest.raises(ledger.NotFound):             # no chain there
        ledger.record_note(workbench_db, note_payload(chain_signal_date='2026-09-01'), now=NOW)
    with pytest.raises(ledger.LedgerError):          # a plan of another ticker
        ledger.record_note(workbench_db, note_payload(ticker='MU', plan_id=str(rule['id'])), now=NOW)


def test_note_body_limits_and_correction_scope(workbench_db):
    agent3_version(workbench_db, 0, None, FILL - timedelta(days=1))
    rule = plan_row(workbench_db, signal_date=T - timedelta(days=3))
    chain = ledger.record_note(workbench_db, note_payload(chain_signal_date=T.isoformat()), now=NOW)['record']
    with pytest.raises(ledger.LedgerError):
        ledger.record_note(workbench_db, note_payload(chain_signal_date=T.isoformat(), body='  '), now=NOW)
    with pytest.raises(ledger.LedgerError):
        ledger.record_note(workbench_db, note_payload(chain_signal_date=T.isoformat(), body='字' * 1001), now=NOW)
    ledger.record_note(workbench_db, note_payload(chain_signal_date=T.isoformat(), body='字' * 1000), now=NOW)
    with pytest.raises(ledger.LedgerError):          # a correction must point at a note of the same scope
        ledger.record_note(workbench_db, note_payload(plan_id=str(rule['id']), supersedes_id=chain['id']), now=NOW)
    with pytest.raises(ledger.LedgerError):
        ledger.record_note(workbench_db, note_payload(kind='retract', chain_signal_date=T.isoformat()), now=NOW)


def test_note_without_029_is_unavailable(workbench_db, monkeypatch):
    import investment_assistant.core.tx as service

    monkeypatch.setattr(core_schema, 'executable_ready', lambda conn: False)
    with pytest.raises(service.Unavailable):
        ledger.record_note(workbench_db, note_payload(chain_signal_date=T.isoformat()), now=NOW)


# --- decisions are retired (spec §6.4) ---

def test_no_insert_decision_outside_tests():
    """Review Focus 6: production code never writes ``signal_decisions``; the private repository writer is only
    defined in the repository and used by the fixtures under tests/."""
    root = Path(__file__).resolve().parents[1] / 'investment_assistant'
    users = [str(path.relative_to(root)) for path in root.rglob('*.py')
             if '_insert_decision_for_tests' in path.read_text(encoding='utf-8')]
    assert users == ['execution/repo_legacy.py']
    assert not hasattr(ledger, 'record_decision') and not hasattr(ledger, 'validate_decision')
    for path in root.rglob('*.py'):
        text = path.read_text(encoding='utf-8')
        assert 'INSERT INTO signal_decisions' not in text or path == root / 'execution' / 'repo_legacy.py', path


def test_explicit_real_account_preserves_old_request_hash():
    from investment_assistant.execution.accounts import REAL_ACCOUNT_ID
    payload=trade_payload()
    old=ledger.validate_trade(payload,now=NOW+timedelta(days=1))
    explicit=ledger.validate_trade({**payload,'account_id':str(REAL_ACCOUNT_ID)},now=NOW+timedelta(days=1))
    assert ledger.trade_hash(old)==ledger.trade_hash(explicit)
    nonreal={**old,'account_id':uuid.uuid4()}
    assert ledger.trade_hash(nonreal)!=ledger.trade_hash(old)


def test_snapshot_and_flow_reject_paper_account_before_writing():
    from investment_assistant.execution import account_records as account
    for fn in (account.record_snapshot,account.record_flow):
        with pytest.raises(ledger.LedgerError,match='flows_only_for_real_account'):
            fn(None,{'account_id':str(uuid.uuid4())},now=NOW)


def _fake_real_record_write(monkeypatch, kind, *, opened_on=T, ready=True, existing=None):
    """Exercise command validation and lock order without opening any database connection."""
    from investment_assistant.execution import account_records as account
    events = []
    def lock(conn, key):
        events.append(('lock', key))
    def replay(conn, request_id):
        events.append(('replay', request_id))
        return existing
    def read_account(conn, account_id):
        assert ready, 'pre-030 must never query accounts'
        events.append(('account', account_id))
        return {'id': account_id, 'opened_on': opened_on}
    def insert(conn, record, digest, *, recorded_at):
        events.append(('insert', record))
        return {**record, 'id': uuid.uuid4(), 'request_hash': digest}
    monkeypatch.setattr(core_dbutil, 'lock', lock)
    monkeypatch.setattr(core_schema, 'accounts_ready', lambda conn: ready)
    monkeypatch.setattr(execution_repo, 'account_by_id', read_account)
    monkeypatch.setattr(execution_repo, f'{kind}_by_request', replay)
    monkeypatch.setattr(execution_repo, f'insert_{kind}', insert)
    if kind == 'trade':
        monkeypatch.setattr(execution_repo, 'list_trades', lambda *args, **kwargs: [])
        monkeypatch.setattr(data_repo, 'split_events', lambda *args, **kwargs: [])
    return ledger.record_trade if kind == 'trade' else account.record_flow, events


@pytest.mark.parametrize('kind, value, accepted', [
    ('trade', '2026-10-22T03:59:59+00:00', False),  # still 10-21 in New York
    ('trade', '2026-10-22T04:00:00+00:00', True),
    ('trade', '2026-10-23T14:00:00+00:00', True),
    ('flow', '2026-10-21', False), ('flow', '2026-10-22', True), ('flow', '2026-10-23', True),
])
def test_new_real_record_respects_opening_date_after_account_lock(monkeypatch, kind, value, accepted):
    fn, events = _fake_real_record_write(monkeypatch, kind)
    payload = trade_payload(executed_at=value) if kind == 'trade' else {
        'request_id': str(uuid.uuid4()), 'flow_date': value, 'amount': '500'}
    if accepted:
        assert fn(None, payload, now=NOW+timedelta(days=1))['status'] == 'created'
    else:
        with pytest.raises(ledger.LedgerError, match='^record_before_opened_on$'):
            fn(None, payload, now=NOW+timedelta(days=1))
        assert not any(event[0] == 'insert' for event in events)
    assert events[:2] == [('lock', f'account:{execution_repo.REAL_ACCOUNT_ID}'),
                          ('lock', 'trades:NVDA' if kind == 'trade' else 'account:flows')]
    assert events[2][0] == 'replay' and events[3] == ('account', execution_repo.REAL_ACCOUNT_ID)


@pytest.mark.parametrize('kind', ['trade', 'flow'])
def test_real_record_exact_replay_precedes_new_opening_date_check(monkeypatch, kind):
    from investment_assistant.execution import account_records as account
    payload = trade_payload(executed_at='2026-10-21T14:00:00+00:00') if kind == 'trade' else {
        'request_id': str(uuid.uuid4()), 'flow_date': '2026-10-21', 'amount': '500'}
    record = ledger.validate_trade(payload, now=NOW) if kind == 'trade' else account._validate_flow(payload)
    digest = ledger.trade_hash(record) if kind == 'trade' else ledger.request_hash(record)
    existing = {**record, 'id': uuid.uuid4(), 'request_hash': digest, 'voided_at': NOW}
    fn, events = _fake_real_record_write(monkeypatch, kind, existing=existing)
    assert fn(None, payload, now=NOW) == {'status': 'existing', 'record': existing}
    assert not any(event[0] in ('account', 'insert') for event in events)


@pytest.mark.parametrize('kind', ['trade', 'flow'])
@pytest.mark.parametrize('ready', [False, True])
def test_unopened_and_pre030_records_keep_legacy_dates(monkeypatch, kind, ready):
    fn, _ = _fake_real_record_write(monkeypatch, kind, ready=ready, opened_on=None)
    payload = trade_payload(executed_at='2020-01-02T14:00:00+00:00') if kind == 'trade' else {
        'request_id': str(uuid.uuid4()), 'flow_date': '2020-01-02', 'amount': '500'}
    assert fn(None, payload, now=NOW)['status'] == 'created'
