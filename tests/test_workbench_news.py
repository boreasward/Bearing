import investment_assistant.data.repo as data_repo
from datetime import UTC, datetime, timedelta

import pytest

# Repository consumers use their owning layers below.
from investment_assistant.data.adapters import news

T0 = datetime(2026, 10, 22, 22, 30, tzinfo=UTC)

CURRENT = {'id': 'a1', 'content': {
    'title': 'Chipmaker beats estimates', 'pubDate': '2026-10-21T20:30:00Z',
    'provider': {'displayName': 'Example Wire'},
    'canonicalUrl': {'url': 'https://news.example.com/a1'},
    'clickThroughUrl': {'url': 'https://click.example.com/a1'}}}
LEGACY = {'title': 'Old style item', 'link': 'https://news.example.com/b2',
          'publisher': 'Legacy Wire', 'providerPublishTime': 1792000000}


def test_normalize_both_yfinance_shapes():
    items, skipped = news.normalize_yfinance([CURRENT, LEGACY])
    assert skipped == 0
    assert items[0] == {'source': 'yfinance', 'title': 'Chipmaker beats estimates',
                        'url': 'https://news.example.com/a1', 'publisher': 'Example Wire',
                        'published_at': datetime(2026, 10, 21, 20, 30, tzinfo=UTC)}
    assert items[1]['url'] == 'https://news.example.com/b2'
    assert items[1]['published_at'] == datetime.fromtimestamp(1792000000, UTC)


def test_missing_publish_time_is_unknown_not_fetch_time():
    item = {'content': {'title': 'Undated', 'canonicalUrl': {'url': 'https://news.example.com/c3'}}}
    items, _ = news.normalize_yfinance([item])
    assert items[0]['published_at'] is None and items[0]['publisher'] is None


def test_items_without_title_or_url_are_skipped_and_counted():
    items, skipped = news.normalize_yfinance([{'content': {'title': 'no link'}}, {'link': 'https://x'}, 'junk', CURRENT])
    assert [i['url'] for i in items] == ['https://news.example.com/a1']
    assert skipped == 3


def test_8k_becomes_sec_news_using_acceptance_time():
    metas = [
        {'ticker': 'NVDA', 'form': '8-K', 'filing_date': '2026-08-27', 'items': ['2.02', '9.01'],
         'accepted_at': '2026-08-27T16:20:31-04:00', 'edgar_url': 'https://www.sec.gov/Archives/k.htm'},
        {'ticker': 'NVDA', 'form': '10-Q', 'filing_date': '2026-08-27', 'items': [],
         'accepted_at': None, 'edgar_url': 'https://www.sec.gov/Archives/q.htm'},
    ]
    out = news.filing_news(metas)
    assert out == [({'source': 'sec_8k', 'title': 'NVDA 8-K (Items 2.02, 9.01)',
                     'url': 'https://www.sec.gov/Archives/k.htm', 'publisher': 'SEC EDGAR',
                     'published_at': datetime.fromisoformat('2026-08-27T16:20:31-04:00')}, ['NVDA'])]


def test_8k_without_acceptance_keeps_unknown_time():
    out = news.filing_news([{'ticker': 'MU', 'form': '8-K', 'items': None, 'accepted_at': None,
                             'edgar_url': 'https://www.sec.gov/Archives/m.htm'}])
    assert out[0][0]['published_at'] is None and out[0][0]['title'] == 'MU 8-K'


def test_fetch_uses_injected_fetcher():
    assert news.fetch_yfinance('nvda', fetcher=lambda t: [t]) == ['NVDA']


# --- storage (disposable schema) ---

def item(url, title='t', published=T0 - timedelta(hours=3)):
    return {'source': 'yfinance', 'title': title, 'url': url, 'publisher': 'W', 'published_at': published}


def test_store_dedupes_by_source_and_url_and_links_tickers(workbench_db):
    first = data_repo.store_news(workbench_db, [(item('https://a'), ['NVDA'])], now=T0)
    again = data_repo.store_news(workbench_db, [(item('https://a', title='edited'), ['NVDA', 'MU'])],
                                  now=T0 + timedelta(hours=1))
    assert first == {'inserted': 1, 'existing': 0} and again == {'inserted': 0, 'existing': 1}
    rows = workbench_db.execute('SELECT title, fetched_at FROM news_items').fetchall()
    assert rows == [('t', T0)]
    tickers = workbench_db.execute('SELECT ticker FROM news_item_tickers ORDER BY ticker').fetchall()
    assert tickers == [('MU',), ('NVDA',)]


def test_read_news_respects_cutoff_on_fetch_and_publish(workbench_db):
    data_repo.store_news(workbench_db, [(item('https://old'), ['NVDA'])], now=T0)
    data_repo.store_news(workbench_db, [(item('https://undated', published=None), ['NVDA'])], now=T0)
    data_repo.store_news(workbench_db, [(item('https://future', published=T0 + timedelta(hours=2)), ['NVDA'])], now=T0)
    data_repo.store_news(workbench_db, [(item('https://late-fetch'), ['NVDA'])], now=T0 + timedelta(hours=3))
    data_repo.store_news(workbench_db, [(item('https://other'), ['MU'])], now=T0)
    got = data_repo.read_news(workbench_db, 'nvda', cutoff_at=T0 + timedelta(hours=1))
    assert {g['url'] for g in got} == {'https://old', 'https://undated'}
    assert all(isinstance(g['id'], int) for g in got)


def test_read_news_since_filters_by_publish_or_fetch_time(workbench_db):
    data_repo.store_news(workbench_db, [(item('https://old', published=T0 - timedelta(days=9)), ['NVDA'])], now=T0)
    data_repo.store_news(workbench_db, [(item('https://new'), ['NVDA'])], now=T0)
    got = data_repo.read_news(workbench_db, 'NVDA', cutoff_at=T0, since=T0 - timedelta(days=2))
    assert [g['url'] for g in got] == ['https://new']


@pytest.mark.parametrize('bad', [{'source': 'blog'}])
def test_store_rejects_unknown_source(workbench_db, bad):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        data_repo.store_news(workbench_db, [({**item('https://x'), **bad}, ['NVDA'])], now=T0)


def test_search_results_are_kept_only_when_related_to_the_ticker():
    raw = [
        {'title': 'Related', 'link': 'https://n/1', 'publisher': 'W', 'providerPublishTime': 1792000000,
         'relatedTickers': ['NVDA', 'TSLA']},
        {'title': 'Unrelated', 'link': 'https://n/2', 'publisher': 'W', 'providerPublishTime': 1792000000,
         'relatedTickers': ['TSLA']},
        {'title': 'No tags', 'link': 'https://n/3', 'publisher': 'W', 'providerPublishTime': 1792000000},
    ]
    items, skipped = news.normalize_yfinance(raw, ticker='nvda')
    assert [i['url'] for i in items] == ['https://n/1', 'https://n/3'] and skipped == 1


def test_default_fetcher_uses_search_news(monkeypatch):
    import yfinance as yf
    calls = []

    class FakeSearch:
        def __init__(self, query, **kwargs):
            calls.append((query, kwargs))
            self.news = [{'title': 't'}]
    monkeypatch.setattr(yf, 'Search', FakeSearch)
    assert news.fetch_yfinance('nvda') == [{'title': 't'}]
    assert calls[0][0] == 'NVDA' and calls[0][1]['news_count'] == news.NEWS_COUNT
