import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'

beforeEach(() => { localStorage.clear(); vi.resetModules() })
afterEach(() => vi.restoreAllMocks())

describe('workbench layout state', () => {
  it('defaults to expanded quick entries and the dashboard', async () => {
    const { layout } = await import('./layout.svelte')
    expect({ ...layout }).toEqual({ navCollapsed: false, navSegment: 'quick', drawer: 'dash', narrow: false })
  })
  it('toggles drawers mutually and persists exact keys', async () => {
    const { layout, toggleDrawer, toggleNav, setSegment } = await import('./layout.svelte')
    toggleDrawer('market'); expect(layout.drawer).toBe('market')
    toggleDrawer('market'); expect(layout.drawer).toBe('closed')
    toggleNav(); setSegment('chats')
    expect(localStorage.getItem('ws.drawer')).toBe('closed')
    expect(localStorage.getItem('ws.nav.collapsed')).toBe('1')
    expect(localStorage.getItem('ws.nav.segment')).toBe('chats')
    toggleNav(); expect(localStorage.getItem('ws.nav.collapsed')).toBeNull()
  })
  it('restores preferences and ignores invalid values', async () => {
    localStorage.setItem('ws.nav.collapsed', '1'); localStorage.setItem('ws.nav.segment', 'chats'); localStorage.setItem('ws.drawer', 'jobs')
    const { layout } = await import('./layout.svelte')
    expect(layout.navCollapsed).toBe(true); expect(layout.navSegment).toBe('chats'); expect(layout.drawer).toBe('jobs')
    vi.resetModules(); localStorage.setItem('ws.nav.segment', 'invalid'); localStorage.setItem('ws.drawer', 'invalid')
    const fresh = await import('./layout.svelte')
    expect(fresh.layout.navSegment).toBe('quick'); expect(fresh.layout.drawer).toBe('dash')
  })
  it('survives denied storage reads and writes', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('denied') })
    const { layout, toggleNav, toggleDrawer, setSegment } = await import('./layout.svelte')
    expect(() => { toggleNav(); setSegment('chats'); toggleDrawer('market'); toggleNav() }).not.toThrow()
    expect(layout.drawer).toBe('market'); expect(layout.navSegment).toBe('chats')
  })
})
