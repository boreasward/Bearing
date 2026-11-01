import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/svelte'
import PlanDerivation from './PlanDerivation.svelte'
import * as api from '../api'
import { resetConceptIndex } from '../knowledge'

const conceptIndexOf = (ids: string[]) => ({
  knowledge_version: 'knowledge-1', groups: [],
  concepts: ids.map(id => ({ id, ref: `concept:${id}@1`, title: `T:${id}`, group: id.split('.')[0], version: 1,
                             applies_to: [], summary: `S:${id}`, related: [] })),
})
const termIds = async () => new Set((await screen.findAllByRole('button', { name: /^概念：/, hidden: true }))
  .map(b => b.getAttribute('aria-label')!.replace(/^概念：T:/, '')))
import type { Derivation, Step, TriggerItem } from '../derivation'

// 逐字文案来自 v2.4 spec §7 / 后端 derivation.py 常量（接口原样下发，前端不改动）
const EVIDENCE = '规则 v1 与 `plan-1` 都未经验证（Phase 0 Gate HOLD）。推导一致只说明算术正确，不说明规则有效。'
const TRIGGER_NOTE = '触发段只复核候选冻结的取值是否满足阈值，不从原始日线重算均线。'
const RUN_NOTE_NO_TABLE = '迁移 025 未执行，暂无阶段证据'
const RUN_NOTE_PRE_V24 = 'v2.4 之前生成，无阶段证据'

const step = (over: Partial<Step> = {}): Step => ({
  key: 'entry_low', label: '入场下沿', formula: 'ref_price − 0.5 × atr，四舍五入到分',
  substitutions: { ref_price: '110.750000', ENTRY_ATR: '0.5', atr: '2.234567' },
  result: '109.63', stored: '109.63', source: 'formula', consistent: true, ...over,
})

const triggerItem = (over: Partial<TriggerItem> = {}): TriggerItem => ({
  candidate_id: 'c1', rule_id: 'trend_reclaim', rule_version: '1', action: 'buy',
  clauses: [
    { text: 'close > ma50 > ma200',
      operands: { 'values.close': 110.75, 'values.ma50': 108.1, 'values.ma200': 102.4 }, result: 'holds' },
    { text: 'close > ma20', operands: { 'values.close': 110.75, 'values.ma20': 108.2 }, result: 'holds' },
    { text: '此前连续 3 日收于 MA20 之下', operands: { 'thresholds.days_below_ma20': null }, result: 'not_frozen' },
  ], ...over,
})

const derivation = (over: Partial<Derivation> = {}): Derivation => ({
  derivation_version: 'derivation-1',
  plan: { id: 'p1', ticker: 'NVDA', signal_date: '2026-10-22', kind: 'rule', plan_version: 'plan-1', status: 'ok' },
  trigger: { note: TRIGGER_NOTE, candidates: [triggerItem()] },
  inputs: {
    bar_ids: [1, 2],
    bars: [
      { id: '1', price_date: '2026-10-21', open: '109', high: '111', low: '108.9', close: '110.1', revision: '0' },
      { id: '2', price_date: '2026-10-22', open: '110', high: '111.5', low: '109.8', close: '110.75', revision: '0' },
    ],
    bars_note: '拆股调整、未分红调整',
    ref_price: { value: '110.750000', source_bar_id: 2 },
    earnings: null, earnings_note: '无记录',
    snapshot: { id: '42', as_of: '2026-10-20', equity: '100000.00' }, snapshot_note: null,
    exposure: { ticker_value: '0', total_value: '15000.00', note: '生成时冻结，不重算' },
    sizing_params: { risk_pct: '0.01', single_cap: '0.2', total_cap: '0.8', note: '取计划冻结值，不读当前配置' },
    regime: { id: 'r1', label: 'risk_off', calc_version: 'regime-2' },
    sector: { etf: 'SMH', rs20: -0.12, rs60: -0.2, state: 'weak' },
    next_earnings: '2026-11-20', pool_tier: 'core', source: null, screen: null, agent_run: null,
  },
  steps: [step()],
  checks: [{ key: 'levels_ordered', label: '价位顺序', formula: 'stop < entry_low ≤ entry_high < target1',
             substitutions: { stop: '106.29', entry_low: '109.63', entry_high: '111.87', target1: '119.69' },
             holds: true }],
  consistency: { status: 'consistent', mismatches: [], missing: [] },
  evidence: EVIDENCE,
  run: { ready: true, run_id: 'run-9', stage: 'plan', status: 'success',
         derivation: { checked: 2, consistent: 2, mismatch: 0, incomplete: 0 } },
  ...over,
})

