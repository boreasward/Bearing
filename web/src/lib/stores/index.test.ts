import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, waitFor } from '@testing-library/svelte'
import ResourceConsumer from './ResourceConsumer.test.svelte'
import { ledger, ledgerForLimit, invalidateAll } from './index'
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('memoizes valid ledger limits and reuses default resource', () => {
 expect(ledgerForLimit(20)).toBe(ledger)
 expect(ledgerForLimit(40)).toBe(ledgerForLimit(40))
 for (const invalid of [0,101,NaN,1.5]) expect(() => ledgerForLimit(invalid)).toThrow()
})
it('refetches an expanded mounted ledger after global writes', async () => {
 const fetcher = vi.fn(async () => new Response(JSON.stringify({ trades:[], cash_flows:[], equity:[], voids:[] })))
 vi.stubGlobal('fetch', fetcher)
 const resource = ledgerForLimit(40)
 render(ResourceConsumer, { resource })
 await waitFor(() => expect(resource.state).toBe('ready'))
 fetcher.mockClear(); invalidateAll()
 await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/ledger?limit=40', expect.anything()))
 expect(fetcher).toHaveBeenCalledTimes(1)
})
it('memoizes default briefs and retrospect selection and refreshes mounted historical resources', async () => {
 const { retrospect, retrospectFor, briefs, briefFor } = await import('./index')
 expect(retrospectFor(null,20)).toBe(retrospect)
 expect(retrospectFor('2026-11-06',50)).toBe(retrospectFor('2026-11-06',50))
 expect(briefFor()).toBe(briefFor('latest'))
 const fetcher = vi.fn(async (_url: RequestInfo | URL) => new Response(JSON.stringify({ session:'2026-11-06' })))
 vi.stubGlobal('fetch', fetcher)
 const selected = retrospectFor('2026-11-06',50)
 const brief = briefFor('2026-11-06')
 render(ResourceConsumer, { resource: selected })
 render(ResourceConsumer, { resource: brief })
 render(ResourceConsumer, { resource: briefs })
 await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3))
 await waitFor(() => expect(selected.state).toBe('ready'))
 fetcher.mockClear(); invalidateAll()
 await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3))
 expect(fetcher.mock.calls.map(call => String(call[0]))).toEqual(expect.arrayContaining(['/api/retrospect?limit=50&week=2026-11-06','/api/briefs/2026-11-06','/api/briefs?limit=30']))
})
