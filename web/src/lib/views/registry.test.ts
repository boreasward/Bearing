import { fixtures } from './test-fixtures'
import { afterEach, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/svelte'
import { REGISTRY, viewFor } from './registry'
import GenericCard from './cards/GenericCard.svelte'

const tools = Object.keys(fixtures)
afterEach(cleanup)
it('every_tool_has_an_entry', () => {
  expect(tools).toHaveLength(35)
  expect(Object.keys(REGISTRY)).toHaveLength(35)
  for (const tool of tools) {
    expect(REGISTRY[tool as keyof typeof REGISTRY]).toBeDefined()
    expect(viewFor(tool).component).toBe(REGISTRY[tool as keyof typeof REGISTRY].component)
  }
})
it.each(tools)('every_fixture_renders_a_source_line: %s', tool => {
  const data = fixtures[tool]
  const { container } = render(viewFor(tool).component, { data, meta: { tool, ref: 'call:7', session: '2026-11-06', calledAt: null } })
  expect(container).toHaveTextContent('call:7')
})
it('unknown_tool_falls_back_to_generic', () => expect(viewFor('future_tool').component).toBe(GenericCard))
it('labels every top-level fixture field in Chinese', async () => {
 const { FIELD_LABELS } = await import('./labels')
 for (const data of Object.values(fixtures)) {
  const rows = Array.isArray(data) ? data : [data]
  for (const row of rows) if (row && typeof row === 'object') {
   for (const key of Object.keys(row)) expect(FIELD_LABELS[key], key).toMatch(/[\u3400-\u9fff]|ATR|RS/)
  }
 }
})
