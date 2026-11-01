import { afterEach, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/svelte'
import CompareTable from './CompareTable.svelte'
import { compareFixture } from './fixtures'

afterEach(() => vi.unstubAllGlobals())

const narrowScreen = () => vi.stubGlobal('matchMedia', (media: string) => ({ matches: true, media, addEventListener: () => {}, removeEventListener: () => {} }))

it('presents one readable card per metric on a narrow screen with named account values, notes and SPY', () => {
  narrowScreen()
  const view = { ...compareFixture, columns: compareFixture.columns.map(column => ({ ...column, name: `${column.name} · 验证数据` })), rows: compareFixture.rows.map(row => row.key === 'month_return'
    ? { ...row, values: [{ text: '—', value: null, note: '缺少区间端点' }, row.values[1]] } : row) }
  const { container } = render(CompareTable, { view })
  expect(screen.queryByRole('table')).toBeNull()
  const cards = within(screen.getByRole('list', { name: '账户指标对比' })).getAllByRole('listitem')
  expect(cards).toHaveLength(view.rows.length)
  for (const [index, row] of view.rows.entries()) {
    expect(within(cards[index]).getByText(row.label)).toBeInTheDocument()
    for (const account of view.columns) expect(within(cards[index]).getByText(account.name)).toBeInTheDocument()
    expect(within(cards[index]).getByText('SPY')).toBeInTheDocument()
  }
  expect(within(cards[0]).getByText('— · 缺少区间端点')).toBeInTheDocument()
  expect(within(cards[0]).getByText('+0.20%')).toBeInTheDocument()
  expect(within(cards[0]).getByText('+0.05%')).toBeInTheDocument()
  expect(within(cards[1]).getByText('样本 < 5')).toBeInTheDocument()
  expect(within(cards[1]).getByText('+0.50R')).toBeInTheDocument()
  expect(container).toHaveTextContent('account-1')
  expect(container).toHaveTextContent('paper-fill-1')
  expect(container).toHaveTextContent('截止 2026-10-31 10:00:00 北京')
})

it('preserves incomplete-valuation warnings and safe values in narrow metric cards', () => {
  narrowScreen()
  render(CompareTable, { view: compareFixture, valuationWarnings: { [compareFixture.columns[0].account_id]: ['2026-10-30：估值不完整：缺当日收盘 MU'] } })
  expect(screen.getByText(/真实账户.*2026-10-30.*MU/)).toBeInTheDocument()
  const cards = within(screen.getByRole('list', { name: '账户指标对比' })).getAllByRole('listitem')
  expect(within(cards[0]).getByText('本月收益率')).toBeInTheDocument()
  expect(within(cards[0]).getByText('估值不完整')).toBeInTheDocument()
  expect(within(cards[0]).queryByText('+0.10%')).toBeNull()
  expect(within(cards[0]).getByText('+0.20%')).toBeInTheDocument()
  expect(within(cards[0]).getByText('+0.05%')).toBeInTheDocument()
  expect(within(cards[1]).getByText('样本 < 5')).toBeInTheDocument()
})

it('keeps account order and leaves low-sample mean R empty with its reason', () => {
  render(CompareTable, { view: compareFixture })
  expect(screen.getAllByRole('columnheader').map(h => h.textContent?.trim())).toEqual(['指标', '真实账户', 'Agent 账户', 'SPY'])
  expect(screen.getByText('样本 < 5')).toBeInTheDocument()
  expect(screen.getByText('同一截止时间、同一口径；各账户首个交易日可能不同，曲线分别归一到首个交易日 = 100。')).toBeInTheDocument()
  expect(screen.getByText(/首个交易日权益不可用时不绘制该账户曲线/)).toBeInTheDocument()
  expect(screen.queryByText(/最好|排名/)).toBeNull()
})
it('marks the affected account/date and withholds valuation-derived statistics based on incomplete prices', () => {
  render(CompareTable, { view: compareFixture, valuationWarnings: { [compareFixture.columns[0].account_id]: ['2026-10-30：估值不完整：缺当日收盘 MU'] } })
  expect(screen.getByText(/真实账户.*2026-10-30.*MU/)).toBeInTheDocument()
  expect(screen.getByRole('table', { name: '账户指标对比' })).toHaveTextContent('估值不完整')
  expect(screen.queryByText('+0.10%')).toBeNull()
  expect(screen.getByText('+0.20%')).toBeInTheDocument()
})
