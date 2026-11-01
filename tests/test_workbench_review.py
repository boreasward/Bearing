"""Review math on synthetic bars (fabricated numbers)."""
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest

from investment_assistant.core import tx as core_tx

from investment_assistant.data.calendar import add_trading_days
from investment_assistant.workbench import review

T = date(2026, 10, 1)  # Thursday
NOW = datetime(2026, 11, 20, tzinfo=UTC)


def bars_from(start, closes, opens=None, factor=1.0):
    days = [add_trading_days(start, i) for i in range(len(closes))]
    opens = opens or closes
    return [{'price_date': d, 'open': float(o), 'close': float(c), 'adj_close': float(c) * factor}
            for d, o, c in zip(days, opens, closes)]


def unit(action='buy', execution='adopted', rules=('trend_reclaim',), signal_date=T, uid=None):
    """A review-8 unit: every ticker-day with candidates, with its execution-1 state (no decision any more)."""
    return {'id': uid or f'NVDA:{uuid.uuid4()}', 'ticker': 'NVDA', 'signal_date': signal_date,
            'proposed_action': action, 'execution': execution, 'rule_ids': list(rules)}


# T at 100, then entry day opens at 100 and rises 1 per session.
STOCK = bars_from(T, [100] + [100 + i for i in range(1, 30)], opens=[100, 100] + [100 + i for i in range(2, 30)])
SPY = bars_from(T, [500] * 30)


def test_buy_shadow_enters_next_open_and_measures_windows():
    out = review.shadow_outcome(unit(), STOCK, SPY)
    assert out['entry_date'] == add_trading_days(T, 1) and out['entry_price'] == 100.0
    w5 = out['windows']['5']
    # exit at close of the 5th session counting the entry session: close 105
    assert w5 == {'status': 'mature', 'exit_date': add_trading_days(T, 5).isoformat(),
                  'return': pytest.approx(0.05), 'spy_return': pytest.approx(0.0), 'excess': pytest.approx(0.05)}


def test_sell_shadow_reports_avoided_move_with_sign_flipped():
    w5 = review.shadow_outcome(unit('sell'), STOCK, SPY)['windows']['5']
    assert w5['return'] == pytest.approx(-0.05) and w5['excess'] == pytest.approx(-0.05)


def test_watch_shadow_records_move_without_pnl():
    w5 = review.shadow_outcome(unit('watch'), STOCK, SPY)['windows']['5']
    assert w5['move'] == pytest.approx(0.05) and 'return' not in w5 and 'excess' not in w5


def test_windows_not_yet_complete_are_immature():
    short = STOCK[:8]
    out = review.shadow_outcome(unit(), short, SPY)
    assert out['windows']['5']['status'] == 'mature'
    assert out['windows']['10'] == {'status': 'immature'} and out['windows']['20'] == {'status': 'immature'}


def test_missing_entry_bar_is_pending():
    assert review.shadow_outcome(unit(), STOCK[:1], SPY) == {'status': 'pending_entry', 'windows': {}}


def test_returns_use_adjusted_prices_including_dividends():
    # adj_close 1% below close on every day before a dividend paid after the window: no effect on ratios.
    stock = bars_from(T, [100] * 10, factor=0.99)
    w5 = review.shadow_outcome(unit(), stock, SPY)['windows']['5']
    assert w5['return'] == pytest.approx(0.0)


def test_trade_review_for_closed_segment():
    segment = {'buy_trade_id': 'b', 'sell_trade_id': 's', 'quantity': D('10'), 'entry_cost_per_share': D('100.1'),
               'exit_price': D('105'), 'realized_pnl': D('48'),
               'opened_at': datetime(2026, 10, 2, 14, tzinfo=UTC), 'closed_at': datetime(2026, 10, 8, 19, tzinfo=UTC)}
    out = review.trade_segment_review(segment, STOCK, SPY)
    assert out['holding_sessions'] == 5
    assert out['return'] == pytest.approx(48 / 1001)
    assert out['spy_return'] == pytest.approx(0.0)
    assert out['max_favorable'] == pytest.approx(0.05) and out['max_adverse'] == pytest.approx(0.0)


def test_open_position_mark_to_latest_close():
    pos = {'quantity': D('10'), 'avg_cost': D('100'), 'opened_at': datetime(2026, 10, 2, 14, tzinfo=UTC)}
    out = review.open_position_review('NVDA', pos, STOCK)
    assert out['last_close'] == 129.0 and out['unrealized_pnl'] == pytest.approx(290.0)
    assert out['as_of'] == STOCK[-1]['price_date'].isoformat()


