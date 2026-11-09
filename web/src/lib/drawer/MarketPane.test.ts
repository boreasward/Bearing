import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/svelte'
import MarketPane from './MarketPane.svelte'
import { loadWorkbench, marketData } from '$lib/views/workbench-fixtures'
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('renders regime three lines, all ETFs and watch with callback', async () => {
 await loadWorkbench({ '/api/market':{ ...marketData, etf_table:Array.from({ length:9 }, (_, i) => ({ ...marketData.etf_table[0], etf:`ETF${i}` })) } })
 const onBring = vi.fn(); render(MarketPane, { onBring })
 expect(screen.getByText('中性')).toBeInTheDocument(); expect(screen.getByText(/52 周新高/)).toBeInTheDocument(); expect(screen.getByText(/50 日线上方/)).toBeInTheDocument()
 expect(screen.getByText('ETF8')).toBeInTheDocument(); expect(screen.getByText('MU')).toBeInTheDocument()
 await fireEvent.click(within(screen.getByText('MU').closest('tr')!).getByRole('button', { name:'带进对话' })); expect(onBring).toHaveBeenCalledWith({ kind:'research', ticker:'MU', id:1 })
})
