/** Account ledger and host-managed paper trading. Monetary values remain API Decimal strings. */
import { request, describeError, WorkbenchError, AGENT_REASON_TEXT } from './workbench'
import type { PortfolioCheckView } from './workbench'

export const REAL_ACCOUNT_ID = '00000000-0000-0000-0000-000000000001'
export type DecimalValue = string | number
export type AccountStrategy = 'manual' | 'agent_managed' | 'follow_plans'
export type OrderStatus = 'pending' | 'filled' | 'voided' | 'expired' | 'cancelled' | 'rejected'
export type DecisionStatus = 'ok' | 'skipped' | 'rejected'
export const STRATEGY_TEXT = {
  manual: '手动', agent_managed: 'Agent 自管', follow_plans: '照计划执行',
} as const
export const ORDER_STATUS_TEXT = {
  pending: '待成交', filled: '已成交', voided: '已作废', expired: '已过期', cancelled: '已撤单', rejected: '未通过校验',
} as const
export const REJECT_TEXT = {
  schema_invalid: '订单格式无效', plan_not_buyable: '计划不可买入', chain_consumed: '计划链已使用',
  duplicate_order: '重复订单', exceeds_max_shares: '超过计划股数上限', max_positions: '达到持仓数量上限',
  insufficient_cash: '现金不足', no_position: '无可卖持仓', cannot_halve: '持仓不足以卖出一半', order_not_pending: '订单已不在待成交状态',
  sector_cap: '超过行业集中度上限', max_per_sector: '超过同行业标的数量上限',
  single_cap: '超过单笔风险或单票敞口上限', total_cap: '超过组合总敞口上限', held_ticker: '已持有、在挂或重复的标的',
} as const
/** Portfolio-check reasons = the order rejection words plus two informational ones (core.texts.PORTFOLIO_REASON_TEXT). */
export const PORTFOLIO_REASON_TEXT = {
  ...REJECT_TEXT, sector_unknown: '行业未归类', deferred_by_cash: '现金不足，暂缓执行',
} as const
export const CANCEL_REASON_TEXT = {
  superseded_by_revision: '计划出了新版本，旧订单撤销', chain_ended: '计划链已结束，订单撤销', agent_cancel: 'Agent 主动撤单',
  validity_expired: '计划有效期已过', account_closed: '账户已关闭，订单撤销',
} as const
export const VOID_REASON_TEXT = {
  gap_below_stop: '开盘不高于止损，计划作废', gap_above_zone: '开盘高于入场区间，计划作废', gap_below_zone: '开盘低于入场区间，计划作废',
} as const
export const CHAIN_DONE_TEXT = {
  entry_voided: '入场作废，计划链结束', exited: '已全部卖出，计划链结束',
} as const
export const EXIT_REASON_TEXT = {
  stop: '止损触及', gap_stop: '跳空止损', target1_half: '到 T1 卖出一半', time_stop: '时间止损',
  agent_sell: 'Agent 卖出', exit_instruction: '执行退出指令',
} as const
export const FILL_CASE_TEXT = {
  in_zone: '区间内成交', gap_up_limit: '高开回落限价成交', gap_down_stop_buy: '低开回升触发成交',
} as const
export const RECONCILE_TEXT = {
  matched: '对账一致', mismatch: '待核对', no_ledger: '暂无账本权益',
} as const

