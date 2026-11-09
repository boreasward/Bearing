import { afterEach, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/svelte'
import ResearchSnapshotCard from './ResearchSnapshotCard.svelte'
import { fixture, meta } from '../test-fixtures'
afterEach(cleanup)
it('renders get_research with provenance', () => {
 const { container } = render(ResearchSnapshotCard, { data: fixture('get_research'), meta })
 expect(container).toHaveTextContent('MU'); expect(container).toHaveTextContent('call:7')
})
it('explains missing values', () => {
 const { container } = render(ResearchSnapshotCard, { data: {}, meta })
 expect(container).toHaveTextContent('没算')
})
