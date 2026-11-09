import { afterEach, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/svelte'
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
it.each(['save_summary', 'save_verdict', 'save_holding_review'])('does not claim persistence when %s fails', tool => {
 const { container } = render(NoteCard, { data:{ error:'invalid_argument' }, meta:{ ...meta, tool } })
 expect(container.querySelector('[data-state="error"]')).toHaveTextContent('参数不正确')
 expect(container).toHaveTextContent(`${tool} · call:7 · 2026-11-06`)
 expect(screen.queryByText('已写入')).toBeNull()
 expect(container.querySelector('[data-domain="write"]')).toBeNull()
})
it.each([
 ['written', '已写入'],
 ['preview', '待确认'],
 ['replayed', '重放'],
] as const)('preserves successful %s feedback and suppresses it on errors', (write, label) => {
 const rendered = render(NoteCard, { data:fixture('save_summary'), meta:{ ...meta, write } })
 expect(screen.getByText(label)).toBeInTheDocument()
 rendered.unmount()
 const { container } = render(NoteCard, { data:{ error:'invalid_argument' }, meta:{ ...meta, write } })
 expect(container.querySelector('[data-state="error"]')).toHaveTextContent('参数不正确')
 expect(container.querySelector('[data-domain="write"]')).toBeNull()
})
it('preserves the unconfirmed note preview', () => {
 const { container } = render(NoteCard, { data:{ write:false, preview:'确认后保存总结' }, meta })
 expect(screen.getByText('待确认')).toBeInTheDocument()
 expect(container).toHaveTextContent('确认后保存总结')
 expect(screen.queryByText('已写入')).toBeNull()
})
