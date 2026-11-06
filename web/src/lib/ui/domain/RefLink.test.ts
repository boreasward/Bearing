import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte'
import RefLink from './RefLink.svelte'

afterEach(() => cleanup())

describe('RefLink', () => {
  it('fact: with a handler is a button that calls back with the key', async () => {
    const onFact = vi.fn()
    render(RefLink, { ref: 'fact:plans', onFact })
    await fireEvent.click(screen.getByRole('button', { name: 'fact:plans' }))
    expect(onFact).toHaveBeenCalledWith('plans')
  })

  it('fact: without a handler is plain text', () => {
    render(RefLink, { ref: 'fact:context' })
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('fact:context')).toBeInTheDocument()
  })

  it('call: with a handler toggles and reports expanded', async () => {
    const onCall = vi.fn()
    const { rerender } = render(RefLink, { ref: 'call:2', onCall, expanded: false })
    const button = screen.getByRole('button', { name: 'call:2' })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    await fireEvent.click(button)
    expect(onCall).toHaveBeenCalledWith('call:2')
    await rerender({ ref: 'call:2', onCall, expanded: true })
    expect(screen.getByRole('button', { name: 'call:2' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('call: without a handler is plain text', () => {
    render(RefLink, { ref: 'call:2' })
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('call:2')).toBeInTheDocument()
  })

  it('only fact: and call: are actions; know: and concept: refs render as plain text', () => {
    render(RefLink, { ref: 'know:3@1' })
    render(RefLink, { ref: 'concept:plan.stop@1' })
    expect(screen.getByText('know:3@1')).toBeInTheDocument()
    expect(screen.getByText('concept:plan.stop@1')).toBeInTheDocument()
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('user: is plain text', () => {
    render(RefLink, { ref: 'user:12' })
    expect(screen.getByText('user:12')).toBeInTheDocument()
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('action refs inherit the font size and stay selectable', () => {
    render(RefLink, { ref: 'fact:plans', onFact: vi.fn() })
    const cls = screen.getByRole('button', { name: 'fact:plans' }).className
    expect(cls).toContain('text-[length:inherit]')
    expect(cls).toContain('select-text')
    expect(cls).not.toContain('text-sm')
    expect(cls).not.toContain('select-none')
  })
})
