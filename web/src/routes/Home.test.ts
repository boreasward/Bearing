import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/svelte'
import Home from './Home.svelte'

afterEach(() => cleanup())

describe('Home', () => {
  it('is a placeholder that says where the briefing and the conversation will come', () => {
    render(Home)
    expect(screen.getByRole('heading', { name: '投资助手' })).toBeInTheDocument()
    expect(screen.getByText('今日简报与对话在 v3 第 3 步实现')).toBeInTheDocument()
  })

  it('links to the component gallery', () => {
    render(Home)
    expect(screen.getByRole('link', { name: '组件画廊' })).toHaveAttribute('href', '#_ui')
  })
})
