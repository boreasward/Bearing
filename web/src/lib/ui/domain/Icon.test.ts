import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/svelte'
import Icon from './Icon.svelte'
import { ICONS } from './icons'

afterEach(() => cleanup())

describe('Icon', () => {
  it('aria-hidden without a label', () => {
    const { container } = render(Icon, { name: 'bell' })
    const svg = container.querySelector('svg')!
    expect(svg).toHaveAttribute('aria-hidden', 'true')
    expect(svg).not.toHaveAttribute('aria-label')
  })

  it('label makes it an image with a name', () => {
    const { container } = render(Icon, { name: 'bell', label: '通知' })
    const svg = container.querySelector('svg')!
    expect(svg).toHaveAttribute('aria-label', '通知')
    expect(svg).toHaveAttribute('role', 'img')
    expect(svg).not.toHaveAttribute('aria-hidden', 'true')
  })

  it('sizes 16 and 20', () => {
    const small = render(Icon, { name: 'check' })
    expect(small.container.querySelector('svg')!.getAttribute('class')).toContain('size-4')
    cleanup()
    const big = render(Icon, { name: 'check', size: 20 })
    expect(big.container.querySelector('svg')!.getAttribute('class')).toContain('size-5')
  })

  it('every mapped icon renders an svg', () => {
    for (const name of Object.keys(ICONS) as (keyof typeof ICONS)[]) {
      const { container, unmount } = render(Icon, { name })
      expect(container.querySelector('svg'), name).not.toBeNull()
      unmount()
    }
  })

  it('the table has exactly the standard names', () => {
    expect(Object.keys(ICONS).sort()).toEqual([
      'activity', 'bell', 'book-open', 'calendar-days', 'check', 'chevron-down', 'chevron-right', 'circle-x',
      'clipboard-list', 'database', 'external-link', 'info', 'loader', 'map', 'menu', 'microscope', 'panel-left-close',
      'panel-left-open', 'refresh-cw', 'search', 'settings', 'sun-moon', 'toggle-left', 'triangle-alert', 'wrench', 'x',
    ])
  })
})
