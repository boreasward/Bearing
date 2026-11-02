"""SEC listings reuse EDGAR discovery and the existing news/acceptance stores."""
from investment_assistant.data import repo
from investment_assistant.data.adapters.news import filing_news
from investment_assistant.data.adapter_types import Source
from investment_assistant.data.adapter_support import ONCE, discover_one, make_adapter, sec_outcome, sec_source
from investment_assistant.data.sources.edgar import drop_after_cutoff


def _fetch(target, *, session, start, end, deps, now):
    source = sec_source(deps)
    return sec_outcome(source, lambda: discover_one(source, target, start.isoformat(), end.isoformat()))


def _store(conn, rows, *, target, source_id, now):
    rows, dropped = drop_after_cutoff(rows, now)
    counts = repo.store_news(conn, filing_news(rows), now=now)
    filled = repo.fill_filing_acceptance(conn, rows)
    return {'inserted': counts['inserted'] + filled, 'existing': counts['existing'],
            'latest': max((r['filing_date'] for r in rows), default=None),
            'acceptance_filled': filled, 'after_cutoff_dropped': dropped, 'listed': len(rows)}


ADAPTER = make_adapter('sec_filings', 'SEC 申报', 'SEC申报目录与接受时间', 'filings + news_items(sec_8k)',
    (Source('sec', _fetch, ONCE, 'EDGAR submissions；接受时间UTC，内部fetch-1'),),
    _store, repo.latest_filing_dates, days=120,
    coverage_note='覆盖 = 近120天有filings或sec_8k的标的；无申报不等于缺数据。')
