import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte'
import RunDetail from './RunDetail.svelte'
import * as api from '../api'
import { resetConceptIndex } from '../knowledge'
import { fmtBeijing } from '../format'
import type { FetchFamily, RunDetail as RunDetailView, RunNode } from '../runs'

const RID = 'daily-20261030033000-1a2b3c4d'
const TITLES: Record<string, string> = {
  sec_monitor: 'SEC 监控', collect: '数据采集', screen: '筛选', regime: '市场环境', signals: '信号', plan: '规则计划',
  agent: 'Agent', review: '每日复查', accounts: '账户', push: '推送', retro: '复盘',
}
const IDS = Object.keys(TITLES)
const HISTORICAL_IDS_V4 = ['sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'push', 'retro']

const node = (id: string, over: Partial<RunNode> = {}): RunNode => ({
  id, title: TITLES[id], purpose: `用途-${id}`, concept: 'system.stages', status: 'success', reason: null, reason_text: null,
  attempt: 1, attempts: [{ attempt: 1, status: 'success', started_at: null, finished_at: null, duration_ms: 1200 }],
  started_at: '2026-10-30T03:30:00Z', finished_at: '2026-10-30T03:30:01Z', duration_ms: 1200,
  did: { sentence: `句子-${id}`, input_count: 10, output_count: 8, skips: [], failures: [], degradations: [], retry: null, timings: {} },
  fetch: null, results: null, actions: { resume: false, rerun_agent: [] }, ...over,
})
const target = (name: string, ok: boolean) => ({
  target: name, ok, attempts: ok ? 1 : 3, error_kind: ok ? null : 'timeout', http_status: ok ? 200 : null,
  reason: ok ? null : `timeout after 30s (${name})`, rows: ok ? 2 : null, latest: ok ? '2026-10-29' : null, elapsed_ms: 100,
})
const family = (over: Partial<FetchFamily> = {}): FetchFamily => ({
  family: 'bars', title: '日线', status: 'partial', target_count: 3, ok_count: 2, started_at: '2026-10-30T03:30:00Z',
  finished_at: '2026-10-30T03:35:00Z', scope: null, targets: [target('AAPL', true), target('NVDA', false), target('MSFT', true)],
  failures: [{ target: 'NVDA', reason: 'timeout after 30s (NVDA)' }], legacy: false, ...over,
})

/** A stages-5 daily run that failed in collect: sec_monitor ok, collect failed, nine skipped upstream_failed. */
function failedRun(over: Partial<RunDetailView> = {}): RunDetailView {
  const nodes = IDS.map(id => {
    if (id === 'sec_monitor') return node(id)
    if (id === 'collect') return node(id, {
      status: 'failed', reason: 'aborted: fred 连接失败', reason_text: '数据采集失败', duration_ms: 312000,
      attempt: 2, attempts: [
        { attempt: 1, status: 'failed', started_at: null, finished_at: null, duration_ms: 150000 },
        { attempt: 2, status: 'failed', started_at: null, finished_at: null, duration_ms: 162000 }],
      did: { sentence: '采集 118/131 个目标；13 处失败', input_count: 131, output_count: 118,
        skips: [{ reason: 'a', text: '无新财报', count: 3, samples: ['AAPL'] }],
        failures: [{ target: 'bars:NVDA', reason: 'timeout after 30s' }],
        degradations: [{ kind: 'k', text: '改用缓存', detail: 'yfinance' }],
        retry: { first_error: 'timeout after 30s', waited_s: 60 }, timings: {} },
      fetch: { families: [family()] },
      results: { kind: 'collect', items: [], total: 0, truncated: false },
      actions: { resume: true, rerun_agent: [] },
    })
    return node(id, { status: 'skipped', reason: 'upstream_failed', reason_text: '上游节点失败，未执行', duration_ms: null, attempts: [] })
  })
  return {
    run_id: RID, task: 'daily', kind: 'daily', graph_version: 'daily-1', stages_version: 'stages-5', session: '2026-10-29',
    cutoff_at: '2026-10-30T03:30:00Z', trigger: 'scheduled', status: 'failed',
    headline: '10 月 29 日的运行在「数据采集」失败（已自动重试 1 次）：aborted；其后 9 个节点未执行',
    executions: [{ report_id: 812, trigger_source: 'scheduled', status: 'error', started_at: '2026-10-30T03:30:00Z',
      finished_at: '2026-10-30T03:36:00Z', resume_node: null, attempt: 1 }],
    resume: { allowed: true, node: 'collect', deadline_at: '2026-10-30T21:00:00Z', reason: null },
    nodes, raw_summary: { run_status: 'failed', failed_node: 'collect' }, ...over,
  }
}
const successRun = (over: Partial<RunDetailView> = {}): RunDetailView => ({
  ...failedRun(), status: 'success', headline: '10 月 29 日的运行成功', resume: { allowed: false, node: null, deadline_at: null, reason: null },
  nodes: IDS.map(id => node(id)), ...over,
})

const click = async (name: string | RegExp) => fireEvent.click(await screen.findByRole('button', { name }))
/** The confirm dialogs are Bits dialogs rendered in a portal: the action is named like the button that opened it. */
const confirm = async (name: string) =>
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name }))
const confirmResume = () => confirm('从此节点继续')
const confirmRerun = () => confirm('重新运行 Agent')

