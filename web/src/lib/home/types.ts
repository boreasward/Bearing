import type { MarketData } from '$lib/stores/types'

// Page-API types: the binding contract is Task 5's route table (investment_assistant/api/routes).
// Field names are copied verbatim from that table; the additions beyond it are
// `close_above_entry` and `missing_bar` on plan cards (spec §6.3, rulings R7 + final-review F1).
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

export interface BriefSection { key: string; title: string; markdown: string }
export type BriefMarket = Omit<MarketData, 'watch'>

export interface Brief {
  session: string
  status: string
  markdown: string | null
  sections?: BriefSection[]
  lead?: string | null
  footer?: string | null
  market?: BriefMarket | null
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
  /** pending plans whose session bar is missing — renders 「缺日线，未复查」 (spec §6.3 末段) */
  missing_bar: boolean
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

// GET /api/research?group=…、GET /api/research/{id}、POST /api/research/{id}/drop（v3.2 接口契约表：
// ResearchCard 19 键）。价位与金额是字符串；condition 为 null 表示尚无条件；condition_met_today
// 为 null 表示无条件 / 缺日线 / 均线历史不足（真值只说明按最后一根已知日线判到了）。
export interface ResearchCondition {
  kind: string
  level: string | null
  ma: number | null
}

export interface ResearchCardData {
  research_id: number
  ticker: string
  status: string
  thesis: string
  created_session: string
  day: number
  valid_until: string
  condition: ResearchCondition | null
  condition_text: string | null
  last_close: string | null
  condition_met_today: boolean | null
  triggered_session: string | null
  verdict: string | null
  reasons: string | null
  plan_id: number | null
  ended_session: string | null
  end_reason: string | null
  missing_bar: boolean
  created_at: string
}

export interface ResearchEvent {
  session: string | null
  event: string
  reason: string | null
  before: Record<string, unknown>
  after: Record<string, unknown>
  source: string
  created_at: string
}

export interface ResearchDetail extends ResearchCardData {
  events: ResearchEvent[]
}

// GET /api/retrospect?limit=20&week=…（facts.retrospect.retrospect_view 的输出；日期 ISO 字符串、
// Decimal 全部序列化成字符串，统计值不做浮点运算）。
export interface RetroTagRow {
  code: string
  text: string
  sessions: number | null
}

export interface Hypothetical {
  status: string | null
  reason: string | null
  fill_session: string | null
  fill_price: string | null
  exit_session: string | null
  exit_price: string | null
  exit_reason: string | null
  sessions_held: number | null
  r_multiple: string | null
}

export interface RetroRecord {
  plan_id: number
  ticker: string
  status: string
  kind: string
  origin_kind: string
  session: string
  ended_session: string | null
  end_reason: string | null
  planned: {
    entry_low: string | null
    entry_high: string | null
    initial_stop: string | null
    target1: string | null
    valid_from: string | null
    valid_until: string | null
    shares: number | null
    max_hold_sessions: number | null
  }
  actual: {
    // quantity 是 NUMERIC 字符串，后端已去尾零（core.texts.quantity_value，M2）
    first_buy: { price: string; session: string; quantity: string } | null
    last_sell: { price: string; session: string } | null
    quantity: string
  }
  outcome: CloseOutcome
  r_multiple: string | null
  r_text: string | null
  tags: RetroTagRow[]
  followed: boolean
  hypothetical: Hypothetical | null
  groups: { trigger_rules: string[]; setup_tier: string | null; industry: string | null }
}

export interface WeeklySummary {
  week_start: string
  week_end: string
  ended: number
  followed: number
  deviated: number
  tag_counts: Record<string, number>
  // 每行带 plan_id：同一周同一股票可以有两份已结束的计划（取消后重建），行键不能用 ticker（F3）
  closed: { plan_id: number; ticker: string; r_multiple: string | null; exit_reason: string | null; tags: string[] }[]
  total_r: string
  not_bought: { plan_id: number; ticker: string; end_reason: string | null; hypothetical: Hypothetical | null }[]
  equity_start: string | null
  equity_end: string | null
  realized_pnl: string
  listing: { new: string[]; dropped: string[]; most_relisted: string | null }
}

// 样本不足的组胜率 / 平均 R / R 合计为 null，insufficient 为真（spec §5.5）。
export interface RuleStatRow {
  group: string
  sample: number
  win_rate: string | null
  avg_r: string | null
  total_r: string | null
  insufficient: boolean
}

export type RuleKey = 'trigger_rules' | 'setup_tier' | 'industry'

export interface Retrospect {
  records: RetroRecord[]
  weekly: WeeklySummary
  rules: { realized: Record<RuleKey, RuleStatRow[]>; hypothetical: Record<RuleKey, RuleStatRow[]> }
  empty: boolean
  min_sample: number
  note: string
}

/** The server error envelope is `{"error": "code[: 中文说明]"}`: show the Chinese part, drop the code prefix. */
export function errorText(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error)
  const match = message.match(/^[a-z_]+: (.+)$/s)
  return match ? match[1] : message
}
