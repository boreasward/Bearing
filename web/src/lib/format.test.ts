import { describe, expect, it, beforeAll, vi } from 'vitest'
import { fmtBeijing, moneyText, nowInNewYork, newYorkIso } from './format'

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

describe('nowInNewYork', () => {
  it('formats the current New York date and time, not the browser zone', () => {
    expect(nowInNewYork(new Date('2026-07-15T14:30:00Z'))).toEqual({ date: '2026-07-15', time: '10:30' })
    // UTC 后半夜在美东是前一天
    expect(nowInNewYork(new Date('2026-12-15T02:30:00Z'))).toEqual({ date: '2026-12-14', time: '21:30' })
  })
})

describe('newYorkIso', () => {
  it('assembles an ISO timestamp with the New York offset of that date', () => {
    expect(newYorkIso('2026-07-15', '10:30')).toBe('2026-07-15T10:30:00-04:00') // 夏令时
    expect(newYorkIso('2026-12-15', '10:30')).toBe('2026-12-15T10:30:00-05:00') // 冬令时
  })

  it('picks the offset by the date across the DST switch days', () => {
    expect(newYorkIso('2026-03-08', '10:30')).toBe('2026-03-08T10:30:00-04:00') // 3 月第二个周日起夏令时
    expect(newYorkIso('2026-10-31', '10:30')).toBe('2026-10-31T10:30:00-04:00') // 切换前一天仍是夏令时
    expect(newYorkIso('2026-11-01', '10:30')).toBe('2026-11-01T10:30:00-05:00') // 11 月第一个周日起冬令时
  })

  it('resolves the offset for the supplied time, not just the date (pre-02:00 on switch days)', () => {
    // 切换发生在凌晨 2 点：切换日 2 点前的墙钟还在旧偏移里
    expect(newYorkIso('2026-03-08', '01:30')).toBe('2026-03-08T01:30:00-05:00') // 跳表前仍是冬令时
    expect(newYorkIso('2026-11-01', '00:30')).toBe('2026-11-01T00:30:00-04:00') // 回拨前仍是夏令时
  })

  it('does not invent an offset for malformed input', () => {
    expect(newYorkIso('not-a-date', '10:30')).toBe('not-a-dateT10:30')
    expect(newYorkIso('2026-07-15', '')).toBe('2026-07-15T')
  })
})

describe('moneyText', () => {
  it('renders two decimals with the dollar prefix', () => {
    expect(moneyText('1234.5')).toBe('$1234.50')
    expect(moneyText('-12.3')).toBe('-$12.30')
    expect(moneyText(98)).toBe('$98.00')
    expect(moneyText('98.000000')).toBe('$98.00')
    expect(moneyText('123.456')).toBe('$123.46') // 四舍五入到分
    expect(moneyText('-0.001')).toBe('$0.00') // 不出现 -$0.00
  })

  it('empty is empty and unparseable text stays as it was', () => {
    expect(moneyText(null)).toBe('')
    expect(moneyText(undefined)).toBe('')
    expect(moneyText('')).toBe('')
    expect(moneyText('n/a')).toBe('n/a')
  })
})
