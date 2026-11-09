import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import ChatPanel from './ChatPanel.svelte'
import * as stores from '$lib/stores'
import { fixture } from '$lib/views/test-fixtures'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const frame = (kind: string, payload: Record<string, unknown> = {}) => `event: ${kind}\ndata: ${JSON.stringify({ kind, ...payload })}\n\n`
function stream() {
  let controller: ReadableStreamDefaultController<Uint8Array>
  const response = new Response(new ReadableStream<Uint8Array>({ start(c) { controller = c } }), { headers: { 'content-type': 'text/event-stream' } })
  return { response, raw: (text: string) => controller.enqueue(new TextEncoder().encode(text)), emit: (kind: string, payload: Record<string, unknown> = {}) => controller.enqueue(new TextEncoder().encode(frame(kind, payload))), close: () => controller.close() }
}
const detail = { chat: { chat_id: 'c1', title: '问题', read_only: false }, messages: [] }
function setup(options: { history?: unknown; readOnly?: boolean; chips?: { kind: 'plan'; ticker: string; id: number }[] } = {}) {
  const s = stream()
  const fetcher = vi.fn(async (url: RequestInfo | URL, _init?: RequestInit): Promise<Response> => {
    const path = String(url)
    if (path.endsWith('/messages')) return s.response
    if (path.includes('/calls?')) {
      const ref = new URL(path, 'http://test').searchParams.get('ref')!
      return json({ ref, tool: 'get_market', args: {}, result: fixture('get_market'), error: null, truncated: false, called_at: '2026-11-06T00:00:00Z' })
    }
    if (path === '/api/chats/c1') return json(options.history ?? { ...detail, chat: { ...detail.chat, read_only: options.readOnly ?? false } })
    if (path === '/api/chats/c2') return json({ ...detail, chat: { ...detail.chat, chat_id: 'c2' } })
    return json({})
  })
  vi.stubGlobal('fetch', fetcher)
  const view = render(ChatPanel, { chatId: 'c1', onWrote: vi.fn(), prefillChips: options.chips })
  return { ...s, fetcher, view }
}
async function send(text = '问题') {
  await waitFor(() => expect(screen.getByLabelText('提问')).toBeEnabled())
  await fireEvent.input(screen.getByLabelText('提问'), { target: { value: text } })
  await fireEvent.click(screen.getByRole('button', { name: '发送' }))
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('ChatPanel', () => {
  it('start_frame_sets_agent_session_and_tool_end_fetches_the_call', async () => {
    const s = setup()
    await send()
    s.emit('start', { agent_session: 'chat-c1-1' })
    s.emit('tool_start', { tool: 'get_market' })
    expect(await screen.findByText('市场环境 · 调用中')).toBeInTheDocument()
    s.emit('tool_end', { tool: 'get_market', ref: 'call:1' })
    s.emit('text', { text: '市场已查询。call:1' })
    s.emit('done'); s.close()
    expect(await screen.findByText(/get_market · call:1/)).toBeInTheDocument()
    expect(s.fetcher.mock.calls.some(([url]) => String(url) === '/api/chats/c1/calls?agent_session=chat-c1-1&ref=call:1')).toBe(true)
    expect(await screen.findByText('市场温度')).toBeInTheDocument()
  })
  it('two_calls_of_the_same_tool_resolve_separately', async () => {
    const s = setup()
    s.fetcher.mockImplementation(async (url) => {
      const path = String(url)
      if (path.endsWith('/messages')) return s.response
      if (path.includes('/calls?')) {
        const ref = new URL(path, 'http://test').searchParams.get('ref')!
        return json({ ref, tool: 'get_plans', result: [], called_at: ref === 'call:1' ? '2026-11-05' : '2026-11-06' })
      }
      return json(detail)
    })
    await send()
    s.emit('start', { agent_session: 'chat-c1-1' })
    s.emit('tool_start', { tool: 'get_plans' }); s.emit('tool_start', { tool: 'get_plans' })
    s.emit('tool_end', { tool: 'get_plans', ref: 'call:1' })
    s.emit('tool_end', { tool: 'get_plans', ref: 'call:2' }); s.emit('done'); s.close()
    await screen.findByText(/get_plans · call:2/)
    const cards = document.querySelectorAll('[data-call-ref]')
    expect([...cards].map(card => card.getAttribute('data-call-ref'))).toEqual(['call:1', 'call:2'])
    expect(cards[0]).toHaveTextContent('get_plans · call:1')
    expect(cards[1]).toHaveTextContent('get_plans · call:2')
  })
  it('tool_end_without_ref_shows_untraceable', async () => {
    const s = setup(); await send()
    s.emit('tool_start', { tool: 'get_market' }); s.emit('tool_end', { tool: 'get_market' }); s.emit('done'); s.close()
    expect(await screen.findByText('调用已完成，结果不可追溯')).toBeInTheDocument()
  })
  it('history_replays_cards_above_text', async () => {
    const s = setup({ history: { ...detail, messages: [{ seq: 1, role: 'assistant', content: '历史回答', tool_refs: ['call:1'], agent_session: 'chat-c1-1' }] } })
    expect(await screen.findByText(/get_market · call:1/)).toBeInTheDocument()
    const card = document.querySelector('[data-call-ref]')!
    expect(card.compareDocumentPosition(screen.getByText('历史回答')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(s.fetcher.mock.calls.filter(([u]) => String(u).includes('/calls?'))).toHaveLength(1)
  })
  it('history_null_result_is_distinct_from_fetch_failure', async () => {
    const s = setup({ history: { ...detail, messages: [{ seq: 1, role: 'assistant', content: '仍然显示文字', tool_refs: ['call:1'], agent_session: 'chat-c1-1' }] } })
    s.fetcher.mockImplementation(async (url) => String(url).includes('/calls?') ? json({ result: null, tool: 'get_market', ref: 'call:1' }) : json({ ...detail, messages: [{ seq: 1, role: 'assistant', content: '仍然显示文字', tool_refs: ['call:1'], agent_session: 'chat-c1-1' }] }))
    expect(await screen.findByText('这次调用早于 v3.2.1，没有保留结果')).toBeInTheDocument()
    expect(screen.getByText('仍然显示文字')).toBeInTheDocument()
  })
  it('chips_are_prefixed_and_rendered_back', async () => {
    const s = setup({ chips: [{ kind: 'plan', ticker: 'RKLB', id: 12 }] }); await send('继续问')
    const request = s.fetcher.mock.calls.find(([u]) => String(u).endsWith('/messages'))!
    expect(JSON.parse(String(request[1]?.body)).text).toBe('[RKLB · 计划 #12] 继续问')
    expect(await screen.findByText('RKLB · 计划 #12')).toBeInTheDocument()
    expect(screen.queryByText('[RKLB · 计划 #12] 继续问')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /移除/ })).not.toBeInTheDocument()
    s.emit('done'); s.close()
  })
  it('quick_prompts_send_fixed_sentences_except_research', async () => {
    for (const [label, text] of [['昨天市场热度', '昨天的市场热度怎么样？'], ['今日操作', '今天我要做什么？'], ['持仓怎么样', '持仓现在什么情况？'], ['待入场名单', '待入场的计划有哪些？']]) {
      const s = setup()
      await waitFor(() => expect(screen.getByLabelText('提问')).toBeEnabled())
      await fireEvent.click(screen.getByRole('button', { name: label }))
      await waitFor(() => expect(s.fetcher.mock.calls.some(([u, init]) => String(u).endsWith('/messages') && JSON.parse(String(init?.body)).text === text)).toBe(true))
      s.emit('done'); s.close(); cleanup()
    }
    const s = setup(); await fireEvent.click(screen.getByRole('button', { name: '发起研究…' }))
    expect(screen.getByLabelText('提问')).toHaveValue('研究 ')
    expect(s.fetcher.mock.calls.some(([u]) => String(u).endsWith('/messages'))).toBe(false)
    s.close()
  })
  it('write_tool_triggers_onWrote_and_invalidateAll', async () => {
    const invalidate = vi.spyOn(stores, 'invalidateAll')
    const s = setup(); const onWrote = vi.fn()
    await s.view.rerender({ chatId: 'c1', onWrote }); await send()
    s.emit('start', { agent_session: 'chat-c1-1' }); s.emit('tool_start', { tool: 'start_research' }); s.emit('tool_end', { tool: 'start_research', ref: 'call:1' }); s.emit('done'); s.close()
    await waitFor(() => expect(onWrote).toHaveBeenCalledTimes(1))
    expect(invalidate).toHaveBeenCalledTimes(1)
  })
  it('brief_running keeps the draft usable', async () => {
    const s = setup(); s.fetcher.mockImplementation(async (u) => String(u).endsWith('/messages') ? json({ error: 'brief_running' }, 409) : json(detail))
    await send('稍后问')
    expect(await screen.findByText('正在生成简报，请稍后再问')).toBeInTheDocument()
    expect(screen.getByLabelText('提问')).toBeEnabled()
    expect(screen.getByLabelText('提问')).toHaveValue('稍后问'); s.close()
  })
  it('read_only prevents sending', async () => {
    const s = setup({ readOnly: true })
    expect(await screen.findByText('该对话在切换运行时后变为只读，可看不可写。')).toBeInTheDocument()
    expect(screen.getByLabelText('提问')).toBeDisabled(); s.close()
  })
  it('cancel posts to the original chat after switching and retains its turn', async () => {
    const s = setup(); await send()
    s.emit('start', { agent_session: 'chat-c1-1' }); s.emit('text', { text: '第一段' })
    expect(await screen.findByText('第一段')).toBeInTheDocument()
    await s.view.rerender({ chatId: 'c2', onWrote: vi.fn() })
    await waitFor(() => expect(screen.queryByText('第一段')).not.toBeInTheDocument())
    s.emit('text', { text: '第二段' })
    await s.view.rerender({ chatId: 'c1', onWrote: vi.fn() })
    expect(await screen.findByText('第一段第二段')).toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(await screen.findByText('已取消')).toBeInTheDocument()
    expect(s.fetcher.mock.calls.some(([u]) => String(u) === '/api/chats/c1/cancel')).toBe(true)
    await waitFor(() => expect(screen.queryByRole('button', { name: '取消' })).not.toBeInTheDocument())
  })
  it('local actions append a transient explanation', async () => {
    const s = setup(); await send(); s.emit('done'); s.close()
    s.view.component.appendFeedback('已记录成交')
    expect(await screen.findByText('已记录成交')).toBeInTheDocument()
  })
})
it('history cards wait for the viewport and disconnect the observer', async () => {
  let intersect: IntersectionObserverCallback = () => {}
  const disconnect = vi.fn()
  const observe = vi.fn()
  vi.stubGlobal('IntersectionObserver', class {
    constructor(callback: IntersectionObserverCallback) { intersect = callback }
    observe = observe
    disconnect = disconnect
  })
  const s = setup({ history: { ...detail, messages: [{ seq:1, role:'assistant', content:'先显示历史文字', tool_refs:['call:1'], agent_session:'chat-c1-1' }] } })
  expect(await screen.findByText('先显示历史文字')).toBeInTheDocument()
  expect(observe).toHaveBeenCalledTimes(1)
  expect(s.fetcher.mock.calls.some(([u]) => String(u).includes('/calls?'))).toBe(false)
  intersect([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver)
  expect(await screen.findByText(/get_market · call:1/)).toBeInTheDocument()
  expect(disconnect).toHaveBeenCalled(); s.close()
})
it('call references scroll and highlight the matching card', async () => {
  const scroll = vi.fn()
  const s = setup(); await send()
  s.emit('start', { agent_session:'chat-c1-1' }); s.emit('tool_start', { tool:'get_market' }); s.emit('tool_end', { tool:'get_market', ref:'call:1' }); s.emit('text', { text:'看 call:1 的结果' }); s.emit('done'); s.close()
  const card = await waitFor(() => {
    const node = document.querySelector<HTMLElement>('[data-call-ref="call:1"]')
    expect(node).not.toBeNull(); return node!
  })
  card.scrollIntoView = scroll
  await fireEvent.click(await screen.findByRole('button', { name:'call:1' }))
  expect(scroll).toHaveBeenCalledWith({ behavior:'smooth', block:'center' })
  expect(card.parentElement).toHaveClass('ring-2')
})
it('retains a stream that finishes while another conversation is selected', async () => {
  const s = setup(); await send()
  s.emit('start', { agent_session:'chat-c1-1' }); s.emit('text', { text:'旧对话完整回答' })
  await screen.findByText('旧对话完整回答')
  await s.view.rerender({ chatId:'c2', onWrote:vi.fn() })
  s.emit('done'); s.close()
  await s.view.rerender({ chatId:'c1', onWrote:vi.fn() })
  expect(await screen.findByText('旧对话完整回答')).toBeInTheDocument()
  expect(s.fetcher.mock.calls.filter(([u]) => String(u) === '/api/chats/c1')).toHaveLength(1)
})
it('error and read-only response frames keep their existing notices', async () => {
  const s = setup(); await send()
  s.emit('error', { text:'chat_read_only' }); s.close()
  expect(await screen.findByText('该对话已只读')).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByRole('button', { name:'取消' })).not.toBeInTheDocument())
})
it('prefill sequences focus the input without overwriting an edited draft', async () => {
  const s = setup()
  await s.view.rerender({ chatId:'c1', onWrote:vi.fn(), prefillText:{ text:'研究 RKLB：', seq:1 } })
  await waitFor(() => expect(screen.getByLabelText('提问')).toHaveValue('研究 RKLB：'))
  await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText('提问')))
  await fireEvent.input(screen.getByLabelText('提问'), { target:{ value:'修改的草稿' } })
  await s.view.rerender({ chatId:'c1', onWrote:vi.fn(), prefillText:{ text:'不应该覆盖', seq:1 } })
  expect(screen.getByLabelText('提问')).toHaveValue('修改的草稿'); s.close()
})
it.each(['error', 'eof'])('interrupted calls leave completed cards intact on %s', async (terminal) => {
  const s = setup(); await send()
  s.emit('start', { agent_session:'chat-c1-1' })
  s.emit('tool_start', { tool:'get_market' }); s.emit('tool_end', { tool:'get_market', ref:'call:1' })
  await screen.findByText('市场温度')
  s.emit('tool_start', { tool:'get_plans' })
  expect(await screen.findByText('计划列表 · 调用中')).toBeInTheDocument()
  if (terminal === 'error') s.emit('error', { text:'这一轮出错了' })
  s.close()
  expect(await screen.findByText('调用未完成')).toBeInTheDocument()
  expect(screen.getByText('市场温度')).toBeInTheDocument()
  expect(screen.queryByText('计划列表 · 调用中')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name:'重试' })).not.toBeInTheDocument()
})
it('cancellation marks unresolved calls and blocks another send until the cancel request settles', async () => {
  const s = setup()
  let settleCancel!: (response: Response) => void
  const cancelResponse = new Promise<Response>(resolve => { settleCancel = resolve })
  const original = s.fetcher.getMockImplementation()!
  s.fetcher.mockImplementation((url, init) => String(url).endsWith('/cancel') ? cancelResponse : original(url, init))
  await send()
  s.emit('tool_start', { tool:'record_trade' })
  await screen.findByText('记录成交 · 调用中')
  await fireEvent.click(screen.getByRole('button', { name:'取消' }))
  expect(await screen.findByText('已停止等待，调用结果未确认')).toBeInTheDocument()
  await fireEvent.input(screen.getByLabelText('提问'), { target:{ value:'下一轮' } })
  expect(screen.getByRole('button', { name:'发送' })).toBeDisabled()
  expect(screen.queryByRole('button', { name:'重试' })).not.toBeInTheDocument()
  settleCancel(json({}))
  await waitFor(() => expect(screen.getByRole('button', { name:'发送' })).toBeEnabled())
  expect(s.fetcher.mock.calls.filter(([u]) => String(u).endsWith('/messages'))).toHaveLength(1)
})
it.each(['{"kind":"text","text":"broken', '{"kind":"text","text":"late text"}'])('cancellation remains cancelled with a buffered frame %s', async (payload) => {
  const s = setup(); await send()
  s.emit('tool_start', { tool:'record_trade' })
  await screen.findByText('记录成交 · 调用中')
  s.raw(`event: text\ndata: ${payload}`)
  await Promise.resolve()
  await fireEvent.click(screen.getByRole('button', { name:'取消' }))
  expect(await screen.findByText('已停止等待，调用结果未确认')).toBeInTheDocument()
  expect(screen.queryByText('调用未完成')).not.toBeInTheDocument()
  expect(screen.queryByText('late text')).not.toBeInTheDocument()
  expect(screen.getByText('已取消')).toBeInTheDocument()
})

