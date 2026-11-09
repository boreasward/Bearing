import { afterEach, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/svelte'
import LevelsCard from './LevelsCard.svelte'
import { fixture, meta } from '../test-fixtures'
afterEach(cleanup)
it('renders compute_plan_levels with provenance', () => {
 const { container } = render(LevelsCard, { data: fixture('compute_plan_levels'), meta })
 expect(container).toHaveTextContent('ATR'); expect(container).toHaveTextContent('call:7')
})
it('explains missing values', () => {
 const { container } = render(LevelsCard, { data: {}, meta })
 expect(container).toHaveTextContent('没算')
})