beforeEach(() => {
  resetConceptIndex()
  vi.spyOn(api, 'getConcepts').mockRejectedValue(new Error('offline'))
  vi.spyOn(api, 'getRunDetail').mockResolvedValue(failedRun())
})
afterEach(() => { vi.useRealTimers(); cleanup(); vi.restoreAllMocks() })

describe('RunDetail · page', () => {
  it('keeps historical stages-4 at ten nodes with no account stage', async () => {
    vi.mocked(api.getRunDetail).mockResolvedValue(successRun({ stages_version: 'stages-4', nodes: HISTORICAL_IDS_V4.map(id => node(id)) }))
    render(RunDetail, { runId: RID })
    expect(await screen.findByTestId('meta')).toHaveTextContent('daily-1 / stages-4')
    expect(within(screen.getByRole('list', { name: '节点状态图' })).getAllByRole('listitem')).toHaveLength(10)
    expect(screen.queryByRole('button', { name: /^账户：/ })).toBeNull()
  })
  it('headline and meta line', async () => {
    render(RunDetail, { runId: RID })
    expect(await screen.findByTestId('headline')).toHaveTextContent(
      '10 月 29 日的运行在「数据采集」失败（已自动重试 1 次）：aborted；其后 9 个节点未执行')
    const meta = screen.getByTestId('meta')
    expect(meta).toHaveTextContent('任务 daily')
    expect(meta).toHaveTextContent('触发 定时')
    expect(meta).toHaveTextContent('session 2026-10-29')
    expect(meta).toHaveTextContent(`截止 ${fmtBeijing('2026-10-30T03:30:00Z')}`)
    expect(meta).toHaveTextContent('daily-1 / stages-5')
    expect(screen.getByRole('link', { name: '返回运行记录' })).toHaveAttribute('href', '#tools/runs')
    expect(api.getRunDetail).toHaveBeenCalledWith(RID)
    // 一次执行时不列执行记录
    expect(screen.queryByRole('region', { name: '执行记录' })).toBeNull()
  })

  it('executions list when resumed', async () => {
    vi.mocked(api.getRunDetail).mockResolvedValue(failedRun({
      status: 'success', headline: '…（第 2 次执行，从「数据采集」续跑）', resume: { allowed: false, node: null, deadline_at: null, reason: null },
      nodes: IDS.map(id => node(id)),
      executions: [
        { report_id: 812, trigger_source: 'scheduled', status: 'error', started_at: '2026-10-30T03:30:00Z', finished_at: null, resume_node: null, attempt: 1 },
        { report_id: 813, trigger_source: 'resume', status: 'success', started_at: '2026-10-30T05:01:02Z', finished_at: null, resume_node: 'collect', attempt: 2 },
      ],
    }))
    render(RunDetail, { runId: RID })
    const list = await screen.findByRole('region', { name: '执行记录' })
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('第 1 次')
    expect(items[0]).toHaveTextContent('定时')
    expect(items[0]).toHaveTextContent('失败')
    expect(items[1]).toHaveTextContent('第 2 次')
    expect(items[1]).toHaveTextContent('续跑')
    expect(items[1]).toHaveTextContent('从「数据采集」续跑')
    expect(items[1]).toHaveTextContent('成功')
  })

  it('three blocks for selected node', async () => {
    render(RunDetail, { runId: RID })
    // 失败节点默认选中
    const section = await screen.findByRole('region', { name: '数据采集 详情' })
    expect(within(section).getByRole('heading', { name: '做了什么' })).toBeInTheDocument()
    expect(within(section).getByRole('heading', { name: '拉取详情' })).toBeInTheDocument()
    expect(within(section).getByRole('heading', { name: '结果详情' })).toBeInTheDocument()
    expect(section).toHaveTextContent('采集 118/131 个目标；13 处失败')
    expect(section).toHaveTextContent('输入 131 · 产出 118')
    expect(section).toHaveTextContent('耗时 5 分 12 秒')
    expect(section).toHaveTextContent('无新财报 × 3（样本：AAPL）')
    expect(section).toHaveTextContent('bars:NVDA：timeout after 30s')
    expect(section).toHaveTextContent('改用缓存：yfinance')
    expect(section).toHaveTextContent('aborted: fred 连接失败')
    expect(section).toHaveTextContent('第 1 次：失败')
    expect(section).toHaveTextContent('第 2 次：失败')
    // 点别的格子换块；没有拉取详情的节点不画第二块
    await click('规则计划：跳过')
    const plan = screen.getByRole('region', { name: '规则计划 详情' })
    expect(plan).toHaveTextContent('上游节点失败，未执行')
    expect(within(plan).queryByRole('heading', { name: '拉取详情' })).toBeNull()
    expect(screen.queryByRole('region', { name: '数据采集 详情' })).toBeNull()
  })

  it('retry note 第一次失败：…，60 秒后重试成功', async () => {
    vi.mocked(api.getRunDetail).mockResolvedValue(successRun({
      nodes: IDS.map(id => id === 'collect'
        ? node(id, { status: 'partial', did: { ...node(id).did!, retry: { first_error: 'timeout after 30s', waited_s: 60 } } })
        : node(id)),
    }))
    render(RunDetail, { runId: RID })
    expect(await screen.findByText('第一次失败：timeout after 30s，60 秒后重试成功')).toBeInTheDocument()
    cleanup()
    vi.mocked(api.getRunDetail).mockResolvedValue(failedRun())
    render(RunDetail, { runId: RID })
    expect(await screen.findByText('第一次失败：timeout after 30s，60 秒后重试仍失败')).toBeInTheDocument()
  })

  it('failed targets first and 显示全部 n 个目标', async () => {
    render(RunDetail, { runId: RID })
    const fetchBlock = await screen.findByRole('region', { name: '拉取 日线' })
    expect(within(fetchBlock).getAllByRole('row').slice(1)).toHaveLength(1)
    expect(fetchBlock).toHaveTextContent('NVDA')
    expect(fetchBlock).toHaveTextContent('timeout after 30s (NVDA)')
    await click('显示全部 3 个目标')
    const names = within(fetchBlock).getAllByRole('row').slice(1).map(r => within(r).getAllByRole('cell')[0].textContent)
    expect(names).toEqual(['NVDA', 'AAPL', 'MSFT'])
  })

  it('narrow fetch table as cards', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('max-width: 767px'), media: query,
      addEventListener() {}, removeEventListener() {} }))
    try {
      render(RunDetail, { runId: RID })
      const fetchBlock = await screen.findByRole('region', { name: '拉取 日线' })
      // below 768px the failed-target rows are cards (one per row, field name on the left), not a scrolling table
      expect(within(fetchBlock).queryByRole('table')).toBeNull()
      const cards = within(fetchBlock).getAllByRole('listitem')
      expect(cards).toHaveLength(1)
      expect(cards[0]).toHaveTextContent('NVDA')
      expect(cards[0]).toHaveTextContent('timeout after 30s (NVDA)')
      expect([...cards[0].querySelectorAll('dt')].map(dt => dt.textContent)).toEqual(
        ['目标', '结果', '尝试次数', '失败原因', '行数', '最新时点'])
      await click('显示全部 3 个目标')
      expect(within(fetchBlock).getAllByRole('listitem')).toHaveLength(3)
      // the node graph wraps at the same breakpoint (two rows of five)
      expect(screen.getByRole('list', { name: '节点状态图' })).toHaveAttribute('data-narrow', 'true')
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('legacy fetch note', async () => {
    vi.mocked(api.getRunDetail).mockResolvedValue(failedRun({
      kind: 'batch', task: 'workbench', graph_version: null, stages_version: 'stages-3', resume: null,
      nodes: ['collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'retro', 'push'].map(id => id === 'collect'
        ? node(id, { status: 'partial', fetch: { families: [family({ legacy: true, targets: undefined })] }, actions: { resume: false } })
        : node(id)),
    }))
    render(RunDetail, { runId: RID })
    await click('数据采集：部分成功')
    expect(await screen.findByText('这次运行早于目标级明细（v2.8），只记录了失败的目标')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /显示全部/ })).toBeNull()
    // 旧运行：九格，没有续跑与重跑
    expect(screen.getAllByRole('listitem').filter(li => li.hasAttribute('data-node'))).toHaveLength(9)
    expect(screen.queryByRole('button', { name: '从此节点继续' })).toBeNull()
  })

  it('results rows link to plan and reasoning', async () => {
    vi.mocked(api.getRunDetail).mockResolvedValue(successRun({
      nodes: IDS.map(id => {
        if (id === 'plan') return node(id, { results: { kind: 'plan', total: 1, truncated: false,
          items: [{ ticker: 'NVDA', plan_id: 'plan-uuid-1', status: 'ok', invalid_reasons: [] }] } })
        if (id === 'agent') return node(id, { results: { kind: 'agent', total: 1, truncated: false, items: [{
          ticker: 'NVDA', signal_date: '2026-10-29', mode: 'propose', status: 'ok', reason: null, reason_text: null, attempt: 1,
          input_tokens: 1200, output_tokens: 300, model: 'gpt-x', plan_id: 'plan-uuid-1', run_id: 'run-uuid-1', can_rerun: false }] } })
        return node(id)
      }),
    }))
    const reasoning = vi.spyOn(api, 'getReasoning').mockResolvedValue({
      run: { id: 'run-uuid-1', ticker: 'NVDA', signal_date: '2026-10-29', mode: 'propose', agent_version: 'agent-2', status: 'ok',
        reason: null, reason_text: null, model: 'gpt-x', cutoff_at: null, started_at: null, finished_at: null, input_tokens: 1, output_tokens: 1 },
      legacy: false, version: null, review: null, steps: [], notes: ['推理视图已打开'],
    } as any)
    render(RunDetail, { runId: RID })
    await click('规则计划：成功')
    expect(screen.getByRole('link', { name: 'NVDA' })).toHaveAttribute('href', '#plan/plan-uuid-1')
    await click('Agent：成功')
    const agent = screen.getByRole('region', { name: 'Agent 详情' })
    expect(within(agent).getByRole('link', { name: '计划' })).toHaveAttribute('href', '#plan/plan-uuid-1')
    expect(agent).toHaveTextContent('输入 1200 · 产出 300 token')
    expect(reasoning).not.toHaveBeenCalled()
    await click('看推理过程')
    expect(reasoning).toHaveBeenCalledWith('run-uuid-1')
    expect(await screen.findByText('推理视图已打开')).toBeInTheDocument()
    expect(within(agent).queryByRole('button', { name: '重新运行 Agent' })).toBeNull()
  })

  it('other kinds of results render as links to their pages', async () => {
    vi.mocked(api.getRunDetail).mockResolvedValue(successRun({
      nodes: IDS.map(id => {
        if (id === 'retro') return node(id, { results: { kind: 'retro', total: 2, truncated: false, items: [
          { kind: 'card', id: 12, ticker: 'NVDA', status: 'to_review', push: null },
          { kind: 'weekly', id: 3, ticker: null, status: 'generated', push: 'manual_run' },
          { kind: 'monthly', id: 4, ticker: null, status: 'reminded', push: 'sent' }] } })
        if (id === 'regime') return node(id, { results: { kind: 'regime', total: 1, truncated: false, items: [{ label: 'risk_on', regime_id: 'reg-1' }] } })
        if (id === 'sec_monitor') return node(id, { results: { kind: 'sec_monitor', total: 300, truncated: true, items: [
          { ticker: 'AAPL', form: '8-K', filing_date: '2026-10-29', accession: '0000320193-26-000001', notified: true }] } })
        return node(id)
      }),
    }))
    render(RunDetail, { runId: RID })
    await click('复盘：成功')
    expect(screen.getByRole('link', { name: '复盘卡 NVDA' })).toHaveAttribute('href', '#review/cards/12')
    expect(screen.getByRole('link', { name: '周度复盘' })).toHaveAttribute('href', '#review/weekly')
    expect(screen.getByText(/待审定/)).toBeInTheDocument()
    expect(screen.getByText(/已生成 · 手动运行不推送/)).toBeInTheDocument()
    expect(screen.getByText(/已提醒 · 已推送/)).toBeInTheDocument()
    await click('市场环境：成功')
    expect(screen.getByText('进攻 · reg-1')).toBeInTheDocument()
    await click('SEC 监控：成功')
    expect(screen.getByText('只显示前 1 项，共 300 项')).toBeInTheDocument()
    expect(screen.getByText(/0000320193-26-000001 · 已通知/)).toBeInTheDocument()
  })

  it('other runs show 这类运行没有节点证据', async () => {
    vi.mocked(api.getRunDetail).mockResolvedValue(failedRun({
      kind: 'other', task: 'filings', graph_version: null, stages_version: null, nodes: [], resume: null, status: 'success',
      executions: [], headline: '', raw_summary: { downloaded_count: 3 },
    }))
    render(RunDetail, { runId: 'filings-20261001093000-00112233' })
    expect(await screen.findByText('这类运行没有节点证据')).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: '节点状态图' })).toBeNull()
    expect(screen.getByText(/downloaded_count/)).toBeInTheDocument()
  })

  it('raw summary folded', async () => {
    render(RunDetail, { runId: RID })
    await screen.findByTestId('headline')
    const details = screen.getByText('原始摘要').closest('details')!
    expect(details.hasAttribute('open')).toBe(false)
    expect(details.querySelector('pre')!.textContent).toBe(JSON.stringify({ run_status: 'failed', failed_node: 'collect' }, null, 2))
  })

  it('load errors in Chinese', async () => {
    vi.mocked(api.getRunDetail).mockRejectedValue(new api.ApiError('没有这次运行', 404))
    render(RunDetail, { runId: RID })
    expect(await screen.findByRole('alert')).toHaveTextContent('没有这次运行')
    cleanup()
    vi.mocked(api.getRunDetail).mockRejectedValue(new api.ApiError('迁移 028 未执行', 503))
    render(RunDetail, { runId: RID })
    expect(await screen.findByRole('alert')).toHaveTextContent('数据库未就绪：迁移 028 未执行')
  })
})

