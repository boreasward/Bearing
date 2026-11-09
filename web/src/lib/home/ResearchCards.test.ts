import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte'
import { invalidateAll } from '$lib/stores'
import ResearchCards from './ResearchCards.svelte'
import type { ResearchCardData } from './types'

beforeEach(() => { invalidateAll() })

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

const card = (over: Partial<ResearchCardData>): ResearchCardData => ({
  research_id: 1,
  ticker: 'SNDK',
  status: 'watching',
  thesis: '跌了很多，我认为是入场的时间',
  created_session: '2026-11-04',
  day: 3,
  valid_until: '2026-12-02',
  condition: { kind: 'close_above', level: '105.00', ma: null },
  condition_text: '收盘站上 105.00',
  last_close: '101.00',
  condition_met_today: false,
  triggered_session: null,
  verdict: 'watch',
  reasons: '等回调结束再入',
  plan_id: null,
  ended_session: null,
  end_reason: null,
  missing_bar: false,
  created_at: '2026-11-04T22:00:00+00:00',
  ...over,
})

const WATCHING: ResearchCardData[] = [
  card({ research_id: 1, ticker: 'SNDK' }),
  card({
    research_id: 2,
    ticker: 'IBM',
    condition: { kind: 'close_below', level: '180.00', ma: null },
    condition_text: '收盘跌破 180.00',
    last_close: '181.00',
    condition_met_today: true,
    triggered_session: '2026-11-06',
    verdict: null,
    reasons: null,
  }),
  card({
    research_id: 3,
    ticker: 'AMD',
    condition: { kind: 'close_above_ma', level: null, ma: 50 },
    condition_text: '收盘站上 50 日均线',
    condition_met_today: null,
    verdict: null,
    reasons: null,
  }),
  card({
    research_id: 4,
    ticker: 'INTC',
    condition: { kind: 'touch_below', level: '35.00', ma: null },
    condition_text: '最低价触及 35.00 或以下',
    condition_met_today: false,
    missing_bar: true,
    last_close: '36.00',
    verdict: null,
    reasons: null,
  }),
  card({
    research_id: 5,
    ticker: 'ORCL',
    condition: null,
    condition_text: null,
    condition_met_today: null,
    last_close: null,
    verdict: null,
    reasons: null,
  }),
  // 审查 P1-1：未触发 + 当日缺日线，但最后一根历史日线满足条件——stale 的 true 不能盖过缺日线
  card({
    research_id: 7,
    ticker: 'QCOM',
    condition: { kind: 'close_above', level: '100.00', ma: null },
    condition_text: '收盘站上 100.00',
    last_close: '101.00',
    condition_met_today: true,
    triggered_session: null,
    missing_bar: true,
    verdict: null,
    reasons: null,
  }),
]
const ENDED: ResearchCardData[] = [
  card({
    research_id: 6,
    ticker: 'WDC',
    status: 'planned',
    verdict: 'ready',
    reasons: '突破放量',
    plan_id: 41,
    ended_session: '2026-11-05',
    end_reason: '已保存计划 41',
    day: 2,
  }),
  card({
    research_id: 7,
    ticker: 'STX',
    status: 'expired',
    verdict: null,
    reasons: null,
    ended_session: '2026-11-06',
    end_reason: '20 个交易日条件未到',
    day: 21,
  }),
]

function stubResearch(watching = WATCHING, ended = ENDED) {
  const router = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://local')
    if (url.pathname === '/api/research') {
      const group = url.searchParams.get('group') ?? 'watching'
      return json({ research: group === 'ended' ? ended : watching })
    }
    if (url.pathname === '/api/research/1' || url.pathname === '/api/research/6') {
      const row = [...watching, ...ended].find((c) => `/api/research/${c.research_id}` === url.pathname)!
      return json({
        ...row,
        events: [
          { session: '2026-11-04', event: 'created', reason: '用户发起研究', before: {}, after: {}, source: 'user', created_at: '2026-11-04T22:00:00+00:00' },
          { session: '2026-11-05', event: 'concluded', reason: '值得盯：等回调结束再入', before: {}, after: {}, source: 'agent', created_at: '2026-11-05T22:00:00+00:00' },
        ],
      })
    }
    const confirmed = init?.body ? (JSON.parse(String(init.body)) as { confirmed?: boolean }).confirmed : undefined
    if (confirmed === false) return json({ write: false, preview: '将放弃 SNDK 的研究。确认后写入。' })
    return json({ research: { id: 1 }, event: { id: 9 } })
  })
  vi.stubGlobal('fetch', router)
  return router
}

