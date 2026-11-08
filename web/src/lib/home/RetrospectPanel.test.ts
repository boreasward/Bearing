import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte'
import RetrospectPanel from './RetrospectPanel.svelte'
import type { Retrospect } from './types'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

// 一份 expired 未买计划（有效期首日进过区间、次日收盘跌破止损 → 假设离场 R = -1），
// 对照 tests/test_api_retrospect.py 的真实输出形状。
const VIEW: Retrospect = {
  records: [
    {
      plan_id: 11,
      ticker: 'AAPL',
      status: 'expired',
      kind: 'planned',
      origin_kind: 'rule',
      session: '2026-10-30',
      ended_session: '2026-11-03',
      end_reason: '5 个交易日未买入',
      planned: {
        entry_low: '99.00',
        entry_high: '101.00',
        initial_stop: '96.00',
        target1: '108.00',
        valid_from: '2026-11-02',
        valid_until: '2026-11-09',
        shares: null,
        max_hold_sessions: 20,
      },
      actual: { first_buy: null, last_sell: null, quantity: '0' },
      outcome: {},
      r_multiple: null,
      r_text: null,
      tags: [{ code: 'not_bought_in_zone', text: '未买入：价格进过入场区间', sessions: null }],
      followed: false,
      hypothetical: {
        status: 'exited',
        reason: null,
        fill_session: '2026-11-02',
        fill_price: '100.00',
        exit_session: '2026-11-03',
        exit_price: '96.00',
        exit_reason: 'initial_stop',
        sessions_held: 2,
        r_multiple: '-1.00',
      },
      groups: { trigger_rules: ['pullback'], setup_tier: 'favorable', industry: '半导体' },
    },
    {
      plan_id: 12,
      ticker: 'MU',
      status: 'closed',
      kind: 'adopted',
      origin_kind: 'adopted',
      session: '2026-10-20',
      ended_session: '2026-11-02',
      end_reason: 'trailing_stop',
      planned: {
        entry_low: '95.10',
        entry_high: '97.30',
        initial_stop: '91.40',
        target1: '104.70',
        valid_from: '2026-10-21',
        valid_until: '2026-10-28',
        shares: null,
        max_hold_sessions: 20,
      },
      actual: {
        first_buy: { price: '96.00', session: '2026-10-21', quantity: 120 },
        last_sell: { price: '104.00', session: '2026-11-02' },
        quantity: '120',
      },
      outcome: { realized_pnl: '960.00', r_multiple: null, r_text: '补录，不计', exit_reason: 'trailing_stop' },
      r_multiple: null,
      r_text: '补录，不计',
      tags: [],
      followed: true,
      hypothetical: null,
      groups: { trigger_rules: [], setup_tier: null, industry: null },
    },
  ],
  weekly: {
    week_start: '2026-11-02',
    week_end: '2026-11-04',
    ended: 1,
    followed: 0,
    deviated: 1,
    tag_counts: { '未买入：价格进过入场区间': 1 } as Record<string, number>,
    closed: [],
    total_r: '0',
    not_bought: [{ ticker: 'AAPL', end_reason: '5 个交易日未买入', hypothetical: null }],
    equity_start: '34000.00',
    equity_end: '34500.00',
    realized_pnl: '520.00',
    listing: { new: ['AAPL'], dropped: [], most_relisted: null },
  },
  rules: {
    realized: { trigger_rules: [], setup_tier: [], industry: [] },
    hypothetical: {
      trigger_rules: [
        { group: 'pullback', sample: 1, win_rate: null, avg_r: null, total_r: null, insufficient: true },
      ],
      setup_tier: [],
      industry: [],
    },
  },
  empty: false,
  min_sample: 10,
  note: '规则未经验证；样本不足的组不给数',
}

function stubRetrospect(view: () => Retrospect = () => VIEW) {
  const router = vi.fn(async (_input: RequestInfo | URL) => json(view()))
  vi.stubGlobal('fetch', router)
  return router
}

