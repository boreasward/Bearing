"""Data family catalog: order, purposes, classify, sanitize, lagging, coverage (pure)."""
from investment_assistant.data import families as data_families
from datetime import date, datetime, UTC

import investment_assistant.workflow.targets as families
from investment_assistant.tasks import catalog

S = date(2026, 10, 22)

def test_six_families_in_order_and_described():
    assert [f.id for f in data_families.FAMILIES] == ['bars', 'sec_filings', 'news', 'atr', 'fred', 'universe']
    for f in data_families.FAMILIES:
        assert f.title and f.purpose and f.kind in ('fetched', 'derived', 'manual')
        assert catalog.describe(f.job) is not None
    assert data_families.get('atr').fetch_via == 'bars' and data_families.get('atr').kind == 'derived'
    assert data_families.get('universe').kind == 'manual'
    assert {f.id for f in data_families.FAMILIES if f.per_ticker} == {'bars', 'news', 'sec_filings'}

def test_classify():
    assert data_families.classify(2, 0, 'FRED_API_KEY 未配置') == 'skipped'
    assert data_families.classify(3, 3) == 'success'
    assert data_families.classify(3, 0) == 'failed'
    assert data_families.classify(3, 1) == 'partial'

def test_sanitize_reason_redacts_and_truncates(monkeypatch):
    monkeypatch.setenv('FRED_API_KEY', 'sekret123')
    out = families.sanitize_reason('url?api_key=sekret123 ' + 'x' * 400)
    assert 'sekret123' not in out and '***' in out and len(out) == 300

def test_lagging_edges():
    assert data_families.lagging('bars', S, session=S) is False
    assert data_families.lagging('bars', date(2026, 10, 21), session=S) is True
    assert data_families.lagging('atr', S, session=S, bar_count=15) is False
    assert data_families.lagging('atr', S, session=S, bar_count=14) is True
    assert data_families.lagging('fred', date(2026, 10, 15), session=S) is False   # 第 7 天
    assert data_families.lagging('fred', date(2026, 10, 14), session=S) is True    # 第 8 天
    assert data_families.lagging('sec_filings', date(2026, 6, 24), session=S) is False  # 120 天
    assert data_families.lagging('news', datetime(2026, 10, 15, 12, tzinfo=UTC), session=S) is False
    assert data_families.lagging('bars', None, session=S) is True

def test_coverage_counts_missing_as_lagging():
    cov = data_families.coverage('bars', ['A', 'B', 'C'], {'A': S, 'B': date(2026, 10, 20)}, session=S)
    assert cov == {'targets': 3, 'covered': 1, 'lagging': ['B', 'C']}


def test_fred_coverage_counts_any_observation_and_keeps_lagging_separate():
    """FRED 口径（spec §4，I12）：covered=有观测值的序列数；滞后独立按 7 天阈值。
    有值但旧 → 覆盖且滞后；无值 → 不覆盖且滞后。"""
    old = date(2026, 10, 14)          # session 前 8 天：有观测值但滞后
    cov = data_families.coverage('fred', ['BAMLH0A0HYM2', 'T10Y2Y'],
                            {'BAMLH0A0HYM2': old, 'T10Y2Y': old}, session=S)
    assert cov == {'targets': 2, 'covered': 2, 'lagging': ['BAMLH0A0HYM2', 'T10Y2Y']}
    fresh = data_families.coverage('fred', ['BAMLH0A0HYM2', 'T10Y2Y'],
                              {'BAMLH0A0HYM2': S, 'T10Y2Y': None}, session=S)
    assert fresh == {'targets': 2, 'covered': 1, 'lagging': ['T10Y2Y']}


def test_coverage_notes_exist_for_every_family():
    for family in data_families.FAMILIES:
        assert data_families.COVERAGE_NOTES[family.id]
