import { afterEach, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/svelte'
import BarsTable from './BarsTable.svelte'
import { fixture, meta } from '../test-fixtures'
afterEach(cleanup)
it('keeps OHLCV order and right aligns numeric columns', () => {
 render(BarsTable, { data: fixture('get_price_bars'), meta })
 expect(screen.getAllByRole('columnheader').map(h => h.textContent)).toEqual(['日期','开','高','低','收','量'])
 expect(screen.getAllByRole('cell')[1]).toHaveClass('text-right')
})
