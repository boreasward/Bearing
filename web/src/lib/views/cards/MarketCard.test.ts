import { afterEach, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/svelte'
import MarketCard from './MarketCard.svelte'
import { fixture, meta } from '../test-fixtures'
import type { MarketData } from '$lib/stores/types'
const data = { ...(fixture('get_market') as MarketData), watch: [{ ticker: 'MU', last_close: null, prev_close: null, change_pct: null, source: 'research' as const, plan_id: null, research_id: 1 }] }
afterEach(cleanup)
it.each([['risk_on','进攻'],['neutral','中性'],['risk_off','防守'],['insufficient','样本不足']])('regime %s', (label, text) => {
 render(MarketCard, { data: { ...data, label }, meta }); expect(screen.getByText(text)).toBeInTheDocument()
})
it.each([['strong','强'],['mixed','混合'],['weak','弱']])('ETF %s', (state, text) => {
 render(MarketCard, { data: { ...data, etf_table: [{ ...data.etf_table[0], state }] }, meta }); expect(screen.getByText(text)).toBeInTheDocument()
})
it('full renders macro, watch links and missing changes', () => {
 const { container } = render(MarketCard, { data, meta, full: true })
 expect(screen.getByText('宏观')).toBeInTheDocument(); expect(screen.getByText('关注的标的')).toBeInTheDocument()
 expect(container).toHaveTextContent('DGS10'); expect(container).toHaveTextContent('没算')
 expect(screen.getByRole('link', { name: '研究 #1' })).toHaveAttribute('href', '#research/1')
})
it('defaults to six ETFs and omits absent calculation version', () => {
 const { container } = render(MarketCard, { data: { ...data, calc_version: null, etf_table: Array.from({ length: 9 }, (_, n) => ({ ...data.etf_table[0], etf: `ETF${n}` })) }, meta })
 expect(screen.queryByText('ETF6')).toBeNull(); expect(screen.queryByText('宏观')).toBeNull()
 expect(container.querySelector('[data-slot="source-line"]')).toHaveTextContent('call:7')
 expect(container.querySelector('[data-slot="source-line"]')).not.toHaveTextContent('regime-v3')
})
it('watch percentage points are not multiplied by one hundred', () => {
 const { container } = render(MarketCard, { data:{ ...data, watch:[{ ...data.watch[0], change_pct:'-3.80' }] }, full:true })
 expect(container).toHaveTextContent('-3.80%'); expect(container).not.toHaveTextContent('-380.00%')
})
it('pane independently renders all rows and sorts RS20 without mutating snapshot', async () => {
 const { fireEvent, within } = await import('@testing-library/svelte')
 const { vi } = await import('vitest')
 const onBring = vi.fn()
 const etf_table = Array.from({ length:9 }, (_, i) => ({ ...data.etf_table[0], etf:`ETF${i}`, rs20:i / 100 }))
 const { container } = render(MarketCard, { data:{ ...data, etf_table }, pane:true, onBring })
 expect(screen.queryByText('宏观')).toBeNull(); expect(screen.queryByText('领涨')).toBeNull()
 expect(within(screen.getByRole('table', { name:'板块 ETF' })).getAllByRole('row')[1]).toHaveTextContent('ETF8')
 expect(etf_table[0].etf).toBe('ETF0')
 await fireEvent.click(within(screen.getByText('MU').closest('tr')!).getByRole('button', { name:'带进对话' }))
 expect(onBring).toHaveBeenCalledOnce(); expect(container).toHaveTextContent('50 日线上方占比')
})
