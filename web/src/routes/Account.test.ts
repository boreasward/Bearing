import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte'
import Account from './Account.svelte'
import { REAL_ACCOUNT_ID } from '../lib/accounts'
import { accountFixture, paperFixture, detailFixture, equityFixture, compareFixture } from '../lib/components/account/fixtures'

const chart = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }
vi.mock('echarts', () => ({ init: () => chart }))
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const trade = { id: 't1', ticker: 'MU', side: 'buy', quantity: '10', price: '100.0100', fee: '1.00', executed_at: '2026-10-30T14:00:00Z', recorded_by: 'paper', plan_id: 'p1', plan_label: 'Agent MU 第 1 版', detail: { fill_case: 'gap_up_limit' }, voided_at: null }
let calls: { url: string; init?: RequestInit }[]
let respond: (url: string, init?: RequestInit) => Promise<Response>
let selected = accountFixture
const decision = { id: 'd1', session: '2026-10-30', status: 'skipped', status_reason: '当日行情缺失', summary: '保持现金', orders_placed: 0, orders_rejected: 0 }
beforeEach(() => {
  calls = []; selected = { ...accountFixture }; chart.setOption.mockClear()
  respond = async (url, init) => {
    if (url === '/api/accounts?include_closed=true') return json({ accounts: selected.kind === 'real' ? [selected, paperFixture] : [accountFixture, selected] })
    if (url.startsWith('/api/accounts/compare')) return json(compareFixture)
    if (url.includes('/equity')) return json({ rows: [equityFixture], spy: [{ session: '2026-10-30', close: '500.00' }] })
    if (url.startsWith('/api/accounts/') && url.includes('/decisions?')) return json({ decision: { ...decision, evidence: { account: { cash: '100000' } }, output: {}, holds: ['MU 持有'], orders: [] } })
    if (url.startsWith('/api/accounts/') && url.endsWith('/decisions')) return json({ decisions: [decision] })
    if (url.startsWith('/api/accounts/') && url.endsWith('/orders')) return json({ orders: [] })
    if (url.startsWith('/api/accounts/') && url.endsWith('/trades')) return json({ trades: [trade] })
    if (/^\/api\/accounts\/[^/]+$/.test(url)) return json({ ...detailFixture, account: selected, equity: selected.opened ? equityFixture : null, forms: { can_record_trades: selected.kind === 'real' && selected.status === 'active' }, closed_positions: [{ ticker: 'NVDA', opened: '2026-10-01', closed: '2026-10-20', holding_sessions: 10, realized_pnl: '100.0100', return: 0.01, spy_return: 0.005 }] })
    if (url.startsWith('/api/workbench/account/snapshots') && init?.method !== 'POST') return json({ snapshots: [], flows: [], params: selected.params })
    if (url.startsWith('/api/workbench/trades/linkable-plans')) return json({ plans: [{ plan_id: 'p1', label: 'Agent MU 第 1 版' }], default_plan_id: 'p1' })
    return json({})
  }
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => { calls.push({ url: String(url), init }); return respond(String(url), init) }))
  vi.stubGlobal('crypto', { randomUUID: () => 'request-1' })
})
afterEach(() => vi.unstubAllGlobals())
const posts = (url: string) => calls.filter(c => c.url === url && c.init?.method === 'POST')
const body = (url: string) => JSON.parse(String(posts(url)[0]?.init?.body))

