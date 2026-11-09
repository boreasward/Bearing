import { afterEach, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/svelte'
import AccountCard from './AccountCard.svelte'
import { fixture, meta } from '../test-fixtures'
afterEach(cleanup)
it('renders get_account with provenance', () => {
 const { container } = render(AccountCard, { data: fixture('get_account'), meta })
 expect(container).toHaveTextContent('现金'); expect(container).toHaveTextContent('call:7')
})
it('explains missing values', () => {
 const { container } = render(AccountCard, { data: {}, meta })
 expect(container).toHaveTextContent('没算')
})
