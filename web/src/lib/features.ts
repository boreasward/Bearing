import { request } from './workbench'
export type FeatureFlag = 'backup_sources' | 'company_researcher' | 'portfolio_manager' | 'optimize_proposals'
export interface FeatureState { flag: FeatureFlag; enabled: boolean; changed_at: string | null; reason: string | null; history_count: number }
export interface FeatureChange { id: number; flag: FeatureFlag; enabled_before: boolean | null; enabled_after: boolean; reason: string; request_id: string; created_at: string }
export interface FeatureView { flags: FeatureState[]; history: FeatureChange[] }
export const FEATURE_NAMES: Record<FeatureFlag, string> = { backup_sources: '备源', company_researcher: '公司研究员', portfolio_manager: '组合经理', optimize_proposals: '优化提案' }
export const getFeatures = () => request<FeatureView>('/api/settings/features')
export const setFeature = (flag: FeatureFlag, body: { enabled: boolean; reason: string; request_id: string }) => request<Pick<FeatureState, 'flag' | 'enabled' | 'changed_at'>>(`/api/settings/features/${flag}`, { method: 'POST', body: JSON.stringify(body) })
