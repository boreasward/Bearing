import type * as df from './data-families'
import type { Derivation, UnitDerivation } from './derivation'
import type { Chain, PlanReview, TickerChain, UserInput, UserInputBody } from './timeline'
import type { ConceptCard, ConceptsView, PrinciplesView, ReasoningView, RunOverviewView } from './knowledge'
import type {
  CardVerdictInput, Discussion, DiscussionAttachment, DiscussionContext, DiscussionListItem, KnowledgeDraft,
  KnowledgeDraftInput, KnowledgeEntry, KnowledgeItemView, KnowledgePerf, KnowledgeValues, ReviewCardsView,
  ReviewCardView,
} from './growth'
import { request as workbenchRequest, newRequestId, WorkbenchError } from './workbench'
import type {
  AgentConnectionView, AgentModel, RerunAgentBody, RerunAgentResult, ResumeAccepted, RunDetail,
} from './runs'
export async function get<T>(path: string, options: { errorDetails?: boolean } = {}): Promise<T> {
  const r = await fetch(path, { headers: { Accept: 'application/json' }, cache: 'no-store' })
  if (!r.ok) {
    let message = `HTTP ${r.status}`
    // Opt in per endpoint so existing GET callers keep their error contract.
    if (options.errorDetails) {
      const data = await r.json().catch(() => null)
      const detail = [data?.error, data?.message, data?.detail].find(
        value => typeof value === 'string' && value.trim(),
      )
      if (detail) message = detail
    }
    throw new Error(message)
  }
  return r.json() as Promise<T>
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await r.json()
  if (!r.ok) throw new Error(data?.error ?? `HTTP ${r.status}`)
  return data as T
}

export async function del<T>(path: string): Promise<T> {
  const r = await fetch(path, { method: 'DELETE', headers: { Accept: 'application/json' } })
  const data = await r.json()
  if (!r.ok) throw new Error(data?.error ?? `HTTP ${r.status}`)
  return data as T
}

export async function patch<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: 'PATCH',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await r.json()
  if (!r.ok) throw new Error(data?.error ?? `HTTP ${r.status}`)
  return data as T
}

export async function put<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const data = await r.json()
  if (!r.ok) throw new Error(data?.error ?? `HTTP ${r.status}`)
  return data as T
}

export interface RunRecord {
  run_id: string
  status: 'pending' | 'done' | 'error'
  result?: unknown
  error?: string
}

export async function pollRun(
  runId: string,
  opts: { intervalMs?: number; timeoutMs?: number } = {},
): Promise<RunRecord> {
  const interval = opts.intervalMs ?? 1500
  const deadline = Date.now() + (opts.timeoutMs ?? 120_000)
  for (;;) {
    const rec = await get<RunRecord>(`/api/runs/${runId}`)
    if (rec.status !== 'pending') return rec
    if (Date.now() > deadline) throw new Error('run timeout')
    await new Promise((res) => setTimeout(res, interval))
  }
}

// Convenience wrappers
export const getStatus = () => get<unknown>('/api/status')
export const getRawStatus = () => get<unknown>('/api/raw/status')
export const getHealth = () => get<unknown>('/api/health')
export const getServices = () => get<unknown>('/api/services')
export const getOperations = () => get<unknown>('/api/operations')
export const getWatchlist = (enriched = false) =>
  get<unknown>(`/api/watchlist${enriched ? '?enriched=true' : ''}`)
export const addWatchlistItem = (body: object) => post<unknown>('/api/watchlist', body)
export const deleteWatchlistItem = (ticker: string) => del<unknown>(`/api/watchlist/${ticker}`)
export const getRun = (runId: string) => get<RunRecord>(`/api/runs/${runId}`)

// jobs
export interface SchedulerStatus {
  status: 'up' | 'degraded' | 'down'
  detail: string
  meta: {
    enabled: boolean
    last_tick_at: string | null
    tick_in_progress?: boolean
    tick_started_at?: string | null
    last_tick_failed?: boolean | null
  }
}