def _week_fixture():
    week = date(2026, 9, 28)
    accepted = unit(rules=('trend_reclaim', 'new_filing'))
    ignored = unit(execution='not_executed')
    late = unit(execution='out_of_validity', rules=('new_filing',))
    outcomes = {accepted['id']: review.shadow_outcome(accepted, STOCK, SPY),
                ignored['id']: review.shadow_outcome(ignored, STOCK[:3], SPY),
                late['id']: review.shadow_outcome(late, STOCK, SPY)}
    created = datetime(2026, 10, 1, 21, tzinfo=UTC)         # when the linked plan version was generated
    t1 = {'id': 't1', 'ticker': 'NVDA', 'side': 'buy', 'price': D('101'),
          'executed_at': datetime(2026, 10, 2, 14, tzinfo=UTC), 'voided_at': None}
    t2 = {'id': 't2', 'ticker': 'MU', 'side': 'buy', 'price': D('50'),
          'executed_at': datetime(2026, 10, 2, 15, tzinfo=UTC), 'voided_at': None}
    t3 = {'id': 't3', 'ticker': 'MU', 'side': 'buy', 'price': D('50'),
          'executed_at': datetime(2026, 10, 2, 16, tzinfo=UTC), 'voided_at': datetime(2026, 10, 3, tzinfo=UTC)}
    return week, [accepted, ignored, late], outcomes, [t1, t2, t3], created


def test_weekly_stats_groups_and_never_claims_significance():
    week, units, outcomes, trades, created = _week_fixture()
    stats = review.weekly_stats(week, units, outcomes, trades, segments=[], entry_opens={units[0]['id']: 100.0},
                                fills=[{'trade': trades[0], 'unit_id': units[0]['id'], 'plan_created_at': created}],
                                unplanned=[trades[1]])
    assert stats['calc_version'] == review.CALC_VERSION == 'review-8'
    assert stats['week'] == {'start': '2026-09-28', 'end': '2026-10-04'}
    assert stats['units'] == {'executed': 2, 'not_executed': 1}
    tr = stats['by_rule']['trend_reclaim']['5']
    assert tr['count'] == 2 and tr['mature'] == 1 and tr['mean'] == pytest.approx(0.05)
    assert stats['by_rule']['new_filing']['5']['count'] == 2
    assert 'by_decision' not in stats and 'unsignaled_trades' not in stats
    assert 'significant' not in str(stats) and 'p_value' not in str(stats)


def test_review8_control_group_all_units():
    """D26: the groups cover every unit of the week, not only the ones somebody decided on."""
    week, units, outcomes, trades, _ = _week_fixture()
    stats = review.weekly_stats(week, units, outcomes, trades, segments=[], entry_opens={})
    assert sum(g['5']['count'] for g in stats['by_execution'].values() if g['5']['count'] and True) >= 3
    assert stats['by_rule']['new_filing']['5']['count'] == 2        # the not-executed unit is in the control group too
    assert stats['units']['executed'] + stats['units']['not_executed'] == len(units)


def test_review8_by_execution():
    week, units, outcomes, trades, _ = _week_fixture()
    stats = review.weekly_stats(week, units, outcomes, trades, segments=[], entry_opens={}, unplanned=[trades[1]])
    groups = stats['by_execution']
    assert set(groups) == {'adopted', 'out_of_validity', 'not_executed', 'unplanned'}
    assert groups['adopted']['5']['count'] == 1 and groups['adopted']['5']['mature'] == 1
    assert groups['not_executed']['5'] == {'count': 1, 'mature': 0, 'mean': None, 'median': None, 'values': []}
    assert groups['out_of_validity']['5']['count'] == 1
    assert groups['unplanned']['5'] == {'count': 1, 'mature': 0, 'mean': None, 'median': None, 'values': []}


def test_review8_execution_delay_from_version():
    """The delay runs from the moment the linked plan version was generated, not from a decision."""
    week, units, outcomes, trades, created = _week_fixture()
    stats = review.weekly_stats(week, units, outcomes, trades, segments=[], entry_opens={units[0]['id']: 100.0},
                                fills=[{'trade': trades[0], 'unit_id': units[0]['id'], 'plan_created_at': created}])
    assert stats['execution'] == [{'trade_id': 't1', 'unit': units[0]['id'], 'ticker': 'NVDA',
                                   'slippage': pytest.approx(0.01), 'delay_hours': pytest.approx(17.0)}]


