import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/svelte'
import Home from './Home.svelte'

afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
})

describe('Home', () => {
  it('is a placeholder that says where the briefing and the conversation will come', () => {
    render(Home)
    expect(screen.getByRole('heading', { name: '投资助手' })).toBeInTheDocument()
    expect(screen.getByText('今日简报与对话在 v3 第 3 步实现')).toBeInTheDocument()
  })

  it('links to the component gallery in dev', () => {
    expect(import.meta.env.DEV).toBe(true)
    render(Home)
    expect(screen.getByRole('link', { name: '组件画廊' })).toHaveAttribute('href', '#_ui')
  })

  it('has no gallery link in a production build, where the gallery does not exist', () => {
    vi.stubEnv('DEV', false)
    render(Home)
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.queryByText('组件画廊')).toBeNull()
  })
})
