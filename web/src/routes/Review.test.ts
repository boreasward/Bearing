import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte'
import Review from './Review.svelte'
import { REAL_ACCOUNT_ID } from '../lib/accounts'
import { accountFixture, paperFixture } from '../lib/components/account/fixtures'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const trade = { id: 't1', ticker: 'NVDA', side: 'buy', quantity: 10, price: 101.5, fee: 1, executed_at: '2026-10-23T14:00:00Z',
                decision_id: null, note: null, recorded_at: '2026-10-23T15:00:00Z', voided_at: null, void_reason: null }

let calls: { url: string; init?: RequestInit }[]
let respond: (url: string, init?: RequestInit) => Promise<Response>

beforeEach(() => {
  calls = []
  respond = async (url, init) => {
    if (url.includes('/review/trades')) return json({ session: '2026-10-22', calc_version: 'review-1', closed: [],
      open: [{ ticker: 'NVDA', quantity: '10', avg_cost: 100.1, last_close: 105, as_of: '2026-10-22', unrealized_pnl: 49, unrealized_return: 0.049 }] })
    if (url === '/api/workbench/trades' && init?.method !== 'POST') return json({ trades: [trade] })
    if (url.includes('/review/shadow')) return json({ session: '2026-10-22', since: '2026-07-24', calc_version: 'review-1', units: [
      { id: 'd1', ticker: 'NVDA', signal_date: '2026-10-22', proposed_action: 'buy', final_action: 'ignore', decision: 'reject',
        reason: 'r', decided_at: '2026-10-23T01:00:00Z', supersedes_id: null, rule_ids: ['trend_reclaim'],
        outcome: { status: 'entered', entry_date: '2026-10-23', entry_price: 100, windows: {
          '5': { status: 'mature', return: 0.05, spy_return: 0.01, excess: 0.04 }, '10': { status: 'immature' }, '20': { status: 'immature' } } } }] })
    if (url.includes('/weekly-reviews')) return json({ reviews: [] })
    if (url.startsWith('/api/watchlist')) return json({ rows: [{ ticker: 'NVDA' }] })
    return json({})
  }
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => { calls.push({ url: String(url), init }); return respond(String(url), init) }))
  vi.stubGlobal('crypto', { randomUUID: vi.fn(() => 'req-t') })
})
afterEach(() => { vi.unstubAllGlobals(); history.replaceState(null, '', '#review') })

describe('account-scoped review tabs', () => {
  const discipline = { session: '2026-10-30', since: '2026-10-01', trades: [], counts: { chase: 0, early_take: 0, held_loser: 0, unplanned: 0 }, immature: {}, planned_buys: 0, compliance_rate: null }
  function accountResponses() {
    const base = respond
    respond = async (url, init) => url.startsWith('/api/accounts') ? json({ accounts: [accountFixture, paperFixture] })
      : url.startsWith('/api/workbench/discipline') ? json(discipline) : base(url, init)
  }
  it('removes the migrated tabs and defaults discipline to the real account', async () => {
    accountResponses()
    render(Review, { sub: 'discipline' })
    await screen.findByRole('combobox', { name: '复盘账户' })
    await waitFor(() => expect(calls.some(c => c.url === `/api/workbench/discipline?account_id=${REAL_ACCOUNT_ID}`)).toBe(true))
    const nav = screen.getByRole('navigation', { name: '复盘页签' })
    expect(within(nav).queryByRole('link', { name: '交易与持仓' })).toBeNull()
    expect(within(nav).queryByRole('link', { name: '资金' })).toBeNull()
    expect(screen.queryByRole('button', { name: '记录成交' })).toBeNull()
    expect(screen.queryByRole('button', { name: '录入资金快照' })).toBeNull()
  })
  it('changing the account actually reloads discipline and preserves the query', async () => {
    accountResponses()
    render(Review, { sub: 'discipline' })
    await fireEvent.click(await screen.findByRole('combobox', { name: '复盘账户' }))
    await fireEvent.pointerUp(await screen.findByRole('option', { name: 'Agent 账户' }), { pointerType: 'mouse' })
    await waitFor(() => expect(calls.some(c => c.url === '/api/workbench/discipline?account_id=paper-1')).toBe(true))
    expect(location.hash).toBe('#review/discipline?account=paper-1')
    expect(screen.getByRole('link', { name: '轨道对比' })).toHaveAttribute('href', '#review/tracks?account=paper-1')
  })
  it('loads tracks from the hash account and switching changes only the tracks call', async () => {
    accountResponses()
    const base = respond
    respond = async (url, init) => url.startsWith('/api/workbench/review/tracks') ? json(trackView) : base(url, init)
    history.replaceState(null, '', '#review/tracks?account=paper-1')
    render(Review, { sub: 'tracks' })
    await waitFor(() => expect(calls.some(c => c.url === '/api/workbench/review/tracks?account_id=paper-1')).toBe(true))
    await fireEvent.click(screen.getByRole('combobox', { name: '复盘账户' }))
    await fireEvent.pointerUp(await screen.findByRole('option', { name: '真实账户' }), { pointerType: 'mouse' })
    await waitFor(() => expect(calls.some(c => c.url === `/api/workbench/review/tracks?account_id=${REAL_ACCOUNT_ID}`)).toBe(true))
    expect(calls.some(c => c.url.startsWith('/api/workbench/discipline'))).toBe(false)
  })
})

it('shows shadow outcomes with immature windows marked', async () => {
  render(Review, { sub: 'shadow' })
  expect(await screen.findByText('+4.00%')).toBeInTheDocument()
  expect(screen.getAllByText('未成熟').length).toBe(2)
  expect(screen.getByText(/否决/)).toBeInTheDocument()
})

