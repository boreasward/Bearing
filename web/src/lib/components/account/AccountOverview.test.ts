import { afterEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/svelte'
import AccountOverview from './AccountOverview.svelte'
import { detailFixture, equityFixture, paperFixture } from './fixtures'

const chart = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }
vi.mock('echarts', () => ({ init: () => chart }))
afterEach(() => vi.clearAllMocks())

const dayMetric = () => within(screen.getByText('今日盈亏').closest('[data-account-metric]') as HTMLElement)

it.each([
  { detail: { bars_missing: ['MU'] }, reason: '缺当日收盘 MU' },
  { detail: { stale: { MU: '2026-10-28' } }, reason: '沿用旧收盘 MU（2026-10-28）' },
])('withholds a recovered day PnL when the previous equity was incomplete: $reason', ({ detail, reason }) => {
  const previous = { ...equityFixture, session: '2026-10-29', equity: '99000', detail }
  const current = { ...equityFixture, equity: '100000', day_pnl: '1000.0100', detail: {} }
  render(AccountOverview, { detail: { ...detailFixture, equity: current }, history: { rows: [previous, current], spy: [] } })
  expect(dayMetric().queryByText('1000.0100')).toBeNull()
  expect(dayMetric().getByText(/2026-10-29/)).toHaveTextContent(reason)
})

it('shows the exact API day PnL after both dependent equity rows are verified complete', () => {
  const previous = { ...equityFixture, session: '2026-10-29', equity: '99000', detail: {} }
  const current = { ...equityFixture, equity: '100000', day_pnl: '1000.0100', detail: {} }
  render(AccountOverview, { detail: { ...detailFixture, equity: current }, history: { rows: [current, previous], spy: [] } })
  expect(dayMetric().getByText('1000.0100')).toBeInTheDocument()
})

it('does not validate an older day PnL with a newer equity version from the same session', () => {
  const current = { ...equityFixture, id: 21, day_pnl: '1000.0100', detail: {} }
  const previous = { ...equityFixture, id: 22, session: '2026-10-29', equity: '99999', detail: {} }
  const revised = { ...current, id: 23, computed_at: '2026-10-31T02:01:00Z', day_pnl: '1.0100' }
  render(AccountOverview, { detail: { ...detailFixture, equity: current }, history: { rows: [previous, revised], spy: [] } })
  expect(dayMetric().queryByText('1000.0100')).toBeNull()
  expect(dayMetric().queryByText('1.0100')).toBeNull()
  expect(dayMetric().getByText(/2026-10-30/)).toHaveTextContent('数据版本不同，刷新后核验')
})

it.each([
  { currentId: 21, historyId: '21' },
  { currentId: undefined, historyId: undefined },
  { currentId: 21, historyId: undefined },
  { currentId: undefined, historyId: '21' },
])('accepts matching equity versions without requiring numeric ID types: $currentId/$historyId', ({ currentId, historyId }) => {
  const current = { ...equityFixture, id: currentId, day_pnl: '1000.0100' }
  const previous = { ...equityFixture, session: '2026-10-29', equity: '99000' }
  render(AccountOverview, { detail: { ...detailFixture, equity: current }, history: { rows: [previous, { ...current, id: historyId }], spy: [] } })
  expect(dayMetric().getByText('1000.0100')).toBeInTheDocument()
  expect(dayMetric().queryByText(/数据版本不同/)).toBeNull()
})

it.each([
  { field: 'computed_at', change: { computed_at: '2026-10-31T02:01:00Z' } },
  { field: 'equity', change: { equity: '99999' } },
  { field: 'net_flows_cum', change: { net_flows_cum: '1000' } },
  { field: 'day_pnl', change: { day_pnl: '1.0100' } },
  { field: 'valuation quality', change: { detail: { bars_missing: ['MU'] } } },
])('requires matching $field before verifying a day PnL without row IDs', ({ change }) => {
  const current = { ...equityFixture, day_pnl: '1000.0100' }
  const previous = { ...equityFixture, session: '2026-10-29', equity: '99000' }
  render(AccountOverview, { detail: { ...detailFixture, equity: current }, history: { rows: [previous, { ...current, ...change }], spy: [] } })
  expect(dayMetric().queryByText('1000.0100')).toBeNull()
  expect(dayMetric().getByText(/2026-10-30/)).toHaveTextContent('数据版本不同，刷新后核验')
})

it.each([
  { history: null, historyError: '', reason: '正在读取权益历史' },
  { history: null, historyError: '历史读取失败', reason: '历史读取失败' },
  { history: { rows: [] as typeof equityFixture[], spy: [] }, historyError: '', reason: '未找到当日权益行' },
])('withholds unverified day PnL while $reason', ({ history, historyError, reason }) => {
  const current = { ...equityFixture, day_pnl: '1000.0100' }
  render(AccountOverview, { detail: { ...detailFixture, equity: current }, history, historyError })
  expect(dayMetric().queryByText('1000.0100')).toBeNull()
  expect(dayMetric().getByText(/2026-10-30/)).toHaveTextContent(reason)
})

