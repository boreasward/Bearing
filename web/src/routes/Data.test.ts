import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte'
import Data from './Data.svelte'
import * as api from '../lib/api'
import { resetConceptIndex } from '../lib/knowledge'

const conceptIndexOf = (ids: string[]) => ({
  knowledge_version: 'knowledge-1', groups: [],
  concepts: ids.map(id => ({ id, ref: `concept:${id}@1`, title: `T:${id}`, group: id.split('.')[0], version: 1,
                             applies_to: [], summary: `S:${id}`, related: [] })),
})
const termIds = async () => new Set((await screen.findAllByRole('button', { name: /^概念：/, hidden: true }))
  .map(b => b.getAttribute('aria-label')!.replace(/^概念：T:/, '')))
import { fmtBeijing } from '../lib/format'
import type { DataOverview, FamilyCard, FamilyDetail, FamilyId, FamilyJob, FetchRun } from '../lib/data-families'

const EVIDENCE = '这里只展示采集事实。所有规则都未经验证（Phase 0 Gate HOLD）：数据完整不代表信号有效。'
const TITLES: Partial<Record<FamilyId, string>> = {
  bars: '日线', sec_filings: 'SEC 申报', news: '资讯', atr: 'ATR', fred: 'FRED 利差', universe: '纳指 100 范围',
}

const job = (overrides: Partial<FamilyJob> = {}): FamilyJob => ({
  name: 'workbench', enabled: true, next_run_at: '2026-10-23T06:30:00+00:00', ...overrides,
})

const card = (id: FamilyId, overrides: Partial<FamilyCard> = {}): FamilyCard => ({
  id, title: TITLES[id] ?? id, purpose: `用途-${id}`,
  kind: id === 'atr' ? 'derived' : id === 'universe' ? 'manual' : 'fetched',
  fetch_via: id === 'atr' ? 'bars' : null,
  per_ticker: ['bars', 'sec_filings', 'news'].includes(id),
  job: job(), coverage: { targets: 10, covered: 10, lagging: [] },
  latest_at: '2026-10-22', coverage_note: '覆盖说明', last_run: null, last_ticker_run: null, last_success_at: null,
  evidence: id === 'atr' ? EVIDENCE + ' `plan-1` 的计算输入，规则 `gate_hold`' : EVIDENCE, notes: [],
  current_error: null, fetch_runs_ready: true,
  ...overrides,
})

const six = (overrides: Partial<Record<FamilyId, Partial<FamilyCard>>> = {}) =>
  (Object.keys(TITLES) as FamilyId[]).map(id => card(id, overrides[id] ?? {}))

const overview = (families: FamilyCard[], extra: Partial<DataOverview> = {}): DataOverview => ({
  session: '2026-10-22', evidence: EVIDENCE, fetch_runs_ready: true, families, ...extra,
})

