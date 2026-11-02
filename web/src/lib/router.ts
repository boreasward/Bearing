import { RUN_ID_RE } from './runs'
import { REAL_ACCOUNT_ID } from './accounts'
export type Zone = 'today' | 'instrument' | 'account' | 'review' | 'data' | 'knowledge' | 'tools' | 'settings' | 'plan' | 'discuss' | 'research' | 'roadmap'
export const workbenchZones: Zone[] = ['today', 'instrument', 'account', 'review']
export const zones: Zone[] = [...workbenchZones, 'data', 'knowledge', 'tools', 'settings', 'plan', 'discuss', 'research', 'roadmap']

// Zones whose pages have a path of their own below the first segment: #review/cards/12, #knowledge/mine/3@2.
const nestedZones: Zone[] = ['review', 'knowledge', 'account', 'research', 'roadmap']

export function parseHash(hash: string): { zone: Zone; sub: string | undefined } {
  const [rawZone, ...rest] = hash.replace(/^#/, '').split('?')[0].split('/')
  if (rawZone === 'review' && (rest[0] === 'trades' || rest[0] === 'account'))
    return { zone: 'account', sub: REAL_ACCOUNT_ID + (rest[0] === 'trades' ? '/trades' : '') }
  const known = zones.includes(rawZone as Zone)
  const rawSub = nestedZones.includes(rawZone as Zone) ? rest.join('/') : rest[0]
  // Legacy zones (technicals/strategy/trade) land on today with no sub; so do #plan and #discuss without an id.
  if (!known || ((rawZone === 'plan' || rawZone === 'discuss') && !rawSub)) return { zone: 'today', sub: undefined }
  // #discuss/<id> takes a number only: #discuss/abc would ask for /discussions/NaN.
  if (rawZone === 'discuss' && !/^\d+$/.test(rawSub!)) return { zone: 'today', sub: undefined }
  // #tools/runs/<run_id> is the run detail page; an id of another shape falls back to the run list.
  if (rawZone === 'tools' && rawSub === 'runs' && rest[1] !== undefined)
    return { zone: 'tools', sub: RUN_ID_RE.test(rest[1]) ? `runs/${rest[1]}` : 'runs' }
  return { zone: rawZone as Zone, sub: rawSub || undefined }
}
