"""v3 candidate ranking: group, tiers and RS decide the order; reasons are verbatim Chinese."""

from investment_assistant.facts import ranking


def row(ticker, *, group, trigger_rules=(), rs, selected=True, rank=None, atr_pct=0.03):
    return {'ticker': ticker, 'group': group, 'trigger_rules': list(trigger_rules), 'rs': rs,
            'atr_pct': atr_pct, 'rank': rank, 'selected': selected}


def stat(tier, n, wins, mean_return):
    return {'n': n, 'wins': wins, 'win_rate': wins / n if n else None,
            'mean_return': mean_return, 'tier': tier, 'window': ['2026-09-16', '2026-10-15']}


SETUP = {'volume_breakout': stat('favorable', 19, 13, 0.027),
         'trend_reclaim': stat('unfavorable', 12, 3, -0.044)}
INDUSTRY = {'医药化工': {'members': 5, 'eligible': 5, 'pct_above_50': 0.8, 'median_rs20': 0.05,
                       'rank': 1, 'tier': 'favorable'},
            '电子': {'members': 6, 'eligible': 6, 'pct_above_50': 0.1, 'median_rs20': -0.08,
                    'rank': 4, 'tier': 'unfavorable'}}


def test_order_and_reason():
    results = [row('A', group='trigger', trigger_rules=['volume_breakout'], rs=0.50, rank=2),
               row('B', group='trigger', trigger_rules=['volume_breakout'], rs=0.35, rank=1),
               row('C', group='trigger', trigger_rules=['trend_reclaim'], rs=0.90, rank=3),
               row('D', group='setup', rs=0.20, rank=4),
               row('E', group='setup', rs=0.30, rank=5)]
    groups = {'A': '电子', 'B': '医药化工', 'C': '医药化工', 'D': '医药化工', 'E': '电子'}
    out = ranking.rank_candidates(results, setup=SETUP, industry=INDUSTRY, groups=groups)
    assert [r['ticker'] for r in out] == ['B', 'A', 'C', 'D', 'E']
    assert [r['rank'] for r in out] == [1, 2, 3, 4, 5]
    assert out[0]['reason'] == '放量突破 · 形态近期有利（19 次 13 涨，+2.7%）· 医药化工 有利 · RS 0.35'
    assert out[1]['reason'] == '放量突破 · 形态近期有利（19 次 13 涨，+2.7%）· 电子 不利 · RS 0.50'
    assert out[2]['reason'] == '趋势回踩修复 · 形态近期不利（12 次 3 涨，-4.4%）· 医药化工 有利 · RS 0.90'
    assert out[3]['reason'].startswith('形态待触发 · ') and out[3]['setup_tier'] == 'insufficient'
    assert out[3]['screen_group'] == 'setup' and out[0]['screen_group'] == 'trigger'
    assert out[0]['detail'] == {'setup': SETUP['volume_breakout'], 'industry': INDUSTRY['医药化工'],
                                'screen_rank': 1}


def test_unselected_rows_dropped_and_ties_by_ticker():
    results = [row('Z', group='setup', rs=0.10, selected=False, rank=6),
               row('M', group='setup', rs=0.30, rank=4),
               row('K', group='setup', rs=0.30, rank=5)]
    groups = {'Z': '电子', 'M': '电子', 'K': '电子'}
    out = ranking.rank_candidates(results, setup=SETUP, industry=INDUSTRY, groups=groups)
    assert [r['ticker'] for r in out] == ['K', 'M']


def test_two_trigger_rules_use_the_better_tier_and_unknown_industry():
    both = [row('X', group='trigger', trigger_rules=['trend_reclaim', 'volume_breakout'], rs=0.40)]
    out = ranking.rank_candidates(both, setup=SETUP, industry=INDUSTRY, groups={'X': '医药化工'})
    assert out[0]['setup_tier'] == 'favorable'          # 取档位较好的一条
    assert out[0]['reason'].startswith('趋势回踩修复、放量突破 · 形态近期有利（19 次 13 涨，+2.7%）')
    assert out[0]['detail']['setup'] == SETUP['volume_breakout']

    unknown = [row('Y', group='trigger', trigger_rules=['volume_breakout'], rs=0.10)]
    out2 = ranking.rank_candidates(unknown, setup=SETUP, industry=INDUSTRY, groups={'Y': None})
    assert out2[0]['industry'] is None and out2[0]['industry_tier'] == 'insufficient'
    assert '未归类 样本不足' in out2[0]['reason'] and out2[0]['detail']['industry'] is None

    no_data = {'volume_breakout': stat('insufficient', 0, 0, None)}
    out3 = ranking.rank_candidates(
        [row('W', group='trigger', trigger_rules=['volume_breakout'], rs=0.10)],
        setup=no_data, industry=INDUSTRY, groups={'W': '医药化工'})
    assert '形态近期样本不足（0 次）' in out3[0]['reason']


def test_deterministic():
    results = [row('A', group='trigger', trigger_rules=['volume_breakout'], rs=0.50, rank=1),
               row('B', group='trigger', trigger_rules=['volume_breakout'], rs=0.35, rank=2),
               row('C', group='trigger', trigger_rules=['trend_reclaim'], rs=0.90, rank=3),
               row('D', group='setup', rs=0.20, rank=4),
               row('E', group='setup', rs=0.30, rank=5)]
    groups = {'A': '电子', 'B': '医药化工', 'C': '医药化工', 'D': '医药化工', 'E': '电子'}
    first = ranking.rank_candidates(results, setup=SETUP, industry=INDUSTRY, groups=groups)
    shuffled = ranking.rank_candidates(list(reversed(results)), setup=SETUP, industry=INDUSTRY,
                                       groups=groups)
    assert shuffled == first
