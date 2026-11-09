import { describe, expect, it, vi } from 'vitest'
import { readEvents, type SseEvent } from './sse'

const encoder = new TextEncoder()

/** A SSE Response whose body yields the given strings as separate chunks. */
function sseResponse(chunks: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } })
}

const FRAME_A = 'event: text\ndata: {"kind":"text","text":"你"}\n\n'
const FRAME_B = 'event: text\ndata: {"kind":"text","text":"好"}\n\n'
const FRAME_TOOL = 'event: tool_start\ndata: {"kind":"tool_start","text":"","tool":"record_trade","ref":"","usage":{}}\n\n'

describe('readEvents', () => {
  it('restores events from a stream chopped at arbitrary boundaries', async () => {
    // FRAME_A split into three arbitrary pieces, then B and the tool frame whole
    const response = sseResponse([FRAME_A.slice(0, 8), FRAME_A.slice(8, 20), FRAME_A.slice(20), FRAME_B, FRAME_TOOL])
    const events: { event: string; data: string }[] = []
    await readEvents(response, (e) => events.push(e))
    expect(events).toEqual([
      { event: 'text', data: '{"kind":"text","text":"你"}' },
      { event: 'text', data: '{"kind":"text","text":"好"}' },
      { event: 'tool_start', data: '{"kind":"tool_start","text":"","tool":"record_trade","ref":"","usage":{}}' },
    ])
  })

  it('dispatches the trailing frame even without a final blank line', async () => {
    const response = sseResponse([FRAME_A, 'event: done\ndata: {"kind":"done","text":"","tool":"","ref":"","usage":{}}'])
    const events: { event: string; data: string }[] = []
    await readEvents(response, (e) => events.push(e))
    expect(events.map((e) => e.event)).toEqual(['text', 'done'])
  })

  it('stops dispatching once the AbortController aborts', async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(FRAME_A))
        controller.enqueue(encoder.encode(FRAME_B))
        controller.enqueue(encoder.encode(FRAME_TOOL))
        controller.close()
      },
    })
    const controller = new AbortController()
    const onEvent = vi.fn((_event: SseEvent) => controller.abort())
    await readEvents(new Response(stream), onEvent, controller.signal)
    expect(onEvent).toHaveBeenCalledTimes(1)
    expect(onEvent.mock.calls[0][0]).toEqual({ event: 'text', data: '{"kind":"text","text":"你"}' })
  })

  it('does not call back at all when aborted before the first chunk', async () => {
    const controller = new AbortController()
    controller.abort()
    const onEvent = vi.fn()
    await readEvents(sseResponse([FRAME_A]), onEvent, controller.signal)
    expect(onEvent).not.toHaveBeenCalled()
  })

  it('a stream with an empty body dispatches nothing', async () => {
    const onEvent = vi.fn()
    await readEvents(new Response(null), onEvent)
    expect(onEvent).not.toHaveBeenCalled()
  })
})
it('aborts an idle reader without waiting for another chunk', async () => {
  const cancel = vi.fn()
  const response = new Response(new ReadableStream<Uint8Array>({ cancel }))
  const controller = new AbortController()
  const pending = readEvents(response, vi.fn(), controller.signal)
  controller.abort()
  await pending
  expect(cancel).toHaveBeenCalledTimes(1)
})
