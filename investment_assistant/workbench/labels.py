"""Chinese names for the ten signal rules and the three candidate actions (v2.6 spec D27).

Backend single source for the run overview, "what to watch today" and the K-line
annotations; the frontend `RULE_LABELS` mirrors RULE_TEXT (a cross-language test pins it).
The tables live in ``investment_assistant/core/texts.py`` since Task 0; this module
re-exports them until Task 1."""

from investment_assistant.core.texts import ACTION_TEXT as ACTION_TEXT, RULE_SHORT as RULE_SHORT, \
    RULE_TEXT as RULE_TEXT
