// Page-API types: the binding contract is Task 5's route table (investment_assistant/api/routes).
// Field names are copied verbatim from that table; the only addition beyond it is
// `close_above_entry` on plan cards (spec §6.3 第 4 条, ruling R7).
// Money and prices arrive as JSON strings (serialized Decimal) — never parse them into floats for
// display math, hand them to MetricValue's `raw` instead. Timestamps are ISO 8601 with `T` and a
// timezone offset; sessions and dates are `YYYY-MM-DD`.

// GET /api/today
export interface TodayAction {
  plan_id: number
  ticker: string
  kind: string
  text: string
  stop: string | null
  quantity: number
  reason: string | null
}

export interface AccountRow {
  cash: string
  equity: string
  unrealized_pnl: string
  realized_pnl: string
  cash_negative: boolean
  notes: string[]
}

export interface Today {
  session: string | null
  cutoff_at: string
  actions: TodayAction[]
  account: AccountRow
}

// GET /api/briefs, /api/briefs/latest, /api/briefs/{session}
export interface BriefListItem {
  session: string
  status: string
  pushed: boolean
}

export interface Brief {
  session: string
  status: string
  markdown: string | null
  error: string | null
  runtime: string | null
  provider: string | null
  model: string | null
  usage: Record<string, unknown>
  elapsed_seconds: number | null
  unfinished: boolean
}

// GET /api/plans, /api/plans/{id}
export interface PlanLevels {
  entry_low: string | null
  entry_high: string | null
  stop: string | null
  target1: string | null
}

export interface PlanPosition {
  quantity: number
  avg_cost: string
  last_close: string | null
  unrealized_pnl: string | null
  unrealized_pct: string | null
}

export type NextActionKind = 'stop_hit_unresolved' | 'sell_next_open' | 'unchecked' | 'stop_order' | 'hold'

export interface NextAction {
  kind: NextActionKind
  text: string
  stop: string | null
  reason: string | null
}

export interface CloseOutcome {
  realized_pnl?: string | null
  realized_pct?: string | null
  r_multiple?: string | null
  r_text?: string | null
  exit_reason?: string | null
  stop_delay_sessions?: number | null
  entry_in_zone?: boolean | null
}

export interface PlanCardData {
  plan_id: number
  ticker: string
  status: string
  kind: string
  levels: PlanLevels
  initial_stop: string | null
  trailing: boolean
  shares: number | null
  valid_from: string | null
  valid_until: string | null
  start_session: string | null
  max_hold_sessions: number
  next_action: NextAction | null
  position: PlanPosition | null
  held_sessions: number | null
  stop_order_status: string
  confirmed_stop_order: string | null
  outcome: CloseOutcome
  thesis: string | null
  created_at: string
  ended_session: string | null
  end_reason: string | null
  /** pending plans whose latest close is above the entry zone (spec §6.3 第 4 条) */
  close_above_entry: boolean
}

export interface PlanEvent {
  session: string | null
  event: string
  reason: string | null
  before: Record<string, unknown>
  after: Record<string, unknown>
  source: string
  created_at: string
}

export interface PlanDetail extends PlanCardData {
  events: PlanEvent[]
}

// GET /api/chats, /api/chats/{id}
export interface ChatListItem {
  chat_id: string
  title: string
  created_at: string
  read_only: boolean
  model: string
}

export interface ChatRow {
  chat_id: string
  title: string
  runtime: string
  provider: string
  model: string
  read_only: boolean
  created_at: string
}

export interface ChatMessage {
  seq: number
  role: string
  content: string
  status: string | null
  session: string | null
  tool_refs: string[] | null
  usage: Record<string, unknown> | null
  created_at: string
}

// GET/PUT /api/settings, GET /api/settings/options
export interface Settings {
  runtime: string | null
  provider: string | null
  model: string | null
}

export interface SettingsOptions {
  runtimes: string[]
  providers: string[]
  models: Record<string, string[]>
}

// The write endpoints answer `{write: false, preview}` before confirmation (A18).
export interface WritePreview {
  write: boolean
  preview?: string
}

// SSE payload of POST /api/chats/{id}/messages — one RuntimeEvent per frame.
export interface RuntimeEventPayload {
  kind: 'text' | 'tool_start' | 'tool_end' | 'usage' | 'done' | 'error' | 'cancelled'
  text?: string
  tool?: string
  ref?: string
  usage?: { input?: number; output?: number; total?: number }
}

/** The server error envelope is `{"error": "code[: 中文说明]"}`: show the Chinese part, drop the code prefix. */
export function errorText(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error)
  const match = message.match(/^[a-z_]+: (.+)$/s)
  return match ? match[1] : message
}
