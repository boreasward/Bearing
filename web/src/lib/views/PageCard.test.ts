import { afterEach, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/svelte'
import { createRawSnippet } from 'svelte'
import PageCard from './PageCard.svelte'
afterEach(cleanup)
const snip = (html: string) => createRawSnippet(() => ({ render: () => html }))
it('renders a labelled region with title, tag, action and body', () => {
  render(PageCard, { title: '成交', tag: '2 条', actions: snip('<button>记成交</button>'), children: snip('<p>成交正文</p>') })
  const region = screen.getByRole('region', { name: '成交' })
  expect(region).toHaveTextContent('2 条')
  expect(region).toHaveTextContent('成交正文')
  expect(region).toContainElement(screen.getByRole('button', { name: '记成交' }))
})
