"""Convention version strings the kept code stamps on stored rows and checks against (spec §4.2).

A bump or a rename is caught by ``tests/test_v29_version_constants.py`` before it reaches stored rows."""

# --- plan / agent schema versions ---
PLAN_V1 = 'plan-1'
PLAN_V2 = 'plan-2'
AGENT_VERSION = 'agent-1'           # the agent track's legacy plan version

# --- screening / regime / derivation / fetch policy ---
SCREEN_VERSION = 'screen-1'
REGIME_VERSION = 'regime-2'
DERIVATION_VERSION = 'derivation-1'
FETCH_POLICY_VERSION = 'fetch-1'

# --- indicators ---
INDICATOR_VERSION = 'indicator-1'
