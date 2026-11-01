"""Secret redaction and failure-reason sanitisation (spec §4.2).

``redact`` was ``macro.redact``, ``sanitize_reason`` was ``families.sanitize_reason``;
both modules re-export until Task 1. The FRED key never reaches logs or stored reasons."""
from __future__ import annotations

import os

REASON_MAX_CHARS = 300            # stored failure reasons are truncated after redaction


def redact(text: str, secret: str | None) -> str:
    """Replace every occurrence of the secret with ***; empty secrets redact nothing."""
    if secret:
        text = text.replace(secret, '***')
    return text


def sanitize_reason(text: object) -> str:
    """Stringify, redact the FRED key, keep the first 300 chars."""
    return redact(str(text), os.environ.get('FRED_API_KEY'))[:REASON_MAX_CHARS]
