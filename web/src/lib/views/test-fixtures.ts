const files = import.meta.glob('../../../../tests/fixtures/tool_results/*.json', { eager: true, import: 'default' })
export const fixtures: Record<string, unknown> = Object.fromEntries(
  Object.entries(files).map(([path, data]) => [path.split('/').at(-1)!.replace('.json', ''), data]),
)
export const fixture = (tool: string): unknown => fixtures[tool]
export const meta = { tool: 'test_tool', ref: 'call:7', session: '2026-11-06', calledAt: null }
