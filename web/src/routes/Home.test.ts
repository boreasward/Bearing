import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/svelte'
import Home from './Home.svelte'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  document.body.innerHTML = ''
})

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

const TODAY = {
  session: '2026-11-06',
  cutoff_at: '2026-11-07T01:30:00+00:00',
  actions: [
    { plan_id: 2, ticker: 'MU', kind: 'stop_hit_unresolved', text: '止损已触及，未见卖出记录', stop: '98.00', quantity: 120, reason: null },
  ],
  account: { cash: '10000.00', equity: '34500.00', unrealized_pnl: '260.00', realized_pnl: '520.00', cash_negative: false, notes: [] },
}
const BRIEF = {
  session: '2026-11-06',
  status: 'draft',
  markdown: '# 简报 · 2026-11-06\n\n二次确认未完成\n\n## 持仓\n\n- MU：继续持有',
  error: null,
  runtime: 'pi',
  provider: 'anthropic',
  model: 'claude-sonnet',
  usage: {},
  elapsed_seconds: 12,
  unfinished: true,
}
const PLANS = {
  holdings: {
    plans: [
      {
        plan_id: 42, ticker: 'AAPL', status: 'open', kind: 'breakout',
        levels: { entry_low: '199.00', entry_high: '201.00', stop: '201.50', target1: '210.00' },
        initial_stop: '191.40', trailing: false, shares: 50, valid_from: '2026-11-05', valid_until: '2026-11-12',
        start_session: '2026-11-06', max_hold_sessions: 20,
        next_action: { kind: 'stop_order', text: '未确认止损单', stop: '201.50', reason: null },
        position: { quantity: 50, avg_cost: '200.10', last_close: '205.30', unrealized_pnl: '260.00', unrealized_pct: '0.026' },
        held_sessions: 3, stop_order_status: 'none', confirmed_stop_order: null, outcome: {}, thesis: null,
        created_at: '2026-11-05T22:00:00+00:00', ended_session: null, end_reason: null, close_above_entry: false,
      },
    ],
  },
  pending: {
    plans: [
      {
        plan_id: 41, ticker: 'MU', status: 'pending', kind: 'breakout',
        levels: { entry_low: '95.10', entry_high: '97.30', stop: '91.40', target1: '104.70' },
        initial_stop: '91.40', trailing: false, shares: 120, valid_from: '2026-11-05', valid_until: '2026-11-12',
        start_session: null, max_hold_sessions: 20, next_action: null, position: null, held_sessions: null,
        stop_order_status: 'none', confirmed_stop_order: null, outcome: {}, thesis: null,
        created_at: '2026-11-05T22:00:00+00:00', ended_session: null, end_reason: null, close_above_entry: true,
      },
    ],
  },
  ended: { plans: [] },
}
const CHATS = {
  chats: [{ chat_id: 'c1', title: '关于止损的追问', created_at: '2026-11-06T22:00:00+00:00', read_only: false, model: 'claude-sonnet' }],
}
const CHAT_DETAIL = {
  chat: { chat_id: 'c1', title: '关于止损的追问', runtime: 'pi', provider: 'anthropic', model: 'claude-sonnet', read_only: false, created_at: '2026-11-06T22:00:00+00:00' },
  messages: [],
}

function stubHomeApi(overrides: { latestBrief?: () => Response } = {}) {
  const router = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://local')
    const path = url.pathname
    if (path === '/api/today') return json(TODAY)
    if (path === '/api/briefs') return json({ briefs: [{ session: '2026-11-06', status: 'draft', pushed: false }, { session: '2026-11-05', status: 'final', pushed: true }] })
    if (path === '/api/briefs/latest') return overrides.latestBrief ? overrides.latestBrief() : json(BRIEF)
    if (path === '/api/briefs/2026-11-06') return json(BRIEF)
    if (path === '/api/briefs/2026-11-05') return json({ ...BRIEF, session: '2026-11-05', status: 'final', markdown: '# 简报 · 2026-11-05\n\n没有变化', unfinished: false })
    if (path === '/api/plans') {
      const group = url.searchParams.get('group') ?? 'holdings'
      return json(PLANS[group as keyof typeof PLANS] ?? { plans: [] })
    }
    if (path === '/api/chats') return json(CHATS)
    if (path === '/api/chats/c1') return json(CHAT_DETAIL)
    return json({})
  })
  vi.stubGlobal('fetch', router)
  return router
}

