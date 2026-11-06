"""entry-1: the version string and the entry types."""
from investment_assistant.execution import entry_rules


def test_constants():
    assert entry_rules.ENTRY_RULES_VERSION == 'entry-1'
    assert entry_rules.ENTRY_TYPES == ('zone_limit', 'breakout_stop', 'next_open')
