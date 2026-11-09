import { describe, expect, it } from 'vitest'
import { formatPrefix, parsePrefix } from './context'
import type { ContextChip } from '$lib/views/context'

describe('context prefixes', () => {
  const chips: ContextChip[] = [
    { kind: 'ticker', ticker: 'RKLB', id: null },
    { kind: 'plan', ticker: 'RKLB', id: 12 },
    { kind: 'research', ticker: 'BRK.B', id: 3 },
  ]
  it.each(chips)('round trips $kind', (chip) => {
    expect(parsePrefix(formatPrefix([chip]) + '继续问')).toEqual({ chips: [chip], rest: '继续问' })
  })
  it('uses exact prefixes and handles multiple chips', () => {
    expect(formatPrefix(chips)).toBe('[RKLB] [RKLB · 计划 #12] [BRK.B · 研究 #3] ')
    expect(parsePrefix(formatPrefix(chips) + '问题')).toEqual({ chips, rest: '问题' })
  })
  it('non_chip_brackets_are_left_alone', () => {
    expect(parsePrefix('[1] 第一条')).toEqual({ chips: [], rest: '[1] 第一条' })
    expect(parsePrefix('[RKLB]没有空格')).toEqual({ chips: [], rest: '[RKLB]没有空格' })
    expect(parsePrefix('[RKLB] [随便] 文本')).toEqual({ chips: [chips[0]], rest: '[随便] 文本' })
  })
})
