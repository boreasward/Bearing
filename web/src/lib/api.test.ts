import { describe, it, expect, vi, afterEach } from 'vitest'
import { get, post, del, patch, put } from './api'

afterEach(() => vi.restoreAllMocks())

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })

describe('api client', () => {
  it('get parses json', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ ok: true })))
    expect(await get<{ ok: boolean }>('/api/status')).toEqual({ ok: true })
  })

  it('get throws the HTTP status, or the server detail when the caller opts in', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ error: 'boom' }, 500)))
    await expect(get('/api/x')).rejects.toThrow('HTTP 500')
    await expect(get('/api/x', { errorDetails: true })).rejects.toThrow('boom')
  })

  it('writes send the method and a json body, and throw the server error', async () => {
    const fetchMock = vi.fn(async () => json({ ok: true }))
    vi.stubGlobal('fetch', fetchMock)
    await post('/api/x', { a: 1 })
    await patch('/api/x', { a: 2 })
    await put('/api/x', { a: 3 })
    await del('/api/x')
    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][]
    expect(calls.map(([, init]) => init.method)).toEqual(['POST', 'PATCH', 'PUT', 'DELETE'])
    expect(calls.slice(0, 3).map(([, init]) => init.body)).toEqual(['{"a":1}', '{"a":2}', '{"a":3}'])
    vi.stubGlobal('fetch', vi.fn(async () => json({ error: 'x' }, 400)))
    await expect(post('/api/x', {})).rejects.toThrow('x')
  })
})
