import { NODE_STATUS_TEXT, RETRO_PUSH_TEXT, RETRO_STATUS_TEXT, RUN_STATUS_TEXT } from '$lib/runs'
import { CHAIN_STATE_TEXT } from '$lib/timeline'
import { CARD_STATUS_TEXT, KNOWLEDGE_STATUS_TEXT } from '$lib/growth'
import { CONSISTENCY_TEXT } from '$lib/derivation'
import { EXECUTION_TEXT, INSTRUCTION_TEXT } from '$lib/workbench'
import { ORDER_STATUS_TEXT, RECONCILE_TEXT } from '$lib/accounts'

// The one status vocabulary of the site (spec 4.4): { domain, key } -> { Chinese label, tone }.
// `StatusPill` only knows this table. To add a status: add the key to TONES (one line per domain, the Python guard
// `test_status_vocabulary_complete` parses it by lines), add its label to LABELS, then use it.

export type Tone = 'success' | 'warn' | 'danger' | 'info' | 'running' | 'neutral'
export type StatusDomain =
  | 'node' | 'run' | 'chain' | 'fetch' | 'agent' | 'connection' | 'card' | 'retro' | 'push'
  | 'execution' | 'knowledge' | 'instruction' | 'system' | 'block' | 'consistency'
  | 'account' | 'paper_order' | 'paper_decision' | 'reconcile'

// Keep every domain on ONE line, without spreads: tests/test_frontend_design_guards.py reads this block by lines.
export const TONES = {
  // stage_runs.status (+ pending / running derived by the page) and the run overview's missing / not_recorded stage lines
  node: { success: 'success', partial: 'warn', degraded: 'warn', failed: 'danger', skipped: 'neutral', pending: 'neutral', running: 'running', missing: 'warn', not_recorded: 'neutral' },
  // job_reports.status (success / degraded / error) plus the node values
  run: { success: 'success', partial: 'warn', degraded: 'warn', failed: 'danger', skipped: 'neutral', pending: 'neutral', running: 'running', error: 'danger' },
  // timeline.CHAIN_STATES: only entry / holding / exiting are live; every ended state is neutral
  chain: { entry: 'info', holding: 'success', exiting: 'warn', legacy: 'neutral', invalidated: 'neutral', closed_by_decision: 'neutral', position_closed: 'neutral', superseded: 'neutral', expired: 'neutral' },
  // data_fetch_runs.status (+ running / error in the family cards)
  fetch: { success: 'success', partial: 'warn', failed: 'danger', skipped: 'neutral', running: 'running', error: 'danger' },
  // agent.STATUSES
  agent: { ok: 'success', pass: 'neutral', rejected: 'danger', error: 'danger', skipped: 'neutral' },
  // agent_connections.status (+ no_sharing / unknown reported by the connection view)
  connection: { connected: 'success', pending: 'running', needs_login: 'danger', disconnected: 'neutral', no_sharing: 'warn', unknown: 'neutral' },
  // retro.CARD_STATUSES
  card: { reviewed: 'success', to_review: 'warn', drafting: 'warn', draft_failed: 'danger' },
  // weekly / monthly retro run results (RETRO_STATUS_TEXT)
  retro: { reviewed: 'success', generated: 'success', to_review: 'warn', drafting: 'warn', draft_failed: 'danger', error: 'danger', reused: 'neutral', already_sent: 'neutral', reminded: 'info' },
  // RETRO_PUSH_TEXT (other "not sent" reasons are shown as they come)
  push: { sent: 'success', manual_run: 'neutral', not_sent: 'neutral' },
  // execution.STATUSES | CHAIN_STATUSES | retro.EXECUTION_STATUSES
  execution: { adopted: 'success', executed: 'success', pending: 'info', unplanned: 'warn', out_of_validity: 'neutral', not_executed: 'neutral' },
  // knowledge_status.status
  knowledge: { active: 'success', archived: 'neutral' },
  // timeline.INSTRUCTION_TEXT
  instruction: { hold: 'neutral', sell_half: 'warn', exit: 'danger', no_plan: 'info' },
  // services/health.py check status (up / degraded / down / unknown)
  system: { up: 'success', degraded: 'warn', down: 'danger', unknown: 'neutral' },
  // DataBlock / MetricValue / SampleSize / CutoffTime
  block: { not_computed: 'warn', insufficient: 'warn' },
  // derivation.CONSISTENCY_TEXT (plan derivation check)
  consistency: { consistent: 'success', mismatch: 'danger', incomplete: 'warn' },
  account: { active: 'success', closed: 'neutral' },
  paper_order: { pending: 'info', filled: 'success', voided: 'neutral', expired: 'neutral', cancelled: 'neutral', rejected: 'danger' },
  paper_decision: { ok: 'success', skipped: 'neutral', rejected: 'danger' },
  reconcile: { matched: 'success', mismatch: 'danger', no_ledger: 'neutral' },
} as const satisfies Record<StatusDomain, Record<string, Tone>>

// Existing Chinese tables are referenced, not copied. `execution` is the one exception: EXECUTION_TEXT has no
// `unplanned` (the weekly by_execution key, worded in workbench.ts `executionText`), so the domain is a copy plus it.
// `node` is the same kind of exception: NODE_STATUS_TEXT has no `missing` / `not_recorded` (the run overview's stage lines, wording
// verbatim from workbench/run_overview.py STATUS_TEXT), so the domain is a copy plus them.
// EXECUTION_TEXT.adopted keeps its `{date}` / `{quantity}` placeholders: pass the filled sentence as StatusPill `text`.
export const LABELS: Record<StatusDomain, Record<string, string>> = {
  node: { ...NODE_STATUS_TEXT, missing: '缺证据', not_recorded: '未记录' },
  run: RUN_STATUS_TEXT,
  chain: CHAIN_STATE_TEXT,
  fetch: { success: '成功', partial: '部分成功', failed: '失败', skipped: '跳过', running: '运行中', error: '失败' },
  agent: { ok: '成功', pass: '不建议买入', rejected: '未通过校验', error: '出错', skipped: '跳过' },
  connection: {
    connected: '已连接', pending: '登录中', needs_login: '需要重新登录', disconnected: '未连接', no_sharing: '未开启共享', unknown: '未知',
  },
  card: CARD_STATUS_TEXT,
  retro: RETRO_STATUS_TEXT,
  push: RETRO_PUSH_TEXT,
  execution: { ...EXECUTION_TEXT, unplanned: '计划外成交' },
  knowledge: KNOWLEDGE_STATUS_TEXT,
  instruction: INSTRUCTION_TEXT,
  system: { up: '正常', degraded: '降级', down: '不可用', unknown: '未知' },
  block: { not_computed: '没算', insufficient: '样本不足' },
  consistency: CONSISTENCY_TEXT,
  account: { active: '使用中', closed: '已关闭' },
  paper_order: ORDER_STATUS_TEXT,
  paper_decision: { ok: '已决策', skipped: '跳过', rejected: '未通过校验' },
  reconcile: RECONCILE_TEXT,
}

export type StatusInfo = { label: string; tone: Tone; known: boolean }

/** Unknown keys throw in dev (so a new backend status cannot slip in unnoticed) and are neutral grey with the raw key in production. */
export function statusOf(domain: StatusDomain, key: string): StatusInfo {
  const tones = TONES[domain] as Record<string, Tone>
  if (Object.hasOwn(tones, key)) return { label: LABELS[domain][key] ?? key, tone: tones[key], known: true }
  if (import.meta.env.DEV) throw new Error(`unknown status: ${domain}.${key}`)
  return { label: key, tone: 'neutral', known: false }
}
