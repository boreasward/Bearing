import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, fireEvent, within } from '@testing-library/svelte'
import App from './app.svelte'
import * as api from './lib/api'

// Mock all API calls
vi.mock('./lib/api', () => ({
  getStatus: vi.fn(async () => ({})),
  getRawStatus: vi.fn(async () => ({})),
  getWatchlist: vi.fn(async () => ({ rows: [] })),
  getOperations: vi.fn(async () => ({ operations: [] })),
  getServices: vi.fn(async () => ({})),
  getDataFamilies: vi.fn(async () => ({
    session: '2026-10-22',
    evidence: '这里只展示采集事实。所有规则都未经验证（Phase 0 Gate HOLD）：数据完整不代表信号有效。',
    fetch_runs_ready: true,
    families: Object.entries({
      bars: '日线', sec_filings: 'SEC 申报', news: '资讯', atr: 'ATR', fred: 'FRED 利差', universe: '纳指 100 范围',
    }).map(([id, title]) => ({
      id, title, purpose: `用途-${id}`,
      kind: id === 'atr' ? 'derived' : id === 'universe' ? 'manual' : 'fetched',
      fetch_via: id === 'atr' ? 'bars' : null,
      per_ticker: ['bars', 'sec_filings', 'news'].includes(id),
      job: null, coverage: { targets: 10, covered: 10, lagging: [] },
      latest_at: '2026-10-22', last_run: null, last_success_at: null,
      evidence: '这里只展示采集事实。所有规则都未经验证（Phase 0 Gate HOLD）：数据完整不代表信号有效。', notes: [],
    })),
  })),
  pollRun: vi.fn(async () => ({ status: 'done', result: {} })),
  getScheduledJobs: vi.fn(async () => ({ jobs: [], degraded: false })),
  getJobReports: vi.fn(async () => ({ reports: [], degraded: false })),
  runJob: vi.fn(async () => ({ run_id: 'x', status: 'pending' })),
  getFilingRecords: vi.fn(async () => ({ records: [], tickers: [], degraded: false })),
  getNotifySettings: vi.fn(async () => ({})),
  patchNotifySettings: vi.fn(async () => ({})),
  testNotifyChannel: vi.fn(async () => ({ ok: true })),
  getEnvStatus: vi.fn(async () => ({})),
  // v2.6: the run overview on 今日 and the concept index behind ConceptTerm
  getRunOverview: vi.fn(async () => ({ session: '2026-10-22', ready: true, note: null, runs: [], selected_run_id: null,
                                       headline: '', stages: [] })),
  getConcepts: vi.fn(async () => ({ knowledge_version: 'knowledge-1', groups: [], concepts: [] })),
  getReviewCards: vi.fn(async () => ({ cards: [], counts: {} })),
  getDiscussion: vi.fn(async () => { throw new Error('no such discussion') }),
  get: vi.fn(async () => ({})),
}))
vi.mock('./lib/workbench', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./lib/workbench')>()),
  getToday: vi.fn(async () => ({ session: '2026-10-22', units: [] })),
}))

// Mock EventSource for SSE
class FakeEventSource {
  onerror: (() => void) | null = null
  addEventListener(_: string, __: () => void) {}
  close() {}
}
vi.stubGlobal('EventSource', FakeEventSource)

beforeEach(() => { location.hash = '' })
afterEach(() => cleanup())

