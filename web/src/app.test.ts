import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, cleanup, waitFor } from '@testing-library/svelte'
import App from './app.svelte'

beforeEach(() => { location.hash = '' })
afterEach(() => cleanup())

describe('app routing', () => {
  it('renders the home page on an empty hash', async () => {
    const { findByRole } = render(App)
    expect(await findByRole('heading', { name: '投资助手' })).toBeInTheDocument()
    // the four blocks mount; their data fetches fail in jsdom and fall to their empty/error states
    await waitFor(() => expect(findByRole('heading', { name: '今日简报' })).toBeDefined())
  })

  it('switches to the gallery when the hash changes', async () => {
    const { findByRole, findByText } = render(App)
    expect(await findByRole('heading', { name: '投资助手' })).toBeInTheDocument()
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
