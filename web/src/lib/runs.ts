/** v2.8 run detail page: response types, Chinese labels and small pure helpers (spec §7, §8.9, §12). */
import type { DegradationItem, FailureItem, SkipItem } from './knowledge'

/** `run_id` of a job report / daily run, e.g. daily-20261030033000-1a2b3c4d (same pattern as the backend). */
export const RUN_ID_RE = /^[a-z_]+-\d{14}-[0-9a-f]{8}$/

/** Resume: re-read the detail every 5 seconds, for at most 60 minutes. */
export const RESUME_POLL_MS = 5000
export const RESUME_POLL_MAX_MS = 3_600_000
/** Login: re-read the connection state every 2 seconds, for at most 10 minutes (LOGIN_TTL_S = 600). */
export const LOGIN_POLL_MS = 2000
export const LOGIN_POLL_MAX_MS = 600_000

export type NodeStatus = 'success' | 'degraded' | 'partial' | 'failed' | 'skipped' | 'pending' | 'running'

export const NODE_STATUS_TEXT: Record<string, string> = {
  success: '成功', degraded: '降级', partial: '部分成功', failed: '失败', skipped: '跳过', pending: '未开始', running: '运行中',
}

/** Status of a whole run / one execution (job_reports.status plus the derived `running`). */
export const RUN_STATUS_TEXT: Record<string, string> = {
  ...NODE_STATUS_TEXT, error: '失败',
}

export const TRIGGER_TEXT: Record<string, string> = {
  scheduled: '定时', manual: '手动', manual_batch: '手动批处理', resume: '续跑',
}

/** 409 reasons of POST /api/jobs/daily/resume (spec §5.4, verbatim; `<next>` and `<下一交易日>` are filled in by the page). */
export const RESUME_ERROR_TEXT: Record<string, string> = {
  not_failed: '这次运行没有失败的节点',
  node_mismatch: '只能从失败的节点 <next> 继续',
  resume_window_closed: '已过 <下一交易日> 纽约 17:00，只能整次重新运行',
  graph_version_changed: '这次运行由旧版流程图产生，不能续跑，只能整次重新运行',
  daily_running: '每日工作流正在运行',
  // GET /api/runs/{id}/detail 的 resume.reason 另有三个（不是 409 码）
  interrupted: '这次运行中途中断，只能整次重新运行',
  stage_failed: '这一步记了失败但链已跑完，不能续跑；需要时整次重新运行',
  checkpoint_unavailable: '状态库未就绪，暂时无法续跑',
}

/** `retro` 节点结果行的状态（复盘卡的状态与周 / 月复盘的动作，后端发的是代码）。 */
export const RETRO_STATUS_TEXT: Record<string, string> = {
  to_review: '待审定', drafting: '起草中', draft_failed: '起草失败', reviewed: '已审定',
  generated: '已生成', reused: '沿用已冻结快照', already_sent: '已推送过', reminded: '已提醒', error: '出错',
}

/** 周 / 月复盘的推送结果；其余（未发送的原因）原样显示。 */
export const RETRO_PUSH_TEXT: Record<string, string> = {
  sent: '已推送', manual_run: '手动运行不推送', not_sent: '未发送',
}

export const RESUME_STILL_RUNNING_TEXT = '还在运行，稍后刷新'

// ---- run detail (GET /api/runs/{run_id}/detail, spec §7.1) ----

