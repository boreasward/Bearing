import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte'
import DataBrowse from './DataBrowse.svelte'
import * as api from '../api'
import { resetConceptIndex } from '../knowledge'

const conceptIndexOf = (ids: string[]) => ({
  knowledge_version: 'knowledge-1', groups: [],
  concepts: ids.map(id => ({ id, ref: `concept:${id}@1`, title: `T:${id}`, group: id.split('.')[0], version: 1,
                             applies_to: [], summary: `S:${id}`, related: [] })),
})
const termIds = async () => new Set((await screen.findAllByRole('button', { name: /^概念：/, hidden: true }))
  .map(b => b.getAttribute('aria-label')!.replace(/^概念：T:/, '')))
import { fmtBeijing } from '../format'
import type { BrowseResult, FamilyCard, FamilyId } from '../data-families'

const EVIDENCE = '这里只展示采集事实。所有规则都未经验证（Phase 0 Gate HOLD）：数据完整不代表信号有效。'
const family = (id: FamilyId): FamilyCard => ({
  id, title: id, purpose: '用途', kind: id === 'atr' ? 'derived' : id === 'universe' ? 'manual' : 'fetched',
  fetch_via: id === 'atr' ? 'bars' : null, per_ticker: ['bars', 'news', 'sec_filings'].includes(id),
  job: null, coverage: { targets: 2, covered: 1, lagging: ['NVDA'] }, latest_at: '2026-10-22',
  coverage_note: '覆盖说明', last_run: null, last_ticker_run: null, last_success_at: null,
  evidence: id === 'atr' ? EVIDENCE + ' `plan-1` 的计算输入，规则 `gate_hold`' : EVIDENCE, notes: [],
  current_error: null, fetch_runs_ready: true,
})
const bars: BrowseResult = {
  family: 'bars', key: 'MU', has_data: true, rows: [{
    price_date: '2026-10-22', open: 101, high: 105, low: 99, close: 103,
    adj_close: 102.5, volume: 200001, split_factor: 2, revisions: 3,
    fetched_at: '2026-10-23T00:05:00+00:00', note: '拆股调整、未分红调整；adj_close 全调整',
  }],
}
const newsRow = {
  id: 7, title: 'Micron outlook', source: 'yahoo', publisher: 'Reuters', url: 'https://example.com/news',
  published_at: '2026-10-22T14:30:00+00:00', fetched_at: '2026-10-22T14:40:00+00:00',
}

beforeEach(() => {
  vi.restoreAllMocks()
  resetConceptIndex()
  vi.spyOn(api, 'getConcepts').mockResolvedValue(conceptIndexOf(
    ['family.bars', 'indicator.atr14', 'indicator.hy_oas', 'indicator.t10y2y', 'system.screen']))
  vi.spyOn(api, 'browseData').mockResolvedValue(bars)
  vi.spyOn(api, 'fetchDataFamily').mockResolvedValue({ run_id: 'one', status: 'pending' })
  vi.spyOn(api, 'pollRun').mockResolvedValue({ run_id: 'one', status: 'done', result: { status: 'success' } })
})
afterEach(() => vi.restoreAllMocks())

const mount = (id: FamilyId) => render(DataBrowse, { family: family(id), targets: ['MU', 'NVDA'] })

