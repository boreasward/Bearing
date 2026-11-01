"""The screening universe file: loading, validation and staleness."""
import json
from datetime import date

import pytest

from investment_assistant.data.adapters import universe


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding='utf-8')
    return p


def test_default_file_loads():
    u = universe.load()
    assert 90 <= len(u.tickers) <= 105
    assert 'GOOG' not in u.tickers and 'GOOGL' in u.tickers
    assert len(set(u.tickers)) == len(u.tickers)
    assert u.name == 'nasdaq100'
    assert u.as_of == date(2026, 10, 22)


def test_rejects_bad_files(tmp_path):
    crowd = [f'X{i:02d}' for i in range(95)]
    with pytest.raises(universe.UniverseError):
        universe.load(_write(tmp_path, 'a.json', {'name': 't', 'source': 's', 'tickers': crowd}))
    with pytest.raises(universe.UniverseError):
        universe.load(_write(tmp_path, 'b.json',
                             {'name': 't', 'as_of': '2026-10-01', 'source': 's',
                              'tickers': ['BAD!'] + crowd[:94]}))
    with pytest.raises(universe.UniverseError):
        universe.load(_write(tmp_path, 'c.json',
                             {'name': 't', 'as_of': '2026-10-01', 'source': 's', 'tickers': ['AAPL'] * 95}))


def test_is_stale():
    u = universe.Universe('t', date(2026, 6, 1), 's', ('AAPL',))
    assert universe.is_stale(u, date(2026, 9, 29)) is False  # as_of 之后第 120 天
    assert universe.is_stale(u, date(2026, 9, 30)) is True   # 第 121 天
