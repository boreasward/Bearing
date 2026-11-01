"""Characterization of run_batch's summary, pinned before extracting collect.py (v2.3 Task 4).

The batch below fails in every collection family on purpose: SMH (ETF bars),
BBB (bars), AAA (news), SEC listings (discover raises) and FRED (not configured),
while the screen stage picks from a synthetic 30-ticker range containing CCC and
DDD. The expectations were captured from the current code's actual output and
then hand-checked key by key; timings, screen.timing and screen.screen_run_id
are stripped as nondeterministic.
"""
from datetime import timedelta

import pandas as pd

from test_workbench_service import NOW, SESSION, Sources, db, deps, seed_watchlist
from investment_assistant.data.calendar import add_trading_days
import investment_assistant.core.tx as service
import investment_assistant.workflow.batch as d_workflow_batch
from investment_assistant.data.adapters import universe as universe_module

HISTORY = 299
DAYS_BACK = [add_trading_days(SESSION, -(HISTORY - i)) for i in range(HISTORY + 1)]
# The manual pool (AAA/BBB/CCC) is never screened; CCC is also in the range.
UNIVERSE = ('CCC', 'DDD', *(f'U{i:02d}' for i in range(2, 30)))


def _trigger_path(start):
    """Uptrend, 3-day dip, reclaim on SESSION (closes; pad 0)."""
    up = [start + i * 0.25 for i in range(HISTORY - 3)]
    closes = up + [up[-1] - 6] * 3 + [up[-1] + 1]
    return dict(zip(DAYS_BACK, closes))


def _series(ticker):
    """{date: (close, pad)} for one range ticker: CCC/DDD/U02 trigger on SESSION,
    U03-U24 form the setup band with descending relative strength, U25-U29 sit
    far below the ATR band."""
    if ticker in ('CCC', 'DDD', 'U02'):
        path = _trigger_path({'CCC': 50.0, 'DDD': 60.0, 'U02': 70.0}[ticker])
        return {d: (c, 0.0) for d, c in path.items()}
    i = int(ticker[1:])
    if 3 <= i <= 24:
        step = 0.30 - 0.01 * (i - 3)
        closes = [100.0 + step * k for k in range(len(DAYS_BACK))]
        return {d: (c, c * 0.02) for d, c in zip(DAYS_BACK, closes)}
    closes = [100.0 + 0.1 * k for k in range(len(DAYS_BACK))]
    return {d: (c, c * 0.001) for d, c in zip(DAYS_BACK, closes)}


class BatchSources(Sources):
    """Sources(fail={'SMH', 'BBB'}, fail_news={'AAA'}) with SEC down.

    Range tickers get the screening-style series; SPY, the ETFs and the manual
    pool get test_workbench_service's default frame."""

    def __init__(self, fail={'SMH', 'BBB'}, fail_news={'AAA'}):
        super().__init__(fail=fail, fail_news=fail_news)

    def bars(self, ticker, start, end_exclusive):
        if ticker not in UNIVERSE:
            return super().bars(ticker, start, end_exclusive)
        end = end_exclusive - timedelta(days=1)
        rows = [(d, c, p) for d, (c, p) in _series(ticker).items() if start <= d <= end]
        index = pd.DatetimeIndex([pd.Timestamp(d, tz='America/New_York') for d, _, _ in rows])
        closes = [c for _, c, _ in rows]
        pads = [p for _, _, p in rows]
        return pd.DataFrame({'Open': closes, 'High': [c + p for c, p in zip(closes, pads)],
                             'Low': [c - p for c, p in zip(closes, pads)], 'Close': closes,
                             'Adj Close': closes, 'Volume': [1_000_000] * len(rows),
                             'Stock Splits': [0.0] * len(rows)}, index=index)

    def discover(self, tickers, start, end):
        raise RuntimeError('edgar down')


def batch_deps(fail={'SMH', 'BBB'}, fail_news={'AAA'}):
    d = deps(BatchSources(fail=fail, fail_news=fail_news))
    d.universe = universe_module.Universe('test', SESSION, 'test', UNIVERSE)
    return d


def _reduced(summary):
    """The deterministic comparison keys: everything the collection stage writes."""
    out = {k: summary[k] for k in ('benchmark', 'etfs', 'tickers', 'filings', 'macro',
                                   'sources', 'errors', 'pool')}
    out['screen'] = {k: summary['screen'][k]
                     for k in ('status', 'selected', 'members', 'counts') if k in summary['screen']}
    return out


def _member(bars, candidates):
    """One screen-member ticker entry: bars fetched by the screen, news stored by the batch."""
    return {'bars': bars, 'skipped_bars': [], 'news': {'existing': 0, 'fetched': 1,
                                                       'inserted': 1, 'skipped': 0},
            'candidates': candidates, 'skips': [], 'error': None}