export const getScheduledJobs = () =>
  get<{ jobs: any[]; degraded: boolean; scheduler?: SchedulerStatus }>('/api/jobs/scheduled')
export const getJobReports = (task?: string, limit = 50) =>
  get<{ reports: any[]; degraded: boolean }>(
    `/api/jobs/reports?limit=${limit}${task ? `&task=${task}` : ''}`,
  )
export const runJob = (name: string, body: object = {}) =>
  post<{ run_id: string; status: string }>(`/api/jobs/${name}/run`, body)
export const patchScheduledJob = (name: string, body: object) =>
  patch<unknown>(`/api/jobs/scheduled/${name}`, body)

// data families (the Data menu; 503 details opt in so migration notices surface verbatim)
export const getDataFamilies = () =>
  get<df.DataOverview>('/api/data/families', { errorDetails: true })
export const getIndicators = () => get<df.IndicatorList>('/api/data/indicators', { errorDetails: true })
export const getDataFamily = (id: string) =>
  get<df.FamilyDetail>(`/api/data/families/${encodeURIComponent(id)}`, { errorDetails: true })
export const fetchDataFamily = (id: string, ticker?: string) =>
  post<{ run_id: string; status: string }>(
    `/api/data/families/${encodeURIComponent(id)}/fetch`, ticker ? { ticker } : {})
export const browseData = (id: string, params: { ticker?: string; series?: string } = {}) => {
  const q = new URLSearchParams()
  if (params.ticker) q.set('ticker', params.ticker)
  if (params.series) q.set('series', params.series)
  return get<df.BrowseResult>(
    `/api/data/browse/${encodeURIComponent(id)}?${q.toString()}`, { errorDetails: true })
}

// settings
export const getNotifySettings = () => get<any>('/api/settings/notify')
export const patchNotifySettings = (body: object) => patch<unknown>('/api/settings/notify', body)
export const testNotifyChannel = (body: object) =>
  post<{ ok: boolean; error?: string }>('/api/settings/notify/test', body)
export const getEnvStatus = () => get<Record<string, boolean>>('/api/settings/env')

// filings
export interface FilingRecord {
  id: number
  ticker: string
  form: string
  filing_date: string
  accession_number: string
  primary_document: string
  file_path: string
  downloaded_at: string
  created_at: string
}
export const getFilingRecords = (
  params: { ticker?: string; form?: string; from?: string; to?: string; limit?: number } = {},
) => {
  const q = new URLSearchParams()
  if (params.ticker) q.set('ticker', params.ticker)
  if (params.form) q.set('form', params.form)
  if (params.from) q.set('from', params.from)
  if (params.to) q.set('to', params.to)
  q.set('limit', String(params.limit ?? 100))
  return get<{ records: FilingRecord[]; tickers: string[]; degraded: boolean }>(
    `/api/filings/records?${q.toString()}`,
  )
}
export const filingDownloadUrl = (id: number) => `/api/filings/records/${id}/download`