describe('account route', () => {
  it('renders real overview and keeps the account selection locked for writes', async () => {
    render(Account, { sub: REAL_ACCOUNT_ID })
    expect(await screen.findByRole('heading', { name: '账户概览' })).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: '账户页签' })
    expect(within(nav).getAllByRole('link').map(a => a.textContent?.trim())).toEqual(['概览', '持仓', '成交', '对比'])
    expect(screen.getByRole('combobox', { name: '录入账户' })).toBeDisabled()
    expect(screen.getByRole('heading', { name: '录入成交' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '录入出入金' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '录入资金快照' })).toBeInTheDocument()
    expect(screen.getByText('对账一致')).toBeInTheDocument()
    expect(calls.some(c => c.url === `/api/workbench/account/snapshots?account_id=${REAL_ACCOUNT_ID}`)).toBe(true)
  })
  it.each([
    { note: 'snapshot', text: '+0.10%', explanation: '来源：券商资金快照' },
    { note: 'ledger', text: '+0.10%', explanation: '来源：账户权益账本' },
    { note: '缺少区间端点', text: '—', explanation: '缺少区间端点' },
  ])('preserves the monthly return source or missing-endpoint reason in overview: $note', async ({ note, text, explanation }) => {
    const base = respond
    respond = async (url, init) => {
      if (url === '/api/accounts/compare') return json({ ...compareFixture, rows: compareFixture.rows.map(row => row.key === 'month_return'
        ? { ...row, values: [{ ...row.values[0], text, note }, row.values[1]] } : row) })
      if (url === `/api/accounts/${REAL_ACCOUNT_ID}/equity`) return json({ rows: [{ ...equityFixture, session: '2026-10-29' }, equityFixture], spy: [] })
      return base(url, init)
    }
    render(Account, { sub: REAL_ACCOUNT_ID })
    const metric = within((await screen.findByText('本月收益率')).closest('[data-account-metric]') as HTMLElement)
    expect(await metric.findByText(explanation)).toBeInTheDocument()
    expect(metric.getByText(text, { exact: true })).toBeInTheDocument()
    if (note === 'snapshot' || note === 'ledger') expect(metric.queryByText(note, { exact: true })).toBeNull()
  })
  it('guides an unopened real account while preserving permitted recording forms', async () => {
    selected = { ...accountFixture, opened: false, opened_on: null, opening_cash: null, latest: null }
    render(Account, { sub: REAL_ACCOUNT_ID })
    expect(await screen.findByText('真实账户尚未开账，请先核对期初现金与已有持仓。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '记录成交' })).toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: '开账' }))
    expect(await screen.findByRole('dialog', { name: '真实账户开账' })).toBeInTheDocument()
  })
  it('keeps unopened-account trades, holdings, old snapshots and flows accessible', async () => {
    selected = { ...accountFixture, opened: false, opened_on: null, opening_cash: null, latest: null }
    const base = respond
    respond = async (url, init) => url.startsWith('/api/workbench/account/snapshots') && init?.method !== 'POST'
      ? json({ snapshots: [{ id: 'old-snapshot', as_of: '2026-10-29', equity: '123456.0100', recorded_at: '2026-10-29T14:00:00Z' }], flows: [{ id: 'old-flow', flow_date: '2026-10-29', amount: '5000.0100', voided_at: null }], params: selected.params }) : base(url, init)
    const view = render(Account, { sub: `${REAL_ACCOUNT_ID}/trades` })
    expect(await screen.findByRole('table', { name: '账户成交' })).toHaveTextContent('100.0100')
    expect(screen.queryByText('真实账户尚未开账，请先核对期初现金与已有持仓。')).toBeNull()
    await view.rerender({ sub: `${REAL_ACCOUNT_ID}/positions` })
    expect(await screen.findByRole('table', { name: '账户持仓' })).toHaveTextContent('MU')
    expect(screen.getByRole('table', { name: '已平仓批次' })).toHaveTextContent('NVDA')
    await view.rerender({ sub: REAL_ACCOUNT_ID })
    expect(await screen.findByText(/123456.0100/)).toBeInTheDocument()
    expect(screen.getByText(/5000.0100/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '录入资金快照' })).toBeInTheDocument()
    await fireEvent.input(screen.getByLabelText('出入金日期'), { target: { value: '2026-10-30' } })
    await fireEvent.input(screen.getByLabelText('金额'), { target: { value: '5.00' } })
    await fireEvent.click(screen.getByRole('button', { name: '录入出入金' }))
    await waitFor(() => expect(posts('/api/workbench/account/flows')).toHaveLength(1))
    expect(body('/api/workbench/account/flows')).toMatchObject({ account_id: REAL_ACCOUNT_ID, amount: '5.00' })
  })
  it('merges SPY across accounts even if the first unopened account has no history, and marks incomplete history', async () => {
    const third = { ...paperFixture, id: 'paper-2', name: '第二模拟账户' }
    const unopened = { ...accountFixture, opened: false, opened_on: null, latest: null }
    const first = { ...equityFixture, session: '2026-10-29', detail: {} }
    const stale = { ...equityFixture, session: '2026-10-30', equity: '0', detail: { bars_missing: ['MU'], stale: { MU: '2026-10-29' } } }
    const base = respond
    respond = async (url, init) => {
      if (url === '/api/accounts?include_closed=true') return json({ accounts: [unopened, paperFixture, third] })
      if (url === '/api/accounts/compare') return json({ ...compareFixture, columns: [...compareFixture.columns, { account_id: third.id, name: third.name, strategy: third.strategy }] })
      if (url === `/api/accounts/${REAL_ACCOUNT_ID}/equity`) return json({ rows: [], spy: [] })
      if (url === '/api/accounts/paper-1/equity') return json({ rows: [first, stale], spy: [{ session: '2026-10-30', close: '500' }, { session: '2026-11-02', close: '510' }] })
      if (url === '/api/accounts/paper-2/equity') return json({ rows: [first], spy: [{ session: '2026-10-29', close: '490' }, { session: '2026-10-30', close: '500' }] })
      return base(url, init)
    }
    render(Account, { sub: 'compare' })
    await screen.findByRole('table', { name: '账户指标对比' })
    expect(screen.getAllByText(/Agent 账户.*2026-10-30.*MU/).length).toBeGreaterThan(0)
    const series = chart.setOption.mock.lastCall?.[0].series
    expect(series.find((item: { name: string }) => item.name === 'SPY').data.map((point: [string, number]) => point[0])).toEqual(['2026-10-29', '2026-10-30', '2026-11-02'])
    expect(series.find((item: { name: string }) => item.name === 'Agent 账户').data).toEqual([['2026-10-29', 100], ['2026-10-30', null]])
    expect(screen.queryByText('+0.20%')).toBeNull()
  })
  it('renders full positions and closed batches with exact amounts', async () => {
    render(Account, { sub: `${REAL_ACCOUNT_ID}/positions` })
    expect(await screen.findByRole('table', { name: '账户持仓' })).toHaveTextContent('100.0100')
    expect(screen.getByRole('table', { name: '已平仓批次' })).toHaveTextContent('NVDA')
    expect(screen.getByRole('table', { name: '已平仓批次' })).toHaveTextContent('100.0100')
    expect(within(screen.getByRole('region', { name: '全部持仓' })).getByText('account-1')).toBeInTheDocument()
  })
  it.each(['overview', 'positions'])('propagates missing-price evidence into the %s position table', async tab => {
    const base = respond
    respond = async (url, init) => url === `/api/accounts/${REAL_ACCOUNT_ID}`
      ? json({ ...detailFixture, equity: { ...equityFixture, detail: { bars_missing: ['MU'] } }, positions: [{ ...detailFixture.positions[0], last_close: null, market_value: '0', unrealized_pnl: '0', r_now: '0' }] })
      : base(url, init)
    render(Account, { sub: tab === 'overview' ? REAL_ACCOUNT_ID : `${REAL_ACCOUNT_ID}/positions` })
    const region = await screen.findByRole('region', { name: tab === 'overview' ? '当前持仓' : '全部持仓' })
    expect(within(region).getByText(/2026-10-30.*MU.*缺当日收盘/)).toBeInTheDocument()
    expect(within(region).queryByRole('cell', { name: '0' })).toBeNull()
  })
  it('shows trade provenance, fill cases and real-only void confirmation', async () => {
    render(Account, { sub: `${REAL_ACCOUNT_ID}/trades` })
    const table = await screen.findByRole('table', { name: '账户成交' })
    expect(table).toHaveTextContent('paper')
    expect(table).toHaveTextContent('高开回落限价成交')
    expect(table).toHaveTextContent('100.0100')
    await fireEvent.click(screen.getByRole('button', { name: '作废成交 MU' }))
    expect(screen.getByRole('button', { name: '确认作废' })).toBeDisabled()
    await fireEvent.input(screen.getByLabelText('作废理由'), { target: { value: '价格录错' } })
    await fireEvent.click(screen.getByRole('button', { name: '确认作废' }))
    await waitFor(() => expect(posts('/api/workbench/trades/t1/void')).toHaveLength(1))
    expect(body('/api/workbench/trades/t1/void')).toMatchObject({ reason: '价格录错' })
  })
  it.each(['2026-10-19T13:30:00Z', '2026-10-19T21:30:00+08:00'])('displays trade time in Beijing for the API instant %s', async executed_at => {
    const base = respond
    respond = async (url, init) => url === `/api/accounts/${REAL_ACCOUNT_ID}/trades`
      ? json({ trades: [{ ...trade, executed_at }] }) : base(url, init)
    render(Account, { sub: `${REAL_ACCOUNT_ID}/trades` })
    const table = await screen.findByRole('table', { name: '账户成交' })
    expect(within(table).getByRole('columnheader', { name: '成交时间（北京）' })).toBeInTheDocument()
    expect(within(table).getByRole('cell', { name: '2026-10-19 21:30:00 北京' })).toBeInTheDocument()
  })
  it('keeps closed accounts read-only including the trade list', async () => {
    selected = { ...paperFixture, status: 'closed' }
    const view = render(Account, { sub: 'paper-1' })
    await screen.findByRole('heading', { name: '账户概览' })
    expect(screen.getByText('账户已关闭，只读查看历史记录。')).toBeInTheDocument()
    for (const label of ['修改参数', '关闭模拟账户', '记录成交']) expect(screen.queryByRole('button', { name: label })).toBeNull()
    await view.rerender({ sub: 'paper-1/trades' })
    await screen.findByRole('table', { name: '账户成交' })
    expect(screen.queryByRole('button', { name: '作废成交 MU' })).toBeNull()
    expect(screen.getByText('模拟成交，无滑点')).toBeInTheDocument()
  })
  it('expands skipped decisions with evidence and uses the execution-record label for follow plans', async () => {
    selected = { ...paperFixture, strategy: 'follow_plans' }
    render(Account, { sub: 'paper-1/decisions' })
    expect(await screen.findByRole('button', { name: '展开 2026-10-30' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '执行记录' })).toHaveAttribute('aria-current', 'page')
    await fireEvent.click(screen.getByRole('button', { name: '展开 2026-10-30' }))
    expect(await screen.findByText('跳过原因：当日行情缺失')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Agent 看到的' })).toBeInTheDocument()
    expect(calls.some(c => c.url === '/api/accounts/paper-1/decisions?session=2026-10-30')).toBe(true)
  })
  it('explains a missing decision detail instead of leaving an expanded day blank', async () => {
    selected = { ...paperFixture }
    const base = respond
    respond = async (url, init) => url.includes('/decisions?session=') ? json({ decision: null }) : base(url, init)
    render(Account, { sub: 'paper-1/decisions' })
    await fireEvent.click(await screen.findByRole('button', { name: '展开 2026-10-30' }))
    expect(await screen.findByText('该日未保存决策详情')).toBeInTheDocument()
  })
  it('renders comparison with curves, no ranking and the low-sample explanation', async () => {
    render(Account, { sub: 'compare' })
    expect(await screen.findByRole('table', { name: '账户指标对比' })).toHaveTextContent('样本 < 5')
    expect(screen.getByRole('img', { name: '账户对比权益曲线' })).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: '权益曲线叠加' })).getByText('account-1')).toBeInTheDocument()
    expect(screen.getByText('模拟成交，无滑点')).toBeInTheDocument()
    expect(screen.queryByText(/最好|排名/)).toBeNull()
  })
  it('uses calendar-derived first trading days for comparison and refuses a missing first session', async () => {
    const weekend = { ...accountFixture, opened_on: '2026-10-31', opening_session: '2026-11-02' }
    const holiday = { ...paperFixture, opened_on: '2026-12-25', opening_session: '2026-12-28' }
    const base = respond
    respond = async (url, init) => {
      if (url === '/api/accounts?include_closed=true') return json({ accounts: [weekend, holiday] })
      if (url === `/api/accounts/${REAL_ACCOUNT_ID}/equity`) return json({ rows: [{ ...equityFixture, session: '2026-11-03' }], spy: [] })
      if (url === '/api/accounts/paper-1/equity') return json({ rows: [
        { ...equityFixture, session: '2026-12-28', equity: '100000' },
        { ...equityFixture, session: '2026-12-29', equity: '110000' },
      ], spy: [] })
      return base(url, init)
    }
    render(Account, { sub: 'compare' })
    await screen.findByRole('img', { name: '账户对比权益曲线' })
    const series = chart.setOption.mock.lastCall?.[0].series
    expect(series.find((item: { name: string }) => item.name === weekend.name).data).toEqual([])
    expect(series.find((item: { name: string }) => item.name === holiday.name).data).toEqual([['2026-12-28', 100], ['2026-12-29', 110]])
    expect(screen.getByText(/真实账户.*缺少首个交易日 2026-11-02/)).toBeInTheDocument()
    expect(chart.setOption.mock.lastCall?.[0].yAxis.name).toBe('首个交易日 = 100')
  })
  it('does not reuse old history to verify a newly loaded day PnL during an overview refresh', async () => {
    const previous = { ...equityFixture, session: '2026-10-29' }
    const current = { ...equityFixture, day_pnl: '1000.0100' }
    const updated = { ...current, day_pnl: '1001.0100' }
    let refreshing = false
    let releaseHistory!: (value: Response) => void
    const base = respond
    respond = (url, init) => {
      if (url === `/api/accounts/${REAL_ACCOUNT_ID}/params`) { refreshing = true; return Promise.resolve(json({ account: selected, change: {} })) }
      if (url === `/api/accounts/${REAL_ACCOUNT_ID}`) return Promise.resolve(json({ ...detailFixture, equity: refreshing ? updated : current }))
      if (url === `/api/accounts/${REAL_ACCOUNT_ID}/equity`) return refreshing ? new Promise(resolve => { releaseHistory = resolve }) : Promise.resolve(json({ rows: [previous, current], spy: [] }))
      return base(url, init)
    }
    render(Account, { sub: REAL_ACCOUNT_ID })
    await screen.findByText('1000.0100')
    await fireEvent.click(screen.getByRole('button', { name: '修改参数' }))
    const dialog = await screen.findByRole('dialog', { name: '修改账户参数' })
    await fireEvent.input(within(dialog).getByLabelText('修改理由'), { target: { value: '调整参数后刷新' } })
    await fireEvent.click(within(dialog).getByRole('button', { name: '保存参数' }))
    await waitFor(() => expect(releaseHistory).toBeTypeOf('function'))
    const metric = within(screen.getByText('今日盈亏').closest('[data-account-metric]') as HTMLElement)
    expect(metric.queryByText('1001.0100')).toBeNull()
    expect(metric.getByText(/正在读取权益历史/)).toBeInTheDocument()
    releaseHistory(json({ rows: [previous, updated], spy: [] }))
    expect(await metric.findByText('1001.0100')).toBeInTheDocument()
  })
  it('loads full daily orders for the paper overview even when none remain pending', async () => {
    selected = { ...paperFixture }
    const base = respond
    respond = async (url, init) => {
      if (url === '/api/accounts/paper-1') return json({ ...detailFixture, account: selected, decision: { ...decision, status: 'ok', status_reason: null }, pending_orders: [], forms: { can_record_trades: false } })
      if (url === '/api/accounts/paper-1/decisions?session=2026-10-30') return json({ decision: { ...decision, status: 'ok', status_reason: null, orders: [{ id: 'o1', ticker: 'MU', action: 'buy', shares: '10', status: 'rejected', status_reason: 'insufficient_cash', reason: '等回踩区间' }] } })
      return base(url, init)
    }
    render(Account, { sub: 'paper-1' })
    const table = await screen.findByRole('table', { name: '模拟订单' })
    expect(table).toHaveTextContent('等回踩区间')
    expect(table).toHaveTextContent('现金不足')
  })
  it('ignores a late response from the previous account after a rapid switch', async () => {
    const base = respond
    let release!: (value: Response) => void
    respond = (url, init) => url === `/api/accounts/${REAL_ACCOUNT_ID}` ? new Promise(resolve => { release = resolve }) : base(url, init)
    const view = render(Account, { sub: REAL_ACCOUNT_ID })
    await waitFor(() => expect(release).toBeTypeOf('function'))
    selected = { ...paperFixture }
    await view.rerender({ sub: 'paper-1' })
    await screen.findByRole('heading', { name: '账户概览' })
    release(json({ ...detailFixture, account: { ...accountFixture, name: '过期响应账户' } }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Agent 账户' })).toBeInTheDocument())
    expect(screen.queryByRole('heading', { name: '过期响应账户' })).toBeNull()
    expect(screen.queryByRole('button', { name: '记录成交' })).toBeNull()
  })
  it('shows migration failure while navigation remains available', async () => {
    respond = async () => json({ error: 'migration_030_missing' }, 503)
    render(Account)
    expect((await screen.findAllByText(/迁移 030/))[0]).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '账户' })).toBeInTheDocument()
  })
  it('changes parameters with a reason and next-session explanation', async () => {
    selected = { ...paperFixture }
    render(Account, { sub: 'paper-1' })
    await fireEvent.click(await screen.findByRole('button', { name: '修改参数' }))
    const dialog = await screen.findByRole('dialog', { name: '修改账户参数' })
    expect(dialog).toHaveTextContent('下一交易日生效')
    expect(within(dialog).getByRole('button', { name: '保存参数' })).toBeDisabled()
    await fireEvent.input(within(dialog).getByLabelText('最多持仓数'), { target: { value: '3' } })
    await fireEvent.input(within(dialog).getByLabelText('修改理由'), { target: { value: '降低集中度' } })
    await fireEvent.click(within(dialog).getByRole('button', { name: '保存参数' }))
    await waitFor(() => expect(posts('/api/accounts/paper-1/params')).toHaveLength(1))
    expect(body('/api/accounts/paper-1/params')).toMatchObject({ request_id: 'request-1', params: { max_positions: 3 }, reason: '降低集中度' })
  })
  it('requires a reason to close a paper account and then renders it read-only', async () => {
    selected = { ...paperFixture }
    const base = respond
    respond = async (url, init) => {
      if (url === '/api/accounts/paper-1/close') { selected = { ...selected, status: 'closed' }; return json(selected) }
      return base(url, init)
    }
    render(Account, { sub: 'paper-1' })
    await fireEvent.click(await screen.findByRole('button', { name: '关闭模拟账户' }))
    expect(screen.getByRole('button', { name: '确认关闭账户' })).toBeDisabled()
    await fireEvent.input(screen.getByLabelText('关闭理由'), { target: { value: '本轮观察结束' } })
    await fireEvent.click(screen.getByRole('button', { name: '确认关闭账户' }))
    expect(await screen.findByText('账户已关闭，只读查看历史记录。')).toBeInTheDocument()
    expect(body('/api/accounts/paper-1/close')).toEqual({ reason: '本轮观察结束' })
  })
  it('dismisses an account mutation dialog when the route changes to another account', async () => {
    const view = render(Account, { sub: REAL_ACCOUNT_ID })
    await fireEvent.click(await screen.findByRole('button', { name: '修改参数' }))
    await screen.findByRole('dialog', { name: '修改账户参数' })
    selected = { ...paperFixture }
    await view.rerender({ sub: 'paper-1' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(calls.some(call => call.init?.method === 'POST')).toBe(false)
  })
})

