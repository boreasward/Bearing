"""The screening universe: a versioned, hand-maintained ticker list in config/universes/."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from investment_assistant.data.identity import stock_id
from investment_assistant.data.adapter_types import Source
from investment_assistant.data.adapter_support import ONCE, make_adapter

DEFAULT_PATH = Path('config/universes/nasdaq100.json')
MIN_TICKERS = 90
MAX_TICKERS = 105
STALE_DAYS = 120


class UniverseError(ValueError):
    """The universe file is missing, unreadable or fails validation."""


@dataclass(frozen=True)
class Universe:
    name: str
    as_of: date
    source: str
    tickers: tuple[str, ...]


def load(path: Path | str = DEFAULT_PATH) -> Universe:
    try:
        raw = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as e:
        raise UniverseError(f'cannot read universe file {path}: {e}') from e
    try:
        name = str(raw['name'])
        as_of = date.fromisoformat(str(raw['as_of']))
        source = str(raw['source'])
        listed = raw['tickers']
    except (KeyError, TypeError, ValueError) as e:
        raise UniverseError(f'universe file {path} is malformed: {e}') from e
    if not isinstance(listed, list) or not listed:
        raise UniverseError(f'universe file {path}: tickers must be a non-empty list')
    clean: list[str] = []
    for item in listed:
        try:
            stock_id(str(item))
        except ValueError as e:
            raise UniverseError(f'universe {name}: ticker {item!r}: {e}') from e
        clean.append(str(item).strip().upper())
    unique = tuple(dict.fromkeys(clean))
    if not MIN_TICKERS <= len(unique) <= MAX_TICKERS:
        raise UniverseError(f'universe {name}: {len(unique)} tickers after dedup, '
                            f'need {MIN_TICKERS}..{MAX_TICKERS}')
    return Universe(name=name, as_of=as_of, source=source, tickers=unique)


def is_stale(u: Universe, session: date) -> bool:
    return (session - u.as_of).days > STALE_DAYS


def load_for(deps: Any):
    if deps.universe is not None:
        return deps.universe
    if deps.universe_loader is not None:
        return deps.universe_loader()
    return load()


def _fetch(target, *, session, start, end, deps, now):
    loaded = load_for(deps)
    return [{'name': loaded.name, 'as_of': loaded.as_of, 'source': loaded.source, 'tickers': loaded.tickers}]


def _store(conn, rows, *, target, source_id, now):
    # Reading the manual range file creates no new database data.
    return {'inserted': 0, 'existing': len(rows[0]['tickers']), 'latest': rows[0]['as_of']}


def _latest(conn, targets, *, cutoff_at):
    if 'nasdaq100' not in targets:
        return {}
    loaded = load()
    return {'nasdaq100': loaded.as_of} if loaded.as_of <= cutoff_at.date() else {}


def _lagging(latest, session):
    if latest is None:
        return True
    return is_stale(latest if isinstance(latest, Universe) else Universe('', latest, '', ()), session)


ADAPTER = make_adapter('universe', '纳指 100 范围', '手工版本化筛选范围', 'config/universes/nasdaq100.json',
    (Source('file', _fetch, ONCE, '本地范围文件，无网络'),), _store, _latest,
    per_ticker=False, kind='manual', lock=False, lagging=_lagging,
    coverage_note='覆盖 = 范围文件成员数（90–105）；超过120天未更新标过期。')
