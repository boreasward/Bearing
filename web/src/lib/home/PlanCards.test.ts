import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { invalidateAll } from '$lib/stores'
import PlanCards from './PlanCards.svelte'

beforeEach(() => { invalidateAll() })

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

describe('PlanCards', () => {
  it('补录持仓 opens an empty 记成交 form whose buy carries no plan_id (the host adopts it)', async () => {
    // 用户 2026-11-07：已有持仓只能在对话里补录；页面上给一个入口，复用记成交表单
    const bodies: Record<string, unknown>[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.startsWith('/api/plans')) return json({ plans: [] })
        bodies.push(JSON.parse(String(init?.body ?? '{}')))
        return json({ write: false, preview: '将记录成交：NVDA 买入 100 股，价格 180。没有有效计划，将补录一份计划。确认后写入。' })
      }),
    )
    render(PlanCards)
    expect(await screen.findByText('暂无持仓')).toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: '补录持仓' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('记成交')
    expect((screen.getByLabelText('股票') as HTMLInputElement).value).toBe('')
    await fireEvent.input(screen.getByLabelText('股票'), { target: { value: 'NVDA' } })
    await fireEvent.input(screen.getByLabelText('股数'), { target: { value: '100' } })
    await fireEvent.input(screen.getByLabelText('价格'), { target: { value: '180' } })
    await fireEvent.click(screen.getByRole('button', { name: '提交' }))
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toMatchObject({ ticker: 'NVDA', side: 'buy', quantity: 100, price: 180, confirmed: false })
    expect(bodies[0]).not.toHaveProperty('plan_id')
  })
})
it('opens an ended plan outside the first page and refreshes it after writes', async () => {
  const { plansData } = await import('$lib/views/workbench-fixtures')
  const { invalidateAll } = await import('$lib/stores')
  let stop = '96'
  const fetcher = vi.fn(async (url: RequestInfo | URL) => String(url).includes('?') ? json({ plans:[] }) : json({ ...plansData.plans[0], plan_id:999, status:'expired', levels:{ ...plansData.plans[0].levels, stop }, events:[{ event:'created', reason:'历史记录' }] }))
  vi.stubGlobal('fetch', fetcher)
  render(PlanCards, { id:999 })
  expect(await screen.findByText('$96.00')).toBeInTheDocument()
  expect(await screen.findByText(/历史记录/)).toBeInTheDocument()
  stop = '97'; invalidateAll()
  expect(await screen.findByText('$97.00')).toBeInTheDocument()
})
