"""Local research facts and original-file provenance, never online acquisition."""
import hashlib
from datetime import UTC, date, datetime

import pytest

from investment_assistant.analysis.agent import research
from investment_assistant.analysis.agent.tools import ToolContext, ToolError
from investment_assistant.data import repo

NOW = datetime(2026, 10, 30, 22, tzinfo=UTC)


def context(**kwargs):
    return ToolContext('AMD', date(2026, 10, 30), NOW, lambda fn: fn(None), **kwargs)


def test_research_reads_are_ticker_scoped_and_cutoff_bound(monkeypatch):
    seen = []
    def rows(conn, sid, dataset, *, as_of):
        seen.append((sid, dataset, as_of))
        return []
    monkeypatch.setattr(repo, 'read_rows', rows)
    assert research.get_fundamentals(context(), 'AMD')['periods'] == []
    assert research.get_guidance(context(), 'AMD')['records'] == []
    assert all(sid == 'US:stock:AMD' and cutoff == NOW for sid, _, cutoff in seen)
    with pytest.raises(ToolError):
        research.get_fundamentals(context(), 'TSLA')


def test_filing_facts_do_not_cross_unit(monkeypatch):
    monkeypatch.setattr(repo, 'read_rows', lambda *a, **kw: [{'filing_id': 8, 'analysis': {'facts': ['known'], 'warnings': []}}])
    assert research.get_filing_facts(context(), 8)['facts'] == ['known']
    with pytest.raises(ToolError, match='filing_unavailable'):
        research.get_filing_facts(context(), 9)


