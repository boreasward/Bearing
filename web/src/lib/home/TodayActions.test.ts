import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import TodayActions from './TodayActions.svelte'
import type { TodayAction } from './types'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

const HOLD: TodayAction = {
  plan_id: 1,
  ticker: 'AAPL',
  kind: 'stop_order',
  text: '未确认止损单',
  stop: '201.50',
  quantity: 50,
  reason: null,
}
const STOP_HIT: TodayAction = {
  plan_id: 2,
  ticker: 'MU',
  kind: 'stop_hit_unresolved',
  text: '止损已触及，未见卖出记录',
  stop: '98.00',
  quantity: 120,
  reason: null,
}
const STOP_ORDER: TodayAction = {
  plan_id: 2,
  ticker: 'MU',
  kind: 'stop_order',
  text: '未确认止损单',
  stop: '98.00',
  quantity: 120,
  reason: null,
}

describe('TodayActions', () => {
  it('shows a compact empty card when there is nothing to do', () => {
    // 用户 2026-11-07：没有操作时整块消失会让左栏顶部空出一大块；改为一张小的空卡片
    render(TodayActions, { actions: [] })
    expect(screen.getByRole('heading', { name: '今日操作' })).toBeInTheDocument()
    expect(screen.getByText('今天没有需要操作的事项')).toBeInTheDocument()
    expect(screen.queryAllByRole('listitem')).toHaveLength(0)
  })

  it('sorts stop_hit_unresolved first and shows the fixed texts', () => {
    render(TodayActions, { actions: [HOLD, STOP_HIT] })
    const items = screen.getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('MU')
    expect(items[0]).toHaveTextContent('止损已触及，未见卖出记录')
    expect(items[1]).toHaveTextContent('AAPL')
    expect(items[1]).toHaveTextContent('未确认止损单')
  })

  it('确认已挂单 posts confirmed=false first, then confirmed=true after the dialog confirms', async () => {
    // explicit router: capture request bodies and answer like the server
    const calls: { url: string; body: Record<string, unknown> }[] = []
    const fetchRouter = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, body: JSON.parse(String(init?.body ?? '{}')) })
      const confirmed = (calls.at(-1)!.body as { confirmed?: boolean }).confirmed
      if (url.endsWith('/stop-order') && !confirmed) {
        return json({ write: false, preview: '将确认 MU 的止损单挂单价 98.00。确认后写入。' })
      }
      return json({ plan: { id: 2 }, event: { id: 9 } })
    })
    vi.stubGlobal('fetch', fetchRouter)

    render(TodayActions, { actions: [STOP_ORDER] })
    await fireEvent.click(screen.getByRole('button', { name: '确认已挂单' }))

    expect(await screen.findByRole('dialog')).toHaveTextContent('将确认 MU 的止损单挂单价 98.00。确认后写入。')
    expect(calls).toHaveLength(1)
    expect(calls[0].url).toBe('/api/plans/2/stop-order')
    expect(calls[0].body.confirmed).toBe(false)

    await fireEvent.click(screen.getAllByRole('button', { name: '确认已挂单' }).at(-1)!)
    await waitFor(() => expect(calls).toHaveLength(2))
    expect(calls[1].url).toBe('/api/plans/2/stop-order')
    expect(calls[1].body.confirmed).toBe(true)
    expect(calls[1].body.request_id).toBe(calls[0].body.request_id)
    expect(calls[1].body.price).toBe(98)
  })
})
