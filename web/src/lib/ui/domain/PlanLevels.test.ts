import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/svelte'
import PlanLevels from './PlanLevels.svelte'

afterEach(() => cleanup())

const text = (container: HTMLElement) => container.textContent?.replace(/\s+/g, ' ').trim()

// The expected strings below are the v3.2 money format (spec §7): every price carries the `$` prefix with two
// decimals (`$95.10–$97.30` / `$91.40` / `$104.70`, en dash U+2013) and shares stay a plain count (`120 股`);
// PlanHistoryTable / PlanTimeline / RuleBaseline / PlanRowDetail print `入场 L–H · 止损 S · T1 T` and
// ` · 建议 N 股`. They are literals on purpose: no dependency on how those components lay out their rows.
describe('PlanLevels', () => {
  it('renders the same one-line standard', () => {
    const { container } = render(PlanLevels, { low: 95.1, high: 97.3, stop: 91.4, target1: 104.7 })
    expect(text(container)).toBe('入场 $95.10–$97.30 · 止损 $91.40 · T1 $104.70')
  })

  it('adds the suggested shares', () => {
    // PlanRowDetail: ` · 建议 ${shares} 股`
    const { container } = render(PlanLevels, { low: 95.1, high: 97.3, stop: 91.4, target1: 104.7, shares: 120 })
    expect(text(container)).toBe('入场 $95.10–$97.30 · 止损 $91.40 · T1 $104.70 · 建议 120 股')
  })

  it('lead replaces the word before the entry range', () => {
    const { container } = render(PlanLevels, { low: 95.1, high: 97.3, stop: 91.4, target1: 104.7, lead: '区间限价' })
    expect(text(container)).toBe('区间限价 $95.10–$97.30 · 止损 $91.40 · T1 $104.70')
  })

  it('sharesLabel replaces 建议, and an empty one prints just the shares', () => {
    const base = { low: 95.1, high: 97.3, stop: 91.4, target1: 104.7, shares: 120 }
    const a = render(PlanLevels, { ...base, sharesLabel: '约' })
    expect(text(a.container)).toBe('入场 $95.10–$97.30 · 止损 $91.40 · T1 $104.70 · 约 120 股')
    cleanup()
    const b = render(PlanLevels, { ...base, lead: '区间限价', sharesLabel: '' })
    expect(text(b.container)).toBe('区间限价 $95.10–$97.30 · 止损 $91.40 · T1 $104.70 · 120 股')
  })

  it('shares null or absent adds nothing', () => {
    const { container } = render(PlanLevels, { low: 1, high: 2, stop: 0.5, target1: 3, shares: null })
    expect(text(container)).toBe('入场 $1.00–$2.00 · 止损 $0.50 · T1 $3.00')
  })

  it('a missing level says 没算, never a dash', () => {
    const { container } = render(PlanLevels, { low: 95.1, high: 97.3, stop: null, target1: 104.7 })
    expect(text(container)).toBe('入场 $95.10–$97.30 · 止损 没算 · T1 $104.70')
    expect(text(container)).not.toContain('—')
  })

  it('all levels missing is one not-computed badge', () => {
    const { container } = render(PlanLevels, { low: null, high: null, stop: null, target1: null })
    expect(screen.getByText('没算').closest('[data-slot="badge"]')).toHaveAttribute('data-tone', 'warn')
    expect(text(container)).toBe('没算')
  })

  it('values converted from API strings with Number()', () => {
    const levels = { entry_low: '95.10', entry_high: '97.30', stop: '91.40', target1: '104.70' }
    const { container } = render(PlanLevels, {
      low: Number(levels.entry_low), high: Number(levels.entry_high), stop: Number(levels.stop), target1: Number(levels.target1),
    })
    expect(text(container)).toBe('入场 $95.10–$97.30 · 止损 $91.40 · T1 $104.70')
    cleanup()
    const bad = render(PlanLevels, { low: Number('x'), high: 2, stop: 1, target1: 3 })
    expect(text(bad.container)).toBe('入场 没算–$2.00 · 止损 $1.00 · T1 $3.00')
  })
})
