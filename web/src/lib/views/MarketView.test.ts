import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/svelte'
import MarketView from './MarketView.svelte'
import { loadWorkbench } from './workbench-fixtures'
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('renders MarketCard full from shared store', async () => {
 await loadWorkbench(); render(MarketView)
 expect(screen.getByText('宏观')).toBeInTheDocument(); expect(screen.getByText('关注的标的')).toBeInTheDocument()
})
