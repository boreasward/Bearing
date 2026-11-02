import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SideNav from './SideNav.svelte'

const KEY = 'ui.sidenav.collapsed'
beforeEach(() => { try { localStorage.removeItem(KEY) } catch { /* */ } })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('SideNav', () => {
  it('links research, roadmap children and feature settings', async () => {
    render(SideNav, { current: 'roadmap', sub: 'proposals/12' })
    expect(screen.getByRole('link', { name: '研究' })).toHaveAttribute('href', '#research')
    expect(await screen.findByRole('link', { name: '提案' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: '决策' })).toHaveAttribute('href', '#roadmap/decisions')
    cleanup()
    render(SideNav, { current: 'settings', sub: 'features' })
    expect(await screen.findByRole('link', { name: '功能开关' })).toHaveAttribute('aria-current', 'page')
  })
  it('highlights 运行记录 for the list and for a run detail (#tools/runs/<id>)', async () => {
    render(SideNav, { current: 'tools', sub: 'runs/daily-20261030033000-1a2b3c4d' })
    const runs = await screen.findByRole('link', { name: '运行记录' })
    expect(runs.className).toContain('text-primary')
    expect(screen.getByRole('link', { name: '任务中心' }).className).not.toContain('text-primary')
    cleanup()
    render(SideNav, { current: 'tools', sub: 'runs' })
    expect((await screen.findByRole('link', { name: '运行记录' })).className).toContain('text-primary')
    cleanup()
    render(SideNav, { current: 'tools', sub: 'tasks' })
    expect((await screen.findByRole('link', { name: '运行记录' })).className).not.toContain('text-primary')
  })

  it('the current page is aria-current and uses the primary colour; the others do not', async () => {
    render(SideNav, { current: 'today' })
    const today = await screen.findByRole('link', { name: '今日' })
    expect(today).toHaveAttribute('aria-current', 'page')
    expect(today.className).toContain('text-primary')
    const instrument = screen.getByRole('link', { name: '标的' })
    expect(instrument).not.toHaveAttribute('aria-current')
    expect(instrument.className).not.toContain('text-primary')
  })

  it('a sub-menu is a disclosure button wired with aria-expanded; its current child is aria-current', async () => {
    render(SideNav, { current: 'knowledge', sub: 'principles' })
    const group = await screen.findByRole('button', { name: /知识/ })
    expect(group).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('link', { name: '原理' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: '概念' })).not.toHaveAttribute('aria-current')
    const review = screen.getByRole('button', { name: /复盘/ })
    expect(review).toHaveAttribute('aria-expanded', 'false')
    await fireEvent.click(review)
    expect(review).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('link', { name: '周度复盘' })).toHaveAttribute('href', '#review/weekly')
  })

  it('no emoji: the navigation icons are SVGs hidden from assistive technology', async () => {
    const { container } = render(SideNav, { current: 'today' })
    await screen.findByRole('link', { name: '今日' })
    expect(container.textContent).not.toMatch(/[\u{1F000}-\u{1FAFF}\u2600-\u27BF\u2300-\u23FF\u2B00-\u2BFF\uFE0F]/u)
    const icons = container.querySelectorAll('svg')
    expect(icons.length).toBeGreaterThan(5)
    for (const icon of icons) expect(icon).toHaveAttribute('aria-hidden', 'true')
  })

  it('the Tab order walks the controls: the collapse button, then the entries', async () => {
    render(SideNav, { current: 'today' })
    const collapse = await screen.findByRole('button', { name: '收起菜单' })
    const entries = [...document.querySelectorAll('nav a, nav button')]
    expect(entries[0]).toBe(collapse)
    expect(entries.every(el => el.getAttribute('tabindex') !== '-1')).toBe(true)
  })

  it('collapsed shows icons with tooltip labels: only the icons remain, the label is the accessible name and the tooltip', async () => {
    localStorage.setItem(KEY, '1')
    render(SideNav, { current: 'today' })
    const today = await screen.findByRole('link', { name: '今日' })
    expect(today).toHaveTextContent('')                                 // no visible label
    expect(today.querySelector('svg')).not.toBeNull()
    expect(screen.getByRole('button', { name: '展开菜单' })).toBeInTheDocument()
    await fireEvent.focus(today)
    await waitFor(() => expect(document.querySelector('[data-slot="tooltip-content"]')).toHaveTextContent('今日'))
    expect(today).toHaveAttribute('aria-describedby')                  // the tooltip describes the icon link
  })

  it('toggling collapses and expands, and remembers the choice', async () => {
    render(SideNav, { current: 'today' })
    await fireEvent.click(await screen.findByRole('button', { name: '收起菜单' }))
    expect(localStorage.getItem(KEY)).toBe('1')
    expect(screen.getByRole('link', { name: '今日' })).toHaveTextContent('')
    await fireEvent.click(screen.getByRole('button', { name: '展开菜单' }))
    expect(localStorage.getItem(KEY)).toBe('0')
    expect(screen.getByRole('link', { name: '今日' })).toHaveTextContent('今日')
    cleanup()
    localStorage.setItem(KEY, '1')
    render(SideNav, { current: 'today' })                               // a fresh mount starts collapsed
    expect(await screen.findByRole('button', { name: '展开菜单' })).toBeInTheDocument()
  })

  it('collapse state survives localStorage throwing: it starts expanded and the toggle still works for this page', async () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('denied') }, setItem: () => { throw new Error('denied') },
      removeItem: () => { throw new Error('denied') },
    })
    render(SideNav, { current: 'today' })
    expect(await screen.findByRole('link', { name: '今日' })).toHaveTextContent('今日')
    await fireEvent.click(screen.getByRole('button', { name: '收起菜单' }))
    expect(screen.getByRole('link', { name: '今日' })).toHaveTextContent('')
    await waitFor(() => expect(screen.getByRole('button', { name: '展开菜单' })).toBeInTheDocument())
  })

  it('inside a drawer the menu is never collapsed and has no collapse button', async () => {
    localStorage.setItem(KEY, '1')
    render(SideNav, { current: 'today', collapsible: false })
    expect(await screen.findByRole('link', { name: '今日' })).toHaveTextContent('今日')
    expect(screen.queryByRole('button', { name: /收起菜单|展开菜单/ })).toBeNull()
  })
})
