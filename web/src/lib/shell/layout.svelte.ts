export type DrawerPane = 'dash' | 'market' | 'jobs'
export type NavSegment = 'quick' | 'chats'

function read(key: string): string | null {
  try { return localStorage.getItem(key) } catch { return null }
}
function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch { /* Storage can be disabled; the in-memory preference still works. */ }
}
const savedDrawer = read('ws.drawer')
export const layout = $state<{
  navCollapsed: boolean; navSegment: NavSegment; drawer: DrawerPane | 'closed'; narrow: boolean
}>({
  navCollapsed: read('ws.nav.collapsed') === '1',
  navSegment: read('ws.nav.segment') === 'chats' ? 'chats' : 'quick',
  drawer: savedDrawer === 'market' || savedDrawer === 'jobs' || savedDrawer === 'closed' ? savedDrawer : 'dash',
  narrow: false,
})
export function toggleNav(): void {
  layout.navCollapsed = !layout.navCollapsed
  write('ws.nav.collapsed', layout.navCollapsed ? '1' : null)
}
export function setSegment(segment: NavSegment): void {
  layout.navSegment = segment
  write('ws.nav.segment', segment)
}
export function toggleDrawer(pane: DrawerPane): void {
  layout.drawer = layout.drawer === pane ? 'closed' : pane
  write('ws.drawer', layout.drawer)
}
