import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/svelte'
import AgentReasoning from './AgentReasoning.svelte'
import * as api from '../api'
import { resetConceptIndex } from '../knowledge'
import type { ReasoningView } from '../knowledge'

const call = (seq: number, tool: string, over: Record<string, unknown> = {}) => ({
  seq, ref: `call:${seq}`, tool, tool_text: ({
    get_price_bars: '日线', get_indicators: '指标', get_concept: '概念卡', get_market_context: '市场环境',
  } as Record<string, string>)[tool] ?? tool,
  phase: 'propose', args: { ticker: 'NVDA' }, result: { last_close: '110.75', atr14: '2.23' },
  result_sha256_8: 'abcd1234', called_at: '2026-10-27T22:30:00Z', concept: 'family.bars',
  fields: { last_close: 'family.bars', atr14: 'indicator.atr14' }, cutoff_exempt: false, ...over,
})

const run = (over: Record<string, unknown> = {}) => ({
  id: 'run-1', ticker: 'NVDA', signal_date: '2026-10-22', mode: 'propose', agent_version: 'agent-2', status: 'ok',
  reason: null, reason_text: null, model: 'gpt-test', cutoff_at: '2026-10-27T22:00:00Z',
  started_at: '2026-10-27T22:30:00Z', finished_at: '2026-10-27T22:31:00Z', input_tokens: 10, output_tokens: 5, ...over,
})

const proposal = {
  kind: 'proposal', title: '提议', concept: 'agent.action_levels', action: 'buy', summary: '回踩修复放量',
  levels: [
    { key: 'entry_low', value: '98.50', refs: ['call:1'], concept: 'plan.entry_zone' },
    { key: 'stop', value: '96.00', refs: ['call:2'], concept: 'plan.stop' },
  ],
  thesis: [{ id: 'T1', text: '放量突破 20 日线', refs: ['call:1', 'user:7'] }],
  invalidation: [{ kind: 'close_below', params: { price: '95.00' }, text: '收盘跌破 95.00', refs: ['call:2'] }],
  review_when: [{ kind: 'next_session', params: {}, text: '下一交易日', refs: [] }],
}
const steps = {
  readPropose: { kind: 'read', title: '提议者读了什么', concept: 'agent.tool_calls',
                 calls: [call(1, 'get_price_bars'), call(2, 'get_indicators', { concept: 'indicator.atr14' })] },
  proposal,
  check: { kind: 'check', title: '校验结果', concept: 'agent.reject_reasons', items: [
    { id: 'json', text: '输出是合法 JSON 且字段齐全', result: 'pass' },
    { id: 'refs', text: 'refs 引用都存在', result: 'pass' },
  ] },
  readCounter: { kind: 'read', title: '反方读了什么', concept: 'agent.tool_calls',
                 calls: [call(3, 'get_market_context', { phase: 'counter', concept: 'indicator.regime' })] },
  counter: { kind: 'counter', title: '反方', concept: 'agent.counter',
             available: true, items: [{ thesis: 'T1', text: '量能来自指数调仓', refs: ['call:3'] }],
             risks: [{ text: '财报临近', refs: ['call:3'] }], dropped: 1, uncountered: ['T2'] },
  response: { kind: 'response', title: '回应：本版之后还没有复查', concept: 'agent.review_outcome', items: [] },
  outcome: { kind: 'outcome', title: '结果', concept: 'plan.status', plan_id: 'ap2', status: 'ok',
             invalid_reasons: [], consistency: { status: 'consistent', mismatches: [], missing: [] } },
}
const reasoning = (over: Partial<ReasoningView> = {}): ReasoningView => ({
  run: run(), legacy: false, version: { plan_id: 'ap2', version_no: 0, origin: 'signal' }, review: null,
  steps: [steps.readPropose, steps.proposal, steps.check, steps.readCounter, steps.counter, steps.response, steps.outcome] as any,
  notes: [], ...over,
})

