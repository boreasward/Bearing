import { describe, it, expect } from 'vitest'
import { parseHash } from './router'

describe('parseHash', () => {
  it('an empty hash is the home page', () => {
    expect(parseHash('')).toBe('home')
    expect(parseHash('#')).toBe('home')
  })

  it('#_ui is the component gallery, with or without a query', () => {
    expect(parseHash('#_ui')).toBe('gallery')
    expect(parseHash('#_ui?x=1')).toBe('gallery')
  })

  it('any other hash falls back to home', () => {
    expect(parseHash('#nope')).toBe('home')
    expect(parseHash('#_ui/extra')).toBe('home')
  })
})