describe('real-account migrated forms', () => {
  async function fillTrade() {
    render(Account, { sub: REAL_ACCOUNT_ID })
    await screen.findByRole('heading', { name: '录入成交' })
    await fireEvent.input(screen.getByLabelText('代码'), { target: { value: 'mu' } })
    await fireEvent.input(screen.getByLabelText('数量'), { target: { value: '5' } })
    await fireEvent.input(screen.getByLabelText('成交价'), { target: { value: '102.0100' } })
    await fireEvent.input(screen.getByLabelText('成交时间'), { target: { value: '2026-10-30T10:15' } })
  }
  it('retains timezone, default plan linking and the locked real account on trade writes', async () => {
    await fillTrade()
    await waitFor(() => expect((screen.getByLabelText('关联计划') as HTMLSelectElement).value).toBe('p1'))
    await fireEvent.click(screen.getByRole('button', { name: '记录成交' }))
    await waitFor(() => expect(posts('/api/workbench/trades')).toHaveLength(1))
    expect(body('/api/workbench/trades')).toMatchObject({ request_id: 'request-1', account_id: REAL_ACCOUNT_ID, ticker: 'MU', quantity: '5', price: '102.0100', plan_id: 'p1' })
    expect(body('/api/workbench/trades').executed_at).toMatch(/^2026-10-30T\d{2}:15:00\.000Z$/)
  })
  it('validates an own plan, posts its exact levels, and clears old plans when the ticker changes', async () => {
    await fillTrade()
    await fireEvent.change(screen.getByLabelText('关联计划'), { target: { value: '__own__' } })
    await fireEvent.click(screen.getByRole('button', { name: '记录成交' }))
    expect(await screen.findByText('自定计划需要填写四个价位')).toBeInTheDocument()
    expect(posts('/api/workbench/trades')).toHaveLength(0)
    for (const [label, value] of [['入场下沿', '100'], ['入场上沿', '102'], ['止损', '95'], ['T1', '120'], ['最长持有', '8']]) {
      await fireEvent.input(screen.getByLabelText(label), { target: { value } })
    }
    await fireEvent.click(screen.getByRole('button', { name: '记录成交' }))
    await waitFor(() => expect(posts('/api/workbench/trades')).toHaveLength(1))
    expect(body('/api/workbench/trades').own_plan).toEqual({ entry_low: '100', entry_high: '102', stop: '95', target1: '120', max_hold_sessions: 8 })
    expect(body('/api/workbench/trades')).not.toHaveProperty('plan_id')
  })
  it('keeps cash flows and broker snapshot recording scoped to the real account', async () => {
    render(Account, { sub: REAL_ACCOUNT_ID })
    await screen.findByRole('heading', { name: '录入资金快照' })
    await fireEvent.input(screen.getByLabelText('资金日期'), { target: { value: '2026-10-30' } })
    await fireEvent.input(screen.getByLabelText('总资金'), { target: { value: '100100.0100' } })
    await fireEvent.click(screen.getByRole('button', { name: '录入资金快照' }))
    await fireEvent.input(screen.getByLabelText('出入金日期'), { target: { value: '2026-10-30' } })
    await fireEvent.input(screen.getByLabelText('金额'), { target: { value: '100.0100' } })
    await fireEvent.click(screen.getByRole('button', { name: '录入出入金' }))
    await waitFor(() => expect(posts('/api/workbench/account/flows')).toHaveLength(1))
    expect(body('/api/workbench/account/snapshots')).toMatchObject({ account_id: REAL_ACCOUNT_ID, equity: '100100.0100' })
    expect(body('/api/workbench/account/flows')).toMatchObject({ account_id: REAL_ACCOUNT_ID, amount: '100.0100' })
  })
  it('preserves cash-flow history and requires a reason to void a flow', async () => {
    const base = respond
    respond = async (url, init) => url.startsWith('/api/workbench/account/snapshots') && init?.method !== 'POST'
      ? json({ snapshots: [], params: accountFixture.params, flows: [{ id: 'flow-1', flow_date: '2026-10-30', amount: '5000.0100', recorded_at: '2026-10-30T14:00:00Z', voided_at: null }] }) : base(url, init)
    render(Account, { sub: REAL_ACCOUNT_ID })
    expect(await screen.findByText(/5000.0100/)).toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: '作废' }))
    expect(screen.getByRole('dialog', { name: '作废这笔出入金？' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认作废' })).toBeDisabled()
    await fireEvent.input(screen.getByLabelText('作废理由'), { target: { value: '重复录入' } })
    await fireEvent.click(screen.getByRole('button', { name: '确认作废' }))
    await waitFor(() => expect(posts('/api/workbench/account/flows/flow-1/void')).toHaveLength(1))
    expect(body('/api/workbench/account/flows/flow-1/void')).toEqual({ reason: '重复录入' })
  })
})

