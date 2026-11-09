import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/svelte'
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
