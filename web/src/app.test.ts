import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/svelte'
import App from './app.svelte'

beforeEach(() => { location.hash = '' })
afterEach(() => cleanup())

describe('app routing', () => {
  it('renders the placeholder home on an empty hash', () => {
    const { getByText } = render(App)
    expect(getByText('今日简报与对话在 v3 第 3 步实现')).toBeInTheDocument()
  })

  it('switches to the gallery when the hash changes', async () => {
    const { getByText, findByText } = render(App)
    expect(getByText('今日简报与对话在 v3 第 3 步实现')).toBeInTheDocument()
    location.hash = '#_ui'
    window.dispatchEvent(new HashChangeEvent('hashchange'))
    expect(await findByText('开发模式组件画廊（不进生产包）')).toBeInTheDocument()
  })

  it('#_ui renders the gallery in dev', async () => {
    expect(import.meta.env.DEV).toBe(true)
    location.hash = '#_ui'
    const { findByText } = render(App)
    expect(await findByText('开发模式组件画廊（不进生产包）')).toBeInTheDocument()
  })
})
