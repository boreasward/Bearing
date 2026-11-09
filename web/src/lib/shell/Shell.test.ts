import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte'
import { createRawSnippet, tick } from 'svelte'
import Shell from './Shell.svelte'
import { layout, toggleDrawer, type DrawerPane } from './layout.svelte'
import type { ChatListItem } from '$lib/stores/types'

let mediaChange: (() => void) | undefined
function media(narrow: boolean) {
  const query = { matches: narrow, addEventListener: vi.fn((_, fn) => { mediaChange = fn }), removeEventListener: vi.fn() }
  vi.stubGlobal('matchMedia', vi.fn(() => query))
  return query
}
const center = createRawSnippet(() => ({ render: () => '<div>中间区域</div>' }))
const drawerPane = createRawSnippet<[DrawerPane]>((pane) => ({ render: () => `<p>侧览 ${pane()}</p>` }))
const chat = (chat_id: string, last_message_at: string, title = ''): ChatListItem => ({
  chat_id, title, last_message_at, created_at: '2026-01-01T00:00:00Z', read_only: chat_id === 'old', model: '',
})
const props = () => ({ title: '新对话', session: null, counts: { actions: 2, holdings: 3, pending: 4, watching: 5 },
  chats: [chat('today', '2026-11-08T16:30:00Z', '今天的对话'), chat('week', '2026-11-08T16:00:00Z', '本周的对话'), chat('old', '2026-11-01T12:00:00Z')],
  currentChatId: 'today', onNewChat: vi.fn(), onOpenSettings: vi.fn(), center, drawerPane })
beforeEach(() => {
  localStorage.clear(); location.hash = '#account'
  Object.assign(layout, { navCollapsed: false, navSegment: 'quick', drawer: 'dash', narrow: false })
  media(false)
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers() })

