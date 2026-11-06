"""The retained v2.9 version strings, current account-release versions and the ones that stay put.

Offline: imports the constants only. A bump or a rename shows up here before it reaches stored rows.
Since Task 0 the version constants live in ``investment_assistant/core/versions.py``; the assertions
below pin the current values: since Task 4 the stages registry is stages-6, the daily graph daily-3 and the
Agent-managed account agent-portfolio-2 (their predecessors stay in core.versions for historical rows)."""
from investment_assistant.core import versions


def test_new_v212_v215_versions_are_pinned_in_core_only():
    """The v2.12–v2.15 plan Global Constraints, verbatim; effective from Task 4, defined in core.versions only."""
    assert versions.AGENT_RUN_VERSION == 'agent-4'
    assert versions.PLAN_SCHEMA_VERSION == 'agent-3' == versions.AGENT_V3
    assert versions.PORTFOLIO_AGENT_VERSION == 'agent-portfolio-2'
    assert versions.PM_AGENT_VERSION == 'agent-pm-1'
    assert versions.OPTIMIZE_AGENT_VERSION == 'agent-optimize-1'
    assert versions.BRIEF_AGENT_VERSION == 'agent-impl-brief-1'
    assert versions.STAGES_VERSION == 'stages-6'
    assert versions.DAILY_GRAPH_VERSION == 'daily-3'
    assert versions.PORTFOLIO_CHECK_VERSION == 'portfolio-check-1'
    assert versions.INDICATOR_VERSION == 'indicator-1'
