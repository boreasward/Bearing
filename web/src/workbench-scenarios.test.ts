import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte'
import App from './app.svelte'
import { row as planRow } from './lib/plan-table-fixtures'
import { REAL_ACCOUNT_ID } from './lib/accounts'
import { accountFixture, detailFixture } from './lib/components/account/fixtures'

// In-memory stand-in for the workbench API; SYNTH and all numbers are synthetic.
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const market = { id: 'r1', session: '2026-09-01', calc_version: 'regime-1', label: 'risk_off',
  breadth: { tickers: 27, eligible_50: 27, above_50: 27, pct_50: 1, eligible_200: 27, above_200: 20, pct_200: 0.74,
             eligible_52w: 27, new_highs: 5, new_lows: 0 },
  sectors: { SMH: null, XLK: null, QQQ: null }, macro: { BAMLH0A0HYM2: null, T10Y2Y: null },
  inputs: { macro_status: { BAMLH0A0HYM2: 'not_configured', T10Y2Y: 'not_configured' } }, created_at: '2026-09-01T22:30:00Z' }
const synth = () => planRow({
  row_id: 'chain:SYNTH:2026-09-01', ticker: 'SYNTH',
  chain: { signal_date: '2026-09-01', version_no: 0, head_plan_id: 'ap1', state: 'entry', label: 'SYNTH·09-01 第 0 版', retriggers: [] },
  plan: { ...planRow().plan!, plan_id: 'ap1', entry_low: '123.20', entry_high: '126.30', stop: '117.30', target1: '139.60' },
  notes: [], status: { label: '入场期', execution: 'pending' },
})

let trades: any[]
let tradeBodies: any[]
beforeEach(() => {
  trades = []
  tradeBodies = []
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} })
  vi.stubGlobal('crypto', { randomUUID: () => 'req-scenario' })
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    url = String(url)
    if (url === '/api/accounts?include_closed=true') return json({ accounts: [accountFixture] })
    if (url === '/api/accounts/compare') return json({ columns: [], rows: [], cutoff_at: null, calc_versions: ['account-1'] })
    if (url === `/api/accounts/${REAL_ACCOUNT_ID}`) return json({ ...detailFixture, positions: [], closed_positions: [] })
    if (url === `/api/accounts/${REAL_ACCOUNT_ID}/trades`) return json({ trades })
    if (url === `/api/accounts/${REAL_ACCOUNT_ID}/equity`) return json({ rows: [], spy: [] })
    if (url.startsWith('/api/workbench/account/snapshots')) return json({ snapshots: [], flows: [], params: accountFixture.params })
    if (url === '/api/workbench/today') return json({ session: '2026-09-01', market, screen: null })
    if (url.startsWith('/api/workbench/plan-table'))
      return json({ session: '2026-09-01', groups: { entry: [synth()], holding: [], ended: [] }, other_units: [], counts: {}, errors: {} })
    if (url.startsWith('/api/workbench/trades/linkable-plans'))
      return json({ ticker: 'SYNTH', plans: [{ plan_id: 'ap1', label: 'Agent SYNTH·09-01 第 0 版（入场期，有效至 11-04）', kind: 'agent' }], default_plan_id: 'ap1' })
    if (url === '/api/workbench/trades' && init?.method === 'POST') {
      const body = JSON.parse(String(init.body))
      tradeBodies.push(body)
      trades.push({ id: 't1', ...body, fee: Number(body.fee), quantity: Number(body.quantity), price: Number(body.price),
                    decision_id: null, recorded_at: '2026-09-02T01:00:00Z', voided_at: null, void_reason: null,
                    plan_label: 'Agent SYNTH·09-01 第 0 版' })
      return json({ status: 'created', record: trades[0] })
    }
    if (url === '/api/workbench/trades') return json({ trades })
    if (url.startsWith('/api/workbench/review/trades')) return json({ session: '2026-09-01', calc_version: 'review-1', closed: [], open: [] })
    if (url.startsWith('/api/workbench/review/shadow'))
      return json({ session: '2026-09-22', since: '2026-06-24', calc_version: 'review-1', units: [
        { id: 'd1', ticker: 'SYNTH', signal_date: '2026-09-01', proposed_action: 'buy', final_action: 'ignore', decision: 'reject',
          reason: '旧决定', decided_at: '2026-09-02T01:00:00Z', supersedes_id: null, rule_ids: ['trend_reclaim'],
          outcome: { status: 'entered', entry_price: 100, windows: {
            '5': { status: 'mature', return: 0.02, spy_return: 0, excess: 0.02 }, '10': { status: 'immature' }, '20': { status: 'immature' } } } }] })
    return json({})
  }))
})
afterEach(() => vi.unstubAllGlobals())

