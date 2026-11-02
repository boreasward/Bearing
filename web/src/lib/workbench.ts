/** US workbench API client and pure helpers (design 2026-10-23). */
import type { AgentView, ChainRow, Change, Condition } from './timeline'
import type { Annotations, FocusItem, InstrumentContext, InstrumentHolding, InstrumentPlan, KeyData } from './knowledge'
import type { KnowledgeEntry } from './growth'

export type Action = 'buy' | 'sell' | 'watch'
export type FinalAction = Action | 'ignore'
export type Choice = 'accept' | 'reject' | 'downgrade'

export interface Candidate {
  id: string; ticker?: string; signal_date: string; action: Action; rule_id: string; rule_version?: string
  inputs?: { values?: Record<string, unknown>; thresholds?: Record<string, unknown> }; created_at?: string
}
export interface BriefItem { text: string; refs: string[] }
export interface Brief {
  id: string; status: 'ok' | 'fallback' | 'error'; error?: string | null; created_at?: string
  prompt_version?: string; model?: string | null
  output?: { summary: string; support: BriefItem[]; contra: BriefItem[]; conflicts: BriefItem[] }
}
export interface Filing {
  id: number; form: string; filing_date: string; accepted_at: string | null; items: string[] | null
  accession_number?: string
}
export interface NewsItem {
  id: number; source: 'yfinance' | 'sec_8k'; title: string; url: string; publisher: string | null
  published_at: string | null; fetched_at: string
}
export interface Position { quantity: number; avg_cost: number; opened_at: string }
export interface Plan {
  id: string; ticker: string; signal_date: string; kind: 'rule' | 'own' | 'agent'; plan_version: string
  status: 'ok' | 'invalid'; entry_low: number; entry_high: number; stop: number; target1: number
  ref_price: number; atr: number; valid_from: string; valid_until: string
  sizing: { status: 'sized' | 'capped' | 'no_equity' | 'no_room'; shares: number | null
            risk_amount?: number | null; snapshot_as_of?: string | null; snapshot_id?: string | number | null
            equity_source?: 'ledger' | 'snapshot' } | null
  invalid_reasons: string[]; warnings: string[]; based_on_id?: string | null
  inputs?: { context?: PlanContext; chain_mode?: 'entry' | 'holding' }   // chain_mode: R3, written by build_version_plan
  // v2.9 §5.1: execution fields; absent on plan-1 / agent-2 rows
  entry_type?: EntryType | null; max_hold_sessions?: number | null
}
export interface Holding {
  ticker: string; quantity: number; avg_cost: number; plan_id: string | null
  stop: number | null; mode: string | null; t1_reached: boolean; target1: number | null
  last_close: number | null; to_stop_pct: number | null; to_target_pct: number | null
  next_earnings: string | null
  has_chain?: boolean            // v2.5 D7: the holding's unit has an agent-2 chain (in any state)
  chain_state?: string | null    // that chain's state at S: holding, or ended under the holding (e.g. invalidated)
}
export interface Snapshot {
  id: string; request_id: string; as_of: string; equity: number; note: string | null
  recorded_at: string; supersedes_id: string | null
}
export interface CashFlow {
  id: string; request_id: string; flow_date: string; amount: number; note: string | null
  recorded_at: string; voided_at: string | null; void_reason: string | null
}
export interface AccountParams {
  risk_pct: number; single_cap: number; total_cap: number; public_url: string; return_tiers: number[]
}
export interface AccountView { snapshots: Snapshot[]; flows: CashFlow[]; params: AccountParams }
export interface PlanShadowOutcome {
  status: 'pending' | 'expired' | 'open' | 'closed' | 'voided'; fill_date: string | null; fill_price: number | null
  legs: { fraction: number; exit_date: string; exit_price: number; reason: string }[]
  r: number | null; return: number | null; spy_return: number | null
}
export interface PlanShadow {
  id: string; ticker: string; signal_date: string; kind: 'rule' | 'own' | 'agent'; plan_version: string
  status: string; outcome: PlanShadowOutcome
}
export interface PlanShadowView { session: string; since: string; calc_version: string; plans: PlanShadow[] }
export interface AgentItem { text: string; refs: string[]; id?: string }
export interface AgentOutput {
  action: 'buy' | 'pass'; summary: string
  levels: Record<'entry_low' | 'entry_high' | 'stop' | 'target1', { value: string; refs: string[] }> | null
  thesis: AgentItem[]
  // agent-2 (v2.5 §4.1)
  invalidation?: Condition[]; review_when?: Condition[]; changes?: Change[]
  research?: import('./research').ResearchPhase
}
export interface AgentCounter {
  status: string; risks: AgentItem[]; dropped: number
  counter?: { thesis: number; text: string; refs: string[] }[]; uncountered?: number[]   // agent-2
}
export interface AgentRunOk {
  id: string; status: 'ok' | 'pass'; model: string; agent_version: string
  output: AgentOutput | null
  counter: AgentCounter | null
  plan: Plan | null; input_tokens: number; output_tokens: number; created_at: string
}
export interface AgentRunFailed {
  id: string; status: 'rejected' | 'error' | 'skipped'; reason: string | null; created_at: string
}
export type AgentRun = AgentRunOk | AgentRunFailed
export interface AgentToolCall {
  seq: number; phase: 'propose' | 'counter'; tool: string
  args: Record<string, unknown>; result: Record<string, unknown>
  result_sha256: string; called_at: string
}
export type AgentRunDetail = (AgentRunOk | AgentRunFailed) & { tool_calls: AgentToolCall[] }
export interface DisciplineTrade {
  trade_id: string; ticker: string; track: 'rule' | 'own' | 'agent' | null
  flags: string[]; immature: string[]; r: number | null
}
export interface DisciplineView {
  session: string; since: string; trades: DisciplineTrade[]
  calc_version?: string; cutoff_at?: string | null
  counts: { chase: number; early_take: number; held_loser: number; unplanned: number }
  immature: Record<string, number>; planned_buys: number; compliance_rate: number | null
}
export interface MonthlyReview {
  id: string; month: string; calc_version: string; stats: {
    month: string; return: { status: string; return: number | null; net_flow?: number | null }
    tier: string | null; spy: { return: number; excess: number } | null; review_flag: string
    sensitivity: { approximate: boolean; count?: number; rows: { risk_pct: number; return: number; max_drawdown: number }[] }
    discipline: DisciplineView; plans: Record<string, { count: number; expired: number; r_mean: number | null; r_median: number | null }>
    review?: { flag: string; compound: number | null; spy_compound: number | null;
               months?: { month: string; return: number | null; spy_return: number | null }[] }
    tracks?: { units: TrackRow[]; summary: TrackSummary } | null
    generated_at: string
  }; notes: string; supersedes_id: string | null; created_at: string
}
export interface PlanContext {
  context_version: string; pool_tier: 'core' | 'extended' | 'unknown'
  regime: { id: string; label: string } | null
  sector: { etf: string; rs20: number | null; rs60: number | null
            state: 'strong' | 'weak' | 'mixed' | null } | null
  next_earnings: string | null
}
export interface MarketContext {
  id: string; session: string; calc_version: string
  label: 'risk_on' | 'neutral' | 'risk_off' | 'insufficient'
  breadth: { tickers?: number; eligible_50?: number; above_50?: number; pct_50?: number | null
             eligible_200?: number; above_200?: number; pct_200?: number | null
             eligible_52w?: number; new_highs?: number; new_lows?: number }
  sectors: Record<string, { rs20: number; rs60: number; state: 'strong' | 'weak' | 'mixed' } | null>
  macro: Record<string, { value: number | string; obs_date: string; change: number | string | null
                          change_from: string | null } | null>
  inputs: { macro_status?: Record<string, 'ok' | 'not_configured' | 'error'>; [key: string]: unknown }
  created_at: string
}
export interface ExtendedUnit {
  ticker: string; signal_date: string; proposed_action: Action; rule_ids: string[]; plan: Plan | null
  agent_status: string | null
}
export interface ExcessStat {
  n: number; mean: number | null; median: number | null; positive_share: number | null
  immature: number; missing: number
}
export interface BackcheckRow {
  triggers: number; ticker_sessions: number; per_100: number; max_per_ticker: number; cluster_days: number
  filled: number; expired: number; stopped_within_5: number; stopped_within_5_share: number | null
  t1_share: number | null
  r: { count: number; mean: number | null; median: number | null; p25: number | null; p75: number | null }
  excess?: Record<'5' | '10' | '20', ExcessStat>
  flags: string[]
}
export interface Backcheck {
  id: string; calc_version: string; created_at: string; params: Record<string, unknown>
  stats: { note: string; all: BackcheckRow; rules: Record<string, BackcheckRow>
           tickers: { ticker: string; warmup_end: string | null; eval_start: string | null
                      eval_end: string | null; eval_sessions: number }[] }
}
export interface Unit {
  ticker: string; signal_date: string; proposed_action: Action; labels: string[]; candidate_ids: string[]
  rule_ids: string[]; entry_passed: boolean; holding: boolean; source?: 'watchlist' | 'screen' | null
  candidates: Candidate[]; brief: Brief | null; filings: Filing[]; news: NewsItem[]; position: Position | null
  plan: Plan | null
  agent: AgentView | null
}
export interface ScreenTrigger {
  ticker: string; rule_ids: string[]; rs: number | null
  review: 'plan' | 'invalid' | 'conflict' | 'watch'
  plan: { entry_low: string; entry_high: string; stop: string; target1: string; shares: number | null;
          sizing_status: string | null } | null
  invalid_reasons: string[]
}
export interface ScreenReport {
  session: string; calc_version: string; status: string; error: string | null
  universe: { name: string; as_of: string; stale: boolean }
  counts: Record<string, number>; selected: string[]; members: number; cap: number
  evicted: string[]; removed: string[]; flags: string[]
  triggers: ScreenTrigger[]; setups: { ticker: string; rs: number | null; atr_pct: number | null; rank: number | null }[]
  setups_more?: number; results?: unknown[]
}
export interface TodayView {
  session: string; units?: Unit[]; extended_units?: ExtendedUnit[]; holdings?: Holding[]
  market: MarketContext | null; screen: ScreenReport | null
  chains?: ChainRow[]; timeline_ready?: boolean     // v2.5 §10.2; false = migration 026 missing
  portfolio_check?: PortfolioCheckView | null
}
export interface PortfolioVerdict {
  plan_id: string; ticker: string; group: string | null; verdict: 'ok' | 'blocked' | 'deferred'; reasons: string[]; order: number | null
}
export interface PortfolioResult {
  exposure_by_sector: Record<string, { held_value: string | number; pending_value: string | number; candidate_value: string | number; share_of_equity: string | number | null }>
  verdicts: PortfolioVerdict[]; conflicts: { group: string | null; plan_ids: string[]; rule: 'max_per_sector' }[]
  order: string[]; params_used: Record<string, string | number>; version: string
}
export interface PortfolioCommentary {
  summary: string; ranking: { plan_id: string; rank: number; reason: string }[]
  tradeoffs: { text: string; refs: string[] }[]; hold_actions: { ticker: string; suggestion: 'keep' | 'trim' | 'exit'; reason: string }[]
}
export interface PortfolioCheckView {
  status: 'ready' | 'not_computed' | 'degraded'; reason: string | null; session: string | null; cutoff_at: string | null
  result: PortfolioResult | null; commentary: PortfolioCommentary | null
}
export const PORTFOLIO_REASON_TEXT: Record<string, string> = {
  sector_cap: '超过行业集中度上限', max_per_sector: '超过同行业标的数量上限', single_cap: '超过单笔风险或单票敞口上限', total_cap: '超过组合总敞口上限',
  insufficient_cash: '现金不足', held_ticker: '已持有、在挂或重复的标的', max_positions: '最多持仓数', sector_unknown: '行业未归类',
  deferred_by_cash: '现金不足，暂缓执行',
}
export interface Decision {
  id: string; ticker: string; signal_date: string; proposed_action: Action; final_action: FinalAction
  decision: Choice; reason: string; decided_at: string; supersedes_id: string | null
}
export interface Trade {
  id: string; ticker: string; side: 'buy' | 'sell'; quantity: number; price: number; fee: number
  executed_at: string; decision_id: string | null; note: string | null; recorded_at: string
  voided_at: string | null; void_reason: string | null
  // v2.9 §6.1: the plan version this trade followed; `plan_label` is the backend's sentence for it
  plan_id?: string | null; plan_label?: string | null
}
export interface ChartBar {
  price_date: string; open: number; high: number; low: number; close: number; volume: number
  ma20: number | null; ma50: number | null; ma200: number | null
}
export interface InstrumentView {
  ticker: string; session: string; bars: any[]; filings: Filing[]; news: NewsItem[]; candidates: Candidate[]
  decisions: Decision[]; trades: Trade[]; position: Position | null; closed: any[]
  chart: { bars: ChartBar[]; adjustment: string; annotations?: Annotations }
  // v2.6 (§9.4): additive keys; an older backend simply lacks them
  holding?: InstrumentHolding | null
  focus?: FocusItem[]; key_data?: KeyData; context?: InstrumentContext; plans?: InstrumentPlan[]
  // a new block that failed to read is null and its reason is here, keyed by block name (focus, key_data, ...)
  errors?: Record<string, string>
  // v2.7 (§5.5): active knowledge entries attached to this ticker (latest version); absent on an older backend
  knowledge?: KnowledgeEntry[] | null
}
export interface Window { status: 'mature' | 'immature'; exit_date?: string; return?: number; spy_return?: number; excess?: number; move?: number }
export interface ShadowUnit extends Decision {
  rule_ids: string[]
  outcome: { status: 'entered' | 'pending_entry'; entry_date?: string; entry_price?: number; windows: Record<string, Window> }
}
export interface ShadowView { session: string; since: string; calc_version: string; units: ShadowUnit[] }
export interface TradeReview { session: string; calc_version: string; closed: any[]; open: any[] }
export interface WeeklyReview { id: string; week_start: string; calc_version: string; stats: any; notes: string; supersedes_id: string | null; created_at: string }

