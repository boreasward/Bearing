"""Trusted portfolio facts and structural checks; no citation gate."""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from investment_assistant.core import redact as macro
from investment_assistant.execution.citations import Rejected, _loads

REASON_MAX_CHARS = 300
SUMMARY_MAX_CHARS = 200
PORTFOLIO_RECENT_SESSIONS = 20
FACT_KEYS = ('account', 'positions', 'pending_orders', 'buyable', 'rejected_yesterday', 'recent_closed', 'market',
             'portfolio_check')
_SENSITIVE_KEYS = frozenset({'api_key', 'apikey', 'secret', 'password', 'token', 'access_token', 'refresh_token',
                              'authorization', 'cookie', 'client_secret', 'credentials'})


def _json(value) -> str:
    """Keep Decimal's exact digits as JSON numbers, never convert through binary float."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('nonfinite evidence number')
        return str(value)
    if isinstance(value, dict):
        return '{' + ', '.join(json.dumps(str(k), ensure_ascii=False) + ': ' + _json(v) for k, v in value.items()) + '}'
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(_json(v) for v in value) + ']'
    if isinstance(value, (date, datetime, UUID)):
        return json.dumps(str(value) if isinstance(value, UUID) else value.isoformat())
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def build_evidence(facts: dict) -> str:
    """Caller supplies account-scoped records, the 20-session window and all cutoffs.

    Sensitive-key values are masked; their strings are also redacted from all string
    leaves, including echoed prose. Numeric values retain their exact original digits.
    Optional top-level `secrets` supplies additional sensitive values; it is never
    emitted as a fact section. Missing sections explicitly carry status=missing.
    """
    secrets = set()

    def collect_strings(value):
        if isinstance(value, str) and value:
            secrets.add(value)
        elif isinstance(value, dict):
            for val in value.values():
                collect_strings(val)
        elif isinstance(value, (list, tuple)):
            for val in value:
                collect_strings(val)

    def scrub(value):
        if isinstance(value, dict):
            out = {}
            for key, val in value.items():
                if key.lower() in _SENSITIVE_KEYS:
                    # Collect descendants before masking a credentials container,
                    # so its secret strings cannot survive as echoes elsewhere.
                    collect_strings(val)
                    out[key] = '***'
                else:
                    out[key] = scrub(val)
            return out
        if isinstance(value, (list, tuple)):
            return [scrub(v) for v in value]
        return value

    cleaned = scrub(facts)
    extra = facts.get('secrets', [])
    if isinstance(extra, (list, tuple, set)):
        secrets.update(v for v in extra if isinstance(v, str) and v)
    ordered_secrets = sorted(secrets, key=len, reverse=True)

    def redact_strings(value):
        if isinstance(value, str):
            for secret in ordered_secrets:
                value = macro.redact(value, secret)
            return value
        if isinstance(value, dict):
            return {key: redact_strings(val) for key, val in value.items()}
        if isinstance(value, list):
            return [redact_strings(val) for val in value]
        return value

    # Never replace substrings in serialized JSON: a numeric-looking token may
    # also appear in a legitimate cash/quantity literal, which must stay numeric.
    cleaned = redact_strings(cleaned)
    return '\n\n'.join(f'[fact:{key}]\n' + _json(cleaned.get(key, {'status': 'missing'}))
                       for key in FACT_KEYS)


def evidence_facts(text: str) -> dict:
    """Read our host-built sections for graph tool scopes and holdings coverage."""
    chunks = re.split(r'^\[fact:([^]]+)\]\s*\n', text, flags=re.M)
    return {chunks[i]: json.loads(chunks[i + 1], parse_float=Decimal)
            for i in range(1, len(chunks), 2)}


def _tickers(positions) -> set[str]:
    return {str(row['ticker'] if isinstance(row, dict) else row).strip().upper() for row in positions}


def validate_schema(output: dict, *, expected_positions=None) -> dict:
    """Structural invalidity rejects the entire output; share limits are per-order gates."""
    def reject(message):
        raise Rejected(f'schema_invalid: {message}')

    def text(value, max_chars=None):
        return isinstance(value, str) and bool(value.strip()) and (max_chars is None or len(value) <= max_chars)

    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            return False
        if isinstance(value, dict):
            return all(finite(v) for v in value.values())
        if isinstance(value, list):
            return all(finite(v) for v in value)
        return True

    if not isinstance(output, dict) or set(output) != {'orders', 'holds', 'summary'} or not finite(output):
        reject('expected orders, holds and summary')
    if not isinstance(output['orders'], list) or not isinstance(output['holds'], list):
        reject('orders and holds must be arrays')
    if not text(output['summary'], SUMMARY_MAX_CHARS):
        reject('summary must contain 1..200 characters')
    shapes = {'buy': {'action', 'plan_id', 'shares', 'reason'},
              'sell': {'action', 'ticker', 'portion', 'reason'}, 'cancel': {'action', 'order_id', 'reason'}}
    for order in output['orders']:
        if not isinstance(order, dict) or not isinstance(order.get('action'), str) or order['action'] not in shapes:
            reject('unknown order action')
        action = order['action']
        if set(order) != shapes[action] or not text(order.get('reason'), REASON_MAX_CHARS):
            reject('order fields or reason invalid')
        id_key = {'buy': 'plan_id', 'sell': 'ticker', 'cancel': 'order_id'}[action]
        if not text(order[id_key]):
            reject(f'{id_key} must be a nonempty string')
        if action == 'buy' and not isinstance(order['shares'], (int, float, str, Decimal)):
            reject('buy shares must be a scalar')
        if action == 'sell' and order['portion'] not in ('all', 'half'):
            reject('sell portion must be all or half')
    holds = set()
    for hold in output['holds']:
        if (not isinstance(hold, dict) or set(hold) != {'ticker', 'reason'} or not text(hold['ticker'])
                or not text(hold['reason'], REASON_MAX_CHARS)):
            reject('hold fields or reason invalid')
        ticker = hold['ticker'].strip().upper()
        if ticker in holds:
            reject('duplicate hold ticker')
        holds.add(ticker)
    if expected_positions is not None:
        expected = _tickers(expected_positions)
        sold = {o['ticker'].strip().upper() for o in output['orders'] if o['action'] == 'sell'}
        if expected - sold - holds or holds - expected:
            reject('holds must cover every unsold position and only held tickers')
    return output


def check_portfolio(text: str, *, expected_positions=None) -> dict:
    try:
        output = _loads(text)
    except (ValueError, TypeError, AttributeError):
        raise Rejected('schema_invalid: invalid JSON') from None
    return validate_schema(output, expected_positions=expected_positions)
