import { afterEach, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/svelte'
import NoteCard from './NoteCard.svelte'
import { fixture, meta } from '../test-fixtures'
afterEach(cleanup)
it('renders save_summary with provenance', () => {
 const { container } = render(NoteCard, { data: fixture('save_summary'), meta })
 expect(container).toHaveTextContent('已有持仓'); expect(container).toHaveTextContent('call:7')
})
it('explains missing values', () => {
 const { container } = render(NoteCard, { data: {}, meta })
 expect(container).toHaveTextContent('没算')
})