def test_review8_non_buy_units_have_no_execution_state():
    """M3: a unit nobody could execute (sell / watch / conflict) is in the control group and by_rule only."""
    week, units, outcomes, trades, _ = _week_fixture()
    watch = unit('watch', execution='not_applicable', rules=('new_filing',))
    outcomes[watch['id']] = review.shadow_outcome(watch, STOCK, SPY)
    stats = review.weekly_stats(week, [*units, watch], outcomes, trades, segments=[], entry_opens={})
    assert stats['units'] == {'executed': 2, 'not_executed': 1}                  # the watch unit is in neither
    assert 'not_applicable' not in stats['by_execution']
    assert stats['by_rule']['new_filing']['5']['count'] == 3                       # ... but it is in by_rule


def test_review8_unplanned_trades():
    week, units, outcomes, trades, _ = _week_fixture()
    stats = review.weekly_stats(week, units, outcomes, trades, segments=[], entry_opens={}, unplanned=[trades[1]])
    assert stats['unplanned_trades'] == ['t2']


def test_review8_learning_execution_text():
    cards = [_card(1, execution_text='采纳（照 Agent 第 2 版）'), _card(2, execution_text='计划外成交')]
    out = review.learning_stats(week_start=WEEK, week_end=WEEK_END, cards=cards, perf_by_item={})
    assert [c['execution_text'] for c in out['cards']] == ['采纳（照 Agent 第 2 版）', '计划外成交']
    assert all('decision_text' not in c for c in out['cards'])


def test_review8_plans_split_by_shadow_version():
    """Plans are summarised per shadow version and never mixed (spec §5.6)."""
    week = date(2026, 10, 19)
    base = {'signal_date': date(2026, 10, 20), 'kind': 'rule'}
    new = {**base, 'shadow_version': 'plan-shadow-2', 'outcome': {'status': 'voided', 'r': None},
           'shadow_v1': {'status': 'closed', 'r': 1.0}}
    old = {**base, 'kind': 'agent', 'shadow_version': 'plan-shadow-1', 'outcome': {'status': 'closed', 'r': 2.0},
           'shadow_v1': None, 'chain': None}
    stats = review.weekly_stats(week, [], {}, [], segments=[], entry_opens={}, plan_outcomes=[new, old])
    assert stats['plans']['rule']['voided'] == 1 and 'agent' not in stats['plans']        # the new plans, their own keys
    # ... and the older ones in the same list, under keys that name their shadow (the page lists every key)
    assert stats['plans']['Agent 首版（旧计划 · plan-shadow-1）']['count'] == 1
    assert stats['plans']['Agent 最新版（旧计划 · plan-shadow-1）']['r_mean'] == 2.0
    assert stats['plans_v1']['agent']['count'] == 1
    assert stats['plans_counterfactual']['rule']['r_mean'] == 1.0
    assert stats['shadow_versions']['plans'] == 'plan-shadow-2'


def test_old_snapshots_not_recomputed(workbench_db):
    """Frozen review-7 (and older) snapshots are served as stored: nothing in this module rebuilds them."""
    week = date(2026, 10, 12)
    frozen = {'calc_version': 'review-7', 'units': {'decided': 3, 'undecided': 1}, 'by_decision': {'accept': {}},
              'unsignaled_trades': ['x']}
    first = review.save_weekly(workbench_db, week, frozen, notes='', supersedes_id=None)
    assert review.CALC_VERSION == 'review-8'
    [head] = review.list_weekly(workbench_db)
    assert head['id'] == first['id'] and head['stats'] == frozen


def test_generating_a_new_week_leaves_an_old_snapshot_alone(workbench_db, monkeypatch):
    """Review Focus 3: generating a `review-8` snapshot for another week neither rewrites nor supersedes the frozen
    `review-7` one."""
    from contextlib import contextmanager

    from investment_assistant.workbench import service

    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(core_tx, 'connection', connection)
    old_week = date(2026, 10, 5)
    frozen = {'calc_version': 'review-7', 'units': {'decided': 3, 'undecided': 1}, 'by_decision': {'accept': {}}}
    old = review.save_weekly(workbench_db, old_week, frozen, notes='旧', supersedes_id=None)
    before = workbench_db.execute('SELECT id, calc_version, stats::text, supersedes_id FROM weekly_reviews').fetchall()
    new = service.generate_weekly(date(2026, 10, 19), now=datetime(2026, 11, 2, 22, 30, tzinfo=UTC))
    assert new['calc_version'] == 'review-8' and new['id'] != old['id']
    after = workbench_db.execute('SELECT id, calc_version, stats::text, supersedes_id FROM weekly_reviews').fetchall()
    assert len(after) == 2 and [r for r in after if r[0] == old['id']] == before   # the old row is byte-for-byte the same
    assert {h['id'] for h in review.list_weekly(workbench_db)} == {new['id'], old['id']}      # two weeks, two heads