export interface AccountParams {
  risk_pct?: DecimalValue; single_cap?: DecimalValue; total_cap?: DecimalValue; fee_per_trade?: DecimalValue
  max_positions?: number; reconcile_tolerance_pct?: DecimalValue; reconcile_tolerance_abs?: DecimalValue
  sector_cap?: DecimalValue; max_per_sector?: number
}
export interface EquityRow {
  id?: string | number; account_id?: string; session: string; cash: DecimalValue; positions_value: DecimalValue; equity: DecimalValue
  realized_pnl_cum: DecimalValue; unrealized_pnl: DecimalValue; net_flows_cum: DecimalValue; day_pnl: DecimalValue | null
  positions_count: number; detail: Record<string, unknown>; account_version?: string; run_id?: string | null; computed_at: string
}
export interface Account {
  id: string; kind: 'real' | 'paper'; strategy: AccountStrategy; strategy_text?: string; name: string
  opening_cash: DecimalValue | null; opened_on: string | null; opening_session?: string | null; params: AccountParams; status: 'active' | 'closed'
  settled_through: string | null; latest: EquityRow | null; inception_return: DecimalValue | null
  max_drawdown: { pct: DecimalValue; peak_session: string | null; trough_session: string | null } | null; opened: boolean
}
export interface AccountPosition {
  ticker: string; quantity: DecimalValue; avg_cost: DecimalValue; last_close: DecimalValue | null
  market_value: DecimalValue | null; unrealized_pnl: DecimalValue | null; r_now: DecimalValue | null
  stop: DecimalValue | null; stop_mode: string | null; target1: DecimalValue | null
  to_stop_pct: DecimalValue | null; to_t1_pct: DecimalValue | null; held_sessions: number | null; max_hold_sessions: number | null
  instruction: string; reason: string | null
  plan: { plan_id: string; label: string; chain_signal_date: string | null; version_no: number | null } | null
}
export interface AccountTrade {
  id: string; account_id?: string; ticker: string; side: 'buy' | 'sell'; quantity: DecimalValue; price: DecimalValue; fee: DecimalValue
  executed_at: string; note?: string | null; recorded_at?: string; recorded_by?: string
  plan_id?: string | null; plan_label?: string | null; decision_id?: string | null
  voided_at?: string | null; void_reason?: string | null; exit_reason?: string | null
  detail?: { fill_case?: string; exit_reason?: string; [key: string]: unknown }
}
export interface PaperOrder {
  id: string; session?: string; ticker: string; action?: string; side?: string; shares?: DecimalValue | null; quantity?: DecimalValue | null
  portion?: 'all' | 'half' | null; status_reason?: string | null
  plan_id?: string | null; plan_label?: string | null; reason?: string | null; status: OrderStatus
  rejection_code?: string | null; reject_code?: string | null; reject_reason?: string | null
  host_check?: string | Record<string, unknown> | null; result_text?: string | null; detail?: Record<string, unknown>
  filled_at?: string | null; filled_price?: DecimalValue | null; filled_quantity?: DecimalValue | null
}
export interface PaperDecision {
  id: string | number; session: string; status: DecisionStatus; status_reason: string | null; summary: string | Record<string, unknown> | null
  orders_placed?: number; orders_rejected?: number; evidence?: Record<string, unknown> | null
  output?: Record<string, unknown> | null; holds?: unknown[] | null; orders?: PaperOrder[]
  agent_version?: string | null
}
export interface Reconciliation {
  status: keyof typeof RECONCILE_TEXT; session?: string; as_of?: string; ledger_equity?: DecimalValue | null
  snapshot_equity?: DecimalValue | null; difference?: DecimalValue | null; diff?: DecimalValue | null; note?: string | null
  [key: string]: unknown
}
export interface ClosedPosition {
  ticker: string; opened?: string; closed?: string; quantity?: DecimalValue; realized_pnl?: DecimalValue
  holding_sessions?: number; return?: number | null; spy_return?: number | null; max_favorable?: number | null; max_adverse?: number | null
}
export interface AccountDetail {
  account: Account; equity: EquityRow | null; positions: AccountPosition[]; pending_orders: PaperOrder[]
  decision: PaperDecision | null; reconciliation: Reconciliation[] | null; forms: { can_record_trades: boolean }
  closed_positions?: ClosedPosition[]; cutoff_at?: string | null; calc_versions?: string[] | Record<string, string>
}
export interface AccountMarker { session: string; kind: 'feature' | 'version' | 'params'; label: string }
export interface EquityHistory { rows: EquityRow[]; spy: { session: string; close: DecimalValue }[]; markers?: AccountMarker[] }
export interface CompareValue { text: string | null; value: unknown; note?: string | null }
export interface CompareView {
  columns: { account_id: string; name: string; strategy: AccountStrategy }[]
  rows: { key: string; label: string; values: CompareValue[]; spy: string | null }[]
  cutoff_at: string | null; calc_versions: string[] | Record<string, string>
  markers?: AccountMarker[]
}
export interface CreateAccountBody {
  request_id: string; name: string; strategy: Exclude<AccountStrategy, 'manual'>; opening_cash: string
  opened_on?: string; params?: AccountParams
}
export interface OpeningBody {
  request_id: string; opening_cash: string; opened_on: string
  opening_positions?: { ticker: string; quantity: string; cost_per_share: string }[]
}
export interface ChangeParamsBody { request_id: string; params: AccountParams; reason: string }

