"""Convention version strings the kept code stamps on stored rows and checks against (spec §4.2).

A bump or a rename is caught by ``tests/test_v29_version_constants.py`` before it reaches stored rows."""

# --- screening / regime / fetch policy ---
SCREEN_VERSION = 'screen-1'
REGIME_VERSION = 'regime-2'
FETCH_POLICY_VERSION = 'fetch-1'

# --- indicators ---
INDICATOR_VERSION = 'indicator-1'
