import { afterEach, beforeEach, expect, it, vi, type Mock } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { tick } from 'svelte'
import Home from './Home.svelte'
import * as stores from '$lib/stores'
import { bringToChat, actionCompleted } from '$lib/views/context'
import { fixture } from '$lib/views/test-fixtures'
import { todayData, marketData, runsData } from '$lib/views/workbench-fixtures'
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const chat = { chat_id: 'c1', title: '最近问题', read_only: false, created_at: '2026-11-09T00:00:00Z', last_message_at: '2026-11-09T00:00:00Z', model: 'test-model' }
let fetcher: Mock<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>
function setup(empty = false) {
  fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input)
    if (path === '/api/chats') return json({ chats: [chat] })
    if (path === '/api/chats/c1') return json({ chat, messages: [{ seq: 1, role: 'user', content: '最近一段对话', tool_refs: [] }] })
    if (path === '/api/today') return json(empty ? { ...todayData, session: null, actions: [], counts: { actions: 0, holdings: 0, pending: 0, watching: 0 } } : todayData)
    if (path === '/api/market') return empty ? json({ error: 'no_run' },404) : json(marketData)
    if (path === '/api/runs') return json(empty ? { ...runsData, runs: [] } : runsData)
    if (path === '/api/account') return json(fixture('get_account'))
    if (path.startsWith('/api/ledger')) return json({ trades: [], cash_flows: [], equity: [], voids: [] })
    if (path.startsWith('/api/plans?')) return json({ plans: [] })
    if (path.startsWith('/api/research?')) return json({ research: [] })
    if (path.startsWith('/api/briefs/')) return json({ error: 'no_run' },404)
    if (path.startsWith('/api/briefs?')) return json({ briefs: [] })
    if (path.startsWith('/api/retrospect')) return json({ empty: true, weekly: { week_start:'2026-11-09' } })
    return json({})
  })
  vi.stubGlobal('fetch', fetcher)
}
beforeEach(() => { location.hash = ''; localStorage.clear(); setup(); stores.invalidateAll() })
afterEach(async () => { cleanup(); await tick(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
it('default_route_opens_latest_chat', async () => {
  render(Home)
  expect(await screen.findByText('最近一段对话')).toBeVisible()
  expect(fetcher.mock.calls.some(([url]) => url === '/api/chats/c1')).toBe(true)
})
it.each([['today','今日'],['account','账户'],['market','行情'],['plans','计划与持仓'],['research','研究'],['retrospect','复盘'],['jobs','定时任务']])('view_routes_render_the_view %s', async (hash,title) => {
  location.hash = `#${hash}`; render(Home)
  await waitFor(() => expect(document.querySelector('[data-slot="page-header"]')).toHaveTextContent(title))
})
it('unknown_hash_falls_back_to_chat', async () => {
  location.hash = '#unknown'; render(Home)
  expect(await screen.findByText('最近一段对话')).toBeVisible()
})
it('bring_into_chat_switches_to_chat_with_chip', async () => {
  location.hash = '#plans'; render(Home)
  await screen.findAllByRole('heading', { name:'计划与持仓', level:1 })
  bringToChat({ kind:'plan', ticker:'RKLB', id:12 })
  expect(await screen.findByRole('button', { name:'移除 RKLB · 计划 #12' })).toBeVisible()
  expect(location.hash).toBe('#chat/c1')
})
it('no_run_state_renders_empty_panes', async () => {
  setup(true); stores.invalidateAll(); render(Home)
  expect((await screen.findAllByText('还没有运行记录')).some(node => node.closest('header'))).toBe(true)
  await fireEvent.click(screen.getByRole('button', { name:'行情' }))
  expect(await screen.findByText('还没有运行记录', { selector:'p.text-sm' })).toBeVisible()
  await fireEvent.click(screen.getByRole('button', { name:'定时任务' }))
  expect(await screen.findByText('还没有运行记录', { selector:'p.text-sm' })).toBeVisible()
  await fireEvent.click(screen.getByRole('button', { name:'仪表' }))
  expect(await screen.findByText('今日无操作')).toBeVisible()
})
it('cmd_k_focuses_the_input', async () => {
  location.hash = '#account'; render(Home)
  await screen.findAllByRole('heading', { name:'账户', level:1 })
  await fireEvent.keyDown(window, { key:'k', metaKey:true })
  await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText('提问')))
  expect(location.hash).toBe('#chat/c1')
})
it('switching views keeps the same chat stream alive', async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const response = new Response(new ReadableStream<Uint8Array>({ start(value) { controller = value } }))
  const original = fetcher.getMockImplementation()!
  fetcher.mockImplementation(async (url: RequestInfo | URL, init?: RequestInit) => String(url).endsWith('/messages') ? response : original(url,init))
  render(Home)
  await screen.findByText('最近一段对话')
  await fireEvent.input(screen.getByLabelText('提问'), { target:{ value:'继续问' } })
  await fireEvent.click(screen.getByRole('button', { name:'发送' }))
  const emit = (kind: string, text = '') => controller.enqueue(new TextEncoder().encode(`event: ${kind}\ndata: ${JSON.stringify({ kind, text })}\n\n`))
  emit('text','保持连接')
  expect(await screen.findByText('保持连接')).toBeVisible()
  location.hash = '#market'; window.dispatchEvent(new Event('hashchange'))
  await waitFor(() => expect(screen.getByText('保持连接')).not.toBeVisible())
  emit('text','，完成回答'); emit('done'); controller.close()
  await fireEvent.keyDown(window, { key:'k', ctrlKey:true })
  expect(await screen.findByText('保持连接，完成回答')).toBeVisible()
  expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith('/messages'))).toHaveLength(1)
  expect(fetcher.mock.calls.some(([url]) => String(url).endsWith('/cancel'))).toBe(false)
})

it('page action feedback stays local and follows the most recent conversation', async () => {
  location.hash = '#account'; render(Home)
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => url === '/api/chats/c1')).toBe(true))
  actionCompleted('已记录出入金')
  await fireEvent.keyDown(window, { key:'k', metaKey:true })
  expect(await screen.findByText('已记录出入金')).toBeVisible()
  expect(fetcher.mock.calls.some(([url]) => String(url).endsWith('/messages'))).toBe(false)
})