const query = (params: Record<string, string | boolean | undefined>) => {
  const values = Object.entries(params).filter(([, v]) => v !== undefined && v !== '')
  return values.length ? `?${values.map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')}` : ''
}
const path = (id: string) => `/api/accounts/${encodeURIComponent(id)}`
const post = <T>(url: string, body: unknown) => request<T>(url, { method: 'POST', body: JSON.stringify(body) })
export const listAccounts = (includeClosed = false) => request<{ accounts: Account[] }>(`/api/accounts${query({ include_closed: includeClosed || undefined })}`)
export const createAccount = (body: CreateAccountBody) => post<{ status: 'created' | 'existing'; account: Account }>('/api/accounts', body)
export const openAccount = (id: string, body: OpeningBody) => post<{ account: Account; trades: AccountTrade[] }>(`${path(id)}/opening`, body)
export const changeParams = (id: string, body: ChangeParamsBody) => post<{ account: Account; change: unknown }>(`${path(id)}/params`, body)
export const closeAccount = (id: string, reason: string) => post<Account>(`${path(id)}/close`, { reason })
export const getAccount = (id: string) => request<AccountDetail>(path(id))
export const getEquity = (id: string, from?: string, to?: string) => request<EquityHistory>(`${path(id)}/equity${query({ from, to })}`)
export const getAccountTrades = (id: string) => request<{ trades: AccountTrade[] }>(`${path(id)}/trades`)
export const getOrders = (id: string, status?: OrderStatus) => request<{ orders: PaperOrder[] }>(`${path(id)}/orders${query({ status })}`)
export const getDecisions = (id: string) => request<{ decisions: PaperDecision[] }>(`${path(id)}/decisions`)
export const getDecision = (id: string, session: string) => request<{ decision: PaperDecision | null }>(`${path(id)}/decisions${query({ session })}`)
export const getCompare = (from?: string) => request<CompareView>(`/api/accounts/compare${query({ from })}`)
// UI1 uses the same required envelope as Today, including explicit not_computed/null results.
export const getPortfolioCheck = (id: string, session: string) => request<PortfolioCheckView>(`${path(id)}/portfolio-check${query({ session })}`)

export const MARKER_KIND_TEXT = { feature: '功能开关', version: '版本', params: '参数' } as const
export function accountMarkLine(markers: AccountMarker[] = []) {
  const styles = { feature: 'dashed', version: 'solid', params: 'dotted' } as const
  return { silent: true, symbol: 'none' as const, label: { formatter: '{b}' }, data: markers.map(m => ({ name: m.label, xAxis: m.session, lineStyle: { type: styles[m.kind] } })) }
}

