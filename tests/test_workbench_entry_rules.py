"""The entry types."""

from investment_assistant.execution import entry_rules


def test_constants():
    assert entry_rules.ENTRY_TYPES == ("zone_limit", "breakout_stop", "next_open", "pullback")
