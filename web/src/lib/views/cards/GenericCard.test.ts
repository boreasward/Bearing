import { afterEach, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte'
import GenericCard from './GenericCard.svelte'
import { meta } from '../test-fixtures'
afterEach(cleanup)
it('labels a flat dictionary in two columns', () => {
 const { container } = render(GenericCard, { data: { ticker: 'MU', last_close: null }, meta })
 expect(screen.getByText('股票')).toBeInTheDocument()
 expect(container.querySelector('dl')).toHaveTextContent('MU')
 expect(screen.getByText('没算')).toBeInTheDocument()
})
it('unions dictionary columns and limits them to eight', () => {
 render(GenericCard, { data: [{ a: 1, b: 2, c: 3, d: 4, e: 5, f: 6, g: 7, h: 8 }, { i: 9, j: 10 }], meta })
 expect(screen.getAllByRole('columnheader')).toHaveLength(8)
 expect(screen.getByText('还有 2 列')).toBeInTheDocument()
})
it('expands JSON beyond forty lines', async () => {
 render(GenericCard, { data: { nested: Array.from({ length: 50 }, (_, n) => n) }, meta })
 expect(screen.getByRole('button', { name: '展开' })).toBeInTheDocument()
 expect(document.querySelector('pre')!.textContent!.split('\n').length).toBe(40)
 await fireEvent.click(screen.getByRole('button', { name: '展开' }))
 expect(document.querySelector('pre')!.textContent!.split('\n').length).toBeGreaterThan(40)
})
it('shows tool errors and empty reasons with provenance', () => {
 const { container } = render(GenericCard, { data: { error: 'no_run' }, meta })
 expect(container.querySelector('[data-state="error"]')).toHaveTextContent('还没有运行记录')
 expect(container).toHaveTextContent('call:7')
})
it('explains a successful null result as empty instead of rendering null', () => {
 const { container } = render(GenericCard, { data:null, meta })
 expect(container.querySelector('[data-state="empty"]')).toHaveTextContent('这次查询没有返回记录')
 expect(container.querySelector('pre')).toBeNull()
})