// ticker bars (chart payload shapes reuse these fields)
export interface Bar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}
// US research values preserve explicit null, source versions and accounting basis.
export interface ResearchSource { source_url?:string; source_locator?:string; source_hash?:string; concept?:string; accession?:string; filed?:string; start?:string; end?:string; [key:string]:unknown }
export interface ResearchMetric { value:number|null; unit:string|null; basis:string|null; method:string; input_ids:(number|string)[]; sources:ResearchSource[]; missing_reason:string|null; [key:string]:any }
export interface FiscalRow { period:string; start:string; end:string; days:number; metrics:Record<string,ResearchMetric>; ttm:Record<string,ResearchMetric>; warnings:string[] }
export interface GuidanceRecord { id:number; metric:string; target_start:string; target_end:string; basis:string; unit:string; low:number; high:number; issued_at:string; source_url:string; source_locator:string; note:string; supersedes_id?:number|null; revision?:number; first_seen_at?:string }
export type GuidanceInput = Omit<GuidanceRecord,'id'|'revision'|'first_seen_at'>
export interface GuidanceComparison { guidance_id:number; metric?:string; target_start?:string; target_end?:string; basis?:string; unit?:string; actual:number|null; status:string; reason:string|null; input_ids?:(number|string)[] }
export interface USResearchView {
 security_id:string; ticker:string; currency:string|null; financials:FiscalRow[];
 market:{bars:any[]; benchmarks:Record<string,any[]>; quote:any; performance:{windows:Record<string,any>;basis?:string;limitations?:string[]};limitations:string[]};
 valuation:Record<string,ResearchMetric>; statuses:any[]; template:{id?:string;question:string;value:null;status:string}[];
 card:any; guidance:{records:GuidanceRecord[];comparisons:GuidanceComparison[];consensus:string;status?:string};filings:any[];
 pit:{strict:boolean;as_of?:string|null;limitation?:string;metadata_scope?:string}
}
export const getUSResearch = (ticker:string,asOf?:string) => get<USResearchView>(`/api/us-research/${encodeURIComponent(ticker)}${asOf?'?'+new URLSearchParams({as_of:asOf}):''}`)
export const syncUSResearch = (ticker:string,retry=false) => post<{run_id:string;status:string}>('/api/us-research/sync',{symbols:[ticker],retry_failed:retry})
export const appendUSGuidance = (ticker:string,body:GuidanceInput) => post<{record:GuidanceRecord}>(`/api/us-research/${encodeURIComponent(ticker)}/guidance`,body)

// derivation views (v2.4): 503 details opt in so the backend reason surfaces verbatim
export const getPlanDerivation = (planId: string) =>
  get<Derivation>(`/api/workbench/plans/${encodeURIComponent(planId)}/derivation`, { errorDetails: true })
export const getUnitDerivation = (ticker: string, signalDate: string) =>
  get<UnitDerivation>(
    `/api/workbench/units/${encodeURIComponent(ticker)}/${encodeURIComponent(signalDate)}/derivation`,
    { errorDetails: true })

// plan timeline (v2.5 §12): workbench errors keep the HTTP status (409 / 503 reasons surface verbatim)
const seg = encodeURIComponent
export const getChain = (ticker: string, signalDate: string) =>
  workbenchRequest<Chain>(`/api/workbench/chains/${seg(ticker)}/${seg(signalDate)}`)
export const getTickerChains = (ticker: string) =>
  workbenchRequest<{ ticker: string; chains: TickerChain[] }>(`/api/workbench/chains?ticker=${seg(ticker)}`)
export const getPlanReviews = (session: string) =>
  workbenchRequest<{ session: string; reviews: PlanReview[] }>(`/api/workbench/plan-reviews?session=${seg(session)}`)
export const recheckChain = (ticker: string, signalDate: string) =>
  workbenchRequest<{ run_id: string; status: 'pending' }>(
    `/api/workbench/chains/${seg(ticker)}/${seg(signalDate)}/recheck`, { method: 'POST' })
export const getUserInputs = (ticker: string, signalDate?: string) =>
  workbenchRequest<{ inputs: UserInput[] }>(
    `/api/workbench/user-inputs?ticker=${seg(ticker)}${signalDate ? `&signal_date=${seg(signalDate)}` : ''}`)
export const postUserInput = (body: UserInputBody) =>
  workbenchRequest<{ status: 'created' | 'replayed'; record: UserInput }>(
    '/api/workbench/user-inputs', { method: 'POST', body: JSON.stringify(body) })

// knowledge layer (v2.6): the knowledge endpoints never touch the database; a 503 carries the card problems
export class KnowledgeError extends Error {
  constructor(message: string, public status: number, public problems: string[] = []) { super(message) }
}
async function knowledgeRequest<T>(path: string): Promise<T> {
  const r = await fetch(path, { headers: { Accept: 'application/json' }, cache: 'no-store' })
  const data = await r.json().catch(() => null)
  if (!r.ok) {
    throw new KnowledgeError(
      typeof data?.error === 'string' && data.error ? data.error : `HTTP ${r.status}`, r.status,
      Array.isArray(data?.problems) ? data.problems.map(String) : [])
  }
  return data as T
}
export const getConcepts = () => knowledgeRequest<ConceptsView>('/api/knowledge/concepts')
export const getConcept = (ref: string) => knowledgeRequest<ConceptCard>(`/api/knowledge/concepts/${seg(ref)}`)
export const getPrinciples = () => knowledgeRequest<PrinciplesView>('/api/knowledge/principles')