describe('DataBrowse 原始值', () => {
  it('bars table', async () => {
    mount('bars')
    const table = await screen.findByRole('table', { name: '日线原始值' })
    expect(table).toHaveTextContent('2026-10-22')
    for (const label of ['日期', '开', '高', '低', '收', 'adj_close', '成交量', '修订次数', '采集时间']) {
      expect(within(table).getByRole('columnheader', { name: label })).toBeTruthy()
    }
    for (const value of ['101', '105', '99', '103', '102.5', '200001', '3']) {
      expect(within(table).getByRole('cell', { name: value })).toBeTruthy()
    }
    expect(table).toHaveTextContent(fmtBeijing('2026-10-23T00:05:00+00:00'))
    expect(screen.getByText('拆股调整、未分红调整；adj_close 全调整')).toBeTruthy()
    expect(api.browseData).toHaveBeenCalledWith('bars', { ticker: 'MU' })
  })

  it('switch ticker and fetch one', async () => {
    mount('bars')
    await screen.findByText('2026-10-22')
    const input = screen.getByRole('combobox', { name: '标的' })
    await fireEvent.input(input, { target: { value: ' nvda ' } })
    await fireEvent.keyDown(input, { key: 'Enter' })
    // JSDOM does not perform the browser's implicit form submit on Enter.
    await fireEvent.submit(input.closest('form')!)
    await waitFor(() => expect(api.browseData).toHaveBeenLastCalledWith('bars', { ticker: 'NVDA' }))
    expect(input).toHaveValue('NVDA')
    await fireEvent.click(screen.getByRole('button', { name: '只拉这一只' }))
    await waitFor(() => expect(api.fetchDataFamily).toHaveBeenCalledWith('bars', 'NVDA'))
    expect(await screen.findByText('拉取完成')).toBeTruthy()
    expect(api.pollRun).toHaveBeenCalledWith('one', { timeoutMs: 900_000 })
    expect(screen.getByRole('button', { name: '只拉这一只' })).toBeEnabled()
    expect(api.browseData).toHaveBeenLastCalledWith('bars', { ticker: 'NVDA' })
  })

  it('fred series switch', async () => {
    vi.mocked(api.browseData).mockResolvedValue({
      family: 'fred', key: 'BAMLH0A0HYM2', has_data: true,
      rows: [{ obs_date: '2026-10-21', value: -0.42, revisions: 4 }],
    })
    mount('fred')
    expect(await screen.findByRole('table', { name: 'FRED 原始值' })).toHaveTextContent('-0.42')
    expect(screen.getByRole('cell', { name: '4' })).toBeTruthy()
    expect(api.browseData).toHaveBeenCalledWith('fred', { series: 'BAMLH0A0HYM2' })
    await fireEvent.change(screen.getByRole('combobox', { name: 'FRED 序列' }), { target: { value: 'T10Y2Y' } })
    await waitFor(() => expect(api.browseData).toHaveBeenLastCalledWith('fred', { series: 'T10Y2Y' }))
    expect(screen.queryByRole('button', { name: '只拉这一只' })).toBeNull()
    expect(screen.queryByRole('combobox', { name: '标的' })).toBeNull()
  })

  it('atr note and chronological values', async () => {
    vi.mocked(api.browseData).mockResolvedValue({
      family: 'atr', key: 'MU', has_data: true, note: '现算，与 `plan-1` 同一函数；不入库', rows: [
        { price_date: '2026-10-21', close: 100, atr: 3, atr_pct: 0.03 },
        { price_date: '2026-10-22', close: 120, atr: 4.8, atr_pct: 0.04 },
      ],
    })
    render(DataBrowse, { family: family('atr'), targets: ['MU', 'NVDA'], pageEvidence: EVIDENCE })
    const note = await screen.findByText((_, el) => el?.tagName === 'P'
      && el.textContent === '现算，与 plan-1 同一函数；不入库')
    expect(note.querySelector('code')?.textContent).toBe('plan-1')
    // Only the ATR supplement: the page banner above already carries the shared evidence.
    const evidence = screen.getByText((_, el) => el?.tagName === 'P'
      && el.textContent === 'plan-1 的计算输入，规则 gate_hold')
    expect(evidence).not.toHaveTextContent(EVIDENCE)
    const rows = within(screen.getByRole('table', { name: 'ATR 原始值' })).getAllByRole('row')
    expect(rows[1]).toHaveTextContent('2026-10-21')
    expect(rows[1]).toHaveTextContent('3.0%')
    expect(rows[2]).toHaveTextContent('2026-10-22')
    expect(rows[2]).toHaveTextContent('4.8')
    expect(rows[2]).toHaveTextContent('4.0%')
    expect(screen.getByRole('combobox', { name: '标的' })).toBeTruthy()
    // ATR is browse-by-ticker but not a per-ticker fetch endpoint.
    expect(screen.queryByRole('button', { name: '只拉这一只' })).toBeNull()
  })

  it('news shows title source publisher link and Beijing time including undated news', async () => {
    vi.mocked(api.browseData).mockResolvedValue({ family: 'news', key: 'MU', has_data: true, rows: [
      newsRow, { ...newsRow, id: 8, title: 'Undated item', published_at: null },
    ] })
    mount('news')
    const table = await screen.findByRole('table', { name: '资讯原始值' })
    expect(within(table).getByRole('link', { name: 'Micron outlook' })).toHaveAttribute('href', newsRow.url)
    expect(table).toHaveTextContent('yahoo')
    expect(table).toHaveTextContent('Reuters')
    expect(table).toHaveTextContent(fmtBeijing(newsRow.published_at))
    expect(within(table).getByRole('cell', { name: '—' })).toBeTruthy()
  })

  it('sec filings items TEXT[] cells join with commas, never as JSON', async () => {
    vi.mocked(api.browseData).mockResolvedValue({
      family: 'sec_filings', key: 'MU', has_data: true, rows: 1,
      filings: [{ id: 2, ticker: 'MU', form: '8-K', filing_date: '2026-10-21', accession_number: '0001-26-000124',
        primary_document: 'eight.htm', accepted_at: '2026-10-21T18:00:00+00:00', items: ['2.02', '9.01'] }],
      sec_8k: [],
    })
    mount('sec_filings')
    const filings = await screen.findByRole('table', { name: 'filings' })
    expect(within(filings).getByRole('cell', { name: '2.02, 9.01' })).toBeTruthy()
    expect(filings.textContent).not.toContain('["2.02"')
  })

  it('sec shows both lists with count-shaped rows', async () => {
    vi.mocked(api.browseData).mockResolvedValue({
      family: 'sec_filings', key: 'MU', has_data: true, rows: 3,
      filings: [{ id: 1, ticker: 'MU', form: '10-Q', filing_date: '2026-10-20',
        accession_number: '0001-26-000123', primary_document: 'quarter.htm',
        accepted_at: '2026-10-20T18:00:00+00:00', items: '2.02' }],
      sec_8k: [{ ...newsRow, source: 'sec_8k', title: 'Unfiled 8-K', filing_date_note: null },
               { ...newsRow, id: 9, source: 'sec_8k', title: 'Undated 8-K', url: 'https://example.com/x',
                 published_at: null, filing_date_note: '申报日未知' }],
    })
    mount('sec_filings')
    const filings = await screen.findByRole('table', { name: 'filings' })
    expect(filings).toHaveTextContent('10-Q')
    expect(filings).toHaveTextContent('0001-26-000123')
    expect(filings).toHaveTextContent('quarter.htm')
    expect(filings).toHaveTextContent('2.02')
    expect(filings).toHaveTextContent(fmtBeijing('2026-10-20T18:00:00+00:00'))
    const sec = screen.getByRole('table', { name: 'sec_8k（未入 filings）' })
    expect(within(sec).getByRole('link', { name: 'Unfiled 8-K' })).toHaveAttribute('href', newsRow.url)
    // I14：无 accepted_at 的条目标注「申报日未知」，不用采集时间冒充申报日期
    expect(within(sec).getByRole('columnheader', { name: '申报日期' })).toBeTruthy()
    expect(within(sec).getByRole('cell', { name: '申报日未知' })).toBeTruthy()
    expect(within(sec).getAllByRole('cell', { name: '—' }).length).toBeGreaterThan(0)
  })

  it('universe members', async () => {
    vi.mocked(api.browseData).mockResolvedValue({
      family: 'universe', key: null, has_data: true,
      meta: { name: 'nasdaq100', as_of: '2026-09-01', source: 'https://example.com/universe', note: '手动核对成分' },
      rows: [{ ticker: 'MU', latest_bar: '2026-10-22' }, { ticker: 'NVDA', latest_bar: null }],
    })
    mount('universe')
    expect(await screen.findByText('nasdaq100')).toBeTruthy()
    expect(screen.getByText('2026-09-01')).toBeTruthy()
    expect(screen.getByText('https://example.com/universe')).toBeTruthy()
    expect(screen.getByText('手动核对成分')).toBeTruthy()
    const table = screen.getByRole('table', { name: '范围成员' })
    expect(table).toHaveTextContent('MU')
    expect(table).toHaveTextContent('2026-10-22')
    expect(table).toHaveTextContent('NVDA')
    expect(table).toHaveTextContent('—')
    expect(api.browseData).toHaveBeenCalledWith('universe', {})
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(screen.queryByRole('button', { name: '只拉这一只' })).toBeNull()
  })

  it('unavailable universe shows notes with null meta', async () => {
    vi.mocked(api.browseData).mockResolvedValue({ family: 'universe', key: null, has_data: false,
      rows: [], meta: null, notes: ['universe_unavailable'],
      current_error: 'bad member count: 2' })
    mount('universe')
    expect(await screen.findByText('范围文件不可读或非法')).toBeTruthy()
    expect(screen.getByText('bad member count: 2')).toBeTruthy()   // M03：原文诊断可见
    expect(screen.getByText('无数据，可点拉取')).toBeTruthy()
  })

  it('empty browse for an unknown ticker is not an error or an implicit fetch', async () => {
    vi.mocked(api.browseData).mockResolvedValue({ family: 'bars', key: 'UNKNOWN', has_data: false, rows: [] })
    render(DataBrowse, { family: family('bars'), targets: ['UNKNOWN'] })
    expect(await screen.findByText('无数据，可点拉取')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(api.fetchDataFamily).not.toHaveBeenCalled()
  })

  it('allows manual entry with no targets and rejects an empty selection locally', async () => {
    render(DataBrowse, { family: family('bars'), targets: [] })
    const input = screen.getByRole('combobox', { name: '标的' })
    expect(api.browseData).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '只拉这一只' })).toBeDisabled()
    await fireEvent.input(input, { target: { value: ' mu ' } })
    await fireEvent.submit(input.closest('form')!)
    expect(await screen.findByText('2026-10-22')).toBeTruthy()
    expect(api.browseData).toHaveBeenCalledWith('bars', { ticker: 'MU' })
    await fireEvent.input(input, { target: { value: '  ' } })
    await fireEvent.submit(input.closest('form')!)
    expect(screen.getByRole('button', { name: '只拉这一只' })).toBeDisabled()
  })

  it('shows browse backend errors, not a no-data result', async () => {
    vi.mocked(api.browseData).mockRejectedValue(new Error('需执行迁移 024'))
    mount('bars')
    expect(await screen.findByRole('alert')).toHaveTextContent('需执行迁移 024')
    expect(screen.queryByText('无数据，可点拉取')).toBeNull()
  })

  it('ignores stale browse responses after changing ticker', async () => {
    let finish!: (result: BrowseResult) => void
    vi.mocked(api.browseData).mockImplementation((_id, params) => params?.ticker === 'MU'
      ? new Promise(resolve => { finish = resolve })
      : Promise.resolve({ family: 'bars', key: 'NVDA', has_data: false, rows: [] }))
    mount('bars')
    await waitFor(() => expect(api.browseData).toHaveBeenCalledWith('bars', { ticker: 'MU' }))
    const input = screen.getByRole('combobox', { name: '标的' })
    await fireEvent.input(input, { target: { value: 'NVDA' } })
    await fireEvent.submit(input.closest('form')!)
    expect(await screen.findByText('无数据，可点拉取')).toBeTruthy()
    finish(bars)
    await waitFor(() => expect(screen.queryByText('2026-10-22')).toBeNull())
  })
})

