import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte'
import Markers from './Markers.svelte'
import Account from '../../../routes/Account.svelte'
import { accountFixture, detailFixture, equityFixture, compareFixture, paperFixture } from './fixtures'

const chart = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }
vi.mock('echarts', () => ({ init: () => chart }))
const markers = [{ session: '2026-10-29', kind: 'feature' as const, label: '开启组合经理' }, { session: '2026-10-30', kind: 'version' as const, label: 'agent-portfolio-2' }, { session: '2026-10-30', kind: 'params' as const, label: '行业上限调整' }]
let fetcher: ReturnType<typeof vi.fn>
beforeEach(() => {
  chart.setOption.mockClear()
  fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/accounts?include_closed=true') return Response.json({ accounts: [accountFixture, paperFixture] })
    if (url === '/api/accounts/compare') return Response.json({ ...compareFixture, markers })
    if (url.endsWith('/equity')) return Response.json({ rows: [{ ...equityFixture, session: '2026-10-29' }, equityFixture], spy: [], markers })
    if (url.endsWith('/decisions')) return Response.json({ decisions: [{ id: 'd1', session: '2026-10-30', status: 'ok', status_reason: null, summary: '保持', agent_version: 'agent-portfolio-2' }] })
    if (url.endsWith('/orders')) return Response.json({ orders: [] })
    if (url.includes('/api/workbench/account/snapshots')) return Response.json({ snapshots: [], flows: [], params: {} })
    if (url.endsWith('/params') && init?.method === 'POST') return Response.json({ account: accountFixture })
    if (url.startsWith('/api/accounts/')) return Response.json({ ...detailFixture, account: url.endsWith('paper-1') ? paperFixture : accountFixture })
    throw new Error(`Unexpected ${url}`)
  })
  vi.stubGlobal('fetch', fetcher)
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('renders markLine data for three marker kinds and list below', async () => {
  render(Account, { sub: 'compare' })
  expect(await screen.findByRole('table', { name: '分界记录' })).toHaveTextContent('开启组合经理')
  await waitFor(() => expect(chart.setOption.mock.lastCall?.[0].series.some((s: any) => s.id === 'account-markers')).toBe(true))
  const markLine = chart.setOption.mock.lastCall![0].series.find((s: any) => s.id === 'account-markers').markLine
  expect(markLine.data.map((r: any) => [r.xAxis, r.name])).toEqual([['2026-10-29', '开启组合经理'], ['2026-10-30', 'agent-portfolio-2'], ['2026-10-30', '行业上限调整']])
  expect(new Set(markLine.data.map((r: any) => r.lineStyle.type)).size).toBe(3)
  expect(screen.getByRole('table', { name: '分界记录' })).toHaveTextContent('参数')
})
it('equity chart also carries markers and parameters validate sector limits', async () => {
  render(Account, { sub: accountFixture.id })
  expect(await screen.findByRole('table', { name: '分界记录' })).toHaveTextContent('agent-portfolio-2')
  expect(chart.setOption.mock.lastCall![0].series.find((s: any) => s.name === accountFixture.name).markLine.data).toHaveLength(3)
  await fireEvent.click(screen.getByRole('button', { name: '修改参数' }))
  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent('0.05–1.0，默认 0.35')
  expect(dialog).toHaveTextContent('1–10，默认 3')
  await fireEvent.input(within(dialog).getByLabelText('行业敞口上限'), { target: { value: '0.01' } })
  await fireEvent.input(within(dialog).getByLabelText('修改理由'), { target: { value: '调整风险' } })
  await fireEvent.click(within(dialog).getByRole('button', { name: '保存参数' }))
  expect(await screen.findByText('行业敞口上限须在 0.05–1.0 之间')).toBeInTheDocument()
  expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  await fireEvent.input(within(dialog).getByLabelText('行业敞口上限'), { target: { value: '0.4' } })
  await fireEvent.input(within(dialog).getByLabelText('每行业最多持仓数'), { target: { value: '4' } })
  await fireEvent.click(within(dialog).getByRole('button', { name: '保存参数' }))
  await waitFor(() => expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true))
  const body = JSON.parse(fetcher.mock.calls.find(([, init]) => init?.method === 'POST')![1]!.body as string)
  expect(body.params).toMatchObject({ sector_cap: '0.4', max_per_sector: 4 })
})
it('shows the exact agent version identifier on each decision', async () => {
  render(Account, { sub: 'paper-1/decisions' })
  expect(await screen.findByText('agent-portfolio-2')).toBeInTheDocument()
})
it('an old response with no markers explains that boundaries were not recorded', () => {
  render(Markers)
  expect(screen.getByText('接口尚未提供分界记录')).toBeInTheDocument()
})
