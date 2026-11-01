/** v2.4 derivation view shapes and display text (spec 2026-10-27 §7 / §10).
 *
 * Numbers arrive as the backend's strings and are shown verbatim; the frontend never
 * reformats or rounds them. The two endpoint payloads share the name `trigger` with
 * DIFFERENT shapes: the plan derivation folds the trigger note inside `trigger`, the
 * unit derivation returns a bare candidate list with the note at the top level. */

export type ClauseResult = 'holds' | 'violated' | 'not_frozen'
export type StepSource = 'formula' | 'frozen' | 'user_input' | 'agent_output'
export type ConsistencyStatus = 'consistent' | 'mismatch' | 'incomplete'

export interface TriggerClause {
  text: string
  operands: Record<string, string | number | null>
  result: ClauseResult
  note?: string
}

export interface TriggerItem {
  candidate_id: string
  rule_id: string
  rule_version: string
  action: string
  clauses: TriggerClause[]
  note?: string
}

export interface Step {
  key: string
  label: string
  formula: string
  substitutions: Record<string, string>
  result: string | null
  stored: string | null
  source: StepSource
  consistent: boolean | null
  refs?: string[]
}

export interface Check {
  key: string
  label: string
  formula: string
  substitutions: Record<string, string>
  holds: boolean
}

export interface Mismatch {
  key: string
  result: string | null
  stored: string | null
}

export interface Consistency {
  status: ConsistencyStatus
  mismatches: Mismatch[]
  missing: string[]
}

export interface RunEvidence {
  ready: boolean
  run_id?: string | null
  stage?: string | null
  status?: string | null
  derivation?: { checked: number; consistent: number; mismatch: number; incomplete: number
                 items?: { plan_id: string; ticker: string | null; status: string }[] } | null
  note?: string | null
}

export interface DerivationPlan {
  id: string
  ticker: string | null
  signal_date: string | null
  kind: string | null
  plan_version: string | null
  status: string | null
}

export interface InputBar {
  id: string | number
  price_date: string
  open: string | number
  high: string | number
  low: string | number
  close: string | number
  revision: string | number
}

export interface DerivationInputs {
  bar_ids: (string | number)[]
  bars: InputBar[] | null
  bars_note: string
  ref_price: { value: string | number | null; source_bar_id: string | number | null }
  earnings: { id: string; status: string | null; earnings_date: string | null; fetched_at: string | null } | null
  earnings_note: string | null
  snapshot: { id: string | number; as_of: string | null; equity: string | number | null } | null
  equity_source?: 'ledger' | 'snapshot'
  snapshot_note: string | null
  exposure: { ticker_value: string | number | null; total_value: string | number | null; note: string } | null
  sizing_params: { risk_pct: string | number | null; single_cap: string | number | null
                   total_cap: string | number | null; note: string } | null
  regime: { id?: string | null; label?: string | null; calc_version?: string | null } | null
  sector: { etf?: string | null; rs20?: number | string | null; rs60?: number | string | null
            state?: string | null } | null
  next_earnings: string | null
  pool_tier: string | null
  source: string | null
  screen: { screen_run_id?: string | number; screen_group?: string; rs?: number | null; rank?: number | null } | null
  agent_run: { model?: string | null; agent_version?: string | null; [key: string]: unknown } | null
}

/** GET /api/workbench/plans/{plan_id}/derivation — `trigger` is an object, note folded inside. */
export interface Derivation {
  derivation_version: string
  plan: DerivationPlan
  trigger: { note: string; candidates: TriggerItem[] }
  inputs: DerivationInputs
  steps: Step[]
  checks: Check[]
  consistency: Consistency
  evidence: string
  run: RunEvidence
}

/** GET /api/workbench/units/{ticker}/{signal_date}/derivation — `trigger` is a bare candidate list. */
export interface UnitDerivation {
  derivation_version: string
  trigger: TriggerItem[]
  consistency: Consistency
  evidence: string
  note: string
  plan_ids: string[]
}

export const CONSISTENCY_TEXT: Record<ConsistencyStatus, string> = {
  consistent: '推导与重算一致',
  mismatch: '推导与已存值不一致',
  incomplete: '推导不完整',
}

export const CLAUSE_TEXT: Record<ClauseResult, string> = {
  holds: '成立',
  violated: '不成立',
  not_frozen: '未冻结，无法复核',
}

export const SOURCE_TEXT: Record<StepSource, string> = {
  formula: '公式',
  frozen: '冻结值',
  user_input: '你的输入',
  agent_output: 'Agent 引用',
}