export class WorkbenchError extends Error {
  constructor(message: string, public status: number) { super(message) }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    cache: 'no-store', ...init,
    headers: { Accept: 'application/json', ...(init?.body ? { 'Content-Type': 'application/json' } : {}) },
  })
  const data = await r.json().catch(() => null)
  if (!r.ok) throw new WorkbenchError(data?.error ?? `HTTP ${r.status}`, r.status)
  return data as T
}
const post = <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(body) })

export const getToday = () => request<TodayView>('/api/workbench/today')
export const getScreen = (session?: string, full = false) =>
  request<{ report: ScreenReport | null }>(`/api/workbench/screen${session ? `?session=${encodeURIComponent(session)}` : '?'}${full ? `${session ? '&' : ''}full=1` : ''}`)
export const getInstrument = (ticker: string) => request<InstrumentView>(`/api/workbench/instruments/${encodeURIComponent(ticker)}`)
export const getTrades = (accountId?: string) => request<{ trades: Trade[] }>(`/api/workbench/trades${query({ account_id: accountId })}`)
export const postTrade = (body: TradeBody) => post<{ status: string; record: Trade }>('/api/workbench/trades', body)
export const voidTrade = (id: string, reason: string) => post<Trade>(`/api/workbench/trades/${id}/void`, { reason })
export const getShadow = () => request<ShadowView>('/api/workbench/review/shadow')
export const getTradeReview = () => request<TradeReview>('/api/workbench/review/trades')
export const getWeekly = () => request<{ reviews: WeeklyReview[] }>('/api/workbench/weekly-reviews')
export const postWeekly = (body: object) => post<WeeklyReview>('/api/workbench/weekly-reviews', body)
export const runWorkbench = () => post<{ run_id: string; status: string }>('/api/workbench/run', {})
export const getAccount = (accountId?: string) => request<AccountView>(`/api/workbench/account/snapshots${query({ account_id: accountId })}`)
export const postSnapshot = (body: object) => post<{ status: string; record: Snapshot }>('/api/workbench/account/snapshots', body)
export const postFlow = (body: object) => post<{ status: string; record: CashFlow }>('/api/workbench/account/flows', body)
export const voidFlow = (id: string, reason: string) => post<CashFlow>(`/api/workbench/account/flows/${id}/void`, { reason })
export const getPlanShadow = (since?: string) =>
  request<PlanShadowView>(`/api/workbench/plans/shadow${since ? `?since=${since}` : ''}`)
