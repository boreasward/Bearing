"""Account snapshots and cash flows, append-only (design §3.4, §3.5).

A correction appends a snapshot superseding the current head; flows can only be
voided. Retrying a request_id with the same content returns the original record.
"""
from __future__ import annotations
import investment_assistant.core.dbutil as core_dbutil
import investment_assistant.execution.repo as execution_repo

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

# Repository consumers use their owning layers below.
from investment_assistant.execution.accounts import REAL_ACCOUNT_ID
from investment_assistant.execution.ledger import (Conflict, LedgerError, NotFound, _check_opening_date,
                                                   _decimal, _replay, _text, _uuid, request_hash)


def _validate_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        as_of = date.fromisoformat(str(payload.get('as_of')))
    except ValueError:
        raise LedgerError('as_of must be YYYY-MM-DD') from None
    note = payload.get('note')
    return {
        'request_id': _uuid(payload.get('request_id'), 'request_id'),
        'as_of': as_of,
        'equity': _decimal(payload.get('equity'), 'equity', minimum=Decimal(0), inclusive=False),
        'note': note.strip() if isinstance(note, str) and note.strip() else None,
        'supersedes_id': _uuid(payload.get('supersedes_id'), 'supersedes_id', optional=True),
    }


def _validate_flow(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        amount = Decimal(str(payload.get('amount')))
    except (InvalidOperation, TypeError):
        raise LedgerError('amount must be a number') from None
    if not amount.is_finite() or amount == 0:
        raise LedgerError('amount must be non-zero')
    try:
        flow_date = date.fromisoformat(str(payload.get('flow_date')))
    except ValueError:
        raise LedgerError('flow_date must be YYYY-MM-DD') from None
    note = payload.get('note')
    return {
        'request_id': _uuid(payload.get('request_id'), 'request_id'),
        'flow_date': flow_date,
        'amount': amount,
        'note': note.strip() if isinstance(note, str) and note.strip() else None,
    }


def record_snapshot(conn, payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    if _uuid(payload.get('account_id', REAL_ACCOUNT_ID), 'account_id') != REAL_ACCOUNT_ID:
        raise LedgerError('flows_only_for_real_account')
    rec = _validate_snapshot(payload)
    digest = request_hash(rec)
    core_dbutil.lock(conn, 'account:snapshots')
    if replay := _replay(execution_repo.snapshot_by_request(conn, rec['request_id']), digest):
        return replay
    head = execution_repo.snapshot_head(conn)
    if rec['supersedes_id'] is not None and (head is None or head['id'] != rec['supersedes_id']):
        raise Conflict('必须更正当前有效快照')
    return {'status': 'created', 'record': execution_repo.insert_snapshot(conn, rec, digest, recorded_at=now)}


def record_flow(conn, payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    if _uuid(payload.get('account_id', REAL_ACCOUNT_ID), 'account_id') != REAL_ACCOUNT_ID:
        raise LedgerError('flows_only_for_real_account')
    rec = _validate_flow(payload)
    digest = request_hash(rec)
    core_dbutil.lock(conn, f'account:{REAL_ACCOUNT_ID}')
    core_dbutil.lock(conn, 'account:flows')
    if replay := _replay(execution_repo.flow_by_request(conn, rec['request_id']), digest):
        return replay
    _check_opening_date(conn, rec['flow_date'])
    return {'status': 'created', 'record': execution_repo.insert_flow(conn, rec, digest, recorded_at=now)}


def void_flow(conn, flow_id: Any, reason: Any, *, now: datetime) -> dict[str, Any]:
    reason = _text(reason, 'void_reason')
    flow = execution_repo.flow_by_id(conn, _uuid(flow_id, 'flow_id'))
    if flow is None:
        raise NotFound('flow not found')
    core_dbutil.lock(conn, 'account:flows')
    flow = execution_repo.flow_by_id(conn, flow['id'])
    if flow['voided_at'] is not None:
        raise Conflict('flow is already voided')
    return execution_repo.mark_flow_voided(conn, flow['id'], reason, voided_at=now)
