"""The v2.9 read views (spec §9.1, §10, D34, D35): the plan table, the plan history, the holding-status rows and the
plans a fill may follow. Built on the plan-review harness (fake fetchers, routed scripted model, `now` advancing by
session) and a disposable schema; read-only, no network."""
import investment_assistant.review.weekly as review_weekly
from investment_assistant.execution import chains as execution_chains
from investment_assistant.execution import trades as execution_trades
import investment_assistant.core.schema as core_schema
import investment_assistant.execution.repo as execution_repo
import investment_assistant.review.repo as review_repo
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from test_workbench_executable_flow import exit_review, fill, merge, renewal, spike
from test_workbench_plan_review import S1, Market, counter, day, evening, make_model, proposal, run, signal_day, verdict
from test_workbench_service import db   # noqa: F401  (fixture: service wired to the disposable schema)
from investment_assistant.data.calendar import trading_days_between
import investment_assistant.workflow.nodes.review as plan_review
import investment_assistant.views.plan_table as plan_views
from investment_assistant.analysis import pool as pool_module
import investment_assistant.core.tx as service

NY = ZoneInfo('America/New_York')


def table(db, k, *, now_k=None, **kwargs):
    """The plan table of session day(k), read at the evening of day(now_k) (default k)."""
    return plan_views.plan_table(db, session=day(k), now=evening(day(k if now_k is None else now_k)), **kwargs)


def retime(db, *, notes_at=None):
    """The harness runs on synthetic dates but the database stamps rows with the real clock; the history and the link
    list compare record times with the fill, so move versions and reviews to the cutoff of the run that wrote them."""
    db.execute('UPDATE agent_plan_versions SET created_at = cutoff_at')
    db.execute('UPDATE plan_reviews SET created_at = cutoff_at')
    if notes_at is not None:
        db.execute('UPDATE plan_notes SET created_at = %s', [notes_at])


def rows_of(view, group):
    return view['groups'][group]


def entry_chain(db, market, ticker='SYNTH'):
    return signal_day(db, market, [ticker])[0][ticker]


def holding_chain(db, market, *, fill_k=1):
    """A chain with a fill linked to its first version, holding from day(fill_k); the review of day(fill_k + 1) is the
    first with a holding_status row."""
    v0 = entry_chain(db, market)
    fill(db, 'SYNTH', '10', '125', fill_k, v0['plan']['id'])
    return v0


# ---------------------------------------------------------------- the plan table

def test_plan_table_row_shape_exact(db):
    market = Market()
    v0 = entry_chain(db, market)
    view = table(db, 0)
    assert set(view) == {'session', 'groups', 'other_units', 'counts', 'errors'}
    assert set(view['groups']) == {'entry', 'holding', 'ended'}
    assert view['session'] == day(0) and view['counts'] == {'entry': 1, 'holding': 0, 'ended': 0}
    assert view['errors'] == {} and view['other_units'] == []
    [row] = rows_of(view, 'entry')
    plan_id = str(v0['plan']['id'])
    assert set(row) == {'row_id', 'group', 'ticker', 'pool', 'chain', 'plan', 'baseline', 'open_scenarios', 'void_when',
                        'status', 'holding', 'notes', 'links'}
    assert (row['row_id'], row['group'], row['ticker'], row['pool']) == (f'chain:SYNTH:{S1}', 'entry', 'SYNTH', 'core')
    assert row['chain'] == {'signal_date': S1, 'version_no': 0, 'head_plan_id': plan_id, 'state': 'entry',
                            'label': 'SYNTH·09-01 第 0 版', 'retriggers': []}
    assert row['plan'] == {
        'plan_id': plan_id, 'kind': 'agent', 'plan_version': 'agent-3', 'action': 'buy', 'entry_type': 'zone_limit',
        'entry_type_text': '区间限价', 'entry_low': '120.00', 'entry_high': '128.00', 'stop': '115.00',
        'target1': '140.00', 'shares': None, 'shares_note': '无账户权益快照，未算股数', 'valid_from': day(1),
        'valid_until': day(3), 'max_hold_sessions': 10, 'status': 'ok', 'defaults_applied': False}
    assert [(s['case'], s['action']) for s in row['open_scenarios']] == [
        ('in_zone', 'buy_open'), ('gap_up', 'limit_upper'), ('gap_down', 'stop_buy_lower')]
    assert row['open_scenarios'][1]['text'] == '高开过 128.00：不追，挂 128.00 限价等回落'
    assert row['open_scenarios'][2]['text'] == '低开：≤ 115.00 作废；否则回升到 120.00 时买入'
    assert [(v['source'], v['text']) for v in row['void_when']] == [
        ('validity', f'有效期至 {day(3):%m-%d} 收盘仍未成交'), ('gap', '开盘 ≤ 115.00'),
        ('invalidation', '收盘跌破 112.00'), ('max_hold', '持仓后：持有 10 个交易日仍未到 1R → 次日开盘卖出'),
        ('stop', '持仓后：收盘前触及止损 115.00（T1 之后为移动止损）')]
    assert row['void_when'][2]['last_result'] is None
    assert row['status'] == {'label': '入场期', 'latest_review': None, 'today_signals': [], 'execution': 'pending'}
    assert row['holding'] is None and row['notes'] == []
    assert row['links'] == {'plan': f'#plan/{plan_id}', 'instrument': '#instrument/SYNTH'}
    baseline = row['baseline']
    assert set(baseline) == {'plan_id', 'plan_version', 'entry_low', 'entry_high', 'stop', 'target1', 'shares', 'status',
                             'active_chain'}
    assert baseline['plan_version'] == 'plan-2' and baseline['active_chain'] is False


