"""Pool tier parsing and limits (pure functions)."""

import pytest

from investment_assistant.analysis import pool


def _rows(tickers, *, status='active', tags=()):
    return [{'ticker': t, 'status': status, 'thesis': None, 'tags': list(tags), 'created_at': None, 'updated_at': None}
            for t in tickers]


def test_constants():
    assert pool.MAX_POOL == 100
    assert pool.MAX_CORE == 15
    assert pool.CORE_TAG == 'core'
    assert pool.SECTOR_PREFIX == 'sector:'
    assert pool.SECTOR_ETFS == ('SMH', 'XLK', 'QQQ')


def test_core_tag_case_insensitive():
    assert pool.parse_tags(['Core']) == (True, None)
    assert pool.parse_tags(['CORE']) == (True, None)


def test_sector_tag_normalized():
    assert pool.parse_tags(['sector:smh']) == (False, 'SMH')
    assert pool.parse_tags(['sector:QQQ']) == (False, 'QQQ')


def test_two_sector_tags_rejected():
    with pytest.raises(ValueError):
        pool.parse_tags(['core', 'sector:SMH', 'sector:XLK'])


def test_unknown_sector_rejected():
    with pytest.raises(ValueError):
        pool.parse_tags(['sector:XLE'])


def test_from_watchlist_skips_paused_and_archived():
    rows = (_rows(['AAPL'], tags=['core']) + _rows(['MU'], status='paused')
            + _rows(['AMD'], status='archived', tags=['core']) + _rows(['NVDA'], tags=['sector:smh']))
    p = pool.from_watchlist(rows)
    assert p.core == ('AAPL',)
    assert p.extended == ('NVDA',)
    assert p.sectors == {'NVDA': 'SMH'}
    assert p.tickers == ('AAPL', 'NVDA')
    assert p.tier('AAPL') == 'core'
    assert p.tier('NVDA') == 'extended'
    assert p.tier('MU') is None


def test_from_watchlist_skips_invalid_identity():
    # 688008.SH: an invalid US code that may still sit in the table before migration 021 runs
    rows = _rows(['AAPL'], tags=['core']) + _rows(['688008.SH'], tags=['core']) + _rows(['NVDA'])
    p = pool.from_watchlist(rows)
    assert p.core == ('AAPL',) and p.extended == ('NVDA',)


def test_pool_limit_100():
    assert pool.from_watchlist(_rows([f'T{i:03d}' for i in range(100)]))
    with pytest.raises(ValueError, match='100'):
        pool.from_watchlist(_rows([f'T{i:03d}' for i in range(101)]))


def test_core_limit_15():
    rows = _rows([f'C{i:02d}' for i in range(15)], tags=['core'])
    assert len(pool.from_watchlist(rows).core) == 15
    with pytest.raises(ValueError, match='15'):
        pool.from_watchlist(_rows([f'C{i:02d}' for i in range(16)], tags=['core']))


def test_empty_pool_raises():
    with pytest.raises(ValueError):
        pool.from_watchlist([])
    with pytest.raises(ValueError):
        pool.from_watchlist(_rows(['600519.SH']))


def test_core_empty_is_allowed():
    p = pool.from_watchlist(_rows(['MU', 'NVDA']))
    assert p.core == ()
    assert p.extended == ('MU', 'NVDA')


def test_all_core_normalizes_and_validates():
    p = pool.Pool.all_core(['nvda', 'NVDA', 'mu'])
    assert p.core == ('MU', 'NVDA')
    assert p.extended == ()
    with pytest.raises(ValueError, match='股票池为空'):
        pool.Pool.all_core([])
    with pytest.raises(ValueError, match='100'):
        pool.Pool.all_core([f'T{i:03d}' for i in range(101)])


def test_tickers_core_first_then_alphabetical():
    p = pool.from_watchlist(_rows(['NVDA'], tags=['core']) + _rows(['MU', 'AMD']))
    assert p.tickers == ('NVDA', 'AMD', 'MU')
