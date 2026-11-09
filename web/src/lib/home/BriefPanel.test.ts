import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import { tick } from 'svelte'
import BriefPanel from './BriefPanel.svelte'
import { invalidateAll } from '$lib/stores'
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status })
beforeEach(() => { invalidateAll() })
afterEach(async () => { cleanup(); await tick(); vi.unstubAllGlobals() })
it('loads a route-selected brief and retains selection across global writes', async () => {
  let revision = '原文'
  const fetcher = vi.fn(async (url: RequestInfo | URL) => {
    const path = String(url)
    if (path.includes('?')) return json({ briefs:[{ session:'2026-11-06', status:'final' },{ session:'2026-11-05', status:'final' }] })
    const session = path.split('/').at(-1)
    return json({ session, markdown:`${session} ${revision}`, status:'final', unfinished:false, error:null })
  })
  vi.stubGlobal('fetch', fetcher)
  render(BriefPanel, { session:'2026-11-05' })
  expect(await screen.findByText('2026-11-05 原文')).toBeInTheDocument()
  revision = '更新'; fetcher.mockClear(); invalidateAll()
  expect(await screen.findByText('2026-11-05 更新')).toBeInTheDocument()
  expect(fetcher.mock.calls.some(([url]) => url === '/api/briefs/latest')).toBe(false)
  await fireEvent.click(await screen.findByLabelText('选择简报'))
  await fireEvent.pointerUp(await screen.findByRole('option', { name:'2026-11-06' }))
  expect(await screen.findByText('2026-11-06 更新')).toBeInTheDocument()
  fetcher.mockClear(); invalidateAll()
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => url === '/api/briefs/2026-11-06')).toBe(true))
})
it('shows no-brief and no-run reasons in Chinese', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => String(url).includes('?') ? json({ briefs:[] }) : json({ error:'no_brief' },404)))
  const view = render(BriefPanel)
  expect(await screen.findByText('今天还没有简报')).toBeInTheDocument()
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => String(url).includes('?') ? json({ briefs:[] }) : json({ error:'no_run' },404)))
  await view.rerender({ session:'2026-11-04' })
  expect(await screen.findByText('还没有运行记录，简报会在第一次运行后出现')).toBeInTheDocument()
})
