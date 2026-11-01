"""Agent track: shared status and reason constants (P3 design §6.4).

REASON_TEXT is the single backend copy of the Chinese reason labels; the
frontend keeps its own in web/src/lib/workbench.ts and the two must match.
The table lives in ``investment_assistant/core/texts.py`` (as
``AGENT_REASON_TEXT``) since Task 0; this module re-exports it until Task 1.
"""
from investment_assistant.core.texts import AGENT_REASON_TEXT as REASON_TEXT

STATUSES = ('ok', 'pass', 'rejected', 'error', 'skipped')