describe('PlanDerivation', () => {
  afterEach(() => vi.restoreAllMocks())
  beforeEach(() => resetConceptIndex())
  it('labels a numeric frozen ledger equity source without calling it a broker snapshot', async () => {
    const value = derivation()
    value.inputs = { ...value.inputs, equity_source: 'ledger', snapshot: { id: 42, as_of: '2026-10-20', equity: '100000.0100' } }
    vi.spyOn(api, 'getPlanDerivation').mockResolvedValue(value)
    render(PlanDerivation, { planId: 'p1' })
    expect(await screen.findByText(/账本权益.*2026-10-20.*100000.0100/)).toBeInTheDocument()
    expect(screen.queryByText('资金快照', { exact: true })).toBeNull()
  })

  it('shows consistency badge in three states', async () => {
    const states = [['consistent', '推导与重算一致'], ['mismatch', '推导与已存值不一致'],
                    ['incomplete', '推导不完整']] as const
    for (const [status, text] of states) {
      vi.spyOn(api, 'getPlanDerivation')
        .mockResolvedValueOnce(derivation({ consistency: { status, mismatches: [], missing: [] } }))
      const { unmount } = render(PlanDerivation, { planId: 'p1' })
      expect(await screen.findByText(text)).toBeInTheDocument()
      unmount()
    }
  })

  it('renders four sections and the evidence text', async () => {
    const spy = vi.spyOn(api, 'getPlanDerivation').mockResolvedValue(derivation())
    render(PlanDerivation, { planId: 'p1' })
    expect(await screen.findByText('触发')).toBeInTheDocument()
    expect(screen.getByText('输入快照')).toBeInTheDocument()
    expect(screen.getByText('逐项计算')).toBeInTheDocument()
    expect(screen.getByText('校验')).toBeInTheDocument()
    expect(screen.getByText(EVIDENCE)).toBeInTheDocument()      // 证据文案逐字
    expect(screen.getByText(TRIGGER_NOTE)).toBeInTheDocument()  // 触发段说明逐字
    expect(spy).toHaveBeenCalledWith('p1')                     // 挂载时请求一次
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('step table shows formula, substitutions, result and stored', async () => {
    vi.spyOn(api, 'getPlanDerivation').mockResolvedValue(derivation({ steps: [
      step({ key: 'entry_low', formula: 'ref_price − 0.5 × atr，四舍五入到分',
             substitutions: { ref_price: '110.750000', ENTRY_ATR: '0.5', atr: '2.234567' },
             result: '109.63', stored: '109.63' }),
      step({ key: 'shares', label: '股数', formula: 'min(cap_risk, cap_single, cap_total)',
             substitutions: { cap_risk: '166', cap_single: '198', cap_total: '214' },
             result: '166', stored: '166' }),
    ] }))
    render(PlanDerivation, { planId: 'p1' })
    const table = await screen.findByRole('table', { name: '逐项计算' })
    expect(table).toHaveTextContent('ref_price − 0.5 × atr，四舍五入到分')
    expect(table).toHaveTextContent('ref_price=110.750000')
    expect(table).toHaveTextContent('ENTRY_ATR=0.5')
    expect(table).toHaveTextContent('cap_risk=166')
    expect(within(table).getAllByText('109.63')).toHaveLength(2)   // 结果与已存各一格
    expect(within(table).getAllByText('166')).toHaveLength(2)
    expect(within(table).getByRole('columnheader', { name: '一致' })).toBeInTheDocument()
  })

  it('labels own and agent level sources', async () => {
    vi.spyOn(api, 'getPlanDerivation').mockResolvedValueOnce(derivation({
      plan: { id: 'o1', ticker: 'NVDA', signal_date: '2026-10-22', kind: 'own', plan_version: 'plan-1', status: 'ok' },
      steps: [
        step({ key: 'entry_low', label: '入场下沿', formula: '你的输入，不重算', substitutions: {},
               result: '98', stored: null, source: 'user_input', consistent: null }),
        step({ key: 'equity', label: '资金', formula: '资金快照 equity', substitutions: {},
               result: '100000.00', stored: '100000.00', source: 'frozen', consistent: true }),
      ],
    }))
    const own = render(PlanDerivation, { planId: 'o1' })
    const ownTable = await screen.findByRole('table', { name: '逐项计算' })
    expect(within(ownTable).getByText('入场下沿（你的输入）')).toBeInTheDocument()
    expect(within(ownTable).getByText('资金（冻结值）')).toBeInTheDocument()
    own.unmount()

    vi.spyOn(api, 'getPlanDerivation').mockResolvedValueOnce(derivation({
      plan: { id: 'a1', ticker: 'NVDA', signal_date: '2026-10-22', kind: 'agent', plan_version: 'agent-1', status: 'ok' },
      steps: [step({ key: 'stop', label: '止损',
                     formula: '已存价位须等于 agent_runs.output.levels.stop.value',
                     substitutions: { 'output.levels.stop.value': '96.00' },
                     result: '96.00', stored: '96.00', source: 'agent_output', refs: ['call:1'] })],
    }))
    render(PlanDerivation, { planId: 'a1' })
    const agentTable = await screen.findByRole('table', { name: '逐项计算' })
    expect(within(agentTable).getByText('止损（Agent 引用 call:1）')).toBeInTheDocument()
  })

  it('trigger clauses use CLAUSE_TEXT', async () => {
    vi.spyOn(api, 'getPlanDerivation').mockResolvedValue(derivation({
      trigger: { note: TRIGGER_NOTE, candidates: [triggerItem({
        clauses: [
          { text: 'close > ma50 > ma200',
            operands: { 'values.close': 110.75, 'values.ma50': 108.1, 'values.ma200': 102.4 }, result: 'holds' },
          { text: 'close > ma20', operands: { 'values.close': 110.75, 'values.ma20': 111.5 }, result: 'violated' },
          { text: '此前连续 3 日收于 MA20 之下', operands: { 'thresholds.days_below_ma20': null }, result: 'not_frozen' },
        ] })] } }))
    render(PlanDerivation, { planId: 'p1' })
    const table = await screen.findByRole('table', { name: '触发条件 trend_reclaim' })
    expect(table).toHaveTextContent('成立')
    expect(table).toHaveTextContent('不成立')
    expect(table).toHaveTextContent('未冻结，无法复核')
    expect(table).toHaveTextContent('values.close=110.75')
    expect(table).toHaveTextContent('thresholds.days_below_ma20=无')
  })

  it('lists mismatches and missing items', async () => {
    vi.spyOn(api, 'getPlanDerivation').mockResolvedValue(derivation({
      consistency: { status: 'mismatch',
                     mismatches: [{ key: 'entry_low', result: '109.63', stored: '108.63' },
                                  { key: 'trigger:trend_reclaim', result: 'violated', stored: 'close > ma20' }],
                     missing: ['price_bars:42', 'account_snapshots:7'] } }))
    render(PlanDerivation, { planId: 'p1' })
    expect(await screen.findByText('推导与已存值不一致')).toBeInTheDocument()
    expect(screen.getByText('差异 entry_low：推导 109.63 ≠ 已存 108.63')).toBeInTheDocument()
    expect(screen.getByText('差异 trigger:trend_reclaim：推导 violated ≠ 已存 close > ma20')).toBeInTheDocument()
    expect(screen.getByText('缺失 price_bars:42')).toBeInTheDocument()
    expect(screen.getByText('缺失 account_snapshots:7')).toBeInTheDocument()
  })

  it('shows run evidence notes', async () => {
    vi.spyOn(api, 'getPlanDerivation').mockResolvedValueOnce(derivation({
      run: { ready: false, note: RUN_NOTE_NO_TABLE } }))
    const noTable = render(PlanDerivation, { planId: 'p1' })
    expect(await screen.findByText(RUN_NOTE_NO_TABLE)).toBeInTheDocument()   // 逐字
    noTable.unmount()

    vi.spyOn(api, 'getPlanDerivation').mockResolvedValueOnce(derivation({
      run: { ready: true, run_id: null, note: RUN_NOTE_PRE_V24 } }))
    render(PlanDerivation, { planId: 'p1' })
    expect(await screen.findByText(RUN_NOTE_PRE_V24)).toBeInTheDocument()    // 逐字
  })

  it('shows backend reason on 503', async () => {
    vi.spyOn(api, 'getPlanDerivation').mockRejectedValue(new Error('数据库不可达'))
    render(PlanDerivation, { planId: 'p1' })
    expect(await screen.findByRole('alert')).toHaveTextContent('数据库不可达')
  })

  it('renders numbers as given strings', async () => {
    vi.spyOn(api, 'getPlanDerivation').mockResolvedValue(derivation({ steps: [
      step({ key: 'atr', label: 'ATR14', formula: '(tr_1 + … + tr_14) / 14',
             substitutions: { tr_1: '2.10' }, result: '2.234567', stored: '2.234567' }),
      step({ key: 'entry_high', label: '入场上沿', formula: 'ref_price + 0.5 × atr，四舍五入到分',
             substitutions: {}, result: '111.87', stored: '111.870' }),
    ] }))
    render(PlanDerivation, { planId: 'p1' })
    const table = await screen.findByRole('table', { name: '逐项计算' })
    expect(within(table).getAllByText('2.234567')).toHaveLength(2)   // 6 位小数原样
    expect(within(table).getByText('111.870')).toBeInTheDocument()   // 尾随 0 保留，不取整成 111.87
    expect(screen.getByText(/110\.750000/)).toBeInTheDocument()      // 冻结的参考价同样原样
  })

  it('rule names, inputs and step labels carry ConceptTerm', async () => {
    const wanted = [
      'plan.derivation', 'system.gate_hold', 'rule.trend_reclaim',                        // badge, evidence, trigger table
      'plan.ref_price', 'plan.earnings', 'plan.sizing', 'indicator.regime', 'indicator.sector_strength', 'family.bars',
      'system.stages',                                                                     // run evidence
      'plan.entry_zone', 'indicator.atr14', 'plan.status',                                 // step labels
    ]
    vi.spyOn(api, 'getConcepts').mockResolvedValue(conceptIndexOf(wanted))
    vi.spyOn(api, 'getPlanDerivation').mockResolvedValue(derivation({
      steps: [step(), step({ key: 'tr_1', label: 'TR1' }), step({ key: 'levels_ordered', label: '价位顺序' }),
              step({ key: 'not_mapped', label: '其余' })],
      inputs: { ...derivation().inputs, bars: [
        { id: '1', price_date: '2026-10-21', open: '109', high: '111', low: '108.9', close: '110.1', revision: '0' }] },
    }))
    render(PlanDerivation, { planId: 'p1' })
    await screen.findByRole('table', { name: '逐项计算' })
    expect(await termIds()).toEqual(new Set(wanted))
    // the original row labels are still readable
    expect(screen.getByText('入场下沿（公式）')).toBeInTheDocument()
  })

  it('trigger-only view maps rule names too', async () => {
    vi.spyOn(api, 'getConcepts').mockResolvedValue(conceptIndexOf(['rule.trend_reclaim', 'system.gate_hold', 'plan.derivation']))
    vi.spyOn(api, 'getUnitDerivation').mockResolvedValue({
      derivation_version: 'derivation-1', trigger: [triggerItem()], note: TRIGGER_NOTE, plan_ids: [],
      consistency: { status: 'consistent', mismatches: [], missing: [] }, evidence: EVIDENCE })
    render(PlanDerivation, { unit: { ticker: 'NVDA', signal_date: '2026-10-22' } })
    await screen.findByText('触发')
    expect(await termIds()).toEqual(new Set(['rule.trend_reclaim', 'system.gate_hold', 'plan.derivation']))
  })
})
