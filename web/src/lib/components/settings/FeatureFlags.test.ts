import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte'
import FeatureFlags from './FeatureFlags.svelte'
import Settings from '../../../routes/Settings.svelte'
import type { FeatureChange } from '$lib/features'

const keys = ['backup_sources', 'company_researcher', 'portfolio_manager', 'optimize_proposals']
let fetcher: ReturnType<typeof vi.fn>
let history: FeatureChange[]
beforeEach(() => {
  history = []
  fetcher = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === '/api/settings/features') return Response.json({ flags: keys.map(flag => {
      const changes = history.filter(row => row.flag === flag), last = changes.at(-1)
      return { flag, enabled: last?.enabled_after ?? true, changed_at: last?.created_at ?? null, reason: last?.reason ?? null, history_count: changes.length }
    }), history })
    if (path === '/api/settings/features/company_researcher' && init?.method === 'POST') {
      const body = JSON.parse(init.body as string)
      history.push({ id: history.length + 1, flag: 'company_researcher', enabled_before: history.at(-1)?.enabled_after ?? true, enabled_after: body.enabled, reason: body.reason, request_id: body.request_id, created_at: '2026-11-01T00:00:00Z' })
      return Response.json({ flag: 'company_researcher', enabled: body.enabled, changed_at: '2026-11-01T00:00:00Z' })
    }
    throw new Error(`Unexpected request ${path}`)
  })
  vi.stubGlobal('fetch', fetcher)
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('renders four flags with current state', async () => {
  render(FeatureFlags)
  const table = await screen.findByRole('table', { name: '功能开关' })
  for (const name of ['备源', '公司研究员', '组合经理', '优化提案']) expect(table).toHaveTextContent(name)
  expect(within(table).getAllByText('已开启')).toHaveLength(4)
})
it('turning off requires reason via ConfirmDialog', async () => {
  render(FeatureFlags)
  await fireEvent.click(await screen.findByRole('button', { name: '关闭公司研究员' }))
  const dialog = await screen.findByRole('dialog')
  const submit = within(dialog).getByRole('button', { name: '关闭功能' })
  expect(submit).toBeDisabled()
  await fireEvent.input(within(dialog).getByLabelText('变更理由'), { target: { value: '   ' } })
  expect(submit).toBeDisabled()
  await fireEvent.input(within(dialog).getByLabelText('变更理由'), { target: { value: '观察预算' } })
  expect(fetcher.mock.calls.some(([, i]) => i?.method === 'POST')).toBe(false)
  await fireEvent.click(submit)
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/settings/features/company_researcher', expect.objectContaining({ method: 'POST' })))
  expect(JSON.parse(fetcher.mock.calls.find(([, i]) => i?.method === 'POST')![1]!.body as string)).toEqual({ enabled: false, reason: '观察预算', request_id: expect.any(String) })
})
it('Settings reads real history and re-reads both current state and history after turning off and on', async () => {
  history = [{ id: 1, flag: 'company_researcher', enabled_before: null, enabled_after: true, reason: '首次启用', request_id: 'req1', created_at: '2026-10-31T00:00:00Z' }]
  render(Settings, { sub: 'features' })
  expect(await screen.findByRole('table', { name: '开关变更历史' })).toHaveTextContent('首次启用')
  for (const [action, reason, next] of [['关闭', '观察预算', '开启'], ['开启', '恢复研究', '关闭']]) {
    await fireEvent.click(screen.getByRole('button', { name: `${action}公司研究员` }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('button', { name: `${action}功能` })).toBeDisabled()
    await fireEvent.input(within(dialog).getByLabelText('变更理由'), { target: { value: reason } })
    await fireEvent.click(within(dialog).getByRole('button', { name: `${action}功能` }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(await screen.findByRole('button', { name: `${next}公司研究员` })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: '开关变更历史' })).toHaveTextContent(reason)
  }
  const rows = within(screen.getByRole('table', { name: '开关变更历史' })).getAllByRole('row').slice(1)
  expect(rows.map(row => row.textContent)).toEqual([
    expect.stringContaining('首次启用'), expect.stringContaining('观察预算'), expect.stringContaining('恢复研究'),
  ])
  expect(rows[1]).toHaveTextContent('已开启 → 已关闭')
  expect(rows[2]).toHaveTextContent('已关闭 → 已开启')
  expect(fetcher.mock.calls.filter(([path]) => path === '/api/settings/features')).toHaveLength(3)
})
it('empty API history remains empty rather than fabricating a row from the default state', async () => {
  render(FeatureFlags)
  expect(await screen.findByText('尚无变更记录')).toBeInTheDocument()
  expect(screen.queryByRole('table', { name: '开关变更历史' })).toBeNull()
})
