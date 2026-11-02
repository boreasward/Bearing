import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte'
import Research from './Research.svelte'

const item = { id: 'r1', ticker: 'NVDA', question: '收入能否持续？', status: 'waiting_data', latest_summary: '等待下一次披露', cutoff_at: '2026-10-30T20:00:00Z', tokens: 123, created_at: '2026-10-30T21:00:00Z' }
const version = { version_no: 1, status: 'concluded', action: 'wait', summary: '证据仍有缺口', cutoff_at: item.cutoff_at, agent_run_id: 'agent1', output: { research: { status: 'ok', reason: null, output: { facts: [{ kind: 'disclosure', text: '收入增长', refs: ['call:1'] }, { kind: 'inference', text: '增长可能延续', refs: ['call:1'] }], risks: [], blocked: [{ what: '本季指引', why: '尚未获取' }], summary: '研究摘要' } }, action: 'wait', summary: '证据仍有缺口', catalysts: [{ text: '下季财报', refs: ['call:1'] }], counter: [{ text: '增速可能放缓', refs: ['call:1'] }], counter_status: 'ok', price_scenarios: [], next_steps: [{ text: '等待指引披露', refs: [] }] }, review_when: null, tokens: 123, elapsed_ms: 1500, created_at: item.created_at }
let fetcher: ReturnType<typeof vi.fn>
let queued = false
beforeEach(() => {
  queued = false
  fetcher = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === '/api/research' && !init?.method) return Response.json({ items: [item, ...(queued ? [{ ...item, id: 'r2', ticker: 'NVDA', status: 'queued', latest_summary: null }] : [])] })
    if (path === '/api/research/r1' && !init?.method) return Response.json({ request: { ...item, source: 'manual', horizon_days: 20 }, versions: [version, { ...version, version_no: 2, summary: '新版结论' }], chain: null })
    if (path === '/api/research' && init?.method === 'POST') { queued = true; return Response.json({ id: 'r2', status: 'queued' }, { status: 200 }) }
    if (path === '/api/research/r1/close') return Response.json({ id: 'r1', status: 'closed' })
    throw new Error(`Unexpected request ${path}`)
  })
  vi.stubGlobal('fetch', fetcher)
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('renders list with status pills and cutoff', async () => {
  render(Research)
  expect(await screen.findByText('等待数据')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'NVDA' })).toHaveAttribute('href', '#research/r1')
  expect(document.querySelector('time[datetime="2026-10-30T20:00:00Z"]')).not.toBeNull()
  expect(screen.getByText('等待下一次披露')).toBeInTheDocument()
})
it('submit requires ticker and quota confirmation, then accepts the UI1 daily-busy 200 queued response', async () => {
  render(Research)
  const submit = await screen.findByRole('button', { name: '提交研究' })
  expect(submit).toBeDisabled()
  await fireEvent.input(screen.getByLabelText('标的'), { target: { value: ' nvda ' } })
  await fireEvent.input(screen.getByLabelText('问题'), { target: { value: '增长原因' } })
  await fireEvent.click(submit)
  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent('每天最多 5 次')
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  await fireEvent.click(within(dialog).getByRole('button', { name: '创建研究档案' }))
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/research', expect.objectContaining({ method: 'POST', body: expect.any(String) })))
  const body = JSON.parse(fetcher.mock.calls.find(([, init]) => init?.method === 'POST')![1]!.body as string)
  expect(body).toEqual({ ticker: 'NVDA', question: '增长原因', request_id: expect.any(String) })
  expect(await screen.findByText('研究已排队')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '查看档案' })).toHaveAttribute('href', '#research/r2')
  expect(await screen.findByText('排队中')).toBeInTheDocument()
  expect(fetcher.mock.calls.filter(([path]) => path === '/api/research').length).toBe(3)
})
it('detail renders version tabs and fact kinds as SourceBadge; reasoning link present', async () => {
  render(Research, { sub: 'r1/1' })
  expect(await screen.findByRole('navigation', { name: '结论版本' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '第 2 版' })).toHaveAttribute('href', '#research/r1/2')
  expect(document.querySelector('[data-source="披露事实"]')).toHaveTextContent('披露事实')
  expect(document.querySelector('[data-source="推断"]')).toHaveTextContent('推断')
  expect(screen.getByText('本季指引：尚未获取')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '看推理过程' })).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: '关联计划链' })).toBeNull()
  expect(screen.getByText(/只出研究档案/)).toBeInTheDocument()
})
it('shows request errors and retries, including quota', async () => {
  fetcher.mockResolvedValueOnce(Response.json({ error: 'migration_031_missing' }, { status: 503 }))
  render(Research)
  expect(await screen.findByText('数据库尚未执行迁移 031')).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: '重试' }))
  expect(await screen.findByText('等待下一次披露')).toBeInTheDocument()
})
it('closing requires confirmation and reloads the dossier', async () => {
  render(Research, { sub: 'r1' })
  await fireEvent.click(await screen.findByRole('button', { name: '结束研究' }))
  const dialog = await screen.findByRole('dialog')
  expect(fetcher.mock.calls.some(([p]) => p.endsWith('/close'))).toBe(false)
  await fireEvent.click(within(dialog).getByRole('button', { name: '结束档案' }))
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/research/r1/close', expect.objectContaining({ method: 'POST' })))
})
it('a close dialog never retargets a different dossier after navigation', async () => {
  const view = render(Research, { sub: 'r1' })
  await fireEvent.click(await screen.findByRole('button', { name: '结束研究' }))
  expect(await screen.findByRole('dialog')).toBeInTheDocument()
  await view.rerender({ sub: undefined })
  await screen.findByRole('button', { name: '提交研究' })
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(fetcher.mock.calls.some(([path]) => path.endsWith('/close'))).toBe(false)
})
it('dossier sections read the saved conclusion lists, counter status and keep missing parts unrecorded', async () => {
  const bare = { ...version, version_no: 2, summary: '新版结论', output: { ...version.output, catalysts: undefined, counter: [], counter_status: 'malformed' } }
  const failed = { ...version, version_no: 3, status: 'error', action: null, summary: '校验未过：research_refs_invalid', output: null }
  fetcher.mockImplementation(async (path: string) => Response.json({ request: { ...item, source: 'manual', horizon_days: 20 }, versions: [version, bare, failed], chain: null }))
  const view = render(Research, { sub: 'r1/1' })
  expect(await screen.findByText('下季财报')).toBeInTheDocument()
  expect(screen.getByText('增速可能放缓')).toBeInTheDocument()
  expect(screen.getByText('等待指引披露')).toBeInTheDocument()
  expect(screen.getByText('本版没有情景条目')).toBeInTheDocument()
  expect(screen.getAllByText('本版未记录').length).toBeGreaterThan(0)   // review_when is never recorded by the backend
  await view.rerender({ sub: 'r1/2' })
  expect(await screen.findByText('反方输出格式无效，本版没有反证')).toBeInTheDocument()
  expect(screen.getByText('本版没有反证条目')).toBeInTheDocument()
  expect(screen.getAllByText('本版未记录')).toHaveLength(2)   // missing catalysts + review_when
  await view.rerender({ sub: 'r1/3' })
  expect(await screen.findByText('校验未过：research_refs_invalid')).toBeInTheDocument()
  expect(screen.getByText('本版未记录研究事实')).toBeInTheDocument()
})
