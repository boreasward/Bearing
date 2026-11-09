import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte'
import DashboardPane from './DashboardPane.svelte'
import { loadWorkbench, todayData } from '$lib/views/workbench-fixtures'
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('renders five blocks, primary action and correct per-object chips', async () => {
 await loadWorkbench(); const onBring = vi.fn(); render(DashboardPane, { onBring })
 for (const title of ['今日操作','账户','持仓与计划','研究中','市场温度']) expect(screen.getByRole('heading', { name:new RegExp(title) })).toBeInTheDocument()
 const actions = screen.getByRole('region', { name:/今日操作/ })
 expect(within(actions).getByRole('button', { name:'确认已挂单' })).toHaveClass('bg-primary')
 await fireEvent.click(within(actions).getByRole('button', { name:'带进对话' }))
 expect(onBring).toHaveBeenLastCalledWith({ kind:'plan', ticker:'AAPL', id:1 })
 await fireEvent.click(within(screen.getByRole('region', { name:'研究中' })).getByRole('button', { name:'带进对话' }))
 expect(onBring).toHaveBeenLastCalledWith({ kind:'research', ticker:'MU', id:1 })
})
it('explains empty blocks', async () => {
 await loadWorkbench({ '/api/today':{ ...todayData, session:null, actions:[], counts:{ actions:0, holdings:0, pending:0, watching:0 } }, '/api/plans?group=holdings':{ plans:[] }, '/api/research?group=watching':{ research:[] }, '/api/market':null })
 render(DashboardPane, { onBring:vi.fn() })
 for (const text of ['今日无操作','无持仓与计划','没有观察中的研究','还没有运行记录']) expect(await screen.findByText(text)).toBeInTheDocument()
})
it('confirms stop orders before writing and emitting local feedback', async () => {
 const { waitFor } = await import('@testing-library/svelte')
 await loadWorkbench(); const fetcher = vi.mocked(fetch)
 const calls: Record<string, unknown>[] = []
 const original = fetcher.getMockImplementation()!
 fetcher.mockImplementation(async (input, init) => {
  if (init?.method === 'POST') { const body = JSON.parse(String(init.body)); calls.push(body); return new Response(JSON.stringify(body.confirmed ? {} : { write:false, preview:'即将确认挂单' })) }
  return original(input, init)
 })
 const listener = vi.fn(); window.addEventListener('workbench:action', listener)
 render(DashboardPane, { onBring:vi.fn() })
 await fireEvent.click(screen.getByRole('button', { name:'确认已挂单' }))
 await screen.findByText('即将确认挂单'); expect(listener).not.toHaveBeenCalled()
 await fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name:'确认已挂单' }))
 await waitFor(() => expect(listener).toHaveBeenCalledOnce())
 expect(calls.map(call => call.confirmed)).toEqual([false,true]); expect(calls[1].request_id).toBe(calls[0].request_id)
 window.removeEventListener('workbench:action', listener)
})
it('confirms the previewed stop request after another row opens its trade form', async () => {
 await loadWorkbench({ '/api/today':{ ...todayData, actions:[todayData.actions[0], { plan_id:2, ticker:'NVDA', kind:'sell_next_open', text:'下次开盘卖出', stop:'80', quantity:'20', reason:null }] } })
 const fetcher = vi.mocked(fetch)
 const original = fetcher.getMockImplementation()!
 const calls: { url:string; body:Record<string, unknown> }[] = []
 let resolvePreview!: (response: Response) => void
 fetcher.mockImplementation(async (input, init) => {
  if (init?.method === 'POST') {
   const body = JSON.parse(String(init.body))
   calls.push({ url:String(input), body })
   if (!body.confirmed) return new Promise<Response>(resolve => { resolvePreview = resolve })
   return new Response(JSON.stringify({}))
  }
  return original(input, init)
 })
 render(DashboardPane, { onBring:vi.fn() })
 await fireEvent.click(screen.getByRole('button', { name:'确认已挂单' }))
 expect(calls[0]).toMatchObject({ url:'/api/plans/1/stop-order', body:{ price:96, confirmed:false } })
 await fireEvent.click(screen.getByRole('button', { name:'记成交' }))
 expect(await screen.findByLabelText('股票')).toHaveValue('NVDA')
 resolvePreview(new Response(JSON.stringify({ write:false, preview:'确认 AAPL 计划 1 挂单，止损 96' })))
 await screen.findByText('确认 AAPL 计划 1 挂单，止损 96')
 const confirmation = screen.getByRole('dialog', { name:'确认已挂单' })
 await fireEvent.click(within(confirmation).getByRole('button', { name:'确认已挂单' }))
 await waitFor(() => expect(calls).toHaveLength(2))
 expect(calls[1]).toEqual({ url:'/api/plans/1/stop-order', body:{ price:96, request_id:calls[0].body.request_id, confirmed:true } })
})
