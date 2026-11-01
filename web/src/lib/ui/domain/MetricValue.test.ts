import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/svelte'
import MetricValue from './MetricValue.svelte'

afterEach(() => cleanup())

describe('MetricValue', () => {
  it('keeps explicit raw Decimal text without rounding', () => {
    const { container } = render(MetricValue, { value: null, raw: '100000.0100', unit: 'USD' })
    expect(container.textContent?.trim()).toBe('100000.0100 USD')
    expect(screen.queryByText('没算')).toBeNull()
  })
  it('null shows 没算', () => {
    render(MetricValue, { value: null })
    expect(screen.getByText('没算')).toBeInTheDocument()
    cleanup()
    render(MetricValue, { value: undefined })
    expect(screen.getByText('没算')).toBeInTheDocument()
    cleanup()
    const { container } = render(MetricValue, { value: Number.NaN })
    expect(container.textContent).toContain('没算')
    expect(container.textContent).not.toContain('—')
  })

  it('unit spacing', () => {
    const { container } = render(MetricValue, { value: 12, unit: '股', digits: 0 })
    expect(container.textContent?.trim()).toBe('12 股')
    cleanup()
    const plain = render(MetricValue, { value: 3.14159 })
    expect(plain.container.textContent?.trim()).toBe('3.14')
  })

  it('updown colors', () => {
    const up = render(MetricValue, { value: 1.5, tone: 'updown' })
    expect(up.container.querySelector('.text-up')).not.toBeNull()
    expect(up.container.querySelector('.text-down')).toBeNull()
    cleanup()
    const down = render(MetricValue, { value: -1.5, tone: 'updown' })
    expect(down.container.querySelector('.text-down')).not.toBeNull()
    cleanup()
    const zero = render(MetricValue, { value: 0, tone: 'updown' })
    expect(zero.container.querySelector('.text-up, .text-down')).toBeNull()
    cleanup()
    const none = render(MetricValue, { value: 1.5 })
    expect(none.container.querySelector('.text-up, .text-down')).toBeNull()
  })

  it('percent and signed', () => {
    const neg = render(MetricValue, { value: -0.032, percent: true, digits: 1 })
    expect(neg.container.textContent?.trim()).toBe('-3.2%')
    cleanup()
    const pos = render(MetricValue, { value: 0.0125, percent: true, signed: true })
    expect(pos.container.textContent?.trim()).toBe('+1.25%')
    cleanup()
    const signedNum = render(MetricValue, { value: 4, signed: true, digits: 0, unit: '股' })
    expect(signedNum.container.textContent?.trim()).toBe('+4 股')
    cleanup()
    const zero = render(MetricValue, { value: 0, signed: true, digits: 0 })
    expect(zero.container.textContent?.trim()).toBe('0')
  })

  it('uses tabular numerals', () => {
    const { container } = render(MetricValue, { value: 5 })
    expect(container.querySelector('.tabular-nums')).not.toBeNull()
  })
})
