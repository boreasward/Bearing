/** v2.6 knowledge layer on the front end: shapes of the knowledge / run-overview / reasoning / instrument
 *  extensions, the cached concept index, and the table that says which concept each page field points at
 *  (spec 2026-10-28 §6.2). Templates never write a concept id themselves: they read `ATTACH` or the helpers below. */
import * as api from './api'
import type { KnowledgeRefItem } from './growth'

// ---- concept cards (GET /api/knowledge/*) ----

export interface ConceptSummary {
  id: string; ref: string; title: string; group: string; version: number
  applies_to: string[]; summary: string; related: string[]
}
export interface ConceptGroup { id: string; title: string; count: number }
export interface ConceptsView { knowledge_version: string; groups: ConceptGroup[]; concepts: ConceptSummary[] }
export interface ConceptLink { id: string; title: string }
export interface ConceptCard extends ConceptSummary {
  html: string; related_cards: ConceptLink[]; backlinks: ConceptLink[]
  stale: { requested: number; current: number; note: string } | null
}
export interface PrinciplesSection { heading: string; html: string; stage: string | null; concepts: string[] }
export interface PrinciplesView { title: string; version: number; sections: PrinciplesSection[] }

/** Group id → page title (spec §4.1); the API's `groups` carry the same text with the counts. */
export const GROUP_TEXT: Record<string, string> = {
  family: '数据族', indicator: '指标', rule: '规则 v1', plan: '规则计划 `plan-1`',
  agent: 'Agent 计划', review: '复盘', system: '系统运作',
}

// ---- run overview (GET /api/workbench/run-overview) ----

export interface RunInfo {
  run_id: string; trigger: string; trigger_text: string; stages_version: string
  started_at: string | null; finished_at: string | null; rows: number; closed?: boolean
}
export interface SkipItem { reason: string; text: string; count: number; samples: string[] }
export interface FailureItem { target: string; reason: string }
export interface DegradationItem { kind: string; text: string; detail: string | null }
export interface StageLine {
  id: string; title: string; purpose: string
  status: string; status_text: string; sentence: string
  input_count: number | null; output_count: number | null
  skips: SkipItem[]; failures: FailureItem[]; degradations: DegradationItem[]
  reason: string | null; reason_text: string | null; duration_ms: number | null; concepts: string[]
}
export interface RunOverviewView {
  session: string; ready: boolean; note: string | null
  runs: RunInfo[]; selected_run_id: string | null; headline: string; stages: StageLine[]
}

// ---- Agent reasoning (GET /api/workbench/agent-runs/{run_id}/reasoning) ----