// run overview and the Agent reasoning view (v2.6 §7, §8): read-only workbench endpoints
export const getRunOverview = (session?: string, runId?: string) => {
  const q = new URLSearchParams()
  if (session) q.set('session', session)
  if (runId) q.set('run_id', runId)
  const query = q.toString()
  return workbenchRequest<RunOverviewView>(`/api/workbench/run-overview${query ? `?${query}` : ''}`)
}
export const getReasoning = (runId: string) =>
  workbenchRequest<ReasoningView>(`/api/workbench/agent-runs/${seg(runId)}/reasoning`)

// review cards, my knowledge and plan discussions (v2.7 §12): workbench errors keep the HTTP status
// (409 / 503 reasons surface verbatim; pages turn them into text with growth.growthError)
const query = (params: Record<string, string | number | undefined>) => {
  const q = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== '') q.set(key, String(value))
  const text = q.toString()
  return text ? `?${text}` : ''
}
const send = <T>(path: string, body?: unknown) =>
  workbenchRequest<T>(path, { method: 'POST', ...(body === undefined ? {} : { body: JSON.stringify(body) }) })

export const getReviewCards = (params: { status?: string; ticker?: string; since?: string } = {}) =>
  workbenchRequest<ReviewCardsView>(`/api/workbench/review-cards${query(params)}`)
export const getReviewCard = (id: number) => workbenchRequest<ReviewCardView>(`/api/workbench/review-cards/${id}`)
export const postCardVerdict = (id: number, body: CardVerdictInput) =>
  send<{ status: string; record?: unknown; lessons?: { ref: string }[] }>(`/api/workbench/review-cards/${id}/verdict`, body)
export const redraftCard = (id: number) =>
  send<{ run_id: string; status: 'pending' }>(`/api/workbench/review-cards/${id}/redraft`)

export const getMyKnowledge = (params: { ticker?: string; concept?: string; status?: string } = {}) =>
  workbenchRequest<{ items: KnowledgeEntry[] }>(`/api/workbench/my-knowledge${query(params)}`)
export const postMyKnowledge = (body: Partial<KnowledgeValues> & { request_id: string }) =>
  send<{ status: string; record: { id: number } }>('/api/workbench/my-knowledge', body)
export const getKnowledgeItem = (id: number, version?: number) =>
  workbenchRequest<KnowledgeItemView>(`/api/workbench/my-knowledge/${id}${query({ version })}`)
export const postKnowledgeVersion = (id: number, body: Partial<KnowledgeValues> & { request_id: string; base_version: number }) =>
  send<{ status: string }>(`/api/workbench/my-knowledge/${id}/versions`, body)
export const postKnowledgeStatus = (id: number, body: { request_id: string; status: 'active' | 'archived'; reason: string }) =>
  send<{ status: string }>(`/api/workbench/my-knowledge/${id}/status`, body)
export const getKnowledgePerformance = (id: number) =>
  workbenchRequest<KnowledgePerf>(`/api/workbench/my-knowledge/${id}/performance`)
export const getKnowledgeDrafts = (status: 'open' | 'all' = 'open') =>
  workbenchRequest<{ drafts: KnowledgeDraft[] }>(`/api/workbench/my-knowledge-drafts${query({ status })}`)
export const postDraftVerdict = (id: number, body: KnowledgeDraftInput) =>
  send<{ status: string }>(`/api/workbench/my-knowledge-drafts/${id}/verdict`, body)

export const postDiscussion = (body: { request_id: string; context: DiscussionContext }) =>
  send<Discussion | { status: string; record: Discussion }>('/api/workbench/discussions', body)
export const getDiscussions = (params: { plan_id?: string; card_id?: number }) =>
  workbenchRequest<{ discussions: DiscussionListItem[] }>(`/api/workbench/discussions${query(params)}`)
