import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import TradeForm from './TradeForm.svelte'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

describe('TradeForm', () => {
  it('prefills ticker, plan_id (hidden) and the suggested shares', async () => {
    render(TradeForm, { open: true, ticker: 'MU', planId: 41, shares: 120 })
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('记成交')
    expect((screen.getByLabelText('股票') as HTMLInputElement).value).toBe('MU')
    expect((screen.getByLabelText('股数') as HTMLInputElement).value).toBe('120')
  })

  it('side and a string quantity prefill a sell (rotation_sell 「记成交」)', async () => {
    render(TradeForm, { open: true, ticker: 'AAA', planId: 11, shares: '150', side: 'sell' })
    await screen.findByRole('dialog')
    expect(screen.getByLabelText('方向')).toHaveTextContent('卖出')
    expect((screen.getByLabelText('股数') as HTMLInputElement).value).toBe('150')
  })

  it('submits with confirmed=false first, shows the preview, then writes with confirmed=true', async () => {
    const calls: { url: string; body: Record<string, unknown> }[] = []
    const fetchRouter = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), body: JSON.parse(String(init?.body ?? '{}')) })
      const confirmed = (calls.at(-1)!.body as { confirmed?: boolean }).confirmed
      if (!confirmed) return json({ write: false, preview: '将记录成交：MU 买入 120 股，价格 96.5。确认后写入。' })
      return json({ trade: { id: 7 }, stop_order: '在券商挂 91.40 的止损单' })
    })
    vi.stubGlobal('fetch', fetchRouter)
    const onDone = vi.fn()
    render(TradeForm, { open: true, ticker: 'MU', planId: 41, shares: 120, onDone })
    await screen.findByRole('dialog')

    await fireEvent.input(screen.getByLabelText('价格'), { target: { value: '96.5' } })
    await fireEvent.click(screen.getByRole('button', { name: '提交' }))

    expect(await screen.findByText('将记录成交：MU 买入 120 股，价格 96.5。确认后写入。')).toBeInTheDocument()
    expect(calls).toHaveLength(1)
    expect(calls[0].url).toBe('/api/trades')
    expect(calls[0].body).toMatchObject({ ticker: 'MU', side: 'buy', quantity: 120, price: 96.5, plan_id: 41, confirmed: false })

    await fireEvent.click(screen.getByRole('button', { name: '写入' }))
    await waitFor(() => expect(calls).toHaveLength(2))
    expect(calls[1].body.confirmed).toBe(true)
    expect(calls[1].body.request_id).toBe(calls[0].body.request_id)
    await waitFor(() => expect(onDone).toHaveBeenCalled())
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('splits the time into New York date and time controls and assembles executed_at with the NY offset', async () => {
    const calls: Record<string, unknown>[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        calls.push(JSON.parse(String(init?.body ?? '{}')))
        return json({ write: false, preview: '将记录成交：MU 买入 120 股。确认后写入。' })
      }),
    )
    render(TradeForm, { open: true, ticker: 'MU', planId: 41, shares: 120 })
    await screen.findByRole('dialog')
    const date = screen.getByLabelText('日期（美东）') as HTMLInputElement
    const time = screen.getByLabelText('时间（美东）') as HTMLInputElement
    expect(date.type).toBe('date')
    expect(time.type).toBe('time')
    // 默认值是此刻的美东日期与时间（spec §7）
    expect(date.value).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(time.value).toMatch(/^\d{2}:\d{2}$/)

    await fireEvent.input(date, { target: { value: '2026-07-15' } })
    await fireEvent.input(time, { target: { value: '10:30' } })
    await fireEvent.input(screen.getByLabelText('价格'), { target: { value: '96.5' } })
    await fireEvent.click(screen.getByRole('button', { name: '提交' }))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0].executed_at).toBe('2026-07-15T10:30:00-04:00') // 夏令时按日期算
  })

  it('cannot submit without the time', async () => {
    render(TradeForm, { open: true, ticker: 'MU', planId: 41, shares: 120 })
    await screen.findByRole('dialog')
    await fireEvent.input(screen.getByLabelText('价格'), { target: { value: '96.5' } })
    await fireEvent.input(screen.getByLabelText('时间（美东）'), { target: { value: '' } })
    expect(screen.getByRole('button', { name: '提交' })).toBeDisabled()
    await fireEvent.input(screen.getByLabelText('时间（美东）'), { target: { value: '10:30' } })
    expect(screen.getByRole('button', { name: '提交' })).toBeEnabled()
  })

  it('keeps the dialog open and shows the reason when the write is rejected', async () => {
    const fetchRouter = vi.fn(async () => json({ error: 'position_invalid: 卖出超过持仓' }, 400))
    vi.stubGlobal('fetch', fetchRouter)
    const onDone = vi.fn()
    render(TradeForm, { open: true, ticker: 'MU', planId: 41, shares: 120, onDone })
    await screen.findByRole('dialog')
    await fireEvent.input(screen.getByLabelText('价格'), { target: { value: '96.5' } })
    await fireEvent.click(screen.getByRole('button', { name: '提交' }))
    expect(await screen.findByText('卖出超过持仓')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(onDone).not.toHaveBeenCalled()
  })
})
