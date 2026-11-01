"""Trades and plan notes, append-only (design §4.7, §11; v2.9 spec §6).

Since v2.9 there is no decision button: a fill follows a plan version (``plan_id``), carries its own levels
(``own_plan``, written in the fill's transaction) or follows nothing, and adoption is derived from the fills
(``execution-1``). ``POST /decisions`` is gone (410 in the service); old ``signal_decisions`` rows are read-only.
Trades are only ever voided, never edited. Retrying a request_id with the same content returns the original record.
Plan notes (``plan_notes``) are a log for the user: they never reach an Agent prompt.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from investment_assistant.workbench import positions, repository
from investment_assistant.workbench.accounts import REAL_ACCOUNT_ID

NY = ZoneInfo('America/New_York')
OWN_PLAN_UNIT_SESSIONS = 20      # R12: how far back an own plan looks for the unit it belongs to
NOTE_MAX_CHARS = 1000            # plan_notes.body: char_length(btrim(body)) between 1 and 1000
DECISION_LINK_RETIRED = 'decision_link_retired: v2.9 起成交不再关联决定，请改为关联计划'
PLAN_AFTER_FILL_TEXT = '这一版生成于成交之后，不可能是你照着做的那一版'
NOTE_KINDS = ('note', 'retract')


class LedgerError(ValueError):
    """Invalid request (HTTP 400)."""


BadRequest = LedgerError


class Conflict(Exception):
    """Request clashes with stored state (HTTP 409)."""


class NotFound(LookupError):
    """Unknown record (HTTP 404)."""


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value.normalize(), 'f')
    if isinstance(value, (uuid.UUID, date, datetime)):
        return value.isoformat() if not isinstance(value, uuid.UUID) else str(value)
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def request_hash(record: dict[str, Any]) -> str:
    body = {k: _plain(v) for k, v in record.items() if k != 'request_id'}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _uuid(value: Any, field: str, *, optional: bool = False) -> uuid.UUID | None:
    if value in (None, '') and optional:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise LedgerError(f'{field} must be a UUID') from None


def _choice(value: Any, field: str, options: tuple[str, ...]) -> str:
    if value not in options:
        raise LedgerError(f"{field} must be one of {', '.join(options)}")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LedgerError(f'{field} is required')
    return value.strip()


def _ticker(value: Any) -> str:
    return _text(value, 'ticker').upper()


def _decimal(value: Any, field: str, *, minimum: Decimal, inclusive: bool) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise LedgerError(f'{field} must be a number') from None
    if not number.is_finite() or (number < minimum if inclusive else number <= minimum):
        raise LedgerError(f"{field} must be {'>=' if inclusive else '>'} {minimum}")
    return number


def _own_plan(value: Any) -> dict[str, Any] | None:
    """A trade's ``own_plan`` (v2.9 §6.1): the four levels and an optional ``max_hold_sessions`` (3-30, default 10)."""
    if value in (None, ''):
        return None
    if not isinstance(value, dict):
        raise LedgerError('own_plan must be an object')
    from investment_assistant.workbench import plans as _plans

    try:
        levels = _plans.validate_levels(value)
    except ValueError as exc:
        raise LedgerError(str(exc)) from None
    hold = value.get('max_hold_sessions')
    if hold in (None, ''):
        hold = _plans.RULE_MAX_HOLD_SESSIONS
    low, high = _plans.MAX_HOLD_RANGE
    if isinstance(hold, bool) or not isinstance(hold, int) or not low <= hold <= high:
        raise LedgerError(f'最长持有须为 {low}–{high} 个交易日')
    return {**levels, 'max_hold_sessions': hold}


