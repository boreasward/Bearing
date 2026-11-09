import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/svelte'
import JobsView from './JobsView.svelte'
import { loadWorkbench } from './workbench-fixtures'
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('shows schedule, twenty runs, usage expansion and brief links', async () => {
 await loadWorkbench(); render(JobsView)
 expect(screen.getByText('周二、周三、周四、周五、周六')).toBeInTheDocument()
 expect(screen.getByText('11:30')).toBeInTheDocument(); expect(screen.getByText('Asia/Shanghai')).toBeInTheDocument()
 const table = screen.getByRole('table', { name:'运行记录' })
 expect(within(table).getAllByRole('row')).toHaveLength(21)
 expect(screen.getAllByRole('link', { name:'打开当天简报' })[0]).toHaveAttribute('href','#today?session=2026-10-31')
 await fireEvent.click(within(table).getAllByRole('button', { name:'展开详情' })[0])
 expect(screen.getByText(/"input": 123/)).toBeInTheDocument(); expect(screen.getByText(/test-model/)).toBeInTheDocument()
})
it('expands two runs with the same session separately', async () => {
 const { runsData } = await import('./workbench-fixtures')
 await loadWorkbench({ '/api/runs':{ ...runsData, runs:[runsData.runs[0], { ...runsData.runs[0], started_at:'2026-11-07T04:30:00Z' }] } })
 render(JobsView)
 const toggles = screen.getAllByRole('button', { name:'展开详情' })
 await fireEvent.click(toggles[1])
 expect(toggles[0]).toHaveAttribute('aria-expanded','false'); expect(toggles[1]).toHaveAttribute('aria-expanded','true')
})