def test_filing_text_returns_original_hash_and_exact_utf8_byte_range(monkeypatch, tmp_path):
    accession = '0001234567-26-000001'
    folder = tmp_path / 'AMD'
    folder.mkdir()
    path = folder / f'{accession}.txt'
    content = ('前言\nItem 1A. Risk Factors\n' + '风险与竞争。' * 40 + '\nItem 7. Management Discussion\n' + '稳定。' * 70
               + '\nItem 8. Statements\n')
    path.write_text(content)
    monkeypatch.setattr(repo, 'filing_text_at', lambda *a, **kw: {
        'ticker': 'AMD', 'accession_number': accession, 'file_path': str(path), 'downloaded_at': NOW})
    monkeypatch.setattr(repo, 'read_rows', lambda *a, **kw: [])
    result = research.get_filing_text(context(sec_text_dir=tmp_path), 8, 'item_1a')
    assert result['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    start, end = result['byte_range']
    assert path.read_bytes()[start:end].decode() == result['text']
    assert '风险与竞争' in result['text'] and '稳定' not in result['text']
    with pytest.raises(ToolError, match='section_unavailable'):
        research.get_filing_text(context(sec_text_dir=tmp_path), 8, 'item_2_02')


def test_filing_text_rejects_foreign_path_and_unavailable_download(monkeypatch, tmp_path):
    monkeypatch.setattr(repo, 'read_rows', lambda *a, **kw: [])
    monkeypatch.setattr(repo, 'filing_text_at', lambda *a, **kw: None)
    with pytest.raises(ToolError, match='filing_unavailable'):
        research.get_filing_text(context(sec_text_dir=tmp_path), 1, 'summary')
    other = tmp_path / 'outside.txt'
    other.write_text('Item 1A. Risk Factors\nsecret\n')
    monkeypatch.setattr(repo, 'filing_text_at', lambda *a, **kw: {
        'ticker': 'AMD', 'accession_number': '123', 'file_path': str(other), 'downloaded_at': NOW})
    with pytest.raises(ToolError):
        research.get_filing_text(context(sec_text_dir=tmp_path), 1, 'summary')


def test_filing_text_skips_the_contents_page_and_strips_tags(monkeypatch, tmp_path):
    """B14: an HTML 10-K lists ``Item 1A`` in its contents table and again as the real heading. The contents row (almost
    no text before the next Item) is dropped, the real section is returned without tags, and the byte range still
    addresses the original bytes."""
    accession = '0001234567-26-000002'
    folder = tmp_path / 'AMD'
    folder.mkdir()
    path = folder / f'{accession}.htm'
    risk = '<p>' + '竞争加剧与供应链集中&amp;客户集中。' * 30 + '</p>'
    content = ('<html><body><table><tr><td><a href="#a">Item 1A.</a></td><td>Risk Factors</td></tr>'
               '<tr><td><a href="#b">Item 7.</a></td><td>MD&amp;A</td></tr></table>'
               '<div>Item 1A. Risk Factors</div>' + risk + '<div>Item 7. Management Discussion</div><p>'
               + '稳定。' * 80 + '</p><div>Item 8. Statements</div></body></html>')
    path.write_text(content)
    monkeypatch.setattr(repo, 'filing_text_at', lambda *a, **kw: {
        'ticker': 'AMD', 'accession_number': accession, 'file_path': str(path), 'downloaded_at': NOW})
    monkeypatch.setattr(repo, 'read_rows', lambda *a, **kw: [])
    result = research.get_filing_text(context(sec_text_dir=tmp_path), 8, 'item_1a')
    assert '<' not in result['text'] and '&amp;' not in result['text'] and '竞争加剧与供应链集中&客户集中' in result['text']
    assert '稳定' not in result['text'] and 'MD&A' not in result['text']
    start, end = result['byte_range']
    raw = path.read_bytes()[start:end].decode()
    assert raw.lstrip().startswith('Item 1A. Risk Factors')
    assert 'MD&amp;A' not in raw and '<p>' in raw                      # raw bytes of the real section, tags and all
    seven = research.get_filing_text(context(sec_text_dir=tmp_path), 8, 'item_7')
    assert '稳定' in seven['text']
    # two real-looking headings stay ambiguous: no guessing
    path.write_text(content + '<div>Item 1A. Risk Factors</div><p>' + '又一处。' * 80 + '</p>')
    with pytest.raises(ToolError, match='section_unavailable'):
        research.get_filing_text(context(sec_text_dir=tmp_path), 8, 'item_1a')


def test_sec_hits_are_not_crowded_out_by_ordinary_news(workbench_db, monkeypatch):
    """B13: 60 newer yfinance items must not push the one 8-K hit out of the window."""
    from datetime import timedelta

    hits = [({'source': 'sec_8k', 'title': '8-K 公告', 'url': 'https://sec.example/8k', 'publisher': 'SEC',
              'published_at': NOW - timedelta(days=20)}, ['AMD'])]
    noise = [({'source': 'yfinance', 'title': f'资讯 {i}', 'url': f'https://news.example/{i}', 'publisher': 'W',
               'published_at': NOW - timedelta(hours=i + 1)}, ['AMD']) for i in range(60)]
    monkeypatch.setattr(repo, 'read_rows', lambda *a, **kw: [])
    repo.store_news(workbench_db, hits + noise, now=NOW - timedelta(minutes=1))
    workbench_db.commit()
    ctx = ToolContext('AMD', date(2026, 10, 30), NOW, lambda fn: fn(workbench_db))
    got = research.get_sec_hits(ctx, 90)
    assert [row['title'] for row in got['hits']] == ['8-K 公告']


def test_a_single_short_section_is_still_readable(monkeypatch, tmp_path):
    """R65: the contents-row exclusion only applies when the same section is headed more than once; a lone heading is
    the section however short its body (the `section_unavailable` rule stays for zero or several real ones)."""
    accession = '0001234567-26-000003'
    folder = tmp_path / 'AMD'
    folder.mkdir()
    path = folder / f'{accession}.txt'
    path.write_text('前言\nItem 1A. Risk Factors\n风险与竞争。\nItem 7. Management Discussion\n稳定。\nItem 8. Statements\n')
    monkeypatch.setattr(repo, 'filing_text_at', lambda *a, **kw: {
        'ticker': 'AMD', 'accession_number': accession, 'file_path': str(path), 'downloaded_at': NOW})
    monkeypatch.setattr(repo, 'read_rows', lambda *a, **kw: [])
    result = research.get_filing_text(context(sec_text_dir=tmp_path), 8, 'item_1a')
    assert '风险与竞争' in result['text'] and '稳定' not in result['text']
    start, end = result['byte_range']
    assert path.read_bytes()[start:end].decode() == result['text']
    # a second, equally short heading of the same section: both are contents-like, nothing is left to pick
    path.write_text(path.read_text() + 'Item 1A. Risk Factors\n又一处。\n')
    with pytest.raises(ToolError, match='section_unavailable'):
        research.get_filing_text(context(sec_text_dir=tmp_path), 8, 'item_1a')