export const getAgentRun = (id: string) => request<AgentRunDetail>(`/api/workbench/agent-runs/${id}`)
export const rerunAgent = (ticker: string, signalDate: string) =>
  post<AgentRunOk | AgentRunFailed>('/api/workbench/agent-runs', { ticker, signal_date: signalDate })
export const getDiscipline = (accountId?: string) => request<DisciplineView>(`/api/workbench/discipline${query({ account_id: accountId })}`)
export const getMonthly = () => request<{ reviews: MonthlyReview[] }>('/api/workbench/monthly-reviews')
export const postMonthly = (body: object) => post<MonthlyReview>('/api/workbench/monthly-reviews', body)
export const pushToday = () => post<{ session: string; sent: boolean; messages?: number; reason?: string }>('/api/workbench/push', {})
export const getBackchecks = () => request<{ backchecks: Backcheck[] }>('/api/workbench/backchecks')
export const runBackcheck = (tickers?: string[]) => post<Backcheck>('/api/workbench/backchecks', { tickers: tickers ?? null })

/** The sentence after a `code: sentence` error body, else null. */
function sentenceAfter(message: string, code: string): string | null {
  if (!message.startsWith(code)) return null
  const rest = message.slice(code.length)
  if (rest !== '' && !rest.startsWith(':') && !rest.startsWith('：')) return null
  return rest.slice(1).trim() || null
}