describe('DataBrowse 单只拉取结果', () => {
  it('shows 409 and restores the button', async () => {
    vi.mocked(api.fetchDataFamily).mockRejectedValue(new Error('该族正在采集'))
    mount('bars')
    await screen.findByText('2026-10-22')
    await fireEvent.click(screen.getByRole('button', { name: '只拉这一只' }))
    expect(await screen.findByText('该族正在采集')).toHaveClass('text-destructive')
    expect(screen.getByRole('button', { name: '只拉这一只' })).toBeEnabled()
  })

  it.each([
    { run_id: 'one', status: 'done' as const, result: { status: 'busy' }, message: '该族正在采集' },
    { run_id: 'one', status: 'error' as const, error: '采集服务不可达', message: '采集服务不可达' },
    { run_id: 'one', status: 'done' as const, result: { status: 'partial', row: { failures: [{ target: 'MU', reason: '源超时' }] } }, message: '部分失败' },
    { run_id: 'one', status: 'done' as const, result: { status: 'failed', row: { failures: [{ target: '*', reason: '连接失败' }] } }, message: '拉取失败' },
    { run_id: 'one', status: 'done' as const, result: { status: 'success', data_status: { error: 'permission denied' } }, message: '采集记录写入失败：permission denied' },
  ])('keeps $message visible after refreshing raw values', async ({ message, ...run }) => {
    vi.mocked(api.pollRun).mockResolvedValue(run)
    mount('bars')
    await screen.findByText('2026-10-22')
    await fireEvent.click(screen.getByRole('button', { name: '只拉这一只' }))
    expect(await screen.findByText(message, { exact: false })).toHaveClass('text-destructive')
    if (run.result?.status === 'partial') expect(screen.getByText(/MU：源超时/)).toBeTruthy()
    if (run.result?.status === 'failed') expect(screen.getByText(/\*：连接失败/)).toBeTruthy()
    await waitFor(() => expect(api.browseData).toHaveBeenCalledTimes(2))
    expect(screen.queryByText('拉取完成')).toBeNull()
    expect(screen.getByRole('button', { name: '只拉这一只' })).toBeEnabled()
  })

  const outcomes: { name: string; message: string; run?: api.RunRecord }[] = [
    { name: 'timeout', message: '仍在后台运行，稍后刷新' },
    { name: 'error', message: '采集服务不可达', run: { run_id: 'one', status: 'error', error: '采集服务不可达' } },
    { name: 'success', message: '拉取完成', run: { run_id: 'one', status: 'done', result: { status: 'success' } } },
  ]

  it.each(outcomes)('keeps $name for the same ticker refresh but hides it after switching ticker', async ({ message, run }) => {
    if (run) vi.mocked(api.pollRun).mockResolvedValue(run)
    else vi.mocked(api.pollRun).mockRejectedValue(new Error('run timeout'))
    vi.mocked(api.browseData).mockImplementation(async (id, params) => params?.ticker === 'NVDA'
      ? { family: id, key: 'NVDA', has_data: false, rows: [] }
      : bars)
    const view = mount('bars')
    await screen.findByText('2026-10-22')
    await fireEvent.click(screen.getByRole('button', { name: '只拉这一只' }))
    expect(await screen.findByText(message)).toBeTruthy()
    await waitFor(() => expect(screen.getByRole('button', { name: '只拉这一只' })).toBeEnabled())

    await view.rerender({ family: family('bars'), targets: ['MU', 'NVDA'] })
    await screen.findByRole('table', { name: '日线原始值' })
    expect(screen.getByText(message)).toBeTruthy()

    const input = screen.getByRole('combobox', { name: '标的' })
    await fireEvent.input(input, { target: { value: 'nvda' } })
    await fireEvent.submit(input.closest('form')!)
    await screen.findByText('无数据，可点拉取')
    expect(input).toHaveValue('NVDA')
    expect(screen.queryByText(message)).toBeNull()
    expect(api.fetchDataFamily).toHaveBeenCalledTimes(1)
  })

  it.each(outcomes)('does not assign a late $name to a different family', async ({ message, run }) => {
    let finish!: (value: api.RunRecord) => void
    let reject!: (reason: Error) => void
    vi.mocked(api.pollRun).mockImplementation(() => new Promise((resolve, fail) => {
      finish = resolve
      reject = fail
    }))
    const view = mount('bars')
    await screen.findByText('2026-10-22')
    await fireEvent.click(screen.getByRole('button', { name: '只拉这一只' }))
    await waitFor(() => expect(api.pollRun).toHaveBeenCalledTimes(1))
    await view.rerender({ family: family('news'), targets: ['MU', 'NVDA'] })
    await waitFor(() => expect(api.browseData).toHaveBeenLastCalledWith('news', { ticker: 'MU' }))
    if (run) finish(run)
    else reject(new Error('run timeout'))
    await waitFor(() => expect(screen.getByRole('button', { name: '只拉这一只' })).toBeEnabled())
    expect(screen.queryByText(message)).toBeNull()
    expect(api.fetchDataFamily).toHaveBeenCalledWith('bars', 'MU')
    expect(api.browseData).toHaveBeenLastCalledWith('news', { ticker: 'MU' })
  })

  it('uses a neutral 15-minute timeout message', async () => {
    vi.mocked(api.pollRun).mockRejectedValue(new Error('run timeout'))
    mount('bars')
    await screen.findByText('2026-10-22')
    await fireEvent.click(screen.getByRole('button', { name: '只拉这一只' }))
    expect(await screen.findByText('仍在后台运行，稍后刷新')).toHaveClass('text-muted-foreground')
    expect(screen.getByRole('button', { name: '只拉这一只' })).toBeEnabled()
    expect(api.pollRun).toHaveBeenCalledWith('one', { timeoutMs: 900_000 })
  })

  describe('ConceptTerm', () => {
    it('family names, ATR columns and FRED series carry ConceptTerm', async () => {
      // bars: the split / dividend adjustment note
      const bar = mount('bars')
      await screen.findByRole('table', { name: '日线原始值' })
      expect(await termIds()).toEqual(new Set(['family.bars']))
      expect(screen.getByText('拆股调整、未分红调整；adj_close 全调整')).toBeTruthy()
      bar.unmount()

      // atr: the atr and atr_pct column headers
      vi.mocked(api.browseData).mockResolvedValue({ family: 'atr', key: 'MU', has_data: true, rows: [
        { price_date: '2026-10-22', close: 120, atr: 4.8, atr_pct: 0.04 }] })
      const atr = render(DataBrowse, { family: family('atr'), targets: ['MU'] })
      const table = await screen.findByRole('table', { name: 'ATR 原始值' })
      const buttons = await within(table).findAllByRole('button', { name: /^概念：/, hidden: true })
      expect(buttons).toHaveLength(2)
      expect(buttons.every(b => b.getAttribute('aria-label') === '概念：T:indicator.atr14')).toBe(true)
      expect(within(table).getAllByRole('columnheader', { name: /^atr/ })).toHaveLength(2)
      atr.unmount()

      // fred: the selected series
      vi.mocked(api.browseData).mockResolvedValue({ family: 'fred', key: 'BAMLH0A0HYM2', has_data: true,
        rows: [{ obs_date: '2026-10-21', value: -0.42, revisions: 4 }] })
      mount('fred')
      await screen.findByRole('table', { name: 'FRED 原始值' })
      expect(await termIds()).toEqual(new Set(['indicator.hy_oas']))
      await fireEvent.change(screen.getByRole('combobox', { name: 'FRED 序列' }), { target: { value: 'T10Y2Y' } })
      await waitFor(async () => expect(await termIds()).toEqual(new Set(['indicator.t10y2y'])))
    })

    it('universe metadata carries the screen concept', async () => {
      vi.mocked(api.browseData).mockResolvedValue({
        family: 'universe', key: null, has_data: true,
        meta: { name: 'nasdaq100', as_of: '2026-09-01', source: 'https://example.com/universe', note: '手动核对成分' },
        rows: [{ ticker: 'MU', latest_bar: '2026-10-22' }] })
      mount('universe')
      await screen.findByText('nasdaq100')
      expect(await termIds()).toEqual(new Set(['system.screen']))
    })
  })
})