it('generates a weekly review for the chosen Monday', async () => {
  render(Review, { sub: 'weekly' })
  expect(await screen.findByText(/尚无周度复盘/)).toBeInTheDocument()
  await fireEvent.input(screen.getByLabelText('周一'), { target: { value: '2026-10-19' } })
  await fireEvent.input(screen.getByLabelText('复盘笔记'), { target: { value: '合成笔记' } })
  respond = async (url) => url.includes('/weekly-reviews') && calls.at(-1)?.init?.method === 'POST'
    ? json({ id: 'w1', week_start: '2026-10-19', calc_version: 'review-1', notes: '合成笔记', supersedes_id: null, created_at: 'x',
             stats: { units: { decided: 0, undecided: 0 }, by_rule: {}, by_decision: {}, execution: [], unsignaled_trades: [], note: '描述性统计' } })
    : json({ reviews: [] })
  await fireEvent.click(screen.getByRole('button', { name: '生成复盘' }))
  await waitFor(() => expect(calls.some(c => c.url === '/api/workbench/weekly-reviews' && c.init?.method === 'POST')).toBe(true))
  const body = JSON.parse(String(calls.find(c => c.init?.method === 'POST')!.init!.body))
  expect(body).toEqual({ week_start: '2026-10-19', notes: '合成笔记', supersedes_id: null })
})

it('the weekly generate button is busy while the request is in flight and cannot double-submit', async () => {
  render(Review, { sub: 'weekly' })
  expect(await screen.findByText(/尚无周度复盘/)).toBeInTheDocument()
  await fireEvent.input(screen.getByLabelText('周一'), { target: { value: '2026-10-19' } })
  let release!: (r: Response) => void
  respond = (url, init) => url.includes('/weekly-reviews') && init?.method === 'POST'
    ? new Promise<Response>(resolve => { release = resolve })
    : Promise.resolve(json({ reviews: [] }))
  const button = screen.getByRole('button', { name: '生成复盘' })
  await fireEvent.click(button)
  await waitFor(() => expect(button).toHaveAttribute('aria-busy', 'true'))
  expect(button).toBeDisabled()
  await fireEvent.submit(button.closest('form')!)
  const posts = () => calls.filter(c => c.url === '/api/workbench/weekly-reviews' && c.init?.method === 'POST')
  expect(posts()).toHaveLength(1)
  release(json({ error: 'x' }, 500))
  await waitFor(() => expect(screen.getByRole('button', { name: '生成复盘' })).not.toHaveAttribute('aria-busy', 'true'))
})

it('reports 503 instead of an empty review', async () => {
  respond = async (url) => url.startsWith('/api/watchlist') ? json({ rows: [] }) : json({ error: 'down' }, 503)
  render(Review, { sub: 'shadow' })
  expect(await screen.findByText(/数据库未就绪/)).toBeInTheDocument()
})


describe('monthly and account tabs', () => {
  const monthly = (stats: Record<string, unknown>) => ({
    id: 'm1', month: '2026-10-01', calc_version: 'monthly-1', stats, notes: '', supersedes_id: null,
    created_at: '2026-11-01T02:00:00Z',
  })
  const baseStats = { month: '2026-10-01', tier: null, spy: null, review_flag: 'insufficient',
                      sensitivity: { approximate: true, rows: [] }, discipline: {}, plans: {},
                      generated_at: '2026-11-01T02:00:00Z' }

  it('monthly tab shows missing snapshot message', async () => {
    respond = async (url) => url.includes('/monthly-reviews')
      ? json({ reviews: [monthly({ ...baseStats, return: { status: 'missing_snapshot', return: null } })] })
      : json({})
    render(Review, { sub: 'monthly' })
    expect(await screen.findByText(/缺资金快照，无法计算/)).toBeInTheDocument()
  })

  it('monthly tab shows tier and SPY', async () => {
    respond = async (url) => url.includes('/monthly-reviews')
      ? json({ reviews: [monthly({ ...baseStats, return: { status: 'ok', return: 0.12, net_flow: 5000 },
                                   tier: 'excellent', spy: { return: 0.04, excess: 0.08 } })] })
      : json({})
    render(Review, { sub: 'monthly' })
    expect(await screen.findByText(/优秀/)).toBeInTheDocument()
    expect(screen.getByText(/\+12\.00%/)).toBeInTheDocument()
    expect(screen.getByText(/SPY \+4\.00%/)).toBeInTheDocument()
    expect(screen.getByText(/超额 \+8\.00%/)).toBeInTheDocument()
  })

  it('weekly shows plan and discipline sections', async () => {
    respond = async (url) => url.includes('/weekly-reviews')
      ? json({ reviews: [{ id: 'w1', week_start: '2026-10-19', calc_version: 'review-2',
                           notes: '', supersedes_id: null, created_at: '2026-10-24T02:00:00Z',
                           stats: { week: { start: '2026-10-19', end: '2026-10-25' }, units: { decided: 1, undecided: 0 },
                                    plans: { rule: { count: 2, expired: 1, r_mean: 1.5, r_median: 1.5 } },
                                    discipline: { counts: { chase: 1, early_take: 0, held_loser: 0, unplanned: 0 },
                                                  planned_buys: 2, compliance_rate: 0.5 } } }] })
      : json({})
    render(Review, { sub: 'weekly' })
    expect(await screen.findByText(/计划影子/)).toBeInTheDocument()
    expect(screen.getByText(/已过期 1/)).toBeInTheDocument()
    expect(screen.getByText(/遵守率 50%/)).toBeInTheDocument()
    expect(screen.getByText(/追高 1/)).toBeInTheDocument()
  })
})

