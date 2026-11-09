import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushSync, tick } from 'svelte'
import { writable } from 'svelte/store'
import { fromStore } from 'svelte/store'
import { render, cleanup } from '@testing-library/svelte'
import ResourceConsumer from './ResourceConsumer.test.svelte'
import { createResource } from './resource.svelte'
import { today, market, invalidateAll } from './index'

const response = (data: unknown) => ({ ok: true, json: async () => data }) as Response
const settle = async () => { await tick(); await new Promise(resolve => setTimeout(resolve, 0)); flushSync() }
const mount = (resource: ReturnType<typeof createResource>) => render(ResourceConsumer, { resource }).unmount
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('resources', () => {
  it('loads explicitly from loading to ready', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ value: 1 })))
    const resource = createResource<{ value: number }>('/test')
    expect(resource.state).toBe('loading')
    await resource.load()
    expect(resource.state).toBe('ready')
    expect(resource.data).toEqual({ value: 1 })
  })
  it.each([
    [new TypeError('Failed to fetch'), '网络连接失败，请稍后重试'],
    [new Error('no_run'), '还没有运行记录'],
    [new Error('invalid_argument: 日期不正确'), '日期不正确'],
    [new Error('HTTP 503'), '服务暂时不可用，请稍后重试'],
  ])('maps failure %s to Chinese', async (cause, message) => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(cause))
    const resource = createResource('/test')
    await resource.load()
    expect(resource.state).toBe('error')
    expect(resource.error).toBe(message)
  })
  it('reads Chinese error envelopes', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 400, json: async () => ({ error: 'invalid_argument: 参数不正确' }) }))
    const resource = createResource('/test', { errorDetails: true })
    await resource.load()
    expect(resource.error).toBe('参数不正确')
  })
  it('refetches mounted consumers while preserving cached data, then pauses until remount', async () => {
    let resolve!: (r: Response) => void
    const fetcher = vi.fn().mockResolvedValueOnce(response({ value: 1 })).mockImplementationOnce(() => new Promise(r => { resolve = r }))
      .mockResolvedValue(response({ value: 3 }))
    vi.stubGlobal('fetch', fetcher)
    const resource = createResource('/test')
    const stop = mount(resource)
    await settle()
    expect(resource.data).toEqual({ value: 1 })
    resource.invalidate()
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(resource.data).toEqual({ value: 1 })
    resolve(response({ value: 2 }))
    await settle()
    expect(resource.data).toEqual({ value: 2 })
    stop()
    await settle()
    resource.invalidate()
    await settle()
    expect(fetcher).toHaveBeenCalledTimes(2)
    const stopAgain = mount(resource)
    await settle()
    expect(fetcher).toHaveBeenCalledTimes(3)
    expect(resource.data).toEqual({ value: 3 })
    stopAgain()
    await settle()
  })
  it('supports null and changing paths and ignores late responses', async () => {
    const pathStore = writable<string | null>(null)
    const path = fromStore(pathStore)
    let resolveOld!: (r: Response) => void
    const fetcher = vi.fn().mockImplementationOnce(() => new Promise(r => { resolveOld = r })).mockResolvedValue(response({ newest: true }))
    vi.stubGlobal('fetch', fetcher)
    const resource = createResource(() => path.current)
    expect(resource.state).toBe('empty')
    const stop = mount(resource)
    await settle()
    expect(fetcher).not.toHaveBeenCalled()
    pathStore.set('/old'); await settle()
    pathStore.set('/new'); await settle()
    expect(resource.data).toEqual({ newest: true })
    resolveOld(response({ old: true })); await settle()
    expect(resource.data).toEqual({ newest: true })
    pathStore.set(null); await settle()
    expect(resource.state).toBe('empty')
    expect(resource.data).toBeNull()
    stop(); await settle()
  })
  it('ignores an invalidated in-flight response for the same path', async () => {
    let resolveOld!: (r: Response) => void
    vi.stubGlobal('fetch', vi.fn().mockImplementationOnce(() => new Promise(r => { resolveOld = r })).mockResolvedValue(response({ new: true })))
    const resource = createResource('/test')
    const stop = mount(resource); await settle()
    resource.invalidate(); await settle()
    resolveOld(response({ old: true })); await settle()
    expect(resource.data).toEqual({ new: true })
    stop(); await settle()
  })
  it('invalidateAll refreshes two mounted shared stores', async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ session: '2026-11-06' }))
    vi.stubGlobal('fetch', fetcher)
    const stopToday = mount(today), stopMarket = mount(market)
    await settle()
    expect(fetcher).toHaveBeenCalledTimes(2)
    invalidateAll(); await settle()
    expect(fetcher).toHaveBeenCalledTimes(4)
    stopToday(); stopMarket(); await settle()
  })
})