it('explains the first trading day without treating the opening cash as a previous equity row', () => {
  const current = { ...equityFixture, session: '2026-11-02', day_pnl: '1000.0100' }
  const account = { ...detailFixture.account, opened_on: '2026-10-31', opening_session: '2026-11-02' }
  render(AccountOverview, { detail: { ...detailFixture, account, equity: current }, history: { rows: [current], spy: [] } })
  expect(dayMetric().queryByText('1000.0100')).toBeNull()
  expect(dayMetric().getByText(/首个交易日 2026-11-02/)).toHaveTextContent('无上一权益行')
})

it('does not assert a previous dependency when history starts after the first trading day', () => {
  const current = { ...equityFixture, day_pnl: '1000.0100' }
  render(AccountOverview, { detail: { ...detailFixture, equity: current }, history: { rows: [current], spy: [] } })
  expect(dayMetric().queryByText('1000.0100')).toBeNull()
  expect(dayMetric().getByText(/2026-10-30/)).toHaveTextContent('无法确认上一权益行')
})

it('normalizes own and overlay curves at their calendar-derived first trading day, keeping opening dates visible', async () => {
  const current = { ...equityFixture, session: '2026-11-02', equity: '100000' }
  const account = { ...detailFixture.account, opened_on: '2026-10-31', opening_session: '2026-11-02' }
  const overlay = { ...paperFixture, opened_on: '2026-12-25', opening_session: '2026-12-28' }
  render(AccountOverview, {
    detail: { ...detailFixture, account, equity: current },
    history: { rows: [current, { ...current, session: '2026-11-03', equity: '110000' }], spy: [] },
    otherHistories: [{ account: overlay, history: { rows: [
      { ...current, session: '2026-12-28' }, { ...current, session: '2026-12-29', equity: '120000' },
    ], spy: [] } }],
  })
  await fireEvent.click(screen.getByRole('button', { name: '叠加其他账户' }))
  const series = chart.setOption.mock.lastCall?.[0].series
  expect(series.find((item: { name: string }) => item.name === account.name).data).toEqual([['2026-11-02', 100], ['2026-11-03', 110]])
  expect(series.find((item: { name: string }) => item.name === overlay.name).data).toEqual([['2026-12-28', 100], ['2026-12-29', 120]])
  expect(screen.getByText(/开账日：2026-10-31/)).toHaveTextContent('首个交易日：2026-11-02')
  expect(chart.setOption.mock.lastCall?.[0].yAxis.name).toBe('首个交易日 = 100')
})

it('shows six metrics with exact money strings, versions, cutoff and incomplete valuation', () => {
  const { container } = render(AccountOverview, { detail: { ...detailFixture, equity: { ...equityFixture, detail: { bars_missing: ['MU'], stale: { MU: '2026-10-29' } } } }, history: { rows: [equityFixture], spy: [] }, monthReturn: '+0.10%', spyReturn: '+0.05%' })
  expect(container.querySelectorAll('[data-account-metric]')).toHaveLength(6)
  expect(screen.getByText('99000.0100')).toBeInTheDocument()
  expect(screen.queryByText('100100.0100')).toBeNull()
  expect(screen.getByText(/^2026-10-30 · 估值不完整：/)).toHaveTextContent('2026-10-29')
  expect(container).toHaveTextContent('同月 SPY +0.05%')
  expect(container).toHaveTextContent('account-1')
  expect(container).toHaveTextContent('截止 2026-10-31 10:00:00 北京')
  expect(container).toHaveTextContent('首个交易日 = 100')
})
it('marks historical overlay gaps and refuses to normalize an incomplete opening value', async () => {
  render(AccountOverview, {
    detail: detailFixture,
    history: { rows: [{ ...equityFixture, session: '2026-10-29' }, equityFixture], spy: [] },
    otherHistories: [{ account: paperFixture, history: { rows: [
      { ...equityFixture, session: '2026-10-29', equity: '0', detail: { bars_missing: ['NVDA'] } },
      { ...equityFixture, session: '2026-10-30' },
    ], spy: [] } }],
  })
  await fireEvent.click(screen.getByRole('button', { name: '叠加其他账户' }))
  expect(screen.getByText(/Agent 账户.*2026-10-29.*NVDA/)).toBeInTheDocument()
  expect(screen.getByText(/Agent 账户.*首个交易日.*未绘制/)).toBeInTheDocument()
  const series = chart.setOption.mock.lastCall?.[0].series
  expect(series.find((item: { name: string }) => item.name === 'Agent 账户').data).toEqual([])
})
it('shows a failed curve request as a failure instead of indefinite loading', () => {
  render(AccountOverview, { detail: detailFixture, history: null, historyError: '权益曲线暂不可用' })
  expect(screen.getByText('权益曲线暂不可用')).toBeInTheDocument()
  expect(screen.queryByLabelText('加载中')).toBeNull()
})