describe('P2 review views', () => {
  it('weekly shows by-pool table', async () => {
    respond = async (url) => {
      if (url.includes('/weekly-reviews')) return json({ reviews: [{ id: 'w1', week_start: '2026-10-19',
        calc_version: 'review-3', notes: '', supersedes_id: null, created_at: '2026-10-24T00:00:00Z',
        stats: { week: { start: '2026-10-19', end: '2026-10-25' }, units: { decided: 2, undecided: 1 },
                 by_rule: {}, by_decision: {}, by_pool: {
                   core: { trend_reclaim: { triggers: 3, plans: 2, expired: 1, filled: 1, r_mean: 1.5, r_median: 1.5 } },
                   extended: { trend_reclaim: { triggers: 1, plans: 1, expired: 0, filled: 0, r_mean: null, r_median: null } } } } }] })
      return json({})
    }
    render(Review, { sub: 'weekly' })
    expect(await screen.findByText(/分池视角/)).toBeTruthy()
    expect(screen.getByText(/核心池/)).toBeTruthy()
    expect(screen.getByText(/扩展池/)).toBeTruthy()
    expect(screen.getAllByText(/趋势回踩修复/).length).toBe(2)
  })
  it('backcheck tab lists runs with flags', async () => {
    respond = async (url) => {
      if (url.includes('/backchecks')) return json({ backchecks: [
        { id: 'b1', calc_version: 'backcheck-1', created_at: '2026-10-24T00:00:00Z',
          params: { tickers: ['NVDA', 'MU'] },
          stats: { note: '描述性检查，不调参，不代表规则有效。',
                   all: { triggers: 12, filled: 3, flags: ['too_frequent'] },
                   rules: {}, tickers: [] } }] })
      return json({})
    }
    render(Review, { sub: 'backchecks' })
    expect(await screen.findByText('上线前检查')).toBeTruthy()
    expect(await screen.findByText(/too_frequent/)).toBeTruthy()
    expect(await screen.findByText(/描述性检查，不调参，不代表规则有效。/)).toBeTruthy()
  })
  it('shows excess columns for backcheck-2', async () => {
    respond = async (url) => {
      if (url.includes('/backchecks')) return json({ backchecks: [
        { id: 'b2', calc_version: 'backcheck-2', created_at: '2026-10-24T00:00:00Z',
          params: { tickers: ['NVDA'] },
          stats: { note: '描述性检查，不调参，不代表规则有效。超额是信号日收盘到第 h 日收盘，不代表可成交。',
                   all: { triggers: 12, filled: 3, flags: [],
                          excess: {
                            '5': { n: 3, mean: 0.003333, median: 0.0, positive_share: 1 / 3, immature: 1, missing: 0 },
                            '10': { n: 2, mean: -0.01, median: -0.01, positive_share: 0.0, immature: 2, missing: 0 },
                            '20': { n: 0, mean: null, median: null, positive_share: null, immature: 4, missing: 1 } } },
                   rules: {}, tickers: [] } }] })
      return json({})
    }
    render(Review, { sub: 'backchecks' })
    expect(await screen.findByText('超额 5 日')).toBeTruthy()
    expect(screen.getByText('超额 10 日')).toBeTruthy()
    expect(screen.getByText('超额 20 日')).toBeTruthy()
    expect(screen.getByText('n=3 · 均 0.3% · 中 0.0% · 正 33.3%')).toBeTruthy()
    expect(screen.getByText('n=2 · 均 -1.0% · 中 -1.0% · 正 0.0%')).toBeTruthy()
    expect(screen.getByText('n=0 · 均 — · 中 — · 正 —')).toBeTruthy()
  })
  it('shows a dash for backcheck-1 rows', async () => {
    respond = async (url) => {
      if (url.includes('/backchecks')) return json({ backchecks: [
        { id: 'b1', calc_version: 'backcheck-1', created_at: '2026-10-24T00:00:00Z',
          params: { tickers: ['MU'] },
          stats: { note: '描述性检查，不调参，不代表规则有效。',
                   all: { triggers: 9, filled: 1, flags: ['insufficient'] }, rules: {}, tickers: [] } }] })
      return json({})
    }
    render(Review, { sub: 'backchecks' })
    expect(await screen.findAllByText('—')).toHaveLength(3)
  })
  it('backcheck run posts and shows the descriptive note', async () => {
    respond = async (url, init) => {
      if (url.includes('/backchecks')) {
        if (init?.method === 'POST') return json({ id: 'b2', calc_version: 'backcheck-1', created_at: '2026-10-24T01:00:00Z',
          params: { tickers: [] }, stats: { note: '描述性检查，不调参，不代表规则有效。',
                                             all: { triggers: 0, filled: 0, flags: ['insufficient'] }, rules: {}, tickers: [] } })
        return json({ backchecks: [] })
      }
      return json({})
    }
    render(Review, { sub: 'backchecks' })
    await fireEvent.click(await screen.findByRole('button', { name: '运行历史检查' }))
    await waitFor(() => expect(calls.some(c => c.url.includes('/backchecks') && c.init?.method === 'POST')).toBe(true))
    expect(await screen.findByText(/描述性检查/)).toBeTruthy()
  })
})

it('plan shadow and discipline label agent track', async () => {
  respond = async (url: string, init?: RequestInit) => {
    if (url.includes('/weekly-reviews')) return json({ reviews: [{
      id: 'w1', week_start: '2026-10-19', calc_version: 'review-3', notes: '', supersedes_id: null,
      created_at: '2026-10-24T01:00:00Z',
      stats: { plans: { rule: { count: 2, expired: 1, r_mean: 1.0, r_median: 1.0 },
                        own: { count: 0, expired: 0, r_mean: null, r_median: null },
                        agent: { count: 1, expired: 0, r_mean: 2.5, r_median: 2.5 } },
               discipline: { trades: [], counts: { chase: 0, early_take: 0, held_loser: 0, unplanned: 0 },
                             immature: {}, planned_buys: 1, compliance_rate: 1 } } }] })
    if (url.includes('/review/trades')) return json({ session: '2026-10-22', calc_version: 'review-1', closed: [], open: [] })
    if (url === '/api/workbench/trades' && init?.method !== 'POST') return json({ trades: [trade] })
    if (url.includes('/review/shadow')) return json({ session: '2026-10-22', since: '2026-07-24', calc_version: 'review-1', units: [] })
    if (url.startsWith('/api/watchlist')) return json({ rows: [{ ticker: 'NVDA' }] })
    return json({})
  }
  render(Review, { sub: 'weekly' })
  expect(await screen.findByText(/Agent：1 · 已过期 0 · R 2\.5/)).toBeInTheDocument()
  expect(screen.getByText(/规则：2 · 已过期 1/)).toBeInTheDocument()
})


