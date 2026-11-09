import type { AccountRow, TodayAction, PlanCardData as LegacyPlanCardData, PlanPosition, ResearchCardData, Retrospect } from '$lib/home/types'
export type { Retrospect, ResearchCardData } from '$lib/home/types'

export interface TodayCounts { actions: number; holdings: number; pending: number; watching: number }
export interface TodayData {
  session: string | null
  cutoff_at: string
  actions: (Omit<TodayAction, 'quantity'> & { quantity: string })[]
  counts: TodayCounts
  account: Omit<AccountRow, 'cash' | 'equity'> & { cash: string | null; equity: string | null }
}
export interface ChatListItem {
  chat_id: string
  title: string
  created_at: string
  last_message_at: string
  read_only: boolean
  model: string
}
export interface WatchRow {
  ticker: string
  last_close: string | null
  prev_close: string | null
  change_pct: string | null
  source: 'holding' | 'pending' | 'research'
  plan_id: number | null
  research_id: number | null
}
export interface MarketData {
  session: string
  calc_version: string | null
  label: string
  regime_tier: string
  breadth: {
    tickers: number; eligible_50: number; eligible_200: number; eligible_52w: number
    above_50: number; above_200: number; pct_50: number | null; pct_200: number | null
    new_highs: number; new_lows: number
  }
  spy_ret20: number | null
  etf_table: { etf: string; ret20: number | null; rs20: number | null; rs60: number | null; state: string | null }[]
  macro: Record<string, { value: number; obs_date: string; change: number | null; change_from: string | null } | null>
  watch: WatchRow[]
}
export interface RunsData {
  schedule: { weekdays: number[]; hour: number; minute: number; tz: string; next_at: string }
  runs: {
    session: string; trigger: string; status: string; error: string | null
    started_at: string; finished_at: string | null
    brief: {
      status: string; error: string | null; unfinished: boolean
      runtime: string | null; provider: string | null; model: string | null
      usage: Record<string, unknown>; elapsed_seconds: number | null
      pushed_final_at: string | null; pushed_error_at: string | null
      weekly: { present: boolean; pushed_at: string | null }
    } | null
  }[]
}
export interface VoidInfo { voided_by: number | null; void_reason: string | null }
export interface CashFlowRow extends VoidInfo {
  id: number; kind: string; amount: string; effective_at: string; note: string | null
}
export interface EquityRow { id: number; as_of: string; equity: string; note: string | null }
export interface LedgerData {
  trades: (VoidInfo & {
    id: number; ticker: string; side: string; quantity: string; price: string; fee: string
    executed_at: string; plan_id: number | null; note: string | null; request_id: string
  })[]
  cash_flows: CashFlowRow[]
  equity: EquityRow[]
  voids: { id: number; kind: string; target_id: number; reason: string; created_at: string }[]
}
export interface AccountData {
  ledger: {
    opening: (Omit<CashFlowRow, keyof VoidInfo> & { request_id: string; created_at: string; voided_at: string | null }) | null
    cash: string | null; equity: string | null; market_value: string; realized_pnl: string
    cash_negative: boolean; notes: string[]
    positions: { ticker: string; quantity: string; avg_cost: string; last_close: string | null
      market_value: string | null; unrealized_pnl: string | null; unrealized_pct: string | null }[]
  }
  latest_equity: EquityRow | null
  reconciliation: { as_of: string; reported: string; computed: string | null; difference: string | null } | null
  recent_outcomes: { plan_id: number; ticker: string; ended_session: string | null; outcome: Record<string, unknown> }[]
  sizing_snapshot: { id: number | null; as_of: string; equity: string; source: string } | null
}
export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue }
export interface CallData {
  ref: string; tool: string; args: Record<string, JsonValue>; result: JsonValue
  error: string | null; called_at: string; truncated: boolean; result_available?: boolean
}
// Quantity is serialized as a string by the API; legacy home components require an explicit display conversion.
export interface PlanCardData extends Omit<LegacyPlanCardData, 'position'> {
  position: (Omit<PlanPosition, 'quantity'> & { quantity: string }) | null
}
export interface PlansData { plans: PlanCardData[] }
export interface ResearchData { research: ResearchCardData[] }
export interface ChatsData { chats: ChatListItem[] }
export type RetrospectData = Retrospect
