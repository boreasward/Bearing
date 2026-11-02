import { afterEach, describe, expect, it, vi } from 'vitest'
import { LABELS, TONES, statusOf, type StatusDomain } from './status'
import statusSource from './status.ts?raw'
import { NODE_STATUS_TEXT, RETRO_PUSH_TEXT, RETRO_STATUS_TEXT, RUN_STATUS_TEXT } from '$lib/runs'
import { CHAIN_STATE_TEXT } from '$lib/timeline'
import { CARD_STATUS_TEXT, KNOWLEDGE_STATUS_TEXT } from '$lib/growth'
import { EXECUTION_TEXT, INSTRUCTION_TEXT } from '$lib/workbench'
import { CONSISTENCY_TEXT } from '$lib/derivation'

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
      'knowledge', 'node', 'paper_decision', 'paper_order', 'portfolio_verdict', 'proposal', 'push', 'reconcile', 'research_request', 'retro', 'roadmap_version', 'run', 'system',
    ])
    expect(Object.keys(LABELS).sort()).toEqual(domains)
  })

  it('agent pass is the agent declining to buy (agent/graph.py), not 维持', () => {
    expect(LABELS.agent.pass).toBe('不建议买入')
  })

  it('labels reuse the existing constants', () => {
    // node is a copy plus missing / not_recorded (below), like execution
    expect(LABELS.run).toBe(RUN_STATUS_TEXT)
    expect(LABELS.retro).toBe(RETRO_STATUS_TEXT)
    expect(LABELS.push).toBe(RETRO_PUSH_TEXT)
    expect(LABELS.chain).toBe(CHAIN_STATE_TEXT)
    expect(LABELS.card).toBe(CARD_STATUS_TEXT)
    expect(LABELS.knowledge).toBe(KNOWLEDGE_STATUS_TEXT)
    expect(LABELS.instruction).toBe(INSTRUCTION_TEXT)
    expect(LABELS.consistency).toBe(CONSISTENCY_TEXT)
  })

  it('execution reuses the EXECUTION_TEXT entries and adds the one it lacks', () => {
    // EXECUTION_TEXT has no `unplanned` (the weekly by_execution key), so the domain is a copy plus that key.
    for (const [key, text] of Object.entries(EXECUTION_TEXT)) expect(LABELS.execution[key]).toBe(text)
    expect(LABELS.execution.unplanned).toBe('计划外成交')
  })

  it('node reuses every NODE_STATUS_TEXT entry and adds the run overview wording the backend sends', () => {
    // NODE_STATUS_TEXT (runs.ts) has no missing / not_recorded; they come from run_overview.py STATUS_TEXT, verbatim.
    for (const [key, text] of Object.entries(NODE_STATUS_TEXT)) expect(LABELS.node[key]).toBe(text)
    expect(LABELS.node.missing).toBe('缺证据')
    expect(LABELS.node.not_recorded).toBe('未记录')
    expect(TONES.node.missing).toBe('warn')
    expect(TONES.node.not_recorded).toBe('neutral')
  })

  it('exit is the most salient instruction (danger); the consistency domain follows CONSISTENCY_TEXT', () => {
    expect(TONES.instruction.exit).toBe('danger')
    expect(TONES.instruction.sell_half).toBe('warn')
    expect(TONES.consistency).toEqual({ consistent: 'success', mismatch: 'danger', incomplete: 'warn' })
    expect(Object.keys(CONSISTENCY_TEXT).sort()).toEqual(Object.keys(TONES.consistency).sort())
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
