"""The convention version strings the kept code stamps on stored rows.

Offline: imports the constants only. A bump or a rename shows up here before it reaches stored rows."""
from investment_assistant.core import versions


def test_retained_versions_are_pinned_in_core_only():
    assert versions.SCREEN_VERSION == 'screen-1'
    assert versions.REGIME_VERSION == 'regime-2'
    assert versions.FETCH_POLICY_VERSION == 'fetch-1'
    assert versions.INDICATOR_VERSION == 'indicator-1'
