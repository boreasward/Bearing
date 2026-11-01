"""Idempotency primitives: the request conflict type and the request hash (spec §4.2).

Cut verbatim from ``workbench/ledger.py`` in Task 0; ledger re-exports until Task 1."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any


class Conflict(Exception):
    """Request clashes with stored state (HTTP 409)."""


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
