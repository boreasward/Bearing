import { afterEach, beforeEach, expect, it, vi, type Mock } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte'
import Roadmap from './Roadmap.svelte'
import type { VersionDetail, DecisionDetail, RoadmapProposal } from '$lib/roadmap'

const version = { id: 1, version_id: 'v2.15', version: 1, supersedes: null, title: '研究与组合', theme: '五层', scope: '研究档案', depends_on: [], status: 'planned', exit_conditions: '离线验证', spec_path: null, plan_path: null, tag: null, proposal_id: 9, created_at: '2026-11-01T00:00:00Z' }
const proposal = { id: 9, run_id: 'opt-1', agent_run_id: 'agent-opt-1', source: 'data', title: '补齐数据', change: '补采缺口', rationale: '存在失败', evidence_refs: ['fact:data_failures#1'], evidence_rows: [{ ref: 'fact:data_failures#1', body: { id: 1, session: '2026-10-30', family: 'sec_text', failed: 4, reason: '生成时冻结的超时证据' } }], expected: '减少失败', acceptance: '无静默失败', status: 'proposed', decided_at: null, decision_reason: null, request_id: null, created_at: version.created_at }
const overview = { goals: [{ id: 1, goal_id: 'main', version: 1, supersedes: null, body: '收益与流程质量', created_at: version.created_at }], versions: [version], decisions: [{ id: 1, decision_no: 113, version: 1, supersedes: null, decided_on: '2026-11-01', question: '如何研究', decision: '只出档案', quote: '不要自动开链', version_ids: ['v2.15'], created_at: version.created_at }, { id: 2, decision_no: 112, version: 1, supersedes: null, decided_on: '2026-10-31', question: '其他版本', decision: '保留', quote: '', version_ids: ['v2.11'], created_at: version.created_at }], evidence: [{ id: 1, evidence_id: 'acceptance', version: 1, supersedes: null, version_id: 'v2.15', kind: 'manual', body: '离线测试通过', observed_on: '2026-11-01', source_path: 'docs/roadmap/acceptance.md', created_at: version.created_at }], exported_at: version.created_at }
let status = 'proposed'
let fetcher: Mock<(path: string, init?: RequestInit) => Promise<Response>>
let versionDetail: VersionDetail
let decisionDetail: DecisionDetail
let evidenceRows: RoadmapProposal['evidence_rows']
beforeEach(() => {
  status = 'proposed'
  const latest = { ...version, id: 2, version: 2, supersedes: 1, title: '更新后的研究与组合', scope: '更新后的研究范围', exit_conditions: '新版退出条件', status: 'in_progress' as const }
  versionDetail = { version: latest, history: [{ ...version, status: 'planned' }, latest], brief: null, brief_status: 'pending' }
  const old = overview.decisions[0], current = { ...old, id: 3, version: 2, supersedes: 1, decision: '研究输出保持独立档案', quote: '新的决定原话' }
  decisionDetail = { decision: current, history: [old, current] }
  evidenceRows = proposal.evidence_rows
  fetcher = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === '/api/roadmap') return Response.json(overview)
    if (path.startsWith('/api/roadmap/proposals?')) return Response.json({ items: [{ ...proposal, status, evidence_rows: evidenceRows }] })
    if (path === '/api/roadmap/proposals/9/decide') { status = 'approved'; return Response.json({ status: 'approved', version_id: 'v2.16', brief_status: 'pending' }) }
    if (path === '/api/roadmap/versions/v2.15') return Response.json(versionDetail)
    if (path === '/api/roadmap/versions/v2.16') return Response.json({ ...versionDetail, version: { ...versionDetail.version, version_id: 'v2.16' } })
    if (path === '/api/roadmap/decisions/113') return Response.json(decisionDetail)
    if (path === '/api/roadmap/briefs/v2.15/retry') {      // the real backend only queues a job; the old brief row stays
      return Response.json({ version_id: 'v2.15', brief_status: 'pending', run_id: 'impl-brief-1' })
    }
    throw new Error(`Unexpected request ${path} ${init?.method}`)
  })
  vi.stubGlobal('fetch', fetcher)
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('route tab renders versions table with status and export link', async () => {
  render(Roadmap)
  expect(await screen.findByRole('table', { name: '路线版本' })).toHaveTextContent('研究与组合')
  expect(screen.getByText('已规划')).toBeInTheDocument()
  expect(screen.getByText('收益与流程质量')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '导出 Markdown' })).toHaveAttribute('href', '/api/roadmap/export.md')
})
it('proposals tab decide opens ConfirmDialog requiring reason and version_id', async () => {
  render(Roadmap, { sub: 'proposals' })
  await fireEvent.click(await screen.findByRole('button', { name: '批准' }))
  const dialog = await screen.findByRole('dialog')
  const confirm = within(dialog).getByRole('button', { name: '批准提案' })
  expect(confirm).toBeDisabled()
  await fireEvent.input(within(dialog).getByLabelText('理由'), { target: { value: '减少数据缺口' } })
  await fireEvent.input(within(dialog).getByLabelText('版本编号'), { target: { value: '' } })
  expect(confirm).toBeDisabled()
  await fireEvent.input(within(dialog).getByLabelText('版本编号'), { target: { value: 'v2.16' } })
  expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  await fireEvent.click(confirm)
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/roadmap/proposals/9/decide', expect.objectContaining({ method: 'POST' })))
  const body = JSON.parse(fetcher.mock.calls.find(([, init]) => init?.method === 'POST')![1]!.body as string)
  expect(body).toEqual({ decision: 'approved', version_id: 'v2.16', reason: '减少数据缺口', request_id: expect.any(String) })
  expect(await screen.findByText('brief 生成中')).toBeInTheDocument()
})
it('retry stays pending while only the old error row exists, then shows the new row', async () => {
  status = 'approved'
  versionDetail.brief_status = 'error'
  versionDetail.brief = { id: 1, version_id: 'v2.15', proposal_id: 9, agent_run_id: 'brief-old', body: null, status: 'error', reason: '模型预算不足', created_at: version.created_at }
  render(Roadmap, { sub: 'proposals/9' })
  await fireEvent.click(await screen.findByRole('button', { name: '查看实施 brief' }))
  expect(await screen.findByText('brief 生成失败')).toBeInTheDocument()
  expect(screen.getByText('模型预算不足')).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: '重试 brief' }))
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/roadmap/briefs/v2.15/retry', expect.objectContaining({ method: 'POST' })))
  // the immediate readback still returns the old error row (id 1): the UI keeps pending, never flips back to failure
  expect(await screen.findByText('brief 生成中')).toBeInTheDocument()
  await waitFor(() => expect(screen.getByRole('button', { name: '刷新 brief' })).toBeEnabled())
  expect(fetcher.mock.calls.filter(([path]) => path === '/api/roadmap/versions/v2.15')).toHaveLength(2)
  expect(screen.queryByText('brief 生成失败')).toBeNull()
  expect(screen.queryByText('模型预算不足')).toBeNull()
  expect(screen.queryByRole('button', { name: '重试 brief' })).toBeNull()
  // a refresh while only the old row exists is still pending
  await fireEvent.click(screen.getByRole('button', { name: '刷新 brief' }))
  await waitFor(() => expect(fetcher.mock.calls.filter(([path]) => path === '/api/roadmap/versions/v2.15')).toHaveLength(3))
  await waitFor(() => expect(screen.getByRole('button', { name: '刷新 brief' })).toBeEnabled())
  expect(screen.getByText('brief 生成中')).toBeInTheDocument()
  expect(screen.queryByText('brief 生成失败')).toBeNull()
  // the background job inserts a newer row: now it is shown
  versionDetail = { ...versionDetail, brief_status: 'ok', brief: { id: 2, version_id: 'v2.15', proposal_id: 9, agent_run_id: 'brief-new', body: '# 冻结证据驱动的实施步骤', status: 'ok', reason: null, created_at: version.created_at } }
  await fireEvent.click(screen.getByRole('button', { name: '刷新 brief' }))
  expect(await screen.findByText('# 冻结证据驱动的实施步骤')).toBeInTheDocument()
  expect(screen.getByText('brief 已生成')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '重试 brief' })).toBeNull()
})
it('version link loads the latest definition, ordered revision history and not-requested brief', async () => {
  versionDetail = { ...versionDetail, version: { ...versionDetail.version, proposal_id: null }, brief_status: 'not_requested' }
  const view = render(Roadmap)
  const link = await screen.findByRole('link', { name: 'v2.15' })
  await fireEvent.click(link)
  await view.rerender({ sub: link.getAttribute('href')!.replace('#roadmap/', '') })
  expect(await screen.findByRole('heading', { name: 'v2.15 · 更新后的研究与组合' })).toBeInTheDocument()
  const history = screen.getByRole('region', { name: '版本历史' })
  expect([...history.querySelectorAll('summary')].map(el => el.textContent)).toEqual(['第 1 版 · 研究与组合', '第 2 版 · 更新后的研究与组合'])
  await fireEvent.click(within(history).getByText('第 1 版 · 研究与组合'))
  expect(history.querySelector('details')).toHaveAttribute('open')
  expect(history.querySelector('details')).toHaveTextContent('研究档案')
  expect(history.querySelector('details')).toHaveTextContent('离线验证')
  expect(screen.getByText('未请求 brief')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '重试 brief' })).toBeNull()
})
it('a newer error row after a retry replaces the pending acknowledgement and offers retry again', async () => {
  const failed = { id: 1, version_id: 'v2.15', proposal_id: 9, agent_run_id: 'brief-old', body: null, status: 'error' as const, reason: '旧失败', created_at: version.created_at }
  versionDetail = { ...versionDetail, brief_status: 'error', brief: failed }
  const respond = fetcher.getMockImplementation()!
  fetcher.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === '/api/roadmap/briefs/v2.15/retry') {
      versionDetail = { ...versionDetail, brief: { ...failed, id: 2, reason: '后台任务仍失败' } }
      return Response.json({ version_id: 'v2.15', brief_status: 'pending', run_id: 'impl-brief-2' })
    }
    return respond(path, init)
  })
  render(Roadmap, { sub: 'versions/v2.15' })
  await fireEvent.click(await screen.findByRole('button', { name: '重试 brief' }))
  expect(await screen.findByText('后台任务仍失败')).toBeInTheDocument()
  expect(screen.getByText('brief 生成失败')).toBeInTheDocument()
  expect(screen.queryByText('brief 已生成')).toBeNull()
})
it.each(['versions/missing', 'decisions/999'])('a missing %s detail displays the 404 without inventing a history', async sub => {
  const respond = fetcher.getMockImplementation()!
  fetcher.mockImplementation(async (path: string, init?: RequestInit) => path === `/api/roadmap/${sub}`
    ? Response.json({ error: '没有该路线记录' }, { status: 404 }) : respond(path, init))
  render(Roadmap, { sub })
  expect(await screen.findByRole('alert')).toHaveTextContent('没有该路线记录')
  expect(screen.queryByText('第 1 版 · 研究与组合')).toBeNull()
})
it('decision detail uses its latest row and exposes all revisions including the current one', async () => {
  render(Roadmap, { sub: 'decisions/113' })
  const current = await screen.findByRole('region', { name: '决策 #113' })
  expect(current).toHaveTextContent('研究输出保持独立档案')
  expect(current).toHaveTextContent('新的决定原话')
  const history = screen.getByRole('region', { name: '决策 #113 历史' })
  expect([...history.querySelectorAll('summary')].map(el => el.textContent)).toEqual(['第 1 版 · 2026-11-01', '第 2 版 · 2026-11-01'])
  await fireEvent.click(within(history).getByText('第 1 版 · 2026-11-01'))
  expect(history.querySelector('details')).toHaveAttribute('open')
  expect(history.querySelector('details')).toHaveTextContent('不要自动开链')
})
it('proposal expands the frozen evidence body returned by the list', async () => {
  render(Roadmap, { sub: 'proposals/9' })
  const ref = await screen.findByText('fact:data_failures#1')
  await fireEvent.click(ref)
  expect(ref.closest('details')).toHaveAttribute('open')
  expect(ref.closest('details')).toHaveTextContent('生成时冻结的超时证据')
  expect(ref.closest('details')).toHaveTextContent('2026-10-30')
  expect(ref.closest('details')).toHaveTextContent('"failed": 4')
})
it('historical proposals with no evidence pack show a missing-record notice', async () => {
  evidenceRows = []
  render(Roadmap, { sub: 'proposals/9' })
  const ref = await screen.findByText('fact:data_failures#1')
  await fireEvent.click(ref)
  expect(ref.closest('details')).toHaveTextContent('历史证据包缺少该引用的记录')
})
it('decisions tab filters by version', async () => {
  render(Roadmap, { sub: 'decisions' })
  expect(await screen.findByText('其他版本')).toBeInTheDocument()
  await fireEvent.change(screen.getByLabelText('按版本筛选'), { target: { value: 'v2.15' } })
  expect(screen.queryByText('其他版本')).toBeNull()
  expect(screen.getByText('不要自动开链')).toBeInTheDocument()
})
it('evidence is grouped by version and keeps source paths', async () => {
  render(Roadmap, { sub: 'evidence' })
  expect(await screen.findByText('离线测试通过')).toBeInTheDocument()
  expect(screen.getByText('docs/roadmap/acceptance.md')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'v2.15' })).toBeInTheDocument()
})
