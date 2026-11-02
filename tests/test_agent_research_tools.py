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
    content = '前言\nItem 1A. Risk Factors\n风险与竞争。\nItem 7. Management Discussion\n稳定。\nItem 8. Statements\n'
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
