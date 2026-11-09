import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte'
import AccountView from './AccountView.svelte'
import { loadWorkbench } from './workbench-fixtures'
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('renders AccountCard and three ledgers with void reasons and paging', async () => {
 await loadWorkbench(); const { container } = render(AccountView)
 for (const name of ['成交流水','出入金流水','对账流水']) expect(screen.getByRole('table', { name })).toBeInTheDocument()
 expect(container.querySelector('[data-muted="true"]')).toHaveTextContent('录错股数')
 expect(screen.getByRole('button', { name:/显示更多/ })).toBeInTheDocument()
})
it.each([['记出入金','金额'],['对账','券商总资产'],['记成交','股票']])('opens existing %s form', async (name, label) => {
 await loadWorkbench(); render(AccountView)
 await fireEvent.click(screen.getByRole('button', { name }))
 expect(await screen.findByLabelText(label)).toBeInTheDocument()
})
it('fetches expanded shared ledger and keeps its limit after global invalidation', async () => {
 const { ledgerData } = await import('./workbench-fixtures')
 const { invalidateAll } = await import('$lib/stores')
 const { waitFor } = await import('@testing-library/svelte')
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
 const { waitFor } = await import('@testing-library/svelte')
 const responses = Object.fromEntries([40,60,80,100].map(limit => [`/api/ledger?limit=${limit}`, { ...ledgerData, trades:Array.from({ length:limit }, (_, i) => ({ ...ledgerData.trades[0], id:i+1 })) }]))
 await loadWorkbench(responses)
 for (const limit of [40,60,80,100]) ledgerForLimit(limit).invalidate()
 render(AccountView)
 for (const limit of [40,60,80,100]) {
  await fireEvent.click(screen.getByRole('button', { name:'显示更多' }))
  await waitFor(() => expect(screen.getByRole('table', { name:'成交流水' }).querySelectorAll('tbody > tr')).toHaveLength(limit))
 }
 expect(screen.queryByRole('button', { name:'显示更多' })).toBeNull()
 expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('limit=120'))).toBe(false)
})