WATCHLIST_EXPECTED = {
    'benchmark': {'bars': {'inserted': 300, 'unchanged': 0}, 'skipped_bars': [], 'news': None,
                  'candidates': [], 'skips': [], 'error': None},
    'etfs': {'QQQ': 'inserted 300', 'SMH': 'error: provider down', 'XLK': 'inserted 300'},
    'tickers': {
        'AAA': {'bars': {'inserted': 300, 'unchanged': 0}, 'skipped_bars': [], 'news': None,
                'candidates': ['trend_reclaim', 'abnormal_move'], 'skips': [],
                'error': 'news: news down'},
        'BBB': {'bars': None, 'skipped_bars': [],
                'news': {'existing': 0, 'fetched': 1, 'inserted': 1, 'skipped': 0},
                'candidates': [],
                'skips': ['abnormal_move:missing_signal_bar', 'earnings_gap_down:missing_signal_bar',
                          'earnings_gap_up:missing_signal_bar', 'plan_stop:missing_signal_bar',
                          'plan_target:missing_signal_bar', 'position_drawdown:missing_signal_bar',
                          'trend_break:missing_signal_bar', 'trend_reclaim:missing_signal_bar',
                          'volume_breakout:missing_signal_bar'],
                'error': 'bars: provider down'},
        # CCC is collected by the batch itself with the 420-day lookback, which trims the
        # 300-trading-day series to 290 calendar-window rows; the range tickers below were
        # fetched by the screen stage (400-day lookback) and are not fetched again.
        'CCC': {'bars': {'inserted': 290, 'unchanged': 0}, 'skipped_bars': [],
                'news': {'existing': 0, 'fetched': 1, 'inserted': 1, 'skipped': 0},
                'candidates': ['trend_reclaim', 'abnormal_move'], 'skips': [], 'error': None},
        'DDD': _member('collected_in_screen', ['trend_reclaim', 'abnormal_move']),
        'U02': _member('collected_in_screen', ['trend_reclaim', 'abnormal_move']),
        # The setup band's near-constant daily returns make stdev ~0, so abnormal_move
        # (a sigma watch rule) fires for every setup ticker: current-code behavior.
        **{f'U{i:02d}': _member('collected_in_screen', ['abnormal_move']) for i in range(3, 21)},
    },
    'filings': {'error': 'edgar down'},
    'macro': {'BAMLH0A0HYM2': 'not_configured', 'T10Y2Y': 'not_configured'},
    'sources': {'bars': ['BBB'], 'news': ['AAA'], 'earnings': [], 'sec': ['all'],
                'etf_bars': ['SMH'], 'fred': [], 'regime': [], 'agent': []},
    'errors': ['SMH etf_bars: provider down', 'AAA news: news down', 'BBB bars: provider down',
               'SEC listings: edgar down'],
    'pool': {'core': 1, 'extended': 22, 'holdings': 0, 'screen': 20, 'chains': 0,
             'changes': [{'ticker': 'AAA', 'tier': 'core', 'sector_etf': None,
                          'source': 'watchlist'},
                         {'ticker': 'BBB', 'tier': 'extended', 'sector_etf': None,
                          'source': 'watchlist'},
                         {'ticker': 'CCC', 'tier': 'extended', 'sector_etf': None,
                          'source': 'watchlist'},
                         *({'ticker': t, 'tier': 'extended', 'sector_etf': None,
                            'source': 'screen'}
                           for t in ['DDD', 'U02', *map('U{:02d}'.format, range(3, 21))])]},
    'screen': {'status': 'success',
               'selected': ['DDD', 'U02', 'U03', 'U04', 'U05', 'U06', 'U07', 'U08', 'U09',
                            'U10', 'U11', 'U12', 'U13', 'U14', 'U15', 'U16', 'U17', 'U18',
                            'U19', 'U20'],
               'members': ['DDD', 'U02', 'U03', 'U04', 'U05', 'U06', 'U07', 'U08', 'U09', 'U10',
                           'U11', 'U12', 'U13', 'U14', 'U15', 'U16', 'U17', 'U18', 'U19', 'U20'],
               'counts': {'universe': 29, 'data_skipped': 0, 'illiquid': 0, 'earnings_soon': 0,
                          'no_setup': 5, 'trigger': 2, 'setup': 22, 'selected': 20}},
}