const ERRORS: Record<string, string> = {
  migration_030_missing: '迁移 030 未执行，账户账本暂不可用', already_opened: '该账户已开账，期初值不能修改',
  opened_on_after_records: '开账日不能晚于已有成交或出入金日期', paper_account_is_host_managed: '模拟账户由宿主管理，不能手动录入成交',
  record_before_opened_on: '成交或出入金日期不能早于开账日',
  flows_only_for_real_account: '仅真实账户可以录入出入金', cannot_close_real_account: '真实账户不能关闭',
}
export function describeAccountError(error: unknown): string {
  if (error instanceof WorkbenchError) {
    const code = error.message.split(/[:：]/)[0]
    if (ERRORS[code]) return ERRORS[code]
  }
  return describeError(error)
}
export const decimalText = (value: DecimalValue | null | undefined): string => value == null || value === '' ? '—' : String(value)
// Decision skip reasons that only the account node writes (the shared agent reason table does not have them).
const DECISION_REASON_TEXT: Record<string, string> = { host_error: '宿主处理出错' }
export function decisionReasonText(reason: string | null | undefined): string {
  if (!reason) return '未提供原因'
  return (REJECT_TEXT as Record<string, string>)[reason] ?? DECISION_REASON_TEXT[reason] ?? AGENT_REASON_TEXT[reason] ?? reason
}
/** The reason shown next to an order outcome; the status picks the table, an unknown code stays visible. */
export function orderReasonText(status: OrderStatus, reason: string | null | undefined): string {
  if (!reason) return ''
  const table: Record<string, string> = status === 'rejected' ? REJECT_TEXT : status === 'voided' ? VOID_REASON_TEXT : CANCEL_REASON_TEXT
  return table[reason] ?? reason
}
type EquityPoint = { session: string; equity: DecimalValue; detail?: Record<string, unknown> }
export interface EquityAssessment {
  data: [string, number | null][]
  issues: { session: string; reason: string }[]
  baselineWarning: string | null
}
/** Incomplete prices never become chart values. A later complete day cannot silently replace the opening baseline. */
export function assessEquityHistory(rows: EquityPoint[], openingSession?: string | null): EquityAssessment {
  const ordered = [...rows].sort((a, b) => a.session.localeCompare(b.session))
  const reasons = ordered.map(row => valuationWarning(row.detail) || (row.equity == null || String(row.equity).trim() === '' || !Number.isFinite(Number(row.equity)) ? '权益数值不可用' : ''))
  const issues = ordered.flatMap((row, index) => reasons[index] ? [{ session: row.session, reason: reasons[index] }] : [])
  const first = ordered[0]
  if (!first) return { data: [], issues, baselineWarning: null }
  let baselineWarning: string | null = null
  if (openingSession === null) baselineWarning = '缺少首个交易日，未绘制归一化账户曲线'
  else if (openingSession && first.session !== openingSession) baselineWarning = `缺少首个交易日 ${openingSession} 的完整权益，未绘制归一化账户曲线`
  else if (reasons[0]) baselineWarning = `首个交易日 ${first.session} 估值不完整，未绘制归一化账户曲线`
  else if (!(Number(first.equity) > 0)) baselineWarning = `首个交易日 ${first.session} 权益不可用于归一化，未绘制账户曲线`
  if (baselineWarning) return { data: [], issues, baselineWarning }
  const base = Number(first.equity)
  return { data: ordered.map<[string, number | null]>((row, index) => [row.session, reasons[index] ? null : Number((Number(row.equity) / base * 100).toFixed(10))]), issues, baselineWarning }
}
export const normalizedEquity = (rows: EquityPoint[]): [string, number | null][] => assessEquityHistory(rows).data
/** SPY is shared market data: combine the valid dates from every account, without selecting a privileged account. */
export function mergeSpyHistory(histories: Pick<EquityHistory, 'spy'>[]): EquityHistory['spy'] {
  const points = new Map<string, EquityHistory['spy'][number]>()
  for (const history of histories) for (const point of history.spy ?? []) {
    if (!Number.isFinite(Number(point.close)) || !(Number(point.close) > 0) || points.has(point.session)) continue
    points.set(point.session, point)
  }
  return [...points.values()].sort((a, b) => a.session.localeCompare(b.session))
}
export function valuationWarning(detail: Record<string, unknown> | null | undefined): string {
  const missing = Array.isArray(detail?.bars_missing) ? detail.bars_missing : []
  const stale = detail?.stale && typeof detail.stale === 'object' ? Object.entries(detail.stale).map(([ticker, date]) => `${ticker}（${String(date)}）`) : []
  if (!missing.length && !stale.length) return ''
  return `估值不完整：${missing.length ? `缺当日收盘 ${missing.join('、')}` : ''}${missing.length && stale.length ? '；' : ''}${stale.length ? `沿用旧收盘 ${stale.join('、')}` : ''}`
}
export function parameterSummary(params: AccountParams): string {
  const pct = (v: DecimalValue | undefined) => v == null ? '—' : `${Number(v) * 100}%`
  return `风险 ${pct(params.risk_pct)} · 单票 ${pct(params.single_cap)} · 最多 ${params.max_positions ?? '—'} 只`
}