# --- weekly snapshot persistence (disposable schema) ---

def test_weekly_reviews_are_frozen_and_corrected_by_superseding(workbench_db):
    from investment_assistant.workbench import ledger
    week = date(2026, 10, 19)
    first = review.save_weekly(workbench_db, week, {'units': {'decided': 1}}, notes='初稿', supersedes_id=None)
    with pytest.raises(ledger.Conflict):
        review.save_weekly(workbench_db, week, {'units': {'decided': 2}}, notes='', supersedes_id=None)
    second = review.save_weekly(workbench_db, week, first['stats'], notes='补充笔记', supersedes_id=first['id'])
    assert second['supersedes_id'] == first['id'] and second['stats'] == {'units': {'decided': 1}}
    heads = review.list_weekly(workbench_db)
    assert [h['id'] for h in heads] == [second['id']]
    assert workbench_db.execute('SELECT count(*) FROM weekly_reviews').fetchone() == (2,)


def test_weekly_review_week_must_start_monday(workbench_db):
    from investment_assistant.workbench import ledger
    with pytest.raises(ledger.LedgerError):
        review.save_weekly(workbench_db, date(2026, 10, 20), {}, notes='', supersedes_id=None)


def test_weekly_review2_has_plans_and_discipline():
    week = date(2026, 9, 28)
    in_week = lambda d: week <= d <= week + timedelta(days=6)
    plan_outcomes = [
        {'kind': 'rule', 'signal_date': date(2026, 9, 29), 'outcome': {'status': 'open', 'r': 2.0}},
        {'kind': 'rule', 'signal_date': date(2026, 10, 1), 'outcome': {'status': 'expired', 'r': None}},
        {'kind': 'own', 'signal_date': date(2026, 9, 30), 'outcome': {'status': 'closed', 'r': 1.0}},
        {'kind': 'rule', 'signal_date': date(2026, 10, 10), 'outcome': {'status': 'closed', 'r': 9.0}},  # 周外
    ]
    disc = {'trades': [], 'counts': {'chase': 0, 'early_take': 0, 'held_loser': 0, 'unplanned': 0},
            'immature': {'early_take': 0, 'held_loser': 0}, 'planned_buys': 1, 'compliance_rate': 1.0}
    stats = review.weekly_stats(week, [], {}, [], segments=[], entry_opens={},
                                plan_outcomes=plan_outcomes, discipline=disc)
    assert stats['calc_version'] == 'review-8'   # P4 起带 tracks 键；v2.5 起 plans 分首版与最新版；v2.7 起可带 learning
    assert stats['plans']['rule'] == {'count': 2, 'expired': 1, 'voided': 0, 'r_mean': 2.0, 'r_median': 2.0}
    assert stats['plans']['own'] == {'count': 1, 'expired': 0, 'voided': 0, 'r_mean': 1.0, 'r_median': 1.0}
    assert stats['discipline'] == disc


# --- P2: by-pool view (review-3) ---

def test_pool_of_prefers_context():
    assert review.pool_of({'pool_tier': 'extended'}, {'tier': 'core'}) == 'extended'
    assert review.pool_of({'pool_tier': 'core'}, {'tier': 'extended'}) == 'core'
    assert review.pool_of(None, {'tier': 'extended'}) == 'extended'
    assert review.pool_of(None, {'tier': 'core'}) == 'core'


def test_legacy_plan_without_context_counts_core():
    assert review.pool_of(None, None) == 'core'
    assert review.pool_of({'pool_tier': 'unknown'}, None) == 'core'


def test_pool_of_three_way():
    assert review.pool_of({'pool_tier': 'extended', 'source': 'screen'}, None) == 'screen'
    assert review.pool_of(None, {'tier': 'extended', 'source': 'screen'}) == 'screen'
    assert review.pool_of({'pool_tier': 'extended'}, None) == 'extended'
    assert review.pool_of(None, None) == 'core'
    assert review.pool_of({'pool_tier': 'core', 'source': 'watchlist'}, None) == 'core'