describe('RunDetail · resume', () => {
  it('resume button only on failed node when allowed', async () => {
    render(RunDetail, { runId: RID })
    expect(await screen.findByRole('button', { name: '从此节点继续' })).toBeInTheDocument()
    await click('筛选：跳过')
    expect(screen.queryByRole('button', { name: '从此节点继续' })).toBeNull()
    cleanup()
    // 不允许续跑（过了截止）：没有按钮，写明原因
    vi.mocked(api.getRunDetail).mockResolvedValue(failedRun({
      resume: { allowed: false, node: 'collect', deadline_at: '2026-10-30T21:00:00Z', reason: 'resume_window_closed' },
      nodes: failedRun().nodes.map(n => ({ ...n, actions: { resume: false, rerun_agent: [] } })),
    }))
    render(RunDetail, { runId: RID })
    await screen.findByRole('region', { name: '数据采集 详情' })
    expect(screen.queryByRole('button', { name: '从此节点继续' })).toBeNull()
    expect(screen.getByText('已过 2026-10-30 纽约 17:00，只能整次重新运行')).toBeInTheDocument()
  })

  it('resume refusal reason is an alert', async () => {
    vi.mocked(api.getRunDetail).mockResolvedValue(failedRun({
      resume: { allowed: false, node: 'collect', deadline_at: '2026-10-30T21:00:00Z', reason: 'resume_window_closed' },
      nodes: failedRun().nodes.map(n => ({ ...n, actions: { resume: false, rerun_agent: [] } })),
    }))
    render(RunDetail, { runId: RID })
    await screen.findByRole('region', { name: '数据采集 详情' })
    // the backend's refusal, worded by RESUME_ERROR_TEXT, is announced as an alert (not a muted line)
    expect(screen.getByRole('alert')).toHaveTextContent('已过 2026-10-30 纽约 17:00，只能整次重新运行')
    expect(screen.queryByRole('button', { name: '从此节点继续' })).toBeNull()
  })

  it('interrupted, stage_failed and checkpoint_unavailable reasons have their own text', async () => {
    for (const [reason, text] of [['interrupted', '这次运行中途中断，只能整次重新运行'], ['stage_failed', '这一步记了失败但链已跑完，不能续跑；需要时整次重新运行'],
      ['checkpoint_unavailable', '状态库未就绪，暂时无法续跑']]) {
      vi.mocked(api.getRunDetail).mockResolvedValue(failedRun({
        resume: { allowed: false, node: null, deadline_at: null, reason },
        nodes: failedRun().nodes.map(n => ({ ...n, actions: { resume: false, rerun_agent: [] } })),
      }))
      render(RunDetail, { runId: RID })
      await screen.findByRole('region', { name: '数据采集 详情' })
      expect(screen.getByText(text)).toBeInTheDocument()
      cleanup()
    }
  })

  it('resume confirm text (spec §7.3, verbatim)', async () => {
    const resume = vi.spyOn(api, 'resumeDaily')
    render(RunDetail, { runId: RID })
    await click('从此节点继续')
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent(
      `从「数据采集」继续这次运行？已完成的 1 个节点不会重跑；截止时间仍是 ${fmtBeijing('2026-10-30T03:30:00Z')}。`)
    // 确认前不发请求；取消回到按钮
    expect(resume).not.toHaveBeenCalled()
    await fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(resume).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '从此节点继续' })).toBeInTheDocument()
  })

  it('resume confirm by keyboard', async () => {
    const resume = vi.spyOn(api, 'resumeDaily').mockResolvedValue({
      run_id: RID, node: 'collect', status: 'pending', attempt: 2, runner_id: 'x' })
    render(RunDetail, { runId: RID })
    const button = await screen.findByRole('button', { name: '从此节点继续' })
    // reachable with Tab: an enabled native button that is not taken out of the tab order
    expect(button).toBeEnabled()
    expect(button.tabIndex).toBeGreaterThanOrEqual(0)
    button.focus()
    expect(document.activeElement).toBe(button)
    // Enter on a focused button is a click (jsdom does not translate the key itself)
    await fireEvent.click(button)
    const dialog = await screen.findByRole('dialog')
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true))      // focus moved into the dialog
    // Esc closes it without sending anything, and focus goes back to the button
    await fireEvent.keyDown(document.activeElement!, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(resume).not.toHaveBeenCalled()
    await waitFor(() => expect(document.activeElement).toBe(button))
  })

  it('resume posts run id, node and a request_id, then polls every 5s until not running', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    const resume = vi.spyOn(api, 'resumeDaily').mockResolvedValue({
      run_id: RID, node: 'collect', status: 'pending', attempt: 2, runner_id: 'daily_resume-20261030050102-9f8e7d6c' })
    const get = vi.mocked(api.getRunDetail)
    get.mockResolvedValueOnce(failedRun())                                   // first load
    get.mockResolvedValueOnce(failedRun({ status: 'running' }))              // t = 5 s
    get.mockResolvedValueOnce(failedRun({ status: 'running' }))              // t = 10 s
    get.mockResolvedValue(successRun())                                      // t = 15 s: done
    render(RunDetail, { runId: RID })
    await act(() => vi.advanceTimersByTimeAsync(0))
    await click('从此节点继续')
    await confirmResume()
    expect(resume).toHaveBeenCalledWith(RID, 'collect', expect.stringMatching(/^[0-9a-f-]{36}$/))
    expect(screen.getByText('已提交，第 2 次执行，页面每 5 秒刷新一次')).toBeInTheDocument()
    expect(get).toHaveBeenCalledTimes(1)
    await act(() => vi.advanceTimersByTimeAsync(4900))
    expect(get).toHaveBeenCalledTimes(1)
    await act(() => vi.advanceTimersByTimeAsync(100))
    expect(get).toHaveBeenCalledTimes(2)
    await act(() => vi.advanceTimersByTimeAsync(5000))
    expect(get).toHaveBeenCalledTimes(3)
    await act(() => vi.advanceTimersByTimeAsync(5000))
    expect(get).toHaveBeenCalledTimes(4)
    expect(screen.getByTestId('headline')).toHaveTextContent('10 月 29 日的运行成功')
    await act(() => vi.advanceTimersByTimeAsync(60_000))
    expect(get).toHaveBeenCalledTimes(4)                                      // 状态不再是 running：停止
    expect(screen.queryByText(/页面每 5 秒刷新/)).toBeNull()
  })

  it('polling stops after 60 minutes with a note, and on destroy', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    const get = vi.mocked(api.getRunDetail)
    get.mockResolvedValue(failedRun({ status: 'running' }))                   // opened mid-run
    const view = render(RunDetail, { runId: RID })
    await act(() => vi.advanceTimersByTimeAsync(0))
    expect(get).toHaveBeenCalledTimes(1)
    await act(() => vi.advanceTimersByTimeAsync(3_600_000))
    const total = get.mock.calls.length
    expect(total).toBe(1 + 720)
    expect(screen.getByText('还在运行，稍后刷新')).toBeInTheDocument()
    await act(() => vi.advanceTimersByTimeAsync(60_000))
    expect(get).toHaveBeenCalledTimes(total)
    view.unmount()
    get.mockClear()
    const second = render(RunDetail, { runId: RID })
    await act(() => vi.advanceTimersByTimeAsync(0))
    second.unmount()
    await act(() => vi.advanceTimersByTimeAsync(30_000))
    expect(get).toHaveBeenCalledTimes(1)
  }, 30_000)              // 721 polls of fake time: takes 3–6 s depending on the machine

  it.each([
    ['not_failed', '这次运行没有失败的节点'],
    ['node_mismatch', '只能从失败的节点 数据采集 继续'],
    ['resume_window_closed', '已过 2026-10-30 纽约 17:00，只能整次重新运行'],
    ['daily_running', '每日工作流正在运行'],
  ])('resume 409 texts (%s)', async (code, text) => {
    vi.spyOn(api, 'resumeDaily').mockRejectedValue(new api.ApiError(code, 409, code))
    render(RunDetail, { runId: RID })
    await click('从此节点继续')
    await confirmResume()
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
  })

  it('keeps the 409 text visible after the detail is re-read and resume is no longer allowed', async () => {
    const get = vi.mocked(api.getRunDetail)
    get.mockResolvedValueOnce(failedRun())
    get.mockResolvedValue(failedRun({
      resume: { allowed: false, node: 'collect', deadline_at: '2026-10-30T21:00:00Z', reason: 'resume_window_closed' },
      nodes: failedRun().nodes.map(n => ({ ...n, actions: { resume: false, rerun_agent: [] } })),
    }))
    vi.spyOn(api, 'resumeDaily').mockRejectedValue(new api.ApiError('resume_window_closed', 409, 'resume_window_closed'))
    render(RunDetail, { runId: RID })
    await click('从此节点继续')
    await confirmResume()
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByRole('button', { name: '从此节点继续' })).toBeNull())
    expect(screen.getByRole('alert')).toHaveTextContent('已过 2026-10-30 纽约 17:00，只能整次重新运行')
    // not_failed: the re-read says the run succeeded in the meantime
    cleanup()
    get.mockReset()
    get.mockResolvedValueOnce(failedRun())
    get.mockResolvedValue(successRun())
    vi.mocked(api.resumeDaily).mockRejectedValue(new api.ApiError('not_failed', 409, 'not_failed'))
    render(RunDetail, { runId: RID })
    await click('从此节点继续')
    await confirmResume()
    await waitFor(() => expect(screen.getByTestId('headline')).toHaveTextContent('运行成功'))
    expect(screen.getByRole('alert')).toHaveTextContent('这次运行没有失败的节点')
  })

  it('the reason line names the node by title, like the 409 text', async () => {
    vi.mocked(api.getRunDetail).mockResolvedValue(failedRun({
      resume: { allowed: false, node: 'collect', deadline_at: null, reason: 'node_mismatch' },
      nodes: failedRun().nodes.map(n => ({ ...n, actions: { resume: false, rerun_agent: [] } })),
    }))
    render(RunDetail, { runId: RID })
    expect(await screen.findByText('只能从失败的节点 数据采集 继续')).toBeInTheDocument()
  })

  it('says so when the resume never shows up as running', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    vi.spyOn(api, 'resumeDaily').mockResolvedValue({ run_id: RID, node: 'collect', status: 'pending', attempt: 2, runner_id: 'x' })
    vi.mocked(api.getRunDetail).mockResolvedValue(failedRun())          // always the old state
    render(RunDetail, { runId: RID })
    await act(() => vi.advanceTimersByTimeAsync(0))
    await click('从此节点继续')
    await confirmResume()
    await act(() => vi.advanceTimersByTimeAsync(15_000))
    expect(screen.getByText('未看到续跑开始，请稍后刷新')).toBeInTheDocument()
    expect(screen.queryByText(/页面每 5 秒刷新/)).toBeNull()
  })

  it('keeps the request_id for a retry after a network failure', async () => {
    const resume = vi.spyOn(api, 'resumeDaily')
      .mockRejectedValueOnce(new api.ApiError('HTTP 503', 503))
      .mockResolvedValue({ run_id: RID, node: 'collect', status: 'pending', attempt: 2, runner_id: 'x' })
    vi.mocked(api.getRunDetail).mockResolvedValue(failedRun())
    render(RunDetail, { runId: RID })
    await click('从此节点继续')
    await confirmResume()
    expect(await screen.findByRole('alert')).toHaveTextContent('数据库未就绪')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())      // the error is on the page, not behind the dialog
    await click('从此节点继续')                                                // 按钮还在，可以重试
    await confirmResume()
    await waitFor(() => expect(resume).toHaveBeenCalledTimes(2))
    expect(resume.mock.calls[1][2]).toBe(resume.mock.calls[0][2])
  })
})