def test_plan_table_groups_and_sort(db):
    """Entry rows by pool (core, screen, extended) then ticker; holding rows by instruction then ticker; ended rows by
    the day they ended, newest first."""
    market = Market()
    db.execute('INSERT INTO pool_tiers (ticker, tier, effective_session, recorded_at) '
               "VALUES ('SYNA', 'extended', %s, %s)", [S1 - timedelta(days=1), evening(S1) - timedelta(days=1)])
    pool = pool_module.Pool(core=('SYNB', 'SYNTH'), extended=('SYNA',))
    signal_day(db, market, None, pool=pool)
    view = table(db, 0, include_extended=True)
    assert [(r['ticker'], r['pool']) for r in rows_of(view, 'entry')] == [
        ('SYNB', 'core'), ('SYNTH', 'core'), ('SYNA', 'extended')]
    assert [r['ticker'] for r in rows_of(table(db, 0), 'entry')] == ['SYNB', 'SYNTH']      # extended pool is opt-in


def test_plan_table_holding_and_ended_sort(db):
    """Holding rows by instruction (exit first), ended rows by the day they ended, newest first."""
    market = Market()
    signal_day(db, market, ['SYNB', 'SYNTH'])                              # two chains, both opened on day(0)
    for ticker in ('SYNB', 'SYNTH'):
        fill(db, ticker, '10', '125', 1, execution_repo.chain_head(db, ticker, S1)['plan']['id'])
    for ticker, instruction, reason in (('SYNB', 'hold', None), ('SYNTH', 'exit', 'time_stop')):
        execution_repo.insert_holding_status(db, {
            'ticker': ticker, 'session': day(2), 'chain_signal_date': S1, 'run_id': 'r', 'quantity': 10,
            'avg_cost': 125, 'held_sessions': 2, 'instruction': instruction, 'exit_reason': reason,
            'reason': '维持', 'timeline_version': 'timeline-2'})
    rows = rows_of(table(db, 2), 'holding')
    assert [(r['ticker'], r['holding']['instruction']) for r in rows] == [('SYNTH', 'exit'), ('SYNB', 'hold')]


def test_plan_table_ended_rows_newest_first(db):
    market = Market()
    entry_chain(db, market)                                                # SYNTH: opened day(0), expired day(3)
    spike(market, 'SYNB', 2)
    run(db, market, 2, tickers=['SYNB'], model=make_model(propose=proposal('SYNB'), counter=counter()))
    retime(db)
    ended = rows_of(table(db, 6), 'ended')                                 # SYNB's chain opened later, ended later
    assert [(r['ticker'], r['ended_at']) for r in ended] == [('SYNB', day(5)), ('SYNTH', day(3))]


def test_closed_chain_is_an_ended_row_with_its_r(db):
    market = Market()
    holding_chain(db, market)
    sold = execution_repo.insert_trade(db, {
        'request_id': uuid.uuid4(), 'ticker': 'SYNTH', 'side': 'sell', 'quantity': 10, 'price': 130, 'fee': 0,
        'executed_at': datetime.combine(day(2), time(10, 0), tzinfo=NY), 'decision_id': None, 'plan_id': None,
        'note': None}, 'h', recorded_at=evening(day(2)))
    db.commit()
    view = table(db, 3)
    assert view['counts'] == {'entry': 0, 'holding': 0, 'ended': 1}
    [row] = rows_of(view, 'ended')
    assert row['chain']['state'] == 'position_closed' and row['ended_at'] == day(2)
    assert row['status']['label'] == '已平仓（+0.5R）'            # (130 - 125) / (125 - 115): the stop of the first fill's plan
    assert row['status']['execution'] == 'executed' and row['status']['execution_basis'] == '照 Agent 第 0 版'
    history = plan_views.plan_history(db, 'SYNTH', now=evening(day(3)))
    [end] = [r for r in history['rows'] if r['kind'] == 'chain_end']
    assert end['text'] == '已平仓（实际 +0.5R）' and end['session'] == day(2)
    assert sold['id'] is not None


def test_other_units_lists_the_watch_units(db):
    """Sell / watch / conflict units are not rows: they are the read-only 「其他触发」 lines."""
    market = Market()
    market.filings.append({'ticker': 'SYNF', 'form': '8-K', 'filing_date': S1.isoformat(), 'items': ['8.01'],
                           'accepted_at': f'{S1.isoformat()}T08:00:00-04:00',
                           'edgar_url': 'https://www.sec.gov/Archives/edgar/data/0/synf-8k.htm'})
    run(db, market, 0, tickers=['SYNF'], model=make_model())
    view = table(db, 0)
    assert view['counts'] == {'entry': 0, 'holding': 0, 'ended': 0}
    [unit] = view['other_units']
    assert (unit['ticker'], unit['signal_date'], unit['proposed_action']) == ('SYNF', S1, 'watch')
    assert unit['rule_ids'] == ['new_filing'] and unit['labels'] == []
    assert set(unit) == {'ticker', 'signal_date', 'proposed_action', 'labels', 'rule_ids', 'brief'}


def test_include_extended(db):
    market = Market()
    db.execute('INSERT INTO pool_tiers (ticker, tier, effective_session, recorded_at) '
               "VALUES ('SYNA', 'extended', %s, %s)", [S1 - timedelta(days=1), evening(S1) - timedelta(days=1)])
    pool = pool_module.Pool(core=('SYNTH',), extended=('SYNA',))
    signal_day(db, market, None, pool=pool)
    assert [r['ticker'] for r in rows_of(table(db, 0), 'entry')] == ['SYNTH']
    with_extended = table(db, 0, include_extended=True)
    assert [r['ticker'] for r in rows_of(with_extended, 'entry')] == ['SYNTH', 'SYNA']
    assert with_extended['counts']['entry'] == 2 and table(db, 0)['counts']['entry'] == 1


def test_ended_window_five_sessions(db):
    """D35: only chains that ended within the last 5 sessions are listed; older ones belong to the plan history."""
    market = Market()
    entry_chain(db, market)
    retime(db)
    expired_on = day(3)
    assert rows_of(table(db, 2), 'ended') == []                                # still in the entry phase
    [row] = rows_of(table(db, 3), 'ended')
    assert row['status']['label'] == '已过期（未执行）' and row['ended_at'] == expired_on
    assert row['group'] == 'ended' and row['open_scenarios'] == [] and row['chain']['state'] == 'expired'
    assert rows_of(table(db, 3), 'entry') == []
    assert [r['ended_at'] for r in rows_of(table(db, 8, now_k=12), 'ended')] == [expired_on]     # 5 sessions later
    assert rows_of(table(db, 9, now_k=12), 'ended') == []                       # the 6th: gone from 「今日」


