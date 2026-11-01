"""Every convention version string in one module (spec §4.2, v2.12–v2.15 plan).

Task 0 cuts the existing constants here verbatim (each origin module re-exports its
names until Task 1) and adds the new v2.12–v2.15 versions from the plan's Global
Constraints. A bump or a rename is caught by ``tests/test_v29_version_constants.py``
before it reaches stored rows.

The three names whose values change with v2.12–v2.15 — ``STAGES_VERSION``,
``DAILY_GRAPH_VERSION``, ``PORTFOLIO_AGENT_VERSION`` — carry their *new* values here
from the start. The current values keep living where they are used today
(``core.stages.STAGES_VERSION = 'stages-5'``, ``workbench/workflow.DAILY_GRAPH_VERSION
= 'daily-2'``, ``workbench/agent/graph.PORTFOLIO_AGENT_VERSION = 'agent-portfolio-1'``)
until Task 4 upgrades the graph; importing these three before Task 4 is a bug."""

# --- plan / agent schema versions (existing, cut from plans.py / timeline.py) ---
PLAN_V1 = 'plan-1'
PLAN_V2 = 'plan-2'
AGENT_VERSION = 'agent-1'           # the agent track's legacy plan version (plans.py)
AGENT_V2 = 'agent-2'                # plan_version of agent-2 plans and agent_version of their runs (timeline.py)
AGENT_V3 = 'agent-3'                # v2.9: agent-2 plus entry_type / max_hold_sessions and the host fields

# --- timeline (timeline.py) ---
TIMELINE_V1 = 'timeline-1'
TIMELINE_V2 = 'timeline-2'

# --- conditions (agent/conditions.py) ---
CONDITIONS_V1 = 'condition-1'
CONDITIONS_VERSION = 'condition-2'

# --- plan shadow (plan_shadow.py) ---
SHADOW_V1 = 'plan-shadow-1'
SHADOW_V2 = 'plan-shadow-2'

# --- screening / regime / derivation / fetch policy ---
SCREEN_VERSION = 'screen-1'         # screen.py
REGIME_VERSION = 'regime-2'         # regime.py
DERIVATION_VERSION = 'derivation-1'     # derivation.py
FETCH_POLICY_VERSION = 'fetch-1'        # data/fetch_policy.py

# --- stages (workbench/stages.py) ---
STAGES_VERSION_V1 = 'stages-1'
STAGES_VERSION_V2 = 'stages-2'
STAGES_VERSION_V3 = 'stages-3'
STAGES_VERSION_V4 = 'stages-4'
STAGES_VERSION_V5 = 'stages-5'

# --- agent runs (agent/graph.py) ---
REVIEW_VERSION = 'agent-review-2'   # v2.9: the evidence block gains 「再次触发」 and 「持仓状态」 (D32)
RETRO_AGENT_V1 = 'agent-retro-1'
RETRO_AGENT_V2 = 'agent-retro-2'    # v2.9 D29: 「你的决定」 -> 「你的执行」
DISCUSS_AGENT_VERSION = 'agent-discuss-1'
INTERPRET_VERSION = 'agent-brief-1'

# --- accounts / paper trading (accounts.py, paper_fill.py, paper_strategies.py) ---
ACCOUNT_VERSION = 'account-1'
PAPER_FILL_VERSION = 'paper-fill-1'
FOLLOW_PLANS_VERSION = 'follow-plans-1'

# --- review-side versions (monthly.py) ---
MONTHLY_VERSION = 'monthly-5'

# --- new v2.12–v2.15 conventions (plan Global Constraints, 逐字); effective from Task 4 ---
AGENT_RUN_VERSION = 'agent-4'               # the agent subgraph's run version (plan schema stays agent-3)
PLAN_SCHEMA_VERSION = AGENT_V3              # 'agent-3': the plan schema agent-4 keeps writing
PORTFOLIO_AGENT_VERSION = 'agent-portfolio-2'   # v2.14: account decisions with the portfolio-check evidence block
PM_AGENT_VERSION = 'agent-pm-1'             # v2.14: the portfolio manager unit on the real account
OPTIMIZE_AGENT_VERSION = 'agent-optimize-1'     # v2.15: the propose node's model call
BRIEF_AGENT_VERSION = 'agent-impl-brief-1'      # v2.15: writes the implementation brief of an approved proposal
STAGES_VERSION = 'stages-6'                # 13 stages (portfolio, propose); registry flips in Task 4
DAILY_GRAPH_VERSION = 'daily-3'            # the daily graph with portfolio + propose; effective in Task 4
PORTFOLIO_CHECK_VERSION = 'portfolio-check-1'
INDICATOR_VERSION = 'indicator-1'