describe('Home', () => {
  it('renders the four blocks from the page API data', async () => {
    stubHomeApi()
    render(Home)
    expect(await screen.findByRole('heading', { name: '今日操作' })).toBeInTheDocument()
    expect(screen.getByText('止损已触及，未见卖出记录')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: '计划与持仓' })).toBeInTheDocument()
    expect(screen.getByText('AAPL')).toBeInTheDocument()
    expect(screen.getByText('最新收盘高于入场区间')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: '今日简报' })).toBeInTheDocument()
    expect(screen.getByText('MU：继续持有')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: '对话' })).toBeInTheDocument()
  })

  it('is a wide two-column grid and orders the blocks 今日操作 → 计划与持仓 → 简报 → 对话 on narrow screens', async () => {
    stubHomeApi()
    render(Home)
    await screen.findByRole('heading', { name: '今日操作' })
    await screen.findByRole('heading', { name: '对话' })
    const grid = document.querySelector('[class*="lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]"]')
    expect(grid).not.toBeNull()
    const block = (order: string) => {
      const wrap = grid!.querySelector(`:scope > [class*="${order}"]`)
      expect(wrap, order).not.toBeNull()
      return wrap!
    }
    expect(block('order-1')).toHaveTextContent('今日操作')
    expect(block('order-2')).toHaveTextContent('总资产')
    expect(block('order-3')).toHaveTextContent('计划与持仓')
    expect(block('order-4')).toHaveTextContent('今日简报')
    expect(block('order-5')).toHaveTextContent('对话')
    // the order values themselves say 1 < 3 < 4 < 5: 今日操作 before 计划与持仓 before 简报 before 对话
    const order = (c: string) => Number(/order-(\d)/.exec(c)![1])
    const classes = [...grid!.children].map((el) => el.className)
    expect(order(classes[0])).toBeLessThan(order(classes[2]))
    expect(order(classes[2])).toBeLessThan(order(classes[3]))
    expect(order(classes[3])).toBeLessThan(order(classes[4]))
  })

  it('shows 二次确认未完成 when the latest brief is unfinished', async () => {
    stubHomeApi()
    render(Home)
    // the panel's own marker (the fixture's markdown repeats the phrase after the title)
    await waitFor(() => expect(screen.getAllByText('二次确认未完成').length).toBeGreaterThan(0))
  })

  it('shows the reason when there is no brief yet', async () => {
    stubHomeApi({ latestBrief: () => json({ error: 'no_brief' }, 404) })
    render(Home)
    expect(await screen.findByText('今天还没有简报')).toBeInTheDocument()
  })

  it('offers the brief history and the chat history to switch between', async () => {
    stubHomeApi()
    render(Home)
    const briefSelect = await screen.findByRole('combobox', { name: '选择简报' })
    await waitFor(() => expect(briefSelect.textContent).toContain('2026-11-06'))
    const chatSelect = await screen.findByRole('combobox', { name: '选择对话' })
    await waitFor(() => expect(chatSelect.textContent).toContain('关于止损的追问'))
    expect(screen.getByLabelText('提问')).toBeInTheDocument()
  })

  it('links to the component gallery in dev', async () => {
    stubHomeApi()
    expect(import.meta.env.DEV).toBe(true)
    render(Home)
    expect(await screen.findByRole('link', { name: '组件画廊' })).toHaveAttribute('href', '#_ui')
  })

  it('has no gallery link in a production build, where the gallery does not exist', async () => {
    stubHomeApi()
    vi.stubEnv('DEV', false)
    render(Home)
    await screen.findByRole('heading', { name: '对话' })
    expect(screen.queryByRole('link')).toBeNull()
  })
})