it('review-6 weekly plan shadow labels the agent first version and latest version', async () => {
  respond = async (url: string, init?: RequestInit) => {
    if (url.includes('/weekly-reviews')) return json({ reviews: [{
      id: 'w1', week_start: '2026-10-26', calc_version: 'review-6', notes: '', supersedes_id: null,
      created_at: '2026-10-31T01:00:00Z',
      stats: { plans: { agent: { count: 2, expired: 1, r_mean: 1.5, r_median: 1.5 },
                        agent_latest: { count: 2, expired: 0, r_mean: 2.0, r_median: 2.0 } } } }] })
    if (url.includes('/review/trades')) return json({ session: '2026-10-29', calc_version: 'review-1', closed: [], open: [] })
    if (url === '/api/workbench/trades' && init?.method !== 'POST') return json({ trades: [] })
    if (url.includes('/review/shadow')) return json({ session: '2026-10-29', since: '2026-07-24', calc_version: 'review-1', units: [] })
    return json({})
  }
  render(Review, { sub: 'weekly' })
  expect(await screen.findByText(/Agent（首版）：2 · 已过期 1/)).toBeInTheDocument()
  expect(screen.getByText(/Agent（最新版）：2 · 已过期 0 · R 2/)).toBeInTheDocument()
  expect(screen.queryByText(/agent_latest/)).toBeNull()
})

// --- three-track tab and review pushes (P4 §4, §6) ---

const emptyStats = (units = 0, mean: number | null = null) => ({ units, determined: units, pending: 0, missing: 0,
  mean, median: mean, sum: mean, wins: units, by_status: {} })
const trackView = (() => {
  const rule = emptyStats(1, 1.5)
  const agent = emptyStats(1, 0.5)
  const actual = { ...emptyStats(1, 0.75), by_status: { open: 1 }, by_plan_kind: { agent: 1 } }
  const group = { rule, agent, actual }
  const empty = { rule: emptyStats(), agent: emptyStats(), actual: { ...emptyStats(), by_plan_kind: {} } }
  return {
  session: '2026-10-23', since: '2026-07-25', until: '2026-10-23', calc_version: 'tracks-1',
  units: [{ ticker: 'NVDA', signal_date: '2026-10-21', pool: 'core',
            rule: { plan_id: 'p1', status: 'closed', r: 1.5 },
            agent: { run_id: 'r1', run_status: 'ok', run_reason: null, plan_id: 'p2', status: 'open', r: 0.5 },
            actual: { decision_id: 'd1', final_action: 'buy', plan_id: 'p2', plan_kind: 'agent',
                      buy_trade_ids: ['t1'], status: 'open', r: 0.75, open: true } }],
  summary: { all: group, core: group, extended: empty },
  }
})()

it('tracks tab shows summary by pool and unit rows', async () => {
  respond = async (url) => {
    if (url.includes('/review/tracks')) return json(trackView)
    return json({})
  }
  render(Review, { sub: 'tracks' })
  expect(await screen.findByText('NVDA')).toBeInTheDocument()
  expect(screen.getAllByText('核心池').length).toBeGreaterThan(0)
  expect(screen.getAllByText('+1.50R').length).toBeGreaterThan(0)   // 汇总里的规则均值
  expect(screen.getByText('NVDA')).toBeInTheDocument()
  expect(screen.getAllByText(/未平仓/).length).toBeGreaterThan(0)  // 状态文案
  expect(screen.getAllByText(/照Agent/).length).toBeGreaterThan(0)  // plan_kind
})

it('links the plan id to the standalone plan page, not the instrument page', async () => {
  respond = async (url) => {
    if (url.includes('/review/tracks')) return json(trackView)
    return json({})
  }
  render(Review, { sub: 'tracks' })
  const link = await screen.findByRole('link', { name: '计划 p1' })
  expect(link.getAttribute('href')).toBe('#plan/p1')
  expect(link.getAttribute('href')).not.toBe('#instrument/NVDA')
})

it('expands an agent run tool calls from a track row', async () => {
  respond = async (url) => {
    if (url.includes('/review/tracks')) return json(trackView)
    if (url.includes('/agent-runs/r1')) return json({ id: 'r1', status: 'ok', model: 'gpt-test', agent_version: 'agent-1',
      output: null, counter: null, plan: null, input_tokens: 10, output_tokens: 5, created_at: '2026-10-21T22:00:00Z',
      tool_calls: [{ seq: 1, phase: 'propose', tool: 'get_trigger', args: {}, result: { ref: 'call:1', data: {} },
                     result_sha256: 'a'.repeat(64), called_at: '2026-10-21T22:00:01Z' }] })
    return json({})
  }
  render(Review, { sub: 'tracks' })
  await screen.findByText('NVDA')
  await fireEvent.click(screen.getByRole('button', { name: 'r1' }))
  await waitFor(() => expect(calls.some(c => c.url.includes('/agent-runs/r1'))).toBe(true))
  expect(await screen.findByText(/get_trigger/)).toBeInTheDocument()
})

const weeklyWithTracks = { id: 'w1', week_start: '2026-10-19', calc_version: 'review-4', notes: '',
                           supersedes_id: null, created_at: '2026-10-24T22:00:00Z',
                           stats: { units: { decided: 1, undecided: 0 }, tracks: trackView.units === undefined ? undefined : { units: trackView.units, summary: trackView.summary } } }

it('weekly card renders tracks when present and hides when absent', async () => {
  const withTracks = { ...weeklyWithTracks, stats: { ...weeklyWithTracks.stats,
    tracks: { units: trackView.units, summary: trackView.summary } } }
  const without = { ...weeklyWithTracks, id: 'w2', stats: { ...weeklyWithTracks.stats, tracks: null } }
  respond = async (url) => {
    if (url.includes('/weekly-reviews')) return json({ reviews: [withTracks, without] })
    return json({})
  }
  render(Review, { sub: 'weekly' })
  await screen.findAllByText(/2026-10-19 周/)
  expect(screen.getAllByText(/轨道对比/).length).toBeGreaterThanOrEqual(3)   // tab + 卡片块标题 + 缺失提示
  expect(screen.getByText(/该快照无轨道对比/)).toBeInTheDocument()
})

