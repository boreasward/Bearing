"""User inputs: research notes, news and your own judgment pasted in as evidence (v2.5 spec §6.3, §8.1).

Append-only: a correction or a retract is a new row pointing at the old one (``supersedes_id``, D13), so a
row an earlier version cited stays readable forever. Inputs are evidence only — they reach the model in the
review / revision prompt's evidence block (``timeline.prompt_evidence``) and never become instructions,
tools, rules or price logic. ``request_id`` makes a POST idempotent, the same way as decisions
(``ledger.request_hash``): the same content replays the stored row, different content is a 409.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

import psycopg

from investment_assistant.data import identity
from investment_assistant.workbench import ledger, repository, service
from investment_assistant.core import tx as core_tx

KINDS = ('research', 'news', 'judgment', 'other', 'retract')     # 研报要点、新闻、你的判断、其他、撤回
BODY_MAX = 4000
SOURCE_URL_MAX = 500
MIGRATION_MISSING = '迁移 026 未执行'
_EARLIEST = date(1900, 1, 1)


def _ticker(value: Any) -> str:
    identity.stock_id(value if isinstance(value, str) else '')      # ValueError for anything not a US ticker
    return value.strip().upper()


def _date(value: Any, field: str) -> date | None:
    if value in (None, ''):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f'{field} must be YYYY-MM-DD') from None


def _source_url(value: Any) -> str | None:
    if value in (None, ''):
        return None
    if not isinstance(value, str):
        raise ValueError('source_url must be text')
    url = value.strip()
    if not url.lower().startswith(('http://', 'https://')) or len(url) > SOURCE_URL_MAX \
            or any(ch.isspace() for ch in url):
        raise ValueError(f'source_url must be an http(s):// link of at most {SOURCE_URL_MAX} characters')
    return url


def _supersedes(value: Any) -> int | None:
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        raise ValueError('supersedes_id must be an input id')
    try:
        number = int(str(value))
    except ValueError:
        raise ValueError('supersedes_id must be an input id') from None
    if number < 1:
        raise ValueError('supersedes_id must be an input id')
    return number


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    """The stored shape of one input, or ValueError (HTTP 400). Pure: no database."""
    if not isinstance(payload, dict):
        raise ValueError('body must be a JSON object')
    try:
        request_id = uuid.UUID(str(payload.get('request_id')))
    except ValueError:
        raise ValueError('request_id must be a UUID') from None
    kind = payload.get('kind')
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    body = payload.get('body')
    if not isinstance(body, str) or not 1 <= len(body.strip()) <= BODY_MAX:
        raise ValueError(f'body must be 1–{BODY_MAX} characters')
    rec = {
        'request_id': request_id,
        'ticker': _ticker(payload.get('ticker')),
        'signal_date': _date(payload.get('signal_date'), 'signal_date'),
        'kind': kind,
        'body': body.strip(),
        'source_url': _source_url(payload.get('source_url')),
        'supersedes_id': _supersedes(payload.get('supersedes_id')),
    }
    if kind == 'retract' and rec['supersedes_id'] is None:
        raise ValueError('a retract must name the input it retracts (supersedes_id)')
    return rec


def _require_ready(conn) -> None:
    if not repository.timeline_ready(conn):
        raise service.Unavailable(MIGRATION_MISSING)


def ticker_rows(conn, ticker: str) -> list[dict[str, Any]]:
    """Every input of the ticker — ticker-level and every unit's — oldest first, each with
    ``superseded_by`` (repository.user_inputs_for). A unit-level input always belongs to a unit with
    candidates (``add`` checks it), so the units come from the ticker's candidate dates."""
    rows = {row['id']: row for row in repository.user_inputs_for(conn, ticker, None)}
    dates = {c['signal_date'] for c in repository.read_candidates(conn, ticker, since=_EARLIEST)}
    for signal_date in sorted(dates):
        for row in repository.user_inputs_for(conn, ticker, signal_date):
            rows.setdefault(row['id'], row)
    return sorted(rows.values(), key=lambda r: (r['created_at'], r['id']))


def view(rows: list[dict[str, Any]], signal_date: date | None = None) -> list[dict[str, Any]]:
    """The listed shape: each row plus ``ref`` (``user:<id>``) and ``status`` — ``active``, ``corrected``
    or ``retracted`` (by the earliest row that supersedes it); with ``signal_date`` only the ticker-level
    rows and that chain's."""
    kinds = {row['id']: row['kind'] for row in rows}
    out = []
    for row in rows:
        if signal_date is not None and row['signal_date'] not in (None, signal_date):
            continue
        by = row.get('superseded_by')
        status = 'active' if by is None else ('retracted' if kinds.get(by) == 'retract' else 'corrected')
        out.append({**row, 'ref': f"user:{row['id']}", 'status': status})
    return out


def add(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """Store one input: ``{'status': 'created' | 'replayed', 'record'}``. ValueError (400) for a bad
    payload or a ``supersedes_id`` that is not an input of the same ticker; LookupError (404) for a
    ``signal_date`` whose unit has no candidates; ``ledger.Conflict`` (409) for a reused ``request_id``
    with different content; ``service.Unavailable`` (503) without migration 026. ``created_at`` is the
    database clock (``clock_timestamp()``), the time every cutoff compares against; ``now`` is unused
    beyond the signature shared with the other write use cases."""
    rec = validate(payload)
    digest = ledger.request_hash(rec)
    with core_tx._tx() as conn:
        _require_ready(conn)
        repository.lock(conn, f"user_inputs:{rec['ticker']}")
        existing = repository.user_input_by_request(conn, rec['request_id'])
        if existing is not None:
            if existing['request_hash'] != digest:
                raise ledger.Conflict('request_id was already used with different content')
            return {'status': 'replayed', 'record': existing}
        if rec['signal_date'] is not None and not repository.unit_candidates(conn, rec['ticker'],
                                                                             rec['signal_date']):
            raise LookupError(f"该单元没有候选：{rec['ticker']} {rec['signal_date']}")
        if rec['supersedes_id'] is not None and not any(
                row['id'] == rec['supersedes_id'] for row in ticker_rows(conn, rec['ticker'])):
            raise ValueError(f"supersedes_id {rec['supersedes_id']} 不是 {rec['ticker']} 的输入")
        try:
            record = repository.insert_user_input(conn, {**rec, 'request_hash': digest})
        except psycopg.errors.UniqueViolation:
            # the same request_id landed meanwhile under another ticker (the lock is per ticker): a reuse
            raise ledger.Conflict('request_id was already used with different content') from None
        return {'status': 'created', 'record': record}


def list_for(ticker: str, signal_date: date | None) -> list[dict[str, Any]]:
    """The inputs of a ticker (every one, ticker-level and per chain) or, with ``signal_date``, those that
    apply to that chain (ticker-level plus the chain's), with the corrected / retracted marks. Read-only."""
    ticker = _ticker(ticker)
    with core_tx.read_only_tx() as conn:
        _require_ready(conn)
        return view(ticker_rows(conn, ticker), signal_date)
