import { afterEach, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/svelte'
import PositionsTable from './PositionsTable.svelte'
import { positionFixture } from './fixtures'

afterEach(() => vi.unstubAllGlobals())
it('shows exact amounts and the frozen plan chain', () => {
  render(PositionsTable, { positions: [positionFixture] })
  expect(screen.getByRole('table', { name: '账户持仓' })).toHaveTextContent('100.0100')
  expect(screen.getByRole('link', { name: 'Agent MU 第 1 版' })).toHaveAttribute('href', '#plan/p1')
  expect(screen.getByText('2 / 10')).toBeInTheDocument()
})
it('turns rows into labelled cards on narrow screens', () => {
  vi.stubGlobal('matchMedia', (media: string) => ({ media, matches: true, addEventListener() {}, removeEventListener() {} }))
  render(PositionsTable, { positions: [positionFixture] })
  expect(screen.getByRole('list', { name: '账户持仓' })).toHaveTextContent('距止损')
  expect(screen.queryByRole('table')).toBeNull()
})
it('explains an empty ledger position block', () => {
  render(PositionsTable, { positions: [] })
  expect(screen.getByText('无持仓')).toBeInTheDocument()
})
it('marks stale and missing quotes with their date and never presents unsupported zero valuations as complete', () => {
  render(PositionsTable, {
    positions: [{ ...positionFixture, last_close: null, market_value: '0', unrealized_pnl: '0', r_now: '0' }],
    valuationSession: '2026-10-30', valuationDetail: { bars_missing: ['MU'] },
  })
  expect(screen.getByText(/2026-10-30.*MU.*缺当日收盘/)).toBeInTheDocument()
  expect(screen.getByRole('table', { name: '账户持仓' })).toHaveTextContent('无可用收盘价')
  expect(screen.queryByRole('cell', { name: '0' })).toBeNull()
})
