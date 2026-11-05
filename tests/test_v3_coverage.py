"""v3 coverage: merge Tally rows per family, then render one Chinese line per family."""
from investment_assistant.facts import coverage


def fetch_row(family, *, status='success', target_count=0, ok_count=0, failures=(), reason=None):
    """A Tally.row() dict with the keys facts.coverage reads."""
    return {'family': family, 'status': status, 'target_count': target_count, 'ok_count': ok_count,
            'failures': list(failures), 'detail': {'reason': reason}}


def test_coverage_merges_rows_and_lists_missing():
    failed = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMD', 'INTC', 'AMZN', 'XLE']   # 8 个，含行业 ETF
    rows = [
        fetch_row('bars', target_count=130, ok_count=122,
                  failures=[{'target': t, 'reason': 'timeout'} for t in failed]),
        fetch_row('bars', target_count=8, ok_count=8),
    ]
    cov = coverage.coverage(rows)
    fam = cov['families']['bars']
    assert (fam['total'], fam['ok']) == (138, 130)
    assert [m['target'] for m in fam['missing']] == sorted(failed)    # 按 target 去重
    assert cov['missing_bars'] == sorted(failed)
    lines = coverage.coverage_lines(cov)
    assert any(l.startswith('日线：130/138，缺 ') for l in lines)
    assert not any('降级' in l or 'degraded' in l or 'partial' in l for l in lines)


def test_missing_targets_dedupe_across_rows():
    rows = [fetch_row('news', target_count=3, ok_count=1,
                      failures=[{'target': 'NVDA', 'reason': 'timeout'}]),
            fetch_row('news', target_count=1, ok_count=0,
                      failures=[{'target': 'NVDA', 'reason': 'timeout'}])]
    fam = coverage.coverage(rows)['families']['news']
    assert [m['target'] for m in fam['missing']] == ['NVDA']


def test_whole_call_failure_and_skip():
    rows = [
        fetch_row('sec_filings', status='failed',
                  failures=[{'target': '*', 'reason': '登录过期'}]),
        fetch_row('fred', status='skipped', reason='FRED 未配置'),
        fetch_row('news', target_count=8, ok_count=8),
    ]
    cov = coverage.coverage(rows)
    lines = coverage.coverage_lines(cov)
    assert 'SEC 申报：整族未取到：登录过期' in lines
    assert '宏观：未拉取：FRED 未配置' in lines
    assert '资讯：8/8' in lines
