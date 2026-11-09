import type { Brief, BriefListItem } from '$lib/home/types'
import { createResource, type Resource } from './resource.svelte'
import type { TodayData, MarketData, RunsData, LedgerData, AccountData, PlansData, ResearchData, RetrospectData, ChatsData } from './types'

export const today = createResource<TodayData>('/api/today')
export const market = createResource<MarketData>('/api/market')
export const runs = createResource<RunsData>('/api/runs')
export const ledger = createResource<LedgerData>('/api/ledger')
const ledgerLimits = new Map<number, Resource<LedgerData>>([[20, ledger]])
export function ledgerForLimit(limit: number): Resource<LedgerData> {
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new RangeError('流水条数须为 1 至 100 的整数')
  if (!ledgerLimits.has(limit)) ledgerLimits.set(limit, createResource<LedgerData>(`/api/ledger?limit=${limit}`))
  return ledgerLimits.get(limit)!
}
export const account = createResource<AccountData>('/api/account')
export const retrospect = createResource<RetrospectData>('/api/retrospect')
const retrospectSelections = new Map<string, Resource<RetrospectData>>([['20', retrospect]])
export function retrospectFor(week: string | null = null, limit = 20): Resource<RetrospectData> {
  if (![20, 50, 100].includes(limit)) throw new RangeError('复盘条数须为 20、50 或 100')
  if (week && !/^\d{4}-\d{2}-\d{2}$/.test(week)) throw new RangeError('请选择有效日期')
  const key = `${limit}${week ? `&week=${week}` : ''}`
  if (!retrospectSelections.has(key)) retrospectSelections.set(key, createResource<RetrospectData>(`/api/retrospect?limit=${key}`))
  return retrospectSelections.get(key)!
}
export const briefs = createResource<{ briefs: BriefListItem[] }>('/api/briefs?limit=30')
const briefSessions = new Map<string, Resource<Brief>>()
export function briefFor(session = 'latest'): Resource<Brief> {
  if (!briefSessions.has(session)) briefSessions.set(session, createResource<Brief>(`/api/briefs/${encodeURIComponent(session)}`))
  return briefSessions.get(session)!
}
export const chats = createResource<ChatsData>('/api/chats')

const planGroups = new Map<string, Resource<PlansData>>()
const researchGroups = new Map<string, Resource<ResearchData>>()
export function plans(group: 'holdings' | 'pending' | 'ended'): Resource<PlansData> {
  if (!planGroups.has(group)) planGroups.set(group, createResource<PlansData>(`/api/plans?group=${group}`))
  return planGroups.get(group)!
}
export function research(group: 'watching' | 'ended'): Resource<ResearchData> {
  if (!researchGroups.has(group)) researchGroups.set(group, createResource<ResearchData>(`/api/research?group=${group}`))
  return researchGroups.get(group)!
}
export function invalidateAll(): void {
  for (const resource of [today, market, runs, ...ledgerLimits.values(), account, ...retrospectSelections.values(), briefs, ...briefSessions.values(), chats, ...planGroups.values(), ...researchGroups.values()]) {
    resource.invalidate()
  }
}
