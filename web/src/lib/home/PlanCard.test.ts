import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import PlanCard from './PlanCard.svelte'
import type { PlanCardData } from './types'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

const PENDING: PlanCardData = {
  plan_id: 41,
  ticker: 'MU',
  status: 'pending',
  kind: 'planned',
  levels: { entry_low: '95.10', entry_high: '97.30', stop: '91.40', target1: '104.70' },
  initial_stop: '91.40',
  trailing: false,
  shares: 120,
  valid_from: '2026-11-05',
  valid_until: '2026-11-12',
  start_session: null,
  max_hold_sessions: 20,
  next_action: null,
  position: null,
  held_sessions: null,
  stop_order_status: 'none',
  confirmed_stop_order: null,
  outcome: {},
  thesis: '突破 20 日高点',
  created_at: '2026-11-05T22:00:00+00:00',
  ended_session: null,
  end_reason: null,
  close_above_entry: true,
  missing_bar: false,
}

const OPEN: PlanCardData = {
  ...PENDING,
  plan_id: 42,
  ticker: 'AAPL',
  status: 'open',
  start_session: '2026-11-06',
  held_sessions: 3,
  close_above_entry: false,
  next_action: { kind: 'stop_order', text: '未确认止损单', stop: '201.50', reason: null },
  position: { quantity: 50, avg_cost: '200.10', last_close: '205.30', unrealized_pnl: '260.00', unrealized_pct: '0.026' },
  stop_order_status: 'needs_raise',
}

const CLOSED: PlanCardData = {
  ...PENDING,
  plan_id: 43,
  ticker: 'NVDA',
  status: 'closed',
  kind: 'adopted',
  ended_session: '2026-11-04',
  end_reason: 'trailing_stop',
  close_above_entry: false,
  missing_bar: false,
  outcome: { realized_pnl: '520.00', r_multiple: '2.10', r_text: null, exit_reason: 'trailing_stop', stop_delay_sessions: 1, entry_in_zone: true },
}

describe('PlanCard', () => {
  it('pending card shows status, levels, validity and the close-above-entry warning', () => {
    const { container } = render(PlanCard, { card: PENDING })
    expect(screen.getByText('待入场')).toBeInTheDocument()
    expect(screen.getByText('计划')).toBeInTheDocument() // kind=planned 的计划/补录徽标（终审 M4：fixture 用真值）
    expect(container.textContent).toContain('入场 $95.10–$97.30 · 止损 $91.40 · T1 $104.70 · 建议 120 股')
    expect(container.textContent).toContain('有效至 2026-11-12')
    expect(screen.getByText('最新收盘高于入场区间')).toBeInTheDocument()
    expect(screen.queryByText('缺日线，未复查')).toBeNull() // 当天有日线时不提示
    // 根节点带锚点 id：研究卡「已转计划 #41」链接指向它（审查 P2-5）
    expect(container.querySelector('[data-slot="card"]#plan-41')).not.toBeNull()
  })

  it('pending card shows 缺日线，未复查 when the session bar is missing', () => {
    render(PlanCard, { card: { ...PENDING, close_above_entry: false, missing_bar: true } })
    expect(screen.getByText('缺日线，未复查')).toBeInTheDocument() // spec §6.3 末段固定文案（终审 F1）
    expect(screen.queryByText('最新收盘高于入场区间')).toBeNull()
  })

  it('open card shows the next action, position pnl, held sessions and the stop-order state', () => {
    const { container } = render(PlanCard, { card: OPEN })
    expect(screen.getByText('已买入')).toBeInTheDocument()
    expect(screen.getByText('未确认止损单')).toBeInTheDocument()
    expect(screen.getByText('$260.00')).toBeInTheDocument()
    expect(screen.getByText('$200.10')).toBeInTheDocument() // 成本带 $（spec §7）
    expect(container.textContent).toContain('已持有 3 个交易日')
    expect(screen.getByText('止损单需上调')).toBeInTheDocument()
  })

  it('closed card shows the recorded outcome including r_text', () => {
    render(PlanCard, { card: { ...CLOSED, outcome: { ...CLOSED.outcome, r_multiple: null, r_text: '补录，不计' } } })
    expect(screen.getByText('已平仓')).toBeInTheDocument()
    expect(screen.getByText('补录')).toBeInTheDocument() // kind=adopted 的徽标（终审 M4：fixture 用真值）
    expect(screen.getByText('补录，不计')).toBeInTheDocument()
    expect(screen.getByText('$520.00')).toBeInTheDocument()
  })

  it('closed card shows the risk multiple when there is one', () => {
    const { container } = render(PlanCard, { card: CLOSED })
    expect(container.textContent).toContain('相当于计划风险 2.10 倍')
    expect(container.textContent).toContain('离场原因：移动止损')
  })

  it('expanding fetches the plan detail and lists the event timeline', async () => {
    const fetchRouter = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/plans/41') {
        return json({
          ...PENDING,
          events: [
            { session: '2026-11-05', event: 'created', reason: '名单确认后保存', before: {}, after: {}, source: 'agent', created_at: '2026-11-05T22:00:00+00:00' },
            { session: '2026-11-06', event: 'continued', reason: '延续：仍在入场区间', before: {}, after: {}, source: 'agent', created_at: '2026-11-06T22:00:00+00:00' },
          ],
        })
      }
      return json({})
    })
    vi.stubGlobal('fetch', fetchRouter)
    render(PlanCard, { card: PENDING })
    await fireEvent.click(screen.getByRole('button', { name: /变化记录/ }))
    expect(await screen.findByText(/延续：仍在入场区间/)).toBeInTheDocument()
    expect(screen.getByText(/名单确认后保存/)).toBeInTheDocument()
    expect(fetchRouter).toHaveBeenCalledWith('/api/plans/41', expect.anything())
  })

  it('取消计划 goes through preview then confirmed=true', async () => {
    const calls: { url: string; body: Record<string, unknown> }[] = []
    const fetchRouter = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), body: JSON.parse(String(init?.body ?? '{}')) })
      const confirmed = (calls.at(-1)!.body as { confirmed?: boolean }).confirmed
      if (!confirmed) return json({ write: false, preview: '将取消计划 41。确认后写入。' })
      return json({ plan: { id: 41 }, event: { id: 5 } })
    })
    vi.stubGlobal('fetch', fetchRouter)
    const onChanged = vi.fn()
    render(PlanCard, { card: PENDING, onChanged })
    await fireEvent.click(screen.getByRole('button', { name: '取消计划' }))
    expect(await screen.findByRole('dialog')).toHaveTextContent('将取消计划 41。确认后写入。')
    await fireEvent.click(screen.getAllByRole('button', { name: '取消计划' }).at(-1)!)
    await waitFor(() => expect(calls.filter((c) => c.url === '/api/plans/41/cancel')).toHaveLength(2))
    const bodies = calls.filter((c) => c.url === '/api/plans/41/cancel').map((c) => c.body)
    expect(bodies[0].confirmed).toBe(false)
    expect(bodies[1].confirmed).toBe(true)
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })
})