def test_calc_version_review_8():
    assert review.CALC_VERSION == 'review-8'


def test_pool_summary_groups_three_pools():
    triggers = [{'pool': 'core', 'rule_id': 'trend_reclaim'},
                {'pool': 'screen', 'rule_id': 'volume_breakout'},
                {'pool': 'extended', 'rule_id': 'volume_breakout'}]
    out = review.pool_summary(triggers, [])
    assert set(out) == {'core', 'screen', 'extended'}


def test_pool_summary_splits_by_pool_and_rule():
    triggers = [{'pool': 'core', 'rule_id': 'trend_reclaim'}, {'pool': 'core', 'rule_id': 'trend_reclaim'},
                {'pool': 'extended', 'rule_id': 'trend_reclaim'}, {'pool': 'core', 'rule_id': 'volume_breakout'}]
    plan_outcomes = [{'pool': 'core', 'rule_ids': ['trend_reclaim', 'volume_breakout'],
                      'outcome': {'status': 'expired', 'r': None}},
                     {'pool': 'extended', 'rule_ids': ['trend_reclaim'], 'outcome': {'status': 'open', 'r': 2.0}}]
    out = review.pool_summary(triggers, plan_outcomes)
    assert out['core']['trend_reclaim'] == {'triggers': 2, 'plans': 1, 'expired': 1, 'filled': 0,
                                            'r_mean': None, 'r_median': None}
    assert out['core']['volume_breakout']['triggers'] == 1 and out['core']['volume_breakout']['plans'] == 1
    assert out['extended']['trend_reclaim'] == {'triggers': 1, 'plans': 1, 'expired': 0, 'filled': 1,
                                                'r_mean': 2.0, 'r_median': 2.0}


def test_pool_summary_counts_expired_and_filled():
    plan_outcomes = [{'pool': 'core', 'rule_ids': ['earnings_gap_up'], 'outcome': {'status': 'expired', 'r': None}},
                     {'pool': 'core', 'rule_ids': ['earnings_gap_up'], 'outcome': {'status': 'open', 'r': 1.0}},
                     {'pool': 'core', 'rule_ids': ['earnings_gap_up'], 'outcome': {'status': 'closed', 'r': 3.0}},
                     {'pool': 'core', 'rule_ids': ['earnings_gap_up'], 'outcome': {'status': 'pending', 'r': None}}]
    out = review.pool_summary([], plan_outcomes)['core']['earnings_gap_up']
    assert (out['plans'], out['expired'], out['filled']) == (4, 1, 2)
    assert out['r_mean'] == 2.0 and out['r_median'] == 2.0


def test_pool_summary_counts_only_rule_plans():
    # An own plan on the same unit must not double the rule plan's counts (spec §9: rule plans only).
    plan_outcomes = [{'pool': 'core', 'kind': 'rule', 'rule_ids': ['trend_reclaim'],
                      'outcome': {'status': 'closed', 'r': 1.0}},
                     {'pool': 'core', 'kind': 'own', 'rule_ids': ['trend_reclaim'],
                      'outcome': {'status': 'closed', 'r': 3.0}}]
    out = review.pool_summary([], plan_outcomes)['core']['trend_reclaim']
    assert (out['plans'], out['filled'], out['r_mean']) == (1, 1, 1.0)


def test_weekly_stats_carries_by_pool():
    stats = review.weekly_stats(date(2026, 10, 19), [], {}, [], segments=[], entry_opens={},
                                by_pool={'core': {'trend_reclaim': {'triggers': 1}}})
    assert stats['by_pool'] == {'core': {'trend_reclaim': {'triggers': 1}}}
    assert review.weekly_stats(date(2026, 10, 19), [], {}, [], segments=[], entry_opens={})['by_pool'] == {}
    split = review.weekly_stats(date(2026, 10, 19), [], {}, [], segments=[], entry_opens={}, by_pool_v1={'core': {}})
    assert split['by_pool'] == {} and split['by_pool_v1'] == {'core': {}}        # plan columns of two shadows, apart


