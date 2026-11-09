import { afterEach, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/svelte'
import SourceLine from './SourceLine.svelte'
import { meta } from '../test-fixtures'
afterEach(cleanup)
it('renders exact call provenance with calculation version', () => {
 const { container } = render(SourceLine, { meta:{ ...meta, tool:'get_market' }, calcVersion:'regime-v3' })
 expect(container.textContent).toBe('get_market · call:7 · 2026-11-06 · regime-v3')
})
it('renders in-progress provenance without a ref', () => {
 const { container } = render(SourceLine, { meta:{ ...meta, tool:'get_market', ref:null } })
 expect(container.textContent).toBe('get_market · 调用中')
})
it('falls back to call timestamp in Beijing', () => {
 const { container } = render(SourceLine, { meta:{ ...meta, session:null, calledAt:'2026-11-06T00:00:00Z' } })
 expect(container.textContent).toBe('test_tool · call:7 · 2026-11-06 08:00:00 北京')
})
