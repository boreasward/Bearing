"""The screening universe: a versioned, hand-maintained ticker list in config/universes/."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from investment_assistant.data.identity import stock_id

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