describe('account mutation generations', () => {
  it.each(['params', 'close'] as const)('%s ignores late success and error after A→B and A→B→A', async kind => {
    for (const outcome of ['success', 'error'] as const) for (const returnToA of [false, true]) {
      calls = []
      selected = { ...paperFixture }
      const base = respond
      let release!: (response: Response) => void
      let intercepted = false
      const oldUrl = `/api/accounts/paper-1/${kind}`
      respond = (url, init) => {
        if (url === oldUrl && init?.method === 'POST' && !intercepted) {
          intercepted = true
          return new Promise(resolve => { release = resolve })
        }
        return base(url, init)
      }
      const view = render(Account, { sub: 'paper-1' })
      const openLabel = kind === 'params' ? '修改参数' : '关闭模拟账户'
      const title = kind === 'params' ? '修改账户参数' : '关闭模拟账户？'
      const reasonLabel = kind === 'params' ? '修改理由' : '关闭理由'
      const submitLabel = kind === 'params' ? '保存参数' : '确认关闭账户'
      await fireEvent.click(await screen.findByRole('button', { name: openLabel }))
      await fireEvent.input(screen.getByLabelText(reasonLabel), { target: { value: '旧操作' } })
      await fireEvent.click(screen.getByRole('button', { name: submitLabel }))
      await waitFor(() => expect(release).toBeTypeOf('function'))
      selected = { ...paperFixture, id: 'paper-2', name: '第二账户' }
      await view.rerender({ sub: 'paper-2' })
      await screen.findByRole('heading', { name: '第二账户' })
      if (returnToA) {
        selected = { ...paperFixture }
        await view.rerender({ sub: 'paper-1' })
        await screen.findByRole('heading', { name: 'Agent 账户' })
      }
      await fireEvent.click(screen.getByRole('button', { name: openLabel }))
      await screen.findByRole('dialog', { name: title })
      await fireEvent.input(screen.getByLabelText(reasonLabel), { target: { value: '新操作' } })
      await act(async () => {
        release(outcome === 'success' ? json(kind === 'params' ? { account: paperFixture, change: {} } : paperFixture) : json({ error: 'old_operation_failed' }, 400))
        await new Promise(resolve => setTimeout(resolve, 0))
      })
      const dialog = screen.getByRole('dialog', { name: title })
      expect((within(dialog).getByLabelText(reasonLabel) as HTMLInputElement).value).toBe('新操作')
      expect(within(dialog).getByRole('button', { name: submitLabel })).toBeEnabled()
      expect(screen.queryByText(/old_operation_failed/)).toBeNull()
      expect(posts(oldUrl)).toHaveLength(1)
      if (!returnToA) {
        await fireEvent.click(within(dialog).getByRole('button', { name: submitLabel }))
        await waitFor(() => expect(posts(`/api/accounts/paper-2/${kind}`)).toHaveLength(1))
        expect(body(`/api/accounts/paper-2/${kind}`)).toMatchObject({ reason: '新操作' })
      }
      view.unmount()
      respond = base
    }
  })

  it.each(['success', 'error'] as const)('a late legacy trade %s cannot reset a new real-account form after A→B→A', async outcome => {
    const base = respond
    let release!: (response: Response) => void
    respond = (url, init) => url === '/api/workbench/trades' && init?.method === 'POST' ? new Promise(resolve => { release = resolve }) : base(url, init)
    const view = render(Account, { sub: REAL_ACCOUNT_ID })
    await screen.findByRole('heading', { name: '录入成交' })
    for (const [label, value] of [['代码', 'MU'], ['数量', '5'], ['成交价', '102'], ['成交时间', '2026-10-30T10:15']]) await fireEvent.input(screen.getByLabelText(label), { target: { value } })
    await fireEvent.click(screen.getByRole('button', { name: '记录成交' }))
    await waitFor(() => expect(release).toBeTypeOf('function'))
    selected = { ...paperFixture }
    await view.rerender({ sub: 'paper-1' })
    await screen.findByRole('heading', { name: 'Agent 账户' })
    selected = { ...accountFixture }
    await view.rerender({ sub: REAL_ACCOUNT_ID })
    await screen.findByRole('heading', { name: '录入成交' })
    await fireEvent.input(screen.getByLabelText('数量'), { target: { value: '99' } })
    await act(async () => { release(outcome === 'success' ? json({ status: 'created', record: trade }) : json({ error: 'old_trade_failed' }, 400)); await new Promise(resolve => setTimeout(resolve, 0)) })
    expect((screen.getByLabelText('数量') as HTMLInputElement).value).toBe('99')
    expect(screen.getByRole('button', { name: '记录成交' })).toBeEnabled()
    expect(screen.queryByText(/old_trade_failed/)).toBeNull()
  })
})