export interface ReasoningRun {
  id: string; ticker: string; signal_date: string; mode: string; agent_version: string
  status: string; reason: string | null; reason_text: string | null; model: string | null; plan_id?: string | null
  cutoff_at: string | null; started_at: string | null; finished_at: string | null
  input_tokens: number; output_tokens: number
}
export interface ReasoningCall {
  seq: number; ref: string; tool: string; tool_text?: string; phase?: string
  args: Record<string, unknown>; result: unknown; error?: unknown; result_sha256_8: string; called_at: string
  concept: string | null; fields: Record<string, string>; cutoff_exempt: boolean; cutoff_note?: string | null
}
export interface ReasoningCondition { kind: string; params?: Record<string, unknown>; text?: string; refs?: string[] }
/** One reading step; `kind` picks which of the optional fields the page shows (spec §8.2, backend reasoning.py). */
interface ReasoningStepFields {
  title: string; concept: string | null; note?: string | null
  /** false on a proposal / counter / verdict / interpretation step the rejected run never stored; `text` says why. */
  available?: boolean; text?: string | null
  // read
  calls?: ReasoningCall[]
  // proposal
  action?: string; action_text?: string; summary?: string
  levels?: { key: string; label?: string; value: string; refs: string[]; concept: string | null }[]
  thesis?: { id?: string; text: string; refs: string[] }[]
  reasons?: { text: string; refs: string[] }[]                           // agent-1 「理由」
  invalidation?: ReasoningCondition[]; review_when?: ReasoningCondition[]
  changes?: { field: string; text: string; refs: string[] }[]
  diff?: { field: string; before: unknown; after: unknown }[]
  // check / counter / response (the list items differ by kind)
  items?: Record<string, any>[]; reason?: string | null; reason_text?: string | null
  // counter
  risks?: { text: string; refs: string[] }[]; dropped?: number; uncountered?: (string | number)[]
  // evidence
  review_id?: number | null; session?: string; trigger?: string
  signals?: { kind: string; fired: boolean; detail?: string | null }[]
  conditions?: { list: string; index: number; kind: string; params?: Record<string, unknown>; result: string }[]
  user_input_ids?: number[]; review_agent_run_id?: string | null; revision_agent_run_id?: string | null
  // verdict (also carried by evidence for the review it quotes)
  outcome?: string; outcome_text?: string; decided_by?: string | null; refs?: string[]
  // outcome
  plan_id?: string | null; status?: string | null; invalid_reasons?: string[]; warnings?: string[]
  valid_from?: string | null; valid_until?: string | null
  consistency?: string | { status: string } | null
  // interpretation
  support?: { text: string; refs: string[] }[]; contra?: { text: string; refs: string[] }[]
  conflicts?: { text: string; refs: string[] }[]
  // v2.7 retro / discuss runs (spec §6.5): evidence lists the frozen fact keys; draft = narrative (summary + items) and
  // lessons; question = the user's turn with its attachment marker and the context; answer = text + refs + optional draft
  fact_keys?: string[]
  lessons?: { title: string; body: string; scope: string; counter_examples: string; refs: string[] }[]
  attachment?: string | null; context?: string | null
  knowledge_draft?: { title: string; body: string; scope: string; counter_examples: string; refs?: string[] } | null
}
export interface ResearchReasoningStep extends ReasoningStepFields {
  kind: 'research'; title: '公司研究'; concept: 'agent.company_researcher'
  status: import('./research').ResearchPhase['status']
  reason: string | null; output: import('./research').ResearchOutput | null
  /** status 'carried' only (R16): the original run owns this segment's call:N refs; `original_status` is its status there */
  carried_from?: string | null; original_status?: import('./research').ResearchPhase['status'] | null
}
export interface OtherReasoningStep extends ReasoningStepFields {
  kind: 'evidence' | 'read' | 'proposal' | 'check' | 'counter' | 'response' | 'outcome' | 'verdict'
    | 'interpretation' | 'previous_chain' | 'draft' | 'question' | 'answer'
}
export type ReasoningStep = ResearchReasoningStep | OtherReasoningStep
export interface ReasoningView {
  run: ReasoningRun; legacy: boolean
  version: { plan_id: string; version_no: number; origin: string } | null
  review: Record<string, unknown> | null
  steps: ReasoningStep[]; notes: string[]
  /** knowledge this run's plan version cited (spec §6.2), when the backend sends it */
  knowledge_refs?: KnowledgeRefItem[]
}

// ---- instrument page (GET /api/workbench/instruments/{ticker}, spec §9.4) ----

export interface FocusItem { kind: string; text: string; link: string | null; concepts: string[] }
export interface KeyData {
  as_of: string | null
  values: Record<string, string | number | null>
  concepts: Record<string, string>
}
export interface InstrumentContext {
  earnings: { date: string | null; status: string | null; sessions_until: number | null } | null
  regime: { label: string; session: string } | null
  sector: { etf: string; state: string | null; rs20: number | string | null; rs60: number | string | null } | null
}
export interface InstrumentPlan {
  id: string; kind: 'rule' | 'own' | 'agent'; plan_version: string; signal_date: string
  entry_low: string | number; entry_high: string | number; stop: string | number; target1: string | number
  valid_from: string; valid_until: string; status: string; version_no: number | null
}
/** The instrument's holding as a 「今日」 holdings row (same backend function); stop is null for a holding without a plan. */
export interface InstrumentHolding {
  quantity: number; avg_cost: number | string; plan_id: string | null
  stop: string | number | null; mode: string | null; t1_reached: boolean; target1: string | number | null
  last_close: number | string | null; to_stop_pct: number | null; to_target_pct: number | null
}
export interface Marker {
  time: string; position?: string; shape: string; text?: string; kind?: string; refs?: string[]; color?: string
}
export interface Segment {
  plan_id?: string; kind: string; label?: string
  level: 'entry_high' | 'entry_low' | 'stop' | 'target1' | string
  from: string; to: string; value: string | number
  /** The plan's status: `ok`, or `invalid` for a voided plan (drawn faded). Absent reads as `ok`. */
  status?: 'ok' | 'invalid' | string
}
export interface Annotations { markers: Marker[]; segments: Segment[]; omitted: number }