it.each(['POST', 'GET'])('serializes empty-state sends while creation %s is pending', async (pendingStage) => {
  let settle!: (response: Response) => void
  const deferred = new Promise<Response>(resolve => { settle = resolve })
  const s = stream()
  const fetcher = vi.fn(async (url: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = String(url)
    if (path === '/api/chats' && init?.method === 'POST') return pendingStage === 'POST' ? deferred : json({ chat_id:'new' })
    if (path === '/api/chats/new') return pendingStage === 'GET' ? deferred : json({ chat:{ ...detail.chat, chat_id:'new' }, messages:[] })
    if (path.endsWith('/messages')) return s.response
    return json({ chats:[] })
  })
  vi.stubGlobal('fetch', fetcher)
  render(ChatPanel, { chatId:null, onWrote:vi.fn() })
  await fireEvent.input(screen.getByLabelText('提问'), { target:{ value:'第一句' } })
  const form = screen.getByLabelText('提问').closest('form')!
  await fireEvent.submit(form)
  await fireEvent.submit(form)
  await waitFor(() => expect(fetcher.mock.calls.filter(([url, init]) => url === '/api/chats' && init?.method === 'POST')).toHaveLength(1))
  expect(screen.getByRole('button', { name:'发送' })).toBeDisabled()
  expect(screen.getByRole('button', { name:'今日操作' })).toBeDisabled()
  expect(screen.getByRole('button', { name:'新开对话' })).toBeDisabled()
  settle(pendingStage === 'POST' ? json({ chat_id:'new' }) : json({ chat:{ ...detail.chat, chat_id:'new' }, messages:[] }))
  await screen.findByRole('button', { name:'取消' })
  expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith('/messages'))).toHaveLength(1)
  expect(JSON.parse(String(fetcher.mock.calls.find(([url]) => String(url).endsWith('/messages'))![1]?.body))).toEqual({ text:'第一句' })
  await fireEvent.click(screen.getByRole('button', { name:'取消' }))
  expect(fetcher.mock.calls.some(([url]) => url === '/api/chats/new/cancel')).toBe(true)
})
it('releases the empty-state send guard after a creation failure and preserves the draft for retry', async () => {
  let attempts = 0
  const s = stream()
  const fetcher = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    if (url === '/api/chats' && init?.method === 'POST') return ++attempts === 1 ? json({ error:'no_runtime_selected' },503) : json({ chat_id:'new' })
    if (url === '/api/chats/new') return json({ chat:{ ...detail.chat, chat_id:'new' }, messages:[] })
    if (String(url).endsWith('/messages')) return s.response
    return json({ chats:[] })
  })
  vi.stubGlobal('fetch', fetcher)
  render(ChatPanel, { chatId:null, onWrote:vi.fn() })
  await send('失败后重试')
  await waitFor(() => expect(screen.getByRole('button', { name:'发送' })).toBeEnabled())
  expect(screen.getByLabelText('提问')).toHaveValue('失败后重试')
  await fireEvent.click(screen.getByRole('button', { name:'发送' }))
  await screen.findByRole('button', { name:'取消' })
  expect(attempts).toBe(2)
  expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith('/messages'))).toHaveLength(1)
  s.emit('done'); s.close()
})

