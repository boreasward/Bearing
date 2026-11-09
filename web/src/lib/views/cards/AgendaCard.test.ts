import { afterEach, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/svelte'
import AgendaCard from './AgendaCard.svelte'
import { fixture, meta } from '../test-fixtures'
afterEach(cleanup)
it('uses agenda groups and explains empty groups', () => {
 render(AgendaCard, { data: fixture('get_agenda'), meta })
 for (const title of ['持仓复查','再次上榜','确认队列','研究复看']) expect(screen.getByRole('heading', { name: title })).toBeInTheDocument()
 expect(screen.getByText('无')).toBeInTheDocument()
})
