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
  sell_quantity: null,
  counterpart: null,
  stale_quantity: false,
}
const STOP_HIT: TodayAction = {
  plan_id: 2,
  ticker: 'MU',
  kind: 'stop_hit_unresolved',
  text: '止损已触及，未见卖出记录',
  stop: '98.00',
  quantity: 120,
  reason: null,
  sell_quantity: null,
  counterpart: null,
  stale_quantity: false,
}
const STOP_ORDER: TodayAction = {
  plan_id: 2,
  ticker: 'MU',
  kind: 'stop_order',
  text: '未确认止损单',
  stop: '98.00',
  quantity: 120,
  reason: null,
  sell_quantity: null,
  counterpart: null,
  stale_quantity: false,
}

const ROTATION: TodayAction = {
  plan_id: 11,
  ticker: 'AAA',
  kind: 'rotation_sell',
  text: 'AAA 下次开盘卖出 150 股（换仓至 BBB）',
  stop: '36.00',
  quantity: 301,
  reason: null,
  sell_quantity: '150',
  counterpart: 'BBB',
  stale_quantity: false,
}
const STALE_STOP: TodayAction = { ...STOP_ORDER, plan_id: 5, ticker: 'CCC', text: '止损单数量需改为 151 股', stale_quantity: true }

describe('TodayActions', () => {
  it('shows a compact empty card when there is nothing to do', () => {
    // 用户 2026-11-07：没有操作时整块消失会让左栏顶部空出一大块；改为一张小的空卡片
    render(TodayActions, { actions: [] })
    expect(screen.getByRole('region', { name: '今日操作' })).toBeInTheDocument()
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

  it('换仓卖出一行：显示文案，记成交预填卖出与股数', async () => {
    render(TodayActions, { actions: [ROTATION] })
    const row = screen.getByRole('listitem')
    expect(row).toHaveTextContent('AAA')
    expect(row).toHaveTextContent('AAA 下次开盘卖出 150 股（换仓至 BBB）')
    await fireEvent.click(screen.getByRole('button', { name: '记成交' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('记成交')
    expect((screen.getByLabelText('股票') as HTMLInputElement).value).toBe('AAA')
    // 方向是卖出，数量是 sell_quantity（不是持仓 301）
    expect(screen.getByLabelText('方向')).toHaveTextContent('卖出')
    expect((screen.getByLabelText('股数') as HTMLInputElement).value).toBe('150')
  })

  it('换仓卖出一行有「撤回」：先复述，确认后提交', async () => {
    const calls: { url: string; body: Record<string, unknown> }[] = []
    const onChanged = vi.fn()
    const fetchRouter = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), body: JSON.parse(String(init?.body ?? '{}')) })
      if (!calls.at(-1)!.body.confirmed) return json({ write: false, preview: '将撤回 AAA 的换仓指令（卖出 150 股，换至 BBB）。确认后写入。' })
      return json({ plan: { id: 11 }, event: { id: 41 } })
    })
    vi.stubGlobal('fetch', fetchRouter)

    render(TodayActions, { actions: [ROTATION], onChanged })
    await fireEvent.click(screen.getByRole('button', { name: '撤回' }))

    expect(await screen.findByRole('dialog')).toHaveTextContent('将撤回 AAA 的换仓指令（卖出 150 股，换至 BBB）。确认后写入。')
    expect(calls).toHaveLength(1)
    expect(calls[0].url).toBe('/api/plans/11/rotation/withdraw')
    expect(calls[0].body.confirmed).toBe(false)

    await fireEvent.click(screen.getAllByRole('button', { name: '撤回换仓' }).at(-1)!)
    await waitFor(() => expect(calls).toHaveLength(2))
    expect(calls[1].url).toBe('/api/plans/11/rotation/withdraw')
    expect(calls[1].body.confirmed).toBe(true)
    expect(calls[1].body.request_id).toBe(calls[0].body.request_id)
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('止损单数量待改一行：仍用「确认已挂单」按钮', () => {
    render(TodayActions, { actions: [STALE_STOP] })
    expect(screen.getByRole('listitem')).toHaveTextContent('止损单数量需改为 151 股')
    expect(screen.getByRole('button', { name: '确认已挂单' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '撤回' })).toBeNull()
  })

  it('换仓卖出排在止损触及与下次开盘卖出之后、缺日线之前', () => {
    const SELL: TodayAction = { ...STOP_HIT, plan_id: 7, ticker: 'ZZZ', kind: 'sell_next_open', text: '下次开盘卖出' }
    const UNCHECKED: TodayAction = { ...STOP_HIT, plan_id: 8, ticker: 'YYY', kind: 'unchecked', text: '缺日线，无法检查止损' }
    render(TodayActions, { actions: [UNCHECKED, ROTATION, SELL, STOP_HIT] })
    const tickers = screen.getAllByRole('listitem').map((item) => item.textContent ?? '')
    expect(tickers[0]).toContain('MU')
    expect(tickers[1]).toContain('ZZZ')
    expect(tickers[2]).toContain('AAA')
    expect(tickers[3]).toContain('YYY')
  })
})