beforeEach(() => {
  vi.restoreAllMocks()
  resetConceptIndex()
  vi.spyOn(api, 'getConcepts').mockResolvedValue(conceptIndexOf(
    ['family.bars', 'family.sec_filings', 'family.news', 'indicator.atr14', 'family.fred', 'family.universe', 'system.gate_hold']))
  vi.spyOn(api, 'getDataFamilies').mockResolvedValue(overview(six()))
  vi.spyOn(api, 'getDataFamily').mockResolvedValue(detail())
  vi.spyOn(api, 'browseData').mockResolvedValue({ family: 'bars', key: 'MU', has_data: false, rows: [] })
  vi.spyOn(api, 'fetchDataFamily').mockResolvedValue({ run_id: 'r1', status: 'pending' })
  vi.spyOn(api, 'pollRun').mockResolvedValue({ run_id: 'r1', status: 'done', result: { status: 'success' } })
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('Data 总览', () => {
  it('indicator card lists items with basis and never offers a fetch action', async () => {
    vi.mocked(api.getDataFamilies).mockResolvedValue({ ...overview([]), families: [{ id: 'indicators', kind: 'derived', items: [{ id: 'atr14', title: 'ATR14', basis: 'high_low', window: 14, used_by: ['plan-2', 'agent-tools'], concept: 'indicator.atr14' }] }] })
    render(Data)
    const article = await screen.findByRole('article', { name: '指标' })
    expect(article).toHaveTextContent('ATR14')
    expect(article).toHaveTextContent('high_low')
    expect(article).toHaveTextContent('14')
    expect(article).toHaveTextContent('plan-2、agent-tools')
    expect(within(article).queryByRole('button', { name: /拉取/ })).toBeNull()
  })
  it('family card shows today sources and fallback share', async () => {
    vi.mocked(api.getDataFamilies).mockResolvedValue(overview([card('bars', { sources: [{ id: 'yfinance', note: '主源' }, { id: 'stooq', note: '备源，按最近比例调整' }], today_sources: { primary: 7, backup: 2, failed: 1 } })]))
    render(Data)
    const article = await screen.findByRole('article', { name: '日线' })
    expect(article).toHaveTextContent('今日来源：主源 7 · 备源 2 · 失败 1')
    expect(article).toHaveTextContent('备源占目标 20.0%')
    expect(article).toHaveTextContent('stooq')
    expect(article).toHaveTextContent('备源，按最近比例调整')
  })
  it('renders six cards with evidence banner', async () => {
    render(Data)
    expect(await screen.findByLabelText('证据说明')).toHaveTextContent(EVIDENCE)
    expect(screen.getAllByRole('article')).toHaveLength(6)
    for (const title of Object.values(TITLES)) expect(screen.getByText(title)).toBeTruthy()
    expect(screen.getByRole('button', { name: '拉取日线' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '重新加载' })).toBeTruthy()
    expect(screen.getAllByRole('button', { name: '拉取' })).toHaveLength(4)
    expect(screen.getByText('派生')).toBeTruthy()
    expect(screen.getByText('手动维护')).toBeTruthy()
    expect(screen.getAllByText('采集')).toHaveLength(4)
  })

  it('renders backtick spans as code, not raw backticks', async () => {
    vi.mocked(api.getDataFamilies).mockResolvedValue(
      overview(six({ bars: { purpose: '用于 `plan-1` 价位和 `screen-1` 筛选。' } })))
    render(Data)
    const article = await screen.findByRole('article', { name: '日线' })
    expect(article.textContent).not.toContain('`')
    expect([...article.querySelectorAll('code')].map(el => el.textContent)).toEqual(['plan-1', 'screen-1'])
  })

  it('does not repeat the page evidence on every card', async () => {
    render(Data)
    expect(await screen.findByLabelText('证据说明')).toHaveTextContent(EVIDENCE)
    for (const article of screen.getAllByRole('article')) expect(article).not.toHaveTextContent(EVIDENCE)
    const atr = screen.getByRole('article', { name: 'ATR' })
    expect(atr).toHaveTextContent('plan-1 的计算输入，规则 gate_hold')
    expect(atr.textContent).not.toContain('`')
  })

  it('does not repeat a note the coverage sentence already says', async () => {
    vi.mocked(api.getDataFamilies).mockResolvedValue(overview(six({
      sec_filings: { coverage_note: '覆盖 = 近 120 天有申报记录的标的数；无申报 ≠ 缺数据。',
                     notes: ['no_filing_is_not_missing', 'pool_empty'] },
    })))
    render(Data)
    const article = await screen.findByRole('article', { name: 'SEC 申报' })
    expect(within(article).getAllByText(/无申报 ≠ 缺数据/)).toHaveLength(1)
    expect(within(article).getByText('关注列表中没有可用的池标的')).toBeTruthy()
  })

  it('highlights lagging coverage', async () => {
    vi.mocked(api.getDataFamilies).mockResolvedValue(
      overview(six({ bars: { coverage: { targets: 10, covered: 8, lagging: ['MU', 'NVDA'] } } })))
    render(Data)
    const lagging = await screen.findByText('8 / 10')
    expect(lagging.className).toContain('text-warn')
    expect(screen.getAllByText('10 / 10')[0].className).not.toContain('text-warn')
  })

  it('shows failure reasons of the last run', async () => {
    vi.mocked(api.getDataFamilies).mockResolvedValue(overview(six({
      news: {
        last_run: {
          status: 'partial', trigger: 'manual_batch', scope: 'family', ticker: null,
          job: 'workbench', reason: null, finished_at: '2026-10-22T08:00:00+00:00',
          ok_count: 3, target_count: 5,
          failures: [{ target: 'MU', reason: '日线超时' }, { target: 'NVDA', reason: '网络错误' }],
        },
      },
    })))
    render(Data)
    expect(await screen.findByText(/日线超时/)).toBeTruthy()
    expect(screen.getByText(/网络错误/)).toBeTruthy()
    expect(screen.getByText(fmtBeijing('2026-10-22T08:00:00+00:00'))).toBeTruthy()
    expect(screen.getByText('手动批处理')).toBeTruthy()
    expect(screen.getByText('3/5')).toBeTruthy()
  })

  it('keeps the family run as the main line and a newer single-ticker run apart', async () => {
    vi.mocked(api.getDataFamilies).mockResolvedValue(overview(six({
      bars: {
        last_run: {
          status: 'success', trigger: 'scheduled', scope: 'family', ticker: null,
          job: 'workbench', reason: null, finished_at: '2026-10-22T06:40:00+00:00',
          ok_count: 106, target_count: 106, failures: [],
        },
        last_ticker_run: {
          status: 'failed', trigger: 'manual_family', scope: 'ticker', ticker: 'ZZZZ',
          job: 'workbench', reason: null, finished_at: '2026-10-22T08:00:00+00:00',
          ok_count: 0, target_count: 1, failures: [{ target: 'ZZZZ', reason: '连接中断' }],
        },
      },
    })))
    render(Data)
    const card = await screen.findByRole('article', { name: '日线' })
    const main = within(card).getByLabelText('最近全族拉取')
    expect(main).toHaveTextContent('success')
    expect(main).toHaveTextContent('106/106')
    expect(main).not.toHaveTextContent('ZZZZ')
    const single = within(card).getByLabelText('最近单只拉取')
    expect(single).toHaveTextContent('failed')
    expect(single).toHaveTextContent('ZZZZ')
    expect(single).toHaveTextContent(fmtBeijing('2026-10-22T08:00:00+00:00'))
    expect(within(card).getByText(/ZZZZ：连接中断/)).toBeTruthy()
  })

  it('has no single-ticker line without a newer single-ticker run', async () => {
    vi.mocked(api.getDataFamilies).mockResolvedValue(overview(six({
      bars: {
        last_run: {
          status: 'success', trigger: 'scheduled', scope: 'family', ticker: null,
          job: 'workbench', reason: null, finished_at: '2026-10-22T06:40:00+00:00',
          ok_count: 106, target_count: 106, failures: [],
        },
      },
    })))
    render(Data)
    const card = await screen.findByRole('article', { name: '日线' })
    expect(within(card).getByLabelText('最近全族拉取')).toHaveTextContent('success')
    expect(within(card).queryByLabelText('最近单只拉取')).toBeNull()
  })

  it('shows the skipped reason and the actual job path of the last run', async () => {
    // 真实无 key 载荷（I11/I13）：skipped 原因与实际 job 都可见，不只剩 0/2
    vi.mocked(api.getDataFamilies).mockResolvedValue(overview(six({
      fred: {
        last_run: {
          status: 'skipped', trigger: 'scheduled', scope: 'family', ticker: null,
          job: 'workbench', reason: 'FRED_API_KEY 未配置',
          finished_at: '2026-10-22T08:00:00+00:00', ok_count: 0, target_count: 2, failures: [],
        },
      },
      sec_filings: {
        last_run: {
          status: 'failed', trigger: 'scheduled', scope: 'family', ticker: null,
          job: 'filings', reason: null, finished_at: '2026-10-22T07:00:00+00:00',
          ok_count: 0, target_count: 3,
          failures: [{ target: '*', reason: 'edgar down' }],
        },
      },
    })))
    render(Data)
    expect(await screen.findByText(/跳过原因：FRED_API_KEY 未配置/)).toBeTruthy()
    const fredCard = screen.getByRole('article', { name: 'FRED 利差' })
    expect(within(fredCard).getAllByText('workbench').length).toBeGreaterThan(0)
    const secCard = screen.getByRole('article', { name: 'SEC 申报' })
    expect(within(secCard).getAllByText('filings').length).toBeGreaterThan(0)   // 记录来源 ≠ 注册任务
    expect(within(secCard).getByText(/edgar down/)).toBeTruthy()
  })

  it('shows last success time and the per-family coverage note', async () => {
    vi.mocked(api.getDataFamilies).mockResolvedValue(overview(six({
      bars: { last_success_at: '2026-10-22T06:30:00+00:00' },
      fred: { coverage: { targets: 2, covered: 2, lagging: ['BAMLH0A0HYM2', 'T10Y2Y'] } },
    })))
    render(Data)
    expect(await screen.findByText(/最近成功/)).toHaveTextContent(
      fmtBeijing('2026-10-22T06:30:00+00:00'))
    expect(screen.getAllByText(/（覆盖说明）/).length).toBeGreaterThan(0)   // M04：各族口径一句话
  })

  it('shows the universe file error separately from run history', async () => {
    vi.mocked(api.getDataFamilies).mockResolvedValue(overview(six({
      universe: { current_error: 'bad member count: 2', coverage: { targets: 0, covered: 0, lagging: [] } },
    })))
    render(Data)
    expect(await screen.findByText(/当前范围文件错误：bad member count: 2/)).toBeTruthy()
  })

  it('shows migration notice', async () => {
    vi.mocked(api.getDataFamilies).mockResolvedValue(overview(six(), { fetch_runs_ready: false }))
    render(Data)
    expect(await screen.findByText('迁移 024 未执行')).toBeTruthy()
    expect(screen.getAllByText('10 / 10')).toHaveLength(6)
  })

  it('ATR fetch calls bars', async () => {
    render(Data)
    await screen.findByText('ATR')
    await fireEvent.click(screen.getByRole('button', { name: '拉取日线' }))
    await waitFor(() => expect(api.fetchDataFamily).toHaveBeenCalledWith('bars', undefined))
  })

  it('busy fetch shows message', async () => {
    vi.mocked(api.fetchDataFamily).mockRejectedValue(new Error('该族正在采集'))
    render(Data)
    await screen.findByText('日线')
    await fireEvent.click(screen.getAllByRole('button', { name: '拉取' })[0])
    expect(await screen.findByText('该族正在采集')).toBeTruthy()
    expect((screen.getAllByRole('button', { name: '拉取' })[0] as HTMLButtonElement).disabled).toBe(false)
  })

  it('refreshes card after run completes', async () => {
    vi.mocked(api.getDataFamilies).mockResolvedValueOnce(overview(six()))
      .mockResolvedValue(overview(six({ bars: { coverage: { targets: 10, covered: 9, lagging: ['MU'] } } })))
    render(Data)
    await screen.findByText('日线')
    await fireEvent.click(screen.getAllByRole('button', { name: '拉取' })[0])
    await waitFor(() => expect(api.getDataFamilies).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('9 / 10')).toBeTruthy()
  })

  it('shows still-running on poll timeout', async () => {
    vi.mocked(api.pollRun).mockRejectedValue(new Error('run timeout'))
    render(Data)
    await screen.findByText('日线')
    await fireEvent.click(screen.getAllByRole('button', { name: '拉取' })[0])
    const message = await screen.findByText('仍在后台运行，稍后刷新')
    expect(message).toBeTruthy()
    expect(message).toHaveClass('text-muted-foreground')
    expect(message.className).not.toMatch(/text-(?:red|danger)/)
    expect(screen.getAllByRole('button', { name: '拉取' })[0]).toBeEnabled()
    expect(api.pollRun).toHaveBeenCalledWith('r1', { timeoutMs: 900_000 })
  })

  it('shows backend 503 text', async () => {
    vi.mocked(api.getDataFamilies).mockRejectedValue(new Error('需执行迁移 024'))
    render(Data)
    expect(await screen.findByRole('alert')).toHaveTextContent('需执行迁移 024')
  })

  it('job link', async () => {
    vi.mocked(api.getDataFamilies).mockResolvedValue(
      overview(six({ fred: { job: job({ enabled: false, next_run_at: null }) } })))
    render(Data)
    await screen.findByText('日线')
    const barsJob = within(screen.getByRole('article', { name: '日线' })).getByRole('link', { name: /workbench/ })
    expect(barsJob).toHaveAttribute('href', '#tools/tasks')
    expect(barsJob).toHaveTextContent('开')
    expect(barsJob).toHaveTextContent(fmtBeijing('2026-10-23T06:30:00+00:00'))
    const fredJob = within(screen.getByRole('article', { name: 'FRED 利差' })).getByRole('link', { name: /workbench/ })
    expect(fredJob).toHaveTextContent('关')
    expect(fredJob).toHaveTextContent('下次 —')
  })
})

// Boundary regressions from self-review: a finished runner may be error or may
// have lost the lock after the API's probe. Neither is an unqualified success.
describe('Data 拉取结果', () => {
  it('keeps fetch-record write errors visible after a successful overview refresh', async () => {
    vi.mocked(api.pollRun).mockResolvedValue({
      run_id: 'r1', status: 'done', result: {
        status: 'success',
        data_status: { written: 0, error: 'permission denied for table data_fetch_runs' },
      },
    })
    vi.mocked(api.getDataFamilies).mockResolvedValueOnce(overview(six()))
      .mockResolvedValue(overview(six({ bars: { coverage: { targets: 10, covered: 9, lagging: ['MU'] } } })))
    render(Data)
    const bars = within(await screen.findByRole('article', { name: '日线' }))
    await fireEvent.click(bars.getByRole('button', { name: '拉取' }))
    expect(await bars.findByText('9 / 10')).toBeTruthy()
    expect(api.getDataFamilies).toHaveBeenCalledTimes(2)
    expect(bars.getByText('采集记录写入失败：permission denied for table data_fetch_runs')).toHaveClass('text-destructive')
    expect(bars.getByRole('button', { name: '拉取' })).toBeEnabled()
  })

  it('shows runner error and refreshes the overview after completion', async () => {
    vi.mocked(api.pollRun).mockResolvedValue({ run_id: 'r1', status: 'error', error: '采集服务不可达' })
    render(Data)
    await fireEvent.click(await screen.findByRole('button', { name: '重新加载' }))
    expect(await screen.findByText('采集服务不可达')).toHaveClass('text-destructive')
    expect(api.getDataFamilies).toHaveBeenCalledTimes(2)
    await waitFor(() => expect(screen.getByRole('button', { name: '重新加载' })).toBeEnabled())
  })

  it('shows busy result when the background task loses the lock', async () => {
    vi.mocked(api.pollRun).mockResolvedValue({ run_id: 'r1', status: 'done', result: { status: 'busy' } })
    render(Data)
    await fireEvent.click(await screen.findByRole('button', { name: '重新加载' }))
    expect(await screen.findByText('该族正在采集')).toBeTruthy()
    await waitFor(() => expect(screen.getByRole('button', { name: '重新加载' })).toBeEnabled())
  })
})

describe('Data API wrappers', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })))
  })

  it('sends fetch requests with optional ticker, and browse filters in the URL', async () => {
    await api.fetchDataFamily('bars', 'MU')
    expect(fetch).toHaveBeenLastCalledWith('/api/data/families/bars/fetch', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ ticker: 'MU' }),
    }))
    await api.fetchDataFamily('universe')
    expect(fetch).toHaveBeenLastCalledWith('/api/data/families/universe/fetch', expect.objectContaining({
      method: 'POST', body: '{}',
    }))
    await api.browseData('bars', { ticker: 'MU' })
    expect(fetch).toHaveBeenLastCalledWith('/api/data/browse/bars?ticker=MU', expect.anything())
    await api.browseData('fred', { series: 'T10Y2Y' })
    expect(fetch).toHaveBeenLastCalledWith('/api/data/browse/fred?series=T10Y2Y', expect.anything())
  })

  it('preserves backend 503 details for all three GET wrappers', async () => {
    vi.mocked(fetch).mockImplementation(async () => new Response(JSON.stringify({ error: '需执行迁移 024' }), { status: 503 }))
    await expect(api.getDataFamilies()).rejects.toThrow('需执行迁移 024')
    await expect(api.getDataFamily('bars')).rejects.toThrow('需执行迁移 024')
    await expect(api.browseData('bars', { ticker: 'MU' })).rejects.toThrow('需执行迁移 024')
  })
})