it('monthly card shows review detail compound', async () => {
  respond = async (url) => {
    if (url.includes('/monthly-reviews')) return json({ reviews: [
      { id: 'm1', month: '2026-10-01', calc_version: 'monthly-2', notes: '', supersedes_id: null,
        created_at: '2026-11-01T22:00:00Z', stats: {
          return: { status: 'ok', return: 0.05, net_flow: 0 }, tier: 'excellent',
          spy: { return: 0.02, excess: 0.03 },
          review: { flag: 'review', compound: -0.06, spy_compound: 0.02,
                    months: [{ month: '2026-08-01', return: 0.01, spy_return: 0.01 }] },
          sensitivity: { count: 2, rows: [] } } }] })
    return json({})
  }
  render(Review, { sub: 'monthly' })
  expect(await screen.findByText(/2026-10-01/)).toBeInTheDocument()
  expect(screen.getByText(/3 个月复合/)).toBeInTheDocument()
  expect(screen.getByText(/-6.00%/)).toBeInTheDocument()
})

it('push button posts and shows the latest push result', async () => {
  respond = async (url, init) => {
    if (url.includes('/weekly-reviews') && init?.method !== 'POST') return json({ reviews: [weeklyWithTracks] })
    if (url.includes('/review-pushes') && init?.method === 'POST') return json({ kind: 'weekly', period: '2026-10-19', sent: true, messages: 1 })
    if (url.includes('/review-pushes')) return json({ pushes: [] })
    return json({})
  }
  render(Review, { sub: 'weekly' })
  await screen.findByText(/2026-10-19 周/)
  await fireEvent.click(screen.getByRole('button', { name: '推送到 Discord' }))
  await waitFor(() => expect(calls.some(c => c.url.includes('/review-pushes') && c.init?.method === 'POST')).toBe(true))
  expect(await screen.findByText('已推送')).toBeInTheDocument()
})


it('shows the latest recorded push beside each card', async () => {
  respond = async (url, init) => {
    if (url.includes('/weekly-reviews') && init?.method !== 'POST') return json({ reviews: [weeklyWithTracks] })
    if (url.includes('/review-pushes')) return json({ pushes: [
      { id: '2', kind: 'weekly', period: '2026-10-19', review_id: 'w1', trigger_source: 'scheduled', sent: false,
        messages: 0, reason: 'task_disabled', created_at: '2026-10-24T00:00:00Z' },
      { id: '1', kind: 'weekly', period: '2026-10-19', review_id: 'w1', trigger_source: 'manual', sent: true,
        messages: 1, reason: null, created_at: '2026-10-23T00:00:00Z' }] })
    return json({})
  }
  render(Review, { sub: 'weekly' })
  await screen.findByText(/2026-10-19 周/)
  expect(await screen.findByText(/最近推送 .* · 未推送：task_disabled · 定时/)).toBeInTheDocument()
  expect(screen.queryByText(/· 手动/)).toBeNull()
})

it('expands the second tool call from a track row', async () => {
  respond = async (url) => {
    if (url.includes('/review/tracks')) return json(trackView)
    if (url.includes('/agent-runs/r1')) return json({ id: 'r1', status: 'ok', model: 'gpt-test', agent_version: 'agent-1',
      output: null, counter: null, plan: null, input_tokens: 10, output_tokens: 5, created_at: '2026-10-21T22:00:00Z',
      tool_calls: [
        { seq: 1, phase: 'propose', tool: 'get_trigger', args: {}, result: { ref: 'call:1', data: {} },
          result_sha256: 'a'.repeat(64), called_at: '2026-10-21T22:00:01Z' },
        { seq: 2, phase: 'propose', tool: 'get_indicators', args: { ticker: 'NVDA' },
          result: { ref: 'call:2', data: { last_close: 100 } },
          result_sha256: 'b'.repeat(64), called_at: '2026-10-21T22:00:02Z' }] })
    return json({})
  }
  render(Review, { sub: 'tracks' })
  await screen.findByText('NVDA')
  await fireEvent.click(screen.getByRole('button', { name: 'r1' }))
  expect(await screen.findByText(/call:2/)).toBeInTheDocument()          // 第二个调用可达
  await fireEvent.click(screen.getByRole('button', { name: 'call:2' }))
  expect(await screen.findByText(/get_indicators/)).toBeInTheDocument()  // 点击后显示其详情
})

