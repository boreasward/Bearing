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