const run = (overrides: Partial<FetchRun> = {}): FetchRun => ({
  id: 11, created_at: '2026-10-22T08:00:00+00:00', family: 'bars', job: 'workbench',
  trigger: 'manual_family', run_id: 'r1', scope: 'family', ticker: null, session: '2026-10-22',
  status: 'partial', target_count: 2, ok_count: 1,
  failures: [{ target: 'NVDA', reason: '源超时' }], detail: {},
  started_at: '2026-10-22T07:00:00+00:00', finished_at: '2026-10-22T08:00:00+00:00',
  ...overrides,
})
const detail = (extra: Partial<FamilyDetail> = {}): FamilyDetail => ({
  card: card('bars'), runs: [run()], rows: [
    { target: 'MU', latest_at: '2026-10-22', lagging: false, last_failure: '整族连接失败' },
    { target: 'NVDA', latest_at: '2026-10-20', lagging: true, last_failure: '源超时' },
  ], ...extra,
})

describe('Data 详情', () => {
  it('detail shows runs and coverage rows', async () => {
    render(Data, { sub: 'bars' })
    const runs = within(await screen.findByRole('table', { name: '最近拉取' }))
    expect(runs.getByText('手动拉取')).toBeTruthy()
    expect(runs.getByText('partial')).toBeTruthy()
    expect(runs.getByText('1/2')).toBeTruthy()
    expect(runs.getByText(fmtBeijing('2026-10-22T08:00:00+00:00'))).toBeTruthy()
    expect(runs.getByText(/NVDA.*源超时/)).toBeTruthy()
    expect(screen.getByRole('link', { name: '运行记录' })).toHaveAttribute('href', '#tools/runs')
    const coverage = within(screen.getByRole('table', { name: '逐目标覆盖' }))
    expect(coverage.getByText('MU')).toBeTruthy()
    expect(coverage.getByText('整族连接失败')).toBeTruthy()
    expect(coverage.getByText('NVDA')).toBeTruthy()
    expect(coverage.getByText('2026-10-20')).toBeTruthy()
    expect(await screen.findByLabelText('证据说明')).toHaveTextContent(EVIDENCE)
    const calls = vi.mocked(api.getDataFamily).mock.calls.length
    await fireEvent.click(screen.getByRole('checkbox', { name: '仅看滞后' }))
    expect(coverage.queryByText('MU')).toBeNull()
    expect(coverage.getByText('NVDA')).toBeTruthy()
    expect(api.getDataFamily).toHaveBeenCalledTimes(calls)
    expect(api.browseData).toHaveBeenCalledWith('bars', { ticker: 'MU' })
  })

  it('fetch status pill text', async () => {
    vi.mocked(api.getDataFamily).mockResolvedValue(detail({
      runs: [run({ id: 31, status: 'success' }), run({ id: 32, status: 'partial' }), run({ id: 33, status: 'failed' }),
             run({ id: 34, status: 'skipped', failures: [], detail: { reason: 'FRED_API_KEY 未配置' } })],
    }))
    render(Data, { sub: 'bars' })
    const runs = within(await screen.findByRole('table', { name: '最近拉取' }))
    // the status column is a fetch-domain pill: the text is shown with its tone, never colour alone
    const tones = ['success', 'partial', 'failed', 'skipped'].map(status => {
      const pill = runs.getByText(status).closest('[data-domain]')!
      expect(pill).toHaveAttribute('data-domain', 'fetch')
      expect(pill).toHaveAttribute('data-status', status)
      return pill.getAttribute('data-tone')
    })
    expect(tones).toEqual(['success', 'warn', 'danger', 'neutral'])
    // and the same pill sits on the overview cards
    cleanup()
    vi.mocked(api.getDataFamilies).mockResolvedValue(overview(six({
      news: { last_run: { status: 'partial', trigger: 'manual_batch', scope: 'family', ticker: null, job: 'workbench', reason: null,
                          finished_at: '2026-10-22T08:00:00+00:00', ok_count: 3, target_count: 5, failures: [] } } })))
    render(Data)
    const main = within(await screen.findByRole('article', { name: '资讯' })).getByLabelText('最近全族拉取')
    expect(main.querySelector('[data-domain="fetch"]')).toHaveAttribute('data-tone', 'warn')
  })

  it('unknown sub falls back to overview', async () => {
    render(Data, { sub: 'signals' })
    expect(await screen.findByRole('heading', { name: '数据' })).toBeTruthy()
    expect(screen.getAllByRole('article')).toHaveLength(6)
    expect(api.getDataFamily).not.toHaveBeenCalled()
  })

  it('keeps the card refresh wired to detail, runs, coverage and raw values', async () => {
    vi.mocked(api.getDataFamily).mockResolvedValueOnce(detail()).mockResolvedValue(detail({
      card: card('bars', { coverage: { targets: 2, covered: 1, lagging: ['NVDA'] } }),
      runs: [run({ id: 12, status: 'success', ok_count: 2, failures: [] })],
      rows: [{ target: 'MU', latest_at: '2026-10-23', lagging: false, last_failure: null }],
    }))
    render(Data, { sub: 'bars' })
    await screen.findByRole('table', { name: '逐目标覆盖' })
    await waitFor(() => expect(api.browseData).toHaveBeenCalledTimes(1))
    await fireEvent.click(within(screen.getByRole('article', { name: '日线' })).getByRole('button', { name: '拉取' }))
    expect(await screen.findByText('1 / 2')).toBeTruthy()
    expect(within(screen.getByRole('table', { name: '最近拉取' })).getByText('success')).toBeTruthy()
    expect(within(screen.getByRole('table', { name: '逐目标覆盖' })).getByText('2026-10-23')).toBeTruthy()
    expect(api.getDataFamilies).toHaveBeenCalledTimes(2)
    expect(api.getDataFamily).toHaveBeenCalledTimes(2)
    await waitFor(() => expect(api.browseData).toHaveBeenCalledTimes(2))
  })

  it('single fetch refreshes detail without resetting the selected ticker', async () => {
    render(Data, { sub: 'bars' })
    const input = await screen.findByRole('combobox', { name: '标的' })
    await fireEvent.input(input, { target: { value: 'nvda' } })
    await fireEvent.submit(input.closest('form')!)
    await waitFor(() => expect(api.browseData).toHaveBeenLastCalledWith('bars', { ticker: 'NVDA' }))
    await fireEvent.click(screen.getByRole('button', { name: '只拉这一只' }))
    await waitFor(() => expect(api.getDataFamily).toHaveBeenCalledTimes(2))
    expect(api.fetchDataFamily).toHaveBeenCalledWith('bars', 'NVDA')
    expect(input).toHaveValue('NVDA')
    expect(api.browseData).toHaveBeenLastCalledWith('bars', { ticker: 'NVDA' })
  })

  it('refreshes the current detail when an overview fetch finishes after navigation', async () => {
    let finish!: (value: api.RunRecord) => void
    vi.mocked(api.pollRun).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const view = render(Data)
    const bars = within(await screen.findByRole('article', { name: '日线' }))
    await fireEvent.click(bars.getByRole('button', { name: '拉取' }))
    await waitFor(() => expect(api.pollRun).toHaveBeenCalledTimes(1))
    await view.rerender({ sub: 'bars' })
    await screen.findByRole('table', { name: '逐目标覆盖' })
    finish({ run_id: 'r1', status: 'done', result: { status: 'success' } })
    await waitFor(() => expect(api.getDataFamilies).toHaveBeenCalledTimes(3))
    await waitFor(() => expect(api.getDataFamily).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('table', { name: '逐目标覆盖' })).toBeTruthy()
    expect(screen.getAllByRole('article')).toHaveLength(1)
  })

  it('shows detail backend errors instead of a placeholder', async () => {
    vi.mocked(api.getDataFamily).mockRejectedValue(new Error('数据库不可达'))
    render(Data, { sub: 'news' })
    expect(await screen.findByRole('alert')).toHaveTextContent('数据库不可达')
    expect(screen.queryByText(/建设中/)).toBeNull()
  })

  it('reacts to known sub changes and ignores stale detail responses', async () => {
    let finish!: (v: FamilyDetail) => void
    vi.mocked(api.getDataFamily).mockImplementation(id => id === 'bars'
      ? new Promise(resolve => { finish = resolve })
      : Promise.resolve(detail({ card: card('fred'), rows: [], runs: [] })))
    const view = render(Data, { sub: 'bars' })
    await waitFor(() => expect(api.getDataFamily).toHaveBeenCalledWith('bars'))
    await view.rerender({ sub: 'fred' })
    expect(await screen.findByRole('article', { name: 'FRED 利差' })).toBeTruthy()
    finish(detail())
    await waitFor(() => expect(screen.queryByRole('article', { name: '日线' })).toBeNull())
    expect(screen.getByRole('combobox', { name: 'FRED 序列' })).toBeTruthy()
  })

  it('detail runs show the task path, its target source and skipped reasons', async () => {
    vi.mocked(api.getDataFamily).mockResolvedValue(detail({
      runs: [
        run({ id: 21, family: 'sec_filings', job: 'filings', trigger: 'scheduled', status: 'failed',
              target_count: 1, ok_count: 0, failures: [{ target: '*', reason: 'edgar down' }],
              detail: { targets_source: 'sec_monitor.watchlist_path', mode: 'monitor' } }),
        run({ id: 22, family: 'fred', trigger: 'scheduled', status: 'skipped',
              target_count: 2, ok_count: 0, failures: [],
              detail: { reason: 'FRED_API_KEY 未配置' } }),
      ],
    }))
    render(Data, { sub: 'bars' })
    const runs = within(await screen.findByRole('table', { name: '最近拉取' }))
    expect(runs.getByText('sec_monitor.watchlist_path')).toBeTruthy()   // I13：目标来源如实展示
    expect(runs.getByText(/跳过原因：FRED_API_KEY 未配置/)).toBeTruthy()  // I11：skipped 原因
    expect(runs.getAllByText('filings').length).toBeGreaterThan(0)
    expect(runs.getByText(/edgar down/)).toBeTruthy()
  })

  it('shows the migration notice from the detail card itself', async () => {
    vi.mocked(api.getDataFamilies).mockResolvedValue(overview(six(), { fetch_runs_ready: true }))
    vi.mocked(api.getDataFamily).mockResolvedValue(detail({
      card: card('bars', { fetch_runs_ready: false }), runs: [], rows: [],
    }))
    render(Data, { sub: 'bars' })
    expect(await screen.findByText('迁移 024 未执行')).toBeTruthy()   // M02：卡片自带 readiness
  })

  it('shows migration notice and empty detail tables', async () => {
    vi.mocked(api.getDataFamilies).mockResolvedValue(overview(six(), { fetch_runs_ready: false }))
    vi.mocked(api.getDataFamily).mockResolvedValue(detail({ runs: [], rows: [] }))
    render(Data, { sub: 'bars' })
    expect(await screen.findByText('迁移 024 未执行')).toBeTruthy()
    // an empty table is a DataBlock with the reason, not a table with an empty body
    expect(screen.getByRole('region', { name: '最近 20 次拉取' })).toHaveTextContent('无记录')
    expect(screen.getByRole('region', { name: '逐目标覆盖' })).toHaveTextContent('无目标')
    expect(screen.queryByRole('table', { name: '最近拉取' })).toBeNull()
  })

  it('family names, ATR columns and FRED series carry ConceptTerm', async () => {
    render(Data)
    await screen.findAllByRole('article')
    // six family names plus the page evidence banner
    expect(await termIds()).toEqual(new Set(['family.bars', 'family.sec_filings', 'family.news', 'indicator.atr14',
                                              'family.fred', 'family.universe', 'system.gate_hold']))
    expect(screen.getByRole('link', { name: '日线' })).toHaveAttribute('href', '#data/bars')
    expect(screen.getByRole('heading', { name: '日线' })).toBeTruthy()      // the card heading keeps its name
    expect(screen.getByLabelText('证据说明')).toHaveTextContent(EVIDENCE)
  })
})