EXPLICIT_EXPECTED = {
    'benchmark': {'bars': {'inserted': 300, 'unchanged': 0}, 'skipped_bars': [], 'news': None,
                  'candidates': [], 'skips': [], 'error': None},
    'etfs': {'QQQ': 'inserted 300', 'SMH': 'error: provider down', 'XLK': 'inserted 300'},
    'tickers': {'NVDA': {'bars': {'inserted': 300, 'unchanged': 0}, 'skipped_bars': [],
                         'news': {'existing': 0, 'fetched': 1, 'inserted': 1, 'skipped': 0},
                         'candidates': ['trend_reclaim', 'abnormal_move'], 'skips': [],
                         'error': None}},
    'filings': {'error': 'edgar down'},
    'macro': {'BAMLH0A0HYM2': 'not_configured', 'T10Y2Y': 'not_configured'},
    'sources': {'bars': [], 'news': [], 'earnings': [], 'sec': ['all'], 'etf_bars': ['SMH'],
                'fred': [], 'regime': [], 'agent': []},
    'errors': ['SMH etf_bars: provider down', 'SEC listings: edgar down'],
    'pool': {'core': 1, 'extended': 0, 'holdings': 0, 'screen': None, 'changes': [], 'chains': 0},
    'screen': {'status': 'skipped'},
}


def test_characterization_watchlist_and_screen(db):
    seed_watchlist(db, [('AAA', ['core']), ('BBB', []), ('CCC', [])])
    actual = _reduced(d_workflow_batch.run_batch(now=NOW, deps=batch_deps()))
    assert actual == WATCHLIST_EXPECTED


def test_characterization_explicit_tickers(db):
    actual = _reduced(d_workflow_batch.run_batch(tickers=['NVDA'], now=NOW, deps=batch_deps()))
    assert actual == EXPLICIT_EXPECTED


def test_characterization_spy_failure(db):
    """spec §6.1 guardrail the original capture skipped (M05): SPY's own bars
    failing. The benchmark error must land in summary and the fetch-run row —
    the denominator stays the real target set, '*' never appears. The screen's
    benchmark is QQQ, so screening itself still succeeds here."""
    seed_watchlist(db, [('AAA', ['core']), ('BBB', []), ('CCC', [])])
    summary = d_workflow_batch.run_batch(now=NOW, deps=batch_deps(fail={'SPY', 'SMH', 'BBB'}),
                                record_trigger='scheduled')
    assert {k: summary[k] for k in ('benchmark', 'etfs', 'sources', 'errors')} == {
        'benchmark': {'bars': None, 'skipped_bars': [], 'news': None, 'candidates': [],
                      'skips': [], 'error': 'bars: provider down'},
        'etfs': {'QQQ': 'inserted 300', 'SMH': 'error: provider down', 'XLK': 'inserted 300'},
        'sources': {'bars': ['SPY', 'BBB'], 'news': ['AAA'], 'earnings': [], 'sec': ['all'],
                    'etf_bars': ['SMH'], 'fred': [], 'regime': [], 'agent': []},
        'errors': ['SPY bars: provider down', 'SMH etf_bars: provider down',
                   'AAA news: news down', 'BBB bars: provider down',
                   'SEC listings: edgar down'],
    }
    # 4 (SPY/ETFs) + 29 screen-collected range tickers + 3 manual-pool tickers;
    # SPY, SMH, BBB failed and nothing else.
    row = db.execute("SELECT status, target_count, ok_count, failures FROM data_fetch_runs "
                     "WHERE family = 'bars' ORDER BY id DESC LIMIT 1").fetchone()
    assert row == ('partial', 36, 33, [{'target': 'BBB', 'reason': 'provider down'},
                                       {'target': 'SMH', 'reason': 'provider down'},
                                       {'target': 'SPY', 'reason': 'provider down'}])
    sec = db.execute("SELECT status, target_count, failures FROM data_fetch_runs "
                     "WHERE family = 'sec_filings' ORDER BY id DESC LIMIT 1").fetchone()
    # v2.8 D4 / D21: the family row covers the collect node's list (the 3 pool tickers), one target each; the screen
    # members are back-filled by the screen node (the shell writes no rows for them); no '*' here.
    assert sec == ('failed', 3, [{'target': 'AAA', 'reason': 'edgar down'}, {'target': 'BBB', 'reason': 'edgar down'},
                                 {'target': 'CCC', 'reason': 'edgar down'}])


# ------------------------------------------------------- v2.4 §6.3 stages 护栏
# 开启阶段记账（recorder 不写库）时，summary 与不传 stages 逐字段相同；
# stage_status 由 Task 5 的任务层加，这里还没有。


def _stage_recorder(rows, run_id):
    import investment_assistant.workflow.recorder as stages
    return stages.StageRecorder(run_id, 'manual_batch', SESSION, writer=rows.append)


def test_characterization_watchlist_and_screen_with_stages(db):
    seed_watchlist(db, [('AAA', ['core']), ('BBB', []), ('CCC', [])])
    rows = []
    actual = _reduced(d_workflow_batch.run_batch(now=NOW, deps=batch_deps(),
                                        stages=_stage_recorder(rows, 'run-ch-a')))
    assert actual == WATCHLIST_EXPECTED