beforeEach(() => {
  resetConceptIndex()
  vi.spyOn(api, 'getConcepts').mockRejectedValue(new Error('offline'))
  vi.spyOn(api, 'getReasoning').mockResolvedValue(reasoning())
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

const headings = () => screen.getAllByRole('heading', { level: 4 }).map(h => h.textContent!.replace(/ⓘ/g, '').trim())

describe('AgentReasoning', () => {
  it('renders research section with facts by kind and blocked list', async () => {
    vi.mocked(api.getReasoning).mockRestore()
    const payload = reasoning({ run: run({ agent_version: 'agent-4' }), steps: [
      { kind: 'read', title: '研究员读了什么', concept: 'agent.tool_calls', calls: [call(7, 'get_price_bars')] },
      { kind: 'research', title: '公司研究', concept: 'agent.company_researcher', status: 'ok', reason: null, output: { summary: '公司研究摘要', facts: [{ text: '披露营收增长', kind: 'disclosure', refs: ['call:7'] }, { text: '增长有望延续', kind: 'inference', refs: ['call:7'] }], risks: [], blocked: [{ what: '本季指引', why: '工具没有返回' }] } },
      { kind: 'proposal', title: '提议', concept: 'agent.action_levels', action: 'pass', reasons: [{ text: '缺本季指引，等待披露', refs: ['call:7'] }] },
    ] })
    vi.stubGlobal('fetch', vi.fn(async (path: string) => {
      if (path === '/api/workbench/agent-runs/run-1/reasoning') return Response.json(payload)
      throw new Error(`Unexpected request ${path}`)
    }))
    render(AgentReasoning, { runId: 'run-1' })
    const region = await screen.findByRole('region', { name: '公司研究' })
    expect(region.querySelector('[data-source="披露事实"]')).not.toBeNull()
    expect(region.querySelector('[data-source="推断"]')).not.toBeNull()
    expect(region).toHaveTextContent('本季指引：工具没有返回')
    expect(headings()).toEqual(['研究员读了什么', '公司研究', '提议'])
    await fireEvent.click(within(region).getAllByRole('button', { name: 'call:7' })[0])
    expect(document.querySelector('[data-call="run-1-7"]')).toHaveAttribute('data-highlighted', 'true')
  })
  it('a carried research segment shows its provenance and never jumps into this run\'s calls', async () => {
    vi.mocked(api.getReasoning).mockRestore()
    const output = { summary: '沿用的研究', facts: [{ text: '原运行读到的事实', kind: 'disclosure' as const, refs: ['call:1'] }], risks: [], blocked: [] }
    const mine = reasoning({ run: run({ agent_version: 'agent-4' }), steps: [
      { kind: 'read', title: '提议者读了什么', concept: 'agent.tool_calls', calls: [call(1, 'get_price_bars')] },
      { kind: 'research', title: '公司研究', concept: 'agent.company_researcher', status: 'carried', reason: null, output, carried_from: 'run-0', original_status: 'ok' },
    ] })
    const original = reasoning({ run: run({ id: 'run-0', agent_version: 'agent-4' }), steps: [
      { kind: 'read', title: '研究员读了什么', concept: 'agent.tool_calls', calls: [call(1, 'get_price_bars')] },
    ] })
    vi.stubGlobal('fetch', vi.fn(async (path: string) => {
      if (path === '/api/workbench/agent-runs/run-1/reasoning') return Response.json(mine)
      if (path === '/api/workbench/agent-runs/run-0/reasoning') return Response.json(original)
      throw new Error(`Unexpected request ${path}`)
    }))
    render(AgentReasoning, { runId: 'run-1' })
    const region = await screen.findByRole('region', { name: '公司研究' })
    expect(region).toHaveTextContent('沿用研究（原状态：研究完成）')
    expect(region).toHaveTextContent('引用编号属于原运行')
    expect(within(region).queryByRole('button', { name: 'call:1' })).toBeNull()      // not a jump link: call:1 here is a different call
    await fireEvent.click(within(region).getByRole('button', { name: '看原运行的推理' }))
    expect(await within(region).findByRole('region', { name: '研究员读了什么' })).toBeInTheDocument()
    expect(document.querySelector('[data-call="run-1-1"]')).not.toHaveAttribute('data-highlighted')
  })
  it('fetches on mount only', async () => {
    const { rerender } = render(AgentReasoning, { runId: 'run-1' })
    await screen.findByText('回应：本版之后还没有复查')
    expect(api.getReasoning).toHaveBeenCalledTimes(1)
    expect(api.getReasoning).toHaveBeenCalledWith('run-1')
    await rerender({ runId: 'run-1' })
    expect(api.getReasoning).toHaveBeenCalledTimes(1)
  })

  it('propose steps in order (check before counter)', async () => {
    render(AgentReasoning, { runId: 'run-1' })
    await screen.findByText('回应：本版之后还没有复查')
    expect(headings()).toEqual(['提议者读了什么', '提议', '校验结果', '反方读了什么', '反方', '回应：本版之后还没有复查', '结果'])
    const read = screen.getByRole('region', { name: '提议者读了什么' })
    expect(within(read).getByText(/call:1/)).toBeInTheDocument()
    expect(within(read).getByText(/日线/)).toBeInTheDocument()
    expect(within(read).getAllByText(/abcd1234/)).toHaveLength(2)      // one hash per call row
    // proposal levels, thesis and conditions
    const prop = screen.getByRole('region', { name: '提议' })
    expect(prop).toHaveTextContent('回踩修复放量')
    expect(prop).toHaveTextContent('入场下限')
    expect(prop).toHaveTextContent('98.50')
    expect(prop).toHaveTextContent('T1')
    expect(prop).toHaveTextContent('放量突破 20 日线')
    expect(prop).toHaveTextContent('user:7')
    expect(prop).toHaveTextContent('收盘跌破 95.00')
    expect(prop).toHaveTextContent('下一交易日')
    // counter
    const counter = screen.getByRole('region', { name: '反方' })
    expect(counter).toHaveTextContent('量能来自指数调仓')
    expect(counter).toHaveTextContent('财报临近')
    expect(counter).toHaveTextContent('丢弃 1')
    expect(counter).toHaveTextContent('T2')    // uncountered thesis 2
    // outcome
    expect(screen.getByRole('region', { name: '结果' })).toHaveTextContent('推导与重算一致')
    expect(screen.getByRole('link', { name: '看推导' })).toHaveAttribute('href', '#plan/ap2')
  })

  it('implicated checks show the shared-reason note', async () => {
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({
      run: run({ status: 'rejected', reason: 'invalid_citation', reason_text: '引用无效已作废' }),
      steps: [steps.readPropose, steps.proposal, {
        kind: 'check', title: '校验结果', concept: 'agent.reject_reasons', items: [
          { id: 'json', text: '输出是合法 JSON 且字段齐全', result: 'not_recorded' },
          { id: 'refs', text: 'refs 引用都存在', result: 'implicated' },
          { id: 'price_refs', text: '每个价位至少引用一次价格类工具', result: 'implicated' },
          { id: 'tool_rounds', text: '工具轮数不超过 8', result: 'not_recorded' },
        ] }] as any }))
    render(AgentReasoning, { runId: 'run-1' })
    const check = await screen.findByRole('region', { name: '校验结果' })
    expect(check).toHaveTextContent('未通过的检查在这几项之中：refs 引用都存在、每个价位至少引用一次价格类工具；校验器遇到第一处失败就停，不记录是哪一项')
    expect(check).toHaveTextContent('可能未通过')
    expect(check).toHaveTextContent('未单独记录')
    expect(screen.getByText(/本次运行状态：已作废（引用无效已作废）/)).toBeInTheDocument()
  })

  it('revise shows evidence and diff', async () => {
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({
      run: run({ id: 'rv-1', mode: 'revise' }), version: { plan_id: 'p1', version_no: 1, origin: 'review' },
      steps: [
        { kind: 'evidence', title: '触发这次修订的复查', concept: 'agent.review_outcome', session: '2026-10-23',
          trigger: 'batch', signals: [{ kind: 't1_touched', fired: true, detail: '最高价触及 T1 108.00' },
                                       { kind: 'new_8k', fired: false, detail: '没有新的 8-K' }],
          conditions: [{ list: 'invalidation', index: 0, kind: 'close_below', params: { price: '94.00' }, result: 'not_met' }],
          user_input_ids: [7], review_agent_run_id: 'rr-1' },
        { ...proposal, changes: [{ field: 'stop', text: '止损上移', refs: ['call:2'] }],
          diff: [{ field: 'stop', before: '95.00', after: '96.00' }] },
      ] as any }))
    render(AgentReasoning, { runId: 'rv-1' })
    const evidence = await screen.findByRole('region', { name: '触发这次修订的复查' })
    expect(evidence).toHaveTextContent('2026-10-23')
    expect(evidence).toHaveTextContent('触及 T1')
    expect(evidence).toHaveTextContent('user:7')
    expect(evidence).not.toHaveTextContent('新 8-K')       // only fired signals
    expect(screen.getByText(/止损上移/)).toBeInTheDocument()
    const prop = screen.getByRole('region', { name: '提议' })
    expect(prop).toHaveTextContent('止损 95.00 → 96.00')
    await fireEvent.click(within(evidence).getByRole('button', { name: '看复查推理' }))
    expect(api.getReasoning).toHaveBeenCalledWith('rr-1')
  })

  it('review shows verdict', async () => {
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({
      run: run({ id: 'rr-1', mode: 'review', agent_version: 'agent-review-1' }),
      steps: [
        steps.readPropose,
        { kind: 'verdict', title: '复查结论', concept: 'agent.review_outcome', outcome: 'revise', decided_by: 'agent',
          reason: 'T1 已触及，上移止损', refs: ['call:1', 'user:7'] },
        steps.check,
      ] as any }))
    render(AgentReasoning, { runId: 'rr-1' })
    const verdict = await screen.findByRole('region', { name: '复查结论' })
    expect(verdict).toHaveTextContent('修订')
    expect(verdict).toHaveTextContent('Agent')
    expect(verdict).toHaveTextContent('T1 已触及，上移止损')
    expect(verdict).toHaveTextContent('call:1')
  })

  it('call:N in thesis scrolls to and highlights the call', async () => {
    render(AgentReasoning, { runId: 'run-1' })
    const prop = await screen.findByRole('region', { name: '提议' })
    const read = screen.getByRole('region', { name: '提议者读了什么' })
    const row = read.querySelector('[data-call="run-1-1"]') as HTMLElement
    expect(row.dataset.highlighted).toBeUndefined()
    const thesis = within(prop).getByRole('list', { name: '论点' })
    await fireEvent.click(within(thesis).getByRole('button', { name: 'call:1' }))
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled()
    expect(row.dataset.highlighted).toBe('true')
    // user refs are plain text, not buttons
    expect(within(thesis).queryByRole('button', { name: 'user:7' })).toBeNull()
    expect(thesis).toHaveTextContent('user:7')
    await fireEvent.click(within(prop).getAllByRole('button', { name: 'call:2' })[0])
    expect(row.dataset.highlighted).toBeUndefined()
    expect((read.querySelector('[data-call="run-1-2"]') as HTMLElement).dataset.highlighted).toBe('true')
  })

  it('call results show a count and first/last rows for lists, expandable', async () => {
    const bars = Array.from({ length: 6 }, (_, i) => ({ d: `2026-10-0${i + 1}`, c: String(100 + i) }))
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({ steps: [{
      kind: 'read', title: '提议者读了什么', concept: 'agent.tool_calls',
      calls: [call(1, 'get_price_bars', { result: { bars }, fields: {} })] }] as any }))
    render(AgentReasoning, { runId: 'run-1' })
    const read = await screen.findByRole('region', { name: '提议者读了什么' })
    await fireEvent.click(within(read).getByRole('button', { name: '看返回值' }))
    expect(read).toHaveTextContent('bars（6 条）')
    expect(read).toHaveTextContent('2026-10-01')
    expect(read).toHaveTextContent('2026-10-06')
    expect(read).not.toHaveTextContent('2026-10-03')
    await fireEvent.click(within(read).getByRole('button', { name: '展开全部' }))
    expect(read).toHaveTextContent('2026-10-03')
  })

  it('shows the previous-chain block before what the proposer read (v2.9 D9)', async () => {
    const block = '同标的上一条链（只读背景，不可引用；价位与论点仍须引用本次工具调用）\n- 链 MU·2026-10-21（共 3 版），结束：已过期未执行'
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({ steps: [
      { kind: 'previous_chain', title: '同标的上一条链', concept: 'system.active_chain', text: block, sha256: 'ab',
        note: '只读背景，不可引用' }, steps.readPropose, steps.proposal] as any }))
    render(AgentReasoning, { runId: 'run-1' })
    const chain = await screen.findByRole('region', { name: '同标的上一条链' })
    expect(chain.querySelector('pre[data-previous-chain]')?.textContent).toContain('链 MU·2026-10-21')
    const titles = screen.getAllByRole('heading', { level: 4 }).map(h => h.textContent?.trim())
    expect(titles.indexOf('同标的上一条链')).toBeLessThan(titles.indexOf('提议者读了什么'))
  })

  it('get_concept call shows cutoff exemption', async () => {
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({ steps: [{
      kind: 'read', title: '提议者读了什么', concept: 'agent.tool_calls',
      calls: [call(1, 'get_concept', { args: { concept_id: 'plan.stop' }, result: { id: 'plan.stop', title: '止损' },
                                       fields: { id: 'plan.stop' }, concept: 'system.concept_card', cutoff_exempt: true })] }] as any }))
    render(AgentReasoning, { runId: 'run-1' })
    expect(await screen.findByText('概念卡随代码版本走，不受截止时间约束')).toBeInTheDocument()
  })

  it('agent-1 note', async () => {
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({
      legacy: true, notes: ['agent-1（旧版）：没有失效条件、下次复查条件与逐条对应的反证，也不参与复查；以下按原样显示'],
      steps: [steps.readPropose, { ...proposal, invalidation: undefined, review_when: undefined }, steps.check,
              steps.readCounter, { kind: 'counter', title: '反方', concept: 'agent.counter', available: true, items: [], risks: [{ text: '财报临近', refs: [] }] },
              steps.outcome] as any }))
    render(AgentReasoning, { runId: 'run-1' })
    expect(await screen.findByText(/agent-1（旧版）：没有失效条件/)).toBeInTheDocument()
    expect(screen.queryByText(/回应/)).toBeNull()
    expect(screen.getByRole('region', { name: '提议' })).toHaveTextContent('理由')
  })

  it('rejected run shows calls and reason only', async () => {
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({
      run: run({ status: 'rejected', reason: 'level_check', reason_text: '价位校验未通过' }),
      steps: [steps.readPropose, {
        kind: 'check', title: '校验结果', concept: 'agent.reject_reasons', items: [
          { id: 'level_order', text: '价位顺序', result: 'implicated' }] }] as any }))
    render(AgentReasoning, { runId: 'run-1' })
    expect(await screen.findByText('这次运行作废：价位校验未通过，只保留工具调用')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '提议者读了什么' })).toHaveTextContent('call:1')
    expect(screen.queryByRole('region', { name: '提议' })).toBeNull()
  })

  it('unavailable steps show the backend sentence', async () => {
    const void_text = '这次运行作废：价位校验未通过，只保留工具调用'
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({
      run: run({ status: 'rejected', reason: 'level_check', reason_text: '价位校验未通过' }),
      steps: [steps.readPropose,
              { kind: 'proposal', title: '提议', concept: 'agent.action_levels', available: false, text: void_text },
              steps.check, steps.readCounter,
              { kind: 'counter', title: '反方审查', concept: 'agent.counter', available: false, text: void_text }] as any }))
    render(AgentReasoning, { runId: 'run-1' })
    await screen.findByRole('region', { name: '提议' })
    expect(screen.getAllByText(void_text)).toHaveLength(2)      // the page does not add a third copy
  })

  it('interpret and verdict steps use the stored fields', async () => {
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({
      run: run({ mode: 'interpret' }),
      steps: [{ kind: 'interpretation', title: '解读', concept: 'agent.brief', available: true, summary: '多头占优',
                support: [{ text: '放量', refs: ['call:1'] }], contra: [], conflicts: [] },
              { kind: 'verdict', title: '复查结论', concept: 'agent.review_outcome', available: true, outcome: 'maintain',
                outcome_text: '维持', reason: '无新信号', refs: [] }] as any }))
    render(AgentReasoning, { runId: 'run-1' })
    const interp = await screen.findByRole('region', { name: '解读' })
    expect(interp).toHaveTextContent('多头占优')
    expect(interp).toHaveTextContent('放量')
    expect(screen.getByRole('region', { name: '复查结论' })).toHaveTextContent('维持')
  })

  it('shows the version this run produced and read errors', async () => {
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({
      version: { plan_id: 'p9', version_no: 3, origin: 'review' },
      steps: [{ kind: 'read', title: '提议者读了什么', concept: 'agent.tool_calls', note: null,
                calls: [call(1, 'get_news', { result: null, error: '数据源不可用' })] }] as any }))
    render(AgentReasoning, { runId: 'run-1' })
    expect(await screen.findByText(/这次运行产出第 3 版 · 每日复查/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '看计划页' })).toHaveAttribute('href', '#plan/p9')
    expect(screen.getByText('工具返回错误：数据源不可用')).toBeInTheDocument()
  })

  it('fields keyed by macro series and rule ids get their concepts', async () => {
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({ steps: [{
      kind: 'read', title: '提议者读了什么', concept: 'agent.tool_calls',
      calls: [call(1, 'get_market_context', { result: { label: 'risk_on', macro: { T10Y2Y: {} } },
                fields: { label: 'indicator.regime', 'macro.T10Y2Y': 'indicator.t10y2y' } })] }] as any }))
    render(AgentReasoning, { runId: 'run-1' })
    const read = await screen.findByRole('region', { name: '提议者读了什么' })
    await fireEvent.click(within(read).getByRole('button', { name: '看返回值' }))
    expect(read).toHaveTextContent('涉及：macro.T10Y2Y')
    expect(read).not.toHaveTextContent('涉及：label')
  })

  it('response empty text', async () => {
    render(AgentReasoning, { runId: 'run-1' })
    expect(await screen.findByRole('heading', { name: '回应：本版之后还没有复查' })).toBeInTheDocument()
  })

  it('response lists later reviews with a link to the new version', async () => {
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({ steps: [{
      kind: 'response', title: '回应', concept: 'agent.review_outcome', items: [
        { review_id: 9, session: '2026-10-23', trigger: 'batch', outcome: 'revise', decided_by: 'agent',
          reason: 'T1 已触及', signals_fired: ['t1_touched'], new_plan_id: 'p1', new_version_no: 1,
          changes: [{ field: 'stop', before: '95.00', after: '96.00' }] }] }] as any }))
    render(AgentReasoning, { runId: 'run-1' })
    const region = await screen.findByRole('region', { name: '回应' })
    expect(region).toHaveTextContent('2026-10-23')
    expect(region).toHaveTextContent('修订')
    expect(region).toHaveTextContent('触及 T1')
    expect(within(region).getByRole('link', { name: '第 1 版' })).toHaveAttribute('href', '#plan/p1')
  })

  it('shows the error when the request fails', async () => {
    vi.mocked(api.getReasoning).mockRejectedValue(new Error('不存在'))
    render(AgentReasoning, { runId: 'nope' })
    expect(await screen.findByRole('alert')).toHaveTextContent('不存在')
  })

  it('retro steps', async () => {
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({
      run: run({ id: 'rd-1', mode: 'retro', agent_version: 'agent-retro-1' }), version: null,
      steps: [
        { kind: 'evidence', title: '冻结的事实', concept: 'review.retro_card',
          text: '平仓（按止损） · 结束日 2026-10-23', fact_keys: ['subject', 'plans', 'bars_after'] },
        { kind: 'read', title: '起草者读了什么', concept: 'agent.tool_calls', calls: [call(1, 'get_price_bars')] },
        { kind: 'draft', title: '起草', concept: 'review.retro_card', summary: '回踩买入后触及止损',
          items: [{ text: '入场区间当天触及', refs: ['fact:plans', 'call:1'] }, { text: '决定照规则计划', refs: ['fact:decision'] }],
          lessons: [{ title: '回踩不追高', body: '高于上沿就等', scope: '回踩触发后', counter_examples: '放量突破除外',
                      refs: ['fact:discipline', 'know:3@1'] }] },
        { kind: 'check', title: '校验结果', concept: 'agent.reject_reasons', items: [
          { id: 'retro_shape', text: '草稿格式', result: 'pass' }, { id: 'refs', text: '引用', result: 'pass' },
          { id: 'lessons', text: '教训', result: 'pass' }] },
      ] as any }))
    render(AgentReasoning, { runId: 'rd-1' })
    await screen.findByRole('region', { name: '起草' })
    expect(headings()).toEqual(['冻结的事实', '起草者读了什么', '起草', '校验结果'])
    expect(screen.getByText(/复盘起草/)).toBeInTheDocument()
    const evidence = screen.getByRole('region', { name: '冻结的事实' })
    expect(evidence).toHaveTextContent('平仓（按止损）')
    expect(evidence).toHaveTextContent('fact:subject')
    expect(evidence).toHaveTextContent('fact:bars_after')
    const draft = screen.getByRole('region', { name: '起草' })
    expect(draft).toHaveTextContent('回踩买入后触及止损')
    expect(draft).toHaveTextContent('入场区间当天触及')
    expect(draft).toHaveTextContent('回踩不追高')
    expect(draft).toHaveTextContent('适用范围：回踩触发后')
    expect(draft).toHaveTextContent('反例：放量突破除外')
    expect(within(draft).getByRole('link', { name: 'know:3@1' })).toHaveAttribute('href', '#knowledge/mine/3@1')
    expect(draft).toHaveTextContent('fact:discipline')
  })

  it('discuss steps', async () => {
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({
      run: run({ id: 'ds-1', mode: 'discuss', agent_version: 'agent-discuss-1' }), version: null,
      steps: [
        { kind: 'question', title: '你的问题', concept: 'system.discussion', text: '这个止损怎么来的？',
          attachment: '附件：回踩别追（第 2 版）（用户提供，非证据，不可引用）', context: '[fact:context] 计划当时的截止时间 X' },
        { kind: 'read', title: '回答者读了什么', concept: 'agent.tool_calls', calls: [call(1, 'get_derivation')] },
        { kind: 'answer', title: '回答', concept: 'system.discussion', text: '止损 = 参考价减 2 倍 ATR14。',
          refs: ['call:1', 'concept:plan.stop@1', 'fact:context'],
          knowledge_draft: { title: '止损别拖', body: '次日就卖', scope: '持仓期', counter_examples: '财报前后', refs: ['call:1'] } },
        { kind: 'check', title: '校验结果', concept: 'agent.reject_reasons', items: [
          { id: 'answer_shape', text: '回答格式', result: 'pass' }] },
      ] as any }))
    render(AgentReasoning, { runId: 'ds-1' })
    await screen.findByRole('region', { name: '回答' })
    expect(headings()).toEqual(['你的问题', '回答者读了什么', '回答', '校验结果'])
    expect(screen.getByText(/讨论/)).toBeInTheDocument()
    const question = screen.getByRole('region', { name: '你的问题' })
    expect(question).toHaveTextContent('这个止损怎么来的？')
    expect(question).toHaveTextContent('附件：回踩别追（第 2 版）（用户提供，非证据，不可引用）')
    expect(question).toHaveTextContent('[fact:context] 计划当时的截止时间 X')
    const answer = screen.getByRole('region', { name: '回答' })
    expect(answer).toHaveTextContent('止损 = 参考价减 2 倍 ATR14。')
    expect(within(answer).getByRole('button', { name: 'call:1' })).toBeInTheDocument()
    expect(answer).toHaveTextContent('经验草稿')
    expect(answer).toHaveTextContent('止损别拖')
  })

  it('get_knowledge call note', async () => {
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({ steps: [{
      kind: 'read', title: '提议者读了什么', concept: 'agent.tool_calls',
      calls: [call(1, 'get_knowledge', { tool_text: '我的知识', args: { ticker: 'NVDA' }, concept: 'system.my_knowledge',
        fields: {}, result: { query: { ticker: 'NVDA' }, point_in_time: true, omitted: 0, note: '这是用户审定过的经验',
          entries: [{ ref: 'know:3@2', id: 3, version: 2, title: '回踩别追' }] } })] }] as any }))
    render(AgentReasoning, { runId: 'run-1' })
    const read = await screen.findByRole('region', { name: '提议者读了什么' })
    expect(read).toHaveTextContent('我的知识按截止时间读取：只含截止时间之前写入的版本')
    await fireEvent.click(within(read).getByRole('button', { name: '看返回值' }))
    const link = within(read).getByRole('link', { name: 'know:3@2' })
    expect(link).toHaveAttribute('href', '#knowledge/mine/3@2')
    expect(read).toHaveTextContent('回踩别追')
  })

  it('know: refs inside a proposal link to the version page', async () => {
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({ steps: [
      { ...proposal, thesis: [{ id: 'T1', text: '回踩企稳', refs: ['call:1', 'know:3@2'] }] }] as any }))
    render(AgentReasoning, { runId: 'run-1' })
    const prop = await screen.findByRole('region', { name: '提议' })
    expect(within(prop).getByRole('link', { name: 'know:3@2' })).toHaveAttribute('href', '#knowledge/mine/3@2')
  })

  it('shows 引用的知识 when the view carries knowledge_refs', async () => {
    vi.mocked(api.getReasoning).mockResolvedValue(reasoning({
      knowledge_refs: [{ ref: 'know:3@2', title: '回踩别追', fields: ['thesis'], current_version: 2 }] } as any))
    render(AgentReasoning, { runId: 'run-1' })
    const refs = await screen.findByRole('region', { name: '引用的知识' })
    expect(within(refs).getByRole('link', { name: 'know:3@2' })).toBeInTheDocument()
  })
})