def test_weekly_plan_summary_has_agent_kind():
    week = date(2026, 9, 28)
    plan_outcomes = [
        {'kind': 'rule', 'signal_date': date(2026, 9, 29), 'outcome': {'status': 'open', 'r': 2.0}},
        {'kind': 'agent', 'signal_date': date(2026, 9, 30), 'outcome': {'status': 'closed', 'r': 1.5}},
    ]
    disc = {'trades': [], 'counts': {'chase': 0, 'early_take': 0, 'held_loser': 0, 'unplanned': 0},
            'immature': {'early_take': 0, 'held_loser': 0}, 'planned_buys': 1, 'compliance_rate': 1.0}
    stats = review.weekly_stats(week, [], {}, [], segments=[], entry_opens={},
                                plan_outcomes=plan_outcomes, discipline=disc)
    assert stats['plans']['agent'] == {'count': 1, 'expired': 0, 'voided': 0, 'r_mean': 1.5, 'r_median': 1.5}


def test_pool_summary_ignores_agent_plans():
    triggers = [{'pool': 'core', 'rule_id': 'trend_reclaim'}]
    plan_outcomes = [
        {'pool': 'core', 'rule_ids': ['trend_reclaim'], 'outcome': {'status': 'open', 'r': 1.0}},
        {'pool': 'core', 'rule_ids': ['trend_reclaim'], 'kind': 'agent',
         'outcome': {'status': 'open', 'r': 5.0}},
    ]
    out = review.pool_summary(triggers, plan_outcomes)
    assert out['core']['trend_reclaim'] == {'triggers': 1, 'plans': 1, 'expired': 0, 'filled': 1,
                                            'r_mean': 1.0, 'r_median': 1.0}


def test_weekly_stats_without_tracks_keeps_shape():
    stats = review.weekly_stats(date(2026, 10, 19), [], {}, [], segments=[], entry_opens={})
    assert stats['calc_version'] == 'review-8'
    assert stats['tracks'] is None            # 旧调用方仍可用，无对比数据时为空
    assert 'learning' not in stats            # 没传 learning 的旧调用方，快照里没有这一键
    assert 'by_rule' in stats and 'plans' in stats and 'note' in stats


# --- v2.5 D17: plans.agent counts each chain's first version; agent_latest its head ---

def _agent_entry(r, version_no, is_head, signal_date=date(2026, 9, 30)):
    chain = None if version_no is None else {'version_no': version_no, 'is_head': is_head}
    return {'kind': 'agent', 'signal_date': signal_date, 'outcome': {'status': 'closed', 'r': r}, 'chain': chain}


def test_three_version_chain_counts_once_in_agent():
    week = date(2026, 9, 28)
    entries = [_agent_entry(1.0, 0, False), _agent_entry(2.0, 1, False), _agent_entry(3.0, 2, True),
               {'kind': 'rule', 'signal_date': date(2026, 9, 29), 'outcome': {'status': 'open', 'r': 0.5}}]
    out = review._plan_summary(entries, week, week + timedelta(days=6))
    assert out['agent'] == {'count': 1, 'expired': 0, 'voided': 0, 'r_mean': 1.0, 'r_median': 1.0}
    assert out['agent_latest'] == {'count': 1, 'expired': 0, 'voided': 0, 'r_mean': 3.0, 'r_median': 3.0}
    assert out['rule']['count'] == 1


def test_agent1_plan_is_its_own_first_and_latest():
    week = date(2026, 9, 28)
    out = review._plan_summary([_agent_entry(1.5, None, None)], week, week + timedelta(days=6))
    assert out['agent'] == out['agent_latest'] == {'count': 1, 'expired': 0, 'voided': 0, 'r_mean': 1.5, 'r_median': 1.5}