describe('trade form · 关联计划', () => {
  const linkableView = {
    ticker: 'NVDA',
    plans: [
      { plan_id: 'ap2', label: 'Agent NVDA·10-22 第 2 版（入场期，有效至 10-27）', kind: 'agent', chain_signal_date: '2026-10-22', version_no: 2 },
      { plan_id: 'rp1', label: '规则基线 plan-2 NVDA·10-23（有效至 10-27）', kind: 'rule' },
      { plan_id: 'op1', label: '自定 NVDA·10-20', kind: 'own' }],
    default_plan_id: 'ap2',
  }
  const isPost = (c: { url: string; init?: RequestInit }) => c.url === '/api/workbench/trades' && c.init?.method === 'POST'
  const postBody = () => JSON.parse(String(calls.find(isPost)!.init!.body))
  const linkableCalls = () => calls.filter(c => c.url.startsWith('/api/workbench/trades/linkable-plans'))

  async function fill(extra: Record<string, string> = {}) {
    render(Account, { sub: REAL_ACCOUNT_ID })
    await screen.findByRole('heading', { name: '录入成交' })
    await fireEvent.input(screen.getByLabelText('代码'), { target: { value: 'nvda' } })
    await fireEvent.input(screen.getByLabelText('数量'), { target: { value: '5' } })
    await fireEvent.input(screen.getByLabelText('成交价'), { target: { value: '102' } })
    await fireEvent.input(screen.getByLabelText('成交时间'), { target: { value: extra.executed ?? '2026-10-23T10:15' } })
  }
  const withLinkable = (view: unknown = linkableView) => {
    const base = respond
    respond = async (url, init) => url.startsWith('/api/workbench/trades/linkable-plans') ? json(view) : base(url, init)
  }

  it('asks for the plans linkable at the fill time and defaults to the active chain head', async () => {
    withLinkable()
    await fill()
    await waitFor(() => expect(linkableCalls()).toHaveLength(1))
    const url = new URL(linkableCalls()[0].url, 'http://x')
    expect(url.searchParams.get('ticker')).toBe('NVDA')
    expect(url.searchParams.get('executed_at')).toMatch(/^2026-10-23T\d{2}:15:00\.000Z$/)
    const select = screen.getByLabelText('关联计划') as HTMLSelectElement
    await waitFor(() => expect(select.value).toBe('ap2'))
    expect([...select.options].map(o => o.textContent)).toEqual([
      '无计划', 'Agent NVDA·10-22 第 2 版（入场期，有效至 10-27）', '规则基线 plan-2 NVDA·10-23（有效至 10-27）', '自定 NVDA·10-20', '自定'])
    expect(screen.queryByText('关联决定')).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: '记录成交' }))
    await waitFor(() => expect(calls.some(isPost)).toBe(true))
    const body = postBody()
    expect(body).toMatchObject({ plan_id: 'ap2', ticker: 'NVDA', side: 'buy' })
    expect('decision_id' in body).toBe(false)
    expect('own_plan' in body).toBe(false)
  })

  it('defaults to 无计划 without an active chain, and sends neither link', async () => {
    withLinkable({ ticker: 'NVDA', plans: [], default_plan_id: null })
    await fill()
    await waitFor(() => expect(linkableCalls()).toHaveLength(1))
    expect((screen.getByLabelText('关联计划') as HTMLSelectElement).value).toBe('__none__')
    await fireEvent.click(screen.getByRole('button', { name: '记录成交' }))
    await waitFor(() => expect(calls.some(isPost)).toBe(true))
    const body = postBody()
    for (const key of ['plan_id', 'own_plan', 'decision_id']) expect(key in body).toBe(false)
  })

  it('a sell is not linked by default, and a chosen plan wins over the default', async () => {
    withLinkable()
    await fill()
    await waitFor(() => expect((screen.getByLabelText('关联计划') as HTMLSelectElement).value).toBe('ap2'))
    await fireEvent.change(screen.getByLabelText('关联计划'), { target: { value: 'rp1' } })
    await fireEvent.click(screen.getByRole('button', { name: '记录成交' }))
    await waitFor(() => expect(calls.some(isPost)).toBe(true))
    expect(postBody().plan_id).toBe('rp1')
  })

  it('does not link a sell by default', async () => {
    withLinkable()
    await fill()
    await fireEvent.change(screen.getByLabelText('方向'), { target: { value: 'sell' } })
    await waitFor(() => expect(linkableCalls().length).toBeGreaterThan(0))
    expect((screen.getByLabelText('关联计划') as HTMLSelectElement).value).toBe('__none__')
  })

  it('自定 opens the four levels and the max hold, validates them, and posts own_plan', async () => {
    withLinkable()
    await fill()
    await waitFor(() => expect((screen.getByLabelText('关联计划') as HTMLSelectElement).value).toBe('ap2'))
    await fireEvent.change(screen.getByLabelText('关联计划'), { target: { value: '__own__' } })
    const group = screen.getByRole('group', { name: '自定计划' })
    expect(within(group).getByLabelText('最长持有')).toBeInTheDocument()
    await fireEvent.input(within(group).getByLabelText('入场下沿'), { target: { value: '99' } })
    await fireEvent.input(within(group).getByLabelText('入场上沿'), { target: { value: '101' } })
    await fireEvent.input(within(group).getByLabelText('止损'), { target: { value: '100' } })
    await fireEvent.input(within(group).getByLabelText('T1'), { target: { value: '112' } })
    expect(group).toHaveTextContent('价位需满足：止损 < 入场下限 ≤ 入场上限 < T1')
    expect(screen.getByRole('button', { name: '记录成交' })).toBeDisabled()
    await fireEvent.input(within(group).getByLabelText('止损'), { target: { value: '94' } })
    await fireEvent.input(within(group).getByLabelText('最长持有'), { target: { value: '7.5' } })
    expect(group).toHaveTextContent('最长持有须为正整数（交易日）')
    await fireEvent.input(within(group).getByLabelText('最长持有'), { target: { value: '12' } })
    expect(screen.getByRole('button', { name: '记录成交' })).toBeEnabled()
    await fireEvent.click(screen.getByRole('button', { name: '记录成交' }))
    await waitFor(() => expect(calls.some(isPost)).toBe(true))
    const body = postBody()
    expect(body.own_plan).toEqual({ entry_low: '99', entry_high: '101', stop: '94', target1: '112', max_hold_sessions: 12 })
    expect('plan_id' in body).toBe(false)
  })

  it('an empty own plan omits the max hold (the backend defaults to 10) and blocks blank levels', async () => {
    withLinkable()
    await fill()
    await fireEvent.change(screen.getByLabelText('关联计划'), { target: { value: '__own__' } })
    await fireEvent.click(screen.getByRole('button', { name: '记录成交' }))
    expect(await screen.findByText('自定计划需要填写四个价位')).toBeInTheDocument()
    expect(calls.some(isPost)).toBe(false)
    const group = screen.getByRole('group', { name: '自定计划' })
    for (const [label, value] of [['入场下沿', '99'], ['入场上沿', '101'], ['止损', '94'], ['T1', '112']])
      await fireEvent.input(within(group).getByLabelText(label), { target: { value } })
    await fireEvent.click(screen.getByRole('button', { name: '记录成交' }))
    await waitFor(() => expect(calls.some(isPost)).toBe(true))
    expect('max_hold_sessions' in postBody().own_plan).toBe(false)
  })

  it.each([
    [409, 'plan_stale: 成交时这条链已是第 3 版，请关联第 3 版', '成交时这条链已是第 3 版，请关联第 3 版'],
    [409, 'plan_after_fill', '这一版生成于成交之后，不可能是你照着做的那一版'],
    [400, 'decision_link_retired', 'v2.9 起成交不再关联决定，请改为关联计划'],
    [503, 'migration_029_missing', '迁移 029 未执行'],
  ])('shows the %s %s text and keeps the form', async (status, error, text) => {
    withLinkable()
    const base = respond
    respond = async (url, init) => url === '/api/workbench/trades' && init?.method === 'POST' ? json({ error }, status) : base(url, init)
    await fill()
    await waitFor(() => expect((screen.getByLabelText('关联计划') as HTMLSelectElement).value).toBe('ap2'))
    await fireEvent.click(screen.getByRole('button', { name: '记录成交' }))
    expect(await screen.findByText(text)).toBeInTheDocument()
    expect((screen.getByLabelText('数量') as HTMLInputElement).value).toBe('5')
    if (status === 409) await waitFor(() => expect(linkableCalls().length).toBeGreaterThanOrEqual(2))   // re-read after a stale link
  })

  it('clears the chosen plan and the own levels when the ticker changes, and a stale plan id is never sent', async () => {
    const base = respond
    respond = async (url, init) => {
      if (url.startsWith('/api/workbench/trades/linkable-plans')) {
        const t = new URL(url, 'http://x').searchParams.get('ticker')
        return json(t === 'MU' ? { ticker: 'MU', plans: [{ plan_id: 'mu1', label: 'Agent MU·10-29 第 2 版' }], default_plan_id: 'mu1' }
                              : { ticker: 'NVDA', plans: [{ plan_id: 'ap2', label: 'Agent NVDA·10-22 第 2 版' }], default_plan_id: null })
      }
      return base(url, init)
    }
    render(Account, { sub: REAL_ACCOUNT_ID })
    await screen.findByRole('heading', { name: '录入成交' })
    await fireEvent.input(screen.getByLabelText('代码'), { target: { value: 'mu' } })
    await fireEvent.input(screen.getByLabelText('数量'), { target: { value: '5' } })
    await fireEvent.input(screen.getByLabelText('成交价'), { target: { value: '102' } })
    await fireEvent.input(screen.getByLabelText('成交时间'), { target: { value: '2026-10-23T10:15' } })
    const select = screen.getByLabelText('关联计划') as HTMLSelectElement
    await waitFor(() => expect([...select.options].some(o => o.value === 'mu1')).toBe(true))
    await fireEvent.change(select, { target: { value: 'mu1' } })
    expect(select.value).toBe('mu1')
    await fireEvent.input(screen.getByLabelText('代码'), { target: { value: 'nvda' } })
    await waitFor(() => expect([...select.options].some(o => o.value === 'ap2')).toBe(true))
    expect(select.value).toBe('__none__')                           // the MU choice is gone, NVDA has no default
    await fireEvent.click(screen.getByRole('button', { name: '记录成交' }))
    await waitFor(() => expect(calls.some(isPost)).toBe(true))
    const body = postBody()
    expect(body.ticker).toBe('NVDA')
    for (const key of ['plan_id', 'own_plan']) expect(key in body).toBe(false)
  })

  it('a change of the fill time drops a chosen plan row but keeps 自定', async () => {
    withLinkable()
    await fill()
    const select = screen.getByLabelText('关联计划') as HTMLSelectElement
    await waitFor(() => expect(select.value).toBe('ap2'))
    await fireEvent.change(select, { target: { value: 'rp1' } })
    expect(select.value).toBe('rp1')
    await fireEvent.input(screen.getByLabelText('成交时间'), { target: { value: '2026-10-24T10:15' } })
    await waitFor(() => expect(select.value).toBe('ap2'))           // back to the default for the new time
    await waitFor(() => expect(linkableCalls()).toHaveLength(2))
    await fireEvent.change(select, { target: { value: '__own__' } })
    await fireEvent.input(screen.getByLabelText('成交时间'), { target: { value: '2026-10-25T10:15' } })
    await waitFor(() => expect(linkableCalls()).toHaveLength(3))
    expect(select.value).toBe('__own__')
  })

  it('keeps the form usable when the linkable list cannot be read', async () => {
    const base = respond
    respond = async (url, init) => url.startsWith('/api/workbench/trades/linkable-plans')
      ? json({ error: 'migration_029_missing' }, 503) : base(url, init)
    await fill()
    expect(await screen.findByText('可关联的计划暂不可用：迁移 029 未执行')).toBeInTheDocument()
    expect((screen.getByLabelText('关联计划') as HTMLSelectElement).value).toBe('__none__')
    expect([...(screen.getByLabelText('关联计划') as HTMLSelectElement).options].map(o => o.textContent)).toEqual(['无计划', '自定'])
  })

})
