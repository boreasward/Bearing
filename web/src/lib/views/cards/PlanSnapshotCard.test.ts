import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import PlanSnapshotCard from './PlanSnapshotCard.svelte'
import { fixture, meta } from '../test-fixtures'
const plan = (fixture('get_plans') as { plans: Record<string, unknown>[] }).plans[0]
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('renders raw plan prices, status, shares, validity and thesis', () => {
 const { container } = render(PlanSnapshotCard, { data: { ...plan, shares: 24 }, meta: { ...meta, write: 'written' } })
 expect(container).toHaveTextContent('$99.00–$101.00')
 expect(container).toHaveTextContent('24 股')
 expect(container).toHaveTextContent('待入场')
 expect(container).toHaveTextContent('2026-11-11')
 expect(container).toHaveTextContent('论点')
 expect(screen.getByText('已写入')).toBeInTheDocument()
 expect(screen.getByRole('link', { name: '打开计划' })).toHaveAttribute('href', '#plans/2')
})
it('renders a write preview without actions', () => {
 render(PlanSnapshotCard, { data: { write: false, preview: '将建立计划' }, meta })
 expect(screen.getByText('待确认')).toBeInTheDocument()
 expect(screen.getByText('将建立计划')).toBeInTheDocument()
 expect(screen.queryByRole('button', { name: '记成交' })).toBeNull()
})
it('prefills TradeForm and emits feedback only after confirmed success', async () => {
 const listener = vi.fn(); window.addEventListener('workbench:action', listener)
 const calls: Record<string, unknown>[] = []
 vi.stubGlobal('fetch', vi.fn(async (_url, init) => { calls.push(JSON.parse(init.body)); return new Response(JSON.stringify(calls.at(-1)!.confirmed ? {} : { write: false, preview: '确认该成交' })) }))
 render(PlanSnapshotCard, { data: { ...plan, shares: 24 }, meta })
 await fireEvent.click(screen.getByRole('button', { name: '记成交' }))
 expect(await screen.findByLabelText('股票')).toHaveValue('NVDA')
 expect(screen.getByLabelText('股数')).toHaveValue('24')
 await fireEvent.input(screen.getByLabelText('价格'), { target: { value: '100' } })
 await fireEvent.click(screen.getByRole('button', { name: '提交' }))
 await screen.findByText('确认该成交'); expect(listener).not.toHaveBeenCalled()
 await fireEvent.click(screen.getByRole('button', { name: '写入' }))
 await waitFor(() => expect(listener).toHaveBeenCalledOnce())
 expect(listener.mock.calls[0][0].detail).toEqual({ text: '已记录成交' })
 expect(calls.map(c => c.confirmed)).toEqual([false, true])
 window.removeEventListener('workbench:action', listener)
})
it('renders holdings from get_positions without inventing levels', () => {
 const { container } = render(PlanSnapshotCard, { data: fixture('get_positions'), meta })
 expect(container).toHaveTextContent('AAPL')
 expect(container).toHaveTextContent('$100.00')
 expect(container).toHaveTextContent('没算')
})
it('continues a single plan from its header with the correct context chip', async () => {
 const listener = vi.fn(); window.addEventListener('workbench:bring', listener)
 render(PlanSnapshotCard, { data:plan, meta })
 const action = screen.getByRole('button', { name:'就这个继续问' })
 expect(action.closest('[data-slot="card-header"]')).toBeInTheDocument()
 await fireEvent.click(action)
 expect(listener).toHaveBeenCalledOnce(); expect(listener.mock.calls[0][0].detail).toEqual({ kind:'plan', ticker:'NVDA', id:2 })
 window.removeEventListener('workbench:bring', listener)
})
