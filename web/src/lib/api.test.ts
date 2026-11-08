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

  it('write errors carry the server text detail as code: detail, so errorText shows the Chinese reason', async () => {
    // 2026-11-08 用户截图：记出入金只显示「invalid_argument」，后端其实给了「effective_at 需带时区」
    const { errorText } = await import('./home/types')
    vi.stubGlobal('fetch', vi.fn(async () => json({ error: 'invalid_argument', detail: 'effective_at 需带时区' }, 400)))
    const failure = await post('/api/x', {}).catch((error: unknown) => error)
    expect((failure as Error).message).toBe('invalid_argument: effective_at 需带时区')
    expect(errorText(failure)).toBe('effective_at 需带时区')
    // detail 是对象（例如 active_plan_exists 带回已有计划）时只给错误码
    vi.stubGlobal('fetch', vi.fn(async () => json({ error: 'active_plan_exists', detail: { id: 3 } }, 400)))
    await expect(put('/api/x', {})).rejects.toThrow(/^active_plan_exists$/)
  })
})
