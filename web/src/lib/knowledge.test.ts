import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import { ATTACH, conceptIndex, derivationStepConcept, familyConcept, resetConceptIndex, ruleConcept, TOOL_TEXT }
  from './knowledge'
import type { ConceptsView } from './knowledge'

const view = (): ConceptsView => ({
  knowledge_version: 'knowledge-1',
  groups: [{ id: 'plan', title: '规则计划 `plan-1`', count: 1 }],
  concepts: [{ id: 'plan.stop', ref: 'concept:plan.stop@1', title: '止损', group: 'plan', version: 1,
               applies_to: ['plan-1'], summary: '参考价减 2 倍 ATR14。', related: [] }],
})

beforeEach(() => resetConceptIndex())
afterEach(() => vi.restoreAllMocks())

describe('conceptIndex', () => {
  it('requests once and caches', async () => {
    const spy = vi.spyOn(api, 'getConcepts').mockResolvedValue(view())
    const [a, b] = await Promise.all([conceptIndex(), conceptIndex()])
    const c = await conceptIndex()
    expect(spy).toHaveBeenCalledTimes(1)
    expect(a).toBe(b)
    expect(c.get('plan.stop')!.title).toBe('止损')
  })

  it('retries after failure', async () => {
    const spy = vi.spyOn(api, 'getConcepts')
      .mockRejectedValueOnce(new Error('HTTP 503'))
      .mockResolvedValueOnce(view())
    await expect(conceptIndex()).rejects.toThrow('HTTP 503')
    const index = await conceptIndex()
    expect(spy).toHaveBeenCalledTimes(2)
    expect(index.has('plan.stop')).toBe(true)
  })

  it('rejects a response that is not a concept list instead of caching it', async () => {
    const spy = vi.spyOn(api, 'getConcepts')
      .mockResolvedValueOnce({ session: 'x' } as any)
      .mockResolvedValueOnce(view())
    await expect(conceptIndex()).rejects.toThrow()
    expect((await conceptIndex()).size).toBe(1)
    expect(spy).toHaveBeenCalledTimes(2)
  })
})

describe('derivationStepConcept', () => {
  it('maps every rule in spec 6.2', () => {
    const table: [string, string | null][] = [
      ['tr_3', 'indicator.atr14'], ['tr_14', 'indicator.atr14'], ['atr', 'indicator.atr14'],
      ['ref_price', 'plan.ref_price'], ['entry_low', 'plan.entry_zone'], ['entry_high', 'plan.entry_zone'],
      ['stop', 'plan.stop'], ['target1', 'plan.target1'], ['trail_atr_multiple', 'plan.trailing_stop'],
      ['valid_from', 'plan.validity'], ['valid_until', 'plan.validity'],
      ['earnings', 'plan.earnings'], ['earnings_note', 'plan.earnings'], ['earnings_date', 'plan.earnings'],
      ['context_flags', 'plan.context_flags'],
      ['per_share_risk', 'plan.sizing'], ['risk_amount', 'plan.sizing'], ['equity', 'plan.sizing'],
      ['ticker_value', 'plan.sizing'], ['total_value', 'plan.sizing'], ['cap_single', 'plan.sizing'],
      ['cap_total', 'plan.sizing'], ['shares', 'plan.sizing'], ['applied_cap', 'plan.sizing'],
      ['sizing.status', 'plan.sizing'],
      ['invalid_reasons', 'plan.status'], ['warnings', 'plan.status'], ['status', 'plan.status'],
      ['levels_ordered', 'plan.status'],
      ['something_else', null], ['', null],
    ]
    for (const [key, expected] of table) expect([key, derivationStepConcept(key)]).toEqual([key, expected])
  })
})

describe('ATTACH', () => {
  it('holds only well-formed concept ids and follows the spec 6.2 rows', () => {
    const ids = Object.values(ATTACH).flatMap(group => Object.values(group))
    for (const id of ids) expect(id).toMatch(/^(family|indicator|rule|plan|agent|review|system)\.[a-z0-9_]+$/)
    expect(ATTACH.data.evidence).toBe('system.gate_hold')
    expect(ATTACH.data.atr).toBe('indicator.atr14')
    expect(ATTACH.data.BAMLH0A0HYM2).toBe('indicator.hy_oas')
    expect(ATTACH.data.T10Y2Y).toBe('indicator.t10y2y')
    expect(ATTACH.derivation.consistency).toBe('plan.derivation')
    expect(ATTACH.derivation.run).toBe('system.stages')
    expect(ATTACH.agentCard.version).toBe('agent.version_chain')
    expect(ATTACH.agentCard.cutoff).toBe('system.cutoff')
    expect(ATTACH.agentCard.noAdd).toBe('plan.sizing')
    expect(ATTACH.ruleBaseline.status).toBe('plan.status')
    expect(ATTACH.timeline.shadow).toBe('review.plan_shadow')
    expect(ATTACH.today.basis).toBe('review.execution_status')       // v2.9 R20
    expect(ATTACH.overview.stages).toBe('system.stages')
    expect(ATTACH.overview.status).toBe('system.stage_status')
  })

  it('ATTACH has retroCard, myKnowledge, discussion, weeklyLearning', () => {
    expect(ATTACH.retroCard).toEqual({
      card: 'review.retro_card', end: 'review.retro_end', tracks: 'review.tracks',
      chase: 'review.discipline_chase', earlyTake: 'review.discipline_early_take',
      heldLoser: 'review.discipline_held_loser', unplanned: 'review.discipline_unplanned',
    })
    expect(ATTACH.myKnowledge).toEqual({
      item: 'system.my_knowledge', performance: 'review.knowledge_performance', citation: 'agent.knowledge_citation',
    })
    expect(ATTACH.discussion).toEqual({ about: 'system.discussion' })
    expect(ATTACH.weeklyLearning).toEqual({ learning: 'review.weekly_learning' })
  })

  it('builds the dynamic family and rule ids', () => {
    expect(familyConcept('sec_filings')).toBe('family.sec_filings')
    expect(familyConcept('atr')).toBe('indicator.atr14')      // no family.atr card: ATR is an indicator
    expect(ruleConcept('trend_reclaim')).toBe('rule.trend_reclaim')
  })

  it('has a Chinese name for every tool', () => {
    for (const tool of ['get_trigger', 'get_price_bars', 'get_indicators', 'get_filings', 'get_news',
                        'get_earnings', 'get_market_context', 'get_position', 'get_concept',
                        'get_knowledge', 'get_plan_context', 'get_derivation', 'get_agent_run'])
      expect(TOOL_TEXT[tool]).toBeTruthy()
  })
})
