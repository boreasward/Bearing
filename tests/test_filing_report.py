from investment_assistant.data.sec.report import build_filing_report


def test_report_keeps_source_and_does_not_invent_numbers():
    text = build_filing_report(
        {
            "ticker": "NVDA",
            "form": "10-Q",
            "filing_date": "2026-08-27",
            "accession_number": "0001045810-26-000123",
            "path": "/tmp/nvda.htm",
        },
        "<html><body>Quarter ended July 26, 2026.</body></html>",
    )
    assert "NVDA" in text
    assert "10-Q" in text
    assert "2026-08-27" in text
    assert "0001045810-26-000123" in text
    assert "/tmp/nvda.htm" in text
    assert "未披露" in text
    assert "买入" not in text
    assert "目标价" not in text
