import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import CashFlowForm from './CashFlowForm.svelte'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

describe('CashFlowForm', () => {
  it('sends a YYYY-MM-DD date as local midnight with its offset (the server wants a timezone)', async () => {
    // 2026-11-08 用户截图：填「2026-11-08」提交被拒（invalid_argument），表单提示却写着 YYYY-MM-DD
    const bodies: Record<string, unknown>[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        bodies.push(JSON.parse(String(init?.body ?? '{}')))
        return json({ write: false, preview: '将记录期初现金 30。确认后写入。' })
      }),
    )
    render(CashFlowForm, { open: true })
    await screen.findByRole('dialog')
    await fireEvent.input(screen.getByLabelText('金额'), { target: { value: '30' } })
    await fireEvent.input(screen.getByLabelText('生效日期'), { target: { value: '2026-11-08' } })
    await fireEvent.click(screen.getByRole('button', { name: '提交' }))
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(String(bodies[0].effective_at)).toMatch(/^2026-11-08T00:00:00[+-]\d{2}:\d{2}$/)
  })

  it('shows the server reason, not just the error code', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ error: 'invalid_argument', detail: 'effective_at 必须是 ISO 时间' }, 400)))
    render(CashFlowForm, { open: true })
    await screen.findByRole('dialog')
    await fireEvent.input(screen.getByLabelText('金额'), { target: { value: '30' } })
    await fireEvent.input(screen.getByLabelText('生效日期'), { target: { value: '11/08' } })
    await fireEvent.click(screen.getByRole('button', { name: '提交' }))
    expect(await screen.findByText('effective_at 必须是 ISO 时间')).toBeInTheDocument()
  })
})