it('today shows the plan table with the executable plan and no way to decide', async () => {
  history.replaceState(null, '', '#today')
  render(App)
  expect(await screen.findByRole('heading', { name: '入场期（可执行）1' })).toBeInTheDocument()
  const table = screen.getByRole('table', { name: '入场期计划' })
  expect(table).toHaveTextContent('SYNTH·09-01 第 0 版')
  expect(table).toHaveTextContent('区间限价 123.20–126.30')
  expect(table).toHaveTextContent('开在 95.10–97.30：开盘买入 120 股')
  expect(screen.queryByRole('button', { name: '提交决定' })).toBeNull()
  expect(screen.queryByLabelText('理由')).toBeNull()
  expect(screen.queryByLabelText('否决')).toBeNull()
})

it('records a trade against the plan the table showed, and the list names it', async () => {
  history.replaceState(null, '', '#review/trades')
  render(App)
  await screen.findByText('成交记录')
  expect(location.hash).toBe(`#account/${REAL_ACCOUNT_ID}/trades`)
  await fireEvent.click(screen.getByRole('link', { name: '概览' }))
  await screen.findByRole('heading', { name: '录入成交' })
  await fireEvent.input(screen.getByLabelText('代码'), { target: { value: 'synth' } })
  await fireEvent.input(screen.getByLabelText('数量'), { target: { value: '10' } })
  await fireEvent.input(screen.getByLabelText('成交价'), { target: { value: '124' } })
  await fireEvent.input(screen.getByLabelText('成交时间'), { target: { value: '2026-09-02T10:15' } })
  await waitFor(() => expect((screen.getByLabelText('关联计划') as HTMLSelectElement).value).toBe('ap1'))
  await fireEvent.click(screen.getByRole('button', { name: '记录成交' }))
  await waitFor(() => expect(tradeBodies).toHaveLength(1))
  expect(tradeBodies[0]).toMatchObject({ account_id: REAL_ACCOUNT_ID, ticker: 'SYNTH', plan_id: 'ap1', request_id: 'req-scenario' })
  expect('decision_id' in tradeBodies[0]).toBe(false)
  await fireEvent.click(screen.getByRole('link', { name: '成交' }))
  const list = await screen.findByRole('table', { name: '账户成交' })
  expect(within(list).getByRole('link', { name: 'Agent SYNTH·09-01 第 0 版' })).toHaveAttribute('href', '#plan/ap1')
})

it('an old decision is still readable in the shadow review', async () => {
  history.replaceState(null, '', '#review/shadow')
  render(App)
  expect(await screen.findByText('+2.00%')).toBeInTheDocument()
  expect(screen.getByText(/否决/)).toBeInTheDocument()
  await waitFor(() => expect(screen.getAllByText('未成熟')).toHaveLength(2))
})

it('shows the market context card on today', async () => {
  history.replaceState(null, '', '#today')
  render(App)
  expect(await screen.findByText(/市场环境/)).toBeTruthy()
  expect(screen.getByText('防守')).toBeTruthy()
  expect(screen.getByText(/站上 MA50/)).toHaveTextContent(/MA200 74\.0%/)
  expect(screen.getByText('FRED 未配置')).toBeTruthy()
})

it('expanding a row shows the baseline and the note box, still without a decision form', async () => {
  history.replaceState(null, '', '#today')
  render(App)
  await fireEvent.click(await screen.findByRole('button', { name: '展开 SYNTH' }))
  const detail = await screen.findByTestId('row-detail')
  expect(within(detail).getByTestId('rule-baseline')).toHaveTextContent('规则基线 plan-2')
  expect(within(detail).getByRole('region', { name: '备注' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '提交决定' })).toBeNull()
})
