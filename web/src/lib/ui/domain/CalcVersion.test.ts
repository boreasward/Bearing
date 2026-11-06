import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/svelte'
import CalcVersion from './CalcVersion.svelte'

afterEach(() => cleanup())

describe('CalcVersion', () => {
  it('shows 口径 and the version as code', () => {
    const { container } = render(CalcVersion, { version: 'review-8' })
    expect(container.textContent?.replace(/\s+/g, ' ').trim()).toBe('口径 review-8')
    const code = screen.getByText('review-8')
    expect(code.tagName).toBe('CODE')
    expect(code.className).toContain('font-mono')
    expect(screen.queryByRole('link')).toBeNull()
  })
})
