"""v3 market factors: trigger mirroring, setup follow-through, industry strength, regime tier."""

import pytest
from test_workbench_rules import T, breakout, series, uptrend_with_dip

from investment_assistant.analysis import rules
from investment_assistant.config import V3Config
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.facts import factors


def followthrough(trigger_day, ret, *, sessions_after=8):
    """A volume breakout on ``trigger_day`` (breakout(2.0) shape), then a geometric path to
    ``ret`` over the next 5 sessions and flat bars through ``sessions_after``."""
    closes = [100 + (i % 5) * 0.1 for i in range(249)] + [105]
    volumes = [1000] * 249 + [2000]
    closes = [c + i * 0.05 for i, c in enumerate(closes)]
    bars = list(series(closes, volumes=volumes, end=trigger_day))
    last = bars[-1]['close']
    for k in range(1, sessions_after + 1):
        price = last * (1 + ret) ** (min(k, 5) / 5)
        bars.append({'id': len(bars) + 1, 'price_date': add_trading_days(trigger_day, k),
                     'open': price, 'high': price, 'low': price, 'close': price,
                     'adj_close': price, 'volume': 1000, 'split_factor': 1.0})
    return bars


def ind_stock(target, *, bars=60):
    """40 flat bars then a linear ramp to ``target`` over the last 20 (rs20 = target/100 - 1)."""
    closes = [100.0] * (bars - 20) + [100.0 + (target - 100.0) * k / 20 for k in range(1, 21)]
    return series(closes)


SPY_FLAT = series([100.0] * 60)


def test_trigger_rules_on_mirrors_screen():
    assert factors.trigger_rules_on(breakout(2.0), T) == ['volume_breakout']
    assert factors.trigger_rules_on(series(uptrend_with_dip(3)), T) == ['trend_reclaim']
    assert factors.trigger_rules_on(series([100.0] * 250), T) == []


def test_sell_candidate_on_the_day_suppresses_triggers(monkeypatch):
    # 与 screen_rules 判定触发组的条件相同：当天有卖出候选 -> 不算触发
    def with_sell(ticker, day, bars, filings, position, plan_state=None):
        return {'candidates': [rules.Candidate('volume_breakout', 'buy', {}),
                               rules.Candidate('trend_break', 'sell', {})], 'skips': []}

    monkeypatch.setattr(factors, '_evaluate_rules', with_sell)
    assert factors.trigger_rules_on(breakout(2.0), T) == []


def test_setup_followthrough_counts_and_tiers():
    trigger_day = add_trading_days(T, -8)
    rets = [0.03] * 8 + [-0.02] * 4
    bars = {f'S{i:02d}': followthrough(trigger_day, ret) for i, ret in enumerate(rets, start=1)}
    cfg = V3Config(min_triggers=10)
    out = factors.setup_followthrough(bars, session=T, cfg=cfg)['volume_breakout']
    assert (out['n'], out['wins']) == (12, 8)
    assert out['win_rate'] == pytest.approx(8 / 12, abs=1e-4)
    assert out['mean_return'] == pytest.approx((8 * 0.03 - 4 * 0.02) / 12, abs=1e-4)
    assert out['tier'] == 'favorable'
    window_end = add_trading_days(T, -cfg.hold_sessions)
    assert out['window'] == [add_trading_days(window_end, -(cfg.replay_sessions - 1)).isoformat(),
                             window_end.isoformat()]
    # 样本下限抬到 13 -> 样本不足
    strict = factors.setup_followthrough(bars, session=T, cfg=V3Config(min_triggers=13))
    assert strict['volume_breakout']['tier'] == 'insufficient'
    # 4 涨 8 跌 -> 不利
    losing = {f'S{i:02d}': followthrough(trigger_day, ret)
              for i, ret in enumerate([0.03] * 4 + [-0.02] * 8, start=1)}
    assert factors.setup_followthrough(losing, session=T, cfg=cfg)['volume_breakout']['tier'] == 'unfavorable'
    # 7 涨（58%，win_hi=0.6）且均值为正 -> 中性
    mid = factors.setup_followthrough(
        {f'S{i:02d}': followthrough(trigger_day, ret)
         for i, ret in enumerate([0.03] * 7 + [-0.02] * 5, start=1)},
        session=T, cfg=cfg)['volume_breakout']
    assert mid['tier'] == 'neutral' and mid['win_rate'] == pytest.approx(7 / 12, abs=1e-4)