describe('RunDetail · rerun agent', () => {
  const agentRun = () => successRun({
    nodes: IDS.map(id => id === 'agent'
      ? node(id, { status: 'partial', results: { kind: 'agent', total: 2, truncated: false, items: [
        { ticker: 'NVDA', signal_date: '2026-10-29', mode: 'propose', status: 'skipped', reason: 'max_attempts', reason_text: '已达重试上限',
          attempt: 4, input_tokens: 0, output_tokens: 0, model: null, plan_id: null, run_id: null, can_rerun: true },
        { ticker: 'AMD', signal_date: '2026-10-29', mode: 'propose', status: 'ok', reason: null, reason_text: null,
          attempt: 1, input_tokens: 10, output_tokens: 5, model: 'gpt-x', plan_id: 'p2', run_id: 'r2', can_rerun: false }] } })
      : node(id)),
  })

  it('rerun agent button posts request_id', async () => {
    const rerun = vi.spyOn(api, 'rerunAgent').mockResolvedValue({ reset_id: 7, run_id: 'run-new', replayed: false })
    vi.mocked(api.getRunDetail).mockResolvedValue(agentRun())
    render(RunDetail, { runId: RID })
    await click('Agent：部分成功')
    expect(screen.getAllByRole('button', { name: '重新运行 Agent' })).toHaveLength(1)     // 只有 can_rerun 的行
    await click('重新运行 Agent')
    await confirmRerun()
    expect(rerun).toHaveBeenCalledWith({
      ticker: 'NVDA', signal_date: '2026-10-29', mode: 'propose', request_id: expect.stringMatching(/^[0-9a-f-]{36}$/), origin: 'run_detail',
    })
    expect(await screen.findByText('已重置尝试次数，并已重新运行')).toBeInTheDocument()
    await waitFor(() => expect(api.getRunDetail).toHaveBeenCalledTimes(2))     // 重新读取详情
  })

  it('rerun agent needs confirm: the row button only opens the dialog, Cancel posts nothing', async () => {
    const rerun = vi.spyOn(api, 'rerunAgent').mockResolvedValue({ reset_id: 7, run_id: 'run-new', replayed: false })
    vi.mocked(api.getRunDetail).mockResolvedValue(agentRun())
    render(RunDetail, { runId: RID })
    await click('Agent：部分成功')
    await click('重新运行 Agent')
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveAccessibleName('重新运行 Agent')
    expect(dialog).toHaveTextContent('NVDA')
    expect(rerun).not.toHaveBeenCalled()
    await fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(rerun).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '重新运行 Agent' })).toBeInTheDocument()
  })

  it('shows the 409 not_needed text and keeps the same request_id on retry', async () => {
    const rerun = vi.spyOn(api, 'rerunAgent')
      .mockRejectedValueOnce(new api.ApiError('HTTP 503', 503))
      .mockRejectedValueOnce(new api.ApiError('not_needed', 409, 'not_needed'))
    vi.mocked(api.getRunDetail).mockResolvedValue(agentRun())
    render(RunDetail, { runId: RID })
    await click('Agent：部分成功')
    await click('重新运行 Agent')
    await confirmRerun()
    expect(await screen.findByText(/数据库未就绪/)).toBeInTheDocument()
    await click('重新运行 Agent')
    await confirmRerun()
    expect(await screen.findByText('这个单元不需要重跑')).toBeInTheDocument()
    expect(rerun.mock.calls[1][0].request_id).toBe(rerun.mock.calls[0][0].request_id)
  })

  it('hides the rerun button of a unit after it succeeded', async () => {
    vi.spyOn(api, 'rerunAgent').mockResolvedValue({ reset_id: 7, run_id: null, replayed: false })
    vi.mocked(api.getRunDetail).mockResolvedValue(agentRun())      // the re-read still says can_rerun
    render(RunDetail, { runId: RID })
    await click('Agent：部分成功')
    await click('重新运行 Agent')
    await confirmRerun()
    expect(await screen.findByText('已重置尝试次数，同一交易日的续跑或手动运行时执行')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', { name: '重新运行 Agent' })).toBeNull())     // the dialog unmounts on the next tick
  })

  it('a 409 without the not_needed code shows what the backend said', async () => {
    vi.spyOn(api, 'rerunAgent').mockRejectedValue(new api.ApiError('这个单元不需要重跑（后端原文）', 409))
    vi.mocked(api.getRunDetail).mockResolvedValue(agentRun())
    render(RunDetail, { runId: RID })
    await click('Agent：部分成功')
    await click('重新运行 Agent')
    await confirmRerun()
    expect(await screen.findByText('这个单元不需要重跑（后端原文）')).toBeInTheDocument()
  })

  it('old runs never offer rerun', async () => {
    vi.mocked(api.getRunDetail).mockResolvedValue({ ...agentRun(), kind: 'batch', stages_version: 'stages-3' })
    render(RunDetail, { runId: RID })
    await click('Agent：部分成功')
    expect(screen.queryByRole('button', { name: '重新运行 Agent' })).toBeNull()
  })
})