it('closing the tool-call drawer gives focus back to the run button that opened it', async () => {
  respond = async (url) => {
    if (url.includes('/review/tracks')) return json(trackView)
    if (url.includes('/agent-runs/r1')) return json({ id: 'r1', status: 'ok', model: 'gpt-test', agent_version: 'agent-1',
      output: null, counter: null, plan: null, input_tokens: 10, output_tokens: 5, created_at: '2026-10-21T22:00:00Z',
      tool_calls: [{ seq: 1, phase: 'propose', tool: 'get_trigger', args: {}, result: { ref: 'call:1', data: {} },
        result_sha256: 'a'.repeat(64), called_at: '2026-10-21T22:00:01Z' }] })
    return json({})
  }
  render(Review, { sub: 'tracks' })
  await screen.findByText('NVDA')
  const opener = screen.getByRole('button', { name: 'r1' })
  opener.focus()
  await fireEvent.click(opener)
  const dialog = await screen.findByRole('dialog')
  await fireEvent.click(within(dialog).getByRole('button', { name: '关闭' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  await waitFor(() => expect(document.activeElement).toBe(opener))
})

// ---- v2.7: 复盘卡 tab and 你学到了什么 ----

describe('growth loop', () => {
  const cardRows = [
    { id: 12, ticker: 'NVDA', subject: 'unit', signal_date: '2026-10-14', end_kind: 'position_closed', exit_basis: 'stop',
      end_session: '2026-10-23', decision_text: '采纳（照规则计划）', actual_r: -0.8, rule_r: -1, agent_r: null,
      flags: ['chase'], immature: [], status: 'to_review', headline: '追高是主因' },
  ]
  const learning = {
    discipline: { cards: 3, chase: 1, early_take: 0, held_loser: 1, unplanned: 0, pending: 2 },
    cards: [
      { card_id: 12, ticker: 'NVDA', end_kind: 'position_closed', end_text: '平仓（按止损）', decision_text: '采纳（照规则计划）',
        actual_r: -0.8, rule_r: -1, agent_r: null, flags: ['chase'], status: 'to_review', headline: '追高是主因' },
      { card_id: 13, ticker: 'MU', end_kind: 'expired', end_text: '过期未成交', decision_text: '否决',
        actual_r: null, filled: false, rule_r: null, agent_r: 0.4, flags: [], status: 'reviewed', headline: null },
      { card_id: 14, ticker: 'AMD', end_kind: 'position_closed', end_text: '平仓（其他）', decision_text: '否决',
        actual_r: null, filled: true, rule_r: null, agent_r: null, flags: ['unplanned'], status: 'to_review', headline: null },
    ],
    counts: { total: 3, reviewed: 1, to_review: 1, drafting: 1, draft_failed: 0 },
    knowledge: [
      { ref: 'know:3@2', title: '回踩别追', new_outcomes: 1, sample: { count: 6, determined: 5, expired: 1, r_mean: 0.42, r_median: 0.3 } },
      { ref: 'know:4@1', title: '财报前不加仓', new_outcomes: 1, sample: { count: 2, determined: 1, expired: 1, r_mean: null, r_median: null } },
    ],
  }
  const weeklyOf = (calc: string, stats: Record<string, unknown> = {}) => ({ reviews: [
    { id: 'w1', week_start: '2026-10-19', calc_version: calc, notes: '', supersedes_id: null, created_at: '2026-10-26T01:00:00Z',
      stats: { units: { decided: 1, undecided: 0 }, by_rule: {}, by_decision: {}, execution: [], unsignaled_trades: [],
               note: '描述性统计', ...stats } }] })

  beforeEach(() => {
    const base = respond
    respond = async (url, init) => {
      if (url.startsWith('/api/workbench/review-cards/12')) return json({ error: 'x' }, 500)
      if (url.startsWith('/api/workbench/review-cards')) return json({ cards: cardRows, counts: { to_review: 2, reviewed: 1, drafting: 0, draft_failed: 0 } })
      return base(url, init)
    }
  })

  it('cards tab is now first', async () => {
    render(Review, { sub: 'shadow' })
    await screen.findByText('+4.00%')
    const tabs = screen.getAllByRole('link').filter(a => a.getAttribute('href')!.startsWith('#review/'))
    expect(tabs.slice(0, 2).map(a => a.getAttribute('href'))).toEqual(['#review/cards', '#review/shadow'])
    expect(tabs[0]).toHaveTextContent('复盘卡')
    // the badge shows the to_review count on every tab
    expect(await screen.findByLabelText('待审定 2 张')).toHaveTextContent('2')
  })

  it('no badge without anything to review, and none when the cards cannot be read', async () => {
    respond = async (url) => url.startsWith('/api/workbench/review-cards') ? json({ error: '迁移 027 未执行' }, 503)
      : url.includes('/review/shadow') ? json({ session: '2026-10-22', since: '2026-07-24', calc_version: 'review-1', units: [] })
      : json({})
    render(Review, { sub: 'shadow' })
    await waitFor(() => expect((fetch as any).mock.calls.some((c: any[]) => String(c[0]).startsWith('/api/workbench/review-cards'))).toBe(true))
    expect(screen.queryByLabelText(/待审定/)).toBeNull()
  })

  it('the cards tab lists the cards', async () => {
    render(Review, { sub: 'cards' })
    expect(await screen.findByRole('link', { name: /NVDA/ })).toHaveAttribute('href', '#review/cards/12')
    expect(screen.getByRole('link', { name: /^复盘卡/ })).toHaveAttribute('aria-current', 'page')
  })

  it('#review/cards/12 opens that card', async () => {
    render(Review, { sub: 'cards/12' })
    expect(await screen.findByText(/加载失败/)).toBeInTheDocument()     // the card page asked for card 12
    expect(calls.some(c => c.url === '/api/workbench/review-cards/12')).toBe(true)
  })

  it('weekly card shows 你学到了什么', async () => {
    respond = async (url) => url.includes('/weekly-reviews') ? json(weeklyOf('review-7', { learning }))
      : url.startsWith('/api/workbench/review-cards') ? json({ cards: [], counts: {} }) : json({})
    render(Review, { sub: 'weekly' })
    const section = await screen.findByRole('region', { name: '你学到了什么' })
    expect(section).toHaveTextContent('纪律（本周结束的 3 笔）：追高 1 · 过早止盈 0 · 死扛亏损 1 · 计划外 0 · 待定 2')
    expect(section).toHaveTextContent('复盘卡 3 张（已审定 1 · 待审定 1）')
    const first = within(section).getByRole('link', { name: /NVDA 平仓（按止损）/ })
    expect(first).toHaveAttribute('href', '#review/cards/12')
    expect(first.closest('li')).toHaveTextContent('实际 -0.80R')
    expect(first.closest('li')).toHaveTextContent('采纳（照规则计划）')
    expect(within(section).getByRole('link', { name: /MU 过期未成交/ }).closest('li')).toHaveTextContent('实际 未成交')
    const unplanned = within(section).getByRole('link', { name: /AMD 平仓/ }).closest('li')!
    expect(unplanned).toHaveTextContent('实际 无 R（计划外）')
    expect(unplanned).not.toHaveTextContent('未成交')
    expect(section).toHaveTextContent('被检验的知识 2 条')
    const know = within(section).getByRole('link', { name: /know:3@2/ })
    expect(know).toHaveAttribute('href', '#knowledge/mine/3@2')
    expect(know.closest('li')).toHaveTextContent('回踩别追 · 样本 6（均值 +0.42R）')
    expect(within(section).getByRole('link', { name: /know:4@1/ }).closest('li')).toHaveTextContent('样本 2（少于 5 个不给均值）')
  })

  it('weekly learning with nothing in it says so in one line', async () => {
    const empty = { discipline: { cards: 0, chase: 0, early_take: 0, held_loser: 0, unplanned: 0, pending: 0 }, cards: [],
                    counts: { total: 0, reviewed: 0, to_review: 0, drafting: 0, draft_failed: 0 }, knowledge: [] }
    respond = async (url) => url.includes('/weekly-reviews') ? json(weeklyOf('review-7', { learning: empty }))
      : url.startsWith('/api/workbench/review-cards') ? json({ cards: [], counts: {} }) : json({})
    render(Review, { sub: 'weekly' })
    const section = await screen.findByRole('region', { name: '你学到了什么' })
    expect(section).toHaveTextContent('本周没有结束的交易，也没有被检验的知识')
    expect(section).not.toHaveTextContent('纪律（本周结束的')
  })

  it('old snapshot note', async () => {
    respond = async (url) => url.includes('/weekly-reviews') ? json(weeklyOf('review-6'))
      : url.startsWith('/api/workbench/review-cards') ? json({ cards: [], counts: {} }) : json({})
    render(Review, { sub: 'weekly' })
    expect(await screen.findByText('该快照没有『你学到了什么』（review-6 及更早）')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '你学到了什么' })).toBeNull()
  })
})



// ---- v2.9: 关联计划 (spec §6.1, §10.4) ----

describe('weekly review-8 snapshots', () => {
  it('read executed / not executed and by_execution, while review-7 snapshots keep the decision wording', async () => {
    const weeklyRow = (id: string, stats: Record<string, unknown>) => ({
      id, week_start: '2026-10-19', calc_version: 'review-8', notes: '', supersedes_id: null, created_at: '2026-10-26T02:00:00Z', stats })
    const w = (a: number, b: number) => ({ '5': { mean: a, median: b, mature: 1, count: 1 } })
    const base = respond
    respond = async url => url.includes('/weekly-reviews') ? json({ reviews: [
      weeklyRow('w8', { week: { start: '2026-10-19' }, units: { executed: 3, not_executed: 4 }, by_rule: {}, by_decision: {},
                        by_execution: { adopted: w(0.02, 0.01), not_executed: w(0.04, 0.03) }, note: '描述性统计' }),
      { ...weeklyRow('w7', { week: { start: '2026-10-12' }, units: { decided: 2, undecided: 1 }, by_rule: {},
                              by_decision: { reject: w(0.01, 0.01) }, note: '描述性统计' }), calc_version: 'review-7', week_start: '2026-10-12' }] })
      : base(url)
    render(Review, { sub: 'weekly' })
    const articles = await screen.findAllByRole('article')
    expect(articles[0]).toHaveTextContent('已执行 3 · 未执行 4')
    expect(articles[0]).toHaveTextContent('按执行')
    expect(articles[0]).toHaveTextContent('未执行： 5 日 +4.00% / +3.00%')
    expect(articles[0]).not.toHaveTextContent('已决定')
    expect(articles[1]).toHaveTextContent('已决定 2 · 未处理 1')
    expect(articles[1]).toHaveTextContent('否决')
  })
})

describe('review-8 vs older snapshots (D30, D26)', () => {
  const stats = (extra: Record<string, unknown>) => ({ week: { start: '2026-10-19' }, by_rule: {}, note: '描述性统计',
    execution: [{ ticker: 'MU', slippage: 0.01, delay_hours: 5.5 }], ...extra })
  const snap = (id: string, calc_version: string, st: Record<string, unknown>) => ({
    id, week_start: '2026-10-19', calc_version, notes: '', supersedes_id: null, created_at: '2026-10-26T02:00:00Z', stats: st })

  it('review-8 reads 版本生成后 / 计划外成交 / execution_text; review-7 keeps 决定后 / 无信号交易 / decision_text', async () => {
    const learning = (over: Record<string, unknown>) => ({
      discipline: { cards: 1, chase: 0, early_take: 0, held_loser: 0, unplanned: 1, pending: 0 },
      cards: [{ card_id: 4, ticker: 'MU', end_kind: 'position_closed', end_text: '平仓', decision_text: '否决', execution_text: '计划外成交',
                actual_r: 0.5, rule_r: null, agent_r: null, flags: [], status: 'to_review', headline: null, ...over }],
      counts: { total: 1, reviewed: 0, to_review: 1, drafting: 0, draft_failed: 0 }, knowledge: [] })
    const base = respond
    respond = async url => url.includes('/weekly-reviews') ? json({ reviews: [
      snap('w8', 'review-8', stats({ units: { executed: 1, not_executed: 2 }, unplanned_trades: [{ id: 't1' }, { id: 't2' }], learning: learning({}) })),
      snap('w7', 'review-7', stats({ units: { decided: 1, undecided: 2 }, unsignaled_trades: [{ id: 't1' }], learning: learning({}) }))] }) : base(url)
    render(Review, { sub: 'weekly' })
    const [w8, w7] = await screen.findAllByRole('article')
    expect(w8).toHaveTextContent('版本生成后 5.5 小时成交')
    expect(w8).not.toHaveTextContent('决定后')
    expect(w8).toHaveTextContent('计划外成交 2 笔')
    expect(w8).not.toHaveTextContent('无信号交易')
    expect(w8).toHaveTextContent('实际 +0.50R · 计划外成交')            // learning card follows execution_text
    expect(w7).toHaveTextContent('决定后 5.5 小时成交')
    expect(w7).toHaveTextContent('无信号交易 1 笔')
    expect(w7).toHaveTextContent('否决')
    expect(w7).not.toHaveTextContent('版本生成后')
  })

  it('a fill of an own plan has no delay: the line says so instead of 0.0 hours (M2)', async () => {
    const base = respond
    respond = async url => url.includes('/weekly-reviews') ? json({ reviews: [
      snap('w8', 'review-8', stats({ units: { executed: 1, not_executed: 0 }, execution: [{ ticker: 'MU', slippage: 0.01, delay_hours: null }] }))] }) : base(url)
    render(Review, { sub: 'weekly' })
    const [w8] = await screen.findAllByRole('article')
    expect(w8).toHaveTextContent('MU 成交价较 T+1 开盘 +1.00% · 自定计划，不计延迟')
    expect(w8).not.toHaveTextContent('0.0 小时')
  })

  it('the 对照组 tab under review-8 covers all units and has no 决定 column; older snapshots are unchanged', async () => {
    const unit = (over: Record<string, unknown>) => ({ id: 'u', ticker: 'MU', signal_date: '2026-10-22', proposed_action: 'buy',
      rule_ids: ['trend_reclaim'], outcome: { status: 'entered', entry_price: 100, windows: {
        '5': { status: 'mature', return: 0.02, spy_return: 0, excess: 0.02 }, '10': { status: 'immature' }, '20': { status: 'immature' } } }, ...over })
    const base = respond
    respond = async url => url.includes('/review/shadow') ? json({ session: '2026-10-29', since: '2026-07-30', calc_version: 'review-8', units: [unit({})] }) : base(url)
    const first = render(Review, { sub: 'shadow' })
    expect(await screen.findByText('+2.00%')).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: '决定' })).toBeNull()
    expect(screen.getByText(/全部信号单元/)).toBeInTheDocument()
    first.unmount()
    respond = async url => url.includes('/review/shadow') ? json({ session: '2026-10-29', since: '2026-07-30', calc_version: 'review-8', units: [] }) : base(url)
    const second = render(Review, { sub: 'shadow' })
    expect(await screen.findByText('暂无信号单元')).toBeInTheDocument()
    second.unmount()
    respond = base
    render(Review, { sub: 'shadow' })
    expect(await screen.findByRole('columnheader', { name: '决定' })).toBeInTheDocument()
  })

  it('the tracks tab names tracks-2 statuses voided and merged', async () => {
    const base = respond
    const track = (status: string) => ({ status, r: null, plan_id: null })
    const stat = { units: 1, determined: 0, pending: 0, missing: 0, mean: null, median: null, sum: null, wins: 0, by_status: {} }
    const grp = { rule: stat, agent: stat, actual: stat }
    respond = async url => url.includes('/review/tracks') && !url.includes('/trades') ? json({
      session: '2026-10-29', since: '2026-10-01', until: '2026-10-29', calc_version: 'tracks-2',
      summary: { all: grp, core: grp, extended: grp }, units: [{ ticker: 'MU', signal_date: '2026-10-22', pool: 'core',
        rule: track('voided'), agent: { ...track('merged'), run_id: null, run_status: null, run_reason: null },
        actual: { ...track('not_executed'), decision_id: null, final_action: null, buy_trade_ids: [], open: false, plan_kind: null } }] }) : base(url)
    render(Review, { sub: 'tracks' })
    const row = (await screen.findByText('MU')).closest('tr')!
    expect(row).toHaveTextContent('开盘作废')
    expect(row).toHaveTextContent('并入其他链')
    expect(row).not.toHaveTextContent('voided')
  })
})



