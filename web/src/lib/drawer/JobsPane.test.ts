import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/svelte'
import JobsPane from './JobsPane.svelte'
import { loadWorkbench } from '$lib/views/workbench-fixtures'
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('shows last four, next Beijing time, statuses and research/weekly', async () => {
 await loadWorkbench(); const { container } = render(JobsPane, { onBring:vi.fn() })
 expect(screen.getByText('2026-10-28')).toBeInTheDocument(); expect(screen.queryByText('2026-10-27')).toBeNull()
 expect(screen.getByText(/2026-11-10 11:30:00 北京/)).toBeInTheDocument()
 expect(container.querySelector('[data-domain="daily_run"][data-status="final"]')).toBeInTheDocument()
 expect(screen.getByText('降级')).toBeInTheDocument(); expect(screen.getByText('失败')).toBeInTheDocument(); expect(screen.getByText('未做完')).toBeInTheDocument()
 expect(screen.getByRole('heading', { name:'研究复查' })).toBeInTheDocument(); expect(screen.getByRole('heading', { name:'周报' })).toBeInTheDocument()
})
it('keeps repeated sessions distinct and ignores previous-week weekly reports at NY boundary', async () => {
 vi.useFakeTimers({ toFake:['Date'] }); vi.setSystemTime(new Date('2026-11-09T00:30:00Z'))
 const { runsData } = await import('$lib/views/workbench-fixtures')
 await loadWorkbench({ '/api/runs':{ ...runsData, runs:[{ ...runsData.runs[0], session:'2026-11-06' }, { ...runsData.runs[0], session:'2026-11-06', started_at:'2026-11-07T04:30:00Z' }] } })
 render(JobsPane, { onBring:vi.fn() })
 expect(screen.getAllByText('2026-11-06')).toHaveLength(2)
 expect(screen.getByText(/2026-11-02 至 2026-11-06/)).toBeInTheDocument()
 expect(screen.getByText(/已生成/)).toBeInTheDocument()
 cleanup()
 vi.setSystemTime(new Date('2026-11-09T05:30:00Z'))
 render(JobsPane, { onBring:vi.fn() })
 expect(screen.getByText(/2026-11-09 至 2026-11-13/)).toBeInTheDocument()
 expect(screen.getByText('本周还没有周报')).toBeInTheDocument()
 vi.useRealTimers()
})
