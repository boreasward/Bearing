from decimal import Decimal
from pathlib import Path

import pytest

from investment_assistant.data.sec.extraction import analyze_filing, analyze_files
from investment_assistant.data.sec.report import build_filing_report, filing_notification, write_filing_report


def context(key, start="2025-01-01", end="2025-12-31", *, dimension=False):
    segment = '<xbrli:segment><xbrldi:explicitMember dimension="us-gaap:SegmentsAxis">example:Segment</xbrldi:explicitMember></xbrli:segment>' if dimension else ''
    return f'<xbrli:context id="{key}"><xbrli:entity>{segment}</xbrli:entity><xbrli:period><xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>'


def value(concept, text, *, ctx="current", unit="usd", scale="6", extra="", id="fact"):
    return f'<ix:nonFraction name="us-gaap:{concept}" contextRef="{ctx}" unitRef="{unit}" scale="{scale}" id="{id}" {extra}>{text}</ix:nonFraction>'


def document(facts, *, contexts=None, end="December 31, 2025"):
    return f'''<?xml version="1.0" encoding="UTF-8"?><html><body><!-- SEC comment -->
    <ix:nonNumeric name="dei:DocumentPeriodEndDate">{end}</ix:nonNumeric>
    <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
    <xbrli:unit id="eps"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>
    {contexts or context('current')}{facts}</body></html>'''


def meta(form="10-K"):
    return {"ticker": "TEST", "form": form, "filing_date": "2026-02-01", "accession_number": "0000000001-26-000001",
            "path": "/source/main.htm", "edgar_url": "https://www.sec.gov/Archives/main.htm"}


def test_scaled_facts_periods_sign_and_calculated_margins_are_source_grounded():
    raw = document(value("Revenues", "1,200", id="revenue") + value("GrossProfit", "480")
                   + value("OperatingIncomeLoss", "60", extra='sign="-"')
                   + value("EarningsPerShareDiluted", "2.15", unit="eps", scale="0"))
    a = analyze_filing(meta(), raw)
    assert Decimal(a['facts']['revenue']['value']) == 1200000000
    assert a['facts']['revenue']['period'] == "2025-01-01 至 2025-12-31"
    assert a['facts']['gross_margin']['display'] == "40.00%"
    assert a['facts']['operating_margin']['display'] == "-5.00%"
    assert a['facts']['eps_diluted']['display'] == "2.15 美元/股"
    assert a['facts']['gross_margin']['method'] == 'calculated'
    assert len(a['facts']['gross_margin']['sources']) == 2
    assert a['facts']['revenue']['sources'][0]['locator'] == 'revenue'
    assert 'revenue' in a['facts'] and 'eps_non_gaap' not in a['facts']


def test_quarter_ignores_ytd_comparatives_and_segment_revenue():
    contexts = context('current', '2025-10-01') + context('ytd') + context('prior', '2024-10-01', '2024-12-31') + context('segment', '2025-10-01', dimension=True)
    raw = document(value('Revenues', '250') + value('Revenues', '900', ctx='ytd')
                   + value('Revenues', '200', ctx='prior') + value('Revenues', '77', ctx='segment'), contexts=contexts)
    facts = analyze_filing(meta('10-Q'), raw)['facts']
    assert facts['revenue']['value'] == '250000000'
    assert facts['revenue']['period'] == '2025-10-01 至 2025-12-31'


def test_conflicting_current_facts_are_not_selected():
    a = analyze_filing(meta(), document(value('Revenues', '1200') + value('Revenues', '1300')))
    assert 'revenue' not in a['facts']
    assert any('冲突' in x for x in a['warnings'])


@pytest.mark.parametrize('extra,text', [('xsi:nil="true"', '900'), ('format="ixt:unknown"', '900')])
def test_nil_and_unsupported_transforms_are_not_fabricated(extra, text):
    a = analyze_filing(meta(), document(value('Revenues', text, extra=extra)))
    assert 'revenue' not in a['facts']


def test_numeric_comma_transform_and_excluded_footnote():
    raw = document(value('Revenues', '1.200,5<ix:exclude> (1)</ix:exclude>', extra='format="ixt:num-comma-decimal"'))
    assert analyze_filing(meta(), raw)['facts']['revenue']['value'] == '1200500000.0'


def test_missing_cover_period_does_not_guess_latest_number():
    a = analyze_filing(meta(), document(value('Revenues', '1200'), end='unrecognized'))
    assert not a['facts'] and a['warnings']


def earnings_release():
    return '''<html><body><table>
    <tr><td colspan="7">Quarterly Financial Results (in millions, except per share amounts)</td></tr>
    <tr><td rowspan="2">Metric</td><td colspan="3">GAAP(1)</td><td colspan="3">Non-GAAP(2)</td></tr>
    <tr><td>FQ4-25</td><td>FQ3-25</td><td>FQ4-24</td><td>FQ4-25</td><td>FQ3-25</td><td>FQ4-24</td></tr>
    <tr><td>Revenue</td><td>$120</td><td>$100</td><td>$90</td><td>$120</td><td>$100</td><td>$90</td></tr>
    <tr><td>Gross margin</td><td>48</td><td>39</td><td>36</td><td>50</td><td>41</td><td>37</td></tr>
    <tr><td>Percent of revenue</td><td>40%</td><td>39%</td><td>40%</td><td>41.7%</td><td>41%</td><td>41.1%</td></tr>
    <tr><td>Diluted earnings per share</td><td>2.10</td><td>1.80</td><td>1.50</td><td>2.30</td><td>2.00</td><td>1.60</td></tr></table>
    <table><tr><td>FQ1-26</td><td>GAAP Outlook</td><td>Non-GAAP Outlook</td></tr>
    <tr><td>Revenue</td><td>$130 million</td><td>$130 million</td></tr></table></body></html>'''


