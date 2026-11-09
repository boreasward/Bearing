import { createResource, type Resource } from './resource.svelte'
import type { TodayData, MarketData, RunsData, LedgerData, AccountData, PlansData, ResearchData, RetrospectData, ChatsData } from './types'

export const today = createResource<TodayData>('/api/today')
export const market = createResource<MarketData>('/api/market')
export const runs = createResource<RunsData>('/api/runs')
export const ledger = createResource<LedgerData>('/api/ledger')
export const account = createResource<AccountData>('/api/account')
export const retrospect = createResource<RetrospectData>('/api/retrospect')
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
  for (const resource of [today, market, runs, ledger, account, retrospect, chats, ...planGroups.values(), ...researchGroups.values()]) {
    resource.invalidate()
  }
}
