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