export interface RunExecution {
  report_id: number; trigger_source: string; status: string
  started_at: string | null; finished_at: string | null; resume_node: string | null; attempt: number
}
export interface ResumeInfo { allowed: boolean; node: string | null; deadline_at: string | null; reason: string | null }
export interface NodeAttempt {
  attempt: number; status: string; started_at: string | null; finished_at: string | null; duration_ms: number | null
}
export interface FetchTarget {
  target: string; ok: boolean; attempts: number | null; error_kind: string | null; http_status: number | null
  reason: string | null; rows: number | null; latest: string | null; elapsed_ms: number | null
}
export interface FetchFamily {
  family: string; title: string; status: string; target_count: number; ok_count: number
  started_at: string | null; finished_at: string | null; scope: string | null
  /** absent on rows older than v2.8 (the page then lists `failures` only) */
  targets?: FetchTarget[]
  failures: FailureItem[]; legacy: boolean
}
export interface NodeResults { kind: string; items: any[]; total: number; truncated: boolean }
export interface DataRequestCounts { queued: number; fetched: number; failed: number; deferred: number; reruns: number }
export interface RunNodeDid {
  sentence: string | null; input_count: number | null; output_count: number | null
  skips: SkipItem[]; failures: FailureItem[]; degradations: DegradationItem[]
  retry: { first_error: string; waited_s: number } | null
  timings?: Record<string, unknown>
  // Optional only for older stages; UI1 fixes the shapes when present.
  data_requests?: DataRequestCounts
  research_requests?: number
  /** agent node only: research requests closed automatically (30 days without a new version) in this run */
  research_auto_closed?: number
}
export interface PortfolioNodeItem { account_id: string; name: string; ok: number; blocked: number; deferred: number; commentary_status: string | null }
export interface ProposalNodeItem { id: number; title: string; source: 'data' | 'agent_quality' | 'route'; status: 'proposed' | 'approved' | 'rejected' | 'superseded' }
export interface RunNode {
  id: string; title: string; purpose?: string; concept?: string | null
  status: string; reason: string | null; reason_text: string | null
  attempt: number; attempts: NodeAttempt[]
  started_at: string | null; finished_at: string | null; duration_ms: number | null
  did?: RunNodeDid | null
  fetch?: { families: FetchFamily[] } | null
  results?: NodeResults | null
  actions?: { resume: boolean; rerun_agent?: unknown[] }
}
export interface RunDetail {
  run_id: string; task: string; kind: 'daily' | 'batch' | 'other'
  graph_version: string | null; stages_version: string | null
  session: string | null; cutoff_at: string | null; trigger: string | null
  status: string; headline: string
  executions: RunExecution[]; resume: ResumeInfo | null
  nodes: RunNode[]; raw_summary: unknown
}
export interface ResumeAccepted { run_id: string; node: string; status: string; attempt: number; runner_id: string }
export interface RerunAgentBody {
  ticker: string; signal_date: string; mode: 'propose' | 'interpret'; request_id: string; origin: 'run_detail' | 'plan_page'
}
export interface RerunAgentResult { reset_id: number | string; run_id: string | null; replayed: boolean }

// ---- Agent connection (GET /api/settings/agent-connection, spec §8.9) ----

export type ConnectionStatus = 'connected' | 'needs_login' | 'disconnected' | 'pending' | 'no_sharing' | 'unknown'
export interface AgentConnectionView {
  status: ConnectionStatus
  email_masked: string | null
  scopes_ok: boolean | null
  access_expires_at: string | null
  refresh_expires_at: string | null
  last_refresh_at: string | null
  last_error: string | null
  model: string | null
  model_display_name: string | null
  provider_setting: string | null
  pending: { expires_at: string } | null
}
export interface AgentModel { slug: string; display_name: string }

// ---- helpers ----

/** Link to the run detail page. */
export const runHref = (runId: string) => `#tools/runs/${runId}`

/** Failed targets first; the order inside each group is kept. */
export function failedTargetsFirst<T extends { ok: boolean }>(targets: T[]): T[] {
  return [...targets.filter(t => !t.ok), ...targets.filter(t => t.ok)]
}

/** a***@example.com — the backend already masks the address; used for test data shape only. */
export function maskEmail(email: string): string {
  const at = email.indexOf('@')
  if (at < 1) return '***'
  return `${email[0]}***${email.slice(at)}`
}

/** 1234 → 1.2 秒; 65000 → 1 分 5 秒; null → ''. */
export function durationText(ms: number | null | undefined): string {
  if (ms == null) return ''
  if (ms < 1000) return `${ms} 毫秒`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`
  return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`
}

/** Fill the placeholders of a RESUME_ERROR_TEXT entry. */
export function resumeErrorText(code: string, ctx: { next?: string | null; day?: string | null } = {}): string | null {
  const template = RESUME_ERROR_TEXT[code]
  if (!template) return null
  return template.replace('<next>', ctx.next ?? '—').replace('<下一交易日>', ctx.day ?? '—')
}

/** The New York calendar day of an instant (the resume deadline is that day, 17:00). */
export function nyDay(iso: string | null | undefined): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(d)
  const get = (t: string) => parts.find(p => p.type === t)?.value ?? ''
  return `${get('year')}-${get('month')}-${get('day')}`
}
