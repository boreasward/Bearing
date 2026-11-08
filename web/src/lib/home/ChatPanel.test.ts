import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import ChatPanel from './ChatPanel.svelte'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

const encoder = new TextEncoder()
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const frame = (kind: string, payload: Record<string, unknown>) =>
  `event: ${kind}\ndata: ${JSON.stringify({ kind, text: '', tool: '', ref: '', usage: {}, ...payload })}\n\n`
const sse = (frames: string[]) =>
  new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const f of frames) controller.enqueue(encoder.encode(f))
        controller.close()
      },
    }),
    { status: 200, headers: { 'content-type': 'text/event-stream' } },
  )

const CHATS = {
  chats: [{ chat_id: 'c1', title: '关于止损的追问', created_at: '2026-11-06T22:00:00+00:00', read_only: false, model: 'claude-sonnet' }],
}
const DETAIL = {
  chat: { chat_id: 'c1', title: '关于止损的追问', runtime: 'pi', provider: 'anthropic', model: 'claude-sonnet', read_only: false, created_at: '2026-11-06T22:00:00+00:00' },
  messages: [{ seq: 1, role: 'user', content: 'MU 的止损为什么是这个价位？', status: null, session: '2026-11-06', tool_refs: [], usage: null, created_at: '2026-11-06T22:01:00+00:00' }],
}

function mountWith(messagesResponse: () => Response) {
  const router = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.startsWith('/api/chats?')) return json(CHATS)
    if (url === '/api/chats/c1') return json(DETAIL)
    if (url === '/api/chats/c1/messages') return messagesResponse()
    return json({})
  })
  vi.stubGlobal('fetch', router)
  render(ChatPanel)
  return router
}

async function send(text: string) {
  await fireEvent.input(screen.getByLabelText('提问'), { target: { value: text } })
  await fireEvent.click(screen.getByRole('button', { name: '发送' }))
}

describe('ChatPanel', () => {
  it('streams text incrementally and renders expandable tool calls', async () => {
    mountWith(() =>
      sse([
        frame('text', { text: 'MU 的止损按 ' }),
        frame('text', { text: '2 倍 ATR 算出。' }),
        frame('tool_start', { tool: 'record_trade' }),
        frame('tool_end', { tool: 'record_trade', ref: 'call:3' }),
        frame('done', { usage: { input: 100, output: 40, total: 140 } }),
      ]),
    )
    expect(await screen.findByText('MU 的止损为什么是这个价位？')).toBeInTheDocument()

    await send('记一笔买入')
    expect(await screen.findByText(/2 倍 ATR 算出。/)).toBeInTheDocument()

    const tool = screen.getByRole('button', { name: /record_trade/ })
    expect(tool).toHaveAttribute('aria-expanded', 'false')
    await fireEvent.click(tool)
    expect(tool).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('call:3')).toBeVisible()
    // the turn is over: the input is usable again
    await waitFor(() => expect(screen.getByLabelText('提问')).toBeEnabled())
  })

  it('409 brief_running shows 正在生成简报，请稍后再问 and keeps the input usable', async () => {
    mountWith(() => json({ error: 'brief_running' }, 409))
    await screen.findByText('MU 的止损为什么是这个价位？')

    await send('现在可以问了吗')
    expect(await screen.findByText('正在生成简报，请稍后再问')).toBeInTheDocument()
    expect(screen.getByLabelText('提问')).toBeEnabled()
  })

  it('the cancel button posts …/cancel and shows 已取消', async () => {
    let timer: ReturnType<typeof setInterval> | undefined
    const router = mountWith(() => {
      let n = 0
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          timer = setInterval(() => {
            n += 1
            try {
              controller.enqueue(encoder.encode(frame('text', { text: `第 ${n} 段。` })))
            } catch {
              if (timer) clearInterval(timer)
            }
          }, 5)
        },
        cancel() {
          if (timer) clearInterval(timer)
        },
      })
      return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } })
    })
    await screen.findByText('MU 的止损为什么是这个价位？')

    await send('这一轮会很长')
    expect(await screen.findByText('第 1 段。')).toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(await screen.findByText('已取消')).toBeInTheDocument()
    await waitFor(() => expect(router.mock.calls.some(([u]) => String(u) === '/api/chats/c1/cancel')).toBe(true))
    await waitFor(() => expect(screen.getByLabelText('提问')).toBeEnabled())
  })

  it('an error frame shows the reason and re-enables the input', async () => {
    mountWith(() => sse([frame('text', { text: '回答到一半' }), frame('error', { text: '模型调用失败' })]))
    await screen.findByText('MU 的止损为什么是这个价位？')

    await send('再问一次')
    expect(await screen.findByText('模型调用失败')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('提问')).toBeEnabled())
  })

  it('prefill sets the draft and focuses the input; an unchanged seq never overwrites it', async () => {
    const router = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/chats?')) return json(CHATS)
      if (url === '/api/chats/c1') return json(DETAIL)
      if (url === '/api/chats/c1/messages') return sse([frame('done', {})])
      return json({})
    })
    vi.stubGlobal('fetch', router)
    const view = render(ChatPanel)
    await screen.findByText('MU 的止损为什么是这个价位？')
    const input = () => screen.getByLabelText('提问') as HTMLTextAreaElement
    await fireEvent.input(input(), { target: { value: '自己打的一句话' } })

    view.rerender({ prefill: { text: '研究 SNDK：', seq: 1 } })
    await waitFor(() => expect(input().value).toBe('研究 SNDK：'))
    expect(document.activeElement).toBe(input()) // 预填后聚焦

    // 同一个 seq 重渲染不覆盖用户草稿（只有新预填才写）
    await fireEvent.input(input(), { target: { value: '改过的草稿' } })
    view.rerender({ prefill: { text: '研究 ：', seq: 1 } })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(input().value).toBe('改过的草稿')
    // 新 seq 才重新预填
    view.rerender({ prefill: { text: '研究 ：', seq: 2 } })
    await waitFor(() => expect(input().value).toBe('研究 ：'))
  })

  it('新开对话 posts /api/chats and switches to the new chat', async () => {
    const created = { chat_id: 'c2', title: '新对话', created_at: '2026-11-07T01:00:00+00:00', read_only: false, model: 'claude-sonnet' }
    const list = { chats: [...CHATS.chats, created] }
    const detail = { chat: { ...DETAIL.chat, chat_id: 'c2', title: '新对话' }, messages: [] }
    const router = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/chats?')) return json(list)
      if (url === '/api/chats') return json({ chat_id: 'c2', read_only: false })
      if (url === '/api/chats/c2') return json(detail)
      if (url === '/api/chats/c1') return json(DETAIL)
      return json({})
    })
    vi.stubGlobal('fetch', router)
    render(ChatPanel)
    await screen.findByText('MU 的止损为什么是这个价位？')

    await fireEvent.click(screen.getByRole('button', { name: '新开对话' }))
    expect(router).toHaveBeenCalledWith('/api/chats', expect.anything())
    await waitFor(() => expect(router.mock.calls.some(([u]) => String(u) === '/api/chats/c2')).toBe(true))
  })
})
