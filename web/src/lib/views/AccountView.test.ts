import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within, waitFor } from '@testing-library/svelte'
import AccountView from './AccountView.svelte'
import { loadWorkbench } from './workbench-fixtures'
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('renders five cards with their own header actions', async () => {
 await loadWorkbench(); const { container } = render(AccountView)
 const regions = screen.getAllByRole('region')
 expect(regions.slice(0, 5).map(region => region.getAttribute('aria-label'))).toEqual(['概览','持仓','成交','出入金','对账'])
 expect(within(screen.getByRole('region', { name:'成交' })).getByRole('button', { name:'记成交' })).toBeInTheDocument()
 expect(within(screen.getByRole('region', { name:'出入金' })).getByRole('button', { name:'记出入金' })).toBeInTheDocument()
 expect(within(screen.getByRole('region', { name:'对账' })).getByRole('button', { name:'对账' })).toBeInTheDocument()
 expect(within(screen.getByRole('region', { name:'概览' })).queryByRole('button')).toBeNull()
 for (const label of ['现金','总资产','浮动盈亏','已实现盈亏','对账差额']) expect(within(screen.getByRole('region', { name:'概览' })).getByText(label)).toBeInTheDocument()
 expect(container.querySelector('[data-muted="true"]')).toHaveTextContent('录错股数')
 expect(screen.getByRole('button', { name:'显示更多' })).toBeInTheDocument()
 expect(screen.getByRole('button', { name:'显示更多' }).closest('[role="region"]')).toBeNull()
})
it('each header action opens its form', async () => {
 await loadWorkbench(); render(AccountView)
 for (const [region, name, label] of [['成交','记成交','股票'],['出入金','记出入金','金额'],['对账','对账','券商总资产']]) {
  await fireEvent.click(within(screen.getByRole('region', { name:region })).getByRole('button', { name }))
  expect(await screen.findByLabelText(label)).toBeInTheDocument()
  await fireEvent.keyDown(document, { key:'Escape' })
 }
})
it('account and ledger fail independently', async () => {
 const { marketData, todayData, runsData, ledgerData, plansData, researchData } = await import('./workbench-fixtures')
 const responses: Record<string, unknown> = { '/api/account': { error:'account unavailable' }, '/api/ledger':ledgerData, '/api/today':todayData, '/api/market':marketData, '/api/runs':runsData, '/api/plans?group=holdings':plansData, '/api/plans?group=pending':{ plans:[] }, '/api/research?group=watching':researchData }
 vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(responses[String(input)] ?? {}), { status:String(input) === '/api/account' ? 500 : 200, headers:{ 'content-type':'application/json' } })))
 const { account, ledger } = await import('$lib/stores'); await Promise.all([account, ledger].map(resource => resource.load()))
 render(AccountView)
 expect(screen.getByRole('region', { name:'概览' })).toHaveTextContent('服务暂时不可用')
 expect(screen.getByRole('region', { name:'持仓' })).toHaveTextContent('服务暂时不可用')
 expect(screen.getByRole('region', { name:'成交' })).toHaveTextContent('AAPL')
 expect(screen.getByRole('region', { name:'出入金' })).toBeInTheDocument()
 expect(screen.getByRole('region', { name:'对账' })).toBeInTheDocument()
})
it('fetches expanded shared ledger and keeps its limit after global invalidation', async () => {
 const { ledgerData } = await import('./workbench-fixtures')
 const { invalidateAll } = await import('$lib/stores')
 await loadWorkbench({ '/api/ledger?limit=40':{ ...ledgerData, trades:Array.from({ length:40 }, (_, i) => ({ ...ledgerData.trades[0], id:i+1 })) } })
 render(AccountView)
 await fireEvent.click(screen.getByRole('button', { name:'显示更多' }))
 await waitFor(() => expect(screen.getByRole('table', { name:'成交流水' }).querySelectorAll('tbody > tr')).toHaveLength(40))
 const fetcher = vi.mocked(fetch); fetcher.mockClear(); invalidateAll()
 await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/ledger?limit=40', expect.anything()))
 await waitFor(() => expect(screen.getByRole('table', { name:'成交流水' }).querySelectorAll('tbody > tr')).toHaveLength(40))
 expect(fetcher.mock.calls.filter(([url]) => String(url).includes('limit=40'))).toHaveLength(1)
})
it('stops requesting more at the API cap of one hundred', async () => {
 const { ledgerData } = await import('./workbench-fixtures')
 const { ledgerForLimit } = await import('$lib/stores')
 const responses = Object.fromEntries([40,60,80,100].map(limit => [`/api/ledger?limit=${limit}`, { ...ledgerData, trades:Array.from({ length:limit }, (_, i) => ({ ...ledgerData.trades[0], id:i+1 })) }]))
 await loadWorkbench(responses)
 for (const limit of [40,60,80,100]) ledgerForLimit(limit).invalidate()
 render(AccountView)
 for (const limit of [40,60,80,100]) {
  const more = screen.getByRole('button', { name:'显示更多' }); expect(more).toBeVisible(); await fireEvent.click(more)
  await waitFor(() => expect(screen.getByRole('table', { name:'成交流水' }).querySelectorAll('tbody > tr')).toHaveLength(limit))
 }
 expect(screen.queryByRole('button', { name:'显示更多' })).toBeNull()
 expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('limit=120'))).toBe(false)
})