def test_rule_only_row_when_agent_failed(db):
    """A buy unit with no chain (the Agent gave no plan) is a row of its own: the rule baseline is the plan and the
    label says why."""
    market = Market()
    summary, rows, _ = run(db, market, 0, tickers=['SYNTH'], model=make_model(
        propose=proposal('SYNTH', action='pass'), counter=counter()))
    view = table(db, 0)
    [row] = rows_of(view, 'entry')
    assert row['chain'] is None and row['baseline'] is None and row['row_id'] == f'unit:SYNTH:{S1}'
    assert row['plan']['kind'] == 'rule' and row['plan']['plan_version'] == 'plan-2'
    assert row['status']['label'].startswith('Agent 判断不建议买入（pass）')
    assert row['status']['execution'] == 'pending'
    assert [s['case'] for s in row['open_scenarios']] == ['in_zone', 'gap_up', 'gap_down']
    assert [v['source'] for v in row['void_when']] == ['validity', 'gap', 'max_hold', 'stop']       # a baseline has no conditions
    assert view['counts']['entry'] == 1


def test_rule_only_row_when_the_agent_is_unavailable(db):
    from test_workbench_plan_review import settings

    market = Market()
    run(db, market, 0, tickers=['SYNTH'], agent=settings(provider='off'))
    [row] = rows_of(table(db, 0), 'entry')
    assert row['chain'] is None and row['status']['label'].startswith('Agent 未给出计划：')
    assert row['plan']['kind'] == 'rule'


def test_retrigger_shown_in_chain_row_not_separate(db):
    """A unit merged into an active chain is a line inside the chain's row, never a row of its own."""
    market = Market()
    entry_chain(db, market)
    spike(market, 'SYNTH', 1)
    summary, rows, model = run(db, market, 1, tickers=['SYNTH'], model=make_model(
        revise=renewal('SYNTH'), counter=counter()))
    [merge_row] = execution_repo.chain_retriggers_for(db, 'SYNTH')
    view = table(db, 1)
    [row] = rows_of(view, 'entry')
    assert len(view['groups']['entry']) == 1 and view['other_units'] == []
    [trig] = row['chain']['retriggers']
    assert trig['unit_signal_date'] == day(1) and trig['rule_ids'] == ['volume_breakout']
    assert trig['version_no'] == row['chain']['version_no'] == 1                # the merge produced version 1
    assert row['status']['label'] == '入场期 · 今日修订（再次触发）'
    assert row['status']['today_signals'] == ['retrigger']
    assert row['status']['latest_review']['via'] == 'retrigger'
    assert row['status']['diff'] and all('field' in d for d in row['status']['diff'])
    assert row['baseline']['active_chain'] == {'ticker': 'SYNTH', 'signal_date': S1.isoformat()}
    assert row['baseline']['plan_id'] == str(merge_row['baseline_plan_id'])


def test_holding_row_has_status_and_instruction(db):
    market = Market()
    holding_chain(db, market)
    run(db, market, 2, tickers=['SYNTH'], model=make_model(review=verdict('SYNTH', 'maintain', '趋势未变', cite=False)))
    view = table(db, 2)
    assert view['counts'] == {'entry': 0, 'holding': 1, 'ended': 0}
    [row] = rows_of(view, 'holding')
    assert row['group'] == 'holding' and row['chain']['state'] == 'holding' and row['open_scenarios'] == []
    holding = row['holding']
    assert holding['instruction'] == 'hold' and holding['ticker'] == 'SYNTH' and holding['session'] == day(2)
    assert holding['reason'] == '趋势未变' and 'stale' not in holding
    assert holding['held_sessions'] == 2 and holding['max_hold_sessions'] == 10
    assert row['status']['label'] == '持仓期' and row['status']['execution'] == 'executed'
    assert row['status']['execution_basis'] == '照 Agent 第 0 版'
    assert row['status']['execution_detail'] == {'date': f'{day(1):%m-%d}', 'quantity': '10'}
    assert [v['source'] for v in row['void_when']] == ['invalidation', 'max_hold', 'stop']        # no entry-only lines
    assert row['void_when'][1]['text'] == '持有 10 个交易日仍未到 1R → 次日开盘卖出'


def test_exiting_holding_row_sorts_first(db):
    market = Market()
    v0 = holding_chain(db, market)
    run(db, market, 2, tickers=['SYNTH'], model=make_model(review=verdict('SYNTH', 'maintain', 'ok', cite=False)))
    exit_review(db, v0, 3)
    view = table(db, 3)
    [row] = rows_of(view, 'holding')
    assert row['chain']['state'] == 'exiting' and row['status']['label'].startswith('退出期 · 退出指令自 ')


def test_plan_table_stale_holding_rows(db):
    """R18: a holding with no holding_status row today (the review stage failed as a whole) is computed on the fly
    and marked stale; the computed row is never stored."""
    market = Market()
    holding_chain(db, market)
    assert execution_repo.holding_status_for(db, session=day(2)) == []
    [row] = rows_of(table(db, 2), 'holding')
    assert row['holding']['stale'] is True and row['holding']['instruction'] == 'hold'
    assert row['holding']['session'] == day(2) and row['holding']['held_sessions'] == 2
    assert execution_repo.holding_status_for(db, session=day(2)) == []             # nothing was written
    run(db, market, 2, tickers=['SYNTH'], model=make_model(review=verdict('SYNTH', 'maintain', 'ok', cite=False)))
    [stored] = rows_of(table(db, 2), 'holding')
    assert 'stale' not in stored['holding']                                      # the written row wins


