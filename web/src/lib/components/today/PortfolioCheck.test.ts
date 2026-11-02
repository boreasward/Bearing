import { afterEach, expect, it } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/svelte'
import PortfolioCheck from './PortfolioCheck.svelte'
import type { PortfolioCheckView } from '$lib/workbench'
const check: PortfolioCheckView = {
  status: 'ready', reason: null, session: '2026-10-30', cutoff_at: '2026-10-30T21:00:00Z',
  result: { version: 'portfolio-check-1', exposure_by_sector: { technology: { held_value: '100', pending_value: '0', candidate_value: '200', share_of_equity: '0.3' } },
    verdicts: [{ plan_id: 'p2', ticker: 'MSFT', group: 'technology', verdict: 'blocked', reasons: ['sector_cap'], order: null }, { plan_id: 'p1', ticker: 'NVDA', group: 'technology', verdict: 'ok', reasons: [], order: 1 }, { plan_id: 'p3', ticker: 'AMD', group: null, verdict: 'deferred', reasons: ['insufficient_cash'], order: null }],
    conflicts: [{ group: 'technology', plan_ids: ['p1', 'p2'], rule: 'max_per_sector' }], order: ['p1'], params_used: { sector_cap: '0.35', max_per_sector: 3 } },
  commentary: { summary: '优先处理较早的信号', ranking: [{ plan_id: 'p1', rank: 1, reason: '证据较充分' }], tradeoffs: [{ text: '集中风险较高', refs: ['fact:portfolio_check'] }], hold_actions: [{ ticker: 'AAPL', suggestion: 'keep', reason: '维持现有仓位' }] },
}
afterEach(cleanup)
it('not_computed shows reason', () => {
  render(PortfolioCheck, { check: { ...check, status: 'not_computed', result: null, commentary: null, reason: '功能已关闭' } })
  expect(screen.getByText('功能已关闭')).toBeInTheDocument()
  expect(screen.queryByRole('table')).toBeNull()
})
it('ready renders ordered table with verdict pills, exposure bars and conflicts', () => {
  render(PortfolioCheck, { check })
  const table = screen.getByRole('table', { name: '组合计划判定' })
  const rows = within(table).getAllByRole('row')
  expect(rows[1]).toHaveTextContent('NVDA')
  expect(rows[2]).toHaveTextContent('被拦截')
  expect(rows[3]).toHaveTextContent('暂缓')
  expect(screen.getByRole('meter', { name: 'technology 行业敞口' })).toHaveAttribute('value', '0.3')
  expect(screen.getByRole('region', { name: '互斥组' })).toHaveTextContent('超过同行业标的数量上限')
  expect(screen.getByText('优先处理较早的信号')).toBeInTheDocument()
})
it('degraded shows hard results only', () => {
  render(PortfolioCheck, { check: { ...check, status: 'degraded', reason: '模型不可用' } })
  expect(screen.getByText('模型不可用；只有硬结果')).toBeInTheDocument()
  expect(screen.getByRole('table', { name: '组合计划判定' })).toBeInTheDocument()
  expect(screen.queryByText('优先处理较早的信号')).toBeNull()
})