// ---- v2.10: primitive layer ----

describe('primitive layer', () => {
  it('tabs are links with aria-current', async () => {
    const base = respond
    respond = async (url, init) => url.startsWith('/api/workbench/review-cards')
      ? json({ cards: [], counts: { to_review: 2, reviewed: 0, drafting: 0, draft_failed: 0 } }) : base(url, init)
    render(Review, { sub: 'shadow' })
    const nav = screen.getByRole('navigation', { name: '复盘页签' })
    const links = within(nav).getAllByRole('link')
    expect(links.map(a => a.getAttribute('href'))).toEqual(['#review/cards', '#review/shadow', `#review/discipline?account=${REAL_ACCOUNT_ID}`, `#review/tracks?account=${REAL_ACCOUNT_ID}`,
      '#review/weekly', '#review/monthly', '#review/backchecks'])
    expect(links.map(a => a.textContent!.replace(/\d+$/, '').trim())).toEqual(['复盘卡', '对照组', '纪律', '轨道对比', '周度复盘', '月度复盘', '上线前检查'])
    expect(links.filter(a => a.getAttribute('aria-current') === 'page').map(a => a.textContent!.trim())).toEqual(['对照组'])
    expect(screen.queryByRole('tablist')).toBeNull()
    expect(screen.queryByRole('tab')).toBeNull()
    expect(await within(nav).findByLabelText('待审定 2 张')).toHaveTextContent('2')
  })

  it('stats header shows calc version, n and cutoff', async () => {
    respond = async (url) => url.includes('/weekly-reviews')
      ? json({ reviews: [{ id: 'w1', week_start: '2026-10-19', calc_version: 'review-8', notes: '', supersedes_id: null,
          created_at: '2026-10-26T02:00:00Z', stats: { week: { start: '2026-10-19' }, units: { executed: 1, not_executed: 2 },
                                                       by_rule: {}, note: '描述性统计' } }] })
      : json({})
    render(Review, { sub: 'weekly' })
    const [card] = await screen.findAllByRole('article')
    expect(within(card).getByText('review-8').tagName).toBe('CODE')
    expect(card).toHaveTextContent('口径 review-8')
    expect(card).toHaveTextContent('n = 3')
    expect(within(card).getByText(/^生成于 2026-10-26 10:00:00 北京$/)).toBeInTheDocument()
    // n = 3 is under the minimum of 5: the sample pill says so
    expect(card.querySelector('[data-domain="block"][data-status="insufficient"]')).not.toBeNull()
  })

  it('stats header of the shadow and track tabs shows version and n', async () => {
    const first = render(Review, { sub: 'shadow' })
    const shadow = await screen.findByTestId('stats-header')
    expect(shadow).toHaveTextContent('口径 review-1')
    expect(shadow).toHaveTextContent('n = 1')
    first.unmount()
    respond = async (url) => url.includes('/review/tracks') ? json(trackView) : json({})
    render(Review, { sub: 'tracks' })
    const tracks = await screen.findByTestId('stats-header')
    expect(tracks).toHaveTextContent('口径 tracks-1')
    expect(tracks).toHaveTextContent('n = 1')
  })

  it('an empty table says why instead of an empty grid', async () => {
    respond = async (url) => url.includes('/review/shadow')
      ? json({ session: '2026-10-22', since: '2026-07-24', calc_version: 'review-1', units: [] }) : json({})
    render(Review, { sub: 'shadow' })
    expect(await screen.findByText('暂无已决定的信号')).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('an error is an alert block and the other tabs stay reachable', async () => {
    respond = async (url) => url.startsWith('/api/watchlist') ? json({ rows: [] }) : json({ error: 'down' }, 503)
    render(Review, { sub: 'shadow' })
    expect((await screen.findByText(/数据库未就绪/)).closest('[data-slot="alert"]')).toHaveAttribute('role', 'alert')
    expect(screen.getByRole('navigation', { name: '复盘页签' })).toBeInTheDocument()
  })
})