def validate_trade(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """v2.9 §6.1: a fill follows a plan (``plan_id``), carries its own levels (``own_plan``) or neither; a request
    that names a ``decision_id`` is refused (400 ``decision_link_retired``)."""
    account_id = _uuid(payload.get('account_id', REAL_ACCOUNT_ID), 'account_id')
    if account_id != REAL_ACCOUNT_ID:
        raise LedgerError('paper_account_is_host_managed')
    if payload.get('decision_id') not in (None, ''):
        raise LedgerError(DECISION_LINK_RETIRED)
    try:
        executed_at = datetime.fromisoformat(str(payload.get('executed_at')))
    except ValueError:
        raise LedgerError('executed_at must be an ISO timestamp') from None
    if executed_at.tzinfo is None or executed_at.utcoffset() is None:
        raise LedgerError('executed_at must include a timezone')
    if executed_at > now:
        raise LedgerError('executed_at cannot be in the future')
    note = payload.get('note')
    rec = {
        'account_id': account_id,
        'request_id': _uuid(payload.get('request_id'), 'request_id'),
        'ticker': _ticker(payload.get('ticker')),
        'side': _choice(payload.get('side'), 'side', ('buy', 'sell')),
        'quantity': _decimal(payload.get('quantity'), 'quantity', minimum=Decimal(0), inclusive=False),
        'price': _decimal(payload.get('price'), 'price', minimum=Decimal(0), inclusive=False),
        'fee': _decimal(payload.get('fee', 0) or 0, 'fee', minimum=Decimal(0), inclusive=True),
        'executed_at': executed_at,
        'plan_id': _uuid(payload.get('plan_id'), 'plan_id', optional=True),
        'own_plan': _own_plan(payload.get('own_plan')),
        'note': note.strip() if isinstance(note, str) and note.strip() else None,
    }
    if rec['plan_id'] is not None and rec['own_plan'] is not None:
        raise LedgerError('plan_id 与 own_plan 只能二选一')
    if rec['own_plan'] is not None and rec['side'] != 'buy':
        raise LedgerError('own_plan 只用于买入成交')
    return rec


def trade_hash(rec: dict[str, Any]) -> str:
    """request_hash of a validated trade. ``decision_id`` stays in the hashed body as ``None`` and the v2.9 fields
    enter only when given, so a request recorded before v2.9 still replays to the same hash."""
    body = {k: v for k, v in rec.items() if k not in ('plan_id', 'own_plan', 'account_id')}
    if str(rec.get('account_id', REAL_ACCOUNT_ID)) != str(REAL_ACCOUNT_ID):
        body['account_id'] = rec['account_id']
    body['decision_id'] = None
    body.update({k: rec[k] for k in ('plan_id', 'own_plan') if rec[k] is not None})
    return request_hash(body)


def _replay(existing: dict[str, Any] | None, digest: str) -> dict[str, Any] | None:
    if existing is None:
        return None
    if existing['request_hash'] != digest:
        raise Conflict('request_id was already used with different content')
    return {'status': 'existing', 'record': existing}


def _need_executable(conn) -> None:
    if not repository.executable_ready(conn):
        from investment_assistant.workbench import service as _service

        raise _service.Unavailable('migration_029_missing')


def _ny_day(moment: datetime) -> date:
    return moment.astimezone(NY).date()


def _check_opening_date(conn, record_date: date) -> None:
    """New ledger facts cannot precede opening; caller holds the real-account lock and has checked replay."""
    if repository.accounts_ready(conn):
        account = repository.account_by_id(conn, REAL_ACCOUNT_ID)
        if account and account['opened_on'] is not None and record_date < account['opened_on']:
            raise LedgerError('record_before_opened_on')


def _checked_plan(conn, rec: dict[str, Any]) -> uuid.UUID | None:
    """R6: the plan a fill follows. The plan must be the same ticker's. An Agent chain version must be the chain's
    head *at the moment of the fill* (``created_at <= executed_at`` and no later version by then): an older one is
    409 ``plan_stale``, one generated after the fill 409 ``plan_after_fill``. Sells are not checked further."""
    plan = repository.plan_by_id(conn, rec['plan_id'])
    if plan is None or plan['ticker'] != rec['ticker']:
        raise LedgerError('plan_id must refer to a plan of the same ticker')
    if rec['side'] != 'buy':
        return plan['id']
    version = repository.version_by_plan(conn, plan['id'])
    if version is None:
        return plan['id']
    executed_at = rec['executed_at']
    if version['created_at'] > executed_at:
        raise Conflict(f'plan_after_fill: {PLAN_AFTER_FILL_TEXT}')
    known = [v for v in repository.chain_versions(conn, version['ticker'], version['signal_date'])
             if v['created_at'] <= executed_at]
    head = known[-1]
    if head['plan_id'] != plan['id']:
        raise Conflict(f"plan_stale: 成交时这条链已是第 {head['version_no']} 版，请关联第 {head['version_no']} 版")
    return plan['id']


def own_plan_signal_date(conn, ticker: str, executed_at: datetime) -> date:
    """R12, in this order: the unit of the ticker's active chain at the moment of the fill; else the ticker's latest
    unit with candidates in the 20 sessions before the fill day; else the fill day itself (then the plan belongs to
    no unit and D22 maps its fills to no chain)."""
    from investment_assistant.data.calendar import add_trading_days
    from investment_assistant.workbench import plan_review

    fill_day = _ny_day(executed_at)
    # judged as the plan table of the session before the fill day showed it (a chain whose last valid day is the fill
    # day is still active, I1); read as the fill knew it: a chain opened later is not there, and a plan that expired
    # the day before (only a trigger could renew it) is no longer the unit
    found = plan_review.active_chain_of(conn, ticker, session=add_trading_days(fill_day, -1), now=executed_at,
                                        point_in_time=True)
    if found is not None and not found['renewable']:
        return found['chain']['signal_date']
    since = add_trading_days(fill_day, -OWN_PLAN_UNIT_SESSIONS)
    days = [c['signal_date'] for c in repository.read_candidates(conn, ticker, since=since)
            if c['signal_date'] < fill_day]
    return max(days) if days else fill_day


def _record_own_plan(conn, rec: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """Build and store the own plan in the trade's transaction (validity = the fill day, ``plan-2`` with the execution
    fields); the plan's request id is derived from the trade's, so a replay of the trade never reaches here twice."""
    from investment_assistant.config import load_config
    from investment_assistant.workbench import plans as _plans, service as _service

    signal_date = own_plan_signal_date(conn, rec['ticker'], rec['executed_at'])
    bars = repository.read_bars(conn, rec['ticker'], signal_date, cutoff_at=now)
    if not bars:
        raise LedgerError('该标的缺少 K 线，无法生成自定计划')
    own = rec['own_plan']
    plan = _plans.build_own_plan(
        rule_plan=repository.rule_plan(conn, rec['ticker'], signal_date),
        levels={k: own[k] for k in _plans.LEVEL_KEYS}, ticker=rec['ticker'], signal_date=signal_date, bars=bars,
        earnings_row=repository.latest_earnings(conn, rec['ticker'], cutoff_at=now),
        snapshot=repository.snapshot_at(conn, signal_date, cutoff_at=now),
        exposure=_service.exposure(conn, signal_date, cutoff_at=now, ticker=rec['ticker']),
        params=load_config().workbench, context=_service.plan_context(conn, rec['ticker'], signal_date, now=now),
        version=_plans.PLAN_V2, fill_session=_ny_day(rec['executed_at']), max_hold_sessions=own['max_hold_sessions'])
    plan = {**plan, 'request_id': uuid.uuid5(rec['request_id'], 'own_plan')}
    plan['request_hash'] = request_hash({k: v for k, v in plan.items() if k not in ('request_id', 'request_hash')})
    return repository.insert_plan(conn, plan)


def _check_book(conn, ticker: str, trades: list[dict[str, Any]]) -> None:
    try:
        positions.build(trades, splits=repository.split_events(conn, ticker))
    except positions.PositionError as exc:
        raise LedgerError(str(exc)) from None


def record_trade(conn, payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """Record one fill. With ``plan_id`` the plan is checked (R6); with ``own_plan`` the plan is written in the same
    transaction (R12, ``plan-2``, validity = the fill day) and the fill follows it. A plan link without migration 029
    is ``Unavailable`` (503 ``migration_029_missing``)."""
    rec = validate_trade(payload, now=now)
    digest = trade_hash(rec)
    # Serialize with open_real_account before taking the ticker lock. This also
    # works before migration 030 because advisory locks do not depend on tables.
    repository.lock(conn, f'account:{REAL_ACCOUNT_ID}')
    repository.lock(conn, f"trades:{rec['ticker']}")
    if replay := _replay(repository.trade_by_request(conn, rec['request_id']), digest):
        return replay
    _check_opening_date(conn, _ny_day(rec['executed_at']))
    if rec['plan_id'] is not None or rec['own_plan'] is not None:
        _need_executable(conn)
    plan_id = _checked_plan(conn, rec) if rec['plan_id'] is not None else None
    stored = {k: v for k, v in rec.items() if k not in ('own_plan',)}
    _check_book(conn, rec['ticker'], [*repository.list_trades(conn, rec['ticker']),
                                      {**stored, 'id': 'new', 'voided_at': None}])
    if rec['own_plan'] is not None:
        plan_id = _record_own_plan(conn, rec, now=now)['id']
    stored['plan_id'] = plan_id
    return {'status': 'created', 'record': repository.insert_trade(conn, stored, digest, recorded_at=now)}


def void_trade(conn, trade_id: Any, reason: Any, *, now: datetime) -> dict[str, Any]:
    reason = _text(reason, 'void_reason')
    trade = repository.trade_by_id(conn, _uuid(trade_id, 'trade_id'))
    if trade is None:
        raise NotFound('trade not found')
    repository.lock(conn, f"trades:{trade['ticker']}")
    trade = repository.trade_by_id(conn, trade['id'])
    if trade['voided_at'] is not None:
        raise Conflict('trade is already voided')
    _check_book(conn, trade['ticker'], [t for t in repository.list_trades(conn, trade['ticker']) if t['id'] != trade['id']])
    return repository.mark_trade_voided(conn, trade['id'], reason, voided_at=now)


# ---------------------------------------------------------------- plan notes (v2.9 spec §6.5, D25)

def _note_body(value: Any, *, required: bool = True) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise LedgerError('body is required')
        return ''
    if not isinstance(value, str):
        raise LedgerError('body must be a string')
    body = value.strip()
    if len(body) > NOTE_MAX_CHARS:
        raise LedgerError(f'body 不能超过 {NOTE_MAX_CHARS} 字')
    return body


def validate_note(payload: dict[str, Any]) -> dict[str, Any]:
    kind = payload.get('kind', 'note')
    if kind not in NOTE_KINDS:
        raise LedgerError(f"kind must be one of {', '.join(NOTE_KINDS)}")
    chain_day = payload.get('chain_signal_date')
    if chain_day in (None, ''):
        chain_signal_date = None
    else:
        try:
            chain_signal_date = date.fromisoformat(str(chain_day))
        except ValueError:
            raise LedgerError('chain_signal_date must be YYYY-MM-DD') from None
    plan_id = _uuid(payload.get('plan_id'), 'plan_id', optional=True)
    if (chain_signal_date is None) == (plan_id is None):
        raise LedgerError('备注作用于一条链（chain_signal_date）或一份非链计划（plan_id），二选一')
    supersedes = payload.get('supersedes_id')
    if supersedes in (None, ''):
        supersedes_id = None
    elif isinstance(supersedes, bool) or not isinstance(supersedes, int):
        raise LedgerError('supersedes_id must be an integer')
    else:
        supersedes_id = supersedes
    if kind == 'retract' and supersedes_id is None:
        raise LedgerError('撤回需要 supersedes_id')
    body = _note_body(payload.get('body'), required=kind == 'note') or '（撤回）'
    given = payload.get('ticker')
    if given in (None, '') and plan_id is not None:
        ticker = None                    # a plan knows its ticker: record_note takes it from the plan
    else:
        ticker = _ticker(given)
    return {'request_id': _uuid(payload.get('request_id'), 'request_id'), 'ticker': ticker,
            'chain_signal_date': chain_signal_date, 'plan_id': plan_id, 'kind': kind, 'body': body,
            'supersedes_id': supersedes_id}


def record_note(conn, payload: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Append one plan note, a correction (a ``note`` with ``supersedes_id``) or a retraction (``kind = 'retract'``).

    Scope is a chain (``ticker`` + ``chain_signal_date``, which must exist) or a non-chain plan (``plan_id`` of a
    rule or own plan of the same ticker); a correction or retraction must point at a note of the same scope. Idempotent
    on ``request_id`` (a different content under the same id is a ``Conflict``). With a ``plan_id`` the ``ticker`` may be left
    out: it is the plan's (a ticker that does not match is a 400). Needs migration 029. Notes are never
    handed to an Agent. Returns ``{'status': 'created' | 'existing', 'record': <the row>}``."""
    rec = validate_note(payload)
    _need_executable(conn)
    if rec['ticker'] is None:
        plan = repository.plan_by_id(conn, rec['plan_id'])
        if plan is None:
            raise NotFound('plan not found')
        rec['ticker'] = plan['ticker']
    digest = request_hash({k: v for k, v in rec.items() if v is not None})
    repository.lock(conn, f"plan-note:{rec['ticker']}")
    row = repository.plan_note_by_request(conn, rec['request_id'])
    if row is not None:
        if row['request_hash'] != digest:
            raise Conflict('request_id was already used with different content')
        return {'status': 'existing', 'record': row}
    if rec['chain_signal_date'] is not None:
        if not repository.chain_versions(conn, rec['ticker'], rec['chain_signal_date']):
            raise NotFound(f"该单元没有 agent 计划链：{rec['ticker']} {rec['chain_signal_date']}")
    else:
        plan = repository.plan_by_id(conn, rec['plan_id'])
        if plan is None or plan['ticker'] != rec['ticker']:
            raise LedgerError('plan_id must refer to a plan of the same ticker')
        if repository.version_by_plan(conn, plan['id']) is not None:
            raise LedgerError('链上的计划请用 chain_signal_date 写备注')
    if rec['supersedes_id'] is not None:
        scope = {'chain_signal_date': rec['chain_signal_date'], 'plan_id': rec['plan_id']}
        target = next((n for n in repository.plan_notes_for(conn, rec['ticker'], **{
            k: v for k, v in scope.items() if v is not None}) if n['id'] == rec['supersedes_id']), None)
        if target is None:
            raise LedgerError('supersedes_id 不是这条链 / 这份计划上的一条备注')
    stored = repository.insert_plan_note(conn, {**rec, 'request_hash': digest})
    created = not stored.pop('replayed', False)
    return {'status': 'created' if created else 'existing', 'record': stored}
