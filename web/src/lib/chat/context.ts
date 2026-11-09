import type { ContextChip } from '$lib/views/context'
export type { ContextChip } from '$lib/views/context'

export function formatPrefix(chips: ContextChip[]): string {
  return chips.map(chip => `[${chip.ticker}${chip.kind === 'ticker' ? '' : ` · ${chip.kind === 'plan' ? '计划' : '研究'} #${chip.id}`}] `).join('')
}

export function parsePrefix(text: string): { chips: ContextChip[]; rest: string } {
  const chips: ContextChip[] = []
  let rest = text
  // Ticker symbols start with a letter; arbitrary bracketed text and numbered lists stay literal.
  const prefix = /^\[([A-Z][A-Z0-9.\-^=]*)(?: · (计划|研究) #([1-9]\d*))?\] /
  for (let match = rest.match(prefix); match; match = rest.match(prefix)) {
    const id = match[3] ? Number(match[3]) : null
    if (id !== null && !Number.isSafeInteger(id)) break
    chips.push({ kind: match[2] === '计划' ? 'plan' : match[2] === '研究' ? 'research' : 'ticker', ticker: match[1], id })
    rest = rest.slice(match[0].length)
  }
  return { chips, rest }
}