export function describeError(error: unknown): string {
  if (error instanceof WorkbenchError) {
    const messages: Record<string, string> = {
      invalid_ticker: '标的格式不正确', feature_disabled: '该功能已关闭，请到功能开关开启',
      research_quota: '今日手动研究额度已用完（每天最多 5 次）', migration_031_missing: '数据库尚未执行迁移 031',
    }
    const code = error.message.split(/[:：]/)[0]
    if (messages[code]) return sentenceAfter(error.message, code) ?? messages[code]
    // v2.9 (§6.4, §13, §21.4): retired decisions, trade links, migration 029
    if (error.status === 410 && error.message.startsWith('decisions_retired'))
      return sentenceAfter(error.message, 'decisions_retired') ?? DECISIONS_RETIRED_SENTENCE
    if (error.status === 400 && error.message.startsWith('decision_link_retired')) return DECISION_LINK_RETIRED_TEXT
    if (error.status === 503 && (error.message.startsWith('migration_029_missing') || error.message.includes('迁移 029')))
      return MIGRATION_029_TEXT
    if (error.status === 409) {
      for (const code of Object.keys(CONFLICT_TEXT)) {
        if (error.message.startsWith(code)) return sentenceAfter(error.message, code) ?? conflictFallback(code)
      }
    }
    if (error.status === 503) return `数据库未就绪：${error.message}`
    if (error.status === 409) return `已有更新的记录，请刷新后基于最新记录操作（${error.message}）`
    if (error.status === 400) return `输入有误：${error.message}`
  }
  return `请求失败：${error instanceof Error ? error.message : String(error)}`
}

export const newRequestId = () => crypto.randomUUID()

export interface OwnPlanInput { entry_low: string; entry_high: string; stop: string; target1: string }

export function levelsError(input: OwnPlanInput): string | null {
  const [low, high, stop, target1] = [input.entry_low, input.entry_high, input.stop, input.target1].map(Number)
  if (![low, high, stop, target1].every(n => Number.isFinite(n) && n > 0)) return '四个价位都必须是正数'
  if (!(stop < low && low <= high && high < target1)) return '价位需满足：止损 < 入场下限 ≤ 入场上限 < T1'
  return null
}

export const RULE_LABELS: Record<string, string> = {
  trend_reclaim: '趋势回踩修复', volume_breakout: '放量突破', earnings_gap_up: '财报跳空向上',
  trend_break: '跌破 MA50', earnings_gap_down: '财报跳空向下', position_drawdown: '持仓回撤',
  new_filing: '新财报', abnormal_move: '异常波动',
  plan_stop: '计划止损', plan_target: '计划目标',   // v2.6 D27: sell rules of held positions with a plan
}
export const ACTION_LABELS: Record<string, string> = { buy: '买入', sell: '卖出', watch: '观察', ignore: '忽略' }
export const UNIT_LABELS: Record<string, string> = { conflict: '冲突', avoid: '回避' }
export const REGIME_LABELS: Record<string, string> = { risk_on: '进攻', neutral: '中性', risk_off: '防守', insufficient: '样本不足' }
export const SECTOR_STATE_LABELS: Record<string, string> = { strong: '强', weak: '弱', mixed: '分化' }
export const TIER_LABELS: Record<string, string> = { excellent_plus: '非常优秀', excellent: '优秀', viable: '可行', attention: '需关注' }
export const FLAG_LABELS: Record<string, string> = { chase: '追高', early_take: '过早止盈', held_loser: '死扛亏损', unplanned: '计划外' }
export const CHOICE_LABELS: Record<Choice, string> = { accept: '采纳', reject: '否决', downgrade: '降级为观察' }
export const KIND_TEXT: Record<string, string> = { rule: '规则', own: '自定', agent: 'Agent', agent_latest: 'Agent（最新版）' }
// v2.5 D17: from weekly review-6 / monthly review monthly-3 on, plans.agent counts each chain's first version
// (plans.agent_latest its head); older snapshots keep the plain「Agent」. Every later calc version (review-7 / review-8,
// monthly-4) keeps that meaning, so it is listed too.
const FIRST_VERSION_CALCS = new Set(['review-6', 'review-7', 'review-8', 'monthly-3', 'monthly-4'])
export function planKindText(kind: string, calcVersion: string | null | undefined): string {
  if (kind === 'agent' && calcVersion && FIRST_VERSION_CALCS.has(calcVersion)) return 'Agent（首版）'
  return KIND_TEXT[kind] ?? kind
}
export const AGENT_REASON_TEXT: Record<string, string> = {
  disabled: '已关闭', not_configured: '未配置', quota_exhausted: '额度用尽', token_budget: '超出 token 上限',
  stage_budget: '超出时限', max_attempts: '已达重试上限', malformed: '输出格式无效',
  invalid_citation: '引用无效已作废', level_check: '价位校验未通过', tool_rounds_exceeded: '工具调用超限',
  model_error: '模型调用失败', checkpoint_unavailable: '状态库未就绪',
  // agent-2 / daily review (v2.5 §4.4, D23): same as backend agent.REASON_TEXT
  thesis_count: '论点数量不符', invalid_condition: '失效或复查条件无效', changes_mismatch: '变化说明与实际差异不符',
  no_change: '修订与上版没有差异', review_malformed: '复查输出格式无效', migration_026_missing: '迁移 026 未执行',
  review_in_progress: '正在复查', plan_invalid: '修订版按规则作废',
  // v2.7 growth loop (spec §6.4, verbatim)
  retro_malformed: '复盘卡草稿格式无效', lesson_invalid: '教训草稿缺适用范围、反例或挂靠无效',
  answer_malformed: '回答格式无效', migration_027_missing: '迁移 027 未执行',
  discussion_busy: '上一个问题还在回答', draft_exists: '这张卡已有成功的草稿',
  // v2.8 ChatGPT connection (spec §8.8, verbatim; same as backend agent.REASON_TEXT)
  not_connected: 'ChatGPT 未连接', model_unavailable: '所选模型不可用',
  // v2.9 agent-3 (spec §5.5 + §21.4 R3, verbatim; same as backend agent.REASON_TEXT)
  invalid_entry_type: '入场类型不在允许的三种之内', invalid_max_hold: '最长持有须为 5–20 个交易日',
  stop_lowered: '持仓期不能下移止损', stop_above_close: '持仓期止损不能高于最新收盘',
  active_chain: '已有进行中的计划链，这次触发已并入',
  // v2.12-v2.15 researcher / portfolio manager / optimizer / brief writer (same as backend agent.REASON_TEXT)
  research_malformed: '研究输出格式无效', research_facts_count: '研究事实数量不符', research_kind: '研究事实种类无效',
  research_inference_unsupported: '研究推断缺少事实支持', research_refs_invalid: '研究引用无效',
  research_summary_length: '研究摘要长度不符', research_blocked_shape: '研究缺失数据说明无效',
  pm_malformed: '组合评论格式无效', pm_summary_length: '组合摘要长度不符', pm_levels_forbidden: '组合评论不得修改价位或股数',
  pm_ranking_invalid: '组合排序无效', pm_refs_invalid: '组合评论引用无效',
  proposal_malformed: '提案格式无效', proposal_source: '提案来源无效', proposal_title: '提案标题为空',
  proposal_length: '提案字段长度不符', proposal_refs_invalid: '提案证据引用无效', proposal_duplicate_title: '提案标题重复',
  proposal_limit: '超出每次三条提案上限',
  brief_empty: '实施说明为空', brief_length: '实施说明过长', brief_acceptance_missing: '实施说明缺少验收小节',
}

