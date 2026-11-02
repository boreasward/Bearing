import { request } from './workbench'

export type ResearchStatus = 'queued' | 'running' | 'waiting_data' | 'concluded' | 'closed'
export type FactKind = 'disclosure' | 'guidance' | 'price_fact' | 'user' | 'inference'
export interface ResearchOutput {
  facts: { text: string; kind: FactKind; refs: string[] }[]
  risks: { text: string; refs: string[] }[]
  blocked: { what: string; why: string }[]
  summary: string
}
export interface ResearchPhase {
  status: 'ok' | 'rejected' | 'error' | 'disabled' | 'carried'
  reason: string | null
  output: ResearchOutput | null
  /** only when status is 'carried' (D42/R16): the run that owns the segment's call:N refs, and the status it had there */
  carried_from?: string | null
  original_status?: ResearchPhase['status'] | null
}
/** One catalyst / counter / price-scenario / next-step entry of a dossier conclusion (spec §6.3). */
export interface DossierEntry { text: string; refs: string[] }
/** The independent counter stage's outcome (UI1): `ok` entries may still be empty; the others carry none. */
export type CounterStatus = 'ok' | 'malformed' | 'skipped' | 'tool_rounds_exceeded'
export const COUNTER_STATUS_TEXT: Record<CounterStatus, string> = {
  ok: '反方阶段完成', malformed: '反方输出格式无效，本版没有反证', skipped: '反方阶段因时限未运行，本版没有反证',
  tool_rounds_exceeded: '反方工具调用超限，本版没有反证',
}
export const DOSSIER_ACTION_TEXT: Record<string, string> = { buy: '倾向买入', pass: '不建议买入', wait: '等待数据' }
/** `research_versions.output` of a concluded dossier: the conclusion's lists plus the researcher's checked segment.
 * A dossier never carries thesis / levels / plan fields and opens no chain; a rejected or failed attempt stores none. */
export interface DossierOutput {
  action?: 'buy' | 'pass' | 'wait'; summary?: string
  catalysts?: DossierEntry[]; counter?: DossierEntry[]; counter_status?: CounterStatus
  price_scenarios?: DossierEntry[]; next_steps?: DossierEntry[]
  research?: ResearchPhase | null
}
export const FACT_KIND_TEXT: Record<FactKind, string> = {
  disclosure: '披露事实', guidance: '公司指引', price_fact: '价格事实', user: '用户输入', inference: '推断',
}
export const RESEARCH_PHASE_TEXT: Record<ResearchPhase['status'], string> = {
  ok: '研究完成', rejected: '未通过校验', error: '研究出错', disabled: '研究员已关闭', carried: '沿用研究',
}
export interface ResearchItem {
  id: string; ticker: string; question: string | null; status: ResearchStatus
  latest_summary: string | null; cutoff_at: string | null; tokens: number | null; created_at: string
}
export interface ResearchRequest extends ResearchItem {
  horizon_days?: number | null; source?: 'manual'; attempts?: number; closed_reason?: string | null
}
export interface ResearchVersion {
  version_no: number; status: 'concluded' | 'waiting_data' | 'error'; action: 'buy' | 'pass' | 'wait' | null
  summary: string | null; cutoff_at: string; agent_run_id: string | null
  output: DossierOutput | null
  review_when: unknown; tokens: number | null; elapsed_ms: number | null; created_at: string
}
export interface ResearchDetail {
  request: ResearchRequest; versions: ResearchVersion[]
  chain: { head_plan_id?: string; [key: string]: unknown } | null
}
export interface ResearchInput { ticker: string; question?: string; horizon_days?: number; request_id: string }
export const listResearch = () => request<{ items: ResearchItem[] }>('/api/research')
export const getResearch = (id: string) => request<ResearchDetail>(`/api/research/${encodeURIComponent(id)}`)
export const createResearch = (body: ResearchInput) => request<{ id: string; status: ResearchStatus }>('/api/research', { method: 'POST', body: JSON.stringify(body) })
export const closeResearch = (id: string) => request<unknown>(`/api/research/${encodeURIComponent(id)}/close`, { method: 'POST' })
