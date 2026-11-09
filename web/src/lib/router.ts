export type ViewName = 'today' | 'account' | 'market' | 'plans' | 'research' | 'retrospect' | 'jobs'
export type Route =
  | { kind: 'chat'; chatId: string | null }
  | { kind: 'view'; view: ViewName; id: number | null }
  | { kind: 'gallery' }

const views = new Set<string>(['today', 'account', 'market', 'plans', 'research', 'retrospect', 'jobs'])

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#/, '').split('?')[0]
  const home: Route = { kind: 'chat', chatId: null }
  if (path === '_ui') return { kind: 'gallery' }
  const [name, value, extra] = path.split('/')
  if (extra !== undefined) return home
  if (name === 'chat' && value) {
    try { return { kind: 'chat', chatId: decodeURIComponent(value) } } catch { return home }
  }
  if (!views.has(name)) return home
  if (value === undefined) return { kind: 'view', view: name as ViewName, id: null }
  if ((name === 'plans' || name === 'research') && /^[1-9]\d*$/.test(value) && Number.isSafeInteger(Number(value))) {
    return { kind: 'view', view: name, id: Number(value) }
  }
  return home
}

export function hrefOf(route: Route): string {
  if (route.kind === 'gallery') return '#_ui'
  if (route.kind === 'chat') return route.chatId ? `#chat/${encodeURIComponent(route.chatId)}` : '#'
  return `#${route.view}${route.id === null ? '' : `/${route.id}`}`
}
