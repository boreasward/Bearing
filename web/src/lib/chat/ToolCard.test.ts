import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/svelte'
import ToolCardConsumer from './ToolCardConsumer.test.svelte'
import type { Block } from './types'
import { fixture } from '$lib/views/test-fixtures'

function load(tool: string, result: unknown, extra: Record<string, unknown> = {}) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
    tool, ref:'call:7', args:{}, result, result_available:true,
    error:null, truncated:false, called_at:'2026-11-06T00:00:00Z', ...extra,
  }), { headers:{ 'content-type':'application/json' } })))
  const block: Extract<Block, { kind:'tool' }> = { kind:'tool', id:7, tool, ref:'call:7', state:'ready' }
  return render(ToolCardConsumer, { block })
}
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('shows successful null as an ordinary empty result with provenance', async () => {
  const { container } = load('get_earnings', null)
  expect(await screen.findByText('这次查询没有返回记录')).toBeInTheDocument()
  expect(container.querySelector('[data-state="empty"]')).toBeInTheDocument()
  expect(container).toHaveTextContent('get_earnings · call:7')
  expect(screen.queryByText('这次调用早于 v3.2.1，没有保留结果')).toBeNull()
})
it('shows unavailable legacy results as empty rather than degraded', async () => {
  const { container } = load('get_earnings', null, { result_available:false })
  expect(await screen.findByText('这次调用早于 v3.2.1，没有保留结果')).toBeInTheDocument()
  expect(container.querySelector('[data-state="empty"]')).toBeInTheDocument()
})
it('preserves old-server compatibility for a non-null result', async () => {
  load('save_summary', fixture('save_summary'), { result_available:undefined })
  expect(await screen.findByText('已写入')).toBeInTheDocument()
})
it.each(['save_plan','record_trade','record_cash_flow','record_equity','start_research','save_summary'])('derives successful %s write status from the fetched payload', async tool => {
  load(tool, fixture(tool))
  expect(await screen.findByText('已写入')).toBeInTheDocument()
})
it.each([
  ['saved replay', { replay:true }, null, '重放'],
  ['preview', { write:false, preview:'确认后保存', replay:true }, null, '待确认'],
  ['error', { error:'invalid_argument', replay:true }, 'invalid_argument', null],
] as const)('derives %s status without injected call metadata', async (_, result, error, label) => {
  const { container } = load('save_plan', result, { error })
  await screen.findByText(/save_plan · call:7/)
  if (label) expect(await screen.findByText(label)).toBeInTheDocument()
  else {
    await screen.findByText('参数不正确')
    expect(container.querySelector('[data-domain="write"]')).toBeNull()
  }
  expect(screen.queryByText('已写入')).toBeNull()
})
it('does not give read tools a write badge even if their data has a replay field', async () => {
  const { container } = load('get_plans', { replay:true })
  await screen.findByText('计划快照')
  expect(container.querySelector('[data-domain="write"]')).toBeNull()
})