// ---- concept index (ConceptTerm) ----

let indexPromise: Promise<Map<string, ConceptSummary>> | null = null

/** id → summary for every card. One request per page session; a failure is not cached, the next call retries. */
export function conceptIndex(): Promise<Map<string, ConceptSummary>> {
  if (!indexPromise) {
    const attempt: Promise<Map<string, ConceptSummary>> = Promise.resolve()
      .then(() => api.getConcepts())
      .then(view => {
        if (!view || !Array.isArray(view.concepts)) throw new Error('概念卡索引格式不对')
        return new Map(view.concepts.map(c => [c.id, c] as [string, ConceptSummary]))
      })
    indexPromise = attempt
    attempt.catch(() => { if (indexPromise === attempt) indexPromise = null })
  }
  return indexPromise
}

/** Forget the cached index (tests). */
export function resetConceptIndex(): void { indexPromise = null }

// ---- which concept each field points at (spec §6.2) ----

/** ATR is a derived indicator with its own concept card; every other family has a family card. */
export const familyConcept = (familyId: string) => familyId === 'atr' ? 'indicator.atr14' : `family.${familyId}`
export const ruleConcept = (ruleId: string) => `rule.${ruleId}`

export const ATTACH = {
  // 「数据」菜单
  data: {
    evidence: 'system.gate_hold',
    barsAdjust: 'family.bars',
    atr: 'indicator.atr14',
    atr_pct: 'indicator.atr14',
    BAMLH0A0HYM2: 'indicator.hy_oas',
    T10Y2Y: 'indicator.t10y2y',
    universeMeta: 'system.screen',
  },
  // 推导视图 PlanDerivation（触发段的规则名用 ruleConcept，逐项计算的项目列用 derivationStepConcept）
  derivation: {
    consistency: 'plan.derivation',
    evidence: 'system.gate_hold',
    refPrice: 'plan.ref_price',
    earnings: 'plan.earnings',
    snapshot: 'plan.sizing',
    exposure: 'plan.sizing',
    sizingParams: 'plan.sizing',
    regime: 'indicator.regime',
    sector: 'indicator.sector_strength',
    bars: 'family.bars',
    run: 'system.stages',
  },
  // 计划卡 AgentPlanCard
  agentCard: {
    version: 'agent.version_chain',
    cutoff: 'system.cutoff',
    levels: 'agent.action_levels',
    citation: 'agent.citation',
    thesis: 'agent.thesis',
    counter: 'agent.counter',
    invalidation: 'agent.invalidation',
    reviewWhen: 'agent.review_when',
    changes: 'agent.changes',
    review: 'agent.review_outcome',
    evidence: 'system.gate_hold',
    legacy: 'agent.legacy',
    rejectReasons: 'agent.reject_reasons',
    noAdd: 'plan.sizing',
  },
  // 规则基线 RuleBaseline
  ruleBaseline: {
    entry: 'plan.entry_zone',
    stop: 'plan.stop',
    target1: 'plan.target1',
    sizing: 'plan.sizing',
    status: 'plan.status',
  },
  // 版本链 PlanTimeline
  timeline: {
    origin: 'agent.version_chain',
    chainState: 'review.chain_state',
    outcome: 'agent.review_outcome',
    signals: 'agent.signals',
    conditions: 'agent.invalidation',
    userInput: 'agent.user_input',
    shadow: 'review.plan_shadow',
  },
  // 「今日」计划表的执行状态列（v2.9 R20：决定退役，执行依据 basis 改指执行状态）
  today: {
    basis: 'review.execution_status',  // v2.9 R20: the execution basis is derived from the fills, not a decision
  },
  // 「标的」页（关键数据一段的概念由接口的 key_data.concepts 给出，规则名用 ruleConcept）
  instrument: {
    trigger: 'system.decision_unit',
    chain: 'agent.version_chain',
    chainState: 'review.chain_state',
    stop: 'plan.stop',
    target1: 'plan.target1',
    trailing: 'plan.trailing_stop',
    position: 'review.position',
    userInput: 'agent.user_input',
    earnings: 'plan.earnings',
    regime: 'indicator.regime',
    sector: 'indicator.sector_strength',
    news: 'family.news',
    filings: 'family.sec_filings',
    movingAverage: 'indicator.moving_average',
    entryZone: 'plan.entry_zone',
  },
  // 运行一览
  overview: {
    stages: 'system.stages',
    status: 'system.stage_status',
  },
  // v2.7 复盘卡（结束类型、卡本身、三轨道、纪律四项）
  retroCard: {
    card: 'review.retro_card',
    end: 'review.retro_end',
    tracks: 'review.tracks',
    chase: 'review.discipline_chase',
    earlyTake: 'review.discipline_early_take',
    heldLoser: 'review.discipline_held_loser',
    unplanned: 'review.discipline_unplanned',
  },
  // v2.7 我的知识（条目、表现回看、计划卡里的「引用的知识」）
  myKnowledge: {
    item: 'system.my_knowledge',
    performance: 'review.knowledge_performance',
    citation: 'agent.knowledge_citation',
  },
  // v2.7 就此计划讨论
  discussion: {
    about: 'system.discussion',
  },
  // v2.7 周度「你学到了什么」
  weeklyLearning: {
    learning: 'review.weekly_learning',
  },
} as const

