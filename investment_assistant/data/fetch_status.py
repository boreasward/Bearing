"""Pure fetch-run status and ISO formatting, shared by tally and display."""
from investment_assistant.core.redact import sanitize_reason as sanitize_reason


def iso(value):
    return None if value is None else value.isoformat() if hasattr(value, 'isoformat') else str(value)


def classify(target_count, ok_count, skipped_reason=None):
    if skipped_reason:
        return 'skipped'
    if ok_count >= target_count:
        return 'success'
    return 'failed' if ok_count == 0 else 'partial'