export const getDiscussion = (id: number) => workbenchRequest<Discussion>(`/api/workbench/discussions/${id}`)
export const postDiscussionTurn = (id: number, body: { request_id: string; body: string; attachment?: DiscussionAttachment }) =>
  send<{ turn_id: number; run_id: string; status: 'pending' | 'replayed' }>(`/api/workbench/discussions/${id}/turns`, body)

// run detail, resume, agent rerun and the Agent connection (v2.8 §7, §8.9, §11.1)
// Errors keep the HTTP status and a machine code: the backend answers {"error": "<code>"} (a "<code>: <text>" string,
// a top-level "code" or {"error": {"code", "message"}} are accepted too); pages turn the code into Chinese text.
export class ApiError extends WorkbenchError {
  constructor(message: string, status: number, public code: string | null = null) { super(message, status) }
}
async function jsonRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(path, {
    cache: 'no-store', ...init,
    headers: { Accept: 'application/json', ...(init.body ? { 'Content-Type': 'application/json' } : {}) },
  })
  const data = await r.json().catch(() => null)
  if (!r.ok) {
    const raw = data?.error ?? data?.detail ?? data?.message
    let message = `HTTP ${r.status}`
    let code: string | null = typeof data?.code === 'string' ? data.code : null
    if (typeof raw === 'string' && raw) {
      message = raw
      code ??= /^([A-Za-z0-9_.-]+)(?::|$)/.exec(raw)?.[1] ?? null
    } else if (raw && typeof raw === 'object') {
      if (typeof raw.message === 'string' && raw.message) message = raw.message
      if (typeof raw.code === 'string') code ??= raw.code
    }
    throw new ApiError(message, r.status, code)
  }
  return data as T
}
const jsonBody = (method: string, body?: unknown): RequestInit =>
  ({ method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })

export const getRunDetail = (runId: string) => jsonRequest<RunDetail>(`/api/runs/${seg(runId)}/detail`)
export const resumeDaily = (runId: string, node: string, requestId: string = newRequestId()) =>
  jsonRequest<ResumeAccepted>('/api/jobs/daily/resume', jsonBody('POST', { run_id: runId, node, request_id: requestId }))
export const runDaily = (requestId: string = newRequestId()) =>
  jsonRequest<{ run_id: string; daily_run_id?: string; status: string }>(
    '/api/jobs/daily/run', jsonBody('POST', { request_id: requestId }))
export const rerunAgent = (body: RerunAgentBody) =>
  jsonRequest<RerunAgentResult>('/api/workbench/agent-runs/rerun', jsonBody('POST', body))
export const getAgentConnection = () => jsonRequest<AgentConnectionView>('/api/settings/agent-connection')
export const startAgentLogin = (requestId: string = newRequestId()) =>
  jsonRequest<{ authorize_url: string; expires_at: string }>(
    '/api/settings/agent-connection/login', jsonBody('POST', { request_id: requestId }))
export const cancelAgentLogin = () =>
  jsonRequest<{ cancelled: boolean }>('/api/settings/agent-connection/cancel', jsonBody('POST', {}))
export const disconnectAgent = () =>
  jsonRequest<{ revoked: boolean }>('/api/settings/agent-connection', jsonBody('DELETE'))
export const getAgentModels = () =>
  jsonRequest<{ models: AgentModel[] }>('/api/settings/agent-connection/models')
export const setAgentModel = (slug: string, requestId: string = newRequestId()) =>
  jsonRequest<{ model: string; model_display_name: string | null }>(
    '/api/settings/agent-connection/model', jsonBody('PUT', { slug, request_id: requestId }))

// the unit's agent runs since a date (existing endpoint): the plan page looks for a max_attempts / error run
export interface AgentRunRow {
  id: string; ticker: string; signal_date: string; mode: string; status: string; reason: string | null
  attempt?: number; created_at: string
}
export const getAgentRuns = (since: string) =>
  workbenchRequest<{ runs: AgentRunRow[] }>(`/api/workbench/agent-runs${query({ since })}`)