def test_setup_followthrough_window_and_missing_forward_bar():
    cfg = V3Config()
    window_end = add_trading_days(T, -cfg.hold_sessions)
    window_start = add_trading_days(window_end, -(cfg.replay_sessions - 1))
    # 触发日在 T-4：持有期没走完（且已出窗）-> 不计入
    late = {'L1': followthrough(add_trading_days(T, -4), 0.03)}
    assert factors.setup_followthrough(late, session=T, cfg=cfg)['volume_breakout']['n'] == 0
    # 触发日在窗口最早一天之前 -> 不计入
    early = {'E1': followthrough(add_trading_days(window_start, -1), 0.03)}
    assert factors.setup_followthrough(early, session=T, cfg=cfg)['volume_breakout']['n'] == 0
    # 触发后只有 3 根后续日线 -> 不计入，也不抛异常
    short = {'X1': followthrough(add_trading_days(T, -8), 0.03, sessions_after=3)}
    assert factors.setup_followthrough(short, session=T, cfg=cfg)['volume_breakout']['n'] == 0
    # 没有任何触发时每条 BUY_RULES 都在结果里
    empty = factors.setup_followthrough({}, session=T, cfg=cfg)
    assert set(empty) == set(factors.BUY_RULES)
    for stat in empty.values():
        assert (stat['n'], stat['win_rate'], stat['mean_return']) == (0, None, None)
        assert stat['tier'] == 'insufficient'


def test_industry_strength_ranks_groups():
    plan = {'g1': [110.0] * 3, 'g2': [108.0] * 3,
            'g3': [106.0, 106.0, 99.0], 'g4': [104.0, 99.0, 99.0],
            'g5': [99.0] * 3, 'g6': [98.0] * 3}
    bars, groups = {}, {}
    for group, targets in plan.items():
        for i, target in enumerate(targets):
            ticker = f'{group}{i}'
            bars[ticker] = ind_stock(target)
            groups[ticker] = group
    out = factors.industry_strength(bars, SPY_FLAT, groups, session=T, cfg=V3Config())
    assert [out[g]['tier'] for g in ('g1', 'g2', 'g3', 'g4', 'g5', 'g6')] == \
        ['favorable', 'favorable', 'neutral', 'neutral', 'unfavorable', 'unfavorable']
    assert [out[g]['rank'] for g in ('g1', 'g2', 'g3', 'g4', 'g5', 'g6')] == [1, 2, 3, 4, 5, 6]
    assert out['g1']['members'] == 3 and out['g1']['eligible'] == 3
    assert out['g1']['pct_above_50'] == 1.0
    assert out['g3']['pct_above_50'] == pytest.approx(2 / 3) and out['g5']['pct_above_50'] == 0.0
    assert out['g1']['median_rs20'] == pytest.approx(0.10) and out['g6']['median_rs20'] == pytest.approx(-0.02)


def test_unclassified_and_small_groups_are_insufficient():
    cfg = V3Config()
    bars = {'a1': ind_stock(110.0), 'a2': ind_stock(109.0),
            'b1': ind_stock(108.0), 'b2': ind_stock(107.0), 'b3': ind_stock(106.0),
            'n1': ind_stock(105.0), 'n2': ind_stock(104.0)}
    groups = {'a1': 'small', 'a2': 'small', 'b1': 'big', 'b2': 'big', 'b3': 'big',
              'n1': None, 'n2': None}
    out = factors.industry_strength(bars, SPY_FLAT, groups, session=T, cfg=cfg)
    assert set(out) == {'small', 'big'}                    # groups 里值为 None 的股票不形成组
    assert out['small']['tier'] == 'insufficient' and out['small']['rank'] is None
    assert (out['small']['members'], out['small']['eligible']) == (2, 2)
    assert out['small']['pct_above_50'] == 1.0             # 不参与排名也给出原始统计
    # 只有 1 个合格组（k < 3）-> 合格组全部中性，名次照排
    assert out['big']['tier'] == 'neutral' and out['big']['rank'] == 1
    # 不足 21 根日线：计入成员、不算 eligible，相对强弱跳过它
    tiny = {'t1': ind_stock(110.0), 't2': ind_stock(109.0), 't3': ind_stock(108.0),
            't4': series([100.0] * 10)}
    out2 = factors.industry_strength(tiny, SPY_FLAT, {t: 'tiny' for t in tiny},
                                     session=T, cfg=cfg)
    assert (out2['tiny']['members'], out2['tiny']['eligible']) == (4, 3)
    assert out2['tiny']['pct_above_50'] == 1.0
    assert out2['tiny']['median_rs20'] == pytest.approx(0.09)


def test_regime_tier():
    assert [factors.regime_tier(x) for x in ('risk_on', 'neutral', 'risk_off', 'insufficient', 'other')] == \
        ['favorable', 'neutral', 'unfavorable', 'insufficient', 'insufficient']