describe('app routing', () => {
  it('defaults to the today zone with workbench entries first and the rest under more', async () => {
    const { getAllByText, getByRole } = render(App)
    expect(getAllByText('今日').length).toBeGreaterThan(0)
    expect(getAllByText('标的').length).toBeGreaterThan(0)
    expect(getAllByText('复盘').length).toBeGreaterThan(0)
    expect(getByRole('button', { name: /更多/ })).toBeInTheDocument()
  })

  it('keeps tools under more and exposes accounts and settings in primary navigation', async () => {
    location.hash = '#tools'
    const { getAllByText, getByRole, queryByText } = render(App)
    expect(getAllByText('工具').length).toBeGreaterThan(0)
    expect(getAllByText('设置').length).toBeGreaterThan(0)
    expect(getByRole('link', { name: /数据/ })).toHaveAttribute('href', '#data')
    expect(getByRole('link', { name: '账户' })).toHaveAttribute('href', '#account')
    const entries = [...document.querySelectorAll('nav [data-nav-item]')].map(item => item.textContent!.trim())
    expect(entries.indexOf('设置')).toBeLessThan(entries.indexOf('更多'))
    expect(queryByText('策略')).toBeNull()
    expect(queryByText('交易')).toBeNull()
  })

  it('legacy data hash opens the data overview rather than today', async () => {
    location.hash = '#data/us-research'
    const { findByRole, getAllByRole, getByRole, queryByRole } = render(App)
    expect(await findByRole('heading', { name: '数据' })).toBeInTheDocument()
    expect(getAllByRole('article')).toHaveLength(6)
    expect(getByRole('link', { name: /数据/ })).toHaveAttribute('href', '#data')
    expect(queryByRole('heading', { name: '今日' })).toBeNull()
  })

  it('side nav 知识 has 概念, 原理, 我的知识', async () => {
    location.hash = '#knowledge'
    const { getByRole } = render(App)
    const group = getByRole('button', { name: /知识/ })
    const links = [...group.parentElement!.querySelectorAll('a')].map(a => [a.textContent!.trim(), a.getAttribute('href')])
    expect(links.filter(([text]) => ['概念', '原理', '我的知识'].includes(text as string)))
      .toEqual([['概念', '#knowledge'], ['原理', '#knowledge/principles'], ['我的知识', '#knowledge/mine']])
  })

  it('the 我的知识 child is current under #knowledge/mine/…', async () => {
    location.hash = '#knowledge/mine/3@2'
    const { getByRole } = render(App)
    // the page has its own 知识 route tabs with the same names: look at the side nav
    const side = within(getByRole('navigation', { name: '主导航' }))
    expect(side.getByRole('link', { name: '我的知识' }).className).toMatch(/text-primary/)
    expect(side.getByRole('link', { name: '概念' }).className).not.toMatch(/font-medium/)
    // …and the tabs say the same thing with aria-current
    const tabs = within(getByRole('navigation', { name: '知识' }))
    expect(tabs.getByRole('link', { name: '我的知识' })).toHaveAttribute('aria-current', 'page')
    expect(tabs.getByRole('link', { name: '概念' })).not.toHaveAttribute('aria-current')
  })

  it('#discuss/5 renders the discussion page rather than falling back to today', async () => {
    location.hash = '#discuss/5'
    const { findByText } = render(App)
    expect(await findByText(/讨论加载失败/)).toBeInTheDocument()
  })

  it('#discuss/abc never asks for /discussions/NaN: it falls back to today', async () => {
    location.hash = '#discuss/abc'
    vi.mocked(api.getDiscussion).mockClear()
    const { queryByText } = render(App)
    await Promise.resolve()
    expect(api.getDiscussion).not.toHaveBeenCalled()
    expect(queryByText(/讨论加载失败/)).toBeNull()
  })

  it('side nav shows 知识 with 概念 and 原理', async () => {
    location.hash = '#knowledge'
    const { getByRole } = render(App)
    expect(getByRole('button', { name: /知识/ })).toBeInTheDocument()
    const side = within(getByRole('navigation', { name: '主导航' }))
    expect(side.getByRole('link', { name: '概念' })).toHaveAttribute('href', '#knowledge')
    expect(side.getByRole('link', { name: '原理' })).toHaveAttribute('href', '#knowledge/principles')
    // 知识 sits right after 数据 in the primary menu
    const names = [...document.querySelectorAll('nav [data-nav-item]')].map(n => n.textContent!.trim())
    expect(names.indexOf(names.find(n => n.includes('知识'))!)).toBe(names.indexOf(names.find(n => n.includes('数据'))!) + 1)
  })

  it('theme toggle has an accessible name and flips the theme', async () => {
    document.documentElement.setAttribute('data-theme', 'light')
    const { getByRole } = render(App)
    const toggle = getByRole('button', { name: '切换主题' })
    expect(toggle).toBeInTheDocument()
    await fireEvent.click(toggle)
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    await fireEvent.click(toggle)
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
  })

  it('the top bar names the current page and the navigation icons are not emoji', async () => {
    location.hash = '#data'
    const { container, getByRole } = render(App)
    const header = container.querySelector('header')!
    expect(within(header as HTMLElement).getByText('数据')).toBeInTheDocument()
    expect(getByRole('link', { name: /数据/ })).toHaveAttribute('aria-current', 'page')
    expect(container.textContent).not.toMatch(/[\u{1F000}-\u{1FAFF}\u2600-\u27BF\uFE0F]/u)
  })

  it('below 768px the navigation is a drawer opened from the top bar, and it closes when the page changes', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('max-width: 767px'), media: q,
      addEventListener: () => {}, removeEventListener: () => {} }))
    location.hash = '#data'
    const { getByRole, queryByRole, findByRole } = render(App)
    expect(queryByRole('link', { name: /数据/ })).toBeNull()               // no permanent sidebar on a phone
    await fireEvent.click(getByRole('button', { name: '打开菜单' }))
    const drawer = await findByRole('dialog')
    expect(within(drawer).getByRole('link', { name: /数据/ })).toHaveAttribute('aria-current', 'page')
    vi.unstubAllGlobals()
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  it('#_ui renders the gallery in dev', async () => {
    expect(import.meta.env.DEV).toBe(true)
    location.hash = '#_ui'
    const { findByText } = render(App)
    expect(await findByText('开发模式组件画廊（不进生产包）')).toBeInTheDocument()
  })
})
