// SSE reader for the chat stream (POST /api/chats/{id}/messages answers `text/event-stream`
// with frames `event: <kind>\ndata: {json RuntimeEvent}\n\n`). Chunks can arrive split at any
// boundary, so the parser buffers until a blank line completes a frame.

export interface SseEvent {
  event: string
  data: string
}

/** Read the response body and dispatch every complete frame; returns when the stream ends or `signal` aborts. */
export async function readEvents(
  response: Response,
  onEvent: (event: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (!response.body) return
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const abort = () => { void reader.cancel().catch(() => {}) }
  signal?.addEventListener('abort', abort, { once: true })

  const dispatch = (block: string) => {
    let event = 'message'
    const data: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
    }
    onEvent({ event, data: data.join('\n') })
  }

  try {
    while (true) {
      if (signal?.aborted) {
        await reader.cancel().catch(() => {})
        return
      }
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
      let index = buffer.indexOf('\n\n')
      while (index !== -1) {
        if (signal?.aborted) {
          await reader.cancel().catch(() => {})
          return
        }
        const block = buffer.slice(0, index)
        buffer = buffer.slice(index + 2)
        if (block.trim()) dispatch(block)
        index = buffer.indexOf('\n\n')
      }
    }
    buffer += decoder.decode()
    if (buffer.trim()) dispatch(buffer)
  } catch (error) {
    // aborting the fetch surfaces here as an AbortError — the caller already knows, just stop
    if ((error as Error)?.name === 'AbortError') return
    throw error
  } finally {
    signal?.removeEventListener('abort', abort)
    reader.releaseLock()
  }
}