describe('workbench Shell', () => {
  it('renders seven real links, counts, current route and injected content', () => {
    render(Shell, props())
    const nav = screen.getByRole('navigation', { name: '快捷入口' })
    expect(within(nav).getAllByRole('link')).toHaveLength(7)
    expect(within(nav).getAllByRole('link').map(a => a.getAttribute('href'))).toEqual(['#today', '#account', '#market', '#plans', '#research', '#retrospect', '#jobs'])
    expect(within(nav).getByRole('link', { name: /账户/ })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByLabelText('今日 2 项')).toHaveTextContent('2')
    expect(screen.getByLabelText('计划与持仓 7 项')).toHaveTextContent('7')
    expect(screen.getByLabelText('研究 5 项')).toHaveTextContent('5')
    expect(screen.getByText('中间区域')).toBeVisible()
    expect(screen.getByText('还没有运行记录')).toBeVisible()
  })
  it('switches segments, groups chats by Beijing dates and supports new/read-only chats', async () => {
    vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(new Date('2026-11-10T04:00:00Z'))
    const input = props()
    input.chats[0].last_message_at = '2026-11-09T16:30:00Z'
    render(Shell, input)
    await fireEvent.click(screen.getByRole('button', { name: '对话' }))
    expect(screen.getByRole('button', { name: '对话' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('今天')).toBeVisible(); expect(screen.getByText('本周')).toBeVisible(); expect(screen.getByText('更早')).toBeVisible()
    expect(screen.getByRole('link', { name: '今天的对话' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByText('只读')).toBeVisible(); expect(screen.getByRole('link', { name: /新对话/ })).toHaveAttribute('href', '#chat/old')
    await fireEvent.click(screen.getByRole('button', { name: '新开对话' }))
    expect(input.onNewChat).toHaveBeenCalledOnce()
  })
  it('collapses to labelled icons and dots and only a new-chat action in chats', async () => {
    render(Shell, props())
    const collapse = screen.getByRole('button', { name: '折叠左栏' })
    await fireEvent.click(collapse)
    expect(collapse).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByText('账户')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '账户' })).toBeVisible()
    expect(screen.getByLabelText('今日 2 项')).not.toHaveTextContent('2')
    await fireEvent.click(screen.getByRole('button', { name: '对话' }))
    expect(screen.getByRole('button', { name: '新开对话' })).toBeVisible()
    expect(screen.queryByText('今天的对话')).not.toBeInTheDocument()
  })
  it('toggles mutually exclusive panels, retains a hidden drawer and ignores desktop Escape', async () => {
    const input = props(); render(Shell, { ...input, session: '2026-11-06' })
    expect(screen.getByText('数据截至 2026-11-06')).toBeVisible()
    const top = screen.getByRole('banner')
    for (const [name, pane] of [['行情', 'market'], ['定时任务', 'jobs'], ['仪表', 'dash']] as const) {
      await fireEvent.click(within(top).getByRole('button', { name }))
      expect(layout.drawer).toBe(pane)
      expect(within(top).getByRole('button', { name })).toHaveAttribute('aria-pressed', 'true')
    }
    await fireEvent.keyDown(window, { key: 'Escape' }); expect(layout.drawer).toBe('dash')
    await fireEvent.click(within(top).getByRole('button', { name: '仪表' }))
    expect(layout.drawer).toBe('closed')
    expect(document.querySelector('aside[aria-label="侧览"]')).not.toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '设置' })); expect(input.onOpenSettings).toHaveBeenCalledOnce()
  })
  it('uses opposite Sheets below 1024, closes the other pane and restores focus', async () => {
    media(true); layout.drawer = 'closed'
    render(Shell, props())
    expect(window.matchMedia).toHaveBeenCalledWith('(max-width: 1023px)')
    const menu = screen.getByRole('button', { name: '打开左栏' }); menu.focus()
    await fireEvent.click(menu)
    const navSheet = await screen.findByRole('dialog', { name: '导航' })
    expect(navSheet).toHaveAttribute('data-side', 'left')
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    toggleDrawer('market'); await tick()
    const drawerSheet = await screen.findByRole('dialog', { name: '行情侧览' })
    expect(drawerSheet).toHaveAttribute('data-side', 'right')
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    expect(screen.queryByRole('dialog', { name: '导航' })).not.toBeInTheDocument()
    await fireEvent.click(menu)
    expect(layout.drawer).toBe('closed')
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    expect(screen.getByRole('dialog', { name: '导航' })).toHaveAttribute('data-side', 'left')
    toggleDrawer('market'); await tick()
    await fireEvent.keyDown(document.activeElement!, { key: 'Escape' })
    await waitFor(() => expect(layout.drawer).toBe('closed'))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '行情侧览' })).not.toBeInTheDocument())
    await fireEvent.click(menu)
    const reopened = await screen.findByRole('dialog', { name: '导航' })
    await fireEvent.click(within(reopened).getByRole('button', { name: '关闭' }))
    await waitFor(() => expect(menu).toHaveFocus())
  })
  it('reacts to hash navigation and viewport changes and cleans up media listener', async () => {
    const query = media(false)
    const result = render(Shell, props())
    location.hash = '#market'; await fireEvent(window, new Event('hashchange'))
    expect(within(screen.getByRole('navigation', { name: '快捷入口' })).getByRole('link', { name: '行情' })).toHaveAttribute('aria-current', 'page')
    query.matches = true; mediaChange?.(); await tick()
    expect(layout.narrow).toBe(true)
    result.unmount(); expect(query.removeEventListener).toHaveBeenCalledWith('change', expect.any(Function))
  })
})
it('dismisses narrow Sheets for chat once, keeps preference, reopens the same pane, and preserves ordinary focus restoration', async () => {
  media(true); localStorage.setItem('ws.drawer','dash')
  const result = render(Shell, props())
  await screen.findByRole('dialog', { name:'仪表侧览' })
  await Promise.all([result.component.dismissForChat(), result.component.dismissForChat()])
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(layout.drawer).toBe('dash')
  expect(localStorage.getItem('ws.drawer')).toBe('dash')
  const button = screen.getByRole('button', { name:'仪表' })
  expect(button).toHaveAttribute('aria-pressed','false')
  button.focus(); await fireEvent.click(button)
  const reopened = await screen.findByRole('dialog', { name:'仪表侧览' })
  expect(button).toHaveAttribute('aria-pressed','true')
  await fireEvent.click(within(reopened).getByRole('button', { name:'关闭' }))
  await waitFor(() => expect(button).toHaveFocus())
  expect(layout.drawer).toBe('closed')
})
it('reopens a different narrow pane after chat dismissal and retains it on desktop', async () => {
  const query = media(true)
  const result = render(Shell, props())
  await screen.findByRole('dialog', { name:'仪表侧览' })
  await result.component.dismissForChat()
  await fireEvent.click(screen.getByRole('button', { name:'行情' }))
  await screen.findByRole('dialog', { name:'行情侧览' })
  const closing = result.component.dismissForChat()
  query.matches = false; mediaChange?.()
  await closing
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name:'行情' })).toHaveAttribute('aria-pressed','true')
  expect(screen.getByRole('complementary', { name:'侧览' })).toBeVisible()
  expect(layout.drawer).toBe('market')
  expect(localStorage.getItem('ws.drawer')).toBe('market')
})
it('chat dismissal resolves for closed Sheets, desktop, and teardown during close', async () => {
  media(true); layout.drawer = 'closed'
  const result = render(Shell, props())
  await tick()
  await result.component.dismissForChat()
  await fireEvent.click(screen.getByRole('button', { name:'打开左栏' }))
  await screen.findByRole('dialog', { name:'导航' })
  const closing = result.component.dismissForChat()
  result.unmount()
  await closing
  media(false); layout.drawer = 'dash'
  const desktop = render(Shell, props())
  await desktop.component.dismissForChat()
  expect(layout.drawer).toBe('dash')
  expect(screen.getByRole('complementary', { name:'侧览' })).toBeVisible()
})