export const ruleLabel = (id: string) => RULE_LABELS[id] ?? id

export function pct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)}%`
}

export function dateTime(value: string | null | undefined): string {
  return value ? value.replace('T', ' ').slice(0, 16) : '—'
}

export function publishedText(value: string | null): string {
  return value ? dateTime(value) : '发布时间未知'
}

/** 「历史」时间线与成交列表里成交关联的计划（v2.9 §10.3）；旧成交只带决定。 */
export function tradePlanText(t: Pick<Trade, 'plan_id' | 'plan_label' | 'decision_id'>): string {
  if (t.plan_id) return ` · 关联计划：${t.plan_label ?? t.plan_id}`
  if (t.decision_id) return ' · 关联旧决定'
  return ''
}

export interface TimelineItem { kind: 'filing' | 'news' | 'candidate' | 'decision' | 'trade'; at: string; text: string; url?: string }

export function timeline(view: InstrumentView): TimelineItem[] {
  const items: TimelineItem[] = [
    ...view.filings.map(f => ({ kind: 'filing' as const, at: f.accepted_at ?? f.filing_date,
      text: `${f.form}${f.items?.length ? `（Items ${f.items.join(', ')}）` : ''}${f.accepted_at ? '' : ' · 受理时间未知'}` })),
    ...view.news.map(n => ({ kind: 'news' as const, at: n.published_at ?? n.fetched_at, url: n.url,
      text: `${n.title} · ${n.publisher ?? n.source} · ${publishedText(n.published_at)}` })),
    ...view.candidates.map(c => ({ kind: 'candidate' as const, at: c.created_at ?? c.signal_date,
      text: `${c.signal_date} ${ruleLabel(c.rule_id)} → ${ACTION_LABELS[c.action]}` })),
    ...view.decisions.map(d => ({ kind: 'decision' as const, at: d.decided_at,
      text: `${d.signal_date} ${CHOICE_LABELS[d.decision]} → ${ACTION_LABELS[d.final_action]}：${d.reason}` })),
    ...view.trades.map(t => ({ kind: 'trade' as const, at: t.executed_at,
      text: `${t.side === 'buy' ? '买入' : '卖出'} ${t.quantity} @ ${t.price}${t.voided_at ? `（已作废：${t.void_reason}）` : ''}${tradePlanText(t)}` })),
  ]
  return items.sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
}


// --- three-track comparison and review pushes (P4 design §4, §6) ---

export type TrackStatus = 'missing' | 'not_executable' | 'pending' | 'expired' | 'open' | 'closed' | 'pass'
  | 'not_executed' | 'no_plan' | 'voided' | 'merged'
export interface TrackResult { status: TrackStatus; r: number | null }
export interface RuleTrack extends TrackResult { plan_id: string | null }
export interface AgentTrack extends TrackResult { run_id: string | null; run_status: string | null; run_reason: string | null; plan_id: string | null }
export interface ActualTrack extends TrackResult {
  decision_id: string | null; final_action: string | null; plan_id: string | null; plan_kind: 'rule' | 'agent' | 'own' | null
  buy_trade_ids: string[]; open: boolean
}
export interface TrackRow {
  ticker: string; signal_date: string; pool: 'core' | 'extended'
  rule: RuleTrack; agent: AgentTrack; actual: ActualTrack
}
export interface TrackStats {
  units: number; determined: number; pending: number; missing: number
  mean: number | null; median: number | null; sum: number | null; wins: number
  by_status: Record<string, number>; by_plan_kind?: Record<string, number>
}
export interface TrackSummary { all: Record<'rule' | 'agent' | 'actual', TrackStats>; core: Record<'rule' | 'agent' | 'actual', TrackStats>; extended: Record<'rule' | 'agent' | 'actual', TrackStats> }
export interface TracksView { session: string; since: string; until: string; calc_version: string; units: TrackRow[]; summary: TrackSummary }
export interface ReviewPush {
  id: string; kind: 'weekly' | 'monthly' | 'monthly_reminder'; period: string; review_id: string | null
  trigger_source: 'scheduled' | 'manual'; sent: boolean; messages: number; reason: string | null; created_at: string
}

export const TRACK_STATUS_TEXT: Record<Exclude<TrackStatus, 'voided' | 'merged'>, string> = {
  missing: '缺失', not_executable: '计划作废', pending: '待定', expired: '到期未成交',
  open: '未平仓', closed: '已平仓', pass: '不建议', not_executed: '未执行', no_plan: '无计划成交',
}
/** tracks.TRACK_STATUS_TEXT_V2: tracks-2 adds two statuses (the table above stays the tracks-1 one). */
export const TRACK_STATUS_TEXT_V2: Record<TrackStatus, string> = {
  ...TRACK_STATUS_TEXT, voided: '开盘作废', merged: '并入其他链',
}

export const getTracks = (since?: string, until?: string, accountId?: string) => request<TracksView>(
  `/api/workbench/review/tracks${query({ since, until, account_id: accountId })}`)
export const getReviewPushes = () => request<{ pushes: ReviewPush[] }>('/api/workbench/review-pushes')
export const postReviewPush = (kind: 'weekly' | 'monthly', period: string) =>
  post<{ kind: string; period: string; sent: boolean; messages?: number; reason?: string }>(
    '/api/workbench/review-pushes', { kind, period })

export function rText(r: number | null): string {
  if (r === null || !Number.isFinite(r)) return '—'
  return `${r >= 0 ? '+' : ''}${r.toFixed(2)}R`
}


// =====================================================================================================
// v2.9 executable plans (spec docs/specs/2026-10-30-v2.9-executable-plans-design.md §5–§10, §21)
// Texts below are copied character for character from the spec; tests/test_executable_frontend_sync.py (Task 6)
// compares them with the backend constants.
// =====================================================================================================

export type EntryType = 'zone_limit' | 'breakout_stop' | 'next_open'
export type OpenCase = 'in_zone' | 'gap_up' | 'gap_down'
export type Instruction = 'hold' | 'sell_half' | 'exit' | 'no_plan'
export type ExecutionStatus = 'adopted' | 'out_of_validity' | 'pending' | 'not_executed'
export type RowGroup = 'entry' | 'holding' | 'ended'

/** entry_rules.ENTRY_TYPES labels (§5.2 table headings). */
export const ENTRY_TYPE_TEXT: Record<EntryType, string> = {
  zone_limit: '区间限价', breakout_stop: '突破买入', next_open: '次日开盘',
}
/** entry_rules.OPEN_CASE_TEXT: column heads of the three opening cases (§10.1). */
export const OPEN_CASE_TEXT: Record<OpenCase, string> = { in_zone: '区间内', gap_up: '高开', gap_down: '低开' }
/** entry_rules.TEXT (§5.2, verbatim): {L} = entry_low, {U} = entry_high, {S} = stop, {N} = shares; see `openCaseText`. */
export const OPEN_SCENARIO_TEXT = {
  in_zone: '开在 {L}–{U}：开盘买入 {N} 股',
  gap_up: {
    zone_limit: '高开过 {U}：不追，挂 {U} 限价等回落', breakout_stop: '高开过 {U}：不追，挂 {U} 限价等回落',
    next_open: '高开过 {U}：不买，本计划作废',
  },
  gap_down: {
    zone_limit: '低开：≤ {S} 作废；否则回升到 {L} 时买入', breakout_stop: '低开：≤ {S} 作废；否则升破 {L} 时买入',
    next_open: '低开过 {L}：不买，本计划作废',
  },
} as const
/** timeline.INSTRUCTION_TEXT (§7.3). */
export const INSTRUCTION_TEXT: Record<Instruction, string> = {
  hold: '继续持有', sell_half: '到 T1：卖出一半，余下按移动止损', exit: '次一交易日开盘卖出',
  no_plan: '无计划持仓（回撤规则看护）',
}
/** execution.EXECUTION_TEXT (§6.2 page column; `{date}` / `{quantity}` filled by `executionText`). */
export const EXECUTION_TEXT = {
  adopted: '已采纳（{date} 成交 {quantity} 股）', out_of_validity: '有效期外成交', pending: '入场期，未成交',
  not_executed: '未执行', executed: '已执行',
} as const
/** timeline.EXIT_REASON_TEXT: §7.3 `exit_reason` codes in words. */
export const EXIT_REASON_TEXT: Record<string, string> = {
  stop_touched: '止损触及', time_stop: '时间止损', invalidation: '失效条件成立', agent_invalidate: 'Agent 判作废',
}
export const STOP_MODE_TEXT: Record<string, string> = { initial: '初始', trailing: '移动' }

/** §6.4 error body sentence (after `decisions_retired: `). */
export const DECISIONS_RETIRED_SENTENCE = 'v2.9 起不再记录决定，采纳由成交推导；可在计划上写备注'
export const DECISIONS_RETIRED_TEXT = `decisions_retired: ${DECISIONS_RETIRED_SENTENCE}`
/** 400 `decision_link_retired` (§6.1; the spec names the code only, the sentence is this page's own). */
export const DECISION_LINK_RETIRED_TEXT = 'v2.9 起成交不再关联决定，请改为关联计划'
/** 503 `migration_029_missing` (§13). */
export const MIGRATION_029_TEXT = '迁移 029 未执行'
/** 409 texts, spec templates verbatim (R6 `plan_stale` / `plan_after_fill`, §4.7 `active_chain`, R10 `chain_exiting`).
 *  `{n}` `{ticker}` `{date}` are filled by the backend; the page shows the backend's finished sentence. */
export const CONFLICT_TEXT: Record<string, string> = {
  plan_stale: '成交时这条链已是第 {n} 版，请关联第 {n} 版',
  plan_after_fill: '这一版生成于成交之后，不可能是你照着做的那一版',
  chain_exiting: '这条链已发出退出指令，不再复查',
  active_chain: '{ticker} 已有进行中的计划链（{date} 第 {n} 版），这次触发已并入',
}
/** What the page shows for a bare 409 code (no sentence): the template with unknown parts as 「—」, except
 *  `active_chain`, which falls back to the void-reason sentence (§5.5). */
export function conflictFallback(code: string): string {
  if (code === 'active_chain') return AGENT_REASON_TEXT.active_chain
  return (CONFLICT_TEXT[code] ?? code).replace(/\{[a-z]+\}/g, '—')
}
export const CHAIN_EXITING_TEXT = CONFLICT_TEXT.chain_exiting
/** R18: a holding without a `holding_status` row today is computed on the fly. */
export const STALE_HOLDING_TEXT = '今日持仓状态未写入（每日复查失败），以下为现算'
/** §5.5 / §21: an agent-2 version has no execution fields. */
export const DEFAULTS_APPLIED_TEXT = '旧版计划，执行字段按默认值显示'
/** §10.4 note hint under the input. */
export const NOTE_HINT_TEXT = '备注只给你自己看；要让 Agent 参考，请用『我的补充』'
export const NOTE_MAX_CHARS = 1000

const OPEN_VAR = /\{([LUSN])\}/g

/** The sentence of one opening case with {L} / {U} / {S} / {N} replaced (shares unknown -> 「—」). */
export function openCaseText(
  kase: OpenCase, entryType: EntryType,
  vars: { entry_low: string | number; entry_high: string | number; stop: string | number; shares?: number | null },
): string {
  const map: Record<string, string> = {
    L: String(vars.entry_low), U: String(vars.entry_high), S: String(vars.stop), N: vars.shares == null ? '—' : String(vars.shares),
  }
  const template: string = kase === 'in_zone' ? OPEN_SCENARIO_TEXT.in_zone : OPEN_SCENARIO_TEXT[kase][entryType]
  return template.replace(OPEN_VAR, (_m, ch: string) => map[ch])
}

/** §6.2 page text of an execution state; `detail` fills 「已采纳（<日期> 成交 n 股）」 when the backend sends it. */
export function executionText(status: string | null | undefined, detail?: { date?: string | null; quantity?: number | string | null } | null): string {
  if (!status) return ''
  if (status === 'adopted') {
    if (detail?.date && detail.quantity != null)
      return EXECUTION_TEXT.adopted.replace('{date}', detail.date).replace('{quantity}', String(detail.quantity))
    return '已采纳'
  }
  if (status === 'unplanned') return '计划外成交'      // weekly by_execution key (§6.3); the page's own wording
  return (EXECUTION_TEXT as Record<string, string>)[status] ?? status
}

// ---- types (spec §9.1) ----

export interface RowRetrigger { unit_signal_date: string; rule_ids: string[]; version_no?: number | null }
export interface RowChain {
  signal_date: string; version_no: number; head_plan_id: string; state: string; label: string; retriggers: RowRetrigger[]
}
export interface RowPlan {
  plan_id: string; kind: 'rule' | 'own' | 'agent'; plan_version: string; action: 'buy' | 'pass' | string
  entry_type: EntryType | null; entry_type_text: string | null
  entry_low: string; entry_high: string; stop: string; target1: string
  shares: number | null; shares_note: string | null; valid_from: string; valid_until: string
  max_hold_sessions: number | null; status: string; defaults_applied: boolean
}
export interface RowBaseline {
  plan_id: string; plan_version: string; entry_low: string; entry_high: string; stop: string; target1: string
  shares: number | null; status: string
  active_chain: boolean | { ticker: string; signal_date: string }    // inputs.active_chain = {ticker, signal_date} (§5.4)
}
export interface OpenScenario { case: OpenCase; action: string; text: string }
export interface VoidWhen { source: 'validity' | 'gap' | 'invalidation' | 'max_hold' | 'stop' | string; text: string; last_result?: string | null }
export interface RowStatus {
  label: string; execution: ExecutionStatus | 'executed' | null    // chain level (R13): `executed`
  execution_basis?: string | null     // chain level: what it followed, e.g. 「照 Agent 第 2 版」
  execution_detail?: { date?: string | null; quantity?: number | string | null } | null
  latest_review?: { outcome: string; decided_by: string | null; reason: string | null; session: string; via?: string | null; trigger?: string | null } | null
  today_signals?: string[]; changes_text?: string[]; diff?: { field: string; before: unknown; after: unknown }[]
}
export interface PlanNote { id: number; body: string; created_at: string; ticker?: string; chain_signal_date?: string | null; plan_id?: string | null; supersedes_id?: number | null }
/** One `holding_status` row (§7.3 / §8.4); percentages are percent points (−5.2 = −5.20%). */
export interface HoldingStatus {
  ticker: string; session?: string; quantity: number | string; avg_cost: number | string; last_close: number | string | null
  stop: number | string | null; stop_mode: 'initial' | 'trailing' | string | null
  target1: number | string | null; t1_reached: boolean
  to_stop_pct: number | string | null; to_t1_pct: number | string | null
  held_sessions: number | null; max_hold_sessions: number | null; remaining_sessions: number | null
  r_now: number | string | null; reached_1r: boolean | null
  instruction: Instruction; exit_reason: string | null; reason: string | null
  review_id?: number | null; plan_id?: string | null; chain_signal_date?: string | null
  stale?: boolean; line?: string | null; detail?: Record<string, unknown> | null
}
export interface PlanTableRow {
  row_id: string; group: RowGroup; ticker: string; pool: 'core' | 'extended' | 'screen' | string
  chain: RowChain | null; plan: RowPlan | null; baseline: RowBaseline | null
  open_scenarios: OpenScenario[]; void_when: VoidWhen[]; status: RowStatus
  holding: HoldingStatus | null; notes: PlanNote[]
  links: { plan: string | null; instrument: string }
  ended_at?: string | null
}
export interface PlanTableGroups { entry: PlanTableRow[]; holding: PlanTableRow[]; ended: PlanTableRow[] }
export interface PlanTableView {
  session: string; groups: PlanTableGroups; other_units: Unit[]
  counts?: Partial<Record<RowGroup, number>>; errors?: Record<string, string>
}
export interface HistoryLevels { entry_low: string; entry_high: string; stop: string; target1: string }
export type HistoryKind = 'chain_open' | 'version' | 'retrigger' | 'review' | 'fill' | 'chain_end'
export interface HistoryRow {
  kind: HistoryKind; at: string; session: string; chain_signal_date: string | null; version_no?: number | null
  levels?: HistoryLevels | null; validity?: { valid_from: string; valid_until: string } | null
  diff?: { field: string; before: unknown; after: unknown }[] | null
  changes?: ({ field?: string; text: string } | string)[] | null
  reason?: string | null; decided_by?: string | null; signals_fired?: string[] | null
  end?: unknown; text: string
}
export interface HistoryChain {
  signal_date: string; state: string; versions: number; ended_at: string | null; end: unknown
}
export interface PlanHistoryView { ticker: string; rows: HistoryRow[]; chains: HistoryChain[] }
export interface LinkablePlan {
  plan_id: string; label: string; kind?: 'agent' | 'rule' | 'own'; chain_signal_date?: string | null
  version_no?: number | null
}
export interface LinkablePlansView { ticker?: string; plans: LinkablePlan[]; default_plan_id: string | null }
export interface OwnPlanBody extends OwnPlanInput { max_hold_sessions?: number }
/** POST /trades (§6.1): at most one of `plan_id` / `own_plan`; `decision_id` is gone (400 `decision_link_retired`). */
export interface TradeBody {
  request_id: string; ticker: string; side: 'buy' | 'sell'; quantity: string; price: string; fee: string
  account_id?: string
  executed_at: string; note: string | null; plan_id?: string | null; own_plan?: OwnPlanBody | null
}
export interface PlanNoteBody {
  request_id: string; body: string; ticker?: string; chain_signal_date?: string; plan_id?: string
}

// ---- API (spec §9.1) ----

const query = (params: Record<string, string | number | boolean | null | undefined>): string => {
  const parts = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
  return parts.length ? `?${parts.join('&')}` : ''
}

/** GET /plan-table?session=&include_extended= (read-only; `include_extended` is sent only when true). */
export const getPlanTable = (session?: string, includeExtended = false) =>
  request<PlanTableView>(`/api/workbench/plan-table${query({ session, include_extended: includeExtended ? 'true' : undefined })}`)
/** GET /instruments/{ticker}/plan-history?include_reviews=&include_baselines= (each sent only when true). */
export const getPlanHistory = (ticker: string, opts: { include_reviews?: boolean; include_baselines?: boolean } = {}) =>
  request<PlanHistoryView>(`/api/workbench/instruments/${encodeURIComponent(ticker)}/plan-history${query({
    include_reviews: opts.include_reviews ? 'true' : undefined, include_baselines: opts.include_baselines ? 'true' : undefined })}`)
/** GET /holding-status?session= */
export const getHoldingStatus = (session?: string) =>
  request<{ session: string; rows: HoldingStatus[] }>(`/api/workbench/holding-status${query({ session })}`)
/** GET /trades/linkable-plans?ticker=&executed_at= (executed_at is an ISO instant). */
export const getLinkablePlans = (ticker: string, executedAt: string) =>
  request<LinkablePlansView>(`/api/workbench/trades/linkable-plans${query({ ticker, executed_at: executedAt })}`)
/** POST /plan-notes */
export const postPlanNote = (body: PlanNoteBody) =>
  post<{ status: string; record: PlanNote }>('/api/workbench/plan-notes', body)
/** GET /plan-notes?ticker=&chain_signal_date=&plan_id= */
export const getPlanNotes = (q: { ticker?: string; chain_signal_date?: string; plan_id?: string }) =>
  request<{ notes: PlanNote[] }>(`/api/workbench/plan-notes${query(q)}`)

// ---- one-line displays ----

/** Percent points with two decimals and an explicit sign (U+2212 for minus, as the spec's example). */
export function signedPct(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return `${n < 0 ? '−' : '+'}${Math.abs(n).toFixed(2)}%`
}
function signedR(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return `${n < 0 ? '−' : '+'}${Math.abs(n).toFixed(1)}R`
}

/** §7.3 one-line holding status: 「MU 持有 4/10 天 · 距止损 −5.20% · 距 T1 +8.10% · 现 +0.6R · 继续持有：<维持理由>」. */
export function holdingLine(h: HoldingStatus): string {
  if (h.line) return h.line
  const parts: string[] = []
  if (h.instruction === 'no_plan') {
    parts.push(`${h.ticker} 持有 ${h.quantity} 股`)
    if (h.to_stop_pct != null) parts.push(`距止损 ${signedPct(h.to_stop_pct)}`)
  } else {
    parts.push(`${h.ticker} 持有 ${h.held_sessions ?? '—'}/${h.max_hold_sessions ?? '—'} 天`)
    parts.push(`距止损 ${signedPct(h.to_stop_pct)}`)
    parts.push(h.t1_reached || h.to_t1_pct == null ? '已触 T1' : `距 T1 ${signedPct(h.to_t1_pct)}`)
    parts.push(`现 ${signedR(h.r_now)}`)
    if (h.reached_1r) parts.push('已到 1R，时间止损解除')
  }
  const head = parts.join(' · ')
  const tail = h.reason ? `：${h.reason}` : ''
  const verb = INSTRUCTION_TEXT[h.instruction] ?? h.instruction
  return `${head} · ${verb}${tail}`
}

/** §5.4: 「已有活跃链，见 MU·10-29」 for a baseline whose unit joined an active chain; '' when it did not. */
export function activeChainText(row: Pick<PlanTableRow, 'baseline' | 'chain' | 'ticker'>): string {
  const flag = row.baseline?.active_chain
  if (!flag) return ''
  const mmdd = (d: string) => d.slice(5)
  if (typeof flag === 'object') return `已有活跃链，见 ${flag.ticker}·${mmdd(flag.signal_date)}`
  if (row.chain) return `已有活跃链，见 ${row.ticker}·${mmdd(row.chain.signal_date)}`
  return '已有活跃链'
}

/** Row-end execution text: the state, plus what the chain followed when the backend says (R13 「照 Agent 第 n 版」). */
export function rowExecutionText(status: Pick<RowStatus, 'execution' | 'execution_detail' | 'execution_basis'>): string {
  const base = executionText(status.execution, status.execution_detail)
  return base && status.execution_basis ? `${base}（${status.execution_basis}）` : base
}

/** Review snapshots from review-8 on speak of executions, not decisions (D30). */
export const isExecutionReview = (calcVersion: string | null | undefined) => calcVersion === 'review-8'
