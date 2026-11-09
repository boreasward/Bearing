import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, cleanup, screen, within, waitFor } from '@testing-library/svelte'
import { tick } from 'svelte'
import { layout } from '$lib/shell/layout.svelte'
import { invalidateAll } from '$lib/stores'
import { loadWorkbench } from '$lib/views/workbench-fixtures'
import App from './app.svelte'

beforeEach(async () => {
  location.hash = ''
  localStorage.clear()
  Object.assign(layout, { narrow: false, drawer: 'closed', navSegment: 'quick', navCollapsed: false })
  await loadWorkbench({ '/api/chats': { chats: [] } })
  invalidateAll()
})
afterEach(async () => {
  cleanup()
  await tick()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function navigate(hash: string) {
  location.hash = hash
  window.dispatchEvent(new HashChangeEvent('hashchange'))
}

describe('app routing', () => {
  it('opens the default chat inside the shell with seven view destinations', async () => {
    render(App)
    expect(await screen.findByRole('heading', { name: '新开对话' })).toBeVisible()
    expect(screen.getByLabelText('提问')).toBeVisible()
    const nav = within(screen.getByRole('navigation', { name: '快捷入口' }))
    const destinations = [
      ['今日', '#today'], ['账户', '#account'], ['行情', '#market'],
      ['计划与持仓', '#plans'], ['研究', '#research'], ['复盘', '#retrospect'], ['定时任务', '#jobs'],
    ]
    expect(nav.getAllByRole('link')).toHaveLength(7)
    for (const [name, href] of destinations) {
      expect(nav.getByRole('link', { name })).toHaveAttribute('href', href)
    }
  })

  it('opens a full view when the hash changes and marks its navigation current', async () => {
    render(App)
    await screen.findByRole('heading', { name: '新开对话' })
    navigate('#market')
    await screen.findAllByRole('heading', { name: '行情', level: 1 })
    expect(await screen.findByRole('table', { name: '关注的标的' })).toBeVisible()
    await waitFor(() => expect(screen.getByRole('link', { name: '行情' })).toHaveAttribute('aria-current', 'page'))
  })

  it('switches to the gallery when the hash changes', async () => {
    render(App)
    await screen.findByRole('heading', { name: '新开对话' })
    navigate('#_ui')
    expect(await screen.findByText('开发模式组件画廊（不进生产包）')).toBeInTheDocument()
  })

  it('#_ui renders the gallery in dev', async () => {
    expect(import.meta.env.DEV).toBe(true)
    location.hash = '#_ui'
    render(App)
    expect(await screen.findByText('开发模式组件画廊（不进生产包）')).toBeInTheDocument()
  })
})