const STEP_CONCEPT: Record<string, string> = {
  atr: 'indicator.atr14', ref_price: 'plan.ref_price',
  entry_low: 'plan.entry_zone', entry_high: 'plan.entry_zone',
  stop: 'plan.stop', target1: 'plan.target1', trail_atr_multiple: 'plan.trailing_stop',
  valid_from: 'plan.validity', valid_until: 'plan.validity', context_flags: 'plan.context_flags',
  per_share_risk: 'plan.sizing', risk_amount: 'plan.sizing', equity: 'plan.sizing', ticker_value: 'plan.sizing',
  total_value: 'plan.sizing', shares: 'plan.sizing', applied_cap: 'plan.sizing', 'sizing.status': 'plan.sizing',
  invalid_reasons: 'plan.status', warnings: 'plan.status', status: 'plan.status', levels_ordered: 'plan.status',
  entry_type: 'plan.entry_type', max_hold_sessions: 'plan.max_hold',
}

/** The concept of a derivation step by its `key` (spec §6.2 row「逐项计算的项目列」); unmapped steps get none. */
export function derivationStepConcept(key: string): string | null {
  if (key.startsWith('tr_')) return 'indicator.atr14'
  if (key.startsWith('earnings')) return 'plan.earnings'
  if (key.startsWith('cap_')) return 'plan.sizing'
  return STEP_CONCEPT[key] ?? null
}

/** Chinese name of each Agent tool (the API also sends `tool_text`). */
export const TOOL_TEXT: Record<string, string> = {
  get_trigger: '触发候选', get_price_bars: '日线', get_indicators: '指标', get_filings: 'SEC 文件',
  get_news: '资讯', get_earnings: '财报日', get_market_context: '市场环境', get_position: '持仓',
  get_concept: '概念卡', get_knowledge: '我的知识',
  // 讨论专用（只读）
  get_plan_context: '计划上下文', get_derivation: '推导', get_agent_run: 'Agent 运行',
}
