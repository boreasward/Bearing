"""The retained v2.9 version strings, current account-release versions and the ones that stay put.

Offline: imports the constants only. A bump or a rename shows up here before it reaches stored rows.
Since Task 0 the version constants live in ``investment_assistant/core/versions.py``; the assertions
below pin the current (pre-stages-6) values — the v2.12–v2.15 upgrades change them only in Task 4."""
from investment_assistant.core import versions
from investment_assistant.data import fetch_policy
from investment_assistant.workbench import (backcheck, derivation, discipline, entry_rules, execution, monthly,
                                            plan_shadow, plans, regime, retro, review, screen, stages, timeline,
                                            tracks, workflow)
from investment_assistant.workbench.agent import chatgpt, conditions, graph


def test_v29_versions_are_final():
    assert plans.PLAN_VERSION == 'plan-2'
    assert versions.PLAN_V1 == 'plan-1'                   # kept: old rule baselines are never recomputed
    assert versions.PLAN_V2 == plans.PLAN_VERSION
    assert versions.TIMELINE_V2 == 'timeline-2' and timeline.TIMELINE_VERSION == versions.TIMELINE_V2
    assert versions.CONDITIONS_VERSION == 'condition-2' and conditions.CONDITIONS_VERSION == versions.CONDITIONS_VERSION
    assert entry_rules.ENTRY_RULES_VERSION == 'entry-1'
    assert versions.SHADOW_V2 == 'plan-shadow-2' and plan_shadow.SHADOW_VERSION == versions.SHADOW_V2
    assert versions.SHADOW_V1 == 'plan-shadow-1'          # kept: plan-shadow-1 snapshots stay readable
    assert execution.EXECUTION_VERSION == 'execution-1'
    assert discipline.DISCIPLINE_VERSION == 'discipline-2'
    assert tracks.TRACKS_VERSION == 'tracks-2'
    assert retro.RETRO_VERSION == 'retro-2'
    assert versions.RETRO_AGENT_V2 == 'agent-retro-2' and graph.RETRO_AGENT_VERSION == versions.RETRO_AGENT_V2
    assert versions.REVIEW_VERSION == 'agent-review-2'
    assert review.CALC_VERSION == 'review-8'
    assert monthly.MONTHLY_V4 == 'monthly-4'              # kept: historical monthly-4 reports stay readable


def test_agent_proposals_of_new_chains_are_agent_3():
    assert versions.AGENT_V3 == 'agent-3'
    assert graph.AGENT_V3 == timeline.AGENT_V3 == versions.AGENT_V3   # the proposal / revision prompt and checks of new chains
    assert versions.AGENT_V2 == 'agent-2'                # kept: old chains and their versions keep reading
    assert timeline.AGENT_V2 == versions.AGENT_V2


def test_account_release_versions_are_current_and_keep_the_previous_stage_registry():
    assert versions.MONTHLY_VERSION == 'monthly-5' and monthly.MONTHLY_VERSION == versions.MONTHLY_VERSION
    assert versions.ACCOUNT_VERSION == 'account-1'
    assert versions.PAPER_FILL_VERSION == 'paper-fill-1'
    assert versions.FOLLOW_PLANS_VERSION == 'follow-plans-1'
    assert stages.STAGES_VERSION == 'stages-5'           # the registry default flips to stages-6 only in Task 4
    assert workflow.DAILY_GRAPH_VERSION == 'daily-2'     # likewise daily-3 (core.versions carries the future value)
    assert graph.PORTFOLIO_AGENT_VERSION == 'agent-portfolio-1'
    assert versions.STAGES_VERSION_V4 == 'stages-4'
    assert stages.STAGE_IDS_BY_VERSION['stages-4'] == (
        'sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'push', 'retro')


def test_untouched_versions_did_not_move():
    assert versions.FETCH_POLICY_VERSION == 'fetch-1' and fetch_policy.FETCH_POLICY_VERSION == versions.FETCH_POLICY_VERSION
    assert chatgpt.CHATGPT_PROTOCOL_VERSION == 'siwc-1'
    assert versions.INTERPRET_VERSION == 'agent-brief-1'
    assert versions.DISCUSS_AGENT_VERSION == 'agent-discuss-1'
    assert versions.DERIVATION_VERSION == 'derivation-1' and derivation.DERIVATION_VERSION == versions.DERIVATION_VERSION
    assert versions.SCREEN_VERSION == 'screen-1'
    assert versions.REGIME_VERSION == 'regime-2'
    assert plans.CONTEXT_VERSION == 'context-2'
    assert backcheck.CALC_VERSION == 'backcheck-2'


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