def test_characterization_explicit_tickers_with_stages(db):
    rows = []
    actual = _reduced(d_workflow_batch.run_batch(tickers=['NVDA'], now=NOW, deps=batch_deps(),
                                        stages=_stage_recorder(rows, 'run-ch-b')))
    assert actual == EXPLICIT_EXPECTED


def test_characterization_spy_failure_with_stages(db):
    seed_watchlist(db, [('AAA', ['core']), ('BBB', []), ('CCC', [])])
    rows = []
    summary = d_workflow_batch.run_batch(now=NOW, deps=batch_deps(fail={'SPY', 'SMH', 'BBB'}),
                                record_trigger='scheduled', stages=_stage_recorder(rows, 'run-ch-c'))
    assert {k: summary[k] for k in ('benchmark', 'etfs', 'sources', 'errors')} == {
        'benchmark': {'bars': None, 'skipped_bars': [], 'news': None, 'candidates': [],
                      'skips': [], 'error': 'bars: provider down'},
        'etfs': {'QQQ': 'inserted 300', 'SMH': 'error: provider down', 'XLK': 'inserted 300'},
        'sources': {'bars': ['SPY', 'BBB'], 'news': ['AAA'], 'earnings': [], 'sec': ['all'],
                    'etf_bars': ['SMH'], 'fred': [], 'regime': [], 'agent': []},
        'errors': ['SPY bars: provider down', 'SMH etf_bars: provider down',
                   'AAA news: news down', 'BBB bars: provider down',
                   'SEC listings: edgar down'],
    }
    row = db.execute("SELECT status, target_count, ok_count, failures FROM data_fetch_runs "
                     "WHERE family = 'bars' ORDER BY id DESC LIMIT 1").fetchone()
    assert row == ('partial', 36, 33, [{'target': 'BBB', 'reason': 'provider down'},
                                       {'target': 'SMH', 'reason': 'provider down'},
                                       {'target': 'SPY', 'reason': 'provider down'}])
    sec = db.execute("SELECT status, target_count, failures FROM data_fetch_runs "
                     "WHERE family = 'sec_filings' ORDER BY id DESC LIMIT 1").fetchone()
    assert sec == ('failed', 3, [{'target': 'AAA', 'reason': 'edgar down'}, {'target': 'BBB', 'reason': 'edgar down'},
                                 {'target': 'CCC', 'reason': 'edgar down'}])


# ------------------------------------------------------- v2.5 §7.1 summary 护栏
# stages-2 只在 summary 里新增 review 键与 pool.chains 计数；stages-3（v2.7 §9）再新增 retro 键（唯一的结构新增）；
# 其余键与改动前逐字段相同。

SUMMARY_KEYS_V24 = {'session', 'cutoff_at', 'tickers', 'benchmark', 'filings', 'earnings', 'plans', 'conflicts',
                    'errors', 'briefs', 'screen', 'pool', 'etfs', 'macro', 'regime', 'sources', 'timings', 'agent'}
EMPTY_REVIEW = {'objects': 0, 'outcomes': {'maintain': 0, 'revise': 0, 'invalidate': 0, 'skipped': 0, 'error': 0},
                'tokens': {'input': 0, 'output': 0},
                'holding_status': {'rows': 0, 'by_instruction': {'hold': 0, 'sell_half': 0, 'exit': 0, 'no_plan': 0}}}
EMPTY_RETRO = {'new_cards': 0, 'drafts': 0, 'to_review': 0}


def test_batch_characterization(db):
    seed_watchlist(db, [('AAA', ['core']), ('BBB', []), ('CCC', [])])
    rows = []
    summary = d_workflow_batch.run_batch(now=NOW, deps=batch_deps(), stages=_stage_recorder(rows, 'run-ch-review'))
    assert set(summary) == SUMMARY_KEYS_V24 | {'review', 'retro'}
    assert set(summary['pool']) == {'core', 'extended', 'holdings', 'screen', 'changes', 'chains'}
    assert _reduced(summary) == WATCHLIST_EXPECTED
    assert summary['review'] == EMPTY_REVIEW and summary['retro'] == EMPTY_RETRO
    assert set(summary['timings']) == {'collect', 'regime', 'rules', 'agent', 'total'}
    plain = d_workflow_batch.run_batch(tickers=['NVDA'], now=NOW, deps=batch_deps())     # 不记阶段：键集合相同
    assert set(plain) == SUMMARY_KEYS_V24 | {'review', 'retro'} and plain['review'] == EMPTY_REVIEW
    assert plain['retro'] == EMPTY_RETRO
    assert plain['pool'] == EXPLICIT_EXPECTED['pool']
