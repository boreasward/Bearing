import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte'
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
        plan_id: 42, ticker: 'AAPL', status: 'open', kind: 'planned',
        levels: { entry_low: '199.00', entry_high: '201.00', stop: '201.50', target1: '210.00' },
        initial_stop: '191.40', trailing: false, shares: 50, valid_from: '2026-11-05', valid_until: '2026-11-12',
        start_session: '2026-11-06', max_hold_sessions: 20,
        next_action: { kind: 'stop_order', text: '未确认止损单', stop: '201.50', reason: null },
        position: { quantity: 50, avg_cost: '200.10', last_close: '205.30', unrealized_pnl: '260.00', unrealized_pct: '0.026' },
        held_sessions: 3, stop_order_status: 'none', confirmed_stop_order: null, outcome: {}, thesis: null,
        created_at: '2026-11-05T22:00:00+00:00', ended_session: null, end_reason: null, close_above_entry: false, missing_bar: false,
      },
    ],
  },
  pending: {
    plans: [
      {
        plan_id: 41, ticker: 'MU', status: 'pending', kind: 'planned',
        levels: { entry_low: '95.10', entry_high: '97.30', stop: '91.40', target1: '104.70' },
        initial_stop: '91.40', trailing: false, shares: 120, valid_from: '2026-11-05', valid_until: '2026-11-12',
        start_session: null, max_hold_sessions: 20, next_action: null, position: null, held_sessions: null,
        stop_order_status: 'none', confirmed_stop_order: null, outcome: {}, thesis: null,
        created_at: '2026-11-05T22:00:00+00:00', ended_session: null, end_reason: null, close_above_entry: true, missing_bar: false,
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

async function openAssistant() {
  await fireEvent.click(await screen.findByRole('button', { name: '打开助手' }))
}

function stubHomeApi(overrides: { latestBrief?: () => Response; today?: () => Response } = {}) {
  const router = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://local')
    const path = url.pathname
    if (path === '/api/today') return overrides.today ? overrides.today() : json(TODAY)
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
    expect(await screen.findByText('止损已触及，未见卖出记录')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: '计划与持仓' })).toBeInTheDocument()
    expect(screen.getByText('AAPL')).toBeInTheDocument()
    expect(screen.getByText('最新收盘高于入场区间')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: '今日简报' })).toBeInTheDocument()
    expect(screen.getByText('MU：继续持有')).toBeInTheDocument()
    await openAssistant()
    expect(await screen.findByRole('heading', { name: '对话' })).toBeInTheDocument()
  })

  it('stacks each wide-screen column on its own, so the brief sits right under 今日操作 instead of below the plans', async () => {
    // 用户 2026-11-07：单一网格让两栏的行高绑在一起，简报被挤到计划卡片之后、左栏空出一大块。
    // 现在每栏一个纵向堆叠（窄屏用 contents 摊平回单栏，靠 order 排序）。
    stubHomeApi()
    render(Home)
    await screen.findByRole('heading', { name: '今日操作' })
    await screen.findByRole('heading', { name: '今日简报' })
    const grid = document.querySelector('[class*="lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]"]')
    expect(grid).not.toBeNull()
    const main = grid!.querySelector(':scope > [data-column="main"]')!
    const side = grid!.querySelector(':scope > [data-column="side"]')!
    for (const column of [main, side]) {
      expect(column.className).toContain('contents')
      expect(column.className).toContain('lg:flex')
    }
    const blocks = (column: Element) => [...column.children].map((el) => ({ order: /order-(\d)/.exec(el.className)?.[1], text: el.textContent ?? '' }))
    const left = blocks(main)
    expect(left.map((b) => b.order)).toEqual(['1', '4'])
    expect(left[0].text).toContain('今日操作')
    expect(left[1].text).toContain('今日简报')
    const right = blocks(side)
    expect(right.map((b) => b.order)).toEqual(['2', '3'])
    expect(right[0].text).toContain('总资产')
    expect(right[1].text).toContain('计划与持仓')
    expect(grid!.textContent).not.toContain('选择对话') // 对话不在页面栏里，在助手面板里
  })

  it('keeps 今日操作 as a small empty card when there is nothing to do', async () => {
    stubHomeApi({ today: () => json({ ...TODAY, actions: [] }) })
    render(Home)
    expect(await screen.findByRole('heading', { name: '今日操作' })).toBeInTheDocument()
    expect(screen.getByText('今天没有需要操作的事项')).toBeInTheDocument()
  })

  it('opens the assistant from a floating button and keeps it mounted while closed', async () => {
    stubHomeApi()
    render(Home)
    await screen.findByRole('button', { name: '打开助手' })
    // 隐藏状态下 Testing Library 算不出可访问名，直接按 aria-label 取
    const panel = document.querySelector('aside[aria-label="助手"]')!
    expect(panel).not.toBeNull()
    expect(panel).toHaveAttribute('hidden')
    await openAssistant()
    expect(panel).not.toHaveAttribute('hidden')
    expect(await screen.findByRole('heading', { name: '对话' })).toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: '关闭助手' }))
    expect(panel).toHaveAttribute('hidden')
    // 关闭只是隐藏：正在流式输出的一轮不会因为关面板而丢
    expect(panel.querySelector('textarea')).not.toBeNull()
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

  it('offers the brief history and the chat history to switch between, and switching briefs loads the picked one', async () => {
    stubHomeApi()
    render(Home)
    const briefSelect = await screen.findByRole('combobox', { name: '选择简报' })
    await waitFor(() => expect(briefSelect.textContent).toContain('2026-11-06'))
    await openAssistant()
    const chatSelect = await screen.findByRole('combobox', { name: '选择对话' })
    await waitFor(() => expect(chatSelect.textContent).toContain('关于止损的追问'))
    expect(screen.getByLabelText('提问')).toBeInTheDocument()

    // a real switch: pick 2026-11-05 and expect that brief's content (regression: the
    // old guard compared against `selected`, which bits-ui had already updated — dead code;
    // bits-ui select items select on pointerup, not click)
    await fireEvent.click(briefSelect)
    const option = await screen.findByRole('option', { name: /2026-11-05/ })
    await fireEvent.pointerUp(option)
    expect(await screen.findByText('没有变化')).toBeInTheDocument()
    await waitFor(() => expect(briefSelect.textContent).toContain('2026-11-05'))
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
    await screen.findByRole('heading', { name: '今日简报' })
    expect(screen.queryByRole('link')).toBeNull()
  })
})
