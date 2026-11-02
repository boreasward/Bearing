import { request } from './workbench'
export interface RoadmapRecord { id: number; version: number; supersedes: number | null; created_at: string }
export interface RoadmapGoal extends RoadmapRecord { goal_id: string; body: string }
export interface RoadmapVersion extends RoadmapRecord {
  version_id: string; title: string; theme: string | null; scope: string | null; depends_on: string[]
  status: 'planned' | 'in_progress' | 'engineering_done' | 'user_acceptance_pending' | 'done' | 'dropped'
  exit_conditions: string | null; spec_path: string | null; plan_path: string | null; tag: string | null; proposal_id: number | null
}
export interface RoadmapDecision extends RoadmapRecord {
  decision_no: number; decided_on: string; question: string; decision: string; quote: string | null; version_ids: string[]
}
export interface RoadmapEvidence extends RoadmapRecord {
  evidence_id: string; version_id: string; kind: 'manual' | 'scheduled' | 'user_confirmed'
  body: string; observed_on: string | null; source_path: string | null
}
export interface RoadmapProposal {
  id: number; run_id: string | null; agent_run_id: string | null; source: 'data' | 'agent_quality' | 'route'
  title: string; change: string; rationale: string; evidence_refs: string[]; expected: string | null; acceptance: string | null
  evidence_rows: { ref: string; body: Record<string, unknown> }[]
  /** the backend's sentence when the frozen evidence pack is missing or partial; null when every ref has its row */
  evidence_note?: string | null
  status: 'proposed' | 'approved' | 'rejected' | 'superseded'; decided_at: string | null; decision_reason: string | null
  request_id: string | null; created_at: string
}
export interface RoadmapBrief {
  id: number; version_id: string; proposal_id: number; agent_run_id: string | null; body: string | null
  status: 'ok' | 'error'; reason: string | null; created_at: string
}
export interface RoadmapView { goals: RoadmapGoal[]; versions: RoadmapVersion[]; decisions: RoadmapDecision[]; evidence: RoadmapEvidence[]; exported_at: string }
export type BriefStatus = 'not_requested' | 'pending' | 'ok' | 'error'
export const BRIEF_STATUS_TEXT: Record<BriefStatus, string> = { not_requested: '未请求 brief', pending: 'brief 生成中', ok: 'brief 已生成', error: 'brief 生成失败' }
// UI1: history includes the current revision, in ascending version/id order.
export interface VersionDetail { version: RoadmapVersion; history: RoadmapVersion[]; brief: RoadmapBrief | null; brief_status: BriefStatus }
export interface DecisionDetail { decision: RoadmapDecision; history: RoadmapDecision[] }
export const getRoadmap = () => request<RoadmapView>('/api/roadmap')
export const getRoadmapVersion = (id: string) => request<VersionDetail>(`/api/roadmap/versions/${encodeURIComponent(id)}`)
export const getRoadmapDecision = (no: number) => request<DecisionDetail>(`/api/roadmap/decisions/${no}`)
export const listProposals = (status = '') => request<{ items: RoadmapProposal[] }>(`/api/roadmap/proposals?${new URLSearchParams(status ? { status } : {})}`)
export interface ProposalDecision { decision: 'approved' | 'rejected'; version_id?: string; reason: string; request_id: string }
export type ProposalDecisionResult = { status: 'approved'; version_id: string; brief_status: BriefStatus } | { status: 'rejected'; version_id: null; brief_status: null }
export const decideProposal = (id: number, body: ProposalDecision) => request<ProposalDecisionResult>(`/api/roadmap/proposals/${id}/decide`, { method: 'POST', body: JSON.stringify(body) })
export interface BriefRetryResult { version_id: string; brief_status: 'pending'; run_id: string }
export const retryBrief = (id: string) => request<BriefRetryResult>(`/api/roadmap/briefs/${encodeURIComponent(id)}/retry`, { method: 'POST' })
export const nextVersionId = (versions: RoadmapVersion[]) => `v2.${Math.max(0, ...versions.map(v => /^v2\.(\d+)$/.exec(v.version_id)).filter(Boolean).map(m => Number(m![1]))) + 1}`
