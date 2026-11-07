import { afterEach, describe, expect, it, vi } from 'vitest'
import { LABELS, TONES, statusOf, type StatusDomain } from './status'
import statusSource from './status.ts?raw'

afterEach(() => vi.unstubAllEnvs())

const domains = Object.keys(TONES) as StatusDomain[]

describe('status vocabulary', () => {
  it('knows new request, proposal, verdict and flag states with legal tones', () => {
    const expected = {
      research_request: ['queued', 'running', 'waiting_data', 'concluded', 'closed', 'error'],
      proposal: ['proposed', 'approved', 'rejected', 'superseded'],
      portfolio_verdict: ['ok', 'blocked', 'deferred'], data_request: ['queued', 'fetched', 'failed', 'deferred'],
      feature_flag: ['enabled', 'disabled'],
      roadmap_version: ['planned', 'in_progress', 'engineering_done', 'user_acceptance_pending', 'done', 'dropped'],
    }
    for (const [domain, keys] of Object.entries(expected)) for (const key of keys) {
      const info = statusOf(domain as StatusDomain, key)
      expect(info.known).toBe(true)
      expect(info.label).not.toBe(key)
      expect(['success', 'warn', 'danger', 'info', 'running', 'neutral']).toContain(info.tone)
    }
  })
  it('every TONES key has a label', () => {
    for (const domain of domains) {
      for (const key of Object.keys(TONES[domain])) {
        expect(LABELS[domain][key], `${domain}.${key}`).toBeTruthy()
      }
    }
  })

  it('every domain of the type is in TONES and LABELS', () => {
    expect(domains.sort()).toEqual([
      'account', 'agent', 'block', 'card', 'chain', 'connection', 'consistency', 'data_request', 'execution', 'feature_flag', 'fetch', 'instruction',
      'knowledge', 'node', 'paper_decision', 'paper_order', 'plan', 'plan_action', 'portfolio_verdict', 'proposal', 'push', 'reconcile', 'research_request', 'retro', 'roadmap_version', 'run', 'stop_order', 'system',
    ])
    expect(Object.keys(LABELS).sort()).toEqual(domains)
  })

  it('agent pass is the agent declining to buy (agent/graph.py), not 维持', () => {
    expect(LABELS.agent.pass).toBe('不建议买入')
  })

  it('execution carries its adopted sentence with placeholders and the unplanned key', () => {
    expect(LABELS.execution.adopted).toBe('已采纳（{date} 成交 {quantity} 股）')
    expect(LABELS.execution.unplanned).toBe('计划外成交')
  })

  it('node carries the run overview stage wording', () => {
    expect(LABELS.node.missing).toBe('缺证据')
    expect(LABELS.node.not_recorded).toBe('未记录')
    expect(TONES.node.missing).toBe('warn')
    expect(TONES.node.not_recorded).toBe('neutral')
  })

  it('instruction: exit is danger and sell_half warns', () => {
    expect(TONES.instruction.exit).toBe('danger')
    expect(TONES.instruction.sell_half).toBe('warn')
  })

  it('plan states: pending/open live, every ended state neutral (v3.1 spec §6.2)', () => {
    expect(LABELS.plan).toEqual({
      pending: '待入场', open: '已买入', closed: '已平仓', expired: '已过期', invalidated: '已作废', replaced: '被替换', cancelled: '已取消',
    })
    expect(TONES.plan.pending).toBe('info')
    expect(TONES.plan.open).toBe('success')
    for (const key of ['closed', 'expired', 'invalidated', 'replaced', 'cancelled'] as const) {
      expect(TONES.plan[key], key).toBe('neutral')
    }
  })

  it('plan_action: the two alert kinds warn, the three routine kinds stay regular (spec §7.4)', () => {
    expect(LABELS.plan_action.stop_hit_unresolved).toBe('止损已触及，未见卖出记录')
    expect(LABELS.plan_action.sell_next_open).toBe('下次开盘卖出')
    expect(LABELS.plan_action.unchecked).toBe('缺日线，无法检查止损')
    expect(LABELS.plan_action.stop_order).toBe('未确认止损单')
    expect(LABELS.plan_action.hold).toBe('继续持有')
    expect(TONES.plan_action).toEqual({
      stop_hit_unresolved: 'danger', sell_next_open: 'warn', unchecked: 'info', stop_order: 'info', hold: 'neutral',
    })
  })

  it('stop_order states: none neutral, needs_raise warn, confirmed success', () => {
    expect(TONES.stop_order).toEqual({ none: 'neutral', needs_raise: 'warn', confirmed: 'success' })
    expect(LABELS.stop_order.needs_raise).toBe('止损单需上调')
  })

  it('consistency tones: consistent success, mismatch danger, incomplete warn', () => {
    expect(TONES.consistency).toEqual({ consistent: 'success', mismatch: 'danger', incomplete: 'warn' })
  })

  it('LABELS and TONES list exactly the same keys in every domain', () => {
    for (const domain of domains) {
      expect(Object.keys(LABELS[domain]).sort(), domain).toEqual(Object.keys(TONES[domain]).sort())
    }
  })

  it('unknown key throws in dev', () => {
    expect(() => statusOf('node', 'nope')).toThrow('unknown status: node.nope')
  })

  it('unknown key is neutral with raw key outside dev', () => {
    vi.stubEnv('DEV', false)
    expect(statusOf('node', 'nope')).toEqual({ label: 'nope', tone: 'neutral', known: false })
  })

  it('known keys give label and tone', () => {
    expect(statusOf('node', 'degraded')).toEqual({ label: '降级', tone: 'warn', known: true })
    expect(statusOf('block', 'not_computed')).toEqual({ label: '没算', tone: 'warn', known: true })
    expect(statusOf('block', 'insufficient')).toEqual({ label: '样本不足', tone: 'warn', known: true })
  })

  it('chain entry is info and ended states neutral', () => {
    expect(TONES.chain.entry).toBe('info')
    for (const key of ['legacy', 'invalidated', 'closed_by_decision', 'position_closed', 'superseded', 'expired'] as const) {
      expect(TONES.chain[key], key).toBe('neutral')
    }
  })

  it('same key, different tone by domain', () => {
    expect(TONES.node.pending).toBe('neutral')
    expect(TONES.run.pending).toBe('neutral')
    expect(TONES.execution.pending).toBe('info')
    expect(TONES.connection.pending).toBe('running')
    expect(TONES.run.error).toBe('danger')
  })

  it('follows the spec 4.4 tone table', () => {
    expect(TONES.node.success).toBe('success')
    expect(TONES.node.partial).toBe('warn')
    expect(TONES.node.failed).toBe('danger')
    expect(TONES.node.skipped).toBe('neutral')
    expect(TONES.node.running).toBe('running')
    expect(TONES.connection.connected).toBe('success')
    expect(TONES.connection.needs_login).toBe('danger')
    expect(TONES.card.reviewed).toBe('success')
    expect(TONES.card.to_review).toBe('warn')
    expect(TONES.card.drafting).toBe('warn')
    expect(TONES.card.draft_failed).toBe('danger')
    expect(TONES.execution.adopted).toBe('success')
    expect(TONES.execution.executed).toBe('success')
    expect(TONES.execution.out_of_validity).toBe('neutral')
    expect(TONES.execution.not_executed).toBe('neutral')
    expect(TONES.agent.rejected).toBe('danger')
    expect(TONES.agent.error).toBe('danger')
    expect(TONES.knowledge.active).toBe('success')
    expect(TONES.knowledge.archived).toBe('neutral')
    expect(TONES.block.not_computed).toBe('warn')
    expect(TONES.block.insufficient).toBe('warn')
  })

  it('backend vocabularies are covered', () => {
    const has = (domain: StatusDomain, keys: string[]) => {
      for (const key of keys) expect(TONES[domain], `${domain}.${key}`).toHaveProperty(key)
    }
    has('node', ['success', 'partial', 'degraded', 'failed', 'skipped', 'pending', 'running'])
    has('run', ['success', 'partial', 'degraded', 'failed', 'skipped', 'pending', 'running', 'error'])
    has('chain', ['legacy', 'invalidated', 'closed_by_decision', 'exiting', 'holding', 'position_closed', 'superseded', 'expired', 'entry'])
    has('fetch', ['success', 'partial', 'failed', 'skipped'])
    has('agent', ['ok', 'pass', 'rejected', 'error', 'skipped'])
    has('connection', ['disconnected', 'pending', 'connected', 'needs_login'])
    has('card', ['reviewed', 'to_review', 'draft_failed', 'drafting'])
    has('execution', ['adopted', 'out_of_validity', 'pending', 'not_executed', 'executed', 'unplanned'])
    has('knowledge', ['archived', 'active'])
    has('instruction', ['hold', 'sell_half', 'exit', 'no_plan'])
    has('system', ['up', 'degraded', 'down', 'unknown'])
  })

  it('TONES keeps the one-line-per-domain shape the Python guard parses', () => {
    const body = statusSource.match(/export const TONES = \{\n([\s\S]*?)\n\} as const satisfies/)
    expect(body).not.toBeNull()
    for (const line of body![1].split('\n')) {
      if (!line.trim() || line.trim().startsWith('//')) continue
      expect(line, line).toMatch(/^\s*\w+: \{ ((\w+|'[^']+'): '\w+'(, )?)+ \},$/)
      expect(line).not.toContain('...')
    }
  })
})