it.each(['done', 'error', 'cancelled', 'eof', 'abort', 'network'])('refreshes completed writes once after terminal %s', async terminal => {
 const invalidate = vi.spyOn(stores, 'invalidateAll')
 const onWrote = vi.fn()
 const s = setup(); await s.view.rerender({ chatId:'c1', onWrote }); await send()
 s.emit('start', { agent_session:'chat-c1-1' })
 for (const [tool, ref] of [['start_research', 'call:1'], ['save_summary', 'call:2']]) {
  s.emit('tool_start', { tool }); s.emit('tool_end', { tool, ref })
 }
 s.emit('text', { text:'两次工具调用已结束' })
 await screen.findByText('两次工具调用已结束')
 expect(invalidate).not.toHaveBeenCalled(); expect(onWrote).not.toHaveBeenCalled()
 if (terminal === 'abort') await fireEvent.click(screen.getByRole('button', { name:'取消' }))
 else if (terminal === 'network') s.raw('event: text\ndata: not-json\n\n')
 else { if (terminal !== 'eof') s.emit(terminal); s.close() }
 await waitFor(() => expect(onWrote).toHaveBeenCalledTimes(1))
 expect(invalidate).toHaveBeenCalledTimes(1)
})
it.each([false, true])('renders a saved JSON null normally in history=%s', async history => {
 const message = { seq:1, role:'assistant', content:'没有近期财报', tool_refs:['call:1'], agent_session:'chat-c1-1' }
 const s = setup(history ? { history:{ ...detail, messages:[message] } } : {})
 const original = s.fetcher.getMockImplementation()!
 s.fetcher.mockImplementation((url, init) => String(url).includes('/calls?') ? Promise.resolve(json({ ref:'call:1', tool:'get_earnings', result:null, result_available:true, error:null, truncated:false, called_at:'2026-11-06T00:00:00Z' })) : original(url,init))
 if (!history) {
  await send(); s.emit('start', { agent_session:'chat-c1-1' }); s.emit('tool_start', { tool:'get_earnings' }); s.emit('tool_end', { tool:'get_earnings', ref:'call:1' }); s.emit('done'); s.close()
 }
 expect(await screen.findByText('这次查询没有返回记录')).toBeInTheDocument()
 expect(screen.queryByText('这次调用早于 v3.2.1，没有保留结果')).not.toBeInTheDocument()
 if (history) expect(screen.getByText('没有近期财报')).toBeInTheDocument()
})
