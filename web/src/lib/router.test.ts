import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/svelte'
import SideNav from './components/SideNav.svelte'
import { parseHash, workbenchZones, zones } from './router'

describe('parseHash', () => {
  it('places accounts between instruments and review in the main navigation', () => {
    const { container } = render(SideNav, { current: 'today' })
    const labels = [...container.querySelectorAll('[data-nav-item]')].map(el => (el.getAttribute('aria-label') ?? el.textContent ?? '').trim())
    expect(labels.slice(0, 9)).toEqual(['今日', '标的', '账户', '研究', '复盘', '数据', '知识', '路线', '设置'])
  })
  it('parses research and roadmap nested paths', () => {
    expect(parseHash('#research/request-id/2')).toEqual({ zone: 'research', sub: 'request-id/2' })
    expect(parseHash('#roadmap/versions/v2.15')).toEqual({ zone: 'roadmap', sub: 'versions/v2.15' })
    expect(parseHash('#roadmap/proposals/12')).toEqual({ zone: 'roadmap', sub: 'proposals/12' })
    expect(parseHash('#settings/features')).toEqual({ zone: 'settings', sub: 'features' })
  })
  it.each([
    ['#account', undefined], ['#account/00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001'],
    ['#account/00000000-0000-0000-0000-000000000001/trades', '00000000-0000-0000-0000-000000000001/trades'],
    ['#account/compare', 'compare'],
  ])('routes %s to accounts with the full nested path', (hash, sub) => {
    expect(parseHash(hash)).toEqual({ zone: 'account', sub })
  })
  it('redirects both retired review pages to the real account', () => {
    expect(parseHash('#review/trades')).toEqual({ zone: 'account', sub: '00000000-0000-0000-0000-000000000001/trades' })
    expect(parseHash('#review/account')).toEqual({ zone: 'account', sub: '00000000-0000-0000-0000-000000000001' })
  })
  it('parses the workbench zones with subs', () => {
    expect(parseHash('#instrument/NVDA')).toEqual({ zone: 'instrument', sub: 'NVDA' })
    expect(parseHash('#review/weekly')).toEqual({ zone: 'review', sub: 'weekly' })
    expect(parseHash('#tools/filings')).toEqual({ zone: 'tools', sub: 'filings' })
  })
  it('falls back to today for unknown zone', () => {
    expect(parseHash('#nope')).toEqual({ zone: 'today', sub: undefined })
  })
  it('routes the data zone; other legacy zones still fall back to today', () => {
    expect(parseHash('#data')).toEqual({ zone: 'data', sub: undefined })
    expect(parseHash('#data/signals')).toEqual({ zone: 'data', sub: 'signals' })
    for (const hash of ['#technicals/MU', '#strategy/scores', '#trade/macro']) {
      expect(parseHash(hash)).toEqual({ zone: 'today', sub: undefined })
    }
  })
  it('query parameters never become part of route sub', () => {
    expect(parseHash('#tools/runs?task=filings')).toEqual({ zone: 'tools', sub: 'runs' })
  })
  it('routes #plan/<id> to the plan zone; #plan without an id falls back to today', () => {
    expect(zones).toContain('plan')
    expect(workbenchZones).not.toContain('plan')   // 独立页，不属于工作台分区
    expect(parseHash('#plan/abc')).toEqual({ zone: 'plan', sub: 'abc' })
    expect(parseHash('#plan')).toEqual({ zone: 'today', sub: undefined })
  })
  it('keeps the plan zone out of the side nav', () => {
    render(SideNav, { current: 'plan', sub: 'abc' })
    expect(screen.queryByText('计划')).toBeNull()
    const hrefs = screen.getAllByRole('link').map(a => a.getAttribute('href'))
    expect(hrefs.every(href => !href!.startsWith('#plan'))).toBe(true)
  })
  it('#knowledge/plan.stop@1 parses to knowledge zone with sub', () => {
    expect(zones).toContain('knowledge')
    expect(workbenchZones).not.toContain('knowledge')
    expect(parseHash('#knowledge/plan.stop@1')).toEqual({ zone: 'knowledge', sub: 'plan.stop@1' })
    expect(parseHash('#knowledge/principles')).toEqual({ zone: 'knowledge', sub: 'principles' })
    expect(parseHash('#knowledge')).toEqual({ zone: 'knowledge', sub: undefined })
  })
  it('#review/cards/12 keeps the card id in sub', () => {
    expect(parseHash('#review/cards')).toEqual({ zone: 'review', sub: 'cards' })
    expect(parseHash('#review/cards/12')).toEqual({ zone: 'review', sub: 'cards/12' })
    expect(parseHash('#review/cards/12?x=1')).toEqual({ zone: 'review', sub: 'cards/12' })
  })
  it('#knowledge/mine/3@2, #knowledge/mine/drafts and #knowledge/mine/new', () => {
    expect(parseHash('#knowledge/mine')).toEqual({ zone: 'knowledge', sub: 'mine' })
    expect(parseHash('#knowledge/mine/3@2')).toEqual({ zone: 'knowledge', sub: 'mine/3@2' })
    expect(parseHash('#knowledge/mine/3')).toEqual({ zone: 'knowledge', sub: 'mine/3' })
    expect(parseHash('#knowledge/mine/drafts')).toEqual({ zone: 'knowledge', sub: 'mine/drafts' })
    expect(parseHash('#knowledge/mine/new?ticker=NVDA')).toEqual({ zone: 'knowledge', sub: 'mine/new' })
  })
  it('#discuss/5 is its own zone, out of the side nav; #discuss alone falls back to today', () => {
    expect(zones).toContain('discuss')
    expect(workbenchZones).not.toContain('discuss')
    expect(parseHash('#discuss/5')).toEqual({ zone: 'discuss', sub: '5' })
    expect(parseHash('#discuss')).toEqual({ zone: 'today', sub: undefined })
    // a non-numeric id would become /discussions/NaN: treated like no id at all
    expect(parseHash('#discuss/abc')).toEqual({ zone: 'today', sub: undefined })
    expect(parseHash('#discuss/5x')).toEqual({ zone: 'today', sub: undefined })
    expect(parseHash('#discuss/-1')).toEqual({ zone: 'today', sub: undefined })
    render(SideNav, { current: 'discuss', sub: '5' })
    const hrefs = screen.getAllByRole('link').map(a => a.getAttribute('href'))
    expect(hrefs.every(href => !href!.startsWith('#discuss'))).toBe(true)
  })
  it('other zones still keep only their first segment', () => {
    expect(parseHash('#instrument/NVDA/extra')).toEqual({ zone: 'instrument', sub: 'NVDA' })
    expect(parseHash('#plan/abc/extra')).toEqual({ zone: 'plan', sub: 'abc' })
  })
  it('#tools/runs/<run_id> is the run detail page; an id of another shape falls back to the run list', () => {
    expect(parseHash('#tools/runs/daily-20261030033000-1a2b3c4d'))
      .toEqual({ zone: 'tools', sub: 'runs/daily-20261030033000-1a2b3c4d' })
    expect(parseHash('#tools/runs/daily-20261030033000-1a2b3c4d?x=1'))
      .toEqual({ zone: 'tools', sub: 'runs/daily-20261030033000-1a2b3c4d' })
    for (const hash of ['#tools/runs/abc', '#tools/runs/', '#tools/runs/daily-20261030033000-1A2B3C4D', '#tools/runs/../settings']) {
      expect(parseHash(hash)).toEqual({ zone: 'tools', sub: 'runs' })
    }
    expect(parseHash('#tools/runs')).toEqual({ zone: 'tools', sub: 'runs' })
    expect(parseHash('#tools/filings/extra')).toEqual({ zone: 'tools', sub: 'filings' })
  })
})
