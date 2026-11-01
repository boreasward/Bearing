"""The retained v2.9 version strings, current account-release versions and the ones that stay put.

Offline: imports the constants only. A bump or a rename shows up here before it reaches stored rows."""
from investment_assistant.data import fetch_policy
from investment_assistant.workbench import (backcheck, derivation, discipline, entry_rules, execution, monthly,
                                            plan_shadow, plans, regime, retro, review, screen, stages, timeline,
                                            tracks, workflow)
from investment_assistant.workbench.agent import chatgpt, conditions, graph


def test_v29_versions_are_final():
    assert plans.PLAN_VERSION == 'plan-2'
    assert plans.PLAN_V1 == 'plan-1'                       # kept: old rule baselines are never recomputed
    assert timeline.TIMELINE_VERSION == 'timeline-2'
    assert conditions.CONDITIONS_VERSION == 'condition-2'
    assert entry_rules.ENTRY_RULES_VERSION == 'entry-1'
    assert plan_shadow.SHADOW_VERSION == 'plan-shadow-2'
    assert plan_shadow.SHADOW_V1 == 'plan-shadow-1'        # kept: plan-shadow-1 snapshots stay readable
    assert execution.EXECUTION_VERSION == 'execution-1'
    assert discipline.DISCIPLINE_VERSION == 'discipline-2'
    assert tracks.TRACKS_VERSION == 'tracks-2'
    assert retro.RETRO_VERSION == 'retro-2'
    assert graph.RETRO_AGENT_VERSION == 'agent-retro-2'
    assert graph.REVIEW_VERSION == 'agent-review-2'
    assert review.CALC_VERSION == 'review-8'
    assert monthly.MONTHLY_V4 == 'monthly-4'              # kept: historical monthly-4 reports stay readable


def test_agent_proposals_of_new_chains_are_agent_3():
    assert timeline.AGENT_V3 == 'agent-3'
    assert graph.AGENT_V3 == timeline.AGENT_V3             # the proposal / revision prompt and checks of new chains
    assert timeline.AGENT_V2 == 'agent-2'                  # kept: old chains and their versions keep reading


def test_account_release_versions_are_current_and_keep_the_previous_stage_registry():
    assert monthly.MONTHLY_VERSION == 'monthly-5'
    assert stages.STAGES_VERSION == 'stages-5'
    assert workflow.DAILY_GRAPH_VERSION == 'daily-2'
    assert stages.STAGES_VERSION_V4 == 'stages-4'
    assert stages.STAGE_IDS_BY_VERSION['stages-4'] == (
        'sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'push', 'retro')


def test_untouched_versions_did_not_move():
    assert fetch_policy.FETCH_POLICY_VERSION == 'fetch-1'
    assert chatgpt.CHATGPT_PROTOCOL_VERSION == 'siwc-1'
    assert graph.INTERPRET_VERSION == 'agent-brief-1'
    assert graph.DISCUSS_AGENT_VERSION == 'agent-discuss-1'
    assert derivation.DERIVATION_VERSION == 'derivation-1'
    assert screen.SCREEN_VERSION == 'screen-1'
    assert regime.REGIME_VERSION == 'regime-2'
    assert plans.CONTEXT_VERSION == 'context-2'
    assert backcheck.CALC_VERSION == 'backcheck-2'
