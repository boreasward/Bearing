import { describe, it, expect } from 'vitest'
import { parseHash, hrefOf, type Route } from './router'

const cases: [string, Route][] = [
  ['#', { kind: 'chat', chatId: null }],
  ['#chat/chat-123', { kind: 'chat', chatId: 'chat-123' }],
  ...(['today', 'account', 'market', 'plans', 'research', 'retrospect', 'jobs'] as const)
    .map(view => [`#${view}`, { kind: 'view', view, id: null }] as [string, Route]),
  ['#plans/12', { kind: 'view', view: 'plans', id: 12 }],
  ['#research/34', { kind: 'view', view: 'research', id: 34 }],
  ['#_ui', { kind: 'gallery' }],
]

describe('workbench routes', () => {
  it.each(cases)('parses %s and round trips', (hash, route) => {
    expect(parseHash(hash)).toEqual(route)
    expect(hrefOf(route)).toBe(hash)
    expect(parseHash(hrefOf(route))).toEqual(route)
  })
  it.each(['', '#unknown', '#account/1', '#plans/no', '#plans/-1', '#plans/0', '#plans/1/extra', '#chat/', '#chat/a/b', '#chat/%ZZ'])('falls back for %s', hash => {
    expect(parseHash(hash)).toEqual({ kind: 'chat', chatId: null })
  })
  it('preserves query compatibility and encodes chat identifiers', () => {
    expect(parseHash('#_ui?x=1')).toEqual({ kind: 'gallery' })
    const route: Route = { kind: 'chat', chatId: '对话 #1' }
    expect(parseHash(hrefOf(route))).toEqual(route)
  })
})