describe('RetrospectPanel', () => {
  it('shows the empty message when there is nothing to review', async () => {
    stubRetrospect(() => ({ ...VIEW, records: [], empty: true, weekly: { ...VIEW.weekly, ended: 0, not_bought: [], tag_counts: {} }, rules: { realized: { trigger_rules: [], setup_tier: [], industry: [] }, hypothetical: { trigger_rules: [], setup_tier: [], industry: [] } } }))
    render(RetrospectPanel)
    expect(await screen.findByText('还没有已结束的计划')).toBeInTheDocument()
    expect(screen.queryByText('执行复盘')).toBeNull()
  })

  it('renders one row per ended plan: levels in dollars, actual trades, tags, hypothetical', async () => {
    stubRetrospect()
    const { container } = render(RetrospectPanel)
    expect(await screen.findByText('AAPL')).toBeInTheDocument()
    expect(screen.getByText('MU')).toBeInTheDocument()
    // 计划说的（PlanLevels 带 $；文案被 span 拆开，按整块文本断言）与起止
    expect(container.textContent).toContain('入场 $99.00–$101.00 · 止损 $96.00 · T1 $108.00')
    expect(screen.getByText('2026-10-30–2026-11-03')).toBeInTheDocument()
    // 实际做的：一份未买入、一份补录买卖
    expect(screen.getByText('未买入')).toBeInTheDocument()
    expect(screen.getByText(/买 \$96\.00 × 120（2026-10-21）/)).toBeInTheDocument()
    // 标签：偏差用 retro_tag，补录无标签且记照做
    expect(screen.getByText('未买入：价格进过入场区间')).toBeInTheDocument()
    expect(screen.getByText('照做')).toBeInTheDocument()
    expect(screen.getByText('补录，不计')).toBeInTheDocument()
    // 假设结果一句
    expect(screen.getByText(/照做会在 2026-11-02 成交于 \$100\.00，2026-11-03 以初始止损 \$96\.00 离场，R -1\.00/)).toBeInTheDocument()
  })

  it('weekly summary shows the week numbers and money', async () => {
    stubRetrospect()
    render(RetrospectPanel)
    expect(await screen.findByText('AAPL')).toBeInTheDocument()
    const weekly = screen.getByRole('region', { name: /周汇总/ })
    expect(within(weekly).getByText(/2026-11-02–2026-11-04/)).toBeInTheDocument()
    expect(within(weekly).getByText('已结束 1 份')).toBeInTheDocument()
    expect(within(weekly).getByText(/照做 0 份/)).toBeInTheDocument()
    expect(within(weekly).getByText(/有偏差 1 份/)).toBeInTheDocument()
    expect(within(weekly).getByText('$520.00')).toBeInTheDocument() // 本周已实现盈亏
    expect(within(weekly).getAllByText('$34000.00').length).toBeGreaterThan(0) // 周初总资产
    expect(within(weekly).getAllByText('$34500.00').length).toBeGreaterThan(0) // 周末总资产
  })

  it('insufficient rule groups show the sample count and 样本不足, never a rate', async () => {
    stubRetrospect()
    render(RetrospectPanel)
    expect(await screen.findByText('AAPL')).toBeInTheDocument()
    expect(screen.getByText(VIEW.note!)).toBeInTheDocument() // 表头上方的口径行
    const row = screen.getByText('pullback').closest('tr')!
    expect(within(row as HTMLElement).getByText('1')).toBeInTheDocument() // 样本数
    expect(within(row as HTMLElement).getByText('样本不足')).toBeInTheDocument()
    expect((row as HTMLElement).textContent).not.toMatch(/胜率|%/)
    // 两张表：真实成交（空）与假设结果（一个样本不足的组）——「假设结果」同时是列名，按多处断言
    expect(screen.getAllByText('真实成交').length).toBeGreaterThan(0)
    expect(screen.getAllByText('假设结果').length).toBeGreaterThan(1)
  })

  it('switching the week refetches with the week parameter', async () => {
    const router = stubRetrospect()
    render(RetrospectPanel)
    await screen.findByText('AAPL')
    await fireEvent.click(screen.getByRole('combobox', { name: '选择周' }))
    // 选项含本周与之前 8 周；挑两周前的选项（固定日期，不受当天影响）
    const option = await screen.findByRole('option', { name: /2026-10-19–2026-10-23/ })
    await fireEvent.pointerUp(option)
    await waitFor(() => {
      const urls = router.mock.calls.map(([u]) => String(u))
      expect(urls.some((u) => u.includes('/api/retrospect') && u.includes('week=2026-10-23'))).toBe(true)
    })
  })

  it('shows the error reason when the fetch fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ error: 'no_run' }, 404)))
    render(RetrospectPanel)
    expect(await screen.findByText(/no_run/)).toBeInTheDocument()
  })
})
