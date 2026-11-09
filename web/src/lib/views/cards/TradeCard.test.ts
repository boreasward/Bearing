import { afterEach, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/svelte'
import TradeCard from './TradeCard.svelte'
import { fixture, meta } from '../test-fixtures'
afterEach(cleanup)
it('renders record_trade with provenance', () => {
 const { container } = render(TradeCard, { data: fixture('record_trade'), meta })
 expect(container).toHaveTextContent('AAPL'); expect(container).toHaveTextContent('call:7')
})
it('explains missing values', () => {
 const { container } = render(TradeCard, { data: {}, meta })
 expect(container).toHaveTextContent('没算')
})