def test_generate_weekly_counts_first_and_latest(workbench_db, monkeypatch):
    from contextlib import contextmanager

    from investment_assistant.workbench import repository, service

    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(core_tx, 'connection', connection)
    day = date(2026, 10, 21)
    previous = None
    for version_no in range(3):
        plan = repository.insert_plan(workbench_db, {
            'ticker': 'NVDA', 'signal_date': day, 'kind': 'agent', 'based_on_id': None, 'plan_version': 'agent-2',
            'ref_price': D('100'), 'atr': D('2'), 'entry_low': D('99'), 'entry_high': D('101'), 'stop': D('94'),
            'target1': D('108'), 'trail_atr_multiple': D('3'), 'valid_from': date(2026, 10, 22),
            'valid_until': date(2026, 10, 26), 'sizing': {'status': 'no_equity'}, 'status': 'ok',
            'invalid_reasons': [], 'warnings': [], 'inputs': {'bar_ids': [1]}, 'request_id': None,
            'request_hash': None})
        run_id, at = uuid.uuid4(), NOW - timedelta(days=30 - version_no)
        repository.insert_agent_run(workbench_db, {
            'id': run_id, 'ticker': 'NVDA', 'signal_date': day, 'pool': 'main', 'attempt': 1,
            'mode': 'propose' if version_no == 0 else 'revise', 'agent_version': 'agent-2', 'provider': 'chatgpt',
            'model': 'gpt-test', 'cutoff_at': at, 'status': 'ok', 'reason': None, 'output': {'action': 'buy'},
            'counter': None, 'plan_id': plan['id'], 'input_tokens': 0, 'output_tokens': 0,
            'started_at': at, 'finished_at': at}, [])
        repository.insert_plan_version(workbench_db, {
            'plan_id': plan['id'], 'ticker': 'NVDA', 'signal_date': day, 'version_no': version_no,
            'supersedes_plan_id': previous, 'origin': 'signal' if version_no == 0 else 'review',
            'agent_run_id': run_id, 'cutoff_at': at, 'as_of_session': day,
            'diff': None if version_no == 0 else [{'field': 'stop', 'before': '94', 'after': '95'}],
            'schema_version': 'agent-2'})
        previous = plan['id']
    out = service.generate_weekly(date(2026, 10, 19), now=NOW)
    assert out['calc_version'] == 'review-8' and out['stats']['calc_version'] == 'review-8'
    # agent-2 versions carry no execution fields: plan-shadow-1, listed under keys that name that shadow
    assert out['stats']['shadow_versions']['plans_v1'] == 'plan-shadow-1'
    assert out['stats']['plans']['Agent 首版（旧计划 · plan-shadow-1）']['count'] == 1
    assert set(out['stats']['plans']) == {'Agent 首版（旧计划 · plan-shadow-1）', 'Agent 最新版（旧计划 · plan-shadow-1）'}
    assert out['stats']['plans_v1']['agent']['count'] == 1
    assert out['stats']['plans_v1']['agent_latest']['count'] == 1
    agent = out['stats']['tracks']['units'][0]['agent']
    assert agent['version_no'] == 0 and agent['version_basis'] == 'first'


def _agent_row(r, *, status='closed', version_no=0, is_head=True):
    return {'kind': 'agent', 'outcome': {'status': status, 'r': r},
            'chain': {'version_no': version_no, 'is_head': is_head}}


def test_shadow_block_matches_previous_summary():
    """v2.7 Task 1 extracted ``shadow_block`` unchanged out of ``shadow_agent_summary``; the literal
    expectations below are what the summary returned before the extraction."""
    rows = [_agent_row(1.0), _agent_row(-1.0), _agent_row(3.0), _agent_row(0.0), _agent_row(2.0),
            _agent_row(None, status='expired'), _agent_row(None, status='pending'),
            {'kind': 'rule', 'outcome': {'status': 'closed', 'r': 9.0}, 'chain': None},
            _agent_row(5.0, version_no=1, is_head=False)]                      # a later version: not "first"
    out = review.shadow_agent_summary(rows)
    assert out == {
        'first': {'count': 7, 'determined': 5, 'expired': 1, 'r_mean': 1.0, 'r_median': 1.0},
        'latest': {'count': 7, 'determined': 5, 'expired': 1, 'r_mean': 1.0, 'r_median': 1.0}}
    assert review.shadow_block([row['outcome'] for row in rows if row['kind'] == 'agent']) \
        == {'count': 8, 'determined': 6, 'expired': 1, 'r_mean': 1.6666666666666667, 'r_median': 1.5}
    # below the threshold no mean / median
    assert review.shadow_block([{'status': 'closed', 'r': 1.0}] * 4) \
        == {'count': 4, 'determined': 4, 'expired': 0, 'r_mean': None, 'r_median': None}
    assert review.shadow_block([]) == {'count': 0, 'determined': 0, 'expired': 0, 'r_mean': None, 'r_median': None}


# --- v2.7 §10: the weekly 「你学到了什么」 ---

WEEK = date(2026, 10, 19)           # Monday
WEEK_END = date(2026, 10, 25)