def test_a_failing_ticker_leaves_no_partial_rows(db, monkeypatch):
    """M7: rows of a ticker that failed half way are dropped with its savepoint; the failure goes to ``errors`` and the
    position still shows, computed by the holding section."""
    market = Market()
    holding_chain(db, market)
    real_items, real_row, calls = execution_chains.ticker_chain_items, plan_views._chain_row, []

    def two_items(*args, **kwargs):
        items = real_items(*args, **kwargs)
        return [*items, items[0]]                                   # a second chain row to build after the first

    def second_fails(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError('boom')
        return real_row(*args, **kwargs)

    monkeypatch.setattr(execution_chains, 'ticker_chain_items', two_items)
    monkeypatch.setattr(plan_views, '_chain_row', second_fails)
    view = table(db, 2)
    assert view['errors'] == {'SYNTH': 'boom'} and len(calls) == 2
    assert [r['row_id'] for r in rows_of(view, 'holding')] == ['holding:SYNTH']          # computed, not the half row
    assert rows_of(view, 'holding')[0]['status']['label'].startswith('无 Agent 链')
    assert view['counts'] == {'entry': 0, 'holding': 1, 'ended': 0}


def test_renewal_day_without_a_new_version_cannot_be_executed_tomorrow(db):
    """M8: on ``valid_until`` a trigger keeps the chain in its entry period (R5), but when the renewal made no new
    version the plan is out of date tomorrow: the row says so and offers no opening cases."""
    market = Market()
    v0 = entry_chain(db, market)                                    # valid_until = day(3)
    merge(db, 3)                                                    # a trigger on the last valid day, no new version
    [row] = rows_of(table(db, 3), 'entry')
    assert row['chain']['state'] == 'entry' and row['plan']['valid_until'] == day(3)
    assert row['status']['label'] == '入场期 · 再次触发未产生新版，本计划已到截止日，明日不可执行'
    assert row['open_scenarios'] == []
    [earlier] = rows_of(table(db, 2), 'entry')                      # a day earlier the plan is as usual
    assert earlier['open_scenarios'] and earlier['status']['label'] == '入场期'
    assert v0['plan']['id'] is not None


def test_position_without_a_chain_is_a_holding_row(db):
    """A position whose fills follow a rule baseline (no chain) still shows in 「今日」 with the host's status."""
    market = Market()
    run(db, market, 0, tickers=['SYNF'], model=make_model())            # SYNF: flat path, no candidates
    from test_workbench_ledger import plan_row

    baseline = plan_row(db, ticker='SYNF', signal_date=day(0), valid_from=day(1), valid_until=day(3),
                        entry_low=95, entry_high=105, stop=90, target1=120, atr=2)
    fill(db, 'SYNF', '10', '100', 1, baseline['id'])
    view = table(db, 2)
    [row] = rows_of(view, 'holding')
    assert row['chain'] is None and row['ticker'] == 'SYNF' and row['row_id'] == 'holding:SYNF'
    assert row['plan']['kind'] == 'rule' and row['status']['label'] == '无 Agent 链，按规则基线看护'
    assert row['holding']['stale'] is True and row['holding']['instruction'] == 'hold'


def test_views_503_without_029(db, monkeypatch):
    monkeypatch.setattr(core_schema, 'executable_ready', lambda conn: False)
    for call in (lambda: plan_views.plan_table(db, session=day(0), now=evening(day(0))),
                 lambda: plan_views.plan_history(db, 'SYNTH', now=evening(day(0))),
                 lambda: plan_views.holding_status_view(db, session=day(0), now=evening(day(0))),
                 lambda: plan_views.linkable_plans(db, 'SYNTH', executed_at=evening(day(0)))):
        with pytest.raises(plan_views.NotReady) as caught:
            call()
        assert str(caught.value) == 'migration_029_missing' and isinstance(caught.value, service.Unavailable)


# ---------------------------------------------------------------- the plan history

def test_plan_history_two_chains_order_and_chain_open_text(db):
    """Two chains of one ticker in order; the second chain's chain_open row says how the first one ended."""
    market = Market()
    entry_chain(db, market)                                              # chain A: SYNTH·09-01, expires unfilled
    spike(market, 'SYNTH', 8)                                            # a new buy signal once A has expired
    model = make_model(propose=proposal('SYNTH'), counter=counter())
    run(db, market, 8, tickers=['SYNTH'], model=model)                   # chain B opens on day(8)
    retime(db)
    view = plan_views.plan_history(db, 'SYNTH', now=evening(day(8)))
    assert view['ticker'] == 'SYNTH'
    assert [(c['signal_date'], c['state'], c['versions']) for c in view['chains']] == [
        (S1, 'expired', 1), (day(8), 'entry', 1)]
    assert view['chains'][0]['end'] == {'kind': 'expired', 'text': '已过期（未执行）'}
    assert view['chains'][1]['end'] is None
    kinds = [(r['kind'], r['chain_signal_date']) for r in view['rows']]
    assert kinds == [('chain_open', S1), ('version', S1), ('chain_end', S1), ('chain_open', day(8)), ('version', day(8))]
    first, _, end, second, _ = view['rows']
    assert first['text'].startswith('新开链 SYNTH·09-01：规则 ') and first['text'].endswith('此前没有计划链')
    assert end['text'] == '已过期（未执行）' and end['end'] == {'kind': 'expired', 'text': '已过期（未执行）'}
    gap = len(trading_days_between(day(3), day(8))) - 1
    assert second['text'].endswith(f'上一条链 SYNTH·09-01 已于 {day(3):%m-%d} 过期未执行，间隔 {gap} 个交易日')
    assert [r['at'] for r in view['rows']] == sorted(r['at'] for r in view['rows'])
    version = view['rows'][1]
    assert set(version) >= {'kind', 'at', 'session', 'chain_signal_date', 'version_no', 'levels', 'validity', 'diff',
                            'changes', 'text'}
    assert version['levels'] == {'entry_low': '120.00', 'entry_high': '128.00', 'stop': '115.00', 'target1': '140.00'}


def test_plan_history_retrigger_version_and_fill_rows(db):
    market = Market()
    v0 = entry_chain(db, market)
    spike(market, 'SYNTH', 1)
    run(db, market, 1, tickers=['SYNTH'], model=make_model(revise=renewal('SYNTH'), counter=counter()))
    fill(db, 'SYNTH', '10', '125', 2, execution_repo.chain_head(db, 'SYNTH', S1)['plan']['id'])
    retime(db)
    view = plan_views.plan_history(db, 'SYNTH', now=evening(day(2)))
    kinds = [r['kind'] for r in view['rows']]
    assert kinds.count('retrigger') == 1 and kinds.count('fill') == 1 and kinds.count('version') == 2
    retrigger = next(r for r in view['rows'] if r['kind'] == 'retrigger')
    assert retrigger['version_no'] == 1 and retrigger['signals_fired'] == ['retrigger']
    assert retrigger['text'].startswith(f'{day(1):%m-%d} 再次触发（') and retrigger['text'].endswith('产出第 1 版')
    fill_row = next(r for r in view['rows'] if r['kind'] == 'fill')
    assert fill_row['chain_signal_date'] == S1 and '买入 10 股 @ 125.00，关联 Agent SYNTH·09-01 第 1 版' in fill_row['text']
    assert v0['plan']['id'] is not None


def test_plan_history_toggles(db):
    """include_reviews adds the daily maintain reviews, include_baselines the rule baselines."""
    market = Market()
    entry_chain(db, market)
    run(db, market, 1, tickers=['SYNTH'], model=make_model(review=verdict('SYNTH', 'maintain', '每天维持', cite=False)))
    retime(db)
    plain = plan_views.plan_history(db, 'SYNTH', now=evening(day(1)))
    assert 'review' not in [r['kind'] for r in plain['rows']]
    with_reviews = plan_views.plan_history(db, 'SYNTH', include_reviews=True, now=evening(day(1)))
    [review_row] = [r for r in with_reviews['rows'] if r['kind'] == 'review']
    assert review_row['reason'] == '每天维持' and review_row['decided_by'] == 'agent'
    assert len(with_reviews['rows']) == len(plain['rows']) + 1
    with_baselines = plan_views.plan_history(db, 'SYNTH', include_baselines=True, now=evening(day(1)))
    [baseline] = [r for r in with_baselines['rows'] if r['kind'] == 'version' and r['version_no'] is None]
    assert baseline['text'].startswith(f'规则基线 {S1}：入场 ') and baseline['chain_signal_date'] is None
    assert len(with_baselines['rows']) == len(plain['rows']) + 1


def test_plan_history_invalidation_and_exit_rows_are_always_shown(db):
    market = Market()
    v0 = holding_chain(db, market)
    run(db, market, 2, tickers=['SYNTH'], model=make_model(review=verdict('SYNTH', 'maintain', 'ok', cite=False)))
    exit_review(db, v0, 3)
    retime(db)
    view = plan_views.plan_history(db, 'SYNTH', now=evening(day(3)))
    [exit_row] = [r for r in view['rows'] if r['kind'] == 'review']
    assert exit_row['text'].startswith('退出指令（止损触及）：') and exit_row['decided_by'] == 'host'
    assert view['chains'][0]['state'] == 'exiting' and view['chains'][0]['ended_at'] is None


# ---------------------------------------------------------------- plans a fill may follow

def test_linkable_plans_default_active_head(db):
    market = Market()
    v0 = entry_chain(db, market)
    retime(db)
    when = datetime.combine(day(1), time(10, 0), tzinfo=NY)
    out = plan_views.linkable_plans(db, 'SYNTH', executed_at=when)
    assert out['ticker'] == 'SYNTH' and out['default_plan_id'] == str(v0['plan']['id'])
    [agent, *rest] = out['plans']
    assert agent == {'plan_id': str(v0['plan']['id']), 'kind': 'agent', 'chain_signal_date': S1, 'version_no': 0,
                     'label': f'Agent SYNTH·09-01 第 0 版（入场期，有效至 {day(3):%m-%d}）'}
    assert [p['kind'] for p in rest] == ['rule'] and rest[0]['label'].startswith('规则基线 SYNTH·09-01（有效至 ')
    assert out['plans'][0]['plan_id'] == out['default_plan_id']
    # the version is chosen as of the fill: before the chain existed there is no default
    before = plan_views.linkable_plans(db, 'SYNTH', executed_at=evening(S1) - timedelta(days=3))
    assert before['default_plan_id'] is None and all(p['kind'] != 'agent' for p in before['plans'])


def test_linkable_default_follows_the_head_at_fill_time(db):
    market = Market()
    v0 = entry_chain(db, market)
    spike(market, 'SYNTH', 1)
    run(db, market, 1, tickers=['SYNTH'], model=make_model(revise=renewal('SYNTH'), counter=counter()))
    v1 = execution_repo.chain_head(db, 'SYNTH', S1)
    assert v1['version']['version_no'] == 1
    db.execute('UPDATE agent_plan_versions SET created_at = %s WHERE plan_id = %s',
               [evening(day(1)), v1['plan']['id']])
    db.execute('UPDATE agent_plan_versions SET created_at = %s WHERE plan_id = %s',
               [evening(S1), v0['plan']['id']])
    early = datetime.combine(day(1), time(10, 0), tzinfo=NY)                   # before version 1 existed
    late = datetime.combine(day(2), time(10, 0), tzinfo=NY)
    assert plan_views.linkable_plans(db, 'SYNTH', executed_at=early)['default_plan_id'] == str(v0['plan']['id'])
    assert plan_views.linkable_plans(db, 'SYNTH', executed_at=late)['default_plan_id'] == str(v1['plan']['id'])


def test_linkable_on_the_last_valid_day_and_the_day_after(db):
    """I1: a fill on ``valid_until`` follows the plan table of the session before it, where the chain is still in its
    entry period; the day after ``valid_until`` the chain is not offered."""
    market = Market()
    v0 = entry_chain(db, market)                                    # valid_until = day(3)
    retime(db)
    last = plan_views.linkable_plans(db, 'SYNTH', executed_at=datetime.combine(day(3), time(10, 0), tzinfo=NY))
    assert last['default_plan_id'] == str(v0['plan']['id'])
    assert [p['kind'] for p in last['plans']] == ['agent', 'rule']
    after = plan_views.linkable_plans(db, 'SYNTH', executed_at=datetime.combine(day(4), time(10, 0), tzinfo=NY))
    assert after['default_plan_id'] is None and all(p['kind'] != 'agent' for p in after['plans'])


def test_linkable_excludes_exiting(db):
    """R10: an exiting chain is not offered, nothing new is bought into a chain that has given its exit instruction."""
    market = Market()
    v0 = holding_chain(db, market)
    retime(db)
    when = datetime.combine(day(2), time(10, 0), tzinfo=NY)
    assert plan_views.linkable_plans(db, 'SYNTH', executed_at=when)['default_plan_id'] == str(v0['plan']['id'])
    exit_review(db, v0, 2)
    retime(db)
    out = plan_views.linkable_plans(db, 'SYNTH', executed_at=datetime.combine(day(3), time(10, 0), tzinfo=NY))
    assert out['default_plan_id'] is None and all(p['kind'] != 'agent' for p in out['plans'])


def test_linkable_lists_recent_own_plans(db):
    from test_workbench_ledger import plan_row

    own = plan_row(db, ticker='SYNTH', signal_date=day(0), kind='own', valid_from=day(1), valid_until=day(1),
                   request_id=uuid.uuid4(), request_hash='h')
    out = plan_views.linkable_plans(db, 'SYNTH', executed_at=datetime.combine(day(3), time(10, 0), tzinfo=NY))
    [item] = [p for p in out['plans'] if p['kind'] == 'own']
    assert item['plan_id'] == str(own['id']) and item['label'].startswith('自定 SYNTH·09-01 入场 ')
    far = plan_views.linkable_plans(db, 'SYNTH', executed_at=datetime.combine(day(12), time(10, 0), tzinfo=NY))
    assert [p for p in far['plans'] if p['kind'] == 'own'] == []


# ---------------------------------------------------------------- holding status, command line

def test_holding_status_view_orders_by_instruction(db):
    market = Market()
    v0 = holding_chain(db, market)
    run(db, market, 2, tickers=['SYNTH'], model=make_model(review=verdict('SYNTH', 'maintain', 'ok', cite=False)))
    [row] = plan_views.holding_status_view(db, session=day(2), now=evening(day(2)))
    assert row['ticker'] == 'SYNTH' and row['instruction'] == 'hold' and 'stale' not in row
    assert row['chain_signal_date'] == S1 and row['plan_id'] == str(v0['plan']['id'])
    assert plan_views.holding_status_view(db, session=day(0), now=evening(day(2))) == []


def test_plan_table_show_prints_three_groups(db, capsys):
    market = Market()
    entry_chain(db, market)
    out = []
    code = plan_views.show(day(0), out.append, now=evening(day(0)))
    assert code == 0
    text = '\n'.join(out)
    assert f'计划表 {day(0)}' in text and '入场期 1' in text and '持仓中 0' in text and '已结束 0' in text
    assert 'SYNTH' in text and '区间限价' in text and '120.00–128.00' in text


def test_plan_table_show_without_029(db, monkeypatch):
    monkeypatch.setattr(core_schema, 'executable_ready', lambda conn: False)
    out = []
    assert plan_views.show(day(0), out.append, now=evening(day(0))) == 2
    assert 'migration_029_missing' in out[0]


def test_plan_table_notes_follow_the_row(db):
    from investment_assistant.execution import ledger

    market = Market()
    entry_chain(db, market)
    ledger.record_note(db, {'request_id': str(uuid.uuid4()), 'ticker': 'SYNTH', 'chain_signal_date': S1.isoformat(),
                            'body': '等回踩再看'}, now=evening(day(0)))
    retime(db, notes_at=evening(day(0)))
    [row] = rows_of(table(db, 0), 'entry')
    assert [n['body'] for n in row['notes']] == ['等回踩再看']
    assert set(row['notes'][0]) == {'id', 'body', 'created_at'}


def test_notes_not_in_any_agent_input(db):
    """D25: a note is a log for yourself. After writing one and running a review with the fake model, no agent run
    input or tool call mentions it."""
    from investment_assistant.execution import ledger

    market = Market()
    entry_chain(db, market)
    secret = '这是只给自己看的备注，不能出现在任何提示词里'
    ledger.record_note(db, {'request_id': str(uuid.uuid4()), 'ticker': 'SYNTH', 'chain_signal_date': S1.isoformat(),
                            'body': secret}, now=evening(day(0)))
    db.commit()
    summary, rows, model = run(db, market, 1, tickers=['SYNTH'],
                               model=make_model(review=verdict('SYNTH', 'maintain', '照旧', cite=False)))
    assert model.calls.get('review', 0) == 1
    seen = '\n'.join(str(getattr(m, 'content', '')) for _, messages in model.seen for m in messages)
    assert secret not in seen
    stored = db.execute('SELECT count(*) FROM agent_runs WHERE output::text LIKE %s OR counter::text LIKE %s',
                        [f'%{secret}%', f'%{secret}%']).fetchone()[0]
    calls = db.execute('SELECT count(*) FROM agent_tool_calls WHERE args::text LIKE %s OR result::text LIKE %s',
                       [f'%{secret}%', f'%{secret}%']).fetchone()[0]
    assert stored == 0 and calls == 0
    # the discussion path: a retro-2 card froze the note; what the discussion tool hands out has no text of it
    import dataclasses
    import json

    from investment_assistant.review import retro
    from investment_assistant.review import discuss_tools
    import investment_assistant.views.derivation as derivation_view
    import investment_assistant.views.reasoning as reasoning

    notes = execution_repo.plan_notes_for(db, 'SYNTH', chain_signal_date=S1)
    facts = {'subject': {'kind': 'unit', 'exit_basis': None},
             'execution': retro.execution_fact(buys=[], plans_by_trade={}, notes=notes)}
    assert secret in json.dumps(facts, ensure_ascii=False)                   # the card does carry it (for you)
    card_id = review_repo.insert_review_card(db, {
        'event_key': 'unit:SYNTH:x', 'subject': 'unit', 'ticker': 'SYNTH', 'signal_date': S1, 'buy_trade_id': None,
        'end_kind': 'expired', 'end_session': day(3), 'retro_version': 'retro-2', 'facts': facts,
        'detected_run_id': 'r'})
    ctx = discuss_tools.DiscussContext(ticker='SYNTH', session=day(3), cutoff_at=evening(day(3)),
                                       read=lambda fn: fn(db), signal_date=S1, plan_id=None, card_id=card_id,
                                       unit_plan_ids=frozenset(), unit_run_ids=frozenset(),
                                       read_derivation=derivation_view.plan_derivation_in,
                                       read_agent_run=reasoning.view_in)
    context = discuss_tools.get_plan_context(ctx)
    assert secret not in json.dumps(context, ensure_ascii=False)
    assert [n['id'] for n in context['facts']['execution']['notes']] == [n['id'] for n in notes]


# ---------------------------------------------------------------- an older day is read as that day knew it (I6)

def test_past_plan_table_ignores_the_next_days_exit_instruction(db):
    """The probe of the review: a position whose exit instruction comes on day 3 is still `holding` in the table of
    day 2 when that table is read later."""
    market = Market()
    v0 = holding_chain(db, market)
    run(db, market, 2, tickers=['SYNTH'], model=make_model(review=verdict('SYNTH', 'maintain', 'ok', cite=False)))
    exit_review(db, v0, 3)
    retime(db)
    [row] = rows_of(table(db, 2, now_k=4), 'holding')                      # read two days later
    assert row['chain']['state'] == 'holding' and row['status']['label'] == '持仓期'
    [after] = rows_of(table(db, 4), 'holding')
    assert after['chain']['state'] == 'exiting'


def test_past_plan_table_shows_an_entry_chain_invalidated_later(db):
    """An entry-phase chain that a review invalidates on day 2 is still an entry row in the table of day 1."""
    market = Market()
    v0 = entry_chain(db, market)
    db.execute("INSERT INTO plan_reviews (ticker, signal_date, head_plan_id, session, trigger, run_id, mode, "
               "timeline_version, outcome, decided_by, reason, attempt, cutoff_at, detail) VALUES "
               "('SYNTH', %s, %s, %s, 'batch', 'r', 'entry', 'timeline-2', 'invalidate', 'host', '失效条件成立', 1, %s, '{}')",
               [S1, v0['plan']['id'], day(2), evening(day(2))])
    retime(db)
    [row] = rows_of(table(db, 1, now_k=5), 'entry')
    assert row['chain']['state'] == 'entry'
    assert rows_of(table(db, 1, now_k=5), 'ended') == []
    [ended] = rows_of(table(db, 2, now_k=5), 'ended')
    assert ended['status']['label'].startswith('已作废：')


def test_past_plan_table_shows_the_version_of_that_day(db):
    market = Market()
    v0 = entry_chain(db, market)
    spike(market, 'SYNTH', 1)
    run(db, market, 1, tickers=['SYNTH'], model=make_model(revise=renewal('SYNTH'), counter=counter()))
    retime(db)
    [then] = rows_of(table(db, 0, now_k=3), 'entry')
    assert then['chain']['version_no'] == 0 and then['plan']['plan_id'] == str(v0['plan']['id'])
    [later] = rows_of(table(db, 1, now_k=3), 'entry')
    assert later['chain']['version_no'] == 1


def test_past_table_does_not_show_a_chain_opened_later(db):
    market = Market()
    entry_chain(db, market)
    retime(db)
    assert table(db, 0, now_k=0)['counts']['entry'] == 1
    before = plan_views.plan_table(db, session=day(0) - timedelta(days=3), now=evening(day(4)))
    assert before['counts'] == {'entry': 0, 'holding': 0, 'ended': 0}


def test_linkable_reads_the_chain_as_the_fill_knew_it(db):
    """M11: a fill back-filled for day 1 is offered the version that was the head then, even though the chain was
    invalidated later."""
    market = Market()
    v0 = entry_chain(db, market)
    db.execute("INSERT INTO plan_reviews (ticker, signal_date, head_plan_id, session, trigger, run_id, mode, "
               "timeline_version, outcome, decided_by, reason, attempt, cutoff_at, detail) VALUES "
               "('SYNTH', %s, %s, %s, 'batch', 'r', 'entry', 'timeline-2', 'invalidate', 'host', 'x', 1, %s, '{}')",
               [S1, v0['plan']['id'], day(2), evening(day(2))])
    retime(db)
    out = plan_views.linkable_plans(db, 'SYNTH', executed_at=datetime.combine(day(1), time(10, 0), tzinfo=NY))
    assert out['default_plan_id'] == str(v0['plan']['id'])
    late = plan_views.linkable_plans(db, 'SYNTH', executed_at=datetime.combine(day(3), time(10, 0), tzinfo=NY))
    assert late['default_plan_id'] is None


def test_include_extended_never_hides_a_position(db):
    """I3: the extended-pool switch is for extended-pool units; their positions stay in the holding group."""
    market = Market()
    db.execute('INSERT INTO pool_tiers (ticker, tier, effective_session, recorded_at) '
               "VALUES ('SYNA', 'extended', %s, %s)", [S1 - timedelta(days=1), evening(S1) - timedelta(days=1)])
    signal_day(db, market, None, pool=pool_module.Pool(core=('SYNTH',), extended=('SYNA',)))
    fill(db, 'SYNA', '10', '125', 1, execution_repo.chain_head(db, 'SYNA', S1)['plan']['id'])
    hidden = table(db, 2)
    assert [r['ticker'] for r in rows_of(hidden, 'holding')] == ['SYNA'] and rows_of(hidden, 'holding')[0]['pool'] == 'extended'
    assert hidden['counts']['holding'] == 1
    assert [r['ticker'] for r in rows_of(table(db, 2, include_extended=True), 'holding')] == ['SYNA']


def test_chain_view_versions_carry_their_execution(db):
    """I4 (R13): the plan page's 「执行状态」 column: the version a fill was linked to is adopted, the other one is not."""
    import investment_assistant.workflow.nodes.review as plan_review

    market = Market()
    v0 = entry_chain(db, market)
    spike(market, 'SYNTH', 1)
    run(db, market, 1, tickers=['SYNTH'], model=make_model(revise=renewal('SYNTH'), counter=counter()))
    fill(db, 'SYNTH', '10', '125', 2, execution_repo.chain_head(db, 'SYNTH', S1)['plan']['id'])
    view = plan_review.chain_view('SYNTH', S1, now=evening(day(2)))
    first, second = view['versions']
    assert first['execution'] == {'status': 'not_executed'}                        # a holding chain waits for no fill
    assert second['execution'] == {'status': 'adopted', 'date': f'{day(2):%m-%d}', 'quantity': '10'}
    assert v0['plan']['id'] is not None


def test_trade_list_carries_the_plan_label(db):
    """M1: the trade list says which plan a fill followed, in the words of the link list, not a UUID."""
    market = Market()
    v0 = entry_chain(db, market)
    retime(db)
    fill(db, 'SYNTH', '10', '125', 1, v0['plan']['id'])
    plain = execution_repo.insert_trade(db, {
        'request_id': uuid.uuid4(), 'ticker': 'SYNTH', 'side': 'sell', 'quantity': 5, 'price': 130, 'fee': 0,
        'executed_at': datetime.combine(day(2), time(10, 0), tzinfo=NY), 'decision_id': None, 'plan_id': None,
        'note': None}, 'h', recorded_at=evening(day(2)))
    db.commit()
    rows = {str(t['id']): t for t in execution_trades.list_trades()}
    linked = next(t for t in rows.values() if t['plan_id'] == v0['plan']['id'])
    assert linked['plan_label'] == f'Agent SYNTH·09-01 第 0 版（有效至 {day(3):%m-%d}）'
    assert 'plan_label' not in rows[str(plain['id'])]
    assert execution_trades.plan_label(v0['plan'], version_no=0, state='入场期') == \
        f'Agent SYNTH·09-01 第 0 版（入场期，有效至 {day(3):%m-%d}）'


def test_week_fills_belong_to_one_unit_and_own_plans_have_no_delay(db):
    """M2: a fill linked to the baseline of a unit merged into a chain is the chain unit's, once; a fill that wrote its own
    plan has no delay hours."""
    market = Market()
    entry_chain(db, market)
    spike(market, 'SYNTH', 1)
    run(db, market, 1, tickers=['SYNTH'], model=make_model(revise=renewal('SYNTH'), counter=counter()))
    [merged] = execution_repo.chain_retriggers_for(db, 'SYNTH')
    fill(db, 'SYNTH', '10', '125', 2, merged['baseline_plan_id'])
    retime(db)
    week = review_weekly._week_units(db, S1 - timedelta(days=S1.weekday()), S1 + timedelta(days=6), now=evening(day(5)))
    owned = [(f['unit_id'], str(f['trade']['id'])) for f in week['fills']]
    assert len(owned) == len(set(t for _, t in owned)) == 1                       # once, not once per unit
    states = {u['id']: u['execution'] for u in week['units']}
    assert states[f'SYNTH:{S1}'] == 'adopted' and states[f'SYNTH:{day(1)}'] == 'not_executed'
    assert owned[0][0] == f'SYNTH:{S1}'


def test_a_fill_that_wrote_its_own_plan_has_no_delay(db):
    from investment_assistant.review import outcomes as review
    from investment_assistant.execution import ledger

    market = Market()
    entry_chain(db, market)
    retime(db)
    when = datetime.combine(day(1), time(10, 0), tzinfo=NY)
    out = ledger.record_trade(db, {'request_id': str(uuid.uuid4()), 'ticker': 'SYNTH', 'side': 'buy', 'quantity': '5',
                                   'price': '125', 'executed_at': when.isoformat(),
                                   'own_plan': {'entry_low': '120', 'entry_high': '128', 'stop': '115',
                                                'target1': '140'}}, now=evening(day(1)))
    db.commit()
    week = review_weekly._week_units(db, S1 - timedelta(days=S1.weekday()), S1 + timedelta(days=6), now=evening(day(1)))
    [one] = [f for f in week['fills'] if f['trade']['id'] == out['record']['id']]
    assert one['plan_created_at'] is None
    stats = review.weekly_stats(S1 - timedelta(days=S1.weekday()), week['units'], {}, [], segments=[], entry_opens={},
                                fills=week['fills'])
    assert stats['execution'][0]['delay_hours'] is None


def test_merged_unit_says_how_its_chain_ended(db):
    """M8: the baseline of a unit merged into a chain that has since ended is a row, and the label says how and when."""
    from test_workbench_plan_review import settings

    market = Market()
    entry_chain(db, market)
    spike(market, 'SYNTH', 1)
    run(db, market, 1, tickers=['SYNTH'], agent=settings(provider='off'))           # merged on day 1, nothing revised
    retime(db)
    assert rows_of(table(db, 1), 'entry')[0]['chain'] is not None                  # chain A still active: a line in its row
    assert len(rows_of(table(db, 1), 'entry')) == 1
    entry = [r for r in rows_of(table(db, 3), 'entry') if r['chain'] is None]
    [row] = entry
    assert row['status']['label'] == f'所并入的链 SYNTH·09-01 已于 {day(3):%m-%d} 过期未执行，以下为规则基线'


def test_older_unit_of_a_ticker_with_an_active_chain_is_a_line(db):
    """M8: an older buy unit that never joined the ticker's (later) active chain is listed under 「其他触发」."""
    from test_workbench_plan_review import settings

    market = Market()
    run(db, market, 0, tickers=['SYNTH'], agent=settings(provider='off'))           # day 0: the unit, no chain
    spike(market, 'SYNTH', 1)
    run(db, market, 1, tickers=['SYNTH'], model=make_model(propose=proposal('SYNTH'), counter=counter()))
    retime(db)
    view = table(db, 1)
    assert [r['chain']['signal_date'] for r in rows_of(view, 'entry')] == [day(1)]
    assert [(u['ticker'], u['signal_date'], u['proposed_action']) for u in view['other_units']] == [('SYNTH', S1, 'buy')]
