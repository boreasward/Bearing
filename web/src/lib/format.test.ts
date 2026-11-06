import { describe, it, expect, beforeAll, vi } from 'vitest'
import { fmtBeijing } from './format'

beforeAll(() => {
  // 尽量把测试环境固定在非北京时区，验证 fmtBeijing 不跟浏览器时区走
  vi.stubEnv('TZ', 'America/New_York')
})

describe('fmtBeijing', () => {
  it('fmtBeijing ignores the browser time zone', () => {
    expect(fmtBeijing('2026-10-26T22:30:00Z')).toBe('2026-10-27 06:30:00 北京')
    expect(fmtBeijing('2026-11-03T01:30:00Z')).toBe('2026-11-03 09:30:00 北京') // 美东冬令时之后
    expect(fmtBeijing(null)).toBe('—')
  })

  it('returns the raw value when it cannot be parsed', () => {
    expect(fmtBeijing('not-a-date')).toBe('not-a-date')
    expect(fmtBeijing('')).toBe('—')
  })
})