def test_earnings_table_separates_gaap_non_gaap_and_keeps_outlook_headers():
    a = analyze_filing(meta('8-K'), '<html>Item 2.02</html>', attachments=[{'path': '/source/ex99.htm', 'url': 'https://www.sec.gov/ex99.htm', 'text': earnings_release()}])
    assert a['facts']['revenue']['value'] == '120000000'
    assert a['facts']['gross_margin']['display'] == '40.00%'
    assert a['facts']['eps_diluted']['display'] == '2.10 美元/股'
    assert a['facts']['eps_non_gaap']['display'] == '2.30 美元/股'
    assert a['facts']['eps_non_gaap']['basis'] == 'non-GAAP'
    assert a['facts']['revenue']['period'] == 'FQ4-25'
    assert 'Non-GAAP Outlook' in a['guidance'][0]['quote']
    assert a['guidance'][0]['quote'].count('$130 million') == 2
    assert a['facts']['revenue']['sources'][0]['path'] == '/source/ex99.htm'


def test_regulatory_expectations_and_risk_language_are_not_guidance():
    raw = '''<html><div>Our products are governed by regulations and customer expectations. In 2025 we spent $50 million in capital expenditures.</div>
    <div>We may be unable to obtain future incentives for capital expenditures of $20 million, which may adversely impact profitability.</div>
    <div>We expect revenue of $150 million for the first quarter of 2026, supported by existing orders.</div></html>'''
    a = analyze_filing(meta('8-K'), raw)
    assert len(a['guidance']) == 1
    assert '$150 million' in a['guidance'][0]['quote']


def test_nonfinancial_8k_is_a_business_update_not_earnings():
    raw = '<html><div>The Company announced a Neutron development update and now expects the inaugural launch to occur in 2027.</div></html>'
    a = analyze_filing(meta('8-K'), raw)
    assert a['kind'] == 'business_update' and not a['facts']
    assert '2027' in a['developments'][0]['quote']
    m = {**meta('8-K'), 'analysis': a}
    notification = filing_notification(m, ['/report/example.md'])
    assert '2027' in notification and '完整报告' in notification
    assert '【SEC公告】' in notification and len(notification) <= 2000


def test_report_has_traceable_facts_and_preserves_existing_report(tmp_path):
    m = meta()
    raw = document(value('Revenues', '1200'))
    text = build_filing_report(m, raw)
    assert '1,200.00 百万美元' in text and '2025-01-01' in text
    assert 'SHA-256' in text and 'https://www.sec.gov/Archives/main.htm#fact' in text
    assert '未自动提取' in text and '不等同于公司“未披露”' in text
    path = write_filing_report(tmp_path, m, raw)
    before = path.read_bytes()
    assert write_filing_report(tmp_path, m, document(value('Revenues', '9999'))) is None
    assert path.read_bytes() == before


def test_missing_exhibit_cannot_be_treated_as_an_empty_analysis(tmp_path):
    p = tmp_path/'main.htm'; p.write_text(document(value('Revenues', '1200')))
    m = {**meta(), 'path': str(p), 'attachments': [str(tmp_path/'missing.htm')]}
    with pytest.raises(FileNotFoundError):
        analyze_files(m)


def test_service_analyzes_exhibit_and_monitor_notification_has_financial_values(tmp_path, monkeypatch):
    from investment_assistant.data.sec.storage import _write_reports
    main = tmp_path/'main.htm'; main.write_text('<html>Item 2.02</html>')
    exhibit = tmp_path/'ex99.htm'; exhibit.write_text(earnings_release())
    monkeypatch.setenv('FILINGS_REPORT_DIR', str(tmp_path/'reports'))
    m = {**meta('8-K'), 'path': str(main), 'attachments': [str(exhibit)],
         'document_urls': {str(exhibit): 'https://www.sec.gov/ex99.htm'}}
    paths = _write_reports([m])
    assert len(paths) == 1 and '120.00 百万美元' in Path(paths[0]).read_text()
    notice = filing_notification(m, paths)
    assert '120.00 百万美元' in notice and '2.30 美元/股' in notice
    assert 'https://www.sec.gov/ex99.htm' in notice
    before = Path(paths[0]).read_bytes()
    assert _write_reports([m]) == paths
    assert Path(paths[0]).read_bytes() == before


def test_unparsed_annual_report_is_not_mislabeled_as_a_nonfinancial_announcement():
    m = meta()
    m['analysis'] = analyze_filing(m, '<html>Unsupported annual report layout.</html>')
    notice = filing_notification(m, [])
    assert '【SEC财报】' in notice and '需核查原文' in notice
    assert '不按财报结果' not in notice