def _card(card_id, *, ticker='NVDA', end_session=date(2026, 10, 21), end_kind='position_closed', status='to_review',
          flags=None, pending=0, actual_r=1.5, rule_r=2.0, agent_r=None, headline='一句话', execution_text='采纳（照规则计划）',
          filled=False):
    totals = {'chase': 0, 'early_take': 0, 'held_loser': 0, 'unplanned': 0, 'pending': pending}
    for flag in flags or []:
        totals[flag] += 1
    return {'card_id': card_id, 'ticker': ticker, 'end_kind': end_kind, 'end_text': {'position_closed': '平仓',
            'stop': '止损'}[end_kind], 'end_session': end_session, 'execution_text': execution_text,
            'actual_r': actual_r, 'filled': filled, 'rule_r': rule_r, 'agent_r': agent_r, 'status': status, 'headline': headline,
            'discipline': totals}


def _perf(ref, title, rows, *, count=None, determined=None, r_mean=None):
    return {'ref': ref, 'title': title, 'summary': {
        'rows': rows, 'sample': {'count': count if count is not None else len(rows),
                                 'determined': determined if determined is not None else len(rows),
                                 'expired': 0, 'r_mean': r_mean, 'r_median': r_mean}}}


def test_learning_stats_three_keys():
    cards = [_card(1, end_session=date(2026, 10, 20), flags=['chase'], pending=1),
             _card(2, end_session=date(2026, 10, 22), ticker='AMD', end_kind='stop', status='reviewed',
                   flags=['early_take', 'unplanned'], actual_r=None, rule_r=-1.0, agent_r=-1.0, filled=True),
             _card(3, end_session=date(2026, 10, 18)),              # 上周结束：不算
             _card(4, end_session=date(2026, 10, 26))]              # 下周结束：不算
    perf = {7: _perf('know:7@2', '回踩不追', [{'outcome_date': '2026-10-21'}, {'outcome_date': '2026-10-23'},
                                              {'outcome_date': '2026-10-10'}], r_mean=0.4),
            8: _perf('know:8@1', '没有本周结果', [{'outcome_date': '2026-10-10'}, {'outcome_date': None}])}
    out = review.learning_stats(week_start=WEEK, week_end=WEEK_END, cards=cards, perf_by_item=perf)
    assert set(out) == {'discipline', 'cards', 'counts', 'knowledge'}
    assert out['discipline'] == {'cards': 2, 'chase': 1, 'early_take': 1, 'held_loser': 0, 'unplanned': 1,
                                 'pending': 1}
    assert [c['card_id'] for c in out['cards']] == [1, 2]                  # 按结束日
    assert set(out['cards'][0]) == {'card_id', 'ticker', 'end_kind', 'end_text', 'execution_text', 'actual_r',
                                    'filled', 'rule_r', 'agent_r', 'flags', 'status', 'headline'}
    assert [c['filled'] for c in out['cards']] == [False, True]          # card 2 was bought but has no R
    assert out['cards'][0]['flags'] == ['chase'] and out['cards'][1]['flags'] == ['early_take', 'unplanned']
    assert out['cards'][1]['actual_r'] is None and out['cards'][1]['agent_r'] == -1.0
    assert out['counts'] == {'total': 2, 'reviewed': 1, 'to_review': 1, 'drafting': 0, 'draft_failed': 0}
    assert out['knowledge'] == [{'ref': 'know:7@2', 'title': '回踩不追', 'new_outcomes': 2,
                                 'sample': {'count': 3, 'determined': 3, 'expired': 0, 'r_mean': 0.4,
                                            'r_median': 0.4}}]


def test_learning_stats_empty_and_json_safe():
    out = review.learning_stats(week_start=WEEK, week_end=WEEK_END, cards=[], perf_by_item={})
    assert out == {'discipline': {'cards': 0, 'chase': 0, 'early_take': 0, 'held_loser': 0, 'unplanned': 0,
                                  'pending': 0},
                   'cards': [], 'counts': {'total': 0, 'reviewed': 0, 'to_review': 0, 'drafting': 0,
                                           'draft_failed': 0},
                   'knowledge': []}
    import json
    json.dumps(review.learning_stats(week_start=WEEK, week_end=WEEK_END, cards=[_card(1)], perf_by_item={}))


def test_weekly_stats_carries_learning_when_given():
    learning = review.learning_stats(week_start=WEEK, week_end=WEEK_END, cards=[], perf_by_item={})
    stats = review.weekly_stats(WEEK, [], {}, [], segments=[], entry_opens={}, learning=learning)
    assert stats['learning'] == learning and stats['calc_version'] == 'review-8'