describe('ResearchCards', () => {
  it('renders the two groups; ended starts folded', async () => {
    stubResearch()
    render(ResearchCards)
    expect(await screen.findByRole('heading', { name: '研究' })).toBeInTheDocument()
    expect(await screen.findByText('SNDK')).toBeInTheDocument() // 等数据到位再查组内
    const watching = screen.getByRole('region', { name: '观察中' })
    expect(within(watching).getByText('SNDK')).toBeInTheDocument()
    expect(within(watching).getAllByText('跌了很多，我认为是入场的时间').length).toBeGreaterThan(0) // 夹具同论点，只验在场
    // 已结束折叠：默认只有折叠钮，卡片内容不在
    expect(screen.queryByText('WDC')).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: '已结束' }))
    expect(await screen.findByText('WDC')).toBeInTheDocument()
  })

  it('发起研究 hands 研究 ␣（研究 + 空格，用户补股票与论点） to the callback', async () => {
    stubResearch()
    const onStartResearch = vi.fn()
    render(ResearchCards, { onStartResearch })
    await screen.findByText('SNDK')
    await fireEvent.click(screen.getByRole('button', { name: '发起研究' }))
    expect(onStartResearch).toHaveBeenCalledWith('研究 ')
  })

  it('shows the five display states derived from the card fields', async () => {
    stubResearch()
    render(ResearchCards)
    await screen.findByText('SNDK')
    expect(screen.getByText('条件已到，待复看')).toBeInTheDocument() // IBM：triggered_session 非空
    expect(screen.getAllByText('缺日线，未复查').length).toBeGreaterThan(0) // INTC/QCOM：missing_bar
    expect(screen.getByText('日线不足，未检查')).toBeInTheDocument() // AMD：close_above_ma 且判不了
    expect(screen.getAllByText('尚无条件').length).toBeGreaterThan(0) // ORCL：无条件（条件行与状态行各一次）
    expect(screen.getAllByText('观察中').length).toBeGreaterThan(0) // SNDK：条件没到、正常盯
  })

  it('an untriggered missing-bar card shows 缺日线 even when the stale bar satisfies the condition', async () => {
    // 审查 P1-1：优先级 触发（真 triggered_session）→ 无条件 → 缺日线 → 当日条件已到 → 均线不足 → 观察中
    stubResearch()
    render(ResearchCards)
    await screen.findByText('QCOM')
    const qcom = screen.getByText('QCOM').closest('[data-slot="card"]') as HTMLElement
    expect(within(qcom).getByText('缺日线，未复查')).toBeInTheDocument()
    expect(within(qcom).queryByText('条件已到，待复看')).toBeNull()
    // 对照：真触发的 IBM 仍显示「条件已到，待复看」
    const ibm = screen.getByText('IBM').closest('[data-slot="card"]') as HTMLElement
    expect(within(ibm).getByText('条件已到，待复看')).toBeInTheDocument()
  })

  it('shows the condition with the latest close in dollars and the agent conclusion', async () => {
    stubResearch()
    render(ResearchCards)
    await screen.findByText('SNDK')
    expect(screen.getByText(/收盘站上 \$105\.00/)).toBeInTheDocument() // 审查 P1-3：价位带 $
    expect(screen.getAllByText('$101.00').length).toBeGreaterThan(0) // 最新收盘带 $
    expect(screen.getByText(/值得盯：等回调结束再入/)).toBeInTheDocument() // agent 结论一句
  })

  it('formats all three level-based condition kinds with dollars; close_above_ma keeps its wording', async () => {
    // 审查 P1-3：三种价位条件由 kind + moneyText(level) 拼文案，不再直出服务端的裸数字文案
    stubResearch()
    render(ResearchCards)
    await screen.findByText('SNDK')
    expect(screen.getByText(/收盘站上 \$105\.00/)).toBeInTheDocument() // close_above
    expect(screen.getByText(/收盘跌破 \$180\.00/)).toBeInTheDocument() // close_below
    expect(screen.getByText(/最低价触及 \$35\.00 或以下/)).toBeInTheDocument() // touch_below
    expect(screen.getByText(/收盘站上 50 日均线/)).toBeInTheDocument() // close_above_ma 沿用服务端文案
    expect(screen.queryByText('收盘跌破 180.00')).toBeNull()
  })

  it('ended planned card links to the plan card anchor; expired shows the end reason', async () => {
    // 审查 P2-5：「已转计划」是导航（同页锚点指向计划卡根节点），不是纯文本
    stubResearch()
    render(ResearchCards)
    await screen.findByText('SNDK')
    await fireEvent.click(screen.getByRole('button', { name: '已结束' }))
    const link = await screen.findByRole('link', { name: '已转计划 #41' })
    expect(link).toHaveAttribute('href', '#plans/41')
    expect(screen.getByText(/20 个交易日条件未到/)).toBeInTheDocument()
  })

  it('放弃研究 goes through preview then confirmed=true and reloads', async () => {
    const router = stubResearch()
    const onChanged = vi.fn()
    render(ResearchCards, { onChanged })
    await screen.findByText('SNDK')
    await fireEvent.click(screen.getAllByRole('button', { name: '放弃研究' })[0]!)
    expect(await screen.findByRole('dialog')).toHaveTextContent('将放弃 SNDK 的研究。确认后写入。')
    const dropCalls = () => router.mock.calls.filter(([u]) => String(u).startsWith('/api/research/1/drop'))
    expect(dropCalls()).toHaveLength(1)
    expect((dropCalls()[0]![1]!.body as string)).toContain('"confirmed":false')
    // 确认对话框里的按钮也叫「放弃研究」；对话框渲染在 body 末尾的 Portal 里
    const confirmButton = within(screen.getByRole('dialog')).getByRole('button', { name: '放弃研究' })
    await fireEvent.click(confirmButton)
    await waitFor(() => expect(dropCalls()).toHaveLength(2))
    expect((dropCalls()[1]![1]!.body as string)).toContain('"confirmed":true')
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('expanding a card fetches the research detail and lists the event timeline', async () => {
    const router = stubResearch()
    render(ResearchCards)
    await screen.findByText('SNDK')
    await fireEvent.click(screen.getAllByRole('button', { name: /变化记录/ })[0]!)
    expect(await screen.findByText(/值得盯：等回调结束再入/)).toBeInTheDocument()
    expect(screen.getByText(/用户发起研究/)).toBeInTheDocument()
    expect(router.mock.calls.some(([u]) => String(u) === '/api/research/1')).toBe(true)
  })

  it('empty groups say why', async () => {
    stubResearch([], [])
    render(ResearchCards)
    expect(await screen.findByText('暂无观察中的研究')).toBeInTheDocument()
  })

  it('shows the error reason when the fetch fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ error: 'no_run' }, 404)))
    render(ResearchCards)
    expect(await screen.findByText(/还没有运行记录/)).toBeInTheDocument()
  })
})
it('opens an ended research outside the first page and refreshes it after writes', async () => {
  const { invalidateAll } = await import('$lib/stores')
  let thesis = '原来的论点'
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => String(url).includes('?') ? json({ research:[] }) : json({ ...card({ research_id:999, status:'dropped', thesis }), events:[{ event:'created', reason:'历史记录' }] })))
  render(ResearchCards, { id:999 })
  expect(await screen.findByText('原来的论点')).toBeInTheDocument()
  expect(await screen.findByText(/历史记录/)).toBeInTheDocument()
  thesis = '刷新后的论点'; invalidateAll()
  expect(await screen.findByText('刷新后的论点')).toBeInTheDocument()
})
